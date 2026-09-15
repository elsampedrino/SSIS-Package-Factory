# `derived-column-v1`

**STATUS: CLOSED — STABLE / SSDT VALIDATED.**

El Gate SSDT manual fue completado y confirmado por el usuario (ver "Gate
SSDT — resultado final" más abajo). `control-flow-v1` sigue **BLOCKED /
EXPERIMENTAL** y no fue tocado — verificado explícitamente mediante
`tests/test_control_flow.py` en aislamiento (62/62 tests, sin cambios de
comportamiento) tanto antes como después del cierre. `mapping_planner/`,
`project_generator/`, `project_context/` y `profiles/` tampoco se tocaron.

Suite completa antes del Gate: **434/434 tests OK** (372 previos + 62
nuevos de este milestone). E2E `DerivedColumnV1_E2E.dtsx`: `valid=True`,
`errors=[]`, 4 componentes, 3 paths, `DesignTimeProperties` presente.

## Gate SSDT — resultado final (✅ PASS)

Confirmado manualmente por el usuario sobre `DerivedColumnV1_E2E.dtsx`,
agregado al proyecto BipSuc:

| Evidencia | Resultado | Medio |
|---|---|---|
| Package abre en SSDT sin error (`LoadFromXML`) | PASS | Visual |
| Source reconocido | PASS | Visual |
| Data Conversion reconocida | PASS | Visual |
| Derived Column reconocida | PASS | Visual |
| Destination reconocido | PASS | Visual |
| Topología completa: Teradata Source → Data Conversion → Derived Column → OLE DB Destination | PASS | Visual |
| `FEC_INICIO_TXT` → `Fechainicio_SAL` → `fecha de base de datos [DT_DBDATE]` (Data Conversion preexistente, convive con Derived Column) | PASS | Visual |
| `PLASTICOSUDN` → `PLASTICOSUDN__derived` | PASS | Visual |
| Modo `<add as new column>` (confirma no replace-existing) | PASS | Visual |
| `ISNULL(PLASTICOSUDN) ? NULL(DT_WSTR,6) : (DT_WSTR,6)PLASTICOSUDN` reconocida e interpretada por SSDT | PASS | Visual |
| Data Type `cadena Unicode [DT_WSTR]`, Length `6` | PASS | Visual |
| Cierre y reapertura de Visual Studio, reapertura del paquete, estructura conservada | PASS | Visual |
| Mappings del OLE DB Destination | **NOT INSPECTED** (tabla destino ficticia, ver nota abajo) | — |
| Mappings del OLE DB Destination (directo + convertido + derivado conviviendo) | PASS | Programático (translator/generator/parser/validator/tests/E2E) |

**Nota explícita sobre el punto no inspeccionado visualmente**: la pestaña
de mappings/asignaciones del OLE DB Destination no pudo inspeccionarse en
SSDT porque `[dbo].[DerivedColumnV1E2E]` es **deliberadamente** una tabla
ficticia — SSDT no puede resolver un destino real inexistente. **Esto no se
considera un fallo del gate** (mismo criterio ya aplicado en
`mapping-planner-v1`): la generación de esos mappings ya está cubierta
programáticamente (tests del generador, del `spec_validator`, de
`ssis_parser`/`ssis_validator`, del E2E automático — ver §13 más abajo). No
se afirma una verificación visual que no ocurrió.

### 2.1–2.4 — Detalle de lo observado

- **Topología**: el Data Flow abrió mostrando exactamente `Teradata Source
  → Data Conversion → Derived Column → OLE DB Destination`, sin
  `LoadFromXML`, con los 4 componentes reconocidos por SSDT.
- **Data Conversion**: `Input Column = FEC_INICIO_TXT`,
  `Output Alias = Fechainicio_SAL`, `Data Type = fecha de base de datos
  [DT_DBDATE]` — confirma que la Data Conversion preexistente convive
  correctamente con Derived Column (capacidad ya existente de Data
  Conversion, no una capacidad nueva de este milestone).
- **Derived Column**: `Derived Column Name = PLASTICOSUDN__derived`,
  `Modo = <add as new column>`,
  `Expression = ISNULL(PLASTICOSUDN) ? NULL(DT_WSTR,6) : (DT_WSTR,6)PLASTICOSUDN`,
  `Data Type = cadena Unicode [DT_WSTR]`, `Length = 6` — confirma input
  correcto, output nuevo, no replace-existing, `ISNULL`/`NULL(DT_WSTR,6)`/
  cast `(DT_WSTR,6)` reconocidos e interpretados por SSDT. Valida
  específicamente el caso seguro `i2 → wstr(6)` mediante
  `null_preserving_cast`.
- **Persistencia**: cerrar Visual Studio, reabrir la solución y reabrir
  `DerivedColumnV1_E2E.dtsx` preservó la estructura completa del paquete.

## Motivación y evidencia real

Todo este milestone está motivado por un único patrón real, encontrado
idéntico 3 veces en `Examples/Originals/BipSuc_Turnero.dtsx`
(`Microsoft.DerivedColumn`, columna `PlasticosUdn_Sal`):

- Origen: `PLASTICOSUDN`, tipo Teradata `i2` (SMALLINT), sin metadata de
  longitud (los enteros exactos no la llevan).
- Salida de la columna derivada: `dataType="wstr"`, `length="1"`.
- Columna física de destino: `wstr(1)`.
- `errorRowDisposition="FailComponent"` y
  `truncationRowDisposition="FailComponent"` en las 3 instancias — SSIS ya
  estaba configurado para fallar duro, no truncar en silencio.

**Sobre el incidente real conocido (PlasticosUn)**: lo único que sabemos
con evidencia es que el origen era `i2`, la salida derivada fue declarada
`wstr(1)`, y esa capacidad declarada es menor que la capacidad TEÓRICA que
`i2` necesita para representarse completo (hasta 6 caracteres, signo
incluido: `-32768`..`32767`). **No sabemos, y no se afirma en esta
documentación, que `length=1` haya sido elegido "mirando datos
observados"** — esa sería una inferencia sin evidencia. La única regla
metodológica válida y verificable es la inversa: **nunca usar valores
observados para decidir automáticamente la capacidad de una conversión**
(ver "Regla de seguridad" más abajo). Esta regla es la lección tomada del
incidente, no una reconstrucción de por qué se eligió `1` en su momento.

**Expresión SSIS real (única variante evidenciada)**:

```
Expression:         [ISNULL](#{<lineageId>}) ? NULL(DT_WSTR,<n>) : (DT_WSTR,<n>)#{<lineageId>}
FriendlyExpression:  ISNULL(<nombre>) ? NULL(DT_WSTR,<n>) : (DT_WSTR,<n>)<nombre>
```

Ningún otro patrón de Derived Column (concatenación, expresión
condicional, formateo de fecha, formateo numérico/string, constante,
rename+cast sin `ISNULL`) tiene evidencia real en el corpus disponible —
por eso **no se implementan** en v1 (ver "Fuera de alcance").

## 1. Patrón soportado (único)

**`null_preserving_cast`**: cast explícito de un entero exacto a `wstr`,
preservando `NULL` (si el origen es `NULL`, la salida es `NULL`, nunca una
cadena vacía ni un valor por defecto).

| Origen soportado | Destino soportado |
|---|---|
| `i2` (SMALLINT) | `wstr` |
| `i4` (INTEGER) | `wstr` |
| `i8` (BIGINT) | `wstr` |

Ninguna otra combinación de tipos, ni ninguna otra operación, es aceptada
por `derived_planner` en v1 (clasifica `UNSUPPORTED`, ver §3).

## 2. Regla de seguridad — capacidad teórica, nunca observada

`derived_planner.schema.THEORETICAL_STRING_CAPACITY` fija, por tipo
entero, la cantidad de caracteres que un `wstr(n)` necesita para poder
representar **cualquier** valor posible de ese tipo, signo incluido —
calculado a partir del rango teórico del tipo, **nunca inspeccionando
datos reales de ninguna tabla**:

| Tipo origen | Rango teórico | Capacidad mínima requerida |
|---|---|---|
| `i2` | `-32768` .. `32767` | `6` |
| `i4` | `-2147483648` .. `2147483647` | `11` |
| `i8` | `-9223372036854775808` .. `9223372036854775807` | `20` |

Si `target_length` declarado es **menor** que esta capacidad mínima, la
clasificación es **`UNSAFE`** con prioridad absoluta — no se genera nada,
no hay override automático, no se infiere `target_length` desde valores
reales, no se inspeccionan datos, no se trunca en silencio. El caller
(humano) debe declarar explícitamente un `target_length` suficiente.

## 3. Taxonomía — reutilizada, no ampliada

`derived_planner.schema` **importa** (no duplica) `DIRECT`,
`CONVERSION_REQUIRED`, `AMBIGUOUS`, `UNSAFE`, `UNSUPPORTED` y
`RESOLVABLE_CLASSIFICATIONS` desde `mapping_planner.schema` — dependencia
unidireccional, sin acoplamiento circular. Se evaluó y descartó introducir
una categoría nueva (`SAFE_DERIVATION`): `null_preserving_cast` válido y
con capacidad suficiente es, en esencia, una conversión de tipo con manejo
explícito de `NULL`, y encaja sin forzar nada en `CONVERSION_REQUIRED`.

- `null_preserving_cast`, tipo/target soportados, capacidad suficiente →
  **`CONVERSION_REQUIRED`**.
- capacidad insuficiente → **`UNSAFE`** (prioridad absoluta, ver §2).
- operación no reconocida, o par (origen, destino) fuera de
  `i2/i4/i8 → wstr` → **`UNSUPPORTED`**.
- `AMBIGUOUS` y `DIRECT` no tienen ningún caso alcanzable en v1 (no hay
  evidencia real de un patrón "derivación directa sin cast" ni de un caso
  ambiguo).

## 4. Arquitectura

```
derived_planner/
  __init__.py
  schema.py       -- constantes: tipos soportados, capacidad teorica, naming
  validator.py    -- validate_derived_rules()/assert_valid_derived_rules()
  planner.py      -- classify_derived_rule()/build_derived_column_plan()
  translator.py   -- translate_plan_to_process_spec_fragment()
```

Paquete **separado** de `mapping_planner/` (no un submódulo) — el input de
`derived_planner` es autocontenido (`source_schema` + `derived_rules`, sin
reutilizar el concepto de "Logical Mapping" de `mapping_planner`, porque
una derivación no es un mapping contra un destino ya declarado, es una
columna nueva). `mapping_planner/` en sí **no se modificó** en este
milestone.

Separación de responsabilidades (misma que `mapping_planner`):

```
Source metadata + derived_rules
        |
derived_planner.validator  -- valida ESTRUCTURA (fail-fast)
        |
derived_planner.planner    -- CLASIFICA (UNSAFE/CONVERSION_REQUIRED/UNSUPPORTED), arma el plan
        |
derived_planner.translator -- TRADUCE el plan resuelto a fragmento de ProcessSpec
        |
ProcessSpec completo (ensamblado por el caller)
        |
generator.spec_validator.assert_valid_spec()
        |
generator.campanias_generator.generate()  -- SERIALIZA XML, no conoce reglas de seguridad
```

El generador nunca decide: asume que recibe un `ProcessSpec` ya válido.

## 5. Input de `derived_planner` (no es una expresión SSIS)

```python
{
    "source_schema": [{"name": "PLASTICOSUDN", "data_type": "i2"}],
    "derived_rules": [
        {
            "source": "PLASTICOSUDN",
            # "output": "Salida",   # opcional -- ver Naming
            "operation": "null_preserving_cast",
            "target_type": "wstr",
            "target_length": 6,
        }
    ],
}
```

No se acepta una expresión SSIS cruda como input en v1 (ver "Fuera de
alcance"). No existe `replace_existing`: toda derivación crea una columna
nueva, nunca sobrescribe una existente.

## 6. Naming

Sin `output` explícito: `<source>__derived` (ej. `PLASTICOSUDN__derived`).
Sufijo distinto del `__conv` de `mapping-planner-v1` (para no confundir una
columna derivada con un resultado de Data Conversion), y distinto del
patrón humano real `_Sal` (nunca generado automáticamente). Colisión
(contra `source_schema` o contra otro output ya resuelto en el mismo plan,
explícito o por defecto) → `OutputNameCollisionError`, nunca un sufijo
numérico automático.

## 7. Extensión del `ProcessSpec`

`generator/spec_validator.py` ahora soporta **dos** tipos de
`transformations[]`, independientes y cada uno opcional (a lo sumo 1 de
cada tipo): `data_conversion` (sin cambios) y `derived_column` (nuevo):

```yaml
transformations:
  - type: data_conversion       # opcional
    name: "Conversión de datos"
    conversions: [...]
  - type: derived_column        # opcional, nuevo en v1
    name: "Columna derivada"
    columns:
      - input: PLASTICOSUDN
        output: PLASTICOSUDN__derived
        operation: null_preserving_cast
        target_type: wstr
        target_length: 6
```

Si ambos coexisten, `data_conversion` debe declararse **antes** que
`derived_column` en la lista (único orden evidenciado, ver §9) —
verificado explícitamente por `spec_validator.py`. Ningún spec legacy
(sin `derived_column`) cambia de comportamiento ni de mensajes de error.
No se almacena ninguna expresión SSIS cruda en el `ProcessSpec` — el
generador construye `Expression`/`FriendlyExpression` a partir de
`operation`/`target_type`/`target_length`.

## 8. Generador — serialización de `Microsoft.DerivedColumn`

`generator/campanias_generator.py::_build_derived_column` replica
exactamente la evidencia real: `usesDispositions="true"`, sin atributo
`version` (a diferencia de otros componentes), input síncrono único,
output normal + output de error (`exclusionGroup="1"`,
`synchronousInputId` igual al input), `errorRowDisposition`/
`truncationRowDisposition="FailComponent"`,
`errorOrTruncationOperation="Cálculo"`, `ErrorCode`/`ErrorColumn` en la
columna de error (mismo helper `_make_error_columns` que Data Conversion).
Los IDs/refIds/lineageIds se generan con los mismos helpers existentes de
`xml_helpers.py` — nunca se copian los del archivo real de Turnero.

`templates/campanias_base.dtsx` ahora tiene 4 componentes de pipeline
(antes 3): se agregó un `Microsoft.DerivedColumn` genérico (inputColumns/
outputColumns vacíos, se limpian y reconstruyen por spec igual que Data
Conversion) entre Data Conversion y OLE DB Destination, con su
`NodeLayout`/`EdgeLayout` correspondiente **agregados al final** de los
existentes en `DesignTimeProperties` (nunca insertados en medio) — esto
preserva la resolución de `.find()` (primer resultado en orden de
documento) que usa `generator/control_flow_generator.py` para el
`NodeLayout` de paquete y el primer `EdgeLayout`, sin necesidad de tocar
ese módulo.

## 9. Orden topológico

Único orden evidenciado en el corpus real (`BipSuc_Turnero.dtsx`, grafo de
`<paths>`, idéntico en las 3 instancias):

```
Teradata Source -> Data Conversion -> Derived Column -> OLE DB Destination
```

Esto se replica **aunque** el input de la columna derivada
(`PLASTICOSUDN`) no sea una de las columnas tocadas por Data Conversion —
su lineage resuelve directo al Teradata Source, no al output de Data
Conversion. Es un artefacto de encadenamiento de buffers de SSIS (el orden
en que los componentes aparecen en el pipeline), no una dependencia
funcional; como es el único orden con evidencia real, v1 lo replica
exactamente cuando ambas transformaciones coexisten.

El generador soporta las 4 combinaciones de topología de forma coherente
(nunca fuerza la creación de una transformación que el spec no declaró):

1. Source → Destination (directo).
2. Source → Data Conversion → Destination.
3. Source → Derived Column → Destination.
4. Source → Data Conversion → Derived Column → Destination.

## 10. Propagación de metadata (crítico)

El registro `pipeline_columns` (nombre → metadata, incluyendo `lineage_id`)
se completa en orden — Teradata Source, luego (si existe) Data Conversion,
luego (si existe) Derived Column — de modo que el input de una columna
derivada puede resolver tanto una columna de origen sin tocar como el
output de una conversión previa. Aguas abajo, el OLE DB Destination puede
mapear indistintamente columnas directas, columnas convertidas y columnas
derivadas — verificado en el E2E (§13): las 3 categorías conviven en el
mismo `mappings[]` del destino, cada una con su propio `lineageId`
resuelto correctamente. Ninguna columna passthrough se pierde.

## 11. Parser — limitación conocida (no se corrige en v1)

`ssis_parser.py::extract_output_columns` no expone `precision`/`scale`
para `outputColumn` (gap preexistente, confirmado por búsqueda exhaustiva
en el código). No hace falta corregirlo en este milestone porque v1 solo
soporta `wstr` como destino (sin `precision`/`scale`). Documentado
explícitamente como limitación futura, a resolver si una versión posterior
de `derived-column-v1` soporta destinos `numeric`.

## 12. Fuera de alcance de v1

- **Expresión SSIS cruda como input**: `raw expression → UNSUPPORTED /
  OUT OF SCOPE en derived-column-v1`. No es un "NO-GO permanente" — una
  versión futura podría reevaluarlo si aparece evidencia real de un patrón
  que lo requiera (ej. concatenación, expresión condicional). En v1 no
  existe ningún caso evidenciado que lo justifique, y aceptar una
  expresión arbitraria del caller reintroduciría exactamente el riesgo que
  este milestone existe para evitar (lógica de negocio oculta,
  sin clasificación de seguridad posible).
- Cualquier operación distinta de `null_preserving_cast` (concatenación,
  columna constante, formateo de fecha, formateo numérico/string,
  rename+cast sin `ISNULL`): sin evidencia real en el corpus disponible.
- Cualquier origen distinto de `i2`/`i4`/`i8`, o destino distinto de
  `wstr`.
- `replace_existing` (sobrescribir una columna existente en vez de crear
  una nueva).
- Compatibilidad con Flat File Destination (no se inicia
  `template-teradata-to-flat-file-v1` en este milestone).

## 13. E2E: `DerivedColumnV1_E2E`

`specs/derived_column_v1_e2e.json` → `DerivedColumnV1_E2E.dtsx` (raíz del
repo, mismo criterio que otros milestones: artefacto tangible para
revisión manual, tabla destino ficticia
`[dbo].[DerivedColumnV1E2E]`). Usa el `ProjectContext` real de BipSuc
(`Examples/Originals/BipSuc_CampaniasVIgentes`). Topología:
`Teradata Source → Data Conversion → Derived Column → OLE DB Destination`.

Columnas: `CODIGO_CAMPANIA` (i4, mapeo `DIRECT`), `FEC_INICIO_TXT` (str →
Data Conversion → `dbDate` → mapeo `wstr(27)`), `PLASTICOSUDN` (i2 →
Derived Column `null_preserving_cast` → `wstr(6)`, mapeo directo del
output derivado). Validado con `ssis_parser.parse_file` +
`ssis_validator.validate_ir`: `valid=True`, `errors=[]`, 4 componentes,
3 paths, `DesignTimeProperties` presente, sin secretos (`Salt=`, `IV=`,
`Algorithm=`, `Password=`, `DTS:Password`, `TeraPassword` — ninguno
presente).

## Cierre del milestone

`derived-column-v1` queda **CLOSED — STABLE / SSDT VALIDATED**. Los 6 pasos
manuales que este documento pedía antes del cierre (agregar el paquete a
BipSuc, confirmar apertura sin error, confirmar la topología visual, abrir
la transformación Derived Column y confirmar expresión/tipo/longitud,
confirmar el mapeo del output derivado, cerrar/reabrir Visual Studio) fueron
todos ejecutados por el usuario y dieron **PASS** — ver "Gate SSDT —
resultado final" más arriba para el detalle completo de cada uno.

Aislamiento de `control-flow-v1` confirmado en el cierre mediante una
reconstrucción limpia (worktree temporal basado en el commit estable
`mapping-planner-v1`, con únicamente los cambios de este milestone aplicados
encima, sin ningún archivo de `control-flow-v1`): la suite completa corrió
verde (310 tests de `mapping-planner-v1` + 62 de `derived-column-v1` = 372,
1 skip preexistente no relacionado), y el E2E se generó y validó
(`valid=True`, `errors=[]`) exactamente igual que en el árbol de trabajo
real. Esto demuestra que `derived-column-v1` no depende, ni funcional ni
estructuralmente, de ningún cambio pendiente de `control-flow-v1`.
