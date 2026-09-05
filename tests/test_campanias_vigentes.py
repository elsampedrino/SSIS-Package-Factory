"""
Tests de regresion sobre BipSuc_CampaniasVigentes.dtsx.

Congelan el conocimiento verificado manualmente contra el XML fuente en la
iteracion anterior (ver docs/xml_patterns.md), para detectar si un cambio
futuro en ssis_parser.py rompe algo que ya estaba validado.

No es una suite exhaustiva: cubre lo minimo pedido para este paquete de
referencia (estructura, tipos, paths, conexiones, mappings, conversiones de
fecha y validacion del IR).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssis_parser
import ssis_validator


PACKAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Examples",
    "Originals",
    "BipSuc_CampaniasVigentes.dtsx",
)

EXPECTED_PHYSICAL_DESTINATIONS = {
    "COD_CAMPANIA_DIRIGIDA": "CodigoCampania",
    "COD_ACCION_DIRIGIDA": "CodigoAccion",
    "FechaInicio_SAL": "FechaInicio",
    "FechaFin_SAL": "FechaFin",
    "DESC_SPEECH_VENTA": "Speech",
    "DESC_CAMPANIA_DIRIGIDA": "Descripcion",
    "publico_objetivo": "PublicoObjetivo",
}


class CampaniasVigentesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = ssis_parser.parse_file(PACKAGE_PATH)
        cls.data_flow = cls.ir["data_flows"][0]
        cls.components_by_type = {
            c["type"]: c for c in cls.data_flow["components"]
        }

    def test_source_file_and_package_name_are_distinct(self):
        self.assertEqual(self.ir["source_file"], "BipSuc_CampaniasVigentes.dtsx")
        self.assertEqual(self.ir["package_name"], "Package1")

    def test_single_data_flow(self):
        self.assertEqual(len(self.ir["data_flows"]), 1)

    def test_three_components_with_expected_types(self):
        components = self.data_flow["components"]
        self.assertEqual(len(components), 3)
        types = {c["type"] for c in components}
        self.assertEqual(
            types, {"teradata_source", "data_conversion", "ole_db_destination"}
        )

    def test_two_paths(self):
        self.assertEqual(len(self.data_flow["paths"]), 2)

    def test_paths_connect_expected_components(self):
        pairs = {(p["from"], p["to"]) for p in self.data_flow["paths"]}
        self.assertIn(("Teradata Source", "Conversión de datos"), pairs)
        self.assertIn(("Conversión de datos", "Destino de OLE DB"), pairs)

    def test_destination_table(self):
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(dest["properties"]["open_rowset"], "[dbo].[TiposCampanias]")

    def test_connections_referenced(self):
        source = self.components_by_type["teradata_source"]
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(source["connection"], "Project.ConnectionManagers[cnxTeradata]")
        self.assertEqual(
            dest["connection"], "Project.ConnectionManagers[cnxSrvBsLogSBD01]"
        )

    def test_seven_mappings(self):
        dest = self.components_by_type["ole_db_destination"]
        self.assertEqual(len(dest["mappings"]), 7)

    def test_date_conversions(self):
        conv = self.components_by_type["data_conversion"]
        columns_by_name = {c["name"]: c for c in conv["outputs"][0]["columns"]}

        self.assertIn("FechaInicio_SAL", columns_by_name)
        self.assertIn("FechaFin_SAL", columns_by_name)

        fecha_inicio = columns_by_name["FechaInicio_SAL"]
        fecha_fin = columns_by_name["FechaFin_SAL"]

        self.assertTrue(
            fecha_inicio["source_input_lineage_id"].endswith(
                "Teradata Source.Outputs[Teradata Source Output].Columns[FEC_INICIO_TXT]"
            )
        )
        self.assertTrue(
            fecha_fin["source_input_lineage_id"].endswith(
                "Teradata Source.Outputs[Teradata Source Output].Columns[FEC_FIN_TXT]"
            )
        )
        self.assertEqual(fecha_inicio["data_type"], "dbDate")
        self.assertEqual(fecha_fin["data_type"], "dbDate")

    def test_mappings_resolve_correct_physical_destination_column(self):
        dest = self.components_by_type["ole_db_destination"]
        resolved = {
            m["pipeline_input_column"]: m["destination_column"]
            for m in dest["mappings"]
        }
        self.assertEqual(resolved, EXPECTED_PHYSICAL_DESTINATIONS)

    def test_mappings_resolve_source_column(self):
        dest = self.components_by_type["ole_db_destination"]
        for mapping in dest["mappings"]:
            with self.subTest(mapping=mapping["pipeline_input_column"]):
                self.assertIsNotNone(mapping["source_column"])
                self.assertIsNotNone(mapping["source_component"])

    def test_ir_validates_without_errors(self):
        validation = ssis_validator.validate_ir(self.ir)
        self.assertEqual(validation["errors"], [])
        self.assertTrue(validation["valid"])


if __name__ == "__main__":
    unittest.main()
