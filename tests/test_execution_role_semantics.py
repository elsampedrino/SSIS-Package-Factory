"""
Test sintetico (sin .dtsx real) para la semantica de reachability corregida
en ssis_parser.annotate_execution_roles / build_active_execution_graph.

Motivo de esta correccion: una version anterior consideraba "orphaned" (y por
lo tanto NO ejecutable) a cualquier executable sin PrecedenceConstraints en su
nivel. Eso es incorrecto en general: un Control Flow puede tener varias
raices independientes que arrancan en paralelo sin ningun constraint
entrante. Este test construye el caso minimo que lo demuestra:

    A -> B
    D

donde D no tiene ningun constraint (ni entrante ni saliente), esta
habilitado, y DEBE aparecer en active_execution_graph como raiz
independiente — no debe ser tratado como codigo muerto.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssis_parser


def _make_task(ref_id, name, disabled=False):
    return {
        "ref_id": ref_id,
        "name": name,
        "dtsid": None,
        "executable_type": "Microsoft.ExecuteSQLTask",
        "type": "execute_sql_task",
        "description": None,
        "disabled": disabled,
        "delay_validation": False,
        "thread_hint": None,
        "variables": [],
        "sql": {
            "connection_id": None,
            "connection_manager_ref_id": None,
            "sql_statement_source": f"SELECT '{name}'",
        },
    }


def _make_constraint(ref_id, name, from_ref, to_ref, value=None):
    return {
        "ref_id": ref_id,
        "name": name,
        "dtsid": None,
        "from": from_ref,
        "to": to_ref,
        "logical_and": True,
        "value": value,
    }


class ExecutionRoleSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.executables = [
            _make_task("Package\\A", "A"),
            _make_task("Package\\B", "B"),
            _make_task("Package\\D", "D"),
        ]
        self.precedence_constraints = [
            _make_constraint(
                "Package.PrecedenceConstraints[C1]", "C1", "Package\\A", "Package\\B"
            )
        ]
        ssis_parser.annotate_execution_roles(
            self.executables, self.precedence_constraints
        )
        self.by_name = {n["name"]: n for n in self.executables}

    def test_a_is_a_normal_root_with_an_outgoing_edge(self):
        a = self.by_name["A"]
        self.assertTrue(a["enabled"])
        self.assertTrue(a["effective_enabled"])
        self.assertFalse(a["has_incoming_constraint"])
        self.assertTrue(a["has_outgoing_constraint"])
        self.assertFalse(a["structurally_isolated"])
        self.assertTrue(a["is_control_flow_root"])
        self.assertEqual(a["execution_role"], "active")

    def test_b_is_reached_from_a_not_a_root(self):
        b = self.by_name["B"]
        self.assertTrue(b["has_incoming_constraint"])
        self.assertFalse(b["has_outgoing_constraint"])
        self.assertFalse(b["structurally_isolated"])
        self.assertFalse(b["is_control_flow_root"])
        self.assertEqual(b["execution_role"], "active")

    def test_d_is_structurally_isolated_but_is_a_valid_independent_root(self):
        d = self.by_name["D"]
        self.assertTrue(d["enabled"])
        self.assertTrue(d["effective_enabled"])
        self.assertFalse(d["has_incoming_constraint"])
        self.assertFalse(d["has_outgoing_constraint"])
        self.assertTrue(d["structurally_isolated"])
        # el punto central de la correccion: aislado != no ejecutable
        self.assertTrue(d["is_control_flow_root"])
        self.assertEqual(d["execution_role"], "active")

    def test_active_execution_graph_includes_the_isolated_enabled_root(self):
        control_flow = {
            "executables": self.executables,
            "precedence_constraints": self.precedence_constraints,
        }
        graph = ssis_parser.build_active_execution_graph(control_flow)

        active_names = {n["name"] for n in graph["executables"]}
        self.assertEqual(active_names, {"A", "B", "D"})

        d_entry = next(n for n in graph["executables"] if n["name"] == "D")
        self.assertTrue(d_entry["is_control_flow_root"])
        self.assertTrue(d_entry["structurally_isolated"])

        # el unico constraint activo sigue siendo A->B; D no genera ninguno
        self.assertEqual(len(graph["precedence_constraints"]), 1)
        self.assertEqual(graph["precedence_constraints"][0]["from"], "Package\\A")
        self.assertEqual(graph["precedence_constraints"][0]["to"], "Package\\B")

    def test_disabled_isolated_executable_is_excluded_from_active_graph(self):
        # Mismo escenario, pero D esta deshabilitado: sigue siendo
        # structurally_isolated (dato estructural, no cambia), pero ahora
        # execution_role="disabled" y NO debe aparecer en el grafo activo.
        executables = [
            _make_task("Package\\A", "A"),
            _make_task("Package\\B", "B"),
            _make_task("Package\\D", "D", disabled=True),
        ]
        ssis_parser.annotate_execution_roles(executables, self.precedence_constraints)
        by_name = {n["name"]: n for n in executables}

        d = by_name["D"]
        self.assertFalse(d["enabled"])
        self.assertTrue(d["structurally_isolated"])  # sigue aislado estructuralmente
        self.assertFalse(d["is_control_flow_root"])  # pero no es root: no esta enabled
        self.assertEqual(d["execution_role"], "disabled")

        graph = ssis_parser.build_active_execution_graph(
            {"executables": executables, "precedence_constraints": self.precedence_constraints}
        )
        active_names = {n["name"] for n in graph["executables"]}
        self.assertEqual(active_names, {"A", "B"})


if __name__ == "__main__":
    unittest.main()
