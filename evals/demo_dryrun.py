"""Scripted demo dry-run: the full keyless path, end to end, against a live API.

Usage: python -m evals.demo_dryrun [--runs 2] [--api http://localhost:8000]
Exits non-zero on the first assertion failure so it can gate a release.
"""
from __future__ import annotations

import argparse
import sys

import requests

GOAL = "increase checkout conversion on mobile"


def run_once(api: str, run_index: int) -> None:
    """Execute the full demo path once, asserting the outcome of every beat."""
    print(f"\n--- demo run {run_index} " + "-" * 44)

    # Beat 0: the platform is up and serving its registry, with no LLM key present.
    flags = requests.get(f"{api}/flags", timeout=30)
    assert flags.status_code == 200, flags.text
    assert len(flags.json()) == 20
    print("  registry........ 20 flags")

    # Beat 1: a business goal becomes hypotheses grounded in real, citable precedent.
    body = requests.post(f"{api}/copilot/hypotheses", json={"goal": GOAL}, timeout=120).json()
    hypotheses, precedents = body["hypotheses"], body["precedents"]
    assert len(hypotheses) == 3
    valid = {p["id"] for p in precedents}
    for hypothesis in hypotheses:
        assert set(hypothesis["precedent_ids"]) <= valid, "cited a precedent that does not exist"
    print(f"  hypotheses...... 3 grounded in {len(precedents)} real precedents")

    # Beat 2: the design's sample size is computed by the stats core, not proposed by a model.
    design = requests.post(
        f"{api}/copilot/config", json={"hypothesis": hypotheses[0]}, timeout=120
    ).json()
    config = design["config"]
    assert config["required_n_per_arm"] > 0
    print(
        f"  design.......... n={config['required_n_per_arm']:,}/arm, "
        f"{config['estimated_days']}d (computed by stats core)"
    )

    # Beat 3: launch the SRM scenario — the tension beat.
    created = requests.post(
        f"{api}/experiments", json={"config": config, "scenario": "srm", "seed": 42}, timeout=60
    ).json()
    experiment_id = created["experiment_id"]
    assert created["status"] == "running"
    print(f"  launch.......... {experiment_id} as scenario 'srm'")

    # Beat 4: advance to day 6. SRM must be caught and must beat a scale-worthy posterior.
    result = None
    for _ in range(5):
        result = requests.post(f"{api}/experiments/{experiment_id}/advance", timeout=120).json()

    assert result["day"] == 6, result["day"]
    assert result["stats"]["srm_flag"] is True
    assert result["action"] == "pause", result["action"]
    assert any(a["kind"] == "srm" for a in result["alerts"])
    print(
        f"  monitor......... day 6, P(beats)={result['stats']['prob_beats_control']:.4f} "
        f"but SRM p={result['stats']['srm_p_value']:.2e}"
    )
    print(f"  decision........ '{result['action']}' — trust verdict beat the posterior")

    # Beat 5: a human overrides, and the override becomes a durable lesson.
    verdict = requests.post(
        f"{api}/experiments/{experiment_id}/decisions/6/verdict",
        json={"verdict": "rejected", "reason": "assignment bug suspected, investigate first"},
        timeout=60,
    )
    assert verdict.status_code == 200, verdict.text
    lessons = requests.get(f"{api}/memory", params={"kind": "lesson"}, timeout=30).json()
    assert any(rec["source_experiment_id"] == experiment_id for rec in lessons)
    print("  learning........ rejection written to long-term memory")

    # Beat 6: the receipts are intact and reachable.
    audit = requests.get(f"{api}/experiments/{experiment_id}/audit", timeout=30).json()
    nodes_run = {r["node"] for r in audit["agent_runs"]}
    assert {"compute_stats", "monitor", "decide"} <= nodes_run, nodes_run
    assert len(audit["decisions"]) >= 1
    print(f"  audit........... {len(audit['agent_runs'])} agent_runs rows, receipts intact")

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
