"""Deterministic statistics core. Zero LLM calls, zero imports from agents/api/ui.

Given identical inputs (and an explicit seed for the Monte-Carlo sampler), every
function here returns the identical output every time. This module is the only
place a Scale/Continue/Stop/Rollback/Pause decision may be produced.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as sp_stats

from shared.models import (
    EXPECTED_LOSS_EPSILON,
    GUARDRAIL_MARGIN,
    KILL_PROB_THRESHOLD,
    SHIP_PROB_THRESHOLD,
    SRM_ALPHA,
    ExperimentConfig,
    DayStats,
    StatsResult,
)


def power_analysis(baseline_rate: float, mde: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Required sample size per arm for a two-sided two-proportion z-test."""
    p1 = baseline_rate
    p2 = baseline_rate + mde
    pbar = (p1 + p2) / 2
    z_a = sp_stats.norm.ppf(1 - alpha / 2)
    z_b = sp_stats.norm.ppf(power)
    n = ((z_a + z_b) ** 2 * 2 * pbar * (1 - pbar)) / mde**2
    return math.ceil(n)


def srm_check(control_n: int, treatment_n: int, expected_ratio: float = 0.5) -> tuple[float, bool]:
    """Chi-square goodness-of-fit p-value for sample-ratio mismatch, flagged at SRM_ALPHA."""
    total = control_n + treatment_n
    expected = [total * expected_ratio, total * (1 - expected_ratio)]
    chi2, p_value = sp_stats.chisquare([control_n, treatment_n], f_exp=expected)
    flag = bool(p_value < SRM_ALPHA)
    return float(p_value), flag


def freq_test(c_conv: int, c_n: int, t_conv: int, t_n: int) -> dict:
    """Two-proportion pooled z-test with a 95% CI on the absolute lift (unpooled SE)."""
    c_rate = c_conv / c_n
    t_rate = t_conv / t_n
    p_pool = (c_conv + t_conv) / (c_n + t_n)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / c_n + 1 / t_n))
    z_stat = (t_rate - c_rate) / se if se > 0 else 0.0
    p_value = 2 * (1 - sp_stats.norm.cdf(abs(z_stat))) if se > 0 else 1.0

    diff = t_rate - c_rate
    se_ci = math.sqrt(c_rate * (1 - c_rate) / c_n + t_rate * (1 - t_rate) / t_n)
    ci_low = diff - 1.96 * se_ci
    ci_high = diff + 1.96 * se_ci

    return {
        "z_stat": float(z_stat),
        "p_value": float(p_value),
        "lift_abs": float(diff),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
    }


def bayes_decision(c_conv: int, c_n: int, t_conv: int, t_n: int, seed: int = 0, draws: int = 50_000) -> dict:
    """Beta(1,1)-prior Monte-Carlo posterior comparison of treatment vs control."""
    rng = np.random.default_rng(seed)
    a = rng.beta(1 + c_conv, 1 + c_n - c_conv, draws)
    b = rng.beta(1 + t_conv, 1 + t_n - t_conv, draws)
    prob_beats = float(np.mean(b > a))
    expected_loss_ship = float(np.mean(np.maximum(a - b, 0)))
    expected_loss_keep = float(np.mean(np.maximum(b - a, 0)))
    return {
        "prob_beats_control": prob_beats,
        "expected_loss_ship": expected_loss_ship,
        "expected_loss_keep": expected_loss_keep,
    }


def guardrail_check(c_rate: float, t_rate: float, margin: float = GUARDRAIL_MARGIN) -> tuple[bool, float]:
    """Breach if the treatment 'bad event' rate exceeds control by more than margin."""
    observed_margin = t_rate - c_rate
    breach = observed_margin > margin
    return breach, observed_margin


def decide(stats: StatsResult, config: ExperimentConfig) -> str:
    """THE decision function. Deterministic, precedence-ordered, LLM-free."""
    if stats.srm_flag:
        return "pause"
    if stats.guardrail_breach:
        return "rollback"
    if stats.prob_beats_control >= SHIP_PROB_THRESHOLD and stats.expected_loss_ship <= EXPECTED_LOSS_EPSILON:
        return "scale"
    if stats.prob_beats_control <= KILL_PROB_THRESHOLD:
        return "stop"
    return "continue"


def compute_day_stats(cumulative: DayStats, config: ExperimentConfig, seed: int = 0) -> StatsResult:
    """Assemble a StatsResult from cumulative-to-date counts using the functions above."""
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
    )
