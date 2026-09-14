"""
schema.py

Constantes y contratos del Logical Mapping / Transformation Plan de
mapping-planner-v1 -- ver docs/mapping_planner_v1.md para la auditoria
completa que respalda cada valor.

Tipos soportados: UNICAMENTE los observados con evidencia real en el corpus
(BipSuc_CampaniasVigentes, PagosYRecaudaciones_FacturacionComi). Cualquier
tipo fuera de este conjunto se clasifica UNSUPPORTED por el planner (ver
planner.py) -- nunca rechazado de entrada por el validador (ver
validator.py): el objetivo es que el Transformation Plan pueda MOSTRAR por
que un par no es soportado, no reventar antes de construir el plan.
"""

from __future__ import annotations

STRING_TYPES = {"str", "wstr"}
NON_UNICODE_STRING_TYPE = "str"
INTEGER_TYPES = {"i2", "i4", "i8"}
NUMERIC_TYPE = "numeric"
DATE_TYPE = "dbDate"

SUPPORTED_TYPES = STRING_TYPES | INTEGER_TYPES | {NUMERIC_TYPE, DATE_TYPE}

# ---------------------------------------------------------------------------
# Categorias de clasificacion (ver docs/mapping_planner_v1.md, seccion
# "Categorias" para la semantica exacta de cada una).
# ---------------------------------------------------------------------------
DIRECT = "DIRECT"
CONVERSION_REQUIRED = "CONVERSION_REQUIRED"
AMBIGUOUS = "AMBIGUOUS"
UNSAFE = "UNSAFE"
UNSUPPORTED = "UNSUPPORTED"

CLASSIFICATIONS = {DIRECT, CONVERSION_REQUIRED, AMBIGUOUS, UNSAFE, UNSUPPORTED}

# Unicas dos clasificaciones que translator.py puede convertir en un
# fragmento de ProcessSpec automatico -- AMBIGUOUS/UNSAFE/UNSUPPORTED
# siempre bloquean la traduccion (ver translator.PlanNotResolvedError).
RESOLVABLE_CLASSIFICATIONS = {DIRECT, CONVERSION_REQUIRED}

# Naming determinista de outputs de conversion generados por el planner
# (ver docs/mapping_planner_v1.md, seccion "Naming"). Doble guion bajo para
# no colisionar con el patron humano real observado en el corpus (`_Sal`).
CONVERSION_OUTPUT_SUFFIX = "__conv"

# target_type fijo para el UNICO caso CONVERSION_REQUIRED evidenciado en v1
# (str(n) -> wstr(n)) -- ver planner.classify_mapping().
CONVERSION_TARGET_TYPE = "wstr"
