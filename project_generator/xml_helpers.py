"""
xml_helpers.py (project_generator)

Namespaces y formulas de bajo nivel para escribir Project.params/.conmgr --
identicos a los que project_context/xml_helpers.py usa para LEER, duplicados
a proposito (mismo criterio ya aplicado entre generator/ y project_context/:
cada paquete queda independiente en tiempo de ejecucion de los demas).

Estrategia de IDs (ver docs/project_generation_v1.md, seccion "IDs"):
    - DTSID de Connection Manager: MAYUSCULAS + llaves. Se reutiliza
      generator.xml_helpers.new_guid() tal cual -- es el mismo formato, no
      hace falta una segunda implementacion.
    - ID de Project Parameter: minusculas + llaves. Confirmado distinto del
      DTSID en los 3 proyectos reales auditados (BipSuc, PagosYRecaudaciones,
      SSDT_Golden) -- nunca se generan ambos con la misma formula.
"""

from __future__ import annotations

import uuid

from generator.xml_helpers import new_guid

DTS_NS = "www.microsoft.com/SqlServer/Dts"
SSIS_NS = "www.microsoft.com/SqlServer/SSIS"


def dts(tag: str) -> str:
    return f"{{{DTS_NS}}}{tag}"


def ssis(tag: str) -> str:
    return f"{{{SSIS_NS}}}{tag}"


def new_connection_dtsid() -> str:
    """DTSID de un Connection Manager de proyecto: '{XXXX...-MAYUSCULAS}'."""
    return new_guid()


def new_parameter_id() -> str:
    """ID de un Project Parameter: '{xxxx...-minusculas}' -- str(uuid.uuid4())
    ya produce hex en minusculas, solo se envuelve en llaves."""
    return "{" + str(uuid.uuid4()) + "}"
