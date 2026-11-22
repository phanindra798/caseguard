"""OpenTelemetry for the agent: one span per conversation turn and per graph node, and trace context
sent to the Case API on every HTTP call (so the Java spans join the same trace).

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. http://localhost:4318 for Jaeger or any OTLP
collector). Without it, the OpenTelemetry API is a no-op and costs nothing.
"""
from __future__ import annotations

import os

from opentelemetry import trace

tracer = trace.get_tracer("caseguard-agent")
_configured = False


def setup_tracing(service_name: str = "caseguard-agent") -> bool:
    """Configure export once per process. Returns True when tracing is on."""
    global _configured
    if _configured:
        return True
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), schedule_delay_millis=200))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()        # every Case API call carries the W3C traceparent
    _configured = True
    return True


def flush() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()


def current_traceparent() -> str | None:
    """The active span as a W3C traceparent string (a plain string is safe to put in graph config)."""
    from opentelemetry.propagate import inject
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent")


def traced_node(name: str, fn):
    """Wrap a graph node in a span that is a CHILD of the turn's span.

    LangGraph doesn't carry Python's tracing context into the code that runs nodes, so without help
    each node would start its own, unconnected trace. The runner puts the turn's traceparent into
    config["configurable"]["traceparent"], and every node span continues from it."""
    import inspect
    from opentelemetry.propagate import extract

    wants_config = "config" in inspect.signature(fn).parameters

    def wrapper(state, config):
        parent = (config.get("configurable") or {}).get("traceparent")
        ctx = extract({"traceparent": parent}) if parent else None
        with tracer.start_as_current_span(f"agent.node.{name}", context=ctx) as span:
            update = fn(state, config) if wants_config else fn(state)
            if isinstance(update, dict):
                for d in update.get("decisions", []) or []:
                    span.add_event("decision", {"action": d.get("action") or "",
                                                "tool": d.get("tool") or "", "reason": d.get("reason") or ""})
                if update.get("execute_outcome"):
                    span.set_attribute("caseguard.execute_outcome", update["execute_outcome"])
                if update.get("eligibility"):
                    span.set_attribute("caseguard.eligibility", update["eligibility"])
            return update

    wrapper.__name__ = getattr(fn, "__name__", name)
    return wrapper
