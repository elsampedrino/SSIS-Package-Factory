"""
Gate B de project-generation-v1: demuestra que el generador ESTABLE
TERADATA_TO_SQL v1.1 (generator/campanias_generator.py, sin modificar) puede
consumir un ProjectContext construido a partir de recursos generados por
project-generation-v1 (generated_project_v1/Project.params +
generated_project_v1/cnxTeradataGen.conmgr + cnxSqlGen.conmgr).

Circuito verificado end-to-end:

    ProjectSpec (specs/project_generation_v1_gate.json, ya generado)
        -> Project.params + *.conmgr (generated_project_v1/, ya en disco)
        -> build_project_context() [project_context/, SIN modificar]
        -> Process Spec (specs/project_generation_v1_e2e.json)
        -> generate() [generator/campanias_generator.py, SIN modificar]
        -> .dtsx generado
        -> ssis_parser + ssis_validator [SIN modificar]

FIXTURE_DTPROJ_XML es UNICAMENTE un fixture de test (igual criterio que
tests/test_project_generation.py::RoundTripTests): build_project_context()
resuelve Connection Managers por nombre de archivo DECLARADO en un .dtproj,
nunca por contenido de disco, y project-generation-v1 no genera ni muta
ningun .dtproj real. El fixture declara los mismos dos archivos .conmgr que
ya existen en generated_project_v1/ (generados en el turno anterior) --
no es una capacidad nueva del producto.

No se modifica generator/, project_context/, ni nada de Control Flow.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import ssis_parser
import ssis_validator
from generator.campanias_generator import generate
from generator.spec_validator import validate_spec
from generator.xml_helpers import format_connection_manager_id
from project_context.context_builder import build_project_context
from project_context.validator import validate_project_context

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "campanias_base.dtsx")
GENERATED_RESOURCES_DIR = os.path.join(REPO_ROOT, "generated_project_v1")
PARAMS_PATH = os.path.join(GENERATED_RESOURCES_DIR, "Project.params")
E2E_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "project_generation_v1_e2e.json")

FORBIDDEN_SECRET_MARKERS = (
    "Salt=",
    "IV=",
    "Algorithm=",
    "<DTS:Password",
    "<TeraPassword",
    "aes256-cbc",
    "Password=",
)

# Fixture de TEST unicamente -- ver docstring del modulo. Declara los mismos
# dos .conmgr ya presentes en generated_project_v1/ (cnxTeradataGen,
# cnxSqlGen). No representa generacion/mutacion real de .dtproj.
FIXTURE_DTPROJ_XML = """<?xml version="1.0" encoding="utf-8"?>
<Project xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <DeploymentModel>Project</DeploymentModel>
  <Configurations>
    <Configuration>
      <Options>
        <TargetServerVersion>SQLServer2022</TargetServerVersion>
      </Options>
    </Configuration>
  </Configurations>
  <DeploymentModelSpecificContent>
    <Manifest>
      <SSIS:Project SSIS:ProtectionLevel="EncryptSensitiveWithUserKey" xmlns:SSIS="www.microsoft.com/SqlServer/SSIS">
        <SSIS:Properties>
          <SSIS:Property SSIS:Name="Name">ProjectGenerationV1Fixture</SSIS:Property>
        </SSIS:Properties>
        <SSIS:Packages />
        <SSIS:ConnectionManagers>
          <SSIS:ConnectionManager SSIS:Name="cnxTeradataGen.conmgr" />
          <SSIS:ConnectionManager SSIS:Name="cnxSqlGen.conmgr" />
        </SSIS:ConnectionManagers>
      </SSIS:Project>
    </Manifest>
  </DeploymentModelSpecificContent>
</Project>"""


def _load_e2e_spec():
    with open(E2E_SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


class ProjectGenerationV1GateBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. ProjectContext a partir de los recursos YA generados por
        # project-generation-v1 (generated_project_v1/) + un .dtproj FIXTURE
        # DE TEST que los declara.
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_project_generation_e2e_")
        dtproj_path = os.path.join(cls.tmp_dir, "ProjectGenerationV1Fixture.dtproj")
        with open(dtproj_path, "w", encoding="utf-8") as f:
            f.write(FIXTURE_DTPROJ_XML)

        cls.project_context = build_project_context(
            dtproj_path, PARAMS_PATH, GENERATED_RESOURCES_DIR
        )
        cls.context_validation = validate_project_context(cls.project_context)

        # 2. Process Spec minimo (TERADATA_TO_SQL v1.1, sin Data Conversion)
        # usando cnxTeradataGen/cnxSqlGen por nombre.
        cls.spec = _load_e2e_spec()

        # 3. Generar el .dtsx con el generador ESTABLE, sin modificar.
        cls.output_path = os.path.join(cls.tmp_dir, "ProjectGenerationV1_E2E.dtsx")
        generate(cls.spec, cls.project_context, TEMPLATE_PATH, cls.output_path)

        with open(cls.output_path, encoding="utf-8") as f:
            cls.raw_xml = f.read()

        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.components_by_type = {c["type"]: c for c in cls.data_flow["components"]}

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    # -- ProjectContext (paso 3 de la tarea) ---------------------------------

    def test_project_context_includes_both_generated_connections(self):
        self.assertIn("cnxTeradataGen", self.project_context["connections"])
        self.assertIn("cnxSqlGen", self.project_context["connections"])
        self.assertEqual(self.project_context["missing_conmgr_files"], [])

    def test_project_context_includes_generated_parameters(self):
        names = {p["name"] for p in self.project_context["parameters"]}
        self.assertEqual(
            names, {"dbTeradataGen", "srvTeradataGen", "pwTeradataGen", "pwSqlGen"}
        )

    def test_project_context_validator_is_green(self):
        self.assertTrue(self.context_validation["valid"])
        self.assertEqual(self.context_validation["errors"], [])

    # -- Process Spec / spec_validator ---------------------------------------

    def test_e2e_spec_is_valid_against_project_context(self):
        self.assertEqual(validate_spec(self.spec, self.project_context), [])

    # -- parser / validator del .dtsx generado -------------------------------

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_package_name_matches_spec(self):
        self.assertEqual(self.ir["package_name"], "ProjectGenerationV1_E2E")

    def test_data_flow_complete_two_components_no_conversion(self):
        self.assertEqual(len(self.ir["data_flows"]), 1)
        self.assertEqual(len(self.data_flow["components"]), 2)
        self.assertEqual(
            {c["type"] for c in self.data_flow["components"]},
            {"teradata_source", "ole_db_destination"},
        )
        self.assertEqual(len(self.data_flow["paths"]), 1)

    # -- resolucion de conexiones (paso 6 de la tarea) -----------------------

    def test_source_connection_resolves_to_cnxTeradataGen(self):
        source = self.components_by_type["teradata_source"]
        self.assertEqual(source["connection"], "Project.ConnectionManagers[cnxTeradataGen]")

    def test_destination_connection_resolves_to_cnxSqlGen(self):
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(dest["connection"], "Project.ConnectionManagers[cnxSqlGen]")

    def test_dtsids_match_the_generated_conmgr_files(self):
        expected_teradata_dtsid = self.project_context["connections"]["cnxTeradataGen"]["dtsid"]
        expected_sql_dtsid = self.project_context["connections"]["cnxSqlGen"]["dtsid"]

        source = self.components_by_type["teradata_source"]
        dest = self.components_by_type["ole_db_destination"]

        self.assertEqual(
            source["connections"][0]["connection_manager_id"],
            format_connection_manager_id(expected_teradata_dtsid),
        )
        self.assertEqual(
            dest["connections"][0]["connection_manager_id"],
            format_connection_manager_id(expected_sql_dtsid),
        )

    def test_mappings_resolve_correctly(self):
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

    def test_destination_table_matches_spec(self):
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(dest["properties"]["open_rowset"], "[staging].[Factory_Test]")

    # -- seguridad ------------------------------------------------------------

    def test_no_secret_markers_in_generated_dtsx(self):
        for marker in FORBIDDEN_SECRET_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.raw_xml)


if __name__ == "__main__":
    unittest.main()
