"""
Telemetry hooks for LLM calls: Prometheus metrics and OpenTelemetry spans.

Both are inert until something turns them on. The metrics live in the default
Prometheus registry and are only visible once the server exposes ``/metrics``
(``PORTF_METRICS_ENABLED``). The spans go through the global OpenTelemetry
tracer, which is a no-op until ``portf_server.observability`` installs a real
tracer provider (``PORTF_OTEL_ENDPOINT``). So the CLI and the tests pay nothing.

Spans follow the OpenInference conventions, so Arize Phoenix shows them as LLM
calls. Prompt and response text is attached only when
``PORTF_OTEL_CAPTURE_LLM_CONTENT`` is true: prompts carry portfolio data.
"""

import contextlib
import os
from typing import Any, Iterator, Optional

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from prometheus_client import Counter, Histogram

# Truncate captured prompt/response text so one huge import can't bloat a span
_MAX_CONTENT_CHARS = 20_000

LLM_CALLS = Counter(
    "pfm_llm_calls_total",
    "LLM provider calls, by outcome (ok, succeeded after retry, failed)",
    ["provider", "model", "operation", "outcome"],
)
LLM_DURATION = Histogram(
    "pfm_llm_call_duration_seconds",
    "Wall time of one LLM call including retries",
    ["provider", "operation"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
LLM_ATTEMPTS = Counter(
    "pfm_llm_call_attempts_total",
    "Individual LLM attempts, retries included",
    ["provider", "operation"],
)


def capture_content() -> bool:
    """Return whether prompt/response text may be attached to spans."""
    return os.getenv("PORTF_OTEL_CAPTURE_LLM_CONTENT", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text[:_MAX_CONTENT_CHARS]


def observe_llm_call(
    provider: str,
    model: str,
    operation: str,
    outcome: str,
    attempts: int,
    duration_s: float,
) -> None:
    """Record one finished LLM call in the Prometheus metrics."""
    LLM_CALLS.labels(provider, model, operation, outcome).inc()
    LLM_DURATION.labels(provider, operation).observe(duration_s)
    LLM_ATTEMPTS.labels(provider, operation).inc(attempts)


@contextlib.contextmanager
def llm_span(provider: str, model: str, operation: str, args: tuple) -> Iterator[Span]:
    """Open an OpenInference-style LLM span around one provider call."""
    tracer = trace.get_tracer("portf_manager.llm_client")
    with tracer.start_as_current_span(
        f"llm.{operation}", record_exception=True, set_status_on_exception=True
    ) as span:
        if span.is_recording():
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("llm.provider", provider)
            span.set_attribute("llm.model_name", model)
            span.set_attribute("pfm.llm.operation", operation)
            if capture_content() and args:
                prompt = _as_text(args[0])
                if prompt is not None:
                    span.set_attribute("input.value", prompt)
        yield span


def finish_llm_span(span: Span, attempts: int, result: Any = None) -> None:
    """Attach the outcome of a successful call to its span."""
    if not span.is_recording():
        return
    span.set_attribute("pfm.llm.attempts", attempts)
    if capture_content():
        output = _as_text(result)
        if output is not None:
            span.set_attribute("output.value", output)
    span.set_status(Status(StatusCode.OK))
