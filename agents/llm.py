"""Cursor CLI adapter.

This module intentionally has no API-key or local-model provider.  Cursor's
authenticated CLI is the sole LLM integration; deterministic templates preserve
the lifecycle when the CLI is unavailable (for example inside CI).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CURSOR_BIN = os.getenv("CURSOR_AGENT_BIN", "cursor-agent")


def cursor_available() -> bool:
    return shutil.which(CURSOR_BIN) is not None


def ask_cursor(prompt: str, timeout: int = 90) -> str | None:
    """Ask the authenticated Cursor CLI without supplying credentials.

    The CLI receives the workspace context through its existing user session.
    Failures are deliberately non-fatal so deterministic safeguards continue to
    operate in restricted deployments.
    """
    if not cursor_available():
        return None
    try:
        completed = subprocess.run(
            [
                CURSOR_BIN,
                "--trust",
                "--print",
                "--mode",
                "ask",
                "--output-format",
                "json",
                prompt,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode:
            return None
        payload = json.loads(completed.stdout)
        result = payload.get("result")
        return result.strip() if isinstance(result, str) else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


_GENERIC_FALLBACK: list[dict[str, Any]] = [
    {
        "statement": "If we reduce checkout friction for mobile users, then {goal} will improve because fewer users abandon the flow.",
        "segment": "mobile_users",
        "rationale": "Mobile checkout is a high-intent, high-friction journey.",
        "expected_mde": 0.015,
        "direction": "increase",
    },
    {
        "statement": "If we clarify value before payment, then {goal} will improve because price uncertainty decreases.",
        "segment": "new_users",
        "rationale": "New users have less learned trust in the product.",
        "expected_mde": 0.01,
        "direction": "increase",
    },
    {
        "statement": "If we simplify the primary call to action, then {goal} will improve because cognitive load decreases.",
        "segment": "all_users",
        "rationale": "A focused action reduces decision latency.",
        "expected_mde": 0.01,
        "direction": "increase",
    },
]

# Design-target effect sizes are clamped into this band regardless of what a
# single historical precedent observed. Using the best-case realized lift of
# one past experiment as a new experiment's design MDE is a classic power-
# analysis mistake: it systematically underpowers the new test, because the
# realized effect of a shipped experiment is itself the tail of a
# distribution, not its typical value.
_MDE_FLOOR = 0.005
_MDE_CEILING = 0.03


def _estimate_mde_from_precedents(precedents: list[dict[str, Any]]) -> float:
    """A defensible design-target MDE: the mean absolute lift of precedents that
    actually shipped (an informed prior), not the single best result observed."""
    shipped = [abs(p["lift_observed"]) for p in precedents if p.get("outcome") == "shipped"]
    if not shipped:
        return 0.01
    mean_lift = sum(shipped) / len(shipped)
    return max(_MDE_FLOOR, min(_MDE_CEILING, mean_lift))


def _build_prompt(goal: str, precedents: list[dict[str, Any]]) -> str:
    precedent_block = ""
    if precedents:
        examples = "\n".join(
            f"- ({p['outcome']}, {p['lift_observed']:+.1%} lift, segment {p['segment_key']}): {p['hypothesis_text']}"
            for p in precedents[:5]
        )
        precedent_block = (
            "\n\nHere are real past experiments in this product area, for grounding only "
            "-- propose something new for THIS goal, do not copy them verbatim:\n"
            f"{examples}"
        )
    return (
        "Return only a JSON array of exactly three experiment hypotheses for this "
        "product goal. Each object must contain statement, segment, rationale, "
        "expected_mde (number between .005 and .05), and direction ('increase' or "
        "'decrease'). Do not invent telemetry names; use conversion_rate as the "
        f"primary metric. Goal: {goal!r}{precedent_block}"
    )


def _deterministic_fallback(goal: str, precedents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Auditable, keyless fallback. When real precedents exist for this goal's
    category, hypotheses are grounded in them (cited by id, sharing one
    defensible design MDE); generic templates only fill in when there is
    truly no precedent to ground on."""
    estimated_mde = _estimate_mde_from_precedents(precedents)
    grounded = [
        {
            "statement": (
                f"Precedent {precedent['id']} ({precedent['outcome']}, "
                f"{precedent['lift_observed']:+.1%} observed lift) tested "
                f"{precedent['hypothesis_text'][0].lower()}{precedent['hypothesis_text'][1:]} "
                f"A comparable approach for {goal!r} is worth testing on the same segment."
            ),
            "segment": precedent["segment_key"],
            "rationale": (
                f"Historical precedent {precedent['id']} in this category was "
                f"{precedent['outcome']} with an observed lift of {precedent['lift_observed']:+.1%}."
            ),
            "expected_mde": estimated_mde,
            "direction": "increase" if precedent["lift_observed"] >= 0 else "decrease",
        }
        for precedent in precedents[:3]
    ]
    filler = [{**item, "statement": item["statement"].format(goal=goal)} for item in _GENERIC_FALLBACK]
    return (grounded + filler)[:3]


def hypotheses_for_goal(goal: str, precedents: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Generate candidates with Cursor, grounded in real precedents when given,
    falling back to auditable templates -- themselves precedent-grounded when
    possible -- if Cursor is unavailable or returns something unusable."""
    precedents = precedents or []
    prompt = _build_prompt(goal, precedents)
    response = ask_cursor(prompt)
    if response:
        try:
            candidates = json.loads(response)
            if isinstance(candidates, list) and len(candidates) >= 1:
                return [item for item in candidates[:3] if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    return _deterministic_fallback(goal, precedents)
