"""Ground-truth synthetic experiment engine. The copilot must NEVER read the ground-truth dict."""
from __future__ import annotations

import hashlib
import math

import numpy as np

from shared.models import DayStats, ExperimentConfig
from stats.core import power_analysis

SCENARIOS = ("true_lift", "aa_null", "srm", "guardrail_breach", "underpowered")

_N_DAYS = 14
_GUARDRAIL_BASE_RATE = 0.05
_GUARDRAIL_BREACH_MARGIN = 0.03


def _deterministic_id(prefix: str, scenario: str, seed: int) -> str:
    """Derive a stable 8-hex-char id from scenario+seed so runs are fully reproducible."""
    digest = hashlib.sha1(f"{scenario}_{seed}".encode()).hexdigest()[:8]
    return f"{prefix}_{digest}"


def make_experiment(scenario: str, seed: int) -> tuple[ExperimentConfig, list[DayStats], dict]:
    """Generate a 14-day synthetic experiment with a hidden ground-truth label."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario!r}, must be one of {SCENARIOS}")

    rng = np.random.default_rng(seed)
    exp_id = _deterministic_id("exp", scenario, seed)
    hyp_id = _deterministic_id("hyp", scenario, seed)

    mde = 0.02
    if scenario == "true_lift":
        control_rate = 0.10
        lift = float(rng.uniform(0.02, 0.05))
        daily_traffic = 6000
        correct_action = "scale"
    elif scenario == "aa_null":
        control_rate = 0.10
        lift = 0.0
        daily_traffic = 6000
        correct_action = "continue"
    elif scenario == "srm":
        control_rate = 0.10
        lift = float(rng.uniform(0.02, 0.05))
        daily_traffic = 6000
        correct_action = "pause"
    elif scenario == "guardrail_breach":
        control_rate = 0.10
        lift = float(rng.uniform(0.02, 0.05))
        daily_traffic = 6000
        correct_action = "rollback"
    else:  # underpowered
        control_rate = 0.10
        lift = 0.005
        daily_traffic = 800
        correct_action = "continue"

    treatment_rate = control_rate + lift

    day_stats: list[DayStats] = []
    for day in range(1, _N_DAYS + 1):
        if scenario == "srm" and day >= 4:
            control_ratio, treatment_ratio = 0.54, 0.46
        else:
            control_ratio, treatment_ratio = 0.5, 0.5

        control_n = max(int(rng.poisson(daily_traffic * control_ratio)), 1)
        treatment_n = max(int(rng.poisson(daily_traffic * treatment_ratio)), 1)

        control_conv = int(rng.binomial(control_n, control_rate))
        treatment_conv = int(rng.binomial(treatment_n, treatment_rate))

        guardrail_control_rate = float(rng.binomial(control_n, _GUARDRAIL_BASE_RATE)) / control_n
        if scenario == "guardrail_breach":
            guardrail_treatment_rate = guardrail_control_rate + _GUARDRAIL_BREACH_MARGIN
        else:
            guardrail_treatment_rate = float(rng.binomial(treatment_n, _GUARDRAIL_BASE_RATE)) / treatment_n

        day_stats.append(
            DayStats(
                experiment_id=exp_id,
                day=day,
                control_n=control_n,
                control_conversions=control_conv,
                treatment_n=treatment_n,
                treatment_conversions=treatment_conv,
                guardrail_control_rate=guardrail_control_rate,
                guardrail_treatment_rate=guardrail_treatment_rate,
            )
        )

    required_n_per_arm = power_analysis(control_rate, mde)
    estimated_days = math.ceil(required_n_per_arm * 2 / daily_traffic)

    config = ExperimentConfig(
        id=exp_id,
        hypothesis_id=hyp_id,
        flag_key="synthetic_flag",
        audience_segment="synthetic_segment",
        traffic_split={"control": 0.5, "treatment": 0.5},
        baseline_rate=control_rate,
        mde=mde,
        required_n_per_arm=required_n_per_arm,
        estimated_days=estimated_days,
        guardrail_metrics=["latency_breach_rate"],
        daily_traffic=daily_traffic,
        status="running",
    )

    ground_truth = {
        "scenario": scenario,
        "true_lift": lift,
        "correct_action": correct_action,
    }

    return config, day_stats, ground_truth
