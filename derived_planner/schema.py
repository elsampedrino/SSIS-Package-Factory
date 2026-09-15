"""
schema.py

Constantes y contratos de derived-column-v1 -- ver docs/derived_column_v1.md
para la auditoria completa que respalda cada valor.

Taxonomia de clasificacion: REUTILIZADA de mapping_planner.schema, sin
duplicar ni introducir una categoria nueva (se evaluo y descarto una
categoria 'SAFE_DERIVATION' dedicada -- ver docs/derived_column_v1.md,
seccion Taxonomia). 'null_preserving_cast' valido y con capacidad suficiente
clasifica CONVERSION_REQUIRED (es, en esencia, una conversion de tipo con
manejo explicito de NULL); 'target_length' insuficiente clasifica UNSAFE
(prioridad absoluta, igual que en mapping_planner); una operacion o par de
tipos no soportados clasifica UNSUPPORTED.

Alcance funcional de v1: UNA sola operacion estructurada
('null_preserving_cast'), origen restringido a los enteros exactos con
evidencia real (i2/i4/i8), destino restringido a 'wstr'. Ninguna otra
combinacion tiene evidencia real en el corpus -- ver docs/derived_column_v1.md.
"""

from __future__ import annotations

from mapping_planner.schema import (  # noqa: F401 -- reexport deliberado
    AMBIGUOUS,
    CONVERSION_REQUIRED,
    DIRECT,
    RESOLVABLE_CLASSIFICATIONS,
    UNSAFE,
    UNSUPPORTED,
)

# ---------------------------------------------------------------------------
# Alcance funcional (unica operacion, unico target) -- ver
# docs/derived_column_v1.md, seccion "Patron soportado".
# ---------------------------------------------------------------------------
NULL_PRESERVING_CAST = "null_preserving_cast"
SUPPORTED_OPERATIONS = {NULL_PRESERVING_CAST}

SUPPORTED_SOURCE_TYPES = {"i2", "i4", "i8"}
SUPPORTED_TARGET_TYPE = "wstr"

# ---------------------------------------------------------------------------
# Capacidad TEORICA (no observada, no inferida de datos) que un wstr(n)
# necesita para representar CUALQUIER valor posible del tipo entero origen,
# signo incluido -- ver docs/derived_column_v1.md, seccion "Regla de
# seguridad". Nunca se ajusta mirando valores reales de ninguna tabla.
#   i2 (SMALLINT): rango [-32768, 32767]  -> 6 caracteres ("-32768")
#   i4 (INTEGER):  rango [-2147483648, 2147483647] -> 11 caracteres
#   i8 (BIGINT):   rango [-9223372036854775808, 9223372036854775807] -> 20 caracteres
# ---------------------------------------------------------------------------
THEORETICAL_STRING_CAPACITY = {"i2": 6, "i4": 11, "i8": 20}

# Naming determinista del output de una derivacion sin 'output' explicito
# (ver docs/derived_column_v1.md, seccion "Naming"). Distinto del sufijo de
# mapping-planner-v1 ('__conv') para no confundir una columna derivada con
# una columna resultado de Data Conversion.
DERIVED_OUTPUT_SUFFIX = "__derived"
