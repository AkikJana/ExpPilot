"""Unit tests for agents/recommender.py (Recommendation Engine)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.recommender import (
    Recommendation,
    find_precedents,
    infer_category,
    produce_hypothesis_spec,
    recommend,
    recommend_flag,
    recommend_metrics,
    recommend_segment,
)
from data import db
from data.seed import ensure_seeded
from shared.models import HypothesisSpec


class RecommenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"
        ensure_seeded()

    def tearDown(self) -> None:
        try:
            self.tempdir.cleanup()
        except Exception:
            pass

    def test_infer_category_keyword_matching(self) -> None:
        self.assertEqual(infer_category("Optimize checkout payment step"), "checkout")
        self.assertEqual(infer_category("Reduce cart abandonment rate"), "cart")
        self.assertEqual(infer_category("Offer phone accessory bundle"), "bundles")
        self.assertEqual(infer_category("Drive subscription plan upgrade"), "plan_upgrades")
        self.assertEqual(infer_category("Reduce customer churn and cancellations"), "churn")
        self.assertEqual(infer_category("Improve user onboarding and welcome flow"), "onboarding")
        self.assertEqual(infer_category("Enable autopay billing and recharge"), "payments")
        self.assertEqual(infer_category("Increase loyalty points and referrals"), "loyalty")
        self.assertEqual(infer_category("Enhance search and item discovery"), "discovery")
        self.assertEqual(infer_category("Generic non-matching statement"), "checkout")  # Default fallback

    def test_recommend_segment_selection_and_fallbacks(self) -> None:
        # Explicit segment preference
        seg_pref = recommend_segment("checkout", preferred_segment="desktop_users")
        self.assertEqual(seg_pref["segment_key"], "desktop_users")

        # Historical precedent segment selection
        seg_history = recommend_segment("checkout")
        self.assertIn("segment_key", seg_history)
        self.assertGreater(seg_history["daily_traffic"], 0)

        # Busy segment avoidance: mark flag on mobile_users as running
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO flags(key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)",
                ("flag_1", seg_history["segment_key"], "running", "exp_1"),
            )
            conn.commit()
        finally:
            conn.close()

        seg_next = recommend_segment("checkout")
        self.assertNotEqual(seg_next["segment_key"], seg_history["segment_key"])

    def test_recommend_flag_selection_and_fallbacks(self) -> None:
        flag = recommend_flag("checkout", "mobile_users")
        self.assertIsNotNone(flag)
        self.assertEqual(flag["status"], "free")

        # Mark all flags in checkout as non-free
        conn = db.get_conn()
        try:
            conn.execute("UPDATE feature_flags SET status = 'running' WHERE category = 'checkout'")
            conn.commit()
        finally:
            conn.close()

        # Should fall back to another free flag in another category if available
        fallback_flag = recommend_flag("checkout", "mobile_users")
        self.assertIsNotNone(fallback_flag)
        self.assertEqual(fallback_flag["status"], "free")

    def test_recommend_metrics_derivation(self) -> None:
        metrics = recommend_metrics("checkout")
        self.assertIn("primary", metrics)
        self.assertIn("guardrails", metrics)
        self.assertEqual(metrics["primary"]["metric_key"], "conversion_rate")
        self.assertGreater(len(metrics["guardrails"]), 0)
        guardrail_keys = [m["metric_key"] for m in metrics["guardrails"]]
        self.assertIn("checkout_abandon_rate", guardrail_keys)

    def test_find_precedents_lookup(self) -> None:
        precedents = find_precedents("checkout", limit=3)
        self.assertIsInstance(precedents, list)
        self.assertGreater(len(precedents), 0)
        self.assertLessEqual(len(precedents), 3)
        for p in precedents:
            self.assertEqual(p["category"], "checkout")

    def test_recommend_end_to_end(self) -> None:
        rec = recommend("Improve checkout conversion for mobile users")
        self.assertIsInstance(rec, Recommendation)
        self.assertEqual(rec.category, "checkout")
        self.assertIsNotNone(rec.segment)
        self.assertIsNotNone(rec.flag)
        self.assertEqual(rec.primary_metric["metric_key"], "conversion_rate")
        self.assertGreater(len(rec.guardrail_metrics), 0)
        self.assertGreater(len(rec.precedents), 0)

    def test_produce_hypothesis_spec(self) -> None:
        spec = produce_hypothesis_spec("Reduce customer churn and cancellations", statement="Save offer on cancel")
        self.assertIsInstance(spec, HypothesisSpec)
        self.assertEqual(spec.primary_metric, "retention_d30")
        self.assertGreater(len(spec.guardrail_metrics), 0)
        self.assertGreater(len(spec.feature_flag_keys), 0)


if __name__ == "__main__":
    unittest.main()
