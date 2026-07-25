"""Unit tests for stats/core.py (Frequentist, Bayesian, SRM, Power, Guardrails, Decision Hierarchy, mSPRT, Continuous Metrics, FDR/Bonferroni)."""

from __future__ import annotations

import math
import unittest

from shared.models import DayStats, ExperimentConfig, StatsResult
from stats.core import (
    _ready_to_call,
    bayes_decision,
    bayes_decision_continuous,
    benjamini_hochberg_correction,
    bonferroni_correction,
    compute_day_stats,
    decide,
    freq_test,
    freq_test_continuous,
    guardrail_check,
    msprt_test,
    msprt_test_continuous,
    power_analysis,
    srm_check,
)


class CoreStatsTests(unittest.TestCase):
    def test_power_analysis_sample_size_calculation(self) -> None:
        required_n = power_analysis(baseline_rate=0.10, mde=0.01, alpha=0.05, power=0.80)
        self.assertIsInstance(required_n, int)
        self.assertGreater(required_n, 0)
        # Larger MDE requires smaller sample size
        required_n_large_mde = power_analysis(baseline_rate=0.10, mde=0.02, alpha=0.05, power=0.80)
        self.assertLess(required_n_large_mde, required_n)

    def test_srm_check_balanced_and_imbalanced(self) -> None:
        # Balanced 50/50 split -> No SRM
        p_val_balanced, srm_flag_balanced = srm_check(control_n=5000, treatment_n=5000)
        self.assertFalse(srm_flag_balanced)
        self.assertGreater(p_val_balanced, 0.05)

        # Severely imbalanced split (70/30) -> SRM flag raised
        p_val_imbalanced, srm_flag_imbalanced = srm_check(control_n=7000, treatment_n=3000)
        self.assertTrue(srm_flag_imbalanced)
        self.assertLess(p_val_imbalanced, 0.001)

    def test_freq_test_z_stat_and_confidence_intervals(self) -> None:
        # Equal conversion rates -> zero z-stat and lift
        res_equal = freq_test(c_conv=1000, c_n=10000, t_conv=1000, t_n=10000)
        self.assertAlmostEqual(res_equal["z_stat"], 0.0)
        self.assertAlmostEqual(res_equal["p_value"], 1.0)
        self.assertAlmostEqual(res_equal["lift_abs"], 0.0)

        # Significant positive lift
        res_lift = freq_test(c_conv=1000, c_n=10000, t_conv=1200, t_n=10000)
        self.assertGreater(res_lift["z_stat"], 2.0)
        self.assertLess(res_lift["p_value"], 0.05)
        self.assertAlmostEqual(res_lift["lift_abs"], 0.02)
        self.assertLess(res_lift["ci_low"], res_lift["ci_high"])

    def test_bayes_decision_probabilities(self) -> None:
        # Equal performance -> prob_beats_control ~ 0.5
        bayes_equal = bayes_decision(c_conv=1000, c_n=10000, t_conv=1000, t_n=10000, seed=42, draws=10000)
        self.assertAlmostEqual(bayes_equal["prob_beats_control"], 0.5, delta=0.05)

        # Strong treatment win -> prob_beats_control near 1.0
        bayes_win = bayes_decision(c_conv=1000, c_n=10000, t_conv=1500, t_n=10000, seed=42, draws=10000)
        self.assertGreater(bayes_win["prob_beats_control"], 0.99)
        self.assertLess(bayes_win["expected_loss_ship"], 0.001)

    def test_guardrail_check_breach_detection(self) -> None:
        # Treatment rate increases guardrail beyond margin 0.01 -> breach (decrease_good)
        breach, margin = guardrail_check(c_rate=0.01, t_rate=0.03, margin=0.01, direction="decrease_good")
        self.assertTrue(breach)
        self.assertAlmostEqual(margin, 0.02)

        # Treatment rate change within margin -> no breach
        no_breach, margin_ok = guardrail_check(c_rate=0.01, t_rate=0.015, margin=0.01, direction="decrease_good")
        self.assertFalse(no_breach)
        self.assertAlmostEqual(margin_ok, 0.005)

    def test_guardrail_directionality(self) -> None:
        # 1. decrease_good: error_rate increase is BAD
        breach_dec, m_dec = guardrail_check(c_rate=0.01, t_rate=0.03, margin=0.01, metric_key="error_rate")
        self.assertTrue(breach_dec)
        self.assertAlmostEqual(m_dec, 0.02)

        # 2. increase_good: crash_free_rate DROP is BAD
        breach_inc_drop, m_inc_drop = guardrail_check(c_rate=0.99, t_rate=0.96, margin=0.01, metric_key="crash_free_rate")
        self.assertTrue(breach_inc_drop)
        self.assertAlmostEqual(m_inc_drop, 0.03)

        # 3. increase_good: crash_free_rate IMPROVEMENT is NOT a breach
        breach_inc_imp, m_inc_imp = guardrail_check(c_rate=0.99, t_rate=0.998, margin=0.01, metric_key="crash_free_rate")
        self.assertFalse(breach_inc_imp)
        self.assertLess(m_inc_imp, 0.0)

    def test_multi_guardrail_evaluation(self) -> None:
        config = ExperimentConfig(
            id="exp_multi_g",
            hypothesis_id="hyp_multi_g",
            flag_key="flag_multi_g",
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
        cumulative = DayStats(
            experiment_id="exp_multi_g",
            day=7,
            control_n=10000,
            control_conversions=1000,
            treatment_n=10000,
            treatment_conversions=1200,
            guardrail_metrics_data={
                "error_rate": {"control_rate": 0.01, "treatment_rate": 0.012},  # +0.002 <= 0.01 (ok)
                "crash_free_rate": {"control_rate": 0.99, "treatment_rate": 0.96},  # -0.03 > 0.01 (breach!)
            },
        )
        res = compute_day_stats(cumulative, config, seed=42)
        self.assertTrue(res.guardrail_breach)
        self.assertAlmostEqual(res.guardrail_margin, 0.03)

    def test_msprt_sequential_testing(self) -> None:
        # Equal groups -> high p-value, low lambda
        res_equal = msprt_test(c_conv=1000, c_n=10000, t_conv=1000, t_n=10000)
        self.assertEqual(res_equal["msprt_p_value"], 1.0)
        self.assertLess(res_equal["lambda_stat"], 1.0)
        self.assertEqual(res_equal["is_significant"], 0.0)

        # Strong treatment win -> low msprt_p_value, high lambda
        res_win = msprt_test(c_conv=1000, c_n=10000, t_conv=1300, t_n=10000)
        self.assertLess(res_win["msprt_p_value"], 0.05)
        self.assertGreater(res_win["lambda_stat"], 20.0)
        self.assertEqual(res_win["is_significant"], 1.0)

        # Continuous metric mSPRT
        res_cont = msprt_test_continuous(c_mean=100.0, c_std=15.0, c_n=5000, t_mean=105.0, t_std=15.0, t_n=5000)
        self.assertLess(res_cont["msprt_p_value"], 0.05)

    def test_continuous_metrics_models(self) -> None:
        # Frequentist continuous (Normal)
        freq_norm = freq_test(c_conv=50.0, c_n=1000, t_conv=55.0, t_n=1000, c_std=10.0, t_std=10.0)
        self.assertGreater(freq_norm["z_stat"], 2.0)
        self.assertLess(freq_norm["p_value"], 0.05)
        self.assertAlmostEqual(freq_norm["lift_abs"], 5.0)

        # Frequentist continuous (Log-Normal for latency/revenue)
        freq_log = freq_test_continuous(c_mean=4.0, c_std=0.5, c_n=1000, t_mean=4.2, t_std=0.5, t_n=1000, log_normal=True)
        self.assertIn("lift_rel", freq_log)
        self.assertGreater(freq_log["lift_rel"], 0.0)

        # Bayesian continuous
        bayes_cont = bayes_decision(c_conv=50.0, c_n=1000, t_conv=55.0, t_n=1000, c_std=10.0, t_std=10.0, seed=42)
        self.assertGreater(bayes_cont["prob_beats_control"], 0.99)

    def test_multiple_testing_corrections(self) -> None:
        p_vals = [0.001, 0.02, 0.04, 0.20]

        # Bonferroni
        adj_bonf, rejects_bonf = bonferroni_correction(p_vals, alpha=0.05)
        self.assertEqual(adj_bonf, [0.004, 0.08, 0.16, 0.80])
        self.assertEqual(rejects_bonf, [True, False, False, False])

        # Benjamini-Hochberg (FDR)
        adj_bh, rejects_bh = benjamini_hochberg_correction(p_vals, alpha=0.05)
        self.assertLessEqual(adj_bh[0], 0.05)
        self.assertTrue(rejects_bh[0])

    def test_readiness_gate(self) -> None:
        config = ExperimentConfig(
            id="exp_test",
            hypothesis_id="hyp_test",
            flag_key="flag_test",
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

        # Runtime < 7 days -> Not ready
        stats_early = StatsResult(
            experiment_id="exp_test", day=5, srm_p_value=0.5, srm_flag=False,
            z_stat=3.0, p_value=0.001, lift_abs=0.02, ci_low=0.01, ci_high=0.03,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        self.assertFalse(_ready_to_call(stats_early, config))

        # Sample size < required -> Not ready
        stats_underpowered = StatsResult(
            experiment_id="exp_test", day=7, srm_p_value=0.5, srm_flag=False,
            z_stat=3.0, p_value=0.001, lift_abs=0.02, ci_low=0.01, ci_high=0.03,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=5000, treatment_n=5000,
        )
        self.assertFalse(_ready_to_call(stats_underpowered, config))

        # Both criteria met -> Ready
        stats_ready = StatsResult(
            experiment_id="exp_test", day=7, srm_p_value=0.5, srm_flag=False,
            z_stat=3.0, p_value=0.001, lift_abs=0.02, ci_low=0.01, ci_high=0.03,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=10000, treatment_n=10000,
        )
        self.assertTrue(_ready_to_call(stats_ready, config))

    def test_decision_hierarchy_precedence(self) -> None:
        config = ExperimentConfig(
            id="exp_test",
            hypothesis_id="hyp_test",
            flag_key="flag_test",
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

        # 1. SRM flag takes absolute precedence -> "pause"
        stats_srm = StatsResult(
            experiment_id="exp_test", day=10, srm_p_value=0.0001, srm_flag=True,
            z_stat=4.0, p_value=0.0001, lift_abs=0.05, ci_low=0.03, ci_high=0.07,
            prob_beats_control=0.99, expected_loss_ship=0.0, expected_loss_keep=0.05,
            guardrail_breach=True, guardrail_margin=0.05, control_n=15000, treatment_n=5000,
        )
        self.assertEqual(decide(stats_srm, config), "pause")

        # 2. Guardrail breach takes precedence if no SRM -> "rollback"
        stats_guardrail = StatsResult(
            experiment_id="exp_test", day=10, srm_p_value=0.5, srm_flag=False,
            z_stat=4.0, p_value=0.0001, lift_abs=0.05, ci_low=0.03, ci_high=0.07,
            prob_beats_control=0.99, expected_loss_ship=0.0, expected_loss_keep=0.05,
            guardrail_breach=True, guardrail_margin=0.05, control_n=15000, treatment_n=15000,
        )
        self.assertEqual(decide(stats_guardrail, config), "rollback")

        # 3. Not ready -> "continue"
        stats_not_ready = StatsResult(
            experiment_id="exp_test", day=4, srm_p_value=0.5, srm_flag=False,
            z_stat=4.0, p_value=0.0001, lift_abs=0.05, ci_low=0.03, ci_high=0.07,
            prob_beats_control=0.99, expected_loss_ship=0.0, expected_loss_keep=0.05,
            guardrail_breach=False, guardrail_margin=0.0, control_n=15000, treatment_n=15000,
        )
        self.assertEqual(decide(stats_not_ready, config), "continue")

        # 4. Ready + high win prob + low loss -> "scale"
        stats_scale = StatsResult(
            experiment_id="exp_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=3.5, p_value=0.0005, lift_abs=0.02, ci_low=0.01, ci_high=0.03,
            prob_beats_control=0.98, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0, control_n=12000, treatment_n=12000,
        )
        self.assertEqual(decide(stats_scale, config), "scale")

        # 5. Ready + low win prob -> "stop"
        stats_stop = StatsResult(
            experiment_id="exp_test", day=8, srm_p_value=0.5, srm_flag=False,
            z_stat=-3.5, p_value=0.0005, lift_abs=-0.02, ci_low=-0.03, ci_high=-0.01,
            prob_beats_control=0.02, expected_loss_ship=0.02, expected_loss_keep=0.001,
            guardrail_breach=False, guardrail_margin=0.0, control_n=12000, treatment_n=12000,
        )
        self.assertEqual(decide(stats_stop, config), "stop")

    def test_compute_day_stats_integration(self) -> None:
        config = ExperimentConfig(
            id="exp_full",
            hypothesis_id="hyp_full",
            flag_key="flag_full",
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
        cumulative = DayStats(
            experiment_id="exp_full",
            day=7,
            control_n=10000,
            control_conversions=1000,
            treatment_n=10000,
            treatment_conversions=1200,
            guardrail_control_rate=0.01,
            guardrail_treatment_rate=0.01,
        )
        res = compute_day_stats(cumulative, config, seed=42)
        self.assertEqual(res.experiment_id, "exp_full")
        self.assertEqual(res.day, 7)
        self.assertFalse(res.srm_flag)
        self.assertAlmostEqual(res.lift_abs, 0.02)
        self.assertGreater(res.prob_beats_control, 0.95)


if __name__ == "__main__":
    unittest.main()
