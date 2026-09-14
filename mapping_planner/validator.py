"""
validator.py

Validacion fail-fast del Logical Mapping (input de mapping_planner) --
ANTES de clasificar nada. Junta TODOS los errores en una sola pasada, mismo
criterio que generator/spec_validator.py y project_generator/spec_schema.py
-- pero este modulo es independiente en tiempo de ejecucion de ambos (no
los importa), mismo criterio de aislamiento ya aplicado entre generator/,
project_context/, project_generator/ y profiles/.

Deliberadamente NO valida que 'data_type'/'target_data_type' esten dentro
de schema.SUPPORTED_TYPES -- eso es responsabilidad del planner (ver
planner.py), que clasifica un tipo desconocido como UNSUPPORTED dentro del
Transformation Plan en vez de abortar aca sin producir ningun plan. Este
modulo solo valida ESTRUCTURA: nombres, unicidad, referencias, y la
metadata condicional que ya conoce para los tipos reconocidos
(str/wstr/numeric) -- para un tipo no reconocido no exige nada adicional,
porque no sabe que exigirle.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .schema import NON_UNICODE_STRING_TYPE, NUMERIC_TYPE, STRING_TYPES


class LogicalMappingValidationError(Exception):
    """Se lanza cuando validate_logical_mapping() encuentra uno o mas
    problemas. El mensaje incluye la lista completa, numerada, de errores."""

    def __init__(self, errors: List[str]):
        self.errors = errors
        message = "Logical Mapping invalido:\n" + "\n".join(
            f"  {i}. {e}" for i, e in enumerate(errors, start=1)
        )
        super().__init__(message)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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

        data_type = col.get("data_type")
        if not _non_empty_str(data_type):
            errors.append(f"{label}.data_type es obligatorio y no puede estar vacio.")
            continue

        # Metadata condicional -- SOLO para los tipos que la propia
        # evidencia dice que la necesitan (str/wstr/numeric). Un tipo no
        # reconocido no exige nada aca: el planner lo clasifica UNSUPPORTED.
        if data_type in STRING_TYPES and not _positive_int(col.get("length")):
            errors.append(f"{label} (data_type={data_type!r}) requiere 'length' entero positivo.")
        if data_type == NON_UNICODE_STRING_TYPE and not _positive_int(col.get("code_page")):
            errors.append(f"{label} (data_type='str') requiere 'code_page' entero positivo.")
        if data_type == NUMERIC_TYPE:
            if not _positive_int(col.get("precision")):
                errors.append(f"{label} (data_type='numeric') requiere 'precision' entero positivo.")
            if not _non_negative_int(col.get("scale")):
                errors.append(f"{label} (data_type='numeric') requiere 'scale' entero >= 0.")

    return errors, by_name


def _validate_mappings(mappings: Any, source_by_name: Dict[str, Dict[str, Any]]) -> List[str]:
    errors: List[str] = []

    if not isinstance(mappings, list) or len(mappings) == 0:
        return ["'mappings' debe ser una lista no vacia."]

    seen_targets = set()
    seen_pairs = set()

    for idx, mapping in enumerate(mappings):
        label = f"mappings[{idx}]"
        if not isinstance(mapping, dict):
            errors.append(f"{label} debe ser un objeto.")
            continue

        source_name = mapping.get("source")
        if not _non_empty_str(source_name):
            errors.append(f"{label}.source es obligatorio y no puede estar vacio.")
        elif source_name not in source_by_name:
            errors.append(f"{label}.source = '{source_name}' no existe en 'source_schema'.")

        target_name = mapping.get("target")
        if not _non_empty_str(target_name):
            errors.append(f"{label}.target es obligatorio y no puede estar vacio.")
        else:
            if target_name in seen_targets:
                errors.append(
                    f"'{target_name}' aparece como target en mas de un mapping (target duplicado)."
                )
            seen_targets.add(target_name)

        if _non_empty_str(source_name) and _non_empty_str(target_name):
            pair = (source_name, target_name)
            if pair in seen_pairs:
                errors.append(
                    f"{label}: el par (source={source_name!r}, target={target_name!r}) esta duplicado."
                )
            seen_pairs.add(pair)

        target_type = mapping.get("target_data_type")
        if not _non_empty_str(target_type):
            errors.append(f"{label}.target_data_type es obligatorio y no puede estar vacio.")
            continue

        if target_type in STRING_TYPES and not _positive_int(mapping.get("target_length")):
            errors.append(
                f"{label} (target_data_type={target_type!r}) requiere 'target_length' entero positivo."
            )
        if target_type == NON_UNICODE_STRING_TYPE and not _positive_int(mapping.get("target_code_page")):
            errors.append(
                f"{label} (target_data_type='str') requiere 'target_code_page' entero positivo."
            )
        if target_type == NUMERIC_TYPE:
            if not _positive_int(mapping.get("target_precision")):
                errors.append(
                    f"{label} (target_data_type='numeric') requiere 'target_precision' entero positivo."
                )
            if not _non_negative_int(mapping.get("target_scale")):
                errors.append(
                    f"{label} (target_data_type='numeric') requiere 'target_scale' entero >= 0."
                )

    return errors


def validate_logical_mapping(spec: Dict[str, Any]) -> List[str]:
    """Devuelve la lista de errores encontrados (vacia si el Logical Mapping
    es valido). No lanza excepcion -- ver assert_valid_logical_mapping()."""
    if not isinstance(spec, dict):
        return ["El Logical Mapping debe ser un objeto (dict)."]

    schema_errors, source_by_name = _validate_source_schema(spec.get("source_schema"))
    mapping_errors = _validate_mappings(spec.get("mappings"), source_by_name)

    return schema_errors + mapping_errors


def assert_valid_logical_mapping(spec: Dict[str, Any]) -> None:
    """Como validate_logical_mapping(), pero lanza LogicalMappingValidationError
    si hay errores. Debe llamarse ANTES de clasificar nada (ver
    planner.build_transformation_plan)."""
    errors = validate_logical_mapping(spec)
    if errors:
        raise LogicalMappingValidationError(errors)
