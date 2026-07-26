"""Unit tests for the evaluation runner and metrics calculations (evals/run_evals.py and evals/evaluator.py)."""

from __future__ import annotations

import unittest

from evals.run_evals import run_evaluation_suite


class EvalsSuiteTests(unittest.TestCase):
    def test_run_evaluation_suite_completes_and_reports_high_quality_metrics(self) -> None:
        report = run_evaluation_suite()
        self.assertEqual(report["status"], "PASS")

        # Composite score check
        composite = report["composite_adoption_readiness_score"]
        self.assertIsInstance(composite, float)
        self.assertGreater(composite, 70.0)

        # Recommendation metrics check
        rec = report["recommendation_metrics"]
        self.assertEqual(rec["total_scenarios"], 20)
        self.assertGreater(rec["overall_precision"], 0.65)
        self.assertGreater(rec["overall_recall"], 0.65)

        # Telemetry metrics check
        tel = report["telemetry_metrics"]
        self.assertEqual(tel["total_scenarios"], 30)
        self.assertGreater(tel["detection_accuracy"], 0.90)
        self.assertGreater(tel["true_positive_rate"], 0.90)
        self.assertGreater(tel["true_negative_rate"], 0.90)
        self.assertGreater(tel["srm_detection_rate"], 0.90)
        self.assertGreater(tel["guardrail_detection_rate"], 0.90)

        # Acceptance metrics check
        acc = report["acceptance_metrics"]
        self.assertEqual(acc["total_configs"], 20)
        self.assertGreater(acc["acceptance_rate"], 0.80)

        # Time reduction metrics check
        tim = report["time_reduction_metrics"]
        self.assertGreater(tim["creation_time_reduction_pct"], 99.0)
        self.assertGreater(tim["analysis_time_reduction_pct"], 99.0)

    def test_evals_suite_metric_boundary_assertions(self) -> None:
        """Adversarial test confirming strict metric boundary constraints [0.0, 1.0] and [0.0, 100.0] across all evaluation outputs."""
        report = run_evaluation_suite()

        # 1. Composite Readiness Score Boundary: [0.0, 100.0]
        composite = report["composite_adoption_readiness_score"]
        self.assertTrue(0.0 <= composite <= 100.0, f"Composite score {composite} out of bounds [0.0, 100.0]")

        # 2. Recommendation Metrics Boundaries: [0.0, 1.0]
        rec = report["recommendation_metrics"]
        for key in (
            "category_accuracy",
            "segment_accuracy",
            "flag_accuracy",
            "primary_metric_accuracy",
            "guardrail_precision",
            "guardrail_recall",
            "overall_precision",
            "overall_recall",
        ):
            val = rec[key]
            self.assertTrue(0.0 <= val <= 1.0, f"Recommendation metric '{key}'={val} out of bounds [0.0, 1.0]")
        self.assertGreater(rec["total_scenarios"], 0)

        # 3. Telemetry Metrics Boundaries: [0.0, 1.0]
        tel = report["telemetry_metrics"]
        for key in (
            "detection_accuracy",
            "true_positive_rate",
            "true_negative_rate",
            "srm_detection_rate",
            "guardrail_detection_rate",
        ):
            val = tel[key]
            self.assertTrue(0.0 <= val <= 1.0, f"Telemetry metric '{key}'={val} out of bounds [0.0, 1.0]")
        self.assertGreater(tel["total_scenarios"], 0)

        # 4. Acceptance Metrics Boundaries: [0.0, 1.0]
        acc = report["acceptance_metrics"]
        self.assertTrue(0.0 <= acc["acceptance_rate"] <= 1.0)
        self.assertTrue(0 <= acc["accepted_configs"] <= acc["total_configs"])

        # 5. Time Reduction Metrics Boundaries: [0.0, 100.0]
        tim = report["time_reduction_metrics"]
        self.assertTrue(0.0 <= tim["creation_time_reduction_pct"] <= 100.0)
        self.assertTrue(0.0 <= tim["analysis_time_reduction_pct"] <= 100.0)
        self.assertGreater(tim["automated_creation_time_sec"], 0.0)
        self.assertGreater(tim["automated_analysis_time_sec"], 0.0)

    def test_calculate_composite_readiness_score_exact_formula(self) -> None:
        """Adversarial test verifying exact composite readiness score calculation formula and weights."""
        from evals.evaluator import calculate_composite_readiness_score

        # Rec (25%), Telem (35%), Accept (20%), Time (20%)
        rec_metrics = {"overall_precision": 0.80}  # 80.0 * 0.25 = 20.0
        telem_metrics = {"detection_accuracy": 0.90}  # 90.0 * 0.35 = 31.5
        accept_metrics = {"acceptance_rate": 0.70}  # 70.0 * 0.20 = 14.0
        time_metrics = {"creation_time_reduction_pct": 90.0, "analysis_time_reduction_pct": 90.0}  # avg 90.0 * 0.20 = 18.0
        # Total expected = 20.0 + 31.5 + 14.0 + 18.0 = 83.50

        score = calculate_composite_readiness_score(rec_metrics, telem_metrics, accept_metrics, time_metrics)
        self.assertEqual(score, 83.5)

        # Lower bound: all zeroes -> 0.0
        zero_score = calculate_composite_readiness_score(
            {"overall_precision": 0.0},
            {"detection_accuracy": 0.0},
            {"acceptance_rate": 0.0},
            {"creation_time_reduction_pct": 0.0, "analysis_time_reduction_pct": 0.0},
        )
        self.assertEqual(zero_score, 0.0)

        # Upper bound: all ones / 100% -> 100.0
        max_score = calculate_composite_readiness_score(
            {"overall_precision": 1.0},
            {"detection_accuracy": 1.0},
            {"acceptance_rate": 1.0},
            {"creation_time_reduction_pct": 100.0, "analysis_time_reduction_pct": 100.0},
        )
        self.assertEqual(max_score, 100.0)

    def test_evaluator_empty_benchmark_resilience(self) -> None:
        """Adversarial test verifying evaluator functions gracefully handle empty benchmark JSON arrays without ZeroDivisionError."""
        import json
        import tempfile
        from pathlib import Path
        from evals.evaluator import evaluate_configuration_acceptance_rate, evaluate_recommendations

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump([], f)
            empty_path = Path(f.name)

        try:
            rec_res = evaluate_recommendations(empty_path)
            self.assertEqual(rec_res["total_scenarios"], 0)
            self.assertEqual(rec_res["overall_precision"], 0.0)
            self.assertEqual(rec_res["overall_recall"], 0.0)

            acc_res = evaluate_configuration_acceptance_rate(empty_path)
            self.assertEqual(acc_res["total_configs"], 0)
            self.assertEqual(acc_res["acceptance_rate"], 0.0)
        finally:
            empty_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

