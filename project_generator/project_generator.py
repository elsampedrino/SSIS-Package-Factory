"""
project_generator.py

Orquestador de MODE B (project-generation-v1):

    ProjectSpec -> spec_schema (fail-fast)
                -> params_writer.write_params_file()
                -> conmgr_writer.write_conmgr_file() (una vez por conexion)

El resultado son archivos sueltos (Project.params + N x *.conmgr) pensados
para incorporarse a un proyecto SSIS EXISTENTE via SSDT ("Add Existing
Item") -- ver docs/project_generation_v1.md, seccion "Relacion con .dtproj".
Este modulo NUNCA lee, genera ni modifica ningun .dtproj: el flujo de
consumo posterior (build_project_context/validate_project_context/Package
Generator) sigue viviendo, sin cambios, en project_context/ y generator/.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from .conmgr_writer import write_conmgr_file
from .params_writer import write_params_file
from .spec_schema import assert_valid_project_spec


def generate_project_resources(spec: Dict[str, Any], output_dir: str) -> Dict[str, Any]:
    """
    Valida el ProjectSpec (fail-fast, antes de escribir nada) y genera:
      - <output_dir>/Project.params
      - <output_dir>/<connection.name>.conmgr, uno por conexion declarada

    Devuelve {"params_path": ..., "conmgr_paths": {nombre_conexion: ruta}}.
    """
    assert_valid_project_spec(spec)
    os.makedirs(output_dir, exist_ok=True)

    project = spec["project"]
    parameters = project.get("parameters", [])
    connections = project.get("connections", [])

    params_path = os.path.join(output_dir, "Project.params")
    write_params_file(parameters, params_path)

    conmgr_paths: Dict[str, str] = {}
    for conn in connections:
        conn_path = os.path.join(output_dir, f"{conn['name']}.conmgr")
        write_conmgr_file(conn, conn_path)
        conmgr_paths[conn["name"]] = conn_path

    return {"params_path": params_path, "conmgr_paths": conmgr_paths}
