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
- template-teradata-to-sql-v1: que Data Conversion sea OPCIONAL -- Campanias
  (Caso A, con conversion) sigue funcionando igual, y un spec sintetico sin
  transformaciones (Caso B) genera Source -> Destination directo, sin dejar
  componentes/paths/layout huerfanos.
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
from generator.spec_validator import SpecValidationError, validate_spec
from project_context.context_builder import build_project_context

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "campanias_base.dtsx")
SPEC_PATH = os.path.join(REPO_ROOT, "specs", "campanias_generated.json")
SYNTHETIC_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "synthetic_direct_mapping.json")
SYNTHETIC_STRING_NUMERIC_SPEC_PATH = os.path.join(
    REPO_ROOT, "specs", "synthetic_string_and_numeric_conversion.json"
)
FIXTURES_DIR = os.path.join(REPO_ROOT, "Examples", "Originals", "BipSuc_CampaniasVIgentes")
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


def _load_synthetic_spec():
    with open(SYNTHETIC_SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_synthetic_string_numeric_spec():
    with open(SYNTHETIC_STRING_NUMERIC_SPEC_PATH, encoding="utf-8") as f:
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


class OptionalDataConversionTests(unittest.TestCase):
    """
    template-teradata-to-sql-v1: Data Conversion pasa a ser OPCIONAL.

    Caso A (Campanias, con conversion) se re-verifica acá con foco
    estructural (3 componentes/2 paths); Caso B (spec sintetico,
    specs/synthetic_direct_mapping.json) prueba la topologia directa
    Source -> Destination sin ningun componente/path/layout huerfano.
    """

    @classmethod
    def setUpClass(cls):
        cls.campanias_spec = _load_spec()
        cls.synthetic_spec = _load_synthetic_spec()
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_optional_conversion_")

        cls.campanias_output = os.path.join(cls.tmp_dir, "CaseA_Campanias.dtsx")
        generate(cls.campanias_spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.campanias_output)
        cls.campanias_ir = ssis_parser.parse_file(cls.campanias_output)
        cls.campanias_validation = ssis_validator.validate_ir(cls.campanias_ir)
        cls.campanias_df = cls.campanias_ir["data_flows"][0]

        cls.synthetic_output = os.path.join(cls.tmp_dir, "CaseB_Synthetic.dtsx")
        generate(cls.synthetic_spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.synthetic_output)
        cls.synthetic_ir = ssis_parser.parse_file(cls.synthetic_output)
        cls.synthetic_validation = ssis_validator.validate_ir(cls.synthetic_ir)
        cls.synthetic_df = cls.synthetic_ir["data_flows"][0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    # -- spec_validator: 0 o 1 transformations --------------------------
    def test_zero_transformations_is_a_valid_spec(self):
        self.assertEqual(validate_spec(self.synthetic_spec, PROJECT_CONTEXT), [])

    def test_one_data_conversion_is_still_a_valid_spec(self):
        self.assertEqual(validate_spec(self.campanias_spec, PROJECT_CONTEXT), [])

    def test_more_than_one_transformation_is_rejected(self):
        spec = copy.deepcopy(self.campanias_spec)
        spec["data_flow"]["transformations"].append(
            {"type": "data_conversion", "name": "Segunda conversion", "conversions": []}
        )
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(errors)
        self.assertTrue(any("maximo 1 elemento" in e for e in errors))

    def test_missing_transformations_key_is_equivalent_to_empty_list(self):
        spec = copy.deepcopy(self.synthetic_spec)
        del spec["data_flow"]["transformations"]
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])

    # -- Caso A: con Data Conversion (Campanias) -------------------------
    def test_case_a_has_three_components_and_two_paths(self):
        self.assertEqual(len(self.campanias_df["components"]), 3)
        self.assertEqual(
            {c["type"] for c in self.campanias_df["components"]},
            {"teradata_source", "data_conversion", "ole_db_destination"},
        )
        self.assertEqual(len(self.campanias_df["paths"]), 2)

    def test_case_a_validates_without_errors(self):
        self.assertTrue(self.campanias_validation["valid"])
        self.assertEqual(self.campanias_validation["errors"], [])

    # -- Caso B: sin Data Conversion (sintetico) -------------------------
    def test_case_b_has_two_components_no_data_conversion(self):
        types_found = {c["type"] for c in self.synthetic_df["components"]}
        self.assertEqual(len(self.synthetic_df["components"]), 2)
        self.assertEqual(types_found, {"teradata_source", "ole_db_destination"})
        self.assertNotIn("data_conversion", types_found)

    def test_case_b_has_exactly_one_direct_path(self):
        self.assertEqual(len(self.synthetic_df["paths"]), 1)
        path = self.synthetic_df["paths"][0]
        self.assertEqual(path["from"], self.synthetic_spec["data_flow"]["source"]["name"])
        self.assertEqual(path["to"], self.synthetic_spec["data_flow"]["destination"]["name"])

    def test_case_b_mappings_resolve_lineage_directly_from_source(self):
        dest = next(
            c for c in self.synthetic_df["components"] if c["type"] == "ole_db_destination"
        )
        self.assertEqual(len(dest["mappings"]), 2)
        for mapping in dest["mappings"]:
            with self.subTest(column=mapping["pipeline_input_column"]):
                self.assertIsNotNone(mapping["source_column"])
                self.assertEqual(
                    mapping["source_component"],
                    self.synthetic_spec["data_flow"]["source"]["name"],
                )

    def test_case_b_no_orphaned_data_conversion_component_in_raw_xml(self):
        with open(self.synthetic_output, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("Microsoft.DataConvert", content)

    def test_case_b_design_time_properties_has_no_dangling_conversion_refs(self):
        with open(self.synthetic_output, encoding="utf-8") as f:
            content = f.read()
        design_time_start = content.index("<DTS:DesignTimeProperties>")
        design_time_section = content[design_time_start:]
        source_name = self.synthetic_spec["data_flow"]["source"]["name"]
        destination_name = self.synthetic_spec["data_flow"]["destination"]["name"]
        # No debe quedar NINGUNA referencia al componente eliminado, ni al
        # nombre que tenia en el template original.
        self.assertNotIn("Conversión de datos", design_time_section)
        self.assertNotIn("Data Conversion", design_time_section)
        # Los 2 componentes que SI existen deben estar presentes en el layout.
        self.assertIn(source_name, design_time_section)
        self.assertIn(destination_name, design_time_section)

    def test_case_b_validates_without_errors(self):
        self.assertTrue(self.synthetic_validation["valid"])
        self.assertEqual(self.synthetic_validation["errors"], [])

    def test_case_b_output_path_not_written_before_validation_would_fail(self):
        # Sanity check inverso: un spec sintetico INVALIDO (mapping a una
        # columna inexistente) sigue fallando fail-fast igual que antes,
        # tambien en la topologia sin conversion.
        spec = copy.deepcopy(self.synthetic_spec)
        spec["data_flow"]["destination"]["mappings"][0]["source"] = "NO_EXISTE"
        output_path = os.path.join(self.tmp_dir, "should_not_exist_case_b.dtsx")
        with self.assertRaises(SpecValidationError):
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
        self.assertFalse(os.path.exists(output_path))


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


class ConversionMetadataTests(unittest.TestCase):
    """
    Incremento posterior a template-teradata-to-sql-v1: cierra el gap
    confirmado contra PagosYRecaudaciones_FacturacionComi (ver
    docs/template_teradata_to_sql_v1_generalization_pagosyrecaudaciones.md,
    seccion 6) -- antes de esto, _build_data_conversion() descartaba
    incondicionalmente length/code_page/precision/scale de cualquier
    conversion, sin importar target_type. Ahora conversions[] soporta
    target_length/target_code_page (str/wstr) y target_precision/
    target_scale (numeric), y esa metadata se propaga correctamente a
    pipeline_columns y de ahi al OLE DB Destination
    (cachedLength/cachedCodepage/cachedPrecision/cachedScale). El mismo
    soporte se agrego, simetricamente, a source.columns[] y
    destination.mappings[] para el tipo numeric.

    NO genera el paquete completo PagosYRecaudaciones (sin Script Task,
    variables de paquete, PropertyExpression ni Control Flow, y sin las
    conexiones/tabla reales de ese proyecto) -- solo prueba que el spec
    puede EXPRESAR sus 6 conversiones str->wstr y sus 2 columnas numeric
    con precision/scale (ver specs/synthetic_string_and_numeric_conversion.json).
    """

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_synthetic_string_numeric_spec()
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_conversion_metadata_")
        cls.output_path = os.path.join(cls.tmp_dir, "SyntheticStringAndNumeric.dtsx")
        generate(cls.spec, PROJECT_CONTEXT, TEMPLATE_PATH, cls.output_path)
        cls.tree = ET.parse(cls.output_path)
        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _component(self, class_id):
        for comp in self.tree.getroot().iter("component"):
            if comp.get("componentClassID") == class_id:
                return comp
        return None

    def _conversion_output_column(self, name):
        comp = self._component("Microsoft.DataConvert")
        for col in comp.iter("outputColumn"):
            if col.get("name") == name:
                return col
        return None

    def _destination_input_column(self, cached_name):
        comp = self._component("Microsoft.OLEDBDestination")
        for col in comp.iter("inputColumn"):
            if col.get("cachedName") == cached_name:
                return col
        return None

    def _destination_external_metadata_column(self, name):
        comp = self._component("Microsoft.OLEDBDestination")
        for col in comp.iter("externalMetadataColumn"):
            if col.get("name") == name:
                return col
        return None

    def _destination_property(self, name):
        comp = self._component("Microsoft.OLEDBDestination")
        for prop in comp.find("properties").findall("property"):
            if prop.get("name") == name:
                return prop.text
        return None

    # -- expresividad del spec: los 8 puntos de PagosYRecaudaciones --------
    def test_spec_expresses_six_str_to_wstr_conversions_with_length(self):
        conversions = self.spec["data_flow"]["transformations"][0]["conversions"]
        self.assertEqual(len(conversions), 6)
        for conv in conversions:
            with self.subTest(output=conv["output"]):
                self.assertEqual(conv["target_type"], "wstr")
                self.assertIsInstance(conv["target_length"], int)
                self.assertGreater(conv["target_length"], 0)

    def test_spec_expresses_mto_evento_numeric_15_2(self):
        mapping = next(
            m
            for m in self.spec["data_flow"]["destination"]["mappings"]
            if m["source"] == "Mto_Evento"
        )
        self.assertEqual(mapping["target_data_type"], "numeric")
        self.assertEqual(mapping["target_precision"], 15)
        self.assertEqual(mapping["target_scale"], 2)

    def test_spec_expresses_num_cambio_numeric_9_5(self):
        mapping = next(
            m
            for m in self.spec["data_flow"]["destination"]["mappings"]
            if m["source"] == "Num_Cambio"
        )
        self.assertEqual(mapping["target_data_type"], "numeric")
        self.assertEqual(mapping["target_precision"], 9)
        self.assertEqual(mapping["target_scale"], 5)

    def test_spec_itself_is_valid(self):
        self.assertEqual(validate_spec(self.spec, PROJECT_CONTEXT), [])

    def test_ir_validates_without_errors(self):
        self.assertTrue(self.validation["valid"])
        self.assertEqual(self.validation["errors"], [])

    # -- generacion XML: length en el outputColumn de Data Conversion ------
    def test_conversion_output_columns_have_length(self):
        expected_lengths = {
            "Id_Evento_Sal": 20,
            "Cod_Identif_Tributaria_Sal": 10,
            "Num_Identif_Tributaria_Sal": 20,
            "Desc_Tipo_Impuesto_Sal": 255,
            "Desc_Movimiento_Trx_Sal": 100,
            "Cod_Moneda_Sal": 3,
        }
        for name, length in expected_lengths.items():
            with self.subTest(column=name):
                col = self._conversion_output_column(name)
                self.assertIsNotNone(col)
                self.assertEqual(col.get("dataType"), "wstr")
                self.assertEqual(col.get("length"), str(length))

    # -- propagacion Data Conversion -> pipeline_columns -> Destino --------
    def test_destination_input_columns_have_cached_length_from_conversion(self):
        expected_lengths = {
            "Id_Evento_Sal": 20,
            "Cod_Identif_Tributaria_Sal": 10,
            "Num_Identif_Tributaria_Sal": 20,
            "Desc_Tipo_Impuesto_Sal": 255,
            "Desc_Movimiento_Trx_Sal": 100,
            "Cod_Moneda_Sal": 3,
        }
        for name, length in expected_lengths.items():
            with self.subTest(column=name):
                col = self._destination_input_column(name)
                self.assertIsNotNone(col)
                self.assertEqual(col.get("cachedDataType"), "wstr")
                self.assertEqual(col.get("cachedLength"), str(length))

    def test_str_conversion_output_propagates_cached_code_page_to_destination(self):
        # Variante puntual: uno de los 6 pares pasa a target_type='str' (en
        # vez de 'wstr') para probar la propagacion de cachedCodepage, que
        # el spec sintetico principal no ejercita (sus 6 conversiones son
        # todas wstr, igual que las reales de PagosYRecaudaciones).
        spec = copy.deepcopy(self.spec)
        conv = spec["data_flow"]["transformations"][0]["conversions"][0]  # Id_Evento_Sal
        conv["target_type"] = "str"
        conv["target_code_page"] = 1252
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])

        tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_str_conversion_")
        try:
            output_path = os.path.join(tmp_dir, "StrConversion.dtsx")
            generate(spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
            tree = ET.parse(output_path)
            dest = next(
                c
                for c in tree.getroot().iter("component")
                if c.get("componentClassID") == "Microsoft.OLEDBDestination"
            )
            col = next(
                c for c in dest.iter("inputColumn") if c.get("cachedName") == "Id_Evento_Sal"
            )
            self.assertEqual(col.get("cachedDataType"), "str")
            self.assertEqual(col.get("cachedCodepage"), "1252")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -- numeric: source -> pipeline_columns -> Destino (sin Data Conversion) --
    def test_destination_input_columns_have_cached_precision_and_scale(self):
        cases = {"Mto_Evento": (15, 2), "Num_Cambio": (9, 5)}
        for name, (precision, scale) in cases.items():
            with self.subTest(column=name):
                col = self._destination_input_column(name)
                self.assertIsNotNone(col)
                self.assertEqual(col.get("cachedDataType"), "numeric")
                self.assertEqual(col.get("cachedPrecision"), str(precision))
                self.assertEqual(col.get("cachedScale"), str(scale))

    def test_destination_external_metadata_columns_have_precision_and_scale(self):
        cases = {"Mto_Evento": (15, 2), "Num_Cambio": (9, 5)}
        for name, (precision, scale) in cases.items():
            with self.subTest(column=name):
                col = self._destination_external_metadata_column(name)
                self.assertIsNotNone(col)
                self.assertEqual(col.get("dataType"), "numeric")
                self.assertEqual(col.get("precision"), str(precision))
                self.assertEqual(col.get("scale"), str(scale))

    # -- OLE DB Destination.AccessMode (gap confirmado contra PagosYRecaudaciones) --
    def test_synthetic_spec_declares_access_mode_3(self):
        self.assertEqual(self.spec["data_flow"]["destination"]["access_mode"], 3)

    def test_generated_destination_has_access_mode_3(self):
        self.assertEqual(self._destination_property("AccessMode"), "3")


class ConversionAndMappingValidationTests(unittest.TestCase):
    """spec_validator: reglas fail-fast para target_length/target_code_page
    (str/wstr) y target_precision/target_scale (numeric) en conversions[],
    y su equivalente simetrico en source.columns[] y destination.mappings[].
    Usa specs/synthetic_string_and_numeric_conversion.json como base (ya
    cubre los 8 puntos de PagosYRecaudaciones) y muta copias puntuales."""

    def setUp(self):
        self.spec = _load_synthetic_string_numeric_spec()

    # -- conversions[]: str / wstr ---------------------------------------
    def test_wstr_conversion_with_length_is_accepted(self):
        self.assertEqual(validate_spec(self.spec, PROJECT_CONTEXT), [])

    def test_wstr_conversion_without_target_length_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        del spec["data_flow"]["transformations"][0]["conversions"][0]["target_length"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_length" in e for e in errors))

    def test_str_conversion_with_length_and_code_page_is_accepted(self):
        spec = copy.deepcopy(self.spec)
        conv = spec["data_flow"]["transformations"][0]["conversions"][0]
        conv["target_type"] = "str"
        conv["target_code_page"] = 1252
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])

    def test_str_conversion_without_target_length_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        conv = spec["data_flow"]["transformations"][0]["conversions"][0]
        conv["target_type"] = "str"
        conv["target_code_page"] = 1252
        del conv["target_length"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_length" in e for e in errors))

    def test_str_conversion_without_target_code_page_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        conv = spec["data_flow"]["transformations"][0]["conversions"][0]
        conv["target_type"] = "str"
        # target_length ya esta presente (heredado del wstr original);
        # falta unicamente target_code_page.
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_code_page" in e for e in errors))

    # -- conversions[]: numeric -------------------------------------------
    def test_numeric_conversion_target_with_precision_and_scale_is_accepted(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["columns"].append(
            {"name": "Extra_Numeric_Origen", "data_type": "str", "length": 12, "code_page": 1252}
        )
        spec["data_flow"]["transformations"][0]["conversions"].append(
            {
                "input": "Extra_Numeric_Origen",
                "output": "Extra_Numeric_Sal",
                "target_type": "numeric",
                "target_precision": 18,
                "target_scale": 4,
            }
        )
        spec["data_flow"]["destination"]["mappings"].append(
            {
                "source": "Extra_Numeric_Sal",
                "target": "ExtraNumeric",
                "target_data_type": "numeric",
                "target_precision": 18,
                "target_scale": 4,
            }
        )
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])

    def test_numeric_conversion_target_missing_precision_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["columns"].append(
            {"name": "Extra_Numeric_Origen", "data_type": "str", "length": 12, "code_page": 1252}
        )
        spec["data_flow"]["transformations"][0]["conversions"].append(
            {
                "input": "Extra_Numeric_Origen",
                "output": "Extra_Numeric_Sal",
                "target_type": "numeric",
                "target_scale": 4,
            }
        )
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_precision" in e for e in errors))

    def test_numeric_conversion_target_missing_scale_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["data_flow"]["source"]["columns"].append(
            {"name": "Extra_Numeric_Origen", "data_type": "str", "length": 12, "code_page": 1252}
        )
        spec["data_flow"]["transformations"][0]["conversions"].append(
            {
                "input": "Extra_Numeric_Origen",
                "output": "Extra_Numeric_Sal",
                "target_type": "numeric",
                "target_precision": 18,
            }
        )
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_scale" in e for e in errors))

    # -- source.columns[]: numeric ----------------------------------------
    def test_numeric_source_column_with_precision_and_scale_is_accepted(self):
        # Mto_Evento y Num_Cambio ya estan en el spec sintetico base.
        self.assertEqual(validate_spec(self.spec, PROJECT_CONTEXT), [])

    def test_numeric_source_column_missing_precision_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        col = next(
            c for c in spec["data_flow"]["source"]["columns"] if c["name"] == "Mto_Evento"
        )
        del col["precision"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("'precision'" in e for e in errors))

    def test_numeric_source_column_missing_scale_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        col = next(
            c for c in spec["data_flow"]["source"]["columns"] if c["name"] == "Num_Cambio"
        )
        del col["scale"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("'scale'" in e for e in errors))

    # -- destination.mappings[]: numeric -----------------------------------
    def test_numeric_destination_mapping_with_precision_and_scale_is_accepted(self):
        # Mto_Evento y Num_Cambio ya estan mapeados en el spec sintetico base.
        self.assertEqual(validate_spec(self.spec, PROJECT_CONTEXT), [])

    def test_numeric_destination_mapping_missing_precision_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        mapping = next(
            m
            for m in spec["data_flow"]["destination"]["mappings"]
            if m["source"] == "Mto_Evento"
        )
        del mapping["target_precision"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_precision" in e for e in errors))

    def test_numeric_destination_mapping_missing_scale_is_rejected(self):
        spec = copy.deepcopy(self.spec)
        mapping = next(
            m
            for m in spec["data_flow"]["destination"]["mappings"]
            if m["source"] == "Num_Cambio"
        )
        del mapping["target_scale"]
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("target_scale" in e for e in errors))


class AccessModeTests(unittest.TestCase):
    """
    Gap confirmado de OLE DB Destination.AccessMode (Campanias real=0,
    PagosYRecaudaciones real=3, template=0, generator no lo sobrescribia).
    'access_mode' es OPCIONAL en data_flow.destination: ausente preserva el
    valor del template; presente sobrescribe la property "AccessMode". Solo
    se valida el TIPO (entero >= 0), no un enum cerrado -- ver
    generator/spec_validator.py.
    """

    def setUp(self):
        self.campanias_spec = _load_spec()
        self.string_numeric_spec = _load_synthetic_string_numeric_spec()

    # -- Campanias: sin access_mode en el spec -> se preserva el del template --
    def test_campanias_spec_does_not_declare_access_mode(self):
        self.assertNotIn("access_mode", self.campanias_spec["data_flow"]["destination"])

    def test_campanias_generated_preserves_access_mode_0_from_template(self):
        tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_access_mode_campanias_")
        try:
            output_path = os.path.join(tmp_dir, "CampaniasAccessMode.dtsx")
            generate(self.campanias_spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
            tree = ET.parse(output_path)
            dest = next(
                c
                for c in tree.getroot().iter("component")
                if c.get("componentClassID") == "Microsoft.OLEDBDestination"
            )
            access_mode = next(
                p.text for p in dest.find("properties").findall("property")
                if p.get("name") == "AccessMode"
            )
            self.assertEqual(access_mode, "0")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -- string/numeric sintetico: access_mode=3 sobrescribe correctamente --
    def test_string_numeric_spec_declares_access_mode_3(self):
        self.assertEqual(
            self.string_numeric_spec["data_flow"]["destination"]["access_mode"], 3
        )

    def test_string_numeric_generated_has_access_mode_3(self):
        tmp_dir = tempfile.mkdtemp(prefix="ssis_generator_access_mode_synthetic_")
        try:
            output_path = os.path.join(tmp_dir, "SyntheticAccessMode.dtsx")
            generate(self.string_numeric_spec, PROJECT_CONTEXT, TEMPLATE_PATH, output_path)
            tree = ET.parse(output_path)
            dest = next(
                c
                for c in tree.getroot().iter("component")
                if c.get("componentClassID") == "Microsoft.OLEDBDestination"
            )
            access_mode = next(
                p.text for p in dest.find("properties").findall("property")
                if p.get("name") == "AccessMode"
            )
            self.assertEqual(access_mode, "3")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -- validator: tipos/valores invalidos rechazados --------------------
    def test_access_mode_as_string_is_rejected(self):
        spec = copy.deepcopy(self.string_numeric_spec)
        spec["data_flow"]["destination"]["access_mode"] = "3"
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("access_mode" in e for e in errors))

    def test_access_mode_negative_is_rejected(self):
        spec = copy.deepcopy(self.string_numeric_spec)
        spec["data_flow"]["destination"]["access_mode"] = -1
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("access_mode" in e for e in errors))

    def test_access_mode_float_is_rejected(self):
        spec = copy.deepcopy(self.string_numeric_spec)
        spec["data_flow"]["destination"]["access_mode"] = 3.0
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("access_mode" in e for e in errors))

    def test_access_mode_bool_is_rejected(self):
        # bool es subclase de int en Python -- debe rechazarse explicitamente,
        # igual criterio que _positive_int/_non_negative_int en el resto del
        # modulo (ver generator/spec_validator.py).
        spec = copy.deepcopy(self.string_numeric_spec)
        spec["data_flow"]["destination"]["access_mode"] = True
        errors = validate_spec(spec, PROJECT_CONTEXT)
        self.assertTrue(any("access_mode" in e for e in errors))

    def test_access_mode_zero_is_accepted(self):
        spec = copy.deepcopy(self.string_numeric_spec)
        spec["data_flow"]["destination"]["access_mode"] = 0
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])

    def test_access_mode_omitted_is_accepted(self):
        spec = copy.deepcopy(self.string_numeric_spec)
        del spec["data_flow"]["destination"]["access_mode"]
        self.assertEqual(validate_spec(spec, PROJECT_CONTEXT), [])


if __name__ == "__main__":
    unittest.main()
