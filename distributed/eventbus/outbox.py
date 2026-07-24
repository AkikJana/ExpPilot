"""The transactional outbox pattern — the default EventBus backend.

docs/distributed-architecture.md §2: "Services never dual-write; every state
change that other domains care about is emitted transactionally" via outbox +
CDC (Debezium) -> Kafka.

In production, `publish()` is a row INSERT in the *same transaction* as the
domain write it accompanies (e.g. writing a Decision row), and a CDC connector
tails the table and republishes to Kafka — callers never talk to Kafka directly,
so a broker outage cannot lose an event or block a request.

This SQLite-backed class stands in for both halves during local dev: `publish()`
is the transactional insert, `drain()` is the CDC tail + republish, done
synchronously and deterministically — no broker, no infra, fully testable.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from distributed.eventbus.interface import Event

_DB_PATH = Path(__file__).resolve().parent.parent / "eventbus_outbox.db"


class OutboxEventBus:
    """SQLite-backed transactional outbox. Same interface as a real Kafka backend
    (distributed.eventbus.kafka_bus.KafkaEventBus) — callers are backend-agnostic."""

    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._handlers: dict[str, list[Callable[[Event], None]]] = {}
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
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_topic ON outbox(topic, consumed_at)")
            conn.commit()
        finally:
            conn.close()

    def publish(self, topic: str, payload: dict[str, Any]) -> Event:
        """Append an event. In production this shares the caller's DB transaction;
        here it commits immediately since there is no larger transaction to join."""
        event = Event(topic=topic, payload=payload)
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO outbox (event_id, topic, payload, published_at) VALUES (?, ?, ?, ?)",
                (event.event_id, event.topic, json.dumps(event.payload), event.published_at),
            )
            conn.commit()
        finally:
            conn.close()
        return event

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    def drain(self) -> int:
        """Stand-in for the CDC tail: deliver every unconsumed event to its
        subscribed handlers, then mark it consumed. Returns the count delivered.

        A handler exception marks the event delivered anyway rather than retrying
        forever — matches at-least-once-with-a-dead-letter-path semantics rather
        than blocking the whole outbox on one bad handler.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id, event_id, topic, payload, published_at FROM outbox "
                "WHERE consumed_at IS NULL ORDER BY id"
            ).fetchall()
            delivered = 0
            for row in rows:
                event = Event(
                    topic=row["topic"],
                    payload=json.loads(row["payload"]),
                    event_id=row["event_id"],
                    published_at=row["published_at"],
                )
                for handler in self._handlers.get(row["topic"], []):
                    try:
                        handler(event)
                    except Exception:
                        pass
                conn.execute(
                    "UPDATE outbox SET consumed_at = datetime('now') WHERE id = ?", (row["id"],)
                )
                delivered += 1
            conn.commit()
        finally:
            conn.close()
        return delivered

    def unconsumed_count(self, topic: str | None = None) -> int:
        """Backlog depth — the signal a real deployment would alert on."""
        conn = self._conn()
        try:
            if topic:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM outbox WHERE consumed_at IS NULL AND topic = ?", (topic,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM outbox WHERE consumed_at IS NULL").fetchone()
        finally:
            conn.close()
        return row["c"]
