#!/usr/bin/env python3
"""CLI runner for ExpPilot Evaluation Suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure root directory is on pythonpath
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from evals.evaluator import (
    calculate_composite_readiness_score,
    evaluate_configuration_acceptance_rate,
    evaluate_creation_analysis_time_reduction,
    evaluate_recommendations,
    evaluate_telemetry_accuracy,
)


def run_evaluation_suite(
    gold_path: Path | None = None,
    telemetry_path: Path | None = None,
) -> dict:
    gold = gold_path or (ROOT_DIR / "evals" / "benchmarks" / "gold_recommendations.json")
    telemetry = telemetry_path or (ROOT_DIR / "evals" / "benchmarks" / "telemetry_scenarios.json")

    if not gold.exists():
        raise FileNotFoundError(f"Gold recommendations benchmark file not found: {gold}")
    if not telemetry.exists():
        raise FileNotFoundError(f"Telemetry scenarios benchmark file not found: {telemetry}")

    rec_metrics = evaluate_recommendations(gold)
    telem_metrics = evaluate_telemetry_accuracy(telemetry)
    accept_metrics = evaluate_configuration_acceptance_rate(gold)
    time_metrics = evaluate_creation_analysis_time_reduction()

    composite_score = calculate_composite_readiness_score(
        rec_metrics, telem_metrics, accept_metrics, time_metrics
    )

    report = {
        "status": "PASS",
        "composite_adoption_readiness_score": composite_score,
        "recommendation_metrics": rec_metrics,
        "telemetry_metrics": telem_metrics,
        "acceptance_metrics": accept_metrics,
        "time_reduction_metrics": time_metrics,
    }
    return report


def print_summary(report: dict) -> None:
    print("==================================================================")
    print("                EXPILOT EVALUATION SUITE REPORT                   ")
    print("==================================================================")
    print(f"Status:                             {report['status']}")
    print(f"Composite Adoption Readiness Score: {report['composite_adoption_readiness_score']} / 100")
    print("------------------------------------------------------------------")
    print("1. Recommendation Performance vs Gold Benchmark:")
    rec = report["recommendation_metrics"]
    print(f"   - Scenarios Evaluated:           {rec['total_scenarios']}")
    print(f"   - Category Accuracy:             {rec['category_accuracy'] * 100:.1f}%")
    print(f"   - Segment Accuracy:              {rec['segment_accuracy'] * 100:.1f}%")
    print(f"   - Flag Accuracy:                 {rec['flag_accuracy'] * 100:.1f}%")
    print(f"   - Primary Metric Accuracy:       {rec['primary_metric_accuracy'] * 100:.1f}%")
    print(f"   - Guardrail Precision / Recall:  {rec['guardrail_precision'] * 100:.1f}% / {rec['guardrail_recall'] * 100:.1f}%")
    print(f"   - Overall Precision / Recall:    {rec['overall_precision'] * 100:.1f}% / {rec['overall_recall'] * 100:.1f}%")
    print("------------------------------------------------------------------")
    print("2. Statistical Significance Detection Accuracy:")
    tel = report["telemetry_metrics"]
    print(f"   - Telemetry Scenarios Evaluated: {tel['total_scenarios']}")
    print(f"   - Detection Accuracy:            {tel['detection_accuracy'] * 100:.1f}%")
    print(f"   - True Positive Rate (TPR):      {tel['true_positive_rate'] * 100:.1f}%")
    print(f"   - True Negative Rate (TNR):      {tel['true_negative_rate'] * 100:.1f}%")
    print(f"   - SRM Detection Rate:            {tel['srm_detection_rate'] * 100:.1f}%")
    print(f"   - Guardrail Breach Rate:         {tel['guardrail_detection_rate'] * 100:.1f}%")
    print("------------------------------------------------------------------")
    print("3. Pre-Launch Configuration Acceptance:")
    acc = report["acceptance_metrics"]
    print(f"   - Configurations Evaluated:      {acc['total_configs']}")
    print(f"   - Configuration Acceptance Rate: {acc['acceptance_rate'] * 100:.1f}%")
    print("------------------------------------------------------------------")
    print("4. Creation & Analysis Time Reduction:")
    tim = report["time_reduction_metrics"]
    print(f"   - Experiment Creation Time:      {tim['automated_creation_time_sec'] * 1000:.2f} ms (Manual: {tim['manual_creation_time_sec'] / 60:.0f} min -> {tim['creation_time_reduction_pct']:.2f}% reduction)")
    print(f"   - Telemetry Analysis Time:      {tim['automated_analysis_time_sec'] * 1000:.2f} ms (Manual: {tim['manual_analysis_time_sec'] / 60:.0f} min -> {tim['analysis_time_reduction_pct']:.2f}% reduction)")
    print("==================================================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ExpPilot Evals Suite")
    parser.add_argument("--gold", type=Path, default=None, help="Path to gold recommendations benchmark JSON")
    parser.add_argument("--telemetry", type=Path, default=None, help="Path to telemetry scenarios benchmark JSON")
    parser.add_argument("--json", action="store_true", help="Print report as raw JSON")
    args = parser.parse_args()

    report = run_evaluation_suite(args.gold, args.telemetry)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_summary(report)


if __name__ == "__main__":
    main()
