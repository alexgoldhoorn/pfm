# Observability: metrics, error tracking, traces

Three optional features, each switched on by its own setting and inert otherwise. A
default install exposes no extra endpoint, makes no network call and adds no
middleware. Setup lives in `portf_server/observability.py` (`setup_observability`,
called once in `app.py`), and the LLM hooks live in `portf_manager/telemetry.py`.

| Setting | Effect |
|---|---|
| `PORTF_METRICS_ENABLED=true` | Prometheus metrics on `GET /metrics` |
| `PORTF_METRICS_TOKEN=<token>` | `/metrics` requires `Authorization: Bearer <token>` (401 otherwise) |
| `PORTF_SENTRY_DSN=<dsn>` | Errors go to a Sentry-protocol server (Sentry, GlitchTip, Bugsink) |
| `PORTF_SENTRY_TRACES_SAMPLE_RATE` | Share of requests Sentry also records as performance traces (default 0) |
| `PORTF_OTEL_ENDPOINT=<url>` | OpenTelemetry spans over OTLP/HTTP, e.g. `http://phoenix:6006/v1/traces` |
| `PORTF_OTEL_SERVICE_NAME` | `service.name` and Phoenix project name (default `pfm`) |
| `PORTF_OTEL_CAPTURE_LLM_CONTENT=true` | Attach prompt and response text to LLM spans (off by default: prompts carry portfolio data) |

`PORTF_OTEL_CAPTURE_LLM_CONTENT` is read straight from the environment by
`telemetry.py`, like the other `PORTF_LLM_*` variables. The rest are `ServerSettings`
fields.

A feature that fails to start (a bad DSN, say) is logged as a warning and skipped.
Telemetry never stops the API from serving.

## Metrics (`/metrics`)

- HTTP: `prometheus-fastapi-instrumentator` records `http_requests_total{handler,method,status}`,
  `http_request_duration_seconds` (histogram) and request/response sizes. The route
  template is the `handler` label, so `/assets/12` and `/assets/13` count as one
  handler. `/metrics` and `/health` are excluded.
- LLM (`portf_manager/telemetry.py`, recorded from `_log_llm_call`, so every call
  `_instrument` wraps is counted):
  - `pfm_llm_calls_total{provider,model,operation,outcome}` (outcome is `ok`, `succeeded after retry` or `failed`)
  - `pfm_llm_call_duration_seconds{provider,operation}` (wall time including retries)
  - `pfm_llm_call_attempts_total{provider,operation}`
- Process: the default `prometheus_client` collectors (`process_resident_memory_bytes`,
  `process_cpu_seconds_total`, …).

The LLM metrics sit in the default registry whether or not `/metrics` is enabled.
That costs nothing, and it keeps `llm_client` free of configuration checks.
Metrics are per process: with several gunicorn workers each worker reports only
itself, and there is no multiprocess mode.

## Error tracking (Sentry SDK)

`sentry_sdk.init` with `send_default_pii=False` and `max_request_body_size="never"`,
because request bodies carry statements and headers carry API keys. It reports:

- unhandled exceptions. The global `Exception` handler in `app.py` returns the 500,
  and Starlette re-raises afterwards, so Sentry still sees the exception.
- every `logging.ERROR` record, through Sentry's default logging integration. That
  includes a final `LLMError` failure (`llm.call` logged at ERROR).

Release is `pfm@<version>`, and environment is `PORTF_ENVIRONMENT`.

## Traces (OpenTelemetry)

- `FastAPIInstrumentor` creates one server span per request. ASGI `send`/`receive`
  sub-spans are excluded as noise, and `/metrics` and `/health` are excluded.
- `telemetry.llm_span` wraps each instrumented LLM call (the whole retry loop) in an
  `llm.<operation>` span that follows the **OpenInference** conventions
  (`openinference.span.kind=LLM`, `llm.provider`, `llm.model_name`,
  `pfm.llm.attempts`, optionally `input.value`/`output.value`). Arize Phoenix renders
  these as LLM calls, nested under the request that made them. A failed call records
  the exception and ERROR status.
- The resource carries `openinference.project.name`, which Phoenix uses to sort
  traces into projects.

The OpenTelemetry API is a no-op until a tracer provider is installed, so
`llm_span` costs nothing in the CLI and tests. The unit tests route spans into an
`InMemorySpanExporter` by patching `telemetry.trace.get_tracer`.

## Reference deployment (homelab)

The author's instance uses self-hosted, single-container backends:

- **Prometheus + Grafana** scrape `/metrics` with the token.
- **Bugsink** (Sentry protocol, SQLite) receives errors.
- **Arize Phoenix** (OTLP, SQLite) receives traces.

Any Sentry-compatible server or OTLP collector works (Grafana Tempo, Jaeger, SigNoz,
Langfuse's OTLP endpoint).

The backend container must be able to reach those services, so either put them on
`portf_net` or use an address the container can route to.
