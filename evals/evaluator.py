"""Evaluation suite module for ExpPilot recommendations, statistical engine, and system readiness."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from agents.recommender import recommend
from agents.validator import validate_experiment
from data import db
from data.seed import ensure_seeded
from shared.models import DayStats, ExperimentConfig
from stats.core import compute_day_stats, decide


def evaluate_recommendations(gold_path: str | Path) -> dict[str, Any]:
    """Evaluate recommendation precision and recall against gold benchmark dataset."""
    gold_file = Path(gold_path)
    with open(gold_file, "r", encoding="utf-8") as f:
        benchmarks = json.load(f)

    tempdir = tempfile.TemporaryDirectory()
    orig_db = db.DB_PATH
    db.DB_PATH = Path(tempdir.name) / "eval_rec.db"
    ensure_seeded()

    try:
        total = len(benchmarks)
        cat_matches = 0
        seg_matches = 0
        flag_matches = 0
        primary_matches = 0
        guardrail_precision_sum = 0.0
        guardrail_recall_sum = 0.0

        for item in benchmarks:
            rec = recommend(item["goal"])
            
            # Category
            if rec.category == item["expected_category"]:
                cat_matches += 1
            
            # Segment
            pred_seg = rec.segment.get("segment_key") if rec.segment else None
            if pred_seg == item["expected_segment"]:
                seg_matches += 1
                
            # Flag
            pred_flag = rec.flag.get("flag_key") if rec.flag else None
            if pred_flag == item["expected_flag"]:
                flag_matches += 1

            # Primary Metric
            pred_primary = rec.primary_metric.get("metric_key") if rec.primary_metric else None
            if pred_primary == item["expected_primary_metric"]:
                primary_matches += 1

            # Guardrails (Overlap / Precision & Recall)
            pred_guardrails = set(m.get("metric_key") for m in rec.guardrail_metrics if m)
            exp_guardrails = set(item.get("expected_guardrail_metrics", []))

            intersection = pred_guardrails.intersection(exp_guardrails)
            prec = len(intersection) / len(pred_guardrails) if pred_guardrails else 1.0
            rec_val = len(intersection) / len(exp_guardrails) if exp_guardrails else 1.0
            guardrail_precision_sum += prec
            guardrail_recall_sum += rec_val

        category_accuracy = cat_matches / total if total > 0 else 0.0
        segment_accuracy = seg_matches / total if total > 0 else 0.0
        flag_accuracy = flag_matches / total if total > 0 else 0.0
        primary_metric_accuracy = primary_matches / total if total > 0 else 0.0
        guardrail_precision = guardrail_precision_sum / total if total > 0 else 0.0
        guardrail_recall = guardrail_recall_sum / total if total > 0 else 0.0

        overall_precision = (category_accuracy + segment_accuracy + flag_accuracy + primary_metric_accuracy + guardrail_precision) / 5.0
        overall_recall = (category_accuracy + segment_accuracy + flag_accuracy + primary_metric_accuracy + guardrail_recall) / 5.0

        return {
            "total_scenarios": total,
            "category_accuracy": category_accuracy,
            "segment_accuracy": segment_accuracy,
            "flag_accuracy": flag_accuracy,
            "primary_metric_accuracy": primary_metric_accuracy,
            "guardrail_precision": guardrail_precision,
            "guardrail_recall": guardrail_recall,
            "overall_precision": overall_precision,
            "overall_recall": overall_recall,
        }
    finally:
        db.DB_PATH = orig_db
        tempdir.cleanup()


def evaluate_telemetry_accuracy(telemetry_path: str | Path) -> dict[str, Any]:
    """Evaluate decision engine accuracy against synthetic telemetry scenarios."""
    telemetry_file = Path(telemetry_path)
    with open(telemetry_file, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    total = len(scenarios)
    correct_actions = 0

    # Counts for TPR, TNR, SRM rate, Guardrail rate
    tp_expected, tp_correct = 0, 0
    tn_expected, tn_correct = 0, 0
    srm_expected, srm_correct = 0, 0
    gr_expected, gr_correct = 0, 0

    for sc in scenarios:
        config = ExperimentConfig(
            id=f"exp_{sc['id']}",
            hypothesis_id="hyp_eval",
            flag_key="checkout_one_page",
            audience_segment="mobile_users",
            traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=10000,
            estimated_days=14,
            guardrail_metrics=["checkout_abandon_rate"],
            daily_traffic=2000,
            status="running",
        )
        day_stats = DayStats(
            experiment_id=config.id,
            day=sc["day"],
            control_n=sc["control_n"],
            control_conversions=sc["control_conversions"],
            treatment_n=sc["treatment_n"],
            treatment_conversions=sc["treatment_conversions"],
            guardrail_control_rate=sc["guardrail_control_rate"],
            guardrail_treatment_rate=sc["guardrail_treatment_rate"],
        )

        stats_res = compute_day_stats(day_stats, config, seed=sc["day"])
        action = decide(stats_res, config)
        expected = sc["expected_action"]

        is_correct = (action == expected)
        if is_correct:
            correct_actions += 1

        # Detailed breakdown
        if sc["scenario_type"] == "true_positive":
            tp_expected += 1
            if is_correct:
                tp_correct += 1
        elif sc["scenario_type"] == "true_negative":
            tn_expected += 1
            if is_correct:
                tn_correct += 1
        elif sc["scenario_type"] == "srm_imbalance":
            srm_expected += 1
            if is_correct:
                srm_correct += 1
        elif sc["scenario_type"] == "guardrail_breach":
            gr_expected += 1
            if is_correct:
                gr_correct += 1

    detection_accuracy = correct_actions / total if total > 0 else 0.0
    tpr = tp_correct / tp_expected if tp_expected > 0 else 1.0
    tnr = tn_correct / tn_expected if tn_expected > 0 else 1.0
    srm_rate = srm_correct / srm_expected if srm_expected > 0 else 1.0
    guardrail_rate = gr_correct / gr_expected if gr_expected > 0 else 1.0

    return {
        "total_scenarios": total,
        "detection_accuracy": detection_accuracy,
        "true_positive_rate": tpr,
        "true_negative_rate": tnr,
        "srm_detection_rate": srm_rate,
        "guardrail_detection_rate": guardrail_rate,
    }


def evaluate_configuration_acceptance_rate(gold_path: str | Path) -> dict[str, Any]:
    """Evaluate pre-launch validation configuration acceptance rate."""
    gold_file = Path(gold_path)
    with open(gold_file, "r", encoding="utf-8") as f:
        benchmarks = json.load(f)

    tempdir = tempfile.TemporaryDirectory()
    orig_db = db.DB_PATH
    db.DB_PATH = Path(tempdir.name) / "eval_accept.db"
    ensure_seeded()

    try:
        total = len(benchmarks)
        passed_count = 0

        for i, item in enumerate(benchmarks):
            rec = recommend(item["goal"])
            config = ExperimentConfig(
                id=f"exp_accept_{i}",
                hypothesis_id=f"hyp_accept_{i}",
                flag_key=rec.flag["flag_key"] if rec.flag else f"flag_accept_{i}",
                audience_segment=rec.segment["segment_key"],
                traffic_split={"control": 0.5, "treatment": 0.5},
                baseline_rate=rec.segment["baseline_conversion_rate"],
                mde=0.01,
                required_n_per_arm=10000,
                estimated_days=14,
                guardrail_metrics=[m["metric_key"] for m in rec.guardrail_metrics] or ["error_rate"],
                daily_traffic=rec.segment["daily_traffic"],
                status="draft",
            )
            report = validate_experiment(config, primary_metric_key=rec.primary_metric["metric_key"])
            if report.passed:
                passed_count += 1

        acceptance_rate = passed_count / total if total > 0 else 0.0
        return {
            "total_configs": total,
            "accepted_configs": passed_count,
            "acceptance_rate": acceptance_rate,
        }
    finally:
        db.DB_PATH = orig_db
        tempdir.cleanup()


def evaluate_creation_analysis_time_reduction() -> dict[str, Any]:
    """Benchmark creation and analysis execution speed vs manual baselines."""
    # Measure creation time
    tempdir = tempfile.TemporaryDirectory()
    orig_db = db.DB_PATH
    db.DB_PATH = Path(tempdir.name) / "eval_time.db"
    ensure_seeded()

    try:
        start_create = time.perf_counter()
        for _ in range(10):
            recommend("Improve checkout conversion for mobile users")
        create_duration = (time.perf_counter() - start_create) / 10.0

        # Measure analysis time
        config = ExperimentConfig(
            id="exp_bench", hypothesis_id="hyp_bench", flag_key="checkout_one_page",
            audience_segment="mobile_users", traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.1, mde=0.01, required_n_per_arm=10000, estimated_days=14,
            guardrail_metrics=["checkout_abandon_rate"], daily_traffic=2000, status="running",
        )
        day_stats = DayStats(
            experiment_id="exp_bench", day=7, control_n=10000, control_conversions=1000,
            treatment_n=10000, treatment_conversions=1250, guardrail_control_rate=0.01, guardrail_treatment_rate=0.01,
        )

        start_analysis = time.perf_counter()
        for _ in range(10):
            res = compute_day_stats(day_stats, config, seed=7)
            decide(res, config)
        analysis_duration = (time.perf_counter() - start_analysis) / 10.0

        manual_creation_sec = 1800.0  # 30 minutes
        manual_analysis_sec = 7200.0  # 120 minutes (2 hours)

        creation_reduction_pct = ((manual_creation_sec - create_duration) / manual_creation_sec) * 100.0
        analysis_reduction_pct = ((manual_analysis_sec - analysis_duration) / manual_analysis_sec) * 100.0

        return {
            "automated_creation_time_sec": create_duration,
            "manual_creation_time_sec": manual_creation_sec,
            "creation_time_reduction_pct": creation_reduction_pct,
            "automated_analysis_time_sec": analysis_duration,
            "manual_analysis_time_sec": manual_analysis_sec,
            "analysis_time_reduction_pct": analysis_reduction_pct,
        }
    finally:
        db.DB_PATH = orig_db
        tempdir.cleanup()


def calculate_composite_readiness_score(
    rec_metrics: dict[str, Any],
    telem_metrics: dict[str, Any],
    accept_metrics: dict[str, Any],
    time_metrics: dict[str, Any],
) -> float:
    """Calculate composite adoption readiness score (0 to 100)."""
    rec_score = rec_metrics.get("overall_precision", 0.0) * 100.0
    telem_score = telem_metrics.get("detection_accuracy", 0.0) * 100.0
    accept_score = accept_metrics.get("acceptance_rate", 0.0) * 100.0
    time_score = (time_metrics.get("creation_time_reduction_pct", 0.0) + time_metrics.get("analysis_time_reduction_pct", 0.0)) / 2.0

    # Composite weights: Rec (25%), Telem (35%), Accept (20%), Time (20%)
    composite = (rec_score * 0.25) + (telem_score * 0.35) + (accept_score * 0.20) + (time_score * 0.20)
    return round(composite, 2)
