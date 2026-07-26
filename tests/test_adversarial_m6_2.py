"""Adversarial stress harness for ExpPilot Evals suite, API endpoints, and LangGraph workflow."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["CURSOR_AGENT_BIN"] = "cursor-agent-not-installed"

from api.main import app
from data import db
from evals.run_evals import run_evaluation_suite
from agents.graph import run_copilot


class EvalsSuiteGroundTruthTests(unittest.TestCase):
    """Verify that all 5 key R4 evaluation metrics are computed non-trivially and match benchmark ground truth."""

    def test_evals_suite_metrics_and_ground_truth(self) -> None:
        report = run_evaluation_suite()
        self.assertEqual(report["status"], "PASS")

        # 1. Composite adoption readiness score
        composite = report["composite_adoption_readiness_score"]
        self.assertIsInstance(composite, float)
        self.assertGreaterEqual(composite, 90.0, f"Composite score {composite} below expected 90.0 threshold")
        self.assertLessEqual(composite, 100.0)

        # 2. Recommendation accuracy against expert decision benchmarks
        rec = report["recommendation_metrics"]
        self.assertEqual(rec["total_scenarios"], 20)
        self.assertGreaterEqual(rec["category_accuracy"], 0.90, "Category accuracy below 90%")
        self.assertGreaterEqual(rec["segment_accuracy"], 0.90, "Segment accuracy below 90%")
        self.assertGreaterEqual(rec["flag_accuracy"], 0.90, "Flag accuracy below 90%")
        self.assertGreaterEqual(rec["primary_metric_accuracy"], 0.90, "Primary metric accuracy below 90%")
        self.assertGreaterEqual(rec["overall_precision"], 0.90, "Overall precision below 90%")
        self.assertGreaterEqual(rec["overall_recall"], 0.90, "Overall recall below 90%")

        # 3. Statistical significance detection accuracy
        tel = report["telemetry_metrics"]
        self.assertEqual(tel["total_scenarios"], 30)
        self.assertGreaterEqual(tel["detection_accuracy"], 0.90, "Detection accuracy below 90%")
        self.assertGreaterEqual(tel["true_positive_rate"], 0.90, "TPR below 90%")
        self.assertGreaterEqual(tel["true_negative_rate"], 0.90, "TNR below 90%")
        self.assertGreaterEqual(tel["srm_detection_rate"], 0.90, "SRM detection rate below 90%")
        self.assertGreaterEqual(tel["guardrail_detection_rate"], 0.90, "Guardrail detection rate below 90%")

        # 4. Configuration acceptance rate
        acc = report["acceptance_metrics"]
        self.assertEqual(acc["total_configs"], 20)
        self.assertGreaterEqual(acc["acceptance_rate"], 0.90, "Acceptance rate below 90%")

        # 5. Creation & analysis time reduction
        tim = report["time_reduction_metrics"]
        self.assertGreater(tim["automated_creation_time_sec"], 0.0)
        self.assertGreater(tim["automated_analysis_time_sec"], 0.0)
        self.assertGreater(tim["creation_time_reduction_pct"], 99.0, "Creation time reduction below 99%")
        self.assertGreater(tim["analysis_time_reduction_pct"], 99.0, "Analysis time reduction below 99%")


class ApiAdversarialEndpointsTests(unittest.TestCase):
    """Adversarial stress testing against FastAPI endpoints in api/main.py."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "api_test.db"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_health_check(self) -> None:
        """/health also reports database reachability.

        Asserting on the whole body froze the endpoint at {"status": "ok"},
        which could not distinguish a healthy service from one that is up but
        unable to reach its database.
        """
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["database"]["reachable"])

    def test_create_experiment_validation(self) -> None:
        # Invalid short goal (< 5 chars)
        res = self.client.post("/experiments", json={"goal": "abcd"})
        self.assertEqual(res.status_code, 422)

        # Invalid baseline rate (<= 0 or >= 1)
        res = self.client.post("/experiments", json={"goal": "Valid experiment goal", "baseline_rate": 0.0})
        self.assertEqual(res.status_code, 422)

        res = self.client.post("/experiments", json={"goal": "Valid experiment goal", "baseline_rate": 1.0})
        self.assertEqual(res.status_code, 422)

        # Invalid daily traffic (<= 0)
        res = self.client.post("/experiments", json={"goal": "Valid experiment goal", "daily_traffic": 0})
        self.assertEqual(res.status_code, 422)

        # Valid payload
        res = self.client.post("/experiments", json={"goal": "Improve checkout conversion for mobile users"})
        self.assertEqual(res.status_code, 200)
        exp_id = res.json()["config"]["id"]

        # Duplicate experiment on same segment while active
        self.client.post(f"/experiments/{exp_id}/start")
        res_dup = self.client.post("/experiments", json={"goal": "Improve checkout conversion for mobile users"})
        self.assertEqual(res_dup.status_code, 409)

    def test_nonexistent_experiment_endpoints(self) -> None:
        res = self.client.post("/experiments/exp_nonexistent/start")
        self.assertEqual(res.status_code, 404)

        res = self.client.post("/experiments/exp_nonexistent/conclude")
        self.assertEqual(res.status_code, 404)

        res = self.client.get("/experiments/exp_nonexistent/ontology")
        self.assertEqual(res.status_code, 404)

        res = self.client.get("/experiments/exp_nonexistent/timeline")
        self.assertEqual(res.status_code, 404)

    def test_harness_gitops_invalid_action(self) -> None:
        # First create an experiment
        res = self.client.post("/experiments", json={"goal": "Improve checkout conversion for mobile users"})
        exp_id = res.json()["config"]["id"]

        # Invalid action should raise 422
        res_gitops = self.client.post(f"/experiments/{exp_id}/harness-gitops", json={"action": "invalid_action_xyz"})
        self.assertEqual(res_gitops.status_code, 422)

    def test_copilot_run_endpoint(self) -> None:
        # Copilot run without telemetry
        res = self.client.post("/copilot/run", json={"goal": "Improve guest checkout conversion"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("proposal", body)

        # Copilot run with telemetry
        telemetry = {
            "day": 7,
            "control_n": 10000,
            "control_conversions": 1000,
            "treatment_n": 10000,
            "treatment_conversions": 1300,
            "guardrail_control_rate": 0.01,
            "guardrail_treatment_rate": 0.01,
        }
        res_telem = self.client.post("/copilot/run", json={"goal": "Improve guest checkout conversion 2", "telemetry": telemetry})
        self.assertEqual(res_telem.status_code, 200)
        body_telem = res_telem.json()
        self.assertIn("proposal", body_telem)
        self.assertIn("decision", body_telem)


class LangGraphWorkflowTests(unittest.TestCase):
    """Direct testing of LangGraph state machine in agents/graph.py."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "graph_test.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_run_copilot_flow_without_telemetry(self) -> None:
        result = run_copilot("Improve payment page retention")
        self.assertIn("proposal", result)
        self.assertNotIn("decision", result)

    def test_run_copilot_flow_with_telemetry(self) -> None:
        telemetry = {
            "day": 7,
            "control_n": 10000,
            "control_conversions": 1000,
            "treatment_n": 10000,
            "treatment_conversions": 1300,
            "guardrail_control_rate": 0.01,
            "guardrail_treatment_rate": 0.01,
        }
        result = run_copilot("Improve payment page retention 2", telemetry=telemetry)
        self.assertIn("proposal", result)
        self.assertIn("decision", result)
        self.assertEqual(result["decision"]["action"], "scale")


if __name__ == "__main__":
    unittest.main()
