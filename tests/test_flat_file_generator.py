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
)
from generator.spec_validator import SpecValidationError, validate_spec
from project_context.context_builder import build_project_context

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "teradata_to_flat_file_base.dtsx")
E2E_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "teradata_to_flat_file_v1_e2e.json")
E2E_OUTPUT_PATH = os.path.join(REPO_ROOT, "TeradataToFlatFileV1_E2E.dtsx")

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


if __name__ == "__main__":
    unittest.main()
