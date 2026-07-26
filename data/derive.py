"""Derive a real audience catalog from the user's own transactional data.

The seeded catalog in data/seeds/*.csv is fabricated demo data. Every number it
supplies -- segment population, daily traffic, baseline conversion rate -- flows
straight into the sample-size and runtime maths, so planning against it produces
plausible-looking but invented experiment designs.

This module replaces those three numbers with measured ones. Point it at a
transaction log and tell it which column is the user, which is the timestamp,
what counts as a conversion, and (optionally) what splits the audience; it
returns segment rows in exactly the shape the `segments` table expects.

What it deliberately does NOT do: manufacture experiment results. Transaction
logs are observational -- nobody was randomised into a control or treatment arm
-- so this can ground an experiment's *design* but can never supply its
*measurement*. Precedent lifts stay synthetic until real experiments are run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from data.db import get_conn, init_db

# A conversion has to be defined, not assumed. Two rules cover most transaction
# logs without pretending to understand the schema:
#   repeat_event  -- the user has more than one distinct event (e.g. a second
#                    invoice). A standard retention/repeat-purchase proxy.
#   value_threshold -- the user's summed value column reaches a threshold
#                    (e.g. lifetime spend >= 100). A revenue-quality proxy.
OutcomeRule = Literal["repeat_event", "value_threshold"]

MIN_USERS_PER_SEGMENT = 30  # below this a baseline rate is too noisy to plan on


@dataclass(frozen=True)
class ColumnMapping:
    """Which columns carry the concepts the derivation needs.

    user_col and timestamp_col are required: without an identity you cannot
    count users, and without a time axis you cannot compute daily traffic.
    """

    user_col: str
    timestamp_col: str
    outcome_rule: OutcomeRule = "repeat_event"
    event_col: str | None = None       # distinct-event id for repeat_event
    value_col: str | None = None       # numeric column for value_threshold
    value_threshold: float = 0.0
    segment_col: str | None = None     # split into segments; None -> one segment
    max_segments: int = 12             # keep the catalog reviewable

    def validate(self, columns: list[str]) -> list[str]:
        """Return human-readable problems; empty list means usable."""
        problems: list[str] = []
        known = set(columns)

        for label, col in (("user", self.user_col), ("timestamp", self.timestamp_col)):
            if not col:
                problems.append(f"No {label} column selected.")
            elif col not in known:
                problems.append(f"{label.capitalize()} column '{col}' is not in the file.")

        if self.segment_col and self.segment_col not in known:
            problems.append(f"Segment column '{self.segment_col}' is not in the file.")

        if self.outcome_rule == "repeat_event":
            if self.event_col and self.event_col not in known:
                problems.append(f"Event column '{self.event_col}' is not in the file.")
        elif self.outcome_rule == "value_threshold":
            if not self.value_col:
                problems.append("A value column is required for the value-threshold rule.")
            elif self.value_col not in known:
                problems.append(f"Value column '{self.value_col}' is not in the file.")
            elif self.value_threshold <= 0:
                problems.append("The value threshold must be greater than zero.")

        return problems


def segment_key_from(label: Any) -> str:
    """Turn an arbitrary column value into a stable snake_case catalog key."""
    text = re.sub(r"[^0-9a-zA-Z]+", "_", str(label).strip().lower()).strip("_")
    return text or "unknown"


def _converted_users(frame: pd.DataFrame, mapping: ColumnMapping) -> set:
    """The set of users counted as converted under the chosen rule."""
    if mapping.outcome_rule == "value_threshold":
        totals = frame.groupby(mapping.user_col)[mapping.value_col].sum()
        return set(totals[totals >= mapping.value_threshold].index)

    # repeat_event: more than one distinct event, falling back to row count when
    # no event id was mapped (each row then counts as an event).
    if mapping.event_col:
        counts = frame.groupby(mapping.user_col)[mapping.event_col].nunique()
    else:
        counts = frame.groupby(mapping.user_col).size()
    return set(counts[counts > 1].index)


def _describe_rule(mapping: ColumnMapping) -> str:
    if mapping.outcome_rule == "value_threshold":
        return f"summed {mapping.value_col} >= {mapping.value_threshold:g}"
    unit = mapping.event_col or "row"
    return f"more than one distinct {unit}"


def _segment_row(frame: pd.DataFrame, key: str, display: str, mapping: ColumnMapping) -> dict | None:
    """Measure one segment. Returns None when there is too little data to plan on."""
    users = frame[mapping.user_col].nunique()
    if users < MIN_USERS_PER_SEGMENT:
        return None

    active_days = frame[mapping.timestamp_col].dt.normalize().nunique()
    if active_days <= 0:
        return None

    # Daily traffic is distinct users per active day, not rows per day: the
    # sample-size maths counts users entering the experiment, and one user can
    # generate many transaction rows.
    daily_users = frame.groupby(frame[mapping.timestamp_col].dt.normalize())[mapping.user_col].nunique()
    daily_traffic = int(round(float(daily_users.mean())))

    converted = len(_converted_users(frame, mapping))
    rate = converted / users if users else 0.0
    # ExperimentConfig requires a baseline strictly inside (0, 1); clamp rather
    # than emit a row that would fail validation downstream.
    rate = min(max(rate, 0.001), 0.999)

    return {
        "segment_key": key,
        "display_name": display,
        "population": int(users),
        "daily_traffic": max(1, daily_traffic),
        "baseline_conversion_rate": round(rate, 4),
        "description": (
            f"Derived from uploaded data: {users:,} users over {active_days} active day(s); "
            f"conversion = {_describe_rule(mapping)}"
        ),
    }


def derive_segments(df: pd.DataFrame, mapping: ColumnMapping) -> dict[str, Any]:
    """Measure segments from a transaction log.

    Returns {"segments": [...], "skipped": [...], "meta": {...}}. `skipped`
    records segments dropped for having too few users, so the caller can say why
    a value in the file did not become a segment.
    """
    problems = mapping.validate(list(df.columns))
    if problems:
        raise ValueError("; ".join(problems))

    frame = df.copy()
    frame[mapping.timestamp_col] = pd.to_datetime(frame[mapping.timestamp_col], errors="coerce")
    frame = frame.dropna(subset=[mapping.timestamp_col, mapping.user_col])

    if mapping.outcome_rule == "value_threshold":
        frame[mapping.value_col] = pd.to_numeric(frame[mapping.value_col], errors="coerce")
        frame = frame.dropna(subset=[mapping.value_col])

    if frame.empty:
        raise ValueError("No usable rows after parsing timestamps and dropping blanks.")

    segments: list[dict] = []
    skipped: list[dict] = []

    if mapping.segment_col:
        # Largest groups first so max_segments keeps the ones that matter.
        order = frame[mapping.segment_col].value_counts().index
        for label in order:
            group = frame[frame[mapping.segment_col] == label]
            key = segment_key_from(label)
            row = _segment_row(group, key, str(label), mapping)
            if row is None:
                skipped.append(
                    {
                        "segment_key": key,
                        "reason": f"only {group[mapping.user_col].nunique()} distinct users "
                        f"(minimum {MIN_USERS_PER_SEGMENT})",
                    }
                )
                continue
            segments.append(row)
            if len(segments) >= mapping.max_segments:
                break
    else:
        row = _segment_row(frame, "all_users", "All users", mapping)
        if row is None:
            raise ValueError(
                f"Fewer than {MIN_USERS_PER_SEGMENT} distinct users in the file -- "
                "not enough to derive a baseline."
            )
        segments.append(row)

    return {
        "segments": segments,
        "skipped": skipped,
        "meta": {
            "rows_used": int(len(frame)),
            "distinct_users": int(frame[mapping.user_col].nunique()),
            "date_range": [
                frame[mapping.timestamp_col].min().date().isoformat(),
                frame[mapping.timestamp_col].max().date().isoformat(),
            ],
            "outcome_rule": mapping.outcome_rule,
            "outcome_definition": _describe_rule(mapping),
        },
    }


def persist_segments(segments: list[dict], *, replace_seeded: bool = False) -> dict[str, int]:
    """Upsert derived segments into the catalog.

    By default derived rows are added alongside the seeded ones, because the
    seeded historical_experiments reference seeded segment keys and wiping them
    would strand every precedent. `replace_seeded=True` clears the table first
    for users who want a catalog containing only their own measured audiences.
    """
    if not segments:
        return {"inserted": 0, "updated": 0, "deleted": 0}

    init_db()
    conn = get_conn()
    deleted = 0
    inserted = 0
    updated = 0
    try:
        existing = {row["segment_key"] for row in conn.execute("SELECT segment_key FROM segments").fetchall()}

        if replace_seeded:
            keep = {s["segment_key"] for s in segments}
            for key in existing - keep:
                conn.execute("DELETE FROM segments WHERE segment_key = ?", (key,))
                deleted += 1
            existing = existing & keep

        for segment in segments:
            conn.execute(
                """
                INSERT INTO segments(
                    segment_key, display_name, population, daily_traffic,
                    baseline_conversion_rate, description
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(segment_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    population = excluded.population,
                    daily_traffic = excluded.daily_traffic,
                    baseline_conversion_rate = excluded.baseline_conversion_rate,
                    description = excluded.description
                """,
                (
                    segment["segment_key"],
                    segment["display_name"],
                    int(segment["population"]),
                    int(segment["daily_traffic"]),
                    float(segment["baseline_conversion_rate"]),
                    segment.get("description", "Derived from uploaded data"),
                ),
            )
            if segment["segment_key"] in existing:
                updated += 1
            else:
                inserted += 1
        conn.commit()
    finally:
        conn.close()

    return {"inserted": inserted, "updated": updated, "deleted": deleted}


_ROLE_HINTS: dict[str, tuple[str, ...]] = {
    "user_col": ("customerid", "customer_id", "user_id", "userid", "user", "customer", "account", "email"),
    "timestamp_col": ("invoicedate", "date", "timestamp", "created_at", "time", "occurred_at", "event_time"),
    "event_col": ("invoiceno", "invoice_no", "order_id", "orderid", "transaction_id", "invoice", "order", "session_id"),
    "value_col": ("unitprice", "price", "revenue", "amount", "total", "value", "spend", "sales"),
    "segment_col": ("country", "region", "segment", "market", "channel", "platform", "device", "cohort"),
}


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    """Best-guess column for each role, for pre-filling the mapping UI.

    Exact normalised match first, then substring -- so 'CustomerID' wins over
    'CustomerIDHash' for the user role.
    """
    normalised = {col: re.sub(r"[^0-9a-z]+", "", col.lower()) for col in columns}
    taken: set[str] = set()
    guesses: dict[str, str | None] = {}

    for role, hints in _ROLE_HINTS.items():
        pick = None
        for hint in hints:
            for col, norm in normalised.items():
                if col not in taken and norm == hint:
                    pick = col
                    break
            if pick:
                break
        if pick is None:
            for hint in hints:
                for col, norm in normalised.items():
                    if col not in taken and hint in norm:
                        pick = col
                        break
                if pick:
                    break
        if pick:
            taken.add(pick)
        guesses[role] = pick

    return guesses
