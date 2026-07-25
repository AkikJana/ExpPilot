"""Integration tests for FastAPI HTTP routes (api/main.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from data import db


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_create_and_lifecycle_endpoints(self) -> None:
        # 1. Create experiment
        create_resp = self.client.post(
            "/experiments",
            json={"goal": "Improve checkout conversion rate", "baseline_rate": 0.1, "daily_traffic": 2000},
        )
        self.assertEqual(create_resp.status_code, 200)
        proposal = create_resp.json()
        exp_id = proposal["config"]["id"]
        self.assertEqual(proposal["config"]["status"], "validated")

        # 2. Start experiment
        start_resp = self.client.post(f"/experiments/{exp_id}/start")
        self.assertEqual(start_resp.status_code, 200)
        self.assertEqual(start_resp.json()["status"], "running")

        # 3. Monitor experiment telemetry
        monitor_resp = self.client.post(
            "/monitor",
            json={
                "experiment_id": exp_id,
                "day": 7,
                "control_n": 10000,
                "control_conversions": 1000,
                "treatment_n": 10000,
                "treatment_conversions": 1250,
                "guardrail_control_rate": 0.01,
                "guardrail_treatment_rate": 0.01,
            },
        )
        self.assertEqual(monitor_resp.status_code, 200)
        decision = monitor_resp.json()
        self.assertEqual(decision["action"], "scale")

        # 4. Get timeline
        timeline_resp = self.client.get(f"/experiments/{exp_id}/timeline")
        self.assertEqual(timeline_resp.status_code, 200)
        timeline_data = timeline_resp.json()
        self.assertEqual(timeline_data["days_observed"], 1)

        # 5. Harness GitOps proposal
        gitops_resp = self.client.post(
            f"/experiments/{exp_id}/harness-gitops",
            json={"action": "scale"},
        )
        self.assertEqual(gitops_resp.status_code, 200)
        self.assertIn("manifest", gitops_resp.json())

        # 6. Conclude experiment
        conclude_resp = self.client.post(f"/experiments/{exp_id}/conclude")
        self.assertEqual(conclude_resp.status_code, 200)
        self.assertEqual(conclude_resp.json()["status"], "concluded")

    def test_copilot_run_endpoint(self) -> None:
        resp = self.client.post(
            "/copilot/run",
            json={
                "goal": "Optimize cart checkout flow",
                "baseline_rate": 0.08,
                "daily_traffic": 3000,
                "telemetry": {
                    "day": 7,
                    "control_n": 10000,
                    "control_conversions": 800,
                    "treatment_n": 10000,
                    "treatment_conversions": 1100,
                    "guardrail_control_rate": 0.01,
                    "guardrail_treatment_rate": 0.01,
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertIn("proposal", result)
        self.assertIn("decision", result)

    def test_ontology_endpoints(self) -> None:
        create_resp = self.client.post(
            "/experiments",
            json={"goal": "Optimize onboarding step"},
        )
        proposal = create_resp.json()
        exp_id = proposal["config"]["id"]
        parent_id = proposal["ontology"]["id"]

        # GET ontology
        ont_resp = self.client.get(f"/experiments/{exp_id}/ontology")
        self.assertEqual(ont_resp.status_code, 200)

        # Branch ontology
        branch_resp = self.client.post(
            f"/experiments/{exp_id}/ontology/branches",
            json={
                "parent_id": parent_id,
                "statement": "Simplify signup form inputs to improve conversion",
                "rationale": "Shorter form reduces user dropoff.",
                "segment": "new_users",
            },
        )
        self.assertEqual(branch_resp.status_code, 200)
        tree = branch_resp.json()
        self.assertEqual(len(tree["children"]), 1)
        self.assertEqual(tree["children"][0]["status"], "queued")

    def test_error_handling_and_404s(self) -> None:
        # Non-existent experiment
        self.assertEqual(self.client.post("/experiments/exp_missing/start").status_code, 404)
        self.assertEqual(self.client.post("/experiments/exp_missing/conclude").status_code, 404)
        self.assertEqual(self.client.get("/experiments/exp_missing/timeline").status_code, 404)
        self.assertEqual(self.client.get("/experiments/exp_missing/ontology").status_code, 404)

        # Invalid gitops action (non-terminal action) -> 422 Unprocessable Entity
        create_resp = self.client.post("/experiments", json={"goal": "Improve checkout flow"})
        exp_id = create_resp.json()["config"]["id"]
        invalid_gitops = self.client.post(
            f"/experiments/{exp_id}/harness-gitops",
            json={"action": "continue"},
        )
        self.assertEqual(invalid_gitops.status_code, 422)

    def test_reset_endpoint(self) -> None:
        self.client.post("/experiments", json={"goal": "Improve checkout flow"})
        reset_resp = self.client.post("/reset")
        self.assertEqual(reset_resp.status_code, 200)
        self.assertEqual(reset_resp.json()["status"], "reset")


if __name__ == "__main__":
    unittest.main()
