"""Ground-truth eval harness. Runs the copilot's decision path with zero LLM calls.

Scores the deterministic monitoring loop against hidden ground-truth labels and logs
every run to MLflow: one parent run for the sweep, one nested child run per experiment.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from data.db import init_db
from data.synth import SCENARIOS, make_experiment
from shared.models import DayStats, ExperimentConfig
from stats.core import compute_day_stats, decide

N_DAYS = 14
SCALE_MIN_DAY = 5
AA_SCENARIO = "aa_null"

TARGET_OVERALL_ACCURACY = 0.85
TARGET_AA_FALSE_POSITIVE_RATE = 0.10
TARGET_SRM_DETECTION_RATE = 1.0


def _cumulative_through(day_stats: list[DayStats], day: int) -> DayStats:
    """Sum days 1..day into one cumulative DayStats, weighting guardrail rates by arm size."""
    window = day_stats[:day]
    control_n = sum(d.control_n for d in window)
    treatment_n = sum(d.treatment_n for d in window)
    return DayStats(
        experiment_id=window[0].experiment_id,
        day=day,
        control_n=control_n,
        control_conversions=sum(d.control_conversions for d in window),
        treatment_n=treatment_n,
        treatment_conversions=sum(d.treatment_conversions for d in window),
        guardrail_control_rate=(
            sum(d.guardrail_control_rate * d.control_n for d in window) / control_n if control_n else 0.0
        ),
        guardrail_treatment_rate=(
            sum(d.guardrail_treatment_rate * d.treatment_n for d in window) / treatment_n if treatment_n else 0.0
        ),
    )


def run_single(scenario: str, seed: int) -> dict:
    """Run one synthetic experiment through the decision path; return its scored result."""
    config, day_stats, ground_truth = make_experiment(scenario, seed)

    predicted_action = "continue"
    decision_day = N_DAYS
    day14_p_value = 1.0
    srm_detected = False

    for day in range(1, N_DAYS + 1):
        cumulative = _cumulative_through(day_stats, day)
        stats_result = compute_day_stats(cumulative, config, seed=0)
        action = decide(stats_result, config)

        if stats_result.srm_flag:
            srm_detected = True
        if day == N_DAYS:
            day14_p_value = stats_result.p_value

        if action != "continue" and predicted_action == "continue":
            predicted_action = action
            decision_day = day

    correct_action = ground_truth["correct_action"]
    if correct_action == "scale":
        correct = predicted_action == "scale" and decision_day >= SCALE_MIN_DAY
    else:
        correct = predicted_action == correct_action

    return {
        "scenario": scenario,
        "seed": seed,
        "predicted_action": predicted_action,
        "correct_action": correct_action,
        "decision_day": decision_day,
        "correct": bool(correct),
        "day14_p_value": day14_p_value,
        "srm_detected": srm_detected,
        "premature_scale": bool(predicted_action == "scale" and decision_day < SCALE_MIN_DAY),
    }


def run_harness(n_per_scenario: int = 6, seed_base: int = 100, aa_seeds: int = 12) -> dict:
    """Run every scenario x seed, log to MLflow, and return the summary metrics dict.

    aa_null gets `aa_seeds` seeds rather than n_per_scenario: the false-positive bar is
    <= 0.10, which 6 seeds cannot express (1/6 = 0.167 already breaches it).
    """
    init_db()
    results: list[dict] = []

    for scenario in SCENARIOS:
        count = aa_seeds if scenario == AA_SCENARIO else n_per_scenario
        for i in range(count):
            results.append(run_single(scenario, seed_base + i))

    summary = _summarize(results)
    _log_to_mlflow(results, summary, n_per_scenario, seed_base)
    return summary


def _summarize(results: list[dict]) -> dict:
    """Aggregate per-run results into the headline metrics."""
    per_scenario: dict[str, float] = {}
    for scenario in SCENARIOS:
        rows = [r for r in results if r["scenario"] == scenario]
        per_scenario[scenario] = sum(r["correct"] for r in rows) / len(rows) if rows else 0.0

    aa_rows = [r for r in results if r["scenario"] == AA_SCENARIO]
    aa_false_positives = sum(1 for r in aa_rows if r["day14_p_value"] < 0.05)

    srm_rows = [r for r in results if r["scenario"] == "srm"]
    srm_detection_rate = sum(r["srm_detected"] for r in srm_rows) / len(srm_rows) if srm_rows else 0.0

    decided = [r["decision_day"] for r in results if r["predicted_action"] != "continue"]

    return {
        "overall_accuracy": sum(r["correct"] for r in results) / len(results),
        "aa_false_positive_rate": aa_false_positives / len(aa_rows) if aa_rows else 0.0,
        "srm_detection_rate": srm_detection_rate,
        "mean_days_to_decision": sum(decided) / len(decided) if decided else float(N_DAYS),
        "per_scenario_accuracy": per_scenario,
        "n_experiments": len(results),
    }


def _log_to_mlflow(results: list[dict], summary: dict, n_per_scenario: int, seed_base: int) -> None:
    """Log the sweep as one parent run with a nested child run per experiment."""
    try:
        import mlflow
    except ImportError:
        return

    prompt_version = os.environ.get("PROMPT_VERSION", "v1")
    try:
        mlflow.set_experiment("exppilot-evals")
        with mlflow.start_run(run_name=f"eval-sweep-{prompt_version}"):
            mlflow.log_params(
                {"n_per_scenario": n_per_scenario, "seed_base": seed_base, "prompt_version": prompt_version}
            )
            mlflow.log_metrics(
                {
                    "overall_accuracy": summary["overall_accuracy"],
                    "aa_false_positive_rate": summary["aa_false_positive_rate"],
                    "srm_detection_rate": summary["srm_detection_rate"],
                    "mean_days_to_decision": summary["mean_days_to_decision"],
                }
            )
            for scenario, accuracy in summary["per_scenario_accuracy"].items():
                mlflow.log_metric(f"accuracy_{scenario}", accuracy)

            for result in results:
                with mlflow.start_run(nested=True, run_name=f"{result['scenario']}-{result['seed']}"):
                    mlflow.log_params({"scenario": result["scenario"], "seed": result["seed"]})
                    mlflow.log_metrics(
                        {"correct": int(result["correct"]), "decision_day": result["decision_day"]}
                    )
    except Exception as exc:  # MLflow must never break the harness
        print(f"[warn] MLflow logging skipped: {exc}")


def _render_report(summary: dict, results: list[dict]) -> str:
    """Render the markdown eval report."""
    lines = [
        "# ExpPilot Eval Report",
        "",
        f"Experiments scored: **{summary['n_experiments']}** (zero LLM calls — the decision path is deterministic)",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value | Target | Status |",
        "|---|---|---|---|",
    ]

    checks = [
        ("Overall accuracy", summary["overall_accuracy"], TARGET_OVERALL_ACCURACY, "≥"),
        ("A/A false-positive rate", summary["aa_false_positive_rate"], TARGET_AA_FALSE_POSITIVE_RATE, "≤"),
        ("SRM detection rate", summary["srm_detection_rate"], TARGET_SRM_DETECTION_RATE, "≥"),
    ]
    for name, value, target, direction in checks:
        met = value >= target if direction == "≥" else value <= target
        lines.append(f"| {name} | {value:.3f} | {direction} {target:.2f} | {'PASS' if met else 'FAIL'} |")

    lines += [
        f"| Mean days to decision | {summary['mean_days_to_decision']:.2f} | – | – |",
        "",
        "## Per-scenario accuracy",
        "",
        "| Scenario | Accuracy |",
        "|---|---|",
    ]
    for scenario, accuracy in summary["per_scenario_accuracy"].items():
        lines.append(f"| {scenario} | {accuracy:.3f} |")

    lines += ["", "## Individual runs", "", "| Scenario | Seed | Predicted | Expected | Day | Correct |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r['scenario']} | {r['seed']} | {r['predicted_action']} | {r['correct_action']} "
            f"| {r['decision_day']} | {'yes' if r['correct'] else 'NO'} |"
        )

    lines += ["", "Full run history in the MLflow UI: `mlflow ui` (experiment `exppilot-evals`).", ""]
    return "\n".join(lines)


def _print_table(summary: dict, results: list[dict]) -> None:
    """Print the console summary table."""
    print("\n" + "=" * 62)
    print("ExpPilot eval harness")
    print("=" * 62)
    print(f"{'scenario':<20}{'accuracy':>12}")
    print("-" * 62)
    for scenario, accuracy in summary["per_scenario_accuracy"].items():
        print(f"{scenario:<20}{accuracy:>12.3f}")
    print("-" * 62)
    print(f"{'overall_accuracy':<32}{summary['overall_accuracy']:>10.3f}  (target >= {TARGET_OVERALL_ACCURACY})")
    print(
        f"{'aa_false_positive_rate':<32}{summary['aa_false_positive_rate']:>10.3f}"
        f"  (target <= {TARGET_AA_FALSE_POSITIVE_RATE})"
    )
    print(
        f"{'srm_detection_rate':<32}{summary['srm_detection_rate']:>10.3f}"
        f"  (target == {TARGET_SRM_DETECTION_RATE})"
    )
    print(f"{'mean_days_to_decision':<32}{summary['mean_days_to_decision']:>10.2f}")
    print("=" * 62)

    bars = [
        summary["overall_accuracy"] >= TARGET_OVERALL_ACCURACY,
        summary["aa_false_positive_rate"] <= TARGET_AA_FALSE_POSITIVE_RATE,
        summary["srm_detection_rate"] >= TARGET_SRM_DETECTION_RATE,
    ]
    print("ALL TARGET BARS MET" if all(bars) else "SOME TARGET BARS NOT MET")
    print("=" * 62 + "\n")


def main() -> None:
    """CLI entry point: run the sweep, print the table, write evals/report.md."""
    parser = argparse.ArgumentParser(description="Run the ExpPilot eval harness.")
    parser.add_argument("--n-per-scenario", type=int, default=6)
    parser.add_argument("--seed-base", type=int, default=100)
    parser.add_argument("--aa-seeds", type=int, default=12)
    args = parser.parse_args()

    init_db()
    results: list[dict] = []
    for scenario in SCENARIOS:
        count = args.aa_seeds if scenario == AA_SCENARIO else args.n_per_scenario
        for i in range(count):
            results.append(run_single(scenario, args.seed_base + i))

    summary = _summarize(results)
    _log_to_mlflow(results, summary, args.n_per_scenario, args.seed_base)
    _print_table(summary, results)

    report_path = Path(__file__).resolve().parent / "report.md"
    report_path.write_text(_render_report(summary, results), encoding="utf-8")
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
