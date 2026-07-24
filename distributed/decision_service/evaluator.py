"""The Decision Service evaluator. Pure, deterministic, stateless — no I/O, no LLM.

This is stats/core.py::decide() and ::_ready_to_call() re-expressed as data-driven
checks against a versioned RulePack instead of hardcoded module constants. The
control flow is intentionally identical to the original so the parity test in
test_evaluator.py can assert byte-for-byte agreement across real scenarios.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from distributed.decision_service.schemas import (
    CHECK_ACTION,
    Action,
    EvaluationInput,
    EvaluationResult,
    FiredCheck,
    RuleCheck,
    RulePack,
)

DEFAULT_PACK = RulePack(
    id="experiment-lifecycle",
    version="1.0.0",
    owner="experimentation-platform",
    description=(
        "The governed form of shared/models.py's decision constants. Matches "
        "stats.core.decide()/._ready_to_call() exactly as of the peeking-guard fix."
    ),
    created_at="2026-07-24T00:00:00+00:00",
    precedence=[RuleCheck.SRM, RuleCheck.GUARDRAIL, RuleCheck.SHIP, RuleCheck.KILL],
    srm_alpha=0.001,
    guardrail_margin=0.01,
    ship_prob_threshold=0.95,
    kill_prob_threshold=0.05,
    expected_loss_epsilon=0.0025,
    min_runtime_days=7,
)


def _is_ready(pack: RulePack, inp: EvaluationInput) -> bool:
    """Powered and past the minimum weekly cycle — the peeking guard, as data."""
    if inp.day < pack.min_runtime_days:
        return False
    return min(inp.control_n, inp.treatment_n) >= inp.required_n_per_arm


def _check_srm(pack: RulePack, inp: EvaluationInput) -> FiredCheck:
    return FiredCheck(
        check=RuleCheck.SRM,
        fired=inp.srm_flag,
        detail=f"srm_p_value={inp.srm_p_value:.4g} vs alpha={pack.srm_alpha}",
    )


def _check_guardrail(pack: RulePack, inp: EvaluationInput) -> FiredCheck:
    return FiredCheck(
        check=RuleCheck.GUARDRAIL,
        fired=inp.guardrail_breach,
        detail=f"guardrail_margin={inp.guardrail_margin:.4f} allowed={pack.guardrail_margin}",
    )


def _check_ship(pack: RulePack, inp: EvaluationInput) -> FiredCheck:
    ready = _is_ready(pack, inp)
    fired = (
        ready
        and inp.prob_beats_control >= pack.ship_prob_threshold
        and inp.expected_loss_ship <= pack.expected_loss_epsilon
    )
    return FiredCheck(
        check=RuleCheck.SHIP,
        fired=fired,
        detail=(
            f"ready={ready} (day={inp.day}>={pack.min_runtime_days}, "
            f"min_n={min(inp.control_n, inp.treatment_n)}>={inp.required_n_per_arm}) "
            f"prob={inp.prob_beats_control:.4f}>={pack.ship_prob_threshold} "
            f"loss_ship={inp.expected_loss_ship:.5f}<={pack.expected_loss_epsilon}"
        ),
    )


def _check_kill(pack: RulePack, inp: EvaluationInput) -> FiredCheck:
    ready = _is_ready(pack, inp)
    fired = ready and inp.prob_beats_control <= pack.kill_prob_threshold
    return FiredCheck(
        check=RuleCheck.KILL,
        fired=fired,
        detail=f"ready={ready} prob={inp.prob_beats_control:.4f}<={pack.kill_prob_threshold}",
    )


_CHECK_FNS = {
    RuleCheck.SRM: _check_srm,
    RuleCheck.GUARDRAIL: _check_guardrail,
    RuleCheck.SHIP: _check_ship,
    RuleCheck.KILL: _check_kill,
}


def _digest(inp: EvaluationInput) -> str:
    """Stable hash of the evaluation input, for audit replay verification."""
    return hashlib.sha256(inp.model_dump_json().encode()).hexdigest()[:16]


def evaluate(pack: RulePack, inp: EvaluationInput) -> EvaluationResult:
    """THE decision function, generalized. Same pack + same input -> same action, forever.

    Every check in pack.precedence runs and is recorded (fired or not) — the caller gets
    a full explanation trace, not just the winning check. The first check to fire, in
    precedence order, determines the action; if none fire, the action is "continue".
    """
    fired_checks: list[FiredCheck] = []
    action: Action = "continue"
    decided = False

    for check in pack.precedence:
        result = _CHECK_FNS[check](pack, inp)
        fired_checks.append(result)
        if not decided and result.fired:
            action = CHECK_ACTION[check]
            decided = True

    return EvaluationResult(
        experiment_id=inp.experiment_id,
        day=inp.day,
        pack_id=pack.id,
        pack_version=pack.version,
        action=action,
        fired_checks=fired_checks,
        inputs_digest=_digest(inp),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )
