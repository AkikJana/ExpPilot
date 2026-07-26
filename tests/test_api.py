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
        """/health reports liveness *and* whether the database is reachable.

        A bare {"status": "ok"} could not distinguish 'working' from 'up but
        cannot reach its database', which is the failure this endpoint exists to
        make visible.
        """
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"]["engine"], "sqlite")
        self.assertTrue(body["database"]["reachable"])

    def test_list_experiments_endpoint(self) -> None:
        """Experiments are discoverable without the caller retaining an id."""
        empty = self.client.get("/experiments")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["experiments"], [])

        created = self.client.post(
            "/experiments", json={"goal": "Increase checkout conversion on mobile"}
        )
        self.assertEqual(created.status_code, 200)
        experiment_id = created.json()["config"]["id"]

        listed = self.client.get("/experiments").json()["experiments"]
        self.assertEqual([e["id"] for e in listed], [experiment_id])
        self.assertEqual(listed[0]["status"], "validated")
        self.assertIn("audience_segment", listed[0])

    def test_hypothesis_index_selects_a_different_candidate(self) -> None:
        """The caller chooses which hypothesis to configure, not just the top one."""
        first = self.client.post(
            "/experiments", json={"goal": "Increase checkout conversion on mobile"}
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["selected_hypothesis_index"], 0)

        candidates = first.json()["hypotheses"]
        self.assertGreater(len(candidates), 1, "need >1 candidate to exercise selection")

        second = self.client.post(
            "/experiments",
            json={"goal": "Increase checkout conversion on mobile", "hypothesis_index": 1},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["selected_hypothesis_index"], 1)
        self.assertEqual(
            second.json()["hypothesis"]["statement"], candidates[1]["statement"]
        )

    def test_out_of_range_hypothesis_index_is_clamped(self) -> None:
        """A stale UI selection must not 500 the request."""
        response = self.client.post(
            "/experiments",
            json={"goal": "Increase checkout conversion on mobile", "hypothesis_index": 99},
        )
        self.assertEqual(response.status_code, 200)
        candidates = response.json()["hypotheses"]
        self.assertEqual(response.json()["selected_hypothesis_index"], len(candidates) - 1)

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

    def test_http_404_not_found_comprehensive(self) -> None:
        """Adversarial test confirming HTTP 404 Not Found for all endpoints with nonexistent experiment IDs."""
        bad_id = "exp_missing_99999"

        # POST endpoints
        self.assertEqual(self.client.post(f"/experiments/{bad_id}/start").status_code, 404)
        self.assertEqual(self.client.post(f"/experiments/{bad_id}/conclude").status_code, 404)
        self.assertEqual(
            self.client.post(f"/experiments/{bad_id}/harness-gitops", json={"action": "scale"}).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                f"/experiments/{bad_id}/ontology/branches",
                json={
                    "parent_id": "root_node",
                    "statement": "Valid statement with enough characters",
                    "rationale": "Valid rationale",
                    "segment": "mobile_users",
                },
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/monitor",
                json={
                    "experiment_id": bad_id,
                    "day": 1,
                    "control_n": 100,
                    "control_conversions": 10,
                    "treatment_n": 100,
                    "treatment_conversions": 10,
                    "guardrail_control_rate": 0.01,
                    "guardrail_treatment_rate": 0.01,
                },
            ).status_code,
            404,
        )

        # GET endpoints
        self.assertEqual(self.client.get(f"/experiments/{bad_id}/timeline").status_code, 404)
        self.assertEqual(self.client.get(f"/experiments/{bad_id}/ontology").status_code, 404)

    def test_http_409_conflict_on_validation_failure(self) -> None:
        """Adversarial test confirming HTTP 409 Conflict when experiment creation or copilot run fails pre-launch validation."""
        from data.seed import ensure_seeded

        ensure_seeded()
        conn = db.get_conn()
        try:
            # Set all flags to 'running' so validation fails with blocking flag_unavailable error
            conn.execute("UPDATE feature_flags SET status = 'running'")
            conn.commit()
        finally:
            conn.close()

        # POST /experiments -> 409 Conflict, carrying the structured report so a
        # client can render each failed check rather than one joined sentence.
        resp_create = self.client.post(
            "/experiments",
            json={"goal": "Improve checkout conversion rate"},
        )
        self.assertEqual(resp_create.status_code, 409)
        detail = resp_create.json()["detail"]
        self.assertEqual(detail["error"], "validation_blocked")
        self.assertIn("already 'running'", detail["detail"])
        self.assertFalse(detail["validation"]["passed"])
        self.assertTrue(
            any(issue["code"] == "flag_unavailable" for issue in detail["validation"]["blocking"]),
            f"expected a flag_unavailable blocking issue, got {detail['validation']['blocking']}",
        )

        # POST /copilot/run -> 409 Conflict
        resp_copilot = self.client.post(
            "/copilot/run",
            json={"goal": "Improve checkout conversion rate"},
        )
        self.assertEqual(resp_copilot.status_code, 409)
        self.assertIn("detail", resp_copilot.json())

    def test_unreachable_database_returns_503_not_opaque_500(self) -> None:
        """Regression: a configured-but-unreachable database must be diagnosable.

        Pointing DATABASE_URL at a dead host previously surfaced as a bare 500
        'Internal Server Error' with nothing to act on. It must now be a 503
        naming the problem.
        """
        original_url = db.DATABASE_URL
        # Port 1 refuses immediately, so this does not wait on a connect timeout.
        db.DATABASE_URL = "postgresql://u:p@127.0.0.1:1/postgres"
        try:
            health = self.client.get("/health")
            self.assertEqual(health.status_code, 200, "health must answer even when the DB is down")
            self.assertEqual(health.json()["status"], "degraded")
            self.assertFalse(health.json()["database"]["reachable"])
            self.assertTrue(health.json()["database"]["detail"], "must say what went wrong")

            created = self.client.post(
                "/experiments", json={"goal": "Increase checkout conversion on mobile"}
            )
            self.assertEqual(created.status_code, 503)
            body = created.json()
            self.assertEqual(body["error"], "database_unavailable")
            self.assertTrue(body["detail"])
            # The connection URL holds the password and must never be echoed back.
            self.assertNotIn("127.0.0.1:1", body["detail"])
        finally:
            db.DATABASE_URL = original_url

    def test_http_422_unprocessable_entity_validation_errors(self) -> None:
        """Adversarial test confirming HTTP 422 Unprocessable Entity for invalid request payloads."""
        # 1. Goal too short (< 5 chars)
        r1 = self.client.post("/experiments", json={"goal": "tiny"})
        self.assertEqual(r1.status_code, 422)

        # 2. Baseline rate >= 1.0 or <= 0.0
        r2_high = self.client.post("/experiments", json={"goal": "Improve conversion", "baseline_rate": 1.5})
        self.assertEqual(r2_high.status_code, 422)
        r2_low = self.client.post("/experiments", json={"goal": "Improve conversion", "baseline_rate": 0.0})
        self.assertEqual(r2_low.status_code, 422)

        # 3. Daily traffic <= 0
        r3 = self.client.post("/experiments", json={"goal": "Improve conversion", "daily_traffic": 0})
        self.assertEqual(r3.status_code, 422)

        # 4. Invalid GitOps action
        create_resp = self.client.post("/experiments", json={"goal": "Improve checkout flow"})
        self.assertEqual(create_resp.status_code, 200)
        exp_id = create_resp.json()["config"]["id"]

        r4 = self.client.post(
            f"/experiments/{exp_id}/harness-gitops",
            json={"action": "unsupported_action"},
        )
        self.assertEqual(r4.status_code, 422)

        # 5. Invalid ontology branch statement (< 10 chars) or rationale (< 3 chars)
        parent_id = create_resp.json()["ontology"]["id"]
        r5_stmt = self.client.post(
            f"/experiments/{exp_id}/ontology/branches",
            json={"parent_id": parent_id, "statement": "too short", "rationale": "Valid rationale", "segment": "mobile"},
        )
        self.assertEqual(r5_stmt.status_code, 422)

        r5_rat = self.client.post(
            f"/experiments/{exp_id}/ontology/branches",
            json={"parent_id": parent_id, "statement": "Valid statement length", "rationale": "no", "segment": "mobile"},
        )
        self.assertEqual(r5_rat.status_code, 422)

        # 6. Malformed monitor payload (e.g., negative conversion count or wrong types)
        r6 = self.client.post("/monitor", json={"experiment_id": exp_id, "day": "not_an_int"})
        self.assertEqual(r6.status_code, 422)


if __name__ == "__main__":
    unittest.main()

