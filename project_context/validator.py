"""
validator.py

Validaciones sobre un ProjectContext ya construido (ver context_builder.py).
Mismo criterio de separacion que ssis_validator.py: no parsea ni consolida,
solo revisa lo que ya se extrajo. Devuelve {"valid": bool, "errors": [...],
"warnings": [...]}.

Tambien expone helpers de consulta puntual (provider/DTSID por nombre de
conexion) que el futuro generador podria usar en lugar de un mapa
hardcodeado -- ver docs/project_context.md, seccion de integracion.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _issue(code: str, message: str, **context: Any) -> Dict[str, Any]:
    issue: Dict[str, Any] = {"code": code, "message": message}
    if context:
        issue["context"] = {k: v for k, v in context.items() if v is not None}
    return issue


def validate_project_context(context: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    connections = context.get("connections", {})

    # Registro de conexiones: un .conmgr declarado en el .dtproj pero
    # ausente en disco es un WARNING, no un error -- el resto del contexto
    # sigue siendo utilizable (caso real: cnxSrvTurnosDb.conmgr no esta
    # entre los fixtures de este repositorio).
    for file_name in context.get("missing_conmgr_files", []):
        warnings.append(
            _issue(
                "missing_conmgr_file",
                f"El proyecto declara el Connection Manager '{file_name}' pero "
                "el archivo .conmgr no se encontro en disco -- no se pudo "
                "analizar su contenido.",
                conmgr_file=file_name,
            )
        )

    # Referencias '$Project::parametro' en Property Expressions deben
    # existir en Project.params -- si no, es un error real (el paquete
    # fallaria al ejecutarse en SSIS).
    known_parameter_names = {p["name"] for p in context.get("parameters", [])}
    for conn_name, conn in connections.items():
        for expr in conn.get("property_expressions", []):
            for ref in expr.get("referenced_parameters", []):
                if ref not in known_parameter_names:
                    errors.append(
                        _issue(
                            "unresolved_project_parameter_reference",
                            f"La Property Expression '{expr.get('property')}' de "
                            f"'{conn_name}' referencia el parametro de proyecto "
                            f"'{ref}', que no existe en Project.params.",
                            connection=conn_name,
                            property=expr.get("property"),
                            parameter=ref,
                        )
                    )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def get_connection_provider(context: Dict[str, Any], name: str) -> str:
    """Consulta puntual: 'cnxTeradata' -> 'TERADATA'. KeyError si el nombre
    no esta registrado en este ProjectContext (nunca devuelve un default)."""
    conn = connections_or_raise(context, name)
    return conn["provider"]


def get_connection_dtsid(context: Dict[str, Any], name: str) -> str:
    """Consulta puntual: 'cnxTeradata' -> '{29B4FDD4-...}'. KeyError si el
    nombre no esta registrado en este ProjectContext."""
    conn = connections_or_raise(context, name)
    return conn["dtsid"]


def get_retain_same_connection(context: Dict[str, Any], name: str) -> Optional[bool]:
    """Consulta puntual del valor ya resuelto (.dtproj preferido, .conmgr de
    fallback -- ver context_builder._resolve_retain_same_connection). None
    si ninguna fuente lo tenia."""
    conn = connections_or_raise(context, name)
    return conn["retain_same_connection"]


def connections_or_raise(context: Dict[str, Any], name: str) -> Dict[str, Any]:
    conn = context.get("connections", {}).get(name)
    if conn is None:
        raise KeyError(f"Connection Manager '{name}' no esta en este ProjectContext.")
    return conn
