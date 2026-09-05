"""
Tests de regresion sobre BipSuc_Turnero.dtsx (segundo paquete de referencia).

Cubre lo minimo para congelar los hallazgos de la segunda pasada de
ingenieria inversa (ver docs/campanias_vs_turnero.md): Control Flow completo,
los 5 componentClassID nuevos, el cross-reference de connection managers para
Execute SQL Task, y el warning de validacion sobre el Data Flow huerfano.
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
    "BipSuc_Turnero.dtsx",
)


class TurneroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = ssis_parser.parse_file(PACKAGE_PATH)
        cls.data_flows_by_name = {df["name"]: df for df in cls.ir["data_flows"]}
        cls.validation = ssis_validator.validate_ir(cls.ir)

    def test_source_file_and_package_name(self):
        self.assertEqual(self.ir["source_file"], "BipSuc_Turnero.dtsx")
        self.assertEqual(self.ir["package_name"], "BipSuc_Turnero")

    def test_three_data_flows_found_including_nested_one(self):
        self.assertEqual(
            set(self.data_flows_by_name),
            {"Tarea Flujo de datos 1", "Tarea Flujo de datos", "Tarea Flujo Staging"},
        )

    def test_package_variables(self):
        qualified_names = {v["qualified_name"] for v in self.ir["variables"]}
        self.assertEqual(
            qualified_names,
            {"User::CantNIPAmbos", "User::CantNIPSoloDestino", "User::CantNIPSoloOrigen"},
        )

    def test_new_component_types_present(self):
        types_found = set()
        for df in self.ir["data_flows"]:
            for c in df["components"]:
                types_found.add(c["type"])
        self.assertTrue(
            {
                "derived_column",
                "merge_join",
                "conditional_split",
                "ole_db_source",
                "row_count",
            }.issubset(types_found)
        )
        # ningun componente desconocido en este archivo
        self.assertFalse(any(t.startswith("unknown:") for t in types_found))

    def test_row_count_writes_expected_variables(self):
        complex_flow = self.data_flows_by_name["Tarea Flujo de datos"]
        row_counts = [c for c in complex_flow["components"] if c["type"] == "row_count"]
        self.assertEqual(len(row_counts), 3)
        variable_names = {c["properties"]["variable_name"] for c in row_counts}
        self.assertEqual(
            variable_names,
            {"User::CantNIPAmbos", "User::CantNIPSoloDestino", "User::CantNIPSoloOrigen"},
        )

    def test_complex_flow_has_no_database_destination(self):
        complex_flow = self.data_flows_by_name["Tarea Flujo de datos"]
        types_found = {c["type"] for c in complex_flow["components"]}
        self.assertNotIn("ole_db_destination", types_found)

    def test_control_flow_finds_execute_sql_tasks_and_sequence_containers(self):
        top_types = [e["type"] for e in self.ir["control_flow"]["executables"]]
        self.assertEqual(top_types.count("execute_sql_task"), 7)
        self.assertEqual(top_types.count("sequence_container"), 2)
        self.assertEqual(top_types.count("pipeline"), 2)  # los top-level; el 3ro esta anidado

    def test_execute_sql_task_connections_resolve_via_guid_crossref(self):
        for exe in self.ir["control_flow"]["executables"]:
            if exe["type"] == "execute_sql_task":
                with self.subTest(task=exe["name"]):
                    self.assertEqual(
                        exe["sql"]["connection_manager_ref_id"],
                        "Project.ConnectionManagers[cnxSrvTurnosDb]",
                    )

    def test_isolated_disabled_pipeline_is_flagged_as_warning_not_error(self):
        self.assertTrue(self.validation["valid"])
        self.assertEqual(self.validation["errors"], [])
        codes = {w["code"] for w in self.validation["warnings"]}
        self.assertIn("structurally_isolated_executable", codes)
        isolated = next(
            w
            for w in self.validation["warnings"]
            if w["code"] == "structurally_isolated_executable"
        )
        self.assertEqual(isolated["context"]["executable"], "Tarea Flujo de datos")
        # el warning es puramente estructural: no afirma que sea codigo muerto
        self.assertFalse(isolated["context"]["effective_enabled"])

    def test_destination_mappings_resolve_for_both_destinations(self):
        for name in ("Tarea Flujo de datos 1", "Tarea Flujo Staging"):
            with self.subTest(data_flow=name):
                df = self.data_flows_by_name[name]
                dest = next(c for c in df["components"] if c["type"] == "ole_db_destination")
                self.assertGreater(len(dest["mappings"]), 0)
                for mapping in dest["mappings"]:
                    self.assertIsNotNone(mapping["source_column"])
                    self.assertIsNotNone(mapping["destination_column"])


if __name__ == "__main__":
    unittest.main()
