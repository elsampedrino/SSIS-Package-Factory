# `teradata-to-sql-profile-v1`

**STATUS: CLOSED — STABLE / SSDT VALIDATED.**

El Gate SSDT manual fue completado y confirmado por el usuario (ver "Gate
SSDT — resultado final" más abajo). `control-flow-v1` sigue **BLOCKED /
EXPERIMENTAL** y no fue tocado (los cambios backward-compatible descritos
abajo se verificaron explícitamente contra la suite completa de Control
Flow, 62/62 tests, sin ningún cambio de comportamiento). `project-generation-v1`
y `template-teradata-to-sql-v1.1` siguen siendo la base estable —
`project_generator/` no se modificó en absoluto; `generator/` se extendió
de forma aditiva y opcional (ver §7/§9).

## Gate SSDT — resultado final (✅ PASS)

Confirmado manualmente por el usuario sobre los artefactos regenerados de
`generated_profile_v1/` + `TeradataToSqlProfileV1_E2E.dtsx`:

| Verificación | Resultado |
|---|---|
| `Project.params` (`ambiente`, `dbTeradata`, `pwTeradata` shell, `srvTeradata`, `usrTeradata`, `srvSRVTESTDB`, `pwSRVTESTDB` shell) visibles y editables | PASS |
| Parámetros sensibles sin secreto persistido | PASS |
| SQL Connection Manager: usuario `usSxSSIS`; `PropertyExpression` `ServerName→@[$Project::srvSRVTESTDB]`, `Password→@[$Project::pwSRVTESTDB]` | PASS |
| Teradata Connection Manager: `PropertyExpression` `Database→@[$Project::dbTeradata]`, `Password→@[$Project::pwTeradata]`, `ServerName→@[$Project::srvTeradata]`, `UserName→@[$Project::usrTeradata]` | PASS |
| Teradata Source (Advanced Editor): `MinSessions=4`, `MaxSessions=8` | PASS |
| Package `ProtectionLevel = EncryptSensitiveWithPassword` | PASS |
| Ausencia visual de `pwUsSxSSIS` | PASS |
| Guardado, cierre completo de Visual Studio, reapertura de la solución y del package | PASS |
| Sin `LoadFromXML`, sin corrupción, sin pérdida de componentes/conexiones, sin referencias inválidas | PASS |

No se exigió ejecución/conectividad real — los datos del gate son
deliberadamente ficticios.

## Corrección funcional (detectada durante el Gate SSDT manual)

La primera implementación de este milestone generaba, para la conexión SQL,
un parámetro de servidor **configurable por proyecto** más un parámetro de
password con nombre **canónico fijo** (`pwUsSxSSIS`). El Gate SSDT manual
detectó que esto no representa correctamente la convención corporativa real:

> Para cada servidor SQL, **tanto** el parámetro `ServerName` **como** el
> parámetro `Password` se nombran a partir del **nombre del servidor**:
> `srv<NombreServidor>` / `pw<NombreServidor>`.

Ejemplo: servidor `SRVBSQADB` → `srvSRVBSQADB` / `pwSRVBSQADB` (nunca
`pwUsSxSSIS`). El usuario SQL sigue siendo un default fijo del profile
(`usSxSSIS`), y **nunca** se convierte en Project Parameter.

Igual que la corrección de `ambiente` en la auditoría previa, esto es una
**convención corporativa declarada explícitamente para proyectos nuevos**,
no una inferencia del corpus histórico — de hecho, ningún proyecto real
disponible sigue esta convención tal cual (BipSuc nombra su parámetro SQL
por un alias corto, `srvBsLogSBD01`/`pwSrvBsLogSBD01`; PagosYRecaudaciones
nombra su password por el usuario fijo, `pwUsSxSSIS`) — ver §7 para el
detalle completo, incluida la sanitización necesaria para servidores con
`.` en el nombre (evidenciado: `SRVBSDESADB.child01.root.test`).

`pwUsSxSSIS` **ya no existe** en ningún lugar del profile nuevo (código,
specs de gate, artefactos generados, tests, documentación) — sigue
apareciendo, sin cambios, únicamente como evidencia histórica en
`Examples/Originals/PagosYRecaudaciones_FacturacionComi/` (fixture real, no
tocado).

## Objetivo (capa opcional)

```
MinimalSpec
    |
TERADATA_TO_SQL Profile Resolver   (profiles/, nuevo)
    |
ProjectSpec completo  +  ProcessSpec completo/enriquecido
    |
project_generator existente  +  campanias_generator existente   (SIN cambios de arquitectura)
    |
Project.params / *.conmgr / *.dtsx
```

`profiles/` es una capa **anterior y opcional**: los specs explícitos
actuales (`ProjectSpec`/`ProcessSpec` completos, sin pasar por ningún
profile) siguen funcionando exactamente igual — confirmado con regresión
completa (324 tests, 0 fallos) y con tests de compatibilidad hacia atrás
dedicados (ver §14).

## 1. Arquitectura implementada

```
profiles/
  __init__.py
  minimal_spec_schema.py       -- validate_minimal_spec()/assert_valid_minimal_spec()
  teradata_to_sql_profile.py   -- defaults corporativos + resolve_teradata_to_sql_profile()
```

`resolve_teradata_to_sql_profile(minimal_spec)` devuelve
`{"project_spec": ..., "process_spec": ...}` — nada más. No escribe
archivos, no construye `ProjectContext`, no genera XML. El caller (script,
test, o una futura CLI) sigue orquestando, sin cambios, exactamente el mismo
flujo de siempre:

```python
resolved = resolve_teradata_to_sql_profile(minimal_spec)
generate_project_resources(resolved["project_spec"], output_dir)   # project_generator, sin cambios
# ... build_project_context() ...                                   # project_context, sin cambios
generate(resolved["process_spec"], project_context, template_path, output_path)  # campanias_generator
```

## 2. Archivos creados/modificados

**Nuevos**: `profiles/__init__.py`, `profiles/minimal_spec_schema.py`,
`profiles/teradata_to_sql_profile.py`, `tests/test_teradata_to_sql_profile.py`,
`specs/teradata_to_sql_profile_v1_gate.json`, `generated_profile_v1/*`,
`TeradataToSqlProfileV1_E2E.dtsx`, este documento.

**Modificados, de forma aditiva y opcional** (ver §7/§9 para el detalle
backward-compat): `generator/spec_validator.py` (nuevo
`SUPPORTED_PACKAGE_PROTECTION_LEVELS`, nueva `_validate_optional_teradata_source_sessions`,
2 checks opcionales agregados **solo** en el branch legacy de
`validate_spec()` — cero líneas tocadas en `_validate_data_flow` ni en
`_validate_control_flow_package`, que Control Flow comparte); `generator/campanias_generator.py`
(nuevo `PROTECTION_LEVEL_CODES`, 2 bloques `if` opcionales en
`build_package_tree()`/`_build_teradata_source()`). `project_generator/` **no
se tocó**.

## 3. `MinimalSpec` final

```json
{
  "profile": "teradata_to_sql",
  "environment": "QA",
  "teradata": {
    "charset": "ASCII"
  },
  "sql": {
    "server": "SRVTESTDB",
    "catalog": "TestCatalog"
  },
  "process": {
    "package_name": "TeradataToSqlProfileV1_E2E",
    "data_flow_name": "Flujo E2E Profile",
    "source_name": "Origen Teradata",
    "sql": "SELECT Id_Test, Desc_Test FROM D_TEST.Factory_Test",
    "columns": [
      {"name": "Id_Test", "data_type": "i4"},
      {"name": "Desc_Test", "data_type": "wstr", "length": 100}
    ],
    "destination_name": "Destino SQL",
    "table": "[staging].[Factory_Test]",
    "mappings": [
      {"source": "Id_Test", "target": "Id_Test", "target_data_type": "i4"},
      {"source": "Desc_Test", "target": "Desc_Test", "target_data_type": "wstr", "target_length": 100}
    ]
  }
}
```

`sql.server_parameter_name`/`sql.password_parameter_name` **NO aparecen** en
este ejemplo porque son opcionales: por convención se **derivan siempre**
de `sql.server` (`srv<Servidor>`/`pw<Servidor>`, ver §7) — para
`SRVTESTDB` esto produce `srvSRVTESTDB`/`pwSRVTESTDB`. Overrides opcionales
disponibles: `teradata.server/database/user`, `sql.user`,
`sql.server_parameter_name`/`sql.password_parameter_name` (si se necesita
fijar un nombre distinto al derivado), `teradata_source.min_sessions/max_sessions`,
`protection_level` (top-level). `environment` y `sql.server/catalog` son
**siempre obligatorios**, nunca tienen default.

## 4. `ProjectSpec` resuelto (ejemplo real, del gate)

```json
{
  "project": {
    "parameters": [
      {"name": "srvTeradata", "sensitive": false, "value": "tdtest.example.local"},
      {"name": "dbTeradata", "sensitive": false, "value": "D_DW_TEST"},
      {"name": "usrTeradata", "sensitive": false, "value": "usTestTeradata"},
      {"name": "pwTeradata", "sensitive": true},
      {"name": "srvSRVTESTDB", "sensitive": false, "value": "SRVTESTDB"},
      {"name": "pwSRVTESTDB", "sensitive": true},
      {"name": "ambiente", "sensitive": false, "value": "QA"}
    ],
    "connections": [
      {
        "name": "cnxTeradata", "provider": "teradata",
        "server": "tdtest.example.local", "database": "D_DW_TEST", "user": "usTestTeradata", "charset": "ASCII",
        "property_expressions": [
          {"property": "ServerName", "parameter": "srvTeradata"},
          {"property": "Database", "parameter": "dbTeradata"},
          {"property": "UserName", "parameter": "usrTeradata"},
          {"property": "Password", "parameter": "pwTeradata"}
        ]
      },
      {
        "name": "cnxSql", "provider": "oledb",
        "server": "SRVTESTDB", "catalog": "TestCatalog", "user": "usSxSSIS",
        "property_expressions": [
          {"property": "ServerName", "parameter": "srvSRVTESTDB"},
          {"property": "Password", "parameter": "pwSRVTESTDB"}
        ]
      }
    ]
  }
}
```

Este `ProjectSpec` es **100% válido contra `project_generator.spec_schema`
sin ningún cambio** (verificado con test dedicado).

## 5. `ProcessSpec` resuelto (ejemplo real, del gate)

```json
{
  "package": {"name": "TeradataToSqlProfileV1_E2E", "protection_level": "EncryptSensitiveWithPassword"},
  "data_flow": {
    "name": "Flujo E2E Profile",
    "source": {
      "type": "teradata", "name": "Origen Teradata", "connection": "cnxTeradata",
      "sql": "SELECT Id_Test, Desc_Test FROM D_TEST.Factory_Test",
      "columns": [...],
      "min_sessions": 4, "max_sessions": 8
    },
    "transformations": [],
    "destination": {
      "type": "ole_db", "name": "Destino SQL", "connection": "cnxSql",
      "table": "[staging].[Factory_Test]", "mappings": [...]
    }
  }
}
```

## 6. Defaults TERADATA

| Parámetro | Nombre | Default corporativo | Override |
|---|---|---|---|
| Server | `srvTeradata` | `tdgnn.ccba.usr.bpba` | `teradata.server` |
| Database | `dbTeradata` | `D_DW_APPLICATIONS` | `teradata.database` |
| User | `usrTeradata` | `D_DW_APPLICATIONS_USR` | `teradata.user` |
| Password | `pwTeradata` | — (shell, nunca tiene valor) | N/A |
| Charset | — | **sin default** | `teradata.charset` (obligatorio) |

`PropertyExpression` generadas **siempre**: `ServerName→srvTeradata`,
`Database→dbTeradata`, `UserName→usrTeradata`, `Password→pwTeradata` —
formaliza el patrón correcto (evidenciado completo solo en 1 de 2 proyectos
históricos; ahora es el comportamiento único y consistente del profile).

Constantes centralizadas **únicamente** en `profiles/teradata_to_sql_profile.py`
(`CORPORATE_TERADATA_SERVER/DATABASE/USER`) — ningún otro módulo las conoce.

## 7. Defaults SQL — convención de nombres derivada del servidor

**Corrección aplicada** (ver "Corrección funcional" al inicio de este
documento): tanto el parámetro `ServerName` como el `Password` de la
conexión SQL se derivan **siempre** del nombre del servidor destino, nunca
de un nombre configurable independiente ni del usuario fijo:

```
ServerName parameter = "srv" + <NombreServidor>
Password parameter   = "pw"  + <NombreServidor>
```

| Parámetro | Nombre | Origen | Override |
|---|---|---|---|
| Server | `srv<Servidor>` (derivado de `sql.server`) | siempre explícito (`sql.server`), sin default | `sql.server_parameter_name` |
| Password | `pw<Servidor>` (derivado de `sql.server`) | shell, nunca tiene valor | `sql.password_parameter_name` |
| User | **no es Project Parameter** | `usSxSSIS` (`CORPORATE_SQL_USER`) | `sql.user` |
| Catalog | — | siempre explícito, sin default | — |

`PropertyExpression` generadas: `ServerName→srv<Servidor>`,
`Password→pw<Servidor>`. **Nunca** se genera una `PropertyExpression` para
`UserName` — el usuario SQL queda embebido literal en el `ConnectionString`
derivado, exactamente como en el 100% de los casos reales auditados.

**Sanitización del fragmento `<Servidor>`**
(`profiles.minimal_spec_schema.sanitize_server_name_fragment`): se eliminan
todos los caracteres que no son letras/dígitos (`.`, `\`, `-`, espacios,
etc.) antes de anteponer `srv`/`pw`. Necesario porque un Project Parameter
se referencia dentro de una expresión SSIS (`@[$Project::<nombre>]`), y un
nombre de servidor real puede traer `.` — evidenciado en el corpus
(`SRVBSDESADB.child01.root.test`, PagosYRecaudaciones). Ejemplo:
`SRVBSDESADB.child01.root.test` → `srvSRVBSDESADBchild01roottest` /
`pwSRVBSDESADBchild01roottest`. No hay evidencia real de que el equipo
abrevie un FQDN de otra forma al nombrar un parámetro — se aplica la regla
más simple y predecible posible en vez de inventar un esquema de
abreviación; si el resultado no es el deseado para un caso puntual, usar
los overrides explícitos (`sql.server_parameter_name`/`sql.password_parameter_name`).
Si el servidor no tiene ningún carácter alfanumérico (caso degenerado), la
validación del `MinimalSpec` lo rechaza explícitamente pidiendo un override.

## 8. `ambiente`

**Corrección explícita respecto de la auditoría previa**: no es una
inferencia del corpus histórico (donde aparecía en 1 de 2 proyectos) sino
una **convención corporativa actual**, declarada por el usuario para
proyectos nuevos. Por eso el profile **siempre** genera el Project Parameter
`ambiente` (`String`, `sensitive=false`, `required=true`), con el valor
resuelto de `environment` (`"QA"` o `"Produ"`, **sin default** — el
MinimalSpec debe declararlo siempre explícitamente, para nunca asumir uno).

**No implementado, deliberadamente**: la expresión ternaria de
`InitialCatalog` (`@[$Project::ambiente] == "QA" ? "Optimus" : "Migas"`) que
consume `ambiente` en el único caso histórico conocido. El parámetro
`ambiente` queda generado y disponible en `Project.params`, listo para que
una capacidad futura lo consuma — pero ninguna conexión generada por este
profile lo referencia todavía en v1.

## 9. `ProtectionLevel` explícito

Antes de este milestone, `ProtectionLevel` se heredaba **implícitamente**
del template (`templates/campanias_base.dtsx`, que a su vez lo hereda de
`BipSuc_CampaniasVigentes.dtsx`) — sin ninguna garantía de código de que
siguiera siendo así en el futuro.

Ahora es explícito y opcional: `spec["package"]["protection_level"]`
(`ProcessSpec`) — ausente preserva el comportamiento actual (heredado del
template, sin tocar el atributo); presente, `build_package_tree()`
sobrescribe `DTS:ProtectionLevel` con el código evidenciado:

```python
PROTECTION_LEVEL_CODES = {
    "EncryptSensitiveWithUserKey": "1",   # SSDT_Golden (default de fabrica, sin customizar)
    "EncryptSensitiveWithPassword": "2",  # BipSuc + PagosYRecaudaciones (ambos proyectos productivos reales)
}
```

El profile resuelve siempre `"EncryptSensitiveWithPassword"` salvo override
explícito (`protection_level` en el `MinimalSpec`).

### Confirmación definitiva del estándar (post-Gate)

Durante el Gate SSDT surgió la duda de si el estándar corporativo real
debía ser `EncryptAllWithPassword` en lugar de `EncryptSensitiveWithPassword`.
Se realizó una auditoría READ-ONLY exploratoria de `EncryptAllWithPassword`
(sin implementar nada, sin tocar código) para entender su viabilidad técnica.
Posteriormente, el usuario localizó la documentación interna oficial del
procedimiento de trabajo, que confirma explícitamente que el estándar real
—tanto a nivel proyecto como a nivel package— **es y sigue siendo
`EncryptSensitiveWithPassword`**, consistente con toda la evidencia
histórica del repo (100% de los proyectos/packages productivos reales
disponibles).

**`EncryptAllWithPassword` queda registrada únicamente como hipótesis
explorada y descartada** — no representa una decisión de arquitectura, no
fue ni será implementada en `teradata-to-sql-profile-v1`. `PROTECTION_LEVEL_CODES`
NO incluye ni incluirá el código `3` (`EncryptAllWithPassword`) mientras
esta decisión se mantenga vigente.

## 10. `MinSessions`/`MaxSessions` explícitos

Mismo patrón que `ProtectionLevel`: antes se heredaban implícitamente del
template; ahora `spec["data_flow"]["source"]["min_sessions"/"max_sessions"]`
son opcionales en el `ProcessSpec` — ausentes preservan el comportamiento
actual; presentes, `_build_teradata_source()` sobrescribe las properties
`MinSessions`/`MaxSessions` ya existentes en el template.

El profile resuelve siempre `4`/`8` (`CORPORATE_MIN_SESSIONS`/`CORPORATE_MAX_SESSIONS`)
salvo override explícito (`teradata_source.min_sessions/max_sessions`).

## 11. Overrides — resumen

| Campo | Estrategia |
|---|---|
| Teradata server/database/user | default-with-override |
| SQL user | default-with-override |
| Nombre de parámetro SQL server/password (`srv<Servidor>`/`pw<Servidor>`) | derivado automático, con override explícito opcional (`sql.server_parameter_name`/`sql.password_parameter_name`) |
| `MinSessions`/`MaxSessions` | default-with-override |
| `ProtectionLevel` | default-with-override |
| `pwTeradata`/`pw<Servidor>` | **nunca** override con secreto — siempre shell |
| SQL server/catalog | siempre explícito, sin default |
| Teradata `charset` | siempre explícito, sin default |
| `environment` (`ambiente`) | siempre explícito, sin default |
| `process` (Data Flow específico) | siempre explícito, sin default |

## 12. Security guarantees

- `pwTeradata`/`pw<Servidor>` se generan **siempre** como shell (`sensitive=true`,
  sin clave `"value"` en el dict Python — ni siquiera vacía) — verificado con
  test dedicado (`test_sensitive_parameters_never_have_value_key`).
- `pwUsSxSSIS` **no existe** en ningún spec resuelto por el profile —
  verificado con test dedicado (`test_pw_us_sx_ssis_never_appears_anywhere_in_the_profile`).
- Ningún módulo del profile conoce ni puede conocer la contraseña real
  corporativa — no hay ningún campo, constante ni ruta de código que la
  reciba o persista.
- `PackagePassword`: **NO implementado**. No agregado a `MinimalSpec`, a
  `ProjectSpec` ni a `ProcessSpec`. **`PackagePassword` requires runtime
  secret injection and is outside Factory v1 scope.**
- `PasswordVerifier` (el hash cifrado a nivel `.dtproj` que usa el esquema
  `EncryptSensitiveWithPassword`): **nunca** generado ni copiado — ni
  siquiera es alcanzable, porque `.dtproj` sigue completamente fuera de
  scope de `project_generator`.
- Tests de seguridad dedicados verifican, sobre los `ProjectSpec`/`ProcessSpec`
  resueltos Y sobre el `.dtsx`/`.conmgr`/`Project.params` generados
  realmente, la ausencia de: `Salt=`, `IV=`, `Algorithm=`, `<DTS:Password`,
  `<TeraPassword`, `PasswordVerifier`, `aes256-cbc`.

## 13. `InitialCatalog` por `ambiente` — confirmación de scope

Explícitamente **fuera de scope de v1** (ver §8). No se generó ninguna
`PropertyExpression` ternaria, no se extendió `project_generator.PropertyExpressions`
(sigue soportando únicamente `property→parameter` simple), no se agregó
ningún mecanismo de expresión cruda. Verificado con test dedicado
(`test_ternary_initial_catalog_not_generated`).

## 14. Backward compatibility

Verificado explícitamente, no solo por inferencia:

- `specs/campanias_generated.json` (spec legacy real, sin `protection_level`
  ni `min_sessions`/`max_sessions`) sigue pasando `validate_spec()` sin
  errores, y el `.dtsx` generado a partir de él sigue teniendo
  `DTS:ProtectionLevel="2"` y `MinSessions=4`/`MaxSessions=8` — **los mismos
  valores heredados del template de siempre**, confirmando que las
  extensiones son puramente aditivas.
- La suite completa de `tests/test_control_flow.py` (62 tests) se ejecuta
  explícitamente desde dentro de la suite de este milestone
  (`test_control_flow_suite_unaffected`) para confirmar cero efecto — los 2
  checks nuevos de `spec_validator.py` viven **únicamente** en el branch
  legacy de `validate_spec()`, nunca en `_validate_data_flow` ni en
  `_validate_control_flow_package` (las funciones que Control Flow
  comparte).
- Regresión completa del repositorio: **324/324 tests, 0 fallos** (268
  previos + 56 nuevos).

## 15. Tests nuevos

`tests/test_teradata_to_sql_profile.py` — 56 tests: validación de
`MinimalSpec` (16, incluida la convención `srv<Servidor>`/`pw<Servidor>` —
nombres de parámetro SQL ahora opcionales/derivados, rechazo de servidor sin
caracteres alfanuméricos), resolución del profile (23: defaults/overrides
TERADATA/SQL, derivación `srv<Servidor>`/`pw<Servidor>` con el ejemplo
exacto `SRVBSQADB`→`srvSRVBSQADB`/`pwSRVBSQADB`, un segundo servidor
`SRVDB2016QA` para confirmar que no quedó hardcodeado, overrides explícitos
de nombre de parámetro, `ambiente` QA/Produ, PropertyExpressions,
`ProtectionLevel`, `MinSessions`/`MaxSessions`, confirmación de que el
ternario NO se genera), seguridad (4, incluida la ausencia explícita de
`pwUsSxSSIS`), `ProtectionLevel`/sesiones reflejados en el XML real generado
con y sin override (3), compatibilidad hacia atrás (4, incluida la
re-ejecución completa de Control Flow), y E2E completo (5: `MinimalSpec` →
profile → `project_generator` → `ProjectContext` → `campanias_generator` →
`ssis_parser`/`ssis_validator`).

**324 tests en total, 0 fallos.**

## 16. Artefactos para el gate manual

`specs/teradata_to_sql_profile_v1_gate.json` (`MinimalSpec` de prueba,
valores ficticios, demuestra el override de `teradata.server/database/user`
y la derivación automática `srv<Servidor>`/`pw<Servidor>` para SQL,
regenerado tras la corrección) → generado a:
- `generated_profile_v1/Project.params` (7 parámetros: `srvTeradata`/
  `dbTeradata`/`usrTeradata` no sensibles, `pwTeradata` shell,
  `srvSRVTESTDB` no sensible, `pwSRVTESTDB` shell, `ambiente`).
- `generated_profile_v1/cnxTeradata.conmgr` (4 `PropertyExpression`).
- `generated_profile_v1/cnxSql.conmgr` (2 `PropertyExpression`:
  `ServerName→srvSRVTESTDB`, `Password→pwSRVTESTDB`; usuario `usSxSSIS`
  hardcodeado en el `ConnectionString`, **`pwUsSxSSIS` ya no aparece**).
- `TeradataToSqlProfileV1_E2E.dtsx` (repo root) — `ProtectionLevel="2"`,
  `MinSessions=4`/`MaxSessions=8`, parser/validator en verde, cero secretos
  (confirmado por grep dirigido sobre los 4 archivos, incluida la ausencia
  de `pwUsSxSSIS`).

## 17. Limitaciones (no son bugs)

- `.dtproj` sigue sin generarse/mutarse — misma limitación heredada de
  `project-generation-v1`.
- `PackagePassword` fuera de scope permanente — requiere secreto en
  runtime.
- `InitialCatalog` por `ambiente` fuera de scope de v1 — `ambiente` existe
  como Project Parameter, pero ninguna conexión lo consume todavía; una
  capacidad futura que quiera hacerlo necesitará extender
  `project_generator.PropertyExpressions` de forma aditiva (nuevo "tipo" de
  expresión, sin tocar el mecanismo simple ya validado).
- Los defaults corporativos (`CORPORATE_TERADATA_SERVER/DATABASE/USER`,
  etc.) son valores de texto plano no sensibles, pero SÍ son datos
  operativos reales de la empresa — viven centralizados en
  `profiles/teradata_to_sql_profile.py`, ya visibles también en los
  fixtures versionados de `Examples/Originals/`.
- **Colisión teórica de sanitización de nombres SQL (conocida, no
  resuelta).** `sanitize_server_name_fragment()` elimina todo carácter no
  alfanumérico del nombre de servidor para derivar `srv<Servidor>`/
  `pw<Servidor>` (ver §7). Esto puede, en teoría, hacer que dos servidores
  distintos deriven al mismo nombre de parámetro — ejemplo conceptual:
  `SRV-A` y `SRVA` colisionarían en `srvSRVA`/`pwSRVA`. **No hay evidencia
  de una colisión real** en ningún proyecto disponible. Se documenta como
  limitación conocida, deliberadamente **sin diseñar un mecanismo de
  detección/prevención nuevo** (fuera de scope de v1) — los overrides ya
  existentes (`sql.server_parameter_name`/`sql.password_parameter_name`)
  sirven como *escape hatch* manual si algún día se necesitara.
- **`EncryptAllWithPassword` fue explorado y descartado** — ver nota en §9.
  El estándar corporativo confirmado, tanto por evidencia histórica como
  por documentación interna oficial, es `EncryptSensitiveWithPassword`.

## 18. Gate SSDT — resultado final (✅ PASS)

Ver tabla completa al inicio de este documento ("Gate SSDT — resultado
final"). Artefactos usados: ver §16.

1. Add Existing Item de `Project.params`, `cnxTeradata.conmgr`,
   `cnxSql.conmgr` y `TeradataToSqlProfileV1_E2E.dtsx` en un proyecto SSIS
   limpio y ya existente.
2. Apertura del proyecto en SSDT sin error (`LoadFromXML`).
3. Project Parameters: `srvTeradata`/`dbTeradata`/`usrTeradata`/`srvSRVTESTDB`
   visibles con su valor; `pwTeradata`/`pwSRVTESTDB` visibles como
   sensibles, **sin valor** (y `pwUsSxSSIS` **NO debe aparecer** en ningún
   lado); `ambiente` visible con valor `QA`.
4. Connection Manager TERADATA: reconocido, `PropertyExpression` en
   `ServerName`/`Database`/`UserName`/`Password` resueltas visualmente.
5. Connection Manager OLEDB: reconocido, `PropertyExpression` en
   `ServerName`/`Password` resueltas; usuario `usSxSSIS` visible en la
   connection string (no como parámetro).
6. Abrir el package: Data Flow visible (Teradata Source → OLE DB
   Destination).
7. Propiedades del Teradata Source: `MinSessions=4`, `MaxSessions=8`.
8. Propiedades del proyecto/package: `ProtectionLevel = EncryptSensitiveWithPassword`.
9. Cerrar y reabrir Visual Studio — persistencia sin errores.

No se exigió conectividad real — todos los valores (`tdtest.example.local`,
`SRVTESTDB`, `[staging].[Factory_Test]`) son ficticios.

Los 9 pasos pasaron. Ver tabla de evidencia al inicio del documento.

## 19. Cierre del milestone

Con el Gate SSDT en PASS y 324/324 tests automáticos en verde,
`teradata-to-sql-profile-v1` queda formalmente cerrado:

**`teradata-to-sql-profile-v1 = STABLE / SSDT VALIDATED`**

El circuito `MinimalSpec → Profile Resolver → ProjectSpec + ProcessSpec →
project_generator + campanias_generator (existentes, sin arquitectura XML
paralela) → Project.params/*.conmgr/*.dtsx → SSDT` fue validado
manualmente en SSDT de punta a punta, sin modificar `project_generator/` y
extendiendo `generator/` de forma exclusivamente aditiva y opcional (specs
legacy sin cambios de comportamiento). `control-flow-v1` permanece
`BLOCKED / EXPERIMENTAL`, sin tocar.

Las limitaciones documentadas en §17 (`.dtproj` no generado/mutado,
`PackagePassword` fuera de scope permanente, `InitialCatalog` por
`ambiente` fuera de scope, colisión teórica de sanitización sin resolver)
**siguen vigentes** y son el punto de partida de cualquier milestone futuro
que amplíe este scope. Ninguna de esas ampliaciones fue iniciada en este
cierre.
