"""Gold scenario set for the recommendation-accuracy eval.

Each scenario carries a hand-labeled `correct_action` (the expert decision) produced
by the synthetic engine's ground truth. The harness compares the copilot's
deterministic decision against these labels. This is the offline proxy for the
"recommendation accuracy vs expert decisions" evaluation metric.
"""
from __future__ import annotations

from data.synth import SCENARIOS, make_experiment
from shared.models import DayStats


def _cumulative(days: list[DayStats], upto: int, experiment_id: str) -> DayStats:
    cn = sum(d.control_n for d in days[:upto])
    tn = sum(d.treatment_n for d in days[:upto])
    return DayStats(
        experiment_id=experiment_id,
        day=upto,
        control_n=cn,
        control_conversions=sum(d.control_conversions for d in days[:upto]),
        treatment_n=tn,
        treatment_conversions=sum(d.treatment_conversions for d in days[:upto]),
        # Traffic-weighted cumulative guardrail rates (matches agents.tools).
        guardrail_control_rate=sum(d.guardrail_control_rate * d.control_n for d in days[:upto]) / cn,
        guardrail_treatment_rate=sum(d.guardrail_treatment_rate * d.treatment_n for d in days[:upto]) / tn,
    )


def build_gold_set(seeds_per_scenario: int = 3, base_seed: int = 7000) -> list[dict]:
    """Return a labeled gold set spanning every scenario across multiple seeds."""
    gold: list[dict] = []
    for scenario in SCENARIOS:
        for i in range(seeds_per_scenario):
            seed = base_seed + i
            config, days, ground_truth = make_experiment(scenario, seed)
            final = _cumulative(days, len(days), config.id)
            gold.append(
                {
                    "id": f"{scenario}_{seed}",
                    "scenario": scenario,
                    "seed": seed,
                    "config": config,
                    "final_cumulative": final,
                    "expected_action": ground_truth["correct_action"],
                    "true_lift": ground_truth["true_lift"],
                }
            )
    return gold
