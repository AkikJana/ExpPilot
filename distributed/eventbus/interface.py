"""The EventBus port every backend implements (docs/distributed-architecture.md §2, §5).

Publishers never talk to Kafka directly — they publish through this interface, so
swapping the outbox-pattern default for a real Kafka/Redpanda backend in
production is a wiring change, not a rewrite of every caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class Event:
    """One immutable fact published to a topic. `event_id` makes consumers
    idempotent-by-construction: a consumer that has seen this id can always skip it."""

    topic: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: uuid4().hex)
    published_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EventBus(Protocol):
    """Publish/consume port. Implementations: outbox.py (Postgres/SQLite, the
    transactional default) and kafka_bus.py (production, optional import)."""

    def publish(self, topic: str, payload: dict[str, Any]) -> Event: ...

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for a topic. Backend-dependent delivery semantics:
        the outbox backend delivers synchronously on drain(); a real Kafka backend
        delivers asynchronously as messages arrive."""
        ...
