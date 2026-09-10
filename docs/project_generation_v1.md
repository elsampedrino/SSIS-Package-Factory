# `project-generation-v1`

**STATUS: CLOSED — STABLE / SSDT VALIDATED.**

Gate A y Gate B fueron confirmados manualmente por el usuario en SSDT.
`control-flow-v1` permanece **BLOCKED / EXPERIMENTAL** y no fue tocado en
ningún momento de este milestone. `template-teradata-to-sql-v1.1` sigue
siendo la capacidad estable de generación de *packages* (`.dtsx`) y tampoco
fue modificada — el Gate B usó ese mismo generador
(`generator/campanias_generator.py`) sin ningún cambio de código.
`project-context-v1` se reutilizó tal cual (cero cambios) como mecanismo de
lectura/validación del *round-trip* en ambos gates.

Circuito E2E validado manualmente en SSDT, de punta a punta:

```
ProjectSpec
    |
Project.params + *.conmgr   (project_generator/, nuevo)
    |
ProjectContext              (project_context/, existente, sin cambios)
    |
TERADATA_TO_SQL v1.1        (generator/, existente, sin cambios)
    |
.dtsx generado
    |
SSDT (Gate A + Gate B — validado manualmente, ver abajo)
```

## Gate A — Project.params + .conmgr en SSDT — ✅ PASS

Validado manualmente por el usuario sobre los artefactos de
`generated_project_v1/` (spec: `specs/project_generation_v1_gate.json`),
incorporados al mismo proyecto SSIS existente reemplazando su
`Project.params` original:

| Verificación | Resultado |
|---|---|
| `Project.params` incorporado y abierto en el diseñador de parámetros | PASS |
| `dbTeradataGen`/`srvTeradataGen`: String, no sensible, con valor | PASS |
| `pwTeradataGen`/`pwSqlGen`: String, `Sensitive=True`, sin valor | PASS |
| `cnxTeradataGen.conmgr` reconocido como Teradata Connection Manager (server/user/database/`TD2` correctos, password vacío) | PASS |
| `cnxSqlGen.conmgr` reconocido como OLE DB Connection Manager (server/user/catálogo correctos, password vacío) | PASS |
| PropertyExpressions TERADATA (`ServerName`→`@[$Project::srvTeradataGen]` evaluado a `tdtest.example.local`; `Password`→`@[$Project::pwTeradataGen]`) | PASS |
| PropertyExpressions OLEDB (`Password`→`@[$Project::pwSqlGen]`, valor evaluado vacío por ser shell sin valor) | PASS |
| Cierre completo de Visual Studio y reapertura del proyecto — artefactos vuelven a cargar y son editables | PASS |

No se exigió `Test Connection` ni ejecución real contra Teradata/SQL — las
conexiones usan datos ficticios de prueba, fuera del criterio de este
milestone.

## Gate B — E2E con Package Generator — ✅ PASS

Validado manualmente por el usuario: `ProjectGenerationV1_E2E.dtsx`
agregado al **mismo** proyecto SSDT del Gate A, cerrando el circuito
`ProjectSpec → Project resources → ProjectContext → TERADATA_TO_SQL v1.1 →
.dtsx → SSDT`.

| Verificación | Resultado |
|---|---|
| Cierre y reapertura completos de Visual Studio / la solución | PASS |
| `ProjectGenerationV1_E2E.dtsx` vuelve a abrir correctamente | PASS |
| Data Flow conserva la topología `Origen Teradata → Destino SQL` | PASS |
| Teradata Source resuelve `cnxTeradataGen` | PASS |
| OLE DB Destination resuelve `cnxSqlGen` | PASS |
| Ambas conexiones aparecen como Project Connection Managers | PASS |
| `Project.params`, `.conmgr` TERADATA/OLEDB y PropertyExpressions validados visualmente | PASS |
| Parámetros sensibles visibles como shell, sin `Value` | PASS |
| Sin error `LoadFromXML` | PASS |

No se exigió conectividad real ni ejecución del *package* — las conexiones
usan datos ficticios de prueba, fuera del criterio de este milestone. Con
Gate A y Gate B en PASS, el circuito completo queda demostrado tanto
programáticamente (268/268 tests, ver §12) como manualmente en SSDT.

## Capacidades estables confirmadas

Confirmado como soportado en `project-generation-v1` (código + 268 tests
automáticos + Gate A/Gate B manuales en SSDT):

### `Project.params`
- Archivo vacío válido (sin parámetros).
- Parámetros `String` (único tipo soportado en v1).
- Parámetros no sensibles con `Value`.
- Parámetros `Sensitive` como shell **sin** `Value`.
- Parameter IDs correctos (GUID lowercase+llaves).

### TERADATA `.conmgr`
- Generación de Connection Manager.
- Metadata estructurada (server/database/user/authentication).
- `TeraConnectionString` derivado determinísticamente.
- `charset` explícito (`ASCII`|`UTF8`, sin default inventado).
- PropertyExpressions simples (`property` → parámetro de proyecto).
- DTSID correcto (uppercase+llaves).
- Sin `TeraPassword` / sin secrets.

### OLEDB `.conmgr`
- Generación de Connection Manager.
- `ConnectionString` derivado determinísticamente.
- `Application Name` propio de Factory (patrón estable, no imita ningún
  proyecto real puntual).
- PropertyExpressions simples (`property` → parámetro de proyecto).
- DTSID correcto (uppercase+llaves).
- Sin `Password` / sin secrets.

### Integración
- *Round-trip* hacia `ProjectContext` (`project_context/`, existente, sin
  duplicar).
- Consumo posterior por `TERADATA_TO_SQL v1.1` (`generator/`, existente, sin
  modificar) — demostrado end-to-end en Gate B.
- Resolución correcta de `Project.ConnectionManagers[...]` (verificado
  programáticamente y confirmado visualmente en SSDT).
- *Package* final reconocido por SSDT sin error `LoadFromXML`, sin
  Connection Manager inexistente, sin pérdida de componentes.

## Objetivo (MODE B)

```
ProjectSpec
    |
ProjectGenerator (project_generator/)
    |-- Project.params
    `-- N x *.conmgr
    |
ContextBuilder existente (project_context.context_builder)
    |
ProjectContext existente
    |
Package Generator existente (generator/, sin cambios)
```

`project-generation-v1` genera **recursos sueltos** para incorporar a un
proyecto SSIS **ya existente** — no genera ni muta ningún `.dtproj`.

## Scope implementado (congelado, sin ampliar)

1. Generación de `Project.params`.
2. Generación de `.conmgr` TERADATA.
3. Generación de `.conmgr` OLEDB.
4. `PropertyExpression` simples: `property` → parámetro de proyecto
   (`@[$Project::<parametro>]`). Nada de expresiones crudas ni ternarios.
5. IDs/GUIDs correctos (DTSID uppercase+llaves; Parameter ID lowercase+llaves).
6. Parámetros sensibles como **shell sin valor** (sin `Value`, sin cifrado).
7. *Round-trip* vía `project_context` (existente, sin duplicar).

## Fuera de scope (sin excepciones)

`.dtproj` generation/mutation; secrets/passwords; blobs cifrados; providers
distintos de TERADATA/OLEDB; expresiones SSIS arbitrarias; Control Flow;
generación de *packages* nueva; `RetainSameConnection` para OLEDB.

## 1. Arquitectura

Paquete nuevo `project_generator/`, espejo de escritura de `project_context/`
(que solo lee). No crea ninguna representación interna paralela de
`ProjectContext` — reutiliza directamente `build_project_context()` /
`validate_project_context()` para el *round-trip*.

```
project_generator/
  __init__.py
  xml_helpers.py      -- namespaces + estrategia de IDs
  spec_schema.py       -- validate_project_spec()/assert_valid_project_spec()
  params_writer.py     -- Project.params
  conmgr_writer.py      -- .conmgr TERADATA/OLEDB (dispatch por provider)
  project_generator.py -- orquestador: generate_project_resources(spec, output_dir)
```

Reutilización explícita:
- `generator.xml_helpers.new_guid()` se reutiliza tal cual para el DTSID de
  Connection Manager (mismo formato, mayúsculas+llaves) — no se reimplementó.
- `project_context.xml_helpers` (namespaces `DTS`/`SSIS`) se **duplica**
  deliberadamente en `project_generator/xml_helpers.py`, con el mismo
  criterio ya aplicado entre `generator/` y `project_context/`: cada paquete
  queda independiente en tiempo de ejecución de los demás.
- `project_context.context_builder.build_project_context()` y
  `project_context.validator.validate_project_context()` se usan **sin
  modificar** para el *round-trip* (sección 11).

`project_generator/` no importa nada de `generator.campanias_generator` ni
de `generator.control_flow_generator` (verificado con test de regresión
explícito, `tests/test_project_generation.py::RegressionTests`).

## 2. ProjectSpec v1

```json
{
  "project": {
    "parameters": [
      {"name": "dbTeradata", "sensitive": false, "value": "D_DW_APPLICATIONS"},
      {"name": "pwTeradata", "sensitive": true}
    ],
    "connections": [
      {
        "name": "cnxTeradata",
        "provider": "teradata",
        "server": "tdgnn.ccba.usr.bpba",
        "database": "D_DW_APPLICATIONS",
        "user": "D_DW_APPLICATIONS_USR",
        "authentication": "TD2",
        "charset": "ASCII",
        "retain_same_connection": false,
        "property_expressions": [
          {"property": "ServerName", "parameter": "srvTeradata"},
          {"property": "Password", "parameter": "pwTeradata"}
        ]
      },
      {
        "name": "cnxSql",
        "provider": "oledb",
        "server": "SRVBSQADB",
        "catalog": "Optimus",
        "user": "usSxSSIS",
        "property_expressions": [
          {"property": "Password", "parameter": "pwSql"}
        ]
      }
    ]
  }
}
```

### Required
- `parameters[].name`, `parameters[].sensitive`.
- `connections[].name`, `.provider`, `.server`.
- TERADATA además: `database`, `user`, `charset` (`ASCII`|`UTF8` — sin
  default: la evidencia real es inconsistente entre proyectos, ver §5).
- OLEDB además: `catalog`, `user`.

### Optional
- `parameters[].value` (obligatorio solo si `sensitive=false`; **prohibido**
  si `sensitive=true`), `.required` (default `true`, justificado: `Required=1`
  en el 100% de los parámetros de los 3 proyectos auditados), `.description`.
- TERADATA: `authentication` (default `"TD2"`, único valor observado),
  `retain_same_connection`, `property_expressions`.
- OLEDB: `property_expressions` (`retain_same_connection` está **prohibido**
  para OLEDB, ver §8).

### Derived (nunca en el spec)
Parameter ID, Connection DTSID, `TeraConnectionString`,
`DTS:ConnectionString`, namespaces XML, `Application Name`.

## 3. Validación (`project_generator/spec_schema.py`)

Fail-fast, junta **todos** los errores en una sola pasada (mismo criterio
que `generator/spec_validator.py`). Reglas exactas: ver docstring del
módulo. Puntos no obvios:

- `sensitive=true` + `value` presente → error explícito ("PROHIBIDO"), nunca
  se ignora en silencio.
- `property_expressions[].expression` (clave cruda) → error explícito: v1
  solo acepta `property`+`parameter`.
- `oledb.retain_same_connection` presente (aunque sea `false`) → error
  explícito citando DEFERRED.
- `charset` fuera de `{ASCII, UTF8}` (incluida su ausencia) → error.

## 4. Serialización de `Project.params`

Forma evidenciada, idéntica a los 3 proyectos reales auditados:
`SSIS:Parameters` → `SSIS:Parameter` → `SSIS:Properties` →
`ID/CreationName/Description/IncludeInDebugDump/Required/Sensitive/Value/DataType`.

- Sin parámetros → raíz self-closing (confirmado válido contra
  `Examples/Originals/SSDT_Golden/Project.params`, vacío en el proyecto real).
- `ID`: GUID **lowercase**+llaves (`uuid.uuid4()` ya es lowercase).
- `DataType`: siempre `"18"` (String) — único código confirmado a nivel
  `Project.params` en el corpus; los códigos `3`/`9` vistos en
  `.dtproj/ProjectConnectionParameters` pertenecen a un mecanismo distinto,
  no se traducen aquí sin evidencia.
- **`Sensitive=true` → la property `Value` se OMITE POR COMPLETO**, no vacía:
  mismo patrón evidenciado en
  `SSDT_Golden/Control_Flow_Base.dtproj` →
  `CM.SRVBSQADB.Optimus.usSxSSIS.Password` (misma forma `SSIS:Properties`
  genérica, reutilizada por `Project.params`).

## 5. Serialización TERADATA (`.conmgr`)

`DTS:ConnectionManager` (raíz) → `DTS:PropertyExpression` (0..N) →
`DTS:ObjectData/DTS:ConnectionManager` con elementos planos sin namespace
(`Tera*`), en el orden evidenciado: `TeraConnectionString, TeraRetain,
TeraInitialCatalog, TeraServerName, TeraUserName, TeraDatabase, TeraAccount,
TeraAuthentication, TeraWinAuthentication, TeraUseUTF8CharSet`.

- `TeraConnectionString` **derivado** determinísticamente:
  `DBCNAME=<server>;UID=<user>;AUTHENTICATION=<authentication>;DATABASE=<database>;CHARSET=<charset>;DRIVER={Teradata Database ODBC Driver 20.00};LOGINTIMEOUT=20;`
  — formato idéntico byte a byte al de los 3 `.conmgr` TERADATA reales.
- `charset` se refleja tal cual en `CHARSET=`; no hay default (ver §3).
- `TeraUseUTF8CharSet` se deja fijo en `"False"` — sin evidencia de un caso
  `True` en el corpus (incluso Golden, con `CHARSET=UTF8`, lo tiene en
  `False`); no se deriva de `charset` para no inventar un mapeo no evidenciado.
- `TeraInitialCatalog`/`TeraAccount`: siempre vacíos (evidencia: los 3
  proyectos reales los dejan vacíos).
- **Nunca se escribe `TeraPassword`.**

## 6. Serialización OLEDB (`.conmgr`)

`DTS:ConnectionManager` (raíz) → `DTS:PropertyExpression` (0..N) →
`DTS:ObjectData/DTS:ConnectionManager` con atributos con namespace `DTS`:
`ConnectRetryCount="1"`, `ConnectRetryInterval="5"` (constantes — únicos
valores vistos en los 3 proyectos), `ConnectionString` derivado:

```
Data Source=<server>;User ID=<user>;Initial Catalog=<catalog>;Provider=SQLOLEDB.1;Auto Translate=False;Application Name=<AppName>;
```

`Application Name` = `SSIS-Factory-{<DTSID>}<connection_name>` — **decisión
explícita de no imitar** ningún patrón real puntual: la evidencia es
inconsistente entre proyectos (`SSIS-Package-{GUID}...` en
BipSuc/PagosYRecaudaciones vs. `SSIS-<NombreProyecto>-{GUID}...` en Golden),
y `ProjectSpec` v1 no tiene ningún campo de "nombre de proyecto" (fuera de
scope, no se genera/muta `.dtproj`). Se usa un prefijo propio y estable,
con la misma forma general `SSIS-<X>-{GUID}<nombre>` que comparten los 3
ejemplos reales.

**Nunca se escribe `DTS:Password`.**

## 7. Estrategia de secrets

Un parámetro `sensitive=true` se genera **siempre** como *shell sin valor*
(§4). Ningún `.conmgr` generado incluye jamás `TeraPassword`/`DTS:Password`
ni ningún atributo de cifrado (`Salt`/`IV`/`Algorithm`). Esto está cubierto
por tests de seguridad explícitos (`tests/test_project_generation.py::SecurityTests`)
que fallan si cualquier archivo generado contiene esos marcadores.

## 8. `RetainSameConnection`

- **TERADATA: PARTIAL.** `retain_same_connection` (si viene en el spec, o
  `false` por default — único valor observado en los 3 `.conmgr` reales) se
  escribe en `TeraRetain`. Esto **no sustituye**
  `ProjectConnectionParameters` de `.dtproj` — la única fuente confirmada
  como uniforme entre providers, fuera de scope de este milestone.
- **OLEDB: DEFERRED.** `retain_same_connection` está explícitamente
  **rechazado** por `spec_schema` si aparece en una conexión OLEDB — OLE DB
  nunca serializa esto en su `.conmgr` en ningún proyecto real auditado; no
  se finge soporte.

## 9. IDs

- DTSID de Connection Manager: `generator.xml_helpers.new_guid()`
  reutilizado tal cual (mayúsculas+llaves).
- ID de Project Parameter: `project_generator.xml_helpers.new_parameter_id()`
  (minúsculas+llaves) — confirmado distinto del DTSID en los 3 proyectos
  reales auditados.
- Ningún GUID hardcodeado en ningún lado.

## 10. Serialización XML — lección de `control-flow-v1` aplicada

Namespaces siempre explícitos (`ET.register_namespace("SSIS", ...)`,
`ET.register_namespace("DTS", ...)`), nunca con prefijo vacío (evita el bug
de *namespace hoisting* que bloqueó `control-flow-v1`). Tests inspeccionan
QNames reales vía `ElementTree` (`{namespace}tag`), no *substring matching*
sobre el XML serializado.

## 11. Round-trip

```
ProjectSpec -> generate_project_resources() -> Project.params + N x .conmgr
    -> project_context.context_builder.build_project_context(...)
    -> project_context.validator.validate_project_context(...)
```

Implementado en `tests/test_project_generation.py::RoundTripTests`, usando
`build_project_context`/`validate_project_context` **sin modificar**.

**Limitación de diseño confirmada, no un bug**: `build_project_context()`
resuelve Connection Managers por **nombre de archivo declarado en el
`.dtproj`**, nunca por contenido de disco — por lo tanto el *round-trip*
necesita un `.dtproj` que **declare** los archivos recién generados. Como
generar/mutar `.dtproj` está fuera de scope, el test usa un
`.dtproj` **fixture de test** (`FIXTURE_DTPROJ_XML`, definido inline en
`tests/test_project_generation.py`) que declara los dos `.conmgr` generados
por nombre — **no es una capacidad nueva del producto**, es exclusivamente
un artefacto de test. Deliberadamente no declara
`ProjectConnectionParameters`, lo que ejercita con comportamiento real (no
simulado) exactamente la limitación de §8: `RetainSameConnection` de
TERADATA cae al *fallback* de `TeraRetain` (fuente `"conmgr"`, PARTIAL);
OLEDB queda en `None` (sin fuente, DEFERRED).

Invariantes verificados: nombres/sensibilidad/valor de parámetros,
`provider`/`dtsid` por conexión, `property_expressions` y sus
`referenced_parameters` resueltos, `validate_project_context(...)["valid"] is True`
con cero errores/warnings.

## 12. Tests agregados

`tests/test_project_generation.py` — 47 tests nuevos, cubriendo:
- `Project.params`: vacío, no sensible, sensible shell, IDs lowercase,
  `DataType=18`, `Required` default/override.
- TERADATA: sin/con/múltiples `PropertyExpression`, DTSID uppercase,
  charset ASCII/UTF8, `TeraConnectionString` exacto, sin `TeraPassword`,
  `TeraRetain` true/false/default, provider.
- OLEDB: básico, `PropertyExpression`, DTSID uppercase, `ConnectionString`
  exacto, `Application Name` estable, sin `Password`, provider.
- Validación: parámetro inexistente, duplicados (parámetro/conexión),
  provider desconocido, campos requeridos faltantes (por provider),
  `sensitive+value`, `oledb.retain_same_connection`, `expression` cruda.
- Round-trip: parámetros, sensibilidad, provider, DTSID, property
  expressions, referenced parameters, validador en verde.
- Seguridad: ausencia de `Salt=`/`IV=`/`Algorithm=`/`<DTS:Password`/
  `<TeraPassword`/`aes256-cbc` en cualquier archivo generado; parámetro
  sensible sin property `Value`; connection strings sin `Password=`/`PWD=`.
- Regresión: `project_generator/` no importa `campanias_generator` ni
  `control_flow_generator`.

`tests/test_project_generation_e2e_gate.py` — 13 tests nuevos (Gate B),
demostrando el circuito completo `ProjectSpec -> Project resources ->
ProjectContext -> TERADATA_TO_SQL v1.1 -> .dtsx -> ssis_parser/ssis_validator`:
- `ProjectContext` construido desde `generated_project_v1/` incluye ambas
  conexiones (`cnxTeradataGen`/`cnxSqlGen`) y los 4 parámetros generados,
  con `validate_project_context(...)["valid"] is True`.
- El Process Spec E2E (`specs/project_generation_v1_e2e.json`) pasa
  `generator.spec_validator.validate_spec(...)` sin errores contra ese
  `ProjectContext`.
- El `.dtsx` generado pasa `ssis_parser`/`ssis_validator` con `valid=True`,
  cero errores; topología exacta (2 componentes, 1 path, sin Data
  Conversion).
- `teradata_source.connection == "Project.ConnectionManagers[cnxTeradataGen]"`,
  `ole_db_destination.connection == "Project.ConnectionManagers[cnxSqlGen]"`.
- El `connectionManagerID` de cada componente coincide EXACTAMENTE con el
  DTSID real de `generated_project_v1/cnxTeradataGen.conmgr` /
  `cnxSqlGen.conmgr` (formato `{DTSID}:external`).
- Mappings (`Id_Test`, `Desc_Test`) resueltos correctamente hacia
  `[staging].[Factory_Test]`.
- Ausencia de `Salt=`/`IV=`/`Algorithm=`/`<DTS:Password`/`<TeraPassword`/
  `aes256-cbc`/`Password=` en el `.dtsx` generado.

**268 tests en total, 0 fallos** (255 previos + 13 nuevos de Gate B). Ningún
test de Control Flow ni de `template-teradata-to-sql-v1.1` fue modificado;
`generator/campanias_generator.py` se usó tal cual, sin ningún cambio —
**no se detectó ningún problema real de integración** entre
project-generation-v1 y TERADATA_TO_SQL v1.1.

## 13. Artefactos para el gate manual

**Gate A** — `specs/project_generation_v1_gate.json` (spec de prueba, sin
credenciales reales) → generado a `generated_project_v1/`:
- `Project.params` (2 parámetros no sensibles + 2 shells sensibles).
- `cnxTeradataGen.conmgr` (2 `PropertyExpression`: `ServerName`, `Password`).
- `cnxSqlGen.conmgr` (1 `PropertyExpression`: `Password`).

**Gate B** — `specs/project_generation_v1_e2e.json` (Process Spec E2E,
metadata ficticia estructuralmente válida) → generado a
`ProjectGenerationV1_E2E.dtsx` (repo root), usando **las mismas conexiones**
`cnxTeradataGen`/`cnxSqlGen` del Gate A, vía `TERADATA_TO_SQL v1.1` sin
modificar.

Todos los valores (`server`, `database`, `catalog`, `user`, `sql`, `tabla`)
son de prueba, no credenciales ni objetos reales.

## 14. Limitaciones documentadas (no son bugs)

### `.dtproj`
- Factory **NO genera** `.dtproj`.
- Factory **NO modifica** `.dtproj`.
- Los `.conmgr` generados deben incorporarse **manualmente** al proyecto
  (confirmado en Gate A: hizo falta "Add Existing Item") — SSDT no los
  muestra como parte de un proyecto hasta que se incorporen a mano o se
  edite el `.dtproj` manualmente. Confirmado indirectamente por la misma
  limitación ya documentada en `docs/generator_mvp.md` un nivel más abajo
  (Connection Managers del *package*).
- La integración automática con `.dtproj` queda para un milestone futuro —
  no implementada, no planificada dentro de este cierre.

### `Project.params` — hallazgo del Gate A (no suavizar)
Un proyecto SSIS **ya tiene su propio `Project.params`**. En el Gate A hizo
falta **reemplazar** el `Project.params` existente del proyecto por el
generado para poder validarlo — `project-generation-v1` **no sabe fusionar**
un `Project.params` generado con uno preexistente. Por lo tanto, **una
futura integración automática NO debe asumir que puede simplemente agregar
otro `Project.params`** junto al existente: una fase futura probablemente
necesitará *merge*, actualización o reconciliación de parámetros contra el
`Project.params` real del proyecto destino. **No implementado en este
milestone.**

### `RetainSameConnection`
- **TERADATA: PARTIAL** — se escribe en el `.conmgr` generado (`TeraRetain`),
  pero queda "huérfano" respecto de `ProjectConnectionParameters` hasta que
  el proyecto se abra en SSDT (que lo registra) o un milestone futuro toque
  `.dtproj`.
- **OLEDB: DEFERRED** — depende 100% de `.dtproj` (fuera de scope); no
  soportado, rechazado explícitamente por `spec_schema` si se pide.

### Secrets
- Factory **nunca genera passwords**.
- Factory **nunca genera ni copia blobs cifrados**.
- Los parámetros sensibles permanecen siempre *shells* sin `Value`.

### Providers
- Únicamente **TERADATA** y **OLEDB** en v1 — cualquier otro provider es
  rechazado explícitamente por `spec_schema`.

### PropertyExpressions
- Únicamente referencia simple `property → parámetro de proyecto`
  (`@[$Project::<parametro>]`).
- Expresiones crudas, ternarios o cualquier lógica SSIS arbitraria quedan
  **fuera de scope** — rechazadas explícitamente por `spec_schema`.

### `Application Name` (OLEDB)
- No imita ningún proyecto real puntual — patrón propio y estable de
  Factory, documentado en §6.

## 15. Gate A — resultado final (✅ PASS)

Ver tabla completa al inicio de este documento ("Gate A — Project.params +
.conmgr en SSDT"). Confirmado manualmente por el usuario: `Project.params`
incorporado (reemplazando el existente del proyecto, ver hallazgo en §14),
ambos `.conmgr` reconocidos por su provider correcto, PropertyExpressions
resueltas, parámetros sensibles visibles como shell sin valor, y
persistencia confirmada tras cerrar/reabrir Visual Studio.

## 16. Gate B — resultado final (✅ PASS)

Ver tabla completa en la sección "Gate B — E2E con Package Generator".
Confirmado manualmente por el usuario: `ProjectGenerationV1_E2E.dtsx`
incorporado al mismo proyecto del Gate A, Data Flow con la topología
`Origen Teradata → Destino SQL` intacta, ambas conexiones resueltas
correctamente, sin error `LoadFromXML`, y persistencia confirmada tras
cerrar/reabrir Visual Studio.

## 17. Cierre del milestone

Con Gate A y Gate B en PASS, y 268/268 tests automáticos en verde,
`project-generation-v1` queda formalmente cerrado:

**`project-generation-v1 = STABLE / SSDT VALIDATED`**

El circuito `ProjectSpec → Project.params + *.conmgr → ProjectContext →
TERADATA_TO_SQL v1.1 → .dtsx → SSDT` fue validado manualmente en SSDT de
punta a punta, sin modificar ningún código productivo existente
(`generator/`, `project_context/`) y sin tocar `control-flow-v1`, que
permanece `BLOCKED / EXPERIMENTAL`.

Las limitaciones documentadas en §14 (`.dtproj` no generado/mutado,
`Project.params` preexistente requiere reemplazo manual — no *merge*,
`RetainSameConnection` OLEDB DEFERRED, providers limitados a TERADATA/OLEDB,
PropertyExpressions solo referencia simple) **siguen vigentes** y no quedan
resueltas por este cierre — son el punto de partida de cualquier milestone
futuro que amplíe este scope (por ejemplo, integración automática con
`.dtproj`, o reconciliación de `Project.params`). Ninguna de esas
ampliaciones fue iniciada en este cierre.
