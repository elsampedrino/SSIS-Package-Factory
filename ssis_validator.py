"""
ssis_validator.py

Validacion del IR (representacion intermedia) producido por ssis_parser,
deliberadamente separada del parsing.

ssis_parser.parse_package() nunca "arregla" ni descarta datos inconsistentes:
si un lineageId no resuelve o un externalMetadataColumnId no existe, el IR
lo deja tal cual (en general como None en el campo resuelto), sin ocultarlo.

Este modulo es el que decide si eso es un problema real (error) o solo algo
a tener en cuenta (warning), y por que. La distincion no es solo de forma:
- error: una referencia interna del propio pipeline (lineageId,
  externalMetadataColumnId, startId/endId de un path) que apunta a algo que
  no existe DENTRO del mismo data flow. Esto es siempre un problema, sea por
  un bug del parser o por un .dtsx real inconsistente.
- warning: algo notable pero esperable dado lo que sabemos que el .dtsx NO
  puede resolver por si solo (p. ej. un componentClassID nuevo que el parser
  todavia no soporta con builder especifico), o una ausencia que puede ser
  legitima segun el componente.
"""

from typing import Any, Dict, List, Optional, Set


def _issue(code: str, message: str, **context: Any) -> Dict[str, Any]:
    issue: Dict[str, Any] = {"code": code, "message": message}
    if context:
        issue["context"] = {k: v for k, v in context.items() if v is not None}
    return issue


def _collect_structural_indexes(components: List[Dict[str, Any]]):
    """
    Primer pase sobre los componentes de un data flow: junta todo lo que
    hace falta para validar referencias cruzadas en un segundo pase
    (lineageId definidos, refIds de inputs/outputs, externalMetadataColumn
    refIds por input/output).
    """
    lineage_ids_defined: Set[str] = set()
    input_ref_ids: Set[str] = set()
    output_ref_ids: Set[str] = set()
    ext_meta_ids_by_input: Dict[str, Set[str]] = {}
    ext_meta_ids_by_output: Dict[str, Set[str]] = {}

    for comp in components:
        for inp in comp.get("inputs", []):
            input_ref_ids.add(inp.get("ref_id"))
            ext_meta_ids_by_input[inp.get("ref_id")] = {
                c.get("ref_id") for c in inp.get("external_metadata_columns", [])
            }

        for out in comp.get("outputs", []) + comp.get("error_outputs", []):
            output_ref_ids.add(out.get("ref_id"))
            ext_meta_ids_by_output[out.get("ref_id")] = {
                c.get("ref_id") for c in out.get("external_metadata_columns", [])
            }
            for col in out.get("columns", []):
                if col.get("lineage_id"):
                    lineage_ids_defined.add(col["lineage_id"])

    return {
        "lineage_ids_defined": lineage_ids_defined,
        "input_ref_ids": input_ref_ids,
        "output_ref_ids": output_ref_ids,
        "ext_meta_ids_by_input": ext_meta_ids_by_input,
        "ext_meta_ids_by_output": ext_meta_ids_by_output,
    }


def validate_data_flow(data_flow: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    components = data_flow.get("components", [])
    idx = _collect_structural_indexes(components)

    for comp in components:
        comp_name = comp.get("name")
        comp_type = comp.get("type", "")

        if comp_type.startswith("unknown:"):
            warnings.append(
                _issue(
                    "unknown_component",
                    f"Componente '{comp_name}' tiene componentClassID sin builder especifico",
                    component=comp_name,
                    class_id=comp.get("class_id"),
                )
            )

        for conn in comp.get("connections", []):
            if not conn.get("connection_manager_ref_id"):
                warnings.append(
                    _issue(
                        "connection_missing_ref_id",
                        f"Connection '{conn.get('name')}' del componente '{comp_name}' no tiene connectionManagerRefId",
                        component=comp_name,
                        connection=conn.get("name"),
                    )
                )

        # inputColumn.source_lineage_id (a.k.a. lineageId) debe resolver
        # contra alguna outputColumn del mismo data flow.
        for inp in comp.get("inputs", []):
            ext_ids = idx["ext_meta_ids_by_input"].get(inp.get("ref_id"), set())
            for col in inp.get("columns", []):
                lid = col.get("source_lineage_id")
                if lid and lid not in idx["lineage_ids_defined"]:
                    errors.append(
                        _issue(
                            "unresolved_lineage_id",
                            f"inputColumn '{col.get('name')}' referencia un lineageId "
                            "que ningun output del data flow produce",
                            component=comp_name,
                            input=inp.get("name"),
                            column=col.get("name"),
                            lineage_id=lid,
                        )
                    )
                emid = col.get("external_metadata_column_id")
                if emid and emid not in ext_ids:
                    errors.append(
                        _issue(
                            "dangling_external_metadata_id",
                            f"inputColumn '{col.get('name')}' referencia un "
                            "externalMetadataColumnId que no existe en su input",
                            component=comp_name,
                            input=inp.get("name"),
                            column=col.get("name"),
                            external_metadata_column_id=emid,
                        )
                    )

        # outputColumn.source_input_lineage_id (Data Conversion, etc.) debe
        # resolver igual que un lineageId comun, y su externalMetadataColumnId
        # (si tiene) debe existir dentro del propio output.
        for out in comp.get("outputs", []) + comp.get("error_outputs", []):
            ext_ids = idx["ext_meta_ids_by_output"].get(out.get("ref_id"), set())
            sync_id = out.get("synchronous_input_id")
            if sync_id and sync_id not in idx["input_ref_ids"]:
                errors.append(
                    _issue(
                        "dangling_synchronous_input_id",
                        f"Output '{out.get('name')}' referencia synchronousInputId "
                        "que no corresponde a ningun input existente",
                        component=comp_name,
                        output=out.get("name"),
                        synchronous_input_id=sync_id,
                    )
                )
            for col in out.get("columns", []):
                sid = col.get("source_input_lineage_id")
                if sid and sid not in idx["lineage_ids_defined"]:
                    errors.append(
                        _issue(
                            "unresolved_lineage_id",
                            f"outputColumn '{col.get('name')}' referencia "
                            "SourceInputColumnLineageID que ningun output del "
                            "data flow produce",
                            component=comp_name,
                            output=out.get("name"),
                            column=col.get("name"),
                            lineage_id=sid,
                        )
                    )
                emid = col.get("external_metadata_column_id")
                if emid and emid not in ext_ids:
                    errors.append(
                        _issue(
                            "dangling_external_metadata_id",
                            f"outputColumn '{col.get('name')}' referencia un "
                            "externalMetadataColumnId que no existe en su output",
                            component=comp_name,
                            output=out.get("name"),
                            column=col.get("name"),
                            external_metadata_column_id=emid,
                        )
                    )

        # mappings (solo presentes en componentes destino con builder especifico)
        for mapping in comp.get("mappings", []):
            label = mapping.get("pipeline_input_column") or mapping.get(
                "destination_column"
            )
            if mapping.get("source_lineage_id") and mapping.get("source_column") is None:
                errors.append(
                    _issue(
                        "mapping_source_unresolved",
                        f"Mapping de '{label}' no pudo resolver source_column "
                        "a partir de su lineageId",
                        component=comp_name,
                        mapping=mapping,
                    )
                )
            if (
                mapping.get("destination_external_metadata_id")
                and mapping.get("destination_column") is None
            ):
                errors.append(
                    _issue(
                        "mapping_destination_unresolved",
                        f"Mapping de '{label}' no pudo resolver destination_column "
                        "a partir de su externalMetadataColumnId",
                        component=comp_name,
                        mapping=mapping,
                    )
                )
            elif (
                not mapping.get("destination_external_metadata_id")
                and mapping.get("destination_column") is None
            ):
                warnings.append(
                    _issue(
                        "mapping_without_destination_metadata",
                        f"Mapping de '{label}' no trae externalMetadataColumnId "
                        "(no se puede resolver el nombre fisico de destino)",
                        component=comp_name,
                        mapping=mapping,
                    )
                )

    # paths: startId/endId deben corresponder a un output/input existente.
    for path in data_flow.get("paths", []):
        start_id = path.get("start_id")
        end_id = path.get("end_id")
        if start_id not in idx["output_ref_ids"]:
            errors.append(
                _issue(
                    "dangling_path_start",
                    f"Path '{path.get('name')}' referencia startId que no "
                    "corresponde a ningun output existente",
                    path=path.get("name"),
                    start_id=start_id,
                )
            )
        if end_id not in idx["input_ref_ids"]:
            errors.append(
                _issue(
                    "dangling_path_end",
                    f"Path '{path.get('name')}' referencia endId que no "
                    "corresponde a ningun input existente",
                    path=path.get("name"),
                    end_id=end_id,
                )
            )

    return {"errors": errors, "warnings": warnings}


def validate_control_flow_level(
    executables: List[Dict[str, Any]],
    precedence_constraints: List[Dict[str, Any]],
    container_name: Optional[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Valida un nivel del arbol de Control Flow (el Package, o un Sequence
    Container) y despues recurre a sus hijos. No cruza niveles: un
    PrecedenceConstraint siempre conecta ejecutables HERMANOS dentro del
    mismo container (asi esta serializado en ambos archivos de referencia).
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    sibling_ref_ids = {e.get("ref_id") for e in executables}

    for pc in precedence_constraints:
        if pc.get("from") not in sibling_ref_ids:
            errors.append(
                _issue(
                    "dangling_precedence_from",
                    f"PrecedenceConstraint '{pc.get('name')}' referencia From "
                    "que no corresponde a ningun executable de este nivel",
                    container=container_name,
                    constraint=pc.get("name"),
                    from_ref=pc.get("from"),
                )
            )
        if pc.get("to") not in sibling_ref_ids:
            errors.append(
                _issue(
                    "dangling_precedence_to",
                    f"PrecedenceConstraint '{pc.get('name')}' referencia To "
                    "que no corresponde a ningun executable de este nivel",
                    container=container_name,
                    constraint=pc.get("name"),
                    to_ref=pc.get("to"),
                )
            )

    # structurally_isolated ya viene calculado por
    # ssis_parser.annotate_execution_roles: el executable no tiene ningun
    # PrecedenceConstraint de su nivel, ni entrante ni saliente. El validador
    # no recalcula esa topologia, solo reporta lo que el parser ya derivo.
    #
    # OJO: esto es SOLO una nota estructural, no una afirmacion de que el
    # executable sea codigo muerto ni de que no vaya a ejecutarse. Un
    # executable aislado puede ser perfectamente una raiz independiente del
    # Control Flow que arranca en paralelo (ver docs/campanias_vs_turnero.md).
    # Que efectivamente corra o no depende de effective_enabled, que se
    # informa aparte para que quien lea el warning no confunda ambas cosas.
    #
    # El warning solo tiene sentido cuando hay MAS de un executable en el
    # nivel: si un Data Flow Task es el UNICO hijo del Package (caso
    # BipSuc_CampaniasVigentes.dtsx), no hay ningun hermano del cual estar
    # "aislado" — marcarlo igual seria ruido, no una observacion util.
    if len(executables) > 1:
        for exe in executables:
            if exe.get("structurally_isolated"):
                warnings.append(
                    _issue(
                        "structurally_isolated_executable",
                        f"Executable '{exe.get('name')}' no participa de ningun "
                        "PrecedenceConstraint de su nivel. Puede representar una "
                        "raiz independiente del Control Flow, no necesariamente codigo muerto.",
                        container=container_name,
                        executable=exe.get("name"),
                        effective_enabled=exe.get("effective_enabled"),
                    )
                )

    for exe in executables:
        exe_type = exe.get("type", "")
        if exe_type.startswith("unknown:"):
            warnings.append(
                _issue(
                    "unknown_executable_type",
                    f"Executable '{exe.get('name')}' tiene DTS:ExecutableType sin builder especifico",
                    container=container_name,
                    executable=exe.get("name"),
                    executable_type=exe.get("executable_type"),
                )
            )
        elif exe_type == "execute_sql_task":
            sql = exe.get("sql", {})
            if sql.get("connection_id") and not sql.get("connection_manager_ref_id"):
                warnings.append(
                    _issue(
                        "unresolved_sql_task_connection",
                        f"Execute SQL Task '{exe.get('name')}' referencia un connection GUID "
                        "que no se pudo cruzar contra ningun Connection Manager nombrado "
                        "en los Data Flows del mismo paquete",
                        container=container_name,
                        executable=exe.get("name"),
                        connection_id=sql.get("connection_id"),
                    )
                )
        elif exe_type == "sequence_container":
            nested = validate_control_flow_level(
                exe.get("executables", []),
                exe.get("precedence_constraints", []),
                exe.get("name"),
            )
            errors.extend(nested["errors"])
            warnings.extend(nested["warnings"])

    return {"errors": errors, "warnings": warnings}


def validate_ir(ir: Dict[str, Any]) -> Dict[str, Any]:
    """
    Valida la representacion intermedia completa de un paquete: todos sus
    Data Flows (columnas, lineage, mappings, paths) y todo el arbol de
    Control Flow (executables, containers, precedence constraints). No
    modifica el IR.

    Devuelve {"valid": bool, "errors": [...], "warnings": [...]}, donde
    valid == (len(errors) == 0). Warnings nunca afectan `valid`.
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for data_flow in ir.get("data_flows", []):
        result = validate_data_flow(data_flow)
        for issue in result["errors"]:
            issue.setdefault("context", {})["data_flow"] = data_flow.get("name")
            errors.append(issue)
        for issue in result["warnings"]:
            issue.setdefault("context", {})["data_flow"] = data_flow.get("name")
            warnings.append(issue)

    control_flow = ir.get("control_flow")
    if control_flow is not None:
        cf_result = validate_control_flow_level(
            control_flow.get("executables", []),
            control_flow.get("precedence_constraints", []),
            "Package",
        )
        errors.extend(cf_result["errors"])
        warnings.extend(cf_result["warnings"])

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
