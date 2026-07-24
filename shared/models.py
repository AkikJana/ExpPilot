"""Pydantic v2 contracts shared across every ExpPilot module. Frozen after Section 1 acceptance."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

SRM_ALPHA = 0.001
SHIP_PROB_THRESHOLD = 0.95
KILL_PROB_THRESHOLD = 0.05
EXPECTED_LOSS_EPSILON = 0.0025
GUARDRAIL_MARGIN = 0.01


class Hypothesis(BaseModel):
    """A falsifiable, LLM-proposed hypothesis grounded in a business goal."""

    id: str
    goal: str
    statement: str
    primary_metric: Literal["conversion_rate"]
    expected_direction: Literal["increase", "decrease"]
    expected_mde: float
    segment: str
    rationale: str
    precedent_ids: list[str]


class ExperimentConfig(BaseModel):
    """The design-time configuration of an experiment; power-analysis fields are code-computed."""

    id: str
    hypothesis_id: str
    flag_key: str
    audience_segment: str
    traffic_split: dict[str, float]
    baseline_rate: float
    mde: float
    alpha: float = 0.05
    power: float = 0.80
    required_n_per_arm: int
    estimated_days: int
    guardrail_metrics: list[str]
    daily_traffic: int
    status: Literal["draft", "validated", "running", "paused", "concluded"]

    @field_validator("traffic_split")
    @classmethod
    def _split_sums_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        """Reject traffic splits that do not sum to 1.0 (within floating-point tolerance)."""
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"traffic_split must sum to 1.0, got {total}")
        return v

    @field_validator("baseline_rate")
    @classmethod
    def _rate_in_unit_interval(cls, v: float) -> float:
        """Reject a baseline_rate outside the open interval (0, 1)."""
        if not (0 < v < 1):
            raise ValueError(f"baseline_rate must be in (0, 1), got {v}")
        return v


class DayStats(BaseModel):
    """One day's raw counts for a running experiment."""

    experiment_id: str
    day: int
    control_n: int
    control_conversions: int
    treatment_n: int
    treatment_conversions: int
    guardrail_control_rate: float
    guardrail_treatment_rate: float


class StatsResult(BaseModel):
    """Deterministic output of stats/core.py for a given day's cumulative counts."""

    experiment_id: str
    day: int
    srm_p_value: float
    srm_flag: bool
    z_stat: float
    p_value: float
    lift_abs: float
    ci_low: float
    ci_high: float
    prob_beats_control: float
    expected_loss_ship: float
    expected_loss_keep: float
    guardrail_breach: bool
    guardrail_margin: float


class Alert(BaseModel):
    """A typed alert derived from a StatsResult by the monitor node."""

    experiment_id: str
    day: int
    kind: Literal["srm", "guardrail", "underpowered", "none"]
    severity: Literal["info", "warning", "critical"]
    detail: str


class Decision(BaseModel):
    """The action recommendation, always derived from stats.core.decide."""

    experiment_id: str
    day: int
    action: Literal["scale", "continue", "stop", "rollback", "pause"]
    confidence: float
    reasoning_stats: StatsResult
    narrative: str
    requires_human: bool
    human_verdict: Literal["pending", "approved", "rejected"] | None
    human_reason: str | None


class MemoryRecord(BaseModel):
    """A long-term memory entry: episodic outcome, distilled lesson, or gold exemplar."""

    id: str
    kind: Literal["episodic", "lesson", "exemplar"]
    category: str
    content: str
    source_experiment_id: str | None
    created_at: str
