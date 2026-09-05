# Patrones XML observados en los paquetes de referencia

Fuentes: `Examples/Originals/BipSuc_CampaniasVigentes.dtsx` (736 líneas, Parte 1)
y `Examples/Originals/BipSuc_Turnero.dtsx` (8968 líneas, Parte 2). Todas las
referencias de línea corresponden al archivo indicado en cada sección tal como
fue leído para este análisis. Todo lo marcado como **hipótesis** es
conocimiento general de SSIS, NO respaldado por ninguno de los dos archivos, y
se lista aparte a propósito.

Cada patrón de la Parte 2 esta etiquetado como:
- **OBSERVADO EN TURNERO** — presente en Turnero, ausente en Campanias (Campanias es demasiado simple para haberlo mostrado).
- **OBSERVADO EN AMBOS** — presente en los dos archivos, reforzando la regla de la Parte 1.
- **CONFIRMA HIPÓTESIS** / **CONTRADICE HIPÓTESIS** — cuando Turnero aporta evidencia sobre algo que la Parte 1 había dejado como hipótesis abierta (§10).

---

# Parte 1 — `BipSuc_CampaniasVigentes.dtsx`

## 1. Estructura general del paquete

```
DTS:Executable (Package, DTS:ExecutableType="Microsoft.Package")   [líneas 2-16]
└─ DTS:Executables
   └─ DTS:Executable (DTS:CreationName="Microsoft.Pipeline")       [líneas 21-29]  = Data Flow Task
      └─ DTS:ObjectData
         └─ pipeline (sin namespace)                                [línea 32]
            ├─ components
            │  ├─ component componentClassID="Microsoft.DataConvert"     [líneas 35-138]
            │  ├─ component componentClassID="Microsoft.OLEDBDestination"[líneas 139-319]
            │  └─ component componentClassID="Microsoft.SSISTeradataSrc" [líneas 320-606]
            └─ paths                                                [líneas 608-619]
```

Observación importante: dentro de `<DTS:ObjectData>`, el elemento `<pipeline>`
y todo su contenido (`components`, `inputs`, `outputs`, `properties`, `paths`,
etc.) **no declara namespace** — sus atributos (`refId`, `componentClassID`,
`name`, ...) se leen sin prefijo. Solo el nivel "paquete"/"executable" usa el
namespace `DTS` (`www.microsoft.com/SqlServer/Dts`).

El orden de los `<component>` en el XML (Conversión → Destino → Origen) **no**
representa el orden de ejecución; el orden real de flujo se reconstruye
exclusivamente a partir de `<paths>`.

---

## 2. Data Flow Task

Patrón: un `DTS:Executable` con `DTS:CreationName="Microsoft.Pipeline"` (línea 23) es
un Data Flow Task. Su contenido funcional vive en `DTS:ObjectData/pipeline`.

Atributos observados en el `DTS:Executable` del Data Flow:

| Atributo | Valor observado | Clasificación |
|---|---|---|
| `DTS:refId` | `Package\Tarea Flujo de datos` | identificador dinámico — depende del nombre del objeto |
| `DTS:ObjectName` | `Tarea Flujo de datos` | configuración funcional (nombre visible) |
| `DTS:DTSID` | `{EBC1ABDC-...}` | **opaque/generated** (GUID, sin regla inferible) |
| `DTS:CreationName` | `Microsoft.Pipeline` | metadata técnica SSIS (fija para todo Data Flow Task) |
| `DTS:LocaleID` | `-1` | valor default/repetitivo |
| `DTS:TaskContact` | string largo de copyright Microsoft | metadata técnica SSIS, valor fijo repetido |

---

## 3. Teradata Source (`componentClassID="Microsoft.SSISTeradataSrc"`)

Ubicación: líneas 320-606.

### Identificación
- `componentClassID="Microsoft.SSISTeradataSrc"` (línea 322) — clave de detección, no usar `name`.
- `version="1"` (línea 327) — metadata técnica ligada a la versión del componente/engine SSIS instalado (`DTS:LastModifiedProductVersion="17.0.1010.2"` a nivel de paquete). No se puede inferir de este único archivo qué pasa con otras versiones.

### Conexión (líneas 439-446)
```xml
<connection
  refId="...Teradata Source.Connections[TeradataConnection]"
  connectionManagerID="{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}:external"
  connectionManagerRefId="Project.ConnectionManagers[cnxTeradata]"
  name="TeradataConnection" />
```
- `connectionManagerRefId` apunta a un Connection Manager **a nivel de proyecto** (`Project.ConnectionManagers[cnxTeradata]`), no definido dentro de este `.dtsx`.
- `connectionManagerID="{GUID}:external"` — el sufijo `:external` es un patrón observado (también aparece en el destino OLE DB); el GUID es **opaque/generated**, probablemente el `DTSID` real del Connection Manager en su archivo `.conmgr` de proyecto (no verificable: ese archivo no está en `Examples/Originals/`).
- **No inferible de este archivo**: proveedor, servidor, base de datos, credenciales. Son datos del Connection Manager real, ausente en la fuente analizada.

### Propiedades relevantes (`<properties>`, líneas 328-438)
| Propiedad | Valor | Clasificación |
|---|---|---|
| `SqlCommand` | Query Teradata completa (multilinea, con `CAST(...AS DATE FORMAT 'YYYY-MM-DD')`) | **configuración funcional** — el corazón del origen |
| `TableName` | `""` (vacío) | funcional pero no usado (se usa `SqlCommand`, no modo tabla) |
| `AccessMode` | `1` | funcional — modo "SQL Command" (typeConverter="AccessMode"); observado 1=SQL command, 0=OpenRowset (ver destino) — **hipótesis** de mapeo numérico→modo, no confirmable solo con estos dos valores |
| `BlockSize` | `1048576` | funcional/tuning |
| `BufferMaxSize`, `BufferMode`, `MaxSessions`, `MinSessions`, `TenacityHours`, `TenacitySleep`, `JobMaxRowSize`, `SpoolMode`, `DetailedTracingLevel`, `DataEncryption`, `DiscardLargeRow`, `ExtendedStringColumnsAllocation`, `UnicodePassThrough`, `QueryBandSessionInfo`, `DetailedTracingFile` | valores presentes | **valores default/repetitivos** típicos de instalación del driver Teradata — no cambian el comportamiento funcional del mapeo de columnas, sí el rendimiento/logging |
| `DefaultCodePage` | `1252` | configuración funcional (encoding) |

Cada `<property>` trae metadata técnica reutilizable en todos los componentes: `dataType` (tipo .NET), `description` (texto fijo de Microsoft), `expressionType="Notify"` (indica que la propiedad admite SSIS Expressions — ninguna tiene expresión asignada en este archivo), `typeConverter` (nombre de un enum interno de SSIS, p. ej. `AccessMode`, `SpoolMode`).

### Outputs (líneas 447-605)
Dos `<output>`:
1. **`Teradata Source Output`** (normal, líneas 448-542): 7 `outputColumn`, cada una con:
   - `lineageId` — string determinista: `Package\Tarea Flujo de datos\Teradata Source.Outputs[Teradata Source Output].Columns[<Nombre>]`
   - `externalMetadataColumnId` — apunta a la `externalMetadataColumn` homónima del mismo output (líneas 504-541)
   - `dataType`, `length`, `codePage` (solo en columnas `str`) — **configuración funcional** (tipos SSIS: `i4`, `wstr`, `str`)
2. **`Teradata Source Error Output`** (`isErrorOut="true"`, líneas 543-604): mismas 7 columnas + `ErrorCode`/`ErrorColumn` (`specialFlags="1"`/`"2"`). **No está conectado a ningún `<path>`** — existe como metadata por defecto de todo componente `usesDispositions="true"`, pero no se usa en este paquete.

### externalMetadataColumns (líneas 504-541)
Reflejan el esquema "externo" (columnas tal como las ve el origen antes de cualquier mapeo). En el Teradata Source, sus nombres son 1:1 con las `outputColumn` — no hay renombrado en el origen.

---

## 4. Data Conversion (`componentClassID="Microsoft.DataConvert"`)

Ubicación: líneas 35-138.

### Identificación
- `componentClassID="Microsoft.DataConvert"` (línea 37).
- No tiene `<connections>` ni `<properties>` a nivel de componente (a diferencia de Source/Destination) — toda su configuración vive a nivel de columna.

### Input (líneas 42-63)
Un único `<input>` con 2 `inputColumn` (`FEC_INICIO_TXT`, `FEC_FIN_TXT`):
```xml
<inputColumn
  cachedCodepage="1252" cachedDataType="str" cachedLength="10"
  cachedName="FEC_INICIO_TXT"
  lineageId="Package\...\Teradata Source.Outputs[Teradata Source Output].Columns[FEC_INICIO_TXT]" />
```
- `cachedXxx` = snapshot del tipo/nombre de la columna **tal como llegaba del componente upstream** en el momento en que se diseñó el paquete. Es metadata técnica de caché de diseño, no se recalcula en runtime salvo al reabrir el componente en el diseñador.
- `lineageId` en un `inputColumn` **no genera un nuevo id** — reutiliza el `lineageId` de la columna de salida del componente anterior (Teradata Source). Este es el mecanismo real de "conexión" de columnas entre componentes, independiente del `<path>` (que solo conecta inputs/outputs a nivel de componente, no columna a columna).

### Output normal — `Salida de conversión de datos` (líneas 65-114)
2 `outputColumn` (`FechaInicio_SAL`, `FechaFin_SAL`), cada una:
```xml
<outputColumn
  dataType="dbDate" errorOrTruncationOperation="Conversión"
  errorRowDisposition="FailComponent" truncationRowDisposition="FailComponent"
  lineageId="Package\...\Conversión de datos.Outputs[Salida de conversión de datos].Columns[FechaInicio_SAL]"
  name="FechaInicio_SAL">
  <properties>
    <property containsID="true" dataType="System.Int32" name="SourceInputColumnLineageID">
      #{Package\...\Teradata Source.Outputs[Teradata Source Output].Columns[FEC_INICIO_TXT]}
    </property>
    <property dataType="System.Boolean" name="FastParse">false</property>
  </properties>
</outputColumn>
```
- **Patrón `#{...}`**: una `<property containsID="true">` serializa una referencia a otro `lineageId` envolviéndola en `#{ }`. Es el único caso de este patrón observado en el archivo (una sola instancia de Data Conversion). Se documenta como observado-en-este-caso, no como regla general validada para otros componentes con `containsID="true"`.
- `errorRowDisposition="FailComponent"` / `truncationRowDisposition="FailComponent"` — **configuración funcional** (qué hacer si la conversión falla o trunca). Aquí ambos están en modo "fallar el componente", no "redirigir fila" ni "ignorar".
- `FastParse=false` — configuración funcional (rendimiento vs. sensibilidad a configuración regional).

### Output de error — `Salida de error de conversión de datos` (líneas 115-136)
`isErrorOut="true"`, con `ErrorCode`/`ErrorColumn` estándar. Mismo patrón que en el Teradata Source: presente pero **no conectado** por ningún `<path>`.

`exclusionGroup="1"` aparece en ambos outputs (normal y error) del mismo input — es el mecanismo SSIS para indicar "una fila sale por uno u otro de estos outputs, nunca ambos" (síncronos al mismo input). Es metadata técnica estructural, no configurable por el usuario directamente.

---

## 5. OLE DB Destination (`componentClassID="Microsoft.OLEDBDestination"`)

Ubicación: líneas 139-319.

### Identificación
- `componentClassID="Microsoft.OLEDBDestination"` (línea 141), `version="4"` (línea 146).

### Conexión (líneas 195-202)
Mismo patrón que Teradata Source: `connectionManagerRefId="Project.ConnectionManagers[cnxSrvBsLogSBD01]"` + `connectionManagerID="{5DA5808C-...}:external"`. Definición real fuera de este archivo.

### Propiedades relevantes (líneas 147-194)
| Propiedad | Valor | Clasificación |
|---|---|---|
| `OpenRowset` | `[dbo].[TiposCampanias]` | **configuración funcional** — tabla destino |
| `OpenRowsetVariable` | `""` | funcional, no usado (se usa OpenRowset fijo, no variable) |
| `SqlCommand` | `""` | funcional, no usado (AccessMode=0 = modo OpenRowset, no SQL) |
| `AccessMode` | `0` | funcional — modo de acceso (0 = OpenRowset directo, según observación cruzada con Teradata Source donde 1=SQL command; **hipótesis** de mapeo numérico, SSIS conocido en general pero no confirmable solo con este archivo) |
| `FastLoadOptions` | `TABLOCK,CHECK_CONSTRAINTS` | configuración funcional de carga masiva |
| `FastLoadMaxInsertCommitSize` | `2147483647` | funcional — `int32.MaxValue`, usado por SSIS como "sin commits intermedios" (**hipótesis** de significado; el XML solo confirma el valor, la semántica exacta de ese sentinel es conocimiento general de SSIS) |
| `FastLoadKeepIdentity`, `FastLoadKeepNulls` | `false` | valores default/repetitivos |
| `CommandTimeout` | `0` | valor default (0 = sin timeout) |
| `DefaultCodePage` | `1252` | funcional (encoding) |
| `AlwaysUseDefaultCodePage` | `false` | valor default |

### Input — `Entrada de destino de OLE DB` (líneas 203-293)
7 `inputColumn`, cada una con:
- `lineageId` → apunta al `lineageId` de la columna **origen real** en el pipeline (5 desde Teradata Source directo, 2 desde Data Conversion — las fechas). Este campo es la base para reconstruir los *mappings* pipeline→destino.
- `externalMetadataColumnId` → apunta a la `externalMetadataColumn` del **destino** (esquema real de la tabla SQL Server), definida en líneas 257-292.
- `cachedName`/`cachedDataType`/`cachedLength` — snapshot de diseño, igual que en Data Conversion.

Este es el patrón central de **mapping**: `inputColumn.lineageId` (de dónde viene el dato) + `inputColumn.externalMetadataColumnId` (a qué columna real de la tabla destino va) + `inputColumn.cachedName`/`name del input column` (nombre visible en el mapeo del componente). El nombre de columna destino real (`CodigoCampania`, `FechaInicio`, etc.) solo aparece en `externalMetadataColumn.name`, y es **distinto** del nombre de columna del pipeline (`COD_CAMPANIA_DIRIGIDA`, `FechaInicio_SAL`) — confirma que hay remapeo de nombres pipeline→tabla física.

### externalMetadataColumns del destino (líneas 257-292)
**Corrección importante de terminología** (versión anterior de este documento
decía "esquema físico real" — impreciso): lo que `externalMetadataColumn`
representa es la metadata que el **proveedor OLE DB expuso y que SSIS cacheó**
al configurar el componente — no necesariamente el tipo de columna literal tal
como está declarado en el `CREATE TABLE` de SQL Server. El XML por sí solo
**no dice** cuál es el tipo SQL real de la columna; solo dice qué tipo/longitud
le reportó el proveedor a SSIS en ese momento. Valores cacheados observados:
`CodigoCampania` (i4), `CodigoAccion` (i4), `FechaInicio` (wstr, length=27),
`FechaFin` (wstr, length=27), `Speech` (wstr, length=1000), `Descripcion`
(wstr, length=1000), `PublicoObjetivo` (wstr, length=1000).

Dato notable, con la fuente de cada afirmación separada: `FechaInicio`/`FechaFin`
tienen metadata externa cacheada `wstr(27)` (esto SÍ está en el XML, líneas
268-276). Según información aportada por el usuario del proyecto (NO
observable en este `.dtsx`), el tipo SQL real de esas columnas en
`dbo.TiposCampanias` es `datetime2(7)` — es decir, el proveedor OLE DB le
reportó a SSIS una representación de cadena (`wstr(27)`, longitud consistente
con `YYYY-MM-DD HH:MM:SS.fffffff`) para una columna que en la base es
`datetime2(7)`, no una columna `varchar`/`nvarchar` real. Esto confirma con
más fuerza el punto ya documentado: **el tipo físico SQL no necesariamente
coincide con el `dataType`/longitud que SSIS cachea como metadata externa** —
cualquier generador debe tomar esa metadata cacheada tal cual (o re-consultarla
contra la base real), nunca inferirla de una tabla de conversión SQL→SSIS
simplista. `FechaInicio_SAL`/`FechaFin_SAL` en el pipeline son `dbDate`; SSIS
permite el mapeo implícito `dbDate` (pipeline) → `wstr(27)` (metadata externa
del destino) sin conversión explícita adicional en el XML más allá de la ya
hecha en Data Conversion.

### Output de error (líneas 295-318)
Mismo patrón `isErrorOut="true"` + `ErrorCode`/`ErrorColumn`, no conectado por `<path>`.

---

## 6. Paths (líneas 608-619)

```xml
<path refId="...Paths[Teradata Source Output]"
      startId="...Teradata Source.Outputs[Teradata Source Output]"
      endId="...Conversión de datos.Inputs[Entrada de conversión de datos]"
      name="Teradata Source Output" />
<path refId="...Paths[Salida de conversión de datos]"
      startId="...Conversión de datos.Outputs[Salida de conversión de datos]"
      endId="...Destino de OLE DB.Inputs[Entrada de destino de OLE DB]"
      name="Salida de conversión de datos" />
```
- Un `<path>` conecta un `output` (por `refId`) con un `input` (por `refId`) — siempre a nivel de componente completo, nunca columna a columna (el mapeo columna a columna ya está resuelto vía `lineageId` en cada `inputColumn`, como se documentó arriba).
- `refId` del path = `Package\<DataFlow>.Paths[<nombre del output de origen>]` — el nombre del path es igual al `name` del output que lo origina, no un id independiente.
- Solo existen 2 paths, ambos "felices" (happy path). **No hay ningún path que conecte un `isErrorOut="true"` a otro componente** — el archivo no documenta cómo luce un error-flow real y cableado.

---

## 7. `lineageId`, `refId`, `externalMetadataColumnId`, `connectionManagerRefId` — regla observada

Los cuatro son **strings jerárquicos deterministas**, no GUIDs, construidos concatenando nombres de objetos con separadores fijos:

```
Package\<DataFlowName>\<ComponentName>.Outputs[<OutputName>].Columns[<ColumnName>]
Package\<DataFlowName>\<ComponentName>.Inputs[<InputName>].Columns[<ColumnName>]
Package\<DataFlowName>\<ComponentName>.Inputs[<InputName>].ExternalColumns[<ExtColName>]
Package\<DataFlowName>\<ComponentName>.Connections[<ConnName>]
Package\<DataFlowName>.Paths[<PathName>]
Project.ConnectionManagers[<ConnMgrName>]
```

Consecuencia directa para un generador automático: **si se conoce la jerarquía de nombres (paquete, data flow, componente, input/output, columna), estos identificadores son 100% reconstruibles sin necesidad de generar nada al azar.** Esta es la única clase de "identificador dinámico" en el archivo que es determinística por regla de nombres.

En cambio, `DTS:DTSID` (a nivel de Package y de cada Executable) es un GUID **opaque/generated** — no hay ninguna evidencia en el archivo de cómo se deriva; se asume generado aleatoriamente por el diseñador de SSIS al crear el objeto. Lo mismo aplica al GUID embebido en `connectionManagerID="{GUID}:external"`.

---

## 8. Metadata de diseño (no funcional)

`DTS:DesignTimeProperties` (líneas 624-735, dentro de un `CDATA`) contiene únicamente coordenadas (`TopLeft`, `Size`), curvas de las flechas del diagrama y una anotación visual ("Se agrega variable de entorno..."). No afecta el comportamiento en runtime — el propio comentario del XML generado por SSIS lo dice explícitamente (línea 627). Se debe **separar completamente** de la lógica de migración: es candidato a regenerarse automáticamente (auto-layout) en cualquier paquete generado, no a traducirse desde DataStage.

---

## 9. Resumen de clasificación

| Categoría | Ejemplos |
|---|---|
| **Configuración funcional** | `SqlCommand` (Teradata), `OpenRowset`, `AccessMode`, `FastLoadOptions`, tipos/longitudes de columna, `errorRowDisposition`, mapeos input↔external metadata |
| **Metadata técnica SSIS** | `componentClassID`, `version`, `usesDispositions`, `exclusionGroup`, `synchronousInputId`, `dataType`/`description`/`expressionType` de cada `<property>` |
| **Valores default/repetitivos** | `CommandTimeout=0`, `FastLoadKeepIdentity/Nulls=false`, `DefaultCodePage=1252`, textos de copyright en `contactInfo`/`TaskContact`, casi todas las propiedades de tuning del driver Teradata |
| **Identificadores deterministas (reconstruibles por regla de nombres)** | `refId`, `lineageId`, `externalMetadataColumnId`, `connectionManagerRefId`, nombre de `<path>` |
| **Opaque/generated (sin regla inferible)** | `DTS:DTSID` (Package y cada Executable), GUID dentro de `connectionManagerID="{...}:external"`, `DTS:VersionGUID` |
| **Fuera del alcance de este archivo** | Definición real de los Connection Managers (`cnxTeradata`, `cnxSrvBsLogSBD01`): proveedor, servidor, cadena de conexión — viven en archivos de proyecto no incluidos |

---

## 10. Hipótesis generales de SSIS (NO respaldadas por este archivo — separadas a propósito)

Estas afirmaciones son conocimiento general de SSIS que **no se puede confirmar ni refutar** con un único archivo de ejemplo tan simple; se listan aparte para no mezclarlas con lo observado:

- Que `AccessMode=0` siempre signifique "OpenRowset" y `AccessMode=1` siempre "SQL Command" en *todos* los componentes OLE DB/ADO — aquí se observó consistente entre Source y Destination, pero son componentes distintos (`Microsoft.SSISTeradataSrc` vs `Microsoft.OLEDBDestination`), cada uno con su propio enum `AccessMode`; no se debe asumir que el mapeo numérico es idéntico entre tipos de componente sin verificarlo contra la documentación de cada uno.
- Que `containsID="true"` en cualquier `<property>` siempre implica el formato `#{lineageId}` — solo se vio en `SourceInputColumnLineageID` de `Microsoft.DataConvert`.
- Cómo luce un `<path>` que conecta un `isErrorOut="true"` a un destino de error real (Lookup con "redirect row", por ejemplo) — no hay ningún ejemplo cableado en este archivo.
- Cómo se comportan estos patrones con componentes de fan-in/fan-out (Merge, Union All, Multicast, Lookup) — no presentes en este archivo.
- Reglas de compatibilidad de `version` de componente entre distintas versiones de SQL Server/Visual Studio — no verificable con una sola versión de motor (`17.0.1010.2`).

---

# Parte 2 — `BipSuc_Turnero.dtsx`

Paquete real, mucho más complejo: 3 Data Flow Tasks, 2 Sequence Containers, 7
Execute SQL Task, 8 Precedence Constraints a nivel Package + 2 a nivel
container, 3 variables de paquete. Confirma varias reglas de la Parte 1 y
aporta patrones enteramente nuevos (Control Flow, 5 componentClassID nuevos).

## 11. Control Flow — ausente por completo en Campanias

**OBSERVADO EN TURNERO.** Campanias tiene un único `DTS:Executable` hijo del
Package (el Data Flow Task), sin containers ni precedence constraints. Turnero
tiene una jerarquía real:

```
Package
├─ DTS:Variables (3 variables, ver §14)
├─ Executables (nivel Package)
│  ├─ Execute SQL Task × 7  (Confirma transacción[,1], Inicia Transacción[,1],
│  │                          Revierte todo[,1], Crear #ClientesStage)
│  ├─ Sequence Container "Sequence Container"      (DISABLED, líneas 172-3051)
│  │  ├─ Data Flow Task "Tarea Flujo de datos 1"   (líneas 183-3023)
│  │  ├─ Execute SQL Task "Trunca tabla"           (línea 3024)
│  │  └─ PrecedenceConstraints (1 constraint interno)
│  ├─ Sequence Container "Sequence Container 1"    (activo, líneas 3052-3107)
│  │  ├─ Execute SQL Task "Inserta Destino"
│  │  ├─ Execute SQL Task "Trunca tabla"
│  │  └─ PrecedenceConstraints (1 constraint interno)
│  ├─ Data Flow Task "Tarea Flujo de datos"        (DISABLED, líneas 3108-5208, huérfano — ver §17)
│  └─ Data Flow Task "Tarea Flujo Staging"         (activo, líneas 5209-8050)
└─ PrecedenceConstraints (nivel Package, 8 constraints, líneas 8052-8119)
```

Regla de deteccion confirmada: un Data Flow Task se identifica por
`DTS:ExecutableType="Microsoft.Pipeline"` (equivalente a `DTS:CreationName` en
los casos vistos, pero `ExecutableType` es el atributo semánticamente correcto
y es el que usa ahora el parser). `root.iter()` sobre `DTS:Executable`
encuentra los tres Data Flow Tasks sin importar que uno esté anidado dentro de
un Sequence Container — confirmado contra este archivo.

## 12. Execute SQL Task (`DTS:ExecutableType="Microsoft.ExecuteSQLTask"`)

**OBSERVADO EN TURNERO.** Tarea de Control Flow (no de Data Flow). Ejemplo
(líneas 49-66):

```xml
<DTS:Executable DTS:refId="Package\Confirma transacción" DTS:CreationName="Microsoft.ExecuteSQLTask"
    DTS:ExecutableType="Microsoft.ExecuteSQLTask" DTS:ObjectName="Confirma transacción"
    DTS:Disabled="True" DTS:ThreadHint="0" ...>
  <DTS:Variables />
  <DTS:ObjectData>
    <SQLTask:SqlTaskData
      SQLTask:Connection="{69CB5656-2C95-41A4-AB24-87D08E85CF15}"
      SQLTask:SqlStatementSource="IF @@TRANCOUNT &gt; 0&#xA;    COMMIT TRAN;"
      xmlns:SQLTask="www.microsoft.com/sqlserver/dts/tasks/sqltask" />
  </DTS:ObjectData>
</DTS:Executable>
```

Clasificación:
- `SQLTask:SqlStatementSource` (texto SQL) — **configuración funcional**.
- `SQLTask:Connection="{GUID}"` — **identificador determinista solo a medias**: es un GUID "pelado" (sin `:external`, sin `Project.ConnectionManagers[nombre]`). Dentro de este `.dtsx` en aislamiento sería **opaque/generated**. Sin embargo, ese mismo GUID (`{69CB5656-...}`) aparece también como `connectionManagerID="{69CB5656-...}:external"` en las conexiones de `Microsoft.OLEDBSource`/`Microsoft.OLEDBDestination` de los tres Data Flows de este paquete, junto con `connectionManagerRefId="Project.ConnectionManagers[cnxSrvTurnosDb]"`. Cruzando ambas fuentes DENTRO del mismo archivo se puede resolver el nombre real del Connection Manager para los 7 Execute SQL Task — implementado en el parser (`build_connection_guid_index` + `resolve_execute_sql_task_connections`), confirmado: los 7 resuelven a `cnxSrvTurnosDb`.
- `DTS:Disabled="True"` — **configuración funcional**, nueva (no aparecía en Campanias, donde ningún Executable estaba deshabilitado). 4 de los 7 Execute SQL Task de Turnero (más un Sequence Container y un Data Flow Task) están deshabilitados.
- `DTS:ThreadHint` — **metadata técnica del motor** (hint de asignación de hilos), nueva, sin evidencia de impacto funcional en este archivo.

## 13. Sequence Container (`DTS:ExecutableType="STOCK:SEQUENCE"`)

**OBSERVADO EN TURNERO.** Container de Control Flow: agrupa sus propios
`DTS:Executables` y `DTS:PrecedenceConstraints` hijos, con la MISMA forma que
a nivel Package (líneas 171-3051 y 3052-3107). No tiene `DTS:ObjectData`
propio — su "contenido" son los executables anidados. Confirma que el modelo
de Control Flow es recursivo: un container puede aparecer en cualquier nivel,
y el parser lo trata igual que al nivel Package (mismo `extract_precedence_constraints`, misma recursión de `parse_executable`).

## 14. Variables de paquete (`DTS:Variables` con contenido)

**OBSERVADO EN TURNERO.** En Campanias `<DTS:Variables />` está vacío en todos
lados. En Turnero, el Package trae 3 variables reales (líneas 19-47):

```xml
<DTS:Variable DTS:Namespace="User" DTS:ObjectName="CantNIPAmbos" DTS:DTSID="{...}" DTS:IncludeInDebugDump="6789">
  <DTS:VariableValue DTS:DataType="20">0</DTS:VariableValue>
</DTS:Variable>
```

- `DTS:Namespace` + `DTS:ObjectName` — **identificador determinista**: se referencian desde otros lugares del paquete como `"<Namespace>::<ObjectName>"` (ver §16, RowCount). Confirmado exactamente con las 3 variables (`User::CantNIPAmbos`, `User::CantNIPSoloDestino`, `User::CantNIPSoloOrigen`).
- `DTS:VariableValue` (texto="0") — **configuración funcional**: valor inicial de la variable.
- `DTS:VariableValue/@DTS:DataType="20"` — **hipótesis no confirmada**: coincide con el código de tipo VARIANT de OLE Automation para un entero (conocimiento general de SSIS/COM), pero este archivo por sí solo no permite confirmar la tabla completa de códigos; se documenta el valor observado (20) sin afirmar su significado como hecho verificado.
- `DTS:IncludeInDebugDump` — **metadata técnica**, sin evidencia de impacto funcional; valor idéntico (`6789`) en las 3 variables.
- `DTS:DTSID` de la variable — **opaque/generated**, igual criterio que el resto de los DTSID.

## 15. Precedence Constraints (`DTS:PrecedenceConstraints` / `DTS:PrecedenceConstraint`)

**OBSERVADO EN TURNERO.** Conectan Executables HERMANOS dentro de un mismo
nivel (Package o container). Ejemplo con y sin `DTS:Value` (líneas 8077-8094):

```xml
<DTS:PrecedenceConstraint DTS:From="Package\Sequence Container" DTS:To="Package\Confirma transacción"
    DTS:LogicalAnd="True" DTS:ObjectName="Constraint 1" />
<DTS:PrecedenceConstraint DTS:From="Package\Sequence Container" DTS:To="Package\Revierte todo"
    DTS:LogicalAnd="True" DTS:ObjectName="Constraint 2" DTS:Value="1" />
```

- `DTS:From` / `DTS:To` — **identificadores deterministas**: son los mismos `refId` jerárquicos de la Parte 1, aplicados a Executables en vez de columnas/outputs.
- `DTS:LogicalAnd="True"` en las 10 constraints del archivo (8 a nivel Package + 2 a nivel container) — **valor default/repetitivo** en este archivo; no hay ningún caso con `False` para contrastar qué cambia.
- `DTS:Value="1"` — presente solo en las 2 constraints que van hacia "Revierte todo"/"Revierte todo 1" (rollback). **Hipótesis, no confirmada por el archivo**: coincide con la semántica general de SSIS donde la ausencia de `DTS:Value` implica éxito (0) y `1` implica fallo — es coherente con que ambas apunten a tareas de rollback, pero el archivo no trae ningún comentario o metadato que lo confirme explícitamente; se documenta como lectura razonable, no como hecho verificado.
- `DTS:refId` de la constraint sigue el patrón `<Container>.PrecedenceConstraints[<Nombre>]` — **identificador determinista**, mismo estilo que `Paths[...]` de la Parte 1.

## 16. Nuevos `componentClassID` de Data Flow

**OBSERVADO EN TURNERO.** Cinco tipos de componente que no aparecían en
Campanias, encontrados en los 3 Data Flow Tasks de este archivo:

| componentClassID | Tipo asignado | Rol observado |
|---|---|---|
| `Microsoft.DerivedColumn` | `derived_column` | Calcula una columna nueva por fila a partir de una expresión SSIS |
| `Microsoft.MergeJoin` | `merge_join` | Combina dos entradas ORDENADAS (INNER/LEFT/FULL) |
| `Microsoft.ConditionalSplit` | `conditional_split` | Enruta cada fila a una de varias salidas según una expresión |
| `Microsoft.OLEDBSource` | `ole_db_source` | Origen OLE DB (además del Teradata Source ya conocido) |
| `Microsoft.RowCount` | `row_count` | Cuenta filas y escribe el conteo en una variable de paquete |

### 16.1 Derived Column
La expresión vive **por columna de salida**, dentro de `<outputColumn><properties>` (líneas 3161-3173):
```xml
<outputColumn name="PlasticosUdn_Sal" dataType="wstr" length="1">
  <properties>
    <property containsID="true" name="Expression">[ISNULL](#{...Columns[PLASTICOSUDN]}) ? NULL(DT_WSTR,1) : (DT_WSTR,1)#{...Columns[PLASTICOSUDN]}</property>
    <property containsID="true" name="FriendlyExpression" expressionType="Notify">ISNULL(PLASTICOSUDN) ? NULL(DT_WSTR,1) : (DT_WSTR,1)PLASTICOSUDN</property>
  </properties>
</outputColumn>
```
- `Expression` / `FriendlyExpression` — **configuración funcional**, el par central de este componente: `Expression` es la forma "de máquina" (con lineageIds embebidos), `FriendlyExpression` es la forma legible (con nombres de columna). **CONFIRMA HIPÓTESIS §10**: el patrón `#{...}` con `containsID="true"` SÍ se generaliza más allá de `SourceInputColumnLineageID` de Data Conversion — acá aparece dentro de una expresión más larga, potencialmente más de una vez (la misma columna referenciada dos veces en este ejemplo).
- No hay `<properties>` a nivel de `<component>` — toda la configuración es por columna. Sin `<connections>` (es una transformación pura, no toca ninguna base de datos).

### 16.2 Merge Join
`<component>` con propiedades (líneas 3210-3228): `JoinType` (int, typeConverter="JoinType", valor `0` en este archivo — **hipótesis no confirmada** sobre qué join representa cada valor, solo se observó uno), `NumKeyColumns=1`, `TreatNullsAsEqual=true`, `MaxBuffersPerInput=5`. Dos `<input>` (izquierda/derecha), cada uno con exactamente 1 `inputColumn` marcada `cachedSortKeyPosition="1"` — **nuevo atributo, configuración funcional**: posición de esa columna en la clave de ordenación que Merge Join exige como precondición. El `<output>` trae `isSorted="true"` (nuevo atributo a nivel output) y sus `outputColumn` incluyen `sortKeyPosition` y una property `InputColumnID` (`containsID="true"`, con un único `#{...}`) que indica de cuál de los dos inputs vino cada columna de salida.

### 16.3 Conditional Split
Sin `<properties>` a nivel de componente. La lógica vive **por output**, no por columna (rutea la fila completa) — líneas 3505-3601:
```xml
<output name="NIP_Ambos" ...>
  <properties>
    <property containsID="true" name="Expression">![ISNULL](#{...Columns[NIP_Origen]}) &amp;&amp; ![ISNULL](#{...Columns[Nip_Destino]})</property>
    <property containsID="true" name="FriendlyExpression" expressionType="Notify">!ISNULL(NIP_Origen) &amp;&amp; !ISNULL(Nip_Destino)</property>
    <property name="EvaluationOrder">0</property>
  </properties>
</output>
...
<output name="Salida predeterminada de división condicional" ...>
  <properties><property name="IsDefaultOut">true</property></properties>
</output>
```
- `EvaluationOrder` (int) — **configuración funcional**: orden en que se evalúan las condiciones.
- `IsDefaultOut="true"` — **configuración funcional**: marca la rama "else" (sin `Expression`, sin `outputColumns` propias).
- Los outputs de una condición NO redeclaran `outputColumns` (ni el default ni las condicionales) — solo el output de error las tiene (`ErrorCode`/`ErrorColumn`, mismo patrón que en todos los demás componentes con `usesDispositions="true"`). Esto es coherente con ser una transformación "row-routing" (columnas idénticas a las del input, solo cambia el output por el que sale la fila).

### 16.4 OLE DB Source
Estructura casi simétrica a OLE DB Destination de la Parte 1 (mismas properties: `OpenRowset`, `OpenRowsetVariable`, `SqlCommand`, `AccessMode`), más una nueva `SqlCommandVariable` (vacía en este archivo). **CONTRADICE HIPÓTESIS §10**: aquí `AccessMode=2` mientras usa `SqlCommand` (línea 3671), distinto del `AccessMode=1` que Teradata Source usaba para lo mismo — confirma que el mapeo numérico de `AccessMode` NO es universal entre `componentClassID`, es un enum propio de cada componente. La hipótesis de la Parte 1 sobre esto queda formalmente refutada como regla general (aunque cada componente sea internamente consistente).

### 16.5 Row Count
El componente más simple observado: una sola property (`VariableName`, valor `"User::CantNIPAmbos"` — **configuración funcional**, referencia una variable de paquete por su nombre calificado `Namespace::ObjectName`, confirmado 1:1 contra las 3 variables de §14). Su `<input>` no declara `<inputColumns>` en absoluto (pass-through total, sin tocar columnas) y su `<output>` tampoco. Es un componente terminal legítimo de un Data Flow: el pipeline "Tarea Flujo de datos" de este archivo no tiene ningún `Microsoft.OLEDBDestination` — termina en tres `Microsoft.RowCount`.

## 17. Executable deshabilitado y estructuralmente aislado del Control Flow

**OBSERVADO EN TURNERO — hallazgo puntual, no un patrón XML nuevo per se.**
`Package\Tarea Flujo de datos` (el Data Flow más complejo: Merge Join +
Conditional Split + Row Count) tiene `DTS:Disabled="True"` y **no aparece como
`From` ni `To` en ningún `DTS:PrecedenceConstraint` del archivo** (ver la
lista completa en §15) — son dos hechos independientes que el XML documenta
por separado. Es un dato real del paquete (probablemente lógica de
reconciliación retirada de producción pero no borrada del `.dtsx`), no un
patrón a generalizar.

**Corrección importante** (ver `docs/campanias_vs_turnero.md`, sección
"Corrección de diseño"): el aislamiento estructural (sin ningún
`PrecedenceConstraint` que lo mencione) **no implica por sí solo** que un
executable no vaya a ejecutarse — un Control Flow puede tener varias raíces
independientes que arrancan en paralelo sin ningún constraint. Lo que
efectivamente saca a `Tarea Flujo de datos` de la ejecución es su propio
`DTS:Disabled="True"`, un hecho nativo del XML. El parser expone ambos datos
por separado (`enabled` nativo, `structurally_isolated` derivado) para no
mezclarlos. El validador reporta el aislamiento como warning estructural
(`structurally_isolated_executable`, solo cuando hay más de un executable en
el nivel) sin afirmar que sea código muerto — ver `ssis_validator.py`.

## 18. Confirmado / nuevo / refutado gracias a Turnero

| # | Ítem | Antes (solo Campanias) | Después de Turnero |
|---|---|---|---|
| 1 | `refId`/`lineageId`/`externalMetadataColumnId` deterministas por nombre | Observado | **Confirmado** en 3 Data Flows distintos, incluida jerarquía con container |
| 2 | `#{...}` con `containsID="true"` = referencia a otro objeto | Solo en `SourceInputColumnLineageID` (1 instancia) | **Confirmado y generalizado**: aparece embebido dentro de expresiones más largas, más de una vez, en Derived Column/Conditional Split/Merge Join |
| 3 | `AccessMode` numérico universal entre componentes OLE DB | Hipótesis abierta | **Refutada**: mismo nombre de property, enum distinto por `componentClassID` (Teradata Source AccessMode=1 = SQL Command; OLE DB Source AccessMode=2 = SQL Command) |
| 4 | Existencia de error outputs no cableados | Observado (3/3 sin usar) | Sigue sin verse un error output cableado a un `<path>` real — **hipótesis sigue abierta** |
| 5 | Connection Managers fuera del `.dtsx` | Limitación aceptada | Sigue siendo cierto para el CONTENIDO del Connection Manager, pero se descubrió que su NOMBRE puede cruzarse entre Control Flow (GUID pelado) y Data Flow (`connectionManagerID`) dentro del mismo archivo |
| 6 | Todo Data Flow termina en un destino (`OLEDBDestination`) | Cierto en el único caso visto | **Refutado**: `Row Count` es un terminal legítimo sin destino de base de datos |
| 7 | Un `.dtsx` es siempre un único Data Flow sin Control Flow propio | Cierto en el único caso visto | **Refutado**: Turnero tiene Control Flow completo (tareas SQL, containers, precedence constraints, variables) |
| 8 | `DTS:Value="1"` en PrecedenceConstraint = fallo | No aplicable (sin PrecedenceConstraints) | Observado consistente con esa lectura, pero **sigue como hipótesis** (el archivo no lo confirma explícitamente) |
| 9 | Semántica exacta de `DTS:VariableValue/@DataType` (código `20`) | No aplicable | **Sigue como hipótesis abierta** — un solo código observado, sin tabla de referencia en el archivo |
| 10 | `AccessMode` consistente entre instancias del MISMO `componentClassID` | No verificable (1 sola instancia de OLEDBDestination) | **Refutada de nuevo, más fuerte**: los 2 `Microsoft.OLEDBDestination` de Turnero usan `AccessMode="3"`, el de Campanias usa `AccessMode="0"` — misma clase de componente, mismo uso observable de `OpenRowset`, valores distintos. Ver `docs/campanias_vs_turnero.md` |

## 19. `DTS:Disabled`, `DelayValidation`, `validateExternalMetadata` — habilitación y validación diferida

**OBSERVADO EN TURNERO**, encontrados al reanalizar el paquete para
reconstruir su arquitectura activa (ver `docs/campanias_vs_turnero.md`,
sección "Arquitectura activa de Turnero"):

- `DTS:Disabled="True"` en un `DTS:Executable` — **configuración funcional**,
  ya documentado en §12, pero con un matiz importante confirmado ahora: NO se
  hereda automáticamente en el XML a los Executables hijos de un container.
  `Package\Sequence Container` tiene `DTS:Disabled="True"`, pero su hijo
  `Package\Sequence Container\Tarea Flujo de datos 1` (y `Trunca tabla`) NO
  tienen el atributo (equivale a `"False"`). El motor SSIS efectivamente no
  los ejecuta igual (conocimiento general, no confirmable solo con el XML),
  pero el **dato crudo por nodo** dice que no están deshabilitados — de ahí
  la necesidad de un campo derivado (`effective_enabled`) separado del dato
  nativo (`enabled`). Ver `ssis_parser.annotate_execution_roles`.
- `DTS:DelayValidation="True"` — atributo de `DTS:Executable`, encontrado
  **una sola vez en todo el corpus** (los dos archivos), en
  `Package\Tarea Flujo Staging` (línea 5211). **Configuración funcional**:
  le indica a SSIS que no valide la metadata de ese Data Flow Task al abrir
  el paquete — necesario porque su destino apunta a `#ClientesStage`, una
  tabla temporal que todavía no existe en ese momento (se crea en un paso
  anterior del Control Flow, `Crear #ClientesStage`).
- `validateExternalMetadata="False"` — atributo DIRECTO del elemento
  `<component>` (NO una `<property>` dentro de `<properties>`), encontrado
  **una sola vez en todo el corpus**, en el `Microsoft.OLEDBDestination` de
  `Tarea Flujo Staging` (línea 5825). **Configuración funcional**, y trabaja
  en conjunto con `DelayValidation` de arriba: le dice específicamente a ESE
  componente que no valide sus columnas contra el esquema real de
  `#ClientesStage`. Ningún otro componente del corpus (ni en Campanias ni en
  el resto de Turnero) trae este atributo explícito — no hay evidencia de
  cuál es el valor por default cuando se omite (ausencia y `"False"` explícito
  podrían no significar lo mismo; se documenta el valor observado, no se
  asume un default).

Ambos atributos nuevos ya se extraen en el parser: `delay_validation` en cada
nodo de `control_flow` y `validate_external_metadata` en cada componente de
Data Flow.
