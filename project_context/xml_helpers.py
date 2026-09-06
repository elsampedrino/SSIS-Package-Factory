"""
xml_helpers.py

Utilidades XML compartidas para leer archivos de contexto de proyecto SSIS
(.dtproj, Project.params, .conmgr).

Namespaces identicos a los usados en ssis_parser.py / generator/xml_helpers.py
-- se duplican aca a proposito para mantener project_context/ independiente
en tiempo de ejecucion de los otros dos paquetes (mismo criterio ya aplicado
en generator/xml_helpers.py).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

DTS_NS = "www.microsoft.com/SqlServer/Dts"
SSIS_NS = "www.microsoft.com/SqlServer/SSIS"


def dts(tag: str) -> str:
    return f"{{{DTS_NS}}}{tag}"


def ssis(tag: str) -> str:
    return f"{{{SSIS_NS}}}{tag}"


def extract_ssis_properties(properties_el: Optional[ET.Element]) -> Dict[str, Dict[str, Any]]:
    """
    Lee un <SSIS:Properties> generico -- la MISMA forma se repite en
    Project.params, en .dtproj/ProjectConnectionParameters y en
    .dtproj/PackageMetaData: una lista de <SSIS:Property SSIS:Name="...">
    con texto y, opcionalmente, atributos de encriptacion.

    Devuelve un dict name -> {"text": str|None, "is_encrypted": bool}.
    is_encrypted refleja el atributo SSIS:Sensitive="1" DIRECTO sobre esa
    property puntual (normalmente la property "Value" cuando el parametro es
    sensible) -- no la property HERMANA llamada "Sensitive" que describe al
    parametro en su conjunto; esa se interpreta en cada caller porque su
    significado depende de la estructura que la contiene.
    """
    result: Dict[str, Dict[str, Any]] = {}
    if properties_el is None:
        return result
    for prop_el in properties_el.findall(ssis("Property")):
        name = prop_el.get(ssis("Name"))
        if not name:
            continue
        text = prop_el.text if (prop_el.text and prop_el.text.strip()) else None
        result[name] = {
            "text": text,
            "is_encrypted": prop_el.get(ssis("Sensitive")) == "1",
        }
    return result


def redact_if_sensitive(
    prop_dict: Dict[str, Dict[str, Any]], declared_sensitive: bool
) -> Optional[str]:
    """
    Devuelve el texto de la property 'Value' de prop_dict, EXCEPTO si esta
    marcada como encriptada (SSIS:Sensitive='1' sobre la propia property) o
    si el parametro que la contiene fue declarado sensible (property
    hermana 'Sensitive'='1') -- en ambos casos se devuelve None. Nunca se
    propaga un blob cifrado como si fuera un valor utilizable.
    """
    value_entry = prop_dict.get("Value", {})
    if declared_sensitive or value_entry.get("is_encrypted"):
        return None
    return value_entry.get("text")
