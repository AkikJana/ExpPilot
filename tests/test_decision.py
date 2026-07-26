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

    def test_adversarial_conflicting_flags_and_override_hierarchy(self) -> None:
        """Adversarial Test: Multiple conflicting signals verify exact precedence order.
        Hierarchy: 1. SRM > 2. Guardrail > 3. Unready > 4. Bayes Win > 5. Bayes Loss > 6. Inconclusive
        """
        # Scenario A: SRM + Guardrail + Bayes Win -> SRM wins (Pause)
        stats_all_true = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.0001, srm_flag=True,
            z_stat=5.0, p_value=0.0001, lift_abs=0.10, ci_low=0.08, ci_high=0.12,
            prob_beats_control=0.999, expected_loss_ship=0.0, expected_loss_keep=0.10,
            guardrail_breach=True, guardrail_margin=0.05, control_n=20000, treatment_n=5000,
        )
        rec_a = evaluate_decision(stats_all_true, self.config)
        self.assertIn(rec_a.action, ("Pause", "Rollback"))
        self.assertTrue(rec_a.risk_assessment["srm_info"]["srm_flag"])

        # Scenario B: No SRM + Guardrail + Bayes Win + Day 2 (unready) -> Guardrail wins over unready (Rollback)
        stats_guardrail_unready = StatsResult(
            experiment_id="exp_rule_test", day=2, srm_p_value=0.5, srm_flag=False,
            z_stat=4.0, p_value=0.0001, lift_abs=0.05, ci_low=0.03, ci_high=0.07,
            prob_beats_control=0.99, expected_loss_ship=0.0, expected_loss_keep=0.05,
            guardrail_breach=True, guardrail_margin=0.04, control_n=15000, treatment_n=15000,
        )
        rec_b = evaluate_decision(stats_guardrail_unready, self.config)
        self.assertEqual(rec_b.action, "Rollback")

        # Scenario C: No SRM + Guardrail + Bayes Win + Day 10 (ready) -> Guardrail wins over Scale (Rollback)
        stats_guardrail_win = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=4.0, p_value=0.0001, lift_abs=0.05, ci_low=0.03, ci_high=0.07,
            prob_beats_control=0.99, expected_loss_ship=0.0, expected_loss_keep=0.05,
            guardrail_breach=True, guardrail_margin=0.04, control_n=15000, treatment_n=15000,
        )
        rec_c = evaluate_decision(stats_guardrail_win, self.config)
        self.assertEqual(rec_c.action, "Rollback")

    def test_adversarial_boundary_thresholds(self) -> None:
        """Adversarial Test: Strict boundary checks for Bayes decision thresholds."""
        # Exact win threshold: prob_beats_control = 0.95, expected_loss_ship = 0.0025 -> Scale
        stats_win_exact = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=2.0, p_value=0.04, lift_abs=0.02, ci_low=0.001, ci_high=0.039,
            prob_beats_control=0.95, expected_loss_ship=0.0025, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_win_exact = evaluate_decision(stats_win_exact, self.config)
        self.assertEqual(rec_win_exact.action, "Scale")

        # Just below win prob: prob_beats_control = 0.9499 -> Continue
        stats_win_below = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=1.9, p_value=0.05, lift_abs=0.019, ci_low=0.0, ci_high=0.038,
            prob_beats_control=0.9499, expected_loss_ship=0.0025, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_win_below = evaluate_decision(stats_win_below, self.config)
        self.assertEqual(rec_win_below.action, "Continue")

        # Win prob high but loss slightly exceeds epsilon: expected_loss_ship = 0.0026 -> Continue
        stats_loss_high = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=2.0, p_value=0.04, lift_abs=0.02, ci_low=0.001, ci_high=0.039,
            prob_beats_control=0.98, expected_loss_ship=0.0026, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_loss_high = evaluate_decision(stats_loss_high, self.config)
        self.assertEqual(rec_loss_high.action, "Continue")

        # Exact loss threshold: prob_beats_control = 0.05 -> Stop
        stats_loss_exact = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=-2.0, p_value=0.04, lift_abs=-0.02, ci_low=-0.039, ci_high=-0.001,
            prob_beats_control=0.05, expected_loss_ship=0.02, expected_loss_keep=0.001,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_loss_exact = evaluate_decision(stats_loss_exact, self.config)
        self.assertEqual(rec_loss_exact.action, "Stop")

    def test_adversarial_missing_config_and_dict_formats(self) -> None:
        """Adversarial Test: Evaluate decision when config is None, empty dict, or dictionary."""
        stats = StatsResult(
            experiment_id="exp_rule_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=2.0, p_value=0.04, lift_abs=0.02, ci_low=0.001, ci_high=0.039,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        # None config
        rec_none = evaluate_decision(stats, None)
        self.assertEqual(rec_none.action, "Scale")

        # Empty dict config
        rec_empty_dict = evaluate_decision(stats, {})
        self.assertEqual(rec_empty_dict.action, "Scale")

        # Dict config with required_n_per_arm
        self.assertEqual(rec_dict.action, "Continue")


class AdversarialDecisionBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ExperimentConfig(
            id="exp_bnd_test",
            hypothesis_id="hyp_bnd_test",
            flag_key="flag_bnd_test",
            audience_segment="mobile_users",
            traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=10000,
            estimated_days=14,
            guardrail_metrics=["error_rate"],
            daily_traffic=2000,
            status="running",
        )

    def test_border_probabilities_bayes_win(self) -> None:
        """Adversarial: Exact boundary checks around prob_beats_control (0.949 vs 0.950 vs 0.951) and expected_loss_ship (0.00249 vs 0.00251)."""
        # Exactly 0.950 win prob, 0.0025 loss -> Scale
        stats_exact = StatsResult(
            experiment_id="exp_bnd_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=2.0, p_value=0.04, lift_abs=0.02, ci_low=0.001, ci_high=0.039,
            prob_beats_control=0.950, expected_loss_ship=0.0025, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_exact = evaluate_decision(stats_exact, self.config)
        self.assertEqual(rec_exact.action, "Scale")

        # 0.949 win prob -> Continue
        stats_below_win = StatsResult(
            experiment_id="exp_bnd_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=1.9, p_value=0.05, lift_abs=0.019, ci_low=0.0, ci_high=0.038,
            prob_beats_control=0.949, expected_loss_ship=0.0025, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_below_win = evaluate_decision(stats_below_win, self.config)
        self.assertEqual(rec_below_win.action, "Continue")

        # 0.951 win prob but loss = 0.00251 (> 0.0025) -> Continue
        stats_high_loss = StatsResult(
            experiment_id="exp_bnd_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=2.1, p_value=0.03, lift_abs=0.021, ci_low=0.002, ci_high=0.04,
            prob_beats_control=0.951, expected_loss_ship=0.00251, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_high_loss = evaluate_decision(stats_high_loss, self.config)
        self.assertEqual(rec_high_loss.action, "Continue")

    def test_border_probabilities_bayes_loss(self) -> None:
        """Adversarial: Exact boundary checks around prob_beats_control (0.049 vs 0.050 vs 0.051)."""
        # Exactly 0.050 win prob -> Stop
        stats_exact_loss = StatsResult(
            experiment_id="exp_bnd_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=-2.0, p_value=0.04, lift_abs=-0.02, ci_low=-0.039, ci_high=-0.001,
            prob_beats_control=0.050, expected_loss_ship=0.02, expected_loss_keep=0.001,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_exact_loss = evaluate_decision(stats_exact_loss, self.config)
        self.assertEqual(rec_exact_loss.action, "Stop")

        # 0.049 win prob -> Stop
        stats_below_loss = StatsResult(
            experiment_id="exp_bnd_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=-2.1, p_value=0.03, lift_abs=-0.021, ci_low=-0.04, ci_high=-0.002,
            prob_beats_control=0.049, expected_loss_ship=0.02, expected_loss_keep=0.001,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_below_loss = evaluate_decision(stats_below_loss, self.config)
        self.assertEqual(rec_below_loss.action, "Stop")

        # 0.051 win prob -> Continue
        stats_above_loss = StatsResult(
            experiment_id="exp_bnd_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=-1.9, p_value=0.05, lift_abs=-0.019, ci_low=-0.038, ci_high=0.0,
            prob_beats_control=0.051, expected_loss_ship=0.019, expected_loss_keep=0.001,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_above_loss = evaluate_decision(stats_above_loss, self.config)
        self.assertEqual(rec_above_loss.action, "Continue")

    def test_readiness_border_days_and_samples(self) -> None:
        """Adversarial: Day 6 vs Day 7 (MIN_RUNTIME_DAYS=7) and required_n boundary (9999 vs 10000)."""
        # Day 6 (unready) vs Day 7 (ready) with high win prob
        stats_base = dict(
            experiment_id="exp_bnd_test", srm_p_value=0.5, srm_flag=False,
            z_stat=3.0, p_value=0.001, lift_abs=0.03, ci_low=0.01, ci_high=0.05,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.03,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        rec_day6 = evaluate_decision(StatsResult(day=6, **stats_base), self.config)
        self.assertEqual(rec_day6.action, "Continue")

        rec_day7 = evaluate_decision(StatsResult(day=7, **stats_base), self.config)
        self.assertEqual(rec_day7.action, "Scale")

        # N=9999 (unready) vs N=10000 (ready)
        rec_n9999 = evaluate_decision(StatsResult(day=8, **dict(stats_base, control_n=9999, treatment_n=15000)), self.config)
        self.assertEqual(rec_n9999.action, "Continue")

        rec_n10000 = evaluate_decision(StatsResult(day=8, **dict(stats_base, control_n=10000, treatment_n=10000)), self.config)
        self.assertEqual(rec_n10000.action, "Scale")


class AdversarialDecisionPrecedenceTests(unittest.TestCase):
    def test_multi_guardrail_breach_reporting(self) -> None:
        """Adversarial: Multi-guardrail breach reports affected metrics in risk assessment."""
        config_multi = ExperimentConfig(
            id="exp_multi_g", hypothesis_id="hyp_multi_g", flag_key="flag_multi_g",
            audience_segment="all", traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10, mde=0.01, required_n_per_arm=10000, estimated_days=14,
            guardrail_metrics=["error_rate", "crash_free_rate", "latency_p95_ms"],
            daily_traffic=2000, status="running",
        )
        stats_breach = StatsResult(
            experiment_id="exp_multi_g", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=1.0, p_value=0.30, lift_abs=0.01, ci_low=-0.01, ci_high=0.03,
            prob_beats_control=0.80, expected_loss_ship=0.005, expected_loss_keep=0.01,
            guardrail_breach=True, guardrail_margin=0.04, control_n=15000, treatment_n=15000,
        )
        rec = evaluate_decision(stats_breach, config_multi)
        self.assertEqual(rec.action, "Rollback")
        self.assertEqual(rec.risk_assessment["affected_metrics"], ["error_rate", "crash_free_rate", "latency_p95_ms"])

    def test_srm_overrides_everything(self) -> None:
        """Adversarial: SRM flag overrides guardrail breach, unready status, and scale/stop signals simultaneously."""
        stats_srm_everything = StatsResult(
            experiment_id="exp_srm_all", day=2, srm_p_value=0.00001, srm_flag=True,
            z_stat=10.0, p_value=0.00001, lift_abs=0.20, ci_low=0.15, ci_high=0.25,
            prob_beats_control=0.999, expected_loss_ship=0.0, expected_loss_keep=0.20,
            guardrail_breach=True, guardrail_margin=0.10, control_n=5000, treatment_n=1000,
        )
        rec = evaluate_decision(stats_srm_everything)
        self.assertEqual(rec.action, "Pause")
        self.assertEqual(rec.confidence_score, 1.0)


class AdversarialDecisionConfigFlexibilityTests(unittest.TestCase):
    def test_decision_with_hypothesis_spec(self) -> None:
        """Adversarial: evaluate_decision accepts HypothesisSpec instance."""
        hyp = HypothesisSpec(
            id="hyp_123",
            name="Test Hyp",
            description="Hypothesis spec test",
            metric="conversion_rate",
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=10000,
            guardrail_metrics=["error_rate"],
        )
        stats = StatsResult(
            experiment_id="exp_hyp", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=3.0, p_value=0.001, lift_abs=0.02, ci_low=0.01, ci_high=0.03,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=12000, treatment_n=12000,
        )
        rec = evaluate_decision(stats, hyp)
        self.assertEqual(rec.action, "Scale")

    def test_decision_with_none_and_empty_config(self) -> None:
        """Adversarial: evaluate_decision handles config=None and config={} without raising errors."""
        stats = StatsResult(
            experiment_id="exp_none", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=3.0, p_value=0.001, lift_abs=0.02, ci_low=0.01, ci_high=0.03,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=12000, treatment_n=12000,
        )
        rec_none = evaluate_decision(stats, None)
        self.assertEqual(rec_none.action, "Scale")

        rec_empty = evaluate_decision(stats, {})
        self.assertEqual(rec_empty.action, "Scale")


if __name__ == "__main__":
    unittest.main()


