"""Unit tests for rules_engine/decision.py (Decision Recommendation Engine)."""

from __future__ import annotations

import unittest

from rules_engine.decision import evaluate_decision
from shared.models import (
    DayStats,
    Decision,
    DecisionRecommendation,
    ExperimentConfig,
    StatsResult,
)


class DecisionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ExperimentConfig(
            id="exp_rule_test",
            hypothesis_id="hyp_rule_test",
            flag_key="flag_rule_test",
            audience_segment="mobile_users",
            traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=10000,
            estimated_days=14,
            guardrail_metrics=["error_rate", "crash_free_rate"],
            daily_traffic=2000,
            status="running",
        )

    def test_step_1_srm_check_precedence(self) -> None:
        """Step 1: SRM flag raises Pause/Rollback with high severity SRM risk assessment."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=10,
            srm_p_value=0.0001,
            srm_flag=True,
            z_stat=4.0,
            p_value=0.0001,
            lift_abs=0.05,
            ci_low=0.03,
            ci_high=0.07,
            prob_beats_control=0.99,
            expected_loss_ship=0.0,
            expected_loss_keep=0.05,
            guardrail_breach=True,  # Guardrail breach also set, but SRM takes precedence
            guardrail_margin=0.05,
            control_n=15000,
            treatment_n=5000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertIsInstance(rec, DecisionRecommendation)
        self.assertIn(rec.action, ("Pause", "Rollback"))
        self.assertEqual(rec.confidence_score, 1.0)
        self.assertEqual(rec.risk_assessment["risk_level"], "high")
        self.assertTrue(rec.risk_assessment["srm_info"]["srm_flag"])
        self.assertIn("Sample Ratio Mismatch", rec.explainable_summary)

    def test_step_2_guardrail_breach_precedence(self) -> None:
        """Step 2: Guardrail breach recommends Rollback with guardrail details and margin drop."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=10,
            srm_p_value=0.5,
            srm_flag=False,
            z_stat=4.0,
            p_value=0.0001,
            lift_abs=0.05,
            ci_low=0.03,
            ci_high=0.07,
            prob_beats_control=0.99,
            expected_loss_ship=0.0,
            expected_loss_keep=0.05,
            guardrail_breach=True,
            guardrail_margin=0.03,
            control_n=15000,
            treatment_n=15000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertEqual(rec.action, "Rollback")
        self.assertEqual(rec.confidence_score, 1.0)
        self.assertEqual(rec.risk_assessment["risk_level"], "high")
        self.assertAlmostEqual(rec.risk_assessment["margin_drop"], 0.03)
        self.assertIn("Guardrail metric breach", rec.explainable_summary)

    def test_step_3_unready_runtime_gate(self) -> None:
        """Step 3: Runtime < 7 days recommends Continue with progress ratio."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=4,  # Runtime < 7 days
            srm_p_value=0.5,
            srm_flag=False,
            z_stat=3.5,
            p_value=0.0005,
            lift_abs=0.03,
            ci_low=0.01,
            ci_high=0.05,
            prob_beats_control=0.98,
            expected_loss_ship=0.001,
            expected_loss_keep=0.03,
            guardrail_breach=False,
            guardrail_margin=0.0,
            control_n=12000,
            treatment_n=12000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertEqual(rec.action, "Continue")
        self.assertGreater(rec.confidence_score, 0.0)
        self.assertLessEqual(rec.confidence_score, 1.0)
        self.assertIn("progress", rec.risk_assessment)
        self.assertIn("Day 4/7", rec.explainable_summary)

    def test_step_3_unready_sample_size_gate(self) -> None:
        """Step 3: Sample size < required recommends Continue with sample progress ratio."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=8,  # Runtime >= 7 days
            srm_p_value=0.5,
            srm_flag=False,
            z_stat=3.5,
            p_value=0.0005,
            lift_abs=0.03,
            ci_low=0.01,
            ci_high=0.05,
            prob_beats_control=0.98,
            expected_loss_ship=0.001,
            expected_loss_keep=0.03,
            guardrail_breach=False,
            guardrail_margin=0.0,
            control_n=5000,  # required = 10000 per arm
            treatment_n=5000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertEqual(rec.action, "Continue")
        self.assertAlmostEqual(rec.confidence_score, 0.5, delta=0.05)
        self.assertIn("progress", rec.risk_assessment)

    def test_step_4_bayes_win_recommends_scale(self) -> None:
        """Step 4: Ready + prob_beats_control >= 0.95 and expected_loss <= 0.0025 recommends Scale."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=8,
            srm_p_value=0.5,
            srm_flag=False,
            z_stat=3.5,
            p_value=0.0005,
            lift_abs=0.025,
            ci_low=0.01,
            ci_high=0.04,
            prob_beats_control=0.98,
            expected_loss_ship=0.001,
            expected_loss_keep=0.03,
            guardrail_breach=False,
            guardrail_margin=0.0,
            control_n=12000,
            treatment_n=12000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertEqual(rec.action, "Scale")
        self.assertEqual(rec.confidence_score, 0.98)
        self.assertEqual(rec.risk_assessment["risk_level"], "low")
        self.assertEqual(rec.risk_assessment["posterior_win_probability"], 0.98)
        self.assertIn("scaling treatment to 100%", rec.explainable_summary)

    def test_step_5_bayes_loss_recommends_stop(self) -> None:
        """Step 5: Ready + prob_beats_control <= 0.05 recommends Stop with posterior loss probability."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=8,
            srm_p_value=0.5,
            srm_flag=False,
            z_stat=-3.5,
            p_value=0.0005,
            lift_abs=-0.025,
            ci_low=-0.04,
            ci_high=-0.01,
            prob_beats_control=0.02,
            expected_loss_ship=0.03,
            expected_loss_keep=0.001,
            guardrail_breach=False,
            guardrail_margin=0.0,
            control_n=12000,
            treatment_n=12000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertEqual(rec.action, "Stop")
        self.assertAlmostEqual(rec.confidence_score, 0.98)
        self.assertEqual(rec.risk_assessment["posterior_loss_probability"], 0.98)
        self.assertIn("stopping the experiment", rec.explainable_summary)

    def test_step_6_inconclusive_recommends_continue(self) -> None:
        """Step 6: Ready but inconclusive results recommends Continue."""
        stats = StatsResult(
            experiment_id="exp_rule_test",
            day=8,
            srm_p_value=0.5,
            srm_flag=False,
            z_stat=1.2,
            p_value=0.23,
            lift_abs=0.008,
            ci_low=-0.005,
            ci_high=0.02,
            prob_beats_control=0.75,
            expected_loss_ship=0.005,
            expected_loss_keep=0.01,
            guardrail_breach=False,
            guardrail_margin=0.0,
            control_n=12000,
            treatment_n=12000,
        )
        rec = evaluate_decision(stats, self.config)
        self.assertEqual(rec.action, "Continue")
        self.assertEqual(rec.confidence_score, 0.75)
        self.assertIn("inconclusive", rec.explainable_summary)

    def test_decision_recommendation_model_contracts(self) -> None:
        """Test DecisionRecommendation title-casing, action_code, and confidence clamping."""
        rec_lower = DecisionRecommendation(
            action="scale",  # lowercase passed
            confidence_score=1.5,  # out of bounds, clamped
            risk_assessment={"risk_level": "low"},
            explainable_summary="Test summary",
        )
        self.assertEqual(rec_lower.action, "Scale")
        self.assertEqual(rec_lower.action_code, "scale")
        self.assertEqual(rec_lower.confidence_score, 1.0)

        decision_wrapper = Decision(
            experiment_id="exp_wrap",
            day=7,
            action="scale",
            confidence=0.95,
            reasoning_stats=StatsResult(
                experiment_id="exp_wrap",
                day=7,
                srm_p_value=0.5,
                srm_flag=False,
                z_stat=2.0,
                p_value=0.04,
                lift_abs=0.01,
                ci_low=0.001,
                ci_high=0.02,
                prob_beats_control=0.96,
                expected_loss_ship=0.001,
                expected_loss_keep=0.01,
                guardrail_breach=False,
                guardrail_margin=0.0,
            ),
            narrative="Test narrative",
            requires_human=True,
            human_verdict="pending",
            human_reason=None,
            recommendation=rec_lower,
        )
        self.assertIsNotNone(decision_wrapper.recommendation)
        self.assertEqual(decision_wrapper.recommendation.action, "Scale")
        self.assertEqual(decision_wrapper.action_code, "scale")


if __name__ == "__main__":
    unittest.main()
