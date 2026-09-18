# `template-teradata-to-flat-file-v1`

**STATUS: STABLE / SSDT VALIDATED.**

Implementación completa de la familia `TERADATA_TO_FLAT_FILE` (Teradata
Source → Flat File Destination), basada en evidencia real de 4 packages del
proyecto `MediosDePago`. Gate SSDT manual completo: package abre, Data Flow
reconocido, Flat File CM package-level y su editor, RaggedRight, locale,
code page, Unicode=false, HeaderRowDelimiter, Flat File Destination y su
editor, mappings, sentinel `EndLine`, PropertyExpression de `ConnectionString`
(escaping correcto, `Evaluar expresión` funcionando), y Teradata Source con
`SQL command - TPT Export` y el SQL almacenado visible — todo confirmado
manualmente. El primer Gate detectó 2 defectos concretos, ambos corregidos
antes de este cierre (ver "Gate SSDT fix #1"); un comportamiento adicional
del editor de Teradata Source (no un defecto de serialización demostrado)
queda documentado como known quirk (ver "Known SSDT designer quirk —
Teradata Source access mode").

## Gate SSDT fix #1

**BUG 1 — PropertyExpression `ConnectionString` mal escapada.** Causa raíz:
`_build_filename_expression` escapaba comillas al estilo SQL (`"` → `""`) y
no escapaba backslashes en absoluto. SSIS Expression Language usa escaping
estilo C (`\` → `\\`, `"` → `\"`), una capa distinta de XML escaping (que
ya maneja `ElementTree`) y de Python escaping (que no aplica). SSDT
rechazaba la expresión: *"The string literal ... contains an illegal escape
sequence of '\S'"*.

Fix: nuevo helper `_escape_ssis_expression_string_literal` (escapa `\` y
`"`, único par de caracteres con evidencia real de necesitarlo), aplicado
**solo** al fragmento `literal` de `_build_filename_expression` — las
referencias `@[$Project::...]`/`@[User::...]` nunca pasan por este
escaping, se insertan literalmente. Corregido para los 3 filename
strategies (literal solo; parameter+literal; parameter+literal+variable),
no un parche puntual en el E2E.

| | Antes (inválido) | Después (válido) |
|---|---|---|
| Literal solo | `"C:\SSISPackageFactory_Test\...txt"` | `"C:\\SSISPackageFactory_Test\\...txt"` |
| parameter+literal+variable | `@[$Project::pathLocal] + "MediosDePago\" + @[User::nombreArchivo]` | `@[$Project::pathLocal] + "MediosDePago\\" + @[User::nombreArchivo]` |

La forma corregida coincide exactamente con la evidencia real auditada
(`DatosContactoProcesadoras.dtsx`: `@[$Project::pathLocal]  + "MediosDePago\\" +  @[User::nombreArchivo]`).

**BUG 2 — Teradata Source del E2E en modo "Table Name" con tabla vacía
(SQL con funciones Teradata-específicas).** Investigación exhaustiva
(comparación propiedad por propiedad contra los 10 Teradata Source reales
del corpus completo, un archivo `SSDT_Golden` creado directamente en SSDT
por un humano, y el Teradata Source generado por
`template-teradata-to-sql-v1.1` estable/QA-ejecutado): **el componente
serializado por Factory es byte-idéntico, en todo atributo relevante
(`AccessMode=1`, `TableName` sin texto, `SqlCommand` poblado,
`dataType`/`typeConverter`/`expressionType` de cada property), a las 10
instancias reales y al golden SSDT-authored.** No se encontró ninguna
diferencia de serialización explicable como causa raíz. Mitigación
aplicada: SQL del E2E simplificado a un `SELECT` directo de columnas sin
ninguna función específica de Teradata, manteniendo los mismos alias
esperados por el Flat File
(`Fec_Proceso_PIC`/`Id_d_Cliente_PIC`/`Nom_Cliente_PIC`).

| | Antes | Después |
|---|---|---|
| SQL | `COALESCE(TO_CHAR(...))`/`LPAD`/`RPAD`/`SUBSTR` | `SELECT Fec_Proceso_PIC, Id_d_Cliente_PIC, Nom_Cliente_PIC FROM ...` |
| `AccessMode`/`TableName`/`SqlCommand` | ya eran correctos (`1`/vacío/poblado) — sin cambios | sin cambios (confirmado igual a la evidencia real y al golden SSDT) |

No se tocó `_build_teradata_source`, el template, ni ninguna propiedad del
componente — el único cambio fue el contenido del `sql` en
`specs/teradata_to_flat_file_v1_e2e.json`. Esta simplificación **no resolvió**
el comportamiento inicial del editor (ver siguiente sección) — se mantiene
igualmente porque produce un E2E más simple y sin funciones específicas de
un vendor, sin ninguna desventaja.

## Known SSDT designer quirk — Teradata Source access mode

On first load of a Factory-generated package, the Teradata Source editor may
display `Table Name - TPT Export` even though the serialized component
contains `AccessMode=1`, an empty `TableName`, and the correct populated
`SqlCommand`.

Selecting `SQL command - TPT Export` once immediately reveals the already
persisted SQL. Saving the package preserves the selection on subsequent
loads.

A forensic comparison between Factory-clean and SSDT-saved packages (ver
auditoría READ-ONLY completa) found no semantic serialization difference in
the Teradata Source. A second experiment with a freshly generated package
(nunca antes abierto en SSDT), agregado al mismo proyecto, guardado y
reabierto sin tocar el Data Flow, reprodujo el mismo comportamiento inicial.
The behavior is therefore treated as a designer/UI quirk rather than a
demonstrated Factory serialization defect.

**EVIDENCE:**
- El XML generado por Factory es correcto (`AccessMode=1`, `TableName`
  vacío, `SqlCommand` poblado con el SQL esperado).
- El SQL nunca se pierde: seleccionar `SQL command - TPT Export` lo muestra
  de inmediato, sin necesidad de reescribirlo.
- El comportamiento es reproducible: ocurrió tanto en el E2E original como
  en un package `Fresh` recién generado, nunca antes abierto en SSDT.
- Comparación estructural completa (component attributes, las 20
  properties, connection manager reference, inputs/outputs, columns,
  DesignTimeProperties) entre Factory-clean y SSDT-saved: **NO
  SERIALIZATION DIFFERENCE FOUND**.

**HYPOTHESIS (no confirmada como causa factual):**
- Podría tratarse de estado interno/inicialización del diseñador SSDT (caché
  de proyecto, comportamiento de primer-render del editor custom de
  Teradata Source), externo al contenido del `.dtsx` y no auditable desde
  este repositorio.

### Workaround

1. Abrir el Teradata Source.
2. Seleccionar manualmente `SQL command - TPT Export`.
3. Verificar que aparece el SQL ya almacenado (no hace falta reescribirlo).
4. Guardar el package — la selección se conserva en aperturas posteriores.

## Principio SQL-first

Decisión de equipo, confirmada por evidencia real: **todo lo que pueda
resolverse razonablemente en el SQL de origen se resuelve allí**. Los 4
packages auditados (`DatosContactoProcesadoras`, `DatosContactoProcesadorasSemanal`,
`TarjetaDebitoSinUsoLink`, `Tokenizacion`) no usan Data Conversion ni Derived
Column en su Data Flow — el SQL Teradata hace `COALESCE`/`TO_CHAR`/`LPAD`/
`RPAD`/`SUBSTR` para producir columnas `str` de ancho fijo, listas para
escribir directamente al archivo. Por eso el pipeline estándar de esta
familia es:

```
SQL Teradata (formato/longitud ya resueltos)
        ↓
  Teradata Source
        ↓
Flat File Destination
        ↓
      archivo
```

**No** `Source → Data Conversion → Derived Column → Flat File`. Esta
decisión es de **diseño**, no una limitación técnica: la evidencia real no
muestra ningún caso donde el pipeline necesite transformar tipos antes del
Flat File Destination.

## Mapping Planner

**No se usa automáticamente en v1.** La auditoría detectó que columnas
derivadas de expresiones SQL (`TO_CHAR(fecha,'YYYYMMDD')`) reportan, vía
ODBC, una longitud "fuente" genérica del driver (ej. `str(65)`) que no
refleja el ancho real garantizado por el SQL (`8` caracteres). La regla
`UNSAFE` de `mapping_planner` (target_length < source_length) clasificaría
esos 11 mappings reales de `DatosContactoProcesadoras`/`Semanal` como
inseguros, aunque el SQL garantiza el ancho exacto — serían **falsos
positivos**. Por eso los mappings de `destination.columns[]` son siempre
**explícitos** en el spec: `mapping_planner` no se modificó, no se debilitó,
y no se usa delante de un destino `flat_file`. Esto no es una incompatibilidad
permanente — es una decisión de scope para v1, documentada como gap real
(ver "Limitaciones").

## Derived Planner

**No se usa automáticamente en v1.** Ninguno de los 4 packages auditados
contiene `Microsoft.DerivedColumn`. `derived_planner/` no se modificó ni se
amplió.

## MoveFile

Segunda decisión de equipo, también confirmada por evidencia: en 3 de los 4
packages, el Data Flow escribe el archivo a una ruta local
(`$Project::pathLocal`) y una tarea posterior (`Microsoft.FileSystemTask`,
`TaskOperationType="MoveFile"`) lo mueve a un share de red
(`$Project::pathTraspaso`). Esto **no es el patrón estándar corporativo**:
es una excepción operativa para archivos pesados (evitar escritura
prolongada directamente sobre red). La evidencia lo confirma: el cuarto
package (`TarjetaDebitoSinUsoLink`) escribe **directo** al share de red, sin
ningún paso intermedio, sin variables de package, sin File System Task —
y es un package real, productivo, completo.

Por eso `template-teradata-to-flat-file-v1` se concentra en la
**generación** del archivo (`Teradata Source → Flat File Destination`,
escribiendo al path indicado por el spec). El **movimiento/entrega**
posterior queda:

`MoveFile → DEFERRED / CONTROL FLOW CONCERN`

No se implementó `Microsoft.FileSystemTask`, ni `Microsoft.ScriptTask`, ni
`Sequence Container`, ni `Execute SQL Task`, ni `precedence constraints` —
ninguno tiene evidencia de ser imprescindible para producir el archivo. Esto
mantiene a `template-teradata-to-flat-file-v1` **independiente** de
`control-flow-v1` (`BLOCKED / EXPERIMENTAL`, sin tocar).

## Explicit metadata

Todo el schema físico del archivo (format, code page, locale, unicode,
delimitadores, columnas, mappings) es **declarativo**: el spec lo declara
por completo, el generador nunca lo infiere, nunca lo interpreta desde SQL,
nunca aplica un default silencioso para `code_page`/`locale_id`/`unicode`
(deben venir explícitos siempre, aunque los 4 packages reales compartan los
mismos 3 valores).

## Formats

| Format | Estado | Evidencia |
|---|---|---|
| `ragged_right` | **GO** | 3/4 packages (`DatosContactoProcesadoras`, `Semanal`, `Tokenizacion`) |
| `delimited` | **GO CON RESTRICCIONES** | 1/4 packages (`TarjetaDebitoSinUsoLink`, una sola columna) — implementado por ser evidenciado, de baja complejidad (mismo código de columnas) y probado de forma independiente (ver tests) |
| `fixed_width` | **OUT OF SCOPE / INSUFFICIENT EVIDENCE** | 0/4 packages |

## Holdout

**`Tokenizacion`** (19 columnas, mezcla `str`/`wstr`, sin columna sentinel
`EndLine` — el terminador de fila lo lleva la última columna real,
`Txt_Email_PIC`) se usó como holdout: reservado explícitamente, **no**
usado como referencia principal de diseño (esa fue `DatosContactoProcesadoras`
+ `TarjetaDebitoSinUsoLink`, con `DatosContactoProcesadorasSemanal` como
evidencia secundaria). Ver "Resultado del holdout" más abajo — representado
correctamente con la arquitectura ya diseñada, sin necesitar ningún cambio.

## Arquitectura implementada

```
Project
├── Project.params
└── Teradata Connection Manager (project-level, SIN CAMBIOS — ProjectContext)

Package.dtsx
├── Flat File Connection Manager (package-level, NUEVO)
└── Data Flow
      Teradata Source (reutilizado sin cambios)
            ↓
      Flat File Destination (NUEVO)
```

Confirmado por los 4 packages reales: el Flat File Connection Manager
**siempre** está embebido en el `.dtsx` (nunca un `.conmgr` externo, nunca
una referencia project-level) — evidencia suficiente para fijar en v1:
`Flat File Connection Manager = package-level`.

### Módulo nuevo: `generator/flat_file_generator.py`

Separado de `generator/campanias_generator.py` (mismo criterio que
`generator/control_flow_generator.py`: familia estructuralmente distinta —
sin Data Conversion/Derived Column, con un Connection Manager package-level
que no existe en la familia OLE DB, con un componente destino sin
`usesDispositions` ni outputs). Reutiliza directamente, por import, las
piezas genéricas de `campanias_generator.py`: `_build_teradata_source`,
`GeneratorError`, `_find_component`, `_find_dataflow_executable`,
`_make_input_column`, `_make_external_metadata_column`, `_clear_children`,
`_rewrite_design_time_properties`. **No** reutiliza `_build_dataflow_executable`/
`_rebuild_paths`/`_update_design_time_properties` (formados alrededor de la
familia OLE DB) ni `templates/campanias_base.dtsx`.

### Template nuevo: `templates/teradata_to_flat_file_base.dtsx`

Construido a partir de `Examples/Originals/MediosDePago/DatosContactoProcesadoras.dtsx`
(referencia principal), con el `Microsoft.FileSystemTask` y su precedence
constraint **removidos** (MoveFile fuera de scope), y el `ConnectionString`
cacheado del Flat File CM reemplazado por un placeholder no corporativo
(se sobrescribe siempre en generación; el valor real nunca se necesita en
el template). Nombres de columnas/SQL reales conservados (se limpian y
reconstruyen por spec en cada generación, mismo criterio que
`campanias_base.dtsx` con BipSuc).

## ProcessSpec — extensión aditiva

`data_flow.destination.type` acepta ahora `"ole_db"` (sin cambios) o
`"flat_file"` (nuevo). Distinción **estructural** (por el valor de `type`),
nunca heurística de nombres:

```yaml
data_flow:
  source: {...}                          # SIN CAMBIOS
  destination:
    type: flat_file
    name: "Destino de archivo plano"
    connection_manager:
      name: ffcNombre
      format: ragged_right               # o "delimited"
      code_page: 1252
      locale_id: 11274
      unicode: false
      header_row_delimiter: CRLF         # o "SEMICOLON"
      text_qualifier: none
      filename:
        parameter: pathLocal             # opcional
        literal: "MediosDePago\\..."     # opcional
        variable:                        # opcional
          name: nombreArchivo
          value: "archivo.txt"
    columns:                             # mapping Y schema fisico UNIFICADOS
      - source: Col1                     # ausente para columna sentinel pura
        target: Col1
        target_data_type: str            # o wstr
        target_length: 20
        target_code_page: 1252           # opcional (wstr no lo necesita)
      - ...
      - target: EndLine                  # ultima columna: SIEMPRE el terminador
        row_terminator: true
```

`destination.columns[]` es **una sola lista**, no dos declaraciones
paralelas (nunca `connection_manager.columns` + `mappings[]` por separado):
cada entrada sirve a la vez de mapping (pipeline → Flat File) y de
declaración física de columna (ancho, tipo, code page). Exactamente **una**
columna debe tener `row_terminator: true`, y debe ser la **última** de la
lista — puede ser una columna sentinel sin `source`/`target_data_type`/
`target_length` (ej. `EndLine`, patrón de `DatosContactoProcesadoras`) o una
columna real que también cierra la fila (patrón de `Tokenizacion`/
`TarjetaDebitoSinUsoLink`).

No rompe specs legacy: `destination.type=ole_db` sigue validando/generando
exactamente igual (verificado con la suite completa de
`template-teradata-to-sql-v1.1`/`mapping-planner-v1`/`derived-column-v1`).

## Ubicación conceptual del Flat File CM package-level (What/Where)

`Package Spec = WHAT`, `ProjectContext = WHERE/recursos existentes` se
mantiene: el Flat File CM **no** es un recurso preexistente del proyecto que
Factory deba descubrir (a diferencia de `cnxTeradata`, resuelto vía
`ProjectContext`) — nace con el package, así que es **WHAT**. No se creó una
sección `package_connections` nueva ni una representación paralela:
`destination.connection_manager` declara directamente su configuración,
igual criterio que `data_flow.destination` ya usa para OLE DB.

## Flat File Connection Manager — XML real

```xml
<DTS:ConnectionManager
  DTS:refId="Package.ConnectionManagers[ffcNombre]"
  DTS:CreationName="FLATFILE"
  DTS:DTSID="{...}"
  DTS:ObjectName="ffcNombre">
  <DTS:PropertyExpression DTS:Name="ConnectionString">@[$Project::pathLocal] + "..." + @[User::nombreArchivo]</DTS:PropertyExpression>
  <DTS:ObjectData>
    <DTS:ConnectionManager
      DTS:Format="RaggedRight"
      DTS:LocaleID="11274"
      DTS:HeaderRowDelimiter="_x000D__x000A_"
      DTS:RowDelimiter=""
      DTS:TextQualifier="_x003C_none_x003E_"
      DTS:CodePage="1252"
      DTS:ConnectionString="...">
      <DTS:FlatFileColumns>
        <DTS:FlatFileColumn DTS:ColumnDelimiter="" DTS:ColumnWidth="20" DTS:MaximumWidth="20" DTS:DataType="129" DTS:TextQualified="True" DTS:ObjectName="Col1" DTS:DTSID="{...}" DTS:CreationName="" />
        ...
        <DTS:FlatFileColumn DTS:ColumnType="Delimited" DTS:ColumnDelimiter="_x000D__x000A_" DTS:DataType="129" DTS:TextQualified="True" DTS:ObjectName="EndLine" DTS:DTSID="{...}" DTS:CreationName="" />
      </DTS:FlatFileColumns>
    </DTS:ConnectionManager>
  </DTS:ObjectData>
</DTS:ConnectionManager>
```

`RowDelimiter=""` **siempre** (fijo por el generador, cero variación en los
4 packages — el terminador de fila vive exclusivamente en la última
columna). `TextQualifier="_x003C_none_x003E_"` decodifica al literal
`<none>`. `HeaderRowDelimiter` tiene 2 valores evidenciados: CRLF
(`_x000D__x000A_`, 3/4 packages) y `;` (`_x003B_`, `TarjetaDebitoSinUsoLink`)
— **independiente** del `ColumnDelimiter` real del terminador, que es
**siempre CRLF** en los 4 casos (confirmado incluso en el package con
`HeaderRowDelimiter=";"`).

`Unicode` nunca aparece en la evidencia (ausente = false): v1 exige el
campo `unicode` explícito en el spec, pero **solo acepta `false`** —
`true` se rechaza explícitamente por falta de evidencia real
(`GO CON RESTRICCIONES` documentado como no implementado, no como
imposible).

## Schema de columnas — Ragged Right

Dos convenciones reales confirmadas para representar el terminador de fila
(ninguna es "la correcta", ambas son evidencia real):

1. **Columna sentinel dedicada** (`DatosContactoProcesadoras`/`Semanal`):
   una columna `EndLine` sin `ColumnWidth`/`MaximumWidth`, solo
   `ColumnType="Delimited"` + `ColumnDelimiter`. No tiene `inputColumn`
   correspondiente (asimetría real: N inputColumns vs N+1
   externalMetadataColumns) — su `externalMetadataColumn` sí lleva metadata
   (`data_type=str`, `length=255`, `code_page=1252`, valores que SSDT
   asigna por defecto a una columna sin ancho propio).
2. **Última columna real carga el terminador** (`Tokenizacion`,
   `TarjetaDebitoSinUsoLink`): la columna con datos reales recibe
   `ColumnType="Delimited"` + `ColumnDelimiter`, sin sentinel adicional.

El generador soporta ambas mediante el flag `row_terminator: true` en
`destination.columns[]`: si la columna del terminador no declara `source`/
`target_data_type`/`target_length`, se trata como sentinel pura (patrón 1);
si los declara, es una columna real que también cierra la fila (patrón 2).

Columnas NO-terminador siempre llevan `ColumnWidth == MaximumWidth ==
target_length` y `ColumnDelimiter=""` — confirmado en 4/4 packages, sin
excepción.

## Encoding / Code Page / Locale

`1252`/`11274`/`Unicode=false` son **COMMON** en los 4 packages reales, pero
**no se hardcodearon** como default: son campos obligatorios y explícitos
del spec (`code_page`, `locale_id`, `unicode`). Un futuro
`teradata-to-flat-file-profile-v1` podría fijarlos como defaults
corporativos (análogo a `teradata-to-sql-profile-v1`) — **no implementado
en este milestone**.

## str → wstr (caso Tokenizacion)

Evidencia real: en `Tokenizacion`, el `inputColumn` de una columna del Flat
File Destination mantiene `cachedDataType='str'` (el tipo real que fluye
desde Teradata Source, sin ningún Data Conversion intermedio), mientras que
su `externalMetadataColumn`/`FlatFileColumn` declara `DataType='wstr'`(130)
— SSIS hace la coerción **dentro del propio Flat File Destination**, sin
componente adicional. El generador lo soporta (`target_data_type: wstr` en
`destination.columns[]`, sin exigir ni generar Data Conversion), clasificado
**GO CON RESTRICCIONES**: implementado únicamente porque el holdout real
(`Tokenizacion`) lo necesita y la evidencia XML es clara — no se generaliza
a otra combinación de tipos sin evidencia. `mapping_planner` no se tocó.

## Reutilización de Teradata Source

100% reutilizado sin cambios: `_build_teradata_source`, resolución de
`cnxTeradata` vía `ProjectContext`, `min_sessions`/`max_sessions` opcionales
(evidenciado 4/8 en 3 packages, 1/1 en `TarjetaDebitoSinUsoLink` — ya
soportado desde `teradata-to-sql-profile-v1`), SQL command, output/error
columns con los mismos helpers.

## Resultado del holdout: Tokenizacion

Representado con la arquitectura ya diseñada (sin ningún cambio de código
posterior a haberla visto), comparado estructuralmente contra el `.dtsx`
real:

| Aspecto | Real | Generado | Coincide |
|---|---|---|---|
| Topología (componentes/paths) | Source + Flat File Dest, 1 path | idéntico | ✅ |
| Cantidad de columnas del archivo | 19 | 19 | ✅ |
| Mezcla de tipos str/wstr por columna | 4 str + 15 wstr | idéntico (mismo mapa nombre→tipo) | ✅ |
| Terminador de fila | última columna real (`Txt_Email_PIC`), sin sentinel | idéntico | ✅ |
| Flat File CM package-level | `ffcTokenizacion` | generado package-level (nombre distinto, esperado) | ✅ |
| IDs/DTSID | reales | frescos (`new_guid()`) | N/A (no se exige igualdad) |

**No se descubrió ninguna capacidad nueva no prevista** — el holdout se
representó completamente con el diseño ya construido a partir de
`DatosContactoProcesadoras`/`TarjetaDebitoSinUsoLink`. No hubo necesidad de
ampliar scope.

## Limitaciones

- `mapping_planner` no se usa delante de `flat_file` (gap real: longitud
  fuente de expresiones `TO_CHAR` no confiable, ver "Mapping Planner").
- `derived_planner` no se usa (sin evidencia de necesidad).
- `Microsoft.FileSystemTask`/MoveFile fuera de scope (`DEFERRED / CONTROL
  FLOW CONCERN`, ver "MoveFile").
- `fixed_width` fuera de scope (sin evidencia).
- `Unicode=true` fuera de scope (sin evidencia).
- `ssis_parser.py` no exponía, antes de este milestone, ninguna estructura
  de `<DTS:ConnectionManagers>` package-level ni de `FlatFileColumns` —
  **no se modificó** (no fue necesario: `_build_generic`/`extract_outputs`
  ya exponen todo lo necesario para validar este milestone vía
  `raw_properties`/`inputs`/`external_metadata_columns`). Se documenta como
  gap conocido si una futura versión necesita inspeccionar el Flat File CM
  en detalle vía IR (hoy solo es inspeccionable con `xml.etree` directo,
  como hacen los tests de este milestone).
- Column delimiter real ENTRE columnas (no solo el terminador) no tiene
  evidencia en el corpus — no implementado.
- El valor "cacheado" `DTS:ConnectionString` del Connection Manager (el que
  ve SSDT antes de evaluar la `PropertyExpression`) se resuelve con la
  mejor información disponible (valor real del project parameter si está en
  `ProjectContext` y no es sensible) pero es puramente cosmético — SSIS lo
  recalcula solo con la `PropertyExpression` en tiempo de ejecución.

## Gate SSDT — resultado final

Gate manual completo, aprobado por el usuario:

- Package abre sin errores; Data Flow reconocido.
- `ConnectionString` PropertyExpression: Expression Builder abre, la
  expresión contiene los backslashes correctamente escapados, `Evaluar
  expresión` devuelve el path Windows correcto (BUG 1 — **SSDT VALIDATED**).
- Flat File CM package-level: editor abre; RaggedRight (`Derecho
  irregular`); Locale Español Argentina; CodePage 1252; Unicode
  desactivado; HeaderRowDelimiter CRLF — todo reconocido correctamente.
- Flat File Destination: editor abre; mappings correctos
  (`Fec_Proceso_PIC→Fec_Proceso_PIC`, `Id_d_Cliente_PIC→Id_d_Cliente_PIC`,
  `Nom_Cliente_PIC→Nom_Cliente_PIC`, `<omitir>→EndLine`); sentinel `EndLine`
  reconocido.
- Teradata Source: `cnxTeradata` reconocido; seleccionando manualmente
  `SQL command - TPT Export` (ver "Known SSDT designer quirk" arriba) el
  SQL aparece correcto y persiste tras guardar/cerrar/reabrir.

## Scope final

### GO / STABLE

`TERADATA_TO_FLAT_FILE`, principio SQL-first, Teradata Source reutilizado,
Flat File Destination, Flat File Connection Manager package-level,
`ragged_right`, schema físico explícito, mappings explícitos, external
metadata, locale/code page explícitos, `Unicode=false`, delimitadores,
sentinel `EndLine`, filename strategies (literal / parameter+literal /
parameter+literal+variable), PropertyExpression estructurada sobre
`ConnectionString`, escaping correcto para SSIS Expression Language,
holdout real `Tokenizacion`, E2E sintético, parser/validator existentes,
ProjectContext para conexión Teradata.

### GO CON RESTRICCIONES

- `delimited`: exactamente con las restricciones documentadas en
  "Formats" (evidenciado en 1/4 packages, una sola columna).
- `str → wstr`: únicamente el caso interno del Flat File Destination
  evidenciado en el holdout `Tokenizacion` (ver "str → wstr").

### DEFERRED

Mapping Planner automático, Derived Planner automático, Data Conversion
automático, Derived Column automático (todos ortogonales a esta familia
por decisión SQL-first), `Microsoft.FileSystemTask`/MoveFile (ver
"MoveFile"), delivery/orchestration, Control Flow, generalización de
transformaciones de pipeline.

### OUT OF SCOPE / INSUFFICIENT EVIDENCE

`fixed_width` (0/4 packages), `Unicode=true` (0/4 packages), cualquier otra
capacidad no listada arriba.
