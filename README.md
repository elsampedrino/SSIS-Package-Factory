# SSIS Package Factory

Automatiza la migración de procesos hacia paquetes SSIS reales, en dos
etapas: **analizar** `.dtsx` reales para aprender sus patrones XML de forma
verificable (`ssis_parser.py`/`ssis_validator.py`), y **generar** paquetes
nuevos a partir de una especificación funcional limpia (`generator/`).

## 🏁 Milestone — Generator MVP Campanias, validado end-to-end

**`CampaniasGenerado.dtsx` (generado por este proyecto) fue probado
manualmente en Visual Studio/SSDT dentro del proyecto SSIS real `BipSuc`, y
la prueba fue exitosa de punta a punta**: apertura correcta en el
diseñador, resolución correcta de los Project Connection Managers, ejecución
real contra el ambiente **QA** (Teradata Source / Data Conversion / OLE DB
Destination → SUCCESS, 1 fila procesada) y verificación posterior en SQL
Server QA. Detalle completo, incluida una lección aprendida importante sobre
cómo debe agregarse un paquete al proyecto para que sus Connection Managers
se resuelvan, en [`docs/generator_mvp.md`](docs/generator_mvp.md) (sección
"MILESTONE — Generator MVP Campanias E2E"). No se afirma deployment a
producción — la ejecución confirmada fue en QA.

## Estado de los milestones (generador)

| Milestone | Estado | Documento |
|---|---|---|
| `generator-mvp-campanias-e2e` | ✅ Estable, validado en SSDT/QA | `docs/generator_mvp.md` |
| `project-context-v1` | ✅ **STABLE / SSDT VALIDATED** | `docs/project_context.md` |
| `template-teradata-to-sql-v1.1` | ✅ **STABLE / SSDT VALIDATED** — última capacidad estable de package generation | `docs/template_teradata_to_sql_v1.1_closure.md` |
| `project-generation-v1` | ✅ **STABLE / SSDT VALIDATED** — genera `Project.params`+`.conmgr` (MODE B), consumidos por `TERADATA_TO_SQL v1.1`; Gate A y Gate B confirmados manualmente en SSDT | `docs/project_generation_v1.md` |
| `control-flow-v1` | 🚫 **BLOCKED / EXPERIMENTAL** — SSDT `LoadFromXML` incompatibility not diagnosed | `docs/control_flow_v1.md` |
| `teradata-to-sql-profile-v1` | ✅ **STABLE / SSDT VALIDATED** — capa opcional de defaults corporativos (`profiles/`) sobre `project-generation-v1`/`TERADATA_TO_SQL v1.1`, Gate SSDT confirmado manualmente | `docs/teradata_to_sql_profile_v1.md` |
| `mapping-planner-v1` | ⚪ **NOT STARTED** — candidato futuro de roadmap | — |

`template-teradata-to-sql-v1.1` sigue siendo la última familia de generación
de *packages* completamente estable: Teradata Source, OLE DB/SQL Server
Destination, Data Conversion opcional, mappings explícitos (incluyendo
`str→dbDate`, `str→wstr`, metadata `length`/`code_page`, `numeric`
`precision`/`scale`), `AccessMode` explícito de destino, y resolución de
conexiones vía `ProjectContext` — todo validado manualmente en SSDT.
`project-generation-v1` (MODE B: genera `Project.params`+`.conmgr` para
incorporar a un proyecto SSIS existente, sin tocar `.dtproj`) cerró su
circuito completo (`ProjectSpec → Project resources → ProjectContext →
TERADATA_TO_SQL v1.1 → .dtsx → SSDT`) con Gate A y Gate B confirmados
manualmente en SSDT y 268/268 tests automáticos en verde — ver
`docs/project_generation_v1.md` para el detalle completo, incluidas las
limitaciones que siguen vigentes (`.dtproj` no se genera ni se muta;
`Project.params` preexistente requiere reemplazo manual, no *merge*;
`RetainSameConnection` DEFERRED para OLEDB; providers limitados a
TERADATA/OLEDB; PropertyExpressions solo como referencia simple).
`control-flow-v1` (múltiples executables / Execute SQL Task / Precedence
Constraints a nivel Package) fue implementado y pasa toda la suite
automática, pero **no superó el gate manual de SSDT** y por eso no forma
parte de las capacidades estables — permanece `BLOCKED / EXPERIMENTAL`, ver
`docs/control_flow_v1.md` para el detalle completo (scope, implementación
alcanzada, las 4 rondas de diagnóstico, y cómo retomarlo).
`teradata-to-sql-profile-v1` agrega una capa **opcional** (`profiles/`) que
resuelve un `MinimalSpec` a `ProjectSpec`/`ProcessSpec` completos con
defaults corporativos (servidor/base/usuario Teradata, usuario SQL
`usSxSSIS`, nombres de parámetro SQL derivados `srv<Servidor>`/`pw<Servidor>`,
parámetro `ambiente`, `ProtectionLevel=EncryptSensitiveWithPassword`,
`MinSessions=4`/`MaxSessions=8`), consumidos sin cambios por
`project_generator`/`TERADATA_TO_SQL v1.1` — Gate SSDT confirmado
manualmente y 324/324 tests en verde — ver
`docs/teradata_to_sql_profile_v1.md`. `mapping-planner-v1` sigue como idea
de roadmap, **sin iniciar**.

### Regla metodológica (agregada tras `control-flow-v1`)

> Un `.dtsx` generado no se considera válido únicamente porque sea XML
> válido, el parser pueda leerlo, el validator devuelva `valid=True` y todos
> los tests estén verdes. Todo nuevo template/familia/capacidad estructural
> debe superar un gate manual de carga en SSDT antes de considerarse
> estable.

Regla existente que sigue vigente:

> Un template no se valida porque puede regenerar el paquete del cual fue
> extraído. Se valida cuando puede generar correctamente un segundo paquete
> de la misma familia que no fue usado para diseñarlo.

## Alcance actual

- **Parser/validador**: dos archivos de referencia analizados en profundidad.
  - `Examples/Originals/BipSuc_CampaniasVigentes.dtsx` — caso mínimo: 1 Data Flow, Teradata Source → Data Conversion → OLE DB Destination, sin Control Flow propio.
  - `Examples/Originals/BipSuc_Turnero.dtsx` — paquete de producción real: Control Flow completo (Execute SQL Task, Sequence Containers, Precedence Constraints, variables) + 3 Data Flows con 8 tipos de componente distintos.
- **Generador**: MVP limitado exclusivamente a la topología Teradata Source → Data Conversion → OLE DB Destination (el caso Campanias). Turnero **todavía no se genera** — sigue siendo solo evidencia de análisis (ver `docs/generator_mvp.md`, "Próxima fase").

El análisis detallado de patrones XML está en [`docs/xml_patterns.md`](docs/xml_patterns.md)
(Parte 1: Campanias, Parte 2: Turnero). La comparación punto por punto entre
ambos archivos, con reglas confirmadas/nuevas/refutadas, está en
[`docs/campanias_vs_turnero.md`](docs/campanias_vs_turnero.md). El diseño e
implementación del generador está en [`docs/generator_mvp.md`](docs/generator_mvp.md).
El resto de este README cubre el estado del parser en sí; ver ese último
documento para el generador.

## Archivos

- `ssis_parser.py` — módulo de parsing: Control Flow (Executables anidados, Sequence Containers, Execute SQL Task, Precedence Constraints, variables) + Data Flow (componentes, propiedades, conexiones, columnas, mappings, paths). Serializa a JSON.
- `ssis_validator.py` — validación del IR, **separada del parsing**: detecta referencias colgantes (lineageId, externalMetadataColumnId, precedence constraints), mappings sin resolver, componentes/tareas desconocidos, y executables desconectados del Control Flow. Nunca "arregla" nada silenciosamente.
- `main.py` — ejemplo de uso: parsea un `.dtsx`, escribe `<salida>.json` y `<salida>.validation.json`.
- `tests/` — regresión automática sobre ambos archivos de referencia (`python -m unittest discover -s tests`).
- `docs/xml_patterns.md` — catálogo de los patrones XML encontrados en cada archivo, con línea de origen y clasificación (funcional / metadata técnica / default / determinista / opaco / hipótesis).
- `docs/campanias_vs_turnero.md` — informe de diferencias entre los dos paquetes de referencia.
- `generator/` — generador MVP (solo Campanias): `xml_helpers.py` (namespaces, fórmulas de refId/lineageId, GUIDs), `spec_validator.py` (validación fail-fast de `process_spec`), `campanias_generator.py` (template + modificación de árbol XML). Ver `docs/generator_mvp.md` para el diseño completo y el resultado de la validación end-to-end.
- `specs/campanias_generated.json` — `process_spec` funcional de Campanias (entrada del generador).
- `templates/campanias_base.dtsx` — copia exacta de `BipSuc_CampaniasVigentes.dtsx`, usada como template validado por el generador.

## Uso

```bash
python main.py
# o con rutas explícitas:
python main.py Examples/Originals/BipSuc_Turnero.dtsx analysis_turnero.json

# tests
python -m unittest discover -s tests -v
```

Requiere solo Python 3 estándar (`xml.etree.ElementTree`, `json`, `re`) — sin dependencias externas.

## Qué interpreta hoy

### Control Flow
- `DTS:Executable` raíz (Package) y sus `DTS:Executables` hijos, recorridos **recursivamente** (containers incluidos), clasificados por `DTS:ExecutableType` vía `EXECUTABLE_TYPE_MAP`:
  - `Microsoft.Pipeline` → `pipeline` (delega en el parsing de Data Flow, ver abajo)
  - `Microsoft.ExecuteSQLTask` → `execute_sql_task` (SQL, GUID de conexión)
  - `STOCK:SEQUENCE` → `sequence_container` (anida sus propios Executables + Precedence Constraints)
  - Cualquier otro `DTS:ExecutableType` → `unknown:<tipo>`, sin perder información genérica (nombre, disabled, variables, etc.)
- `DTS:Variables` / `DTS:Variable` (a nivel Package o de cualquier Executable), expuestas con su `qualified_name` (`Namespace::ObjectName`).
- `DTS:PrecedenceConstraints` / `DTS:PrecedenceConstraint` (a nivel Package o dentro de un container), con `From`/`To`/`LogicalAnd`/`Value`.
- **Cross-reference de Connection Manager para Execute SQL Task**: `SQLTask:Connection` es un GUID pelado (sin nombre); se resuelve cruzándolo contra los `connectionManagerID="{GUID}:external"` de los componentes de Data Flow del mismo paquete (`build_connection_guid_index` + `resolve_execute_sql_task_connections`). Si el GUID no aparece en ningún Data Flow del paquete, queda en `None` — no se inventa.
- `DTS:DelayValidation` (a nivel Executable) y `validateExternalMetadata` (atributo directo de `<component>`, distinto de una `<property>`) se extraen tal cual — mecanismo real detrás de un Data Flow que escribe en una tabla temporal creada en un paso previo del Control Flow (ver `docs/campanias_vs_turnero.md`).
- **`enabled` / `effective_enabled` / `has_incoming_constraint` / `has_outgoing_constraint` / `structurally_isolated` / `is_control_flow_root` / `execution_role`** en cada nodo de `control_flow` — `enabled` es NATIVO (leído de `DTS:Disabled`, sin heredar del container padre); el resto es **DERIVADO** por `annotate_execution_roles` (documentado como tal en el código y en `docs/campanias_vs_turnero.md`, sección "Corrección de diseño"). Separan explícitamente lo ESTRUCTURAL (si un executable tiene o no `PrecedenceConstraint`s en su nivel) de la EJECUCIÓN real: un executable sin ningún constraint (`structurally_isolated=True`) puede perfectamente ser una raíz de ejecución válida (`is_control_flow_root=True`), no código muerto — `execution_role` es binario (`"active" | "disabled"`), determinado solo por `effective_enabled` (propaga la deshabilitación de containers a sus hijos), nunca por el aislamiento estructural.
- **`active_execution_graph`**: vista derivada y separada de `control_flow` que conserva TODO executable con `effective_enabled == True` (tenga o no `PrecedenceConstraint`s — una raíz aislada pero habilitada SÍ aparece) y los precedence constraints entre ellos, con la misma forma anidada. `control_flow` nunca pierde la lógica deshabilitada — sigue siendo evidencia XML válida de patrones de componente, solo que no participa de la arquitectura funcional vigente.
- `data_flows` en el IR final sigue siendo una lista **plana** de TODOS los Data Flow Tasks encontrados a cualquier profundidad, incluidos los deshabilitados/huérfanos (compatibilidad con la forma anterior del IR; su `execution_role` real se consulta en `control_flow`).

### Data Flow
- Componentes clasificados **por `componentClassID`** (nunca por `name`) vía `COMPONENT_TYPE_MAP`:
  - `Microsoft.SSISTeradataSrc` → `teradata_source`
  - `Microsoft.OLEDBSource` → `ole_db_source`
  - `Microsoft.DataConvert` → `data_conversion`
  - `Microsoft.DerivedColumn` → `derived_column`
  - `Microsoft.ConditionalSplit` → `conditional_split`
  - `Microsoft.MergeJoin` → `merge_join`
  - `Microsoft.RowCount` → `row_count`
  - `Microsoft.OLEDBDestination` → `ole_db_destination`
  - Cualquier otro `componentClassID` → `unknown:<classID>`, sin perder información (queda con `raw_properties`, `inputs`, `outputs`/`error_outputs` completos, solo sin los campos de conveniencia por tipo).
- Propiedades genéricas de componente, output y columna (`<properties><property>`), con toda su metadata (`dataType`, `description`, `containsID`, `expressionType`, `typeConverter`) más `referenced_lineage_ids`: TODAS las referencias `#{...}` encontradas en el valor (puede haber más de una embebida dentro de una expresión SSIS más larga, como en Derived Column/Conditional Split — no solo el caso "valor completo" de Data Conversion).
- Conexiones (`<connections><connection>`): se captura la referencia (`connectionManagerRefId`, `connectionManagerID`) — **no** la definición real del Connection Manager (ver limitaciones).
- Input columns (con `hasSideEffects`, `errorRowDisposition`, `errorOrTruncationOperation` a nivel `<input>`, además de las columnas), output columns (normales y de error, con `expression`/`friendly_expression`/`sort_key_position` cuando aplica), external metadata columns.
- Outputs con `is_sorted`, `expression`/`friendly_expression`/`evaluation_order`/`is_default_output` (Conditional Split rutea por output completo, no por columna).
- Un índice global `lineageId → columna que lo originó` (`build_lineage_index`), usado para resolver mappings.
- Mappings columna-origen → columna-destino, distinguiendo explícitamente `pipeline_input_column` (nombre en el pipeline) de `destination_column` (nombre FÍSICO real, resuelto contra `externalMetadataColumn.name` vía `externalMetadataColumnId` — nunca inferido por coincidencia de strings).
- Paths (`<paths><path>`), resueltos a nombres de componente (`from`/`to`) además de los `refId`/`startId`/`endId` crudos.
- Outputs de error, separados en `error_outputs`, incluso cuando no están conectados por ningún `<path>` (caso de los dos archivos de referencia).

### Validación (`ssis_validator.py`)
Capa separada del parser. `validate_ir(ir)` devuelve `{"valid": bool, "errors": [...], "warnings": [...]}`:
- **errors** (afectan `valid`): lineageId sin resolver, `externalMetadataColumnId` colgante, `synchronousInputId`/paths/precedence constraints que apuntan a algo inexistente, mappings sin `source_column`/`destination_column` resuelto.
- **warnings** (no afectan `valid`): `componentClassID`/`ExecutableType` desconocido, Execute SQL Task cuyo GUID de conexión no resolvió, Connection sin `connectionManagerRefId`, mapping sin `externalMetadataColumnId` (puede ser legítimo), **executable estructuralmente aislado** (`structurally_isolated_executable` — sin ningún `PrecedenceConstraint` en su nivel, solo cuando hay más de un executable en ese nivel; encontrado realmente en Turnero, ver `docs/campanias_vs_turnero.md`. El mensaje aclara explícitamente que puede ser una raíz independiente, no código muerto).

## Qué NO interpreta todavía

- **Connection Managers reales**: el `.dtsx` solo trae la referencia (nombre + GUID). La definición (proveedor, servidor, cadena de conexión) vive en archivos de proyecto (`.conmgr`) que no están presentes en `Examples/Originals/`.
- **Project Parameters**: no aparecen en ningún `.dtsx` (viven en el `.dtproj`, fuera del alcance de estos dos archivos).
- **`DTS:PropertyExpression` reales**: no se encontró ninguna instancia en ninguno de los dos archivos de referencia pese a que ambos tienen `expressionType="Notify"` en varias properties — no hay evidencia de cómo se serializa una expresión SSIS efectivamente asignada a una property.
- **Error paths / precedence constraints de fallo cableados a un destino real**: en ambos archivos, todos los error outputs y ramas de error existen pero ninguno está conectado; el parser nunca fue ejercitado contra ese caso.
- **Componentes sin evidencia todavía**: Lookup, Union All, Multicast, Aggregate, Sort, Script Component, Execute Package Task, ForEach Loop Container, etc. Agregar soporte es mecánico (ver "Cómo extenderlo") pero no está hecho ni validado sin un tercer archivo real que los use.
- **DesignTimeProperties**: se ignoran intencionalmente (son coordenadas de diagrama, no comportamiento).

## Riesgos y limitaciones

1. **Dependencia de nombres**: `refId`/`lineageId`/`externalMetadataColumnId` son strings deterministas construidos a partir de los nombres de paquete/data-flow/componente/columna (ver `docs/xml_patterns.md` §7). El parser los lee tal cual; no los reconstruye ni los valida contra una regla.
2. **GUIDs opacos**: `DTS:DTSID`, `VersionGUID` y los GUID embebidos en `connectionManagerID`/`SQLTask:Connection` no tienen regla de generación conocida. El parser los expone tal cual, y en el caso de Execute SQL Task intenta resolverlos por cross-reference (best-effort, puede quedar en `None`).
3. **Builders específicos validados contra pocas instancias**: cada builder de `COMPONENT_BUILDERS`/`EXECUTABLE_BUILDERS` está probado contra las instancias realmente vistas en los dos archivos de referencia (1-3 según el tipo). Generalizaciones no confirmadas quedan explícitamente marcadas como hipótesis en `docs/xml_patterns.md` — por ejemplo, ya se **refutó** la hipótesis de que `AccessMode` sea un enum universal entre componentes OLE DB (Turnero probó que cada `componentClassID` tiene el suyo).
4. **Connection Managers fuera de alcance**: cualquier automatización que dependa del servidor/base real de `cnxTeradata`, `cnxSrvBsLogSBD01` o `cnxSrvTurnosDb` necesita una fuente adicional (archivos de proyecto) que este parser no lee.
5. **`primary_connection_ref` asume una sola `<connection>` por componente** (cierto en los 4 componentes con conexión vistos hasta ahora). Si aparece un componente con múltiples conexiones, ese helper solo devuelve la primera; el dato completo sigue disponible en `connections` (lista).
6. **El cross-reference de GUID para Execute SQL Task depende de que el mismo Connection Manager se use también en algún Data Flow del paquete** — si un Execute SQL Task usara una conexión que NINGÚN componente de Data Flow referencia, quedaría sin resolver (con warning, no error).
7. **Precedence Constraints solo se validan dentro de su propio nivel** (hermanos directos); no hay validación cross-container de que el grafo de Control Flow completo sea alcanzable desde un único punto de entrada.

## Cómo extenderlo

### Un `componentClassID` nuevo (Data Flow)

1. Agregarlo a `COMPONENT_TYPE_MAP` en `ssis_parser.py`. Con esto ya se detecta y se parsea de forma **genérica** (propiedades, inputs, outputs, conexiones, columnas) sin escribir nada más.
2. (Opcional) Si necesita campos de conveniencia específicos, escribir `_build_<tipo>(component_el, component_type, common, lineage_index)` y registrarlo en `COMPONENT_BUILDERS`. No hace falta tocar `parse_component`.

### Un `DTS:ExecutableType` nuevo (Control Flow)

1. Agregarlo a `EXECUTABLE_TYPE_MAP` en `ssis_parser.py`. Igual que con componentes, queda parseado de forma genérica (ref_id, name, disabled, variables) sin builder.
2. (Opcional) Escribir `_build_<tipo>(exe_el, common)` y registrarlo en `EXECUTABLE_BUILDERS`. No hace falta tocar `parse_executable`.

### En ambos casos

3. Si el nuevo tipo introduce un patrón de referencia no visto, documentarlo en `docs/xml_patterns.md` con el mismo criterio ya usado: dónde aparece, qué representa, y su clasificación (funcional / metadata técnica / default / determinista / opaco / hipótesis) — **antes** de codificarlo, no después.
4. Antes de dar por buena la extensión, correr el parser contra un `.dtsx` real que use ese tipo, revisar el JSON a mano, y agregar al menos un test de regresión en `tests/` — no asumir que la estructura genérica alcanza sin verificarlo.
5. Si agregás un chequeo de validación nuevo, decidí explícitamente si es `error` (referencia interna rota, siempre un problema) o `warning` (notable pero esperable) — ver el criterio documentado al inicio de `ssis_validator.py`.
