"""
teradata_to_sql_profile.py

Profile Resolver para la familia TERADATA_TO_SQL:

    MinimalSpec
        |
    resolve_teradata_to_sql_profile()
        |
    ProjectSpec (para project_generator.project_generator.generate_project_resources)
    +
    ProcessSpec (para generator.campanias_generator.generate)

No genera XML: solo produce los dos dicts que YA consumen, sin cambios, los
generadores existentes. Los defaults corporativos viven UNICAMENTE aca
(centralizados) -- ningun otro modulo (project_generator, campanias_generator)
conoce estos valores.

Evidencia detras de cada default (ver docs/teradata_to_sql_profile_v1.md
para el detalle completo de la auditoria):

  - CORPORATE_TERADATA_SERVER/DATABASE/USER: identicos en los 2 proyectos
    TERADATA_TO_SQL productivos reales disponibles (BipSuc_CampaniasVigentes,
    PagosYRecaudaciones_FacturacionComi) -- mismo servidor Teradata
    corporativo compartido.
  - CORPORATE_SQL_USER: 'usSxSSIS', hardcodeado (nunca parametrizado) en
    las 4 conexiones SQL independientes observadas, en 3 proyectos, sobre 3
    servidores SQL distintos -- cero excepciones.

Convencion de nombres de parametro SQL (declarada explicitamente por el
usuario, NO inferida del corpus historico -- ningun proyecto real disponible
la sigue tal cual: BipSuc usa 'srvBsLogSBD01'/'pwSrvBsLogSBD01' -- nombrado
por un alias corto, no por 'SRVBSQADB', su servidor real -- y
PagosYRecaudaciones usa 'srvDbSqlCons11'/'pwUsSxSSIS' -- nombrado por el
usuario fijo, no por su servidor real 'SRVBSDESADB.child01.root.test'.
Misma naturaleza que la correccion de 'ambiente': una convencion corporativa
ACTUAL para proyectos nuevos, no un patron a inferir del pasado):

    ServerName parameter = 'srv' + <NombreServidor>
    Password parameter   = 'pw'  + <NombreServidor>

Ejemplo: servidor 'SRVBSQADB' -> 'srvSRVBSQADB' / 'pwSRVBSQADB'. El fragmento
de servidor se sanitiza (ver minimal_spec_schema.sanitize_server_name_fragment)
porque un nombre de servidor real puede traer '.'  (evidenciado:
'SRVBSDESADB.child01.root.test') que no es seguro dentro de una referencia
de expresion SSIS ('@[$Project::<nombre>]').
  - CORPORATE_PROTECTION_LEVEL: 'EncryptSensitiveWithPassword' en los 2
    proyectos productivos reales (a nivel .dtproj Y a nivel package,
    DTS:ProtectionLevel="2"); el default de SSDT sin customizar es distinto
    ('EncryptSensitiveWithUserKey', SSDT_Golden) -- confirma que es una
    decision corporativa deliberada, no un default de la herramienta.
  - CORPORATE_MIN_SESSIONS/MAX_SESSIONS: 4/8 en el 100% de los Teradata
    Source ACTIVOS del corpus (los unicos contraejemplos pertenecen a Data
    Flows deshabilitados/historicos en BipSuc_Turnero); el default de un
    Teradata Source de SSDT sin customizar es 1/1 (SSDT_Golden).

'ambiente': a diferencia de los defaults de arriba, esto NO es una
inferencia del corpus historico (donde aparece en 1 de 2 proyectos) sino una
convencion corporativa ACTUAL declarada explicitamente por el usuario para
proyectos nuevos -- por eso 'environment' es siempre OBLIGATORIO en el
MinimalSpec (nunca tiene un valor default silencioso), pero el Project
Parameter 'ambiente' SI se genera siempre.

Deliberadamente NO incluido (fuera de scope de v1, ver docs): la expresion
ternaria de InitialCatalog por ambiente (@[$Project::ambiente] == "QA" ? ...),
y PackagePassword (requiere secreto en runtime, Factory nunca lo genera ni
lo persiste).
"""

from __future__ import annotations

from typing import Any, Dict

from .minimal_spec_schema import assert_valid_minimal_spec, sanitize_server_name_fragment

# ---------------------------------------------------------------------------
# Defaults corporativos -- UNICA fuente de verdad, no duplicar en otro modulo.
# ---------------------------------------------------------------------------
CORPORATE_TERADATA_SERVER = "tdgnn.ccba.usr.bpba"
CORPORATE_TERADATA_DATABASE = "D_DW_APPLICATIONS"
CORPORATE_TERADATA_USER = "D_DW_APPLICATIONS_USR"
CORPORATE_SQL_USER = "usSxSSIS"
CORPORATE_PROTECTION_LEVEL = "EncryptSensitiveWithPassword"
CORPORATE_MIN_SESSIONS = 4
CORPORATE_MAX_SESSIONS = 8

# Nombres de Project Parameter / Connection Manager -- fijos, congelados en
# v1 (el profile decide estos nombres; el usuario del MinimalSpec no los
# elige, salvo 'sql.server_parameter_name', que no tiene convencion unica
# observada -- ver docs).
TERADATA_PARAM_SERVER = "srvTeradata"
TERADATA_PARAM_DATABASE = "dbTeradata"
TERADATA_PARAM_USER = "usrTeradata"
TERADATA_PARAM_PASSWORD = "pwTeradata"
AMBIENTE_PARAM_NAME = "ambiente"

SQL_SERVER_PARAM_PREFIX = "srv"
SQL_PASSWORD_PARAM_PREFIX = "pw"

CNX_TERADATA_NAME = "cnxTeradata"
CNX_SQL_NAME = "cnxSql"


def _build_project_spec(minimal_spec: Dict[str, Any]) -> Dict[str, Any]:
    teradata = minimal_spec.get("teradata", {})
    sql = minimal_spec["sql"]
    environment = minimal_spec["environment"]

    teradata_server = teradata.get("server", CORPORATE_TERADATA_SERVER)
    teradata_database = teradata.get("database", CORPORATE_TERADATA_DATABASE)
    teradata_user = teradata.get("user", CORPORATE_TERADATA_USER)
    charset = teradata["charset"]

    sql_server = sql["server"]
    sql_catalog = sql["catalog"]
    sql_user = sql.get("user", CORPORATE_SQL_USER)

    server_fragment = sanitize_server_name_fragment(sql_server)
    sql_server_parameter_name = sql.get("server_parameter_name") or f"{SQL_SERVER_PARAM_PREFIX}{server_fragment}"
    sql_password_parameter_name = sql.get("password_parameter_name") or f"{SQL_PASSWORD_PARAM_PREFIX}{server_fragment}"

    return {
        "project": {
            "parameters": [
                {"name": TERADATA_PARAM_SERVER, "sensitive": False, "value": teradata_server},
                {"name": TERADATA_PARAM_DATABASE, "sensitive": False, "value": teradata_database},
                {"name": TERADATA_PARAM_USER, "sensitive": False, "value": teradata_user},
                {"name": TERADATA_PARAM_PASSWORD, "sensitive": True},
                {"name": sql_server_parameter_name, "sensitive": False, "value": sql_server},
                {"name": sql_password_parameter_name, "sensitive": True},
                {"name": AMBIENTE_PARAM_NAME, "sensitive": False, "value": environment},
            ],
            "connections": [
                {
                    "name": CNX_TERADATA_NAME,
                    "provider": "teradata",
                    "server": teradata_server,
                    "database": teradata_database,
                    "user": teradata_user,
                    "charset": charset,
                    "property_expressions": [
                        {"property": "ServerName", "parameter": TERADATA_PARAM_SERVER},
                        {"property": "Database", "parameter": TERADATA_PARAM_DATABASE},
                        {"property": "UserName", "parameter": TERADATA_PARAM_USER},
                        {"property": "Password", "parameter": TERADATA_PARAM_PASSWORD},
                    ],
                },
                {
                    "name": CNX_SQL_NAME,
                    "provider": "oledb",
                    "server": sql_server,
                    "catalog": sql_catalog,
                    "user": sql_user,
                    "property_expressions": [
                        {"property": "ServerName", "parameter": sql_server_parameter_name},
                        {"property": "Password", "parameter": sql_password_parameter_name},
                    ],
                },
            ],
        }
    }


def _build_process_spec(minimal_spec: Dict[str, Any]) -> Dict[str, Any]:
    process = minimal_spec["process"]
    teradata_source = minimal_spec.get("teradata_source", {})

    min_sessions = teradata_source.get("min_sessions", CORPORATE_MIN_SESSIONS)
    max_sessions = teradata_source.get("max_sessions", CORPORATE_MAX_SESSIONS)
    protection_level = minimal_spec.get("protection_level", CORPORATE_PROTECTION_LEVEL)

    return {
        "package": {
            "name": process["package_name"],
            "protection_level": protection_level,
        },
        "data_flow": {
            "name": process["data_flow_name"],
            "source": {
                "type": "teradata",
                "name": process["source_name"],
                "connection": CNX_TERADATA_NAME,
                "sql": process["sql"],
                "columns": process["columns"],
                "min_sessions": min_sessions,
                "max_sessions": max_sessions,
            },
            "transformations": process.get("transformations", []),
            "destination": {
                "type": "ole_db",
                "name": process["destination_name"],
                "connection": CNX_SQL_NAME,
                "table": process["table"],
                "mappings": process["mappings"],
            },
        },
    }


def resolve_teradata_to_sql_profile(minimal_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Valida el MinimalSpec (fail-fast) y lo resuelve a
    {"project_spec": ..., "process_spec": ...} -- exactamente las formas que
    hoy aceptan project_generator.project_generator.generate_project_resources()
    y generator.campanias_generator.generate() (via
    generator.spec_validator.assert_valid_spec()), sin ningun cambio en esos
    modulos.

    No escribe ningun archivo ni construye ningun ProjectContext -- eso sigue
    siendo responsabilidad exclusiva de los generadores existentes, llamados
    por el caller con el resultado de esta funcion.
    """
    assert_valid_minimal_spec(minimal_spec)

    return {
        "project_spec": _build_project_spec(minimal_spec),
        "process_spec": _build_process_spec(minimal_spec),
    }
