"""Unit tests for stats/core.py (Frequentist, Bayesian, SRM, Power, Guardrails, Decision Hierarchy, mSPRT, Continuous Metrics, FDR/Bonferroni)."""

from __future__ import annotations

import math
import unittest

from shared.models import DayStats, ExperimentConfig, SegmentDayStats, StatsResult
from stats.diagnostics import DriverAnalysis, SegmentDriver, analyze_drivers
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

    def test_adversarial_zero_and_negative_sample_sizes(self) -> None:
        """Adversarial Test: Zero sample size edge cases in frequentist, Bayesian, and mSPRT routines."""
        # Zero sample sizes in proportions test
        res_zero = freq_test(c_conv=0, c_n=0, t_conv=0, t_n=0)
        self.assertEqual(res_zero["z_stat"], 0.0)
        self.assertEqual(res_zero["p_value"], 1.0)
        self.assertEqual(res_zero["lift_abs"], 0.0)

        # Zero sample sizes in continuous test
        res_zero_cont = freq_test_continuous(c_mean=0.0, c_std=0.0, c_n=0, t_mean=0.0, t_std=0.0, t_n=0)
        self.assertEqual(res_zero_cont["z_stat"], 0.0)
        self.assertEqual(res_zero_cont["p_value"], 1.0)

        # Zero sample sizes in mSPRT
        res_zero_msprt = msprt_test(c_conv=0, c_n=0, t_conv=0, t_n=0)
        self.assertEqual(res_zero_msprt["lambda_stat"], 1.0)
        self.assertEqual(res_zero_msprt["msprt_p_value"], 1.0)
        self.assertEqual(res_zero_msprt["is_significant"], 0.0)

        # Zero sample sizes in mSPRT continuous
        res_zero_msprt_cont = msprt_test_continuous(c_mean=0.0, c_std=0.0, c_n=0, t_mean=0.0, t_std=0.0, t_n=0)
        self.assertEqual(res_zero_msprt_cont["lambda_stat"], 1.0)
        self.assertEqual(res_zero_msprt_cont["msprt_p_value"], 1.0)

        # SRM check behavior on zero samples
        try:
            p_val, srm_flag = srm_check(0, 0)
            self.assertIsInstance(p_val, float)
        except ZeroDivisionError:
            # Documented finding: scipy chisquare raises ZeroDivisionError when total sample size is 0
            pass

    def test_adversarial_high_variance_and_extreme_values(self) -> None:
        """Adversarial Test: Extreme variance and potential overflow bounds."""
        # Extremely high standard deviation
        res_high_var = freq_test_continuous(c_mean=100.0, c_std=1e6, c_n=1000, t_mean=110.0, t_std=1e6, t_n=1000)
        self.assertAlmostEqual(res_high_var["z_stat"], 0.0, delta=0.01)
        self.assertAlmostEqual(res_high_var["p_value"], 1.0, delta=0.01)

        # Log-Normal near exponential boundary (diff = 650)
        res_log_near_cap = freq_test_continuous(c_mean=0.0, c_std=1.0, c_n=100, t_mean=650.0, t_std=1.0, t_n=100, log_normal=True)
        self.assertGreater(res_log_near_cap["lift_rel"], 0.0)

        # Log-Normal exceeding overflow cap (diff = 750)
        res_log_over_cap = freq_test_continuous(c_mean=0.0, c_std=1.0, c_n=100, t_mean=750.0, t_std=1.0, t_n=100, log_normal=True)
        self.assertEqual(res_log_over_cap["lift_rel"], 0.0)

        # mSPRT extreme shift exponent cap
        res_msprt_cap = msprt_test_continuous(c_mean=0.0, c_std=1.0, c_n=1000, t_mean=1000.0, t_std=1.0, t_n=1000)
        self.assertEqual(res_msprt_cap["msprt_p_value"], 1.0 / res_msprt_cap["lambda_stat"])

    def test_adversarial_multiple_testing_corrections_edge_cases(self) -> None:
        """Adversarial Test: Empty lists, single elements, identical p-values, and unsorted sequences."""
        # Empty list
        adj_b, rej_b = bonferroni_correction([])
        self.assertEqual(adj_b, [])
        self.assertEqual(rej_b, [])

        adj_bh, rej_bh = benjamini_hochberg_correction([])
        self.assertEqual(adj_bh, [])
        self.assertEqual(rej_bh, [])

        # Single value
        adj_b, rej_b = bonferroni_correction([0.02], alpha=0.05)
        self.assertEqual(adj_b, [0.02])
        self.assertEqual(rej_b, [True])

        # All identical
        adj_b, rej_b = bonferroni_correction([0.05, 0.05, 0.05], alpha=0.05)
        self.assertEqual(adj_b, [0.15, 0.15, 0.15])
        self.assertEqual(rej_b, [False, False, False])

        # Unsorted inputs
        p_unsorted = [0.40, 0.001, 0.02]
        adj_bh, rej_bh = benjamini_hochberg_correction(p_unsorted, alpha=0.05)
        self.assertEqual(len(adj_bh), 3)
        self.assertTrue(rej_bh[1])  # 0.001 rejected

    def test_adversarial_power_analysis_extreme_alpha(self) -> None:
        """Adversarial Test: Extreme alpha and power levels in power analysis."""
        n_strict = power_analysis(baseline_rate=0.10, mde=0.01, alpha=0.0001, power=0.99)
        n_lenient = power_analysis(baseline_rate=0.10, mde=0.01, alpha=0.10, power=0.50)
        self.assertGreater(n_strict, n_lenient)


class AdversarialProportionsTests(unittest.TestCase):
    def test_zero_conversions_all_models(self) -> None:
        """Adversarial: Zero conversions (0/N) across frequentist, Bayesian, and mSPRT routines."""
        # 0/100 conversions
        res_freq = freq_test(c_conv=0, c_n=100, t_conv=0, t_n=100)
        self.assertEqual(res_freq["z_stat"], 0.0)
        self.assertEqual(res_freq["p_value"], 1.0)
        self.assertEqual(res_freq["lift_abs"], 0.0)

        res_bayes = bayes_decision(c_conv=0, c_n=100, t_conv=0, t_n=100, seed=42, draws=10000)
        self.assertAlmostEqual(res_bayes["prob_beats_control"], 0.5, delta=0.05)

        res_msprt = msprt_test(c_conv=0, c_n=100, t_conv=0, t_n=100)
        self.assertEqual(res_msprt["lambda_stat"], 1.0)
        self.assertEqual(res_msprt["msprt_p_value"], 1.0)
        self.assertEqual(res_msprt["is_significant"], 0.0)

    def test_100_percent_conversions_all_models(self) -> None:
        """Adversarial: 100% conversions (N/N) across frequentist, Bayesian, and mSPRT routines."""
        res_freq = freq_test(c_conv=500, c_n=500, t_conv=500, t_n=500)
        self.assertEqual(res_freq["z_stat"], 0.0)
        self.assertEqual(res_freq["p_value"], 1.0)
        self.assertEqual(res_freq["lift_abs"], 0.0)

        res_bayes = bayes_decision(c_conv=500, c_n=500, t_conv=500, t_n=500, seed=42, draws=10000)
        self.assertAlmostEqual(res_bayes["prob_beats_control"], 0.5, delta=0.05)

        res_msprt = msprt_test(c_conv=500, c_n=500, t_conv=500, t_n=500)
        self.assertEqual(res_msprt["lambda_stat"], 1.0)
        self.assertEqual(res_msprt["msprt_p_value"], 1.0)

    def test_equal_rates_various_baselines(self) -> None:
        """Adversarial: Equal rates (0%, 25%, 50%, 100%) across small and large sample sizes."""
        for rate in [0.0, 0.25, 0.50, 1.0]:
            for n in [10, 1000, 100000]:
                conv = int(rate * n)
                res = freq_test(c_conv=conv, c_n=n, t_conv=conv, t_n=n)
                self.assertEqual(res["z_stat"], 0.0)
                self.assertEqual(res["p_value"], 1.0)
                self.assertEqual(res["lift_abs"], 0.0)

    def test_extreme_sample_sizes_and_imbalance(self) -> None:
        """Adversarial: Extreme sample sizes N=1 vs N=10,000,000 and severe arm imbalance."""
        # N=1 vs N=1
        res_n1 = freq_test(c_conv=0, c_n=1, t_conv=1, t_n=1)
        self.assertIsInstance(res_n1["p_value"], float)

        # Huge N = 10,000,000
        res_huge = freq_test(c_conv=5000000, c_n=10000000, t_conv=5010000, t_n=10000000)
        self.assertGreater(res_huge["z_stat"], 5.0)
        self.assertLess(res_huge["p_value"], 0.0001)

        # Severe arm imbalance (N_c=1, N_t=10,000,000)
        res_imbalance = freq_test(c_conv=0, c_n=1, t_conv=5000000, t_n=10000000)
        self.assertIsInstance(res_imbalance["z_stat"], float)

        # Arm zero sample size finding: single arm c_n=0 with t_n>0 raises ZeroDivisionError in freq_test binary proportion
        with self.assertRaises(ZeroDivisionError):
            freq_test(c_conv=0, c_n=0, t_conv=50, t_n=100)


class AdversarialSRMTests(unittest.TestCase):
    def test_srm_custom_expected_ratios(self) -> None:
        """Adversarial: SRM chi-square with custom expected ratios (90/10, 80/20)."""
        # 90/10 split matching expectation -> No SRM
        p_val_9010, flag_9010 = srm_check(control_n=9000, treatment_n=1000, expected_ratio=0.9)
        self.assertFalse(flag_9010)
        self.assertGreater(p_val_9010, 0.05)

        # 90/10 expected split, but actual traffic is 80/20 -> SRM raised
        p_val_bad, flag_bad = srm_check(control_n=8000, treatment_n=2000, expected_ratio=0.9)
        self.assertTrue(flag_bad)
        self.assertLess(p_val_bad, 0.001)

        # 80/20 split matching expectation -> No SRM
        p_val_8020, flag_8020 = srm_check(control_n=8000, treatment_n=2000, expected_ratio=0.8)
        self.assertFalse(flag_8020)

    def test_srm_extreme_sample_sizes(self) -> None:
        """Adversarial: SRM check with massive sample size N=10,000,000 and tiny N."""
        # 10M vs 10M exact equal -> No SRM
        _, flag_huge_bal = srm_check(control_n=10000000, treatment_n=10000000)
        self.assertFalse(flag_huge_bal)

        # 10M vs 9.99M (0.1% slight skew at huge scale) -> SRM flagged due to power
        p_val_huge_skew, flag_huge_skew = srm_check(control_n=10000000, treatment_n=9990000)
        self.assertTrue(flag_huge_skew)
        self.assertLess(p_val_huge_skew, 0.001)

        # Tiny samples N=2 vs N=2 -> No SRM
        _, flag_tiny = srm_check(control_n=2, treatment_n=2)
        self.assertFalse(flag_tiny)


class AdversarialContinuousMetricsTests(unittest.TestCase):
    def test_continuous_zero_variance(self) -> None:
        """Adversarial: Zero standard deviation (c_std=0, t_std=0) across continuous metric functions."""
        res_freq = freq_test_continuous(c_mean=10.0, c_std=0.0, c_n=100, t_mean=10.0, t_std=0.0, t_n=100)
        self.assertEqual(res_freq["z_stat"], 0.0)
        self.assertEqual(res_freq["p_value"], 1.0)

        res_bayes = bayes_decision_continuous(c_mean=10.0, c_std=0.0, c_n=100, t_mean=10.0, t_std=0.0, t_n=100, seed=42)
        self.assertEqual(res_bayes["prob_beats_control"], 0.0)  # np.mean(10.0 > 10.0) = 0.0

        res_msprt = msprt_test_continuous(c_mean=10.0, c_std=0.0, c_n=100, t_mean=10.0, t_std=0.0, t_n=100)
        self.assertEqual(res_msprt["lambda_stat"], 1.0)
        self.assertEqual(res_msprt["msprt_p_value"], 1.0)

    def test_continuous_negative_means_and_extreme_variance(self) -> None:
        """Adversarial: Continuous metrics with negative means (e.g. net profit drop) and std=1e8."""
        res_neg = freq_test_continuous(c_mean=-50.0, c_std=5.0, c_n=500, t_mean=-40.0, t_std=5.0, t_n=500)
        self.assertGreater(res_neg["z_stat"], 3.0)
        self.assertLess(res_neg["p_value"], 0.01)

        res_huge_var = freq_test_continuous(c_mean=10.0, c_std=1e8, c_n=1000, t_mean=20.0, t_std=1e8, t_n=1000)
        self.assertAlmostEqual(res_huge_var["z_stat"], 0.0, delta=0.001)
        self.assertEqual(res_huge_var["p_value"], 1.0)

    def test_log_normal_overflow_and_underflow(self) -> None:
        """Adversarial: Log-Normal boundary behavior at exp overflow/underflow caps."""
        # Positive diff boundary cap check (< 700 vs >= 700)
        res_under = freq_test_continuous(c_mean=0.0, c_std=1.0, c_n=100, t_mean=699.0, t_std=1.0, t_n=100, log_normal=True)
        self.assertGreater(res_under["lift_rel"], 0.0)

        res_over = freq_test_continuous(c_mean=0.0, c_std=1.0, c_n=100, t_mean=701.0, t_std=1.0, t_n=100, log_normal=True)
        self.assertEqual(res_over["lift_rel"], 0.0)

        # Negative diff (reduction in latency/cost)
        res_neg_diff = freq_test_continuous(c_mean=5.0, c_std=0.2, c_n=1000, t_mean=4.5, t_std=0.2, t_n=1000, log_normal=True)
        self.assertLess(res_neg_diff["lift_rel"], 0.0)
        self.assertAlmostEqual(res_neg_diff["lift_rel"], math.exp(-0.5) - 1.0, delta=0.01)


class AdversarialMSPRTTests(unittest.TestCase):
    def test_msprt_extreme_tau_sq(self) -> None:
        """Adversarial: mSPRT with extremely small (1e-12) and large (1000.0) tau_sq variance prior."""
        res_tiny_tau = msprt_test(c_conv=100, c_n=1000, t_conv=150, t_n=1000, tau_sq=1e-12)
        self.assertIsInstance(res_tiny_tau["msprt_p_value"], float)

        res_large_tau = msprt_test(c_conv=100, c_n=1000, t_conv=150, t_n=1000, tau_sq=1000.0)
        self.assertLess(res_large_tau["msprt_p_value"], 0.05)

    def test_msprt_p_value_clamping_and_zero_variance(self) -> None:
        """Adversarial: mSPRT p-value upper bound clipping to 1.0 and zero variance handling."""
        res_clamped = msprt_test(c_conv=50, c_n=1000, t_conv=50, t_n=1000)
        self.assertEqual(res_clamped["msprt_p_value"], 1.0)
        self.assertEqual(res_clamped["is_significant"], 0.0)


class AdversarialMultipleTestingTests(unittest.TestCase):
    def test_corrections_empty_and_single_elements(self) -> None:
        """Adversarial: Multiple testing corrections on empty lists and single element inputs."""
        self.assertEqual(bonferroni_correction([]), ([], []))
        self.assertEqual(benjamini_hochberg_correction([]), ([], []))

        adj_b, rej_b = bonferroni_correction([0.049], alpha=0.05)
        self.assertEqual(adj_b, [0.049])
        self.assertEqual(rej_b, [True])

        adj_bh, rej_bh = benjamini_hochberg_correction([0.051], alpha=0.05)
        self.assertEqual(adj_bh, [0.051])
        self.assertEqual(rej_bh, [False])

    def test_corrections_extreme_and_precision_p_values(self) -> None:
        """Adversarial: p-values of 0.0, 1.0, and extreme small floats (1e-15)."""
        p_extreme = [0.0, 1e-15, 1.0]
        adj_b, rej_b = bonferroni_correction(p_extreme, alpha=0.05)
        self.assertEqual(adj_b[0], 0.0)
        self.assertEqual(adj_b[2], 1.0)
        self.assertEqual(rej_b, [True, True, False])

        adj_bh, rej_bh = benjamini_hochberg_correction(p_extreme, alpha=0.05)
        self.assertEqual(adj_bh[0], 0.0)
        self.assertEqual(rej_bh, [True, True, False])

    def test_corrections_unsorted_and_duplicates(self) -> None:
        """Adversarial: Unsorted p-value arrays and identical duplicate p-values."""
        p_unsorted = [0.40, 0.001, 0.03, 0.01]
        adj_bh, rej_bh = benjamini_hochberg_correction(p_unsorted, alpha=0.05)
        self.assertTrue(rej_bh[1])  # 0.001 rejected
        self.assertTrue(rej_bh[3])  # 0.01 rejected

        p_dups = [0.02, 0.02, 0.02]
        adj_b, _ = bonferroni_correction(p_dups, alpha=0.05)
        self.assertEqual(adj_b, [0.06, 0.06, 0.06])


class AdversarialDiagnosticsTests(unittest.TestCase):
    def test_analyze_drivers_empty_segments_raises_value_error(self) -> None:
        """Adversarial: analyze_drivers with empty segment list raises ValueError."""
        with self.assertRaises(ValueError):
            analyze_drivers(overall_lift_abs=0.02, segments=[])

    def test_analyze_drivers_low_n_inconclusive(self) -> None:
        """Adversarial: Segment with control_n or treatment_n < 200 classified as inconclusive."""
        low_seg = SegmentDayStats(
            experiment_id="exp_diag",
            day=7,
            segment_key="us_users",
            control_n=150,  # < 200
            control_conversions=15,
            treatment_n=1000,
            treatment_conversions=100,
        )
        res = analyze_drivers(overall_lift_abs=0.02, segments=[low_seg])
        self.assertEqual(res.drivers[0].classification, "inconclusive")
        self.assertIsNone(res.top_driver)

    def test_analyze_drivers_classification_and_ranking(self) -> None:
        """Adversarial: Classifies drivers into driving, dragging, and in_line and ranks by deviation."""
        seg_driver = SegmentDayStats(
            experiment_id="exp_diag", day=7, segment_key="mobile",
            control_n=1000, control_conversions=100, treatment_n=1000, treatment_conversions=160,
        )
        seg_inline = SegmentDayStats(
            experiment_id="exp_diag", day=7, segment_key="desktop",
            control_n=1000, control_conversions=100, treatment_n=1000, treatment_conversions=120,
        )
        seg_drag = SegmentDayStats(
            experiment_id="exp_diag", day=7, segment_key="tablet",
            control_n=1000, control_conversions=100, treatment_n=1000, treatment_conversions=80,
        )
        res = analyze_drivers(overall_lift_abs=0.02, segments=[seg_inline, seg_driver, seg_drag])
        self.assertEqual(len(res.drivers), 3)
        self.assertIn(res.drivers[0].segment_key, ("mobile", "tablet"))
        driver_map = {d.segment_key: d.classification for d in res.drivers}
        self.assertEqual(driver_map["mobile"], "driving")
        self.assertEqual(driver_map["desktop"], "in_line")
        self.assertEqual(driver_map["tablet"], "dragging")
        self.assertIsNotNone(res.top_driver)
        self.assertIn(res.top_driver.segment_key, ("mobile", "tablet"))

        dict_repr = res.as_dict()
        self.assertEqual(dict_repr["experiment_id"], "exp_diag")
        self.assertEqual(len(dict_repr["drivers"]), 3)


if __name__ == "__main__":
    unittest.main()


