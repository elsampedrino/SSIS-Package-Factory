"""
Tests de mapping-planner-v1: Logical Mapping -> Transformation Plan ->
fragmento de ProcessSpec, consumido SIN CAMBIOS por
generator.spec_validator/generator.campanias_generator.

No se modifica generator/, project_generator/, project_context/, ni nada
de Control Flow -- mapping_planner/ es un paquete nuevo, independiente en
tiempo de ejecucion (mismo criterio que project_generator/, profiles/).

Cubre: clasificacion (DIRECT/CONVERSION_REQUIRED/AMBIGUOUS/UNSAFE/UNSUPPORTED)
sobre los casos reales evidenciados, prioridad de UNSAFE, seguridad
(incidente de truncacion), validacion estructural del Logical Mapping,
traduccion a ProcessSpec, y E2E completo contra el generador real.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssis_parser
import ssis_validator
from generator.campanias_generator import generate
from generator.spec_validator import validate_spec
from project_context.context_builder import build_project_context
from mapping_planner.planner import (
    OutputNameCollisionError,
    build_transformation_plan,
    classify_mapping,
)
from mapping_planner.schema import (
    AMBIGUOUS,
    CONVERSION_REQUIRED,
    DIRECT,
    UNSAFE,
    UNSUPPORTED,
)
from mapping_planner.translator import (
    PlanNotResolvedError,
    translate_plan_to_process_spec_fragment,
)
from mapping_planner.validator import (
    LogicalMappingValidationError,
    assert_valid_logical_mapping,
    validate_logical_mapping,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "campanias_base.dtsx")
FIXTURES_DIR = os.path.join(REPO_ROOT, "Examples", "Originals", "BipSuc_CampaniasVIgentes")
DTPROJ_PATH = os.path.join(FIXTURES_DIR, "BipSuc.dtproj")
PARAMS_PATH = os.path.join(FIXTURES_DIR, "Project.params")


def _str_col(name, length, code_page=1252):
    return {"name": name, "data_type": "str", "length": length, "code_page": code_page}


def _wstr_col(name, length):
    return {"name": name, "data_type": "wstr", "length": length}


def _numeric_col(name, precision, scale):
    return {"name": name, "data_type": "numeric", "precision": precision, "scale": scale}


def _to_wstr(source, target, length):
    return {"source": source, "target": target, "target_data_type": "wstr", "target_length": length}


def _to_str(source, target, length, code_page=1252):
    return {
        "source": source, "target": target, "target_data_type": "str",
        "target_length": length, "target_code_page": code_page,
    }


def _to_numeric(source, target, precision, scale):
    return {
        "source": source, "target": target, "target_data_type": "numeric",
        "target_precision": precision, "target_scale": scale,
    }


class ClassificationRealCasesTests(unittest.TestCase):
    """Casos reales obligatorios (ver docs/mapping_planner_v1.md, seccion 2)."""

    def test_pagos_y_recaudaciones_six_str_to_wstr_conversion_required(self):
        cases = [
            ("Id_Evento", 20), ("Cod_Identif_Tributaria", 10),
            ("Num_Identif_Tributaria", 20), ("Desc_Tipo_Impuesto", 255),
            ("Desc_Movimiento_Trx", 100), ("Cod_Moneda", 3),
        ]
        for name, length in cases:
            with self.subTest(column=name):
                classification, _ = classify_mapping(
                    {"data_type": "str", "length": length, "code_page": 1252},
                    {"target_data_type": "wstr", "target_length": length},
                )
                self.assertEqual(classification, CONVERSION_REQUIRED)

    def test_campanias_str_to_dbdate_ambiguous(self):
        classification, _ = classify_mapping(
            {"data_type": "str", "length": 10, "code_page": 1252},
            {"target_data_type": "dbDate"},
        )
        self.assertEqual(classification, AMBIGUOUS)

    def test_direct_str_widening_sector_case(self):
        # Sector str(2) -> CanalIncorporacion str(20), real en PagosYRecaudaciones.
        classification, _ = classify_mapping(
            {"data_type": "str", "length": 2, "code_page": 1252},
            {"target_data_type": "str", "target_length": 20, "target_code_page": 1252},
        )
        self.assertEqual(classification, DIRECT)

    def test_dbdate_to_wstr_direct(self):
        # Fec_Evento dbDate -> wstr, real en PagosYRecaudaciones (y FechaInicio_SAL en Campanias).
        classification, reason = classify_mapping(
            {"data_type": "dbDate"},
            {"target_data_type": "wstr", "target_length": 27},
        )
        self.assertEqual(classification, DIRECT)
        self.assertIn("GO CON RESTRICCIONES", reason)

    def test_numeric_exact_match_direct(self):
        for precision, scale in ((15, 2), (9, 5)):
            with self.subTest(precision=precision, scale=scale):
                classification, _ = classify_mapping(
                    {"data_type": "numeric", "precision": precision, "scale": scale},
                    {"target_data_type": "numeric", "target_precision": precision, "target_scale": scale},
                )
                self.assertEqual(classification, DIRECT)


class SafetyTests(unittest.TestCase):
    """UNSAFE siempre tiene prioridad, incluso sobre pares con evidencia real."""

    def test_wstr_length_reduction_unsafe(self):
        classification, _ = classify_mapping(
            {"data_type": "wstr", "length": 10}, {"target_data_type": "wstr", "target_length": 5}
        )
        self.assertEqual(classification, UNSAFE)

    def test_str_length_reduction_unsafe(self):
        classification, _ = classify_mapping(
            {"data_type": "str", "length": 20, "code_page": 1252},
            {"target_data_type": "str", "target_length": 10, "target_code_page": 1252},
        )
        self.assertEqual(classification, UNSAFE)

    def test_str_to_wstr_length_reduction_is_unsafe_not_conversion_required(self):
        # str(20) -> wstr(10): aunque str->wstr sea un par conocido, la
        # reduccion de longitud gana -- UNSAFE, nunca CONVERSION_REQUIRED.
        classification, _ = classify_mapping(
            {"data_type": "str", "length": 20, "code_page": 1252},
            {"target_data_type": "wstr", "target_length": 10},
        )
        self.assertEqual(classification, UNSAFE)

    def test_numeric_precision_reduction_unsafe(self):
        classification, _ = classify_mapping(
            {"data_type": "numeric", "precision": 15, "scale": 2},
            {"target_data_type": "numeric", "target_precision": 9, "target_scale": 2},
        )
        self.assertEqual(classification, UNSAFE)

    def test_numeric_scale_reduction_unsafe(self):
        classification, _ = classify_mapping(
            {"data_type": "numeric", "precision": 15, "scale": 5},
            {"target_data_type": "numeric", "target_precision": 15, "target_scale": 2},
        )
        self.assertEqual(classification, UNSAFE)

    def test_plasticos_incident_regression_string_capacity_reduction_blocked(self):
        """
        Regresion conceptual del incidente de truncacion real (origen con
        mayor capacidad que el destino producia valores truncados/incorrectos
        en produccion, detectado recien en tiempo de ejecucion). El planner
        debe bloquear ANTES de que exista XML -- no se modela el caso
        SMALLINT/DT_WSTR(1) literal (fuera de los tipos soportados en v1),
        solo la regla general: reduccion de capacidad = UNSAFE, siempre.
        """
        classification, reason = classify_mapping(
            {"data_type": "wstr", "length": 4}, {"target_data_type": "wstr", "target_length": 1}
        )
        self.assertEqual(classification, UNSAFE)
        self.assertIn("nunca automatico", reason)


class UnsupportedTests(unittest.TestCase):
    def test_numeric_widening_unsupported(self):
        classification, _ = classify_mapping(
            {"data_type": "numeric", "precision": 9, "scale": 5},
            {"target_data_type": "numeric", "target_precision": 15, "target_scale": 5},
        )
        self.assertEqual(classification, UNSUPPORTED)

    def test_str_to_wstr_widening_unsupported(self):
        classification, _ = classify_mapping(
            {"data_type": "str", "length": 20, "code_page": 1252},
            {"target_data_type": "wstr", "target_length": 30},
        )
        self.assertEqual(classification, UNSUPPORTED)

    def test_numeric_to_wstr_unsupported(self):
        classification, _ = classify_mapping(
            {"data_type": "numeric", "precision": 9, "scale": 5},
            {"target_data_type": "wstr", "target_length": 20},
        )
        self.assertEqual(classification, UNSUPPORTED)

    def test_dbdate_to_str_unsupported(self):
        classification, _ = classify_mapping(
            {"data_type": "dbDate"},
            {"target_data_type": "str", "target_length": 10, "target_code_page": 1252},
        )
        self.assertEqual(classification, UNSUPPORTED)

    def test_cross_integer_unsupported(self):
        classification, _ = classify_mapping({"data_type": "i2"}, {"target_data_type": "i4"})
        self.assertEqual(classification, UNSUPPORTED)

    def test_unknown_type_unsupported(self):
        classification, _ = classify_mapping(
            {"data_type": "bool"}, {"target_data_type": "numeric", "target_precision": 1, "target_scale": 0}
        )
        self.assertEqual(classification, UNSUPPORTED)

    def test_integer_same_type_direct(self):
        for int_type in ("i2", "i4", "i8"):
            with self.subTest(data_type=int_type):
                classification, _ = classify_mapping(
                    {"data_type": int_type}, {"target_data_type": int_type}
                )
                self.assertEqual(classification, DIRECT)


class LogicalMappingValidationTests(unittest.TestCase):
    def _spec(self, **overrides):
        spec = {
            "source_schema": [_str_col("Id_Evento", 20)],
            "mappings": [_to_wstr("Id_Evento", "Id_Evento", 20)],
        }
        spec.update(overrides)
        return spec

    def test_valid_spec_has_no_errors(self):
        self.assertEqual(validate_logical_mapping(self._spec()), [])

    def test_source_does_not_exist(self):
        spec = self._spec(mappings=[_to_wstr("NoExiste", "X", 20)])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("no existe en 'source_schema'" in e for e in errors))

    def test_duplicate_target(self):
        spec = self._spec(
            source_schema=[_str_col("A", 10), _str_col("B", 10)],
            mappings=[_to_wstr("A", "X", 10), _to_wstr("B", "X", 10)],
        )
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("target duplicado" in e for e in errors))

    def test_duplicate_source_schema_name(self):
        spec = self._spec(source_schema=[_str_col("A", 10), _str_col("A", 20)])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("mas de una vez en source_schema" in e for e in errors))

    def test_duplicate_mapping_pair(self):
        spec = self._spec(
            mappings=[_to_wstr("Id_Evento", "X", 20), _to_wstr("Id_Evento", "X", 20)]
        )
        errors = validate_logical_mapping(spec)
        # Colisiona como target duplicado Y como par duplicado -- ambos mensajes esperados.
        self.assertTrue(any("target duplicado" in e for e in errors))
        self.assertTrue(any("esta duplicado" in e for e in errors))

    def test_missing_length_for_string_type(self):
        spec = self._spec(source_schema=[{"name": "A", "data_type": "str", "code_page": 1252}])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("'length'" in e for e in errors))

    def test_missing_code_page_for_str(self):
        spec = self._spec(source_schema=[{"name": "A", "data_type": "str", "length": 10}])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("'code_page'" in e for e in errors))

    def test_missing_precision_and_scale_for_numeric(self):
        spec = self._spec(source_schema=[{"name": "A", "data_type": "numeric"}])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("'precision'" in e for e in errors))
        self.assertTrue(any("'scale'" in e for e in errors))

    def test_unknown_type_does_not_fail_validation(self):
        # Un tipo desconocido NO es un error de validacion -- el planner lo
        # clasifica UNSUPPORTED en el Transformation Plan (ver ese test).
        spec = self._spec(
            source_schema=[{"name": "A", "data_type": "bool"}],
            mappings=[{"source": "A", "target": "X", "target_data_type": "numeric",
                       "target_precision": 1, "target_scale": 0}],
        )
        self.assertEqual(validate_logical_mapping(spec), [])

    def test_negative_length_rejected(self):
        spec = self._spec(source_schema=[{"name": "A", "data_type": "str", "length": -5, "code_page": 1252}])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("'length'" in e for e in errors))

    def test_invalid_numeric_scale_rejected(self):
        spec = self._spec(source_schema=[{"name": "A", "data_type": "numeric", "precision": 10, "scale": -1}])
        errors = validate_logical_mapping(spec)
        self.assertTrue(any("'scale'" in e for e in errors))

    def test_empty_source_schema_rejected(self):
        errors = validate_logical_mapping(self._spec(source_schema=[]))
        self.assertTrue(any("source_schema" in e for e in errors))

    def test_empty_mappings_rejected(self):
        errors = validate_logical_mapping(self._spec(mappings=[]))
        self.assertTrue(any("mappings" in e for e in errors))

    def test_assert_raises_on_invalid_spec(self):
        with self.assertRaises(LogicalMappingValidationError):
            assert_valid_logical_mapping(self._spec(mappings=[]))


class OutputNameCollisionTests(unittest.TestCase):
    def test_collision_with_source_schema_name_raises(self):
        spec = {
            "source_schema": [_str_col("Id_Evento", 20), _str_col("Id_Evento__conv", 20)],
            "mappings": [
                _to_wstr("Id_Evento", "Id_Evento", 20),
                _to_wstr("Id_Evento__conv", "Otro", 20),
            ],
        }
        with self.assertRaises(OutputNameCollisionError):
            build_transformation_plan(spec)


class TransformationPlanStructureTests(unittest.TestCase):
    def test_direct_entry_has_null_conversion(self):
        spec = {
            "source_schema": [_numeric_col("M", 15, 2)],
            "mappings": [_to_numeric("M", "M", 15, 2)],
        }
        plan = build_transformation_plan(spec)
        self.assertEqual(plan[0]["classification"], DIRECT)
        self.assertIsNone(plan[0]["conversion"])

    def test_conversion_required_entry_has_conversion_dict(self):
        spec = {
            "source_schema": [_str_col("Id_Evento", 20)],
            "mappings": [_to_wstr("Id_Evento", "Id_Evento", 20)],
        }
        plan = build_transformation_plan(spec)
        entry = plan[0]
        self.assertEqual(entry["classification"], CONVERSION_REQUIRED)
        self.assertEqual(
            entry["conversion"],
            {"input": "Id_Evento", "output": "Id_Evento__conv", "target_type": "wstr", "target_length": 20},
        )

    def test_plan_entries_are_json_serializable(self):
        import json

        spec = {
            "source_schema": [_str_col("A", 10), _numeric_col("B", 9, 5)],
            "mappings": [_to_wstr("A", "A", 10), _to_numeric("B", "B", 9, 5)],
        }
        plan = build_transformation_plan(spec)
        json.dumps(plan)  # no debe lanzar


class TranslatorTests(unittest.TestCase):
    def test_six_pagos_y_recaudaciones_conversions_grouped_in_one_data_conversion(self):
        columns = [
            ("Id_Evento", 20), ("Cod_Identif_Tributaria", 10),
            ("Num_Identif_Tributaria", 20), ("Desc_Tipo_Impuesto", 255),
            ("Desc_Movimiento_Trx", 100), ("Cod_Moneda", 3),
        ]
        spec = {
            "source_schema": [_str_col(name, length) for name, length in columns],
            "mappings": [_to_wstr(name, name, length) for name, length in columns],
        }
        plan = build_transformation_plan(spec)
        self.assertTrue(all(e["classification"] == CONVERSION_REQUIRED for e in plan))

        fragment = translate_plan_to_process_spec_fragment(plan)
        self.assertEqual(len(fragment["transformations"]), 1)
        self.assertEqual(fragment["transformations"][0]["type"], "data_conversion")
        self.assertEqual(len(fragment["transformations"][0]["conversions"]), 6)
        self.assertEqual(len(fragment["mappings"]), 6)
        for name, length in columns:
            with self.subTest(column=name):
                mapping = next(m for m in fragment["mappings"] if m["target"] == name)
                self.assertEqual(mapping["source"], f"{name}__conv")
                self.assertEqual(mapping["target_data_type"], "wstr")
                self.assertEqual(mapping["target_length"], length)

    def test_direct_only_plan_has_no_transformations(self):
        spec = {
            "source_schema": [_numeric_col("M", 15, 2)],
            "mappings": [_to_numeric("M", "M", 15, 2)],
        }
        plan = build_transformation_plan(spec)
        fragment = translate_plan_to_process_spec_fragment(plan)
        self.assertEqual(fragment["transformations"], [])
        self.assertEqual(len(fragment["mappings"]), 1)
        self.assertEqual(fragment["mappings"][0], {"source": "M", "target": "M", "target_data_type": "numeric", "target_precision": 15, "target_scale": 2})

    def test_ambiguous_entry_blocks_translation(self):
        spec = {
            "source_schema": [_str_col("F", 10)],
            "mappings": [{"source": "F", "target": "F", "target_data_type": "dbDate"}],
        }
        plan = build_transformation_plan(spec)
        with self.assertRaises(PlanNotResolvedError) as ctx:
            translate_plan_to_process_spec_fragment(plan)
        self.assertEqual(len(ctx.exception.blocking_entries), 1)
        self.assertEqual(ctx.exception.blocking_entries[0]["classification"], AMBIGUOUS)

    def test_unsafe_entry_blocks_translation(self):
        spec = {
            "source_schema": [_wstr_col("A", 10)],
            "mappings": [_to_wstr("A", "A", 5)],
        }
        plan = build_transformation_plan(spec)
        with self.assertRaises(PlanNotResolvedError):
            translate_plan_to_process_spec_fragment(plan)

    def test_unsupported_entry_blocks_translation(self):
        spec = {
            "source_schema": [_numeric_col("N", 9, 5)],
            "mappings": [_to_wstr("N", "N", 20)],
        }
        plan = build_transformation_plan(spec)
        with self.assertRaises(PlanNotResolvedError):
            translate_plan_to_process_spec_fragment(plan)

    def test_mixed_plan_partial_block_lists_only_blocking_entries(self):
        spec = {
            "source_schema": [_numeric_col("Ok", 15, 2), _str_col("Bad", 10)],
            "mappings": [
                _to_numeric("Ok", "Ok", 15, 2),
                {"source": "Bad", "target": "Bad", "target_data_type": "dbDate"},
            ],
        }
        plan = build_transformation_plan(spec)
        with self.assertRaises(PlanNotResolvedError) as ctx:
            translate_plan_to_process_spec_fragment(plan)
        self.assertEqual(len(ctx.exception.blocking_entries), 1)
        self.assertEqual(ctx.exception.blocking_entries[0]["source"], "Bad")


class EndToEndTests(unittest.TestCase):
    """Logical Mapping -> Plan -> fragmento -> ProcessSpec -> spec_validator
    -> campanias_generator -> ssis_parser/ssis_validator, contra el
    ProjectContext real de BipSuc. Un caso DIRECT (numeric), un caso
    CONVERSION_REQUIRED (str->wstr) y un caso DIRECT sin metadata (i4) --
    ningun AMBIGUOUS/UNSAFE/UNSUPPORTED en este caso exitoso."""

    @classmethod
    def setUpClass(cls):
        cls.logical_mapping = {
            "source_schema": [
                _str_col("Id_Evento", 20),
                _numeric_col("Mto_Evento", 15, 2),
                {"name": "Cod_Campania", "data_type": "i4"},
            ],
            "mappings": [
                _to_wstr("Id_Evento", "Id_Evento", 20),
                _to_numeric("Mto_Evento", "Mto_Evento", 15, 2),
                {"source": "Cod_Campania", "target": "CodigoCampania", "target_data_type": "i4"},
            ],
        }
        cls.plan = build_transformation_plan(cls.logical_mapping)
        cls.fragment = translate_plan_to_process_spec_fragment(cls.plan)

        cls.process_spec = {
            "package": {"name": "MappingPlannerV1E2ETest"},
            "data_flow": {
                "name": "Flujo Mapping Planner",
                "source": {
                    "type": "teradata",
                    "name": "Origen Teradata",
                    "connection": "cnxTeradata",
                    "sql": "SELECT Id_Evento, Mto_Evento, Cod_Campania FROM TABLA_SINTETICA",
                    "columns": cls.logical_mapping["source_schema"],
                },
                "transformations": cls.fragment["transformations"],
                "destination": {
                    "type": "ole_db",
                    "name": "Destino SQL",
                    "connection": "cnxSrvBsLogSBD01",
                    "table": "[staging].[MappingPlannerTest]",
                    "mappings": cls.fragment["mappings"],
                },
            },
        }

        cls.project_context = build_project_context(DTPROJ_PATH, PARAMS_PATH, FIXTURES_DIR)

        import tempfile

        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_mapping_planner_e2e_")
        cls.output_path = os.path.join(cls.tmp_dir, "MappingPlannerV1E2ETest.dtsx")
        generate(cls.process_spec, cls.project_context, TEMPLATE_PATH, cls.output_path)

        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.components_by_type = {c["type"]: c for c in cls.data_flow["components"]}

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_plan_has_one_direct_and_one_conversion_required_and_one_direct_int(self):
        by_source = {e["source"]: e for e in self.plan}
        self.assertEqual(by_source["Id_Evento"]["classification"], CONVERSION_REQUIRED)
        self.assertEqual(by_source["Mto_Evento"]["classification"], DIRECT)
        self.assertEqual(by_source["Cod_Campania"]["classification"], DIRECT)

    def test_process_spec_passes_existing_spec_validator_unchanged(self):
        errors = validate_spec(self.process_spec, self.project_context)
        self.assertEqual(errors, [])

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_data_conversion_and_destination_present(self):
        self.assertIn("data_conversion", self.components_by_type)
        self.assertIn("ole_db_destination", self.components_by_type)
        self.assertIn("teradata_source", self.components_by_type)

    def test_mappings_resolve_correctly(self):
        dest = self.components_by_type["ole_db_destination"]
        resolved = {m["pipeline_input_column"]: m for m in dest["mappings"]}
        self.assertEqual(resolved["Id_Evento__conv"]["destination_column"], "Id_Evento")
        self.assertEqual(resolved["Mto_Evento"]["destination_column"], "Mto_Evento")
        self.assertEqual(resolved["Cod_Campania"]["destination_column"], "CodigoCampania")


if __name__ == "__main__":
    unittest.main()
