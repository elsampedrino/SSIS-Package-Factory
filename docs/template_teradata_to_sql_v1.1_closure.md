# Cierre de `template-teradata-to-sql-v1.1`

Este documento cierra el ciclo abierto por la primera prueba de generalización del template congelado contra un segundo caso real (`PagosYRecaudaciones_FacturacionComi.dtsx`, ver `docs/template_teradata_to_sql_v1_generalization_pagosyrecaudaciones.md`). Esa auditoría dejó un resultado de **PASS PARCIAL**, con tres gaps identificados. `v1.1` cierra los dos gaps de Data Flow; el tercero (Control Flow) queda explícitamente diferido.

## Gaps identificados en la generalización (recordatorio)

1. **Conversión str→wstr sin metadata de longitud/code page** — bug de generator + capability gap de schema. `_build_data_conversion()` descartaba incondicionalmente `length`/`code_page` de cualquier conversión, invisible en Campanias porque sus 2 conversiones reales targetean `dbDate` (que no necesita longitud).
2. **`numeric` con `precision`/`scale`** (`Mto_Evento(15,2)`, `Num_Cambio(9,5)`) — capability gap de schema: ningún nivel del spec podía expresar precision/scale.
3. **OLE DB Destination.AccessMode** (descubierto durante la validación manual en SSDT del artefacto sintético, no en la auditoría original) — Campanias real=`0`, PagosYRecaudaciones real=`3` (Fast Load), template=`0`, generator no lo sobrescribía nunca.
4. **Control Flow dinámico** (Script Task + variable + `PropertyExpression` sobre el Data Flow) — estructural, **fuera de alcance de v1.1**, diferido (ver sección "Fuera de alcance" más abajo).

## Qué cierra v1.1

### 1. Metadata de conversiones con target string (`conversions[]`)

**Schema** (`generator/spec_validator.py`): nuevos campos opcionales-condicionales en `data_flow.transformations[].conversions[]`:

| Campo | Requerido cuando |
|---|---|
| `target_length` | `target_type ∈ {"str","wstr"}` |
| `target_code_page` | `target_type == "str"` |
| `target_precision`, `target_scale` | `target_type == "numeric"` |

Mismos nombres que ya usaba `destination.mappings[]` (`target_length`/`target_code_page`), extendidos por simetría — ninguna convención nueva.

**Generator** (`generator/campanias_generator.py::_build_data_conversion`): ahora lee esos campos del spec y los propaga tanto al `<outputColumn>` del componente Data Conversion como a `pipeline_columns[output_name]`, en vez de hardcodear `length`/`code_page` en `None` incondicionalmente. Esto es lo que permite que `_build_ole_db_destination()` recupere correctamente `cachedLength`/`cachedCodepage` aguas abajo.

### 2. `numeric` con `precision`/`scale`

**Schema**: `source.columns[]` acepta `precision`/`scale` (requeridos cuando `data_type == "numeric"`); `destination.mappings[]` acepta `target_precision`/`target_scale` (requeridos cuando `target_data_type == "numeric"`) — simétrico a lo agregado en `conversions[]`.

**Generator**: `_build_teradata_source` propaga `precision`/`scale` al `outputColumn`/`externalMetadataColumn` del Source y a `pipeline_columns`; `_build_ole_db_destination` escribe `cachedPrecision`/`cachedScale` en el `inputColumn` (desde `pipeline_columns`) y `precision`/`scale` en el `externalMetadataColumn` (desde el mapping).

### 3. OLE DB Destination.AccessMode

**Schema**: `destination.access_mode` — entero opcional. Ausente → se preserva el valor que trae el template (`0`, igual que Campanias real). Presente → sobrescribe la property `AccessMode`.

**Validación**: solo se exige tipo (`entero >= 0`, reutilizando `_non_negative_int`), sin restringir a un enum cerrado — la única evidencia real disponible son los valores `0` y `3`; fijar un rango (p. ej. 0-4) hubiera sido una suposición no verificada sobre el enum interno de SSIS.

**Generator** (`_build_ole_db_destination`): `if "access_mode" in spec: _set_property_text(properties_el, "AccessMode", str(spec["access_mode"]))`. Ninguna otra property de Fast Load (`FastLoadOptions`, `FastLoadKeepIdentity`, `FastLoadKeepNulls`, `FastLoadMaxInsertCommitSize`) se tocó — sin evidencia real de que varíen entre paquetes.

## Validación

- **146 tests, 0 fallos** (110 preexistentes a este ciclo + 24 de metadata de conversiones/numeric + 12 de AccessMode).
- **Campanias**: regenerado y verificado en cada incremento — misma topología (3 componentes, 2 paths), mismas 2 conversiones `str→dbDate`, `AccessMode=0` preservado, `ssis_parser`+`ssis_validator` dan `valid=True` con 0 errores. Cero regresiones en todo el ciclo.
- **Paquete sintético** (`specs/synthetic_string_and_numeric_conversion_pagosyrecaudaciones_context.json` → `SyntheticStringAndNumericConversion.dtsx`, generado con el `ProjectContext` real de `PagosYRecaudaciones_FacturacionComi`, conexiones `cnxTeradata`/`cnxSQL` reales): `valid=True`, 0 errores, 3 componentes, 2 paths, `AccessMode=3`. **Validado manualmente en SSDT por el usuario**: las 6 conversiones str→wstr quedaron exactamente iguales al paquete original, incluyendo longitudes — ese gate quedó confirmado end-to-end, no solo a nivel de test automatizado.
- Existe además un segundo spec sintético (`specs/synthetic_string_and_numeric_conversion.json`, con `access_mode: 3` también) usado exclusivamente por el test suite automatizado contra el `ProjectContext` de BipSuc — deliberadamente separado del anterior porque ese test es puramente estructural/de schema y no depende de a qué base apunten los Connection Manager (ver nota `_note` de ambos archivos).

## Fuera de alcance de v1.1 (deferred)

El gap de Control Flow (Script Task que calcula fechas → variable de paquete → `PropertyExpression` sobre `[Teradata Source].[SqlCommand]`, más el `PrecedenceConstraint` entre dos executables) **no se tocó**, tal como se decidió en la comparación de alternativas de la auditoría original: es un gap estructural, no de tipo, y generalizar una capa de Control Flow con un solo caso real observado (n=1) sería anticipar diseño sin evidencia suficiente. Sigue recomendada la Alternativa C (diferir) hasta un tercer caso real que confirme el patrón.

Tampoco se tocaron (sin cambios en este ciclo): `FastLoadOptions`/`FastLoadKeepIdentity`/`FastLoadKeepNulls`/`FastLoadMaxInsertCommitSize`, Mapping Planner, inferencia automática de conversiones, ni ningún archivo de `project_context/`.

## Archivos de este ciclo

- `generator/spec_validator.py` — reglas de `conversions[]`/`source.columns[]`/`destination.mappings[]` para string/numeric, y `destination.access_mode`.
- `generator/campanias_generator.py` — propagación real de metadata en `_build_teradata_source`/`_build_data_conversion`/`_build_ole_db_destination`, y sobrescritura condicional de `AccessMode`.
- `tests/test_generator_campanias.py` — 36 tests nuevos (`ConversionMetadataTests`, `ConversionAndMappingValidationTests`, `AccessModeTests`).
- `specs/synthetic_string_and_numeric_conversion.json` — fixture del test suite automatizado.
- `specs/synthetic_string_and_numeric_conversion_pagosyrecaudaciones_context.json` — fixture usada para generar el artefacto de validación manual, con el `ProjectContext` real de PagosYRecaudaciones.
- `SyntheticStringAndNumericConversion.dtsx` — artefacto generado y validado manualmente en SSDT.
- `docs/template_teradata_to_sql_v1_generalization_pagosyrecaudaciones.md` — auditoría original que originó este ciclo (incluye la corrección posterior sobre las conversiones str→wstr).
- `Examples/Originals/BipSuc_CampaniasVIgentes/`, `Examples/Originals/PagosYRecaudaciones_FacturacionComi/` — fixtures reales de referencia.
