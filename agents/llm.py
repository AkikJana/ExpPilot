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


def hypotheses_for_goal(goal: str) -> list[dict[str, Any]]:
    """Generate candidates with Cursor, falling back to auditable templates."""
    prompt = (
        "Return only a JSON array of exactly three experiment hypotheses for this "
        "product goal. Each object must contain statement, segment, rationale, "
        "expected_mde (number between .005 and .05), and direction ('increase' or "
        "'decrease'). Do not invent telemetry names; use conversion_rate as the "
        f"primary metric. Goal: {goal!r}"
    )
    response = ask_cursor(prompt)
    if response:
        try:
            candidates = json.loads(response)
            if isinstance(candidates, list) and len(candidates) >= 1:
                return [item for item in candidates[:3] if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    return [
        {
            "statement": f"If we reduce checkout friction for mobile users, then {goal} will improve because fewer users abandon the flow.",
            "segment": "mobile_users",
            "rationale": "Mobile checkout is a high-intent, high-friction journey.",
            "expected_mde": 0.015,
            "direction": "increase",
        },
        {
            "statement": f"If we clarify value before payment, then {goal} will improve because price uncertainty decreases.",
            "segment": "new_users",
            "rationale": "New users have less learned trust in the product.",
            "expected_mde": 0.01,
            "direction": "increase",
        },
        {
            "statement": f"If we simplify the primary call to action, then {goal} will improve because cognitive load decreases.",
            "segment": "all_users",
            "rationale": "A focused action reduces decision latency.",
            "expected_mde": 0.01,
            "direction": "increase",
        },
    ]
