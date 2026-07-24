"""Typed contracts for the Decision Service (docs/distributed-architecture.md §4).

A RulePack is a versioned, governed document — the production generalization of the
five constants frozen in shared/models.py (SHIP_PROB_THRESHOLD and friends). Rule
*logic* stays a small fixed set of named, code-reviewed checks (no eval(), no
string-interpreted expressions); what becomes data is which checks run, in what
order, and at what thresholds.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, field_validator

Action = Literal["scale", "continue", "stop", "rollback", "pause"]


class RuleCheck(str, Enum):
    """The fixed set of named, code-reviewed decision checks. Not user-extensible."""

    SRM = "srm"
    GUARDRAIL = "guardrail"
    SHIP = "ship"
    KILL = "kill"


# Every check maps to exactly one action if it fires. This mapping is platform
# invariant, not something a rule pack can override — packs configure *thresholds*
# and *precedence*, never what a check means.
CHECK_ACTION: dict[RuleCheck, Action] = {
    RuleCheck.SRM: "pause",
    RuleCheck.GUARDRAIL: "rollback",
    RuleCheck.SHIP: "scale",
    RuleCheck.KILL: "stop",
}


class RulePack(BaseModel):
    """A versioned, owned, diffable decision policy. This is what PR review governs."""

    id: str
    version: str  # semver, e.g. "1.0.0"
    owner: str
    description: str
    created_at: str  # ISO-8601

    precedence: list[RuleCheck]

    # Trust/safety thresholds — these checks are never gated behind readiness.
    srm_alpha: float = 0.001
    guardrail_margin: float = 0.01

    # Ship/kill thresholds — gated behind min_runtime_days + required sample size,
    # because continuous monitoring inflates false positives (see decide_evals PR).
    ship_prob_threshold: float = 0.95
    kill_prob_threshold: float = 0.05
    expected_loss_epsilon: float = 0.0025
    min_runtime_days: int = 7

    @field_validator("precedence")
    @classmethod
    def _precedence_covers_every_check(cls, v: list[RuleCheck]) -> list[RuleCheck]:
        """A pack that silently omits a check (e.g. never checks guardrails) is a bug,
        not a policy choice — every RuleCheck must appear exactly once."""
        if sorted(v, key=lambda c: c.value) != sorted(set(RuleCheck), key=lambda c: c.value):
            raise ValueError(f"precedence must contain every RuleCheck exactly once, got {v}")
        return v


class EvaluationInput(BaseModel):
    """The subset of StatsResult + config the evaluator needs. Deliberately narrow:
    the Decision Service never sees narrative text, hypothesis text, or PII."""

    experiment_id: str
    day: int
    srm_p_value: float
    srm_flag: bool
    prob_beats_control: float
    expected_loss_ship: float
    expected_loss_keep: float
    guardrail_breach: bool
    guardrail_margin: float
    control_n: int
    treatment_n: int
    required_n_per_arm: int


class FiredCheck(BaseModel):
    """One line of the explanation trace: what was checked, and what it found."""

    check: RuleCheck
    fired: bool
    detail: str


class EvaluationResult(BaseModel):
    """The immutable record of one decision. inputs_digest makes replay verifiable:
    given the same pack version and the same digest, the action must reproduce."""

    experiment_id: str
    day: int
    pack_id: str
    pack_version: str
    action: Action
    fired_checks: list[FiredCheck]  # every check, in precedence order, fired or not
    inputs_digest: str
    evaluated_at: str
