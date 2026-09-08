"""
Tests del Project Context Analyzer v1 (Modo A: solo lectura de un proyecto
SSIS existente). Fixtures reales: Examples/Originals/BipSuc.dtproj,
Project.params, cnxTeradata.conmgr, cnxSrvBsLogSBD01.conmgr.

Cubre, como minimo, los comportamientos pedidos explicitamente:
 1. lectura del .dtproj
 2. deteccion de Project Connection Managers
 3. lectura de Project.params
 4. deteccion de parametros sensibles
 5. lectura de .conmgr TERADATA
 6. lectura de .conmgr OLEDB
 7. deteccion correcta del provider
 8. extraccion de DTSID
 9. extraccion de Property Expressions
10. deteccion de referencias $Project::...
11. validacion de parametros existentes
12. error por parametro inexistente
13. resolucion de RetainSameConnection
14. combinacion de metadata .dtproj + .conmgr
15. regresion completa de los tests anteriores (ver el resto de tests/)

No se registra ningun valor sensible/cifrado en este archivo ni se
imprime/loggea en los asserts -- solo se verifica que el analizador
devuelva None donde corresponde.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project_context.conmgr_parser import (
    find_project_parameter_references,
    parse_conmgr_file,
)
from project_context.context_builder import (
    _resolve_retain_same_connection,
    build_project_context,
)
from project_context.dtproj_parser import parse_dtproj_file
from project_context.params_parser import parse_params_file
from project_context.validator import (
    get_connection_dtsid,
    get_connection_provider,
    get_retain_same_connection,
    validate_project_context,
)

FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Examples", "Originals", "BipSuc_CampaniasVIgentes",
)
DTPROJ_PATH = os.path.join(FIXTURES_DIR, "BipSuc.dtproj")
PARAMS_PATH = os.path.join(FIXTURES_DIR, "Project.params")
TERADATA_CONMGR_PATH = os.path.join(FIXTURES_DIR, "cnxTeradata.conmgr")
OLEDB_CONMGR_PATH = os.path.join(FIXTURES_DIR, "cnxSrvBsLogSBD01.conmgr")

# GUID reales del proyecto BipSuc, congelados acá como valores de regresión.
# Antes de project-context-v1 vivian hardcodeados en
# generator/xml_helpers.KNOWN_CONNECTION_MANAGERS -- ese diccionario ya no
# existe (el generador resuelve estos mismos DTSID via ProjectContext, ver
# tests/test_generator_campanias.py). Se mantienen acá como literales, no
# como import, para no acoplar este test a un modulo de otro paquete.
KNOWN_REAL_DTSIDS = {
    "cnxTeradata": "{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}",
    "cnxSrvBsLogSBD01": "{5DA5808C-8489-48AC-8614-CB034DE02B61}",
}


class DtprojParserTests(unittest.TestCase):
    """1. lectura del .dtproj / 2. deteccion de Project Connection Managers."""

    @classmethod
    def setUpClass(cls):
        cls.dtproj = parse_dtproj_file(DTPROJ_PATH)

    def test_project_name_and_target_server_version(self):
        self.assertEqual(self.dtproj["name"], "BipSuc")
        self.assertEqual(self.dtproj["target_server_version"], "SQLServer2025")

    def test_packages_declared(self):
        file_names = {p["file_name"] for p in self.dtproj["packages_declared"]}
        self.assertEqual(
            file_names,
            {
                "BipSuc_Turnero.dtsx",
                "BipSuc_CampaniasVigentes.dtsx",
                "CampaniasGenerado.dtsx",
            },
        )

    def test_connection_manager_files_declared(self):
        self.assertEqual(
            set(self.dtproj["connection_manager_files"]),
            {"cnxTeradata.conmgr", "cnxSrvTurnosDb.conmgr", "cnxSrvBsLogSBD01.conmgr"},
        )

    def test_project_connection_parameters_grouped_by_connection(self):
        self.assertEqual(
            set(self.dtproj["connection_parameters"]),
            {"cnxTeradata", "cnxSrvTurnosDb", "cnxSrvBsLogSBD01"},
        )

    def test_retain_same_connection_present_for_all_three_connections(self):
        # Paso 2: "CM.<conn>.RetainSameConnection = ..." para las 3 conexiones.
        expected = {
            "cnxTeradata": "false",
            "cnxSrvTurnosDb": "true",
            "cnxSrvBsLogSBD01": "false",
        }
        for conn_name, expected_value in expected.items():
            with self.subTest(connection=conn_name):
                entry = self.dtproj["connection_parameters"][conn_name]["RetainSameConnection"]
                self.assertEqual(entry["value"], expected_value)

    def test_package_metadata_includes_dtsid_and_name(self):
        by_file = {p["file_name"]: p for p in self.dtproj["package_metadata"]}
        self.assertEqual(
            by_file["BipSuc_CampaniasVigentes.dtsx"]["dtsid"],
            "{03C7554A-0D65-44DD-9BEF-4D37D0AF5CB8}",
        )
        self.assertEqual(by_file["BipSuc_CampaniasVigentes.dtsx"]["name"], "Package1")


class ParamsParserTests(unittest.TestCase):
    """3. lectura de Project.params / 4. deteccion de parametros sensibles."""

    @classmethod
    def setUpClass(cls):
        cls.parameters = parse_params_file(PARAMS_PATH)
        cls.by_name = {p["name"]: p for p in cls.parameters}

    def test_nine_parameters_found(self):
        self.assertEqual(len(self.parameters), 9)
        self.assertEqual(
            set(self.by_name),
            {
                "dbTeradata",
                "pwTeradata",
                "srvTeradata",
                "usrTeradata",
                "srvTurnosDb",
                "pwUsSxSSIS",
                "srvBsLogSBD01",
                "pwSrvBsLogSBD01",
                "ambiente",
            },
        )

    def test_non_sensitive_parameter_exposes_value(self):
        ambiente = self.by_name["ambiente"]
        self.assertFalse(ambiente["sensitive"])
        self.assertEqual(ambiente["value"], "QA")

    def test_sensitive_parameters_never_expose_value(self):
        for name in ("pwTeradata", "pwUsSxSSIS", "pwSrvBsLogSBD01"):
            with self.subTest(parameter=name):
                param = self.by_name[name]
                self.assertTrue(param["sensitive"])
                self.assertIsNone(param["value"])


class ConmgrParserTests(unittest.TestCase):
    """5. lectura .conmgr TERADATA / 6. lectura .conmgr OLEDB /
    7. deteccion del provider / 8. extraccion de DTSID /
    9. Property Expressions / 10. referencias $Project::..."""

    @classmethod
    def setUpClass(cls):
        cls.teradata = parse_conmgr_file(TERADATA_CONMGR_PATH)
        cls.oledb = parse_conmgr_file(OLEDB_CONMGR_PATH)

    def test_teradata_provider_and_dtsid(self):
        self.assertEqual(self.teradata["provider"], "TERADATA")
        self.assertEqual(self.teradata["dtsid"], "{29B4FDD4-193E-4D63-AC90-5C5CDA50E051}")
        self.assertEqual(self.teradata["name"], "cnxTeradata")

    def test_oledb_provider_and_dtsid(self):
        self.assertEqual(self.oledb["provider"], "OLEDB")
        self.assertEqual(self.oledb["dtsid"], "{5DA5808C-8489-48AC-8614-CB034DE02B61}")
        self.assertEqual(self.oledb["name"], "cnxSrvBsLogSBD01")

    def test_teradata_provider_data_and_retain_same_connection(self):
        data = self.teradata["provider_data"]
        self.assertEqual(data["server_name"], "tdgnn.ccba.usr.bpba")
        self.assertEqual(data["database"], "D_DW_APPLICATIONS")
        self.assertIs(data["retain_same_connection"], False)  # via TeraRetain
        self.assertTrue(data["password_is_encrypted"])

    def test_oledb_provider_data_has_no_retain_same_connection(self):
        # Confirmado con evidencia real: OLE DB no serializa esto en el
        # .conmgr -- debe quedar None, no False ni un default inventado.
        data = self.oledb["provider_data"]
        self.assertIsNone(data["retain_same_connection"])
        self.assertIn("SRVBSQADB", data["connection_string"])
        self.assertTrue(data["password_is_encrypted"])

    def test_teradata_has_no_property_expressions(self):
        self.assertEqual(self.teradata["property_expressions"], [])

    def test_oledb_property_expressions_and_parameter_references(self):
        by_property = {e["property"]: e for e in self.oledb["property_expressions"]}
        self.assertEqual(set(by_property), {"InitialCatalog", "Password", "ServerName"})

        self.assertEqual(by_property["Password"]["referenced_parameters"], ["pwSrvBsLogSBD01"])
        self.assertEqual(by_property["ServerName"]["referenced_parameters"], ["srvBsLogSBD01"])
        # InitialCatalog: la referencia esta EMBEBIDA dentro de una expresion
        # mas larga con un operador ternario, no es el valor completo.
        self.assertEqual(by_property["InitialCatalog"]["referenced_parameters"], ["ambiente"])
        self.assertIn("?", by_property["InitialCatalog"]["expression"])

    def test_find_project_parameter_references_helper(self):
        self.assertEqual(
            find_project_parameter_references('@[$Project::pwSrvBsLogSBD01]'),
            ["pwSrvBsLogSBD01"],
        )
        self.assertEqual(
            find_project_parameter_references(
                '@[$Project::ambiente] == "QA" ? "Optimus" : "Migas"'
            ),
            ["ambiente"],
        )
        self.assertEqual(find_project_parameter_references("sin referencias aca"), [])

    def test_unknown_provider_falls_back_generically(self):
        # No hay fixture real de un tercer provider todavia -- se prueba el
        # fallback generico contra un .conmgr sintetico minimo, sin inventar
        # contenido de un provider que no conocemos.
        import tempfile

        synthetic = """<?xml version="1.0"?>
<DTS:ConnectionManager xmlns:DTS="www.microsoft.com/SqlServer/Dts"
  DTS:ObjectName="cnxAlgo" DTS:DTSID="{00000000-0000-0000-0000-000000000000}"
  DTS:CreationName="ODBC">
  <DTS:ObjectData>
    <DTS:ConnectionManager />
  </DTS:ObjectData>
</DTS:ConnectionManager>"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".conmgr", delete=False, encoding="utf-8"
        ) as f:
            f.write(synthetic)
            tmp_path = f.name
        try:
            result = parse_conmgr_file(tmp_path)
            self.assertEqual(result["provider"], "unknown:ODBC")
            self.assertEqual(result["dtsid"], "{00000000-0000-0000-0000-000000000000}")
        finally:
            os.remove(tmp_path)


class RetainSameConnectionResolutionTests(unittest.TestCase):
    """13. resolucion de RetainSameConnection / 14. combinacion .dtproj + .conmgr
    -- probado como funcion pura, sin depender de archivos, para cubrir
    explicitamente los 3 casos de combinacion (dtproj gana, fallback a
    conmgr, ninguna fuente lo tiene)."""

    def test_dtproj_wins_when_both_present(self):
        value, source = _resolve_retain_same_connection("false", True)
        self.assertIs(value, False)
        self.assertEqual(source, "dtproj")

    def test_falls_back_to_conmgr_when_dtproj_absent(self):
        value, source = _resolve_retain_same_connection(None, True)
        self.assertIs(value, True)
        self.assertEqual(source, "conmgr")

    def test_none_when_neither_source_has_it(self):
        value, source = _resolve_retain_same_connection(None, None)
        self.assertIsNone(value)
        self.assertIsNone(source)


class ProjectContextBuilderAndValidatorTests(unittest.TestCase):
    """11. validacion de parametros existentes / 12. error por parametro
    inexistente / combinacion end-to-end contra los fixtures reales."""

    @classmethod
    def setUpClass(cls):
        cls.context = build_project_context(DTPROJ_PATH, PARAMS_PATH, FIXTURES_DIR)
        cls.validation = validate_project_context(cls.context)

    def test_missing_conmgr_file_is_a_warning_not_an_error(self):
        self.assertEqual(self.context["missing_conmgr_files"], ["cnxSrvTurnosDb.conmgr"])
        self.assertTrue(self.validation["valid"])
        self.assertEqual(self.validation["errors"], [])
        codes = {w["code"] for w in self.validation["warnings"]}
        self.assertIn("missing_conmgr_file", codes)

    def test_real_context_has_no_unresolved_parameter_references(self):
        # Los 3 $Project::... del .conmgr de cnxSrvBsLogSBD01 SI existen en
        # Project.params -- 0 errores esperados.
        self.assertEqual(self.validation["errors"], [])

    def test_error_when_parameter_referenced_does_not_exist(self):
        broken_context = copy.deepcopy(self.context)
        broken_context["connections"]["cnxSrvBsLogSBD01"]["property_expressions"].append(
            {
                "property": "Fake",
                "expression": "@[$Project::noExiste]",
                "referenced_parameters": ["noExiste"],
            }
        )
        result = validate_project_context(broken_context)
        self.assertFalse(result["valid"])
        codes = {e["code"] for e in result["errors"]}
        self.assertIn("unresolved_project_parameter_reference", codes)

    def test_retain_same_connection_resolved_for_both_real_connections(self):
        self.assertIs(
            get_retain_same_connection(self.context, "cnxTeradata"), False
        )
        self.assertEqual(
            self.context["connections"]["cnxTeradata"]["retain_same_connection_source"],
            "dtproj",
        )
        self.assertIs(
            get_retain_same_connection(self.context, "cnxSrvBsLogSBD01"), False
        )

    def test_provider_query_helper(self):
        self.assertEqual(get_connection_provider(self.context, "cnxTeradata"), "TERADATA")
        self.assertEqual(get_connection_provider(self.context, "cnxSrvBsLogSBD01"), "OLEDB")

    def test_provider_query_helper_raises_for_unknown_connection(self):
        with self.assertRaises(KeyError):
            get_connection_provider(self.context, "cnxNoExiste")

    def test_dtsid_query_helper_matches_known_real_guids(self):
        # Regresion: los DTSID que este analizador resuelve para
        # cnxTeradata/cnxSrvBsLogSBD01 deben seguir siendo los mismos GUID
        # reales del proyecto BipSuc (ya no hay ningun diccionario
        # hardcodeado en el generador con el que comparar -- ver
        # tests/test_generator_campanias.py::ProjectContextIntegrationTests
        # para la integracion real).
        for name, expected_guid in KNOWN_REAL_DTSIDS.items():
            with self.subTest(connection=name):
                self.assertEqual(get_connection_dtsid(self.context, name), expected_guid)

    def test_no_sensitive_value_ever_present_in_built_context(self):
        for param in self.context["parameters"]:
            if param["sensitive"]:
                self.assertIsNone(param["value"])
        for conn in self.context["connections"].values():
            for prop in conn["project_connection_parameters"].values():
                if prop["sensitive"]:
                    self.assertIsNone(prop["value"])


if __name__ == "__main__":
    unittest.main()
