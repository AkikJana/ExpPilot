"""The common trace spine (docs/distributed-architecture.md §6): one OTel SDK
setup, imported by every service, with domain-tagged spans so the Collector can
route and sample by "interestingness" rather than uniformly.

Import is optional and guarded — opentelemetry-sdk is not in base
requirements.txt (see requirements-platform.txt) — so every module in this
codebase can call `span()` unconditionally and get a real span when the SDK is
installed and a no-op contextmanager when it isn't. This mirrors the pattern
agents/graph.py already uses for its MLflow span wrapper.
"""
from __future__ import annotations

import contextlib
import os
from typing import Any

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "exppilot")

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

_tracer = None


def _get_tracer():
    """Lazily construct the tracer provider on first use, not at import time —
    so importing this module never has a side effect, only calling span() does."""
    global _tracer
    if _tracer is not None:
        return _tracer
    if not _HAS_OTEL:
        return None

    resource = Resource(attributes={SERVICE_NAME: _SERVICE_NAME})
    provider = TracerProvider(resource=resource)

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            # Batching is the right tradeoff for a real network exporter: fewer,
            # larger requests to the collector, worth a background thread.
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            # OTLP exporter package not installed; fall through to console so
            # tracing is at least locally visible rather than silently dropped.
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        # No collector configured: console export keeps `span()` genuinely
        # observable in local dev without requiring any infra. Synchronous
        # (SimpleSpanProcessor), not batched — a background export thread has
        # nothing to buffer for stdout, and it outlives short-lived processes
        # (e.g. pytest) in a way that crashes writing to an already-closed
        # stream at teardown. Batching only earns its complexity for a real
        # network exporter, above.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(_SERVICE_NAME)
    return _tracer


@contextlib.contextmanager
def span(name: str, domain: str, **attributes: Any):
    """Open one span tagged with a `domain` attribute — the field the Collector's
    tail-sampling policy routes and retains on (§6's sampling table):

        decision    100%, 7y, immutable audit store
        experiment  100%, 2y, warehouse
        agent       100%, 90d, MLflow
        inference   10%, but 100% when the parent trace contains a decision span
        consumer    1-5%, PII-scrubbed at the collector, 14d

    A no-op when opentelemetry-sdk isn't installed — this function is always
    safe to call, exactly like agents/graph.py's MLflow `_span` helper it sits
    alongside.
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as current_span:
        current_span.set_attribute("domain", domain)
        for key, value in attributes.items():
            if value is not None:
                current_span.set_attribute(key, value)
        try:
            yield current_span
        except Exception as exc:
            current_span.record_exception(exc)
            raise


def current_trace_id() -> str | None:
    """The active trace id, if any — for stamping into exp.agent_runs.trace_id
    and rules.evaluations.trace_id so a decision's audit row and its inference
    spans share one lookup key (§6: "one trace_id answers ... why did this user
    see this")."""
    if not _HAS_OTEL:
        return None
    current = trace.get_current_span()
    context = current.get_span_context()
    if context is None or context.trace_id == 0:
        return None
    return format(context.trace_id, "032x")


def tracing_enabled() -> bool:
    """Whether a real tracer is configured — for health/status endpoints."""
    return _get_tracer() is not None
