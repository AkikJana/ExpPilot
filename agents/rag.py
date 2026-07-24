"""Lightweight retrieval over the historical-experiment and long-term-memory stores.

Deliberately dependency-light: no vector DB. For ~30 curated telco-commerce
records, TF-lite lexical scoring with a category boost gives clean, *citable*
retrieval that we can defend to judges (every citation points at a real row id).
Swapping in Chroma later is a drop-in change behind this same interface.
"""
from __future__ import annotations

import re

from data.db import get_conn

_STOP = {
    "the", "a", "an", "of", "to", "for", "on", "in", "vs", "and", "or", "with",
    "increases", "increase", "reduces", "reduce", "improves", "improve", "lifts",
    "lift", "rate", "users", "user", "new", "at",
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "checkout": ("checkout", "cart", "purchase", "buy", "guest", "coupon", "payment page", "drop-off", "abandon"),
    "device_bundles": ("bundle", "device", "carousel", "add-to-cart", "handset", "accessory"),
    "plan_upgrades": ("plan", "upgrade", "tier", "paywall", "subscription", "comparison", "upsell"),
    "churn": ("churn", "retention", "cancel", "winback", "win-back", "lapsed", "at-risk", "save offer"),
    "onboarding": ("onboarding", "signup", "sign-up", "activation", "welcome", "profiling", "tutorial"),
    "payments": ("autopay", "billing", "saved card", "reorder", "transaction", "invoice"),
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 2}


def infer_category(goal: str) -> str:
    """Map a free-text business goal to one of the seeded experiment categories."""
    low = goal.lower()
    best, best_hits = "checkout", 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in low)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best


def search_past_experiments(query: str, category: str | None = None, k: int = 5) -> list[dict]:
    """Return the k most relevant historical experiments with a citable id and score."""
    q = _tokens(query)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, category, hypothesis_text, lift_observed, outcome FROM history"
        ).fetchall()
    finally:
        conn.close()

    scored: list[tuple[float, dict]] = []
    for r in rows:
        overlap = len(q & _tokens(r["hypothesis_text"]))
        cat_boost = 2.0 if category and r["category"] == category else 0.0
        score = overlap + cat_boost
        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "id": r["id"],
                    "category": r["category"],
                    "hypothesis_text": r["hypothesis_text"],
                    "lift_observed": r["lift_observed"],
                    "outcome": r["outcome"],
                    "score": score,
                },
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [d for _, d in scored[:k]]


def search_memory(category: str | None = None, k: int = 3) -> list[dict]:
    """Return distilled lessons/exemplars from long-term memory (best-effort)."""
    conn = get_conn()
    try:
        if category:
            rows = conn.execute(
                "SELECT id, kind, category, content FROM memory WHERE category = ? LIMIT ?",
                (category, k),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, kind, category, content FROM memory LIMIT ?", (k,)
            ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]
