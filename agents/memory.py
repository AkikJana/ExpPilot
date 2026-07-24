"""Long-term agent memory: plain SQL read/write over the `memory` table. No embeddings."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from data.db import get_conn
from shared.models import MemoryRecord


def new_id() -> str:
    """Generate a new memory record id."""
    return "mem_" + uuid.uuid4().hex[:8]


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def write(record: MemoryRecord) -> None:
    """Insert or replace a memory record."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO memory (id, kind, category, content, source_experiment_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.kind,
                record.category,
                record.content,
                record.source_experiment_id,
                record.created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch(kind: str, category: str, limit: int = 5) -> list[MemoryRecord]:
    """Fetch memory records of a given kind and category, most recent first."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, kind, category, content, source_experiment_id, created_at FROM memory "
            "WHERE kind = ? AND category = ? ORDER BY created_at DESC LIMIT ?",
            (kind, category, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        MemoryRecord(
            id=row["id"],
            kind=row["kind"],
            category=row["category"],
            content=row["content"],
            source_experiment_id=row["source_experiment_id"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def fetch_all(kind: str | None = None, category: str | None = None) -> list[MemoryRecord]:
    """Fetch memory records with optional kind/category filters, most recent first."""
    conn = get_conn()
    try:
        query = "SELECT id, kind, category, content, source_experiment_id, created_at FROM memory WHERE 1=1"
        params: list[str] = []
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if category is not None:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [
        MemoryRecord(
            id=row["id"],
            kind=row["kind"],
            category=row["category"],
            content=row["content"],
            source_experiment_id=row["source_experiment_id"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
