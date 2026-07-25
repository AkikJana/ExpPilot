"""Unit and state machine execution tests for agents/graph.py (LangGraph Orchestration)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.graph import build_graph, run_copilot
from data import db


class GraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_graph_compilation() -> None:
        graph = build_graph()
        self.assertIsNotNone(graph)

    def test_run_copilot_without_telemetry() -> None:
        res = run_copilot(
            goal="Improve checkout conversion",
            baseline_rate=0.1,
            daily_traffic=2000,
        )
        self.assertIn("proposal", res)
        self.assertNotIn("decision", res)
        self.assertEqual(res["goal"], "Improve checkout conversion")
        self.assertIn("config", res["proposal"])

    def test_run_copilot_with_telemetry() -> None:
        telemetry = {
            "day": 7,
            "control_n": 10000,
            "control_conversions": 1000,
            "treatment_n": 10000,
            "treatment_conversions": 1300,
            "guardrail_control_rate": 0.01,
            "guardrail_treatment_rate": 0.01,
        }
        res = run_copilot(
            goal="Optimize user onboarding",
            baseline_rate=0.08,
            daily_traffic=3000,
            telemetry=telemetry,
        )
        self.assertIn("proposal", res)
        self.assertIn("decision", res)
        self.assertEqual(res["decision"]["action"], "scale")

    def test_run_copilot_invalid_goal_raises_error() -> None:
        # Pydantic / validation check when goal is invalid
        with self.assertRaises(Exception):
            run_copilot(goal="")


if __name__ == "__main__":
    unittest.main()
