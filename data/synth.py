"""Synthetic experiment telemetry with a known ground-truth effect.

Transaction logs are observational: nobody was randomised into a control or
treatment arm, so no amount of historical data can produce a real A/B result.
To exercise the monitoring and decision half of the lifecycle you need telemetry
from an experiment that actually ran -- or an honest simulation of one.

This module is the simulation. You choose the true effect; it draws day-by-day
cumulative binomial samples for both arms and hands back the resulting DayStats
plus the ground truth it used. Because the true answer is known up front, the
decision engine can be *checked* rather than merely demonstrated: feed it a real
+2% lift and it should reach Scale; feed it zero lift and it should not.

Anything produced here is simulated and must be labelled as such wherever it is
shown. It is not, and cannot stand in for, production telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from shared.models import GUARDRAIL_MARGIN, DayStats, ExperimentConfig

# What the simulated world looks like. Named scenarios map onto the branches of
# the decision hierarchy so each one can be rehearsed deliberately.
Scenario = Literal["true_win", "no_effect", "true_loss", "srm", "guardrail_breach"]

_SCENARIOS: dict[Scenario, str] = {
    "true_win": "Treatment genuinely better -- the engine should reach Scale once powered.",
    "no_effect": "No real difference -- the engine should keep saying Continue, never Scale.",
    "true_loss": "Treatment genuinely worse -- the engine should reach Stop.",
    "srm": "Traffic split is broken -- the engine should Pause and refuse to analyse.",
    "guardrail_breach": "A guardrail degrades -- the engine should Rollback regardless of lift.",
}


@dataclass(frozen=True)
class SyntheticSpec:
    """How to simulate. `lift_abs` is an absolute rate difference, not relative."""

    scenario: Scenario = "true_win"
    lift_abs: float | None = None   # None -> a sensible default for the scenario
    days: int | None = None         # None -> the config's estimated_days (min 7)
    seed: int = 42

    def resolved_lift(self, config: ExperimentConfig) -> float:
        if self.lift_abs is not None:
            return self.lift_abs
        if self.scenario == "true_win":
            # Exactly the effect the experiment was powered to detect.
            return config.mde
        if self.scenario == "true_loss":
            return -config.mde
        return 0.0  # no_effect, srm, guardrail_breach all simulate a null lift

    def resolved_days(self, config: ExperimentConfig) -> int:
        if self.days is not None:
            return max(1, int(self.days))
        # Run at least past the readiness gate, or the engine can only ever say
        # Continue and the simulation proves nothing.
        return max(7, int(config.estimated_days))


def describe_scenarios() -> dict[str, str]:
    """Scenario -> what the decision engine is expected to do. For UI copy."""
    return dict(_SCENARIOS)


def synthesize(config: ExperimentConfig, spec: SyntheticSpec | None = None) -> dict:
    """Generate a cumulative day-by-day telemetry series for one experiment.

    Returns {"days": [DayStats, ...], "ground_truth": {...}}. Each DayStats is
    cumulative, matching how the real monitor endpoint is fed.
    """
    spec = spec or SyntheticSpec()
    rng = np.random.default_rng(spec.seed)

    lift = spec.resolved_lift(config)
    days = spec.resolved_days(config)

    control_rate = float(config.baseline_rate)
    treatment_rate = min(max(control_rate + lift, 0.0005), 0.9995)

    control_share = float(config.traffic_split.get("control", 0.5))
    treatment_share = float(config.traffic_split.get("treatment", 0.5))

    # An SRM is a *delivery* fault, not a statistical one: treatment silently
    # receives far less traffic than the split promises.
    if spec.scenario == "srm":
        treatment_share *= 0.72

    per_day_control = max(1, int(round(config.daily_traffic * control_share)))
    per_day_treatment = max(1, int(round(config.daily_traffic * treatment_share)))

    # Guardrails: a flat healthy rate, or treatment pushed clearly past the
    # breach margin. GUARDRAIL_MARGIN defaults to decrease_good, so a breach
    # means treatment's rate rises above control's by more than the margin.
    healthy_guardrail = 0.01
    if spec.scenario == "guardrail_breach":
        breached_guardrail = healthy_guardrail + GUARDRAIL_MARGIN * 2.5
    else:
        breached_guardrail = healthy_guardrail

    series: list[DayStats] = []
    control_n = control_conv = 0
    treatment_n = treatment_conv = 0

    for day in range(1, days + 1):
        control_n += per_day_control
        treatment_n += per_day_treatment
        control_conv += int(rng.binomial(per_day_control, control_rate))
        treatment_conv += int(rng.binomial(per_day_treatment, treatment_rate))

        series.append(
            DayStats(
                experiment_id=config.id,
                day=day,
                control_n=control_n,
                control_conversions=control_conv,
                treatment_n=treatment_n,
                treatment_conversions=treatment_conv,
                guardrail_control_rate=healthy_guardrail,
                guardrail_treatment_rate=breached_guardrail,
            )
        )

    observed_control = control_conv / control_n if control_n else 0.0
    observed_treatment = treatment_conv / treatment_n if treatment_n else 0.0

    return {
        "days": series,
        "ground_truth": {
            "synthetic": True,
            "scenario": spec.scenario,
            "scenario_note": _SCENARIOS[spec.scenario],
            "seed": spec.seed,
            "days": days,
            "true_lift_abs": round(lift, 6),
            "true_control_rate": round(control_rate, 6),
            "true_treatment_rate": round(treatment_rate, 6),
            "observed_control_rate": round(observed_control, 6),
            "observed_treatment_rate": round(observed_treatment, 6),
            "observed_lift_abs": round(observed_treatment - observed_control, 6),
            "expected_action": _expected_action(spec.scenario),
        },
    }


def _expected_action(scenario: Scenario) -> str:
    """What a correct engine should conclude once the experiment is powered."""
    return {
        "true_win": "scale",
        "no_effect": "continue",
        "true_loss": "stop",
        "srm": "pause",
        "guardrail_breach": "rollback",
    }[scenario]
