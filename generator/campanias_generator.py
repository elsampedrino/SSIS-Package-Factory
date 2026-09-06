"""
campanias_generator.py

Generador MVP: process_spec (funcional, limpio) -> .dtsx (SSIS real), tomando
como base templates/campanias_base.dtsx (copia exacta de
BipSuc_CampaniasVigentes.dtsx, un paquete real y validado).

Estrategia (ver docs/generator_mvp.md): template validado + modificacion
programatica sobre arbol ElementTree. Nunca string-replace ciego sobre el
XML funcional — la unica excepcion, deliberada y documentada, es el bloque
DTS:DesignTimeProperties (puro layout, sin impacto funcional segun el propio
comentario del archivo), donde se hace una sustitucion de texto acotada
usando exactamente las mismas cadenas de refId ya calculadas para el resto
del documento.

Alcance: EXCLUSIVAMENTE la topologia Teradata Source -> Data Conversion ->
OLE DB Destination. No soporta Sequence Containers, Execute SQL Task,
transacciones, staging, Merge Join, Conditional Split, Row Count ni OLE DB
Source como origen.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from project_context.validator import get_connection_dtsid

from .spec_validator import assert_valid_spec
from .xml_helpers import (
    NS,
    connection_manager_ref_id,
    connection_ref,
    dataflow_executable_ref,
    dts,
    component_ref,
    format_connection_manager_id,
    id_reference_wrapper,
    input_column_ref,
    input_external_column_ref,
    input_ref,
    new_guid,
    output_column_ref,
    output_external_column_ref,
    output_ref,
    path_ref,
)

# componentClassID: duplicados a proposito de COMPONENT_TYPE_MAP de
# ssis_parser.py — el generador no depende en tiempo de ejecucion del
# analizador (ver generator/__init__.py).
TERADATA_SOURCE_CLASS_ID = "Microsoft.SSISTeradataSrc"
DATA_CONVERSION_CLASS_ID = "Microsoft.DataConvert"
OLE_DB_DESTINATION_CLASS_ID = "Microsoft.OLEDBDestination"

# Nombres estructurales FIJOS del template (sub-etiquetas de input/output que
# el process_spec no expone — ver docs/generator_mvp.md, "Estructura de
# process_spec propuesta"). Solo package.name/data_flow.name y el 'name' de
# cada componente (source/transformation/destination) vienen del spec.
TEMPLATE_DATA_FLOW_NAME = "Tarea Flujo de datos"
TEMPLATE_SOURCE_NAME = "Teradata Source"
TEMPLATE_CONVERSION_NAME = "Conversión de datos"
TEMPLATE_DESTINATION_NAME = "Destino de OLE DB"

SOURCE_OUTPUT_NAME = "Teradata Source Output"
SOURCE_ERROR_OUTPUT_NAME = "Teradata Source Error Output"
SOURCE_CONNECTION_LOCAL_NAME = "TeradataConnection"

CONVERSION_INPUT_NAME = "Entrada de conversión de datos"
CONVERSION_OUTPUT_NAME = "Salida de conversión de datos"
CONVERSION_ERROR_OUTPUT_NAME = "Salida de error de conversión de datos"

DESTINATION_INPUT_NAME = "Entrada de destino de OLE DB"
DESTINATION_ERROR_OUTPUT_NAME = "Salida de error de destino de OLE DB"
DESTINATION_CONNECTION_LOCAL_NAME = "OleDbConnection"

PATH_1_NAME = SOURCE_OUTPUT_NAME
PATH_2_NAME = CONVERSION_OUTPUT_NAME


class GeneratorError(Exception):
    """Error del generador que no es de validacion de spec (template
    inesperado, referencia interna faltante, etc.)."""


# ---------------------------------------------------------------------------
# Helpers de arbol
# ---------------------------------------------------------------------------
def _find_component(components_el: ET.Element, class_id: str) -> ET.Element:
    for comp in components_el.findall("component"):
        if comp.get("componentClassID") == class_id:
            return comp
    raise GeneratorError(
        f"El template no contiene un componente con componentClassID={class_id!r}."
    )


def _find_dataflow_executable(root: ET.Element) -> ET.Element:
    for exe in root.iter(dts("Executable")):
        if exe.get(dts("ExecutableType")) == "Microsoft.Pipeline":
            return exe
    raise GeneratorError("El template no contiene ningun Data Flow Task (Microsoft.Pipeline).")


def _find_output(outputs_el: ET.Element, *, is_error: bool) -> ET.Element:
    for output_el in outputs_el.findall("output"):
        if (output_el.get("isErrorOut") == "true") == is_error:
            return output_el
    raise GeneratorError(
        f"El template no tiene un <output isErrorOut={'true' if is_error else 'false/ausente'}>."
    )


def _set_property_text(properties_el: ET.Element, name: str, value: str) -> None:
    for prop in properties_el.findall("property"):
        if prop.get("name") == name:
            prop.text = value
            return
    raise GeneratorError(f"El template no tiene una <property name={name!r}>.")


def _clear_children(parent_el: ET.Element, tag: str) -> None:
    for child in list(parent_el.findall(tag)):
        parent_el.remove(child)


def _resolve_connection_manager_id(project_context: Dict[str, Any], name: str) -> str:
    """
    connectionManagerID completo ('{GUID}:external') para un Connection
    Manager de proyecto, resuelto vía ProjectContext (ver
    project_context.validator.get_connection_dtsid) en vez del diccionario
    hardcodeado que este módulo usaba antes de project-context-v1.

    spec_validator.assert_valid_spec() ya debería haber confirmado que
    'name' existe en project_context — si igual falla acá, es una
    inconsistencia interna, no un problema del spec del usuario.
    """
    try:
        dtsid = get_connection_dtsid(project_context, name)
    except KeyError as exc:
        raise GeneratorError(
            f"Connection Manager '{name}' no se pudo resolver en ProjectContext "
            "(deberia haber sido detectado por spec_validator)."
        ) from exc
    return format_connection_manager_id(dtsid)


# ---------------------------------------------------------------------------
# Constructores de elementos (columnas)
# ---------------------------------------------------------------------------
def _make_output_column(
    ref_id: str,
    name: str,
    data_type: str,
    *,
    length: Optional[int] = None,
    code_page: Optional[int] = None,
    lineage_id: Optional[str] = None,
    external_metadata_column_id: Optional[str] = None,
    special_flags: Optional[str] = None,
    error_row_disposition: Optional[str] = None,
    truncation_row_disposition: Optional[str] = None,
    error_or_truncation_operation: Optional[str] = None,
) -> ET.Element:
    el = ET.Element("outputColumn")
    el.set("refId", ref_id)
    el.set("dataType", data_type)
    if error_or_truncation_operation is not None:
        el.set("errorOrTruncationOperation", error_or_truncation_operation)
    if error_row_disposition is not None:
        el.set("errorRowDisposition", error_row_disposition)
    if external_metadata_column_id is not None:
        el.set("externalMetadataColumnId", external_metadata_column_id)
    if length is not None:
        el.set("length", str(length))
    if code_page is not None:
        el.set("codePage", str(code_page))
    if lineage_id is not None:
        el.set("lineageId", lineage_id)
    el.set("name", name)
    if special_flags is not None:
        el.set("specialFlags", special_flags)
    if truncation_row_disposition is not None:
        el.set("truncationRowDisposition", truncation_row_disposition)
    return el


def _make_external_metadata_column(
    ref_id: str,
    name: str,
    data_type: str,
    *,
    length: Optional[int] = None,
    code_page: Optional[int] = None,
) -> ET.Element:
    el = ET.Element("externalMetadataColumn")
    el.set("refId", ref_id)
    el.set("dataType", data_type)
    if length is not None:
        el.set("length", str(length))
    if code_page is not None:
        el.set("codePage", str(code_page))
    el.set("name", name)
    return el


def _make_input_column(
    ref_id: str,
    cached_name: str,
    cached_data_type: str,
    *,
    cached_length: Optional[int] = None,
    cached_code_page: Optional[int] = None,
    lineage_id: str,
    external_metadata_column_id: Optional[str] = None,
) -> ET.Element:
    el = ET.Element("inputColumn")
    el.set("refId", ref_id)
    if cached_code_page is not None:
        el.set("cachedCodepage", str(cached_code_page))
    el.set("cachedDataType", cached_data_type)
    if cached_length is not None:
        el.set("cachedLength", str(cached_length))
    el.set("cachedName", cached_name)
    if external_metadata_column_id is not None:
        el.set("externalMetadataColumnId", external_metadata_column_id)
    el.set("lineageId", lineage_id)
    return el


def _make_error_columns(data_flow: str, component: str, output: str) -> List[ET.Element]:
    """ErrorCode/ErrorColumn — mismo par en todo componente con
    usesDispositions='true', confirmado en los dos archivos de referencia."""
    columns = []
    for name, flag in (("ErrorCode", "1"), ("ErrorColumn", "2")):
        ref = output_column_ref(data_flow, component, output, name)
        columns.append(
            _make_output_column(ref, name, "i4", lineage_id=ref, special_flags=flag)
        )
    return columns


# ---------------------------------------------------------------------------
# DesignTimeProperties — ver docstring del modulo (excepcion documentada)
# ---------------------------------------------------------------------------
_ANNOTATION_LAYOUT_PATTERN = re.compile(r"\s*<AnnotationLayout\b[^>]*/>")


def _rewrite_design_time_properties(text: str, ref_id_map: Dict[str, str]) -> str:
    """
    Sustituye, dentro del texto embebido de DTS:DesignTimeProperties, cada
    aparicion EXACTA de un refId viejo por su equivalente nuevo. No es
    string-replace "ciego": las claves son cadenas de refId especificas ya
    calculadas para el resto del documento (no patrones genericos), y se
    reemplazan de mas larga a mas corta para evitar colisiones de prefijo.
    Ademas elimina cualquier <AnnotationLayout/> (comentario visual del
    autor original del template, no aplicable a un paquete generado).
    """
    result = text
    for old_ref in sorted(ref_id_map, key=len, reverse=True):
        new_ref = ref_id_map[old_ref]
        if old_ref != new_ref:
            result = result.replace(old_ref, new_ref)
    result = _ANNOTATION_LAYOUT_PATTERN.sub("", result)
    return result


# ---------------------------------------------------------------------------
# Generacion
# ---------------------------------------------------------------------------
def build_package_tree(
    spec: Dict[str, Any], project_context: Dict[str, Any], template_path: str
) -> ET.Element:
    """Construye el arbol XML completo del paquete generado. No escribe a
    disco (ver generate() para eso). Asume que el spec YA fue validado
    contra project_context (ver generate())."""
    package_name = spec["package"]["name"]
    data_flow = spec["data_flow"]
    data_flow_name = data_flow["name"]
    source_spec = data_flow["source"]
    transform_spec = data_flow["transformations"][0]
    destination_spec = data_flow["destination"]

    source_name = source_spec["name"]
    conversion_name = transform_spec["name"]
    destination_name = destination_spec["name"]

    tree = ET.parse(template_path)
    root = tree.getroot()

    # --- Package ---
    root.set(dts("ObjectName"), package_name)
    root.set(dts("DTSID"), new_guid())
    root.set(dts("VersionGUID"), new_guid())

    # --- Data Flow Task Executable ---
    dataflow_exe = _find_dataflow_executable(root)
    dataflow_exe.set(dts("refId"), dataflow_executable_ref(data_flow_name))
    dataflow_exe.set(dts("ObjectName"), data_flow_name)
    dataflow_exe.set(dts("Description"), data_flow_name)
    dataflow_exe.set(dts("DTSID"), new_guid())

    object_data = dataflow_exe.find(dts("ObjectData"))
    pipeline_el = object_data.find("pipeline")
    components_el = pipeline_el.find("components")

    teradata_el = _find_component(components_el, TERADATA_SOURCE_CLASS_ID)
    conversion_el = _find_component(components_el, DATA_CONVERSION_CLASS_ID)
    destination_el = _find_component(components_el, OLE_DB_DESTINATION_CLASS_ID)

    # Registro de columnas del pipeline: nombre -> metadata (para resolver
    # lineageId/tipo cacheado desde cualquier componente aguas abajo). Se
    # completa a medida que se generan Teradata Source y Data Conversion.
    pipeline_columns: Dict[str, Dict[str, Any]] = {}

    _build_teradata_source(
        teradata_el, data_flow_name, source_name, source_spec, pipeline_columns, project_context
    )
    _build_data_conversion(
        conversion_el, data_flow_name, conversion_name, transform_spec, pipeline_columns
    )
    _build_ole_db_destination(
        destination_el,
        data_flow_name,
        destination_name,
        destination_spec,
        pipeline_columns,
        project_context,
    )

    _rebuild_paths(
        pipeline_el, data_flow_name, source_name, conversion_name, destination_name
    )

    _update_design_time_properties(
        root,
        old_data_flow=TEMPLATE_DATA_FLOW_NAME,
        old_source=TEMPLATE_SOURCE_NAME,
        old_conversion=TEMPLATE_CONVERSION_NAME,
        old_destination=TEMPLATE_DESTINATION_NAME,
        new_data_flow=data_flow_name,
        new_source=source_name,
        new_conversion=conversion_name,
        new_destination=destination_name,
    )

    return root


def _build_teradata_source(
    component_el: ET.Element,
    data_flow: str,
    name: str,
    spec: Dict[str, Any],
    pipeline_columns: Dict[str, Dict[str, Any]],
    project_context: Dict[str, Any],
) -> None:
    component_el.set("refId", component_ref(data_flow, name))
    component_el.set("name", name)
    component_el.set("description", name)

    properties_el = component_el.find("properties")
    _set_property_text(properties_el, "SqlCommand", spec["sql"])

    connection_el = component_el.find("connections/connection")
    connection_el.set("refId", connection_ref(data_flow, name, SOURCE_CONNECTION_LOCAL_NAME))
    connection_el.set(
        "connectionManagerID", _resolve_connection_manager_id(project_context, spec["connection"])
    )
    connection_el.set("connectionManagerRefId", connection_manager_ref_id(spec["connection"]))

    outputs_el = component_el.find("outputs")
    normal_output = _find_output(outputs_el, is_error=False)
    error_output = _find_output(outputs_el, is_error=True)

    normal_output.set("refId", output_ref(data_flow, name, SOURCE_OUTPUT_NAME))
    error_output.set("refId", output_ref(data_flow, name, SOURCE_ERROR_OUTPUT_NAME))

    output_columns_el = normal_output.find("outputColumns")
    external_columns_el = normal_output.find("externalMetadataColumns")
    _clear_children(output_columns_el, "outputColumn")
    _clear_children(external_columns_el, "externalMetadataColumn")

    error_output_columns_el = error_output.find("outputColumns")
    _clear_children(error_output_columns_el, "outputColumn")

    for col in spec["columns"]:
        col_name = col["name"]
        data_type = col["data_type"]
        length = col.get("length")
        code_page = col.get("code_page")

        out_ref = output_column_ref(data_flow, name, SOURCE_OUTPUT_NAME, col_name)
        ext_ref = output_external_column_ref(data_flow, name, SOURCE_OUTPUT_NAME, col_name)

        output_columns_el.append(
            _make_output_column(
                out_ref,
                col_name,
                data_type,
                length=length,
                code_page=code_page,
                lineage_id=out_ref,
                external_metadata_column_id=ext_ref,
            )
        )
        external_columns_el.append(
            _make_external_metadata_column(ext_ref, col_name, data_type, length=length, code_page=code_page)
        )

        error_ref = output_column_ref(data_flow, name, SOURCE_ERROR_OUTPUT_NAME, col_name)
        error_output_columns_el.append(
            _make_output_column(
                error_ref, col_name, data_type, length=length, code_page=code_page, lineage_id=error_ref
            )
        )

        pipeline_columns[col_name] = {
            "lineage_id": out_ref,
            "data_type": data_type,
            "length": length,
            "code_page": code_page,
        }

    for err_col in _make_error_columns(data_flow, name, SOURCE_ERROR_OUTPUT_NAME):
        error_output_columns_el.append(err_col)


def _build_data_conversion(
    component_el: ET.Element,
    data_flow: str,
    name: str,
    spec: Dict[str, Any],
    pipeline_columns: Dict[str, Dict[str, Any]],
) -> None:
    component_el.set("refId", component_ref(data_flow, name))
    component_el.set("name", name)
    component_el.set("description", name)

    input_el = component_el.find("inputs/input")
    input_el.set("refId", input_ref(data_flow, name, CONVERSION_INPUT_NAME))
    input_columns_el = input_el.find("inputColumns")
    _clear_children(input_columns_el, "inputColumn")

    outputs_el = component_el.find("outputs")
    normal_output = _find_output(outputs_el, is_error=False)
    error_output = _find_output(outputs_el, is_error=True)
    normal_output.set("refId", output_ref(data_flow, name, CONVERSION_OUTPUT_NAME))
    normal_output.set("synchronousInputId", input_el.get("refId"))
    error_output.set("refId", output_ref(data_flow, name, CONVERSION_ERROR_OUTPUT_NAME))
    error_output.set("synchronousInputId", input_el.get("refId"))

    output_columns_el = normal_output.find("outputColumns")
    _clear_children(output_columns_el, "outputColumn")
    error_output_columns_el = error_output.find("outputColumns")
    _clear_children(error_output_columns_el, "outputColumn")

    for conv in spec["conversions"]:
        input_name = conv["input"]
        output_name = conv["output"]
        target_type = conv["target_type"]

        source_meta = pipeline_columns.get(input_name)
        if source_meta is None:
            raise GeneratorError(
                f"Data Conversion: input '{input_name}' no esta registrado en el "
                "pipeline (deberia haber sido detectado por spec_validator)."
            )

        in_ref = input_column_ref(data_flow, name, CONVERSION_INPUT_NAME, input_name)
        input_columns_el.append(
            _make_input_column(
                in_ref,
                input_name,
                source_meta["data_type"],
                cached_length=source_meta.get("length"),
                cached_code_page=source_meta.get("code_page"),
                lineage_id=source_meta["lineage_id"],
            )
        )

        out_ref = output_column_ref(data_flow, name, CONVERSION_OUTPUT_NAME, output_name)
        out_col = _make_output_column(
            out_ref,
            output_name,
            target_type,
            lineage_id=out_ref,
            error_or_truncation_operation="Conversión",
            error_row_disposition="FailComponent",
            truncation_row_disposition="FailComponent",
        )
        properties_el = ET.SubElement(out_col, "properties")
        source_prop = ET.SubElement(properties_el, "property")
        source_prop.set("containsID", "true")
        source_prop.set("dataType", "System.Int32")
        source_prop.set(
            "description",
            "Especifica la columna de entrada usada como origen de datos para la conversión.",
        )
        source_prop.set("name", "SourceInputColumnLineageID")
        source_prop.text = id_reference_wrapper(source_meta["lineage_id"])

        fast_parse_prop = ET.SubElement(properties_el, "property")
        fast_parse_prop.set("dataType", "System.Boolean")
        fast_parse_prop.set(
            "description",
            "Indica si la columna usa las rutinas de análisis más rápidas independientes "
            "de la configuración regional.",
        )
        fast_parse_prop.set("name", "FastParse")
        fast_parse_prop.text = "false"

        output_columns_el.append(out_col)

        pipeline_columns[output_name] = {
            "lineage_id": out_ref,
            "data_type": target_type,
            "length": None,
            "code_page": None,
        }

    for err_col in _make_error_columns(data_flow, name, CONVERSION_ERROR_OUTPUT_NAME):
        error_output_columns_el.append(err_col)


def _build_ole_db_destination(
    component_el: ET.Element,
    data_flow: str,
    name: str,
    spec: Dict[str, Any],
    pipeline_columns: Dict[str, Dict[str, Any]],
    project_context: Dict[str, Any],
) -> None:
    component_el.set("refId", component_ref(data_flow, name))
    component_el.set("name", name)
    component_el.set("description", name)

    properties_el = component_el.find("properties")
    _set_property_text(properties_el, "OpenRowset", spec["table"])

    connection_el = component_el.find("connections/connection")
    connection_el.set(
        "refId", connection_ref(data_flow, name, DESTINATION_CONNECTION_LOCAL_NAME)
    )
    connection_el.set(
        "connectionManagerID", _resolve_connection_manager_id(project_context, spec["connection"])
    )
    connection_el.set("connectionManagerRefId", connection_manager_ref_id(spec["connection"]))

    input_el = component_el.find("inputs/input")
    input_el.set("refId", input_ref(data_flow, name, DESTINATION_INPUT_NAME))
    input_columns_el = input_el.find("inputColumns")
    external_columns_el = input_el.find("externalMetadataColumns")
    _clear_children(input_columns_el, "inputColumn")
    _clear_children(external_columns_el, "externalMetadataColumn")

    for mapping in spec["mappings"]:
        source_name = mapping["source"]
        target_name = mapping["target"]
        target_type = mapping["target_data_type"]
        target_length = mapping.get("target_length")
        target_code_page = mapping.get("target_code_page")

        source_meta = pipeline_columns.get(source_name)
        if source_meta is None:
            raise GeneratorError(
                f"OLE DB Destination: mapping.source '{source_name}' no esta registrado "
                "en el pipeline (deberia haber sido detectado por spec_validator)."
            )

        ext_ref = input_external_column_ref(data_flow, name, DESTINATION_INPUT_NAME, target_name)
        in_ref = input_column_ref(data_flow, name, DESTINATION_INPUT_NAME, source_name)

        input_columns_el.append(
            _make_input_column(
                in_ref,
                source_name,
                source_meta["data_type"],
                cached_length=source_meta.get("length"),
                cached_code_page=source_meta.get("code_page"),
                lineage_id=source_meta["lineage_id"],
                external_metadata_column_id=ext_ref,
            )
        )
        external_columns_el.append(
            _make_external_metadata_column(
                ext_ref, target_name, target_type, length=target_length, code_page=target_code_page
            )
        )

    outputs_el = component_el.find("outputs")
    error_output = _find_output(outputs_el, is_error=True)
    error_output.set("refId", output_ref(data_flow, name, DESTINATION_ERROR_OUTPUT_NAME))
    error_output.set("synchronousInputId", input_el.get("refId"))
    error_output_columns_el = error_output.find("outputColumns")
    _clear_children(error_output_columns_el, "outputColumn")
    for err_col in _make_error_columns(data_flow, name, DESTINATION_ERROR_OUTPUT_NAME):
        error_output_columns_el.append(err_col)


def _rebuild_paths(
    pipeline_el: ET.Element,
    data_flow: str,
    source_name: str,
    conversion_name: str,
    destination_name: str,
) -> None:
    paths_el = pipeline_el.find("paths")
    _clear_children(paths_el, "path")

    path1 = ET.Element("path")
    path1.set("refId", path_ref(data_flow, PATH_1_NAME))
    path1.set("endId", input_ref(data_flow, conversion_name, CONVERSION_INPUT_NAME))
    path1.set("name", PATH_1_NAME)
    path1.set("startId", output_ref(data_flow, source_name, SOURCE_OUTPUT_NAME))
    paths_el.append(path1)

    path2 = ET.Element("path")
    path2.set("refId", path_ref(data_flow, PATH_2_NAME))
    path2.set("endId", input_ref(data_flow, destination_name, DESTINATION_INPUT_NAME))
    path2.set("name", PATH_2_NAME)
    path2.set("startId", output_ref(data_flow, conversion_name, CONVERSION_OUTPUT_NAME))
    paths_el.append(path2)


def _update_design_time_properties(
    root: ET.Element,
    *,
    old_data_flow: str,
    old_source: str,
    old_conversion: str,
    old_destination: str,
    new_data_flow: str,
    new_source: str,
    new_conversion: str,
    new_destination: str,
) -> None:
    dtp_el = root.find(dts("DesignTimeProperties"))
    if dtp_el is None or dtp_el.text is None:
        return

    ref_id_map = {
        dataflow_executable_ref(old_data_flow): dataflow_executable_ref(new_data_flow),
        component_ref(old_data_flow, old_source): component_ref(new_data_flow, new_source),
        component_ref(old_data_flow, old_conversion): component_ref(new_data_flow, new_conversion),
        component_ref(old_data_flow, old_destination): component_ref(new_data_flow, new_destination),
        path_ref(old_data_flow, PATH_1_NAME): path_ref(new_data_flow, PATH_1_NAME),
        path_ref(old_data_flow, PATH_2_NAME): path_ref(new_data_flow, PATH_2_NAME),
    }
    dtp_el.text = _rewrite_design_time_properties(dtp_el.text, ref_id_map)


def generate(
    spec: Dict[str, Any],
    project_context: Dict[str, Any],
    template_path: str,
    output_path: str,
) -> None:
    """
    Punto de entrada del generador. Valida el spec contra project_context
    (fail-fast, antes de tocar el template), construye el arbol y lo escribe
    en output_path.

    project_context: dict devuelto por
    project_context.context_builder.build_project_context(...) para el
    proyecto SSIS real al que este paquete se va a agregar (Modo A — ver
    docs/project_context.md). Define QUÉ Connection Managers existen y con
    qué provider; el spec los referencia por nombre
    ('source.connection'/'destination.connection') y nunca por GUID.
    """
    assert_valid_spec(spec, project_context)

    ET.register_namespace("DTS", NS["DTS"])

    root = build_package_tree(spec, project_context, template_path)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
