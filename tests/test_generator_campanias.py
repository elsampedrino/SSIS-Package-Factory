"""
Tests del Generador MVP (solo Campanias). Cubren:

- Nivel 1 de validacion end-to-end: process_spec + ProjectContext ->
  generate() -> CampaniasGenerado.dtsx -> ssis_parser -> ssis_validator ->
  0 errores y equivalencia funcional contra el spec (no se exige igualdad
  de GUIDs).
- Que las reglas de validacion fail-fast de spec_validator.py efectivamente
  rechazan specs invalidos ANTES de tocar el template (ningun archivo se
  escribe cuando la validacion falla).
- project-context-v1: que la resolucion de Connection Managers (DTSID por
  nombre) venga del ProjectContext real del proyecto BipSuc, no de un
  diccionario hardcodeado (KNOWN_CONNECTION_MANAGERS ya no existe).
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
from generator.spec_validator import SpecValidationError, validate_spec
from project_context.context_builder import build_project_context

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "campanias_base.dtsx")
SPEC_PATH = os.path.join(REPO_ROOT, "specs", "campanias_generated.json")
FIXTURES_DIR = os.path.join(REPO_ROOT, "Examples", "Originals")
DTPROJ_PATH = os.path.join(FIXTURES_DIR, "BipSuc.dtproj")
PARAMS_PATH = os.path.join(FIXTURES_DIR, "Project.params")

# ProjectContext REAL del proyecto BipSuc -- construido una sola vez, a
# partir de los mismos fixtures que tests/test_project_context.py. Esto es
# el corazon de la integracion: el generador ya no conoce ningun GUID de
# antemano, los resuelve todos consultando esto.
PROJECT_CONTEXT = build_project_context(DTPROJ_PATH, PARAMS_PATH, FIXTURES_DIR)

EXPECTED_TERADATA_DTSID = "{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}"
EXPECTED_OLEDB_DTSID = "{5DA5808C-8489-48AC-8614-CB034DE02B61}"


def _load_spec():
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


class GeneratorEndToEndTests(unittest.TestCase):
    """process_spec + ProjectContext -> generator -> .dtsx -> ssis_parser -> ssis_validator."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_spec()
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_test_")
        cls.output_path = os.path.join(cls.tmp_dir, "CampaniasGenerado.dtsx")

        generate(cls.spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.output_path)

        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.components_by_type = {c["type"]: c for c in cls.data_flow["components"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_spec_itself_is_valid(self):
        self.assertEqual(validate_spec(self.spec, PROJECT_CONTEXT), [])

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_package_name_matches_spec(self):
        self.assertEqual(self.ir["package_name"], self.spec["package"]["name"])
        self.assertEqual(self.ir["source_file"], "CampaniasGenerado.dtsx")

    def test_one_data_flow_three_components(self):
        self.assertEqual(len(self.ir["data_flows"]), 1)
        self.assertEqual(len(self.data_flow["components"]), 3)
        self.assertEqual(
            {c["type"] for c in self.data_flow["components"]},
            {"teradata_source", "data_conversion", "ole_db_destination"},
        )

    def test_teradata_source_matches_spec(self):
        spec_source = self.spec["data_flow"]["source"]
        source = self.components_by_type["teradata_source"]
        self.assertEqual(source["name"], spec_source["name"])
        self.assertEqual(
            source["connection"], f"Project.ConnectionManagers[{spec_source['connection']}]"
        )
        self.assertEqual(source["properties"]["sql_command"], spec_source["sql"])
        output_cols = {c["name"]: c for c in source["outputs"][0]["columns"]}
        self.assertEqual(set(output_cols), {c["name"] for c in spec_source["columns"]})
        for col_spec in spec_source["columns"]:
            with self.subTest(column=col_spec["name"]):
                col = output_cols[col_spec["name"]]
                self.assertEqual(col["data_type"], col_spec["data_type"])
                self.assertEqual(col["length"], col_spec.get("length"))
                self.assertEqual(col["code_page"], col_spec.get("code_page"))

    def test_two_conversions_match_spec(self):
        spec_conversions = self.spec["data_flow"]["transformations"][0]["conversions"]
        conv = self.components_by_type["data_conversion"]
        out_cols = {c["name"]: c for c in conv["outputs"][0]["columns"]}
        self.assertEqual(len(out_cols), len(spec_conversions))
        for spec_conv in spec_conversions:
            with self.subTest(output=spec_conv["output"]):
                col = out_cols[spec_conv["output"]]
                self.assertEqual(col["data_type"], spec_conv["target_type"])
                self.assertTrue(
                    col["source_input_lineage_id"].endswith(
                        f"Columns[{spec_conv['input']}]"
                    )
                )

    def test_seven_mappings_resolve_correctly(self):
        spec_mappings = self.spec["data_flow"]["destination"]["mappings"]
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(len(dest["mappings"]), len(spec_mappings))

        resolved = {m["pipeline_input_column"]: m for m in dest["mappings"]}
        for spec_mapping in spec_mappings:
            with self.subTest(source=spec_mapping["source"]):
                mapping = resolved[spec_mapping["source"]]
                self.assertEqual(mapping["destination_column"], spec_mapping["target"])
                self.assertIsNotNone(mapping["source_column"])
                self.assertIsNotNone(mapping["source_component"])

    def test_destination_table_and_connection_match_spec(self):
        spec_dest = self.spec["data_flow"]["destination"]
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(dest["properties"]["open_rowset"], spec_dest["table"])
        self.assertEqual(
            dest["connection"], f"Project.ConnectionManagers[{spec_dest['connection']}]"
        )

    def test_paths_match_expected_topology(self):
        pairs = {(p["from"], p["to"]) for p in self.data_flow["paths"]}
        spec_source_name = self.spec["data_flow"]["source"]["name"]
        spec_conv_name = self.spec["data_flow"]["transformations"][0]["name"]
        spec_dest_name = self.spec["data_flow"]["destination"]["name"]
        self.assertEqual(len(self.data_flow["paths"]), 2)
        self.assertIn((spec_source_name, spec_conv_name), pairs)
        self.assertIn((spec_conv_name, spec_dest_name), pairs)

    def test_generated_dtsid_are_fresh_not_copied_from_template(self):
        # No se exige igualdad de GUIDs contra el original -- al contrario,
        # deben ser DISTINTOS (es un objeto de paquete nuevo).
        template_root_dtsid = "{03C7554A-0D65-44DD-9BEF-4D37D0AF5CB8}"
        self.assertNotEqual(self.ir["package_dtsid"], template_root_dtsid)
        self.assertIsNotNone(self.ir["package_dtsid"])


class ProjectContextIntegrationTests(unittest.TestCase):
    """project-context-v1: los Connection Managers que usa Campanias se
    resuelven desde el ProjectContext real de BipSuc, no desde un
    diccionario hardcodeado (que ya no existe en el codigo)."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_spec()
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_pc_test_")
        cls.output_path = os.path.join(cls.tmp_dir, "CampaniasGenerado.dtsx")
        generate(cls.spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _connection_manager_id_of(self, component_type):
        component = next(
            c for c in self.ir["data_flows"][0]["components"] if c["type"] == component_type
        )
        conn = component["connections"][0]
        return conn["connection_manager_id"]

    def test_campanias_resolves_cnxteradata_from_project_context(self):
        # 1. Campanias resuelve cnxTeradata desde ProjectContext.
        self.assertIn("cnxTeradata", PROJECT_CONTEXT["connections"])

    def test_campanias_resolves_cnxsrvbslogsbd01_from_project_context(self):
        # 2. Campanias resuelve cnxSrvBsLogSBD01 desde ProjectContext.
        self.assertIn("cnxSrvBsLogSBD01", PROJECT_CONTEXT["connections"])

    def test_generated_dtsids_match_expected_real_guids(self):
        # 3. Los DTSID resultantes en el .dtsx generado coinciden con los
        # DTSID reales de los .conmgr del proyecto (no con un valor inventado).
        self.assertEqual(
            self._connection_manager_id_of("teradata_source"),
            f"{EXPECTED_TERADATA_DTSID}:external",
        )
        self.assertEqual(
            self._connection_manager_id_of("ole_db_destination"),
            f"{EXPECTED_OLEDB_DTSID}:external",
        )

    def test_error_if_source_connection_missing_from_project_context(self):
        # 4. Error claro si falta la source connection.
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["connection"] = "cnxNoExisteEnElProyecto"
        output_path = os.path.join(self.tmp_dir, "should_not_exist_1.dtsx")
        with self.assertRaises(SpecValidationError) as ctx:
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
        self.assertIn("no existe en el ProjectContext", str(ctx.exception))
        self.assertFalse(os.path.exists(output_path))

    def test_error_if_destination_connection_missing_from_project_context(self):
        # 5. Error claro si falta la destination connection.
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["destination"]["connection"] = "cnxNoExisteEnElProyecto"
        output_path = os.path.join(self.tmp_dir, "should_not_exist_2.dtsx")
        with self.assertRaises(SpecValidationError) as ctx:
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
        self.assertIn("no existe en el ProjectContext", str(ctx.exception))
        self.assertFalse(os.path.exists(output_path))

    def test_error_if_source_provider_is_not_teradata(self):
        # 6. Error si el provider del source no es TERADATA.
        # cnxSrvBsLogSBD01 existe en el ProjectContext, pero es OLEDB.
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["connection"] = "cnxSrvBsLogSBD01"
        output_path = os.path.join(self.tmp_dir, "should_not_exist_3.dtsx")
        with self.assertRaises(SpecValidationError) as ctx:
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
        message = str(ctx.exception)
        self.assertIn("se esperaba 'TERADATA'", message)
        self.assertFalse(os.path.exists(output_path))

    def test_error_if_destination_provider_is_not_oledb(self):
        # 7. Error si el provider del destination no es OLEDB.
        # cnxTeradata existe en el ProjectContext, pero es TERADATA.
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["destination"]["connection"] = "cnxTeradata"
        output_path = os.path.join(self.tmp_dir, "should_not_exist_4.dtsx")
        with self.assertRaises(SpecValidationError) as ctx:
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
        message = str(ctx.exception)
        self.assertIn("se esperaba 'OLEDB'", message)
        self.assertFalse(os.path.exists(output_path))

    def test_does_not_depend_on_known_connection_managers_dict(self):
        # 8. No depender de KNOWN_CONNECTION_MANAGERS -- ya no existe en el
        # modulo. Este test falla con AttributeError/ImportError si alguien
        # lo reintroduce sin querer.
        import generator.xml_helpers as xml_helpers

        self.assertFalse(hasattr(xml_helpers, "KNOWN_CONNECTION_MANAGERS"))
        self.assertFalse(hasattr(xml_helpers, "resolve_connection_manager_id"))
        self.assertFalse(hasattr(xml_helpers, "UnknownConnectionManagerError"))

    def test_missing_project_context_is_rejected_explicitly(self):
        spec = copy.deepcopy(self.spec)
        output_path = os.path.join(self.tmp_dir, "should_not_exist_5.dtsx")
        with self.assertRaises(SpecValidationError) as ctx:
            generate(spec, None, TEMPLATE_PATH, output_path)
        self.assertIn("Falta 'project_context'", str(ctx.exception))
        self.assertFalse(os.path.exists(output_path))


class GeneratorRenamedTopologyTests(unittest.TestCase):
    """El generador debe funcionar igual de bien si el spec usa nombres
    distintos a los del template (no debe depender de los nombres originales
    de Campanias mas alla de las 3 sub-etiquetas fijas de input/output)."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_spec()
        cls.spec["data_flow"]["source"]["name"] = "Origen Teradata Renombrado"
        cls.spec["data_flow"]["destination"]["name"] = "Destino Renombrado"
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_test_renamed_")
        cls.output_path = os.path.join(cls.tmp_dir, "Renamed.dtsx")
        generate(cls.spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_renamed_components_still_validate_clean(self):
        self.assertTrue(self.validation["valid"])
        self.assertEqual(self.validation["errors"], [])
        names = {c["name"] for c in self.ir["data_flows"][0]["components"]}
        self.assertIn("Origen Teradata Renombrado", names)
        self.assertIn("Destino Renombrado", names)

    def test_design_time_properties_reference_new_names_not_old(self):
        with open(self.output_path, encoding="utf-8") as f:
            content = f.read()
        design_time_start = content.index("<DTS:DesignTimeProperties>")
        design_time_section = content[design_time_start:]
        self.assertIn("Origen Teradata Renombrado", design_time_section)
        self.assertIn("Destino Renombrado", design_time_section)
        # El COMPONENTE viejo ("...\Teradata Source" como identidad de nodo)
        # no debe sobrevivir. OJO: "Teradata Source Output" SI puede seguir
        # apareciendo — es una sub-etiqueta de output fija del template, no
        # depende del nombre del componente (ver TEMPLATE_* en
        # campanias_generator.py), asi que no es un resabio del rename.
        self.assertNotIn(r"Tarea Flujo de datos\Teradata Source" + '"', design_time_section)


class SpecValidationFailFastTests(unittest.TestCase):
    """spec_validator debe rechazar, ANTES de tocar el template, cada una de
    las reglas pedidas explicitamente. No se debe escribir ningun archivo
    cuando la validacion falla."""

    def setUp(self):
        self.spec = _load_spec()
        self.tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_failfast_")
        self.output_path = os.path.join(self.tmp_dir, "should_not_be_created.dtsx")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _assert_rejected_and_no_file_written(self, spec):
        with self.assertRaises(SpecValidationError):
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, self.output_path)
        self.assertFalse(os.path.exists(self.output_path))

    def test_source_type_must_be_teradata(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["type"] = "oracle"
        self._assert_rejected_and_no_file_written(spec)

    def test_destination_type_must_be_ole_db(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["destination"]["type"] = "flat_file"
        self._assert_rejected_and_no_file_written(spec)

    def test_unsupported_topology_extra_transformation(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["transformations"].append(
            {"type": "data_conversion", "name": "Extra", "conversions": []}
        )
        self._assert_rejected_and_no_file_written(spec)

    def test_duplicate_source_columns_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["columns"].append(
            dict(spec["data_flow"]["source"]["columns"][0])
        )
        self._assert_rejected_and_no_file_written(spec)

    def test_conversion_input_must_exist(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["transformations"][0]["conversions"][0]["input"] = "NO_EXISTE"
        self._assert_rejected_and_no_file_written(spec)

    def test_conversion_output_collision_with_source_column_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["transformations"][0]["conversions"][0]["output"] = (
            "COD_CAMPANIA_DIRIGIDA"
        )
        self._assert_rejected_and_no_file_written(spec)

    def test_mapping_source_must_exist_in_pipeline(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["destination"]["mappings"][0]["source"] = "NO_EXISTE"
        self._assert_rejected_and_no_file_written(spec)

    def test_mapping_target_data_type_required(self):
        spec = copy.deepcopy(self.spec)
        del spec["data_flow"]["destination"]["mappings"][0]["target_data_type"]
        self._assert_rejected_and_no_file_written(spec)

    def test_wstr_source_column_requires_length(self):
        spec = copy.deepcopy(self.spec)
        del spec["data_flow"]["source"]["columns"][1]["length"]  # DESC_CAMPANIA_DIRIGIDA, wstr
        self._assert_rejected_and_no_file_written(spec)

    def test_str_source_column_requires_code_page(self):
        spec = copy.deepcopy(self.spec)
        del spec["data_flow"]["source"]["columns"][3]["code_page"]  # FEC_INICIO_TXT, str
        self._assert_rejected_and_no_file_written(spec)

    def test_wstr_mapping_target_requires_target_length(self):
        spec = copy.deepcopy(self.spec)
        del spec["data_flow"]["destination"]["mappings"][2]["target_length"]  # Speech, wstr
        self._assert_rejected_and_no_file_written(spec)

    def test_unknown_connection_manager_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["connection"] = "cnxNoExisteEnElTemplate"
        self._assert_rejected_and_no_file_written(spec)

    def test_empty_functional_names_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["package"]["name"] = "   "
        self._assert_rejected_and_no_file_written(spec)


if __name__ == "__main__":
    unittest.main()
