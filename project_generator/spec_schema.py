"""
spec_schema.py

Validacion fail-fast de un ProjectSpec v1, ANTES de escribir cualquier
archivo. Mismo criterio que generator/spec_validator.py: junta TODOS los
errores encontrados en una sola pasada (no aborta en el primero) para que
quien escribe el spec vea de una vez la lista completa de correcciones.

ProjectSpec v1 -- scope congelado (ver docs/project_generation_v1.md):

    project:
      parameters:
        - name: dbTeradata
          sensitive: false
          value: "D_DW_APPLICATIONS"
        - name: pwTeradata
          sensitive: true          # sin 'value' -- shell sin valor
      connections:
        - name: cnxTeradata
          provider: teradata
          server: ...
          database: ...
          user: ...
          authentication: TD2      # opcional, default TD2
          charset: ASCII           # obligatorio (ASCII|UTF8), sin default justificado
          retain_same_connection: false   # opcional -- PARTIAL, ver docs
          property_expressions:
            - {property: ServerName, parameter: srvTeradata}
        - name: cnxSql
          provider: oledb
          server: ...
          catalog: ...
          user: ...
          property_expressions:
            - {property: Password, parameter: pwSql}
          # retain_same_connection: NO SOPORTADO para oledb (DEFERRED)

Derivados (NO deben aparecer en el spec): Parameter ID, Connection DTSID,
TeraConnectionString, DTS:ConnectionString, XML namespaces, Application Name.

Fuera de scope explicito: .dtproj, secrets/passwords, blobs cifrados,
providers distintos de teradata/oledb, expresiones SSIS arbitrarias
('expression' cruda, ternarios), RetainSameConnection para OLEDB.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

SUPPORTED_PROVIDERS = {"teradata", "oledb"}

# Unicos valores de CHARSET con evidencia real (BipSuc/PagosYRecaudaciones =
# ASCII, SSDT_Golden = UTF8) -- inconsistentes entre proyectos, por lo que NO
# se justifica un default: el spec debe declararlo siempre explicitamente.
SUPPORTED_TERADATA_CHARSETS = {"ASCII", "UTF8"}


class ProjectSpecValidationError(Exception):
    """Se lanza cuando validate_project_spec() encuentra uno o mas problemas.
    El mensaje incluye la lista completa, numerada, de errores."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        message = "ProjectSpec invalido:\n" + "\n".join(
            f"  {i}. {e}" for i, e in enumerate(errors, start=1)
        )
        super().__init__(message)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_parameters(parameters: Any) -> Tuple[List[str], Set[str]]:
    errors: List[str] = []
    names: Set[str] = set()

    if not isinstance(parameters, list):
        return ["'project.parameters' debe ser una lista."], names

    for idx, param in enumerate(parameters):
        label = f"project.parameters[{idx}]"
        if not isinstance(param, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        name = param.get("name")
        if not _non_empty_str(name):
            errors.append(f"{label}.name es obligatorio y no puede estar vacio.")
        else:
            if name in names:
                errors.append(
                    f"Parametro duplicado: '{name}' aparece mas de una vez en "
                    "project.parameters."
                )
            names.add(name)

        sensitive = param.get("sensitive")
        if not isinstance(sensitive, bool):
            errors.append(f"{label}.sensitive es obligatorio y debe ser boolean.")
            continue

        if sensitive:
            if "value" in param:
                errors.append(
                    f"{label}.value esta PROHIBIDO cuando sensitive=true -- un "
                    "parametro sensible se genera siempre como shell SIN valor "
                    "(Factory nunca genera ni copia contenido cifrado, ver "
                    "docs/project_generation_v1.md)."
                )
        else:
            if not _non_empty_str(param.get("value")):
                errors.append(
                    f"{label}.value es obligatorio y no puede estar vacio cuando "
                    "sensitive=false."
                )

        required = param.get("required", True)
        if not isinstance(required, bool):
            errors.append(f"{label}.required, si esta presente, debe ser boolean.")

        description = param.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{label}.description, si esta presente, debe ser un string.")

    return errors, names


def _validate_property_expressions(
    expressions: Any, parameter_names: Set[str], label: str
) -> List[str]:
    """
    v1 solo soporta la forma estructurada 'property' + 'parameter' (ver
    docstring del modulo) -- nunca 'expression' cruda, texto libre, ni
    ternarios, aunque haya evidencia real de que SSIS los soporta (queda
    fuera de scope, igual que Script Task en Control Flow).
    """
    errors: List[str] = []
    if expressions is None:
        return errors
    if not isinstance(expressions, list):
        return [f"'{label}' debe ser una lista."]

    for idx, expr in enumerate(expressions):
        e_label = f"{label}[{idx}]"
        if not isinstance(expr, dict):
            errors.append(f"{e_label} debe ser un objeto.")
            continue

        if "expression" in expr:
            errors.append(
                f"{e_label}: no se acepta 'expression' cruda -- v1 solo soporta "
                "'property' + 'parameter' (referencia simple a un parametro de "
                "proyecto, ver docs/project_generation_v1.md)."
            )

        prop = expr.get("property")
        if not _non_empty_str(prop):
            errors.append(f"{e_label}.property es obligatorio y no puede estar vacio.")

        parameter = expr.get("parameter")
        if not _non_empty_str(parameter):
            errors.append(f"{e_label}.parameter es obligatorio y no puede estar vacio.")
        elif parameter not in parameter_names:
            errors.append(
                f"{e_label}.parameter = '{parameter}' no existe en project.parameters "
                "del mismo ProjectSpec."
            )

    return errors


def _validate_teradata_connection(conn: Dict[str, Any], label: str) -> List[str]:
    errors: List[str] = []

    if not _non_empty_str(conn.get("database")):
        errors.append(
            f"{label}.database es obligatorio y no puede estar vacio (provider teradata)."
        )
    if not _non_empty_str(conn.get("user")):
        errors.append(f"{label}.user es obligatorio y no puede estar vacio (provider teradata).")

    authentication = conn.get("authentication")
    if authentication is not None and not _non_empty_str(authentication):
        errors.append(f"{label}.authentication, si esta presente, no puede estar vacio.")

    charset = conn.get("charset")
    if charset not in SUPPORTED_TERADATA_CHARSETS:
        errors.append(
            f"{label}.charset es obligatorio para provider teradata y debe ser uno "
            f"de {sorted(SUPPORTED_TERADATA_CHARSETS)} (unicos valores con evidencia "
            f"real confirmada -- no hay un default justificado entre proyectos); "
            f"vino: {charset!r}."
        )

    retain = conn.get("retain_same_connection")
    if retain is not None and not isinstance(retain, bool):
        errors.append(f"{label}.retain_same_connection, si esta presente, debe ser boolean.")

    return errors


def _validate_oledb_connection(conn: Dict[str, Any], label: str) -> List[str]:
    errors: List[str] = []

    if not _non_empty_str(conn.get("catalog")):
        errors.append(f"{label}.catalog es obligatorio y no puede estar vacio (provider oledb).")
    if not _non_empty_str(conn.get("user")):
        errors.append(f"{label}.user es obligatorio y no puede estar vacio (provider oledb).")

    if "retain_same_connection" in conn:
        errors.append(
            f"{label}.retain_same_connection no esta soportado para provider oledb "
            "(DEFERRED -- OLE DB nunca serializa esto en su .conmgr en ningun proyecto "
            "real auditado; depende 100% de ProjectConnectionParameters en .dtproj, "
            "fuera de scope de project-generation-v1, ver docs/project_generation_v1.md)."
        )

    return errors


_PROVIDER_VALIDATORS = {
    "teradata": _validate_teradata_connection,
    "oledb": _validate_oledb_connection,
}


def _validate_connections(connections: Any, parameter_names: Set[str]) -> List[str]:
    errors: List[str] = []

    if not isinstance(connections, list):
        return ["'project.connections' debe ser una lista."]

    names: Set[str] = set()
    for idx, conn in enumerate(connections):
        label = f"project.connections[{idx}]"
        if not isinstance(conn, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        name = conn.get("name")
        if not _non_empty_str(name):
            errors.append(f"{label}.name es obligatorio y no puede estar vacio.")
        else:
            if name in names:
                errors.append(
                    f"Conexion duplicada: '{name}' aparece mas de una vez en "
                    "project.connections."
                )
            names.add(name)

        provider = conn.get("provider")
        if provider not in SUPPORTED_PROVIDERS:
            errors.append(
                f"{label}.provider debe ser uno de {sorted(SUPPORTED_PROVIDERS)} "
                f"(unicos providers soportados en project-generation-v1); "
                f"vino: {provider!r}."
            )
            provider = None

        if not _non_empty_str(conn.get("server")):
            errors.append(f"{label}.server es obligatorio y no puede estar vacio.")

        if provider is not None:
            errors.extend(_PROVIDER_VALIDATORS[provider](conn, label))

        errors.extend(
            _validate_property_expressions(
                conn.get("property_expressions"),
                parameter_names,
                f"{label}.property_expressions",
            )
        )

    return errors


def validate_project_spec(spec: Dict[str, Any]) -> List[str]:
    """Devuelve la lista de errores encontrados (vacia si el spec es valido).
    No lanza excepcion -- ver assert_valid_project_spec() para la version
    que si."""
    if not isinstance(spec, dict):
        return ["El ProjectSpec debe ser un objeto (dict)."]

    project = spec.get("project")
    if not isinstance(project, dict):
        return ["Falta 'project' (objeto) en el ProjectSpec."]

    param_errors, parameter_names = _validate_parameters(project.get("parameters", []))
    connection_errors = _validate_connections(project.get("connections", []), parameter_names)

    return param_errors + connection_errors


def assert_valid_project_spec(spec: Dict[str, Any]) -> None:
    """Como validate_project_spec(), pero lanza ProjectSpecValidationError si
    hay errores. Debe llamarse ANTES de escribir cualquier archivo."""
    errors = validate_project_spec(spec)
    if errors:
        raise ProjectSpecValidationError(errors)
