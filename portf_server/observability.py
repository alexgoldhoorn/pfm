"""
Optional observability for the API server: metrics, error tracking, traces.

Each piece is switched on by its own setting and does nothing otherwise, so a
default install carries no extra endpoints, network calls or overhead:

- ``PORTF_METRICS_ENABLED`` — Prometheus metrics on ``/metrics`` (request rate,
  latency, status codes, plus the ``pfm_llm_*`` metrics from
  ``portf_manager.telemetry``). ``PORTF_METRICS_TOKEN`` puts it behind a bearer
  token.
- ``PORTF_SENTRY_DSN`` — unhandled exceptions and ERROR log records go to any
  Sentry-protocol server (Sentry, GlitchTip, Bugsink).
- ``PORTF_OTEL_ENDPOINT`` — OpenTelemetry spans for every request and every LLM
  call, exported over OTLP/HTTP (Arize Phoenix, Grafana Tempo, Jaeger...).

Full reference: docs/features/observability.md.
"""

import logging
import secrets
from typing import TYPE_CHECKING, Optional

from fastapi import FastAPI, Request, Response

from .settings import ServerSettings

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)

# Probes and the scrape itself would otherwise dominate the numbers
_EXCLUDED_PATHS = ["/metrics", "/health"]


def setup_observability(app: FastAPI, settings: ServerSettings) -> list[str]:
    """Enable whichever observability features the settings ask for.

    A feature that fails to start is logged and skipped: telemetry must never
    stop the API from serving.

    Args:
        app: The FastAPI application, before it starts serving.
        settings: Server settings carrying the observability fields.

    Returns:
        The names of the features that were enabled.
    """
    enabled: list[str] = []
    for name, wanted, start in (
        ("sentry", bool(settings.sentry_dsn), _setup_sentry),
        ("otel", bool(settings.otel_endpoint), _setup_otel),
        ("metrics", settings.metrics_enabled, _setup_metrics),
    ):
        if not wanted:
            continue
        try:
            start(app, settings)
            enabled.append(name)
        except Exception as exc:
            logger.warning("Observability feature %s not enabled: %s", name, exc)
    if enabled:
        logger.info("Observability enabled: %s", ", ".join(enabled))
    return enabled


def _setup_sentry(app: FastAPI, settings: ServerSettings) -> None:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"pfm@{settings.version}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Request bodies and headers carry portfolio data and API keys
        send_default_pii=False,
        max_request_body_size="never",
    )


def _setup_otel(app: FastAPI, settings: ServerSettings) -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    provider = build_tracer_provider(
        settings.otel_service_name,
        settings.version,
        settings.environment,
        OTLPSpanExporter(endpoint=settings.otel_endpoint),
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=",".join(p.lstrip("/") for p in _EXCLUDED_PATHS),
        # One span per ASGI send/receive message is noise, not timing data
        exclude_spans=["receive", "send"],
    )


def build_tracer_provider(
    service_name: str, version: str, environment: str, exporter
) -> "TracerProvider":
    """Build a tracer provider that batches spans to ``exporter``.

    Args:
        service_name: Reported as ``service.name``.
        version: Reported as ``service.version``.
        environment: Reported as ``deployment.environment``.
        exporter: Any OpenTelemetry ``SpanExporter``.

    Returns:
        A configured ``TracerProvider``, not yet installed globally.
    """
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": version,
            "deployment.environment": environment,
            # Phoenix groups traces into projects by this attribute
            "openinference.project.name": service_name,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def _setup_metrics(app: FastAPI, settings: ServerSettings) -> None:
    from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        excluded_handlers=_EXCLUDED_PATHS,
        # Report exact status codes (404 vs 422), not 2xx/4xx buckets
        should_group_status_codes=False,
    ).instrument(app)

    token: Optional[str] = settings.metrics_token

    # include_in_schema=False keeps it out of /docs and the auth-coverage test
    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        if token:
            given = request.headers.get("authorization", "")
            if not secrets.compare_digest(given, f"Bearer {token}"):
                return Response(status_code=401)
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
