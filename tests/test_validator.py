"""Unit tests for rules_engine/validator.py (Pre-launch Validation Rules)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.validator import (
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
from data import db
from data.seed import ensure_seeded
from shared.models import ExperimentConfig, HypothesisSpec


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"
        ensure_seeded()

    def tearDown(self) -> None:
        try:
            self.tempdir.cleanup()
        except Exception:
            pass

    def _make_config(
        self,
        flag_key: str = "checkout_one_page",
        audience_segment: str = "mobile_users",
        traffic_split: dict[str, float] | None = None,
        estimated_days: int = 14,
        daily_traffic: int = 2000,
        guardrail_metrics: list[str] | None = None,
    ) -> ExperimentConfig:
        return ExperimentConfig(
            id="exp_val_test",
            hypothesis_id="hyp_val_test",
            flag_key=flag_key,
            audience_segment=audience_segment,
            traffic_split=traffic_split or {"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=10000,
            estimated_days=estimated_days,
            guardrail_metrics=guardrail_metrics if guardrail_metrics is not None else ["checkout_abandon_rate"],
            daily_traffic=daily_traffic,
            status="draft",
        )

    def test_valid_experiment_passes_validation(self) -> None:
        config = self._make_config()
        report = validate_experiment(config, primary_metric_key="conversion_rate")
        self.assertTrue(report.passed)
        self.assertEqual(len(report.blocking), 0)

    def test_flag_availability_rules(self) -> None:
        conn = db.get_conn()
        try:
            # Free flag -> No issues
            config_free = self._make_config(flag_key="checkout_one_page")
            issues_free = _check_flag_availability(conn, config_free)
            self.assertEqual(len(issues_free), 0)

            # Occupied flag -> Blocking error
            conn.execute("UPDATE feature_flags SET status = 'running' WHERE flag_key = 'checkout_one_page'")
            conn.commit()
            issues_busy = _check_flag_availability(conn, config_free)
            self.assertEqual(len(issues_busy), 1)
            self.assertEqual(issues_busy[0].severity, "blocking")
            self.assertEqual(issues_busy[0].code, "flag_unavailable")

            # Uncataloged synthetic flag -> Warning
            config_uncataloged = self._make_config(flag_key="synthetic_flag_123")
            issues_uncataloged = _check_flag_availability(conn, config_uncataloged)
            self.assertEqual(len(issues_uncataloged), 1)
            self.assertEqual(issues_uncataloged[0].severity, "warning")
            self.assertEqual(issues_uncataloged[0].code, "flag_not_cataloged")
        finally:
            conn.close()

    def test_audience_overlap_rules(self) -> None:
        conn = db.get_conn()
        try:
            config = self._make_config(audience_segment="mobile_users")

            # No running experiment -> Pass
            issues_clean = _check_audience_overlap(conn, config)
            self.assertEqual(len(issues_clean), 0)

            # Insert another running experiment on same audience
            conn.execute(
                "INSERT INTO flags(key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)",
                ("other_flag", "mobile_users", "running", "exp_other"),
            )
            conn.commit()

            issues_overlap = _check_audience_overlap(conn, config)
            self.assertEqual(len(issues_overlap), 1)
            self.assertEqual(issues_overlap[0].severity, "blocking")
            self.assertEqual(issues_overlap[0].code, "audience_overlap")
        finally:
            conn.close()

    def test_traffic_split_rule(self) -> None:
        config_valid = self._make_config(traffic_split={"control": 0.5, "treatment": 0.5})
        issues_valid = _check_traffic_split(config_valid)
        self.assertEqual(len(issues_valid), 0)

    def test_power_feasibility_rule(self) -> None:
        # Horizon <= 30 days -> Pass
        config_ok = self._make_config(estimated_days=20)
        issues_ok = _check_power_feasibility(config_ok)
        self.assertEqual(len(issues_ok), 0)

        # Horizon > 30 days -> Warning
        config_underpowered = self._make_config(estimated_days=45)
        issues_underpowered = _check_power_feasibility(config_underpowered)
        self.assertEqual(len(issues_underpowered), 1)
        self.assertEqual(issues_underpowered[0].severity, "warning")
        self.assertEqual(issues_underpowered[0].code, "underpowered_horizon")

    def test_segment_traffic_rules(self) -> None:
        conn = db.get_conn()
        try:
            # Reasonable traffic -> Pass
            config_ok = self._make_config(audience_segment="mobile_users", daily_traffic=10000)
            issues_ok = _check_segment_traffic(conn, config_ok)
            self.assertEqual(len(issues_ok), 0)

            # Exceeds segment capacity -> Warning
            config_exceed = self._make_config(audience_segment="mobile_users", daily_traffic=999999)
            issues_exceed = _check_segment_traffic(conn, config_exceed)
            self.assertEqual(len(issues_exceed), 1)
            self.assertEqual(issues_exceed[0].severity, "warning")
            self.assertEqual(issues_exceed[0].code, "traffic_exceeds_segment")

            # Missing segment -> Warning
            config_missing = self._make_config(audience_segment="non_existent_segment")
            issues_missing = _check_segment_traffic(conn, config_missing)
            self.assertEqual(len(issues_missing), 1)
            self.assertEqual(issues_missing[0].severity, "warning")
            self.assertEqual(issues_missing[0].code, "segment_not_cataloged")
        finally:
            conn.close()

    def test_guardrail_metrics_rules(self) -> None:
        conn = db.get_conn()
        try:
            # Empty guardrails -> Warning
            config_empty = self._make_config(guardrail_metrics=[])
            issues_empty = _check_guardrail_metrics(conn, config_empty, primary_metric_key="conversion_rate")
            self.assertEqual(len(issues_empty), 1)
            self.assertEqual(issues_empty[0].code, "no_guardrails")

            # Guardrail equals primary -> Blocking
            config_same = self._make_config(guardrail_metrics=["conversion_rate"])
            issues_same = _check_guardrail_metrics(conn, config_same, primary_metric_key="conversion_rate")
            self.assertEqual(len(issues_same), 1)
            self.assertEqual(issues_same[0].severity, "blocking")
            self.assertEqual(issues_same[0].code, "guardrail_equals_primary")

            # Missing from catalog -> Warning
            config_unknown = self._make_config(guardrail_metrics=["unknown_guardrail"])
            issues_unknown = _check_guardrail_metrics(conn, config_unknown, primary_metric_key="conversion_rate")
            self.assertEqual(len(issues_unknown), 1)
            self.assertEqual(issues_unknown[0].code, "guardrail_not_cataloged")

            # Wrong metric kind -> Warning
            config_wrong_kind = self._make_config(guardrail_metrics=["conversion_rate"])
            issues_wrong_kind = _check_guardrail_metrics(conn, config_wrong_kind, primary_metric_key="other_metric")
            self.assertEqual(len(issues_wrong_kind), 1)
            self.assertEqual(issues_wrong_kind[0].code, "guardrail_wrong_kind")
        finally:
            conn.close()

    def test_hypothesis_spec_validation(self) -> None:
        spec = HypothesisSpec(
            hypothesis="Personalized onboarding increases retention",
            primary_metric="retention_d30",
            guardrail_metrics=["crash_free_rate", "error_rate"],
            feature_flag_keys=["checkout_one_page"],
            target_audience={"segment_key": "mobile_users"},
        )
        res = validate_hypothesis_spec(spec)
        self.assertIsInstance(res, ValidationResult)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.errors), 0)


if __name__ == "__main__":
    unittest.main()
