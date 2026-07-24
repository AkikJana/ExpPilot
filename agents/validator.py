"""Comprehensive pre-launch validation.

Covers Objective 3 ("validates setup before launch") and Objective 4 ("detects
configuration issues or overlapping experiments"). Every check is deterministic
and code-only — nothing here is asked of an LLM, and every referenced flag,
segment, or metric is checked against the real catalog tables rather than
trusted at face value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from data.db import get_conn
from shared.models import ExperimentConfig

Severity = Literal["blocking", "warning"]

MAX_HORIZON_DAYS = 30


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ValidationReport:
    issues: list[ValidationIssue]

    @property
    def blocking(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "blocking"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "blocking": [issue.as_dict() for issue in self.blocking],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }


def validate_experiment(config: ExperimentConfig, primary_metric_key: str | None = None) -> ValidationReport:
    """Run every check against the current catalog state and return a full report.

    Blocking issues must be resolved before the experiment may start; warnings
    surface risk without preventing launch.
    """
    issues: list[ValidationIssue] = []
    conn = get_conn()
    try:
        issues.extend(_check_flag_availability(conn, config))
        issues.extend(_check_audience_overlap(conn, config))
        issues.extend(_check_traffic_split(config))
        issues.extend(_check_power_feasibility(config))
        issues.extend(_check_segment_traffic(conn, config))
        issues.extend(_check_guardrail_metrics(conn, config, primary_metric_key))
    finally:
        conn.close()
    return ValidationReport(issues=issues)


def _check_flag_availability(conn, config: ExperimentConfig) -> list[ValidationIssue]:
    row = conn.execute("SELECT status FROM feature_flags WHERE flag_key = ?", (config.flag_key,)).fetchone()
    if row is None:
        # A synthetic per-experiment flag_key (legacy behavior) will not be in the
        # catalog; that is a warning, not a blocker, since it does not collide
        # with anything real. A catalog flag_key that is not free IS a blocker.
        return [
            ValidationIssue(
                "warning",
                "flag_not_cataloged",
                f"Feature flag '{config.flag_key}' is not in the flag catalog; it will not be "
                "discoverable by future recommendations or overlap checks.",
            )
        ]
    if row["status"] != "free":
        return [
            ValidationIssue(
                "blocking",
                "flag_unavailable",
                f"Feature flag '{config.flag_key}' is already '{row['status']}'.",
            )
        ]
    return []


def _check_audience_overlap(conn, config: ExperimentConfig) -> list[ValidationIssue]:
    rows = conn.execute(
        "SELECT running_experiment_id, key FROM flags "
        "WHERE segment = ? AND status = 'running' AND running_experiment_id IS NOT NULL",
        (config.audience_segment,),
    ).fetchall()
    issues = []
    for row in rows:
        if row["running_experiment_id"] != config.id:
            issues.append(
                ValidationIssue(
                    "blocking",
                    "audience_overlap",
                    f"Audience segment '{config.audience_segment}' already has a running experiment "
                    f"({row['running_experiment_id']}) on flag '{row['key']}'. Concurrent experiments on "
                    "the same audience can confound each other's results.",
                )
            )
    return issues


def _check_traffic_split(config: ExperimentConfig) -> list[ValidationIssue]:
    # Defense in depth: ExperimentConfig's own pydantic validator already
    # enforces this at construction time, so this should be unreachable in
    # practice. It stays here because "validates setup before launch" should
    # not silently rely on a constructor having already run.
    total = sum(config.traffic_split.values())
    if abs(total - 1.0) > 1e-6:
        return [
            ValidationIssue(
                "blocking", "traffic_split_invalid", f"Traffic split sums to {total:.4f}, not 1.0."
            )
        ]
    return []


def _check_power_feasibility(config: ExperimentConfig) -> list[ValidationIssue]:
    if config.estimated_days > MAX_HORIZON_DAYS:
        return [
            ValidationIssue(
                "warning",
                "underpowered_horizon",
                f"Estimated {config.estimated_days} days to reach {config.required_n_per_arm:,} per arm "
                f"exceeds the {MAX_HORIZON_DAYS}-day planning horizon at {config.daily_traffic:,}/day. "
                "Consider a larger minimum detectable effect, more traffic, or a longer commitment.",
            )
        ]
    return []


def _check_segment_traffic(conn, config: ExperimentConfig) -> list[ValidationIssue]:
    row = conn.execute(
        "SELECT daily_traffic FROM segments WHERE segment_key = ?", (config.audience_segment,)
    ).fetchone()
    if row is None:
        return [
            ValidationIssue(
                "warning",
                "segment_not_cataloged",
                f"Audience segment '{config.audience_segment}' is not in the segment catalog; traffic "
                "assumptions could not be checked.",
            )
        ]
    if config.daily_traffic > row["daily_traffic"]:
        return [
            ValidationIssue(
                "warning",
                "traffic_exceeds_segment",
                f"Requested daily_traffic ({config.daily_traffic:,}) exceeds segment "
                f"'{config.audience_segment}' actual daily traffic ({row['daily_traffic']:,}).",
            )
        ]
    return []


def _check_guardrail_metrics(
    conn, config: ExperimentConfig, primary_metric_key: str | None
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not config.guardrail_metrics:
        issues.append(
            ValidationIssue("warning", "no_guardrails", "No guardrail metrics are configured for this experiment.")
        )
    for metric_key in config.guardrail_metrics:
        if primary_metric_key and metric_key == primary_metric_key:
            issues.append(
                ValidationIssue(
                    "blocking",
                    "guardrail_equals_primary",
                    f"Guardrail metric '{metric_key}' is the same as the primary metric; a guardrail must "
                    "measure a different signal or it cannot detect a tradeoff.",
                )
            )
            continue
        row = conn.execute("SELECT kind FROM metrics_catalog WHERE metric_key = ?", (metric_key,)).fetchone()
        if row is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "guardrail_not_cataloged",
                    f"Guardrail metric '{metric_key}' is not in the metrics catalog.",
                )
            )
        elif row["kind"] != "guardrail":
            issues.append(
                ValidationIssue(
                    "warning",
                    "guardrail_wrong_kind",
                    f"Metric '{metric_key}' is cataloged as '{row['kind']}', not a guardrail.",
                )
            )
    return issues
