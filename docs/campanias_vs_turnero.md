# Informe de diferencias: BipSuc_CampaniasVigentes.dtsx vs BipSuc_Turnero.dtsx

Comparación estructural entre los dos paquetes reales usados como fuente de
verdad. El detalle línea-por-línea de cada patrón nuevo está en
[`xml_patterns.md`](xml_patterns.md), Parte 2. Este documento es el resumen
comparativo pedido explícitamente como entregable.

## Resumen ejecutivo

Campanias es el caso mínimo (1 Data Flow, 3 componentes, sin Control Flow
propio). Turnero es un paquete de producción real con Control Flow completo
(transacciones SQL, containers, precedence constraints, variables) y 3 Data
Flows que usan 5 tipos de componente adicionales. Turnero no contradice nada
de lo observado en Campanias sobre los 3 componentes que comparten
(`Microsoft.SSISTeradataSrc`, `Microsoft.DataConvert`, `Microsoft.OLEDBDestination`);
sí refuta una hipótesis (`AccessMode` universal) y confirma/generaliza otra
(`#{...}` embebido).

## Arquitectura activa de Turnero (reconstruida desde el XML)

El usuario aclaró que Turnero contiene lógica histórica deshabilitada, y que
la arquitectura final vigente es conceptualmente: crear una tabla temporal
`#ClientesStage`, cargarla desde Teradata, y recién después truncar e
insertar en `dbo.Clientes` dentro de una transacción manual (`BEGIN`/`COMMIT`/`ROLLBACK`
vía Execute SQL Task). Esta sección reconstruye esa arquitectura **exclusivamente
desde el XML** (sin asumir que la descripción funcional es correcta de
antemano) y confirma que **sí coincide**, con el detalle de qué objeto
concreto del `.dtsx` cumple cada rol.

### Checklist OBSERVADO EN XML / NO ENCONTRADO EN XML

| Ítem pedido | Resultado | Evidencia |
|---|---|---|
| Creación de `#ClientesStage` | **OBSERVADO EN XML** | Execute SQL Task `Package\Crear #ClientesStage` (línea 85-100): `SqlStatementSource="SELECT TOP (0) *\nINTO #ClientesStage\nFROM dbo.Clientes;"` |
| Data Flow que carga `#ClientesStage` | **OBSERVADO EN XML** | `Package\Tarea Flujo Staging` (Microsoft.Pipeline, línea 3108-5217): su componente `Microsoft.OLEDBDestination` tiene `<property name="OpenRowset">#ClientesStage</property>` (línea 5835) |
| Teradata Source usado por ese Data Flow | **OBSERVADO EN XML** | Componente `componentClassID="Microsoft.SSISTeradataSrc"` dentro de `Tarea Flujo Staging` (línea 6688), conectado vía `connectionManagerRefId="Project.ConnectionManagers[cnxTeradata]"` |
| Destination que escribe `#ClientesStage` | **OBSERVADO EN XML** | Mismo componente `Microsoft.OLEDBDestination` de línea 5818-5825, `OpenRowset="#ClientesStage"`, `AccessMode="3"` |
| Sequence Container correspondiente | **OBSERVADO EN XML** | `Package\Sequence Container 1` (línea 3052-3107) — **no** `Sequence Container` (sin número), que es el histórico deshabilitado (ver más abajo) |
| BEGIN TRANSACTION | **OBSERVADO EN XML** | Execute SQL Task `Package\Inicia Transacción 1` (línea 119-135): `SqlStatementSource="begin tran"` |
| TRUNCATE dbo.Clientes | **OBSERVADO EN XML** | Execute SQL Task `Package\Sequence Container 1\Trunca tabla` (línea 3079-3095): `SqlStatementSource="TRUNCATE TABLE dbo.Clientes;"` |
| INSERT INTO dbo.Clientes SELECT * FROM #ClientesStage | **OBSERVADO EN XML** | Execute SQL Task `Package\Sequence Container 1\Inserta Destino` (línea 3062-3078): `SqlStatementSource="INSERT INTO dbo.Clientes\nSELECT *\nFROM #ClientesStage;"` |
| COMMIT | **OBSERVADO EN XML** | Execute SQL Task `Package\Confirma transacción 1` (línea 67-83): `SqlStatementSource="IF @@TRANCOUNT > 0\n    COMMIT TRAN;"` |
| ROLLBACK | **OBSERVADO EN XML** | Execute SQL Task `Package\Revierte todo 1` (línea 154-169): `SqlStatementSource="IF @@TRANCOUNT > 0\n    ROLLBACK TRAN;"` |
| Precedence Constraints que determinan Success/Failure | **OBSERVADO EN XML** | `Package\Sequence Container 1` → `Confirma transacción 1` SIN `DTS:Value` (línea 8069-8076, éxito por default) y → `Revierte todo 1` CON `DTS:Value="1"` (línea 8086-8094, falla) |
| Connection Manager SQL utilizado | **OBSERVADO EN XML (solo la referencia)** | Los 7 Execute SQL Task usan `SQLTask:Connection="{69CB5656-2C95-41A4-AB24-87D08E85CF15}"`; ese mismo GUID aparece como `connectionManagerID="{69CB5656-...}:external"` + `connectionManagerRefId="Project.ConnectionManagers[cnxSrvTurnosDb]"` en los componentes OLE DB de Data Flow — cruce implementado en `build_connection_guid_index`, confirma que todo (staging, truncate, insert, begin/commit/rollback) usa `cnxSrvTurnosDb` |
| `RetainSameConnection` | **NO ENCONTRADO EN XML** | No es un atributo que se serialice en el `.dtsx`: los Connection Managers de este paquete son de PROYECTO (`Project.ConnectionManagers[...]`), definidos en archivos `.conmgr` que no están en `Examples/Originals/`. El `.dtsx` no trae ningún `<DTS:ConnectionManagers>` propio (`grep` sin resultados) |
| `DelayValidation` | **OBSERVADO EN XML — en un solo lugar** | `DTS:DelayValidation="True"` en el `DTS:Executable` de `Package\Tarea Flujo Staging` (línea 5211), únicamente ahí en todo el archivo |
| `ValidateExternalMetadata` | **OBSERVADO EN XML — como `validateExternalMetadata` (atributo de `<component>`, no `<property>`)** | `validateExternalMetadata="False"` en el `Microsoft.OLEDBDestination` de `Tarea Flujo Staging` (línea 5825) — única aparición en los dos archivos completos (`grep` confirma 0 resultados en Campanias) |
| `OpenRowset=#ClientesStage` | **OBSERVADO EN XML** | Ídem fila "Destination que escribe #ClientesStage" arriba |

`DelayValidation` + `validateExternalMetadata="False"` juntos, ambos exclusivos
de `Tarea Flujo Staging`/su destino, son el mecanismo real por el cual SSIS
permite que el paquete valide y abra sin que `#ClientesStage` exista todavía
(se crea recién en el paso anterior del Control Flow) — coincide exactamente
con la arquitectura descrita, y es evidencia más fuerte que una simple
inferencia por nombre de tabla.

**Dato no pedido explícitamente pero relevante**: no se encontró ningún
`DTS:TransactionOption` en ningún Executable de ninguno de los dos archivos.
Esto confirma que la transacción de esta arquitectura es **manual** (SQL de
texto plano `begin tran`/`COMMIT TRAN`/`ROLLBACK TRAN` vía Execute SQL Task),
no el mecanismo nativo de transacciones de SSIS/MSDTC — por lo que, en
efecto, que las tres tareas SQL más el `TRUNCATE`/`INSERT` compartan la MISMA
conexión física (para que `@@TRANCOUNT` tenga sentido y para que la tabla
temporal de sesión `#ClientesStage` sea visible entre `Tarea Flujo Staging` e
`Inserta Destino`) depende enteramente de la configuración del Connection
Manager (`RetainSameConnection`) — que, como se indicó arriba, está fuera del
`.dtsx`. Esto es un **riesgo real para cualquier generador automático**: el
`.dtsx` por sí solo no alcanza para saber si esta arquitectura efectivamente
funciona; hace falta el `.conmgr` del proyecto.

### A) Grafo de Control Flow ACTIVO (reconstruido desde `DTS:Disabled` + `DTS:PrecedenceConstraints`)

```
Crear #ClientesStage  (Execute SQL Task, activo)
        │  [Restricción — Success]
        ▼
Tarea Flujo Staging  (Data Flow: Teradata Source → Columna derivada → Conversión de datos → Destino OLE DB #ClientesStage)
        │  [Restricción 1 — Success]
        ▼
Inicia Transacción 1  (Execute SQL Task, activo — "begin tran")
        │  [Constraint 3 — Success]
        ▼
Sequence Container 1  (activo)
   │
   │   Trunca tabla ──[Restricción — Success]──▶ Inserta Destino
   │   (TRUNCATE dbo.Clientes)                    (INSERT ... SELECT * FROM #ClientesStage)
   │
        │
        ├──[Constraint 1 1 — Success, sin DTS:Value]──▶ Confirma transacción 1  (COMMIT TRAN)
        └──[Constraint 2 1 — Failure, DTS:Value="1"]──▶ Revierte todo 1  (ROLLBACK TRAN)
```

Esto coincide con la descripción funcional del usuario, con el mapeo
concreto: "Sequence Container" de la descripción = `Sequence Container 1` del
XML (no `Sequence Container` a secas). Reconstruido automáticamente por
`ssis_parser.build_active_execution_graph` — ver `analysis_turnero.json`,
clave `active_execution_graph`.

### B) Lógica inactiva / histórica (presente en el XML, no en la ejecución real)

```
Rama "transacción vieja" (misma forma que la activa, pero deshabilitada por completo):

Inicia Transacción  (disabled)
        ▼
Sequence Container  (disabled)
   │
   │   Trunca tabla ──▶ Tarea Flujo de datos 1
   │   (TRUNCATE dbo.Clientes)   (Data Flow: Teradata Source → Columna derivada →
   │                              Conversión de datos → Destino OLE DB [dbo].[Clientes]
   │                              DIRECTO, sin tabla de staging)
        │
        ├──▶ Confirma transacción  (disabled, COMMIT TRAN)
        └──▶ Revierte todo  (disabled, ROLLBACK TRAN)

Pieza ESTRUCTURALMENTE AISLADA (ademas de disabled, sin ningun PrecedenceConstraint que la mencione):

Tarea Flujo de datos  (Data Flow independiente, disabled=True, structurally_isolated=True)
   Teradata Source ──▶ Columna derivada ──▶ Combinación de mezcla (Merge Join)
                                                     ▲
                                    Origen de OLE DB (lee dbo.Clientes) ──┘
   Combinación de mezcla ──▶ División condicional (Conditional Split)
        ├─ NIP_Ambos ──▶ Recuento Ambos (RowCount → User::CantNIPAmbos)
        ├─ NIP_Solo_Origen ──▶ Recuento SoloOrigen (RowCount → User::CantNIPSoloOrigen)
        └─ NIP_Solo_Destino ──▶ Recuento SoloDestino (RowCount → User::CantNIPSoloDestino)
```

La rama "transacción vieja" y la pieza aislada son físicamente independientes
en el grafo: nada conecta `Tarea Flujo de datos` con `Sequence
Container`/`Tarea Flujo de datos 1` (rama vieja pero sí conectada por
constraints). Ambas están descartadas de la ejecución real, pero por razones
DISTINTAS y NO por el mismo motivo estructural:

- `Tarea Flujo de datos` queda fuera porque **su propio `DTS:Disabled="True"`**
  la desactiva — el hecho de que además esté desconectada de todo
  `PrecedenceConstraint` es una observación aparte (`structurally_isolated=True`),
  no la causa de que no se ejecute. Si este mismo executable estuviera
  `Disabled="False"`, seguiría estando desconectado del grafo (seguiría siendo
  `structurally_isolated=True`) pero SÍ se ejecutaría como una raíz
  independiente — ver la corrección de la siguiente sección.
- `Sequence Container`/`Tarea Flujo de datos 1`/`Trunca tabla` quedan fuera
  porque están deshabilitados de punta a punta (el container mismo, y sus
  hijos heredan esa inhabilitación aunque su propio `DTS:Disabled` diga
  `"False"` — ver tabla siguiente), no por ningún problema de conectividad:
  SÍ tienen sus propios `PrecedenceConstraint` (`Trunca tabla → Tarea Flujo de
  datos 1`).

### Corrección de diseño: aislamiento estructural ≠ no ejecutable

Una versión anterior de `annotate_execution_roles` calculaba un campo
`reachable` que daba `False` (y por lo tanto `execution_role="orphaned"`,
excluido de `active_execution_graph`) para CUALQUIER executable sin
`PrecedenceConstraint` en su nivel. Eso es un error conceptual: un Control
Flow puede tener varias raíces independientes que arrancan en paralelo sin
ningún constraint entrante — un executable sin constraints no es
automáticamente código muerto, es simplemente una raíz. La corrección separa
los dos conceptos:

| Campo | Origen | Regla |
|---|---|---|
| `enabled` | **Nativo** (leído de `DTS:Disabled`, invertido) | Por nodo, SIN heredar del container padre — literalmente lo que dice el XML en ESE elemento |
| `effective_enabled` | **Derivado** | `enabled` propio Y de todos sus ancestros — modela que un container deshabilitado apaga a sus hijos aunque su `DTS:Disabled` propio sea `"False"` (regla de motor SSIS, no un atributo serializado) |
| `has_incoming_constraint` | **Derivado** | Existe un `PrecedenceConstraint` del nivel del nodo con `To == su ref_id` |
| `has_outgoing_constraint` | **Derivado** | Existe un `PrecedenceConstraint` del nivel del nodo con `From == su ref_id` |
| `structurally_isolated` | **Derivado** | Ni incoming ni outgoing — dato PURAMENTE ESTRUCTURAL, no implica nada sobre si se ejecuta |
| `is_control_flow_root` | **Derivado** | `effective_enabled` Y sin incoming constraint — cubre tanto una raíz "normal" (con salida hacia otros pasos) como un executable totalmente aislado pero habilitado |
| `execution_role` | **Derivado, binario** | `"active"` si `effective_enabled`, si no `"disabled"` — YA NO existe el valor `"orphaned"` |

`build_active_execution_graph` ahora incluye TODO executable con
`effective_enabled=True`, tenga o no constraints — un executable habilitado
sin ningún constraint aparece en el grafo activo como raíz independiente, en
vez de ser eliminado. `Tarea Flujo de datos` sigue fuera del grafo activo de
Turnero porque `enabled=False` (dato nativo), NO porque esté aislado.

Confirmado contra el XML real (`Tarea Flujo de datos 1`): `enabled=True` (su
`DTS:Disabled` propio no existe / es `False`) pero `effective_enabled=False`
(su container `Sequence Container` sí tiene `DTS:Disabled="True"`) →
`execution_role="disabled"`. Esta distinción — que el parser NO puede leer de
un solo atributo, requiere mirar la cadena de ancestros — es exactamente el
tipo de cosa que hay que marcar como derivada y no como un hecho plano del XML.

El validador (`ssis_validator.py`) reporta `structurally_isolated=True` como
warning `structurally_isolated_executable` — solo cuando hay más de un
executable en el nivel (un único Data Flow Task hijo del Package, como en
Campanias, no tiene ningún hermano del cual estar "aislado" y no genera
warning) — con un mensaje explícito de que puede representar una raíz
independiente, no una afirmación de código muerto.

Un test sintético (`tests/test_execution_role_semantics.py`) prueba el caso
mínimo que motivó la corrección: `A -> B`, `D` sin ningún constraint. `D`
resulta `structurally_isolated=True`, `is_control_flow_root=True`,
`execution_role="active"`, y aparece en `active_execution_graph` junto con
`A` y `B`.

---

## Comparación punto por punto

| # | Aspecto | BipSuc_CampaniasVigentes | BipSuc_Turnero |
|---|---|---|---|
| 1 | **Control Flow del paquete** | Ninguno: el único hijo del Package es el Data Flow Task | Completo: 7 Execute SQL Task, 2 Sequence Container, 3 Data Flow Task, todos hijos directos o anidados del Package |
| 2 | **Todos los Executables** | 1 (`Tarea Flujo de datos`, Microsoft.Pipeline) | 13 en total: 7 `Microsoft.ExecuteSQLTask`, 2 `STOCK:SEQUENCE`, 3 `Microsoft.Pipeline` a distinta profundidad, más 2 `Microsoft.ExecuteSQLTask` anidados dentro de los Sequence Container ("Trunca tabla" ×2, "Inserta Destino") |
| 3 | **Precedence Constraints** | Ninguno | 8 a nivel Package + 1 dentro de cada Sequence Container (10 en total); 2 de ellos con `DTS:Value="1"` (rollback) |
| 4 | **Data Flow Tasks** | 1 (`Tarea Flujo de datos`) | 3: `Tarea Flujo de datos 1` (dentro de Sequence Container, disabled), `Tarea Flujo de datos` (top-level, disabled y **huérfano** — sin ningún precedence constraint), `Tarea Flujo Staging` (top-level, activo) |
| 5 | **componentClassID encontrados** | 3: `Microsoft.SSISTeradataSrc`, `Microsoft.DataConvert`, `Microsoft.OLEDBDestination` | Los mismos 3 + 5 nuevos: `Microsoft.DerivedColumn`, `Microsoft.MergeJoin`, `Microsoft.ConditionalSplit`, `Microsoft.OLEDBSource`, `Microsoft.RowCount` (8 tipos distintos en total, 17 instancias de componente) |
| 6 | **Connection Managers referenciados** | 2: `cnxTeradata`, `cnxSrvBsLogSBD01` | 2: `cnxTeradata` (mismo nombre que en Campanias — confirma que son Connection Managers de PROYECTO, compartidos entre paquetes) y `cnxSrvTurnosDb` (nuevo, usado tanto por componentes de Data Flow como por los 7 Execute SQL Task vía GUID pelado) |
| 7 | **Variables y/o parámetros** | Ninguno (`<DTS:Variables />` vacío en todos lados) | 3 variables de paquete (`User::CantNIPAmbos`, `User::CantNIPSoloDestino`, `User::CantNIPSoloOrigen`), todas escritas por un `Microsoft.RowCount`. Ningún Project Parameter encontrado en ninguno de los dos archivos (no forman parte de un `.dtsx` de todos modos — viven en el `.dtproj`, fuera de alcance) |
| 8 | **Expressions configuradas en propiedades** | Ninguna (`expressionType="Notify"` presente pero sin `<DTS:PropertyExpression>` en ningún lado) | Ninguna tampoco — incluso con Control Flow completo, no se encontró un solo `DTS:PropertyExpression` real en ninguno de los dos archivos. Las "Expression" de Derived Column/Conditional Split son otra cosa: expresiones SSIS que son la CONFIGURACIÓN del propio componente, no expresiones dinámicas sobre una property |
| 9 | **Sources** | 1: Teradata Source | Teradata Source (×3, uno por Data Flow) + OLE DB Source (×1, en `Tarea Flujo de datos`) |
| 10 | **Transformations** | 1: Data Conversion | Data Conversion (×3) + Derived Column (×3) + Merge Join (×1) + Conditional Split (×1) |
| 11 | **Destinations** | 1: OLE DB Destination (`[dbo].[TiposCampanias]`) | OLE DB Destination ×2 (`[dbo].[Clientes]` en `Tarea Flujo de datos 1`, `#ClientesStage` — tabla temporal global — en `Tarea Flujo Staging`) + Row Count ×3 como terminal SIN destino de base de datos en `Tarea Flujo de datos` |
| 12 | **Paths normales y de error** | 2 paths normales, 0 de error (3 error outputs presentes pero no cableados) | 14 paths normales en total (3+8+3 por Data Flow), 0 de error (todos los error outputs de los 17 componentes están presentes pero sin cablear — mismo patrón que Campanias) |
| 13 | **lineageIds** | Formato jerárquico determinista, referenciado 1:1 (whole-string) en `SourceInputColumnLineageID` | Mismo formato; además **embebido dentro de expresiones más largas** (potencialmente varias veces) en Derived Column/Conditional Split/Merge Join — generaliza el patrón, no lo cambia |
| 14 | **externalMetadataColumns** | Presentes en Teradata Source (output) y OLE DB Destination (input) | Mismo patrón, presentes también en OLE DB Source (output) y en los 2 OLE DB Destination; ausentes (correctamente) en componentes puramente transformadores (Data Conversion, Derived Column, Merge Join, Conditional Split, Row Count) — consistente con que solo aplican donde hay un objeto externo real (tabla/vista) de por medio |
| 15 | **Mappings** | 7, todos resueltos (source_column y destination_column) | 62 mappings por cada uno de los 2 OLE DB Destination (124 en total), todos resueltos sin errores de validación |
| 16 | **Estructuras XML nuevas no presentes en Campanias** | — | Control Flow completo (§11-15 de `xml_patterns.md`), `cachedSortKeyPosition`/`sortKeyPosition`/`isSorted` (Merge Join), `hasSideEffects` a nivel `<input>` (ya presente en Campanias pero no extraído hasta ahora), `DTS:Disabled`, `DTS:ThreadHint`, `EvaluationOrder`/`IsDefaultOut` (Conditional Split), `SqlCommandVariable` (OLE DB Source) |

## Reglas confirmadas (se sostienen en los dos archivos)

1. `refId` / `lineageId` / `externalMetadataColumnId` / `connectionManagerRefId` son strings jerárquicos deterministas por nombre — funciona igual dentro de un container anidado.
2. `DTS:DTSID` y los GUID de `connectionManagerID` son opaque/generated — ningún patrón de derivación visible en ninguno de los dos archivos.
3. Ningún error output aparece cableado a un `<path>`/`PrecedenceConstraint` real en ninguno de los dos archivos (14 error outputs entre ambos, 0 usados).
4. `DTS:DesignTimeProperties` sigue siendo puro layout, sin impacto funcional, en ambos.
5. Los Connection Managers reales (proveedor, servidor, credenciales) NO están en el `.dtsx` en ninguno de los dos casos — confirmado que viven a nivel de proyecto.
6. Ningún `DTS:PropertyExpression` real en ninguno de los dos archivos, pese a que Turnero tiene mucho más Control Flow donde cabría usarlos.

## Reglas nuevas (aportadas por Turnero, sin evidencia en Campanias)

1. Un Data Flow Task puede anidarse dentro de un Sequence Container; `root.iter()` sobre `DTS:Executable` lo encuentra igual.
2. Un `Microsoft.RowCount` es un terminal válido de Data Flow sin destino de base de datos — escribe en una variable de paquete (`Namespace::ObjectName`).
3. Las referencias `#{lineageId}` pueden aparecer embebidas dentro de una expresión SSIS más larga (Derived Column, Conditional Split), no solo como valor completo de la property (Data Conversion).
4. Un GUID "pelado" de un Execute SQL Task (`SQLTask:Connection`) puede cruzarse contra el `connectionManagerID` de un componente de Data Flow del mismo paquete para resolver el nombre del Connection Manager — cross-reference implementado y confirmado (7/7 tareas resueltas).
5. Un Executable puede estar `Disabled="True"` Y ausente de todo `PrecedenceConstraint` simultáneamente — un patrón de "código muerto pero no borrado" real (`Tarea Flujo de datos`), detectable automáticamente como warning estructural (`structurally_isolated_executable`). Son dos hechos independientes: lo que efectivamente lo saca de la ejecución es `Disabled="True"` (dato nativo), no el aislamiento — un executable aislado pero habilitado es una raíz válida (ver "Corrección de diseño" arriba).
6. Los Connection Managers de proyecto se reutilizan entre paquetes distintos (`cnxTeradata` aparece igual en Campanias y Turnero).

## Hipótesis descartadas

1. **`AccessMode` numérico universal entre componentes OLE DB/Teradata** — refutada: `Microsoft.OLEDBSource` usa `AccessMode=2` para "SQL Command", mientras `Microsoft.SSISTeradataSrc` usa `AccessMode=1` para lo mismo. Cada `componentClassID` tiene su propio enum, aunque internamente consistente.
2. **Todo Data Flow termina en un destino de base de datos** — refutada por `Microsoft.RowCount` como terminal.
3. **Un `.dtsx` es siempre "solo Data Flow"** — refutada: Turnero tiene Control Flow rico (transacciones, containers, variables).
4. **`AccessMode` es al menos consistente entre instancias del MISMO `componentClassID`** — refutada también en este sentido más estricto: los dos `Microsoft.OLEDBDestination` de Turnero (uno a `[dbo].[Clientes]`, otro a `#ClientesStage`) usan `AccessMode="3"`, mientras el `Microsoft.OLEDBDestination` de Campanias (a `[dbo].[TiposCampanias]`) usa `AccessMode="0"` — los tres usan `OpenRowset` (no `SqlCommand`) y aun así difieren. No hay evidencia en ninguno de los dos archivos de qué distingue a un `AccessMode=0` de un `AccessMode=3` cuando el resultado observable (properties usadas) es el mismo; queda como hipótesis abierta, no como regla.

## Hipótesis que siguen abiertas (ningún archivo las confirma ni las descarta)

1. Cómo luce un `<path>` (Data Flow) o `PrecedenceConstraint` (Control Flow) que conecta explícitamente una salida/rama de error a un destino real — ninguno de los 14 error outputs/paths de error de ambos archivos está cableado.
2. Semántica exacta de `DTS:VariableValue/@DTS:DataType="20"` — un único código observado, sin tabla de referencia dentro de los archivos.
3. Semántica exacta de `DTS:PrecedenceConstraint/@DTS:Value="1"` como "Failure" — coherente con el uso (apunta a tareas de rollback) pero no confirmado por ningún comentario o metadato explícito del archivo.
4. Reglas de compatibilidad de `version` de componente entre motores SSIS distintos — ambos archivos comparten el mismo `LastModifiedProductVersion="17.0.1010.2"`.
5. Cómo se vería un componente con más de una `<connection>` — los 4 componentes con conexión vistos hasta ahora (Teradata Source ×2, OLE DB Source, OLE DB Destination ×2) siempre declaran exactamente una.

## Resultado de validación de ambos IR

| Paquete | Data flows | Componentes | Executables (Control Flow) | `valid` | Errors | Warnings |
|---|---|---|---|---|---|---|
| BipSuc_CampaniasVigentes.dtsx | 1 | 3 | 1 (solo el propio Data Flow Task) | `true` | 0 | 0 |
| BipSuc_Turnero.dtsx | 3 | 17 | 13 (7 SQL Task + 2 Sequence Container + 3 Pipeline, ver tabla arriba — cuenta a nivel Package: 9 nodos top-level, 4 anidados) | `true` | 0 | 1 (`structurally_isolated_executable`: `Tarea Flujo de datos`, ver §17 de `xml_patterns.md`) |

Ninguno de los dos IR tiene errores estructurales (lineageId sin resolver,
externalMetadataColumnId colgante, paths/precedence constraints huérfanos,
mappings sin resolver). El único warning es el hallazgo real documentado en
§17: un Data Flow deshabilitado y desconectado del grafo de ejecución.

`active_execution_graph` de Turnero (recorte derivado, ver sección anterior)
queda con exactamente 6 executables activos a nivel Package (`Crear
#ClientesStage`, `Tarea Flujo Staging`, `Inicia Transacción 1`, `Sequence
Container 1` con sus 2 hijos activos, `Confirma transacción 1`, `Revierte
todo 1`) y 5 precedence constraints activos — de los 9 nodos top-level y 10
constraints totales del `control_flow` completo. El de Campanias queda
idéntico a su `control_flow` (1 solo nodo, sin constraints), como es
esperable al no tener ninguna lógica deshabilitada.
