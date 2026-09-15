"""
translator.py

Traduce un plan de derived-column-v1 YA RESUELTO (ver planner.py) al
fragmento de ProcessSpec que generator/spec_validator.py y
generator/campanias_generator.py saben consumir desde esta version
(transformations[type='derived_column']) -- ver docs/derived_column_v1.md.

Si el plan contiene CUALQUIER entrada AMBIGUOUS/UNSAFE/UNSUPPORTED, la
traduccion se detiene con PlanNotResolvedError -- nunca se genera un
fragmento parcial ni se ignoran en silencio las entradas problematicas
(mismo criterio que mapping_planner.translator.PlanNotResolvedError). El
caller (humano) debe resolverlas explicitamente antes de poder generar nada.

Separacion de responsabilidades (deliberada): derived_planner DECIDE
(clasifica, aplica la regla de seguridad), este modulo TRADUCE (arma el
fragmento de ProcessSpec), generator/campanias_generator.py SERIALIZA (XML).
El generador nunca conoce la logica de seguridad -- asume que recibe un
ProcessSpec ya valido.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import RESOLVABLE_CLASSIFICATIONS

DEFAULT_DERIVED_COLUMN_TRANSFORMATION_NAME = "Columna derivada"


class PlanNotResolvedError(Exception):
    """Se lanza cuando el plan tiene entradas AMBIGUOUS/UNSAFE/UNSUPPORTED --
    el caller debe resolverlas explicitamente (o excluirlas a proposito)
    antes de poder traducir el resto a un ProcessSpec. El mensaje lista cada
    entrada bloqueante con su clasificacion y motivo, nunca las oculta."""

    def __init__(self, blocking_entries: List[Dict[str, Any]]):
        self.blocking_entries = blocking_entries
        lines = [
            f"  - {e['source']} -> {e['output']}: {e['classification']} ({e['reason']})"
            for e in blocking_entries
        ]
        message = (
            "El plan de columnas derivadas tiene entradas sin resolver -- no se "
            "genera ningun ProcessSpec hasta que un humano las resuelva "
            "explicitamente:\n" + "\n".join(lines)
        )
        super().__init__(message)


def translate_plan_to_process_spec_fragment(
    plan: List[Dict[str, Any]],
    *,
    name: str = DEFAULT_DERIVED_COLUMN_TRANSFORMATION_NAME,
) -> Optional[Dict[str, Any]]:
    """
    Devuelve un unico objeto de transformations[] con type='derived_column',
    o None si el plan esta vacio (sin derivaciones, nada que agregar). El
    caller combina este fragmento con el resto de 'transformations' del
    ProcessSpec (ej. 'data_conversion', si existe) -- ver
    docs/derived_column_v1.md, seccion "Orden topologico": cuando ambos
    coexisten, 'data_conversion' debe declararse ANTES que este fragmento.
    """
    if not plan:
        return None

    blocking = [e for e in plan if e["classification"] not in RESOLVABLE_CLASSIFICATIONS]
    if blocking:
        raise PlanNotResolvedError(blocking)

    columns: List[Dict[str, Any]] = [
        {
            "input": entry["source"],
            "output": entry["output"],
            "operation": entry["operation"],
            "target_type": entry["target_type"],
            "target_length": entry["target_length"],
        }
        for entry in plan
    ]

    return {"type": "derived_column", "name": name, "columns": columns}
