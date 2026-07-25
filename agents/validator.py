"""Comprehensive pre-launch validation agent wrapper.

Delegates all validation checks to rules_engine/validator.py.
"""

from __future__ import annotations

from rules_engine.validator import (
    MAX_HORIZON_DAYS,
    Severity,
    ValidationIssue,
    ValidationReport,
    ValidationResult,
    _check_audience_overlap,
    _check_flag_availability,
    _check_guardrail_metrics,
    _check_power_feasibility,
    _check_segment_traffic,
    _check_traffic_split,
    validate_experiment,
    validate_hypothesis_spec,
)

__all__ = [
    "MAX_HORIZON_DAYS",
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "ValidationResult",
    "validate_experiment",
    "validate_hypothesis_spec",
    "_check_flag_availability",
    "_check_audience_overlap",
    "_check_traffic_split",
    "_check_power_feasibility",
    "_check_segment_traffic",
    "_check_guardrail_metrics",
]
