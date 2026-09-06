"""
dtproj_parser.py

Lee un archivo .dtproj (manifiesto del proyecto SSIS).

Estructura real observada (BipSuc.dtproj):
- Elementos de nivel superior (TargetServerVersion dentro de
  Configurations/Configuration/Options, etc.) SIN namespace.
- DeploymentModelSpecificContent/Manifest/SSIS:Project (namespace SSIS,
  IDENTICO al de Project.params) con el resto de la metadata:
  nombre de proyecto, packages registrados, archivos .conmgr referenciados
  (por NOMBRE DE ARCHIVO, no por DTSID), y ProjectConnectionParameters.

ProjectConnectionParameters es un mecanismo de parametrizacion a nivel de
PROYECTO, DISTINTO y COMPLEMENTARIO de Project.params + PropertyExpression:
son propiedades del propio Connection Manager (ServerName, Database,
RetainSameConnection, etc.) expuestas con nombre compuesto
'CM.<ConnectionManager>.<Propiedad>' y su valor directo (no una referencia
'$Project::...'). Confirmado que RetainSameConnection vive de forma
confiable y UNIFORME (misma forma para TERADATA y OLEDB) unicamente aca --
ver docs/project_context.md.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .xml_helpers import extract_ssis_properties, redact_if_sensitive, ssis


def _find_target_server_version(root: ET.Element) -> Optional[str]:
    el = root.find("Configurations/Configuration/Options/TargetServerVersion")
    return el.text if el is not None else None


def _find_ssis_project_element(root: ET.Element) -> Optional[ET.Element]:
    return root.find(f"DeploymentModelSpecificContent/Manifest/{ssis('Project')}")


def _parse_project_connection_parameters(
    ssis_project_el: ET.Element,
) -> Dict[str, Dict[str, Any]]:
    """
    Agrupa los parametros con nombre compuesto 'CM.<ConnectionManager>.<Propiedad>'
    por Connection Manager:
        {"cnxTeradata": {"RetainSameConnection": {"value": "false", ...}, ...}, ...}

    El nombre se parte en el PRIMER '.' despues de 'CM.' -- confirmado que
    ningun nombre de Connection Manager ni de propiedad del proyecto real
    contiene puntos, asi que esto resuelve (conn_name, prop_name) sin
    ambiguedad para todos los casos observados.
    """
    result: Dict[str, Dict[str, Any]] = {}
    params_el = ssis_project_el.find(
        f"{ssis('DeploymentInfo')}/{ssis('ProjectConnectionParameters')}"
    )
    if params_el is None:
        return result

    for param_el in params_el.findall(ssis("Parameter")):
        full_name = param_el.get(ssis("Name")) or ""
        if not full_name.startswith("CM."):
            continue
        rest = full_name[len("CM."):]
        conn_name, _, prop_name = rest.partition(".")
        if not conn_name or not prop_name:
            continue

        properties_el = param_el.find(ssis("Properties"))
        props = extract_ssis_properties(properties_el)
        sensitive = props.get("Sensitive", {}).get("text") == "1"

        result.setdefault(conn_name, {})[prop_name] = {
            "value": redact_if_sensitive(props, sensitive),
            "sensitive": sensitive,
            "required": props.get("Required", {}).get("text") == "1",
            "data_type_code": props.get("DataType", {}).get("text"),
        }

    return result


def _parse_package_metadata(ssis_project_el: ET.Element) -> List[Dict[str, Any]]:
    result = []
    packages_el = ssis_project_el.find(f"{ssis('DeploymentInfo')}/{ssis('PackageInfo')}")
    if packages_el is None:
        return result

    for pkg_el in packages_el.findall(ssis("PackageMetaData")):
        properties_el = pkg_el.find(ssis("Properties"))
        props = extract_ssis_properties(properties_el)
        result.append(
            {
                "file_name": pkg_el.get(ssis("Name")),
                "dtsid": props.get("ID", {}).get("text"),
                "name": props.get("Name", {}).get("text"),
                "version_guid": props.get("VersionGUID", {}).get("text"),
                "package_format_version": props.get("PackageFormatVersion", {}).get("text"),
                "protection_level": props.get("ProtectionLevel", {}).get("text"),
            }
        )
    return result


def parse_dtproj_file(path: str) -> Dict[str, Any]:
    root = ET.parse(path).getroot()
    ssis_project_el = _find_ssis_project_element(root)

    project_name = None
    protection_level = None
    packages_declared: List[Dict[str, Any]] = []
    connection_manager_files: List[str] = []
    connection_parameters: Dict[str, Dict[str, Any]] = {}
    package_metadata: List[Dict[str, Any]] = []

    if ssis_project_el is not None:
        protection_level = ssis_project_el.get(ssis("ProtectionLevel"))

        properties_el = ssis_project_el.find(ssis("Properties"))
        props = extract_ssis_properties(properties_el)
        project_name = props.get("Name", {}).get("text")

        packages_el = ssis_project_el.find(ssis("Packages"))
        if packages_el is not None:
            for pkg_el in packages_el.findall(ssis("Package")):
                packages_declared.append(
                    {
                        "file_name": pkg_el.get(ssis("Name")),
                        "is_entry_point": pkg_el.get(ssis("EntryPoint")) == "1",
                    }
                )

        conn_mgrs_el = ssis_project_el.find(ssis("ConnectionManagers"))
        if conn_mgrs_el is not None:
            for cm_el in conn_mgrs_el.findall(ssis("ConnectionManager")):
                file_name = cm_el.get(ssis("Name"))
                if file_name:
                    connection_manager_files.append(file_name)

        connection_parameters = _parse_project_connection_parameters(ssis_project_el)
        package_metadata = _parse_package_metadata(ssis_project_el)

    return {
        "name": project_name,
        "protection_level": protection_level,
        "target_server_version": _find_target_server_version(root),
        "packages_declared": packages_declared,
        "connection_manager_files": connection_manager_files,
        "connection_parameters": connection_parameters,
        "package_metadata": package_metadata,
    }
