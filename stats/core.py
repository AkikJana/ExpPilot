"""Deterministic statistics core. No LLM calls are allowed here."""

from __future__ import annotations

import math

import numpy as np
from scipy import stats as sp_stats

from shared.models import (
    EXPECTED_LOSS_EPSILON,
    GUARDRAIL_MARGIN,
    KILL_PROB_THRESHOLD,
    MIN_RUNTIME_DAYS,
    SHIP_PROB_THRESHOLD,
    SRM_ALPHA,
    DayStats,
    ExperimentConfig,
    StatsResult,
)


def power_analysis(
    baseline_rate: float,
    mde: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:
    """Return required sample size per arm for a two-sided proportions test."""
    p1 = baseline_rate
    p2 = baseline_rate + mde
    pbar = (p1 + p2) / 2
    z_a = sp_stats.norm.ppf(1 - alpha / 2)
    z_b = sp_stats.norm.ppf(power)
    n = (z_a + z_b) ** 2 * 2 * pbar * (1 - pbar) / mde**2
    return math.ceil(n)


def srm_check(
    control_n: int, treatment_n: int, expected_ratio: float = 0.5
) -> tuple[float, bool]:
    total = control_n + treatment_n
    expected = [total * expected_ratio, total * (1 - expected_ratio)]
    _, p_value = sp_stats.chisquare([control_n, treatment_n], f_exp=expected)
    return float(p_value), bool(p_value < SRM_ALPHA)


def freq_test(c_conv: int, c_n: int, t_conv: int, t_n: int) -> dict[str, float]:
    c_rate = c_conv / c_n
    t_rate = t_conv / t_n
    p_pool = (c_conv + t_conv) / (c_n + t_n)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / c_n + 1 / t_n))
    z_stat = (t_rate - c_rate) / se if se > 0 else 0.0
    p_value = 2 * (1 - sp_stats.norm.cdf(abs(z_stat))) if se > 0 else 1.0
    diff = t_rate - c_rate
    se_ci = math.sqrt(c_rate * (1 - c_rate) / c_n + t_rate * (1 - t_rate) / t_n)
    return {
        "z_stat": float(z_stat),
        "p_value": float(p_value),
        "lift_abs": float(diff),
        "ci_low": float(diff - 1.96 * se_ci),
        "ci_high": float(diff + 1.96 * se_ci),
    }


def bayes_decision(
    c_conv: int,
    c_n: int,
    t_conv: int,
    t_n: int,
    seed: int = 0,
    draws: int = 50000,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    control = rng.beta(1 + c_conv, 1 + c_n - c_conv, draws)
    treatment = rng.beta(1 + t_conv, 1 + t_n - t_conv, draws)
    return {
        "prob_beats_control": float(np.mean(treatment > control)),
        "expected_loss_ship": float(np.mean(np.maximum(control - treatment, 0))),
        "expected_loss_keep": float(np.mean(np.maximum(treatment - control, 0))),
    }


def guardrail_check(
    c_rate: float, t_rate: float, margin: float = GUARDRAIL_MARGIN
) -> tuple[bool, float]:
    observed_margin = t_rate - c_rate
    return observed_margin > margin, observed_margin


def _ready_to_call(stats: StatsResult, config: ExperimentConfig) -> bool:
    if stats.day < MIN_RUNTIME_DAYS:
        return False
    return min(stats.control_n, stats.treatment_n) >= config.required_n_per_arm


def decide(stats: StatsResult, config: ExperimentConfig) -> str:
    """Return the deterministic, precedence-ordered experiment action."""
    if stats.srm_flag:
        return "pause"
    if stats.guardrail_breach:
        return "rollback"
    if not _ready_to_call(stats, config):
        return "continue"
    if (
        stats.prob_beats_control >= SHIP_PROB_THRESHOLD
        and stats.expected_loss_ship <= EXPECTED_LOSS_EPSILON
    ):
        return "scale"
    if stats.prob_beats_control <= KILL_PROB_THRESHOLD:
        return "stop"
    return "continue"


def compute_day_stats(
    cumulative: DayStats, config: ExperimentConfig, seed: int = 0
) -> StatsResult:
    srm_p_value, srm_flag = srm_check(cumulative.control_n, cumulative.treatment_n)
    freq = freq_test(
        cumulative.control_conversions,
        cumulative.control_n,
        cumulative.treatment_conversions,
        cumulative.treatment_n,
    )
    bayes = bayes_decision(
        cumulative.control_conversions,
        cumulative.control_n,
        cumulative.treatment_conversions,
        cumulative.treatment_n,
        seed=seed,
    )
    guardrail_breach, guardrail_margin = guardrail_check(
        cumulative.guardrail_control_rate, cumulative.guardrail_treatment_rate
    )
    return StatsResult(
        experiment_id=cumulative.experiment_id,
        day=cumulative.day,
        srm_p_value=srm_p_value,
        srm_flag=srm_flag,
        z_stat=freq["z_stat"],
        p_value=freq["p_value"],
        lift_abs=freq["lift_abs"],
        ci_low=freq["ci_low"],
        ci_high=freq["ci_high"],
        prob_beats_control=bayes["prob_beats_control"],
        expected_loss_ship=bayes["expected_loss_ship"],
        expected_loss_keep=bayes["expected_loss_keep"],
        guardrail_breach=guardrail_breach,
        guardrail_margin=guardrail_margin,
        control_n=cumulative.control_n,
        treatment_n=cumulative.treatment_n,
    )
