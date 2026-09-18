"""
flat_file_generator.py

Generador de la familia TERADATA_TO_FLAT_FILE (template-teradata-to-flat-file-v1):
Teradata Source -> Flat File Destination, SIN Data Conversion ni Derived Column
en el happy path.

Decision arquitectonica del equipo (ver docs/template_teradata_to_flat_file_v1.md,
"Principio SQL-first"): todo lo que pueda resolverse razonablemente en el SQL de
origen se resuelve alli -- los 4 packages reales auditados (proyecto
MediosDePago) no usan Data Conversion ni Derived Column. Por eso este modulo
NO los soporta y no depende de mapping_planner/derived_planner (mappings y
schema del Flat File son siempre explicitos en el spec, ver
docs/template_teradata_to_flat_file_v1.md, "Mapping Planner"/"Derived Planner").

Reutiliza directamente (mismo criterio que generator/control_flow_generator.py,
que ya importa helpers "privados" de campanias_generator.py como API interna
compartida entre modulos hermanos de generator/): _build_teradata_source,
GeneratorError, _find_component, _find_dataflow_executable, _set_property_text,
_clear_children, _resolve_connection_manager_id, _make_input_column,
_make_external_metadata_column, _rewrite_design_time_properties,
_apply_design_time_ref_id_map, _register_design_time_namespaces. NO reutiliza
_build_dataflow_executable/_rebuild_paths/_update_design_time_properties
(estan formados alrededor de la familia OLE DB -- Data Conversion/Derived
Column/OLE DB Destination -- que no aplica aca) ni el template
templates/campanias_base.dtsx (ver templates/teradata_to_flat_file_base.dtsx,
construido a partir de evidencia real, Examples/Originals/MediosDePago/
DatosContactoProcesadoras.dtsx, con el File System Task y su precedence
constraint removidos -- MoveFile queda fuera de scope, ver
docs/template_teradata_to_flat_file_v1.md, "MoveFile").

NO implementa: File System Task, Script Task, Execute SQL Task, Sequence
Container, precedence constraints -- ninguno tiene evidencia de ser necesario
para el happy path Source->Flat File Destination (ver auditoria, TarjetaDebitoSinUsoLink
es un package real productivo sin ninguno de estos).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .campanias_generator import (
    TERADATA_SOURCE_CLASS_ID,
    GeneratorError,
    _build_teradata_source,
    _clear_children,
    _find_component,
    _find_dataflow_executable,
    _make_external_metadata_column,
    _make_input_column,
    _rewrite_design_time_properties,
)
from .xml_helpers import (
    component_ref,
    connection_ref,
    dataflow_executable_ref,
    dts,
    input_column_ref,
    input_external_column_ref,
    input_ref,
    new_guid,
    output_ref,
    path_ref,
)

FLAT_FILE_DESTINATION_CLASS_ID = "Microsoft.FlatFileDestination"
FLAT_FILE_CONNECTION_CREATION_NAME = "FLATFILE"

# Nombres estructurales FIJOS del template (ver templates/teradata_to_flat_file_base.dtsx,
# construido a partir de Examples/Originals/MediosDePago/DatosContactoProcesadoras.dtsx).
TEMPLATE_DATA_FLOW_NAME = "Genera archivo en local"
TEMPLATE_SOURCE_NAME = "Teradata Source"
TEMPLATE_DESTINATION_NAME = "Destino de archivo plano"
TEMPLATE_FLAT_FILE_CM_NAME = "ffcDatosContactoProcesadoras"

DESTINATION_INPUT_NAME = "Entrada de destino de archivo plano"
DESTINATION_CONNECTION_LOCAL_NAME = "FlatFileConnection"

PATH_1_NAME = "Teradata Source Output"

# Unico token de row/column delimiter evidenciado para columnas "normales"
# (nunca llevan delimiter propio en RaggedRight ni en Delimited -- el
# terminador de fila vive exclusivamente en la ultima columna, ver
# docs/template_teradata_to_flat_file_v1.md, "Ragged Right").
_EMPTY_COLUMN_DELIMITER = ""

# RowDelimiter a nivel Connection Manager: SIEMPRE vacio en los 4 packages
# reales auditados -- el terminador de fila se expresa exclusivamente via el
# ColumnDelimiter de la ultima columna (ColumnType='Delimited'), nunca via
# este atributo. No es un default arbitrario: es el UNICO valor observado,
# se fija en el generador en vez de exponerse como campo del spec.
_CONNECTION_ROW_DELIMITER = ""

# TextQualifier: unico valor evidenciado en los 4 packages -- el literal
# "<none>" (decodificado desde DTS:TextQualifier="_x003C_none_x003E_" en el
# XML real). v1 solo soporta este valor (ver spec_validator.SUPPORTED_TEXT_QUALIFIERS).
_TEXT_QUALIFIER_ENCODED = {
    "none": "_x003C_none_x003E_",
}

# HeaderRowDelimiter: los 2 valores evidenciados en el corpus real
# (BipSuc/MediosDePago) -- ver spec_validator.SUPPORTED_HEADER_ROW_DELIMITERS.
# Se usa el mismo token amigable en el spec y se traduce al escape real de
# SSIS aca, nunca al reves (no se acepta la forma cruda '_x000D__x000A_' en
# el spec -- eso seria aceptar una expresion generica sin estructurar, fuera
# de scope segun docs/template_teradata_to_flat_file_v1.md, "Filename/ConnectionString").
_HEADER_ROW_DELIMITER_ENCODED = {
    "CRLF": "_x000D__x000A_",
    "SEMICOLON": "_x003B_",
}

# ColumnDelimiter de la columna que lleva el terminador de fila (row_terminator):
# SIEMPRE CRLF en los 4 packages reales auditados, INCLUSO en
# TarjetaDebitoSinUsoLink, cuyo HeaderRowDelimiter es ';' -- son dos atributos
# independientes en la evidencia real, nunca el mismo valor por definicion.
# No se expone como campo del spec (cero variacion real).
_ROW_TERMINATOR_COLUMN_DELIMITER_ENCODED = _HEADER_ROW_DELIMITER_ENCODED["CRLF"]

# DataType (FlatFileColumn) -- codigos SSIS estandar, unicos dos evidenciados
# en el corpus (str/wstr, ver docs/template_teradata_to_flat_file_v1.md).
_FLAT_FILE_COLUMN_DATA_TYPE_CODES = {
    "str": "129",
    "wstr": "130",
}

# DataType (Variable) -- codigo SSIS estandar para System.String, unico tipo
# de variable con evidencia real en este milestone (ver
# docs/template_teradata_to_flat_file_v1.md, "Filename/ConnectionString").
_STRING_VARIABLE_DATA_TYPE_CODE = "8"


def _package_connection_manager_ref(name: str) -> str:
    """'Package.ConnectionManagers[<name>]' -- misma formula que
    xml_helpers.connection_manager_ref_id pero para Connection Managers
    PACKAGE-level (Project.ConnectionManagers[...] es para project-level).
    Confirmado contra evidencia real (ver DTS:refId del Flat File CM en los
    4 packages auditados)."""
    return f"Package.ConnectionManagers[{name}]"


def _escape_ssis_expression_string_literal(value: str) -> str:
    """
    Escapa un valor Python para insertarlo como STRING LITERAL dentro de una
    SSIS Expression (DTS:PropertyExpression/DTS:Expression) -- una capa
    completamente distinta de XML escaping (que ya maneja ElementTree al
    serializar el atributo/texto) y de Python escaping (que no aplica aca en
    absoluto).

    SSIS Expression Language usa escaping estilo C: '\\' se escribe '\\\\',
    '"' se escribe '\\"'. NO es escaping estilo SQL (comillas dobladas
    '""') -- ese fue exactamente el bug real detectado en el primer Gate
    SSDT de este milestone (SSDT: "The string literal ... contains an
    illegal escape sequence of '\\S'. If a backslash is needed in the
    string, use a double backslash, '\\\\'."). Confirmado tambien por la
    evidencia real ya auditada: el
    literal real 'MediosDePago\\' (un solo backslash) se serializa en el
    corpus como `"MediosDePago\\\\"` (backslash DUPLICADO) dentro de
    `DTS:PropertyExpression`/`DTS:Expression` de los 4 packages de
    MediosDePago -- ver docs/template_teradata_to_flat_file_v1.md.

    El backslash se escapa PRIMERO (antes que la comilla), para no
    duplicar el backslash que la propia comilla-escapada introduciria.
    No es un parser de expresiones generico: solo maneja los 2 caracteres
    con evidencia real de necesitar escaping ('\\' y '"').
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_filename_expression(filename_spec: Dict[str, Any]) -> str:
    """
    Construye la expresion SSIS de ConnectionString a partir de las 3 partes
    estructuradas soportadas (parameter/literal/variable), concatenadas con
    '+' en ese orden -- unico orden evidenciado en el corpus real (ver
    docs/template_teradata_to_flat_file_v1.md, "Filename/ConnectionString").
    No es un lenguaje generico de expresiones: solo estas 3 formas.

    IMPORTANTE: solo 'literal' pasa por _escape_ssis_expression_string_literal
    (es el UNICO fragmento que se serializa como string literal). Las
    referencias '@[$Project::...]'/'@[User::...]' son sintaxis de
    referencia de SSIS, NUNCA se tratan como string literal ni se escapan
    -- ver docs/template_teradata_to_flat_file_v1.md, "Filename/ConnectionString".
    """
    parts: List[str] = []
    parameter = filename_spec.get("parameter")
    if parameter is not None:
        parts.append(f"@[$Project::{parameter}]")
    literal = filename_spec.get("literal")
    if literal is not None:
        parts.append(f'"{_escape_ssis_expression_string_literal(literal)}"')
    variable = filename_spec.get("variable")
    if variable is not None:
        parts.append(f"@[User::{variable['name']}]")
    return " + ".join(parts)


def _resolve_static_connection_string(
    filename_spec: Dict[str, Any], project_context: Dict[str, Any]
) -> str:
    """
    Valor 'cacheado' de ConnectionString (atributo DTS:ConnectionString del
    Connection Manager, distinto de la DTS:PropertyExpression que lo
    sobrescribe en tiempo de ejecucion -- ver evidencia real, ambos
    coexisten). Es puramente cosmetico/informativo para SSDT: se resuelve
    con la mejor informacion disponible (valor real del project parameter si
    esta en ProjectContext y no es sensible; valor literal declarado en el
    spec; valor de la variable, que el propio spec declara) sin necesidad de
    que sea exacto -- SSIS lo recalcula solo con el PropertyExpression.
    """
    parameters = {p["name"]: p for p in project_context.get("parameters", [])}
    fragments: List[str] = []
    parameter = filename_spec.get("parameter")
    if parameter is not None:
        param_meta = parameters.get(parameter)
        value = param_meta.get("value") if param_meta else None
        fragments.append(value if value is not None else f"<{parameter}>")
    literal = filename_spec.get("literal")
    if literal is not None:
        fragments.append(literal)
    variable = filename_spec.get("variable")
    if variable is not None:
        fragments.append(str(variable.get("value", "")))
    return "".join(fragments)


def _build_string_variable(name: str, value: str) -> ET.Element:
    """
    <DTS:Variable> de tipo string (DataType=8), namespace 'User', valor
    literal (sin DTS:EvaluateAsExpression) -- unica forma de variable de
    package con evidencia real necesaria para el happy path de v1 (ver
    docs/template_teradata_to_flat_file_v1.md, "MoveFile": la variable
    calculada 'pathArchivoDestino' solo la usa el File System Task, fuera de
    scope de este milestone).
    """
    var_el = ET.Element(dts("Variable"))
    var_el.set(dts("CreationName"), "")
    var_el.set(dts("DTSID"), new_guid())
    var_el.set(dts("IncludeInDebugDump"), "2345")
    var_el.set(dts("Namespace"), "User")
    var_el.set(dts("ObjectName"), name)
    value_el = ET.SubElement(var_el, dts("VariableValue"))
    value_el.set(dts("DataType"), _STRING_VARIABLE_DATA_TYPE_CODE)
    value_el.text = value
    return var_el


def _build_flat_file_connection_manager(
    cm_el: ET.Element,
    name: str,
    spec: Dict[str, Any],
    columns: List[Dict[str, Any]],
    project_context: Dict[str, Any],
) -> None:
    """
    Serializa el Flat File Connection Manager package-level, reconstruyendo
    'DTS:ObjectData/DTS:ConnectionManager' y 'DTS:FlatFileColumns' desde
    cero por spec (mismo patron 'clear and rebuild' que
    _build_data_conversion/_build_derived_column). 'spec' es
    data_flow.destination.connection_manager; 'columns' es
    data_flow.destination.columns (UNA sola lista, compartida con
    _build_flat_file_destination -- nunca una segunda declaracion paralela
    del mismo schema, ver docs/template_teradata_to_flat_file_v1.md,
    "Separacion WHAT/WHERE").
    """
    cm_el.set(dts("refId"), _package_connection_manager_ref(name))
    cm_el.set(dts("DTSID"), new_guid())
    cm_el.set(dts("ObjectName"), name)

    _clear_children(cm_el, dts("PropertyExpression"))
    filename_spec = spec["filename"]
    prop_expr = ET.SubElement(cm_el, dts("PropertyExpression"))
    prop_expr.set(dts("Name"), "ConnectionString")
    prop_expr.text = _build_filename_expression(filename_spec)

    object_data = cm_el.find(dts("ObjectData"))
    inner_cm = object_data.find(dts("ConnectionManager"))

    format_map = {"ragged_right": "RaggedRight", "delimited": "Delimited"}
    inner_cm.set(dts("Format"), format_map[spec["format"]])
    inner_cm.set(dts("LocaleID"), str(spec["locale_id"]))
    inner_cm.set(
        dts("HeaderRowDelimiter"),
        _HEADER_ROW_DELIMITER_ENCODED[spec["header_row_delimiter"]],
    )
    inner_cm.set(dts("RowDelimiter"), _CONNECTION_ROW_DELIMITER)
    inner_cm.set(
        dts("TextQualifier"), _TEXT_QUALIFIER_ENCODED[spec["text_qualifier"]]
    )
    inner_cm.set(dts("CodePage"), str(spec["code_page"]))
    inner_cm.set(
        dts("ConnectionString"),
        _resolve_static_connection_string(filename_spec, project_context),
    )

    flat_file_columns_el = inner_cm.find(dts("FlatFileColumns"))
    _clear_children(flat_file_columns_el, dts("FlatFileColumn"))

    for col in columns:
        is_terminator = bool(col.get("row_terminator"))
        col_el = ET.SubElement(flat_file_columns_el, dts("FlatFileColumn"))
        if is_terminator:
            col_el.set(dts("ColumnType"), "Delimited")
            col_el.set(dts("ColumnDelimiter"), _ROW_TERMINATOR_COLUMN_DELIMITER_ENCODED)
        else:
            col_el.set(dts("ColumnDelimiter"), _EMPTY_COLUMN_DELIMITER)
        target_length = col.get("target_length")
        if target_length is not None:
            if not is_terminator:
                col_el.set(dts("ColumnWidth"), str(target_length))
            col_el.set(dts("MaximumWidth"), str(target_length))
        # Columna sentinel pura (row_terminator=true sin datos reales, ej.
        # 'EndLine'): igual necesita DataType -- SSIS lo exige aunque la
        # columna no tenga ancho. 'str' es el UNICO valor evidenciado para
        # este caso (EndLine en DatosContactoProcesadoras/Semanal).
        col_data_type = col.get("target_data_type", "str")
        col_el.set(dts("DataType"), _FLAT_FILE_COLUMN_DATA_TYPE_CODES[col_data_type])
        col_el.set(dts("TextQualified"), "True")
        col_el.set(dts("ObjectName"), col["target"])
        col_el.set(dts("DTSID"), new_guid())
        col_el.set(dts("CreationName"), "")


def _build_flat_file_destination(
    component_el: ET.Element,
    data_flow: str,
    name: str,
    connection_manager_name: str,
    spec: Dict[str, Any],
    pipeline_columns: Dict[str, Dict[str, Any]],
) -> None:
    """
    Serializa Microsoft.FlatFileDestination. A diferencia de OLE DB
    Destination/Data Conversion/Derived Column, este componente NO tiene
    'usesDispositions', NO tiene <outputs> (ni normal ni de error) -- ver
    docs/template_teradata_to_flat_file_v1.md, evidencia real (4/4
    packages). 'spec' es data_flow.destination.columns[] ya validado por
    spec_validator.py (nombres unicos, target_data_type en {str,wstr},
    target_length positivo, exactamente 1 columna con row_terminator=true al
    final de la lista).
    """
    component_el.set("refId", component_ref(data_flow, name))
    component_el.set("name", name)
    component_el.set("description", name)

    connection_el = component_el.find("connections/connection")
    connection_el.set("refId", connection_ref(data_flow, name, DESTINATION_CONNECTION_LOCAL_NAME))
    package_cm_ref = _package_connection_manager_ref(connection_manager_name)
    connection_el.set("connectionManagerID", package_cm_ref)
    connection_el.set("connectionManagerRefId", package_cm_ref)

    input_el = component_el.find("inputs/input")
    input_el.set("refId", input_ref(data_flow, name, DESTINATION_INPUT_NAME))
    input_columns_el = input_el.find("inputColumns")
    external_columns_el = input_el.find("externalMetadataColumns")
    _clear_children(input_columns_el, "inputColumn")
    _clear_children(external_columns_el, "externalMetadataColumn")

    for col in spec:
        target_name = col["target"]
        source_name = col.get("source")
        is_pure_sentinel = (
            bool(col.get("row_terminator"))
            and source_name is None
            and col.get("target_data_type") is None
            and col.get("target_length") is None
        )
        if is_pure_sentinel:
            # Columna sentinel pura (ej. 'EndLine'): su externalMetadataColumn
            # SI lleva metadata, con los defaults evidenciados en
            # BipSuc/MediosDePago para este caso especifico (data_type='str',
            # length=255, code_page=1252 -- valores que SSDT asigna a una
            # columna sin ancho fisico propio). NO se aplican a columnas
            # normales: una columna wstr real, por ejemplo, evidencia
            # code_page ausente (None), nunca 1252 (ver Tokenizacion).
            target_type = "str"
            target_length = 255
            target_code_page = 1252
        else:
            target_type = col["target_data_type"]
            target_length = col.get("target_length")
            target_code_page = col.get("target_code_page")

        ext_ref = input_external_column_ref(data_flow, name, DESTINATION_INPUT_NAME, target_name)
        external_columns_el.append(
            _make_external_metadata_column(
                ext_ref, target_name, target_type,
                length=target_length, code_page=target_code_page,
            )
        )

        if source_name is None:
            # Columna sentinel (row_terminator sin datos reales, ej. 'EndLine'):
            # solo existe como externalMetadataColumn/FlatFileColumn -- SIN
            # inputColumn correspondiente (asimetria confirmada en evidencia
            # real: 39 inputColumns vs 40 externalMetadataColumns).
            continue

        source_meta = pipeline_columns.get(source_name)
        if source_meta is None:
            raise GeneratorError(
                f"Flat File Destination: mapping.source '{source_name}' no esta "
                "registrado en el pipeline (deberia haber sido detectado por "
                "spec_validator)."
            )

        in_ref = input_column_ref(data_flow, name, DESTINATION_INPUT_NAME, source_name)
        input_columns_el.append(
            _make_input_column(
                in_ref,
                source_name,
                source_meta["data_type"],
                cached_length=source_meta.get("length"),
                cached_code_page=source_meta.get("code_page"),
                cached_precision=source_meta.get("precision"),
                cached_scale=source_meta.get("scale"),
                lineage_id=source_meta["lineage_id"],
                external_metadata_column_id=ext_ref,
            )
        )


def _rebuild_flat_file_paths(pipeline_el: ET.Element, data_flow: str, source_name: str, destination_name: str) -> None:
    """Topologia fija de v1: Source -> Flat File Destination, un unico path
    (ver docs/template_teradata_to_flat_file_v1.md -- sin Data
    Conversion/Derived Column en el happy path SQL-first)."""
    paths_el = pipeline_el.find("paths")
    _clear_children(paths_el, "path")

    path_el = ET.SubElement(paths_el, "path")
    path_el.set("refId", path_ref(data_flow, PATH_1_NAME))
    path_el.set("name", PATH_1_NAME)
    path_el.set("startId", output_ref(data_flow, source_name, PATH_1_NAME))
    path_el.set("endId", input_ref(data_flow, destination_name, DESTINATION_INPUT_NAME))


def _update_flat_file_design_time_properties(
    root: ET.Element,
    *,
    old_data_flow: str,
    old_source: str,
    old_destination: str,
    new_data_flow: str,
    new_source: str,
    new_destination: str,
) -> None:
    """Analogo a campanias_generator._update_design_time_properties, pero
    para la topologia fija de esta familia (sin Data Conversion/Derived
    Column que remover)."""
    dtp_el = root.find(dts("DesignTimeProperties"))
    if dtp_el is None or dtp_el.text is None:
        return

    ref_id_map = {
        dataflow_executable_ref(old_data_flow): dataflow_executable_ref(new_data_flow),
        component_ref(old_data_flow, old_source): component_ref(new_data_flow, new_source),
        component_ref(old_data_flow, old_destination): component_ref(new_data_flow, new_destination),
        path_ref(old_data_flow, PATH_1_NAME): path_ref(new_data_flow, PATH_1_NAME),
    }
    dtp_el.text = _rewrite_design_time_properties(dtp_el.text, ref_id_map, set())


def build_flat_file_package_tree(
    spec: Dict[str, Any], project_context: Dict[str, Any], template_path: str
) -> ET.Element:
    """
    Construye el arbol XML completo de un paquete TERADATA_TO_FLAT_FILE.
    Analogo a campanias_generator.build_package_tree, pero para la familia
    Flat File (Connection Manager package-level + Flat File Destination en
    vez de Data Conversion/Derived Column/OLE DB Destination). Asume que el
    spec YA fue validado (ver generate() en campanias_generator.py).
    """
    package_name = spec["package"]["name"]
    data_flow = spec["data_flow"]
    data_flow_name = data_flow["name"]
    source_spec = data_flow["source"]
    destination_spec = data_flow["destination"]
    destination_name = destination_spec["name"]
    cm_spec = destination_spec["connection_manager"]
    cm_name = cm_spec["name"]

    tree = ET.parse(template_path)
    root = tree.getroot()

    root.set(dts("ObjectName"), package_name)
    root.set(dts("DTSID"), new_guid())
    root.set(dts("VersionGUID"), new_guid())

    # --- Connection Manager package-level (Flat File) ---
    cm_el = _find_flat_file_connection_manager(root)
    _build_flat_file_connection_manager(
        cm_el, cm_name, cm_spec, destination_spec["columns"], project_context
    )

    # --- Variable de package para el filename (opcional, ver evidencia) ---
    variables_el = root.find(dts("Variables"))
    _clear_children(variables_el, dts("Variable"))
    filename_variable = cm_spec["filename"].get("variable")
    if filename_variable is not None:
        variables_el.append(
            _build_string_variable(filename_variable["name"], filename_variable["value"])
        )

    # --- Data Flow Task Executable (unico) ---
    dataflow_exe = _find_dataflow_executable(root)
    dataflow_exe.set(dts("refId"), dataflow_executable_ref(data_flow_name))
    dataflow_exe.set(dts("ObjectName"), data_flow_name)
    dataflow_exe.set(dts("Description"), data_flow_name)
    dataflow_exe.set(dts("DTSID"), new_guid())

    object_data = dataflow_exe.find(dts("ObjectData"))
    pipeline_el = object_data.find("pipeline")
    components_el = pipeline_el.find("components")

    teradata_el = _find_component(components_el, TERADATA_SOURCE_CLASS_ID)
    destination_el = _find_component(components_el, FLAT_FILE_DESTINATION_CLASS_ID)

    source_name = source_spec["name"]
    pipeline_columns: Dict[str, Dict[str, Any]] = {}
    _build_teradata_source(
        teradata_el, data_flow_name, source_name, source_spec, pipeline_columns, project_context
    )

    _build_flat_file_destination(
        destination_el,
        data_flow_name,
        destination_name,
        cm_name,
        destination_spec["columns"],
        pipeline_columns,
    )

    _rebuild_flat_file_paths(pipeline_el, data_flow_name, source_name, destination_name)

    _update_flat_file_design_time_properties(
        root,
        old_data_flow=TEMPLATE_DATA_FLOW_NAME,
        old_source=TEMPLATE_SOURCE_NAME,
        old_destination=TEMPLATE_DESTINATION_NAME,
        new_data_flow=data_flow_name,
        new_source=source_name,
        new_destination=destination_name,
    )

    return root


def _find_flat_file_connection_manager(root: ET.Element) -> ET.Element:
    for cm in root.findall(f"{dts('ConnectionManagers')}/{dts('ConnectionManager')}"):
        if cm.get(dts("CreationName")) == FLAT_FILE_CONNECTION_CREATION_NAME:
            return cm
    raise GeneratorError(
        "El template no contiene un Connection Manager package-level con "
        f"CreationName={FLAT_FILE_CONNECTION_CREATION_NAME!r}."
    )
