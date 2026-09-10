"""
conmgr_writer.py

Genera archivos .conmgr para TERADATA y OLEDB -- espejo de escritura de
project_context/conmgr_parser.py. Mismo patron de dispatch por provider
(CONMGR_PROVIDER_BUILDERS alla lee, CONMGR_PROVIDER_WRITERS aca escribe).

Estructura comun (confirmada contra los 3 proyectos reales auditados):

    <DTS:ConnectionManager DTS:ObjectName=... DTS:DTSID=... DTS:CreationName="<PROVIDER>">
      <DTS:PropertyExpression DTS:Name="...">@[$Project::<param>]</DTS:PropertyExpression>  (0+)
      <DTS:ObjectData>
        <DTS:ConnectionManager>...especifico del provider...</DTS:ConnectionManager>
      </DTS:ObjectData>
    </DTS:ConnectionManager>

NUNCA se escribe TeraPassword ni DTS:Password, ni ningun atributo de cifrado
(Salt/IV/Algorithm) -- ver docs/project_generation_v1.md, seccion de
seguridad. Un Connection Manager generado con una property de password nunca
lleva su credencial: si el spec quiere parametrizarla, debe hacerlo via
property_expressions -> $Project::<parametro sensible> (shell sin valor).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Optional

from .xml_helpers import DTS_NS, dts, new_connection_dtsid

DEFAULT_TERADATA_AUTHENTICATION = "TD2"
DEFAULT_TERADATA_RETAIN = False
TERADATA_DRIVER = "Teradata Database ODBC Driver 20.00"

OLEDB_PROVIDER = "SQLOLEDB.1"
OLEDB_CONNECT_RETRY_COUNT = "1"
OLEDB_CONNECT_RETRY_INTERVAL = "5"

# Application Name: la evidencia real es INCONSISTENTE entre proyectos
# ('SSIS-Package-{GUID}...' en BipSuc/PagosYRecaudaciones vs.
# 'SSIS-Control_Flow_Base-{GUID}...' -- el propio nombre del proyecto -- en
# SSDT_Golden), y ProjectSpec v1 no tiene ningun campo de "nombre de
# proyecto" (esta fuera de scope, no se genera/muta .dtproj). Por eso NO se
# imita ningun patron real puntual: se usa un prefijo propio, estable y
# documentado, con la MISMA forma general 'SSIS-<X>-{GUID}<nombre>' que los
# 3 ejemplos reales comparten -- ver docs/project_generation_v1.md.
FACTORY_APPLICATION_NAME_PREFIX = "SSIS-Factory"


def _build_property_expressions(expressions: Optional[list], root: ET.Element) -> None:
    """v1 solo soporta la forma estructurada 'property' + 'parameter' (ya
    validada por spec_schema) -- se serializa siempre como
    '@[$Project::<parameter>]', nunca una expresion cruda."""
    for expr in expressions or []:
        expr_el = ET.SubElement(root, dts("PropertyExpression"))
        expr_el.set(dts("Name"), expr["property"])
        expr_el.text = f"@[$Project::{expr['parameter']}]"


def _teradata_connection_string(conn: Dict[str, Any]) -> str:
    """Mismo formato exacto observado en los 3 .conmgr TERADATA reales
    (DBCNAME/UID/AUTHENTICATION/DATABASE/CHARSET/DRIVER/LOGINTIMEOUT)."""
    authentication = conn.get("authentication", DEFAULT_TERADATA_AUTHENTICATION)
    return (
        f"DBCNAME={conn['server']};"
        f"UID={conn['user']};"
        f"AUTHENTICATION={authentication};"
        f"DATABASE={conn['database']};"
        f"CHARSET={conn['charset']};"
        f"DRIVER={{{TERADATA_DRIVER}}};"
        "LOGINTIMEOUT=20;"
    )


def _build_teradata(conn: Dict[str, Any], dtsid: str) -> ET.Element:
    root = ET.Element(dts("ConnectionManager"))
    root.set(dts("ObjectName"), conn["name"])
    root.set(dts("DTSID"), dtsid)
    root.set(dts("CreationName"), "TERADATA")

    _build_property_expressions(conn.get("property_expressions"), root)

    object_data_el = ET.SubElement(root, dts("ObjectData"))
    cm_el = ET.SubElement(object_data_el, dts("ConnectionManager"))

    def add(tag: str, text: Optional[str]) -> None:
        el = ET.SubElement(cm_el, tag)
        el.text = text

    retain = conn.get("retain_same_connection", DEFAULT_TERADATA_RETAIN)
    authentication = conn.get("authentication", DEFAULT_TERADATA_AUTHENTICATION)

    add("TeraConnectionString", _teradata_connection_string(conn))
    # TeraRetain: unico caso real donde RetainSameConnection SI se serializa
    # dentro del propio .conmgr -- PARTIAL, no sustituye
    # ProjectConnectionParameters de .dtproj (fuera de scope). Ver docs.
    add("TeraRetain", "True" if retain else "False")
    add("TeraInitialCatalog", None)
    add("TeraServerName", conn["server"])
    add("TeraUserName", conn["user"])
    add("TeraDatabase", conn["database"])
    add("TeraAccount", None)
    add("TeraAuthentication", authentication)
    add("TeraWinAuthentication", "False")
    # Sin evidencia real de un caso True en el corpus (Golden usa
    # CHARSET=UTF8 en la connection string y aun asi este campo es False) --
    # no se deriva de 'charset', se deja fijo en el unico valor observado.
    add("TeraUseUTF8CharSet", "False")

    return root


def _oledb_connection_string(conn: Dict[str, Any], dtsid: str) -> str:
    app_name = f"{FACTORY_APPLICATION_NAME_PREFIX}-{dtsid}{conn['name']}"
    return (
        f"Data Source={conn['server']};"
        f"User ID={conn['user']};"
        f"Initial Catalog={conn['catalog']};"
        f"Provider={OLEDB_PROVIDER};"
        "Auto Translate=False;"
        f"Application Name={app_name};"
    )


def _build_oledb(conn: Dict[str, Any], dtsid: str) -> ET.Element:
    root = ET.Element(dts("ConnectionManager"))
    root.set(dts("ObjectName"), conn["name"])
    root.set(dts("DTSID"), dtsid)
    root.set(dts("CreationName"), "OLEDB")

    _build_property_expressions(conn.get("property_expressions"), root)

    object_data_el = ET.SubElement(root, dts("ObjectData"))
    cm_el = ET.SubElement(object_data_el, dts("ConnectionManager"))
    cm_el.set(dts("ConnectRetryCount"), OLEDB_CONNECT_RETRY_COUNT)
    cm_el.set(dts("ConnectRetryInterval"), OLEDB_CONNECT_RETRY_INTERVAL)
    cm_el.set(dts("ConnectionString"), _oledb_connection_string(conn, dtsid))
    # Nunca se agrega DTS:Password -- ver docstring del modulo. Tampoco existe
    # ningun equivalente a RetainSameConnection para OLEDB (rechazado ya en
    # spec_schema si el spec lo pide).

    return root


CONMGR_PROVIDER_WRITERS: Dict[str, Callable[[Dict[str, Any], str], ET.Element]] = {
    "teradata": _build_teradata,
    "oledb": _build_oledb,
}


def build_conmgr_tree(conn: Dict[str, Any]) -> ET.Element:
    """Asume que 'conn' ya paso spec_schema.assert_valid_project_spec()."""
    dtsid = new_connection_dtsid()
    builder = CONMGR_PROVIDER_WRITERS[conn["provider"]]
    return builder(conn, dtsid)


def write_conmgr_file(conn: Dict[str, Any], output_path: str) -> None:
    ET.register_namespace("DTS", DTS_NS)
    root = build_conmgr_tree(conn)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
