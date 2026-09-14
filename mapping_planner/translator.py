"""
translator.py

Traduce un Transformation Plan YA RESUELTO (ver planner.py) al fragmento de
ProcessSpec que generator/spec_validator.py y generator/campanias_generator.py
YA saben consumir, SIN NINGUN CAMBIO en esos dos modulos: agrupa todas las
conversiones CONVERSION_REQUIRED en la UNICA Data Conversion que el
generador soporta hoy (misma limitacion de siempre, no se intenta sortear
aca), y produce destination.mappings[] tanto para DIRECT como para
CONVERSION_REQUIRED (desde el output de la conversion en este ultimo caso).

Si el plan contiene CUALQUIER entrada AMBIGUOUS/UNSAFE/UNSUPPORTED, la
traduccion se detiene con PlanNotResolvedError -- nunca se genera un
fragmento parcial ni se ignoran en silencio las entradas problematicas. El
caller (humano) debe resolverlas explicitamente antes de poder generar nada.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .schema import CONVERSION_REQUIRED, DIRECT, RESOLVABLE_CLASSIFICATIONS

DATA_CONVERSION_NAME = "Conversión de datos"

_OPTIONAL_TARGET_METADATA_KEYS = (
    "target_length",
    "target_code_page",
    "target_precision",
    "target_scale",
)


class PlanNotResolvedError(Exception):
    """Se lanza cuando el plan tiene entradas AMBIGUOUS/UNSAFE/UNSUPPORTED --
    el caller debe resolverlas explicitamente (o excluirlas a proposito)
    antes de poder traducir el resto a un ProcessSpec. El mensaje lista cada
    entrada bloqueante con su clasificacion y motivo, nunca las oculta."""

    def __init__(self, blocking_entries: List[Dict[str, Any]]):
        self.blocking_entries = blocking_entries
        lines = [
            f"  - {e['source']} -> {e['target']}: {e['classification']} ({e['reason']})"
            for e in blocking_entries
        ]
        message = (
            "El Transformation Plan tiene entradas sin resolver -- no se genera "
            "ningun ProcessSpec hasta que un humano las resuelva explicitamente:\n"
            + "\n".join(lines)
        )
        super().__init__(message)


def _optional_target_metadata(target_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Solo incluye las claves target_length/target_code_page/target_precision/
    target_scale que efectivamente tengan valor -- evita escribir claves con
    valor None en el ProcessSpec resultante (generator/spec_validator.py
    distingue 'ausente' de 'presente con valor invalido')."""
    return {
        key: target_meta[key]
        for key in _OPTIONAL_TARGET_METADATA_KEYS
        if target_meta.get(key) is not None
    }


def translate_plan_to_process_spec_fragment(plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Devuelve {"transformations": [...], "mappings": [...]} -- EXACTAMENTE
    las dos claves que un ProcessSpec legacy espera en
    'data_flow.transformations'/'data_flow.destination.mappings'. El caller
    combina este fragmento con el resto del ProcessSpec (package/source/
    destination.connection/destination.table), que el planner no conoce ni
    necesita conocer.
    """
    blocking = [e for e in plan if e["classification"] not in RESOLVABLE_CLASSIFICATIONS]
    if blocking:
        raise PlanNotResolvedError(blocking)

    conversions: List[Dict[str, Any]] = []
    mappings: List[Dict[str, Any]] = []

    for entry in plan:
        target_meta = entry["target_metadata"]

        if entry["classification"] == DIRECT:
            mappings.append(
                {
                    "source": entry["source"],
                    "target": entry["target"],
                    "target_data_type": target_meta["target_data_type"],
                    **_optional_target_metadata(target_meta),
                }
            )

        elif entry["classification"] == CONVERSION_REQUIRED:
            conversion = entry["conversion"]
            conversions.append(
                {
                    "input": conversion["input"],
                    "output": conversion["output"],
                    "target_type": conversion["target_type"],
                    "target_length": conversion["target_length"],
                }
            )
            mappings.append(
                {
                    "source": conversion["output"],
                    "target": entry["target"],
                    "target_data_type": target_meta["target_data_type"],
                    **_optional_target_metadata(target_meta),
                }
            )

    transformations: List[Dict[str, Any]] = []
    if conversions:
        transformations.append(
            {"type": "data_conversion", "name": DATA_CONVERSION_NAME, "conversions": conversions}
        )

    return {"transformations": transformations, "mappings": mappings}
