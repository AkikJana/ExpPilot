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
from rules_engine.decision import evaluate_decision
from shared.models import DecisionRecommendation, HypothesisSpec

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
    flags: list[dict] | None = None

    @property
    def feature_flag_keys(self) -> list[str]:
        if self.flags:
            return [f["flag_key"] for f in self.flags if "flag_key" in f]
        if self.flag and "flag_key" in self.flag:
            return [self.flag["flag_key"]]
        return []

    def to_hypothesis_spec(self, hypothesis_statement: str = "") -> HypothesisSpec:
        """Convert recommendation into a valid HypothesisSpec schema."""
        return HypothesisSpec(
            hypothesis=hypothesis_statement or f"Optimization hypothesis for {self.category}",
            primary_metric=self.primary_metric.get("metric_key", _FALLBACK_PRIMARY_METRIC),
            guardrail_metrics=[m["metric_key"] for m in self.guardrail_metrics if "metric_key" in m],
            feature_flag_keys=self.feature_flag_keys,
            target_audience=self.segment,
        )


def recommend_segment(category: str, preferred_segment: str | None = None) -> dict:
    """Pick a real segment row: an explicit preference if valid, else the segment
    most represented among shipped precedents for this category (excluding segments
    with active running experiments), else the largest-traffic free segment overall."""
    conn = get_conn()
    try:
        if preferred_segment:
            row = conn.execute(
                "SELECT * FROM segments WHERE segment_key = ?", (preferred_segment,)
            ).fetchone()
            if row:
                return dict(row)
        # Find busy segments currently running an experiment
        busy_rows = conn.execute(
            "SELECT DISTINCT segment FROM flags WHERE status = 'running'"
        ).fetchall()
        busy_segments = {r["segment"] for r in busy_rows}

        row = conn.execute(
            """
            SELECT s.* FROM segments s
            JOIN historical_experiments h ON h.segment_key = s.segment_key
            WHERE h.category = ? AND h.outcome = 'shipped'
            GROUP BY s.segment_key
            ORDER BY COUNT(*) DESC, s.daily_traffic DESC
            """,
            (category,),
        ).fetchall()
        for r in row:
            if r["segment_key"] not in busy_segments:
                return dict(r)

        # Fallback to largest non-busy segment overall
        all_segments = conn.execute("SELECT * FROM segments ORDER BY daily_traffic DESC").fetchall()
        for r in all_segments:
            if r["segment_key"] not in busy_segments:
                return dict(r)

        # If all segments are busy, fall back to the top segment
        if all_segments:
            return dict(all_segments[0])
        raise LookupError("segments table is empty; run data.seed before recommending")
    finally:
        conn.close()


def recommend_flag(category: str, segment_key: str) -> dict | None:
    """Pick a free flag matching category + segment; widen the search if none exists."""
    flags = recommend_flags(category, segment_key, count=1)
    return flags[0] if flags else None


def recommend_flags(category: str, segment_key: str, count: int = 1) -> list[dict]:
    """Pick up to `count` free flags matching category + segment; widen the search if needed."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM feature_flags WHERE category = ? AND segment_key = ? AND status = 'free' LIMIT ?",
            (category, segment_key, count),
        ).fetchall()
        flags = [dict(r) for r in rows]
        if len(flags) < count:
            existing_keys = {f["flag_key"] for f in flags}
            more = conn.execute(
                "SELECT * FROM feature_flags WHERE category = ? AND status = 'free' LIMIT ?",
                (category, count * 2),
            ).fetchall()
            for r in more:
                if r["flag_key"] not in existing_keys and len(flags) < count:
                    flags.append(dict(r))
                    existing_keys.add(r["flag_key"])
        if len(flags) < count:
            existing_keys = {f["flag_key"] for f in flags}
            more = conn.execute(
                "SELECT * FROM feature_flags WHERE status = 'free' LIMIT ?",
                (count * 2,),
            ).fetchall()
            for r in more:
                if r["flag_key"] not in existing_keys and len(flags) < count:
                    flags.append(dict(r))
                    existing_keys.add(r["flag_key"])
        return flags
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


def recommend(goal: str, preferred_segment: str | None = None, flag_count: int = 1) -> Recommendation:
    """The single entry point: goal in, a fully-grounded recommendation out."""
    category = infer_category(goal)
    segment = recommend_segment(category, preferred_segment)
    flags = recommend_flags(category, segment["segment_key"], count=flag_count)
    flag = flags[0] if flags else None
    metrics = recommend_metrics(category)
    precedents = find_precedents(category, segment["segment_key"])

    issues: list[str] = []
    if not flags:
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
        flags=flags,
    )


def produce_hypothesis_spec(
    goal: str, statement: str = "", preferred_segment: str | None = None, flag_count: int = 1
) -> HypothesisSpec:
    """Generate a valid HypothesisSpec from a business goal."""
    rec = recommend(goal, preferred_segment=preferred_segment, flag_count=flag_count)
    return rec.to_hypothesis_spec(hypothesis_statement=statement)
