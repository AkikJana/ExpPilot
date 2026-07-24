"""Evaluation harness: recommendation accuracy + significance-detection accuracy.

Runs the deterministic decision engine over the labeled gold set and reports a
confusion matrix and headline accuracies. Results are logged to MLflow when
available (best-effort; never blocks the run).
"""
from __future__ import annotations

from pathlib import Path

from evals.gold import build_gold_set
from stats.core import compute_day_stats, decide

ACTIONS = ["scale", "continue", "stop", "rollback", "pause"]
_REPORT_PATH = Path(__file__).resolve().parent / "report.md"


def run(seeds_per_scenario: int = 3, write_report: bool = True) -> dict:
    """Evaluate the copilot's decisions vs expert labels and return a metrics dict."""
    gold = build_gold_set(seeds_per_scenario=seeds_per_scenario)

    correct = 0
    rows: list[dict] = []
    confusion = {a: {b: 0 for b in ACTIONS} for a in ACTIONS}

    # Significance-detection accuracy: does the frequentist test agree with truth?
    sig_correct = sig_total = 0

    for case in gold:
        stats = compute_day_stats(case["final_cumulative"], case["config"], seed=0)
        predicted = decide(stats, case["config"])
        expected = case["expected_action"]
        is_correct = predicted == expected
        correct += int(is_correct)
        confusion[expected][predicted] += 1

        # significance ground truth: true_lift/guardrail_breach have a real effect;
        # aa_null has none. underpowered is intentionally ambiguous -> skip.
        truth_has_effect = None
        if case["scenario"] in ("true_lift", "guardrail_breach"):
            truth_has_effect = True
        elif case["scenario"] == "aa_null":
            truth_has_effect = False
        if truth_has_effect is not None and not stats.srm_flag:
            detected = stats.p_value < 0.05
            sig_total += 1
            sig_correct += int(detected == truth_has_effect)

        rows.append(
            {
                "id": case["id"],
                "scenario": case["scenario"],
                "expected": expected,
                "predicted": predicted,
                "correct": is_correct,
                "prob_beats_control": round(stats.prob_beats_control, 4),
                "p_value": round(stats.p_value, 5),
                "srm_flag": stats.srm_flag,
                "guardrail_breach": stats.guardrail_breach,
            }
        )

    total = len(gold)
    accuracy = round(correct / total, 4) if total else 0.0
    sig_accuracy = round(sig_correct / sig_total, 4) if sig_total else None

    result = {
        "n_scenarios": total,
        "correct": correct,
        "recommendation_accuracy": accuracy,
        "significance_detection_accuracy": sig_accuracy,
        "confusion_matrix": confusion,
        "rows": rows,
    }

    _log_mlflow(result)
    if write_report:
        try:
            _write_report(result)
            result["report_path"] = str(_REPORT_PATH)
        except Exception:
            pass
    return result


def _log_mlflow(result: dict) -> None:
    try:
        import mlflow

        mlflow.set_experiment("exppilot-decision-accuracy")
        with mlflow.start_run():
            mlflow.log_metric("recommendation_accuracy", result["recommendation_accuracy"])
            if result["significance_detection_accuracy"] is not None:
                mlflow.log_metric(
                    "significance_detection_accuracy", result["significance_detection_accuracy"]
                )
            mlflow.log_metric("n_scenarios", result["n_scenarios"])
    except Exception:
        pass


def _write_report(result: dict) -> None:
    lines = [
        "# ExpPilot Decision-Accuracy Report",
        "",
        f"- Scenarios: **{result['n_scenarios']}**",
        f"- Recommendation accuracy vs expert: **{result['recommendation_accuracy'] * 100:.1f}%** "
        f"({result['correct']}/{result['n_scenarios']})",
    ]
    if result["significance_detection_accuracy"] is not None:
        lines.append(
            f"- Significance-detection accuracy: **{result['significance_detection_accuracy'] * 100:.1f}%**"
        )
    lines += ["", "## Confusion matrix (rows = expert, cols = copilot)", ""]
    header = "| expert \\ copilot | " + " | ".join(ACTIONS) + " |"
    sep = "| --- | " + " | ".join("---" for _ in ACTIONS) + " |"
    lines += [header, sep]
    for a in ACTIONS:
        lines.append("| " + a + " | " + " | ".join(str(result["confusion_matrix"][a][b]) for b in ACTIONS) + " |")
    lines += ["", "## Per-scenario", "", "| id | expected | predicted | correct | p_value | srm |", "| --- | --- | --- | --- | --- | --- |"]
    for r in result["rows"]:
        lines.append(
            f"| {r['id']} | {r['expected']} | {r['predicted']} | "
            f"{'✅' if r['correct'] else '❌'} | {r['p_value']} | {r['srm_flag']} |"
        )
    _REPORT_PATH.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    out = run()
    print(f"Recommendation accuracy: {out['recommendation_accuracy'] * 100:.1f}% "
          f"({out['correct']}/{out['n_scenarios']})")
    if out["significance_detection_accuracy"] is not None:
        print(f"Significance-detection accuracy: {out['significance_detection_accuracy'] * 100:.1f}%")
