"""Regression tests for the non-LLM decision boundary."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["CURSOR_AGENT_BIN"] = "cursor-agent-not-installed"

from api import service
from data import db
from shared.models import DayStats, SegmentDayStats


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_positive_result_scales_after_readiness_gate(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        experiment_id = proposal["config"]["id"]
        service.start_experiment(experiment_id)
        decision = service.analyze_day(
            DayStats(
                experiment_id=experiment_id,
                day=7,
                control_n=20_000,
                control_conversions=2_000,
                treatment_n=20_000,
                treatment_conversions=2_300,
                guardrail_control_rate=0.01,
                guardrail_treatment_rate=0.01,
            )
        )
        self.assertEqual(decision.action, "scale")

    def test_srm_pauses_before_other_decisions(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        decision = service.analyze_day(
            DayStats(
                experiment_id=proposal["config"]["id"],
                day=7,
                control_n=30_000,
                control_conversions=3_000,
                treatment_n=10_000,
                treatment_conversions=1_200,
                guardrail_control_rate=0.01,
                guardrail_treatment_rate=0.01,
            )
        )
        self.assertEqual(decision.action, "pause")

    def test_branching_is_queued_and_harness_is_gitops_only(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        experiment_id = proposal["config"]["id"]
        ontology = service.branch_ontology(
            experiment_id,
            proposal["ontology"]["id"],
            "Test clearer fee disclosure",
            "It may reduce price anxiety before checkout.",
            "new_users",
        )
        child = ontology["children"][-1]
        self.assertEqual(child["status"], "queued")
        manifest = service.propose_harness_gitops(experiment_id, "rollback")
        self.assertTrue(manifest["filename"].endswith(".yaml"))
        self.assertIn("requires_review", manifest)


class RecommendationAndDiagnosticsTests(unittest.TestCase):
    """Coverage for the data-grounded recommender/validator/narrator wiring.

    Separate test class, same file, so the three original LifecycleTests
    methods are untouched by this addition -- their setUp/tearDown/bodies are
    byte-identical to before this work started.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "test.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_recommendation_grounds_flag_metrics_and_precedents_in_real_data(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion for mobile users")

        # The flag must be a real catalog flag, not an invented per-experiment key.
        self.assertEqual(proposal["config"]["flag_key"], "checkout_one_page")
        # Guardrails must be real, category-appropriate metric keys, not the old
        # hardcoded ["error_rate"].
        self.assertIn("checkout_abandon_rate", proposal["config"]["guardrail_metrics"])
        # precedent_ids must cite real historical_experiments rows, not be empty.
        self.assertTrue(proposal["hypothesis"]["precedent_ids"])
        for precedent_id in proposal["hypothesis"]["precedent_ids"]:
            self.assertTrue(precedent_id.startswith("hist_"))
        self.assertEqual(proposal["recommendation"]["category"], "checkout")
        self.assertTrue(proposal["validation"]["passed"])

    def test_baseline_and_traffic_derive_from_segment_when_omitted(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion for mobile users")
        # mobile_users in data/seeds/segments.csv: baseline_conversion_rate=0.0840, daily_traffic=21500
        self.assertAlmostEqual(proposal["config"]["baseline_rate"], 0.0840, places=4)
        self.assertEqual(proposal["config"]["daily_traffic"], 21500)

    def test_explicit_baseline_and_traffic_override_the_recommendation(self) -> None:
        """Backward compatibility, made explicit: passing real values (as the
        original LifecycleTests always did) must always win over the
        segment-derived default."""
        proposal = service.create_experiment("Improve checkout conversion", 0.1, 2000)
        self.assertEqual(proposal["config"]["baseline_rate"], 0.1)
        self.assertEqual(proposal["config"]["daily_traffic"], 2000)

    def test_validator_blocks_a_second_experiment_on_a_running_audience(self) -> None:
        first = service.create_experiment("Improve checkout conversion for mobile users")
        service.start_experiment(first["config"]["id"])

        with self.assertRaises(ValueError) as ctx:
            service.create_experiment("Improve checkout conversion for mobile users")
        self.assertIn("already has a running experiment", str(ctx.exception))

    def test_narrative_is_business_language_and_reflects_the_deciding_segment(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion for mobile users")
        experiment_id = proposal["config"]["id"]
        service.start_experiment(experiment_id)

        decision = service.analyze_day(
            DayStats(
                experiment_id=experiment_id,
                day=7,
                control_n=20_000,
                control_conversions=2_000,
                treatment_n=20_000,
                treatment_conversions=2_300,
                guardrail_control_rate=0.01,
                guardrail_treatment_rate=0.01,
            ),
            segments=[
                SegmentDayStats(
                    experiment_id=experiment_id, day=7, segment_key="mobile_users",
                    control_n=12_000, control_conversions=1_000, treatment_n=12_000, treatment_conversions=1_600,
                ),
                SegmentDayStats(
                    experiment_id=experiment_id, day=7, segment_key="desktop_users",
                    control_n=8_000, control_conversions=1_000, treatment_n=8_000, treatment_conversions=700,
                ),
            ],
        )
        # Not the old engineer-speak template ("Deterministic decision: SCALE...").
        self.assertNotIn("Deterministic decision:", decision.narrative)
        # Names the segment actually dragging the result down, using real
        # classified data from stats.diagnostics, not an invented claim.
        self.assertIn("desktop_users", decision.narrative)

    def test_narrative_via_daystats_segments_field_matches_explicit_argument(self) -> None:
        """The additive DayStats.segments field (used by the FastAPI /monitor
        request body) must drive the same diagnostics path as passing
        `segments=` explicitly to analyze_day."""
        proposal = service.create_experiment("Improve checkout conversion for mobile users")
        experiment_id = proposal["config"]["id"]
        service.start_experiment(experiment_id)

        day = DayStats(
            experiment_id=experiment_id,
            day=7,
            control_n=20_000,
            control_conversions=2_000,
            treatment_n=20_000,
            treatment_conversions=2_300,
            guardrail_control_rate=0.01,
            guardrail_treatment_rate=0.01,
            segments=[
                SegmentDayStats(
                    experiment_id=experiment_id, day=7, segment_key="mobile_users",
                    control_n=12_000, control_conversions=1_000, treatment_n=12_000, treatment_conversions=1_600,
                ),
                SegmentDayStats(
                    experiment_id=experiment_id, day=7, segment_key="desktop_users",
                    control_n=8_000, control_conversions=1_000, treatment_n=8_000, treatment_conversions=700,
                ),
            ],
        )
        decision = service.analyze_day(day, day.segments or None)
        self.assertIn("desktop_users", decision.narrative)

    def test_timeline_returns_an_ordered_day_by_day_series(self) -> None:
        proposal = service.create_experiment("Improve checkout conversion for mobile users")
        experiment_id = proposal["config"]["id"]
        service.start_experiment(experiment_id)

        for day_number, treatment_conversions in ((3, 950), (5, 1_600), (7, 2_300)):
            service.analyze_day(
                DayStats(
                    experiment_id=experiment_id,
                    day=day_number,
                    control_n=day_number * 3000,
                    control_conversions=int(day_number * 3000 * 0.1),
                    treatment_n=day_number * 3000,
                    treatment_conversions=treatment_conversions,
                    guardrail_control_rate=0.01,
                    guardrail_treatment_rate=0.01,
                )
            )

        timeline = service.get_timeline(experiment_id)
        self.assertEqual(timeline["days_observed"], 3)
        self.assertEqual([entry["day"] for entry in timeline["series"]], [3, 5, 7])
        self.assertEqual(timeline["latest_action"], timeline["series"][-1]["action"])

    def test_timeline_of_unknown_experiment_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            service.get_timeline("exp_does_not_exist")


class NarratorNumericGuardTests(unittest.TestCase):
    """agents/narrator.py's anti-hallucination guard, independent of the DB."""

    def _stats(self):
        from shared.models import StatsResult

        return StatsResult(
            experiment_id="exp_test", day=7, srm_p_value=0.9, srm_flag=False,
            z_stat=3.1, p_value=0.0019, lift_abs=0.015, ci_low=0.005, ci_high=0.025,
            prob_beats_control=0.97, expected_loss_ship=0.001, expected_loss_keep=0.02,
            guardrail_breach=False, guardrail_margin=0.0,
        )

    def test_grounded_numbers_pass(self) -> None:
        from agents.narrator import verify_numeric_grounding

        stats = self._stats()
        text = "After 7 days we saw a lift of 1.50 percentage points with 97.0% confidence."
        self.assertTrue(verify_numeric_grounding(text, stats))

    def test_invented_numbers_are_rejected(self) -> None:
        from agents.narrator import verify_numeric_grounding

        stats = self._stats()
        text = "We are 50% confident this will scale, with a massive 12.0% lift."
        self.assertFalse(verify_numeric_grounding(text, stats))

    def test_a_plausible_but_wrong_percentage_does_not_slip_through_the_day_count_exemption(self) -> None:
        """The exact bug caught and fixed during development: '50%' must not
        pass just because 50 falls inside the safe bare-integer day/count
        range -- a percent sign always means a claimed fact."""
        from agents.narrator import verify_numeric_grounding

        stats = self._stats()
        self.assertFalse(verify_numeric_grounding("Confidence is 50%.", stats))

    def test_bare_day_count_is_still_allowed(self) -> None:
        from agents.narrator import verify_numeric_grounding

        stats = self._stats()
        self.assertTrue(verify_numeric_grounding("By day 7 the trend was clear.", stats))

    def test_template_fallback_is_always_grounded_by_construction(self) -> None:
        from agents.narrator import narrate_decision

        stats = self._stats()
        narrative, source = narrate_decision("scale", stats)
        self.assertEqual(source, "template")  # Cursor is unavailable in the test env
        self.assertIn("Recommendation:", narrative)


if __name__ == "__main__":
    unittest.main()
