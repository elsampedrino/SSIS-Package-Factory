"""
xml_helpers.py

Utilidades de bajo nivel para el generador: namespaces, formulas
deterministas de refId/lineageId/externalMetadataColumnId (la MISMA formula
que ssis_parser ya valido al leer, aplicada ahora en sentido inverso para
escribir), y generacion de GUID nuevos donde el patron es opaque/generated.

Este modulo es intencionalmente independiente de ssis_parser.py: el
generador no depende en tiempo de ejecucion del analizador (solo los tests
cruzan ambos, para el chequeo de Nivel 1).
"""

from __future__ import annotations

import uuid
from typing import Optional


# ---------------------------------------------------------------------------
# Namespaces (identicos a los de ssis_parser.py — ver docs/xml_patterns.md §1)
# ---------------------------------------------------------------------------
NS = {
    "DTS": "www.microsoft.com/SqlServer/Dts",
    "SQLTask": "www.microsoft.com/sqlserver/dts/tasks/sqltask",
}


def dts(tag: str) -> str:
    return f"{{{NS['DTS']}}}{tag}"


def sqltask(tag: str) -> str:
    return f"{{{NS['SQLTask']}}}{tag}"


# El primer segmento de todo refId/lineageId es SIEMPRE el token literal
# "Package", independientemente de cual sea el DTS:ObjectName real del
# paquete (confirmado contra los dos archivos de referencia: ambos tienen
# DTS:refId="Package" en la raiz, con DTS:ObjectName distinto). No se debe
# parametrizar por el nombre del paquete.
PACKAGE_TOKEN = "Package"


# ---------------------------------------------------------------------------
# GUIDs opaque/generated
# ---------------------------------------------------------------------------
def new_guid() -> str:
    """Genera un GUID nuevo con el mismo formato observado en el XML
    (mayusculas, con llaves): '{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}'."""
    return "{" + str(uuid.uuid4()).upper() + "}"


# ---------------------------------------------------------------------------
# Connection Managers conocidos — ver docs/generator_mvp.md seccion 3.
#
# Estos GUID son REALES, observados en templates/campanias_base.dtsx (que a
# su vez es una copia de un paquete real y validado del proyecto). NO son
# inventados. Limitados al mismo proyecto SSIS que el paquete de referencia.
# ---------------------------------------------------------------------------
KNOWN_CONNECTION_MANAGERS = {
    "cnxTeradata": "{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}",
    "cnxSrvBsLogSBD01": "{5DA5808C-8489-48AC-8614-CB034DE02B61}",
}


class UnknownConnectionManagerError(Exception):
    """El process_spec pidio un Connection Manager que no se puede mapear
    inequivocamente a un GUID conocido del template."""


def resolve_connection_manager_id(name: str) -> str:
    """
    Devuelve el connectionManagerID completo ('{GUID}:external') para un
    nombre de Connection Manager conocido. Si el nombre no esta en
    KNOWN_CONNECTION_MANAGERS, falla explicitamente — NUNCA reutiliza el GUID
    de un nombre distinto ni asume una correspondencia por posicion/orden.
    """
    guid = KNOWN_CONNECTION_MANAGERS.get(name)
    if guid is None:
        known = ", ".join(sorted(KNOWN_CONNECTION_MANAGERS)) or "(ninguno)"
        raise UnknownConnectionManagerError(
            f"Connection Manager '{name}' no reconocido por este template. "
            f"Connection Managers soportados: {known}. "
            "No se puede generar el paquete sin un GUID real conocido: "
            "agregalo a KNOWN_CONNECTION_MANAGERS solo si corresponde al "
            "mismo proyecto SSIS (ver docs/generator_mvp.md, seccion 3)."
        )
    return f"{guid}:external"


def connection_manager_ref_id(name: str) -> str:
    """'Project.ConnectionManagers[<name>]' — determinista por nombre."""
    return f"Project.ConnectionManagers[{name}]"


# ---------------------------------------------------------------------------
# refId / lineageId / externalMetadataColumnId — formulas deterministas
# (ver docs/xml_patterns.md §7). El primer segmento es siempre PACKAGE_TOKEN,
# nunca el DTS:ObjectName real del paquete.
# ---------------------------------------------------------------------------
def component_ref(data_flow: str, component: str) -> str:
    return f"{PACKAGE_TOKEN}\\{data_flow}\\{component}"


def connection_ref(data_flow: str, component: str, connection_name: str) -> str:
    return f"{component_ref(data_flow, component)}.Connections[{connection_name}]"


def output_ref(data_flow: str, component: str, output: str) -> str:
    return f"{component_ref(data_flow, component)}.Outputs[{output}]"


def input_ref(data_flow: str, component: str, input_name: str) -> str:
    return f"{component_ref(data_flow, component)}.Inputs[{input_name}]"


def output_column_ref(data_flow: str, component: str, output: str, column: str) -> str:
    return f"{output_ref(data_flow, component, output)}.Columns[{column}]"


def input_column_ref(data_flow: str, component: str, input_name: str, column: str) -> str:
    return f"{input_ref(data_flow, component, input_name)}.Columns[{column}]"


def output_external_column_ref(
    data_flow: str, component: str, output: str, column: str
) -> str:
    return f"{output_ref(data_flow, component, output)}.ExternalColumns[{column}]"


def input_external_column_ref(
    data_flow: str, component: str, input_name: str, column: str
) -> str:
    return f"{input_ref(data_flow, component, input_name)}.ExternalColumns[{column}]"


def path_ref(data_flow: str, path_name: str) -> str:
    return f"{PACKAGE_TOKEN}\\{data_flow}.Paths[{path_name}]"


def id_reference_wrapper(ref: str) -> str:
    """Envuelve una referencia en el formato '#{...}' usado por properties
    containsID='true' (ej. SourceInputColumnLineageID)."""
    return f"#{{{ref}}}"


def dataflow_executable_ref(data_flow: str) -> str:
    return f"{PACKAGE_TOKEN}\\{data_flow}"
