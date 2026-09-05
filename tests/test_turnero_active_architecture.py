"""
Tests de regresion sobre la reconstruccion de la ARQUITECTURA ACTIVA de
BipSuc_Turnero.dtsx (ver docs/campanias_vs_turnero.md, seccion "Arquitectura
activa reconstruida").

El usuario aclaro que el paquete contiene una implementacion historica
deshabilitada (Merge Join / Conditional Split / carga directa a dbo.Clientes)
y que la arquitectura vigente es la de staging (#ClientesStage) + transaccion
manual via Execute SQL Task. Estos tests congelan, contra el XML real, que:

1. la logica vieja queda marcada como no-activa (execution_role="disabled")
   pero SIGUE presente en el IR completo (no se descarta nada); el
   aislamiento estructural (structurally_isolated) se verifica aparte y NO
   se interpreta como sinonimo de "no ejecutable" (ver
   test_execution_role_semantics.py para el caso sintetico que lo demuestra);
2. la logica de staging es la que efectivamente esta activa;
3. active_execution_graph nunca mezcla ambas cosas.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssis_parser


PACKAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Examples",
    "Originals",
    "BipSuc_Turnero.dtsx",
)


def _flatten_control_flow(nodes):
    """Devuelve TODOS los nodos del arbol de control_flow (incluye anidados)."""
    flat = []
    for node in nodes:
        flat.append(node)
        if node["type"] == "sequence_container":
            flat.extend(_flatten_control_flow(node["executables"]))
    return flat


def _flatten_active_graph(nodes):
    flat = []
    for node in nodes:
        flat.append(node)
        if node["type"] == "sequence_container":
            flat.extend(_flatten_active_graph(node["executables"]))
    return flat


class TurneroActiveArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ir = ssis_parser.parse_file(PACKAGE_PATH)
        cls.all_nodes = {
            n["ref_id"]: n for n in _flatten_control_flow(cls.ir["control_flow"]["executables"])
        }
        cls.active_nodes = _flatten_active_graph(
            cls.ir["active_execution_graph"]["executables"]
        )
        cls.active_ref_ids = {n["ref_id"] for n in cls.active_nodes}
        cls.data_flows_by_name = {df["name"]: df for df in cls.ir["data_flows"]}

    # 1. El Data Flow viejo (reconciliacion Merge Join/Conditional Split/Row
    #    Count) esta deshabilitado Y ademas estructuralmente aislado (sin
    #    ningun PrecedenceConstraint que lo mencione). Son dos hechos
    #    independientes: el aislamiento estructural NO es lo que lo saca de
    #    la ejecucion (podria estar aislado y activo, ver test D en
    #    test_execution_role_semantics.py); lo saca su propio DTS:Disabled.
    def test_old_reconciliation_dataflow_is_disabled_and_structurally_isolated(self):
        node = self.all_nodes["Package\\Tarea Flujo de datos"]
        self.assertFalse(node["enabled"])  # DTS:Disabled="True", leido directo del XML
        self.assertTrue(node["structurally_isolated"])  # sin incoming ni outgoing constraints
        self.assertEqual(node["execution_role"], "disabled")  # por enabled=False, no por el aislamiento
        self.assertFalse(node["is_control_flow_root"])  # no es root: no esta effective_enabled

    def test_old_direct_load_pipeline_disabled_via_ancestor_container(self):
        # "Tarea Flujo de datos 1" NO tiene DTS:Disabled propio, pero su
        # container ("Sequence Container") si lo tiene -> effective_enabled
        # debe dar False aunque el atributo propio sea "enabled".
        node = self.all_nodes["Package\\Sequence Container\\Tarea Flujo de datos 1"]
        self.assertTrue(node["enabled"])  # atributo XML propio: no disabled
        self.assertFalse(node["effective_enabled"])  # heredado del container disabled
        self.assertEqual(node["execution_role"], "disabled")

    # 2. Ninguna de las dos piezas historicas participa de active_execution_graph.
    def test_historical_dataflows_absent_from_active_execution_graph(self):
        self.assertNotIn("Package\\Tarea Flujo de datos", self.active_ref_ids)
        self.assertNotIn(
            "Package\\Sequence Container\\Tarea Flujo de datos 1", self.active_ref_ids
        )
        self.assertNotIn("Package\\Sequence Container", self.active_ref_ids)

    # 3. La logica de staging SI es parte del flujo activo.
    def test_staging_logic_is_active(self):
        staging = self.all_nodes["Package\\Tarea Flujo Staging"]
        self.assertEqual(staging["execution_role"], "active")
        self.assertIn("Package\\Tarea Flujo Staging", self.active_ref_ids)
        # y su Data Flow fue efectivamente parseado (no solo el nodo de Control Flow)
        self.assertIn("Tarea Flujo Staging", self.data_flows_by_name)

    # 4. #ClientesStage aparece en las tareas/componentes correctos.
    def test_clientesstage_appears_in_expected_places(self):
        crear = self.all_nodes["Package\\Crear #ClientesStage"]
        self.assertIn("#ClientesStage", crear["sql"]["sql_statement_source"])
        self.assertIn("INTO #ClientesStage", crear["sql"]["sql_statement_source"])

        inserta = self.all_nodes["Package\\Sequence Container 1\\Inserta Destino"]
        self.assertIn("FROM #ClientesStage", inserta["sql"]["sql_statement_source"])

        staging_df = self.data_flows_by_name["Tarea Flujo Staging"]
        dest = next(
            c for c in staging_df["components"] if c["type"] == "ole_db_destination"
        )
        self.assertEqual(dest["properties"]["open_rowset"], "#ClientesStage")
        # DelayValidation en el Data Flow Task + validateExternalMetadata="False"
        # en el propio componente destino: el mecanismo que permite apuntar a
        # un objeto que todavia no existe al momento de validar el paquete.
        staging_exe = self.all_nodes["Package\\Tarea Flujo Staging"]
        self.assertTrue(staging_exe["delay_validation"])
        self.assertEqual(dest["validate_external_metadata"], "False")

    # 5. BEGIN/COMMIT/ROLLBACK activos estan correctamente identificados.
    def test_begin_commit_rollback_sql_identified(self):
        begin = self.all_nodes["Package\\Inicia Transacción 1"]
        commit = self.all_nodes["Package\\Confirma transacción 1"]
        rollback = self.all_nodes["Package\\Revierte todo 1"]

        self.assertIn("begin tran", begin["sql"]["sql_statement_source"].lower())
        self.assertIn("commit tran", commit["sql"]["sql_statement_source"].lower())
        self.assertIn("rollback tran", rollback["sql"]["sql_statement_source"].lower())

        for node in (begin, commit, rollback):
            with self.subTest(task=node["name"]):
                self.assertEqual(node["execution_role"], "active")

    # 6. Success y Failure llevan a los caminos correspondientes.
    def test_success_and_failure_constraints_point_to_expected_tasks(self):
        top_level_pcs = self.ir["control_flow"]["precedence_constraints"]

        to_commit = next(
            pc for pc in top_level_pcs if pc["to"] == "Package\\Confirma transacción 1"
        )
        to_rollback = next(
            pc for pc in top_level_pcs if pc["to"] == "Package\\Revierte todo 1"
        )

        self.assertEqual(to_commit["from"], "Package\\Sequence Container 1")
        self.assertIsNone(to_commit["value"])  # ausencia de Value = Success (default)

        self.assertEqual(to_rollback["from"], "Package\\Sequence Container 1")
        self.assertEqual(to_rollback["value"], "1")  # Value="1" -> rama de falla

        active_pcs = self.ir["active_execution_graph"]["precedence_constraints"]
        active_targets = {pc["to"] for pc in active_pcs}
        self.assertIn("Package\\Confirma transacción 1", active_targets)
        self.assertIn("Package\\Revierte todo 1", active_targets)

    # 7. El parser no confunde "presente en XML" con "ejecutable": el Data
    #    Flow historico sigue en data_flows (evidencia de Merge Join,
    #    Conditional Split, Row Count, OLE DB Source) aunque no este activo.
    def test_historical_dataflow_still_available_as_xml_evidence(self):
        self.assertIn("Tarea Flujo de datos", self.data_flows_by_name)
        historical_df = self.data_flows_by_name["Tarea Flujo de datos"]
        types_found = {c["type"] for c in historical_df["components"]}
        self.assertTrue({"merge_join", "conditional_split", "ole_db_source", "row_count"}.issubset(types_found))

        node = self.all_nodes["Package\\Tarea Flujo de datos"]
        self.assertEqual(node["execution_role"], "disabled")


if __name__ == "__main__":
    unittest.main()
