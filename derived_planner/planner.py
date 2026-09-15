"""
planner.py

Motor de clasificacion y naming de derived-column-v1: valida el input (ver
validator.py), clasifica cada regla de derivacion y produce un plan.

Orden de prioridad de clasificacion (deliberado y determinista, mismo
criterio que mapping_planner.planner.classify_mapping):
  1. operacion no reconocida (fuera de schema.SUPPORTED_OPERATIONS) ->
     UNSUPPORTED de una.
  2. par (source_type, target_type) no soportado por la operacion ->
     UNSUPPORTED.
  3. capacidad declarada (target_length) insuficiente frente a la capacidad
     TEORICA del tipo origen (schema.THEORETICAL_STRING_CAPACITY) -> UNSAFE,
     SIEMPRE, con prioridad absoluta -- nunca se infiere ni se ajusta
     mirando datos reales (ver docs/derived_column_v1.md).
  4. capacidad suficiente -> CONVERSION_REQUIRED (unica clasificacion
     alcanzable para 'null_preserving_cast' en v1).

El planner NUNCA inspecciona datos ni infiere target_length -- el input ya
validado (ver validator.py) declara siempre target_length explicitamente;
el planner solo decide, no completa.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .schema import (
    CONVERSION_REQUIRED,
    DERIVED_OUTPUT_SUFFIX,
    SUPPORTED_OPERATIONS,
    SUPPORTED_SOURCE_TYPES,
    SUPPORTED_TARGET_TYPE,
    THEORETICAL_STRING_CAPACITY,
    UNSAFE,
    UNSUPPORTED,
)
from .validator import assert_valid_derived_rules


class OutputNameCollisionError(Exception):
    """Se lanza cuando el output (explicito o determinista '<source>__derived')
    de una regla de derivacion colisiona con un nombre ya existente en
    source_schema o con el output de otra regla ya resuelta en el mismo
    plan. Nunca se agrega un sufijo numerico automatico -- error explicito,
    ver docs/derived_column_v1.md."""


def classify_derived_rule(source_meta: Dict[str, Any], rule: Dict[str, Any]) -> Tuple[str, str]:
    """Devuelve (classification, reason) para una unica regla de derivacion.
    Funcion pura, sin efectos secundarios -- ver docs/derived_column_v1.md
    para la matriz de compatibilidad completa que esta funcion implementa."""
    operation = rule.get("operation")
    source_type = source_meta.get("data_type")
    target_type = rule.get("target_type")
    target_length = rule.get("target_length")

    if operation not in SUPPORTED_OPERATIONS:
        return UNSUPPORTED, (
            f"operacion no soportada por derived-column-v1: {operation!r} "
            f"(soportadas: {sorted(SUPPORTED_OPERATIONS)})."
        )

    if source_type not in SUPPORTED_SOURCE_TYPES or target_type != SUPPORTED_TARGET_TYPE:
        return UNSUPPORTED, (
            f"par (source_type, target_type) no soportado por "
            f"'{operation}' en derived-column-v1: source={source_type!r}, "
            f"target={target_type!r} (origenes soportados: "
            f"{sorted(SUPPORTED_SOURCE_TYPES)}, destino soportado: "
            f"{SUPPORTED_TARGET_TYPE!r})."
        )

    # -----------------------------------------------------------------
    # UNSAFE: capacidad declarada insuficiente frente a la capacidad
    # TEORICA (no observada) del tipo origen -- prioridad absoluta, igual
    # que la regla de UNSAFE de mapping_planner. Nunca automatico.
    # -----------------------------------------------------------------
    required_capacity = THEORETICAL_STRING_CAPACITY[source_type]
    if target_length < required_capacity:
        return UNSAFE, (
            f"target_length={target_length} insuficiente para representar "
            f"cualquier valor posible de {source_type} (capacidad teorica "
            f"requerida, signo incluido: {required_capacity}); nunca automatico, "
            "nunca inferido de datos observados."
        )

    return CONVERSION_REQUIRED, (
        f"{operation}: {source_type}->{target_type}({target_length}), capacidad "
        f"declarada suficiente (>= {required_capacity})."
    )


def build_derived_column_plan(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Valida el input (fail-fast) y devuelve el plan completo: una entrada por
    regla de derivacion, en el mismo orden declarado.

    Lanza OutputNameCollisionError si el output (explicito o determinista)
    de una regla colisiona con un nombre de source_schema o con el output
    de otra regla ya resuelta en este mismo plan -- esto incluye el caso de
    dos reglas identicas sobre la misma 'source' sin 'output' explicito,
    que de otro modo generarian el mismo nombre por defecto dos veces.
    """
    assert_valid_derived_rules(spec)

    source_by_name = {col["name"]: col for col in spec["source_schema"]}
    source_names = set(source_by_name)
    outputs_used: set = set()

    plan: List[Dict[str, Any]] = []
    for rule in spec["derived_rules"]:
        source_name = rule["source"]
        source_meta = source_by_name[source_name]

        output_name = rule.get("output") or f"{source_name}{DERIVED_OUTPUT_SUFFIX}"
        if output_name in source_names or output_name in outputs_used:
            raise OutputNameCollisionError(
                f"El output '{output_name}' (para la derivacion de '{source_name}') "
                "colisiona con un nombre ya existente en source_schema o con el "
                "output de otra regla de derivacion ya resuelta en este plan. No se "
                "agrega un sufijo numerico automatico -- declarar 'output' "
                "explicitamente o resolver manualmente."
            )
        outputs_used.add(output_name)

        classification, reason = classify_derived_rule(source_meta, rule)

        plan.append(
            {
                "source": source_name,
                "output": output_name,
                "classification": classification,
                "reason": reason,
                "source_metadata": {"data_type": source_meta.get("data_type")},
                "operation": rule.get("operation"),
                "target_type": rule.get("target_type"),
                "target_length": rule.get("target_length"),
            }
        )

    return plan
