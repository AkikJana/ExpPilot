"""Section 5 acceptance tests. The whole monitoring loop must work with no LLM_API_KEY present."""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from api.main import app
from data.db import get_conn, init_db
from data.seed import main as seed_main
from data.synth import make_experiment


@pytest.fixture(scope="module", autouse=True)
def _seeded_db():
    """Seed flags/history once for the module; the API needs a populated registry."""
    init_db()
    seed_main()


@pytest.fixture()
def client() -> TestClient:
    """A TestClient over the real app."""
    return TestClient(app)


def _insert_validated_config(experiment_id: str) -> dict:
    """Insert a config in 'validated' status, the only state /launch accepts."""
    config, _, _ = make_experiment("true_lift", seed=1)
    config_dict = config.model_dump()
    config_dict["id"] = experiment_id
    config_dict["status"] = "validated"

    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO experiments (id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
        (experiment_id, json.dumps(config_dict), "validated", None),
    )
    conn.execute("DELETE FROM day_stats WHERE experiment_id = ?", (experiment_id,))
    conn.commit()
    conn.close()
    return config_dict


def test_openapi_docs_render(client: TestClient):
    """OpenAPI schema and /docs must render."""
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_flags_endpoint_returns_registry(client: TestClient):
    """The 20-flag registry is served."""
    response = client.get("/flags")
    assert response.status_code == 200
    assert len(response.json()["flags"]) == 20


def test_unknown_experiment_returns_404(client: TestClient):
    """Unknown ids are 404 with a {'detail': str} body."""
    response = client.get("/experiments/does_not_exist")
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_launching_non_validated_config_returns_409(client: TestClient):
    """Launching an experiment that is not in 'validated' status is a 409."""
    experiment_id = "api_test_conflict"
    conn = get_conn()
    config, _, _ = make_experiment("true_lift", seed=2)
    config_dict = config.model_dump()
    config_dict["id"] = experiment_id
    conn.execute(
        "INSERT OR REPLACE INTO experiments (id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
        (experiment_id, json.dumps(config_dict), "draft", None),
    )
    conn.commit()
    conn.close()

    response = client.post(f"/experiments/{experiment_id}/launch")
    assert response.status_code == 409


def test_unknown_scenario_returns_422(client: TestClient):
    """An unrecognised demo_scenario is a 422."""
    experiment_id = "api_test_bad_scenario"
    _insert_validated_config(experiment_id)
    response = client.post(f"/experiments/{experiment_id}/launch?demo_scenario=not_a_scenario")
    assert response.status_code == 422


@pytest.mark.skipif(bool(os.environ.get("LLM_API_KEY")), reason="asserts the keyless 503 path")
def test_llm_endpoints_return_503_without_key(client: TestClient):
    """Generative endpoints degrade to 503 when no key is configured — Person 1 works keyless."""
    response = client.post("/hypotheses", json={"goal": "increase checkout conversion on mobile"})
    assert response.status_code == 503
    assert response.json()["detail"] == "LLM_API_KEY not set"


def test_clarification_path_works_without_key(client: TestClient):
    """A goal with no measurable metric returns a clarification, not a 503 — no LLM call is needed."""
    response = client.post("/hypotheses", json={"goal": "improve engagement"})
    assert response.status_code == 200
    body = response.json()
    assert body["clarification"] is not None
    assert body["hypothesis"] is None


def test_full_srm_loop_keyless_yields_pause(client: TestClient):
    """THE Section 5 acceptance check: launch srm -> advance 6 days -> action == 'pause', no LLM."""
    experiment_id = "api_test_srm_loop"
    _insert_validated_config(experiment_id)

    launch = client.post(f"/experiments/{experiment_id}/launch?demo_scenario=srm")
    assert launch.status_code == 200
    assert launch.json()["status"] == "running"

    response = client.post(f"/experiments/{experiment_id}/advance", json={"days": 6})
    assert response.status_code == 200
    body = response.json()

    assert body["day"] == 6
    assert body["decision"]["action"] == "pause"
    assert body["alert"]["kind"] == "srm"
    assert body["alert"]["severity"] == "critical"
    assert body["stats"]["srm_flag"] is True


def test_audit_trail_populated_after_tick(client: TestClient):
    """Every executed node leaves an audit row reachable via /audit."""
    response = client.get("/experiments/api_test_srm_loop/audit")
    assert response.status_code == 200
    body = response.json()
    assert len(body["decisions"]) >= 1
    assert {run["node"] for run in body["agent_runs"]} >= {"monitor_node", "decision_node"}


def test_experiment_detail_and_list(client: TestClient):
    """List and detail views reflect the advanced experiment."""
    listing = client.get("/experiments")
    assert listing.status_code == 200
    assert any(e["id"] == "api_test_srm_loop" for e in listing.json()["experiments"])

    detail = client.get("/experiments/api_test_srm_loop")
    assert detail.status_code == 200
    body = detail.json()
    assert body["latest_day"] == 6
    assert body["latest_decision"]["action"] == "pause"


def test_memory_endpoint(client: TestClient):
    """Memory records are listable and filterable."""
    assert client.get("/memory").status_code == 200
    assert client.get("/memory?kind=lesson").status_code == 200
