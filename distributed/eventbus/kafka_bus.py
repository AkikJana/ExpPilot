"""Production EventBus backend: Kafka (or a Kafka-API-compatible broker, e.g.
Redpanda — see distributed/docker-compose.yml).

Honest scope note: this class is reviewed, type-checked code, not integration-
tested — there is no live Kafka broker in this development environment to test
it against (§ operational notes in distributed/README.md). The interface it
implements (distributed.eventbus.interface.EventBus) is the same one
OutboxEventBus implements and *is* tested, so swapping backends is the only risk
surface, and it is a thin one: this class is a straight kafka-python wrapper.

Import is optional and guarded, matching the existing pattern in agents/llm.py
for langchain_groq/langchain_anthropic — kafka-python is not a hard dependency
of the base product.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable

from distributed.eventbus.interface import Event


class KafkaUnavailableError(RuntimeError):
    """Raised when KafkaEventBus is constructed but kafka-python isn't installed,
    or the configured broker can't be reached at construction time."""


class KafkaEventBus:
    """Thin kafka-python wrapper behind the shared EventBus interface.

    `subscribe()` starts one background consumer thread per topic on first
    subscription; each delivers messages to its registered handlers as they
    arrive, mirroring the outbox backend's "deliver then continue" semantics
    (a handler exception does not stop delivery of subsequent messages).
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092", client_id: str = "expPilot"):
        try:
            from kafka import KafkaConsumer, KafkaProducer
        except ImportError as exc:
            raise KafkaUnavailableError(
                "kafka-python is not installed. pip install -r requirements-platform.txt"
            ) from exc

        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._KafkaConsumer = KafkaConsumer
        try:
            self._producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                client_id=client_id,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
        except Exception as exc:
            raise KafkaUnavailableError(f"cannot reach Kafka at {bootstrap_servers}: {exc}") from exc

        self._handlers: dict[str, list[Callable[[Event], None]]] = {}
        self._consumer_threads: dict[str, threading.Thread] = {}

    def publish(self, topic: str, payload: dict[str, Any]) -> Event:
        event = Event(topic=topic, payload=payload)
        self._producer.send(
            topic,
            key=event.event_id,
            value={
                "event_id": event.event_id,
                "payload": event.payload,
                "published_at": event.published_at,
            },
        )
        self._producer.flush()
        return event

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        self._handlers.setdefault(topic, []).append(handler)
        if topic not in self._consumer_threads:
            thread = threading.Thread(target=self._consume_loop, args=(topic,), daemon=True)
            self._consumer_threads[topic] = thread
            thread.start()

    def _consume_loop(self, topic: str) -> None:
        consumer = self._KafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            client_id=f"{self._client_id}-{topic}",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        for message in consumer:
            body = message.value
            event = Event(
                topic=topic,
                payload=body.get("payload", {}),
                event_id=body.get("event_id", ""),
                published_at=body.get("published_at", ""),
            )
            for handler in self._handlers.get(topic, []):
                try:
                    handler(event)
                except Exception:
                    pass
