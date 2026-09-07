# Auditoría arquitectónica — Generator MVP → `template-teradata-to-sql-v1`

Auditoría de solo-lectura sobre el Generator MVP de Campanias, hecha para
preparar el milestone `template-teradata-to-sql-v1`. No se modificó ningún
archivo de código como parte de esta iteración — es puro análisis, previo a
implementar nada.

Milestones cerrados al momento de esta auditoría:

```text
generator-mvp-campanias-e2e ✅
project-context-v1          ✅
```

Roadmap vigente:

```text
1. template-teradata-to-sql-v1        ← esta auditoría prepara este paso
2. validación con un segundo caso real Teradata → SQL Server
3. template-teradata-to-flat-file-v1

Turnero queda pospuesto (no influyó ninguna decisión de esta auditoría).
```

Archivos auditados: `generator/campanias_generator.py`,
`generator/spec_validator.py`, `generator/xml_helpers.py`,
`specs/campanias_generated.json`, `templates/campanias_base.dtsx`,
`tests/test_generator_campanias.py`, `docs/generator_mvp.md`. Todas las
referencias de línea corresponden al estado del código en el momento de
esta auditoría (post `project-context-v1`).

---

## 1. Resumen ejecutivo

El generador ya es **mayormente genérico**: conexiones (vía `ProjectContext`),
lineage IDs, refIds, el loop de columnas de origen, y el loop de mappings de
destino no tienen absolutamente nada de Campanias hardcodeado — funcionan
para cualquier lista de columnas/mappings que declare el spec. El problema
real, y es exactamente el que sospechaban, es uno solo pero central: **Data
Conversion es obligatorio por construcción**, no opcional. Esto se refleja
en tres lugares (`spec_validator.py:208`, `campanias_generator.py:283`,
`_rebuild_paths`) y es 100% un artefacto de que Campanias necesita 2
conversiones de fecha, no una necesidad genérica de `TERADATA_TO_SQL`.
Además hay un gap real y no relacionado con esto: **no existe ningún campo
de precision/scale** en todo el spec — si el segundo caso real usa
`DECIMAL`/`NUMERIC` (muy probable en Teradata), el spec actual no puede
representarlo.

**Conclusión: GO CON RESTRICCIONES** (detalle en §12).

---

## 2. Matriz genérico vs Campanias-specific

| Elemento | Genérico `TERADATA_TO_SQL` | Específico Campanias | Parametrizable | Inferible | Riesgo |
|---|---|---|---|---|---|
| Package metadata (ObjectName/DTSID/VersionGUID) | ✅ mecanismo genérico | — | Ya parametrizado (`spec.package.name`) + GUIDs frescos (`new_guid()`) | n/a | BAJO |
| Package metadata estática (LocaleID, PackageType, ProtectionLevel, VersionBuild, PackageFormatVersion) | ✅ boilerplate SSIS genérico | — | No hace falta variarlo | n/a | BAJO |
| Control Flow (1 solo Data Flow Task, sin containers) | ✅ es exactamente la forma deseada de v1 | — | n/a | n/a | BAJO |
| Data Flow (nombre, pipeline) | ✅ | — | Ya parametrizado (`data_flow.name`) | n/a | BAJO |
| Teradata Source: detección/nombre | ✅ (`componentClassID` fijo, correcto) | — | Ya parametrizado | n/a | BAJO |
| Teradata Source: tuning (BlockSize, MaxSessions, MinSessions, TenacityHours, etc.) | Parcial — valores de driver, no de negocio | Los VALORES concretos son los que trae `campanias_base.dtsx` | Sí, trivial (agregar opcionales al spec con default = valor actual) | No hay forma de inferir un valor "óptimo" | BAJO — funciona igual aunque no sea óptimo |
| Source query (`SqlCommand`) | ✅ mecanismo (texto libre) | El SQL en sí es 100% de Campanias | Ya parametrizado (`source.sql`) | No | BAJO |
| Source metadata (`columns[]`) | ✅ mecanismo (lista tipada) | Los VALORES son de Campanias | Ya parametrizado | No (no hay introspección contra Teradata) | MEDIO — hay que declarar tipo/length/codepage a mano, sin ayuda |
| Output columns del Teradata Source | ✅ 100% generado desde el loop `for col in spec["columns"]` (`campanias_generator.py:389`) | — | Ya parametrizado | n/a | BAJO |
| **Data Conversion (presencia/ausencia)** | ❌ **hoy obligatorio, debería ser opcional** | ✅ Campanias lo necesita, pero su obligatoriedad es un artefacto de haber generalizado desde UN solo ejemplo | La lista de conversiones ya es parametrizable; la PRESENCIA no | Parcialmente — ver §4 | **ALTO** |
| Conversion columns (loop) | ✅ ya soporta N columnas en un mismo componente (probado con N=2) | Los pares concretos son de Campanias | Ya parametrizado | Ver §4 | BAJO el mecanismo / ALTO la semántica de tipos |
| OLE DB Destination: detección/nombre/tuning | ✅ / Parcial (mismo caso que Teradata tuning) | Valores concretos | Sí, trivial | No | BAJO |
| Destination table (`OpenRowset`) | ✅ | El nombre de tabla es de Campanias | Ya parametrizado | No | BAJO |
| Destination metadata / external metadata | ✅ 100% generado desde el loop de `mappings` | Valores de Campanias | Ya parametrizado | No | MEDIO (mismo problema de autoría manual de tipos) |
| Mappings | Ver §3 en detalle | Valores de Campanias | Ya parametrizado | No | MEDIO/ALTO según evolución |
| Lineage IDs | ✅ 100% fórmula determinista compartida con `ssis_parser` | — | n/a (no debe parametrizarse, es derivado) | n/a | BAJO |
| Component/input/output refIds | ✅ 100% fórmula determinista | — | n/a | n/a | BAJO |
| Connection Manager references | ✅ **ya generalizado** (`ProjectContext`, `project-context-v1`) | — | Ya parametrizado por nombre | n/a | BAJO |
| **Paths entre componentes** | ❌ **hoy hardcodeados a exactamente 2, forma fija source→conversion→destination** (`_rebuild_paths`) | ✅ topología exacta de Campanias | No hay ninguna rama condicional hoy | Directamente ligado a §4 | **ALTO** |
| Propiedades XML heredadas del template sin tocar (AccessMode, FastLoadOptions, contactInfo, `version` de componente, etc.) | Genéricas EN PRINCIPIO, pero solo probadas con este template | Vienen literalmente de `BipSuc_CampaniasVigentes.dtsx` | Parcial | No | MEDIO — nunca se probó con otra versión/perfil de tuning |
| `DTS:DesignTimeProperties` (layout) | ✅ mecanismo de reescritura de refs | Asume implícitamente 3 nodos + 2 edges fijos | n/a | n/a | MEDIO si cambia la topología (ver §9) |

---

## 3. Hardcoding restante — clasificación A-E

**A. Trivial parametrizable** (cosmético, bajo riesgo):
- `TEMPLATE_SOURCE_NAME`, `CONVERSION_INPUT_NAME`, `SOURCE_CONNECTION_LOCAL_NAME`, etc. (`campanias_generator.py:60-77`) — sub-etiquetas de puerto en español, heredadas 1:1 del template. No rompen nada funcionalmente (SSIS no le importa el texto), pero no son configurables sin tocar código.
- `errorRowDisposition="FailComponent"` hardcodeado en la Data Conversion generada (`campanias_generator.py:491`) — nunca se ofrece "redirect row" ni "ignore failure" como opción del spec.

**B. Metadata específica pero ya declarable** (no requiere cambio de código, solo nuevos valores en el spec):
- `source.sql`, `source.columns`, `destination.table`, `destination.mappings`, todos los `name` — **hoy YA funcionan para un segundo caso Teradata→SQL sin tocar código**, siempre que ese caso necesite exactamente 1 Data Conversion.
- Tuning de Teradata Source/OLE DB Destination — declarable en principio, no declarable hoy (fijo en el template).

**C. Estructura específica de Campanias** (el corazón de esta auditoría):
- `data_flow["transformations"][0]` sin chequeo de existencia (`campanias_generator.py:283`) + `spec_validator.py:208` exige EXACTAMENTE 1 elemento. Esto es la hipótesis del usuario, confirmada: es 100% un artefacto de "Campanias necesita 2 conversiones de fecha", no una regla genérica.
- `_rebuild_paths` (`campanias_generator.py:603-625`): siempre construye 2 paths, siempre `source→conversion→destination`. Cero lógica condicional.
- `templates/campanias_base.dtsx` tiene siempre y exactamente 3 componentes — cualquier topología distinta necesita cirugía sobre el árbol (no existe hoy).

**D. Dependencia del template XML**:
- Todo lo que el generador NUNCA toca (AccessMode, FastLoadOptions, `version` de cada componente, `PackageFormatVersion`, `contactInfo`, `TaskContact`) se hereda tal cual de `campanias_base.dtsx`. Funciona porque ese ÚNICO archivo fue validado en SSIS real — nunca se probó con otra versión de componente o perfil de tuning distinto.
- `_update_design_time_properties` (`campanias_generator.py:628-652`) construye su `ref_id_map` asumiendo exactamente 3 componentes + 2 paths — si Data Conversion desaparece del árbol, esta función necesita ajustarse (dejaría de mapear un refId que ya no existe; no crashea, pero el layout quedaría inconsistente para ese caso).

**E. Difícil de generalizar sin más evidencia**:
- Ninguna dentro del alcance v1 tentativo. Lo más cercano es la inferencia automática de necesidad de conversión (§4), que NO es "difícil" técnicamente sino que **no tenemos evidencia suficiente** para hacerla con confianza (ver más abajo).

---

## 4. Auditoría de mappings — recorrido completo con una columna real

Sigo `FEC_INICIO_TXT → FechaInicio_SAL → FechaInicio` de punta a punta:

```
1. source metadata (spec.data_flow.source.columns):
   {"name": "FEC_INICIO_TXT", "data_type": "str", "length": 10, "code_page": 1252}

2. source output (Teradata Source, campanias_generator.py:389-425):
   outputColumn: dataType="str" length="10" codePage="1252"
   lineageId = su propio refId
   pipeline_columns["FEC_INICIO_TXT"] = {lineage_id, data_type:"str", length:10, code_page:1252}

3. Data Conversion (spec.transformations[0].conversions):
   {"input": "FEC_INICIO_TXT", "output": "FechaInicio_SAL", "target_type": "dbDate"}
   → inputColumn: cachedDataType/Length/Codepage COPIADOS de pipeline_columns["FEC_INICIO_TXT"]
     (el usuario NO redeclara el tipo de entrada — se hereda automáticamente)
   → outputColumn "FechaInicio_SAL": dataType="dbDate", lineageId nuevo (propio),
     property SourceInputColumnLineageID = #{lineage_id del paso 2}
   pipeline_columns["FechaInicio_SAL"] = {lineage_id (nuevo), data_type:"dbDate",
                                           length: None, code_page: None}
                                           ⚠️ SIEMPRE None para cualquier conversión,
                                           sin importar target_type — ver limitación abajo

4. destination mapping (spec.destination.mappings):
   {"source": "FechaInicio_SAL", "target": "FechaInicio",
    "target_data_type": "wstr", "target_length": 27}
   → inputColumn del destino: cachedDataType="dbDate" (de pipeline_columns, paso 3),
     lineageId = el de Data Conversion
   → externalMetadataColumn: name="FechaInicio", dataType="wstr", length="27"
     (SOLO de mapping.target_*, TOTALMENTE independiente del tipo "dbDate" del pipeline)

5. external metadata: <externalMetadataColumn name="FechaInicio" dataType="wstr" length="27" />
```

**Hallazgos clave:**

- **Identidad por nombre, no por objeto**: `conversions[].input`, `columns[].name` y `mappings[].source` comparten un único namespace plano (`pipeline_columns`, keyed por string). No hay ningún ID estructural que los relacione — si dos entradas del spec usan el mismo nombre por error, no hay forma de detectarlo salvo el chequeo de duplicados que ya existe para `source.columns`.
- **El tipo "pipeline" se propaga automáticamente** (cachedDataType/Length/Codepage viajan solos vía `pipeline_columns`); **el tipo "físico destino" es una declaración manual totalmente separada** (`target_data_type`/`target_length`/`target_code_page`) sin ninguna relación automática con el tipo de origen — el usuario debe saber los DOS tipos y declarar el segundo a mano.
- **`length`/`code_page` de una columna de Data Conversion son SIEMPRE `None`** (`campanias_generator.py:520-521`), sin importar si `target_type` fuera `str`/`wstr` — hoy inofensivo porque el único `target_type` real es `dbDate` (no necesita length), pero es un gap real si `TERADATA_TO_SQL v1` alguna vez necesita una conversión hacia texto.
- **Precision/scale: no existe ningún campo, en ningún nivel del spec.** Ni `source.columns`, ni `conversions`, ni `mappings` tienen `precision`/`scale`. `DECIMAL`/`NUMERIC` no se puede representar hoy.

---

## 5. Data Conversion: fijo vs inferible

Las 2 conversiones de Campanias son idénticas en forma: `str(10, codepage 1252) → dbDate`.

**Hallazgo importante, no obvio**: el destino físico de esas columnas (`FechaInicio`/`FechaFin`) es **`wstr(27)`**, es decir, **texto**, no una columna de fecha nativa. Esto significa que, mirando solo compatibilidad de tipos, `str(10) → wstr(27)` sería una ampliación de codificación perfectamente rutinaria que en general SSIS no exige un componente explícito para resolver — **no hay necesidad de tipos que explique por qué existe la Data Conversion acá**. La razón real es casi seguro una de estas dos (no confirmable con la evidencia disponible):
1. Un artefacto heredado de la migración original desde DataStage (donde esas columnas sí eran tipo Date), preservado mecánicamente aunque el destino final sea texto.
2. **Normalización de formato**: el round-trip `str→dbDate→(coerción implícita a wstr en destino)` puede producir un formato de fecha distinto al `'YYYY-MM-DD'` que entrega el `CAST` de Teradata — si es así, la conversión es en realidad una **transformación de negocio disfrazada de adaptación técnica**. No tengo forma de confirmar esto sin ver el dato real resultante.

**Respuesta a la pregunta directa**: *¿la necesidad de Data Conversion puede deducirse solo comparando metadata source/destination?*

**No, con la evidencia actual no.** Dos razones concretas:
- Si la razón real es la (2) de arriba, ninguna regla de compatibilidad de TIPOS la va a descubrir nunca — hace falta saber algo sobre formato de fecha esperado, que hoy no existe en ningún lado del spec.
- Ni siquiera tenemos evidencia para la regla MÁS básica de compatibilidad (`str→wstr` sin conversión): **los dos únicos `str` de todo el dataset pasan por conversión**, así que no hay ningún ejemplo real de un `str` mapeado DIRECTO para confirmar que SSIS lo permite sin componente intermedio. Afirmar esa regla sería importar conocimiento general de SSIS no validado por este repo — exactamente lo que la disciplina del proyecto pide evitar.

**Conclusión**: para v1, Data Conversion debe seguir siendo una **declaración explícita del spec**, nunca inferida — coincide con lo que ya tenías en "fuera de alcance tentativo".

---

## 6. Mapping Planner — evaluación

**Encaja bien con la arquitectura actual.** Hoy `spec_validator` (valida) y `campanias_generator` (genera) están separados, pero la "decisión de forma" (¿hay Data Conversion o no? ¿qué mapea a qué?) está mezclada DENTRO del generador — se decide y se ejecuta en el mismo paso (`build_package_tree` lee `spec["transformations"]`/`spec["mappings"]` y emite XML en la misma pasada). Un Mapping Planner introduciría una responsabilidad que hoy NO existe: **clasificar cada mapping ANTES de tocar XML**, produciendo un "Transformation Plan" que el generador simplemente ejecuta. Esto es consistente con el patrón que el proyecto ya usa dos veces (parser/validator separados en `ssis_parser`+`ssis_validator`, y en `spec_validator`+`campanias_generator`).

Beneficio concreto: la decisión "¿existe Data Conversion en el XML final?" pasaría a ser una propiedad DERIVADA del plan (`len(plan.conversion_required) > 0`), no una suposición de forma del spec — resuelve limpiamente el problema central de §1-3.

**Punto abierto que NO resolvería solo**: `UNSAFE` implica una política que hoy no existe — ¿rechazar el spec, generar con warning, o exigir un flag explícito de "acepto el riesgo"? Es una decisión de producto, no técnica, que dejo señalada para la próxima iteración, no la resuelvo acá.

---

## 7. Tres conceptos — ¿se mezclan hoy?

- **Mapping**: limpio, no mezclado con nada (`_build_ole_db_destination`'s mapping loop solo rutea + declara tipo físico).
- **Type adaptation**: **sí está mezclado** — hoy "adaptación de tipo" y "componente Data Conversion" son literalmente lo mismo en el código (`transformations[].type` debe ser EXACTAMENTE `"data_conversion"`, `spec_validator.py:223`). No hay forma de expresar "no hace falta adaptar nada" como caso de primera clase — el spec fuerza a declarar 1 transformación siempre. Esto conflaciona el CONCEPTO con el MECANISMO concreto que lo implementa en este único ejemplo.
- **Business transformation**: correctamente ausente del todo — ninguna lógica de negocio en ningún lado del generador actual. Buena noticia: no hay que "desenredar" nada acá, solo no agregar nada acá todavía (coincide con el objetivo original).

---

## 8. Alcance recomendado para `TERADATA_TO_SQL v1`

El alcance tentativo original es correcto y ya validado contra el código en casi todo:

| Ítem del alcance tentativo | Estado real |
|---|---|
| mappings directos | Necesita el trabajo de §3 (Data Conversion opcional) |
| source/destino con distinto nombre | ✅ ya funciona hoy, sin cambios |
| adaptación segura de tipos | Solo probado para 1 par (str→dbDate); "segura" en general no está evidenciada |
| Data Conversion opcional | ❌ no implementado — es el cambio central |
| varias columnas convertidas en un componente | ✅ ya funciona hoy (N=2 probado) |
| metadata explícita en el spec | ✅ ya es así |

**Corrección que se agrega**: falta **soporte declarativo de precision/scale** para `DECIMAL`/`NUMERIC`. No estaba en la lista tentativa ni en la "fuera de alcance", simplemente no existe. Recomendación: tratarlo como **requisito condicional**: si el segundo caso real tiene columnas decimales (probable en Teradata), es bloqueante para v1; si no las tiene, se puede diferir sin problema. Decidirlo recién cuando se elija el segundo caso, no antes.

Todo lo demás de la "fuera de alcance" (Derived Column, Lookup, Conditional Split, Aggregate, Script Component, reglas funcionales, inferencia automática) está correctamente fuera hoy — nada de eso se filtró accidentalmente al código actual.

---

## 9. Riesgos

| Riesgo | Nivel | Por qué |
|---|---|---|
| Mappings (mecanismo) | BAJO | Genérico, probado con 7 casos reales, errores de autoría ya cachados por spec_validator |
| **Tipos (compatibilidad semántica)** | **ALTO** | Solo hay UN par de tipos probado (`str(10)→dbDate`). Cualquier otro par es territorio no validado — puede generar XML válido para el parser pero incorrecto en tiempo de ejecución real |
| Metadata externa | MEDIO | Mecanismo correcto, pero 100% dependiente de que el humano declare bien el tipo físico (ej. `wstr(27)` para un `datetime2(7)`) — sin ninguna asistencia |
| Lineage IDs | BAJO | Determinista, compartido con el parser, ya probado por round-trip |
| **Paths** | **ALTO** | Hardcodeados a 2, forma fija. Rompe en cuanto Data Conversion sea opcional — es el trabajo central de la próxima iteración |
| **Data Conversion opcional** | **ALTO** | Suposición estructural más profunda del generador actual (spec_validator + generator + template) |
| Columnas no convertidas atravesando Data Conversion | BAJO | Ya funciona bien: hoy las columnas sin conversión van directo de Teradata Source a destino, sin pasar por Data Conversion |
| Cambios de nombre | BAJO | Ya desacoplado y probado (`pipeline_input_column` vs `destination_column`) |
| Nullability | MEDIO | No modelado en absoluto; `errorRowDisposition` siempre `"FailComponent"` hardcodeado, sin opción de redirect/ignore en el spec |
| **Precision/scale** | **ALTO** | Completamente ausente del spec — bloqueante si el 2º caso real tiene `DECIMAL`/`NUMERIC` |
| Strings Unicode/no Unicode | MEDIO | El mecanismo `str`/`wstr` funciona para lo probado, pero sin ninguna validación de compatibilidad real |
| Code pages | MEDIO | Mecánicamente soportado, valor nunca validado contra nada (una code page incorrecta pasaría validación) |
| Template XML | MEDIO-ALTO | TODO lo no tocado por el generador se hereda de un único archivo real validado UNA vez — nunca probado con otra versión de componente/engine |

---

## 10. Plan incremental (ajustado al código real)

```
Paso 1 — Elegir el segundo caso real ANTES de tocar código
         (idealmente con str→wstr directo sin conversión, o con un DECIMAL,
         para estresar justo los puntos débiles de esta auditoría)

Paso 2 — Escribir a mano el process_spec.json de ese caso contra el
         validador ACTUAL (sin cambiar nada) y anotar los errores reales
         que tira. Esto reemplaza suposiciones por evidencia concreta.

Paso 3 — Hacer Data Conversion opcional (el cambio central):
         a) spec_validator: transformations acepta 0 o 1 elementos
         b) campanias_generator.build_package_tree: transform_spec = None si no hay
         c) si no hay conversión: remover el componente Data Conversion del
            árbol (no dejarlo huérfano sin cablear, como el caso de Turnero)
         d) _rebuild_paths: 1 path (source→destino) o 2 (source→conversión→destino)
         e) _update_design_time_properties: ajustar el ref_id_map según haya
            o no Data Conversion

Paso 4 — Renombrar/reubicar el módulo (decisión de nomenclatura, no de
         lógica) — recién DESPUÉS de probar el Paso 3, no antes

Paso 5 — Regenerar Campanias con el generador ya generalizado, confirmar
         CERO regresión (debe seguir teniendo su Data Conversion, sus 2 paths)

Paso 6 — Generar el segundo caso real, validar Nivel 1 (parser/validator)

Paso 7 — Validación manual SSDT/QA del segundo caso (el gate real)

(condicional) — precision/scale, solo si el Paso 1 lo hace necesario
```

Mapping Planner y modelo canónico de tipos **no entran en este plan como código** — quedan evaluados (§6/§7) pero no planificados, consistente con que §4/§6 concluyen que hoy no hay evidencia ni necesidad inmediata.

---

## 11. Qué NO implementar todavía

- Mapping Planner como módulo de código (solo el concepto quedó evaluado).
- Modelo canónico de tipos.
- Precision/scale, a menos que el Paso 1 del plan lo haga necesario.
- Renombrar/mover `campanias_generator.py`.
- Cualquier regla de inferencia automática de conversión (confirmado sin evidencia suficiente, §4).
- `UNSAFE`/`UNSUPPORTED` como categorías codificadas (falta la decisión de política que se señala en §6).
- Derived Column, Lookup, Conditional Split, Aggregate, Script Component.

---

## 12. Conclusión

**GO CON RESTRICCIONES.**

La base es sólida — la mayor parte del generador (conexiones, lineage, loops de columnas y mappings) ya es genérica y no tiene nada de Campanias hardcodeado. Pero hay un bloqueo estructural concreto y bien entendido (Data Conversion obligatorio + paths fijos, §1-3) que **debe resolverse antes** de intentar un segundo caso real, y un gap de modelado (precision/scale) que puede o no ser bloqueante según qué caso elijan. Ninguno de los dos requiere descartar trabajo hecho ni es arquitectónicamente riesgoso — es una extensión acotada y ya diseñada en el Paso 3 del plan. No es un GO limpio porque falta ese trabajo concreto; no es NO-GO porque nada de lo encontrado cuestiona la estrategia general (template + modificación programática + spec limpio).
