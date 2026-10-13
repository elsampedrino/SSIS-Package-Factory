"""
Tests de template-teradata-to-flat-file-v1. Cubren (ver
docs/template_teradata_to_flat_file_v1.md):

- generator/spec_validator.py: extension aditiva de 'destination.type' para
  soportar 'flat_file' ademas de 'ole_db' -- Connection Manager package-level,
  columns[] (mapping + schema fisico unificado), filename estructurado.
- generator/flat_file_generator.py: serializacion del Flat File Connection
  Manager (RaggedRight/Delimited), Microsoft.FlatFileDestination, columna
  row_terminator (sentinel vs ultima-columna-real), PropertyExpression de
  ConnectionString (parameter/literal/variable).
- Holdout real: Tokenizacion (proyecto MediosDePago) representado con la
  nueva arquitectura sin haber sido usado como referencia principal de diseño
  (esa fue DatosContactoProcesadoras + TarjetaDebitoSinUsoLink).
- E2E sintetico TeradataToFlatFileV1_E2E.

No se usan rutas corporativas reales en artefactos commiteados (E2E usa un
path literal seguro). Los tests que usan el ProjectContext real de
MediosDePago escriben a directorios temporales, nunca al repo.
"""

import copy
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssis_parser
import ssis_validator
from generator.campanias_generator import generate
from generator.flat_file_generator import (
    _build_filename_expression,
    _escape_ssis_expression_string_literal,
    _resolve_static_connection_string,
)
from generator.spec_validator import SpecValidationError, validate_spec
from project_context.context_builder import build_project_context

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "teradata_to_flat_file_base.dtsx")
E2E_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "teradata_to_flat_file_v1_e2e.json")
E2E_OUTPUT_PATH = os.path.join(REPO_ROOT, "TeradataToFlatFileV1_E2E.dtsx")

# flat-file-native-column-types-v1
NATIVE_TYPES_E2E_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "flat_file_native_types_v1_e2e.json")
NATIVE_TYPES_E2E_OUTPUT_PATH = os.path.join(REPO_ROOT, "FlatFileNativeTypesV1_E2E.dtsx")

MEDIOS_DE_PAGO_DIR = os.path.join(REPO_ROOT, "Examples", "Originals", "MediosDePago")
MEDIOS_DE_PAGO_CONTEXT = build_project_context(
    os.path.join(MEDIOS_DE_PAGO_DIR, "MediosDePago.dtproj"),
    os.path.join(MEDIOS_DE_PAGO_DIR, "Project.params"),
    MEDIOS_DE_PAGO_DIR,
)

BIPSUC_DIR = os.path.join(REPO_ROOT, "Examples", "Originals", "BipSuc_CampaniasVIgentes")
BIPSUC_CONTEXT = build_project_context(
    os.path.join(BIPSUC_DIR, "BipSuc.dtproj"),
    os.path.join(BIPSUC_DIR, "Project.params"),
    BIPSUC_DIR,
)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _base_spec(**destination_overrides):
    """
    Spec minimo valido, modelado sobre DatosContactoProcesadoras (referencia
    principal): RaggedRight, columna sentinel EndLine, filename por
    PropertyExpression (parameter 'pathLocal' + literal + variable
    'nombreArchivo') -- usa el ProjectContext REAL de MediosDePago (los 3
    valores de parametro son los mismos del proyecto auditado).
    """
    destination = {
        "type": "flat_file",
        "name": "Destino de archivo plano",
        "connection_manager": {
            "name": "ffcTest",
            "format": "ragged_right",
            "code_page": 1252,
            "locale_id": 11274,
            "unicode": False,
            "header_row_delimiter": "CRLF",
            "text_qualifier": "none",
            "filename": {
                "parameter": "pathLocal",
                "literal": "MediosDePago\\test.txt",
                "variable": {"name": "nombreArchivo", "value": "test.txt"},
            },
        },
        "columns": [
            {
                "source": "Fec_Proceso_PIC",
                "target": "Fec_Proceso_PIC",
                "target_data_type": "str",
                "target_length": 8,
                "target_code_page": 1252,
            },
            {
                "source": "Nom_Cliente_PIC",
                "target": "Nom_Cliente_PIC",
                "target_data_type": "str",
                "target_length": 50,
                "target_code_page": 1252,
            },
            {"target": "EndLine", "row_terminator": True},
        ],
    }
    destination.update(destination_overrides)
    return {
        "package": {"name": "TestPackage"},
        "data_flow": {
            "name": "Tarea Flujo de datos",
            "source": {
                "type": "teradata",
                "name": "Teradata Source",
                "connection": "cnxTeradata",
                "sql": "SELECT ...",
                "columns": [
                    {"name": "Fec_Proceso_PIC", "data_type": "str", "length": 65, "code_page": 1252},
                    {"name": "Nom_Cliente_PIC", "data_type": "str", "length": 50, "code_page": 1252},
                ],
            },
            "destination": destination,
        },
    }


# ---------------------------------------------------------------------------
# spec_validator.py -- extension 'flat_file'
# ---------------------------------------------------------------------------
class SpecValidatorFlatFileTests(unittest.TestCase):
    def test_valid_spec_has_no_errors(self):
        self.assertEqual(validate_spec(_base_spec(), MEDIOS_DE_PAGO_CONTEXT), [])

    def test_literal_only_filename_is_valid(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "literal": "C:\\safe\\path.txt"
        }
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_missing_connection_manager_rejected(self):
        spec = _base_spec()
        del spec["data_flow"]["destination"]["connection_manager"]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("connection_manager" in e for e in errors))

    def test_invalid_format_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["format"] = "fixed_width"
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".format" in e for e in errors))

    def test_missing_code_page_rejected(self):
        spec = _base_spec()
        del spec["data_flow"]["destination"]["connection_manager"]["code_page"]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".code_page" in e for e in errors))

    def test_missing_locale_id_rejected(self):
        spec = _base_spec()
        del spec["data_flow"]["destination"]["connection_manager"]["locale_id"]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".locale_id" in e for e in errors))

    def test_unicode_must_be_explicit_boolean(self):
        spec = _base_spec()
        del spec["data_flow"]["destination"]["connection_manager"]["unicode"]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".unicode" in e for e in errors))

    def test_unicode_true_is_rejected_no_evidence(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["unicode"] = True
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no tiene evidencia real" in e for e in errors))

    def test_invalid_header_row_delimiter_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["header_row_delimiter"] = "PIPE"
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("header_row_delimiter" in e for e in errors))

    def test_invalid_text_qualifier_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["text_qualifier"] = "quote"
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("text_qualifier" in e for e in errors))

    def test_missing_filename_rejected(self):
        spec = _base_spec()
        del spec["data_flow"]["destination"]["connection_manager"]["filename"]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("filename" in e for e in errors))

    def test_empty_filename_object_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {}
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("al menos uno de" in e for e in errors))

    def test_unknown_project_parameter_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parameter": "noExisteEsteParametro"
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no existe entre los parameters" in e for e in errors))

    def test_variable_missing_value_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"]["variable"] = {
            "name": "nombreArchivo"
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("variable.value" in e for e in errors))

    def test_empty_columns_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"] = []
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("destination.columns" in e for e in errors))

    def test_duplicate_target_column_rejected(self):
        spec = _base_spec()
        dup = dict(spec["data_flow"]["destination"]["columns"][0])
        spec["data_flow"]["destination"]["columns"].insert(1, dup)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("colisiona con otra columna" in e for e in errors))

    def test_missing_row_terminator_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"] = [
            c for c in spec["data_flow"]["destination"]["columns"] if not c.get("row_terminator")
        ]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("exactamente 1 columna con" in e for e in errors))

    def test_two_row_terminators_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"].append({"target": "EndLine2", "row_terminator": True})
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("debe haber exactamente 1" in e for e in errors))

    def test_row_terminator_not_last_rejected(self):
        spec = _base_spec()
        cols = spec["data_flow"]["destination"]["columns"]
        spec["data_flow"]["destination"]["columns"] = [cols[-1]] + cols[:-1]
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("debe ser la ULTIMA" in e for e in errors))

    def test_invalid_target_data_type_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"][0]["target_data_type"] = "numeric"
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("target_data_type" in e for e in errors))

    def test_invalid_target_length_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"][0]["target_length"] = 0
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("target_length" in e for e in errors))

    def test_source_must_exist_in_pipeline(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"][0]["source"] = "NoExiste"
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no existe entre las columnas declaradas" in e for e in errors))

    def test_wstr_column_without_code_page_is_valid(self):
        # Tokenizacion (holdout): columnas wstr sin code_page -- ver
        # docs/template_teradata_to_flat_file_v1.md, "str -> wstr".
        spec = _base_spec()
        spec["data_flow"]["destination"]["columns"][0]["target_data_type"] = "wstr"
        del spec["data_flow"]["destination"]["columns"][0]["target_code_page"]
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_ole_db_spec_still_valid_legacy_unaffected(self):
        legacy_spec = _load_json(os.path.join(REPO_ROOT, "specs", "campanias_generated.json"))
        self.assertEqual(validate_spec(legacy_spec, BIPSUC_CONTEXT), [])

    def test_flat_file_destination_with_ole_db_shape_is_rejected(self):
        # Confirma que 'flat_file' y 'ole_db' no comparten forma -- un
        # destination con 'type=flat_file' pero campos de ole_db (mappings/
        # connection/table) se rechaza por FALTA de connection_manager/columns,
        # no se cae de vuelta al camino ole_db.
        legacy_spec = copy.deepcopy(_load_json(os.path.join(REPO_ROOT, "specs", "campanias_generated.json")))
        legacy_spec["data_flow"]["destination"]["type"] = "flat_file"
        errors = validate_spec(legacy_spec, BIPSUC_CONTEXT)
        self.assertTrue(errors)
        self.assertTrue(any("connection_manager" in e for e in errors))


# ---------------------------------------------------------------------------
# SSDT GATE FIX #1 -- BUG 1: PropertyExpression ConnectionString mal
# escapada. SSIS Expression Language usa escaping estilo C ('\' -> '\\',
# '"' -> '\"'), NUNCA duplicado de comillas estilo SQL. Estos tests
# inspeccionan el CONTENIDO XML/expresion que finalmente consume SSIS, no
# una representacion Python accidental.
# ---------------------------------------------------------------------------
class FilenameExpressionEscapingTests(unittest.TestCase):
    def test_backslash_is_doubled(self):
        self.assertEqual(
            _escape_ssis_expression_string_literal("MediosDePago\\"),
            "MediosDePago\\\\",
        )

    def test_windows_path_with_multiple_backslashes(self):
        raw = r"C:\SSISPackageFactory_Test\TeradataToFlatFileV1_E2E.txt"
        escaped = _escape_ssis_expression_string_literal(raw)
        self.assertEqual(
            escaped,
            r"C:\\SSISPackageFactory_Test\\TeradataToFlatFileV1_E2E.txt",
        )
        # El valor escapado, envuelto en comillas, debe ser una expresion
        # SSIS valida: cada '\' individual del valor logico se representa
        # como '\\' (backslash duplicado), nunca como '\' suelto (lo que
        # SSDT rechazo explicitamente: "illegal escape sequence of '\S'").
        # Se verifica removiendo TODOS los pares '\\' -- si queda algun '\'
        # individual, el escaping esta mal (backslash impar/no duplicado).
        without_doubled_backslashes = escaped.replace("\\\\", "")
        self.assertNotIn("\\", without_doubled_backslashes)

    def test_quote_is_escaped_with_backslash_not_doubled(self):
        # SQL-style escaping ('""') fue el bug real -- confirmar que NO se
        # usa esa forma.
        escaped = _escape_ssis_expression_string_literal('a"b')
        self.assertEqual(escaped, 'a\\"b')
        self.assertNotIn('""', escaped)

    def test_literal_only_filename_expression(self):
        expr = _build_filename_expression(
            {"literal": r"C:\SSISPackageFactory_Test\archivo.txt"}
        )
        self.assertEqual(expr, r'"C:\\SSISPackageFactory_Test\\archivo.txt"')

    def test_parameter_plus_literal_expression(self):
        expr = _build_filename_expression(
            {"parameter": "pathLocal", "literal": "MediosDePago\\archivo.txt"}
        )
        self.assertEqual(expr, '@[$Project::pathLocal] + "MediosDePago\\\\archivo.txt"')

    def test_parameter_plus_literal_plus_variable_expression(self):
        # Reproduce exactamente la forma evidenciada en el corpus real
        # (DatosContactoProcesadoras.dtsx): '@[$Project::pathLocal] +
        # "MediosDePago\\" + @[User::nombreArchivo]' (backslash duplicado
        # dentro del literal, referencias SSIS intactas).
        expr = _build_filename_expression(
            {
                "parameter": "pathLocal",
                "literal": "MediosDePago\\",
                "variable": {"name": "nombreArchivo", "value": "archivo.txt"},
            }
        )
        self.assertEqual(
            expr,
            '@[$Project::pathLocal] + "MediosDePago\\\\" + @[User::nombreArchivo]',
        )

    def test_ssis_references_are_never_escaped(self):
        # Las referencias '@[$Project::...]'/'@[User::...]' deben aparecer
        # exactamente tal cual, nunca pasadas por escaping de string literal
        # (no tienen '\' que duplicar en este caso, pero confirmamos que la
        # sintaxis de referencia permanece intacta y fuera de comillas).
        expr = _build_filename_expression(
            {"parameter": "pathLocal", "variable": {"name": "nombreArchivo", "value": "x"}}
        )
        self.assertIn("@[$Project::pathLocal]", expr)
        self.assertIn("@[User::nombreArchivo]", expr)
        self.assertNotIn('"@[', expr)  # nunca envueltas en comillas

    def test_literal_containing_a_quote_produces_valid_expression(self):
        expr = _build_filename_expression({"literal": 'archivo con "comillas".txt'})
        self.assertEqual(expr, '"archivo con \\"comillas\\".txt"')
        # Balance de comillas: exactamente 2 comillas delimitadoras + 2
        # comillas internas escapadas con backslash -- nunca '""' sin escapar.
        self.assertNotIn('""', expr)

    def test_generated_xml_property_expression_matches_real_evidence_pattern(self):
        spec = _base_spec()
        tmp_dir = tempfile.mkdtemp(prefix="ssis_flat_file_escaping_test_")
        try:
            output_path = os.path.join(tmp_dir, "Generated.dtsx")
            generate(spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, output_path)
            tree = ET.parse(output_path)
            root = tree.getroot()
            NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}
            cm = next(
                cm for cm in root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", NS)
                if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
            )
            prop_expr = cm.find("DTS:PropertyExpression", NS)
            self.assertEqual(
                prop_expr.text,
                '@[$Project::pathLocal] + "MediosDePago\\\\test.txt" + @[User::nombreArchivo]',
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# flat-file-dynamic-filename-v1 -- 'filename.parts' (nuevo, dual-path con el
# formato legacy de arriba). Ver docs/template_teradata_to_flat_file_v1.md,
# "Filename dinamico". Evidencia real: Examples/Originals/CRM/InProTarjetaCrm.dtsx.
# ---------------------------------------------------------------------------
class DynamicFilenameValidatorTests(unittest.TestCase):
    def test_parts_empty_list_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {"parts": []}
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no puede ser una lista vacia" in e for e in errors))

    def test_parts_not_a_list_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {"parts": "oops"}
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("debe ser una lista" in e for e in errors))

    def test_part_not_object_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": ["not-an-object"]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("debe ser un objeto" in e for e in errors))

    def test_part_missing_type_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"value": "x"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".type debe ser uno de" in e for e in errors))

    def test_part_unknown_type_rejected(self):
        # 'raw_expression' (o cualquier otro valor no estructurado) se
        # rechaza explicitamente -- el ProcessSpec nunca acepta una
        # expresion SSIS cruda.
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "raw_expression", "value": "GETDATE()"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".type debe ser uno de" in e for e in errors))

    def test_mixing_parts_with_legacy_keys_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parameter": "pathLocal",
            "parts": [{"type": "literal", "value": "x.txt"}],
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("mutuamente excluyentes" in e for e in errors))

    def test_parts_parameter_not_in_project_context_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "parameter", "name": "noExiste"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no existe entre los parameters" in e for e in errors))

    def test_parts_literal_empty_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "literal", "value": ""}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".value es obligatorio" in e for e in errors))

    def test_parts_variable_missing_value_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "variable", "name": "x"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".value es obligatorio" in e for e in errors))

    def test_parts_more_than_one_variable_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [
                {"type": "variable", "name": "a", "value": "1"},
                {"type": "variable", "name": "b", "value": "2"},
            ]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("a lo sumo 1" in e for e in errors))

    def test_current_date_missing_format_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "current_date"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".format es obligatorio" in e for e in errors))

    def test_current_date_unsupported_format_yyyyMM_rejected(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "current_date", "format": "yyyyMM"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any(".format debe ser uno de" in e for e in errors))

    def test_current_date_other_unsupported_formats_rejected(self):
        # yyyy/timestamp/horas/offsets/formatos arbitrarios -- ninguno tiene
        # evidencia real en v1 (ver SUPPORTED_CURRENT_DATE_FORMATS).
        spec = _base_spec()
        for bad_format in ("yyyy", "timestamp", "HHmmss", "ddMMyyyy", "yesterday"):
            spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
                "parts": [{"type": "current_date", "format": bad_format}]
            }
            errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
            self.assertTrue(
                any(".format debe ser uno de" in e for e in errors),
                f"formato {bad_format!r} deberia haber sido rechazado",
            )

    def test_valid_parts_combination_has_no_errors(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [
                {"type": "parameter", "name": "pathLocal"},
                {"type": "literal", "value": "MediosDePago\\archivo_"},
                {"type": "current_date", "format": "yyyyMMdd"},
                {"type": "literal", "value": ".txt"},
            ]
        }
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])


class DynamicFilenameExpressionTests(unittest.TestCase):
    def test_parts_parameter_literal_current_date_literal_produces_canonical_expression(self):
        # Expresion CANONICA extraida de evidencia real (ver
        # Examples/Originals/CRM/InProTarjetaCrm.dtsx, Flat File CM
        # 'ffcArchivoCRM') -- los 3 casts (anio/mes/dia) usan
        # '(DT_STR,4,1252)', nunca length 2 para mes/dia.
        expr = _build_filename_expression(
            {
                "parts": [
                    {"type": "parameter", "name": "pathTraspaso"},
                    {"type": "literal", "value": "CRM\\CRM_DW_PROMOCIONES_"},
                    {"type": "current_date", "format": "yyyyMMdd"},
                    {"type": "literal", "value": ".txt"},
                ]
            }
        )
        self.assertEqual(
            expr,
            '@[$Project::pathTraspaso] + "CRM\\\\CRM_DW_PROMOCIONES_" + '
            '(DT_STR,4,1252)DATEPART("yyyy",getdate()) + '
            'RIGHT("0" + (DT_STR,4,1252)DATEPART("mm",getdate()), 2) + '
            'RIGHT("0" + (DT_STR,4,1252)DATEPART("dd",getdate()), 2) + ".txt"',
        )

    def test_parts_order_is_preserved_not_reordered_by_type(self):
        expr_a = _build_filename_expression(
            {"parts": [{"type": "literal", "value": "A"}, {"type": "literal", "value": "B"}]}
        )
        expr_b = _build_filename_expression(
            {"parts": [{"type": "literal", "value": "B"}, {"type": "literal", "value": "A"}]}
        )
        self.assertEqual(expr_a, '"A" + "B"')
        self.assertEqual(expr_b, '"B" + "A"')
        self.assertNotEqual(expr_a, expr_b)

    def test_two_literals_separated_by_current_date_stay_distinct_fragments(self):
        expr = _build_filename_expression(
            {
                "parts": [
                    {"type": "literal", "value": "prefix_"},
                    {"type": "current_date", "format": "yyyyMMdd"},
                    {"type": "literal", "value": "_suffix"},
                ]
            }
        )
        self.assertTrue(expr.startswith('"prefix_" + '))
        self.assertTrue(expr.endswith(' + "_suffix"'))
        self.assertEqual(expr.count('"prefix_"'), 1)
        self.assertEqual(expr.count('"_suffix"'), 1)

    def test_parts_literal_backslash_uses_existing_c_style_escaping(self):
        expr = _build_filename_expression(
            {"parts": [{"type": "literal", "value": r"CRM\archivo.txt"}]}
        )
        self.assertEqual(expr, r'"CRM\\archivo.txt"')

    def test_parts_literal_quote_uses_existing_escaping(self):
        expr = _build_filename_expression(
            {"parts": [{"type": "literal", "value": 'archivo "raro".txt'}]}
        )
        self.assertEqual(expr, '"archivo \\"raro\\".txt"')

    def test_raw_expression_part_type_rejected_end_to_end_via_validate_spec(self):
        # El generator nunca llega a ver un 'raw_expression' en uso real
        # porque spec_validator lo bloquea antes (fail-fast) -- confirmado
        # end-to-end via validate_spec, no solo a nivel unitario.
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [{"type": "raw_expression", "value": "GETDATE()"}]
        }
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(len(errors) > 0)


class DynamicFilenameStaticConnectionStringTests(unittest.TestCase):
    def test_current_date_resolves_to_injected_date(self):
        # 'now' inyectable -- determinista, no depende del reloj real (ver
        # generator/flat_file_generator.py, _resolve_static_connection_string).
        filename_spec = {
            "parts": [
                {"type": "parameter", "name": "pathTraspaso"},
                {"type": "literal", "value": "CRM\\CRM_DW_PROMOCIONES_"},
                {"type": "current_date", "format": "yyyyMMdd"},
                {"type": "literal", "value": ".txt"},
            ]
        }
        project_context = {
            "parameters": [
                {"name": "pathTraspaso", "value": r"\\srv\share\Traspaso\\", "sensitive": False}
            ]
        }
        resolved = _resolve_static_connection_string(
            filename_spec, project_context, now=datetime.date(2026, 10, 7)
        )
        self.assertTrue(resolved.endswith("CRM_DW_PROMOCIONES_20261007.txt"))

    def test_static_connection_string_without_injected_date_uses_real_today(self):
        # Sin 'now' explicito, debe usar la fecha real de HOY -- confirma
        # que NO queda ningun placeholder simbolico tipo 'YYYYMMDD' (la
        # evidencia real muestra una fecha concreta, ver
        # Examples/Originals/CRM/InProTarjetaCrm.dtsx).
        filename_spec = {"parts": [{"type": "current_date", "format": "yyyyMMdd"}]}
        resolved = _resolve_static_connection_string(filename_spec, {"parameters": []})
        self.assertEqual(resolved, datetime.date.today().strftime("%Y%m%d"))
        self.assertNotIn("YYYYMMDD", resolved)

    def test_property_expression_keeps_getdate_not_generation_date(self):
        filename_spec = {
            "parts": [
                {"type": "literal", "value": "CRM_DW_PROMOCIONES_"},
                {"type": "current_date", "format": "yyyyMMdd"},
                {"type": "literal", "value": ".txt"},
            ]
        }
        expr = _build_filename_expression(filename_spec)
        injected_date_str = datetime.date(2026, 10, 7).strftime("%Y%m%d")
        self.assertIn("getdate()", expr)
        self.assertNotIn(injected_date_str, expr)


class DynamicFilenameE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _base_spec()
        cls.spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [
                {"type": "parameter", "name": "pathLocal"},
                {"type": "literal", "value": "MediosDePago\\archivo_"},
                {"type": "current_date", "format": "yyyyMMdd"},
                {"type": "literal", "value": ".txt"},
            ]
        }
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_flat_file_dynamic_filename_test_")
        cls.output_path = os.path.join(cls.tmp_dir, "Generated.dtsx")
        generate(cls.spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.tree = ET.parse(cls.output_path)
        cls.root = cls.tree.getroot()
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _flat_file_cm(self):
        return next(
            cm for cm in self.root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )

    def test_parser_result_is_valid(self):
        self.assertIsInstance(self.ir, dict)

    def test_validator_has_no_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_property_expression_contains_canonical_current_date_expression(self):
        cm = self._flat_file_cm()
        prop_expr = cm.find("DTS:PropertyExpression", self.NS)
        self.assertEqual(
            prop_expr.text,
            '@[$Project::pathLocal] + "MediosDePago\\\\archivo_" + '
            '(DT_STR,4,1252)DATEPART("yyyy",getdate()) + '
            'RIGHT("0" + (DT_STR,4,1252)DATEPART("mm",getdate()), 2) + '
            'RIGHT("0" + (DT_STR,4,1252)DATEPART("dd",getdate()), 2) + ".txt"',
        )

    def test_cached_connection_string_contains_concrete_date(self):
        cm = self._flat_file_cm()
        inner_cm = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        cached = inner_cm.get("{www.microsoft.com/SqlServer/Dts}ConnectionString")
        today_suffix = datetime.date.today().strftime("%Y%m%d")
        self.assertIn(today_suffix, cached)

    def test_flat_file_cm_format_locale_codepage_preserved(self):
        cm = self._flat_file_cm()
        inner_cm = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        self.assertEqual(inner_cm.get("{www.microsoft.com/SqlServer/Dts}Format"), "RaggedRight")
        self.assertEqual(inner_cm.get("{www.microsoft.com/SqlServer/Dts}LocaleID"), "11274")
        self.assertEqual(inner_cm.get("{www.microsoft.com/SqlServer/Dts}CodePage"), "1252")

    def test_data_flow_topology_unchanged(self):
        class_ids = {c["class_id"] for c in self.data_flow["components"]}
        self.assertEqual(class_ids, {"Microsoft.SSISTeradataSrc", "Microsoft.FlatFileDestination"})


# ---------------------------------------------------------------------------
# generator/flat_file_generator.py -- XML real (RaggedRight, referencia
# principal DatosContactoProcesadoras)
# ---------------------------------------------------------------------------
class GeneratorRaggedRightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _base_spec()
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_flat_file_test_")
        cls.output_path = os.path.join(cls.tmp_dir, "Generated.dtsx")
        generate(cls.spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.tree = ET.parse(cls.output_path)
        cls.root = cls.tree.getroot()
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_two_components_one_path(self):
        self.assertEqual(len(self.data_flow["components"]), 2)
        types = {c["class_id"] for c in self.data_flow["components"]}
        self.assertEqual(types, {"Microsoft.SSISTeradataSrc", "Microsoft.FlatFileDestination"})
        self.assertEqual(len(self.data_flow["paths"]), 1)

    def test_no_data_conversion_no_derived_column(self):
        types = {c["type"] for c in self.data_flow["components"]}
        self.assertNotIn("data_conversion", types)
        self.assertNotIn("derived_column", types)

    def test_flat_file_connection_manager_present_package_level(self):
        cms = self.root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
        flat_file_cms = [
            cm for cm in cms
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        ]
        self.assertEqual(len(flat_file_cms), 1)
        cm = flat_file_cms[0]
        self.assertEqual(cm.get("{www.microsoft.com/SqlServer/Dts}ObjectName"), "ffcTest")

    def test_flat_file_connection_manager_attributes(self):
        cm = self._flat_file_cm()
        inner = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        self.assertEqual(inner.get("{www.microsoft.com/SqlServer/Dts}Format"), "RaggedRight")
        self.assertEqual(inner.get("{www.microsoft.com/SqlServer/Dts}LocaleID"), "11274")
        self.assertEqual(
            inner.get("{www.microsoft.com/SqlServer/Dts}HeaderRowDelimiter"), "_x000D__x000A_"
        )
        self.assertEqual(inner.get("{www.microsoft.com/SqlServer/Dts}RowDelimiter"), "")
        self.assertEqual(
            inner.get("{www.microsoft.com/SqlServer/Dts}TextQualifier"), "_x003C_none_x003E_"
        )
        self.assertEqual(inner.get("{www.microsoft.com/SqlServer/Dts}CodePage"), "1252")

    def test_property_expression_on_connection_string(self):
        cm = self._flat_file_cm()
        prop_expr = cm.find("DTS:PropertyExpression", self.NS)
        self.assertEqual(
            prop_expr.get("{www.microsoft.com/SqlServer/Dts}Name"), "ConnectionString"
        )
        self.assertEqual(
            prop_expr.text,
            '@[$Project::pathLocal] + "MediosDePago\\\\test.txt" + @[User::nombreArchivo]',
        )

    def test_package_variable_generated(self):
        vars_el = self.root.find("DTS:Variables", self.NS)
        variables = vars_el.findall("DTS:Variable", self.NS)
        self.assertEqual(len(variables), 1)
        var = variables[0]
        self.assertEqual(var.get("{www.microsoft.com/SqlServer/Dts}ObjectName"), "nombreArchivo")
        self.assertEqual(var.get("{www.microsoft.com/SqlServer/Dts}Namespace"), "User")
        value_el = var.find("DTS:VariableValue", self.NS)
        self.assertEqual(value_el.get("{www.microsoft.com/SqlServer/Dts}DataType"), "8")
        self.assertEqual(value_el.text, "test.txt")

    def test_flat_file_columns_count_and_terminator(self):
        cm = self._flat_file_cm()
        inner = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        columns = inner.findall("DTS:FlatFileColumns/DTS:FlatFileColumn", self.NS)
        self.assertEqual(len(columns), 3)
        last = columns[-1]
        self.assertEqual(last.get("{www.microsoft.com/SqlServer/Dts}ObjectName"), "EndLine")
        self.assertEqual(last.get("{www.microsoft.com/SqlServer/Dts}ColumnType"), "Delimited")
        self.assertEqual(
            last.get("{www.microsoft.com/SqlServer/Dts}ColumnDelimiter"), "_x000D__x000A_"
        )
        self.assertIsNone(last.get("{www.microsoft.com/SqlServer/Dts}ColumnWidth"))

    def test_non_terminator_columns_have_column_width(self):
        cm = self._flat_file_cm()
        inner = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        columns = inner.findall("DTS:FlatFileColumns/DTS:FlatFileColumn", self.NS)
        for col in columns[:-1]:
            self.assertEqual(
                col.get("{www.microsoft.com/SqlServer/Dts}ColumnWidth"),
                col.get("{www.microsoft.com/SqlServer/Dts}MaximumWidth"),
            )
            self.assertEqual(col.get("{www.microsoft.com/SqlServer/Dts}ColumnDelimiter"), "")

    def test_flat_file_destination_component_class_id(self):
        dest = self._flat_file_destination()
        self.assertEqual(dest["class_id"], "Microsoft.FlatFileDestination")

    def test_flat_file_destination_connection_reference(self):
        dest = self._flat_file_destination()
        self.assertEqual(dest["connection"], "Package.ConnectionManagers[ffcTest]")

    def test_flat_file_destination_has_no_outputs(self):
        dest = self._flat_file_destination()
        self.assertEqual(len(dest["outputs"]), 0)
        self.assertEqual(len(dest["error_outputs"]), 0)

    def test_flat_file_destination_input_columns_exclude_terminator(self):
        dest = self._flat_file_destination()
        inp = dest["inputs"][0]
        names = {c["name"] for c in inp["columns"]}
        self.assertEqual(names, {"Fec_Proceso_PIC", "Nom_Cliente_PIC"})

    def test_flat_file_destination_external_metadata_includes_terminator(self):
        dest = self._flat_file_destination()
        inp = dest["inputs"][0]
        names = {c["name"] for c in inp["external_metadata_columns"]}
        self.assertEqual(names, {"Fec_Proceso_PIC", "Nom_Cliente_PIC", "EndLine"})

    def test_cached_metadata_on_input_column(self):
        dest = self._flat_file_destination()
        inp = dest["inputs"][0]
        col = next(c for c in inp["columns"] if c["name"] == "Fec_Proceso_PIC")
        self.assertEqual(col["cached_data_type"], "str")
        self.assertEqual(col["cached_length"], 65)
        self.assertEqual(col["cached_code_page"], 1252)
        self.assertTrue(col["source_lineage_id"].endswith("Columns[Fec_Proceso_PIC]"))
        self.assertIn("Teradata Source", col["source_lineage_id"])

    def test_design_time_properties_present(self):
        self.assertIsNotNone(self.root.find("DTS:DesignTimeProperties", self.NS))

    def test_ids_are_fresh_not_copied_from_golden(self):
        golden_dtsid = "{843CF89A-7676-4B64-A2F4-018E74F81BE7}"
        cm = self._flat_file_cm()
        self.assertNotEqual(cm.get("{www.microsoft.com/SqlServer/Dts}DTSID"), golden_dtsid)

    def _flat_file_cm(self):
        cms = self.root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
        return next(
            cm for cm in cms
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )

    def _flat_file_destination(self):
        by_type = {c["class_id"]: c for c in self.data_flow["components"]}
        return by_type["Microsoft.FlatFileDestination"]


# ---------------------------------------------------------------------------
# generator/flat_file_generator.py -- Delimited (referencia secundaria
# TarjetaDebitoSinUsoLink): 1 sola columna, terminador = la columna real,
# sin variable de package, filename solo parameter+literal.
# ---------------------------------------------------------------------------
class GeneratorDelimitedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = {
            "package": {"name": "DelimitedTest"},
            "data_flow": {
                "name": "Tarea Flujo de datos",
                "source": {
                    "type": "teradata",
                    "name": "Teradata Source",
                    "connection": "cnxTeradata",
                    "sql": "select Num_tarjeta from f_tarjeta_debito_sin_uso_LINK",
                    "min_sessions": 1,
                    "max_sessions": 1,
                    "columns": [{"name": "Num_Tarjeta", "data_type": "str", "length": 20, "code_page": 1252}],
                },
                "destination": {
                    "type": "flat_file",
                    "name": "Destino de archivo plano",
                    "connection_manager": {
                        "name": "ffcArchivoTarjetaDebito",
                        "format": "delimited",
                        "code_page": 1252,
                        "locale_id": 11274,
                        "unicode": False,
                        "header_row_delimiter": "SEMICOLON",
                        "text_qualifier": "none",
                        "filename": {
                            "parameter": "pathTraspaso",
                            "literal": "MediosDePago\\Tarjeta_Debito_Sin_Uso_Link.csv",
                        },
                    },
                    "columns": [
                        {
                            "source": "Num_Tarjeta",
                            "target": "Num_Tarjeta",
                            "target_data_type": "str",
                            "target_length": 20,
                            "target_code_page": 1252,
                            "row_terminator": True,
                        },
                    ],
                },
            },
        }
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_flat_file_delimited_test_")
        cls.output_path = os.path.join(cls.tmp_dir, "Generated.dtsx")
        generate(cls.spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.tree = ET.parse(cls.output_path)
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_spec_itself_is_valid(self):
        self.assertEqual(validate_spec(self.spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])

    def test_single_column_carries_row_terminator(self):
        root = self.tree.getroot()
        cm = next(
            cm for cm in root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )
        inner = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        self.assertEqual(inner.get("{www.microsoft.com/SqlServer/Dts}Format"), "Delimited")
        columns = inner.findall("DTS:FlatFileColumns/DTS:FlatFileColumn", self.NS)
        self.assertEqual(len(columns), 1)
        self.assertEqual(
            columns[0].get("{www.microsoft.com/SqlServer/Dts}ColumnType"), "Delimited"
        )
        self.assertEqual(
            columns[0].get("{www.microsoft.com/SqlServer/Dts}MaximumWidth"), "20"
        )

    def test_no_package_variable_generated(self):
        root = self.tree.getroot()
        vars_el = root.find("DTS:Variables", self.NS)
        self.assertEqual(len(list(vars_el)), 0)

    def test_min_max_sessions_one_reused_unchanged(self):
        df = self.ir["data_flows"][0]
        src = next(c for c in df["components"] if c["type"] == "teradata_source")
        props = {p["name"]: p["value"] for p in src["raw_properties"]}
        self.assertEqual(props.get("MinSessions"), "1")
        self.assertEqual(props.get("MaxSessions"), "1")


# ---------------------------------------------------------------------------
# Holdout real: Tokenizacion -- NO usado como referencia principal de diseno.
# Representa el package con la nueva arquitectura y compara estructuralmente
# contra el .dtsx real (no byte-a-byte: IDs y metadata de diseno pueden
# variar, ver docs/template_teradata_to_flat_file_v1.md, "Holdout").
# ---------------------------------------------------------------------------
class TokenizacionHoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_path = os.path.join(MEDIOS_DE_PAGO_DIR, "Tokenizacion.dtsx")
        cls.real_ir = ssis_parser.parse_file(cls.real_path)

        # Reconstruccion 1:1 del schema real (19 columnas, 4 str + 15 wstr,
        # la ULTIMA columna real carga el row_terminator -- sin sentinel
        # EndLine, ver auditoria).
        wstr_cols = {
            "Id_d_Cliente_PIC", "Cod_Tipo_Documento_PIC", "Num_Documento_PIC",
            "Desc_Apellido_Individuo_PIC", "Desc_Nombre_Individuo_PIC",
            "Num_Identif_Tributaria_PIC", "Fec_Nacimiento_PIC", "Nom_Domicilio_PIC",
            "Num_Piso_PIC", "Nom_Departamento_PIC", "Cod_Provincia_PIC",
            "Desc_Ciudad_PIC", "Cod_Postal_PIC", "Num_Telefono_PIC", "Txt_Email_PIC",
        }
        source_columns_meta = [
            ("Id_d_Cliente_PIC", 20), ("Cod_Tipo_Documento_PIC", 10),
            ("Num_Documento_PIC", 20), ("Desc_Apellido_Individuo_PIC", 100),
            ("Desc_Nombre_Individuo_PIC", 100), ("Cod_Sexo_PIC", 20),
            ("Num_Identif_Tributaria_PIC", 20), ("Fec_Nacimiento_PIC", 8),
            ("Cod_Estado_Civil_PIC", 30), ("Cod_Nacionalidad_PIC", 30),
            ("Nom_Domicilio_PIC", 100), ("Num_Domicilio_PIC", 20),
            ("Num_Piso_PIC", 20), ("Nom_Departamento_PIC", 10),
            ("Cod_Provincia_PIC", 30), ("Desc_Ciudad_PIC", 100),
            ("Cod_Postal_PIC", 10), ("Num_Telefono_PIC", 40),
            ("Txt_Email_PIC", 100),
        ]
        source_columns = [
            {"name": name, "data_type": "str", "length": length, "code_page": 1252}
            for name, length in source_columns_meta
        ]
        dest_columns = []
        for idx, (name, length) in enumerate(source_columns_meta):
            is_last = idx == len(source_columns_meta) - 1
            col = {
                "source": name,
                "target": name,
                "target_data_type": "wstr" if name in wstr_cols else "str",
                "target_length": length,
            }
            if col["target_data_type"] == "str":
                col["target_code_page"] = 1252
            if is_last:
                col["row_terminator"] = True
            dest_columns.append(col)

        cls.spec = {
            "package": {"name": "TokenizacionHoldout"},
            "data_flow": {
                "name": "Tarea Flujo de datos",
                "source": {
                    "type": "teradata",
                    "name": "Teradata Source 1",
                    "connection": "cnxTeradata",
                    "sql": "SELECT ... FROM d_tokenizacion_clientes_sin_td_LINK",
                    "columns": source_columns,
                },
                "destination": {
                    "type": "flat_file",
                    "name": "Destino de archivo plano",
                    "connection_manager": {
                        "name": "ffcTokenizacion",
                        "format": "ragged_right",
                        "code_page": 1252,
                        "locale_id": 11274,
                        "unicode": False,
                        "header_row_delimiter": "CRLF",
                        "text_qualifier": "none",
                        "filename": {
                            "parameter": "pathLocal",
                            "literal": "MediosDePago\\d_tokenizacion_clientes_sin_td_LINK.txt",
                        },
                    },
                    "columns": dest_columns,
                },
            },
        }

        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_flat_file_holdout_")
        cls.output_path = os.path.join(cls.tmp_dir, "TokenizacionHoldout.dtsx")
        generate(cls.spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.generated_ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.generated_ir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_spec_itself_is_valid(self):
        self.assertEqual(validate_spec(self.spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_generated_package_validates(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_topology_matches_real_package(self):
        real_df = self.real_ir["data_flows"][0]
        gen_df = self.generated_ir["data_flows"][0]
        self.assertEqual(
            {c["class_id"] for c in real_df["components"]},
            {c["class_id"] for c in gen_df["components"]},
        )
        self.assertEqual(len(real_df["paths"]), len(gen_df["paths"]))

    def test_column_count_matches_real_package(self):
        real_df = self.real_ir["data_flows"][0]
        gen_df = self.generated_ir["data_flows"][0]
        real_dest = next(c for c in real_df["components"] if c["class_id"] == "Microsoft.FlatFileDestination")
        gen_dest = next(c for c in gen_df["components"] if c["class_id"] == "Microsoft.FlatFileDestination")
        real_names = {c["name"] for c in real_dest["inputs"][0]["external_metadata_columns"]}
        gen_names = {c["name"] for c in gen_dest["inputs"][0]["external_metadata_columns"]}
        self.assertEqual(real_names, gen_names)

    def test_str_wstr_type_mix_matches_real_package(self):
        real_df = self.real_ir["data_flows"][0]
        gen_df = self.generated_ir["data_flows"][0]
        real_dest = next(c for c in real_df["components"] if c["class_id"] == "Microsoft.FlatFileDestination")
        gen_dest = next(c for c in gen_df["components"] if c["class_id"] == "Microsoft.FlatFileDestination")
        real_types = {
            c["name"]: c["data_type"] for c in real_dest["inputs"][0]["external_metadata_columns"]
        }
        gen_types = {
            c["name"]: c["data_type"] for c in gen_dest["inputs"][0]["external_metadata_columns"]
        }
        self.assertEqual(real_types, gen_types)

    def test_last_column_carries_row_terminator_no_sentinel(self):
        gen_df = self.generated_ir["data_flows"][0]
        gen_dest = next(c for c in gen_df["components"] if c["class_id"] == "Microsoft.FlatFileDestination")
        names = {c["name"] for c in gen_dest["inputs"][0]["external_metadata_columns"]}
        self.assertNotIn("EndLine", names)
        self.assertIn("Txt_Email_PIC", names)

    def test_no_flat_file_connection_manager_at_project_level(self):
        # Confirma package-level (ver docs/template_teradata_to_flat_file_v1.md,
        # "Package-level vs project-level") -- MEDIOS_DE_PAGO_CONTEXT no debe
        # necesitar ni exponer ninguna conexion FLATFILE a nivel proyecto.
        self.assertNotIn("ffcTokenizacion", MEDIOS_DE_PAGO_CONTEXT["connections"])


# ---------------------------------------------------------------------------
# E2E sintetico: TeradataToFlatFileV1_E2E
# ---------------------------------------------------------------------------
class TeradataToFlatFileV1E2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_json(E2E_SPEC_PATH)
        generate(cls.spec, BIPSUC_CONTEXT, TEMPLATE_PATH, E2E_OUTPUT_PATH)
        cls.ir = ssis_parser.parse_file(E2E_OUTPUT_PATH)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]

    def test_spec_itself_is_valid(self):
        self.assertEqual(validate_spec(self.spec, BIPSUC_CONTEXT), [])

    def test_package_is_valid_with_no_structural_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_expected_components_present(self):
        types = {c["class_id"] for c in self.data_flow["components"]}
        self.assertEqual(types, {"Microsoft.SSISTeradataSrc", "Microsoft.FlatFileDestination"})

    def test_expected_paths_present(self):
        self.assertEqual(len(self.data_flow["paths"]), 1)

    def test_no_control_flow_beyond_data_flow(self):
        execs = self.ir["control_flow"]["executables"]
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0]["executable_type"], "Microsoft.Pipeline")

    def test_design_time_properties_present(self):
        with open(E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("DesignTimeProperties", content)

    def test_no_corporate_paths_or_hosts_in_artifact(self):
        with open(E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("srvssisdatosqa", "srvsftp02", "tdgnn.ccba"):
            self.assertNotIn(forbidden, content)

    def test_no_secrets_in_generated_artifact(self):
        with open(E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("Salt=", "IV=", "Algorithm=", "Password=", "DTS:Password", "TeraPassword"):
            self.assertNotIn(forbidden, content)

    # -- SSDT GATE FIX #1 -- BUG 2: Teradata Source debe abrir en modo SQL
    # Command, nunca Table Name. Comparado explicitamente contra evidencia
    # real (10/10 Teradata Source reales del corpus + golden SSDT-authored
    # + template-teradata-to-sql-v1.1 estable, QA-ejecutado, TODOS con
    # AccessMode=1/TableName sin texto/SqlCommand poblado) -- ver
    # docs/template_teradata_to_flat_file_v1.md, "Gate SSDT fix #1".
    def _teradata_source_component(self):
        by_class = {c["class_id"]: c for c in self.data_flow["components"]}
        return by_class["Microsoft.SSISTeradataSrc"]

    def test_teradata_source_uses_cnx_teradata(self):
        src = self._teradata_source_component()
        self.assertEqual(src["connection"], "Project.ConnectionManagers[cnxTeradata]")

    def test_teradata_source_access_mode_is_sql_command(self):
        src = self._teradata_source_component()
        props = {p["name"]: p["value"] for p in src["raw_properties"]}
        # AccessMode=1 confirmado como "SQL Command" en TODO el corpus real
        # disponible (10 instancias: BipSuc Turnero x3, BipSuc Campanias,
        # los 4 de MediosDePago, PagosYRecaudaciones, y el golden
        # SSDT-authored Control_Flow_Base.dtsx) -- nunca refutado por
        # ningun contraejemplo de 'Table Name' mode en el corpus.
        self.assertEqual(props.get("AccessMode"), "1")

    def test_teradata_source_table_name_is_empty(self):
        src = self._teradata_source_component()
        props = {p["name"]: p["value"] for p in src["raw_properties"]}
        self.assertFalse(props.get("TableName"))

    def test_teradata_source_sql_command_present_and_simple(self):
        src = self._teradata_source_component()
        props = {p["name"]: p["value"] for p in src["raw_properties"]}
        sql = props.get("SqlCommand", "")
        self.assertIn("Fec_Proceso_PIC", sql)
        self.assertIn("Id_d_Cliente_PIC", sql)
        self.assertIn("Nom_Cliente_PIC", sql)
        self.assertIn("FROM", sql.upper())
        # SQL simple, sin funciones especificas de Teradata (COALESCE/
        # TO_CHAR/LPAD/RPAD/SUBSTR) -- ver docs/template_teradata_to_flat_file_v1.md,
        # "Gate SSDT fix #1" (mitigacion de BUG 2: evitar cualquier
        # dependencia de parsing/validacion de sintaxis especifica del
        # driver contra una tabla ficticia).
        for teradata_specific_fn in ("COALESCE", "TO_CHAR", "LPAD", "RPAD", "SUBSTR"):
            self.assertNotIn(teradata_specific_fn, sql.upper())

    def test_teradata_source_output_columns_preserved(self):
        src = self._teradata_source_component()
        cols = {c["name"] for c in src["outputs"][0]["columns"]}
        self.assertEqual(cols, {"Fec_Proceso_PIC", "Id_d_Cliente_PIC", "Nom_Cliente_PIC"})

    def test_teradata_source_matches_golden_ssdt_authored_reference(self):
        # Comparacion directa contra Examples/Originals/SSDT_Golden/Control_Flow_Base.dtsx
        # (creado directamente en SSDT por un humano, con SqlCommand
        # tipeado a mano -- solo posible si el modo "SQL Command" estaba
        # efectivamente seleccionado). Mismo AccessMode/TableName-vacio que
        # el E2E generado.
        golden_path = os.path.join(
            REPO_ROOT, "Examples", "Originals", "SSDT_Golden", "Control_Flow_Base.dtsx"
        )
        golden_root = ET.parse(golden_path).getroot()
        golden_comp = next(
            c for c in golden_root.iter("component")
            if c.get("componentClassID") == "Microsoft.SSISTeradataSrc"
        )
        golden_props = {p.get("name"): p for p in golden_comp.find("properties").findall("property")}

        gen_root = ET.parse(E2E_OUTPUT_PATH).getroot()
        gen_comp = next(
            c for c in gen_root.iter("component")
            if c.get("componentClassID") == "Microsoft.SSISTeradataSrc"
        )
        gen_props = {p.get("name"): p for p in gen_comp.find("properties").findall("property")}

        self.assertEqual(golden_props["AccessMode"].text, gen_props["AccessMode"].text)
        self.assertEqual(golden_props["TableName"].text, gen_props["TableName"].text)
        self.assertIsNotNone(gen_props["SqlCommand"].text)


# ---------------------------------------------------------------------------
# flat-file-native-column-types-v1 -- dbDate/numeric/i1, ademas de str/wstr.
# Ver docs/template_teradata_to_flat_file_v1.md, "Tipos nativos SSIS".
# Evidencia real: Examples/Originals/CRM/CDNI_Promociones_Parametros.dtsx
# (paquete productivo, Delimited multicolumna, NO se modifica ni se
# commitea).
# ---------------------------------------------------------------------------
class NativeColumnTypesValidatorTests(unittest.TestCase):
    """source.columns[]/destination.columns[] -- validacion de los 3 tipos
    nuevos (dbDate/numeric/i1) y del cierre de la laguna preexistente en
    source.columns[].data_type (antes sin conjunto cerrado)."""

    def _spec_with_source_column(self, **col_overrides):
        spec = _base_spec()
        col = {"name": "Campo_Nuevo", "data_type": "dbDate"}
        col.update(col_overrides)
        spec["data_flow"]["source"]["columns"].append(col)
        return spec

    def _spec_with_destination_column(self, **col_overrides):
        spec = _base_spec()
        col = {"source": "Fec_Proceso_PIC", "target": "Campo_Nuevo", "target_data_type": "dbDate"}
        col.update(col_overrides)
        # Insertar antes del sentinel EndLine (debe seguir siendo la ultima).
        cols = spec["data_flow"]["destination"]["columns"]
        cols.insert(len(cols) - 1, col)
        return spec

    # --- 1-3: tipos nuevos validos ---
    def test_source_dbdate_valid(self):
        spec = self._spec_with_source_column(name="Fecha_Alta", data_type="dbDate")
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_source_numeric_valid(self):
        spec = self._spec_with_source_column(
            name="Importe", data_type="numeric", precision=15, scale=2
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_source_i1_valid(self):
        spec = self._spec_with_source_column(name="Indicador", data_type="i1")
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_destination_dbdate_valid(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Fecha_Alta", target_data_type="dbDate"
        )
        # El source referenciado (Fec_Proceso_PIC) es 'str' en _base_spec();
        # spec_validator.py no cruza tipos source/destination (documentado
        # en la auditoria), asi que esto valida la FORMA, no una conversion
        # real -- igual criterio que el resto de este modulo.
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_destination_numeric_valid(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Importe",
            target_data_type="numeric", target_precision=15, target_scale=2,
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_destination_i1_valid(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Indicador", target_data_type="i1"
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    # --- i2/i4 (SMALLINT/INTEGER nativos, OPCION A de la auditoria de
    # PromocionesNativeDelimitedV1) -- mismo criterio de validacion que
    # dbDate/i1: sin length/code_page/precision/scale. ---
    def test_source_i2_valid(self):
        spec = self._spec_with_source_column(name="Id_Producto", data_type="i2")
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_source_i4_valid(self):
        spec = self._spec_with_source_column(name="Id_Promo", data_type="i4")
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_destination_i2_valid(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Id_Producto", target_data_type="i2"
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_destination_i4_valid(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Id_Promo", target_data_type="i4"
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_i2_with_length_rejected(self):
        spec = self._spec_with_source_column(name="Id_Producto", data_type="i2", length=6)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'length'" in e for e in errors))

    def test_i4_with_code_page_rejected(self):
        spec = self._spec_with_source_column(name="Id_Promo", data_type="i4", code_page=1252)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'code_page'" in e for e in errors))

    def test_destination_i2_with_length_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Id_Producto",
            target_data_type="i2", target_length=6,
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'target_length'" in e for e in errors))

    def test_destination_i4_with_precision_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Id_Promo",
            target_data_type="i4", target_precision=11,
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'target_precision'" in e for e in errors))

    # --- 4: wstr sin regresion ---
    def test_wstr_still_valid_no_regression(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Texto_Largo",
            target_data_type="wstr", target_length=200,
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    # --- 5: tipo source desconocido ---
    def test_unknown_source_data_type_rejected(self):
        spec = self._spec_with_source_column(name="Campo_Raro", data_type="blob")
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("data_type debe ser uno de" in e for e in errors))

    # --- 6: tipo destination desconocido ---
    def test_unknown_destination_data_type_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Campo_Raro", target_data_type="blob"
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("target_data_type debe ser uno de" in e for e in errors))

    # --- 7: combinaciones de metadata invalida (source) ---
    def test_dbdate_with_length_rejected(self):
        spec = self._spec_with_source_column(name="Fecha_Alta", data_type="dbDate", length=8)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'length'" in e for e in errors))

    def test_i1_with_precision_rejected(self):
        spec = self._spec_with_source_column(name="Indicador", data_type="i1", precision=1)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'precision'" in e for e in errors))

    # --- 8: numeric sin precision ---
    def test_numeric_without_precision_rejected(self):
        spec = self._spec_with_source_column(name="Importe", data_type="numeric", scale=2)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("requiere 'precision'" in e for e in errors))

    # --- 9: numeric sin scale ---
    def test_numeric_without_scale_rejected(self):
        spec = self._spec_with_source_column(name="Importe", data_type="numeric", precision=15)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("requiere 'scale'" in e for e in errors))

    # --- 10: numeric con rangos invalidos (destino, donde se definio el limite) ---
    def test_numeric_precision_over_platform_max_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Importe",
            target_data_type="numeric", target_precision=39, target_scale=2,
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("supera el maximo soportado" in e for e in errors))

    def test_numeric_scale_greater_than_precision_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Importe",
            target_data_type="numeric", target_precision=5, target_scale=9,
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no puede ser mayor" in e for e in errors))

    # --- 11: dbDate con length indebido (destino) ---
    def test_destination_dbdate_with_length_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Fecha_Alta",
            target_data_type="dbDate", target_length=8,
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'target_length'" in e for e in errors))

    # --- 12: i1 con code_page indebido (destino) ---
    def test_destination_i1_with_code_page_rejected(self):
        spec = self._spec_with_destination_column(
            source="Fec_Proceso_PIC", target="Indicador",
            target_data_type="i1", target_code_page=1252,
        )
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no admite 'target_code_page'" in e for e in errors))


class ColumnDelimiterValidatorTests(unittest.TestCase):
    """connection_manager.column_delimiter -- opcional, solo para Delimited,
    obligatorio cuando hay mas de 1 columna."""

    def _delimited_two_column_spec(self, column_delimiter=None):
        destination_overrides = {
            "connection_manager": {
                "name": "ffcTest",
                "format": "delimited",
                "code_page": 1252,
                "locale_id": 11274,
                "unicode": False,
                "header_row_delimiter": "CRLF",
                "text_qualifier": "none",
                "filename": {"literal": "archivo.txt"},
            },
        }
        if column_delimiter is not None:
            destination_overrides["connection_manager"]["column_delimiter"] = column_delimiter
        spec = _base_spec(**destination_overrides)
        spec["data_flow"]["destination"]["columns"] = [
            {"source": "Fec_Proceso_PIC", "target": "Fec_Proceso_PIC", "target_data_type": "str", "target_length": 8, "target_code_page": 1252},
            {"source": "Nom_Cliente_PIC", "target": "Nom_Cliente_PIC", "target_data_type": "str", "target_length": 50, "target_code_page": 1252, "row_terminator": True},
        ]
        return spec

    def test_column_delimiter_required_for_multicolumn_delimited(self):
        spec = self._delimited_two_column_spec(column_delimiter=None)
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("column_delimiter' es obligatorio" in e for e in errors))

    def test_column_delimiter_semicolon_accepted(self):
        spec = self._delimited_two_column_spec(column_delimiter=";")
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_column_delimiter_pipe_accepted(self):
        # #26: delimitador distinto de ';' tambien configurable.
        spec = self._delimited_two_column_spec(column_delimiter="|")
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])

    def test_column_delimiter_empty_string_rejected(self):
        spec = self._delimited_two_column_spec(column_delimiter="")
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("no puede estar vacio" in e for e in errors))

    def test_column_delimiter_rejected_on_ragged_right(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["column_delimiter"] = ";"
        errors = validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT)
        self.assertTrue(any("solo aplica cuando" in e for e in errors))

    # --- 27: compatibilidad Delimited legacy de 1 sola columna ---
    def test_single_column_delimited_legacy_does_not_require_column_delimiter(self):
        spec = _base_spec(
            connection_manager={
                "name": "ffcTest",
                "format": "delimited",
                "code_page": 1252,
                "locale_id": 11274,
                "unicode": False,
                "header_row_delimiter": "CRLF",
                "text_qualifier": "none",
                "filename": {"literal": "archivo.txt"},
            },
        )
        spec["data_flow"]["destination"]["columns"] = [
            {"source": "Fec_Proceso_PIC", "target": "Fec_Proceso_PIC", "target_data_type": "str", "target_length": 8, "target_code_page": 1252, "row_terminator": True},
        ]
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])


class FlatFileNativeTypesE2ETests(unittest.TestCase):
    """Genera FlatFileNativeTypesV1_E2E.dtsx (wstr/dbDate/numeric/i1,
    Delimited multicolumna con ';', ultima columna real con CRLF, sin
    sentinel EndLine, filename fijo, Overwrite heredado) y verifica
    estructuralmente el XML resultante."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_json(NATIVE_TYPES_E2E_SPEC_PATH)
        assert validate_spec(cls.spec, BIPSUC_CONTEXT) == []
        generate(cls.spec, BIPSUC_CONTEXT, TEMPLATE_PATH, NATIVE_TYPES_E2E_OUTPUT_PATH)
        cls.ir = ssis_parser.parse_file(NATIVE_TYPES_E2E_OUTPUT_PATH)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.root = ET.parse(NATIVE_TYPES_E2E_OUTPUT_PATH).getroot()
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    def _teradata_source(self):
        return next(
            c for c in self.root.iter("component")
            if c.get("componentClassID") == "Microsoft.SSISTeradataSrc"
        )

    def _flat_file_cm(self):
        return next(
            cm for cm in self.root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )

    def _flat_file_destination(self):
        return next(
            c for c in self.root.iter("component")
            if c.get("componentClassID") == "Microsoft.FlatFileDestination"
        )

    # --- 34: parser/validator ---
    def test_parser_result_is_valid(self):
        self.assertIsInstance(self.ir, dict)

    def test_validator_has_no_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    # --- 13-14: Teradata Source outputColumn/externalMetadataColumn ---
    def test_teradata_source_output_column_types(self):
        comp = self._teradata_source()
        out_normal = next(
            o for o in comp.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        cols = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        self.assertEqual(cols["Nombre_Cliente"].get("dataType"), "wstr")
        self.assertEqual(cols["Fecha_Alta"].get("dataType"), "dbDate")
        self.assertIsNone(cols["Fecha_Alta"].get("length"))
        self.assertEqual(cols["Importe_Total"].get("dataType"), "numeric")
        self.assertEqual(cols["Importe_Total"].get("precision"), "15")
        self.assertEqual(cols["Importe_Total"].get("scale"), "2")
        self.assertEqual(cols["Indicador_Activo"].get("dataType"), "i1")
        self.assertIsNone(cols["Indicador_Activo"].get("length"))

    def test_teradata_source_external_metadata_column_matches_output_column(self):
        comp = self._teradata_source()
        out_normal = next(
            o for o in comp.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        oc = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        emc = {c.get("name"): c for c in out_normal.find("externalMetadataColumns").findall("externalMetadataColumn")}
        for name in oc:
            self.assertEqual(
                (oc[name].get("dataType"), oc[name].get("length"), oc[name].get("precision"), oc[name].get("scale")),
                (emc[name].get("dataType"), emc[name].get("length"), emc[name].get("precision"), emc[name].get("scale")),
                f"mismatch en {name}",
            )

    # --- 15-17: FlatFileColumn DataType codes ---
    def _flat_file_columns(self):
        inner_cm = self._flat_file_cm().find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        cols = inner_cm.find("DTS:FlatFileColumns", self.NS).findall("DTS:FlatFileColumn", self.NS)
        return {c.get("{www.microsoft.com/SqlServer/Dts}ObjectName"): c for c in cols}

    def test_flat_file_column_datatype_133_for_dbdate(self):
        cols = self._flat_file_columns()
        self.assertEqual(cols["Fecha_Alta"].get("{www.microsoft.com/SqlServer/Dts}DataType"), "133")

    def test_flat_file_column_datatype_131_for_numeric(self):
        cols = self._flat_file_columns()
        self.assertEqual(cols["Importe_Total"].get("{www.microsoft.com/SqlServer/Dts}DataType"), "131")

    def test_flat_file_column_datatype_16_for_i1(self):
        cols = self._flat_file_columns()
        self.assertEqual(cols["Indicador_Activo"].get("{www.microsoft.com/SqlServer/Dts}DataType"), "16")

    def test_flat_file_column_datatype_130_for_wstr_no_regression(self):
        cols = self._flat_file_columns()
        self.assertEqual(cols["Nombre_Cliente"].get("{www.microsoft.com/SqlServer/Dts}DataType"), "130")

    # --- 18: DataPrecision/DataScale ---
    def test_flat_file_column_numeric_has_precision_and_scale(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertEqual(cols["Importe_Total"].get(f"{NS}DataPrecision"), "15")
        self.assertEqual(cols["Importe_Total"].get(f"{NS}DataScale"), "2")

    # --- 19-20: ausencia de ColumnWidth ---
    def test_native_types_have_no_column_width(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        for name in ("Fecha_Alta", "Importe_Total", "Indicador_Activo"):
            self.assertIsNone(cols[name].get(f"{NS}ColumnWidth"), f"{name} no deberia tener ColumnWidth")

    def test_wstr_has_no_column_width_in_delimited(self):
        # Correccion especifica de Delimited: ni siquiera wstr/str llevan
        # ColumnWidth en Delimited (solo MaximumWidth) -- evidencia real.
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertIsNone(cols["Nombre_Cliente"].get(f"{NS}ColumnWidth"))

    # --- 21: MaximumWidth de str/wstr ---
    def test_wstr_has_maximum_width(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertEqual(cols["Nombre_Cliente"].get(f"{NS}MaximumWidth"), "100")

    def test_native_types_have_no_maximum_width(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        for name in ("Fecha_Alta", "Importe_Total", "Indicador_Activo"):
            self.assertIsNone(cols[name].get(f"{NS}MaximumWidth"))

    # --- 22: Delimited multicolumna con ';' ---
    def test_non_terminator_columns_use_semicolon_delimiter(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        for name in ("Nombre_Cliente", "Fecha_Alta", "Importe_Total"):
            self.assertEqual(cols[name].get(f"{NS}ColumnDelimiter"), "_x003B_")

    # --- 23: ColumnType='Delimited' en todas las columnas ---
    def test_all_columns_have_column_type_delimited(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        for name, col in cols.items():
            self.assertEqual(col.get(f"{NS}ColumnType"), "Delimited", f"{name} deberia tener ColumnType=Delimited")

    # --- 24: CRLF en ultima columna real ---
    def test_last_real_column_has_crlf_terminator(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertEqual(cols["Indicador_Activo"].get(f"{NS}ColumnDelimiter"), "_x000D__x000A_")

    # --- 25: sin sentinel EndLine ---
    def test_no_endline_sentinel_column(self):
        cols = self._flat_file_columns()
        self.assertNotIn("EndLine", cols)
        self.assertEqual(len(cols), 4)

    # --- 31: Overwrite=true ---
    def test_overwrite_is_true(self):
        comp = self._flat_file_destination()
        props = {p.get("name"): p.text for p in comp.find("properties").findall("property")}
        self.assertEqual(props.get("Overwrite"), "true")

    # --- 32: mappings y lineage ---
    def test_input_columns_reference_correct_lineage(self):
        comp = self._flat_file_destination()
        input_el = comp.find("inputs/input")
        in_cols = {c.get("cachedName"): c for c in input_el.find("inputColumns").findall("inputColumn")}
        ts = self._teradata_source()
        out_normal = next(
            o for o in ts.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        oc = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        for name, in_col in in_cols.items():
            self.assertEqual(in_col.get("lineageId"), oc[name].get("lineageId"))

    # --- 33: precision/scale propagados al destino ---
    def test_destination_external_metadata_has_precision_scale_for_numeric(self):
        comp = self._flat_file_destination()
        ext_cols = {
            c.get("name"): c
            for c in comp.find("inputs/input/externalMetadataColumns").findall("externalMetadataColumn")
        }
        self.assertEqual(ext_cols["Importe_Total"].get("precision"), "15")
        self.assertEqual(ext_cols["Importe_Total"].get("scale"), "2")

    # --- correccion puntual i2->ui2: confirmar que NO se filtra a otros
    # tipos (dbDate/numeric/i1/wstr) -- el override en
    # _FLAT_FILE_DESTINATION_EXTERNAL_TYPE_OVERRIDES tiene una unica
    # entrada ("i2") y este fixture no tiene ninguna columna i2, asi que
    # confirma el caso "sin override aplicable" de punta a punta. ---
    def test_external_metadata_types_unaffected_by_i2_override_for_other_types(self):
        comp = self._flat_file_destination()
        ext_cols = {
            c.get("name"): c
            for c in comp.find("inputs/input/externalMetadataColumns").findall("externalMetadataColumn")
        }
        self.assertEqual(ext_cols["Nombre_Cliente"].get("dataType"), "wstr")
        self.assertEqual(ext_cols["Fecha_Alta"].get("dataType"), "dbDate")
        self.assertEqual(ext_cols["Importe_Total"].get("dataType"), "numeric")
        self.assertEqual(ext_cols["Indicador_Activo"].get("dataType"), "i1")

    # --- 29: filename fijo ---
    def test_filename_is_fixed_no_date(self):
        cm = self._flat_file_cm()
        prop_expr = cm.find("DTS:PropertyExpression", self.NS)
        self.assertEqual(prop_expr.text, '"FlatFileNativeTypesV1_E2E.txt"')
        self.assertNotIn("getdate", prop_expr.text.lower())

    # --- topology (Data Flow sin componentes intermedios) ---
    def test_topology_is_source_to_destination_only(self):
        class_ids = {c["class_id"] for c in self.data_flow["components"]}
        self.assertEqual(class_ids, {"Microsoft.SSISTeradataSrc", "Microsoft.FlatFileDestination"})

    # --- security: sin infraestructura/credenciales reales ---
    def test_no_corporate_infrastructure_in_generated_artifact(self):
        with open(NATIVE_TYPES_E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("srvssisdatosqa", "srvsftp02", "tdgnn.ccba", "CDNI_Promociones_Parametros"):
            self.assertNotIn(forbidden, content)

    def test_no_secrets_in_generated_artifact(self):
        with open(NATIVE_TYPES_E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("Salt=", "IV=", "Algorithm=", "Password=", "DTS:Password", "TeraPassword"):
            self.assertNotIn(forbidden, content)


class NativeTypesRaggedRightRegressionTests(unittest.TestCase):
    """#28: RaggedRight no debe verse afectado por los cambios de Delimited
    multicolumna ni por el vocabulario de tipos nuevo -- regresion explicita
    post flat-file-native-column-types-v1."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _base_spec()
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_native_types_raggedright_regression_")
        cls.output_path = os.path.join(cls.tmp_dir, "Generated.dtsx")
        generate(cls.spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.root = ET.parse(cls.output_path).getroot()
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_ragged_right_columns_still_have_column_width(self):
        cm = next(
            cm for cm in self.root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )
        inner_cm = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        self.assertEqual(inner_cm.get("{www.microsoft.com/SqlServer/Dts}Format"), "RaggedRight")
        NS = "{www.microsoft.com/SqlServer/Dts}"
        cols = inner_cm.find("DTS:FlatFileColumns", self.NS).findall("DTS:FlatFileColumn", self.NS)
        non_terminator = [c for c in cols if c.get(f"{NS}ColumnType") is None]
        self.assertTrue(len(non_terminator) > 0)
        for c in non_terminator:
            self.assertIsNotNone(c.get(f"{NS}ColumnWidth"))
            self.assertEqual(c.get(f"{NS}ColumnDelimiter"), "")


class NativeTypesFilenameDynamicRegressionTests(unittest.TestCase):
    """#30: filename dinamico (parts[]/current_date) no se ve afectado por
    flat-file-native-column-types-v1 -- regresion explicita, reutilizando
    current_date junto con una columna de tipo nativo nuevo en el mismo
    Data Flow para confirmar que ambas capacidades son ortogonales."""

    def test_current_date_filename_combines_with_dbdate_column(self):
        spec = _base_spec()
        spec["data_flow"]["destination"]["connection_manager"]["filename"] = {
            "parts": [
                {"type": "literal", "value": "archivo_"},
                {"type": "current_date", "format": "yyyyMMdd"},
                {"type": "literal", "value": ".txt"},
            ]
        }
        spec["data_flow"]["source"]["columns"].append(
            {"name": "Fecha_Extra", "data_type": "dbDate"}
        )
        cols = spec["data_flow"]["destination"]["columns"]
        cols.insert(
            len(cols) - 1,
            {"source": "Fecha_Extra", "target": "Fecha_Extra", "target_data_type": "dbDate"},
        )
        self.assertEqual(validate_spec(spec, MEDIOS_DE_PAGO_CONTEXT), [])
        tmp_dir = tempfile.mkdtemp(prefix="ssis_native_types_dynamic_filename_regression_")
        try:
            output_path = os.path.join(tmp_dir, "Generated.dtsx")
            generate(spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, output_path)
            root = ET.parse(output_path).getroot()
            NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}
            cm = next(
                cm for cm in root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", NS)
                if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
            )
            prop_expr = cm.find("DTS:PropertyExpression", NS)
            self.assertIn("getdate()", prop_expr.text)
            ts = next(
                c for c in root.iter("component")
                if c.get("componentClassID") == "Microsoft.SSISTeradataSrc"
            )
            out_normal = next(
                o for o in ts.find("outputs").findall("output") if o.get("isErrorOut") != "true"
            )
            oc = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
            self.assertEqual(oc["Fecha_Extra"].get("dataType"), "dbDate")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class FlatFileNativeTypesStructuralComparisonTests(unittest.TestCase):
    """#35: comparacion estructural (solo metadata/configuracion, nunca
    GUIDs/connection strings/hosts/credenciales) entre
    FlatFileNativeTypesV1_E2E.dtsx (sintetico) y el paquete productivo real
    CDNI_Promociones_Parametros.dtsx (evidencia de SOLO LECTURA, nunca
    modificado ni commiteado)."""

    REAL_PACKAGE_PATH = os.path.join(
        REPO_ROOT, "Examples", "Originals", "CRM", "CDNI_Promociones_Parametros.dtsx"
    )

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(cls.REAL_PACKAGE_PATH):
            raise unittest.SkipTest(
                "CDNI_Promociones_Parametros.dtsx no disponible en este entorno "
                "(evidencia real, no commiteada -- ver docs/template_teradata_to_flat_file_v1.md)."
            )
        if not os.path.isfile(NATIVE_TYPES_E2E_OUTPUT_PATH):
            spec = _load_json(NATIVE_TYPES_E2E_SPEC_PATH)
            generate(spec, BIPSUC_CONTEXT, TEMPLATE_PATH, NATIVE_TYPES_E2E_OUTPUT_PATH)
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    def _flat_file_inner_cm(self, path):
        root = ET.parse(path).getroot()
        cm = next(
            cm for cm in root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )
        return cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)

    def test_connection_manager_level_attributes_match(self):
        real = self._flat_file_inner_cm(self.REAL_PACKAGE_PATH)
        new = self._flat_file_inner_cm(NATIVE_TYPES_E2E_OUTPUT_PATH)
        NS = "{www.microsoft.com/SqlServer/Dts}"
        for attr in ("Format", "LocaleID", "HeaderRowDelimiter", "RowDelimiter", "TextQualifier", "CodePage"):
            self.assertEqual(
                real.get(f"{NS}{attr}"), new.get(f"{NS}{attr}"), f"{attr} no coincide"
            )

    def test_flat_file_column_attribute_shape_matches_by_datatype(self):
        NS = "{www.microsoft.com/SqlServer/Dts}"
        ignored = {"ObjectName", "DTSID", "CreationName"}

        def shape_by_type(path):
            inner = self._flat_file_inner_cm(path)
            result = {}
            for c in inner.find("DTS:FlatFileColumns", self.NS).findall("DTS:FlatFileColumn", self.NS):
                dt = c.get(f"{NS}DataType")
                attrs = {k.split("}")[-1] for k in c.attrib} - ignored
                result.setdefault(dt, set()).update(attrs)
            return result

        real_shapes = shape_by_type(self.REAL_PACKAGE_PATH)
        new_shapes = shape_by_type(NATIVE_TYPES_E2E_OUTPUT_PATH)
        # Solo se compara para los DataType presentes en AMBOS (130/wstr,
        # 131/numeric, 133/dbDate, 16/i1) -- el E2E sintetico no reproduce
        # cada columna del paquete real, solo los 4 tipos representativos.
        common_types = set(real_shapes) & set(new_shapes)
        self.assertEqual(common_types, {"130", "131", "133", "16"})
        for dt in common_types:
            self.assertEqual(
                real_shapes[dt], new_shapes[dt],
                f"forma de atributos distinta para DataType={dt}",
            )


class I2I4PropagationTests(unittest.TestCase):
    """i2 (SMALLINT, DataType=18) / i4 (INTEGER, DataType=3) -- OPCION A de
    la auditoria de PromocionesNativeDelimitedV1. A diferencia de
    dbDate/numeric/i1, NO hay evidencia de un paquete real con estos tipos
    en un Flat File Destination -- los codigos son los ESTANDAR de
    plataforma SSIS. Estos tests verifican que la propagacion por las 5
    capas (outputColumn/externalMetadataColumn del Teradata Source,
    FlatFileColumn, inputColumn/externalMetadataColumn del Flat File
    Destination) es estructuralmente correcta; NO demuestran compatibilidad
    runtime con Teradata -- eso queda para el Gate SSDT."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _base_spec()
        cls.spec["data_flow"]["source"]["columns"] += [
            {"name": "Id_Producto_SMALLINT", "data_type": "i2"},
            {"name": "Id_Promo_INTEGER", "data_type": "i4"},
        ]
        cols = cls.spec["data_flow"]["destination"]["columns"]
        cols[:0] = [
            {"source": "Id_Producto_SMALLINT", "target": "Id_Producto_SMALLINT", "target_data_type": "i2"},
            {"source": "Id_Promo_INTEGER", "target": "Id_Promo_INTEGER", "target_data_type": "i4"},
        ]
        assert validate_spec(cls.spec, MEDIOS_DE_PAGO_CONTEXT) == []
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_i2_i4_propagation_")
        cls.output_path = os.path.join(cls.tmp_dir, "Generated.dtsx")
        generate(cls.spec, MEDIOS_DE_PAGO_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.root = ET.parse(cls.output_path).getroot()
        cls.NS = {"DTS": "www.microsoft.com/SqlServer/Dts"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_validator_has_no_errors(self):
        self.assertEqual(self.validation["errors"], [])

    def _teradata_source(self):
        return next(
            c for c in self.root.iter("component")
            if c.get("componentClassID") == "Microsoft.SSISTeradataSrc"
        )

    def test_teradata_source_output_column_types(self):
        comp = self._teradata_source()
        out_normal = next(
            o for o in comp.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        cols = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        self.assertEqual(cols["Id_Producto_SMALLINT"].get("dataType"), "i2")
        self.assertIsNone(cols["Id_Producto_SMALLINT"].get("length"))
        self.assertEqual(cols["Id_Promo_INTEGER"].get("dataType"), "i4")
        self.assertIsNone(cols["Id_Promo_INTEGER"].get("length"))

    def test_teradata_source_external_metadata_matches_output_column(self):
        comp = self._teradata_source()
        out_normal = next(
            o for o in comp.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        oc = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        emc = {c.get("name"): c for c in out_normal.find("externalMetadataColumns").findall("externalMetadataColumn")}
        for name in ("Id_Producto_SMALLINT", "Id_Promo_INTEGER"):
            self.assertEqual(oc[name].get("dataType"), emc[name].get("dataType"))
            self.assertEqual(oc[name].get("length"), emc[name].get("length"))

    def _flat_file_columns(self):
        cm = next(
            cm for cm in self.root.findall("DTS:ConnectionManagers/DTS:ConnectionManager", self.NS)
            if cm.get("{www.microsoft.com/SqlServer/Dts}CreationName") == "FLATFILE"
        )
        inner_cm = cm.find("DTS:ObjectData/DTS:ConnectionManager", self.NS)
        cols = inner_cm.find("DTS:FlatFileColumns", self.NS).findall("DTS:FlatFileColumn", self.NS)
        return {c.get("{www.microsoft.com/SqlServer/Dts}ObjectName"): c for c in cols}

    def test_flat_file_column_datatype_18_for_i2(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertEqual(cols["Id_Producto_SMALLINT"].get(f"{NS}DataType"), "18")

    def test_flat_file_column_datatype_3_for_i4(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertEqual(cols["Id_Promo_INTEGER"].get(f"{NS}DataType"), "3")

    def test_i2_i4_have_no_column_width_or_maximum_width(self):
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        for name in ("Id_Producto_SMALLINT", "Id_Promo_INTEGER"):
            self.assertIsNone(cols[name].get(f"{NS}ColumnWidth"))
            self.assertIsNone(cols[name].get(f"{NS}MaximumWidth"))

    def _flat_file_destination(self):
        return next(
            c for c in self.root.iter("component")
            if c.get("componentClassID") == "Microsoft.FlatFileDestination"
        )

    def test_destination_input_column_cached_types(self):
        comp = self._flat_file_destination()
        input_el = comp.find("inputs/input")
        in_cols = {c.get("cachedName"): c for c in input_el.find("inputColumns").findall("inputColumn")}
        self.assertEqual(in_cols["Id_Producto_SMALLINT"].get("cachedDataType"), "i2")
        self.assertEqual(in_cols["Id_Promo_INTEGER"].get("cachedDataType"), "i4")

    def test_destination_external_metadata_type_i2_becomes_ui2(self):
        # Correccion puntual confirmada por Gate SSDT real
        # (Examples/Originals/CRM/PromocionesNativeDelimitedV1.dtsx): SOLO
        # el externalMetadataColumn del Flat File Destination usa 'ui2'
        # para columnas i2 -- Teradata Source, inputColumn y
        # DTS:FlatFileColumn.DataType siguen en 'i2'/18 sin cambios (ver
        # tests dedicados).
        comp = self._flat_file_destination()
        ext_cols = {
            c.get("name"): c
            for c in comp.find("inputs/input/externalMetadataColumns").findall("externalMetadataColumn")
        }
        self.assertEqual(ext_cols["Id_Producto_SMALLINT"].get("dataType"), "ui2")

    def test_destination_external_metadata_type_i4_unchanged(self):
        # i4 NO presento esta discrepancia en el Gate real -- permanece
        # 'i4' en el externalMetadataColumn, sin override.
        comp = self._flat_file_destination()
        ext_cols = {
            c.get("name"): c
            for c in comp.find("inputs/input/externalMetadataColumns").findall("externalMetadataColumn")
        }
        self.assertEqual(ext_cols["Id_Promo_INTEGER"].get("dataType"), "i4")

    def test_teradata_source_i2_unchanged_by_the_override(self):
        # La correccion esta estrictamente acotada al externalMetadataColumn
        # del Flat File Destination -- el Teradata Source nunca debe verse
        # afectado.
        comp = self._teradata_source()
        out_normal = next(
            o for o in comp.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        oc = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        self.assertEqual(oc["Id_Producto_SMALLINT"].get("dataType"), "i2")
        emc = {c.get("name"): c for c in out_normal.find("externalMetadataColumns").findall("externalMetadataColumn")}
        self.assertEqual(emc["Id_Producto_SMALLINT"].get("dataType"), "i2")

    def test_flat_file_column_datatype_still_18_for_i2_not_19(self):
        # El override es puramente de 'externalMetadataColumn' (pipeline) --
        # el codigo fisico DTS:FlatFileColumn.DataType NO cambia (confirmado
        # en el Gate real: siguio en 18, nunca paso a 19/DT_UI2).
        cols = self._flat_file_columns()
        NS = "{www.microsoft.com/SqlServer/Dts}"
        self.assertEqual(cols["Id_Producto_SMALLINT"].get(f"{NS}DataType"), "18")

    def test_lineage_matches_between_source_and_destination(self):
        ts = self._teradata_source()
        out_normal = next(
            o for o in ts.find("outputs").findall("output") if o.get("isErrorOut") != "true"
        )
        oc = {c.get("name"): c for c in out_normal.find("outputColumns").findall("outputColumn")}
        comp = self._flat_file_destination()
        in_cols = {
            c.get("cachedName"): c
            for c in comp.find("inputs/input/inputColumns").findall("inputColumn")
        }
        for name in ("Id_Producto_SMALLINT", "Id_Promo_INTEGER"):
            self.assertEqual(in_cols[name].get("lineageId"), oc[name].get("lineageId"))

    def test_i2_i4_do_not_affect_existing_str_wstr_columns(self):
        # Regresion: agregar i2/i4 no debe alterar las columnas str/wstr
        # existentes en el mismo Data Flow.
        comp = self._flat_file_destination()
        ext_cols = {
            c.get("name"): c
            for c in comp.find("inputs/input/externalMetadataColumns").findall("externalMetadataColumn")
        }
        self.assertEqual(ext_cols["Fec_Proceso_PIC"].get("dataType"), "str")
        self.assertEqual(ext_cols["Nom_Cliente_PIC"].get("dataType"), "str")


if __name__ == "__main__":
    unittest.main()
