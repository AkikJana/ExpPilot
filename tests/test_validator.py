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

    def test_multi_flag_specs_validation(self) -> None:
        """Adversarial test for multi-flag specs checking free, busy, and uncataloged flags simultaneously."""
        conn = db.get_conn()
        try:
            # Mark one flag as running
            conn.execute("UPDATE feature_flags SET status = 'running' WHERE flag_key = 'cart_drawer_v2'")
            conn.commit()

            # Spec with 3 flags: 1 free ('checkout_one_page'), 1 busy ('cart_drawer_v2'), 1 uncataloged ('non_existent_flag')
            spec = HypothesisSpec(
                hypothesis="Multi-flag optimization rollout",
                primary_metric="conversion_rate",
                guardrail_metrics=["checkout_abandon_rate"],
                feature_flag_keys=["checkout_one_page", "cart_drawer_v2", "non_existent_flag"],
                target_audience={"segment_key": "mobile_users"},
            )

            issues = _check_flag_availability(conn, spec)
            self.assertEqual(len(issues), 2)
            codes = {issue.code for issue in issues}
            severities = {issue.code: issue.severity for issue in issues}

            self.assertIn("flag_unavailable", codes)
            self.assertEqual(severities["flag_unavailable"], "blocking")

            self.assertIn("flag_not_cataloged", codes)
            self.assertEqual(severities["flag_not_cataloged"], "warning")
        finally:
            conn.close()

    def test_audience_overlap_draft_and_scheduled(self) -> None:
        """Adversarial test for overlapping draft and scheduled experiment segments."""
        conn = db.get_conn()
        try:
            # Insert a draft experiment on mobile_users and a scheduled experiment on desktop_users
            conn.execute(
                "INSERT INTO flags(key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)",
                ("draft_flag", "mobile_users", "draft", "exp_draft_100"),
            )
            conn.execute(
                "INSERT INTO flags(key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)",
                ("sched_flag", "desktop_users", "scheduled", "exp_sched_200"),
            )
            conn.commit()

            # 1. Config targeting mobile_users should collide with draft experiment
            config_mobile = self._make_config(audience_segment="mobile_users")
            issues_mobile = _check_audience_overlap(conn, config_mobile)
            self.assertEqual(len(issues_mobile), 1)
            self.assertEqual(issues_mobile[0].severity, "blocking")
            self.assertEqual(issues_mobile[0].code, "audience_overlap")
            self.assertIn("draft experiment (exp_draft_100)", issues_mobile[0].message)

            # 2. Config targeting desktop_users should collide with scheduled experiment
            config_desktop = self._make_config(audience_segment="desktop_users")
            issues_desktop = _check_audience_overlap(conn, config_desktop)
            self.assertEqual(len(issues_desktop), 1)
            self.assertEqual(issues_desktop[0].severity, "blocking")
            self.assertEqual(issues_desktop[0].code, "audience_overlap")
            self.assertIn("scheduled experiment (exp_sched_200)", issues_desktop[0].message)

            # 3. Same experiment ID checking itself should NOT trigger an overlap issue
            config_self = ExperimentConfig(
                id="exp_draft_100",
                hypothesis_id="hyp_draft",
                flag_key="draft_flag",
                audience_segment="mobile_users",
                traffic_split={"control": 0.5, "treatment": 0.5},
                baseline_rate=0.10,
                mde=0.01,
                required_n_per_arm=10000,
                estimated_days=14,
                guardrail_metrics=["checkout_abandon_rate"],
                daily_traffic=2000,
                status="draft",
            )
            issues_self = _check_audience_overlap(conn, config_self)
            self.assertEqual(len(issues_self), 0)

            # 4. Malformed target_audience structure formats (dict with 'segment', dict with 'segment_key', string, None)
            dict_spec_1 = {"target_audience": {"segment": "mobile_users"}}
            issues_dict_1 = _check_audience_overlap(conn, dict_spec_1)
            self.assertEqual(len(issues_dict_1), 1)

            dict_spec_none = {"target_audience": None}
            issues_none = _check_audience_overlap(conn, dict_spec_none)
            self.assertEqual(len(issues_none), 0)
        finally:
            conn.close()

    def test_invalid_traffic_splits_comprehensive(self) -> None:
        """Adversarial test for invalid traffic split allocations (sum != 1.0, single-arm, multi-arm)."""
        # Sum < 1.0
        config_under = self._make_config(traffic_split={"control": 0.4, "treatment": 0.4})
        issues_under = _check_traffic_split(config_under)
        self.assertEqual(len(issues_under), 1)
        self.assertEqual(issues_under[0].code, "traffic_split_invalid")

        # Sum > 1.0
        config_over = self._make_config(traffic_split={"control": 0.6, "treatment": 0.6})
        issues_over = _check_traffic_split(config_over)
        self.assertEqual(len(issues_over), 1)
        self.assertEqual(issues_over[0].code, "traffic_split_invalid")

        # Single arm
        config_single = self._make_config(traffic_split={"control": 0.5})
        issues_single = _check_traffic_split(config_single)
        self.assertEqual(len(issues_single), 1)
        self.assertEqual(issues_single[0].code, "traffic_split_invalid")

        # 3-way valid split (sum = 1.0)
        config_multi = self._make_config(traffic_split={"control": 0.34, "t1": 0.33, "t2": 0.33})
        issues_multi = _check_traffic_split(config_multi)
        self.assertEqual(len(issues_multi), 0)

        # None split
        config_none = self._make_config(traffic_split=None)
        issues_none = _check_traffic_split(config_none)
        self.assertEqual(len(issues_none), 0)

    def test_power_feasibility_boundary_and_formatting(self) -> None:
        """Adversarial test for planning horizon boundaries (30 vs 31 days) and string formatting."""
        # Boundary: 30 days -> Pass
        config_30 = self._make_config(estimated_days=30)
        self.assertEqual(len(_check_power_feasibility(config_30)), 0)

        # Boundary: 31 days -> Warning
        config_31 = self._make_config(estimated_days=31)
        issues_31 = _check_power_feasibility(config_31)
        self.assertEqual(len(issues_31), 1)
        self.assertEqual(issues_31[0].code, "underpowered_horizon")
        self.assertIn("Estimated 31 days", issues_31[0].message)

        # Check message formatting with required_n and daily_traffic present
        config_formatted = ExperimentConfig(
            id="exp_fmt",
            hypothesis_id="hyp_fmt",
            flag_key="checkout_one_page",
            audience_segment="mobile_users",
            traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=15000,
            estimated_days=45,
            guardrail_metrics=["checkout_abandon_rate"],
            daily_traffic=1000,
            status="draft",
        )
        issues_fmt = _check_power_feasibility(config_formatted)
        self.assertEqual(len(issues_fmt), 1)
        self.assertIn("to reach 15,000 per arm at 1,000/day", issues_fmt[0].message)

    def test_missing_catalog_keys_and_malformed_setups(self) -> None:
        """Adversarial test for missing catalog keys across flags, segments, and guardrail metrics."""
        conn = db.get_conn()
        try:
            # Insert guardrail with invalid direction in catalog
            conn.execute(
                "INSERT INTO metrics_catalog(metric_key, name, kind, direction, description) VALUES (?, ?, ?, ?, ?)",
                ("bad_dir_metric", "Bad Direction Metric", "guardrail", "invalid_direction", "Test metric"),
            )
            conn.commit()

            config_bad_dir = self._make_config(guardrail_metrics=["bad_dir_metric"])
            issues = _check_guardrail_metrics(conn, config_bad_dir, primary_metric_key="conversion_rate")
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].code, "guardrail_missing_direction")
            self.assertIn("invalid or unconfigured direction 'invalid_direction'", issues[0].message)
        finally:
            conn.close()

    def test_recommender_missing_catalog_keys_and_multi_flag(self) -> None:
        """Adversarial test for recommender module with nonexistent segments, multi-flags, and missing free flags."""
        from agents.recommender import recommend, recommend_flags, recommend_segment

        # Nonexistent preferred segment -> graceful fallback to available segment
        segment = recommend_segment("checkout", preferred_segment="non_existent_segment_xyz")
        self.assertIsNotNone(segment)
        self.assertIn("segment_key", segment)

        # Multi-flag request (flag_count=3)
        rec_multi = recommend("Improve checkout flow", flag_count=3)
        self.assertGreaterEqual(len(rec_multi.feature_flag_keys), 1)
        spec = rec_multi.to_hypothesis_spec("Multi-flag statement")
        self.assertIsInstance(spec.feature_flag_keys, list)

        # When all flags in catalog are busy -> returns empty flags list and issues warning
        conn = db.get_conn()
        try:
            conn.execute("UPDATE feature_flags SET status = 'running'")
            conn.commit()
            rec_no_flags = recommend("Improve checkout flow")
            self.assertIsNone(rec_no_flags.flag)
            self.assertIn("No free feature flag available", rec_no_flags.issues[0])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

