"""
minimal_spec_schema.py

Validacion fail-fast de un MinimalSpec para el profile 'teradata_to_sql'
(ver profiles/teradata_to_sql_profile.py). Junta TODOS los errores en una
sola pasada -- mismo criterio que generator/spec_validator.py y
project_generator/spec_schema.py.

MinimalSpec v1 -- forma congelada (ver docs/teradata_to_sql_profile_v1.md):

    {
      "profile": "teradata_to_sql",
      "environment": "QA",                 # REQUIRED: "QA" | "Produ"
      "teradata": {
        "charset": "UTF8",                 # REQUIRED: "ASCII" | "UTF8", sin default
        "server": "...",                   # OPTIONAL override del default corporativo
        "database": "...",                 # OPTIONAL override
        "user": "..."                      # OPTIONAL override
      },
      "teradata_source": {
        "min_sessions": 4,                 # OPTIONAL override del default corporativo (4)
        "max_sessions": 8                  # OPTIONAL override del default corporativo (8)
      },
      "sql": {
        "server_parameter_name": "srvX",   # REQUIRED (no hay nombre corporativo unico)
        "server": "SRVXXXX",               # REQUIRED (el servidor SQL siempre es explicito)
        "catalog": "MiCatalogo",           # REQUIRED (el catalogo SQL siempre es explicito)
        "user": "..."                      # OPTIONAL override del default corporativo (usSxSSIS)
      },
      "protection_level": "EncryptSensitiveWithPassword",  # OPTIONAL override
      "process": {
        "package_name": "...", "data_flow_name": "...", "source_name": "...",
        "sql": "...", "columns": [...], "transformations": [...],
        "destination_name": "...", "table": "...", "mappings": [...]
      }
    }

'process' se revalida en profundidad recien cuando el ProcessSpec resuelto
pasa por generator.spec_validator.assert_valid_spec() (ver
profiles/teradata_to_sql_profile.py) -- este modulo solo verifica presencia
y tipos basicos, para no duplicar esa logica ya existente.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from generator.spec_validator import SUPPORTED_PACKAGE_PROTECTION_LEVELS
from project_generator.spec_schema import SUPPORTED_TERADATA_CHARSETS

PROFILE_NAME = "teradata_to_sql"

# Fragmento de nombre de Project Parameter derivado del nombre de servidor
# SQL ('srv<fragmento>'/'pw<fragmento>', ver profiles/teradata_to_sql_profile.py).
# Se eliminan caracteres que no son letras/digitos -- un Project Parameter se
# referencia dentro de una expresion SSIS ('@[$Project::<nombre>]'), y '.'/
# '\\'/'-' (evidenciados o plausibles en un nombre de servidor real, ej.
# 'SRVBSDESADB.child01.root.test' en PagosYRecaudaciones) podrian
# interpretarse de forma ambigua ahi. No hay evidencia real de que el equipo
# abrevie un FQDN de otra forma al nombrar un parametro -- se aplica la regla
# mas simple y predecible posible en vez de inventar un esquema de
# abreviacion. Ver docs/teradata_to_sql_profile_v1.md.
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")


def sanitize_server_name_fragment(server: str) -> str:
    return _NON_ALNUM_RE.sub("", server)

# Convencion corporativa ACTUAL (no inferida del corpus historico -- ver
# docs/teradata_to_sql_profile_v1.md, seccion "ambiente"). Unicos dos valores
# permitidos; sin default, 'environment' es siempre obligatorio en el
# MinimalSpec para no asumir QA/Produ por el usuario.
SUPPORTED_ENVIRONMENTS = {"QA", "Produ"}


class MinimalSpecValidationError(Exception):
    """Se lanza cuando validate_minimal_spec() encuentra uno o mas problemas.
    El mensaje incluye la lista completa, numerada, de errores."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        message = "MinimalSpec invalido:\n" + "\n".join(
            f"  {i}. {e}" for i, e in enumerate(errors, start=1)
        )
        super().__init__(message)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_teradata_block(teradata: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(teradata, dict):
        return ["Falta 'teradata' (objeto)."]

    charset = teradata.get("charset")
    if charset not in SUPPORTED_TERADATA_CHARSETS:
        errors.append(
            f"'teradata.charset' es obligatorio y debe ser uno de "
            f"{sorted(SUPPORTED_TERADATA_CHARSETS)} (sin default corporativo -- "
            f"ver docs/teradata_to_sql_profile_v1.md); vino: {charset!r}."
        )

    for field in ("server", "database", "user"):
        if field in teradata and not _non_empty_str(teradata[field]):
            errors.append(f"'teradata.{field}', si esta presente (override), no puede estar vacio.")

    return errors


def _validate_sql_block(sql: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(sql, dict):
        return ["Falta 'sql' (objeto)."]

    server = sql.get("server")
    if not _non_empty_str(server):
        errors.append(
            "'sql.server' es obligatorio y no puede estar vacio "
            "(el servidor SQL siempre es explicito, sin default corporativo)."
        )
    elif not sanitize_server_name_fragment(server):
        errors.append(
            f"'sql.server' = {server!r} no contiene ningun caracter alfanumerico -- "
            "no se puede derivar un nombre de Project Parameter valido "
            "('srv<servidor>'/'pw<servidor>'). Usar 'sql.server_parameter_name'/"
            "'sql.password_parameter_name' para fijar los nombres explicitamente."
        )

    # server_parameter_name/password_parameter_name: OPCIONALES -- por
    # convencion se derivan siempre de 'sql.server' ('srv<servidor>'/
    # 'pw<servidor>'), ver profiles/teradata_to_sql_profile.py. Solo se
    # valida el tipo si vienen como override explicito.
    if "server_parameter_name" in sql and not _non_empty_str(sql["server_parameter_name"]):
        errors.append("'sql.server_parameter_name', si esta presente (override), no puede estar vacio.")
    if "password_parameter_name" in sql and not _non_empty_str(sql["password_parameter_name"]):
        errors.append("'sql.password_parameter_name', si esta presente (override), no puede estar vacio.")

    if not _non_empty_str(sql.get("catalog")):
        errors.append(
            "'sql.catalog' es obligatorio y no puede estar vacio "
            "(el catalogo SQL siempre es explicito, sin default corporativo)."
        )
    if "user" in sql and not _non_empty_str(sql["user"]):
        errors.append("'sql.user', si esta presente (override), no puede estar vacio.")

    return errors


def _validate_teradata_source_block(teradata_source: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(teradata_source, dict):
        return ["'teradata_source', si esta presente, debe ser un objeto."]

    if "min_sessions" in teradata_source and not _positive_int(teradata_source["min_sessions"]):
        errors.append("'teradata_source.min_sessions', si esta presente (override), debe ser entero positivo.")
    if "max_sessions" in teradata_source and not _positive_int(teradata_source["max_sessions"]):
        errors.append("'teradata_source.max_sessions', si esta presente (override), debe ser entero positivo.")

    return errors


def _validate_process_block(process: Any) -> List[str]:
    """
    Solo presencia/tipos basicos -- la validacion estructural profunda
    (columnas, mappings, tipos de dato) ya la hace
    generator.spec_validator.assert_valid_spec() sobre el ProcessSpec
    resuelto; duplicarla aca violaria "no crear una segunda implementacion".
    """
    errors: List[str] = []
    if not isinstance(process, dict):
        return ["Falta 'process' (objeto) -- el Data Flow especifico nunca se infiere."]

    for field in ("package_name", "data_flow_name", "source_name", "sql", "destination_name", "table"):
        if not _non_empty_str(process.get(field)):
            errors.append(f"'process.{field}' es obligatorio y no puede estar vacio.")

    columns = process.get("columns")
    if not isinstance(columns, list) or len(columns) == 0:
        errors.append("'process.columns' debe ser una lista no vacia.")

    mappings = process.get("mappings")
    if not isinstance(mappings, list) or len(mappings) == 0:
        errors.append("'process.mappings' debe ser una lista no vacia.")

    transformations = process.get("transformations", [])
    if not isinstance(transformations, list):
        errors.append("'process.transformations', si esta presente, debe ser una lista.")

    return errors


def validate_minimal_spec(spec: Dict[str, Any]) -> List[str]:
    """Devuelve la lista de errores encontrados (vacia si el spec es valido).
    No lanza excepcion -- ver assert_valid_minimal_spec() para la version
    que si."""
    if not isinstance(spec, dict):
        return ["El MinimalSpec debe ser un objeto (dict)."]

    errors: List[str] = []

    if spec.get("profile") != PROFILE_NAME:
        errors.append(
            f"'profile' debe ser exactamente {PROFILE_NAME!r}; vino: {spec.get('profile')!r}."
        )

    environment = spec.get("environment")
    if environment not in SUPPORTED_ENVIRONMENTS:
        errors.append(
            f"'environment' es obligatorio y debe ser uno de {sorted(SUPPORTED_ENVIRONMENTS)} "
            "(convencion corporativa vigente para 'ambiente' -- nunca se asume "
            f"un default); vino: {environment!r}."
        )

    errors.extend(_validate_teradata_block(spec.get("teradata")))
    errors.extend(_validate_sql_block(spec.get("sql")))
    errors.extend(_validate_teradata_source_block(spec.get("teradata_source", {})))

    if "protection_level" in spec and spec["protection_level"] not in SUPPORTED_PACKAGE_PROTECTION_LEVELS:
        errors.append(
            "'protection_level', si esta presente (override), debe ser uno de "
            f"{sorted(SUPPORTED_PACKAGE_PROTECTION_LEVELS)}; vino: {spec['protection_level']!r}."
        )

    errors.extend(_validate_process_block(spec.get("process")))

    return errors


def assert_valid_minimal_spec(spec: Dict[str, Any]) -> None:
    """Como validate_minimal_spec(), pero lanza MinimalSpecValidationError si
    hay errores. Debe llamarse ANTES de resolver el MinimalSpec a
    ProjectSpec/ProcessSpec."""
    errors = validate_minimal_spec(spec)
    if errors:
        raise MinimalSpecValidationError(errors)
