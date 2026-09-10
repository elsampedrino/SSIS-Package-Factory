"""
Tests de project-generation-v1 (MODE B: ProjectSpec -> Project.params +
N x *.conmgr, reutilizando project_context/ para el round-trip).

No modifica generator/ ni nada relacionado con Control Flow. No genera ni
muta ningun .dtproj real -- el fixture .dtproj usado en el round-trip
(FIXTURE_DTPROJ_XML) es exclusivamente de tests, NO una capacidad del
producto (ver docs/project_generation_v1.md, seccion "Round-trip").

Cubre, como minimo:
 Project.params: vacio, no sensible, sensible shell, sensitive+value -> error,
   IDs lowercase, DataType=18, Required/Sensitive.
 TERADATA: sin/con/multiples PropertyExpression, GUID uppercase, charset
   ASCII/UTF8, TeraConnectionString, sin TeraPassword, TeraRetain true/false,
   provider.
 OLEDB: basico, PropertyExpression, GUID uppercase, ConnectionString,
   Application Name estable, sin Password, rechazo de retain_same_connection,
   provider.
 Validation: parametro inexistente, duplicados, provider desconocido,
   campos requeridos faltantes.
 Round-trip: parametros, sensitivity, provider, dtsid, property expressions,
   referenced parameters, validator en verde.
 Security: ningun archivo generado contiene Salt/IV/Algorithm/DTS:Password/
   TeraPassword; parametro sensible sin Property Value; connection strings
   sin password.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project_context.context_builder import build_project_context
from project_context.validator import validate_project_context
from project_generator.conmgr_writer import build_conmgr_tree
from project_generator.params_writer import build_params_tree
from project_generator.project_generator import generate_project_resources
from project_generator.spec_schema import (
    ProjectSpecValidationError,
    validate_project_spec,
)
from project_generator.xml_helpers import DTS_NS, SSIS_NS

FORBIDDEN_SECRET_MARKERS = (
    "Salt=",
    "IV=",
    "Algorithm=",
    "<DTS:Password",
    "<TeraPassword",
    "aes256-cbc",
)


def _dts(tag: str) -> str:
    return f"{{{DTS_NS}}}{tag}"


def _ssis(tag: str) -> str:
    return f"{{{SSIS_NS}}}{tag}"


def _minimal_spec():
    return {
        "project": {
            "parameters": [
                {"name": "dbTeradata", "sensitive": False, "value": "D_DW_APPLICATIONS"},
                {"name": "srvTeradata", "sensitive": False, "value": "tdgnn.test"},
                {"name": "pwTeradata", "sensitive": True},
                {"name": "pwSql", "sensitive": True},
            ],
            "connections": [
                {
                    "name": "cnxTeradataGen",
                    "provider": "teradata",
                    "server": "tdgnn.test",
                    "database": "D_DW_APPLICATIONS",
                    "user": "D_DW_APPLICATIONS_USR",
                    "charset": "ASCII",
                    "retain_same_connection": False,
                    "property_expressions": [
                        {"property": "ServerName", "parameter": "srvTeradata"},
                        {"property": "Password", "parameter": "pwTeradata"},
                    ],
                },
                {
                    "name": "cnxSqlGen",
                    "provider": "oledb",
                    "server": "SRVTEST",
                    "catalog": "TestDB",
                    "user": "usTest",
                    "property_expressions": [
                        {"property": "Password", "parameter": "pwSql"},
                    ],
                },
            ],
        }
    }


class ProjectParamsWriterTests(unittest.TestCase):
    def test_empty_parameters_produce_self_closing_root(self):
        root = build_params_tree([])
        self.assertEqual(list(root), [])
        xml_text = ET.tostring(root, encoding="unicode")
        self.assertEqual(len(ET.fromstring(xml_text).findall(_ssis("Parameter"))), 0)

    def test_non_sensitive_parameter_has_value_datatype_18(self):
        root = build_params_tree(
            [{"name": "dbTeradata", "sensitive": False, "value": "D_DW_APPLICATIONS"}]
        )
        param_el = root.find(_ssis("Parameter"))
        props = {
            p.get(_ssis("Name")): p.text
            for p in param_el.find(_ssis("Properties")).findall(_ssis("Property"))
        }
        self.assertEqual(props["Value"], "D_DW_APPLICATIONS")
        self.assertEqual(props["DataType"], "18")
        self.assertEqual(props["Sensitive"], "0")
        self.assertEqual(props["Required"], "1")

    def test_sensitive_parameter_has_no_value_property_at_all(self):
        root = build_params_tree([{"name": "pwTeradata", "sensitive": True}])
        param_el = root.find(_ssis("Parameter"))
        prop_names = {
            p.get(_ssis("Name"))
            for p in param_el.find(_ssis("Properties")).findall(_ssis("Property"))
        }
        self.assertNotIn("Value", prop_names)
        self.assertIn("DataType", prop_names)

    def test_parameter_id_is_lowercase_with_braces(self):
        root = build_params_tree([{"name": "dbTeradata", "sensitive": False, "value": "x"}])
        param_el = root.find(_ssis("Parameter"))
        id_text = param_el.find(_ssis("Properties")).find(_ssis("Property")).text
        self.assertTrue(id_text.startswith("{"))
        self.assertTrue(id_text.endswith("}"))
        self.assertEqual(id_text, id_text.lower())

    def test_required_defaults_true_and_can_be_overridden(self):
        root = build_params_tree(
            [
                {"name": "a", "sensitive": False, "value": "x"},
                {"name": "b", "sensitive": False, "value": "y", "required": False},
            ]
        )
        by_name = {
            p.get(_ssis("Name")): {
                pp.get(_ssis("Name")): pp.text
                for pp in p.find(_ssis("Properties")).findall(_ssis("Property"))
            }
            for p in root.findall(_ssis("Parameter"))
        }
        self.assertEqual(by_name["a"]["Required"], "1")
        self.assertEqual(by_name["b"]["Required"], "0")


class TeradataConmgrWriterTests(unittest.TestCase):
    def _base_conn(self, **overrides):
        conn = {
            "name": "cnxTeradataGen",
            "provider": "teradata",
            "server": "tdgnn.test",
            "database": "D_DW_APPLICATIONS",
            "user": "D_DW_APPLICATIONS_USR",
            "charset": "ASCII",
        }
        conn.update(overrides)
        return conn

    def test_basic_without_property_expressions(self):
        root = build_conmgr_tree(self._base_conn())
        self.assertEqual(root.findall(_dts("PropertyExpression")), [])
        self.assertEqual(root.get(_dts("CreationName")), "TERADATA")

    def test_with_single_property_expression(self):
        conn = self._base_conn(
            property_expressions=[{"property": "ServerName", "parameter": "srvTeradata"}]
        )
        root = build_conmgr_tree(conn)
        exprs = root.findall(_dts("PropertyExpression"))
        self.assertEqual(len(exprs), 1)
        self.assertEqual(exprs[0].get(_dts("Name")), "ServerName")
        self.assertEqual(exprs[0].text, "@[$Project::srvTeradata]")

    def test_with_multiple_property_expressions(self):
        conn = self._base_conn(
            property_expressions=[
                {"property": "ServerName", "parameter": "srvTeradata"},
                {"property": "Password", "parameter": "pwTeradata"},
                {"property": "Database", "parameter": "dbTeradata"},
            ]
        )
        root = build_conmgr_tree(conn)
        exprs = root.findall(_dts("PropertyExpression"))
        self.assertEqual(len(exprs), 3)
        self.assertEqual(
            [e.get(_dts("Name")) for e in exprs], ["ServerName", "Password", "Database"]
        )

    def test_dtsid_is_uppercase_with_braces(self):
        root = build_conmgr_tree(self._base_conn())
        dtsid = root.get(_dts("DTSID"))
        self.assertTrue(dtsid.startswith("{"))
        self.assertTrue(dtsid.endswith("}"))
        self.assertEqual(dtsid, dtsid.upper())

    def test_charset_ascii_reflected_in_connection_string(self):
        root = build_conmgr_tree(self._base_conn(charset="ASCII"))
        cs = self._connection_string(root)
        self.assertIn("CHARSET=ASCII", cs)

    def test_charset_utf8_reflected_in_connection_string(self):
        root = build_conmgr_tree(self._base_conn(charset="UTF8"))
        cs = self._connection_string(root)
        self.assertIn("CHARSET=UTF8", cs)

    def test_tera_connection_string_matches_evidenced_format(self):
        root = build_conmgr_tree(self._base_conn())
        cs = self._connection_string(root)
        self.assertEqual(
            cs,
            "DBCNAME=tdgnn.test;UID=D_DW_APPLICATIONS_USR;AUTHENTICATION=TD2;"
            "DATABASE=D_DW_APPLICATIONS;CHARSET=ASCII;"
            "DRIVER={Teradata Database ODBC Driver 20.00};LOGINTIMEOUT=20;",
        )

    def test_no_tera_password_element(self):
        root = build_conmgr_tree(self._base_conn())
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        self.assertIsNone(object_data_cm.find("TeraPassword"))

    def test_tera_retain_true(self):
        root = build_conmgr_tree(self._base_conn(retain_same_connection=True))
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        self.assertEqual(object_data_cm.find("TeraRetain").text, "True")

    def test_tera_retain_false(self):
        root = build_conmgr_tree(self._base_conn(retain_same_connection=False))
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        self.assertEqual(object_data_cm.find("TeraRetain").text, "False")

    def test_tera_retain_defaults_false_when_absent(self):
        root = build_conmgr_tree(self._base_conn())
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        self.assertEqual(object_data_cm.find("TeraRetain").text, "False")

    def test_provider_attribute_is_teradata_literal(self):
        root = build_conmgr_tree(self._base_conn())
        self.assertEqual(root.get(_dts("CreationName")), "TERADATA")

    @staticmethod
    def _connection_string(root: ET.Element) -> str:
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        return object_data_cm.find("TeraConnectionString").text


class OledbConmgrWriterTests(unittest.TestCase):
    def _base_conn(self, **overrides):
        conn = {
            "name": "cnxSqlGen",
            "provider": "oledb",
            "server": "SRVTEST",
            "catalog": "TestDB",
            "user": "usTest",
        }
        conn.update(overrides)
        return conn

    def test_basic_oledb(self):
        root = build_conmgr_tree(self._base_conn())
        self.assertEqual(root.get(_dts("CreationName")), "OLEDB")
        self.assertEqual(root.findall(_dts("PropertyExpression")), [])

    def test_with_property_expression(self):
        conn = self._base_conn(
            property_expressions=[{"property": "Password", "parameter": "pwSql"}]
        )
        root = build_conmgr_tree(conn)
        exprs = root.findall(_dts("PropertyExpression"))
        self.assertEqual(len(exprs), 1)
        self.assertEqual(exprs[0].text, "@[$Project::pwSql]")

    def test_dtsid_is_uppercase_with_braces(self):
        root = build_conmgr_tree(self._base_conn())
        dtsid = root.get(_dts("DTSID"))
        self.assertTrue(dtsid.startswith("{") and dtsid.endswith("}"))
        self.assertEqual(dtsid, dtsid.upper())

    def test_connection_string_matches_evidenced_format(self):
        root = build_conmgr_tree(self._base_conn())
        cs = self._connection_string(root)
        self.assertTrue(cs.startswith("Data Source=SRVTEST;User ID=usTest;Initial Catalog=TestDB;"))
        self.assertIn("Provider=SQLOLEDB.1;", cs)
        self.assertIn("Auto Translate=False;", cs)

    def test_application_name_is_stable_factory_pattern(self):
        root = build_conmgr_tree(self._base_conn())
        cs = self._connection_string(root)
        dtsid = root.get(_dts("DTSID"))
        self.assertIn(f"Application Name=SSIS-Factory-{dtsid}cnxSqlGen;", cs)

    def test_no_password_element(self):
        root = build_conmgr_tree(self._base_conn())
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        self.assertIsNone(object_data_cm.find(_dts("Password")))

    def test_provider_attribute_is_oledb_literal(self):
        root = build_conmgr_tree(self._base_conn())
        self.assertEqual(root.get(_dts("CreationName")), "OLEDB")

    @staticmethod
    def _connection_string(root: ET.Element) -> str:
        object_data_cm = root.find(_dts("ObjectData")).find(_dts("ConnectionManager"))
        return object_data_cm.get(_dts("ConnectionString"))


class SpecValidationTests(unittest.TestCase):
    def test_valid_minimal_spec_has_no_errors(self):
        self.assertEqual(validate_project_spec(_minimal_spec()), [])

    def test_sensitive_with_value_is_error(self):
        spec = _minimal_spec()
        spec["project"]["parameters"].append(
            {"name": "pwBad", "sensitive": True, "value": "no-deberia-estar-aca"}
        )
        errors = validate_project_spec(spec)
        self.assertTrue(any("PROHIBIDO" in e for e in errors))

    def test_non_sensitive_without_value_is_error(self):
        spec = _minimal_spec()
        spec["project"]["parameters"].append({"name": "sinValor", "sensitive": False})
        errors = validate_project_spec(spec)
        self.assertTrue(any(".value es obligatorio" in e for e in errors))

    def test_property_expression_referencing_nonexistent_parameter(self):
        spec = _minimal_spec()
        spec["project"]["connections"][0]["property_expressions"].append(
            {"property": "Database", "parameter": "noExiste"}
        )
        errors = validate_project_spec(spec)
        self.assertTrue(any("noExiste" in e for e in errors))

    def test_duplicate_parameter_name(self):
        spec = _minimal_spec()
        spec["project"]["parameters"].append(
            {"name": "dbTeradata", "sensitive": False, "value": "otra_vez"}
        )
        errors = validate_project_spec(spec)
        self.assertTrue(any("duplicado" in e.lower() for e in errors))

    def test_duplicate_connection_name(self):
        spec = _minimal_spec()
        dup = dict(spec["project"]["connections"][0])
        spec["project"]["connections"].append(dup)
        errors = validate_project_spec(spec)
        self.assertTrue(any("duplicada" in e.lower() for e in errors))

    def test_unknown_provider_is_rejected(self):
        spec = _minimal_spec()
        spec["project"]["connections"][0]["provider"] = "odbc"
        errors = validate_project_spec(spec)
        self.assertTrue(any("provider" in e for e in errors))

    def test_missing_required_fields_teradata(self):
        spec = {
            "project": {
                "parameters": [],
                "connections": [{"name": "cnx", "provider": "teradata", "server": "s"}],
            }
        }
        errors = validate_project_spec(spec)
        self.assertTrue(any("database" in e for e in errors))
        self.assertTrue(any("user" in e for e in errors))
        self.assertTrue(any("charset" in e for e in errors))

    def test_missing_required_fields_oledb(self):
        spec = {
            "project": {
                "parameters": [],
                "connections": [{"name": "cnx", "provider": "oledb", "server": "s"}],
            }
        }
        errors = validate_project_spec(spec)
        self.assertTrue(any("catalog" in e for e in errors))
        self.assertTrue(any("user" in e for e in errors))

    def test_oledb_retain_same_connection_rejected(self):
        spec = _minimal_spec()
        spec["project"]["connections"][1]["retain_same_connection"] = False
        errors = validate_project_spec(spec)
        self.assertTrue(any("DEFERRED" in e for e in errors))

    def test_raw_expression_key_rejected(self):
        spec = _minimal_spec()
        spec["project"]["connections"][0]["property_expressions"][0]["expression"] = (
            '@[$Project::ambiente] == "QA" ? "A" : "B"'
        )
        errors = validate_project_spec(spec)
        self.assertTrue(any("expression" in e and "cruda" in e for e in errors))

    def test_assert_raises_on_invalid_spec(self):
        with self.assertRaises(ProjectSpecValidationError):
            from project_generator.spec_schema import assert_valid_project_spec

            assert_valid_project_spec({"project": {"parameters": [], "connections": [
                {"name": "cnx", "provider": "bogus", "server": "s"}
            ]}})


# ---------------------------------------------------------------------------
# Round-trip: ProjectSpec -> archivos generados -> build_project_context ->
# validate_project_context, usando el ContextBuilder/Validator EXISTENTES de
# project_context/ (sin duplicar su logica).
#
# FIXTURE_DTPROJ_XML es UNICAMENTE un fixture de tests -- declara los mismos
# nombres de archivo .conmgr que este test genera, para que
# build_project_context() (que resuelve conexiones por NOMBRE DE ARCHIVO
# declarado en .dtproj, no por contenido de disco) pueda encontrarlas. No
# representa una capacidad nueva del producto: project-generation-v1 sigue
# sin generar ni mutar ningun .dtproj real (ver docs/project_generation_v1.md).
#
# Deliberadamente NO declara ProjectConnectionParameters -- esto ejercita,
# con evidencia real de comportamiento (no simulado), la limitacion ya
# documentada: RetainSameConnection para TERADATA cae al fallback de
# TeraRetain (PARTIAL), y para OLEDB queda en None (DEFERRED), porque
# .dtproj es la unica fuente uniforme y esta fuera de scope.
# ---------------------------------------------------------------------------
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


class RoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        output_dir = cls.tmp_dir.name

        cls.spec = _minimal_spec()
        cls.result = generate_project_resources(cls.spec, output_dir)

        dtproj_path = os.path.join(output_dir, "ProjectGenerationV1Fixture.dtproj")
        with open(dtproj_path, "w", encoding="utf-8") as f:
            f.write(FIXTURE_DTPROJ_XML)

        cls.context = build_project_context(dtproj_path, cls.result["params_path"], output_dir)
        cls.validation = validate_project_context(cls.context)

    @classmethod
    def tearDownClass(cls):
        cls.tmp_dir.cleanup()

    def test_validator_is_green(self):
        self.assertTrue(self.validation["valid"])
        self.assertEqual(self.validation["errors"], [])
        self.assertEqual(self.context["missing_conmgr_files"], [])

    def test_all_generated_parameters_present(self):
        names = {p["name"] for p in self.context["parameters"]}
        self.assertEqual(names, {"dbTeradata", "srvTeradata", "pwTeradata", "pwSql"})

    def test_sensitivity_round_trips_correctly(self):
        by_name = {p["name"]: p for p in self.context["parameters"]}
        self.assertFalse(by_name["dbTeradata"]["sensitive"])
        self.assertEqual(by_name["dbTeradata"]["value"], "D_DW_APPLICATIONS")
        self.assertTrue(by_name["pwTeradata"]["sensitive"])
        self.assertIsNone(by_name["pwTeradata"]["value"])
        self.assertTrue(by_name["pwSql"]["sensitive"])
        self.assertIsNone(by_name["pwSql"]["value"])

    def test_providers_round_trip_correctly(self):
        self.assertEqual(self.context["connections"]["cnxTeradataGen"]["provider"], "TERADATA")
        self.assertEqual(self.context["connections"]["cnxSqlGen"]["provider"], "OLEDB")

    def test_dtsids_round_trip_and_are_correctly_cased(self):
        tera_dtsid = self.context["connections"]["cnxTeradataGen"]["dtsid"]
        sql_dtsid = self.context["connections"]["cnxSqlGen"]["dtsid"]
        self.assertTrue(tera_dtsid.startswith("{") and tera_dtsid == tera_dtsid.upper())
        self.assertTrue(sql_dtsid.startswith("{") and sql_dtsid == sql_dtsid.upper())

    def test_property_expressions_and_referenced_parameters_round_trip(self):
        tera_exprs = {
            e["property"]: e for e in self.context["connections"]["cnxTeradataGen"]["property_expressions"]
        }
        self.assertEqual(tera_exprs["ServerName"]["referenced_parameters"], ["srvTeradata"])
        self.assertEqual(tera_exprs["Password"]["referenced_parameters"], ["pwTeradata"])

        sql_exprs = {
            e["property"]: e for e in self.context["connections"]["cnxSqlGen"]["property_expressions"]
        }
        self.assertEqual(sql_exprs["Password"]["referenced_parameters"], ["pwSql"])

    def test_retain_same_connection_limitation_documented_by_evidence(self):
        # TERADATA: PARTIAL -- sin ProjectConnectionParameters en el fixture,
        # cae al fallback TeraRetain (conmgr) del connection generado.
        tera_conn = self.context["connections"]["cnxTeradataGen"]
        self.assertIs(tera_conn["retain_same_connection"], False)
        self.assertEqual(tera_conn["retain_same_connection_source"], "conmgr")

        # OLEDB: DEFERRED -- ninguna fuente disponible sin .dtproj real.
        sql_conn = self.context["connections"]["cnxSqlGen"]
        self.assertIsNone(sql_conn["retain_same_connection"])
        self.assertIsNone(sql_conn["retain_same_connection_source"])


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.result = generate_project_resources(_minimal_spec(), cls.tmp_dir.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp_dir.cleanup()

    def _all_generated_files(self):
        paths = [self.result["params_path"]] + list(self.result["conmgr_paths"].values())
        for path in paths:
            with open(path, encoding="utf-8") as f:
                yield path, f.read()

    def test_no_forbidden_secret_markers_in_any_generated_file(self):
        for path, content in self._all_generated_files():
            for marker in FORBIDDEN_SECRET_MARKERS:
                with self.subTest(path=path, marker=marker):
                    self.assertNotIn(marker, content)

    def test_sensitive_parameter_has_no_value_property_in_file(self):
        with open(self.result["params_path"], encoding="utf-8") as f:
            root = ET.fromstring(f.read())
        for param_el in root.findall(_ssis("Parameter")):
            props = param_el.find(_ssis("Properties"))
            prop_names = {p.get(_ssis("Name")) for p in props.findall(_ssis("Property"))}
            sensitive = any(
                p.get(_ssis("Name")) == "Sensitive" and p.text == "1"
                for p in props.findall(_ssis("Property"))
            )
            if sensitive:
                self.assertNotIn("Value", prop_names)

    def test_connection_strings_contain_no_password_token(self):
        for path, content in self._all_generated_files():
            if not path.endswith(".conmgr"):
                continue
            with self.subTest(path=path):
                self.assertNotIn("Password=", content)
                self.assertNotIn("PWD=", content.upper())


class RegressionTests(unittest.TestCase):
    """Confirma que project-generation-v1 no acopla generator/ ni
    Control Flow -- este modulo nunca importa de generator.campanias_generator
    ni de generator.control_flow_generator."""

    def test_project_generator_does_not_import_generator_package_generators(self):
        import project_generator.conmgr_writer as conmgr_writer
        import project_generator.params_writer as params_writer
        import project_generator.project_generator as project_generator_module

        for module in (conmgr_writer, params_writer, project_generator_module):
            source = module.__file__
            with open(source, encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("campanias_generator", text)
            self.assertNotIn("control_flow_generator", text)


if __name__ == "__main__":
    unittest.main()
