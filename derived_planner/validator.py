"""
validator.py

Validacion fail-fast del input de derived_planner -- ANTES de clasificar
nada. Mismo criterio de aislamiento y de "juntar todos los errores en una
sola pasada" que mapping_planner/validator.py y generator/spec_validator.py,
pero este modulo es independiente en tiempo de ejecucion de ambos.

Input esperado:
    {
        "source_schema": [{"name": ..., "data_type": ...}, ...],
        "derived_rules": [
            {
                "source": ...,
                "output": ... ,       # opcional -- ver planner.py para el
                                       # naming determinista por defecto
                "operation": ...,
                "target_type": ...,
                "target_length": ...,
            },
            ...
        ],
    }

Deliberadamente NO valida que 'data_type'/'operation'/'target_type' esten
dentro de los conjuntos soportados de schema.py -- eso es responsabilidad
del planner (ver planner.py), que clasifica un valor no soportado como
UNSUPPORTED dentro del plan en vez de abortar aca sin producir ningun plan.
Este modulo solo valida ESTRUCTURA: nombres, unicidad, referencias, y que
los campos minimos esten presentes con el tipo de dato de Python correcto.

Este input es AUTOCONTENIDO: a diferencia de mapping_planner (que resuelve
mappings[] contra un "destino" declarado por fuera), derived_planner no
reutiliza el concepto de Logical Mapping -- 'derived_rules' no es una lista
de mappings, es una lista de derivaciones nuevas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


class DerivedRulesValidationError(Exception):
    """Se lanza cuando validate_derived_rules() encuentra uno o mas
    problemas. El mensaje incluye la lista completa, numerada, de errores."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        message = "Reglas de derivacion invalidas:\n" + "\n".join(
            f"  {i}. {e}" for i, e in enumerate(errors, start=1)
        )
        super().__init__(message)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_source_schema(source_schema: Any) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    errors: List[str] = []
    by_name: Dict[str, Dict[str, Any]] = {}

    if not isinstance(source_schema, list) or len(source_schema) == 0:
        return ["'source_schema' debe ser una lista no vacia."], by_name

    for idx, col in enumerate(source_schema):
        label = f"source_schema[{idx}]"
        if not isinstance(col, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        name = col.get("name")
        if not _non_empty_str(name):
            errors.append(f"{label}.name es obligatorio y no puede estar vacio.")
            continue
        if name in by_name:
            errors.append(
                f"'{name}' aparece mas de una vez en source_schema (los nombres deben ser unicos)."
            )
        by_name[name] = col

        # i2/i4/i8 (unicos tipos de origen soportados en v1) no llevan
        # metadata adicional (length/precision/scale) -- a diferencia de
        # mapping_planner, aca no hace falta metadata condicional por tipo.
        if not _non_empty_str(col.get("data_type")):
            errors.append(f"{label}.data_type es obligatorio y no puede estar vacio.")

    return errors, by_name


def _validate_derived_rules(
    derived_rules: Any, source_by_name: Dict[str, Dict[str, Any]]
) -> List[str]:
    errors: List[str] = []

    if not isinstance(derived_rules, list) or len(derived_rules) == 0:
        return ["'derived_rules' debe ser una lista no vacia."]

    explicit_outputs_seen: set = set()

    for idx, rule in enumerate(derived_rules):
        label = f"derived_rules[{idx}]"
        if not isinstance(rule, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        source_name = rule.get("source")
        if not _non_empty_str(source_name):
            errors.append(f"{label}.source es obligatorio y no puede estar vacio.")
        elif source_name not in source_by_name:
            errors.append(f"{label}.source = '{source_name}' no existe en 'source_schema'.")

        output_name = rule.get("output")
        if output_name is not None:
            if not _non_empty_str(output_name):
                errors.append(f"{label}.output, si se declara, no puede estar vacio.")
            else:
                if output_name in source_by_name:
                    errors.append(
                        f"{label}.output = '{output_name}' colisiona con un nombre de "
                        "columna ya existente en 'source_schema'."
                    )
                if output_name in explicit_outputs_seen:
                    errors.append(
                        f"{label}.output = '{output_name}' esta duplicado (ya fue usado "
                        "como output explicito de otra regla de derivacion)."
                    )
                explicit_outputs_seen.add(output_name)

        if not _non_empty_str(rule.get("operation")):
            errors.append(f"{label}.operation es obligatorio y no puede estar vacio.")

        if not _non_empty_str(rule.get("target_type")):
            errors.append(f"{label}.target_type es obligatorio y no puede estar vacio.")

        if not _positive_int(rule.get("target_length")):
            errors.append(f"{label}.target_length es obligatorio y debe ser entero positivo.")

    return errors


def validate_derived_rules(spec: Dict[str, Any]) -> List[str]:
    """Devuelve la lista de errores encontrados (vacia si el input es
    valido). No lanza excepcion -- ver assert_valid_derived_rules()."""
    if not isinstance(spec, dict):
        return ["El input de derived_planner debe ser un objeto (dict)."]

    schema_errors, source_by_name = _validate_source_schema(spec.get("source_schema"))
    rule_errors = _validate_derived_rules(spec.get("derived_rules"), source_by_name)

    return schema_errors + rule_errors


def assert_valid_derived_rules(spec: Dict[str, Any]) -> None:
    """Como validate_derived_rules(), pero lanza DerivedRulesValidationError
    si hay errores. Debe llamarse ANTES de clasificar nada (ver
    planner.build_derived_column_plan)."""
    errors = validate_derived_rules(spec)
    if errors:
        raise DerivedRulesValidationError(errors)
