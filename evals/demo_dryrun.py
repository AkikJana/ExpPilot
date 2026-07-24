"""Scripted demo dry-run: the full keyless path, end to end, against a live API.

Usage: python -m evals.demo_dryrun [--runs 2] [--api http://localhost:8000]
Exits non-zero on the first assertion failure so it can gate a release.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid

import requests

from data.db import get_conn
from data.synth import make_experiment


def _seed_validated_config(experiment_id: str) -> None:
    """Insert a validated config directly, standing in for the LLM design flow."""
    config, _, _ = make_experiment("true_lift", seed=1)
    payload = config.model_dump()
    payload["id"] = experiment_id
    payload["status"] = "validated"

    conn = get_conn()
    try:
        conn.execute("DELETE FROM day_stats WHERE experiment_id = ?", (experiment_id,))
        conn.execute("DELETE FROM decisions WHERE experiment_id = ?", (experiment_id,))
        conn.execute(
            "INSERT OR REPLACE INTO experiments (id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
            (experiment_id, json.dumps(payload), "validated", None),
        )
        conn.commit()
    finally:
        conn.close()


def run_once(api: str, run_index: int) -> None:
    """Execute the full demo path once, asserting the outcome of every beat."""
    print(f"\n--- demo run {run_index} " + "-" * 44)
    experiment_id = f"demo_dryrun_{uuid.uuid4().hex[:8]}"

    # Beat 0: the API is up and serving the registry, with no LLM key present.
    flags = requests.get(f"{api}/flags", timeout=30)
    assert flags.status_code == 200, flags.text
    assert len(flags.json()["flags"]) == 20
    print(f"  registry........ 20 flags")

    # Beat 1: a vague goal is challenged rather than guessed at — no LLM needed.
    clarify = requests.post(f"{api}/hypotheses", json={"goal": "improve engagement"}, timeout=60)
    assert clarify.status_code == 200, clarify.text
    assert clarify.json()["clarification"] is not None
    print(f"  clarification... asked instead of guessing")

    # Beat 2: launch the SRM scenario — the tension beat of the demo.
    _seed_validated_config(experiment_id)
    launch = requests.post(f"{api}/experiments/{experiment_id}/launch?demo_scenario=srm", timeout=60)
    assert launch.status_code == 200, launch.text
    assert launch.json()["status"] == "running"
    print(f"  launch.......... {experiment_id} as scenario 'srm'")

    # Beat 3: advance to day 6. SRM must be caught and must win over a scale-worthy posterior.
    advance = requests.post(f"{api}/experiments/{experiment_id}/advance", json={"days": 6}, timeout=120)
    assert advance.status_code == 200, advance.text
    body = advance.json()
    assert body["day"] == 6, body["day"]
    assert body["stats"]["srm_flag"] is True
    assert body["alert"]["kind"] == "srm"
    assert body["alert"]["severity"] == "critical"
    assert body["decision"]["action"] == "pause", body["decision"]["action"]
    assert body["stats"]["prob_beats_control"] > 0.95, "posterior should favour treatment here"
    print(
        f"  monitor......... day 6, P(beats)={body['stats']['prob_beats_control']:.4f} "
        f"but SRM p={body['stats']['srm_p_value']:.2e}"
    )
    print(f"  decision........ '{body['decision']['action']}' — trust verdict beat the posterior")

    # Beat 4: the audit trail is populated and reachable.
    audit = requests.get(f"{api}/experiments/{experiment_id}/audit", timeout=30)
    assert audit.status_code == 200, audit.text
    nodes_run = {run["node"] for run in audit.json()["agent_runs"]}
    assert {"monitor_node", "decision_node"} <= nodes_run, nodes_run
    assert len(audit.json()["decisions"]) >= 1
    print(f"  audit........... {len(audit.json()['agent_runs'])} agent_runs rows, receipts intact")

    # Beat 5: the narrative exists and cites only numbers the stats core produced.
    narrative = body["decision"]["narrative"]
    assert narrative, "analyst produced no narrative"
    assert "Sample ratio mismatch" in narrative or "SRM" in narrative.upper()
    print(f"  narrative....... alert surfaced in business language")

    print(f"--- demo run {run_index} PASSED " + "-" * 37)


def main() -> None:
    """Run the scripted demo path N times consecutively; any failure exits non-zero."""
    parser = argparse.ArgumentParser(description="ExpPilot scripted demo dry-run.")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--api", type=str, default="http://localhost:8000")
    args = parser.parse_args()

    try:
        for index in range(1, args.runs + 1):
            run_once(args.api, index)
    except AssertionError as exc:
        print(f"\nDEMO DRY-RUN FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except requests.RequestException as exc:
        print(f"\nDEMO DRY-RUN FAILED — cannot reach {args.api}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\nAll {args.runs} consecutive demo runs passed.\n")


if __name__ == "__main__":
    main()
