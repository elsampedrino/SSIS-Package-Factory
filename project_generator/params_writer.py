"""
params_writer.py

Genera Project.params -- espejo de escritura de
project_context/params_parser.py. Forma evidenciada contra los 3 proyectos
reales auditados (BipSuc, PagosYRecaudaciones, SSDT_Golden):

    <SSIS:Parameters xmlns:SSIS="www.microsoft.com/SqlServer/SSIS">
      <SSIS:Parameter SSIS:Name="...">
        <SSIS:Properties>
          <SSIS:Property SSIS:Name="ID">{minusculas-con-guiones}</SSIS:Property>
          <SSIS:Property SSIS:Name="CreationName"></SSIS:Property>
          <SSIS:Property SSIS:Name="Description"></SSIS:Property>
          <SSIS:Property SSIS:Name="IncludeInDebugDump">0</SSIS:Property>
          <SSIS:Property SSIS:Name="Required">1</SSIS:Property>
          <SSIS:Property SSIS:Name="Sensitive">0</SSIS:Property>
          <SSIS:Property SSIS:Name="Value">...</SSIS:Property>  <!-- OMITIDA si sensitive -->
          <SSIS:Property SSIS:Name="DataType">18</SSIS:Property>
        </SSIS:Properties>
      </SSIS:Parameter>
    </SSIS:Parameters>

Sin parametros: raiz self-closing (confirmado valido con
Examples/Originals/SSDT_Golden/Project.params).

Property 'Value' OMITIDA POR COMPLETO cuando sensitive=true -- no vacia, no
con placeholder: evidencia real en SSDT_Golden/Control_Flow_Base.dtproj
(CM.SRVBSQADB.Optimus.usSxSSIS.Password, MISMA forma SSIS:Properties que
Project.params) muestra exactamente ese patron para un parametro sensible
sin valor persistido -- es el unico comportamiento evidenciado para "shell
sin valor", no una convencion inventada por este modulo.

DataType: unicamente '18' (String) -- ningun parametro no-sensible de
Project.params observado en el corpus usa otro codigo (los codigos '3'/'9'
vistos en .dtproj/ProjectConnectionParameters pertenecen a un mecanismo
distinto -- ver docs/project_generation_v1.md). v1 no soporta otro tipo.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .xml_helpers import SSIS_NS, new_parameter_id, ssis

PARAMETER_DATA_TYPE_STRING = "18"


def _add_property(props_el: ET.Element, name: str, text: Optional[str] = None) -> ET.Element:
    prop_el = ET.SubElement(props_el, ssis("Property"))
    prop_el.set(ssis("Name"), name)
    if text is not None:
        prop_el.text = text
    return prop_el


def _build_parameter_element(param: Dict[str, Any]) -> ET.Element:
    sensitive = bool(param["sensitive"])
    required = bool(param.get("required", True))

    param_el = ET.Element(ssis("Parameter"))
    param_el.set(ssis("Name"), param["name"])
    props_el = ET.SubElement(param_el, ssis("Properties"))

    _add_property(props_el, "ID", new_parameter_id())
    _add_property(props_el, "CreationName")
    _add_property(props_el, "Description", param.get("description"))
    _add_property(props_el, "IncludeInDebugDump", "0")
    _add_property(props_el, "Required", "1" if required else "0")
    _add_property(props_el, "Sensitive", "1" if sensitive else "0")
    if not sensitive:
        _add_property(props_el, "Value", param["value"])
    _add_property(props_el, "DataType", PARAMETER_DATA_TYPE_STRING)

    return param_el


def build_params_tree(parameters: List[Dict[str, Any]]) -> ET.Element:
    root = ET.Element(ssis("Parameters"))
    for param in parameters:
        root.append(_build_parameter_element(param))
    return root


def write_params_file(parameters: List[Dict[str, Any]], output_path: str) -> None:
    """Asume que 'parameters' ya paso spec_schema.assert_valid_project_spec()
    -- este modulo no vuelve a validar."""
    ET.register_namespace("SSIS", SSIS_NS)
    root = build_params_tree(parameters)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
