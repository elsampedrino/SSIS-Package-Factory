# Generador MVP — diseño e implementación (solo BipSuc_CampaniasVigentes)

Este documento se escribió originalmente **antes** de tocar código de
generación (diseño aprobado por el usuario), y se actualizó después con el
resultado real de la implementación. Alcance: exclusivamente el caso simple
de Campanias (Teradata Source → Data Conversion → OLE DB Destination).
Turnero queda fuera a propósito.

> **Nota de actualización (project-context-v1)**: todo lo que este
> documento describe abajo sobre `KNOWN_CONNECTION_MANAGERS`
> (diccionario hardcodeado de GUID) refleja el diseño ORIGINAL de este
> milestone, ya **reemplazado**. Ese diccionario fue eliminado del código;
> la resolución de `connectionManagerID` ahora pasa siempre por un
> `ProjectContext` real (`project_context/`). Ver `docs/project_context.md`,
> sección 6, para el detalle de la migración. Se conserva el texto original
> abajo como registro histórico de la decisión de diseño inicial.

## 🏁 MILESTONE — Generator MVP Campanias E2E

**STATUS: VALIDATED END-TO-END IN REAL SSIS / SSDT.**

Este hito quedó confirmado manualmente por el usuario, fuera de este entorno,
en Visual Studio/SSDT contra el proyecto SSIS real `BipSuc`. Registro formal:

| Campo | Valor |
|---|---|
| Fecha del hito | Confirmada por el usuario en esta iteración (ver fecha de la conversación) |
| Paquete generado | `CampaniasGenerado.dtsx` |
| Spec de origen | `specs/campanias_generated.json` |
| Proyecto SSIS usado | `BipSuc` → carpeta `Paquetes SSIS` |
| Apertura en SSDT | Correcta — el diseñador reconstruyó visualmente Teradata Source → Conversión de datos → Destino de OLE DB |
| Resolución de Project Connection Managers | Correcta (`cnxTeradata`, `cnxSrvBsLogSBD01`, y `cnxSrvTurnosDb` del mismo proyecto) — solo después de ubicar el paquete DENTRO del proyecto (ver lección aprendida abajo) |
| Ejecución | Teradata Source SUCCESS, Data Conversion SUCCESS, OLE DB Destination SUCCESS |
| Filas transferidas | 1 fila |
| Verificación posterior | Confirmada directamente en SQL Server **QA** (no producción) |

Circuito completo validado experimentalmente:

```
process_spec.json → SSIS Package Factory (generator) → CampaniasGenerado.dtsx
  → Visual Studio/SSDT → Project Connection Managers reales
  → Teradata Source → Data Conversion → OLE DB Destination → SQL Server QA
```

**Niveles de validación:**

| Nivel | Qué valida | Estado |
|---|---|---|
| Nivel 1 | Automático: `process_spec → generator → ssis_parser → ssis_validator` | ✅ Completado (ver más abajo) |
| Nivel 2 | Real en Visual Studio/SSDT: apertura, diseñador, resolución de conexiones | ✅ **Completado con éxito** (confirmado por el usuario) |
| Nivel 3 | Ejecución real contra QA + verificación de datos en SQL Server | ✅ **Completado con éxito** (1 fila, confirmada en QA) |

No se afirma deployment a producción — la ejecución confirmada fue en **QA**.

### Lección aprendida: `Project.ConnectionManagers[...]` requiere contexto de proyecto

Al agregar `CampaniasGenerado.dtsx` por error como **"Elemento de la
solución"** (fuera del proyecto `BipSuc`), el paquete abría y mostraba
correctamente el Data Flow, pero SSDT no podía resolver los Connection
Managers de proyecto, con un error del tipo:

```
The connection "{GUID}" is not found.
```

**Esto NO es un defecto del `.dtsx` generado.** Un `connectionManagerID="{GUID}:external"` es una referencia a un Connection Manager que vive a nivel de PROYECTO (`.conmgr`/`.dtproj`), no dentro del propio `.dtsx` (ver `docs/xml_patterns.md` §3 y §5) — SSDT solo puede resolverlo cuando el paquete está cargado como parte del proyecto que declara esos Connection Managers. Al mover el paquete a `BipSuc → Paquetes SSIS` (dentro del proyecto correcto), el error desapareció y las tres conexiones (`cnxTeradata`, `cnxSrvBsLogSBD01`, `cnxSrvTurnosDb`) se resolvieron correctamente.

**Implicancia para pruebas futuras**: cualquier paquete que referencie
`Project.ConnectionManagers[...]` (generado por este proyecto o no) debe
probarse SIEMPRE dentro del proyecto SSIS correspondiente — nunca como
archivo suelto ni como "Elemento de la solución". Esto no es una limitación
del generador; es una propiedad del modelo de Project Connection Managers de
SSIS en sí.

---

## Estado técnico: implementado y verificado

- `generator/xml_helpers.py`, `generator/spec_validator.py`, `generator/campanias_generator.py`.
- `specs/campanias_generated.json` — process_spec funcional de Campanias.
- `templates/campanias_base.dtsx` — copia exacta de `BipSuc_CampaniasVigentes.dtsx`, usada como template validado.
- `CampaniasGenerado.dtsx` — paquete generado, en la raíz del repo.
- `tests/test_generator_campanias.py` — 25 tests (Nivel 1 end-to-end, un caso con nombres renombrados, y 13 casos de validación fail-fast).
- **Resultado (regresión automática)**: `python -m unittest discover -s tests` → **60/60 tests en verde** (35 de la fase de parser/validator + 25 del generador). `CampaniasGenerado.dtsx` reparseado con `ssis_parser` + `ssis_validator` → `valid=True, errors=0, warnings=0`, con 1 Data Flow, 3 componentes, 2 conversiones y 7 mappings, todos resueltos e idénticos en forma a `BipSuc_CampaniasVigentes.dtsx` (con identidad propia: `DTS:DTSID`/`DTS:VersionGUID` nuevos, `DTS:ObjectName="CampaniasGenerado"`).
- **Resultado (real, manual)**: ver sección de Milestone arriba — validado end-to-end en SSDT contra QA.

## 1. Separación de capas

```
IR de análisis (analysis_output.json)   — lo que el parser EXTRAE de un .dtsx real
        ≠
process_spec funcional (specs/*.json)   — la INTENCIÓN funcional, limpia, estable, legible
        ≠
XML SSIS generado (*.dtsx)              — lo que efectivamente abre SSIS
```

El generador NUNCA lee `analysis_output.json` directamente. Recibe un
`process_spec` propio (ver estructura abajo), deliberadamente más chico y sin
metadata técnica de SSIS (sin `raw_properties`, sin GUIDs, sin `ref_id`
crudos). El IR de análisis se usó para *aprender* qué metadata hace falta
completar, no como fuente de datos en runtime del generador.

## 2. Estrategia de generación elegida

**Template XML validado + modificación programática sobre árbol
(`xml.etree.ElementTree`), no string-replace.**

Comparación:

| Opción | Evaluación |
|---|---|
| XML directo desde cero | Riesgo alto: docenas de atributos "default/repetitivos" documentados en `xml_patterns.md` (`TaskContact`, `LocaleID`, `PackageFormatVersion`, esqueleto de `DesignTimeProperties`, etc.) cuya necesidad exacta para que SSIS abra el paquete sin error no está confirmada — no hay motor SSIS en este entorno para validar la hipótesis. |
| **Template + modificación de nodos** | **Elegida.** Partimos de un archivo que sabemos que abre y corre en producción. Todo lo que no se toca explícitamente permanece válido por construcción. Continúa la misma disciplina del parser: mismo árbol, ahora escrito en vez de leído. |
| SSIS Object Model / Biml | Correcta para una fase posterior (Turnero, topologías arbitrarias), pero requiere runtime .NET/SSIS o BimlExpress — fuera del alcance de Python puro de este proyecto y de lo que se puede validar desde este entorno. |

Reglas operativas del template:
- Parsear como árbol (nunca regex/string-replace sobre el XML).
- Modificar atributos/texto de nodos puntuales; clonar sub-árboles (`copy.deepcopy` de un `Element`) para estructuras repetibles (una `outputColumn` de conversión, un `inputColumn`+`externalMetadataColumn` de mapping) en vez de escribir XML a mano.
- IDs deterministas reconstruidos con la MISMA fórmula que ya usa/valida `ssis_parser.py` (ver §7 de `xml_patterns.md`) — no se inventa un esquema nuevo de nombres.
- GUIDs nuevos únicamente donde el patrón es opaque/generated (ver §5).
- Preservar namespaces (`DTS`, y el default sin-namespace del `<pipeline>`) tal como los declara el template.
- No depender de números de línea del template (se navega por tag/atributo, no por posición).
- No depender de nombres localizados salvo que el `process_spec` los pida explícitamente (el spec es la única fuente de nombres funcionales).

## 3. `connectionManagerID="{GUID}:external"` — Connection Managers

El `.dtsx` de Campanias NO define sus Connection Managers (son de proyecto:
`Project.ConnectionManagers[cnxTeradata]` / `[cnxSrvBsLogSBD01]`), pero SÍ
contiene, como referencia, sus GUID reales:

- `cnxTeradata` → `{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}`
- `cnxSrvBsLogSBD01` → `{5DA5808C-8489-48AC-8614-CB034DE02B61}`

**Decisión para este MVP**: reutilizar estos GUID literalmente, copiados del
template, porque el paquete generado está pensado para agregarse al MISMO
proyecto SSIS (autorizado explícitamente por el usuario). No es invención:
son identificadores reales observados en un archivo del mismo proyecto.

**Ajuste de seguridad (obligatorio, agregado en esta iteración)**: el
generador mantiene un mapa explícito `{nombre de Connection Manager → GUID
conocido}` con exactamente estas dos entradas. Cuando `process_spec` pide una
conexión por nombre (`source.connection`, `destination.connection`), el
generador busca ese nombre EXACTO en el mapa. Si el nombre no está en el
mapa, **falla con un error explícito** (`UnknownConnectionManagerError` o
equivalente) indicando el nombre pedido y la lista de nombres soportados —
nunca reutiliza el GUID de un nombre distinto ni asume una correspondencia
por posición/orden. Esto evita el caso silencioso "el spec pide `cnxOtraCosa`
y el generador le asigna por error el GUID de `cnxTeradata`".

**Limitación documentada**: si en el futuro este generador debiera apuntar a
otro proyecto SSIS (con sus propios Connection Managers), estos dos GUID
tendrían que leerse de los `.conmgr` reales de ese proyecto — archivos que no
existen en `Examples/Originals/` y que este MVP no lee ni resuelve. Opciones
descartadas por falta de evidencia/alcance: parametrizar GUIDs de Connection
Manager en el spec (posible a futuro, no necesario si el proyecto es el
mismo) y leer `.conmgr` (no hay ninguno disponible para probar contra él).

## 4. Template vs. generado dinámicamente

### Estático — copiado tal cual del template, sin tocar

- Atributos de identidad NO funcional del Package: `DTS:CreationName`, `DTS:ExecutableType`, `DTS:LocaleID`, `DTS:PackageType`, `DTS:ProtectionLevel`, `DTS:VersionBuild`, la property `PackageFormatVersion`.
- Atributos equivalentes del Data Flow Task Executable: `DTS:CreationName`, `DTS:ExecutableType`, `DTS:LocaleID`, `DTS:TaskContact`.
- `contactInfo` (copyright) y `version` de cada `<component>` (Teradata Source `version="1"`, OLE DB Destination `version="4"`, Data Conversion sin `version`).
- Toda property de tuning sin evidencia de necesitar cambiar para un caso "mismo tipo de origen/destino, distinta tabla/columnas": `BlockSize`, `BufferMaxSize`, `BufferMode`, `DataEncryption`, `DefaultCodePage`, `MaxSessions`, `MinSessions`, `TenacityHours`, `TenacitySleep`, `AccessMode` (se copia el valor observado por componente, no se reinterpreta), `FastLoadKeepIdentity`, `FastLoadKeepNulls`, `FastLoadOptions`, `FastLoadMaxInsertCommitSize`, `CommandTimeout`, `AlwaysUseDefaultCodePage`.
- Metadata técnica de cada `<property>` que se SÍ cambia de valor (`dataType`, `description`, `expressionType`, `typeConverter`, `UITypeEditor`) — se conserva el atributo, solo cambia el texto/valor.
- Esqueleto de `DTS:DesignTimeProperties` (coordenadas) — es puro layout, sin impacto funcional (documentado en §8 de `xml_patterns.md`); se reutiliza la disposición vertical de 3 nodos del template.
- `connectionManagerID="{GUID}:external"` (ver punto 3).

### Dinámico — construido desde `process_spec`

- `DTS:ObjectName` y `DTS:DTSID`/`DTS:VersionGUID` del Package (GUID nuevo, `uuid4`).
- `DTS:ObjectName`, `DTS:Description` y `DTS:DTSID` (GUID nuevo) del Data Flow Task Executable.
- Teradata Source: `name`, `SqlCommand`, lista de `outputColumn`/`externalMetadataColumn` (nombre/tipo/longitud tomados del spec).
- Data Conversion: un `outputColumn` + su `SourceInputColumnLineageID`/`FastParse` por cada conversión declarada en el spec.
- OLE DB Destination: `name`, `OpenRowset` (tabla), `connectionManagerRefId` (nombre desde spec), `inputColumn`+`externalMetadataColumn`+mapping por cada mapping declarado.
- **Todo** `refId`/`lineageId`/`externalMetadataColumnId`/`path refId` — reconstruidos con la fórmula determinista ya validada por el parser (`Package\<DataFlow>\<Componente>.Outputs[<Output>].Columns[<Columna>]`, etc.), nunca copiados del template.
- `<paths>`: los dos paths Source→Conversion→Destination, usando los nombres de componente/input/output del spec.

## Estructura de `process_spec` propuesta

Ver `specs/campanias_generated.json` (a crear). Forma orientativa (ajustada
respecto del ejemplo del usuario para incluir la metadata de columna mínima
que el generador necesita — tipo/longitud — sin la cual no se pueden
construir `outputColumn`/`externalMetadataColumn` sin inventar):

```json
{
  "package": { "name": "CampaniasGenerado" },
  "data_flow": {
    "name": "Tarea Flujo de datos",
    "source": {
      "type": "teradata",
      "name": "Teradata Source",
      "connection": "cnxTeradata",
      "sql": "SELECT ...",
      "columns": [
        {"name": "COD_CAMPANIA_DIRIGIDA", "data_type": "i4"},
        {"name": "DESC_CAMPANIA_DIRIGIDA", "data_type": "wstr", "length": 200},
        {"name": "FEC_INICIO_TXT", "data_type": "str", "length": 10, "code_page": 1252}
      ]
    },
    "transformations": [
      {
        "type": "data_conversion",
        "name": "Conversión de datos",
        "conversions": [
          {"input": "FEC_INICIO_TXT", "output": "FechaInicio_SAL", "target_type": "dbDate"}
        ]
      }
    ],
    "destination": {
      "type": "ole_db",
      "name": "Destino de OLE DB",
      "connection": "cnxSrvBsLogSBD01",
      "table": "[dbo].[TiposCampanias]",
      "mappings": [
        {"source": "COD_CAMPANIA_DIRIGIDA", "target": "CodigoCampania", "target_data_type": "i4"}
      ]
    }
  }
}
```

**Metadata de columnas — decisión documentada**: se declara explícitamente en
`process_spec.json` (opción A del enunciado), NO se infiere de una capa
intermedia ni se adivina por nombre. Motivo: ya confirmamos con evidencia real
(`xml_patterns.md` §5) que el `dataType`/longitud que un `externalMetadataColumn`
cachea es la metadata que el **proveedor OLE DB** le reportó a SSIS, no
necesariamente el tipo SQL literal de la columna en la tabla — `FechaInicio`/
`FechaFin` tienen metadata externa cacheada `wstr(27)` en el XML, mientras que
(según indicó el usuario del proyecto, dato externo al `.dtsx`) el tipo SQL
real de esas columnas en `dbo.TiposCampanias` es `datetime2(7)`. Son dos
capas distintas: tipo físico SQL vs. metadata externa/cacheada que SSIS
serializa — y la segunda es la que efectivamente hace falta para construir
`externalMetadataColumn` en un paquete generado, sin importar si coincide o
no con el tipo SQL real. No existe una tabla universal segura de conversión
tipo-SQL→tipo-SSIS; para este MVP la única fuente confiable es la metadata ya
cacheada/validada del paquete real (reutilizada al escribir el `process_spec`
de Campanias a mano, columna por columna, contra `analysis_output.json`).

## Validación de `process_spec` — fail-fast, antes de tocar el template

Implementada en `generator/spec_validator.py`, función `validate_spec()`
(junta TODOS los errores en una pasada) y `assert_valid_spec()` (lanza
`SpecValidationError` si la lista no está vacía). `generate()` llama a
`assert_valid_spec()` como PRIMER paso — si falla, no se toca el template ni
se escribe ningún archivo (verificado en tests: `SpecValidationFailFastTests`
confirma que el `.dtsx` de salida no existe tras un `SpecValidationError`).

Chequeos implementados (mínimo pedido, todos presentes):

| Chequeo | Dónde |
|---|---|
| `source.type == "teradata"` | obligatorio, exacto |
| `destination.type == "ole_db"` | obligatorio, exacto |
| Topología soportada | `transformations` debe tener EXACTAMENTE 1 elemento, de tipo `data_conversion` |
| Columnas de origen sin duplicados | por nombre, dentro de `source.columns` |
| Input de cada conversión existente | `conversions[].input` debe estar en `source.columns` |
| Output de conversión sin colisiones | ni entre sí, ni contra nombres de columna de origen (evita ambigüedad al resolver `mapping.source`) |
| Source de cada mapping existente en el pipeline | `mappings[].source` debe ser una columna de origen O un output de conversión |
| `target_data_type` obligatorio | en cada mapping |
| `length`/`target_length` obligatorio para `str`/`wstr` | en `source.columns` y en `mappings` |
| `code_page`/`target_code_page` obligatorio para `str` | en `source.columns` (confirmado por evidencia) y en `mappings` (por analogía documentada, sin evidencia directa de un caso `str` del lado destino) |
| Conexiones soportadas por el template | `source.connection`/`destination.connection` deben estar en `KNOWN_CONNECTION_MANAGERS` |
| Nombres funcionales no vacíos | `package.name`, `data_flow.name`, nombres de componente, conexión, tabla, columnas, conversiones, mappings |

## Validación Nivel 1 — estático (implementada)

```
process_spec.json → generator.generate() → CampaniasGenerado.dtsx → ssis_parser.parse_file() → ssis_validator.validate_ir()
```

`tests/test_generator_campanias.py::GeneratorEndToEndTests` compara el IR
generado contra el spec (nunca contra `analysis_output.json` del original,
cuyos GUID/nombres de paquete son necesariamente distintos): 1 Data Flow, 3
componentes con los tipos esperados, 2 conversiones, 7 mappings resueltos,
conexiones correctas, tabla destino correcta, 2 paths correctos,
`ssis_validator.validate_ir()` con 0 errores. Explícitamente NO se exige
igualdad de GUIDs (de hecho `test_generated_dtsid_are_fresh_not_copied_from_template`
confirma que son DISTINTOS) ni de metadata visual/layout.
`GeneratorRenamedTopologyTests` prueba además que el generador funciona
igual de bien con nombres de componente distintos a los del template.

## Validación Nivel 2 y Nivel 3 — SSIS real (manual) — ✅ COMPLETADAS

Estos niveles NO se automatizan desde este entorno (no hay motor SSIS
disponible aquí) — se ejecutan manualmente en Visual Studio/SSDT. **Ya
fueron completados con éxito por el usuario** (ver sección de Milestone al
inicio de este documento); este procedimiento queda documentado como
referencia reproducible para futuras iteraciones (por ejemplo, al extender
el generador o al validar Turnero más adelante):

1. **Copiar el archivo**: llevar `CampaniasGenerado.dtsx` (generado en la raíz de este repo) a la carpeta del proyecto SSIS existente (la misma carpeta donde vive `BipSuc_CampaniasVigentes.dtsx`).
2. **Agregarlo AL PROYECTO** (no como "Elemento de la solución" — ver lección aprendida arriba): en Solution Explorer, click derecho sobre el proyecto `BipSuc` → *Add* → *Existing Item...* → seleccionar `CampaniasGenerado.dtsx`. Debe quedar dentro de `BipSuc → Paquetes SSIS`, al mismo nivel que `BipSuc_CampaniasVigentes.dtsx`.
3. **Abrirlo**: doble click sobre el paquete agregado. Confirmar que el diseñador lo renderiza sin errores de carga (los 3 componentes deben verse conectados: Teradata Source → Conversión de datos → Destino de OLE DB). **Confirmado.**
4. **Validar**: click derecho sobre `CampaniasGenerado.dtsx` en Solution Explorer → *Validate*. Repetir sobre la Tarea Flujo de datos (Data Flow Task). Los Connection Managers de proyecto (`cnxTeradata`, `cnxSrvBsLogSBD01`, `cnxSrvTurnosDb`) deben resolverse sin el error `The connection "{GUID}" is not found.` **Confirmado**, una vez el paquete quedó dentro del proyecto correcto.
5. **Comparar estructuralmente contra el original**: abrir ambos paquetes lado a lado y confirmar que la forma del Data Flow es equivalente componente por componente. **Confirmado.**
6. **Ejecutar** (Nivel 3): `CampaniasGenerado.dtsx` apunta a la misma tabla destino que el original (`[dbo].[TiposCampanias]`, vía el mismo Connection Manager `cnxSrvBsLogSBD01`) — ejecutarlo inserta filas reales en esa tabla. **Ejecutado contra el ambiente QA: Teradata Source / Data Conversion / OLE DB Destination reportaron SUCCESS, 1 fila procesada, confirmada posteriormente en SQL Server QA.**

Advertencia que sigue vigente para cualquier ejecución futura: correr este
paquete (o el original) inserta datos reales en la tabla destino — repetir
la ejecución sin recaudos en un ambiente productivo no está cubierto por
este MVP ni fue lo que se probó (la prueba fue en QA).

## Limitaciones explícitas del MVP

1. Limitado a la forma exacta Teradata Source → Data Conversion → OLE DB Destination (1 fuente, EXACTAMENTE 1 transformación de tipo Data Conversion, 1 destino, sin fan-in/fan-out) — `spec_validator` lo rechaza explícitamente si no se cumple.
2. No soporta Sequence Containers, Execute SQL Task, transacciones, staging, Merge Join, Conditional Split, Row Count ni OLE DB Source como origen — el parser los conoce, el generador no los escribe todavía.
3. Los GUID de Connection Manager están hardcodeados a los dos observados en Campanias (`cnxTeradata`, `cnxSrvBsLogSBD01`) en `generator/xml_helpers.KNOWN_CONNECTION_MANAGERS` — válido solo dentro del mismo proyecto SSIS. Un nombre de conexión distinto es rechazado explícitamente (`SpecValidationError`/`UnknownConnectionManagerError`), nunca se reutiliza el GUID de otro nombre.
4. Este repositorio no automatiza la validación contra un motor SSIS real (no hay uno disponible en este entorno) — Nivel 2 y Nivel 3 se ejecutan manualmente. **Ya se ejecutaron una vez con éxito** (ver Milestone arriba), pero cada cambio futuro al generador/template debería re-verificarse manualmente del mismo modo; no hay un gate automático que lo reemplace.
5. La metadata de columnas debe declararse a mano en el spec; no hay inferencia automática de tipos, y el generador nunca infiere el tipo físico SQL a partir del `data_type`/`target_data_type` de SSIS (son capas distintas, ver sección de metadata de columnas más arriba).
6. `DesignTimeProperties` generado reutiliza el layout fijo de 3 nodos del template (coordenadas sin cambios), con los `Id`/`design-time-name` reescritos por sustitución de texto acotada (ver docstring de `campanias_generator.py`) — no recalculado dinámicamente si el spec tuviera más o menos componentes; para esta topología fija (siempre 3 componentes) no hace falta.
7. El bloque `DTS:DesignTimeProperties` se serializa como texto con entidades XML (`&lt;`/`&gt;`) en vez de una sección `CDATA` explícita — equivalente para cualquier parser XML estándar (incluido `ssis_parser`), pero cosméticamente distinto del archivo original. **Verificado contra SSDT real** (Milestone arriba): SSDT abrió y renderizó el diseñador correctamente pese a esta diferencia cosmética — no fue un problema en la práctica.
8. `DTS:CreationDate`, `DTS:CreatorComputerName`, `DTS:CreatorName` se copian tal cual del template (no se actualizan a la fecha/usuario real de generación) — decisión deliberada para no arriesgar un formato de fecha no confirmado (`M/D/YYYY H:MM:SS AM/PM` con reglas de padding no verificadas).
9. `target_type` de una conversión (Data Conversion) no exige `length`/`code_page` aunque fuera `str`/`wstr` — el único caso real observado es `dbDate`; no hay evidencia de cómo luce una conversión hacia texto para replicar la regla con confianza.
10. Un paquete que referencia `Project.ConnectionManagers[...]` (este generador incluido) solo resuelve esas conexiones cuando está cargado DENTRO del proyecto SSIS correspondiente en Visual Studio/SSDT — nunca como archivo suelto ni como "Elemento de la solución" (ver Milestone arriba). No es una limitación de este generador en particular, sino del modelo de Connection Managers de proyecto de SSIS; se documenta acá porque afecta directamente cómo debe probarse cualquier `.dtsx` que este generador produzca.
11. Sigue sin resolverse programáticamente el contexto de proyecto (`.conmgr`, `Project.params`, `.dtproj`) — los GUID de Connection Manager siguen hardcodeados (ver limitación 3) y no hay lectura de propiedades externas al `.dtsx` como `RetainSameConnection`, `DelayValidation` a nivel de proyecto, o parámetros de proyecto. Ver la sección "Próxima fase" más abajo.

## Próxima fase (solo documentada, NO implementada en esta iteración)

### Project Context / Connection Manager Analysis

**Objetivo**: resolver programáticamente metadata de proyecto y eliminar
gradualmente la dependencia actual de GUIDs de Connection Manager
hardcodeados (`generator/xml_helpers.KNOWN_CONNECTION_MANAGERS`).

**Fuentes a incorporar** (ninguna leída todavía por este proyecto):
- `*.conmgr` — definición real de cada Connection Manager de proyecto (proveedor, cadena de conexión, y propiedades como `RetainSameConnection`).
- `Project.params` — parámetros de proyecto SSIS.
- `*.dtproj` — manifiesto del proyecto (qué paquetes y qué Connection Managers pertenecen a él).

**Caso crítico que motiva esta fase**: `RetainSameConnection=True` es
necesario para reproducir de forma segura el patrón de staging con tabla
temporal de sesión (`#ClientesStage`) que se documentó en Turnero
(`docs/campanias_vs_turnero.md`) — sin esa propiedad, no hay garantía de que
el Execute SQL Task que crea la tabla temporal y el Data Flow que la llena
compartan la misma conexión física, y la tabla temporal dejaría de existir
entre un paso y otro. Esta propiedad vive en el `.conmgr`, no en el `.dtsx`,
por lo que el parser actual (`ssis_parser.py`) no la ve ni la puede ver sin
leer esa fuente adicional.

Después de esta fase de análisis de contexto de proyecto podrá plantearse
**Generator v2 — Turnero** (Sequence Containers, Execute SQL Task,
transacciones, staging, Merge Join, Conditional Split, Row Count, OLE DB
Source como origen) — pero su implementación queda explícitamente fuera de
esta iteración y de la anterior.
