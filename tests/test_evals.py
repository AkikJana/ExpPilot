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


if __name__ == "__main__":
    unittest.main()
