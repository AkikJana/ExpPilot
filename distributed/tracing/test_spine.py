"""Tracing spine tests. opentelemetry-sdk is an optional platform dependency
(requirements-platform.txt), so the realistic case in most environments —
including this one — is the no-op path: span() must be safe to call
unconditionally, and an exception inside the span must still propagate."""
from __future__ import annotations

import pytest

from distributed.tracing.spine import current_trace_id, span, tracing_enabled


def test_span_is_always_safe_to_call():
    with span("test-op", domain="agent", experiment_id="exp_1") as s:
        pass  # must not raise whether or not opentelemetry-sdk is installed


def test_span_propagates_exceptions_from_the_wrapped_body():
    """A no-op or real span must never swallow an exception raised inside it —
    the same property agents/graph.py's MLflow span wrapper was bug-fixed for
    earlier in this project (it used to `yield` twice and mask real errors)."""
    with pytest.raises(ValueError, match="boom"):
        with span("test-op", domain="agent"):
            raise ValueError("boom")


def test_nested_spans_do_not_raise():
    with span("outer", domain="decision"):
        with span("inner", domain="inference", tier="local"):
            pass


def test_current_trace_id_returns_none_or_a_hex_string():
    """Without an active span (or without the SDK installed) this must return
    None cleanly, never raise — callers stamp it into audit rows unconditionally."""
    trace_id = current_trace_id()
    assert trace_id is None or isinstance(trace_id, str)


def test_tracing_enabled_reflects_sdk_availability():
    """Must return a plain bool either way — used by health/status endpoints."""
    assert isinstance(tracing_enabled(), bool)
