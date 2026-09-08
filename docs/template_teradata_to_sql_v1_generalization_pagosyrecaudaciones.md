# Prueba de generalización — `PagosYRecaudaciones_FacturacionComi.dtsx` vs. `template-teradata-to-sql-v1`

Primera prueba independiente de generalización del template congelado `template-teradata-to-sql-v1` contra un segundo caso real que **no participó** en el diseño del template. Evidencia recolectada íntegramente del `.dtsx` real (`Examples/Originals/PagosYRecaudaciones_FacturacionComi/PagosYRecaudaciones_FacturacionComi.dtsx`) más los 4 archivos de contexto de proyecto (`PagosYRecaudaciones.dtproj`, `Project.params`, `cnxTeradata.conmgr`, `cnxSQL.conmgr`).

Esta es una iteración de **análisis puro**: no se modificó el generador, el spec_validator, el parser, el ProjectContext ni el template XML. No se generó ningún `.dtsx` nuevo. No hubo commit ni tag.

Filosofía que guía esta prueba: no se busca conseguir que este paquete funcione a cualquier costo, sino medir cuánto de un segundo caso real puede representar el template congelado sin haber sido diseñado para él.

## 1. Resumen ejecutivo

> **Corrección posterior (ver detalle en secciones 5 y 6):** la versión original de este informe afirmaba que las 6 conversiones str→wstr de este paquete eran representables por v1 "sin cambios". Una verificación de código posterior determinó que eso es **incorrecto**: `_build_data_conversion()` nunca propaga `length`/`code_page` para ningún `target_type`, y el schema de `conversions[]` no tiene campos para declararlos. Esto es invisible en Campanias porque sus dos únicas conversiones reales targetean `dbDate` (que no necesita longitud) — el camino de código para targets string (`str`/`wstr`) nunca fue ejercitado end-to-end. Las tablas y la conclusión de este documento fueron actualizadas para reflejarlo.

El paquete tiene el mismo *core* de Data Flow que Campanias (Teradata Source → Data Conversion opcional → OLE DB Destination) y ese core es representable por v1 en su mecanismo general (topología, mapeos directos, mapeos vía Data Conversion), pero aparecen **tres** categorías de incompatibilidad real, no dos:

1. **Un gap de metadata en conversiones con target string** (`conversions[].target_type` ∈ `{str, wstr}`) — bug de generator + capability gap de schema, confirmado en las 6 conversiones reales de este paquete.
2. **Un gap de tipo (`numeric` con precision/scale)** que bloquea 2 de 16 columnas del Data Flow — acotado, no estructural.
3. **Un gap de Control Flow** (Script Task previo + variable de paquete + `PropertyExpression` sobre una propiedad del Data Flow Task, no de un Connection Manager) — estructural, fuera del modelo actual de v1 por completo.

Ninguno de los tres gaps invalida el diseño de v1; los tres son extensiones aditivas, no contradicciones de lo ya congelado.

## 2. Diagrama real del paquete

```
Control Flow:
[Obtener Fechas]  --Success-->  [Tarea Flujo de datos]
 (Script Task,                   (Data Flow Task,
  Microsoft.ScriptTask)           Microsoft.Pipeline,
                                  DelayValidation=True,
                                  PropertyExpression:
                                  [Teradata Source].[SqlCommand]
                                    = @[User::sqlTeradata])

Data Flow ("Tarea Flujo de datos"):

Teradata Source (16 columnas de salida)
   ├── 6 columnas str ──> Conversión de datos (Microsoft.DataConvert) ──> 6 columnas wstr
   └── 10 columnas (i2/i4/i8/dbDate/numeric/str) ──> directo, sin conversión
                                     │
                                     ▼
                    Destino de OLE DB → [staging].[MovimientoComisionDW]
                    (16 columnas mapeadas + 4 columnas destino sin fuente)
```

Topología del Data Flow: **idéntica en forma** al caso A ya validado (Source → Conversion opcional → Destination), con 2 rutas (`paths`), igual que Campanias.

Conexiones confirmadas contra el contexto de proyecto: `cnxTeradata` (`CreationName="TERADATA"`, `.conmgr`) y `cnxSQL` (`CreationName="OLEDB"`, `.conmgr`) — ambos providers coinciden con `EXPECTED_SOURCE_PROVIDER`/`EXPECTED_DESTINATION_PROVIDER` del `spec_validator` actual. Ambos Connection Managers se resuelven vía `PropertyExpression` a parámetros `$Project::` (`dbTeradata`, `pwTeradata`, `srvTeradata`, `usrTeradata`, `pwUsSxSSIS`, `srvDbSqlCons11`), mismo patrón que BipSuc/Campanias.

## 3. Variables, SQL dinámico y clasificación del Script Task

**Variables de paquete** (`PagosYRecaudaciones_FacturacionComi.dtsx:20-47`):
- `User::fecDesde` (DateTime) — se escribe en el Script Task, **no se lee en ningún otro lugar del paquete** (no hay segunda `PropertyExpression` que la referencie).
- `User::fecHasta` (DateTime) — mismo caso: escrita, nunca leída.
- `User::sqlTeradata` (String, valor inicial vacío) — escrita en el Script Task, leída por la única `PropertyExpression` del paquete.

**Script Task "Obtener Fechas"** (`ReadWriteVariables="User::fecDesde,User::fecHasta,User::sqlTeradata"`, líneas 61-66):

```csharp
DateTime hoy = DateTime.Today;
DateTime primerDiaMesActual = new DateTime(hoy.Year, hoy.Month, 1);
DateTime ultimoDiaMesActual = primerDiaMesActual.AddMonths(1).AddDays(-1);
DateTime primerDiaMesAnterior = primerDiaMesActual.AddMonths(-1);
DateTime ultimoDiaMesAnterior = primerDiaMesActual.AddDays(-1);

Dts.Variables["User::fecDesde"].Value = primerDiaMesActual;
Dts.Variables["User::fecHasta"].Value = ultimoDiaMesActual;

string sql = $@"SELECT Id_Evento, ..., Sector, Num_Cambio
                 FROM f_facturacion_cobros_clientes_BIP
                 WHERE Fec_Evento BETWEEN DATE '{primerDiaMesAnterior:yyyy-MM-dd}'
                                      AND DATE '{ultimoDiaMesAnterior:yyyy-MM-dd}'";
Dts.Variables["User::sqlTeradata"].Value = sql;
```
(líneas 393-434)

**Dato clave:** la consulta NO es dinámica en su *forma* (columnas, tabla, cláusula WHERE son texto fijo en el C#) — sólo los dos límites de fecha se interpolan como literales. `fecDesde`/`fecHasta` son estado muerto: se calculan pero no alimentan nada más en el paquete.

**Recorrido Script Task → variable → PropertyExpression → Teradata Source:**

```
Obtener Fechas (C#)
  → Dts.Variables["User::sqlTeradata"].Value = sql        (línea 432)
  → DTS:PropertyExpression Name="[Teradata Source].[SqlCommand]"
       = "@[User::sqlTeradata]"                            (línea 739-740)
  → Teradata Source, property name="SqlCommand" (valor de diseño vacío,
       expressionType="Notify")                            (línea 1288-1293)
```

Es el único `PropertyExpression` de todo el archivo. `DTS:DelayValidation="True"` en la Data Flow Task (línea 731) es la consecuencia directa de que su `SqlCommand` no tiene texto en tiempo de diseño.

**Clasificación A-E del Script Task:**

Es principalmente **B — parametrización dinámica del source**. El *mecanismo* (una tarea previa calcula un valor, lo escribe en una variable, una `PropertyExpression` liga esa variable a una propiedad de un componente del Data Flow) es un idioma genérico de SSIS para ventanas de extracción incrementales, no algo específico de este paquete. La *regla de negocio concreta* embebida en el C# (mes calendario anterior) sí es específica del paquete (rasgo de **C/E**), pero el Generator no necesitaría entender ni reproducir esa regla — sólo necesitaría saber que "este `SqlCommand` se resuelve por expresión, no por literal".

**Respuesta explícita — ¿nueva familia de template o capacidad adicional?** El SQL dinámico **no obliga a una nueva familia de template**. Puede verse como una **capacidad adicional** de `TERADATA_TO_SQL`: el Data Flow (columnas, Data Conversion, mappings, paths) es idéntico en mecanismo al ya soportado; lo único nuevo es que el Source podría necesitar un `SqlCommand` "referenciado por variable" en vez de literal, y que el paquete podría necesitar un nodo de Control Flow previo — ninguno de los dos cambia cómo se genera el Data Flow en sí.

## 4. Compatibilidad del Data Flow (matriz)

Ver sección 5.

## 5. Matriz de compatibilidad con TERADATA_TO_SQL v1

| Elemento | Soportado por v1 | Gap | Riesgo | Observación |
|---|---|---|---|---|
| Componente Teradata Source | Sí (A) | — | Bajo | Mismo componente/propiedades que Campanias |
| Connection Manager `cnxTeradata` (TERADATA) | Sí (A) | — | Bajo | Confirmado en `.conmgr`; coincide con `EXPECTED_SOURCE_PROVIDER` |
| Connection Manager `cnxSQL` (OLEDB) | Sí (A) | — | Bajo | Confirmado en `.conmgr`; coincide con `EXPECTED_DESTINATION_PROVIDER` |
| `SqlCommand` como texto literal | No | **Gap** | Alto (acotado a este caso) | Valor de diseño vacío; el texto vive sólo en C# |
| 16 columnas de salida del Source (tipos/longitud/codepage) | Sí (A) | — | Bajo | Declaradas explícitamente como metadata de diseño |
| Data Conversion — mecanismo (1 input, N conversiones, salida de error) | Sí (A) | — | Bajo | Mismo mecanismo que Campanias |
| Data Conversion — target string (6× str→wstr: `Id_Evento`, `Cod_Identif_Tributaria`, `Num_Identif_Tributaria`, `Desc_Tipo_Impuesto`, `Desc_Movimiento_Trx`, `Cod_Moneda`) | **No** | **Gap (B)** | Alto (6 de 6 conversiones reales de este paquete) | Campanias solo validó str→`dbDate`; `_build_data_conversion()` nunca propaga `length`/`code_page`, y `conversions[]` no tiene esos campos en el schema — ver sección 6 |
| Mapeo directo sin conversión (8 de 10 columnas: i2/i4/i8/dbDate/str) | Sí (A) | — | Bajo | Ya validado con el spec sintético |
| `numeric(precision,scale)` (`Mto_Evento`, `Num_Cambio`) | No | **Gap** | Alto (2 de 16 columnas) | Sin campos precision/scale en el schema del spec |
| `dbDate`→`wstr` implícito sin Data Conversion (`Fec_Evento`) | Sí (A) mecánicamente | — | Bajo/Medio | Mismo mecanismo de mapeo directo, distinto par de tipos |
| Rename + resize en mapeo directo (`Sector` str(2) → `CanalIncorporacion` str(20)) | Sí (A) | — | Bajo | El schema de mapping ya separa `source`/`target` y `target_length` |
| Columna calculada por el motor origen (`PeriodoFacturado`) | Sí (A) | — | Bajo | Indistinguible de una columna física para el Generator |
| 4 columnas destino sin fuente | Sí (A) estructural | — | Bajo | El spec sólo declara los mappings existentes |
| Script Task previo | No | **Gap** | Alto | Sin noción de Control Flow en v1 |
| Variables de paquete | No | **Gap** | Alto | Sin campo en el spec |
| `PropertyExpression` sobre propiedad de un componente del Data Flow | No | **Gap** | Alto | ProjectContext/Generator sólo conocen esto en Connection Managers |
| `PrecedenceConstraint` entre 2 executables | No | **Gap** | Alto | Template/spec asumen 1 solo Data Flow Task |

## 6. Data Conversions completas

| Columna origen | Tipo/long/codepage origen | Columna salida | Tipo/long destino | FastParse |
|---|---|---|---|---|
| `Id_Evento` | str(20), cp1252 | `Id_Evento_Sal` | wstr(20) | false |
| `Cod_Identif_Tributaria` | str(10), cp1252 | `Cod_Identif_Tributaria_Sal` | wstr(10) | false |
| `Num_Identif_Tributaria` | str(20), cp1252 | `Num_Identif_Tributaria_Sal` | wstr(20) | false |
| `Desc_Tipo_Impuesto` | str(255), cp1252 | `Desc_Tipo_Impuesto_Sal` | wstr(255) | false |
| `Desc_Movimiento_Trx` | str(100), cp1252 | `Desc_Movimiento_Trx_Sal` | wstr(100) | false |
| `Cod_Moneda` | str(3), cp1252 | `Cod_Moneda_Sal` | wstr(3) | false |

Las 6 son **str→wstr, misma longitud**, cero conversiones de fecha, cero conversiones numéricas dentro de Data Conversion. El componente y el patrón (`FastParse=false`) son mecánicamente idénticos a Campanias — pero el **par de tipos no es el mismo**: Campanias solo ejercitó `str→dbDate` (`FEC_INICIO_TXT`/`FEC_FIN_TXT`, ver `specs/campanias_generated.json:16-28`), nunca `str→wstr`. Esa diferencia de tipo importa porque `dbDate` no requiere longitud y `wstr` sí.

### Verificación de código: por qué str→wstr NO es representable hoy sin cambios

**Campanias — pares reales:**

| input | tipo/longitud origen | output | target_type |
|---|---|---|---|
| `FEC_INICIO_TXT` | str(10), cp1252 | `FechaInicio_SAL` | `dbDate` |
| `FEC_FIN_TXT` | str(10), cp1252 | `FechaFin_SAL` | `dbDate` |

**PagosYRecaudaciones — pares reales:**

| input | tipo/longitud origen | output | target_type |
|---|---|---|---|
| `Id_Evento` | str(20), cp1252 | `Id_Evento_Sal` | `wstr(20)` |
| `Cod_Identif_Tributaria` | str(10), cp1252 | `Cod_Identif_Tributaria_Sal` | `wstr(10)` |
| `Num_Identif_Tributaria` | str(20), cp1252 | `Num_Identif_Tributaria_Sal` | `wstr(20)` |
| `Desc_Tipo_Impuesto` | str(255), cp1252 | `Desc_Tipo_Impuesto_Sal` | `wstr(255)` |
| `Desc_Movimiento_Trx` | str(100), cp1252 | `Desc_Movimiento_Trx_Sal` | `wstr(100)` |
| `Cod_Moneda` | str(3), cp1252 | `Cod_Moneda_Sal` | `wstr(3)` |

**`generator/campanias_generator.py::_build_data_conversion()` (líneas 499-593):**

- Lee de cada entrada del spec solo `conv["input"]`, `conv["output"]`, `conv["target_type"]` (línea 528-531) — ningún campo de longitud o code page.
- Construye el `<outputColumn>` propio del componente Data Conversion sin pasar `length=` en ningún caso (línea 553-561): `_make_output_column(out_ref, output_name, target_type, lineage_id=out_ref, ...)`. `_make_output_column` solo emite el atributo `length` si se lo pasan explícitamente (línea 179-180) — así que el `<outputColumn>` generado **nunca tiene `length`**, sea el target `dbDate`, `wstr` o cualquier otro tipo.
- Registra en `pipeline_columns[output_name]` (línea 585-590): `"length": None, "code_page": None` — **hardcodeado, sin condicionar por `target_type`**.

**`generator/spec_validator.py`:** valida `conversions[].target_type` (no vacío, líneas 244-285) pero no tiene ningún campo ni validación de longitud/code page para conversiones. En cambio, para `mappings[]` (destino) sí exige `target_length` cuando `target_data_type` ∈ `STRING_DATA_TYPES = {"str", "wstr"}`, y `target_code_page` cuando es `str` (líneas 341-354). Es una asimetría real en el propio schema: el validador sabe que los tipos string necesitan longitud, y lo aplica en columnas de origen y en mappings de destino, pero no en conversiones.

**Consecuencia para el XML generado:**
- El `<outputColumn>` de la Conversión de datos quedaría sin `length` para las 6 columnas — inconsistente con el `.dtsx` real, donde sí aparece (`dataType="wstr" length="20"`, etc.).
- El `<inputColumn>` del OLE DB Destination (que lee `cachedLength`/`cachedCodepage` desde `pipeline_columns`, línea 649-650) quedaría sin esos atributos para las 6 columnas.
- El `<externalMetadataColumn>` del destino (la columna física) **sí** quedaría correcto, porque se construye leyendo `target_length`/`target_code_page` directamente del `mapping` del spec (líneas 631-632, 655-659), no de `pipeline_columns` — y ese campo sí está validado en `mappings[]`.

**Clasificación:** bug + capability gap combinados, no falsa alarma. Es bug porque `_build_data_conversion` descarta length/code_page incondicionalmente en vez de propagarlos cuando corresponde, rompiendo la simetría que el propio código ya respeta del lado de `mappings[]`; el bug quedó latente porque el único caso real validado hasta ahora (Campanias) nunca ejercitó una conversión con target string. Es también capability gap porque, aun arreglando el generador, `conversions[]` no tiene hoy ningún campo para expresar `target_length`/`target_code_page` — hace falta agregarlo al schema y a la validación, análogo a lo que ya existe en `mappings[]`.

**Conclusión corregida:** las 6 conversiones str→wstr de este segundo caso **no son representables por `template-teradata-to-sql-v1` tal como está congelado hoy**. Se reclasifican como **B (gap del template)** en la matriz de la sección 5, no como A.

## 7. Compatibilidad de mappings

- **Mapeos directos desde Conversión** (6): el mecanismo de mapping en sí (source de Conversión → destino) es el mismo ya validado, pero las 6 columnas de origen dependen de una conversión str→wstr que hoy **no** propaga `length`/`code_page` (ver sección 6) — quedan bloqueadas hasta que se corrija el generator y se extienda el schema de `conversions[]`.
- **Mapeos directos sin conversión** (10): `Id_d_Cliente`, `Cod_Ubicacion`, `Id_d_Contrato`, `Cod_Tipo_Impuesto`, `Cod_Movimiento_Trx`, `Fec_Evento`, `Mto_Evento`, `PeriodoFacturado`, `Sector`→`CanalIncorporacion`, `Num_Cambio`. De estas, **8 son representables hoy**; `Mto_Evento` y `Num_Cambio` quedan bloqueadas por precision/scale (sección 8).
- **Cambio de nombre**: `Sector` (str(2), origen) → `CanalIncorporacion` (str(20), destino) — representable, mismo mecanismo que cualquier mapping con `source`≠`target`.
- **Columnas destino sin fuente**: `IdMovimientoStaging` (i8), `FechaRecepcion` (wstr(23)), `EsProcesableIncorporacion` (bool), `MotivosObservacion` (str(255)) — el Data Flow no les asigna valor; quedan a cargo de defaults/nulabilidad de la tabla. No es un gap de v1: el spec nunca exigió cobertura total del destino.

## 8. DECIMAL/NUMERIC — respuesta binaria

**APARECE Y BLOQUEA.**

- `Mto_Evento`: `numeric`, `precision="15"`, `scale="2"` (líneas 1073-1078 y 1208-1212), mapeo directo Source→Destino.
- `Num_Cambio`: `numeric`, `precision="9"`, `scale="5"` (líneas 1130-1135 y 1240-1245), mapeo directo Source→Destino.

El schema actual del spec (`campanias_generated.json`) no tiene campo de precision/scale en ningún nivel (columna, transformación o mapping). Bloquea exactamente **2 de 16 columnas** del Data Flow; el resto (14 columnas fuente, las 6 conversiones, todos los demás mappings) es representable hoy sin cambios.

### SQL estático vs. dinámico — respuestas a las 5 sub-preguntas

1. **¿El Generator puede reproducir el Data Flow con la metadata almacenada aunque `SqlCommand` esté vacío?** Sí. Todas las columnas, tipos, longitudes, codepages, conversiones y mappings están declarados como metadata de diseño independiente del texto de `SqlCommand`; el Generator nunca lee ese valor para construir la estructura, sólo lo escribiría como texto si existiera.
2. **¿El SQL debería ser literal o referenciado por variable en el spec?** Referenciado por variable — el valor de diseño real está vacío; forzar un literal inventaría un dato que el paquete no tiene.
3. **¿Pertenece a la config del Source o al Control Flow?** A ambos: "`SqlCommand` se resuelve por expresión" es una propiedad del Source; el origen de esa expresión (variable + quién la llena) pertenece al Control Flow, que v1 no modela.
4. **¿La variable SQL debería ser parte del spec?** Sí, si se quisiera representar el caso — declarando la variable y el binding; el contenido C# que la llena podría tratarse como opaco/fuera de alcance.
5. **¿Riesgo de divergencia metadata/runtime?** Sí, y es preexistente al Generator: la lista de columnas del SELECT en C# y la lista de `outputColumn`/`externalMetadataColumn` en el XML son dos fuentes de verdad mantenidas a mano, sin validación cruzada en el paquete original.

## 9. Gaps del Control Flow — clasificación

| Gap | Clasificación |
|---|---|
| Segundo executable (Script Task) | Estructural + de spec + de generator + de template XML (el template congelado sólo tiene 1 executable) |
| `PropertyExpression` sobre propiedad de un componente del Data Flow | De spec + de generator (nunca escrito hoy; ProjectContext sólo cubre Connection Managers) |
| Variables de paquete | De spec + de generator (sin campo, sin código que emita `DTS:Variable`) |
| `PrecedenceConstraint` entre executables | De spec + de generator + de template XML |
| `DelayValidation="True"` en la Data Flow Task | Consecuencia derivada, no gap independiente |

Ninguno es gap de parser ni de validator en el sentido de "algo mal detectado" — son ausencias de modelo, no errores.

## 10. Alternativas A/B/C

**A — Extender el template con `optional_preparation_task`:** ventaja: cambio incremental sobre la infraestructura de "elemento opcional" ya probada con Data Conversion. Costo: alto y heterogéneo (clonar un `DTS:Executable` con C#/`ScriptProject`/DLL compilada, `DTS:Variables` a nivel Package, `PrecedenceConstraint`, `PropertyExpression` sobre un componente del Data Flow). Riesgo de sobregeneralización: **alto** — "generalizar" el Script Task real significa o copiarlo como blob opaco (bordea lo explícitamente prohibido) o modelar C# en el spec (fuera de alcance de un generador de XML). Reutilización: media, sólo sirve para el patrón exacto de "1 tarea previa + 1 Data Flow". Impacto: rompe la invariante actual de que el spec describe sólo un Data Flow.

**B — Capa de Control Flow Template separada, por encima de TERADATA_TO_SQL:** ventaja: mantiene v1 puro y congelado; separa "generar un Data Flow Teradata→SQL" de "orquestar tareas alrededor de él". Costo: diseño nuevo, no incremental. Riesgo: medio, manejable si se construye incrementalmente en vez de anticipar todos los tipos de tarea posibles. Reutilización: alta — sirve a cualquier familia futura de Data Flow, no sólo ésta. Impacto sobre v1: ninguno, es aditivo y ortogonal.

**C — Fuera de alcance de v1 por ahora:** ventaja: costo y riesgo cero, v1 se mantiene enfocado. Costo: este paquete no se puede generar end-to-end hoy sin edición manual posterior. Riesgo: ninguno. Reutilización: ninguna inmediata, pero no cierra puertas.

**Recomendación:** B es la dirección arquitectónica correcta a mediano plazo, pero **C es la decisión correcta para hoy**. Con un solo caso real (n=1) que exhibe este patrón, diseñar la capa de Control Flow ahora sería generalizar sobre evidencia insuficiente — exactamente lo que la filosofía del proyecto busca evitar. Conviene documentar el gap y esperar un tercer caso real que confirme variaciones del patrón antes de diseñar B con evidencia en vez de especulación.

## 11. Recomendación arquitectónica

Mantener `template-teradata-to-sql-v1` congelado tal como está — incluyendo no tocar todavía el Control Flow. El Data Flow de este segundo paquete es representable en su mecanismo general (topología Source→Conversion opcional→Destination, mapeos directos, rename/resize) sin cambio de diseño — pero **no** en su totalidad: 8 de 16 columnas fuente son representables sin cambios, mientras que las otras 8 quedan bloqueadas por dos gaps de tipo acotados y bien entendidos:

- **Conversión str→wstr** (6 columnas): bug de generator (`_build_data_conversion` no propaga `length`/`code_page`) + capability gap de schema (`conversions[]` no tiene esos campos). Corrección candidata: agregar `target_length`/`target_code_page` a `conversions[]` (análogo a `mappings[]`) y corregir `_build_data_conversion`/`pipeline_columns` para propagarlos cuando `target_type` es string.
- **`numeric` con precision/scale** (2 columnas): capability gap de schema — falta el campo en cualquier nivel del spec.

Ninguno de los dos exige tocar el mecanismo de Data Flow ni el template XML — son extensiones acotadas al schema y al generator, no una nueva familia de template. El gap de Control Flow (Script Task + variable + `PropertyExpression` sobre el Data Flow) es de naturaleza distinta — estructural — y se mantiene la recomendación de la sección 10: diferirlo (Alternativa C) hasta contar con un tercer caso real que justifique diseñar la capa de Control Flow (Alternativa B) con evidencia en vez de especulación.

## 12. Resultado final

**PASS PARCIAL**

- El núcleo mecánico del Data Flow (Source→Conversion opcional→Destination, mapeos directos, rename/resize) generaliza correctamente a un segundo caso real que no participó en el diseño del template — validación fuerte del diseño de v1.
- Bloqueado por **tres** gaps de tipo/estructura distintos, no dos:
  1. **Conversión str→wstr** (6 de 6 conversiones reales de este paquete) — bug de generator + capability gap de schema en `conversions[]`. La primera versión de este informe había clasificado esto erróneamente como ya soportado; corregido tras verificación de código (sección 6).
  2. **`numeric(precision,scale)`** (2 de 16 columnas) — gap de tipo, no estructural.
  3. **Control Flow dinámico** (Script Task + variable + `PropertyExpression` sobre el Data Flow) — completamente fuera del modelo actual, documentado como candidato a capacidad futura (Alternativa B), explícitamente diferido.
