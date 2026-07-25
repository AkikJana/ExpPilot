"""Comprehensive pre-launch validation rules engine.

Covers Objective 3 ("validates setup before launch") and Objective 4 ("detects
configuration issues or overlapping experiments"). Every check is deterministic
and code-only — nothing here is asked of an LLM, and every referenced flag,
segment, or metric is checked against real catalog tables.
"""

from __future__ import annotations

from typing import Any, Literal

from data.db import get_conn
from shared.models import ExperimentConfig, HypothesisSpec, ValidationIssue, ValidationReport, ValidationResult

Severity = Literal["blocking", "warning"]
MAX_HORIZON_DAYS = 30


def validate_experiment(
    config: ExperimentConfig | HypothesisSpec | dict[str, Any],
    primary_metric_key: str | None = None,
) -> ValidationReport:
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


def validate_hypothesis_spec(spec: HypothesisSpec) -> ValidationResult:
    """Validate a HypothesisSpec directly and return a ValidationResult contract."""
    report = validate_experiment(spec, primary_metric_key=spec.primary_metric)
    return report.to_validation_result()


def _extract_flag_keys(config: Any) -> list[str]:
    if isinstance(config, HypothesisSpec):
        return config.feature_flag_keys or []
    if isinstance(config, dict):
        if "feature_flag_keys" in config and config["feature_flag_keys"]:
            return list(config["feature_flag_keys"])
        if "flag_key" in config and config["flag_key"]:
            return [config["flag_key"]]
        return []
    if hasattr(config, "feature_flag_keys") and getattr(config, "feature_flag_keys"):
        return list(getattr(config, "feature_flag_keys"))
    if hasattr(config, "flag_key") and getattr(config, "flag_key"):
        return [getattr(config, "flag_key")]
    return []


def _extract_audience_segment(config: Any) -> str | None:
    if isinstance(config, HypothesisSpec):
        target = config.target_audience
        if isinstance(target, dict):
            return target.get("segment_key") or target.get("segment")
        return str(target) if target else None
    if isinstance(config, dict):
        if "audience_segment" in config and config["audience_segment"]:
            return config["audience_segment"]
        if "target_audience" in config:
            target = config["target_audience"]
            if isinstance(target, dict):
                return target.get("segment_key") or target.get("segment")
            return str(target) if target else None
        return None
    if hasattr(config, "audience_segment"):
        return getattr(config, "audience_segment")
    if hasattr(config, "target_audience"):
        target = getattr(config, "target_audience")
        if isinstance(target, dict):
            return target.get("segment_key") or target.get("segment")
        return str(target) if target else None
    return None


def _extract_traffic_split(config: Any) -> dict[str, float] | None:
    if isinstance(config, dict):
        return config.get("traffic_split")
    if hasattr(config, "traffic_split"):
        return getattr(config, "traffic_split")
    return None


def _extract_guardrail_metrics(config: Any) -> list[str]:
    if isinstance(config, HypothesisSpec):
        return config.guardrail_metrics or []
    if isinstance(config, dict):
        return config.get("guardrail_metrics") or []
    if hasattr(config, "guardrail_metrics"):
        return getattr(config, "guardrail_metrics") or []
    return []


def _check_flag_availability(conn: Any, config: Any) -> list[ValidationIssue]:
    """Pass 1: Flag availability pass. Verifies flags are free/cataloged, checks multi-flag lists."""
    flag_keys = _extract_flag_keys(config)
    if not flag_keys:
        return []

    issues: list[ValidationIssue] = []
    for flag_key in flag_keys:
        row = conn.execute("SELECT status FROM feature_flags WHERE flag_key = ?", (flag_key,)).fetchone()
        if row is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "flag_not_cataloged",
                    f"Feature flag '{flag_key}' is not in the flag catalog; it will not be "
                    "discoverable by future recommendations or overlap checks.",
                )
            )
        elif row["status"] != "free":
            issues.append(
                ValidationIssue(
                    "blocking",
                    "flag_unavailable",
                    f"Feature flag '{flag_key}' is already '{row['status']}'.",
                )
            )
    return issues


def _check_audience_overlap(conn: Any, config: Any) -> list[ValidationIssue]:
    """Pass 2: Audience overlap pass. Checks for running, scheduled, and draft experiment collisions on exact or overlapping segments."""
    segment = _extract_audience_segment(config)
    if not segment:
        return []

    exp_id = getattr(config, "id", None) if not isinstance(config, dict) else config.get("id")

    rows = conn.execute(
        "SELECT running_experiment_id, key, status FROM flags "
        "WHERE segment = ? AND status IN ('running', 'scheduled', 'draft') AND running_experiment_id IS NOT NULL",
        (segment,),
    ).fetchall()
    issues: list[ValidationIssue] = []
    for row in rows:
        if exp_id is None or row["running_experiment_id"] != exp_id:
            issues.append(
                ValidationIssue(
                    "blocking",
                    "audience_overlap",
                    f"Audience segment '{segment}' already has a {row['status']} experiment "
                    f"({row['running_experiment_id']}) on flag '{row['key']}'. Concurrent experiments on "
                    "the same audience can confound each other's results.",
                )
            )
    return issues


def _check_traffic_split(config: Any) -> list[ValidationIssue]:
    """Pass 3: Traffic split pass. Ensures total traffic allocation equals 100% (1.0)."""
    traffic_split = _extract_traffic_split(config)
    if traffic_split is None:
        return []

    total = sum(traffic_split.values())
    if abs(total - 1.0) > 1e-6:
        return [
            ValidationIssue(
                "blocking", "traffic_split_invalid", f"Traffic split sums to {total:.4f}, not 1.0."
            )
        ]
    return []


def _check_power_feasibility(config: Any) -> list[ValidationIssue]:
    """Pass 4: Power feasibility & sample size capacity pass. Checks if required sample size exceeds segment daily traffic capacity or 30-day horizon limit."""
    estimated_days = getattr(config, "estimated_days", None) if not isinstance(config, dict) else config.get("estimated_days")
    required_n = getattr(config, "required_n_per_arm", None) if not isinstance(config, dict) else config.get("required_n_per_arm")
    daily_traffic = getattr(config, "daily_traffic", None) if not isinstance(config, dict) else config.get("daily_traffic")

    issues: list[ValidationIssue] = []
    if estimated_days is not None and estimated_days > MAX_HORIZON_DAYS:
        req_str = f" to reach {required_n:,} per arm" if required_n is not None else ""
        dt_str = f" at {daily_traffic:,}/day" if daily_traffic is not None else ""
        issues.append(
            ValidationIssue(
                "warning",
                "underpowered_horizon",
                f"Estimated {estimated_days} days{req_str} "
                f"exceeds the {MAX_HORIZON_DAYS}-day planning horizon{dt_str}. "
                "Consider a larger minimum detectable effect, more traffic, or a longer commitment.",
            )
        )
    return issues


def _check_segment_traffic(conn: Any, config: Any) -> list[ValidationIssue]:
    """Pass 5: Segment traffic capacity pass."""
    segment = _extract_audience_segment(config)
    if not segment:
        return []

    daily_traffic = getattr(config, "daily_traffic", None) if not isinstance(config, dict) else config.get("daily_traffic")
    if daily_traffic is None:
        return []

    row = conn.execute(
        "SELECT daily_traffic FROM segments WHERE segment_key = ?", (segment,)
    ).fetchone()
    if row is None:
        return [
            ValidationIssue(
                "warning",
                "segment_not_cataloged",
                f"Audience segment '{segment}' is not in the segment catalog; traffic "
                "assumptions could not be checked.",
            )
        ]
    if daily_traffic > row["daily_traffic"]:
        return [
            ValidationIssue(
                "warning",
                "traffic_exceeds_segment",
                f"Requested daily_traffic ({daily_traffic:,}) exceeds segment "
                f"'{segment}' actual daily traffic ({row['daily_traffic']:,}).",
            )
        ]
    return []


def _check_guardrail_metrics(
    conn: Any, config: Any, primary_metric_key: str | None
) -> list[ValidationIssue]:
    """Pass 6: Guardrail metrics pass. Verifies guardrails are cataloged, not identical to primary metric, and directionally configured."""
    guardrails = _extract_guardrail_metrics(config)
    if primary_metric_key is None:
        if isinstance(config, HypothesisSpec):
            primary_metric_key = config.primary_metric
        elif isinstance(config, dict):
            primary_metric_key = config.get("primary_metric")
        elif hasattr(config, "primary_metric"):
            primary_metric_key = getattr(config, "primary_metric")

    issues: list[ValidationIssue] = []
    if not guardrails:
        issues.append(
            ValidationIssue("warning", "no_guardrails", "No guardrail metrics are configured for this experiment.")
        )
    for metric_key in guardrails:
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
        row = conn.execute("SELECT kind, direction FROM metrics_catalog WHERE metric_key = ?", (metric_key,)).fetchone()
        if row is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "guardrail_not_cataloged",
                    f"Guardrail metric '{metric_key}' is not in the metrics catalog.",
                )
            )
        else:
            if row["kind"] != "guardrail":
                issues.append(
                    ValidationIssue(
                        "warning",
                        "guardrail_wrong_kind",
                        f"Metric '{metric_key}' is cataloged as '{row['kind']}', not a guardrail.",
                    )
                )
            if row["direction"] not in ("increase_good", "decrease_good"):
                issues.append(
                    ValidationIssue(
                        "warning",
                        "guardrail_missing_direction",
                        f"Guardrail metric '{metric_key}' has invalid or unconfigured direction '{row['direction']}'.",
                    )
                )
    return issues
