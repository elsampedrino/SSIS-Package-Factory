"""
Tests de teradata-to-sql-profile-v1: capa opcional de defaults corporativos
(profiles/) que resuelve un MinimalSpec a ProjectSpec/ProcessSpec, consumidos
SIN CAMBIOS por project_generator.project_generator y
generator.campanias_generator.

No se toca Control Flow: los cambios backward-compatible en
generator/spec_validator.py y generator/campanias_generator.py se verifican
explicitamente contra la suite completa de tests.test_control_flow (ver
BackwardCompatibilityTests) y contra la suite de test_generator_campanias
(regresion completa del repo).

Cubre:
 - MinimalSpec: validos, cada campo requerido/opcional, overrides.
 - Resolucion del profile: defaults TERADATA/SQL, ambiente QA/Produ,
   PropertyExpressions TERADATA (4) y SQL (2), protection_level,
   min/max sessions.
 - Seguridad: ningun valor de password, ningun Salt/IV/Algorithm/
   PasswordVerifier, shells sensibles sin 'value'.
 - Package defaults / Teradata Source defaults reflejados en el XML real
   generado (ProtectionLevel=2, MinSessions=4, MaxSessions=8) con y sin
   override.
 - Backward compatibility: specs legacy existentes (sin protection_level/
   min_sessions/max_sessions) siguen validando y generando exactamente
   igual que antes (valores heredados del template, sin tocar).
 - E2E: MinimalSpec -> profile -> project_generator -> ProjectContext ->
   campanias_generator -> ssis_parser/ssis_validator.
"""

from __future__ import annotations

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
from generator.spec_validator import validate_spec
from project_context.context_builder import build_project_context
from project_context.validator import validate_project_context
from project_generator.project_generator import generate_project_resources
from profiles.minimal_spec_schema import (
    MinimalSpecValidationError,
    assert_valid_minimal_spec,
    validate_minimal_spec,
)
from profiles.teradata_to_sql_profile import (
    CORPORATE_MAX_SESSIONS,
    CORPORATE_MIN_SESSIONS,
    CORPORATE_PROTECTION_LEVEL,
    CORPORATE_SQL_USER,
    CORPORATE_TERADATA_DATABASE,
    CORPORATE_TERADATA_SERVER,
    CORPORATE_TERADATA_USER,
    resolve_teradata_to_sql_profile,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "campanias_base.dtsx")
LEGACY_SPEC_PATH = os.path.join(REPO_ROOT, "specs", "campanias_generated.json")
LEGACY_FIXTURES_DIR = os.path.join(
    REPO_ROOT, "Examples", "Originals", "BipSuc_CampaniasVIgentes"
)

FORBIDDEN_SECRET_MARKERS = (
    "Salt=",
    "IV=",
    "Algorithm=",
    "<DTS:Password",
    "<TeraPassword",
    "PasswordVerifier",
    "aes256-cbc",
)


def _minimal_process():
    return {
        "package_name": "ProfileTestPkg",
        "data_flow_name": "Flujo Profile",
        "source_name": "Origen Teradata",
        "sql": "SELECT Id_Test, Desc_Test FROM D_TEST.Factory_Test",
        "columns": [
            {"name": "Id_Test", "data_type": "i4"},
            {"name": "Desc_Test", "data_type": "wstr", "length": 100},
        ],
        "destination_name": "Destino SQL",
        "table": "[staging].[Factory_Test]",
        "mappings": [
            {"source": "Id_Test", "target": "Id_Test", "target_data_type": "i4"},
            {
                "source": "Desc_Test",
                "target": "Desc_Test",
                "target_data_type": "wstr",
                "target_length": 100,
            },
        ],
    }


def _minimal_spec(**overrides):
    spec = {
        "profile": "teradata_to_sql",
        "environment": "QA",
        "teradata": {"charset": "UTF8"},
        "sql": {
            "server": "SRVTEST",
            "catalog": "TestCatalog",
        },
        "process": _minimal_process(),
    }
    spec.update(overrides)
    return spec


class MinimalSpecSchemaTests(unittest.TestCase):
    def test_valid_minimal_spec_has_no_errors(self):
        self.assertEqual(validate_minimal_spec(_minimal_spec()), [])

    def test_wrong_profile_name_rejected(self):
        spec = _minimal_spec(profile="otra_cosa")
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("'profile'" in e for e in errors))

    def test_missing_environment_rejected(self):
        spec = _minimal_spec()
        del spec["environment"]
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("environment" in e for e in errors))

    def test_invalid_environment_value_rejected(self):
        spec = _minimal_spec(environment="Staging")
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("environment" in e for e in errors))

    def test_environment_qa_and_produ_both_accepted(self):
        for env in ("QA", "Produ"):
            with self.subTest(environment=env):
                spec = _minimal_spec(environment=env)
                self.assertEqual(validate_minimal_spec(spec), [])

    def test_teradata_charset_required(self):
        spec = _minimal_spec(teradata={})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("charset" in e for e in errors))

    def test_teradata_charset_invalid_value_rejected(self):
        spec = _minimal_spec(teradata={"charset": "LATIN1"})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("charset" in e for e in errors))

    def test_teradata_override_empty_string_rejected(self):
        spec = _minimal_spec(teradata={"charset": "UTF8", "server": "  "})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("teradata.server" in e for e in errors))

    def test_sql_server_parameter_name_and_password_parameter_name_are_optional(self):
        # Se derivan siempre de 'sql.server' -- no son obligatorios.
        spec = _minimal_spec()
        self.assertNotIn("server_parameter_name", spec["sql"])
        self.assertNotIn("password_parameter_name", spec["sql"])
        self.assertEqual(validate_minimal_spec(spec), [])

    def test_sql_server_parameter_name_override_must_be_non_empty(self):
        spec = _minimal_spec(sql={"server": "SRVTEST", "catalog": "TestCatalog", "server_parameter_name": "  "})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("server_parameter_name" in e for e in errors))

    def test_sql_password_parameter_name_override_must_be_non_empty(self):
        spec = _minimal_spec(sql={"server": "SRVTEST", "catalog": "TestCatalog", "password_parameter_name": "  "})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("password_parameter_name" in e for e in errors))

    def test_sql_server_with_no_alphanumeric_characters_rejected(self):
        spec = _minimal_spec(sql={"server": "...", "catalog": "TestCatalog"})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("no contiene ningun caracter alfanumerico" in e for e in errors))

    def test_sql_server_required(self):
        spec = _minimal_spec()
        del spec["sql"]["server"]
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("'sql.server'" in e for e in errors))

    def test_sql_catalog_required(self):
        spec = _minimal_spec()
        del spec["sql"]["catalog"]
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("catalog" in e for e in errors))

    def test_teradata_source_invalid_override_rejected(self):
        spec = _minimal_spec(teradata_source={"min_sessions": 0})
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("min_sessions" in e for e in errors))

    def test_protection_level_invalid_override_rejected(self):
        spec = _minimal_spec(protection_level="DontSaveSensitive")
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("protection_level" in e for e in errors))

    def test_process_missing_columns_rejected(self):
        process = _minimal_process()
        process["columns"] = []
        spec = _minimal_spec(process=process)
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("process.columns" in e for e in errors))

    def test_process_missing_mappings_rejected(self):
        process = _minimal_process()
        process["mappings"] = []
        spec = _minimal_spec(process=process)
        errors = validate_minimal_spec(spec)
        self.assertTrue(any("process.mappings" in e for e in errors))

    def test_assert_raises_on_invalid_spec(self):
        with self.assertRaises(MinimalSpecValidationError):
            assert_valid_minimal_spec(_minimal_spec(profile="bogus"))


class ProfileResolutionTests(unittest.TestCase):
    def test_teradata_defaults_applied(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertEqual(params_by_name["srvTeradata"]["value"], CORPORATE_TERADATA_SERVER)
        self.assertEqual(params_by_name["dbTeradata"]["value"], CORPORATE_TERADATA_DATABASE)
        self.assertEqual(params_by_name["usrTeradata"]["value"], CORPORATE_TERADATA_USER)

    def test_teradata_overrides_applied(self):
        spec = _minimal_spec(
            teradata={"charset": "ASCII", "server": "OTRO_SRV", "database": "OTRA_DB", "user": "OTRO_USR"}
        )
        resolved = resolve_teradata_to_sql_profile(spec)
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertEqual(params_by_name["srvTeradata"]["value"], "OTRO_SRV")
        self.assertEqual(params_by_name["dbTeradata"]["value"], "OTRA_DB")
        self.assertEqual(params_by_name["usrTeradata"]["value"], "OTRO_USR")

    def test_pw_teradata_is_sensitive_shell(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertTrue(params_by_name["pwTeradata"]["sensitive"])
        self.assertNotIn("value", params_by_name["pwTeradata"])

    def test_sql_server_parameter_name_derived_from_server(self):
        # Convencion: 'srv' + <NombreServidor> -- ver docs/teradata_to_sql_profile_v1.md.
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertEqual(params_by_name["srvSRVTEST"]["value"], "SRVTEST")
        self.assertFalse(params_by_name["srvSRVTEST"]["sensitive"])

    def test_sql_password_parameter_name_derived_from_server_as_sensitive_shell(self):
        # Convencion: 'pw' + <NombreServidor> -- YA NO 'pwUsSxSSIS' (ver
        # correccion funcional: el password se nombra por el SERVIDOR, no
        # por el usuario fijo).
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertIn("pwSRVTEST", params_by_name)
        self.assertTrue(params_by_name["pwSRVTEST"]["sensitive"])
        self.assertNotIn("value", params_by_name["pwSRVTEST"])
        self.assertNotIn("pwUsSxSSIS", params_by_name)

    def test_sql_parameter_naming_convention_example_from_the_spec(self):
        # Ejemplo exacto pedido: servidor 'SRVBSQADB' -> 'srvSRVBSQADB'/'pwSRVBSQADB'.
        spec = _minimal_spec(sql={"server": "SRVBSQADB", "catalog": "Optimus"})
        resolved = resolve_teradata_to_sql_profile(spec)
        names = {p["name"] for p in resolved["project_spec"]["project"]["parameters"]}
        self.assertIn("srvSRVBSQADB", names)
        self.assertIn("pwSRVBSQADB", names)

    def test_sql_parameter_naming_convention_second_server_not_hardcoded(self):
        # Segundo servidor distinto -- confirma que la derivacion no quedo
        # hardcodeada al servidor usado en el gate.
        spec = _minimal_spec(sql={"server": "SRVDB2016QA", "catalog": "OptimusTurnos"})
        resolved = resolve_teradata_to_sql_profile(spec)
        names = {p["name"] for p in resolved["project_spec"]["project"]["parameters"]}
        self.assertIn("srvSRVDB2016QA", names)
        self.assertIn("pwSRVDB2016QA", names)

    def test_sql_parameter_names_can_still_be_overridden_explicitly(self):
        spec = _minimal_spec(sql={
            "server": "SRVTEST", "catalog": "TestCatalog",
            "server_parameter_name": "srvCustom", "password_parameter_name": "pwCustom",
        })
        resolved = resolve_teradata_to_sql_profile(spec)
        names = {p["name"] for p in resolved["project_spec"]["project"]["parameters"]}
        self.assertIn("srvCustom", names)
        self.assertIn("pwCustom", names)
        self.assertNotIn("srvSRVTEST", names)
        self.assertNotIn("pwSRVTEST", names)

    def test_no_project_parameter_created_for_sql_user(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        names = {p["name"] for p in resolved["project_spec"]["project"]["parameters"]}
        self.assertNotIn("usSxSSIS", names)
        self.assertNotIn("sqlUser", names)
        self.assertNotIn("usrUsSxSSIS", names)

    def test_sql_user_default(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        connections = {c["name"]: c for c in resolved["project_spec"]["project"]["connections"]}
        self.assertEqual(connections["cnxSql"]["user"], CORPORATE_SQL_USER)

    def test_sql_user_override(self):
        spec = _minimal_spec(sql={
            "server": "SRVTEST", "catalog": "TestCatalog", "user": "usOtro",
        })
        resolved = resolve_teradata_to_sql_profile(spec)
        connections = {c["name"]: c for c in resolved["project_spec"]["project"]["connections"]}
        self.assertEqual(connections["cnxSql"]["user"], "usOtro")

    def test_ambiente_qa(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec(environment="QA"))
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertEqual(params_by_name["ambiente"]["value"], "QA")
        self.assertFalse(params_by_name["ambiente"]["sensitive"])

    def test_ambiente_produ(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec(environment="Produ"))
        params_by_name = {
            p["name"]: p for p in resolved["project_spec"]["project"]["parameters"]
        }
        self.assertEqual(params_by_name["ambiente"]["value"], "Produ")

    def test_teradata_property_expressions_wired_correctly(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        connections = {c["name"]: c for c in resolved["project_spec"]["project"]["connections"]}
        exprs = {
            e["property"]: e["parameter"] for e in connections["cnxTeradata"]["property_expressions"]
        }
        self.assertEqual(
            exprs,
            {
                "ServerName": "srvTeradata",
                "Database": "dbTeradata",
                "UserName": "usrTeradata",
                "Password": "pwTeradata",
            },
        )

    def test_sql_property_expressions_wired_correctly(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        connections = {c["name"]: c for c in resolved["project_spec"]["project"]["connections"]}
        exprs = {
            e["property"]: e["parameter"] for e in connections["cnxSql"]["property_expressions"]
        }
        self.assertEqual(exprs, {"ServerName": "srvSRVTEST", "Password": "pwSRVTEST"})

    def test_protection_level_default(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        self.assertEqual(
            resolved["process_spec"]["package"]["protection_level"], CORPORATE_PROTECTION_LEVEL
        )

    def test_protection_level_override(self):
        spec = _minimal_spec(protection_level="EncryptSensitiveWithUserKey")
        resolved = resolve_teradata_to_sql_profile(spec)
        self.assertEqual(
            resolved["process_spec"]["package"]["protection_level"],
            "EncryptSensitiveWithUserKey",
        )

    def test_min_max_sessions_default(self):
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        source = resolved["process_spec"]["data_flow"]["source"]
        self.assertEqual(source["min_sessions"], CORPORATE_MIN_SESSIONS)
        self.assertEqual(source["max_sessions"], CORPORATE_MAX_SESSIONS)

    def test_min_max_sessions_override(self):
        spec = _minimal_spec(teradata_source={"min_sessions": 2, "max_sessions": 16})
        resolved = resolve_teradata_to_sql_profile(spec)
        source = resolved["process_spec"]["data_flow"]["source"]
        self.assertEqual(source["min_sessions"], 2)
        self.assertEqual(source["max_sessions"], 16)

    def test_ternary_initial_catalog_not_generated(self):
        # Fuera de scope explicito de v1 -- ninguna PropertyExpression de
        # cnxSql debe referenciar 'ambiente', ni contener un operador ternario.
        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        connections = {c["name"]: c for c in resolved["project_spec"]["project"]["connections"]}
        for expr in connections["cnxSql"]["property_expressions"]:
            self.assertNotIn("ambiente", expr["parameter"])
        self.assertNotIn("InitialCatalog", [e["property"] for e in connections["cnxSql"]["property_expressions"]])

    def test_resolved_project_spec_passes_project_generator_schema(self):
        from project_generator.spec_schema import validate_project_spec

        resolved = resolve_teradata_to_sql_profile(_minimal_spec())
        self.assertEqual(validate_project_spec(resolved["project_spec"]), [])


class ProfileSecurityTests(unittest.TestCase):
    def setUp(self):
        self.resolved = resolve_teradata_to_sql_profile(_minimal_spec())

    def test_no_forbidden_markers_in_resolved_specs(self):
        serialized = json.dumps(self.resolved)
        for marker in FORBIDDEN_SECRET_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)

    def test_sensitive_parameters_never_have_value_key(self):
        for param in self.resolved["project_spec"]["project"]["parameters"]:
            if param["sensitive"]:
                self.assertNotIn("value", param)

    def test_no_literal_password_string_anywhere(self):
        serialized = json.dumps(self.resolved).lower()
        self.assertNotIn("password123", serialized)
        self.assertNotIn("corporate_password", serialized)

    def test_pw_us_sx_ssis_never_appears_anywhere_in_the_profile(self):
        # Correccion funcional: 'pwUsSxSSIS' dejo de ser el nombre canonico
        # del profile -- el password SQL se nombra por el SERVIDOR, no por
        # el usuario fijo. No debe aparecer en ningun spec resuelto.
        serialized = json.dumps(self.resolved)
        self.assertNotIn("pwUsSxSSIS", serialized)


class PackageAndTeradataSourceDefaultsInXmlTests(unittest.TestCase):
    """Confirma que ProtectionLevel/MinSessions/MaxSessions llegan al XML
    REAL generado (no solo al ProcessSpec intermedio), con y sin override."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_profile_xml_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _generate_and_read(self, minimal_spec, output_name):
        resolved = resolve_teradata_to_sql_profile(minimal_spec)
        output_dir = os.path.join(self.tmp_dir, output_name)
        os.makedirs(output_dir, exist_ok=True)
        gen_result = generate_project_resources(resolved["project_spec"], output_dir)

        dtproj_path = os.path.join(output_dir, "Fixture.dtproj")
        with open(dtproj_path, "w", encoding="utf-8") as f:
            f.write(_FIXTURE_DTPROJ_XML)

        project_context = build_project_context(dtproj_path, gen_result["params_path"], output_dir)
        self.assertEqual(validate_spec(resolved["process_spec"], project_context), [])

        output_path = os.path.join(output_dir, f"{output_name}.dtsx")
        generate(resolved["process_spec"], project_context, TEMPLATE_PATH, output_path)

        with open(output_path, encoding="utf-8") as f:
            return f.read()

    def test_default_protection_level_and_sessions_in_xml(self):
        xml_text = self._generate_and_read(_minimal_spec(), "defaults")
        root = ET.fromstring(xml_text)
        self.assertEqual(root.get("{www.microsoft.com/SqlServer/Dts}ProtectionLevel"), "2")
        self.assertIn('name="MinSessions">4<', xml_text)
        self.assertIn('name="MaxSessions">8<', xml_text)

    def test_overridden_protection_level_and_sessions_in_xml(self):
        spec = _minimal_spec(
            protection_level="EncryptSensitiveWithUserKey",
            teradata_source={"min_sessions": 2, "max_sessions": 16},
        )
        xml_text = self._generate_and_read(spec, "overrides")
        root = ET.fromstring(xml_text)
        self.assertEqual(root.get("{www.microsoft.com/SqlServer/Dts}ProtectionLevel"), "1")
        self.assertIn('name="MinSessions">2<', xml_text)
        self.assertIn('name="MaxSessions">16<', xml_text)

    def test_no_secrets_in_generated_xml(self):
        xml_text = self._generate_and_read(_minimal_spec(), "security")
        for marker in FORBIDDEN_SECRET_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, xml_text)


class BackwardCompatibilityTests(unittest.TestCase):
    """Specs legacy (sin protection_level/min_sessions/max_sessions) deben
    seguir validando y generando EXACTAMENTE igual que antes de este
    milestone: valores heredados del template, sin tocar."""

    @classmethod
    def setUpClass(cls):
        with open(LEGACY_SPEC_PATH, encoding="utf-8") as f:
            cls.legacy_spec = json.load(f)

        dtproj_path = os.path.join(LEGACY_FIXTURES_DIR, "BipSuc.dtproj")
        params_path = os.path.join(LEGACY_FIXTURES_DIR, "Project.params")
        cls.project_context = build_project_context(dtproj_path, params_path, LEGACY_FIXTURES_DIR)

        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_profile_backcompat_")
        cls.output_path = os.path.join(cls.tmp_dir, "CampaniasGenerado.dtsx")
        generate(cls.legacy_spec, cls.project_context, TEMPLATE_PATH, cls.output_path)

        with open(cls.output_path, encoding="utf-8") as f:
            cls.xml_text = f.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_legacy_spec_without_new_optional_fields_still_valid(self):
        self.assertNotIn("protection_level", self.legacy_spec["package"])
        self.assertNotIn("min_sessions", self.legacy_spec["data_flow"]["source"])
        self.assertEqual(validate_spec(self.legacy_spec, self.project_context), [])

    def test_legacy_generation_preserves_template_inherited_protection_level(self):
        # Sin 'protection_level' en el spec, build_package_tree() NO toca el
        # atributo -- debe seguir siendo el heredado del template (2).
        root = ET.fromstring(self.xml_text)
        self.assertEqual(root.get("{www.microsoft.com/SqlServer/Dts}ProtectionLevel"), "2")

    def test_legacy_generation_preserves_template_inherited_sessions(self):
        # Sin 'min_sessions'/'max_sessions' en el spec, _build_teradata_source()
        # no toca esas properties -- deben seguir siendo las del template (4/8).
        self.assertIn('name="MaxSessions">8<', self.xml_text)
        self.assertIn('name="MinSessions">4<', self.xml_text)

    def test_control_flow_suite_unaffected(self):
        # Ejecuta la suite completa de Control Flow desde aca tambien, como
        # doble confirmacion de que los cambios de este milestone (aditivos,
        # solo en el branch LEGACY de validate_spec()) no la afectan.
        #
        # control-flow-v1 (BLOCKED) permanece sin commitear en el repo -- en
        # un checkout que solo tenga teradata-to-sql-profile-v1 (este
        # milestone) tests.test_control_flow no existe todavia. Este test
        # debe entonces OMITIRSE (no fallar): su proposito es "si Control
        # Flow esta presente, confirmar que sigue verde", no "Control Flow
        # debe estar presente".
        import io

        loader = unittest.TestLoader()
        try:
            import tests.test_control_flow as control_flow_tests
        except ModuleNotFoundError:
            self.skipTest(
                "tests/test_control_flow.py no esta presente en este checkout "
                "(control-flow-v1 sigue BLOCKED/sin commitear) -- nada que verificar."
            )

        suite = loader.loadTestsFromModule(control_flow_tests)
        result = unittest.TextTestRunner(verbosity=0, stream=io.StringIO()).run(suite)
        self.assertTrue(result.wasSuccessful())
        self.assertGreater(result.testsRun, 0)


_FIXTURE_DTPROJ_XML = """<?xml version="1.0" encoding="utf-8"?>
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
      <SSIS:Project SSIS:ProtectionLevel="EncryptSensitiveWithPassword" xmlns:SSIS="www.microsoft.com/SqlServer/SSIS">
        <SSIS:Properties>
          <SSIS:Property SSIS:Name="Name">TeradataToSqlProfileFixture</SSIS:Property>
        </SSIS:Properties>
        <SSIS:Packages />
        <SSIS:ConnectionManagers>
          <SSIS:ConnectionManager SSIS:Name="cnxTeradata.conmgr" />
          <SSIS:ConnectionManager SSIS:Name="cnxSql.conmgr" />
        </SSIS:ConnectionManagers>
      </SSIS:Project>
    </Manifest>
  </DeploymentModelSpecificContent>
</Project>"""


class ProfileE2ETests(unittest.TestCase):
    """MinimalSpec -> profile -> project_generator -> ProjectContext ->
    campanias_generator -> ssis_parser/ssis_validator."""

    @classmethod
    def setUpClass(cls):
        cls.minimal_spec = _minimal_spec(
            environment="Produ",
            teradata={"charset": "ASCII"},
        )
        cls.resolved = resolve_teradata_to_sql_profile(cls.minimal_spec)

        cls.tmp_dir = tempfile.mkdtemp(prefix="ssis_profile_e2e_")
        cls.gen_result = generate_project_resources(cls.resolved["project_spec"], cls.tmp_dir)

        cls.dtproj_path = os.path.join(cls.tmp_dir, "Fixture.dtproj")
        with open(cls.dtproj_path, "w", encoding="utf-8") as f:
            f.write(_FIXTURE_DTPROJ_XML)

        cls.project_context = build_project_context(
            cls.dtproj_path, cls.gen_result["params_path"], cls.tmp_dir
        )
        cls.context_validation = validate_project_context(cls.project_context)

        cls.output_path = os.path.join(cls.tmp_dir, "ProfileE2EPkg.dtsx")
        generate(cls.resolved["process_spec"], cls.project_context, TEMPLATE_PATH, cls.output_path)

        cls.ir = ssis_parser.parse_file(cls.output_path)
        cls.validation = ssis_validator.validate_ir(cls.ir)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.components_by_type = {c["type"]: c for c in cls.data_flow["components"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_project_context_valid(self):
        self.assertTrue(self.context_validation["valid"])
        self.assertEqual(self.context_validation["errors"], [])
        self.assertEqual(self.project_context["missing_conmgr_files"], [])

    def test_ambiente_present_in_project_context(self):
        by_name = {p["name"]: p for p in self.project_context["parameters"]}
        self.assertEqual(by_name["ambiente"]["value"], "Produ")

    def test_ir_validates_without_errors(self):
        self.assertEqual(self.validation["errors"], [])
        self.assertTrue(self.validation["valid"])

    def test_source_and_destination_connections_resolved(self):
        self.assertEqual(
            self.components_by_type["teradata_source"]["connection"],
            "Project.ConnectionManagers[cnxTeradata]",
        )
        self.assertEqual(
            self.components_by_type["ole_db_destination"]["connection"],
            "Project.ConnectionManagers[cnxSql]",
        )

    def test_mappings_resolve_correctly(self):
        dest = self.components_by_type["ole_db_destination"]
        resolved = {m["pipeline_input_column"]: m for m in dest["mappings"]}
        self.assertEqual(resolved["Id_Test"]["destination_column"], "Id_Test")
        self.assertEqual(resolved["Desc_Test"]["destination_column"], "Desc_Test")


if __name__ == "__main__":
    unittest.main()
