"""Deterministic statistics core. No LLM calls are allowed here."""

from __future__ import annotations

import math

import numpy as np
from scipy import stats as sp_stats

from rules_engine.decision import evaluate_decision
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

METRIC_DIRECTIONS: dict[str, str] = {
    "error_rate": "decrease_good",
    "latency_p95_ms": "decrease_good",
    "support_contact_rate": "decrease_good",
    "refund_rate": "decrease_good",
    "unsubscribe_rate": "decrease_good",
    "checkout_abandon_rate": "decrease_good",
    "crash_free_rate": "increase_good",
    "app_rating": "increase_good",
    "conversion_rate": "increase_good",
    "checkout_completion_rate": "increase_good",
    "plan_upgrade_rate": "increase_good",
    "add_to_cart_rate": "increase_good",
    "retention_d30": "increase_good",
    "arpu": "increase_good",
}


def get_metric_direction(metric_key: str) -> str:
    """Return metric direction ('increase_good' or 'decrease_good') from catalog or default."""
    return METRIC_DIRECTIONS.get(metric_key, "decrease_good")


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


def freq_test_continuous(
    c_mean: float,
    c_std: float,
    c_n: int,
    t_mean: float,
    t_std: float,
    t_n: int,
    log_normal: bool = False,
) -> dict[str, float]:
    """Frequentist z-test / t-test for continuous metrics (Normal or Log-Normal model)."""
    if c_n <= 0 or t_n <= 0:
        return {
            "z_stat": 0.0,
            "p_value": 1.0,
            "lift_abs": 0.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
        }
    se = math.sqrt((c_std**2) / c_n + (t_std**2) / t_n)
    diff = t_mean - c_mean
    z_stat = diff / se if se > 0 else 0.0
    p_value = 2 * (1 - sp_stats.norm.cdf(abs(z_stat))) if se > 0 else 1.0

    if log_normal:
        lift_rel = math.exp(diff) - 1.0 if abs(diff) < 700 else 0.0
        ci_low = math.exp(diff - 1.96 * se) - 1.0 if abs(diff - 1.96 * se) < 700 else 0.0
        ci_high = math.exp(diff + 1.96 * se) - 1.0 if abs(diff + 1.96 * se) < 700 else 0.0
        return {
            "z_stat": float(z_stat),
            "p_value": float(p_value),
            "lift_abs": float(diff),
            "lift_rel": float(lift_rel),
            "ci_low": float(ci_low),
            "ci_high": float(ci_high),
        }

    return {
        "z_stat": float(z_stat),
        "p_value": float(p_value),
        "lift_abs": float(diff),
        "ci_low": float(diff - 1.96 * se),
        "ci_high": float(diff + 1.96 * se),
    }


def freq_test(
    c_conv: float,
    c_n: int,
    t_conv: float,
    t_n: int,
    c_std: float | None = None,
    t_std: float | None = None,
    log_normal: bool = False,
) -> dict[str, float]:
    """Frequentist test supporting both binary proportions and continuous metrics."""
    if c_std is not None and t_std is not None:
        return freq_test_continuous(
            c_mean=float(c_conv),
            c_std=float(c_std),
            c_n=c_n,
            t_mean=float(t_conv),
            t_std=float(t_std),
            t_n=t_n,
            log_normal=log_normal,
        )
    c_rate = c_conv / c_n if c_n > 0 else 0.0
    t_rate = t_conv / t_n if t_n > 0 else 0.0
    p_pool = (c_conv + t_conv) / (c_n + t_n) if (c_n + t_n) > 0 else 0.0
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / c_n + 1 / t_n)) if p_pool * (1 - p_pool) > 0 else 0.0
    z_stat = (t_rate - c_rate) / se if se > 0 else 0.0
    p_value = 2 * (1 - sp_stats.norm.cdf(abs(z_stat))) if se > 0 else 1.0
    diff = t_rate - c_rate
    se_ci = math.sqrt(c_rate * (1 - c_rate) / c_n + t_rate * (1 - t_rate) / t_n) if c_n > 0 and t_n > 0 else 0.0
    return {
        "z_stat": float(z_stat),
        "p_value": float(p_value),
        "lift_abs": float(diff),
        "ci_low": float(diff - 1.96 * se_ci),
        "ci_high": float(diff + 1.96 * se_ci),
    }


def bayes_decision_continuous(
    c_mean: float,
    c_std: float,
    c_n: int,
    t_mean: float,
    t_std: float,
    t_n: int,
    seed: int = 0,
    draws: int = 50000,
    log_normal: bool = False,
) -> dict[str, float]:
    """Bayesian decision engine for continuous metrics (Normal/Log-Normal model)."""
    rng = np.random.default_rng(seed)
    se_c = c_std / math.sqrt(c_n) if c_n > 0 else 1.0
    se_t = t_std / math.sqrt(t_n) if t_n > 0 else 1.0
    control = rng.normal(c_mean, se_c, draws)
    treatment = rng.normal(t_mean, se_t, draws)

    if log_normal:
        control_orig = np.exp(control)
        treatment_orig = np.exp(treatment)
        return {
            "prob_beats_control": float(np.mean(treatment > control)),
            "expected_loss_ship": float(np.mean(np.maximum(control_orig - treatment_orig, 0))),
            "expected_loss_keep": float(np.mean(np.maximum(treatment_orig - control_orig, 0))),
        }

    return {
        "prob_beats_control": float(np.mean(treatment > control)),
        "expected_loss_ship": float(np.mean(np.maximum(control - treatment, 0))),
        "expected_loss_keep": float(np.mean(np.maximum(treatment - control, 0))),
    }


def bayes_decision(
    c_conv: float,
    c_n: int,
    t_conv: float,
    t_n: int,
    seed: int = 0,
    draws: int = 50000,
    c_std: float | None = None,
    t_std: float | None = None,
    log_normal: bool = False,
) -> dict[str, float]:
    if c_std is not None and t_std is not None:
        return bayes_decision_continuous(
            c_mean=float(c_conv),
            c_std=float(c_std),
            c_n=c_n,
            t_mean=float(t_conv),
            t_std=float(t_std),
            t_n=t_n,
            seed=seed,
            draws=draws,
            log_normal=log_normal,
        )
    rng = np.random.default_rng(seed)
    control = rng.beta(1 + int(c_conv), 1 + c_n - int(c_conv), draws)
    treatment = rng.beta(1 + int(t_conv), 1 + t_n - int(t_conv), draws)
    return {
        "prob_beats_control": float(np.mean(treatment > control)),
        "expected_loss_ship": float(np.mean(np.maximum(control - treatment, 0))),
        "expected_loss_keep": float(np.mean(np.maximum(treatment - control, 0))),
    }


def guardrail_check(
    c_rate: float,
    t_rate: float,
    margin: float = GUARDRAIL_MARGIN,
    direction: str | None = None,
    metric_key: str | None = None,
) -> tuple[bool, float]:
    """Evaluate guardrail breach according to metric directionality.

    For decrease_good: breach when t_rate - c_rate > margin (treatment increase is bad).
    For increase_good: breach when c_rate - t_rate > margin (treatment drop is bad).
    """
    if direction is None:
        if metric_key is not None:
            direction = get_metric_direction(metric_key)
        else:
            direction = "decrease_good"

    if direction == "increase_good":
        observed_margin = c_rate - t_rate
    else:
        observed_margin = t_rate - c_rate

    return observed_margin > margin, observed_margin


def msprt_test(
    c_conv: float,
    c_n: int,
    t_conv: float,
    t_n: int,
    tau_sq: float = 0.0001,
    alpha: float = 0.05,
) -> dict[str, float]:
    """Mixture Sequential Probability Ratio Test (mSPRT) for continuous peeking (Proportions)."""
    if c_n <= 0 or t_n <= 0:
        return {
            "lambda_stat": 1.0,
            "msprt_p_value": 1.0,
            "is_significant": 0.0,
        }

    c_rate = c_conv / c_n
    t_rate = t_conv / t_n
    diff = t_rate - c_rate

    p_pool = (c_conv + t_conv) / (c_n + t_n)
    v_t = p_pool * (1.0 - p_pool) * (1.0 / c_n + 1.0 / t_n)

    if v_t <= 0:
        return {
            "lambda_stat": 1.0,
            "msprt_p_value": 1.0,
            "is_significant": 0.0,
        }

    ratio = tau_sq / (tau_sq + v_t)
    exponent = min((tau_sq * (diff**2)) / (2.0 * v_t * (tau_sq + v_t)), 700.0)

    lambda_stat = math.sqrt(ratio) * math.exp(exponent)
    msprt_p_value = min(1.0, 1.0 / lambda_stat) if lambda_stat > 0 else 1.0

    return {
        "lambda_stat": float(lambda_stat),
        "msprt_p_value": float(msprt_p_value),
        "is_significant": float(msprt_p_value < alpha),
    }


def msprt_test_continuous(
    c_mean: float,
    c_std: float,
    c_n: int,
    t_mean: float,
    t_std: float,
    t_n: int,
    tau_sq: float = 0.01,
    alpha: float = 0.05,
) -> dict[str, float]:
    """mSPRT for continuous metrics (Normal model)."""
    if c_n <= 0 or t_n <= 0:
        return {
            "lambda_stat": 1.0,
            "msprt_p_value": 1.0,
            "is_significant": 0.0,
        }
    diff = t_mean - c_mean
    v_t = (c_std**2) / c_n + (t_std**2) / t_n
    if v_t <= 0:
        return {
            "lambda_stat": 1.0,
            "msprt_p_value": 1.0,
            "is_significant": 0.0,
        }
    ratio = tau_sq / (tau_sq + v_t)
    exponent = min((tau_sq * (diff**2)) / (2.0 * v_t * (tau_sq + v_t)), 700.0)
    lambda_stat = math.sqrt(ratio) * math.exp(exponent)
    msprt_p_value = min(1.0, 1.0 / lambda_stat) if lambda_stat > 0 else 1.0
    return {
        "lambda_stat": float(lambda_stat),
        "msprt_p_value": float(msprt_p_value),
        "is_significant": float(msprt_p_value < alpha),
    }


def bonferroni_correction(
    p_values: list[float], alpha: float = 0.05
) -> tuple[list[float], list[bool]]:
    """Bonferroni correction for multi-variant or multi-metric evaluations."""
    m = len(p_values)
    if m == 0:
        return [], []
    adj_p = [min(1.0, p * m) for p in p_values]
    rejects = [adj <= alpha for adj in adj_p]
    return adj_p, rejects


def benjamini_hochberg_correction(
    p_values: list[float], alpha: float = 0.05
) -> tuple[list[float], list[bool]]:
    """Benjamini-Hochberg False Discovery Rate (FDR) correction."""
    m = len(p_values)
    if m == 0:
        return [], []

    sorted_indices = sorted(range(m), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in sorted_indices]

    adjusted = [0.0] * m
    cum_min = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        val = (m / rank) * sorted_p[i]
        cum_min = min(cum_min, val)
        adjusted[i] = min(1.0, cum_min)

    adj_original = [0.0] * m
    rejects = [False] * m
    for i, orig_idx in enumerate(sorted_indices):
        adj_original[orig_idx] = float(adjusted[i])
        rejects[orig_idx] = bool(adjusted[i] <= alpha)

    return adj_original, rejects


def _ready_to_call(stats: StatsResult, config: ExperimentConfig) -> bool:
    if stats.day < MIN_RUNTIME_DAYS:
        return False
    return min(stats.control_n, stats.treatment_n) >= config.required_n_per_arm


def decide(stats: StatsResult, config: ExperimentConfig | None = None) -> str:
    """Return the deterministic, precedence-ordered experiment action by delegating to rules_engine.decision."""
    rec = evaluate_decision(stats, config)
    return rec.action_code


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
    msprt = msprt_test(
        cumulative.control_conversions,
        cumulative.control_n,
        cumulative.treatment_conversions,
        cumulative.treatment_n,
    )

    guardrail_breach = False
    max_guardrail_margin = -float("inf")

    guardrail_items: list[tuple[str, float, float]] = []

    if cumulative.guardrail_metrics_data:
        for metric_key, data in cumulative.guardrail_metrics_data.items():
            c_r = data.get("control_rate", data.get("control", cumulative.guardrail_control_rate))
            t_r = data.get("treatment_rate", data.get("treatment", cumulative.guardrail_treatment_rate))
            guardrail_items.append((metric_key, c_r, t_r))
    elif config.guardrail_metrics:
        for metric_key in config.guardrail_metrics:
            guardrail_items.append((metric_key, cumulative.guardrail_control_rate, cumulative.guardrail_treatment_rate))
    else:
        guardrail_items.append(("error_rate", cumulative.guardrail_control_rate, cumulative.guardrail_treatment_rate))

    for metric_key, c_r, t_r in guardrail_items:
        breach, margin = guardrail_check(c_r, t_r, margin=GUARDRAIL_MARGIN, metric_key=metric_key)
        if breach:
            guardrail_breach = True
        if margin > max_guardrail_margin:
            max_guardrail_margin = margin

    if max_guardrail_margin == -float("inf"):
        max_guardrail_margin = 0.0

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
        guardrail_margin=max_guardrail_margin,
        control_n=cumulative.control_n,
        treatment_n=cumulative.treatment_n,
        msprt_p_value=msprt["msprt_p_value"],
    )
