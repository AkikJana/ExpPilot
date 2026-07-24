"""Segment-level driver diagnostics: WHY a variant is winning or underperforming.

Objective 6 asks the copilot to explain the *driver* of a result, not just
report the aggregate statistic. This module is a deterministic decomposition —
it reuses stats.core.freq_test on each segment's slice, then ranks segments by
how far their lift deviates from the experiment-wide lift. No LLM call here;
the prose that reads this out to a human lives in agents/narrator.py, which
may only restate numbers this module already computed.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.models import SegmentDayStats
from stats.core import freq_test

# Below this per-arm count, a segment's own lift is too noisy to classify with
# confidence; it is reported but flagged "inconclusive" rather than driving a
# claim about direction.
MIN_SEGMENT_N = 200

# A segment whose lift is within this fraction of the overall lift is "in
# line" rather than a distinct driver or drag.
_RELATIVE_BAND = 0.30

# Absolute floor for the "in line" band, in absolute rate terms. Without this,
# an overall lift near zero collapses the relative band to ~0 and classifies
# pure noise as a strong driver in either direction.
_ABSOLUTE_BAND_FLOOR = 0.01


@dataclass(frozen=True)
class SegmentDriver:
    segment_key: str
    control_n: int
    treatment_n: int
    lift_abs: float
    p_value: float
    deviation_from_overall: float
    classification: str  # "driving" | "in_line" | "dragging" | "inconclusive"

    def as_dict(self) -> dict:
        return {
            "segment_key": self.segment_key,
            "control_n": self.control_n,
            "treatment_n": self.treatment_n,
            "lift_abs": self.lift_abs,
            "p_value": self.p_value,
            "deviation_from_overall": self.deviation_from_overall,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class DriverAnalysis:
    experiment_id: str
    day: int
    overall_lift_abs: float
    drivers: list[SegmentDriver]

    @property
    def top_driver(self) -> SegmentDriver | None:
        confident = [d for d in self.drivers if d.classification in ("driving", "dragging")]
        return confident[0] if confident else None

    def as_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "day": self.day,
            "overall_lift_abs": self.overall_lift_abs,
            "drivers": [d.as_dict() for d in self.drivers],
        }


def _band(overall_lift_abs: float) -> float:
    return max(abs(overall_lift_abs) * _RELATIVE_BAND, _ABSOLUTE_BAND_FLOOR)


def analyze_drivers(overall_lift_abs: float, segments: list[SegmentDayStats]) -> DriverAnalysis:
    """Decompose an aggregate lift into per-segment contributions, ranked by
    how much each segment deviates from the overall result."""
    if not segments:
        raise ValueError("at least one segment slice is required for driver analysis")

    band = _band(overall_lift_abs)
    drivers: list[SegmentDriver] = []
    for segment in segments:
        if segment.control_n < MIN_SEGMENT_N or segment.treatment_n < MIN_SEGMENT_N:
            drivers.append(
                SegmentDriver(
                    segment_key=segment.segment_key,
                    control_n=segment.control_n,
                    treatment_n=segment.treatment_n,
                    lift_abs=0.0,
                    p_value=1.0,
                    deviation_from_overall=0.0,
                    classification="inconclusive",
                )
            )
            continue

        result = freq_test(
            segment.control_conversions, segment.control_n, segment.treatment_conversions, segment.treatment_n
        )
        deviation = result["lift_abs"] - overall_lift_abs
        if abs(deviation) <= band:
            classification = "in_line"
        elif deviation > 0:
            classification = "driving"
        else:
            classification = "dragging"

        drivers.append(
            SegmentDriver(
                segment_key=segment.segment_key,
                control_n=segment.control_n,
                treatment_n=segment.treatment_n,
                lift_abs=result["lift_abs"],
                p_value=result["p_value"],
                deviation_from_overall=deviation,
                classification=classification,
            )
        )

    drivers.sort(key=lambda d: abs(d.deviation_from_overall), reverse=True)
    return DriverAnalysis(
        experiment_id=segments[0].experiment_id,
        day=segments[0].day,
        overall_lift_abs=overall_lift_abs,
        drivers=drivers,
    )
