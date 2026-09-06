"""
context_builder.py

Consolida los tres archivos de Project Context (.dtproj, Project.params,
*.conmgr) en un unico dict "ProjectContext" (ver docs/project_context.md
para el modelo completo con ejemplo real).

No valida (ver validator.py) -- solo combina lo que cada parser extrajo por
separado, incluyendo la resolucion de RetainSameConnection, que puede
requerir cruzar .conmgr + .dtproj (confirmado con evidencia real: para
cnxSrvBsLogSBD01 -- OLE DB -- el .conmgr no trae RetainSameConnection en
absoluto, solo el .dtproj lo tiene; para cnxTeradata SI esta en ambos
lugares). _resolve_retain_same_connection() queda como funcion pura
separada, con .dtproj como fuente preferida (es la unica UNIFORME entre
providers) y .conmgr como fallback.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .conmgr_parser import parse_conmgr_file
from .dtproj_parser import parse_dtproj_file
from .params_parser import parse_params_file


def _to_bool(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    return text.strip().lower() == "true"


def _resolve_retain_same_connection(
    dtproj_raw_value: Optional[str], conmgr_value: Optional[bool]
) -> Tuple[Optional[bool], Optional[str]]:
    """
    Devuelve (valor, fuente). .dtproj se prefiere por ser la unica forma
    confirmada como UNIFORME entre providers (TERADATA y OLE DB usan la
    misma forma 'CM.<conn>.RetainSameConnection' ahi); .conmgr es fallback
    solo quando el provider lo serializa el mismo (confirmado unicamente
    para TERADATA via TeraRetain). Si ninguna fuente lo tiene, (None, None)
    -- no se asume un default.
    """
    if dtproj_raw_value is not None:
        return _to_bool(dtproj_raw_value), "dtproj"
    if conmgr_value is not None:
        return conmgr_value, "conmgr"
    return None, None


def build_project_context(
    dtproj_path: str,
    params_path: str,
    conmgr_dir: str,
) -> Dict[str, Any]:
    """
    conmgr_dir: carpeta donde buscar los archivos *.conmgr que el .dtproj
    declara en connection_manager_files. Si alguno no existe en disco, se
    OMITE (no se inventa su contenido) y se registra en
    'missing_conmgr_files' -- ver validator.py para el warning
    correspondiente. Esto es un caso real, no hipotetico: en este mismo
    repositorio, BipSuc.dtproj declara cnxSrvTurnosDb.conmgr pero ese
    archivo no esta entre los fixtures disponibles.
    """
    dtproj = parse_dtproj_file(dtproj_path)
    parameters = parse_params_file(params_path)

    connections: Dict[str, Dict[str, Any]] = {}
    missing_conmgr_files: List[str] = []

    for file_name in dtproj["connection_manager_files"]:
        full_path = os.path.join(conmgr_dir, file_name)
        if not os.path.isfile(full_path):
            missing_conmgr_files.append(file_name)
            continue

        conmgr = parse_conmgr_file(full_path)
        conn_name = conmgr["name"]

        dtproj_params = dtproj["connection_parameters"].get(conn_name, {})
        retain_dtproj_raw = dtproj_params.get("RetainSameConnection", {}).get("value")
        retain_conmgr = conmgr["provider_data"].get("retain_same_connection")
        retain_same_connection, retain_source = _resolve_retain_same_connection(
            retain_dtproj_raw, retain_conmgr
        )

        connections[conn_name] = {
            "provider": conmgr["provider"],
            "dtsid": conmgr["dtsid"],
            "conmgr_file": file_name,
            "retain_same_connection": retain_same_connection,
            "retain_same_connection_source": retain_source,
            "property_expressions": conmgr["property_expressions"],
            "project_connection_parameters": dtproj_params,
            "provider_data": conmgr["provider_data"],
        }

    return {
        "project": {
            "name": dtproj["name"],
            "target_server_version": dtproj["target_server_version"],
            "protection_level": dtproj["protection_level"],
        },
        "parameters": parameters,
        "connections": connections,
        "packages": dtproj["package_metadata"],
        "packages_declared": dtproj["packages_declared"],
        "missing_conmgr_files": missing_conmgr_files,
    }
