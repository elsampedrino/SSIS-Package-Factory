"""
conmgr_parser.py

Lee un archivo .conmgr (Connection Manager de proyecto).

Estructura COMUN a cualquier provider (confirmada contra cnxTeradata.conmgr
y cnxSrvBsLogSBD01.conmgr, los dos providers reales del proyecto BipSuc):

    <DTS:ConnectionManager DTS:ObjectName=... DTS:DTSID=... DTS:CreationName="<PROVIDER>">
      <DTS:PropertyExpression DTS:Name="...">...</DTS:PropertyExpression>  (0+, hijos DIRECTOS de la raiz)
      <DTS:ObjectData>
        <DTS:ConnectionManager>...contenido especifico del provider...</DTS:ConnectionManager>
      </DTS:ObjectData>
    </DTS:ConnectionManager>

DTS:CreationName en un .conmgr ES el nombre del provider directamente
(ej. "TERADATA", "OLEDB") -- uso distinto del mismo atributo en un .dtsx,
donde identifica un tipo de tarea/componente (ej. "Microsoft.ExecuteSQLTask").

IMPORTANTE (confirmado con evidencia real, no asumido): el contenido de
DTS:ObjectData/DTS:ConnectionManager NO se serializa igual para todos los
providers.
  - TERADATA usa elementos hijos PLANOS, sin namespace (Tera*: TeraServerName,
    TeraRetain, TeraPassword, etc.).
  - OLEDB usa ATRIBUTOS con namespace DTS sobre el propio elemento
    (DTS:ConnectionString, DTS:ConnectRetryCount, ...) mas un hijo
    DTS:Password para la credencial cifrada, y NO trae ningun equivalente a
    RetainSameConnection ahi dentro (solo se pudo resolver via .dtproj, ver
    context_builder.py).

Por eso la extraccion del ObjectData es un dispatch por provider (mismo
patron COMPONENT_TYPE_MAP/EXECUTABLE_TYPE_MAP ya usado en ssis_parser.py),
con un fallback generico para providers todavia no analizados (por ahora
solo TERADATA y OLEDB tienen builder especifico).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

from .xml_helpers import dts

UNKNOWN_PROVIDER_PREFIX = "unknown"

# '$Project::nombre' -- referencia a un Project Parameter dentro de una
# expresion SSIS. Puede ser el valor COMPLETO de la expresion
# ('@[$Project::pwSrvBsLogSBD01]') o aparecer embebida dentro de una
# expresion mas larga con operadores (confirmado en
# cnxSrvBsLogSBD01.conmgr/InitialCatalog: '@[$Project::ambiente] == "QA" ? ...').
_PROJECT_PARAM_REF_PATTERN = re.compile(r"\$Project::(\w+)")


def find_project_parameter_references(expression: str) -> List[str]:
    """Todas las referencias '$Project::nombre' encontradas en una expresion,
    en el orden en que aparecen. Lista vacia si no hay ninguna."""
    return _PROJECT_PARAM_REF_PATTERN.findall(expression or "")


def extract_property_expressions(root: ET.Element) -> List[Dict[str, Any]]:
    expressions = []
    for expr_el in root.findall(dts("PropertyExpression")):
        expression_text = expr_el.text or ""
        expressions.append(
            {
                "property": expr_el.get(dts("Name")),
                "expression": expression_text,
                "referenced_parameters": find_project_parameter_references(expression_text),
            }
        )
    return expressions


def _to_bool(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    return text.strip().lower() == "true"


def _to_int(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_teradata_object_data(object_data_cm_el: ET.Element) -> Dict[str, Any]:
    def _text(tag: str) -> Optional[str]:
        el = object_data_cm_el.find(tag)
        return el.text if el is not None and el.text is not None else None

    password_el = object_data_cm_el.find("TeraPassword")
    return {
        "server_name": _text("TeraServerName"),
        "database": _text("TeraDatabase"),
        "user_name": _text("TeraUserName"),
        "authentication": _text("TeraAuthentication"),
        # TeraRetain: unico caso observado donde RetainSameConnection SI se
        # serializa dentro del propio .conmgr (provider-specific).
        "retain_same_connection": _to_bool(_text("TeraRetain")),
        "password_is_encrypted": password_el is not None and password_el.get("Sensitive") == "1",
    }


def _parse_oledb_object_data(object_data_cm_el: ET.Element) -> Dict[str, Any]:
    password_el = object_data_cm_el.find(dts("Password"))
    return {
        "connection_string": object_data_cm_el.get(dts("ConnectionString")),
        "connect_retry_count": _to_int(object_data_cm_el.get(dts("ConnectRetryCount"))),
        "connect_retry_interval": _to_int(object_data_cm_el.get(dts("ConnectRetryInterval"))),
        # Confirmado: OLE DB NO trae aca ningun equivalente a
        # RetainSameConnection. Se deja explicito en None -- no se asume.
        "retain_same_connection": None,
        "password_is_encrypted": password_el is not None and password_el.get("Sensitive") == "1",
    }


# Dispatch table: agregar aca un builder nuevo para soportar otro provider
# sin tocar parse_conmgr_file(). Mismo criterio que COMPONENT_BUILDERS de
# ssis_parser.py / generator/campanias_generator.py.
CONMGR_PROVIDER_BUILDERS: Dict[str, Callable[[ET.Element], Dict[str, Any]]] = {
    "TERADATA": _parse_teradata_object_data,
    "OLEDB": _parse_oledb_object_data,
}


def _build_generic_object_data(object_data_cm_el: ET.Element) -> Dict[str, Any]:
    """Fallback para un provider todavia no soportado: no se pierde el nodo,
    pero no se interpretan sus atributos/hijos especificos."""
    return {"raw_tag": object_data_cm_el.tag}


def parse_conmgr_file(path: str) -> Dict[str, Any]:
    root = ET.parse(path).getroot()

    name = root.get(dts("ObjectName"))
    dtsid = root.get(dts("DTSID"))
    provider = root.get(dts("CreationName")) or ""

    object_data_el = root.find(dts("ObjectData"))
    object_data_cm_el = (
        object_data_el.find(dts("ConnectionManager")) if object_data_el is not None else None
    )

    is_known_provider = provider in CONMGR_PROVIDER_BUILDERS
    builder = CONMGR_PROVIDER_BUILDERS.get(provider, _build_generic_object_data)
    provider_data = builder(object_data_cm_el) if object_data_cm_el is not None else {}
    provider_key = provider if is_known_provider else f"{UNKNOWN_PROVIDER_PREFIX}:{provider}"

    return {
        "name": name,
        "dtsid": dtsid,
        "provider": provider_key,
        "property_expressions": extract_property_expressions(root),
        "provider_data": provider_data,
    }
