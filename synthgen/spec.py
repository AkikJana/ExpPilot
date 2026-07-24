"""Typed scenario specifications produced by the LLM layer and consumed by the simulator.

The LLM only ever emits/edits this structured spec; the simulator turns it into
row-level data deterministically. This keeps generation grounded and reproducible.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MetricType = Literal["binary", "continuous"]


class Segment(BaseModel):
    """A user sub-population with its own weight and treatment responsiveness."""

    name: str
    weight: float = Field(gt=0)                 # relative share; normalized at sim time
    effect_multiplier: float = 1.0             # scales the true effect for this segment
    base_shift: float = 0.0                    # additive shift to the base metric


class Guardrail(BaseModel):
    """A secondary 'do no harm' metric (e.g. latency-breach / crash rate)."""

    name: str = "guardrail_rate"
    base_rate: float = 0.05
    treatment_delta: float = 0.0               # >0 means treatment worsens the guardrail


class ScenarioRequest(BaseModel):
    """A natural-language ask that the LLM layer turns into a ScenarioSpec."""

    goal: str
    hint_metric: MetricType = "binary"
    n_rows: int | None = None
    seed: int | None = None


class ScenarioSpec(BaseModel):
    """Fully specified, reproducible synthetic experiment."""

    name: str
    hypothesis: str
    metric_type: MetricType = "binary"

    # binary metric params
    base_rate: float = 0.10
    # continuous metric params
    base_mean: float = 0.0
    base_sd: float = 1.0

    true_effect: float = 0.02                  # absolute lift (binary) or mean shift (continuous)
    variants: list[str] = ["control", "treatment"]
    allocation: dict[str, float] = {"control": 0.5, "treatment": 0.5}

    segments: list[Segment] = [Segment(name="all_users", weight=1.0)]
    guardrail: Guardrail = Guardrail()

    n_days: int = 14
    daily_traffic: int = 6000

    # quality-issue injectors (for realistic, testable scenarios)
    srm: bool = False
    srm_ratio: float = 0.54                    # control share when SRM present
    novelty: bool = False                      # early-period inflated effect that decays

    # pre-experiment covariate correlation (enables CUPED-style variance reduction)
    covariate_correlation: float = 0.5

    correct_action: str | None = None          # optional expert label (evals only)

    @field_validator("allocation")
    @classmethod
    def _alloc_sums_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"allocation must sum to 1.0, got {total}")
        return v

    @field_validator("base_rate")
    @classmethod
    def _rate_unit_interval(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError(f"base_rate must be in (0,1), got {v}")
        return v

    @property
    def n_rows(self) -> int:
        return self.n_days * self.daily_traffic
