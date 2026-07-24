"""Regression tests for the non-LLM decision boundary."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["CURSOR_AGENT_BIN"] = "cursor-agent-not-installed"

from api import service
from data import db
from shared.models import DayStats


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_positive_result_scales_after_readiness_gate(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        experiment_id = proposal["config"]["id"]
        service.start_experiment(experiment_id)
        decision = service.analyze_day(
            DayStats(
                experiment_id=experiment_id,
                day=7,
                control_n=20_000,
                control_conversions=2_000,
                treatment_n=20_000,
                treatment_conversions=2_300,
                guardrail_control_rate=0.01,
                guardrail_treatment_rate=0.01,
            )
        )
        self.assertEqual(decision.action, "scale")

    def test_srm_pauses_before_other_decisions(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        decision = service.analyze_day(
            DayStats(
                experiment_id=proposal["config"]["id"],
                day=7,
                control_n=30_000,
                control_conversions=3_000,
                treatment_n=10_000,
                treatment_conversions=1_200,
                guardrail_control_rate=0.01,
                guardrail_treatment_rate=0.01,
            )
        )
        self.assertEqual(decision.action, "pause")

    def test_branching_is_queued_and_harness_is_gitops_only(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        experiment_id = proposal["config"]["id"]
        ontology = service.branch_ontology(
            experiment_id,
            proposal["ontology"]["id"],
            "Test clearer fee disclosure",
            "It may reduce price anxiety before checkout.",
            "new_users",
        )
        child = ontology["children"][-1]
        self.assertEqual(child["status"], "queued")
        manifest = service.propose_harness_gitops(experiment_id, "rollback")
        self.assertTrue(manifest["filename"].endswith(".yaml"))
        self.assertIn("requires_review", manifest)


if __name__ == "__main__":
    unittest.main()
