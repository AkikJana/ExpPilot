"""Business-language narrative generation with a numeric guard.

Objective 7 ("summarizes experiment results in business-friendly language")
and the payoff of Objective 6 ("explains why a variant is winning or
underperforming") both land here. Cursor writes prose; every number it writes
must be traceable to an already-computed fact (StatsResult, or a segment's
DriverAnalysis) within a small rounding tolerance, or the narrative is
rejected and a deterministic, business-friendly template is used instead --
the same anti-hallucination discipline agents/llm.py already applies to
hypothesis generation. Cursor explains; it never computes, and it never gets
to introduce a number nobody computed.
"""

from __future__ import annotations

import re

from agents.llm import ask_cursor
from shared.models import StatsResult
from stats.diagnostics import DriverAnalysis

_TOLERANCE = 0.02  # relative tolerance for a narrated number vs its ground truth
_SAFE_INTEGER_RANGE = (0, 60)  # bare (non-percent) small integers: day/segment counts, not facts

_ACTION_LANGUAGE = {
    "scale": "roll this out to everyone",
    "continue": "keep the experiment running to gather more evidence",
    "stop": "stop the experiment -- it is not beating the current experience",
    "rollback": "roll back immediately -- it is causing real harm",
    "pause": "pause and investigate -- something about how users were assigned looks broken",
}


def _ground_truth_numbers(stats: StatsResult, driver_analysis: DriverAnalysis | None) -> list[float]:
    """Every raw fact Cursor is allowed to restate, expanded into the bare-value
    and percentage forms a human would actually write ('0.015' and '1.5%')."""
    raw = [
        stats.lift_abs,
        stats.p_value,
        stats.prob_beats_control,
        stats.srm_p_value,
        stats.expected_loss_ship,
        stats.expected_loss_keep,
        stats.guardrail_margin,
        stats.z_stat,
    ]
    if driver_analysis is not None:
        raw.append(driver_analysis.overall_lift_abs)
        for driver in driver_analysis.drivers:
            raw.extend([driver.lift_abs, driver.p_value, driver.deviation_from_overall])

    numbers: set[float] = set()
    for value in raw:
        numbers.add(round(value, 6))
        numbers.add(round(value * 100, 4))
        numbers.add(round(abs(value) * 100, 4))
    return sorted(numbers)


def _extract_numbers(text: str) -> list[tuple[float, bool]]:
    """Every numeric token in the text, as (value, was_written_as_a_percent)."""
    found: list[tuple[float, bool]] = []
    for match in re.finditer(r"-?\d+\.?\d*(%)?", text):
        token = match.group(0)
        is_percent = token.endswith("%")
        cleaned = token[:-1] if is_percent else token
        if cleaned in ("", "-", "."):
            continue
        try:
            found.append((float(cleaned), is_percent))
        except ValueError:
            continue
    return found


def _is_grounded(value: float, is_percent: bool, ground_truth: list[float]) -> bool:
    for truth in ground_truth:
        tolerance = _TOLERANCE * max(abs(truth), 1.0)
        if abs(value - truth) <= tolerance:
            return True
    if is_percent:
        # A number written with a '%' sign is always a claimed fact and must
        # match ground truth -- no exemption, otherwise an invented "50%"
        # would slip through the small-integer carve-out below.
        return False
    low, high = _SAFE_INTEGER_RANGE
    return value == int(value) and low <= value <= high


def verify_numeric_grounding(text: str, stats: StatsResult, driver_analysis: DriverAnalysis | None = None) -> bool:
    """True only if every number in `text` traces back to a computed fact (or is
    a safe bare small integer like a day count)."""
    ground_truth = _ground_truth_numbers(stats, driver_analysis)
    return all(_is_grounded(value, is_percent, ground_truth) for value, is_percent in _extract_numbers(text))


def _template_narrative(action: str, stats: StatsResult, driver_analysis: DriverAnalysis | None) -> str:
    """Deterministic, business-friendly fallback -- no LLM, so no risk of an
    invented number. Everything here is read directly off StatsResult."""
    lift_pct = stats.lift_abs * 100
    confidence_pct = stats.prob_beats_control * 100
    direction = "up" if stats.lift_abs >= 0 else "down"

    parts = [
        f"After {stats.day} days, the treatment is tracking {abs(lift_pct):.1f} points {direction} "
        f"versus what people saw before, with {confidence_pct:.0f}% confidence this holds."
    ]

    if stats.srm_flag:
        parts.append(
            "We paused: the split between the two groups does not look random, so this result "
            "cannot be trusted until that is fixed."
        )
    elif stats.guardrail_breach:
        parts.append(
            f"A guardrail metric moved the wrong way by {stats.guardrail_margin * 100:.1f} points, "
            "which is why we are recommending a rollback rather than a scale."
        )

    if driver_analysis is not None:
        top = driver_analysis.top_driver
        if top is not None:
            verb = "driven" if top.classification == "driving" else "dragged down"
            parts.append(
                f"This is mostly {verb} by the '{top.segment_key}' group "
                f"({top.lift_abs * 100:+.1f} points there versus {driver_analysis.overall_lift_abs * 100:+.1f} "
                "points overall)."
            )

    parts.append(f"Recommendation: {_ACTION_LANGUAGE.get(action, action)}.")
    return " ".join(parts)


def narrate_decision(
    action: str,
    stats: StatsResult,
    driver_analysis: DriverAnalysis | None = None,
) -> tuple[str, str]:
    """Return (narrative, source). source is 'llm' if Cursor's prose passed the
    numeric guard, else 'template'."""
    template = _template_narrative(action, stats, driver_analysis)

    driver_summary = ""
    if driver_analysis is not None and driver_analysis.top_driver is not None:
        top = driver_analysis.top_driver
        driver_summary = (
            f"\nSegment breakdown: '{top.segment_key}' is {top.classification} "
            f"({top.lift_abs * 100:+.2f} pts vs {driver_analysis.overall_lift_abs * 100:+.2f} pts overall)."
        )

    prompt = (
        "Write a two-sentence, business-friendly summary of this experiment result for a "
        "non-technical product manager. Use ONLY the numbers given below; do not invent, "
        "round into a different-looking figure, or add any number not present here.\n"
        f"Recommended action: {action}.\n"
        f"Day {stats.day}. Absolute lift: {stats.lift_abs * 100:.2f} percentage points. "
        f"Confidence the treatment beats control: {stats.prob_beats_control * 100:.1f}%. "
        f"P-value: {stats.p_value:.4f}. Guardrail margin: {stats.guardrail_margin * 100:.2f} points."
        f"{driver_summary}"
    )
    response = ask_cursor(prompt)
    if response and verify_numeric_grounding(response, stats, driver_analysis):
        return response.strip(), "llm"
    return template, "template"
