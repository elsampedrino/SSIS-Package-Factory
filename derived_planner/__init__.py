"""
derived_planner

Modulo de derived-column-v1 -- ver docs/derived_column_v1.md.

Deliberadamente un paquete SEPARADO de mapping_planner/ (no un submodulo),
aunque importa su taxonomia de clasificacion (DIRECT/CONVERSION_REQUIRED/
AMBIGUOUS/UNSAFE/UNSUPPORTED) desde mapping_planner.schema -- dependencia
UNIDIRECCIONAL (derived_planner -> mapping_planner), nunca al reves, sin
acoplamiento circular. mapping_planner/ no se modifica en este milestone.

Responsabilidad de derived_planner: recibe metadata de columnas de origen +
reglas de derivacion estructuradas (nunca una expresion SSIS cruda), valida,
clasifica con las reglas de seguridad de derived-column-v1, y traduce el
plan resuelto a un fragmento de ProcessSpec (transformations[type=
'derived_column']). No genera XML -- eso es responsabilidad exclusiva de
generator/campanias_generator.py.
"""

from __future__ import annotations
