"""Unit tests for stats/core.py — hand-computed and cross-validated cases."""
from __future__ import annotations

from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize

from shared.models import ExperimentConfig, StatsResult
from stats.core import bayes_decision, compute_day_stats, decide, freq_test, power_analysis, srm_check


def test_power_analysis_matches_statsmodels():
    baseline, mde, alpha, power = 0.10, 0.02, 0.05, 0.80
    ours = power_analysis(baseline, mde, alpha, power)
    es = proportion_effectsize(baseline + mde, baseline)
    theirs = NormalIndPower().solve_power(
        effect_size=abs(es), alpha=alpha, power=power, ratio=1.0, alternative="two-sided"
    )
    assert abs(ours - theirs) / theirs <= 0.02


def test_srm_check_flags_correctly():
    p_balanced, flag_balanced = srm_check(5000, 5000)
    assert flag_balanced is False

    p_skewed, flag_skewed = srm_check(5300, 4700)
    assert flag_skewed is True


def test_freq_test_hand_computed():
    result = freq_test(100, 1000, 130, 1000)
    assert result["p_value"] < 0.05
    assert 2.0 < result["z_stat"] < 2.15


def test_bayes_decision_true_lift_and_determinism():
    result1 = bayes_decision(100, 1000, 130, 1000, seed=0)
    assert 0.95 <= result1["prob_beats_control"] <= 1.0

    result2 = bayes_decision(100, 1000, 130, 1000, seed=0)
    assert result1["prob_beats_control"] == result2["prob_beats_control"]
    assert result1["expected_loss_ship"] == result2["expected_loss_ship"]
    assert result1["expected_loss_keep"] == result2["expected_loss_keep"]


def test_bayes_decision_aa_sanity():
    result = bayes_decision(100, 1000, 101, 1000)
    assert 0.35 <= result["prob_beats_control"] <= 0.65


def _sample_config() -> ExperimentConfig:
    return ExperimentConfig(
        id="exp_deadbeef",
        hypothesis_id="hyp_deadbeef",
        flag_key="checkout_v2",
        audience_segment="mobile_users",
        traffic_split={"control": 0.5, "treatment": 0.5},
        baseline_rate=0.10,
        mde=0.02,
        required_n_per_arm=1000,
        estimated_days=9,
        guardrail_metrics=["latency_breach_rate"],
        daily_traffic=6000,
        status="running",
    )


def test_decide_precedence_srm_beats_scale():
    """SRM flag must win even when Bayes numbers alone would say 'scale'."""
    stats_result = StatsResult(
        experiment_id="exp_deadbeef",
        day=6,
        srm_p_value=0.0001,
        srm_flag=True,
        z_stat=5.0,
        p_value=0.0001,
        lift_abs=0.03,
        ci_low=0.02,
        ci_high=0.04,
        prob_beats_control=0.99,
        expected_loss_ship=0.0001,
        expected_loss_keep=0.01,
        guardrail_breach=False,
        guardrail_margin=0.0,
    )
    assert decide(stats_result, _sample_config()) == "pause"


def test_decide_guardrail_beats_scale():
    stats_result = StatsResult(
        experiment_id="exp_deadbeef",
        day=6,
        srm_p_value=0.9,
        srm_flag=False,
        z_stat=5.0,
        p_value=0.0001,
        lift_abs=0.03,
        ci_low=0.02,
        ci_high=0.04,
        prob_beats_control=0.99,
        expected_loss_ship=0.0001,
        expected_loss_keep=0.01,
        guardrail_breach=True,
        guardrail_margin=0.03,
    )
    assert decide(stats_result, _sample_config()) == "rollback"


def _scale_worthy_stats(day: int, n: int) -> StatsResult:
    """A StatsResult whose Bayes numbers alone would justify shipping."""
    return StatsResult(
        experiment_id="exp_deadbeef",
        day=day,
        srm_p_value=0.9,
        srm_flag=False,
        z_stat=6.0,
        p_value=0.0001,
        lift_abs=0.04,
        ci_low=0.03,
        ci_high=0.05,
        prob_beats_control=0.999,
        expected_loss_ship=0.0,
        expected_loss_keep=0.04,
        guardrail_breach=False,
        guardrail_margin=0.0,
        control_n=n,
        treatment_n=n,
    )


def test_decide_will_not_ship_before_minimum_runtime():
    """A day-1 landslide must not ship: continuous monitoring inflates false positives."""
    config = _sample_config()
    stats_result = _scale_worthy_stats(day=1, n=config.required_n_per_arm * 5)
    assert decide(stats_result, config) == "continue"


def test_decide_will_not_ship_before_required_sample_size():
    """Past the runtime floor but under the planned per-arm N, the call still waits."""
    config = _sample_config()
    stats_result = _scale_worthy_stats(day=10, n=config.required_n_per_arm - 1)
    assert decide(stats_result, config) == "continue"


def test_decide_ships_once_powered_and_past_runtime_floor():
    """Both gates satisfied and the posterior is decisive — now it ships."""
    config = _sample_config()
    stats_result = _scale_worthy_stats(day=10, n=config.required_n_per_arm)
    assert decide(stats_result, config) == "scale"


def test_srm_fires_even_before_the_readiness_gates():
    """Trust verdicts are not gated: a broken assignment pauses on day 1."""
    config = _sample_config()
    stats_result = _scale_worthy_stats(day=1, n=10)
    stats_result.srm_flag = True
    assert decide(stats_result, config) == "pause"


def test_compute_day_stats_assembles_result():
    from shared.models import DayStats

    cumulative = DayStats(
        experiment_id="exp_deadbeef",
        day=6,
        control_n=3000,
        control_conversions=300,
        treatment_n=3000,
        treatment_conversions=390,
        guardrail_control_rate=0.05,
        guardrail_treatment_rate=0.05,
    )
    result = compute_day_stats(cumulative, _sample_config(), seed=0)
    assert result.experiment_id == "exp_deadbeef"
    assert result.day == 6
    assert decide(result, _sample_config()) in {"scale", "continue", "stop", "rollback", "pause"}
