"""Pydantic v2 contracts shared across every ExpPilot module."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

SRM_ALPHA = 0.001
SHIP_PROB_THRESHOLD = 0.95
KILL_PROB_THRESHOLD = 0.05
EXPECTED_LOSS_EPSILON = 0.0025
GUARDRAIL_MARGIN = 0.01
MIN_RUNTIME_DAYS = 7


class Hypothesis(BaseModel):
    """A falsifiable hypothesis grounded in a business goal."""

    id: str
    goal: str
    statement: str
    primary_metric: str
    expected_direction: Literal["increase", "decrease"]
    expected_mde: float
    segment: str
    rationale: str
    precedent_ids: list[str]


class HypothesisSpec(BaseModel):
    """Structured hypothesis spec for pre-launch validation and experiment configuration."""

    hypothesis: str
    primary_metric: str
    guardrail_metrics: list[str] = []
    feature_flag_keys: list[str] = []
    target_audience: dict = {}


class ValidationIssue(BaseModel):
    """Diagnostic detail for a validation failure or warning."""

    severity: Literal["blocking", "warning"]
    code: str
    message: str

    def __init__(
        self,
        severity: Literal["blocking", "warning"] | None = None,
        code: str | None = None,
        message: str | None = None,
        **data,
    ):
        if severity is not None and "severity" not in data:
            data["severity"] = severity
        if code is not None and "code" not in data:
            data["code"] = code
        if message is not None and "message" not in data:
            data["message"] = message
        super().__init__(**data)

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


class ValidationResult(BaseModel):
    """Schema for pre-launch validation results."""

    is_valid: bool
    errors: list[str] = []
    warnings: list[str] = []


class ValidationReport(BaseModel):
    """Backward compatibility report wrapper for pre-launch validation passes."""

    issues: list[ValidationIssue] = []

    @property
    def blocking(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "blocking"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.blocking

    @property
    def is_valid(self) -> bool:
        return self.passed

    @property
    def errors(self) -> list[str]:
        return [issue.message for issue in self.blocking]

    def to_validation_result(self) -> ValidationResult:
        return ValidationResult(
            is_valid=self.passed,
            errors=[issue.message for issue in self.blocking],
            warnings=[issue.message for issue in self.warnings],
        )

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "blocking": [issue.as_dict() for issue in self.blocking],
            "warnings": [issue.as_dict() for issue in self.warnings],
            "is_valid": self.passed,
            "errors": self.errors,
        }



class ExperimentConfig(BaseModel):
    """The design-time configuration of an experiment."""

    id: str
    hypothesis_id: str
    flag_key: str
    audience_segment: str
    traffic_split: dict[str, float]
    baseline_rate: float
    mde: float
    alpha: float = 0.05
    power: float = 0.8
    required_n_per_arm: int
    estimated_days: int
    guardrail_metrics: list[str]
    daily_traffic: int
    status: Literal["draft", "validated", "running", "paused", "concluded"]

    @field_validator("traffic_split")
    @classmethod
    def _split_sums_to_one(cls, value: dict[str, float]) -> dict[str, float]:
        total = sum(value.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"traffic_split must sum to 1.0, got {total}")
        return value

    @field_validator("baseline_rate")
    @classmethod
    def _rate_in_unit_interval(cls, value: float) -> float:
        if not 0 < value < 1:
            raise ValueError(f"baseline_rate must be in (0, 1), got {value}")
        return value


class SegmentDayStats(BaseModel):
    """Per-segment slice of one day's telemetry, for driver diagnostics
    (Objective 6: explain why a variant is winning or underperforming).
    Optional alongside the aggregate DayStats — nothing requires it."""

    experiment_id: str
    day: int
    segment_key: str
    control_n: int
    control_conversions: int
    treatment_n: int
    treatment_conversions: int


class DayStats(BaseModel):
    experiment_id: str
    day: int
    control_n: int
    control_conversions: int
    treatment_n: int
    treatment_conversions: int
    guardrail_control_rate: float = 0.0
    guardrail_treatment_rate: float = 0.0
    guardrail_metrics_data: dict[str, dict[str, float]] = {}
    # Additive, optional: per-segment breakdown for the same day, unlocking
    # driver diagnostics (Objective 6). Omitted entirely, POST /monitor's
    # request body is unchanged from before this field existed.
    segments: list[SegmentDayStats] = []


class StatsResult(BaseModel):
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
    control_n: int = 0
    treatment_n: int = 0
    msprt_p_value: float = 1.0


class Alert(BaseModel):
    experiment_id: str
    day: int
    kind: Literal["srm", "guardrail", "underpowered", "none"]
    severity: Literal["info", "warning", "critical"]
    detail: str


class DecisionRecommendation(BaseModel):
    """Structured recommendation produced by the decision rules engine."""

    action: Literal["Scale", "Continue", "Stop", "Rollback", "Pause"]
    confidence_score: float
    risk_assessment: dict
    explainable_summary: str

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, value: str) -> str:
        if isinstance(value, str):
            val_title = value.title()
            if val_title in ("Scale", "Continue", "Stop", "Rollback", "Pause"):
                return val_title
        return str(value)

    @field_validator("confidence_score")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @property
    def action_code(self) -> str:
        return self.action.lower()


class Decision(BaseModel):
    experiment_id: str
    day: int
    action: Literal["scale", "continue", "stop", "rollback", "pause", "Scale", "Continue", "Stop", "Rollback", "Pause"]
    confidence: float
    reasoning_stats: StatsResult
    narrative: str
    requires_human: bool
    human_verdict: Literal["pending", "approved", "rejected"] | None
    human_reason: str | None
    recommendation: DecisionRecommendation | None = None

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, value: str) -> str:
        if isinstance(value, str):
            val_lower = value.lower()
            if val_lower in ("scale", "continue", "stop", "rollback", "pause"):
                return val_lower
        return str(value)

    @property
    def action_code(self) -> str:
        return self.action.lower()



class MemoryRecord(BaseModel):
    id: str
    kind: Literal["episodic", "lesson", "exemplar"]
    category: str
    content: str
    source_experiment_id: str | None
    created_at: str
