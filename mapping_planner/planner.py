"""
planner.py

Motor de clasificacion de mapping-planner-v1: compara metadata de origen
(source_schema) y metadata destino (declarada explicitamente en cada
mapping -- en v1 NO existe "destination schema" como entidad de primera
clase, ver docs/mapping_planner_v1.md) y produce un Transformation Plan.

Orden de prioridad de clasificacion (deliberado y determinista):
  1. tipo no reconocido (fuera de schema.SUPPORTED_TYPES) -> UNSUPPORTED de una.
  2. perdida de capacidad detectada (string length / numeric precision o
     scale) -> UNSAFE, SIEMPRE, incluso si el par en si tiene evidencia real
     (ej. str(20)->wstr(10) es UNSAFE, no CONVERSION_REQUIRED-con-truncacion
     -- UNSAFE tiene prioridad sobre cualquier otra regla).
  3. reglas exactas evidenciadas -> DIRECT / CONVERSION_REQUIRED.
  4. el unico caso AMBIGUOUS conocido en v1 (str->dbDate).
  5. cualquier otra cosa -> UNSUPPORTED.

El planner NUNCA infiere metadata destino faltante -- el Logical Mapping ya
validado (ver validator.py) declara siempre toda la metadata necesaria; el
planner solo decide, no completa.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .schema import (
    CONVERSION_OUTPUT_SUFFIX,
    CONVERSION_REQUIRED,
    CONVERSION_TARGET_TYPE,
    DATE_TYPE,
    DIRECT,
    AMBIGUOUS,
    INTEGER_TYPES,
    NUMERIC_TYPE,
    STRING_TYPES,
    SUPPORTED_TYPES,
    UNSAFE,
    UNSUPPORTED,
)
from .validator import assert_valid_logical_mapping


class OutputNameCollisionError(Exception):
    """Se lanza cuando el output de conversion determinista ('<source>__conv')
    colisiona con un nombre ya existente en source_schema o con otro output
    de conversion ya generado en el mismo plan. Nunca se agrega un sufijo
    numerico automatico -- error explicito, ver docs/mapping_planner_v1.md."""


def _source_metadata(col: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "data_type": col.get("data_type"),
        "length": col.get("length"),
        "code_page": col.get("code_page"),
        "precision": col.get("precision"),
        "scale": col.get("scale"),
    }


def _target_metadata(mapping: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "target_data_type": mapping.get("target_data_type"),
        "target_length": mapping.get("target_length"),
        "target_code_page": mapping.get("target_code_page"),
        "target_precision": mapping.get("target_precision"),
        "target_scale": mapping.get("target_scale"),
    }


def classify_mapping(source_meta: Dict[str, Any], target_meta: Dict[str, Any]) -> Tuple[str, str]:
    """Devuelve (classification, reason) para un unico par (source_meta,
    target_meta). Funcion pura, sin efectos secundarios -- ver
    docs/mapping_planner_v1.md para la matriz de compatibilidad completa
    que esta funcion implementa."""
    source_type = source_meta.get("data_type")
    target_type = target_meta.get("target_data_type")

    if source_type not in SUPPORTED_TYPES or target_type not in SUPPORTED_TYPES:
        return UNSUPPORTED, (
            f"tipo no soportado por mapping-planner-v1: source={source_type!r}, "
            f"target={target_type!r} (soportados: {sorted(SUPPORTED_TYPES)})."
        )

    # -----------------------------------------------------------------
    # UNSAFE: perdida de capacidad -- SIEMPRE tiene prioridad sobre
    # cualquier regla DIRECT/CONVERSION_REQUIRED que el par pudiera matchear.
    # -----------------------------------------------------------------
    if source_type in STRING_TYPES and target_type in STRING_TYPES:
        if target_meta["target_length"] < source_meta["length"]:
            return UNSAFE, (
                f"reduccion de longitud: {source_type}({source_meta['length']}) -> "
                f"{target_type}({target_meta['target_length']}); nunca automatico."
            )

    if source_type == NUMERIC_TYPE and target_type == NUMERIC_TYPE:
        if target_meta["target_precision"] < source_meta["precision"]:
            return UNSAFE, (
                f"reduccion de precision numeric: "
                f"numeric({source_meta['precision']},{source_meta['scale']}) -> "
                f"numeric({target_meta['target_precision']},{target_meta['target_scale']}); "
                "nunca automatico."
            )
        if target_meta["target_scale"] < source_meta["scale"]:
            return UNSAFE, (
                f"reduccion de scale numeric (perdida de decimales): "
                f"numeric({source_meta['precision']},{source_meta['scale']}) -> "
                f"numeric({target_meta['target_precision']},{target_meta['target_scale']}); "
                "nunca automatico."
            )

    # -----------------------------------------------------------------
    # DIRECT: reglas exactas evidenciadas (ver docs/mapping_planner_v1.md).
    # -----------------------------------------------------------------
    if source_type == "wstr" and target_type == "wstr" and target_meta["target_length"] >= source_meta["length"]:
        return DIRECT, "wstr->wstr, target_length >= source_length."

    if source_type == "str" and target_type == "str" and target_meta["target_length"] >= source_meta["length"]:
        return DIRECT, (
            "str->str, target_length >= source_length "
            "(evidenciado: Sector->CanalIncorporacion, PagosYRecaudaciones real)."
        )

    if source_type == DATE_TYPE and target_type == "wstr":
        return DIRECT, (
            "dbDate->wstr, evidenciado en 2 proyectos reales (Campanias, PagosYRecaudaciones) -- "
            "GO CON RESTRICCIONES, sin gate SSDT especifico del planner todavia "
            "(ver docs/mapping_planner_v1.md)."
        )

    if source_type in INTEGER_TYPES and source_type == target_type:
        return DIRECT, f"{source_type}->{target_type}, mismo tipo entero exacto."

    if (
        source_type == NUMERIC_TYPE
        and target_type == NUMERIC_TYPE
        and source_meta["precision"] == target_meta["target_precision"]
        and source_meta["scale"] == target_meta["target_scale"]
    ):
        return DIRECT, "numeric->numeric, precision y scale identicos."

    # -----------------------------------------------------------------
    # CONVERSION_REQUIRED: unico caso evidenciado en v1.
    # -----------------------------------------------------------------
    if source_type == "str" and target_type == "wstr" and target_meta["target_length"] == source_meta["length"]:
        return CONVERSION_REQUIRED, "str->wstr, mismo length exacto (evidenciado 6x, PagosYRecaudaciones)."

    # -----------------------------------------------------------------
    # AMBIGUOUS: unico caso evidenciado en v1.
    # -----------------------------------------------------------------
    if source_type == "str" and target_type == DATE_TYPE:
        return AMBIGUOUS, (
            "str->dbDate: evidencia real (Campanias, 2 casos) pero riesgo de logica de negocio "
            "oculta (ver docs/template_teradata_to_sql_v1_audit.md) -- no se genera automaticamente."
        )

    # -----------------------------------------------------------------
    # Fallback: sin evidencia ni regla de seguridad aplicable.
    # -----------------------------------------------------------------
    return UNSUPPORTED, f"{source_type}->{target_type}: sin evidencia real ni regla aplicable en v1."


def build_transformation_plan(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Valida el Logical Mapping (fail-fast) y devuelve el Transformation Plan
    completo: una entrada por mapping, en el mismo orden declarado.

    Lanza OutputNameCollisionError si el output determinista de una
    conversion ('<source>__conv') colisiona con un nombre de source_schema
    o con otro output de conversion ya generado en este mismo plan.
    """
    assert_valid_logical_mapping(spec)

    source_by_name = {col["name"]: col for col in spec["source_schema"]}
    source_names = set(source_by_name)
    conversion_outputs_used: set = set()

    plan: List[Dict[str, Any]] = []
    for mapping in spec["mappings"]:
        source_name = mapping["source"]
        target_name = mapping["target"]
        source_col = source_by_name[source_name]

        source_meta = _source_metadata(source_col)
        target_meta = _target_metadata(mapping)

        classification, reason = classify_mapping(source_meta, target_meta)

        entry: Dict[str, Any] = {
            "source": source_name,
            "target": target_name,
            "classification": classification,
            "reason": reason,
            "source_metadata": source_meta,
            "target_metadata": target_meta,
            "conversion": None,
        }

        if classification == CONVERSION_REQUIRED:
            output_name = f"{source_name}{CONVERSION_OUTPUT_SUFFIX}"
            if output_name in source_names or output_name in conversion_outputs_used:
                raise OutputNameCollisionError(
                    f"El output de conversion generado '{output_name}' (para el mapping "
                    f"'{source_name}' -> '{target_name}') colisiona con un nombre ya "
                    "existente en source_schema o con otro output de conversion ya "
                    "generado en este plan. No se agrega un sufijo numerico automatico "
                    "-- renombrar la columna de origen o resolver manualmente."
                )
            conversion_outputs_used.add(output_name)
            entry["conversion"] = {
                "input": source_name,
                "output": output_name,
                "target_type": CONVERSION_TARGET_TYPE,
                "target_length": source_meta["length"],
            }

        plan.append(entry)

    return plan
