"""
OpenTelemetry tracing: one trace follows an alert through the whole system.
  POST /alerts (API) -> SQL -> Celery publish -> triage task (worker) -> collectors
  (Prometheus/Loki/GitHub calls) -> embedding -> LLM call -> notification
Open Jaeger and you see where the time went (almost always: the LLM call).

Rules:
  - Off by default (OTEL_ENABLED=false). Tracing is a bonus, never a requirement.
  - The OpenTelemetry packages are optional: if they are missing, span() is a no-op.
  - setup_tracing() never raises; a tracing problem must not stop the app.
"""

import logging
from contextlib import contextmanager

from app.config import settings

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    _HAVE_API = True
except ImportError:
    trace = None
    _HAVE_API = False

_configured = False

class _NoSpan:
    """Stand-in span used when OpenTelemetry is not installed."""
    def set_attribute(self, *args, **kwargs):
        pass

def setup_tracing(service_name: str, app=None, engine=None) -> bool:
    """
    Call ONCE per process:
      API    : setup_tracing("incidentlens-api", app=app, engine=engine)   (main.py)
      worker : setup_tracing("incidentlens-worker", engine=engine)         (celeryapp.py)
    Returns True if tracing is active.
    """
    global _configured
    if not settings.OTEL_ENABLED:
        logger.info("tracing disabled (OTEL_ENABLED=false)")
        return False
    if _configured:
        return True
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        logger.warning("OTEL_ENABLED=true but OpenTelemetry packages are missing: %s", e)
        return False

    # service.name is the name you pick in the Jaeger dropdown.
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    # OTLP over HTTP goes to <endpoint>/v1/traces. BatchSpanProcessor sends spans in the
    # background in batches, so tracing never slows a request down.
    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    _instrument(app, engine)
    _configured = True
    logger.info("tracing enabled: service=%s -> %s", service_name, endpoint)
    return True

def _instrument(app, engine) -> None:
    """
    Auto-instrumentation = ready-made spans for common libraries, no code changes.
    Each block is independent: if one library fails, the others still work.
    """
    # Every HTTP call made with httpx (Prometheus, Loki, GitHub, Gemini, embeddings).
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.warning("httpx tracing not enabled: %s", e)

    # Celery: passes the trace id through RabbitMQ so the worker's spans join the
    # API's trace (without this you would see two unrelated traces).
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
    except Exception as e:
        logger.warning("celery tracing not enabled: %s", e)

    # Every SQL statement on our engine.
    if engine is not None:
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            SQLAlchemyInstrumentor().instrument(engine=engine)
        except Exception as e:
            logger.warning("sqlalchemy tracing not enabled: %s", e)

    # Every incoming API request. Health checks and /metrics are skipped: they are noise.
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
        except Exception as e:
            logger.warning("fastapi tracing not enabled: %s", e)


@contextmanager
def span(name: str, attributes: dict | None = None):
    """
    Our own span around a block of code:
        with span("llm.triage", {"incident.id": 7}) as sp:
            ...
            set_attrs(sp, {"llm.model": "gemini-2.5-flash"})
    If an exception escapes the block, OpenTelemetry marks the span as an error.
    If tracing is off, this does nothing and costs almost nothing.
    """
    if not _HAVE_API:
        yield _NoSpan()
        return
    with trace.get_tracer("incidentlens").start_as_current_span(name) as sp:
        set_attrs(sp, attributes)
        yield sp

def set_attrs(sp, attributes: dict | None) -> None:
    """Attach key/value details to a span. None values are skipped; OTel only accepts
    str/bool/int/float, so everything else becomes a string."""
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        if not isinstance(value, (str, bool, int, float)):
            value = str(value)
        sp.set_attribute(key, value)