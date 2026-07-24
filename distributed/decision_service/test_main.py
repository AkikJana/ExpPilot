"""API-level tests for the Decision Service. No external infra required — the
SQLite audit sink is the dev/test backend, exercised exactly as it runs in prod."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from distributed.decision_service.evaluator import DEFAULT_PACK
from distributed.decision_service.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _make_input(**overrides) -> dict:
    base = dict(
        experiment_id="exp_api_test",
        day=10,
        srm_p_value=0.9,
        srm_flag=False,
        prob_beats_control=0.97,
        expected_loss_ship=0.001,
        expected_loss_keep=0.02,
        guardrail_breach=False,
        guardrail_margin=0.0,
        control_n=4000,
        treatment_n=4000,
        required_n_per_arm=3843,
    )
    base.update(overrides)
    return base


def test_health(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}


def test_default_pack_is_registered(client: TestClient):
    packs = client.get("/packs").json()
    assert any(p["id"] == DEFAULT_PACK.id for p in packs)

    pack = client.get(f"/packs/{DEFAULT_PACK.id}").json()
    assert pack["version"] == DEFAULT_PACK.version


def test_unknown_pack_returns_404(client: TestClient):
    assert client.get("/packs/does-not-exist").status_code == 404
    response = client.post(
        "/evaluate", json={"input": _make_input(), "pack_id": "does-not-exist"}
    )
    assert response.status_code == 404


def test_evaluate_scale(client: TestClient):
    response = client.post("/evaluate", json={"input": _make_input()})
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "scale"
    assert len(body["fired_checks"]) == 4
    assert body["pack_version"] == DEFAULT_PACK.version


def test_evaluate_srm_beats_scale(client: TestClient):
    response = client.post(
        "/evaluate",
        json={"input": _make_input(experiment_id="exp_srm_wins", srm_flag=True, srm_p_value=0.0001)},
    )
    assert response.json()["action"] == "pause"


def test_evaluation_is_recorded_and_replayable_via_history(client: TestClient):
    # The audit sink is deliberately append-only/durable (it's the audit trail
    # of record), so a static id would accumulate history across repeated test
    # runs against the same SQLite file — a fresh id per invocation asserts
    # exactly this test's writes, matching the fix applied to the analogous
    # issue in api/test_api.py during the Sections 4-8 reconciliation.
    import uuid

    experiment_id = f"exp_history_test_{uuid.uuid4().hex[:8]}"
    client.post("/evaluate", json={"input": _make_input(experiment_id=experiment_id, day=7)})
    client.post("/evaluate", json={"input": _make_input(experiment_id=experiment_id, day=8)})

    history = client.get(f"/experiments/{experiment_id}/history").json()
    assert [h["day"] for h in history] == [7, 8]
    assert all(h["experiment_id"] == experiment_id for h in history)


def test_register_new_pack_version(client: TestClient):
    """A challenger pack (§4: rule changes are experiments) can be registered and
    evaluated against without touching the default pack."""
    challenger = DEFAULT_PACK.model_dump()
    challenger["id"] = "experiment-lifecycle-challenger"
    challenger["version"] = "1.1.0-shadow"
    challenger["ship_prob_threshold"] = 0.90  # a looser bar, hypothetically proposed

    response = client.post("/packs", json=challenger)
    assert response.status_code == 200

    evaluated = client.post(
        "/evaluate",
        json={
            "input": _make_input(experiment_id="exp_challenger", prob_beats_control=0.92),
            "pack_id": "experiment-lifecycle-challenger",
        },
    )
    assert evaluated.json()["action"] == "scale"  # would be "continue" on the default pack

    default_result = client.post(
        "/evaluate", json={"input": _make_input(experiment_id="exp_challenger", prob_beats_control=0.92)}
    )
    assert default_result.json()["action"] == "continue"
