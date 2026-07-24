"""End-to-end smoke test of the ExpPilot service layer (no web server required).

Exercises: seed -> hypotheses -> config+validation -> create -> advance-to-decide,
plus the offline eval harness. Runs on the LangGraph fallback path when langgraph
is not installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import service  # noqa: E402
from agents import graph  # noqa: E402
from evals import harness  # noqa: E402


def main() -> None:
    print("langgraph active:", graph._HAS_LANGGRAPH)
    service.ensure_ready()

    goal = "Increase prepaid recharge completion on the new mobile checkout"
    hyp = service.copilot_hypotheses(goal)
    print(f"\ncategory={hyp['category']} · {len(hyp['hypotheses'])} hypotheses · "
          f"{len(hyp['precedents'])} precedents")
    for h in hyp["hypotheses"]:
        print("  -", h["id"], "|", h["statement"][:80])

    chosen = hyp["hypotheses"][0]
    cfg = service.copilot_config(chosen, category=hyp["category"])
    print("\nconfig flag:", cfg["config"]["flag_key"],
          "n/arm:", cfg["config"]["required_n_per_arm"],
          "days:", cfg["config"]["estimated_days"])
    print("validation ok:", cfg["validation"]["ok"], "issues:", len(cfg["validation"]["issues"]))
    for i in cfg["validation"]["issues"]:
        print("   ", i["severity"], i["kind"], "-", i["detail"][:70])

    created = service.create_experiment(cfg["config"], scenario="true_lift", seed=2026)
    exp_id = created["experiment_id"]
    print("\ncreated:", exp_id)

    for _ in range(14):
        out = service.advance_experiment(exp_id)
        d = out["decision"]
        print(f"  day {out['day']:2d}: action={out['action']:9s} "
              f"P(beats)={out['stats']['prob_beats_control']:.3f} "
              f"conf={d['confidence']:.2f}")
        if out["action"] in ("scale", "stop", "rollback"):
            print("   narrative:", d["narrative"][:120])
            break

    print("\n--- demo experiments (SRM / guardrail) ---")
    for demo in ("demo_bundle_srm", "demo_paywall_guardrail"):
        for _ in range(6):
            out = service.advance_experiment(demo)
            if out["action"] in ("pause", "rollback"):
                break
        print(f"  {demo}: action={out['action']} — {out['decision']['narrative'][:90]}")

    print("\n--- eval harness ---")
    res = harness.run(seeds_per_scenario=3)
    print(f"recommendation accuracy: {res['recommendation_accuracy']*100:.1f}% "
          f"({res['correct']}/{res['n_scenarios']})")
    print(f"significance detection : {res['significance_detection_accuracy']*100:.1f}%")
    print("confusion matrix:")
    for expert, row in res["confusion_matrix"].items():
        print("  ", expert, row)


if __name__ == "__main__":
    main()
