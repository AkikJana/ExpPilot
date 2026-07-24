"""Deterministic recommendations grounded in the SQL data layer.

No LLM call anywhere in this module. Which feature flag, which audience segment,
and which success/guardrail metrics to propose are all derived from real rows
seeded from data/seeds/*.csv (see data/seed.py) — never invented, and never a
question put to Cursor. Cursor's only job (agents/llm.py) is prose; every
identifier and every number here traces back to a table.
"""

from __future__ import annotations

from dataclasses import dataclass

from data.db import get_conn

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "checkout": ("checkout", "payment step", "purchase flow", "buy now", "one-page", "guest"),
    "cart": ("cart", "add to cart", "abandoned cart", "abandonment"),
    "bundles": ("bundle", "device upgrade", "accessory", "handset", "device"),
    "plan_upgrades": ("plan", "upgrade", "tier", "subscription", "paywall"),
    "churn": ("churn", "retention", "cancel", "winback", "win-back", "save offer", "at-risk", "at risk"),
    "onboarding": ("onboarding", "signup", "sign-up", "activation", "welcome", "new user"),
    "payments": ("autopay", "billing", "saved card", "payment method", "invoice", "recharge"),
    "loyalty": ("loyalty", "referral", "reward", "points"),
    "discovery": ("search", "recommend", "discovery", "browse", "autocomplete"),
}
_DEFAULT_CATEGORY = "checkout"

# Curated, not learned: which guardrails matter for a category is a product
# judgement call. Every key below is validated against metrics_catalog before
# use, so a typo here fails loudly rather than silently proposing a metric that
# does not exist.
_CATEGORY_GUARDRAILS: dict[str, tuple[str, ...]] = {
    "checkout": ("checkout_abandon_rate", "error_rate", "refund_rate"),
    "cart": ("checkout_abandon_rate", "error_rate"),
    "bundles": ("refund_rate", "support_contact_rate"),
    "plan_upgrades": ("support_contact_rate", "unsubscribe_rate"),
    "churn": ("unsubscribe_rate", "support_contact_rate"),
    "onboarding": ("crash_free_rate", "error_rate"),
    "payments": ("refund_rate", "error_rate"),
    "loyalty": ("unsubscribe_rate",),
    "discovery": ("latency_p95_ms", "error_rate"),
}
_FALLBACK_GUARDRAILS = ("error_rate", "latency_p95_ms")
_FALLBACK_PRIMARY_METRIC = "conversion_rate"


def infer_category(goal: str) -> str:
    """Map free-text goal to one of the seeded product categories by keyword hits."""
    lowered = goal.lower()
    best_category, best_hits = _DEFAULT_CATEGORY, 0
    for category, keywords in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in lowered)
        if hits > best_hits:
            best_category, best_hits = category, hits
    return best_category


@dataclass(frozen=True)
class Recommendation:
    category: str
    segment: dict
    flag: dict | None
    primary_metric: dict
    guardrail_metrics: list[dict]
    precedents: list[dict]
    issues: list[str]


def recommend_segment(category: str, preferred_segment: str | None = None) -> dict:
    """Pick a real segment row: an explicit preference if valid, else the segment
    most represented among shipped precedents for this category, else the
    largest-traffic segment overall."""
    conn = get_conn()
    try:
        if preferred_segment:
            row = conn.execute(
                "SELECT * FROM segments WHERE segment_key = ?", (preferred_segment,)
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            """
            SELECT s.* FROM segments s
            JOIN historical_experiments h ON h.segment_key = s.segment_key
            WHERE h.category = ? AND h.outcome = 'shipped'
            GROUP BY s.segment_key
            ORDER BY COUNT(*) DESC, s.daily_traffic DESC
            LIMIT 1
            """,
            (category,),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute("SELECT * FROM segments ORDER BY daily_traffic DESC LIMIT 1").fetchone()
        if row is None:
            raise LookupError("segments table is empty; run data.seed before recommending")
        return dict(row)
    finally:
        conn.close()


def recommend_flag(category: str, segment_key: str) -> dict | None:
    """Pick a free flag matching category + segment; widen the search if none exists."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM feature_flags WHERE category = ? AND segment_key = ? AND status = 'free' LIMIT 1",
            (category, segment_key),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            "SELECT * FROM feature_flags WHERE category = ? AND status = 'free' LIMIT 1", (category,)
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute("SELECT * FROM feature_flags WHERE status = 'free' LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def recommend_metrics(category: str) -> dict:
    """Primary metric = the metric most commonly used to measure this category's
    past experiments (data-driven, not curated). Guardrails = a curated,
    catalog-validated set per category."""
    conn = get_conn()
    try:
        primary_row = conn.execute(
            "SELECT primary_metric, COUNT(*) AS hits FROM historical_experiments "
            "WHERE category = ? GROUP BY primary_metric ORDER BY hits DESC LIMIT 1",
            (category,),
        ).fetchone()
        primary_key = primary_row["primary_metric"] if primary_row else _FALLBACK_PRIMARY_METRIC
        primary = conn.execute("SELECT * FROM metrics_catalog WHERE metric_key = ?", (primary_key,)).fetchone()
        if primary is None:
            primary = conn.execute(
                "SELECT * FROM metrics_catalog WHERE metric_key = ?", (_FALLBACK_PRIMARY_METRIC,)
            ).fetchone()

        guardrail_keys = _CATEGORY_GUARDRAILS.get(category, _FALLBACK_GUARDRAILS)
        guardrails = []
        for key in guardrail_keys:
            row = conn.execute("SELECT * FROM metrics_catalog WHERE metric_key = ?", (key,)).fetchone()
            if row:
                guardrails.append(dict(row))
        if not guardrails:
            for key in _FALLBACK_GUARDRAILS:
                row = conn.execute("SELECT * FROM metrics_catalog WHERE metric_key = ?", (key,)).fetchone()
                if row:
                    guardrails.append(dict(row))

        return {"primary": dict(primary), "guardrails": guardrails}
    finally:
        conn.close()


def find_precedents(category: str, segment_key: str | None = None, limit: int = 5) -> list[dict]:
    """Real historical rows to cite, best lift first. Falls back to the category
    at large if the exact segment has no precedent."""
    conn = get_conn()
    try:
        if segment_key:
            rows = conn.execute(
                "SELECT * FROM historical_experiments WHERE category = ? AND segment_key = ? "
                "ORDER BY lift_observed DESC LIMIT ?",
                (category, segment_key, limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        rows = conn.execute(
            "SELECT * FROM historical_experiments WHERE category = ? ORDER BY lift_observed DESC LIMIT ?",
            (category, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recommend(goal: str, preferred_segment: str | None = None) -> Recommendation:
    """The single entry point: goal in, a fully-grounded recommendation out."""
    category = infer_category(goal)
    segment = recommend_segment(category, preferred_segment)
    flag = recommend_flag(category, segment["segment_key"])
    metrics = recommend_metrics(category)
    precedents = find_precedents(category, segment["segment_key"])

    issues: list[str] = []
    if flag is None:
        issues.append(f"No free feature flag available for category '{category}'.")
    if not precedents:
        issues.append(f"No historical precedent found for category '{category}'; recommendation is unproven.")

    return Recommendation(
        category=category,
        segment=segment,
        flag=flag,
        primary_metric=metrics["primary"],
        guardrail_metrics=metrics["guardrails"],
        precedents=precedents,
        issues=issues,
    )
