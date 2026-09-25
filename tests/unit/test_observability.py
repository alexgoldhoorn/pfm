"""Tests for optional observability (portf_server.observability, telemetry)."""

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from prometheus_client import REGISTRY

from portf_manager import telemetry
from portf_manager.llm_client import LLMError, OllamaLLMClient
from portf_server import observability
from portf_server.observability import build_tracer_provider, setup_observability
from portf_server.settings import ServerSettings


def _settings(**overrides) -> ServerSettings:
    # _env_file=None keeps the developer's .env.local out of the test
    return ServerSettings(_env_file=None, **overrides)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def ollama(monkeypatch):
    """An Ollama client whose HTTP call replays a scripted sequence."""
    client = OllamaLLMClient(model="test-model")
    script: list = []

    def fake_post(*args, **kwargs):
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"response": "%s"}' % step.encode()
        return response

    monkeypatch.setattr("portf_manager.llm_client.requests.post", fake_post)
    client.script = script
    return client


@pytest.fixture
def spans(monkeypatch):
    """Route telemetry's spans into an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider("pfm-test", "0", "development", exporter)
    monkeypatch.setattr(
        telemetry.trace, "get_tracer", lambda name, *a, **k: provider.get_tracer(name)
    )

    def finished():
        provider.force_flush()
        return exporter.get_finished_spans()

    return finished


def test_defaults_enable_nothing():
    app = _app()
    assert setup_observability(app, _settings()) == []
    assert TestClient(app).get("/metrics").status_code == 404


def test_metrics_endpoint_exposes_http_and_llm_metrics():
    app = _app()
    assert setup_observability(app, _settings(metrics_enabled=True)) == ["metrics"]
    client = TestClient(app)
    client.get("/ping")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert 'handler="/ping"' in response.text
    assert "pfm_llm_calls_total" in response.text


def test_metrics_token_is_enforced():
    app = _app()
    setup_observability(app, _settings(metrics_enabled=True, metrics_token="s3cret"))
    client = TestClient(app)
    assert client.get("/metrics").status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.get("/metrics", headers=wrong).status_code == 401
    right = {"Authorization": "Bearer s3cret"}
    assert client.get("/metrics", headers=right).status_code == 200


def test_sentry_is_initialised_without_pii(monkeypatch):
    calls = []
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: calls.append(kw))
    dsn = "http://key@bugsink.invalid/1"
    assert setup_observability(_app(), _settings(sentry_dsn=dsn)) == ["sentry"]
    (kwargs,) = calls
    assert kwargs["dsn"] == dsn
    assert kwargs["send_default_pii"] is False
    assert kwargs["traces_sample_rate"] == 0.0


def test_a_failing_feature_is_skipped_not_raised(monkeypatch):
    def boom(app, settings):
        raise RuntimeError("collector config broken")

    monkeypatch.setattr(observability, "_setup_sentry", boom)
    settings = _settings(sentry_dsn="http://k@x.invalid/1", metrics_enabled=True)
    assert setup_observability(_app(), settings) == ["metrics"]


def test_llm_call_emits_span_without_content_by_default(ollama, spans, monkeypatch):
    monkeypatch.delenv("PORTF_OTEL_CAPTURE_LLM_CONTENT", raising=False)
    ollama.script[:] = ["hello"]
    assert ollama.generate("secret prompt") == "hello"
    (span,) = spans()
    assert span.name == "llm.generate"
    assert span.attributes["openinference.span.kind"] == "LLM"
    assert span.attributes["llm.provider"] == "Ollama"
    assert span.attributes["llm.model_name"] == "test-model"
    assert span.attributes["pfm.llm.attempts"] == 1
    assert "input.value" not in span.attributes
    assert "output.value" not in span.attributes
    assert span.status.status_code == StatusCode.OK


def test_llm_span_captures_content_when_enabled(ollama, spans, monkeypatch):
    monkeypatch.setenv("PORTF_OTEL_CAPTURE_LLM_CONTENT", "true")
    ollama.script[:] = [requests.Timeout("slow"), "hello"]
    ollama.generate("the prompt")
    (span,) = spans()
    assert span.attributes["input.value"] == "the prompt"
    assert span.attributes["output.value"] == "hello"
    assert span.attributes["pfm.llm.attempts"] == 2


def test_failed_llm_call_marks_span_as_error(ollama, spans):
    ollama.script[:] = [ValueError("bad request")]
    with pytest.raises(LLMError):
        ollama.generate("hi")
    (span,) = spans()
    assert span.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)


def test_llm_call_is_counted_in_metrics(ollama):
    labels = {
        "provider": "Ollama",
        "model": "test-model",
        "operation": "generate",
        "outcome": "ok",
    }
    before = REGISTRY.get_sample_value("pfm_llm_calls_total", labels) or 0
    ollama.script[:] = ["hello"]
    ollama.generate("hi")
    assert REGISTRY.get_sample_value("pfm_llm_calls_total", labels) == before + 1
