"""Parity tests: the extracted evaluator must agree with stats/core.py::decide exactly.

This is the whole point of the extraction. If these tests ever fail, the Decision
Service has silently diverged from the system it was supposed to generalize — that
is a shipped-bug-severity event, not a refactor nit.
"""
from __future__ import annotations

import itertools

import pytest

from data.synth import SCENARIOS, make_experiment
from distributed.decision_service.evaluator import DEFAULT_PACK, evaluate
from distributed.decision_service.schemas import EvaluationInput, RuleCheck
from shared.models import DayStats
from stats.core import compute_day_stats, decide


def _to_evaluation_input(experiment_id: str, day: int, stats, config) -> EvaluationInput:
    """Bridge a stats.core.StatsResult + ExperimentConfig into the Decision Service's
    narrower EvaluationInput — the same narrowing api/service.py would perform when
    calling out to a real Decision Service instead of stats.core.decide directly."""
    return EvaluationInput(
        experiment_id=experiment_id,
        day=day,
        srm_p_value=stats.srm_p_value,
        srm_flag=stats.srm_flag,
        prob_beats_control=stats.prob_beats_control,
        expected_loss_ship=stats.expected_loss_ship,
        expected_loss_keep=stats.expected_loss_keep,
        guardrail_breach=stats.guardrail_breach,
        guardrail_margin=stats.guardrail_margin,
        control_n=stats.control_n,
        treatment_n=stats.treatment_n,
        required_n_per_arm=config.required_n_per_arm,
    )


def _cumulative_through(day_stats: list[DayStats], day: int) -> DayStats:
    window = day_stats[:day]
    control_n = sum(d.control_n for d in window)
    treatment_n = sum(d.treatment_n for d in window)
    return DayStats(
        experiment_id=window[0].experiment_id,
        day=day,
        control_n=control_n,
        control_conversions=sum(d.control_conversions for d in window),
        treatment_n=treatment_n,
        treatment_conversions=sum(d.treatment_conversions for d in window),
        guardrail_control_rate=(
            sum(d.guardrail_control_rate * d.control_n for d in window) / control_n if control_n else 0.0
        ),
        guardrail_treatment_rate=(
            sum(d.guardrail_treatment_rate * d.treatment_n for d in window) / treatment_n if treatment_n else 0.0
        ),
    )


@pytest.mark.parametrize("scenario,seed,day", list(itertools.product(SCENARIOS, [100, 101, 102], [1, 6, 7, 10, 14])))
def test_evaluator_matches_stats_core_decide(scenario, seed, day):
    """Every scenario x seed x day the eval harness exercises must produce identical
    actions from both stats.core.decide() and the extracted evaluator."""
    config, day_stats, _ = make_experiment(scenario, seed)
    cumulative = _cumulative_through(day_stats, day)
    stats_result = compute_day_stats(cumulative, config, seed=0)

    expected_action = decide(stats_result, config)
    eval_input = _to_evaluation_input(config.id, day, stats_result, config)
    result = evaluate(DEFAULT_PACK, eval_input)

    assert result.action == expected_action, (
        f"{scenario} seed={seed} day={day}: stats.core.decide()={expected_action!r} "
        f"but evaluator={result.action!r}"
    )


def test_default_pack_precedence_matches_decide_docstring():
    """decide()'s stated precedence: SRM > guardrail > ship > kill > continue."""
    assert DEFAULT_PACK.precedence == [
        RuleCheck.SRM,
        RuleCheck.GUARDRAIL,
        RuleCheck.SHIP,
        RuleCheck.KILL,
    ]


def test_srm_beats_a_scale_worthy_posterior():
    """Precedence test, mirrored from stats/test_core.py: SRM must win regardless of
    how strong the Bayes numbers look."""
    inp = EvaluationInput(
        experiment_id="exp_precedence", day=10, srm_p_value=0.0001, srm_flag=True,
        prob_beats_control=0.99, expected_loss_ship=0.0001, expected_loss_keep=0.01,
        guardrail_breach=False, guardrail_margin=0.0, control_n=5000, treatment_n=5000,
        required_n_per_arm=3843,
    )
    assert evaluate(DEFAULT_PACK, inp).action == "pause"


def test_guardrail_beats_a_scale_worthy_posterior():
    inp = EvaluationInput(
        experiment_id="exp_precedence", day=10, srm_p_value=0.9, srm_flag=False,
        prob_beats_control=0.99, expected_loss_ship=0.0001, expected_loss_keep=0.01,
        guardrail_breach=True, guardrail_margin=0.03, control_n=5000, treatment_n=5000,
        required_n_per_arm=3843,
    )
    assert evaluate(DEFAULT_PACK, inp).action == "rollback"


def test_day_one_landslide_does_not_ship():
    """The peeking guard, re-verified through the evaluator: a day-1 landslide with
    5x the required sample size still must not ship before min_runtime_days."""
    inp = EvaluationInput(
        experiment_id="exp_peeking", day=1, srm_p_value=0.9, srm_flag=False,
        prob_beats_control=0.999, expected_loss_ship=0.0, expected_loss_keep=0.04,
        guardrail_breach=False, guardrail_margin=0.0, control_n=20000, treatment_n=20000,
        required_n_per_arm=3843,
    )
    assert evaluate(DEFAULT_PACK, inp).action == "continue"


def test_every_check_appears_in_the_fired_trace_regardless_of_outcome():
    """The explanation trace must be complete, not just the winning check — that is
    what makes an evaluation auditable rather than a black box."""
    inp = EvaluationInput(
        experiment_id="exp_trace", day=10, srm_p_value=0.9, srm_flag=False,
        prob_beats_control=0.6, expected_loss_ship=0.01, expected_loss_keep=0.01,
        guardrail_breach=False, guardrail_margin=0.0, control_n=4000, treatment_n=4000,
        required_n_per_arm=3843,
    )
    result = evaluate(DEFAULT_PACK, inp)
    assert {f.check for f in result.fired_checks} == set(RuleCheck)
    assert result.action == "continue"


def test_result_is_deterministic_across_repeated_calls():
    """Same pack, same input -> byte-identical action and fired-check trace, every time."""
    inp = EvaluationInput(
        experiment_id="exp_det", day=10, srm_p_value=0.9, srm_flag=False,
        prob_beats_control=0.97, expected_loss_ship=0.001, expected_loss_keep=0.02,
        guardrail_breach=False, guardrail_margin=0.0, control_n=4000, treatment_n=4000,
        required_n_per_arm=3843,
    )
    first = evaluate(DEFAULT_PACK, inp)
    second = evaluate(DEFAULT_PACK, inp)
    assert first.action == second.action
    assert first.inputs_digest == second.inputs_digest
    assert [f.model_dump() for f in first.fired_checks] == [f.model_dump() for f in second.fired_checks]


def test_precedence_must_cover_every_check_exactly_once():
    """A rule pack that silently omits a check is a validation error, not a valid policy."""
    from pydantic import ValidationError

    from distributed.decision_service.schemas import RulePack

    with pytest.raises(ValidationError):
        RulePack(
            id="broken", version="0.0.1", owner="test", description="missing guardrail check",
            created_at="2026-01-01T00:00:00+00:00",
            precedence=[RuleCheck.SRM, RuleCheck.SHIP, RuleCheck.KILL],
        )
