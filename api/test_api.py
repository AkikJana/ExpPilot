"""API acceptance tests. The whole monitoring loop must work with no LLM key present."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import service
from api.main import app
from data.db import get_conn, init_db
from data.seed import main as seed_main
from data.synth import make_experiment


@pytest.fixture(scope="module", autouse=True)
def _seeded_db():
    """Seed the registry and history once for the module."""
    init_db()
    seed_main()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_running_experiment(client: TestClient, scenario: str, seed: int) -> str:
    """Drive the real create path: hypotheses -> config -> create."""
    hypotheses = client.post(
        "/copilot/hypotheses", json={"goal": "increase checkout conversion on mobile"}
    ).json()["hypotheses"]
    config = client.post("/copilot/config", json={"hypothesis": hypotheses[0]}).json()["config"]
    created = client.post(
        "/experiments", json={"config": config, "scenario": scenario, "seed": seed}
    )
    assert created.status_code == 200, created.text
    return created.json()["experiment_id"]


def test_health_and_docs(client: TestClient):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_flags_registry_served(client: TestClient):
    flags = client.get("/flags").json()
    assert len(flags) == 20
    assert {"key", "segment", "status"} <= set(flags[0])


def test_unknown_experiment_returns_404(client: TestClient):
    response = client.get("/experiments/does_not_exist")
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_hypotheses_are_grounded_in_real_precedents(client: TestClient):
    """Keyless: three hypotheses, each citing ids that exist in the history store."""
    body = client.post(
        "/copilot/hypotheses", json={"goal": "increase checkout conversion on mobile"}
    ).json()
    assert len(body["hypotheses"]) == 3
    assert body["category"] == "checkout"

    valid = {p["id"] for p in body["precedents"]}
    for hypothesis in body["hypotheses"]:
        assert set(hypothesis["precedent_ids"]) <= valid


def test_config_sample_size_is_code_computed(client: TestClient):
    """required_n_per_arm comes from the stats core, never from a model."""
    from stats.core import power_analysis

    hypotheses = client.post(
        "/copilot/hypotheses", json={"goal": "increase checkout conversion on mobile"}
    ).json()["hypotheses"]
    body = client.post("/copilot/config", json={"hypothesis": hypotheses[0]}).json()
    config = body["config"]

    assert config["required_n_per_arm"] == power_analysis(config["baseline_rate"], config["mde"])
    assert "validation" in body


def test_full_srm_loop_keyless_yields_pause(client: TestClient):
    """THE headline check: an srm experiment advanced to day 6 must pause, with no LLM key."""
    experiment_id = _create_running_experiment(client, "srm", seed=42)

    body = None
    for _ in range(5):  # day 1 exists at create; advance to day 6
        body = client.post(f"/experiments/{experiment_id}/advance").json()

    assert body["day"] == 6
    assert body["stats"]["srm_flag"] is True
    assert body["action"] == "pause"
    assert body["decision"]["action"] == "pause"
    assert any(a["kind"] == "srm" for a in body["alerts"])


def test_audit_trail_populated(client: TestClient):
    """Every executed node leaves a row reachable through /audit."""
    experiment_id = _create_running_experiment(client, "true_lift", seed=11)
    client.post(f"/experiments/{experiment_id}/advance")

    audit = client.get(f"/experiments/{experiment_id}/audit").json()
    assert len(audit["decisions"]) >= 1
    assert {"compute_stats", "monitor", "decide"} <= {r["node"] for r in audit["agent_runs"]}


def test_verdict_rejection_writes_a_lesson_to_memory(client: TestClient):
    """A human override closes the learning loop: rejection is recorded verbatim."""
    experiment_id = _create_running_experiment(client, "true_lift", seed=12)

    # Long-term memory is intentionally durable across experiments and pytest runs, so a
    # relaunch does not clear it. Drop this experiment's prior lessons to assert an exact
    # count of the one written by this rejection.
    conn = get_conn()
    conn.execute("DELETE FROM memory WHERE source_experiment_id = ?", (experiment_id,))
    conn.commit()
    conn.close()

    advanced = client.post(f"/experiments/{experiment_id}/advance").json()
    day = advanced["day"]

    response = client.post(
        f"/experiments/{experiment_id}/decisions/{day}/verdict",
        json={"verdict": "rejected", "reason": "guardrail margin too close for comfort"},
    )
    assert response.status_code == 200
    assert response.json()["human_verdict"] == "rejected"

    lessons = client.get("/memory", params={"kind": "lesson"}).json()
    mine = [rec for rec in lessons if rec["source_experiment_id"] == experiment_id]
    assert len(mine) == 1
    assert "guardrail margin too close" in mine[0]["content"]


def test_adoption_metric_counts_verdicts(client: TestClient):
    body = client.get("/metrics/adoption").json()
    assert body["total_decisions"] >= 1
    assert {"approved", "rejected", "pending", "adoption_rate"} <= set(body)


def test_verdict_on_missing_decision_returns_404(client: TestClient):
    response = client.post(
        "/experiments/nope/decisions/3/verdict", json={"verdict": "approved", "reason": "x"}
    )
    assert response.status_code == 404


def test_experiment_detail_never_leaks_ground_truth(client: TestClient):
    """The copilot surface must not expose the hidden correct_action label."""
    experiment_id = _create_running_experiment(client, "guardrail_breach", seed=13)
    client.post(f"/experiments/{experiment_id}/advance")

    detail = client.get(f"/experiments/{experiment_id}").json()
    assert "correct_action" not in detail["simulator"]
    assert "correct_action" not in str(detail["config"])
    assert detail["current_day"] >= 1
