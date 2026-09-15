"""
campanias_generator.py

Generador MVP: process_spec (funcional, limpio) -> .dtsx (SSIS real), tomando
como base templates/campanias_base.dtsx (copia exacta de
BipSuc_CampaniasVigentes.dtsx, un paquete real y validado).

Estrategia (ver docs/generator_mvp.md): template validado + modificacion
programatica sobre arbol ElementTree. Nunca string-replace ciego sobre el
XML funcional. El bloque DTS:DesignTimeProperties (puro layout, sin impacto
funcional segun el propio comentario del archivo) es la unica seccion que
no se genera de cero: se parsea su contenido embebido como un documento XML
propio, se editan sus refIds (renombrar o eliminar segun corresponda) y se
vuelve a serializar — nunca se genera su forma final desde cero.

Alcance (template-teradata-to-sql-v1): Teradata Source -> [Data Conversion
OPCIONAL] -> [Derived Column OPCIONAL] -> OLE DB Destination. Si el spec no
declara ninguna conversion/derivacion, el componente correspondiente se
elimina del arbol (no queda presente pero desconectado) y el pipeline se
acorta. No soporta mas de 1 Data Conversion ni mas de 1 Derived Column, ni
Sequence Containers, Execute SQL Task, transacciones, staging, Merge Join,
Conditional Split, Row Count ni OLE DB Source como origen. No hay inferencia
automatica de que columnas necesitan conversion/derivacion — es siempre una
declaracion explicita del spec (ver docs/template_teradata_to_sql_v1_audit.md,
§5, y docs/derived_column_v1.md).
"""

from __future__ import annotations

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
DERIVED_COLUMN_CLASS_ID = "Microsoft.DerivedColumn"
OLE_DB_DESTINATION_CLASS_ID = "Microsoft.OLEDBDestination"

# teradata-to-sql-profile-v1: mapeo evidence-based DTS:ProtectionLevel (atributo
# numerico del <DTS:Executable> raiz) -- SOLO los 2 codigos confirmados contra
# el corpus real (ver generator/spec_validator.py::SUPPORTED_PACKAGE_PROTECTION_LEVELS
# y docs/teradata_to_sql_profile_v1.md). 'package.protection_level' es OPCIONAL
# en el process_spec: ausente => build_package_tree() no toca el atributo, que
# queda con el valor heredado del template (comportamiento actual, sin cambios).
PROTECTION_LEVEL_CODES = {
    "EncryptSensitiveWithUserKey": "1",
    "EncryptSensitiveWithPassword": "2",
}

# Nombres estructurales FIJOS del template (sub-etiquetas de input/output que
# el process_spec no expone — ver docs/generator_mvp.md, "Estructura de
# process_spec propuesta"). Solo package.name/data_flow.name y el 'name' de
# cada componente (source/transformation/destination) vienen del spec.
TEMPLATE_DATA_FLOW_NAME = "Tarea Flujo de datos"
TEMPLATE_SOURCE_NAME = "Teradata Source"
TEMPLATE_CONVERSION_NAME = "Conversión de datos"
# derived-column-v1: nombre del componente evidenciado en las 3 instancias
# identicas de BipSuc_Turnero.dtsx -- no varia entre proyectos (ver
# docs/derived_column_v1.md).
TEMPLATE_DERIVED_COLUMN_NAME = "Columna derivada"
TEMPLATE_DESTINATION_NAME = "Destino de OLE DB"

SOURCE_OUTPUT_NAME = "Teradata Source Output"
SOURCE_ERROR_OUTPUT_NAME = "Teradata Source Error Output"
SOURCE_CONNECTION_LOCAL_NAME = "TeradataConnection"

CONVERSION_INPUT_NAME = "Entrada de conversión de datos"
CONVERSION_OUTPUT_NAME = "Salida de conversión de datos"
CONVERSION_ERROR_OUTPUT_NAME = "Salida de error de conversión de datos"

# derived-column-v1
DERIVED_COLUMN_INPUT_NAME = "Entrada de columna derivada"
DERIVED_COLUMN_OUTPUT_NAME = "Salida de columna derivada"
DERIVED_COLUMN_ERROR_OUTPUT_NAME = "Salida de error de columna derivada"

DESTINATION_INPUT_NAME = "Entrada de destino de OLE DB"
DESTINATION_ERROR_OUTPUT_NAME = "Salida de error de destino de OLE DB"
DESTINATION_CONNECTION_LOCAL_NAME = "OleDbConnection"

PATH_1_NAME = SOURCE_OUTPUT_NAME
PATH_2_NAME = CONVERSION_OUTPUT_NAME
# derived-column-v1: tercer path posible, unico orden evidenciado (ver
# docs/derived_column_v1.md) -- Source -> [Data Conversion] -> [Derived
# Column] -> Destination. El nombre de cada path es siempre el del OUTPUT
# que lo origina (convencion ya establecida), nunca el del destino.
PATH_3_NAME = DERIVED_COLUMN_OUTPUT_NAME


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
    precision: Optional[int] = None,
    scale: Optional[int] = None,
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
    if precision is not None:
        el.set("precision", str(precision))
    if scale is not None:
        el.set("scale", str(scale))
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
    precision: Optional[int] = None,
    scale: Optional[int] = None,
) -> ET.Element:
    el = ET.Element("externalMetadataColumn")
    el.set("refId", ref_id)
    el.set("dataType", data_type)
    if length is not None:
        el.set("length", str(length))
    if code_page is not None:
        el.set("codePage", str(code_page))
    if precision is not None:
        el.set("precision", str(precision))
    if scale is not None:
        el.set("scale", str(scale))
    el.set("name", name)
    return el


def _make_input_column(
    ref_id: str,
    cached_name: str,
    cached_data_type: str,
    *,
    cached_length: Optional[int] = None,
    cached_code_page: Optional[int] = None,
    cached_precision: Optional[int] = None,
    cached_scale: Optional[int] = None,
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
    if cached_precision is not None:
        el.set("cachedPrecision", str(cached_precision))
    if cached_scale is not None:
        el.set("cachedScale", str(cached_scale))
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
#
# Desde que Data Conversion pasa a ser opcional (template-teradata-to-sql-v1),
# la reescritura de este bloque ya no alcanza con renombrar refs 1:1: cuando
# el componente se ELIMINA del arbol principal, su NodeLayout (y el
# EdgeLayout del path que dejo de existir) tambien deben desaparecer del
# layout — dejarlos apuntando a un refId que ya no existe en ningun otro
# lado del documento es exactamente el tipo de "referencia rota" que el
# proyecto evita en todos los demas casos (ver ssis_validator.py).
#
# Por eso esta seccion pasó de sustitucion de texto a PARSEAR el contenido
# embebido como XML de verdad (es un documento valido por si solo, ver
# BipSuc_CampaniasVigentes.dtsx), editar el arbol (renombrar atributos
# Id/design-time-name via el mismo ref_id_map de siempre; eliminar los nodos
# cuyo ref este en removed_refs; eliminar cualquier AnnotationLayout, igual
# que antes), y volver a serializar. Efecto secundario cosmetico aceptado:
# se pierden los comentarios XML explicativos del bloque original (son para
# un humano editando a mano, no aplican a un archivo generado) y los
# namespaces sin prefijo pueden re-serializarse con un prefijo autogenerado
# — ambos casos son válidos y legibles por SSDT igual, ninguno afecta el
# comportamiento en tiempo de ejecución.
# ---------------------------------------------------------------------------
_IDENTITY_ATTRS = ("Id", "design-time-name")


def _rewrite_design_time_properties(
    text: str, ref_id_map: Dict[str, str], removed_refs: set[str]
) -> str:
    """
    Reescribe el texto embebido de DTS:DesignTimeProperties:
    - renombra cada atributo Id/design-time-name cuyo valor este en ref_id_map;
    - elimina cualquier elemento cuyo Id/design-time-name este en removed_refs
      (componentes/paths que ya no existen en la topologia final);
    - elimina cualquier <AnnotationLayout> (comentario visual del autor
      original del template, no aplicable a un paquete generado).
    Si el texto no fuera XML valido por algun motivo inesperado, se devuelve
    sin tocar — mejor conservar el layout viejo que corromper el archivo.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return text

    for prefix, uri in (
        ("", "clr-namespace:Microsoft.SqlServer.IntegrationServices.Designer.Model.Serialization;assembly=Microsoft.SqlServer.IntegrationServices.Graph"),
        ("mssgle", "clr-namespace:Microsoft.SqlServer.Graph.LayoutEngine;assembly=Microsoft.SqlServer.Graph"),
        ("assembly", "http://schemas.microsoft.com/winfx/2006/xaml"),
    ):
        ET.register_namespace(prefix, uri)

    to_remove = []
    for parent in root.iter():
        for child in list(parent):
            if child.tag.endswith("AnnotationLayout"):
                to_remove.append((parent, child))
                continue
            marked_for_removal = False
            for attr in _IDENTITY_ATTRS:
                value = child.get(attr)
                if value is None:
                    continue
                if value in removed_refs:
                    marked_for_removal = True
                    break
                if value in ref_id_map:
                    child.set(attr, ref_id_map[value])
            if marked_for_removal:
                to_remove.append((parent, child))

    for parent, child in to_remove:
        parent.remove(child)

    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# Generacion
# ---------------------------------------------------------------------------
def build_package_tree(
    spec: Dict[str, Any], project_context: Dict[str, Any], template_path: str
) -> ET.Element:
    """Construye el arbol XML completo del paquete generado. No escribe a
    disco (ver generate() para eso). Asume que el spec YA fue validado
    contra project_context (ver generate()).

    derived-column-v1: 'transformations[]' ya no se trata como "como maximo
    1 elemento, implicitamente data_conversion" -- se busca por 'type'
    ("data_conversion"/"derived_column"), cada una opcional e independiente
    (spec_validator.py ya garantiza a lo sumo 1 de cada tipo, y que si
    coexisten, 'data_conversion' aparece antes que 'derived_column' en la
    lista -- unico orden evidenciado, ver docs/derived_column_v1.md)."""
    package_name = spec["package"]["name"]
    data_flow = spec["data_flow"]
    data_flow_name = data_flow["name"]
    source_spec = data_flow["source"]
    transformations = data_flow.get("transformations", [])
    conversion_spec = next(
        (t for t in transformations if t.get("type") == "data_conversion"), None
    )
    derived_column_spec = next(
        (t for t in transformations if t.get("type") == "derived_column"), None
    )
    destination_spec = data_flow["destination"]

    source_name = source_spec["name"]
    conversion_name = conversion_spec["name"] if conversion_spec is not None else None
    derived_column_name = (
        derived_column_spec["name"] if derived_column_spec is not None else None
    )
    destination_name = destination_spec["name"]

    tree = ET.parse(template_path)
    root = tree.getroot()

    # --- Package ---
    root.set(dts("ObjectName"), package_name)
    root.set(dts("DTSID"), new_guid())
    root.set(dts("VersionGUID"), new_guid())

    # teradata-to-sql-profile-v1: 'protection_level' OPCIONAL -- ausente
    # preserva el DTS:ProtectionLevel heredado del template (comportamiento
    # actual, sin cambios); presente lo sobrescribe explicitamente. Ya
    # validado contra SUPPORTED_PACKAGE_PROTECTION_LEVELS por spec_validator.
    protection_level = spec["package"].get("protection_level")
    if protection_level is not None:
        root.set(dts("ProtectionLevel"), PROTECTION_LEVEL_CODES[protection_level])

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
    derived_column_el = _find_component(components_el, DERIVED_COLUMN_CLASS_ID)
    destination_el = _find_component(components_el, OLE_DB_DESTINATION_CLASS_ID)

    # Registro de columnas del pipeline: nombre -> metadata (para resolver
    # lineageId/tipo cacheado desde cualquier componente aguas abajo). Se
    # completa a medida que se generan Teradata Source, (si existe) Data
    # Conversion y (si existe) Derived Column, EN ESE ORDEN -- asi el input
    # de una Derived Column puede resolver tanto una columna de origen como
    # un output de Data Conversion (evidencia real: ver
    # docs/derived_column_v1.md, orden topologico).
    pipeline_columns: Dict[str, Dict[str, Any]] = {}

    _build_teradata_source(
        teradata_el, data_flow_name, source_name, source_spec, pipeline_columns, project_context
    )

    if conversion_spec is not None:
        _build_data_conversion(
            conversion_el, data_flow_name, conversion_name, conversion_spec, pipeline_columns
        )
    else:
        # Data Conversion opcional (template-teradata-to-sql-v1): si el spec
        # no declara ninguna conversion, el componente se ELIMINA del arbol
        # en vez de dejarlo presente pero desconectado. Un componente sin
        # ningun <path> que lo referencie es exactamente el patron
        # "huerfano" que ya detectamos como señal de alarma en Turnero
        # (docs/campanias_vs_turnero.md) — no queremos reproducirlo a
        # proposito en un paquete recien generado.
        components_el.remove(conversion_el)

    if derived_column_spec is not None:
        _build_derived_column(
            derived_column_el, data_flow_name, derived_column_name, derived_column_spec, pipeline_columns
        )
    else:
        # Derived Column opcional (derived-column-v1), mismo criterio que
        # Data Conversion: se ELIMINA del arbol si el spec no la pide, nunca
        # queda presente pero desconectada.
        components_el.remove(derived_column_el)

    _build_ole_db_destination(
        destination_el,
        data_flow_name,
        destination_name,
        destination_spec,
        pipeline_columns,
        project_context,
    )

    _rebuild_paths(
        pipeline_el, data_flow_name, source_name, conversion_name, derived_column_name, destination_name
    )

    _update_design_time_properties(
        root,
        old_data_flow=TEMPLATE_DATA_FLOW_NAME,
        old_source=TEMPLATE_SOURCE_NAME,
        old_conversion=TEMPLATE_CONVERSION_NAME,
        old_derived_column=TEMPLATE_DERIVED_COLUMN_NAME,
        old_destination=TEMPLATE_DESTINATION_NAME,
        new_data_flow=data_flow_name,
        new_source=source_name,
        new_conversion=conversion_name,
        new_derived_column=derived_column_name,
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

    # teradata-to-sql-profile-v1: 'min_sessions'/'max_sessions' OPCIONALES --
    # ausentes preservan los valores heredados del template (comportamiento
    # actual, sin cambios: hoy son 4/8 porque asi esta campanias_base.dtsx,
    # pero ese heredo era implicito/accidental -- ver docs/teradata_to_sql_profile_v1.md).
    # Presentes, sobrescriben explicitamente las properties ya existentes del
    # template (MinSessions/MaxSessions siempre estan presentes en un Teradata
    # Source real, ver _set_property_text).
    if "min_sessions" in spec:
        _set_property_text(properties_el, "MinSessions", str(spec["min_sessions"]))
    if "max_sessions" in spec:
        _set_property_text(properties_el, "MaxSessions", str(spec["max_sessions"]))

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
        precision = col.get("precision")
        scale = col.get("scale")

        out_ref = output_column_ref(data_flow, name, SOURCE_OUTPUT_NAME, col_name)
        ext_ref = output_external_column_ref(data_flow, name, SOURCE_OUTPUT_NAME, col_name)

        output_columns_el.append(
            _make_output_column(
                out_ref,
                col_name,
                data_type,
                length=length,
                code_page=code_page,
                precision=precision,
                scale=scale,
                lineage_id=out_ref,
                external_metadata_column_id=ext_ref,
            )
        )
        external_columns_el.append(
            _make_external_metadata_column(
                ext_ref, col_name, data_type,
                length=length, code_page=code_page, precision=precision, scale=scale,
            )
        )

        error_ref = output_column_ref(data_flow, name, SOURCE_ERROR_OUTPUT_NAME, col_name)
        error_output_columns_el.append(
            _make_output_column(
                error_ref, col_name, data_type,
                length=length, code_page=code_page, precision=precision, scale=scale,
                lineage_id=error_ref,
            )
        )

        pipeline_columns[col_name] = {
            "lineage_id": out_ref,
            "data_type": data_type,
            "length": length,
            "code_page": code_page,
            "precision": precision,
            "scale": scale,
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
        # target_length/target_code_page/target_precision/target_scale: antes
        # de este incremento, esta metadata se descartaba incondicionalmente
        # (bug confirmado en la generalizacion contra PagosYRecaudaciones --
        # ver docs/template_teradata_to_sql_v1_generalization_pagosyrecaudaciones.md,
        # seccion 6). spec_validator.py ya exige estos campos cuando
        # target_type lo requiere (str/wstr/numeric), asi que aca siempre
        # estan presentes para esos tipos.
        target_length = conv.get("target_length")
        target_code_page = conv.get("target_code_page")
        target_precision = conv.get("target_precision")
        target_scale = conv.get("target_scale")

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
                cached_precision=source_meta.get("precision"),
                cached_scale=source_meta.get("scale"),
                lineage_id=source_meta["lineage_id"],
            )
        )

        out_ref = output_column_ref(data_flow, name, CONVERSION_OUTPUT_NAME, output_name)
        out_col = _make_output_column(
            out_ref,
            output_name,
            target_type,
            length=target_length,
            code_page=target_code_page,
            precision=target_precision,
            scale=target_scale,
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
            "length": target_length,
            "code_page": target_code_page,
            "precision": target_precision,
            "scale": target_scale,
        }

    for err_col in _make_error_columns(data_flow, name, CONVERSION_ERROR_OUTPUT_NAME):
        error_output_columns_el.append(err_col)


# ---------------------------------------------------------------------------
# derived-column-v1
#
# Unica operacion soportada: 'null_preserving_cast'. Expresion SSIS
# construida EXACTAMENTE como en las 3 instancias identicas de
# BipSuc_Turnero.dtsx (Examples/Originals/BipSuc_Turnero.dtsx):
#
#   Expression:         [ISNULL](#{<lineageId>}) ? NULL(DT_WSTR,<n>) : (DT_WSTR,<n>)#{<lineageId>}
#   FriendlyExpression: ISNULL(<nombre>) ? NULL(DT_WSTR,<n>) : (DT_WSTR,<n>)<nombre>
#
# 'DT_WSTR' es el UNICO token de tipo de expresion SSIS evidenciado (el
# unico target_type soportado en v1 es 'wstr', ver
# derived_planner.schema.SUPPORTED_TARGET_TYPE) -- no se generaliza a otros
# tokens DT_* sin evidencia real.
# ---------------------------------------------------------------------------
NULL_PRESERVING_CAST_EXPRESSION_TYPE_TOKEN = "DT_WSTR"


def _build_null_preserving_cast_expression(lineage_id: str, target_length: int) -> str:
    ref = id_reference_wrapper(lineage_id)
    return (
        f"[ISNULL]({ref}) ? NULL({NULL_PRESERVING_CAST_EXPRESSION_TYPE_TOKEN},{target_length}) : "
        f"({NULL_PRESERVING_CAST_EXPRESSION_TYPE_TOKEN},{target_length}){ref}"
    )


def _build_null_preserving_cast_friendly_expression(input_name: str, target_length: int) -> str:
    return (
        f"ISNULL({input_name}) ? NULL({NULL_PRESERVING_CAST_EXPRESSION_TYPE_TOKEN},{target_length}) : "
        f"({NULL_PRESERVING_CAST_EXPRESSION_TYPE_TOKEN},{target_length}){input_name}"
    )


def _build_derived_column(
    component_el: ET.Element,
    data_flow: str,
    name: str,
    spec: Dict[str, Any],
    pipeline_columns: Dict[str, Dict[str, Any]],
) -> None:
    """
    Serializa Microsoft.DerivedColumn replicando exactamente la evidencia
    real (ver docstring de NULL_PRESERVING_CAST_EXPRESSION_TYPE_TOKEN).
    Mismo patron que _build_data_conversion: limpia y reconstruye
    inputColumns/outputColumns desde cero por spec, nunca hereda contenido
    del template. 'spec' es la entrada 'derived_column' de
    data_flow.transformations[] (ya validada por spec_validator.py -- unica
    operation soportada 'null_preserving_cast', unico target_type
    soportado 'wstr').
    """
    component_el.set("refId", component_ref(data_flow, name))
    component_el.set("name", name)
    component_el.set("description", name)

    input_el = component_el.find("inputs/input")
    input_el.set("refId", input_ref(data_flow, name, DERIVED_COLUMN_INPUT_NAME))
    input_columns_el = input_el.find("inputColumns")
    _clear_children(input_columns_el, "inputColumn")

    outputs_el = component_el.find("outputs")
    normal_output = _find_output(outputs_el, is_error=False)
    error_output = _find_output(outputs_el, is_error=True)
    normal_output.set("refId", output_ref(data_flow, name, DERIVED_COLUMN_OUTPUT_NAME))
    normal_output.set("synchronousInputId", input_el.get("refId"))
    error_output.set("refId", output_ref(data_flow, name, DERIVED_COLUMN_ERROR_OUTPUT_NAME))
    error_output.set("synchronousInputId", input_el.get("refId"))

    output_columns_el = normal_output.find("outputColumns")
    _clear_children(output_columns_el, "outputColumn")
    error_output_columns_el = error_output.find("outputColumns")
    _clear_children(error_output_columns_el, "outputColumn")

    # Un Derived Column real puede referenciar la MISMA columna de entrada
    # desde mas de una expresion -- se registra <inputColumn> una sola vez
    # por nombre de origen distinto (mismo criterio que evitar refIds
    # duplicados en cualquier otro componente).
    registered_inputs: set = set()

    for col in spec["columns"]:
        input_name = col["input"]
        output_name = col["output"]
        operation = col["operation"]
        target_type = col["target_type"]
        target_length = col["target_length"]

        source_meta = pipeline_columns.get(input_name)
        if source_meta is None:
            raise GeneratorError(
                f"Derived Column: input '{input_name}' no esta registrado en el "
                "pipeline (deberia haber sido detectado por spec_validator)."
            )

        if operation != "null_preserving_cast":
            # No deberia llegar aca: spec_validator.py ya rechaza cualquier
            # otra operation antes de generar (unica soportada en
            # derived-column-v1). Ver derived_planner/schema.py.
            raise GeneratorError(
                f"Derived Column: operation {operation!r} no soportada por "
                "derived-column-v1 (unica soportada: 'null_preserving_cast')."
            )

        if input_name not in registered_inputs:
            in_ref = input_column_ref(data_flow, name, DERIVED_COLUMN_INPUT_NAME, input_name)
            input_columns_el.append(
                _make_input_column(
                    in_ref,
                    input_name,
                    source_meta["data_type"],
                    cached_length=source_meta.get("length"),
                    cached_code_page=source_meta.get("code_page"),
                    cached_precision=source_meta.get("precision"),
                    cached_scale=source_meta.get("scale"),
                    lineage_id=source_meta["lineage_id"],
                )
            )
            registered_inputs.add(input_name)

        out_ref = output_column_ref(data_flow, name, DERIVED_COLUMN_OUTPUT_NAME, output_name)
        out_col = _make_output_column(
            out_ref,
            output_name,
            target_type,
            length=target_length,
            lineage_id=out_ref,
            error_or_truncation_operation="Cálculo",
            error_row_disposition="FailComponent",
            truncation_row_disposition="FailComponent",
        )

        properties_el = ET.SubElement(out_col, "properties")
        expression_prop = ET.SubElement(properties_el, "property")
        expression_prop.set("containsID", "true")
        expression_prop.set("dataType", "System.String")
        expression_prop.set("description", "Expresión de columna derivada")
        expression_prop.set("name", "Expression")
        expression_prop.text = _build_null_preserving_cast_expression(
            source_meta["lineage_id"], target_length
        )

        friendly_prop = ET.SubElement(properties_el, "property")
        friendly_prop.set("containsID", "true")
        friendly_prop.set("dataType", "System.String")
        friendly_prop.set("description", "Expresión descriptiva de columna derivada")
        friendly_prop.set("expressionType", "Notify")
        friendly_prop.set("name", "FriendlyExpression")
        friendly_prop.text = _build_null_preserving_cast_friendly_expression(input_name, target_length)

        output_columns_el.append(out_col)

        pipeline_columns[output_name] = {
            "lineage_id": out_ref,
            "data_type": target_type,
            "length": target_length,
            "code_page": None,
            "precision": None,
            "scale": None,
        }

    for err_col in _make_error_columns(data_flow, name, DERIVED_COLUMN_ERROR_OUTPUT_NAME):
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

    # access_mode: OPCIONAL (ver spec_validator.py). Si no viene en el spec,
    # se preserva el valor que ya trae el template (0, igual que Campanias
    # real) -- no se toca la property. Si viene, sobrescribe "AccessMode"
    # con el mismo helper que ya usa OpenRowset. Ninguna otra property de
    # Fast Load (FastLoadOptions/FastLoadKeepIdentity/FastLoadKeepNulls/
    # FastLoadMaxInsertCommitSize) se toca -- fuera de alcance de este
    # incremento (sin evidencia real de que varien entre paquetes).
    if "access_mode" in spec:
        _set_property_text(properties_el, "AccessMode", str(spec["access_mode"]))

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
        target_precision = mapping.get("target_precision")
        target_scale = mapping.get("target_scale")

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
                cached_precision=source_meta.get("precision"),
                cached_scale=source_meta.get("scale"),
                lineage_id=source_meta["lineage_id"],
                external_metadata_column_id=ext_ref,
            )
        )
        external_columns_el.append(
            _make_external_metadata_column(
                ext_ref, target_name, target_type,
                length=target_length, code_page=target_code_page,
                precision=target_precision, scale=target_scale,
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
    conversion_name: Optional[str],
    derived_column_name: Optional[str],
    destination_name: str,
) -> None:
    """
    Topologia dinamica: Source -> [Data Conversion] -> [Derived Column] ->
    Destination. derived-column-v1 generaliza el mecanismo ya existente de
    template-teradata-to-sql-v1 (que solo conocia Source -> [Conversion] ->
    Destination) a una cadena de "paradas" opcionales, preservando el
    comportamiento EXACTO de los 2 casos ya soportados:
    - Sin Data Conversion ni Derived Column: 1 path directo Source ->
      Destination (igual que siempre).
    - Con Data Conversion, sin Derived Column: 2 paths, igual que siempre.

    Casos nuevos (evidencia: BipSuc_Turnero.dtsx, unico orden real
    disponible cuando ambas transformaciones coexisten -- Data Conversion
    ANTES que Derived Column, nunca al reves, ver spec_validator.py):
    - Sin Data Conversion, con Derived Column: 2 paths, Source -> Derived
      Column -> Destination.
    - Con ambas: 3 paths, Source -> Conversion -> Derived Column -> Destination.

    El NOMBRE de cada path es SIEMPRE el del output que lo origina
    (convencion ya establecida en template-teradata-to-sql-v1, ver
    docs/xml_patterns.md §6) — nunca cambia solo porque cambie su endId.
    PATH_1_NAME == SOURCE_OUTPUT_NAME, PATH_2_NAME == CONVERSION_OUTPUT_NAME,
    PATH_3_NAME == DERIVED_COLUMN_OUTPUT_NAME: los 3 nombres posibles ya son
    exactamente los nombres de output de cada etapa, sin necesidad de un
    caso especial para la primera "parada".
    """
    paths_el = pipeline_el.find("paths")
    _clear_children(paths_el, "path")

    # (nombre_componente, nombre_de_su_output, nombre_de_su_input) -- el
    # primer elemento (Source) no tiene "input" propio en esta cadena (es
    # el origen); el ultimo (Destination) no tiene "output" propio (es el
    # final). Solo se listan las "paradas" intermedias que el spec pidio.
    stops: List[tuple] = [(source_name, SOURCE_OUTPUT_NAME, None)]
    if conversion_name is not None:
        stops.append((conversion_name, CONVERSION_OUTPUT_NAME, CONVERSION_INPUT_NAME))
    if derived_column_name is not None:
        stops.append((derived_column_name, DERIVED_COLUMN_OUTPUT_NAME, DERIVED_COLUMN_INPUT_NAME))
    stops.append((destination_name, None, DESTINATION_INPUT_NAME))

    for (from_name, from_output, _), (to_name, _, to_input) in zip(stops, stops[1:]):
        path_el = ET.Element("path")
        path_el.set("refId", path_ref(data_flow, from_output))
        path_el.set("name", from_output)
        path_el.set("startId", output_ref(data_flow, from_name, from_output))
        path_el.set("endId", input_ref(data_flow, to_name, to_input))
        paths_el.append(path_el)


def _update_design_time_properties(
    root: ET.Element,
    *,
    old_data_flow: str,
    old_source: str,
    old_conversion: str,
    old_destination: str,
    new_data_flow: str,
    new_source: str,
    new_conversion: Optional[str],
    new_destination: str,
    old_derived_column: str = TEMPLATE_DERIVED_COLUMN_NAME,
    new_derived_column: Optional[str] = None,
) -> None:
    """
    new_conversion es None cuando el spec no declara Data Conversion. En ese
    caso, el NodeLayout del componente y el EdgeLayout del segundo path
    (PATH_2_NAME) se ELIMINAN del layout (van a removed_refs) en vez de
    renombrarse — ya no existe ningun componente/path en el documento
    principal al que puedan seguir apuntando. PATH_1_NAME se renombra igual
    en los dos casos: es el mismo path (Source -> algo), solo cambia su
    destino final, no su identidad. Mismo criterio para new_derived_column
    (derived-column-v1).
    """
    dtp_el = root.find(dts("DesignTimeProperties"))
    if dtp_el is None or dtp_el.text is None:
        return

    ref_id_map = {
        dataflow_executable_ref(old_data_flow): dataflow_executable_ref(new_data_flow),
        component_ref(old_data_flow, old_source): component_ref(new_data_flow, new_source),
        component_ref(old_data_flow, old_destination): component_ref(new_data_flow, new_destination),
        path_ref(old_data_flow, PATH_1_NAME): path_ref(new_data_flow, PATH_1_NAME),
    }
    removed_refs: set[str] = set()

    if new_conversion is not None:
        ref_id_map[component_ref(old_data_flow, old_conversion)] = component_ref(
            new_data_flow, new_conversion
        )
        ref_id_map[path_ref(old_data_flow, PATH_2_NAME)] = path_ref(new_data_flow, PATH_2_NAME)
    else:
        removed_refs.add(component_ref(old_data_flow, old_conversion))
        removed_refs.add(path_ref(old_data_flow, PATH_2_NAME))

    if new_derived_column is not None:
        ref_id_map[component_ref(old_data_flow, old_derived_column)] = component_ref(
            new_data_flow, new_derived_column
        )
        ref_id_map[path_ref(old_data_flow, PATH_3_NAME)] = path_ref(new_data_flow, PATH_3_NAME)
    else:
        removed_refs.add(component_ref(old_data_flow, old_derived_column))
        removed_refs.add(path_ref(old_data_flow, PATH_3_NAME))

    dtp_el.text = _rewrite_design_time_properties(dtp_el.text, ref_id_map, removed_refs)


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
