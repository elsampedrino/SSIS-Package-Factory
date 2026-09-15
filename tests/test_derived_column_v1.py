"""
Tests de derived-column-v1. Cubren (ver docs/derived_column_v1.md):

- derived_planner: clasificacion (GO/CONVERSION_REQUIRED, UNSAFE, UNSUPPORTED),
  validacion estructural, coleccion de outputs y colisiones, traduccion a
  fragmento de ProcessSpec.
- generator/spec_validator.py: extension aditiva de 'transformations[]' para
  soportar 'derived_column' ademas de 'data_conversion' -- coexistencia,
  orden estable, rechazo de operacion/target no soportados, specs legacy
  siguen validando igual que antes.
- generator/campanias_generator.py: serializacion de Microsoft.DerivedColumn
  (componentClassID, input, output sincronico, outputColumn, Expression,
  FriendlyExpression, FailComponent, output de error, lineage, paths,
  mapeo del output derivado en el destino).
- E2E dedicado DerivedColumnV1_E2E: Teradata Source -> Data Conversion ->
  Derived Column -> OLE DB Destination, con un ProjectContext real (BipSuc).

No se usan datos productivos reales -- solo el patron estructural ya
publico (PLASTICOSUDN i2 -> wstr, ver docs/derived_column_v1.md).
"""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssis_parser
import ssis_validator
from generator.campanias_generator import generate
from generator.spec_validator import validate_spec
from project_context.context_builder import build_project_context

from derived_planner.planner import (
    OutputNameCollisionError,
    build_derived_column_plan,
    classify_derived_rule,
)
from derived_planner.schema import CONVERSION_REQUIRED, UNSAFE, UNSUPPORTED
from derived_planner.translator import (
    PlanNotResolvedError,
    translate_plan_to_process_spec_fragment,
)
from derived_planner.validator import DerivedRulesValidationError, validate_derived_rules

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "campanias_base.dtsx")
SPEC_PATH = os.path.join(REPO_ROOT, "specs", "campanias_generated.json")
E2E_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "derived_column_v1_e2e.json")
E2E_OUTPUT_PATH = os.path.join(REPO_ROOT, "DerivedColumnV1_E2E.dtsx")
FIXTURES_DIR = os.path.join(REPO_ROOT, "Examples", "Originals", "BipSuc_CampaniasVIgentes")
DTPROJ_PATH = os.path.join(FIXTURES_DIR, "BipSuc.dtproj")
PARAMS_PATH = os.path.join(FIXTURES_DIR, "Project.params")

PROJECT_CONTEXT = build_project_context(DTPROJ_PATH, PARAMS_PATH, FIXTURES_DIR)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# derived_planner.planner.classify_derived_rule -- matriz de clasificacion
# ---------------------------------------------------------------------------
class ClassificationTests(unittest.TestCase):
    def _classify(self, source_type, target_length, target_type="wstr", operation="null_preserving_cast"):
        source_meta = {"data_type": source_type}
        rule = {"operation": operation, "target_type": target_type, "target_length": target_length}
        return classify_derived_rule(source_meta, rule)

    # -- GO / CONVERSION_REQUIRED: capacidad declarada suficiente ----------
    def test_i2_to_wstr_6_is_conversion_required(self):
        classification, _ = self._classify("i2", 6)
        self.assertEqual(classification, CONVERSION_REQUIRED)

    def test_i2_to_wstr_10_is_conversion_required(self):
        classification, _ = self._classify("i2", 10)
        self.assertEqual(classification, CONVERSION_REQUIRED)

    def test_i4_to_wstr_11_is_conversion_required(self):
        classification, _ = self._classify("i4", 11)
        self.assertEqual(classification, CONVERSION_REQUIRED)

    def test_i8_to_wstr_20_is_conversion_required(self):
        classification, _ = self._classify("i8", 20)
        self.assertEqual(classification, CONVERSION_REQUIRED)

    # -- UNSAFE: capacidad declarada insuficiente ---------------------------
    def test_i2_to_wstr_1_is_unsafe_plasticosudn_regression(self):
        # Caso real que motivo este milestone (ver docs/derived_column_v1.md):
        # PLASTICOSUDN (i2) -> wstr(1) truncaba silenciosamente en produccion.
        # Aca solo se usa el patron ESTRUCTURAL (i2 -> longitud declarada 1),
        # nunca un valor de dato real.
        classification, reason = self._classify("i2", 1)
        self.assertEqual(classification, UNSAFE)
        self.assertIn("insuficiente", reason)

    def test_i2_to_wstr_5_is_unsafe(self):
        classification, _ = self._classify("i2", 5)
        self.assertEqual(classification, UNSAFE)

    def test_i4_to_wstr_10_is_unsafe(self):
        classification, _ = self._classify("i4", 10)
        self.assertEqual(classification, UNSAFE)

    def test_i8_to_wstr_19_is_unsafe(self):
        classification, _ = self._classify("i8", 19)
        self.assertEqual(classification, UNSAFE)

    # -- UNSUPPORTED: tipos/operaciones fuera del alcance de v1 -------------
    def test_i2_to_str_is_unsupported(self):
        classification, _ = self._classify("i2", 6, target_type="str")
        self.assertEqual(classification, UNSUPPORTED)

    def test_wstr_to_wstr_is_unsupported(self):
        classification, _ = self._classify("wstr", 6)
        self.assertEqual(classification, UNSUPPORTED)

    def test_dbdate_to_wstr_is_unsupported(self):
        classification, _ = self._classify("dbDate", 27)
        self.assertEqual(classification, UNSUPPORTED)

    def test_numeric_to_wstr_is_unsupported(self):
        classification, _ = self._classify("numeric", 20)
        self.assertEqual(classification, UNSUPPORTED)

    def test_operation_cast_is_unsupported(self):
        classification, _ = self._classify("i2", 6, operation="cast")
        self.assertEqual(classification, UNSUPPORTED)

    def test_operation_constant_is_unsupported(self):
        classification, _ = self._classify("i2", 6, operation="constant")
        self.assertEqual(classification, UNSUPPORTED)

    def test_operation_arbitrary_expression_is_unsupported(self):
        classification, _ = self._classify("i2", 6, operation="arbitrary_expression")
        self.assertEqual(classification, UNSUPPORTED)

    def test_unknown_operation_is_unsupported(self):
        classification, _ = self._classify("i2", 6, operation="frobnicate")
        self.assertEqual(classification, UNSUPPORTED)


# ---------------------------------------------------------------------------
# derived_planner.validator -- validacion estructural
# ---------------------------------------------------------------------------
class ValidatorStructuralTests(unittest.TestCase):
    def _spec(self, **overrides):
        spec = {
            "source_schema": [{"name": "PLASTICOSUDN", "data_type": "i2"}],
            "derived_rules": [
                {
                    "source": "PLASTICOSUDN",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 6,
                }
            ],
        }
        spec.update(overrides)
        return spec

    def test_valid_spec_has_no_errors(self):
        self.assertEqual(validate_derived_rules(self._spec()), [])

    def test_missing_source_is_rejected(self):
        spec = self._spec(
            derived_rules=[
                {
                    "source": "NO_EXISTE",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 6,
                }
            ]
        )
        errors = validate_derived_rules(spec)
        self.assertTrue(any("no existe en 'source_schema'" in e for e in errors))

    def test_duplicate_source_columns_rejected(self):
        spec = self._spec(
            source_schema=[
                {"name": "PLASTICOSUDN", "data_type": "i2"},
                {"name": "PLASTICOSUDN", "data_type": "i2"},
            ]
        )
        errors = validate_derived_rules(spec)
        self.assertTrue(any("mas de una vez" in e for e in errors))

    def test_explicit_output_collision_with_source_column_rejected(self):
        spec = self._spec(
            derived_rules=[
                {
                    "source": "PLASTICOSUDN",
                    "output": "PLASTICOSUDN",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 6,
                }
            ]
        )
        errors = validate_derived_rules(spec)
        self.assertTrue(any("colisiona con un nombre de" in e for e in errors))

    def test_duplicate_explicit_output_rejected(self):
        spec = self._spec(
            source_schema=[
                {"name": "PLASTICOSUDN", "data_type": "i2"},
                {"name": "OTRA_COL", "data_type": "i4"},
            ],
            derived_rules=[
                {
                    "source": "PLASTICOSUDN",
                    "output": "Salida",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 6,
                },
                {
                    "source": "OTRA_COL",
                    "output": "Salida",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 11,
                },
            ],
        )
        errors = validate_derived_rules(spec)
        self.assertTrue(any("esta duplicado" in e for e in errors))

    def test_invalid_target_length_rejected(self):
        spec = self._spec(
            derived_rules=[
                {
                    "source": "PLASTICOSUDN",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 0,
                }
            ]
        )
        errors = validate_derived_rules(spec)
        self.assertTrue(any("target_length" in e for e in errors))

    def test_missing_metadata_rejected(self):
        spec = self._spec(
            derived_rules=[{"source": "PLASTICOSUDN"}],
        )
        errors = validate_derived_rules(spec)
        self.assertTrue(any("operation" in e for e in errors))
        self.assertTrue(any("target_type" in e for e in errors))
        self.assertTrue(any("target_length" in e for e in errors))

    def test_unknown_source_type_is_not_rejected_by_validator(self):
        # El validador NO conoce el vocabulario de tipos soportados (eso es
        # responsabilidad del planner, que clasifica UNSUPPORTED) -- mismo
        # criterio que mapping_planner.validator.
        spec = self._spec(source_schema=[{"name": "PLASTICOSUDN", "data_type": "un_tipo_raro"}])
        self.assertEqual(validate_derived_rules(spec), [])

    def test_empty_source_schema_rejected(self):
        errors = validate_derived_rules(self._spec(source_schema=[]))
        self.assertTrue(any("source_schema" in e for e in errors))

    def test_empty_derived_rules_rejected(self):
        errors = validate_derived_rules(self._spec(derived_rules=[]))
        self.assertTrue(any("derived_rules" in e for e in errors))


# ---------------------------------------------------------------------------
# derived_planner.planner.build_derived_column_plan + translator
# ---------------------------------------------------------------------------
class PlanAndTranslateTests(unittest.TestCase):
    def test_default_output_naming(self):
        spec = {
            "source_schema": [{"name": "PLASTICOSUDN", "data_type": "i2"}],
            "derived_rules": [
                {"source": "PLASTICOSUDN", "operation": "null_preserving_cast", "target_type": "wstr", "target_length": 6}
            ],
        }
        plan = build_derived_column_plan(spec)
        self.assertEqual(plan[0]["output"], "PLASTICOSUDN__derived")

    def test_duplicate_derived_rule_without_explicit_output_collides(self):
        # Dos reglas identicas sobre la misma 'source' sin 'output' explicito
        # generarian el mismo nombre por defecto dos veces -- debe rechazarse
        # explicitamente, nunca con un sufijo numerico automatico.
        spec = {
            "source_schema": [{"name": "PLASTICOSUDN", "data_type": "i2"}],
            "derived_rules": [
                {"source": "PLASTICOSUDN", "operation": "null_preserving_cast", "target_type": "wstr", "target_length": 6},
                {"source": "PLASTICOSUDN", "operation": "null_preserving_cast", "target_type": "wstr", "target_length": 8},
            ],
        }
        with self.assertRaises(OutputNameCollisionError):
            build_derived_column_plan(spec)

    def test_translate_go_plan_produces_expected_fragment(self):
        spec = {
            "source_schema": [{"name": "PLASTICOSUDN", "data_type": "i2"}],
            "derived_rules": [
                {"source": "PLASTICOSUDN", "operation": "null_preserving_cast", "target_type": "wstr", "target_length": 6}
            ],
        }
        plan = build_derived_column_plan(spec)
        fragment = translate_plan_to_process_spec_fragment(plan)
        self.assertEqual(fragment["type"], "derived_column")
        self.assertEqual(
            fragment["columns"],
            [
                {
                    "input": "PLASTICOSUDN",
                    "output": "PLASTICOSUDN__derived",
                    "operation": "null_preserving_cast",
                    "target_type": "wstr",
                    "target_length": 6,
                }
            ],
        )

    def test_translate_empty_plan_returns_none(self):
        self.assertIsNone(translate_plan_to_process_spec_fragment([]))

    def test_translate_unsafe_plan_raises_plan_not_resolved_error(self):
        spec = {
            "source_schema": [{"name": "PLASTICOSUDN", "data_type": "i2"}],
            "derived_rules": [
                {"source": "PLASTICOSUDN", "operation": "null_preserving_cast", "target_type": "wstr", "target_length": 1}
            ],
        }
        plan = build_derived_column_plan(spec)
        with self.assertRaises(PlanNotResolvedError):
            translate_plan_to_process_spec_fragment(plan)

    def test_invalid_spec_raises_validation_error_before_classifying(self):
        with self.assertRaises(DerivedRulesValidationError):
            build_derived_column_plan({"source_schema": [], "derived_rules": []})


# ---------------------------------------------------------------------------
# generator/spec_validator.py -- extension aditiva para 'derived_column'
# ---------------------------------------------------------------------------
class SpecValidatorDerivedColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy_spec = _load_json(SPEC_PATH)
        cls.e2e_spec = _load_json(E2E_SPEC_PATH)

    def test_legacy_spec_without_derived_column_still_validates(self):
        self.assertEqual(validate_spec(self.legacy_spec, PROJECT_CONTEXT), [])

    def test_e2e_spec_with_data_conversion_and_derived_column_is_valid(self):
        self.assertEqual(validate_spec(self.e2e_spec, PROJECT_CONTEXT), [])

    def test_derived_column_before_data_conversion_is_rejected(self):
        spec = copy.deepcopy(self.e2e_spec)
        transformations = spec["data_flow"]["transformations"]
        spec["data_flow"]["transformations"] = list(reversed(transformations))
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(errors)
        self.assertTrue(any("declararse ANTES" in e for e in errors))

    def test_unsupported_operation_is_rejected(self):
        spec = copy.deepcopy(self.e2e_spec)
        spec["data_flow"]["transformations"][1]["columns"][0]["operation"] = "cast"
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("operation" in e for e in errors))

    def test_unsupported_target_type_is_rejected(self):
        spec = copy.deepcopy(self.e2e_spec)
        spec["data_flow"]["transformations"][1]["columns"][0]["target_type"] = "str"
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_type" in e for e in errors))

    def test_missing_target_length_is_rejected(self):
        spec = copy.deepcopy(self.e2e_spec)
        del spec["data_flow"]["transformations"][1]["columns"][0]["target_length"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_length" in e for e in errors))

    def test_derived_column_input_must_exist_in_pipeline(self):
        spec = copy.deepcopy(self.e2e_spec)
        spec["data_flow"]["transformations"][1]["columns"][0]["input"] = "NO_EXISTE"
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("no existe entre las columnas disponibles" in e for e in errors))

    def test_derived_column_output_collision_with_pipeline_column_rejected(self):
        spec = copy.deepcopy(self.e2e_spec)
        spec["data_flow"]["transformations"][1]["columns"][0]["output"] = "CODIGO_CAMPANIA"
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("colisiona con un nombre" in e for e in errors))

    def test_two_derived_column_transformations_is_rejected_as_duplicate_type(self):
        # Sin 'data_conversion', para quedar dentro del tope de 2 elementos
        # totales y ejercitar especificamente la deteccion de tipo repetido
        # (no el tope general, ya cubierto por
        # test_more_transformations_than_supported_types_is_rejected en
        # tests/test_generator_campanias.py).
        spec = copy.deepcopy(self.e2e_spec)
        derived_column_transform = spec["data_flow"]["transformations"][1]
        spec["data_flow"]["transformations"] = [
            derived_column_transform,
            copy.deepcopy(derived_column_transform),
        ]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("esta repetido" in e for e in errors))

    def test_derived_column_alone_without_data_conversion_is_valid(self):
        spec = copy.deepcopy(self.e2e_spec)
        spec["data_flow"]["transformations"] = [spec["data_flow"]["transformations"][1]]
        # PLASTICOSUDN sigue existiendo en source.columns, la conversion
        # de fecha ya no es necesaria para este caso (se elimina tambien el
        # mapping que dependia de su output).
        spec["data_flow"]["destination"]["mappings"] = [
            m for m in spec["data_flow"]["destination"]["mappings"] if m["source"] != "FechaInicio_SAL"
        ]
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])


# ---------------------------------------------------------------------------
# generator/campanias_generator.py -- serializacion de Microsoft.DerivedColumn
# ---------------------------------------------------------------------------
class GeneratorDerivedColumnXMLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_json(E2E_SPEC_PATH)
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_derived_column_test_")
        cls.output_path = os.path.join(cls.tmp_dir, "DerivedColumnGenerated.dtsx")

        generate(cls.spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.output_path)

        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.components_by_type = {c["type"]: c for c in cls.data_flow["components"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_four_components_in_expected_order_topology(self):
        self.assertEqual(len(self.data_flow["components"]), 4)
        self.assertEqual(
            {c["type"] for c in self.data_flow["components"]},
            {"teradata_source", "data_conversion", "derived_column", "ole_db_destination"},
        )

    def test_three_paths_source_conversion_derived_destination(self):
        pairs = [(p["from"], p["to"]) for p in self.data_flow["paths"]]
        self.assertEqual(len(pairs), 3)
        source_name = self.spec["data_flow"]["source"]["name"]
        conv_name = self.spec["data_flow"]["transformations"][0]["name"]
        derived_name = self.spec["data_flow"]["transformations"][1]["name"]
        dest_name = self.spec["data_flow"]["destination"]["name"]
        self.assertEqual(
            pairs, [(source_name, conv_name), (conv_name, derived_name), (derived_name, dest_name)]
        )

    def test_derived_column_has_one_synchronous_input(self):
        dc = self.components_by_type["derived_column"]
        self.assertEqual(len(dc["inputs"]), 1)

    def test_derived_column_has_normal_and_error_output(self):
        dc = self.components_by_type["derived_column"]
        self.assertEqual(len(dc["outputs"]), 1)
        self.assertEqual(len(dc["error_outputs"]), 1)

    def test_derived_column_output_column_expression_and_friendly_expression(self):
        dc = self.components_by_type["derived_column"]
        normal_output = dc["outputs"][0]
        col = normal_output["columns"][0]
        self.assertEqual(col["name"], "PLASTICOSUDN__derived")
        self.assertEqual(col["data_type"], "wstr")
        self.assertEqual(col["length"], 6)
        self.assertIn("ISNULL", col["expression"])
        self.assertIn("DT_WSTR,6", col["expression"])
        self.assertEqual(
            col["friendly_expression"],
            "ISNULL(PLASTICOSUDN) ? NULL(DT_WSTR,6) : (DT_WSTR,6)PLASTICOSUDN",
        )

    def test_derived_column_fail_component_dispositions(self):
        dc = self.components_by_type["derived_column"]
        normal_output = dc["outputs"][0]
        col = normal_output["columns"][0]
        self.assertEqual(col["error_row_disposition"], "FailComponent")
        self.assertEqual(col["truncation_row_disposition"], "FailComponent")

    def test_derived_column_input_lineage_traces_to_teradata_source(self):
        # PLASTICOSUDN nunca pasa por Data Conversion -- la referencia de
        # lineage embebida en su Expression (#{...}, ver
        # generator/xml_helpers.id_reference_wrapper) debe apuntar directo
        # al Teradata Source, aun cuando el pipeline pasa (por buffer-
        # chaining) primero por Data Conversion. Derived Column no expone
        # 'source_input_lineage_id' como columna aparte (esa property,
        # 'SourceInputColumnLineageID', es especifica de Data Conversion) --
        # la referencia real vive dentro de Expression. Ver
        # docs/derived_column_v1.md, orden topologico.
        dc = self.components_by_type["derived_column"]
        normal_output = dc["outputs"][0]
        col = normal_output["columns"][0]
        self.assertIn("Teradata Source", col["expression"])
        self.assertIn("Columns[PLASTICOSUDN]", col["expression"])
        self.assertNotIn("Conversi", col["expression"])

    def test_destination_maps_direct_conversion_and_derived_columns(self):
        dest = self.components_by_type["ole_db_destination"]
        resolved = {m["pipeline_input_column"]: m for m in dest["mappings"]}
        self.assertEqual(len(dest["mappings"]), 3)
        self.assertIn("CODIGO_CAMPANIA", resolved)
        self.assertIn("FechaInicio_SAL", resolved)
        self.assertIn("PLASTICOSUDN__derived", resolved)
        derived_mapping = resolved["PLASTICOSUDN__derived"]
        self.assertEqual(derived_mapping["destination_column"], "PlasticosUdn")
        self.assertIsNotNone(derived_mapping["source_column"])
        self.assertEqual(derived_mapping["source_component"], "Columna derivada")


# ---------------------------------------------------------------------------
# Generacion sin Derived Column / sin Data Conversion -- no debe romper
# ninguna de las 4 combinaciones de topologia soportadas.
# ---------------------------------------------------------------------------
class TopologyCombinationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="ssis_derived_column_topology_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _generate(self, spec, filename):
        output_path = os.path.join(self.tmp_dir, filename)
        generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
        ir = ssis_parser.parse_file(output_path)
        validation = ssis_validator.validate_ir(ir)
        return ir, validation

    def test_source_data_conversion_derived_column_destination(self):
        spec = _load_json(E2E_SPEC_PATH)
        ir, validation = self._generate(spec, "AllFour.dtsx")
        self.assertEqual(validation["errors"], [])
        types = {c["type"] for c in ir["data_flows"][0]["components"]}
        self.assertEqual(
            types, {"teradata_source", "data_conversion", "derived_column", "ole_db_destination"}
        )

    def test_source_derived_column_destination_without_data_conversion(self):
        spec = copy.deepcopy(_load_json(E2E_SPEC_PATH))
        spec["data_flow"]["transformations"] = [spec["data_flow"]["transformations"][1]]
        spec["data_flow"]["destination"]["mappings"] = [
            m
            for m in spec["data_flow"]["destination"]["mappings"]
            if m["source"] != "FechaInicio_SAL"
        ]
        ir, validation = self._generate(spec, "NoConversion.dtsx")
        self.assertEqual(validation["errors"], [])
        types = {c["type"] for c in ir["data_flows"][0]["components"]}
        self.assertEqual(types, {"teradata_source", "derived_column", "ole_db_destination"})
        pairs = {(p["from"], p["to"]) for p in ir["data_flows"][0]["paths"]}
        self.assertIn(("Teradata Source", "Columna derivada"), pairs)

    def test_source_data_conversion_destination_without_derived_column(self):
        spec = _load_json(SPEC_PATH)
        ir, validation = self._generate(spec, "NoDerivedColumn.dtsx")
        self.assertEqual(validation["errors"], [])
        types = {c["type"] for c in ir["data_flows"][0]["components"]}
        self.assertEqual(types, {"teradata_source", "data_conversion", "ole_db_destination"})

    def test_source_destination_direct_without_any_transformation(self):
        spec = _load_json(os.path.join(REPO_ROOT, "specs", "synthetic_direct_mapping.json"))
        ir, validation = self._generate(spec, "Direct.dtsx")
        self.assertEqual(validation["errors"], [])
        types = {c["type"] for c in ir["data_flows"][0]["components"]}
        self.assertEqual(types, {"teradata_source", "ole_db_destination"})


# ---------------------------------------------------------------------------
# E2E dedicado: DerivedColumnV1_E2E
# ---------------------------------------------------------------------------
class DerivedColumnV1E2ETests(unittest.TestCase):
    """
    Genera el artefacto DerivedColumnV1_E2E.dtsx en la raiz del repo (mismo
    criterio que otros milestones -- artefacto tangible para revision manual
    en SSDT), usando el ProjectContext real de BipSuc. Topologia: Teradata
    Source -> Data Conversion -> Derived Column -> OLE DB Destination, con
    al menos 1 columna DIRECT, 1 columna via Data Conversion, y 1 columna
    derivada (i2 -> wstr(6) via null_preserving_cast). Tabla destino
    ficticia (mismo criterio que el E2E de mapping-planner-v1) -- el gate es
    estructural, no ejecucion contra base de datos real.
    """

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_json(E2E_SPEC_PATH)
        generate(cls.spec, PROJECT_CONTEXT, TEMPLATE_PATH, E2E_OUTPUT_PATH)
        cls.ir = ssis_parser.parse_file(E2E_OUTPUT_PATH)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]

    def test_spec_itself_is_valid(self):
        self.assertEqual(validate_spec(self.spec, PROJECT_CONTEXT), [])

    def test_package_is_valid_with_no_structural_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_expected_components_present(self):
        types = {c["type"] for c in self.data_flow["components"]}
        self.assertEqual(
            types, {"teradata_source", "data_conversion", "derived_column", "ole_db_destination"}
        )

    def test_expected_paths_present(self):
        self.assertEqual(len(self.data_flow["paths"]), 3)

    def test_design_time_properties_present(self):
        with open(E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("DesignTimeProperties", content)

    def test_final_mappings_are_consistent(self):
        by_type = {c["type"]: c for c in self.data_flow["components"]}
        dest = by_type["ole_db_destination"]
        resolved = {m["pipeline_input_column"] for m in dest["mappings"]}
        self.assertEqual(
            resolved, {"CODIGO_CAMPANIA", "FechaInicio_SAL", "PLASTICOSUDN__derived"}
        )
        for m in dest["mappings"]:
            self.assertIsNotNone(m["source_column"])
            self.assertIsNotNone(m["source_component"])

    def test_no_secrets_in_generated_artifact(self):
        with open(E2E_OUTPUT_PATH, encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("Salt=", "IV=", "Algorithm=", "Password=", "DTS:Password", "TeraPassword"):
            self.assertNotIn(forbidden, content)


if __name__ == "__main__":
    unittest.main()
