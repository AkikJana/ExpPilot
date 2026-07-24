"""EventBus tests. The outbox backend is fully exercised (SQLite, no infra). The
Kafka backend's only testable-without-a-broker property is that it degrades
predictably when kafka-python isn't installed or the broker is unreachable —
its actual message delivery is reviewed code, not integration-tested here."""
from __future__ import annotations

from pathlib import Path

import pytest

from distributed.eventbus.kafka_bus import KafkaEventBus, KafkaUnavailableError
from distributed.eventbus.outbox import OutboxEventBus


@pytest.fixture()
def bus(tmp_path: Path) -> OutboxEventBus:
    """A fresh outbox per test — never touches the shared dev DB file."""
    return OutboxEventBus(db_path=tmp_path / "test_outbox.db")


def test_publish_returns_an_event_with_a_stable_id(bus: OutboxEventBus):
    event = bus.publish("exp.decisions", {"experiment_id": "exp_1", "action": "pause"})
    assert event.topic == "exp.decisions"
    assert event.payload["action"] == "pause"
    assert event.event_id  # non-empty, generated


def test_drain_delivers_to_subscribed_handlers(bus: OutboxEventBus):
    received = []
    bus.subscribe("exp.decisions", lambda e: received.append(e.payload))

    bus.publish("exp.decisions", {"action": "scale"})
    bus.publish("exp.decisions", {"action": "pause"})
    delivered = bus.drain()

    assert delivered == 2
    assert [r["action"] for r in received] == ["scale", "pause"]


def test_drain_is_idempotent_once_consumed(bus: OutboxEventBus):
    """A second drain() must not redeliver events already marked consumed —
    the property that makes the outbox safe against a crashed-and-restarted
    consumer replaying from the wrong offset."""
    received = []
    bus.subscribe("exp.decisions", lambda e: received.append(e))

    bus.publish("exp.decisions", {"action": "scale"})
    first_drain = bus.drain()
    second_drain = bus.drain()

    assert first_drain == 1
    assert second_drain == 0
    assert len(received) == 1


def test_unconsumed_events_are_delivered_even_without_a_subscriber_at_publish_time(bus: OutboxEventBus):
    """Publish-then-subscribe-then-drain must still deliver — the outbox does not
    require a live subscriber at publish time, unlike a plain pub/sub callback."""
    bus.publish("exp.decisions", {"action": "rollback"})

    received = []
    bus.subscribe("exp.decisions", lambda e: received.append(e.payload))
    bus.drain()

    assert received == [{"action": "rollback"}]


def test_a_failing_handler_does_not_block_delivery_to_other_handlers(bus: OutboxEventBus):
    order = []
    bus.subscribe("exp.decisions", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe("exp.decisions", lambda e: order.append("second handler ran"))

    bus.publish("exp.decisions", {"action": "scale"})
    delivered = bus.drain()

    assert delivered == 1
    assert order == ["second handler ran"]


def test_unconsumed_count_reflects_backlog(bus: OutboxEventBus):
    bus.publish("exp.decisions", {"action": "scale"})
    bus.publish("exp.decisions", {"action": "pause"})
    assert bus.unconsumed_count() == 2
    assert bus.unconsumed_count("exp.decisions") == 2
    assert bus.unconsumed_count("other.topic") == 0

    bus.drain()
    assert bus.unconsumed_count() == 0


def test_topics_are_independent(bus: OutboxEventBus):
    a_events, b_events = [], []
    bus.subscribe("topic.a", lambda e: a_events.append(e))
    bus.subscribe("topic.b", lambda e: b_events.append(e))

    bus.publish("topic.a", {"x": 1})
    bus.publish("topic.b", {"x": 2})
    bus.drain()

    assert len(a_events) == 1 and a_events[0].payload == {"x": 1}
    assert len(b_events) == 1 and b_events[0].payload == {"x": 2}


def test_kafka_backend_degrades_predictably_without_the_optional_dependency():
    """kafka-python is not in the base requirements.txt (see requirements-platform.txt);
    on a machine without it, constructing KafkaEventBus must raise a clear,
    typed error rather than an opaque ImportError or a hang."""
    try:
        import kafka  # noqa: F401

        pytest.skip("kafka-python is installed in this environment; degrade-path not exercised")
    except ImportError:
        pass

    with pytest.raises(KafkaUnavailableError, match="kafka-python is not installed"):
        KafkaEventBus()
