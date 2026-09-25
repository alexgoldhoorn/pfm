"""Tests for LLM retry, typed errors and llm.call logging (llm_client._instrument)."""

import logging

import pytest
import requests

from portf_manager.llm_client import (
    LLMError,
    OllamaLLMClient,
    ToolCapableLLMClient,
    is_transient_llm_error,
)


class StatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.fixture
def ollama(monkeypatch):
    """An Ollama client whose raw HTTP call is replaced by a scripted sequence."""
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


def _llm_events(caplog):
    return [r for r in caplog.records if getattr(r, "pfm_event", None) == "llm.call"]


def test_first_try_success_logs_one_ok_event(ollama, caplog):
    ollama.script[:] = ["hello"]
    with caplog.at_level(logging.INFO):
        assert ollama.generate("hi") == "hello"
    (event,) = _llm_events(caplog)
    assert event.levelno == logging.INFO
    assert event.pfm_details["outcome"] == "ok"
    assert event.pfm_details["attempts"] == 1
    assert event.pfm_details["provider"] == "Ollama"


def test_transient_failure_is_retried_and_reported(ollama, caplog):
    ollama.script[:] = [requests.Timeout("read timed out"), "hello"]
    with caplog.at_level(logging.INFO):
        assert ollama.generate("hi") == "hello"
    (event,) = _llm_events(caplog)
    assert event.levelno == logging.WARNING
    assert event.pfm_details["outcome"] == "succeeded after retry"
    assert event.pfm_details["attempts"] == 2
    assert "timed out" in event.pfm_details["errors"][0]


def test_exhausted_retries_raise_llm_error(ollama, caplog):
    ollama.script[:] = [requests.ConnectionError("refused")] * 3
    with caplog.at_level(logging.INFO), pytest.raises(LLMError) as info:
        ollama.generate("hi")
    err = info.value
    assert (err.provider, err.model, err.operation, err.attempts) == (
        "Ollama",
        "test-model",
        "generate",
        3,
    )
    assert "failed after 3 attempts" in str(err)
    (event,) = _llm_events(caplog)
    assert event.levelno == logging.ERROR
    assert len(event.pfm_details["errors"]) == 3


def test_attempts_are_configurable(ollama, monkeypatch):
    monkeypatch.setenv("PORTF_LLM_MAX_ATTEMPTS", "1")
    ollama.script[:] = [requests.ConnectionError("refused"), "unused"]
    with pytest.raises(LLMError) as info:
        ollama.generate("hi")
    assert info.value.attempts == 1


def test_non_transient_error_is_not_retried(ollama):
    ollama.script[:] = [StatusError(401), "unused"]
    with pytest.raises(LLMError) as info:
        ollama.generate("hi")
    assert info.value.attempts == 1
    assert ollama.script == ["unused"]


def test_llm_error_is_still_a_runtime_error(ollama):
    # Existing callers catch RuntimeError; they must keep working
    ollama.script[:] = [StatusError(400)]
    with pytest.raises(RuntimeError):
        ollama.generate("hi")


@pytest.mark.parametrize(
    "exc,expected",
    [
        (StatusError(429), True),
        (StatusError(503), True),
        (StatusError(400), False),
        (StatusError(401), False),
        (requests.Timeout(), True),
        (requests.ConnectionError(), True),
        (RuntimeError("Empty response from Gemini API"), True),
        (RuntimeError("model is overloaded"), True),
        (RuntimeError("prompt uses 5000 tokens"), False),
        (RuntimeError("safety block"), False),
        (ValueError("API key required"), False),
    ],
)
def test_transient_classification(exc, expected):
    assert is_transient_llm_error(exc) is expected


def test_capability_checks_survive_instrumentation():
    client = OllamaLLMClient(model="m")
    # Ollama has no search grounding; wrapping must not invent the method
    assert not hasattr(client, "generate_with_search")
    assert isinstance(client, ToolCapableLLMClient)
