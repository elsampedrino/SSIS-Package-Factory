"""
ssis_parser.py

Parser exploratorio de paquetes SSIS (.dtsx) para SSIS Package Factory.

Alcance actual (ver README.md para detalle completo):
    - Lee un .dtsx real y lo convierte en una representacion intermedia (IR) en JSON.
    - Recorre el Control Flow completo del paquete (Executables anidados,
      Sequence Containers, Execute SQL Task, Precedence Constraints, variables),
      clasificando cada Executable por `DTS:ExecutableType`.
    - Dentro de cada Data Flow Task (Microsoft.Pipeline), identifica sus
      componentes por `componentClassID`, sin hardcodear nombres.
    - Extrae propiedades, conexiones, columnas de entrada/salida, metadata externa,
      lineageId, mappings hacia destinos y paths entre componentes.
    - Todo lo que no esta respaldado por el XML de origen se documenta como tal en
      docs/xml_patterns.md; este modulo NO inventa estructuras.

Este modulo esta pensado para crecer sin funciones monoliticas que "conozcan"
todos los tipos de SSIS:
    - Un nuevo tipo de TAREA de Control Flow: agregar su DTS:ExecutableType a
      EXECUTABLE_TYPE_MAP y, opcionalmente, un builder en EXECUTABLE_BUILDERS.
    - Un nuevo tipo de COMPONENTE de Data Flow: agregar su componentClassID a
      COMPONENT_TYPE_MAP y, opcionalmente, un builder en COMPONENT_BUILDERS.
Ver README.md, seccion "Como extenderlo".
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------
# El nivel "Control Flow" (DTS:Executable, DTS:Variables, DTS:PrecedenceConstraints,
# etc.) usa el namespace DTS. El ObjectData de un Execute SQL Task usa su propio
# namespace (SQLTask). El contenido de un Data Flow (<pipeline> y descendientes,
# dentro de DTS:ObjectData) NO tiene namespace declarado en el XML de origen, por
# lo que sus tags/atributos se consultan sin prefijo.
NS = {
    "DTS": "www.microsoft.com/SqlServer/Dts",
    "SQLTask": "www.microsoft.com/sqlserver/dts/tasks/sqltask",
}


def dts(tag: str) -> str:
    """Devuelve el tag/atributo calificado con el namespace DTS."""
    return f"{{{NS['DTS']}}}{tag}"


def sqltask(tag: str) -> str:
    """Devuelve el tag/atributo calificado con el namespace SQLTask."""
    return f"{{{NS['SQLTask']}}}{tag}"


UNKNOWN_TYPE_PREFIX = "unknown"


# ---------------------------------------------------------------------------
# Tabla de tareas de Control Flow conocidas.
#
# Igual criterio que COMPONENT_TYPE_MAP mas abajo: la deteccion se hace por
# `DTS:ExecutableType` (nunca por `DTS:ObjectName`, que es el nombre editable
# que le puso el usuario al arrastrar la tarea al diseñador).
# ---------------------------------------------------------------------------
EXECUTABLE_TYPE_MAP = {
    "Microsoft.Pipeline": "pipeline",
    "Microsoft.ExecuteSQLTask": "execute_sql_task",
    "STOCK:SEQUENCE": "sequence_container",
}


# ---------------------------------------------------------------------------
# Tabla de componentes de Data Flow conocidos.
#
# IMPORTANTE: esta tabla es la UNICA fuente de "conocimiento propietario" de
# nombres de componente. La deteccion real se hace siempre por
# `componentClassID`, nunca por el atributo `name` (que es editable por el
# usuario del paquete y no es confiable).
# ---------------------------------------------------------------------------
COMPONENT_TYPE_MAP = {
    "Microsoft.SSISTeradataSrc": "teradata_source",
    "Microsoft.DataConvert": "data_conversion",
    "Microsoft.OLEDBDestination": "ole_db_destination",
    "Microsoft.DerivedColumn": "derived_column",
    "Microsoft.MergeJoin": "merge_join",
    "Microsoft.ConditionalSplit": "conditional_split",
    "Microsoft.OLEDBSource": "ole_db_source",
    "Microsoft.RowCount": "row_count",
}


# ---------------------------------------------------------------------------
# Carga de XML
# ---------------------------------------------------------------------------
def load_package(path: str) -> ET.Element:
    """Carga un .dtsx y devuelve el elemento raiz (DTS:Executable / Package)."""
    tree = ET.parse(path)
    return tree.getroot()


# ---------------------------------------------------------------------------
# Variables (DTS:Variables / DTS:Variable) — pueden colgar del Package o de
# cualquier Executable (en los dos archivos de referencia, solo el Package
# las trae con contenido; en el resto de los Executables aparecen vacias).
# ---------------------------------------------------------------------------
def extract_variables(container_el: ET.Element) -> List[Dict[str, Any]]:
    vars_el = container_el.find(dts("Variables"))
    if vars_el is None:
        return []

    variables = []
    for var_el in vars_el.findall(dts("Variable")):
        namespace = var_el.get(dts("Namespace"))
        name = var_el.get(dts("ObjectName"))
        value_el = var_el.find(dts("VariableValue"))
        variables.append(
            {
                "name": name,
                "namespace": namespace,
                "qualified_name": f"{namespace}::{name}" if namespace and name else None,
                "dtsid": var_el.get(dts("DTSID")),
                "data_type_code": value_el.get(dts("DataType")) if value_el is not None else None,
                "value": value_el.text if value_el is not None else None,
            }
        )
    return variables


# ---------------------------------------------------------------------------
# Precedence Constraints (DTS:PrecedenceConstraints / DTS:PrecedenceConstraint)
# — cuelgan del Package o de cualquier container (Sequence Container observado).
# ---------------------------------------------------------------------------
def extract_precedence_constraints(container_el: ET.Element) -> List[Dict[str, Any]]:
    pc_el = container_el.find(dts("PrecedenceConstraints"))
    if pc_el is None:
        return []

    constraints = []
    for c in pc_el.findall(dts("PrecedenceConstraint")):
        constraints.append(
            {
                "ref_id": c.get(dts("refId")),
                "name": c.get(dts("ObjectName")),
                "dtsid": c.get(dts("DTSID")),
                "from": c.get(dts("From")),
                "to": c.get(dts("To")),
                "logical_and": c.get(dts("LogicalAnd")) == "True",
                # DTS:Value no aparece cuando el constraint es "Success" (el
                # default). Se observaron valores "1" en constraints hacia
                # tareas de rollback — coincide con la semantica general de
                # SSIS (0/ausente=Success, 1=Failure, 2=Completion), pero eso
                # es conocimiento general, no confirmado exhaustivamente por
                # este archivo (ver docs/xml_patterns.md).
                "value": c.get(dts("Value")),
            }
        )
    return constraints


# ---------------------------------------------------------------------------
# Control Flow: Executables (recursivo — Package, tareas, containers)
# ---------------------------------------------------------------------------
def classify_executable(exe_el: ET.Element) -> str:
    """
    Clasifica un DTS:Executable por su DTS:ExecutableType (nunca por
    DTS:ObjectName). Devuelve 'unknown:<ExecutableType>' si no esta en
    EXECUTABLE_TYPE_MAP, preservando toda la info generica igual que un tipo
    conocido.
    """
    exe_type = exe_el.get(dts("ExecutableType"), "")
    return EXECUTABLE_TYPE_MAP.get(exe_type, f"{UNKNOWN_TYPE_PREFIX}:{exe_type}")


def _build_generic_executable(exe_el: ET.Element, common: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback para DTS:ExecutableType no mapeados: conserva los atributos
    genericos (ref_id, name, disabled, variables, etc.) sin builder especifico."""
    return common


def _build_pipeline_executable(exe_el: ET.Element, common: Dict[str, Any]) -> Dict[str, Any]:
    common["data_flow"] = parse_data_flow(exe_el)
    return common


def _build_execute_sql_task_executable(exe_el: ET.Element, common: Dict[str, Any]) -> Dict[str, Any]:
    """
    Microsoft.ExecuteSQLTask serializa su configuracion en
    DTS:ObjectData/SQLTask:SqlTaskData, con namespace propio (SQLTask).

    OJO: SQLTask:Connection es un GUID "pelado" (sin ':external' ni
    'Project.ConnectionManagers[...]'), a diferencia de como los componentes
    de Data Flow referencian sus conexiones. connection_manager_ref_id se
    completa en un segundo paso (ver resolve_execute_sql_task_connections),
    cruzando ese GUID contra los que sí traen nombre en el mismo paquete.
    Si no se encuentra, queda en None (no se inventa).
    """
    object_data = exe_el.find(dts("ObjectData"))
    sql_el = object_data.find(sqltask("SqlTaskData")) if object_data is not None else None
    common["sql"] = {
        "connection_id": sql_el.get(sqltask("Connection")) if sql_el is not None else None,
        "connection_manager_ref_id": None,
        "sql_statement_source": sql_el.get(sqltask("SqlStatementSource")) if sql_el is not None else None,
    }
    return common


def _build_sequence_container_executable(exe_el: ET.Element, common: Dict[str, Any]) -> Dict[str, Any]:
    """STOCK:SEQUENCE anida sus propios DTS:Executables y
    DTS:PrecedenceConstraints, con la misma forma que a nivel Package."""
    children_el = exe_el.find(dts("Executables"))
    common["executables"] = [
        parse_executable(child)
        for child in (children_el.findall(dts("Executable")) if children_el is not None else [])
    ]
    common["precedence_constraints"] = extract_precedence_constraints(exe_el)
    return common


# Dispatch table: agregar aca un builder nuevo para soportar otro tipo de
# tarea de Control Flow sin tocar parse_executable(). Ver README.md.
EXECUTABLE_BUILDERS: Dict[str, Callable] = {
    "pipeline": _build_pipeline_executable,
    "execute_sql_task": _build_execute_sql_task_executable,
    "sequence_container": _build_sequence_container_executable,
}


def parse_executable(exe_el: ET.Element) -> Dict[str, Any]:
    """
    Parsea un DTS:Executable generico (tarea o container) y aplica un builder
    especifico segun su DTS:ExecutableType, igual que parse_component() hace
    para componentes de Data Flow.
    """
    exe_type = classify_executable(exe_el)

    common: Dict[str, Any] = {
        "ref_id": exe_el.get(dts("refId")),
        "name": exe_el.get(dts("ObjectName")),
        "dtsid": exe_el.get(dts("DTSID")),
        "executable_type": exe_el.get(dts("ExecutableType")),
        "type": exe_type,
        "description": exe_el.get(dts("Description")),
        "disabled": exe_el.get(dts("Disabled")) == "True",
        # DelayValidation: observado en 'Tarea Flujo Staging' (unico Data Flow
        # que escribe en una tabla temporal creada en un paso previo del
        # Control Flow, #ClientesStage). Le dice a SSIS que no valide la
        # metadata de este Executable al abrir/validar el paquete, porque el
        # objeto que usa puede no existir todavia en ese momento.
        "delay_validation": exe_el.get(dts("DelayValidation")) == "True",
        "thread_hint": _to_int(exe_el.get(dts("ThreadHint"))),
        "variables": extract_variables(exe_el),
    }

    builder = EXECUTABLE_BUILDERS.get(exe_type, _build_generic_executable)
    return builder(exe_el, common)


def _iter_executable_tree(nodes: List[Dict[str, Any]]):
    """Recorre en profundidad el arbol de executables (incluye containers)."""
    for node in nodes:
        yield node
        if node["type"] == "sequence_container":
            yield from _iter_executable_tree(node.get("executables", []))


def build_connection_guid_index(data_flows: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Indice GUID -> connectionManagerRefId, construido a partir de las
    <connection connectionManagerID="{GUID}:external" connectionManagerRefId="...">
    de todos los componentes de Data Flow del paquete.

    Se usa para resolver, de forma oportunista, el GUID "pelado" que trae
    SQLTask:Connection en un Execute SQL Task (ver
    _build_execute_sql_task_executable). Si el mismo Connection Manager no
    aparece tambien en algun Data Flow del paquete, no hay forma de resolver
    el nombre desde este .dtsx y el campo queda en None.
    """
    index: Dict[str, str] = {}
    for data_flow in data_flows:
        for component in data_flow.get("components", []):
            for conn in component.get("connections", []):
                cmid = conn.get("connection_manager_id")
                ref = conn.get("connection_manager_ref_id")
                if not cmid or not ref:
                    continue
                guid = cmid.split(":", 1)[0]
                index[guid] = ref
    return index


def resolve_execute_sql_task_connections(
    executables: List[Dict[str, Any]], guid_index: Dict[str, str]
) -> None:
    """Completa connection_manager_ref_id de cada execute_sql_task encontrado
    en el arbol, mutando los nodos in-place. No inventa: si el GUID no esta
    en guid_index, el campo queda en None."""
    for node in _iter_executable_tree(executables):
        if node["type"] == "execute_sql_task":
            guid = node["sql"].get("connection_id")
            node["sql"]["connection_manager_ref_id"] = guid_index.get(guid) if guid else None


# ---------------------------------------------------------------------------
# Rol de ejecucion (DERIVADO — no es un atributo nativo del XML)
#
# El XML solo serializa `DTS:Disabled` (booleano, por Executable, NO heredado
# a los hijos de un container). Todo lo demas de esta seccion es una
# interpretacion del parser sobre esa informacion cruda + la topologia de
# `DTS:PrecedenceConstraints`, documentada explicitamente como derivada.
#
# IMPORTANTE (correccion sobre una version anterior de este modulo): la
# ausencia de PrecedenceConstraints sobre un executable NO implica que sea
# codigo muerto. Un Control Flow puede tener varias raices independientes que
# arrancan en paralelo sin ningun constraint entrante — un executable sin
# constraints es simplemente eso, una raiz, no algo "no ejecutable". La
# version anterior conflaba "sin constraints en su nivel" con
# execution_role="orphaned" y build_active_execution_graph() lo excluia, lo
# cual hubiera eliminado por error a cualquier raiz habilitada que no
# encadenara desde otro paso. Se separan por eso los conceptos ESTRUCTURALES
# (que constraints tiene, si esta aislado) de la EJECUCION real (si corre o no):
#
#   enabled                   -> LEIDO directo de DTS:Disabled (invertido).
#                                 Por nodo, SIN heredar del container padre.
#   effective_enabled         -> DERIVADO: enabled propio Y de TODOS sus
#                                 ancestros. Motivo: en SSIS, un Sequence
#                                 Container deshabilitado hace que sus hijos
#                                 no se ejecuten aunque su propio DTS:Disabled
#                                 sea "False" (caso real: "Tarea Flujo de
#                                 datos 1" y "Trunca tabla" dentro de
#                                 "Sequence Container", que esta disabled).
#                                 Regla de motor SSIS, no algo que el XML
#                                 declare explicitamente en el nodo hijo.
#   has_incoming_constraint   -> DERIVADO: existe un PrecedenceConstraint del
#                                 NIVEL del nodo con To == su ref_id.
#   has_outgoing_constraint   -> DERIVADO: existe un PrecedenceConstraint del
#                                 NIVEL del nodo con From == su ref_id.
#   structurally_isolated     -> DERIVADO: ni incoming ni outgoing en su
#                                 nivel. Es un dato ESTRUCTURAL puro — NO
#                                 significa "no ejecutable".
#   is_control_flow_root      -> DERIVADO: effective_enabled Y sin incoming
#                                 constraint. Cubre tanto una raiz "normal"
#                                 (con salida hacia otros pasos) como un
#                                 executable totalmente aislado pero habilitado.
#   execution_role             -> DERIVADO, binario: "active" si
#                                 effective_enabled, si no "disabled". Ya NO
#                                 existe un valor "orphaned": el aislamiento
#                                 estructural se consulta en
#                                 structurally_isolated, nunca mezclado con
#                                 si el executable corre o no.
#
# NINGUNO de estos campos reemplaza al dato crudo: `disabled` (leido directo
# del XML) sigue presente sin modificar en cada nodo.
# ---------------------------------------------------------------------------
def annotate_execution_roles(
    executables: List[Dict[str, Any]],
    precedence_constraints: List[Dict[str, Any]],
    ancestor_effective_enabled: bool = True,
    parent_ref_id: Optional[str] = None,
) -> None:
    """Mutacion in-place: agrega enabled/effective_enabled/
    has_incoming_constraint/has_outgoing_constraint/structurally_isolated/
    is_control_flow_root/execution_role/parent_ref_id a cada nodo del arbol,
    recursivamente."""
    incoming_ref_ids = {pc.get("to") for pc in precedence_constraints}
    outgoing_ref_ids = {pc.get("from") for pc in precedence_constraints}

    for node in executables:
        node["parent_ref_id"] = parent_ref_id

        own_enabled = not node["disabled"]
        node["enabled"] = own_enabled

        effective_enabled = ancestor_effective_enabled and own_enabled
        node["effective_enabled"] = effective_enabled

        has_incoming = node.get("ref_id") in incoming_ref_ids
        has_outgoing = node.get("ref_id") in outgoing_ref_ids
        node["has_incoming_constraint"] = has_incoming
        node["has_outgoing_constraint"] = has_outgoing
        node["structurally_isolated"] = not has_incoming and not has_outgoing
        node["is_control_flow_root"] = effective_enabled and not has_incoming

        node["execution_role"] = "active" if effective_enabled else "disabled"

        if node["type"] == "sequence_container":
            annotate_execution_roles(
                node.get("executables", []),
                node.get("precedence_constraints", []),
                ancestor_effective_enabled=effective_enabled,
                parent_ref_id=node.get("ref_id"),
            )


def _filter_active_level(
    executables: List[Dict[str, Any]], precedence_constraints: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Sub-rutina recursiva de build_active_execution_graph: se queda con los
    executables cuyo effective_enabled sea True en este nivel — SIN importar
    si tienen o no PrecedenceConstraints — y con los precedence constraints
    cuyos DOS extremos esten dentro de ese conjunto. Un executable habilitado
    sin ningun constraint queda incluido como raiz independiente (ver el
    comentario junto a annotate_execution_roles)."""
    active_nodes = [e for e in executables if e.get("effective_enabled")]
    active_ids = {e["ref_id"] for e in active_nodes}

    active_constraints = [
        {
            "ref_id": pc.get("ref_id"),
            "name": pc.get("name"),
            "from": pc.get("from"),
            "to": pc.get("to"),
            "logical_and": pc.get("logical_and"),
            "value": pc.get("value"),
        }
        for pc in precedence_constraints
        if pc.get("from") in active_ids and pc.get("to") in active_ids
    ]

    result_nodes = []
    for node in active_nodes:
        entry: Dict[str, Any] = {
            "ref_id": node.get("ref_id"),
            "name": node.get("name"),
            "type": node.get("type"),
            "is_control_flow_root": node.get("is_control_flow_root"),
            "structurally_isolated": node.get("structurally_isolated"),
        }
        if node["type"] == "pipeline":
            entry["data_flow_name"] = (
                node["data_flow"]["name"] if node.get("data_flow") else None
            )
        elif node["type"] == "execute_sql_task":
            entry["sql"] = node.get("sql")
        elif node["type"] == "sequence_container":
            nested = _filter_active_level(
                node.get("executables", []), node.get("precedence_constraints", [])
            )
            entry["executables"] = nested["executables"]
            entry["precedence_constraints"] = nested["precedence_constraints"]
        result_nodes.append(entry)

    return {"executables": result_nodes, "precedence_constraints": active_constraints}


def build_active_execution_graph(control_flow: Dict[str, Any]) -> Dict[str, Any]:
    """
    Vista DERIVADA de control_flow que conserva UNICAMENTE los executables con
    execution_role == 'active' y los precedence constraints entre ellos.
    Requiere que annotate_execution_roles ya haya corrido sobre el arbol.

    No es una estructura nueva del XML: es un recorte de control_flow. Se
    construye aparte para no perder nunca el arbol completo original (incluida
    la logica deshabilitada/huerfana, que sigue sirviendo como evidencia de
    patrones XML aunque no participe de la ejecucion real).
    """
    return _filter_active_level(
        control_flow.get("executables", []),
        control_flow.get("precedence_constraints", []),
    )


# ---------------------------------------------------------------------------
# Deteccion de Data Flow Tasks
# ---------------------------------------------------------------------------
def find_data_flow_executables(root: ET.Element) -> List[ET.Element]:
    """
    Devuelve los elementos DTS:Executable cuyo DTS:ExecutableType es
    'Microsoft.Pipeline' (= Data Flow Task), en CUALQUIER nivel de anidamiento
    (root.iter recorre todo el subarbol, entre o no dentro de containers).

    Confirmado con BipSuc_Turnero.dtsx: encuentra tanto los Data Flow Tasks
    a nivel de Package como el que esta anidado dentro de un Sequence Container.
    """
    return [
        exe
        for exe in root.iter(dts("Executable"))
        if exe.get(dts("ExecutableType")) == "Microsoft.Pipeline"
    ]


def get_pipeline_element(dataflow_executable: ET.Element) -> Optional[ET.Element]:
    """Devuelve el elemento <pipeline> dentro de DTS:ObjectData de un Data Flow Task."""
    object_data = dataflow_executable.find(dts("ObjectData"))
    if object_data is None:
        return None
    return object_data.find("pipeline")


# ---------------------------------------------------------------------------
# Deteccion / clasificacion de componentes
# ---------------------------------------------------------------------------
def iter_components(pipeline_el: ET.Element) -> List[ET.Element]:
    """Devuelve la lista de elementos <component> dentro de <components>."""
    components_el = pipeline_el.find("components")
    if components_el is None:
        return []
    return components_el.findall("component")


def classify_component(component_el: ET.Element) -> str:
    """
    Clasifica un componente por su componentClassID (nunca por 'name').
    Si el classID no esta en COMPONENT_TYPE_MAP, se devuelve un tipo
    'unknown:<componentClassID>' para no perder informacion ni romper el
    parseo de componentes todavia no soportados.
    """
    class_id = component_el.get("componentClassID", "")
    return COMPONENT_TYPE_MAP.get(class_id, f"{UNKNOWN_TYPE_PREFIX}:{class_id}")


# ---------------------------------------------------------------------------
# Propiedades genericas (<properties><property>)
# ---------------------------------------------------------------------------
# Varios componentes (Data Conversion, Derived Column, Conditional Split,
# Merge Join) serializan referencias a lineageId embebidas dentro del VALOR
# de una property, envueltas en '#{...}'. En Data Conversion el valor entero
# de la property ES la referencia (un solo '#{...}'); en Derived Column y
# Conditional Split, el '#{...}' aparece incrustado dentro de una expresion
# SSIS mas larga junto con operadores y nombres de funcion (ISNULL, etc.),
# potencialmente mas de una vez. _EMBEDDED_REF_PATTERN cubre ambos casos.
_EMBEDDED_REF_PATTERN = re.compile(r"#\{(?P<ref>[^{}]*)\}")
_WHOLE_STRING_REF_PATTERN = re.compile(r"^#\{(?P<ref>.*)\}$")


def find_embedded_lineage_refs(value: Optional[str]) -> List[str]:
    """Devuelve TODAS las referencias '#{...}' encontradas dentro de un valor
    de property, en el orden en que aparecen. Lista vacia si no hay ninguna."""
    if not value:
        return []
    return [m.group("ref") for m in _EMBEDDED_REF_PATTERN.finditer(value)]


def unwrap_id_reference(raw_value: Optional[str]) -> Optional[str]:
    """
    Caso particular (Data Conversion / Merge Join): el valor COMPLETO de la
    property es una unica referencia '#{...}', sin texto alrededor. Devuelve
    la referencia desenvuelta, o el valor sin modificar si no matchea
    exactamente ese patron.
    """
    if raw_value is None:
        return None
    match = _WHOLE_STRING_REF_PATTERN.match(raw_value)
    return match.group("ref") if match else raw_value


def extract_raw_properties(container_el: ET.Element) -> List[Dict[str, Any]]:
    """
    Extrae TODAS las <property> hijas directas de <properties> dentro de
    container_el (componente, output, outputColumn, etc.), preservando su
    metadata tecnica completa. No filtra ni interpreta nada, salvo agregar
    `referenced_lineage_ids`: la lista (posiblemente vacia) de referencias
    '#{...}' encontradas en el valor, de forma puramente estructural.
    """
    properties_el = container_el.find("properties")
    if properties_el is None:
        return []

    props = []
    for prop_el in properties_el.findall("property"):
        value = prop_el.text if prop_el.text is not None else ""
        props.append(
            {
                "name": prop_el.get("name"),
                "value": value,
                "data_type": prop_el.get("dataType"),
                "description": prop_el.get("description"),
                "contains_id": prop_el.get("containsID") == "true",
                "expression_type": prop_el.get("expressionType"),
                "type_converter": prop_el.get("typeConverter"),
                "ui_type_editor": prop_el.get("UITypeEditor"),
                "referenced_lineage_ids": find_embedded_lineage_refs(value),
            }
        )
    return props


def properties_as_dict(raw_properties: List[Dict[str, Any]]) -> Dict[str, str]:
    """Reduce la lista de propiedades crudas a un dict simple name -> value."""
    return {p["name"]: p["value"] for p in raw_properties if p["name"]}


# ---------------------------------------------------------------------------
# Connection Managers referenciados desde un componente
# ---------------------------------------------------------------------------
def extract_connections(component_el: ET.Element) -> List[Dict[str, Any]]:
    """
    Extrae las <connection> del componente.

    OJO: esto solo captura la REFERENCIA (connectionManagerRefId +
    connectionManagerID). La definicion real del Connection Manager
    (proveedor, connection string, servidor) vive fuera de este .dtsx
    (a nivel de proyecto, en archivos .conmgr) y no puede inferirse del XML
    analizado. Ver docs/xml_patterns.md.
    """
    connections_el = component_el.find("connections")
    if connections_el is None:
        return []

    connections = []
    for conn_el in connections_el.findall("connection"):
        connections.append(
            {
                "ref_id": conn_el.get("refId"),
                "name": conn_el.get("name"),
                "connection_manager_ref_id": conn_el.get("connectionManagerRefId"),
                "connection_manager_id": conn_el.get("connectionManagerID"),
                "description": conn_el.get("description"),
            }
        )
    return connections


def primary_connection_ref(connections: List[Dict[str, Any]]) -> Optional[str]:
    """
    Devuelve el connectionManagerRefId de la primera conexion del componente.
    Todos los componentes con conexion observados hasta ahora (Teradata Source,
    OLE DB Source, OLE DB Destination) declaran exactamente una <connection>;
    si en el futuro aparece un componente con varias, este helper deja de ser
    suficiente y debe usarse extract_connections directamente.
    """
    if not connections:
        return None
    return connections[0]["connection_manager_ref_id"]


# ---------------------------------------------------------------------------
# externalMetadataColumns (usadas tanto en <input> como en <output>)
# ---------------------------------------------------------------------------
def extract_external_metadata_columns(container_el: ET.Element) -> List[Dict[str, Any]]:
    ext_el = container_el.find("externalMetadataColumns")
    if ext_el is None:
        return []

    columns = []
    for col_el in ext_el.findall("externalMetadataColumn"):
        columns.append(
            {
                "ref_id": col_el.get("refId"),
                "name": col_el.get("name"),
                "data_type": col_el.get("dataType"),
                "length": _to_int(col_el.get("length")),
                "code_page": _to_int(col_el.get("codePage")),
            }
        )
    return columns


# ---------------------------------------------------------------------------
# Input columns
# ---------------------------------------------------------------------------
def extract_input_columns(input_el: ET.Element) -> List[Dict[str, Any]]:
    input_columns_el = input_el.find("inputColumns")
    if input_columns_el is None:
        return []

    columns = []
    for col_el in input_columns_el.findall("inputColumn"):
        columns.append(
            {
                "ref_id": col_el.get("refId"),
                "name": col_el.get("cachedName"),
                "source_lineage_id": col_el.get("lineageId"),
                "cached_name": col_el.get("cachedName"),
                "cached_data_type": col_el.get("cachedDataType"),
                "cached_length": _to_int(col_el.get("cachedLength")),
                "cached_code_page": _to_int(col_el.get("cachedCodepage")),
                # cachedSortKeyPosition: observado en Merge Join (requiere
                # entradas ordenadas). None en componentes sin orden.
                "cached_sort_key_position": _to_int(col_el.get("cachedSortKeyPosition")),
                "external_metadata_column_id": col_el.get("externalMetadataColumnId"),
            }
        )
    return columns


def extract_inputs(component_el: ET.Element) -> List[Dict[str, Any]]:
    inputs_el = component_el.find("inputs")
    if inputs_el is None:
        return []

    inputs = []
    for input_el in inputs_el.findall("input"):
        inputs.append(
            {
                "ref_id": input_el.get("refId"),
                "name": input_el.get("name"),
                "description": input_el.get("description"),
                # Atributos observados en OLE DB Destination / Merge Join / Row Count:
                # indican que consumir este input tiene efecto mas alla de pasar filas
                # (escribir en tabla, requerir orden, escribir una variable, etc.).
                "has_side_effects": input_el.get("hasSideEffects") == "true",
                "error_row_disposition": input_el.get("errorRowDisposition"),
                "error_or_truncation_operation": input_el.get("errorOrTruncationOperation"),
                "columns": extract_input_columns(input_el),
                "external_metadata_columns": extract_external_metadata_columns(input_el),
            }
        )
    return inputs


# ---------------------------------------------------------------------------
# Output columns
# ---------------------------------------------------------------------------
def extract_output_columns(output_el: ET.Element) -> List[Dict[str, Any]]:
    output_columns_el = output_el.find("outputColumns")
    if output_columns_el is None:
        return []

    columns = []
    for col_el in output_columns_el.findall("outputColumn"):
        raw_props = extract_raw_properties(col_el)
        props_dict = properties_as_dict(raw_props)

        source_input_lineage_id = None
        if "SourceInputColumnLineageID" in props_dict:
            source_input_lineage_id = unwrap_id_reference(
                props_dict["SourceInputColumnLineageID"]
            )

        columns.append(
            {
                "ref_id": col_el.get("refId"),
                "name": col_el.get("name"),
                "data_type": col_el.get("dataType"),
                "length": _to_int(col_el.get("length")),
                "code_page": _to_int(col_el.get("codePage")),
                "lineage_id": col_el.get("lineageId"),
                "external_metadata_column_id": col_el.get("externalMetadataColumnId"),
                "source_input_lineage_id": source_input_lineage_id,
                # Expression/FriendlyExpression: observadas en Derived Column
                # (una expresion SSIS por columna calculada). None si el
                # componente no las usa a este nivel (p. ej. Conditional
                # Split las trae a nivel <output>, no <outputColumn>).
                "expression": props_dict.get("Expression"),
                "friendly_expression": props_dict.get("FriendlyExpression"),
                # sortKeyPosition: observado en Merge Join (columna de salida
                # que preserva el orden de entrada).
                "sort_key_position": _to_int(col_el.get("sortKeyPosition")),
                "error_row_disposition": col_el.get("errorRowDisposition"),
                "truncation_row_disposition": col_el.get("truncationRowDisposition"),
                "special_flags": col_el.get("specialFlags"),
                "raw_properties": raw_props,
            }
        )
    return columns


def extract_outputs(component_el: ET.Element) -> List[Dict[str, Any]]:
    """
    Devuelve TODOS los outputs del componente (normales y de error), cada uno
    marcado con is_error_output=True/False segun el atributo isErrorOut.
    """
    outputs_el = component_el.find("outputs")
    if outputs_el is None:
        return []

    outputs = []
    for output_el in outputs_el.findall("output"):
        raw_props = extract_raw_properties(output_el)
        props_dict = properties_as_dict(raw_props)

        outputs.append(
            {
                "ref_id": output_el.get("refId"),
                "name": output_el.get("name"),
                "description": output_el.get("description"),
                "is_error_output": output_el.get("isErrorOut") == "true",
                # isSorted: observado en Merge Join / OLE DB Source cuando el
                # origen esta ordenado (ORDER BY) para poder alimentar un
                # Merge Join sin un Sort explicito.
                "is_sorted": output_el.get("isSorted") == "true",
                "synchronous_input_id": output_el.get("synchronousInputId"),
                "exclusion_group": output_el.get("exclusionGroup"),
                # Expression/FriendlyExpression/EvaluationOrder/IsDefaultOut:
                # observados en Conditional Split, a nivel de <output> (rutea
                # la FILA completa, no una columna puntual como Derived Column).
                "expression": props_dict.get("Expression"),
                "friendly_expression": props_dict.get("FriendlyExpression"),
                "evaluation_order": _to_int(props_dict.get("EvaluationOrder")),
                "is_default_output": props_dict.get("IsDefaultOut") == "true",
                "raw_properties": raw_props,
                "columns": extract_output_columns(output_el),
                "external_metadata_columns": extract_external_metadata_columns(output_el),
            }
        )
    return outputs


def split_outputs(outputs: List[Dict[str, Any]]):
    """Separa outputs normales de outputs de error (isErrorOut='true')."""
    normal = [o for o in outputs if not o["is_error_output"]]
    error = [o for o in outputs if o["is_error_output"]]
    return normal, error


# ---------------------------------------------------------------------------
# Indice de lineage: lineageId -> columna que lo origino
# ---------------------------------------------------------------------------
def build_lineage_index(components: List[ET.Element]) -> Dict[str, Dict[str, Any]]:
    """
    Recorre todos los componentes del pipeline UNA vez y arma un indice
    lineageId -> metadata de la columna de salida que lo genero
    (componente, output, nombre de columna, tipo de dato).

    Este indice se usa para resolver, por ejemplo, cual es el "source_column"
    de un mapping de destino a partir del lineageId que trae el inputColumn.
    """
    index: Dict[str, Dict[str, Any]] = {}
    for component_el in components:
        component_name = component_el.get("name")
        outputs_el = component_el.find("outputs")
        if outputs_el is None:
            continue
        for output_el in outputs_el.findall("output"):
            output_name = output_el.get("name")
            output_columns_el = output_el.find("outputColumns")
            if output_columns_el is None:
                continue
            for col_el in output_columns_el.findall("outputColumn"):
                lineage_id = col_el.get("lineageId")
                if not lineage_id:
                    continue
                index[lineage_id] = {
                    "component_name": component_name,
                    "output_name": output_name,
                    "column_name": col_el.get("name"),
                    "data_type": col_el.get("dataType"),
                }
    return index


# ---------------------------------------------------------------------------
# Mappings columna-origen -> columna-destino (para componentes destino)
# ---------------------------------------------------------------------------
def build_external_metadata_index(container_el: ET.Element) -> Dict[str, Dict[str, Any]]:
    """
    Indice refId -> metadata de externalMetadataColumn dentro de un <input>
    (o <output>). Se usa para resolver el NOMBRE FISICO real de una columna
    (tal como la ve la tabla/objeto externo) a partir del
    externalMetadataColumnId que trae un inputColumn/outputColumn.
    """
    return {
        col["ref_id"]: col
        for col in extract_external_metadata_columns(container_el)
        if col["ref_id"]
    }


def extract_mappings(
    component_el: ET.Element, lineage_index: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Construye los mappings entre columnas del pipeline y columnas del destino
    a partir de los <inputColumn> del (unico) input del componente destino.

    Distingue explicitamente dos conceptos que el XML mantiene separados:
    - pipeline_input_column: nombre de la columna tal como llega por el
      pipeline (cachedName del inputColumn) — puede no coincidir con el
      nombre fisico de la tabla destino.
    - destination_column: nombre FISICO real en el destino, resuelto contra
      <externalMetadataColumn name="..."> via externalMetadataColumnId. NO
      se infiere por string matching: si el id no resuelve, queda None.

    source_column se resuelve via lineage_index a partir del lineageId
    referenciado por el inputColumn (columna que origino ese lineageId, en
    el componente upstream correspondiente).
    """
    mappings = []
    for input_el in _iter_input_elements(component_el):
        ext_meta_index = build_external_metadata_index(input_el)
        for col_el in _iter_input_columns(input_el):
            source_lineage_id = col_el.get("lineageId")
            source_info = lineage_index.get(source_lineage_id, {})

            ext_meta_id = col_el.get("externalMetadataColumnId")
            ext_meta_entry = ext_meta_index.get(ext_meta_id) if ext_meta_id else None

            mappings.append(
                {
                    "source_column": source_info.get("column_name"),
                    "source_component": source_info.get("component_name"),
                    "source_lineage_id": source_lineage_id,
                    "pipeline_input_column": col_el.get("cachedName"),
                    "destination_column": ext_meta_entry.get("name")
                    if ext_meta_entry
                    else None,
                    "destination_external_metadata_id": ext_meta_id,
                }
            )
    return mappings


def _iter_input_elements(component_el: ET.Element):
    inputs_el = component_el.find("inputs")
    if inputs_el is None:
        return []
    return inputs_el.findall("input")


def _iter_input_columns(input_el: ET.Element):
    input_columns_el = input_el.find("inputColumns")
    if input_columns_el is None:
        return []
    return input_columns_el.findall("inputColumn")


# ---------------------------------------------------------------------------
# Paths (precedencia entre componentes dentro del Data Flow)
# ---------------------------------------------------------------------------
_REF_NAME_PATTERN = re.compile(r"\\([^\\\.]+)\.(?:Outputs|Inputs)\[")


def _component_name_from_ref(ref_id: Optional[str]) -> Optional[str]:
    """
    Extrae el nombre de componente de un refId con forma
    'Package\\<DataFlow>\\<Componente>.Outputs[<Output>]...'.
    Devuelve None si el refId no matchea el patron esperado (no se asume nada).
    """
    if not ref_id:
        return None
    match = _REF_NAME_PATTERN.search(ref_id)
    return match.group(1) if match else None


def extract_paths(pipeline_el: ET.Element) -> List[Dict[str, Any]]:
    paths_el = pipeline_el.find("paths")
    if paths_el is None:
        return []

    paths = []
    for path_el in paths_el.findall("path"):
        start_id = path_el.get("startId")
        end_id = path_el.get("endId")
        paths.append(
            {
                "from": _component_name_from_ref(start_id),
                "to": _component_name_from_ref(end_id),
                "ref_id": path_el.get("refId"),
                "name": path_el.get("name"),
                "start_id": start_id,
                "end_id": end_id,
            }
        )
    return paths


# ---------------------------------------------------------------------------
# Builders especificos por tipo de componente (dispatch table extensible)
# ---------------------------------------------------------------------------
def _build_generic(component_el, component_type, common, lineage_index):
    """Fallback para componentClassID no mapeados: no pierde informacion,
    pero no intenta producir campos de conveniencia (sql_command, mappings, etc).
    El split outputs/error_outputs ya lo hizo parse_component() de forma
    generica, asi que un componente desconocido queda con la misma forma
    basica (inputs/outputs/error_outputs/raw_properties) que uno conocido."""
    return common


def _build_teradata_source(component_el, component_type, common, lineage_index):
    props = properties_as_dict(common["raw_properties"])
    common["properties"] = {
        "sql_command": props.get("SqlCommand"),
        "table_name": props.get("TableName"),
        "block_size": _to_int(props.get("BlockSize")),
        "access_mode": props.get("AccessMode"),
    }
    return common


def _build_ole_db_source(component_el, component_type, common, lineage_index):
    props = properties_as_dict(common["raw_properties"])
    common["properties"] = {
        "open_rowset": props.get("OpenRowset"),
        "sql_command": props.get("SqlCommand"),
        "access_mode": props.get("AccessMode"),
    }
    return common


def _build_data_conversion(component_el, component_type, common, lineage_index):
    return common


def _build_derived_column(component_el, component_type, common, lineage_index):
    """
    Sin propiedades a nivel de componente: toda la logica esta en
    Expression/FriendlyExpression de cada outputColumn (ya expuestas de forma
    generica por extract_output_columns). No hace falta un builder mas alla
    del generico, pero se registra explicitamente para dejar constancia de
    que fue analizado (y no dejarlo caer en el fallback por accidente).
    """
    return common


def _build_conditional_split(component_el, component_type, common, lineage_index):
    """
    Sin propiedades a nivel de componente: cada rama de salida trae su propia
    Expression/FriendlyExpression/EvaluationOrder (ya expuestas de forma
    generica por extract_outputs). Idem Derived Column: registrado por
    explicitud, no por necesidad de logica extra.
    """
    return common


def _build_merge_join(component_el, component_type, common, lineage_index):
    props = properties_as_dict(common["raw_properties"])
    common["properties"] = {
        "join_type": props.get("JoinType"),
        "num_key_columns": _to_int(props.get("NumKeyColumns")),
        "treat_nulls_as_equal": props.get("TreatNullsAsEqual") == "true",
        "max_buffers_per_input": _to_int(props.get("MaxBuffersPerInput")),
    }
    return common


def _build_row_count(component_el, component_type, common, lineage_index):
    props = properties_as_dict(common["raw_properties"])
    common["properties"] = {
        "variable_name": props.get("VariableName"),
    }
    return common


def _build_ole_db_destination(component_el, component_type, common, lineage_index):
    props = properties_as_dict(common["raw_properties"])
    common["properties"] = {
        "open_rowset": props.get("OpenRowset"),
        "access_mode": props.get("AccessMode"),
        "fast_load_options": props.get("FastLoadOptions"),
        "fast_load_max_insert_commit_size": _to_int(
            props.get("FastLoadMaxInsertCommitSize")
        ),
        "sql_command": props.get("SqlCommand"),
    }
    common["mappings"] = extract_mappings(component_el, lineage_index)
    return common


# Dispatch table: agregar aca un builder nuevo para soportar otro tipo de
# componente sin tocar parse_component(). Ver README.md "Como extenderlo".
COMPONENT_BUILDERS: Dict[str, Callable] = {
    "teradata_source": _build_teradata_source,
    "ole_db_source": _build_ole_db_source,
    "data_conversion": _build_data_conversion,
    "derived_column": _build_derived_column,
    "conditional_split": _build_conditional_split,
    "merge_join": _build_merge_join,
    "row_count": _build_row_count,
    "ole_db_destination": _build_ole_db_destination,
}


def parse_component(
    component_el: ET.Element, lineage_index: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Parsea un <component> generico y luego aplica un builder especifico segun
    su tipo (si existe), agregando campos de conveniencia sin perder la
    informacion cruda (raw_properties, inputs, outputs/error_outputs quedan
    disponibles para cualquier tipo, conocido o no).
    """
    component_type = classify_component(component_el)
    raw_properties = extract_raw_properties(component_el)
    connections = extract_connections(component_el)
    normal_outputs, error_outputs = split_outputs(extract_outputs(component_el))

    common: Dict[str, Any] = {
        "name": component_el.get("name"),
        "ref_id": component_el.get("refId"),
        "class_id": component_el.get("componentClassID"),
        "type": component_type,
        "version": component_el.get("version"),
        "description": component_el.get("description"),
        # validateExternalMetadata: atributo DIRECTO del <component> (no una
        # <property>). Solo aparece explicito en un componente de todo el
        # corpus (el OLE DB Destination de 'Tarea Flujo Staging', que escribe
        # en la tabla temporal #ClientesStage) con valor "False" — coherente
        # con DelayValidation=True del Data Flow que lo contiene. Se guarda
        # el valor crudo (None si el atributo no esta presente en el XML;
        # no se asume un default) porque la ausencia y el "False" explicito
        # son cosas distintas y no hay evidencia en el corpus de cual es el
        # default real cuando se omite.
        "validate_external_metadata": component_el.get("validateExternalMetadata"),
        "connection": primary_connection_ref(connections),
        "connections": connections,
        "raw_properties": raw_properties,
        "inputs": extract_inputs(component_el),
        "outputs": normal_outputs,
        "error_outputs": error_outputs,
    }

    builder = COMPONENT_BUILDERS.get(component_type, _build_generic)
    return builder(component_el, component_type, common, lineage_index)


# ---------------------------------------------------------------------------
# Data Flow / Package
# ---------------------------------------------------------------------------
def parse_data_flow(dataflow_executable: ET.Element) -> Optional[Dict[str, Any]]:
    pipeline_el = get_pipeline_element(dataflow_executable)
    if pipeline_el is None:
        return None

    components_el = iter_components(pipeline_el)
    lineage_index = build_lineage_index(components_el)

    return {
        "name": dataflow_executable.get(dts("ObjectName")),
        "ref_id": dataflow_executable.get(dts("refId")),
        "components": [
            parse_component(c, lineage_index) for c in components_el
        ],
        "paths": extract_paths(pipeline_el),
    }


def parse_package(root: ET.Element, source_file: Optional[str] = None) -> Dict[str, Any]:
    """
    source_file es el nombre fisico del archivo .dtsx (informativo, para
    trazabilidad). Es intencionalmente distinto de package_name (DTS:ObjectName),
    que es el nombre interno del paquete y puede no coincidir con el nombre
    de archivo (ej. BipSuc_CampaniasVigentes.dtsx tiene DTS:ObjectName="Package1").

    data_flows sigue siendo una lista PLANA de todos los Data Flow Tasks del
    paquete (encontrados a cualquier profundidad, incluso dentro de Sequence
    Containers), con la misma forma que en la version anterior del parser —
    para no romper consumidores existentes. control_flow expone ademas el
    arbol completo de Executables/containers/precedence constraints tal como
    aparecen en el XML, mas las variables de paquete.
    """
    package_name = root.get(dts("ObjectName"))

    data_flows = [
        df
        for df in (
            parse_data_flow(exe) for exe in find_data_flow_executables(root)
        )
        if df is not None
    ]

    top_executables_el = root.find(dts("Executables"))
    control_flow_executables = [
        parse_executable(exe)
        for exe in (
            top_executables_el.findall(dts("Executable"))
            if top_executables_el is not None
            else []
        )
    ]
    control_flow_precedence = extract_precedence_constraints(root)

    guid_index = build_connection_guid_index(data_flows)
    resolve_execute_sql_task_connections(control_flow_executables, guid_index)

    # annotate_execution_roles es DERIVADO (ver comentario junto a su
    # definicion): agrega enabled/effective_enabled/reachable/execution_role/
    # parent_ref_id a cada nodo de control_flow_executables, mutandolos
    # in-place. Correrlo aca (con ancestor_effective_enabled=True, el Package
    # siempre esta "enabled") asegura que tanto control_flow como
    # active_execution_graph (que depende de estos campos) vean el arbol ya
    # anotado.
    annotate_execution_roles(control_flow_executables, control_flow_precedence)

    control_flow = {
        "executables": control_flow_executables,
        "precedence_constraints": control_flow_precedence,
    }

    return {
        "source_file": source_file,
        "package_name": package_name,
        "package_dtsid": root.get(dts("DTSID")),
        "variables": extract_variables(root),
        # control_flow: el arbol COMPLETO tal como aparece en el XML,
        # incluyendo logica deshabilitada y/o huerfana (nunca se descarta).
        "control_flow": control_flow,
        # active_execution_graph: vista DERIVADA (ver build_active_execution_graph)
        # con SOLO lo que efectivamente puede ejecutarse. No reemplaza a
        # control_flow, es un recorte de el.
        "active_execution_graph": build_active_execution_graph(control_flow),
        # data_flows: lista PLANA de TODOS los Data Flow Tasks del paquete,
        # incluidos los deshabilitados/huerfanos (ver control_flow para su
        # execution_role) — se preservan como evidencia de patrones XML de
        # componentes (Merge Join, Conditional Split, etc.) aunque no
        # participen de la arquitectura funcional activa.
        "data_flows": data_flows,
    }


# ---------------------------------------------------------------------------
# Serializacion
# ---------------------------------------------------------------------------
def to_json(data: Dict[str, Any], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_file(path: str) -> Dict[str, Any]:
    """Punto de entrada de alto nivel: .dtsx -> representacion intermedia (dict)."""
    root = load_package(path)
    return parse_package(root, source_file=os.path.basename(path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return value
