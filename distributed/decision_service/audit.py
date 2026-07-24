"""Audit persistence for evaluation results. Append-only, pluggable backend.

Production wires a Postgres implementation against the `rules.evaluations` table
(docs/distributed-architecture.md §2, §4) — an INSERT-only role grant, no service
can UPDATE or DELETE a row. The SQLite implementation here is the dev/test
backend: same interface, same append-only guarantee, no external infra required.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from distributed.decision_service.schemas import EvaluationResult

_DB_PATH = Path(__file__).resolve().parent.parent / "decision_audit.db"


class AuditSink(Protocol):
    """Port every audit backend implements. Never raises into the caller's request path."""

    def record(self, result: EvaluationResult) -> None: ...

    def history(self, experiment_id: str) -> list[EvaluationResult]: ...


class SqliteAuditSink:
    """Dev/test backend. One append-only table, one process, no server required."""

    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    day INTEGER NOT NULL,
                    pack_id TEXT NOT NULL,
                    pack_version TEXT NOT NULL,
                    action TEXT NOT NULL,
                    inputs_digest TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record(self, result: EvaluationResult) -> None:
        """Append one evaluation. Never raises: an audit failure must not block a decision."""
        try:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO evaluations "
                    "(experiment_id, day, pack_id, pack_version, action, inputs_digest, "
                    " evaluated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.experiment_id,
                        result.day,
                        result.pack_id,
                        result.pack_version,
                        result.action,
                        result.inputs_digest,
                        result.evaluated_at,
                        result.model_dump_json(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def history(self, experiment_id: str) -> list[EvaluationResult]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT payload FROM evaluations WHERE experiment_id = ? ORDER BY id",
                (experiment_id,),
            ).fetchall()
        finally:
            conn.close()
        return [EvaluationResult.model_validate_json(r["payload"]) for r in rows]


_default_sink: AuditSink = SqliteAuditSink()


def get_audit_sink() -> AuditSink:
    """The active audit backend. Swap via dependency injection in main.py for
    production (Postgres) vs test (in-memory) without touching evaluator.py."""
    return _default_sink
