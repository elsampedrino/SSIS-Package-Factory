"""
spec_validator.py

Validacion fail-fast de un process_spec, ANTES de tocar el template XML.
Separado del generador propiamente dicho (mismo criterio de separacion de
responsabilidades que ssis_parser.py / ssis_validator.py).

Desde project-context-v1, la validacion de "conexiones soportadas" ya NO
depende de un diccionario hardcodeado: consulta un ProjectContext real (ver
project_context/context_builder.py), que refleja el proyecto SSIS al que el
paquete generado se va a agregar. Esto separa dos preocupaciones distintas:

    - Project Context validation (project_context.validator.validate_project_context):
      salud GENERAL del proyecto (ej. un .conmgr referenciado que falta en
      disco pero que ningun paquete usa todavia).
    - Spec / Generator validation (este modulo): si LOS RECURSOS QUE ESTE
      SPEC PARTICULAR necesita (una conexion por nombre, con un provider
      esperado) existen y son utilizables. Un .conmgr faltante que Campanias
      no usa (ej. cnxSrvTurnosDb.conmgr) no debe bloquear la generacion de
      Campanias -- solo bloquea si el spec pide justamente esa conexion.

validate_spec() intenta juntar TODOS los problemas en una sola pasada (en vez
de abortar en el primer error) para que quien escribe el spec vea de una vez
toda la lista de correcciones necesarias — sigue siendo "fail-fast" en el
sentido de que el generador nunca empieza a modificar el template si esta
lista no esta vacia.
"""

from __future__ import annotations

from typing import Any, Dict, List

SUPPORTED_SOURCE_TYPE = "teradata"
SUPPORTED_DESTINATION_TYPE = "ole_db"
SUPPORTED_TRANSFORMATION_TYPE = "data_conversion"
STRING_DATA_TYPES = {"str", "wstr"}
NON_UNICODE_STRING_TYPE = "str"
NUMERIC_DATA_TYPE = "numeric"

# template-teradata-to-flat-file-v1: segundo tipo de destino soportado,
# ademas de 'ole_db' -- ver docs/template_teradata_to_flat_file_v1.md.
# Decision de equipo "SQL-first" (evidencia real: 4 packages del proyecto
# MediosDePago): esta familia NUNCA usa 'transformations[]' en su happy
# path (Data Conversion/Derived Column se resuelven en el SQL del origen,
# no en el pipeline) -- por eso 'destination.type=flat_file' no interactua
# con SUPPORTED_TRANSFORMATION_TYPES/derived_column, son ortogonales.
FLAT_FILE_DESTINATION_TYPE = "flat_file"
SUPPORTED_DESTINATION_TYPES = {SUPPORTED_DESTINATION_TYPE, FLAT_FILE_DESTINATION_TYPE}

# Formats evidenciados en el corpus real (ver auditoria): RaggedRight en 3/4
# packages, Delimited en 1/4 (TarjetaDebitoSinUsoLink, una sola columna).
# FixedWidth queda fuera de v1 por falta de evidencia.
SUPPORTED_FLAT_FILE_FORMATS = {"ragged_right", "delimited"}

# HeaderRowDelimiter: unicos 2 valores evidenciados (CRLF en 3/4, SEMICOLON
# en TarjetaDebitoSinUsoLink). Token amigable en el spec, nunca la forma
# cruda '_x...' de SSIS (ver generator/flat_file_generator.py).
SUPPORTED_HEADER_ROW_DELIMITERS = {"CRLF", "SEMICOLON"}

# TextQualifier: unico valor evidenciado en los 4 packages ('<none>').
SUPPORTED_TEXT_QUALIFIERS = {"none"}

# DataType de columna Flat File: unicos 2 evidenciados (str en 3/4 packages
# completos + parte de Tokenizacion; wstr en el resto de Tokenizacion, un
# caso de coercion interna del propio Flat File Destination sin Data
# Conversion -- ver docs/template_teradata_to_flat_file_v1.md, "str -> wstr").
SUPPORTED_FLAT_FILE_COLUMN_DATA_TYPES = {"str", "wstr"}

# derived-column-v1: segundo tipo de transformacion soportado, ademas de
# 'data_conversion' -- ver docs/derived_column_v1.md. A lo sumo UN elemento
# de CADA tipo por Data Flow (nunca 2 del mismo tipo); si ambos coexisten,
# 'data_conversion' debe declararse ANTES que 'derived_column' en
# 'transformations[]' -- unico orden evidenciado en el corpus real
# (BipSuc_Turnero.dtsx, 3 instancias identicas: Source -> Data Conversion ->
# Derived Column -> Destination). La lista codifica el orden real del
# pipeline, nunca se reordena.
DERIVED_COLUMN_TRANSFORMATION_TYPE = "derived_column"
SUPPORTED_TRANSFORMATION_TYPES = {SUPPORTED_TRANSFORMATION_TYPE, DERIVED_COLUMN_TRANSFORMATION_TYPE}

# Unica operacion estructurada soportada en derived-column-v1 (ver
# docs/derived_column_v1.md) -- cualquier otra ('cast', 'constant',
# 'arbitrary_expression', etc.) se rechaza explicitamente, nunca en silencio.
NULL_PRESERVING_CAST_OPERATION = "null_preserving_cast"
SUPPORTED_DERIVED_COLUMN_OPERATIONS = {NULL_PRESERVING_CAST_OPERATION}

# Unico target_type soportado por una Derived Column en v1 -- sin evidencia
# de ningun otro (ver auditoria de derived-column-v1).
SUPPORTED_DERIVED_COLUMN_TARGET_TYPE = "wstr"

# teradata-to-sql-profile-v1: unicos dos valores de DTSProtectionLevel con
# evidencia real en el corpus -- "EncryptSensitiveWithPassword" en los 2
# proyectos productivos reales (BipSuc, PagosYRecaudaciones; DTS:ProtectionLevel="2"
# a nivel package), "EncryptSensitiveWithUserKey" en el unico proyecto SSDT
# recien creado sin customizar (SSDT_Golden; DTS:ProtectionLevel="1") -- ver
# docs/teradata_to_sql_profile_v1.md. Campo OPCIONAL en 'package': ausente
# preserva el comportamiento actual (ProtectionLevel heredado del template
# sin tocar); presente debe ser uno de estos dos valores evidenciados, nunca
# un codigo numerico crudo ni un valor inventado del enum DTSProtectionLevel.
SUPPORTED_PACKAGE_PROTECTION_LEVELS = {
    "EncryptSensitiveWithPassword",
    "EncryptSensitiveWithUserKey",
}

# Provider esperado para cada rol, segun el unico tipo de source/destination
# que este MVP sabe escribir (Microsoft.SSISTeradataSrc / Microsoft.OLEDBDestination).
# No es una propiedad del ProjectContext -- es una regla de ESTE generador.
EXPECTED_SOURCE_PROVIDER = "TERADATA"
EXPECTED_DESTINATION_PROVIDER = "OLEDB"


class SpecValidationError(Exception):
    """Se lanza cuando validate_spec() encuentra uno o mas problemas.
    El mensaje incluye la lista completa, numerada, de errores."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        message = "process_spec invalido:\n" + "\n".join(
            f"  {i}. {e}" for i, e in enumerate(errors, start=1)
        )
        super().__init__(message)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_connection_reference(
    label: str,
    connection_name: str,
    expected_provider: str,
    project_connections: Dict[str, Any],
) -> List[str]:
    """
    Valida que connection_name exista en el ProjectContext y que su provider
    sea el esperado para ese rol (TERADATA para source, OLEDB para
    destination en este MVP). Dos errores distintos y reconocibles por
    separado: "no existe" vs "existe pero es de otro provider" -- no se
    colapsan en un solo mensaje generico.
    """
    conn = project_connections.get(connection_name)
    if conn is None:
        known = sorted(project_connections) or ["(ninguna)"]
        return [
            f"{label} = {connection_name!r} no existe en el ProjectContext del "
            f"proyecto. Connection Managers disponibles: {known}."
        ]

    provider = conn.get("provider")
    if provider != expected_provider:
        return [
            f"{label} = {connection_name!r} existe en el ProjectContext, pero su "
            f"provider es {provider!r} (se esperaba {expected_provider!r} para este rol)."
        ]

    return []


def _validate_optional_teradata_source_sessions(
    source: Dict[str, Any], label: str
) -> List[str]:
    """
    teradata-to-sql-profile-v1: 'min_sessions'/'max_sessions' son OPCIONALES
    en 'data_flow.source' -- ausentes preservan el comportamiento actual
    (valores heredados del template sin tocar, ver
    generator/campanias_generator.py::_build_teradata_source). Si estan
    presentes, deben ser enteros positivos -- no se valida aca ningun rango
    "razonable" especifico (ej. min<=max): no hay evidencia de que SSIS lo
    exija, y esta funcion solo valida el TIPO, igual criterio que el resto
    de los campos numericos opcionales de este modulo (ej. 'access_mode').
    """
    errors: List[str] = []
    if "min_sessions" in source and not _positive_int(source.get("min_sessions")):
        errors.append(f"'{label}.min_sessions', si esta presente, debe ser entero positivo.")
    if "max_sessions" in source and not _positive_int(source.get("max_sessions")):
        errors.append(f"'{label}.max_sessions', si esta presente, debe ser entero positivo.")
    return errors


def _validate_flat_file_destination(
    destination: Dict[str, Any],
    pipeline_columns: set,
    project_parameters: List[Dict[str, Any]],
    label: str,
) -> List[str]:
    """
    template-teradata-to-flat-file-v1. Valida 'destination.connection_manager'
    (Flat File Connection Manager PACKAGE-level, declarado inline -- ver
    docs/template_teradata_to_flat_file_v1.md, "Separacion WHAT/WHERE") y
    'destination.columns' (UNA sola lista que sirve de mapping Y de schema
    fisico del archivo -- nunca dos declaraciones paralelas del mismo dato).

    Deliberadamente NO usa mapping_planner: la decision de equipo "SQL-first"
    exige mappings explicitos, nunca inferidos por capacidad/longitud (ver
    docs/template_teradata_to_flat_file_v1.md, "Mapping Planner" -- el gap de
    longitud-fuente-no-confiable de columnas TO_CHAR haria que la regla
    UNSAFE de mapping_planner rechace mappings reales validos).
    """
    errors: List[str] = []
    parameter_names = {p.get("name") for p in project_parameters}

    cm = destination.get("connection_manager")
    if not isinstance(cm, dict):
        errors.append(f"Falta '{label}.destination.connection_manager' (objeto).")
        cm = {}
    else:
        if not _non_empty_str(cm.get("name")):
            errors.append(
                f"'{label}.destination.connection_manager.name' es obligatorio y "
                "no puede estar vacio."
            )
        if cm.get("format") not in SUPPORTED_FLAT_FILE_FORMATS:
            errors.append(
                f"'{label}.destination.connection_manager.format' debe ser uno de "
                f"{sorted(SUPPORTED_FLAT_FILE_FORMATS)}; vino: {cm.get('format')!r}."
            )
        if not _positive_int(cm.get("code_page")):
            errors.append(
                f"'{label}.destination.connection_manager.code_page' es obligatorio "
                "y debe ser entero positivo."
            )
        if not _positive_int(cm.get("locale_id")):
            errors.append(
                f"'{label}.destination.connection_manager.locale_id' es obligatorio "
                "y debe ser entero positivo."
            )
        unicode_value = cm.get("unicode")
        if not isinstance(unicode_value, bool):
            errors.append(
                f"'{label}.destination.connection_manager.unicode' es obligatorio "
                "y debe ser booleano explicito (true/false) -- no se infiere ni "
                "se defaultea silenciosamente."
            )
        elif unicode_value is True:
            errors.append(
                f"'{label}.destination.connection_manager.unicode' = true no tiene "
                "evidencia real en el corpus auditado (los 4 packages son "
                "Unicode=false, CodePage=1252) -- no soportado en v1."
            )
        if cm.get("header_row_delimiter") not in SUPPORTED_HEADER_ROW_DELIMITERS:
            errors.append(
                f"'{label}.destination.connection_manager.header_row_delimiter' debe "
                f"ser uno de {sorted(SUPPORTED_HEADER_ROW_DELIMITERS)}; "
                f"vino: {cm.get('header_row_delimiter')!r}."
            )
        if cm.get("text_qualifier") not in SUPPORTED_TEXT_QUALIFIERS:
            errors.append(
                f"'{label}.destination.connection_manager.text_qualifier' debe ser "
                f"uno de {sorted(SUPPORTED_TEXT_QUALIFIERS)}; "
                f"vino: {cm.get('text_qualifier')!r}."
            )

        filename = cm.get("filename")
        if not isinstance(filename, dict):
            errors.append(
                f"Falta '{label}.destination.connection_manager.filename' (objeto)."
            )
        else:
            parameter = filename.get("parameter")
            literal = filename.get("literal")
            variable = filename.get("variable")
            if parameter is None and literal is None and variable is None:
                errors.append(
                    f"'{label}.destination.connection_manager.filename' requiere al "
                    "menos uno de: 'parameter', 'literal', 'variable' -- no se "
                    "acepta una expresion generica sin estructurar."
                )
            if parameter is not None:
                if not _non_empty_str(parameter):
                    errors.append(
                        f"{label}.destination.connection_manager.filename.parameter, "
                        "si esta presente, no puede estar vacio."
                    )
                elif parameter not in parameter_names:
                    errors.append(
                        f"{label}.destination.connection_manager.filename.parameter "
                        f"= '{parameter}' no existe entre los parameters del "
                        f"proyecto (ProjectContext): {sorted(n for n in parameter_names if n)}."
                    )
            if literal is not None and not _non_empty_str(literal):
                errors.append(
                    f"{label}.destination.connection_manager.filename.literal, si "
                    "esta presente, no puede estar vacio."
                )
            if variable is not None:
                if not isinstance(variable, dict):
                    errors.append(
                        f"{label}.destination.connection_manager.filename.variable "
                        "debe ser un objeto."
                    )
                else:
                    if not _non_empty_str(variable.get("name")):
                        errors.append(
                            f"{label}.destination.connection_manager.filename.variable.name "
                            "es obligatorio y no puede estar vacio."
                        )
                    if not _non_empty_str(variable.get("value")):
                        errors.append(
                            f"{label}.destination.connection_manager.filename.variable.value "
                            "es obligatorio y no puede estar vacio."
                        )

    columns = destination.get("columns")
    if not isinstance(columns, list) or len(columns) == 0:
        errors.append(f"'{label}.destination.columns' debe ser una lista no vacia.")
        columns = []

    seen_targets = set()
    terminator_indexes = []
    for idx, col in enumerate(columns):
        c_label = f"{label}.destination.columns[{idx}]"
        if not isinstance(col, dict):
            errors.append(f"{c_label} debe ser un objeto.")
            continue

        target = col.get("target")
        if not _non_empty_str(target):
            errors.append(f"{c_label}.target es obligatorio y no puede estar vacio.")
        else:
            if target in seen_targets:
                errors.append(
                    f"{c_label}.target = '{target}' colisiona con otra columna del "
                    "archivo (los nombres de columna del Flat File deben ser "
                    "unicos entre si)."
                )
            seen_targets.add(target)

        is_terminator = bool(col.get("row_terminator"))
        if is_terminator:
            terminator_indexes.append(idx)

        source = col.get("source")
        if source is not None:
            if not _non_empty_str(source):
                errors.append(f"{c_label}.source, si esta presente, no puede estar vacio.")
            elif source not in pipeline_columns:
                errors.append(
                    f"{c_label}.source = '{source}' no existe entre las columnas "
                    f"declaradas en {label}.source.columns."
                )

        target_data_type = col.get("target_data_type")
        target_length = col.get("target_length")
        # Columna sentinel pura (row_terminator=true, sin source ni metadata
        # de datos real, ej. 'EndLine'): target_data_type/target_length
        # quedan OPCIONALES. Cualquier otra columna (terminator con datos
        # reales, o columna normal) los requiere siempre.
        is_pure_sentinel = is_terminator and source is None and target_data_type is None and target_length is None
        if not is_pure_sentinel:
            if target_data_type not in SUPPORTED_FLAT_FILE_COLUMN_DATA_TYPES:
                errors.append(
                    f"{c_label}.target_data_type debe ser uno de "
                    f"{sorted(SUPPORTED_FLAT_FILE_COLUMN_DATA_TYPES)}; "
                    f"vino: {target_data_type!r}."
                )
            if not _positive_int(target_length):
                errors.append(
                    f"{c_label}.target_length es obligatorio y debe ser entero positivo."
                )
        target_code_page = col.get("target_code_page")
        if target_code_page is not None and not _positive_int(target_code_page):
            errors.append(
                f"{c_label}.target_code_page, si esta presente, debe ser entero positivo."
            )

    if len(terminator_indexes) == 0:
        errors.append(
            f"'{label}.destination.columns' debe tener exactamente 1 columna con "
            "'row_terminator: true' (el terminador de fila, ver "
            "docs/template_teradata_to_flat_file_v1.md, 'Ragged Right') -- no se "
            "encontro ninguna."
        )
    elif len(terminator_indexes) > 1:
        errors.append(
            f"'{label}.destination.columns' tiene {len(terminator_indexes)} columnas "
            "con 'row_terminator: true' -- debe haber exactamente 1."
        )
    elif terminator_indexes[0] != len(columns) - 1:
        errors.append(
            f"'{label}.destination.columns': la columna con 'row_terminator: true' "
            "debe ser la ULTIMA de la lista (unico orden evidenciado en el corpus "
            "real -- el terminador de fila siempre va al final)."
        )

    return errors


def validate_spec(spec: Dict[str, Any], project_context: Dict[str, Any]) -> List[str]:
    """
    Devuelve la lista de errores encontrados (vacia si el spec es valido).
    No lanza excepcion — ver assert_valid_spec() para la version que si.

    project_context es OBLIGATORIO: el resultado de
    project_context.context_builder.build_project_context(...) para el
    proyecto SSIS al que este paquete se va a agregar. Se usa para resolver
    y validar 'source.connection'/'destination.connection' por nombre --
    ver EXPECTED_SOURCE_PROVIDER/EXPECTED_DESTINATION_PROVIDER.
    """
    errors: List[str] = []

    if not isinstance(spec, dict):
        return ["El process_spec debe ser un objeto JSON (dict)."]

    if not isinstance(project_context, dict):
        errors.append(
            "Falta 'project_context' (debe ser el dict devuelto por "
            "project_context.context_builder.build_project_context(...))."
        )
        project_connections: Dict[str, Any] = {}
    else:
        project_connections = project_context.get("connections", {})

    package = spec.get("package")
    if not isinstance(package, dict):
        errors.append("Falta 'package' (objeto) en el process_spec.")
    else:
        if not _non_empty_str(package.get("name")):
            errors.append("'package.name' es obligatorio y no puede estar vacio.")
        # teradata-to-sql-profile-v1: 'protection_level' es OPCIONAL -- ausente
        # preserva el comportamiento actual (ver PROTECTION_LEVEL_CODES en
        # campanias_generator.py); presente debe ser uno de los 2 valores
        # evidenciados en SUPPORTED_PACKAGE_PROTECTION_LEVELS.
        if (
            "protection_level" in package
            and package.get("protection_level") not in SUPPORTED_PACKAGE_PROTECTION_LEVELS
        ):
            errors.append(
                "'package.protection_level', si esta presente, debe ser uno de "
                f"{sorted(SUPPORTED_PACKAGE_PROTECTION_LEVELS)}; "
                f"vino: {package.get('protection_level')!r}."
            )

    data_flow = spec.get("data_flow")
    if not isinstance(data_flow, dict):
        errors.append("Falta 'data_flow' (objeto) en el process_spec.")
        return errors  # nada mas se puede validar sin data_flow

    if not _non_empty_str(data_flow.get("name")):
        errors.append("'data_flow.name' es obligatorio y no puede estar vacio.")

    pipeline_columns = set()  # nombres validos como 'source' de un mapping

    # -----------------------------------------------------------------
    # source
    # -----------------------------------------------------------------
    source = data_flow.get("source")
    if not isinstance(source, dict):
        errors.append("Falta 'data_flow.source' (objeto).")
        source = {}
    else:
        if source.get("type") != SUPPORTED_SOURCE_TYPE:
            errors.append(
                f"'data_flow.source.type' debe ser '{SUPPORTED_SOURCE_TYPE}' "
                f"(este MVP no soporta otro tipo de origen); vino: {source.get('type')!r}."
            )
        if not _non_empty_str(source.get("name")):
            errors.append("'data_flow.source.name' es obligatorio y no puede estar vacio.")
        if not _non_empty_str(source.get("connection")):
            errors.append("'data_flow.source.connection' es obligatorio y no puede estar vacio.")
        else:
            errors.extend(
                _validate_connection_reference(
                    label="data_flow.source.connection",
                    connection_name=source["connection"],
                    expected_provider=EXPECTED_SOURCE_PROVIDER,
                    project_connections=project_connections,
                )
            )
        if not _non_empty_str(source.get("sql")):
            errors.append("'data_flow.source.sql' es obligatorio y no puede estar vacio.")

    columns = source.get("columns") if isinstance(source, dict) else None
    if not isinstance(columns, list) or len(columns) == 0:
        errors.append("'data_flow.source.columns' debe ser una lista no vacia.")
        columns = []

    seen_column_names = set()
    for idx, col in enumerate(columns):
        label = f"data_flow.source.columns[{idx}]"
        if not isinstance(col, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue
        name = col.get("name")
        if not _non_empty_str(name):
            errors.append(f"{label}.name es obligatorio y no puede estar vacio.")
            continue
        if name in seen_column_names:
            errors.append(
                f"Columna de origen duplicada: '{name}' aparece mas de una vez "
                "en data_flow.source.columns."
            )
        seen_column_names.add(name)
        pipeline_columns.add(name)

        data_type = col.get("data_type")
        if not _non_empty_str(data_type):
            errors.append(f"{label}.data_type es obligatorio y no puede estar vacio.")
            data_type = None
        if data_type in STRING_DATA_TYPES and not _positive_int(col.get("length")):
            errors.append(
                f"{label} (data_type={data_type!r}) requiere 'length' entero positivo."
            )
        if data_type == NON_UNICODE_STRING_TYPE and not _positive_int(col.get("code_page")):
            errors.append(
                f"{label} (data_type='str') requiere 'code_page' entero positivo."
            )
        if data_type == NUMERIC_DATA_TYPE:
            if not _positive_int(col.get("precision")):
                errors.append(
                    f"{label} (data_type='numeric') requiere 'precision' entero positivo."
                )
            if not _non_negative_int(col.get("scale")):
                errors.append(
                    f"{label} (data_type='numeric') requiere 'scale' entero >= 0."
                )

    # -----------------------------------------------------------------
    # transformations — 0, 1 o 2 elementos: 'data_conversion' y
    # 'derived_column' son OPCIONALES e INDEPENDIENTES (derived-column-v1
    # agrega el segundo tipo, ver SUPPORTED_TRANSFORMATION_TYPES). A lo sumo
    # UN elemento de CADA tipo (nunca 2 del mismo tipo). Si ambos coexisten,
    # 'data_conversion' debe declararse ANTES que 'derived_column' -- unico
    # orden evidenciado en el corpus real (ver
    # docs/derived_column_v1.md). La AUSENCIA de la clave 'transformations'
    # se trata igual que una lista vacia.
    # -----------------------------------------------------------------
    transformations = data_flow.get("transformations", [])
    if not isinstance(transformations, list):
        errors.append(
            "'data_flow.transformations' debe ser una lista (vacia si no hace "
            "falta ninguna transformacion, o con hasta un elemento por tipo "
            f"soportado: {sorted(SUPPORTED_TRANSFORMATION_TYPES)})."
        )
        transformations = []
    elif len(transformations) > len(SUPPORTED_TRANSFORMATION_TYPES):
        errors.append(
            "'data_flow.transformations' admite como maximo "
            f"{len(SUPPORTED_TRANSFORMATION_TYPES)} elementos en este MVP "
            f"(uno por tipo soportado: {sorted(SUPPORTED_TRANSFORMATION_TYPES)}). "
            f"Elementos encontrados: {len(transformations)}."
        )
        transformations = []

    conversion_outputs = set()
    derived_column_outputs = set()
    seen_transformation_types: List[str] = []

    for idx, transform in enumerate(transformations):
        label = f"data_flow.transformations[{idx}]"
        if not isinstance(transform, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        transform_type = transform.get("type")
        if transform_type not in SUPPORTED_TRANSFORMATION_TYPES:
            errors.append(
                f"{label}.type debe ser uno de {sorted(SUPPORTED_TRANSFORMATION_TYPES)} "
                f"(unicos tipos de transformacion soportados por este MVP); "
                f"vino: {transform_type!r}."
            )
            continue

        if transform_type in seen_transformation_types:
            errors.append(
                f"{label}.type = '{transform_type}' esta repetido -- a lo sumo "
                "una transformacion de cada tipo por Data Flow en este MVP."
            )
            continue
        seen_transformation_types.append(transform_type)

        if not _non_empty_str(transform.get("name")):
            errors.append(f"{label}.name es obligatorio y no puede estar vacio.")

        if transform_type == SUPPORTED_TRANSFORMATION_TYPE:
            # --- data_conversion: sin cambios de comportamiento ---
            conversions = transform.get("conversions")
            if not isinstance(conversions, list) or len(conversions) == 0:
                errors.append(f"{label}.conversions debe ser una lista no vacia.")
                conversions = []

            for c_idx, conv in enumerate(conversions):
                c_label = f"{label}.conversions[{c_idx}]"
                if not isinstance(conv, dict):
                    errors.append(f"{c_label} debe ser un objeto.")
                    continue
                conv_input = conv.get("input")
                conv_output = conv.get("output")
                target_type = conv.get("target_type")

                if not _non_empty_str(conv_input):
                    errors.append(f"{c_label}.input es obligatorio y no puede estar vacio.")
                elif conv_input not in seen_column_names:
                    errors.append(
                        f"{c_label}.input = '{conv_input}' no existe entre las columnas "
                        "declaradas en data_flow.source.columns."
                    )

                if not _non_empty_str(conv_output):
                    errors.append(f"{c_label}.output es obligatorio y no puede estar vacio.")
                else:
                    if conv_output in conversion_outputs:
                        errors.append(
                            f"{c_label}.output = '{conv_output}' colisiona con el output "
                            "de otra conversion (nombres de output de Data Conversion "
                            "deben ser unicos entre si)."
                        )
                    if conv_output in seen_column_names:
                        errors.append(
                            f"{c_label}.output = '{conv_output}' colisiona con un nombre "
                            "de columna de origen ya existente (generaria ambiguedad al "
                            "resolver 'source' en un mapping)."
                        )
                    conversion_outputs.add(conv_output)
                    pipeline_columns.add(conv_output)

                if not _non_empty_str(target_type):
                    errors.append(f"{c_label}.target_type es obligatorio y no puede estar vacio.")
                else:
                    # Mismo criterio que mappings[] (destino): el target_type de
                    # una conversion determina que metadata adicional hace falta
                    # para poder escribir un <outputColumn>/<externalMetadataColumn>
                    # valido (ver generator/campanias_generator.py::_build_data_conversion
                    # y docs/template_teradata_to_sql_v1_generalization_pagosyrecaudaciones.md,
                    # seccion 6 -- antes de esto, target_type='wstr'/'str' no
                    # exigia longitud y el generator la descartaba silenciosamente).
                    if target_type in STRING_DATA_TYPES and not _positive_int(
                        conv.get("target_length")
                    ):
                        errors.append(
                            f"{c_label} (target_type={target_type!r}) requiere "
                            "'target_length' entero positivo."
                        )
                    if target_type == NON_UNICODE_STRING_TYPE and not _positive_int(
                        conv.get("target_code_page")
                    ):
                        errors.append(
                            f"{c_label} (target_type='str') requiere 'target_code_page' "
                            "entero positivo."
                        )
                    if target_type == NUMERIC_DATA_TYPE:
                        if not _positive_int(conv.get("target_precision")):
                            errors.append(
                                f"{c_label} (target_type='numeric') requiere "
                                "'target_precision' entero positivo."
                            )
                        if not _non_negative_int(conv.get("target_scale")):
                            errors.append(
                                f"{c_label} (target_type='numeric') requiere "
                                "'target_scale' entero >= 0."
                            )

        elif transform_type == DERIVED_COLUMN_TRANSFORMATION_TYPE:
            # --- derived_column (derived-column-v1): unica operacion
            # soportada 'null_preserving_cast', unico target_type
            # soportado 'wstr'. NO se valida aca si el 'input' es realmente
            # i2/i4/i8, ni si 'target_length' alcanza la capacidad teorica
            # del origen -- esa clasificacion de seguridad (UNSAFE/
            # UNSUPPORTED) es responsabilidad de derived_planner, ANTES de
            # construir este ProcessSpec (mismo criterio de no duplicar
            # reglas entre capas ya aplicado en mapping_planner/). Este
            # modulo solo valida estructura: referencias, tipos de Python,
            # y el vocabulario cerrado de 'operation'/'target_type'.
            columns = transform.get("columns")
            if not isinstance(columns, list) or len(columns) == 0:
                errors.append(f"{label}.columns debe ser una lista no vacia.")
                columns = []

            for c_idx, col in enumerate(columns):
                c_label = f"{label}.columns[{c_idx}]"
                if not isinstance(col, dict):
                    errors.append(f"{c_label} debe ser un objeto.")
                    continue

                col_input = col.get("input")
                col_output = col.get("output")
                operation = col.get("operation")
                target_type = col.get("target_type")

                if not _non_empty_str(col_input):
                    errors.append(f"{c_label}.input es obligatorio y no puede estar vacio.")
                elif col_input not in pipeline_columns:
                    errors.append(
                        f"{c_label}.input = '{col_input}' no existe entre las columnas "
                        "disponibles del pipeline en este punto (columnas de origen "
                        "u outputs de una transformacion previa)."
                    )

                if not _non_empty_str(col_output):
                    errors.append(f"{c_label}.output es obligatorio y no puede estar vacio.")
                else:
                    if col_output in derived_column_outputs:
                        errors.append(
                            f"{c_label}.output = '{col_output}' colisiona con el output "
                            "de otra columna derivada (nombres de output de Derived "
                            "Column deben ser unicos entre si)."
                        )
                    if col_output in pipeline_columns:
                        errors.append(
                            f"{c_label}.output = '{col_output}' colisiona con un nombre "
                            "de columna ya existente en el pipeline (generaria ambiguedad "
                            "al resolver 'source' en un mapping)."
                        )
                    derived_column_outputs.add(col_output)
                    pipeline_columns.add(col_output)

                if operation not in SUPPORTED_DERIVED_COLUMN_OPERATIONS:
                    errors.append(
                        f"{c_label}.operation debe ser uno de "
                        f"{sorted(SUPPORTED_DERIVED_COLUMN_OPERATIONS)} (unica(s) operacion(es) "
                        f"soportada(s) por derived-column-v1); vino: {operation!r}."
                    )

                if target_type != SUPPORTED_DERIVED_COLUMN_TARGET_TYPE:
                    errors.append(
                        f"{c_label}.target_type debe ser '{SUPPORTED_DERIVED_COLUMN_TARGET_TYPE}' "
                        f"(unico target soportado por derived-column-v1); vino: {target_type!r}."
                    )

                if not _positive_int(col.get("target_length")):
                    errors.append(
                        f"{c_label}.target_length es obligatorio y debe ser entero positivo."
                    )

    if (
        SUPPORTED_TRANSFORMATION_TYPE in seen_transformation_types
        and DERIVED_COLUMN_TRANSFORMATION_TYPE in seen_transformation_types
        and seen_transformation_types.index(SUPPORTED_TRANSFORMATION_TYPE)
        > seen_transformation_types.index(DERIVED_COLUMN_TRANSFORMATION_TYPE)
    ):
        errors.append(
            f"'data_flow.transformations': si coexisten '{SUPPORTED_TRANSFORMATION_TYPE}' y "
            f"'{DERIVED_COLUMN_TRANSFORMATION_TYPE}', '{SUPPORTED_TRANSFORMATION_TYPE}' debe "
            f"declararse ANTES que '{DERIVED_COLUMN_TRANSFORMATION_TYPE}' (unico orden "
            "evidenciado en el corpus real, ver docs/derived_column_v1.md)."
        )

    # -----------------------------------------------------------------
    # destination — 'ole_db' (sin cambios) o 'flat_file'
    # (template-teradata-to-flat-file-v1, ver _validate_flat_file_destination).
    # Los dos tipos son mutuamente excluyentes y cada uno valida su propia
    # forma de 'destination' -- no comparten 'mappings'/'connection'/'table'
    # (ole_db) vs 'connection_manager'/'columns' (flat_file).
    # -----------------------------------------------------------------
    destination = data_flow.get("destination")
    if not isinstance(destination, dict):
        errors.append("Falta 'data_flow.destination' (objeto).")
        return errors

    destination_type = destination.get("type")
    if destination_type not in SUPPORTED_DESTINATION_TYPES:
        errors.append(
            f"'data_flow.destination.type' debe ser uno de "
            f"{sorted(SUPPORTED_DESTINATION_TYPES)}; vino: {destination_type!r}."
        )
        return errors

    if not _non_empty_str(destination.get("name")):
        errors.append("'data_flow.destination.name' es obligatorio y no puede estar vacio.")

    if destination_type == FLAT_FILE_DESTINATION_TYPE:
        project_parameters = (
            project_context.get("parameters", []) if isinstance(project_context, dict) else []
        )
        errors.extend(
            _validate_flat_file_destination(
                destination, pipeline_columns, project_parameters, "data_flow"
            )
        )
        return errors

    # --- ole_db: sin cambios de comportamiento ---
    if not _non_empty_str(destination.get("connection")):
        errors.append(
            "'data_flow.destination.connection' es obligatorio y no puede estar vacio."
        )
    else:
        errors.extend(
            _validate_connection_reference(
                label="data_flow.destination.connection",
                connection_name=destination["connection"],
                expected_provider=EXPECTED_DESTINATION_PROVIDER,
                project_connections=project_connections,
            )
        )
    if not _non_empty_str(destination.get("table")):
        errors.append("'data_flow.destination.table' es obligatorio y no puede estar vacio.")

    # access_mode: OPCIONAL. Ausente -> el generator preserva el valor
    # que ya trae el template (0, igual que Campanias real). Presente ->
    # sobrescribe la property "AccessMode" del OLE DB Destination (ver
    # generator/campanias_generator.py::_build_ole_db_destination).
    #
    # Solo se valida el TIPO (entero >= 0, igual criterio que 'scale'),
    # no un enum cerrado de valores permitidos: la evidencia real
    # disponible son unicamente 0 (Campanias) y 3 (PagosYRecaudaciones) --
    # ver docs/template_teradata_to_sql_v1_generalization_pagosyrecaudaciones.md.
    # Restringir a un conjunto fijo (p.ej. 0-4, el rango tipico del
    # dropdown "Data access mode" de OLE DB Destination en SSDT)
    # codificaria una suposicion sobre el enum interno de SSIS que esta
    # auditoria no pudo confirmar de forma independiente para todos sus
    # valores -- se prefiere no inferir mas alla de lo evidenciado.
    if "access_mode" in destination and not _non_negative_int(
        destination.get("access_mode")
    ):
        errors.append(
            "'data_flow.destination.access_mode', si esta presente, debe "
            "ser un entero >= 0 (valor crudo de la property AccessMode "
            "del OLE DB Destination)."
        )

    mappings = destination.get("mappings") if isinstance(destination, dict) else None
    if not isinstance(mappings, list) or len(mappings) == 0:
        errors.append("'data_flow.destination.mappings' debe ser una lista no vacia.")
        mappings = []

    for idx, mapping in enumerate(mappings):
        label = f"data_flow.destination.mappings[{idx}]"
        if not isinstance(mapping, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        source_col = mapping.get("source")
        if not _non_empty_str(source_col):
            errors.append(f"{label}.source es obligatorio y no puede estar vacio.")
        elif source_col not in pipeline_columns:
            errors.append(
                f"{label}.source = '{source_col}' no existe en el pipeline (ni como "
                "columna de origen ni como output de una conversion)."
            )

        if not _non_empty_str(mapping.get("target")):
            errors.append(f"{label}.target es obligatorio y no puede estar vacio.")

        target_type = mapping.get("target_data_type")
        if not _non_empty_str(target_type):
            errors.append(f"{label}.target_data_type es obligatorio y no puede estar vacio.")

        if target_type in STRING_DATA_TYPES and not _positive_int(mapping.get("target_length")):
            errors.append(
                f"{label} (target_data_type={target_type!r}) requiere "
                "'target_length' entero positivo."
            )
        if target_type == NON_UNICODE_STRING_TYPE and not _positive_int(
            mapping.get("target_code_page")
        ):
            errors.append(
                f"{label} (target_data_type='str') requiere 'target_code_page' "
                "entero positivo."
            )
        if target_type == NUMERIC_DATA_TYPE:
            if not _positive_int(mapping.get("target_precision")):
                errors.append(
                    f"{label} (target_data_type='numeric') requiere "
                    "'target_precision' entero positivo."
                )
            if not _non_negative_int(mapping.get("target_scale")):
                errors.append(
                    f"{label} (target_data_type='numeric') requiere "
                    "'target_scale' entero >= 0."
                )

    source = data_flow.get("source")
    if isinstance(source, dict):
        errors.extend(
            _validate_optional_teradata_source_sessions(source, label="data_flow.source")
        )

    return errors


def assert_valid_spec(spec: Dict[str, Any], project_context: Dict[str, Any]) -> None:
    """Como validate_spec(), pero lanza SpecValidationError si hay errores.
    Debe llamarse ANTES de tocar el template — el generador nunca modifica
    XML con un spec invalido."""
    errors = validate_spec(spec, project_context)
    if errors:
        raise SpecValidationError(errors)
