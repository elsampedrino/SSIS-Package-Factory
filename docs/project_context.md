# Project Context Analyzer v1 (`project-context-v1`)

Analizador de **Modo A**: lee (sin modificar) los archivos de contexto de un
proyecto SSIS existente — `*.dtproj`, `Project.params`, `*.conmgr` — y los
consolida en un `ProjectContext` único. Fixtures reales usados: el proyecto
`BipSuc` (`Examples/Originals/BipSuc.dtproj`, `Project.params`,
`cnxTeradata.conmgr`, `cnxSrvBsLogSBD01.conmgr`).

**Fuera de alcance, explícitamente**: crear o modificar `.dtproj` /
`Project.params` / `.conmgr`; descifrar passwords; generalizar a todos los
providers SSIS posibles; integrar con `generator/campanias_generator.py`
(se analiza qué podría cambiar, sin aplicarlo — ver sección final).

---

## 1. Cómo encaja con la arquitectura existente

El repo ya tenía dos pilares con el mismo patrón (parser separado del
validator, dispatch table por tipo en vez de una función monolítica,
IR/resultado como dict plano JSON-serializable, nunca clases):

- **Lectura de `.dtsx`**: `ssis_parser.py` + `ssis_validator.py`.
- **Escritura de `.dtsx`**: `generator/` (`xml_helpers.py`, `spec_validator.py`, `campanias_generator.py`).

`project_context/` es un **tercer pilar**, del mismo tamaño y con el mismo
criterio, para la lectura de archivos de PROYECTO (no de paquete):

```
project_context/
    __init__.py
    xml_helpers.py       -- namespaces DTS/SSIS + helper generico de <SSIS:Properties>
    params_parser.py     -- Project.params
    conmgr_parser.py      -- *.conmgr (dispatch por provider: TERADATA, OLEDB)
    dtproj_parser.py      -- *.dtproj
    context_builder.py    -- consolida los tres en un ProjectContext
    validator.py          -- validaciones + helpers de consulta (provider/DTSID/RetainSameConnection)
```

No se creó ninguna clase ni un segundo "sistema de modelos": `ProjectContext`
es un dict plano, igual que el IR de `ssis_parser.parse_file()`. El dispatch
por provider en `conmgr_parser.py` (`CONMGR_PROVIDER_BUILDERS`) es el mismo
patrón que `COMPONENT_TYPE_MAP`/`COMPONENT_BUILDERS` de `ssis_parser.py` y
`generator/campanias_generator.py`.

### Qué información de Project Context estaba HOY, antes de esta iteración

| Dato | Estado antes de esta iteración |
|---|---|
| GUID de `cnxTeradata` y `cnxSrvBsLogSBD01` | **Hardcodeado** en `generator/xml_helpers.KNOWN_CONNECTION_MANAGERS` (copiado a mano del `.dtsx` de referencia) |
| Nombre → GUID válido | **Hardcodeado**, mismo diccionario, usado tanto en `generator/campanias_generator.py` (para escribir) como en `generator/spec_validator.py` (para validar el nombre pedido en el spec) |
| Provider de cada conexión (TERADATA/OLEDB) | **Implícito**: el generador nunca lo consulta, asume que "conexión de Teradata Source" es Teradata y "conexión de OLE DB Destination" es OLE DB porque son los únicos dos tipos de componente soportados — no viene de ningún archivo de proyecto |
| `RetainSameConnection` | **Desconocido por completo** — ni `ssis_parser.py` ni el generador lo tocan; no hay ningún archivo `.conmgr`/`.dtproj` leído en todo el repo antes de esta iteración |
| `TargetServerVersion`, nombre de proyecto, parámetros de proyecto | **No existían en ningún módulo** — el `.dtsx` no los contiene (confirmado en `docs/xml_patterns.md`) |

---

## 2. Hallazgos por tipo de archivo (evidencia real de `BipSuc`)

### `.conmgr` — estructura común a cualquier provider

```xml
<DTS:ConnectionManager DTS:ObjectName="cnxTeradata" DTS:DTSID="{29B4FDD4-...}" DTS:CreationName="TERADATA">
  <DTS:PropertyExpression DTS:Name="...">...</DTS:PropertyExpression>   <!-- 0+, hijos directos de la raiz -->
  <DTS:ObjectData>
    <DTS:ConnectionManager>...contenido especifico del provider...</DTS:ConnectionManager>
  </DTS:ObjectData>
</DTS:ConnectionManager>
```

`DTS:CreationName` en un `.conmgr` **es el provider directamente**
(`"TERADATA"`, `"OLEDB"`) — uso distinto del mismo atributo en un `.dtsx`
(ahí identifica un tipo de tarea/componente, ej. `"Microsoft.ExecuteSQLTask"`).

**Confirmado: el `ObjectData` NO se serializa igual entre providers.**

- **TERADATA** (`cnxTeradata.conmgr`): hijos PLANOS sin namespace —
  `TeraConnectionString`, `TeraPassword` (cifrado), `TeraRetain`,
  `TeraServerName`, `TeraUserName`, `TeraDatabase`, `TeraAuthentication`, etc.
  **`TeraRetain` = `False`** — este provider SÍ serializa `RetainSameConnection`
  dentro de su propio `.conmgr`.
- **OLEDB** (`cnxSrvBsLogSBD01.conmgr`): ATRIBUTOS con namespace DTS sobre el
  propio elemento (`DTS:ConnectionString`, `DTS:ConnectRetryCount`,
  `DTS:ConnectRetryInterval`) + un hijo `DTS:Password` (cifrado). **No hay
  ningún equivalente a `RetainSameConnection` acá** — confirmado por ausencia,
  no asumido.
- **`DTS:PropertyExpression`**: encontramos acá, por primera vez en todo el
  proyecto, instancias REALES de este elemento (en los dos `.dtsx` de
  referencia no había ninguna — ver `docs/campanias_vs_turnero.md`). Para
  `cnxSrvBsLogSBD01`: `InitialCatalog`, `Password`, `ServerName`, cada una
  referenciando un Project Parameter vía `$Project::nombre`, a veces como
  valor completo (`@[$Project::pwSrvBsLogSBD01]`) y a veces embebida dentro
  de una expresión más larga con operador ternario
  (`@[$Project::ambiente] == "QA" ? "Optimus" : "Migas"`).

### `Project.params`

`SSIS:Parameters/SSIS:Parameter[@SSIS:Name]/SSIS:Properties/SSIS:Property[@SSIS:Name]`,
con propiedades `ID` (GUID opaco), `Required`, `Sensitive`, `Value`
(texto plano, o bloque `Sensitive="1"` + `Salt`/`IV`/`Algorithm` + ciphertext
cuando el parámetro es sensible) y `DataType` (código numérico, expuesto tal
cual — mismo criterio que `DTS:VariableValue/@DataType` en `ssis_parser.py`,
no se interpreta la tabla completa sin evidencia). 9 parámetros reales, 3
sensibles (`pwTeradata`, `pwUsSxSSIS`, `pwSrvBsLogSBD01`).

### `.dtproj`

Raíz **sin namespace** (`TargetServerVersion` vive en
`Configurations/Configuration/Options/TargetServerVersion` = `"SQLServer2025"`).
Dentro de `DeploymentModelSpecificContent/Manifest/SSIS:Project` (mismo
namespace `SSIS` que `Project.params`): nombre de proyecto (`"BipSuc"`),
`SSIS:Packages` (3 archivos, todos `EntryPoint="1"`), `SSIS:ConnectionManagers`
(3 **nombres de archivo**: `cnxTeradata.conmgr`, `cnxSrvTurnosDb.conmgr`,
`cnxSrvBsLogSBD01.conmgr`), y **`SSIS:ProjectConnectionParameters`**.

**Hallazgo central**: `ProjectConnectionParameters` es un mecanismo de
parametrización a nivel de PROYECTO **distinto y complementario** de
`Project.params` + `PropertyExpression`. No son referencias `$Project::x`:
son las propiedades del propio Connection Manager (`ServerName`, `Database`,
`RetainSameConnection`, etc.) expuestas con nombre compuesto
`CM.<ConnectionManager>.<Propiedad>` y su **valor directo**, en una forma
**UNIFORME entre providers** (Teradata y OLE DB usan exactamente la misma
forma acá, a diferencia del `.conmgr` que es provider-specific). Confirmado:

```
CM.cnxTeradata.RetainSameConnection      = false
CM.cnxSrvTurnosDb.RetainSameConnection   = true
CM.cnxSrvBsLogSBD01.RetainSameConnection = false
```

Es decir: **`RetainSameConnection` se puede resolver de forma confiable y
uniforme SOLO leyendo `.dtproj`** — el `.conmgr` lo tiene redundantemente
para Teradata (`TeraRetain`) pero NO para OLE DB. Esto confirma exactamente
lo que se pidió verificar en el Paso 4, y es la razón por la que
`context_builder.py` prefiere `.dtproj` y usa `.conmgr` solo como fallback
(`_resolve_retain_same_connection`, función pura, testeada con los 3 casos
posibles).

`SSIS:PackageInfo/PackageMetaData` también trae, por archivo `.dtsx`, su
`DTSID` (`ID`), `Name`, `VersionGUID`, etc. — un cross-reference útil (y ya
verificado: coincide exactamente con `DTS:DTSID`/`DTS:ObjectName` que
`ssis_parser.py` lee directamente del `.dtsx`).

---

## 3. Modelo `ProjectContext`

```python
{
  "project": {"name": str, "target_server_version": str, "protection_level": str},
  "parameters": [
      {"name": str, "id": str, "required": bool, "sensitive": bool,
       "data_type_code": str, "value": str | None},   # None si sensitive
      ...
  ],
  "connections": {
      "<nombre>": {
          "provider": "TERADATA" | "OLEDB" | "unknown:<CreationName>",
          "dtsid": str,
          "conmgr_file": str,
          "retain_same_connection": bool | None,
          "retain_same_connection_source": "dtproj" | "conmgr" | None,
          "property_expressions": [
              {"property": str, "expression": str, "referenced_parameters": [str, ...]}, ...
          ],
          "project_connection_parameters": {
              "<PropName>": {"value": str|None, "sensitive": bool, "required": bool, "data_type_code": str}, ...
          },
          "provider_data": {...}   # especifico del provider, ver conmgr_parser.py
      },
      ...
  },
  "packages": [ {file_name, dtsid, name, version_guid, package_format_version, protection_level}, ... ],
  "packages_declared": [ {file_name, is_entry_point}, ... ],
  "missing_conmgr_files": [str, ...],
}
```

### Ejemplo real (BipSuc), sin ningún valor sensible/cifrado

```json
{
  "project": {
    "name": "BipSuc",
    "target_server_version": "SQLServer2025",
    "protection_level": "EncryptSensitiveWithPassword"
  },
  "parameters": [
    {"name": "dbTeradata", "id": "{5d14c0ce-...}", "required": true, "sensitive": false, "data_type_code": "18", "value": "D_DW_APPLICATIONS"},
    {"name": "pwTeradata", "id": "{d2a2c466-...}", "required": true, "sensitive": true, "data_type_code": "18", "value": null},
    {"name": "ambiente", "id": "{078cf7d5-...}", "required": true, "sensitive": false, "data_type_code": "18", "value": "QA"}
  ],
  "connections": {
    "cnxTeradata": {
      "provider": "TERADATA",
      "dtsid": "{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}",
      "conmgr_file": "cnxTeradata.conmgr",
      "retain_same_connection": false,
      "retain_same_connection_source": "dtproj",
      "property_expressions": [],
      "provider_data": {
        "server_name": "tdgnn.ccba.usr.bpba",
        "database": "D_DW_APPLICATIONS",
        "retain_same_connection": false,
        "password_is_encrypted": true
      }
    },
    "cnxSrvBsLogSBD01": {
      "provider": "OLEDB",
      "dtsid": "{5DA5808C-8489-48AC-8614-CB034DE02B61}",
      "conmgr_file": "cnxSrvBsLogSBD01.conmgr",
      "retain_same_connection": false,
      "retain_same_connection_source": "dtproj",
      "property_expressions": [
        {"property": "InitialCatalog", "expression": "@[$Project::ambiente] == \"QA\" ? \"Optimus\" : \"Migas\"", "referenced_parameters": ["ambiente"]},
        {"property": "Password", "expression": "@[$Project::pwSrvBsLogSBD01]", "referenced_parameters": ["pwSrvBsLogSBD01"]},
        {"property": "ServerName", "expression": "@[$Project::srvBsLogSBD01]", "referenced_parameters": ["srvBsLogSBD01"]}
      ],
      "provider_data": {
        "connection_string": "Data Source=SRVBSQADB;User ID=usSxSSIS;Initial Catalog=Optimus;Provider=SQLOLEDB.1;...",
        "retain_same_connection": null,
        "password_is_encrypted": true
      }
    }
  },
  "packages": [
    {"file_name": "BipSuc_CampaniasVigentes.dtsx", "dtsid": "{03C7554A-0D65-44DD-9BEF-4D37D0AF5CB8}", "name": "Package1", "version_guid": "{420CD3AB-...}", "package_format_version": "8", "protection_level": "2"}
  ],
  "missing_conmgr_files": ["cnxSrvTurnosDb.conmgr"]
}
```

(Ejemplo completo, con las 9 parámetros y las 3 conexiones declaradas, en el
reporte de esta iteración — omitido acá por espacio; ninguna versión, completa
o resumida, contiene un valor cifrado o un password en texto plano.)

**Nota sobre `cnxSrvTurnosDb`**: el `.dtproj` lo declara y el `.dtsx` de
Turnero lo referencia (ver `docs/campanias_vs_turnero.md`), pero su archivo
`cnxSrvTurnosDb.conmgr` no está entre los fixtures de este repositorio. El
analizador no lo inventa: lo registra en `missing_conmgr_files` y el
validador lo reporta como warning, sin invalidar el resto del contexto.

---

## 4. Validaciones implementadas (`project_context/validator.py`)

| Validación | Severidad | Motivo |
|---|---|---|
| Connection Manager declarado en `.dtproj` pero archivo `.conmgr` ausente en disco | **warning** | El resto del contexto sigue siendo utilizable; es un caso real de este mismo repo (`cnxSrvTurnosDb.conmgr`) |
| Referencia `$Project::x` en una Property Expression cuyo parámetro `x` no existe en `Project.params` | **error** | El paquete fallaría realmente al ejecutarse en SSIS — no es una cuestión de completitud del análisis |

Más helpers de consulta puntual (`get_connection_provider`,
`get_connection_dtsid`, `get_retain_same_connection`) que levantan `KeyError`
explícito si el nombre pedido no está en el contexto — nunca devuelven un
default silencioso.

---

## 5. Tests (`tests/test_project_context.py`, 28 tests)

Cubren los 15 comportamientos pedidos: lectura de cada tipo de archivo por
separado, detección de provider/DTSID/Property Expressions/referencias
`$Project::...`, resolución de `RetainSameConnection` (como función pura,
con los 3 casos: gana `.dtproj`, cae a `.conmgr`, ninguno la tiene),
combinación end-to-end contra los fixtures reales de `BipSuc`, error por
parámetro inexistente (sintético), warning por `.conmgr` faltante (real), y
una verificación explícita de que ningún valor sensible queda expuesto en
todo el `ProjectContext` construido. Incluye además un test de consistencia
cruzada: los DTSID resueltos por el analizador coinciden exactamente con los
GUID hoy hardcodeados en `generator/xml_helpers.KNOWN_CONNECTION_MANAGERS`.

**Resultado**: `python -m unittest discover -s tests` → **88/88 en verde**
(60 de las iteraciones anteriores, sin ningún cambio, + 28 nuevos).

---

## 6. Integración con el Generator de Campanias — ✅ APLICADA

**Actualizado**: esta sección describía originalmente un análisis de qué
podría eliminarse, sin aplicarlo. En la iteración siguiente (integración
mínima Project Context ↔ Generator) se implementó tal como estaba propuesto:

1. **`generator/xml_helpers.KNOWN_CONNECTION_MANAGERS`** — **ELIMINADO**. Ya no existe en el código. La resolución de `connectionManagerID` pasa siempre por `ProjectContext`.
2. **`generator/xml_helpers.resolve_connection_manager_id(name)`** — **ELIMINADO** (junto con `UnknownConnectionManagerError`). Reemplazado por `project_context.validator.get_connection_dtsid(context, name)` + un formateador puro nuevo, `generator/xml_helpers.format_connection_manager_id(dtsid)`, que solo aplica el sufijo `:external` (no busca ni conoce ningún GUID).
3. **`generator/spec_validator.py`** — el chequeo "conexión soportada" ahora es "conexión existe en `project_context['connections']`" **y** "su provider es el esperado para ese rol" (`TERADATA` para source, `OLEDB` para destination) — dos errores distintos y reconocibles por separado.
4. **Provider explícito** — ya no es un supuesto implícito: `spec_validator._validate_connection_reference` lo verifica activamente contra el `ProjectContext` real antes de generar nada.
5. **`RetainSameConnection`** — sigue sin usarse en el generador de Campanias (no aplica, ver más abajo); `ProjectContext` lo sigue leyendo y modelando igual.

`generator/campanias_generator.generate()` ahora requiere un `project_context`
real como segundo argumento posicional:
`generate(spec, project_context, template_path, output_path)`. Ver
`tests/test_generator_campanias.py::ProjectContextIntegrationTests` para la
prueba end-to-end contra los fixtures reales de `BipSuc`, y el reporte de
esa iteración para el detalle completo de la migración.

---

## 7. Limitaciones vigentes de `project-context-v1`

1. Solo dos providers con builder específico: `TERADATA` y `OLEDB`. Cualquier otro (`ODBC`, `ADO.NET`, `FLATFILE`, etc.) cae en el fallback genérico (`unknown:<provider>`), sin perder el DTSID/nombre/Property Expressions, pero sin interpretar su `ObjectData` específico.
2. No se lee ni resuelve ninguna expresión SSIS más allá de detectar referencias `$Project::nombre` por regex — no se evalúa la expresión completa (ej. el operador ternario de `InitialCatalog` no se resuelve a `"Optimus"`, solo se detecta que depende de `ambiente`).
3. No hay integración con `generator/campanias_generator.py` — ver sección 6, es una propuesta, no un cambio aplicado.
4. No se generaliza el mapeo `CM.<nombre>.<propiedad>` más allá de agruparlo; no se interpreta cada propiedad individualmente salvo `RetainSameConnection`, que es la única pedida explícitamente en esta iteración.
5. `packages_declared` (de `SSIS:Packages`) y `packages` (de `SSIS:PackageInfo/PackageMetaData`) son dos listas separadas que, en los datos reales, describen el mismo conjunto de archivos desde dos secciones distintas del `.dtproj` — no se intentó fusionarlas en una sola, para no inventar una correspondencia no declarada explícitamente por el propio archivo.
6. Ningún valor sensible se decodifica ni se decodifica jamás — si en el futuro hiciera falta el valor real de un parámetro sensible (por ejemplo, para ejecutar algo), esa es una decisión de seguridad distinta y explícitamente fuera de alcance de este analizador.
