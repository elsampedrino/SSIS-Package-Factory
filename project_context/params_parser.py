"""
params_parser.py

Lee Project.params: SSIS:Parameters/SSIS:Parameter/SSIS:Properties/SSIS:Property.

Confirmado contra el archivo real de BipSuc: cada Parameter trae ID
(GUID opaco), CreationName/Description (vacios), IncludeInDebugDump,
Required, Sensitive, Value (texto plano, o bloque cifrado con
Salt/IV/Algorithm cuando Sensitive='1') y DataType (codigo numerico -- se
expone tal cual, sin interpretar la tabla completa de codigos, mismo
criterio que ssis_parser.py con DTS:VariableValue/@DataType).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .xml_helpers import extract_ssis_properties, redact_if_sensitive, ssis


def parse_params_file(path: str) -> List[Dict[str, Any]]:
    root = ET.parse(path).getroot()
    parameters = []

    for param_el in root.findall(ssis("Parameter")):
        name = param_el.get(ssis("Name"))
        properties_el = param_el.find(ssis("Properties"))
        props = extract_ssis_properties(properties_el)

        sensitive = props.get("Sensitive", {}).get("text") == "1"
        required = props.get("Required", {}).get("text") == "1"

        parameters.append(
            {
                "name": name,
                "id": props.get("ID", {}).get("text"),
                "required": required,
                "sensitive": sensitive,
                "data_type_code": props.get("DataType", {}).get("text"),
                # NUNCA el texto cifrado: None si sensitive o si la property
                # 'Value' esta marcada individualmente como encriptada.
                "value": redact_if_sensitive(props, sensitive),
            }
        )

    return parameters
