"""LLM access layer with a hard offline fallback.

Design rule (PS3): the LLM never produces a number, a p-value, or a decision.
It only turns already-computed, deterministic facts into business-friendly prose.
Every call therefore takes a list of *allowed_values* and any narrative that
introduces an unlisted number is rejected and replaced by a deterministic template.

If ANTHROPIC_API_KEY is unset (e.g. offline demo), `narrate` transparently uses
the deterministic template so the whole product still runs and demos end to end.
"""
from __future__ import annotations

import os
import re

_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
_NUMERIC_TOLERANCE = 0.02  # relative tolerance when checking narrated numbers


def _provider() -> str | None:
    """Which LLM backend to use, based on which API key is present (Groq preferred)."""
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def active_provider() -> str:
    """Human-readable provider label for the UI."""
    p = _provider()
    if p == "groq":
        return f"Groq ({_GROQ_MODEL})"
    if p == "anthropic":
        return f"Anthropic ({_ANTHROPIC_MODEL})"
    return "offline (deterministic templates)"


def llm_available() -> bool:
    """True only if a key is present AND the matching langchain client imports cleanly."""
    p = _provider()
    if p == "groq":
        try:
            import langchain_groq  # noqa: F401
        except Exception:
            return False
        return True
    if p == "anthropic":
        try:
            import langchain_anthropic  # noqa: F401
        except Exception:
            return False
        return True
    return False


def _build_model():
    """Instantiate the chat model for the active provider (or None if unavailable)."""
    p = _provider()
    if p == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=_GROQ_MODEL, temperature=0.2, max_tokens=400)
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=_ANTHROPIC_MODEL, temperature=0.2, max_tokens=400)
    return None


def _extract_numbers(text: str) -> list[float]:
    """Pull every numeric literal (incl. percentages and decimals) out of a string."""
    out: list[float] = []
    for tok in re.findall(r"-?\d+\.?\d*", text):
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def verify_numbers(text: str, allowed: list[float]) -> bool:
    """Reject narration that contains a number not traceable to the stats engine.

    A number is accepted if it is within a small relative tolerance of any allowed
    value, or matches a rounded/percentage form of one. Small integers (0-14) are
    always allowed because they are days / counts already implied by context.
    """
    allowed_forms: set[float] = set()
    for v in allowed:
        for form in (v, round(v, 4), round(v, 2), round(v * 100, 2), round(v * 100, 1), round(v * 100)):
            allowed_forms.add(float(form))
    for n in _extract_numbers(text):
        if 0 <= n <= 14 and n == int(n):
            continue
        ok = any(
            abs(n - a) <= max(_NUMERIC_TOLERANCE * abs(a), 1e-4) for a in allowed_forms
        ) or n in allowed_forms
        if not ok:
            return False
    return True


def narrate(system: str, prompt: str, allowed_values: list[float], fallback: str) -> tuple[str, str]:
    """Return (text, source). source is 'llm', 'llm_rejected_fallback', or 'template'.

    `fallback` is the deterministic, always-grounded narrative built by the caller.
    We only ever *upgrade* to LLM prose when it passes the numeric guard.
    """
    if not llm_available():
        return fallback, "template"
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        model = _build_model()
        if model is None:
            return fallback, "template"
        resp = model.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        text = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()
        if text and verify_numbers(text, allowed_values):
            return text, "llm"
        return fallback, "llm_rejected_fallback"
    except Exception:
        return fallback, "template"
