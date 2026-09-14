# `mapping-planner-v1`

**STATUS: CLOSED — STABLE / SSDT VALIDATED.**

El Gate SSDT manual fue completado y confirmado por el usuario (ver "Gate
SSDT — resultado final" más abajo). `control-flow-v1` sigue **BLOCKED /
EXPERIMENTAL** y no fue tocado. `generator/campanias_generator.py` y
`generator/spec_validator.py` **no se modificaron en absoluto** en este
milestone (confirmado con `git diff` — el diff existente en ambos archivos
es 100% de `control-flow-v1`, pendiente de otro commit, sin ninguna línea
nueva de este milestone). `project_generator/`, `project_context/` y
`profiles/` tampoco se tocaron.

## Gate SSDT — resultado final (✅ PASS)

Confirmado manualmente por el usuario sobre `MappingPlannerV1_E2E.dtsx`,
agregado al proyecto BipSuc:

| Verificación | Resultado | Medio |
|---|---|---|
| Package abre en SSDT sin error (`LoadFromXML`) | PASS | Visual |
| Data Flow completo: Origen Teradata → Conversión de datos → Destino SQL | PASS | Visual |
| Data Conversion reconocida, con **una sola** conversión (`Id_Evento`→`Id_Evento__conv`, `DT_WSTR`, `Length=20`) | PASS | Visual |
| `Mto_Evento`/`Cod_Campania` disponibles pero **no** seleccionadas para conversión (confirma que el planner no crea conversiones innecesarias para `DIRECT`) | PASS | Visual |
| OLE DB Destination reconocido: `connection=cnxSrvBsLogSBD01`, `destination=[staging].[MappingPlannerV1E2E]` | PASS | Visual |
| Mappings destino (`Id_Evento__conv→Id_Evento`, `Mto_Evento→Mto_Evento`, `Cod_Campania→CodigoCampania`) | **No verificado visualmente** — ver nota abajo | Programático únicamente |
| Cierre completo de Visual Studio y reapertura de la solución | PASS | Visual |
| Reapertura de `MappingPlannerV1_E2E.dtsx`, estructura conservada | PASS | Visual |

**Nota explícita sobre el punto no verificado visualmente**: la pestaña de
mappings/asignaciones del OLE DB Destination no pudo inspeccionarse en
SSDT porque `[staging].[MappingPlannerV1E2E]` es **deliberadamente** una
tabla ficticia — SSDT devuelve `Invalid object name 'staging.MappingPlannerV1E2E'`
al intentar resolver el destino real. **Esto no se considera un fallo del
gate**: la generación de esos mappings ya está cubierta programáticamente
(tests del planner, del translator, del `ProcessSpec` generado, de
`ssis_parser`/`ssis_validator` — ver §10 más abajo). Se documenta
explícitamente para no afirmar una verificación visual que no ocurrió.

## Objetivo

```
Source metadata + Logical mappings
        |
Mapping Planner (mapping_planner/, nuevo)
        |
Transformation Plan
        |
fragmento de ProcessSpec (transformations + mappings)
        |
ProcessSpec completo (ensamblado por el caller)
        |
generator.spec_validator.assert_valid_spec()  -- SIN CAMBIOS
        |
generator.campanias_generator.generate()      -- SIN CAMBIOS
```

El generador no sabe que `mapping_planner/` existe: el fragmento que
produce `translator.py` se mezcla en un `ProcessSpec` ya armado por el
caller (package/source/destination/conexiones) y ese `ProcessSpec` completo
pasa, sin ninguna modificación de código, por el `spec_validator`/generador
existentes.

## 1. Arquitectura implementada

```
mapping_planner/
  __init__.py
  schema.py       -- constantes: tipos soportados, categorias, naming
  validator.py    -- validate_logical_mapping()/assert_valid_logical_mapping()
  planner.py      -- classify_mapping()/build_transformation_plan()
  translator.py   -- translate_plan_to_process_spec_fragment()
```

Python puro, sin ninguna dependencia de `xml.etree` ni de ningún otro
módulo del repo — `mapping_planner/` es independiente en tiempo de
ejecución de `generator/`, `project_context/`, `project_generator/` y
`profiles/`, mismo criterio de aislamiento ya aplicado entre esos cuatro
paquetes.

## 2. Archivos creados/modificados

**Nuevos únicamente**: `mapping_planner/{__init__,schema,validator,planner,translator}.py`,
`tests/test_mapping_planner.py`, `specs/mapping_planner_v1_logical_mapping_e2e.json`,
`MappingPlannerV1_E2E.dtsx`, este documento. **Cero archivos modificados**
fuera de este conjunto — ni `generator/`, ni `project_generator/`, ni
`project_context/`, ni `README.md` (ver §16, no se justificó actualizarlo
todavía).

## 3. Schema final del input (Logical Mapping)

```json
{
  "source_schema": [
    {"name": "Id_Evento", "data_type": "str", "length": 20, "code_page": 1252},
    {"name": "Mto_Evento", "data_type": "numeric", "precision": 15, "scale": 2},
    {"name": "Cod_Campania", "data_type": "i4"}
  ],
  "mappings": [
    {"source": "Id_Evento", "target": "Id_Evento", "target_data_type": "wstr", "target_length": 20},
    {"source": "Mto_Evento", "target": "Mto_Evento", "target_data_type": "numeric", "target_precision": 15, "target_scale": 2},
    {"source": "Cod_Campania", "target": "CodigoCampania", "target_data_type": "i4"}
  ]
}
```

`source_schema[]`: `name` (único, requerido), `data_type` (requerido),
`length`/`code_page` (requeridos si `data_type` es `str`/`wstr`, `code_page`
solo para `str`), `precision`/`scale` (requeridos si `data_type="numeric"`).

`mappings[]`: `source` (debe existir en `source_schema`), `target` (único
entre todos los mappings), `target_data_type` (requerido), y la misma
metadata condicional de arriba con prefijo `target_`.

**Limitación de diseño explícita, no un olvido**: en v1 **no existe
"destination schema" como entidad de primera clase** — el `Logical Mapping`
debe declarar siempre toda la metadata destino necesaria
(`target_data_type`/`target_length`/etc.) en cada mapping. El planner
**nunca infiere metadata faltante**, solo **decide** (clasifica) a partir
de lo ya declarado en ambos lados. Esto significa que `mapping-planner-v1`
reduce la necesidad de declarar manualmente `transformations[]`/parte de
`destination.mappings[]` (el usuario ya no arma la Data Conversion a mano),
pero **no** reduce la necesidad de conocer y declarar el tipo/longitud
destino de cada columna — esa reducción adicional queda para una capacidad
futura de "destination schema discovery" (explícitamente fuera de scope,
ver §20 de la implementación).

## 4. Categorías implementadas

Exactamente las 5 pedidas, sin variación:

| Categoría | Semántica |
|---|---|
| `DIRECT` | Se mapea sin Data Conversion. |
| `CONVERSION_REQUIRED` | Evidencia suficiente para generar automáticamente una Data Conversion. |
| `AMBIGUOUS` | Evidencia real, pero la decisión puede esconder semántica de negocio — nunca automático. |
| `UNSAFE` | Riesgo conocido de pérdida/truncación — bloqueo duro, **prioridad sobre cualquier otra regla**. |
| `UNSUPPORTED` | Sin evidencia suficiente para ese par — bloqueo explícito. |

Tipos soportados (v1, congelado): `str`, `wstr`, `dbDate`, `numeric`, `i2`,
`i4`, `i8`. Cualquier otro tipo → `UNSUPPORTED` de inmediato (`schema.SUPPORTED_TYPES`).

## 5. Compatibility matrix final (implementada en `planner.classify_mapping`)

| Origen → Destino | Clasificación | Regla |
|---|---|---|
| `wstr(n)→wstr(m)`, m≥n | `DIRECT` | — |
| `str(n)→str(m)`, m≥n | `DIRECT` | evidenciado: `Sector`→`CanalIncorporacion` |
| `dbDate→wstr` | `DIRECT` | evidenciado en 2 proyectos reales — **GO CON RESTRICCIONES**, ver §18 |
| `i2→i2`, `i4→i4`, `i8→i8` | `DIRECT` | mismo tipo entero exacto, sin cruces |
| `numeric(p,s)→numeric(p,s)` exacto | `DIRECT` | — |
| `str(n)→wstr(n)` | `CONVERSION_REQUIRED` | evidenciado 6× PagosYRecaudaciones |
| `str→dbDate` | `AMBIGUOUS` | evidenciado 2× Campanias, riesgo de lógica de negocio oculta |
| `wstr(n)→wstr(m)`, m<n | `UNSAFE` | — |
| `str(n)→str(m)`, m<n | `UNSAFE` | — |
| `str(n)→wstr(m)`, m<n | `UNSAFE` | reducción gana sobre el par conocido |
| `str(n)→wstr(m)`, m>n | `UNSUPPORTED` | sin evidencia de ampliación |
| `numeric` reduciendo `precision` o `scale` | `UNSAFE` | — |
| `numeric` ampliando `precision`/`scale` | `UNSUPPORTED` | sin evidencia |
| cualquier cruce de familia sin evidencia (`numeric↔string`, `dbDate→str`, etc.) | `UNSUPPORTED` | — |
| tipos cruzados enteros (`i2→i4`, etc.) | `UNSUPPORTED` | "no inferir cross-integer todavía" |
| tipo fuera de `SUPPORTED_TYPES` | `UNSUPPORTED` | — |

## 6. Safety rules (implementadas, con prioridad absoluta)

`classify_mapping()` evalúa la pérdida de capacidad **antes** que cualquier
regla `DIRECT`/`CONVERSION_REQUIRED` — confirmado con test dedicado
(`test_str_to_wstr_length_reduction_is_unsafe_not_conversion_required`):
`str(20)→wstr(10)` es `UNSAFE`, nunca `CONVERSION_REQUIRED`, aunque
`str→wstr` sea un par con evidencia real. Nunca reduce longitud/precisión/
escala automáticamente; nunca convierte string→date solo porque el destino
sea `dbDate`; nunca infiere desde nombres de columna (el `Logical Mapping`
siempre declara `source`/`target` explícitos); nunca usa datos reales para
adivinar schema (el planner nunca toca datos, solo metadata declarada).

## 7. Naming de outputs

`<source>__conv` (doble guion bajo — no colisiona con el patrón humano real
`_Sal` observado en el corpus). Antes de asignarlo, `build_transformation_plan()`
verifica colisión contra `source_schema` y contra otros outputs de
conversión ya generados en el mismo plan; si colisiona, lanza
`OutputNameCollisionError` explícito — **nunca** agrega un sufijo numérico
automático.

## 8. Traducción a ProcessSpec

`translate_plan_to_process_spec_fragment(plan)` devuelve
`{"transformations": [...], "mappings": [...]}`:

- **Todas** las entradas `CONVERSION_REQUIRED` del plan se agrupan en **una
  única** `Data Conversion` (`type: "data_conversion"`), respetando la
  limitación actual del generador (no soporta más de una) — sin excepción,
  sin intentar sortearla.
- `DIRECT` → un elemento directo en `mappings[]`.
- `CONVERSION_REQUIRED` → un elemento en `conversions[]` de la única Data
  Conversion + un elemento en `mappings[]` cuyo `source` es `<source>__conv`.
- Si el plan contiene **cualquier** entrada `AMBIGUOUS`/`UNSAFE`/`UNSUPPORTED`,
  `translate_plan_to_process_spec_fragment()` lanza `PlanNotResolvedError`
  **antes** de producir cualquier fragmento — listando cada entrada
  bloqueante con su clasificación y motivo. Nunca se genera un `ProcessSpec`
  parcial ignorando las entradas problemáticas.

El fragmento resultante se combina (fuera de `mapping_planner/`) con el
resto del `ProcessSpec` (`package.name`, `data_flow.source.{sql,columns,connection}`,
`data_flow.destination.{table,connection}`) y ese `ProcessSpec` completo
pasa **sin ningún cambio** por `generator.spec_validator.assert_valid_spec()`
y `generator.campanias_generator.generate()`.

## 9. Tests agregados

`tests/test_mapping_planner.py` — 47 tests:
- **Casos reales obligatorios** (5): los 6 `str→wstr` de PagosYRecaudaciones,
  `str→dbDate` de Campanias, `str` ampliando (`Sector`→`CanalIncorporacion`),
  `dbDate→wstr` (`Fec_Evento`), `numeric` exacto (2 pares reales).
- **Safety** (6): reducción `wstr`/`str`/`str→wstr`/`numeric` precision/scale,
  y la regresión conceptual del incidente de truncación (`test_plasticos_incident_regression_string_capacity_reduction_blocked`).
- **`UNSUPPORTED`** (7): ampliación numeric/string, cruces de familia,
  cross-integer, tipo desconocido, enteros mismo tipo (`DIRECT`, contraste).
- **Validación del Logical Mapping** (14): spec válido, referencias
  inexistentes, duplicados (target, par, `source_schema`), metadata faltante
  por tipo, tipo desconocido (no falla validación — ver §3/diseño),
  valores inválidos, listas vacías, `assert_*`.
- **Colisión de naming** (1).
- **Estructura del Transformation Plan** (3): `conversion=null` en `DIRECT`,
  estructura exacta en `CONVERSION_REQUIRED`, serializable a JSON.
- **Translator** (6): agrupación de 6 conversiones en 1 Data Conversion,
  plan solo-`DIRECT` sin `transformations`, bloqueo por cada categoría no
  resoluble, bloqueo parcial (lista solo las entradas problemáticas).
- **E2E** (5): plan correcto, `spec_validator` sin cambios, `ssis_parser`/
  `ssis_validator` en verde, componentes/mappings correctos.

**371 tests en total, 0 fallos** (324 previos + 47 nuevos). Cero
regresiones — confirmado que `generator/campanias_generator.py` y
`generator/spec_validator.py` no tienen ninguna línea nueva de este
milestone (`git diff` idéntico al estado previo, 100% pendiente de
`control-flow-v1`).

## 10. Resultado E2E

Logical Mapping (`specs/mapping_planner_v1_logical_mapping_e2e.json`): 1
`DIRECT` numeric (`Mto_Evento`), 1 `DIRECT` entero (`Cod_Campania`), 1
`CONVERSION_REQUIRED` str→wstr (`Id_Evento`) — deliberadamente sin
`AMBIGUOUS`/`UNSAFE`/`UNSUPPORTED`, tal como se pidió. Traducido a
`ProcessSpec`, pasado contra el `ProjectContext` real y ya estable de
BipSuc (`cnxTeradata`/`cnxSrvBsLogSBD01`), generado a
`MappingPlannerV1_E2E.dtsx`:

- `generator.spec_validator.validate_spec(...)` → `[]` (sin cambios de
  código, ProcessSpec 100% compatible).
- `ssis_parser.parse_file()` + `ssis_validator.validate_ir()` → `valid=True`,
  0 errores.
- 3 componentes (`teradata_source`, `data_conversion`, `ole_db_destination`),
  2 paths — topología idéntica a Campanias/PagosYRecaudaciones reales.
- `DTS:DesignTimeProperties` presente (lección de `control-flow-v1`
  verificada explícitamente, no asumida).
- Mappings resueltos: `Id_Evento__conv→Id_Evento`, `Mto_Evento→Mto_Evento`,
  `Cod_Campania→CodigoCampania`.

## 11. Artefacto `MappingPlannerV1_E2E.dtsx`

Generado en la raíz del repo, listo para el gate manual (ver §17).

## 12. Regresiones

**Cero.** `generator/campanias_generator.py` y `generator/spec_validator.py`
no fueron tocados — el diff presente en ambos archivos en el working tree
es exactamente el mismo que ya existía antes de este milestone (pendiente
de `control-flow-v1`, sin commitear). Los 324 tests previos siguen verdes,
sin ninguna modificación.

## 13. `dbDate → wstr` — estado explícito

`GO CON RESTRICCIONES`: respaldado por evidencia real en **2 proyectos
independientes** (Campanias: `FechaInicio_SAL`/`FechaFin_SAL` → `wstr(27)`;
PagosYRecaudaciones: `Fec_Evento` → `wstr`), cubierto por test unitario
dedicado (`test_dbdate_to_wstr_direct`), pero **sin gate SSDT específico
del planner todavía** — el artefacto E2E principal (§10-11) deliberadamente
no lo ejercita (se usó `numeric`/`i4`/`str→wstr` para el caso exitoso
principal, según lo pedido). Queda documentado en el `reason` de la propia
clasificación (`"GO CON RESTRICCIONES"` aparece literal en el string) para
que cualquier consumidor del Transformation Plan lo vea sin tener que leer
este documento.

## 14. Limitaciones (no son bugs)

- **No existe "destination schema"** como entidad de primera clase (ver
  §3) — el usuario sigue declarando `target_data_type`/`target_length`/etc.
  a mano en cada mapping; el planner solo evita que declare manualmente
  `transformations[]`/la mitad de `destination.mappings[]` derivada de la
  conversión.
- **`dbDate→wstr` sin gate SSDT propio** (ver §13).
- **Sin inferencia de metadata** — deliberado, no una limitación a
  resolver: v1 decide no inferir `target_length` a partir del `source_length`
  ni nada equivalente, para no repetir el patrón de asumir sin evidencia.
- **Fuera de scope, pertenecen a otros milestones** (sin excepción): Control
  Flow, Project Generation, `ProjectContext`, `PackagePassword`/protección
  de packages, defaults corporativos de `teradata-to-sql-profile-v1`
  (`ambiente`, `MinSessions`/`MaxSessions`), destination schema discovery,
  introspección de bases de datos.

## 15. Qué fue validado visualmente vs. programáticamente

Distinción explícita, para no sobre-afirmar el alcance del gate manual:

**Validado visualmente en SSDT** (ver tabla de §"Gate SSDT — resultado
final"): apertura sin error, topología del Data Flow completa, la Data
Conversion automática generada por el planner (una sola conversión,
`Id_Evento`→`Id_Evento__conv`, `DT_WSTR(20)`), que `Mto_Evento`/`Cod_Campania`
NO fueron llevadas a conversión (confirma que `DIRECT` no genera
conversiones innecesarias), reconocimiento del OLE DB Destination
(`connection`/`destination` correctos), y persistencia tras cerrar/reabrir
Visual Studio.

**Validado únicamente de forma programática** (no visualmente, por la tabla
ficticia del destino): los 3 mappings finales del OLE DB Destination
(`Id_Evento__conv→Id_Evento`, `Mto_Evento→Mto_Evento`, `Cod_Campania→CodigoCampania`)
— cubiertos por `tests/test_mapping_planner.py::TranslatorTests`/`EndToEndTests`
(estructura del fragmento traducido, tipos/longitudes correctos) y por
`ssis_parser`/`ssis_validator` sobre el `.dtsx` real generado (`valid=True`,
0 errores, mappings resueltos correctamente en el IR). No se afirma
verificación visual de este punto específico.

## 16. Documentación

Este documento. `README.md` actualizado (ver roadmap) para reflejar el
cierre de este milestone.

## 17. Gate SSDT — resultado final

Ver tabla completa en "Gate SSDT — resultado final" al inicio de este
documento. Los 8 pasos originalmente propuestos fueron cubiertos por la
verificación manual reportada por el usuario, con la única salvedad
explícita de §15 (mappings destino no inspeccionados visualmente por la
tabla ficticia — cubiertos programáticamente).

## 18. Cierre del milestone

Con el Gate SSDT en PASS y 371/371 tests automáticos en verde,
`mapping-planner-v1` queda formalmente cerrado:

**`mapping-planner-v1 = STABLE / SSDT VALIDATED`**

El circuito `Logical Mapping → Mapping Planner → Transformation Plan →
Translator → ProcessSpec → TERADATA_TO_SQL v1.1 (sin cambios) → .dtsx →
SSDT` fue validado manualmente en SSDT de punta a punta, sin modificar
`generator/campanias_generator.py` ni `generator/spec_validator.py` en
ningún momento del milestone. `control-flow-v1` permanece `BLOCKED /
EXPERIMENTAL`, sin tocar.

Las limitaciones documentadas en §14 (`dbDate→wstr` sin gate SSDT propio,
ausencia de "destination schema" de primera clase, sin inferencia de
metadata) **siguen vigentes** y son el punto de partida de cualquier
milestone futuro que amplíe este scope (destination schema discovery, DB
introspection, resolución automática de `AMBIGUOUS`). Ninguna de esas
ampliaciones fue iniciada en este cierre.
