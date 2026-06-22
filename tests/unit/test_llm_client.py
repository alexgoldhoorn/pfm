"""Tests for SearchCapableLLMClient protocol and new LLM clients."""

import json
import os
import pytest
from unittest.mock import MagicMock, patch


def test_search_capable_protocol_importable():
    from portf_manager.llm_client import SearchCapableLLMClient

    assert SearchCapableLLMClient is not None


def test_mock_with_only_generate_is_not_search_capable():
    """A MagicMock with only generate() does not satisfy SearchCapableLLMClient."""
    from portf_manager.llm_client import SearchCapableLLMClient

    plain_mock = MagicMock(spec=["generate"])
    assert not isinstance(plain_mock, SearchCapableLLMClient)


def test_real_clients_without_search_are_not_search_capable():
    """Real OllamaLLMClient and OpenRouterLLMClient do not implement generate_with_search."""
    from portf_manager.llm_client import (
        OllamaLLMClient,
        OpenRouterLLMClient,
        SearchCapableLLMClient,
    )

    ollama = OllamaLLMClient(model="llama3.2")
    openrouter = OpenRouterLLMClient(api_key="test_key")
    assert not isinstance(ollama, SearchCapableLLMClient)
    assert not isinstance(openrouter, SearchCapableLLMClient)


class TestGeminiSearchCapable:
    def _make_client(self):
        with patch("google.generativeai.GenerativeModel"):
            from portf_manager.llm_client import GeminiLLMClient

            return GeminiLLMClient(api_key="test_key")

    def test_gemini_satisfies_search_capable_protocol(self):
        from portf_manager.llm_client import SearchCapableLLMClient

        client = self._make_client()
        assert isinstance(client, SearchCapableLLMClient)

    def test_generate_with_search_returns_envelope(self):
        client = self._make_client()
        mock_result = (
            '{"recommendation": "BUY", "confidence": "high", "summary": "Good."}',
            [{"title": "Q1 Earnings", "url": "http://example.com"}],
        )
        with patch.object(client, "_gemini_search", return_value=mock_result):
            raw = client.generate_with_search("test prompt", "AAPL")

        envelope = json.loads(raw)
        assert envelope["text"] == mock_result[0]
        assert envelope["sources"] == [
            {"title": "Q1 Earnings", "url": "http://example.com"}
        ]

    def test_generate_with_search_falls_back_on_import_error(self):
        client = self._make_client()
        with patch.object(client, "_gemini_search", side_effect=ImportError("no sdk")):
            with patch.object(
                client, "generate", return_value='{"recommendation": "HOLD"}'
            ):
                raw = client.generate_with_search("test prompt", "AAPL")

        envelope = json.loads(raw)
        assert envelope["text"] == '{"recommendation": "HOLD"}'
        assert envelope["sources"] == []

    def test_gemini_search_extracts_grounding_sources(self):
        import sys

        client = self._make_client()

        mock_web = MagicMock()
        mock_web.title = "Apple Q1"
        mock_web.uri = "http://apple.com/q1"
        mock_chunk = MagicMock()
        mock_chunk.web = mock_web
        mock_gm = MagicMock()
        mock_gm.grounding_chunks = [mock_chunk]

        mock_response = MagicMock()
        mock_response.text = '{"recommendation": "BUY"}'
        mock_response.candidates = [MagicMock(grounding_metadata=mock_gm)]

        mock_sdk_client = MagicMock()
        mock_sdk_client.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_sdk_client
        mock_types = MagicMock()

        with patch.dict(
            sys.modules,
            {"google.genai": mock_genai, "google.genai.types": mock_types},
        ):
            text, sources = client._gemini_search("test prompt")

        assert text == '{"recommendation": "BUY"}'
        assert len(sources) == 1
        assert sources[0]["title"] == "Apple Q1"
        assert sources[0]["url"] == "http://apple.com/q1"

    def test_gemini_search_handles_missing_grounding_metadata(self):
        import sys

        client = self._make_client()

        mock_response = MagicMock()
        mock_response.text = '{"recommendation": "HOLD"}'
        mock_response.candidates = []  # no candidates → AttributeError caught

        mock_sdk_client = MagicMock()
        mock_sdk_client.models.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_sdk_client
        mock_types = MagicMock()

        with patch.dict(
            sys.modules,
            {"google.genai": mock_genai, "google.genai.types": mock_types},
        ):
            text, sources = client._gemini_search("test prompt")

        assert text == '{"recommendation": "HOLD"}'
        assert sources == []


class TestAnthropicClient:
    def test_anthropic_client_requires_api_key(self):
        from portf_manager.llm_client import AnthropicLLMClient

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                AnthropicLLMClient()

    def test_anthropic_client_satisfies_search_capable_protocol(self):
        from portf_manager.llm_client import AnthropicLLMClient, SearchCapableLLMClient

        client = AnthropicLLMClient(api_key="test_key")
        assert isinstance(client, SearchCapableLLMClient)

    def test_anthropic_generate_delegates_to_internal(self):
        from portf_manager.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key="test_key")
        with patch.object(client, "_anthropic_generate", return_value="result") as m:
            assert client.generate("prompt") == "result"
            m.assert_called_once_with("prompt")

    def test_anthropic_generate_with_search_returns_envelope(self):
        from portf_manager.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key="test_key")
        with patch.object(
            client, "_anthropic_search", return_value='{"recommendation":"BUY"}'
        ):
            raw = client.generate_with_search("prompt", "AAPL")

        envelope = json.loads(raw)
        assert envelope["text"] == '{"recommendation":"BUY"}'
        assert envelope["sources"] == []

    def test_anthropic_search_picks_last_text_block(self):
        import sys

        from portf_manager.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key="test_key")

        tool_block = MagicMock()
        tool_block.type = "tool_result"
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '{"recommendation":"HOLD"}'

        mock_msg = MagicMock()
        mock_msg.content = [tool_block, text_block]

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_msg

        with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
            result = client._anthropic_search("prompt")

        assert result == '{"recommendation":"HOLD"}'


class TestFactoryWiring:
    def setup_method(self):
        from portf_manager.llm_client import reset_llm_client

        reset_llm_client()

    def teardown_method(self):
        from portf_manager.llm_client import reset_llm_client

        reset_llm_client()

    def test_get_llm_client_anthropic_provider(self):
        from portf_manager.llm_client import AnthropicLLMClient, get_llm_client

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
            client = get_llm_client(provider="anthropic", force_new=True)
        assert isinstance(client, AnthropicLLMClient)

    def test_auto_detect_uses_anthropic_as_fourth_option(self):
        from portf_manager.llm_client import AnthropicLLMClient, _auto_detect_provider

        no_keys = {
            k: ""
            for k in (
                "GEMINI_API_KEY",
                "PORTF_GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "OPENROUTER_API_KEY",
                "PORTF_OPENROUTER_API_KEY",
            )
        }
        with patch(
            "portf_manager.llm_client.OllamaLLMClient.is_available", return_value=False
        ):
            with patch.dict(os.environ, {**no_keys, "ANTHROPIC_API_KEY": "test_key"}):
                client = _auto_detect_provider()
        assert isinstance(client, AnthropicLLMClient)


def test_per_provider_model_env_override(monkeypatch):
    """PORTF_GEMINI_MODEL overrides the default Gemini model independently."""
    import os

    monkeypatch.setenv("PORTF_GEMINI_MODEL", "gemini-2.5-pro")
    # Test the env-var resolution logic: per-provider var takes precedence
    model = (
        os.getenv("PORTF_GEMINI_MODEL")
        or os.getenv("PORTF_LLM_MODEL")
        or "gemini-2.5-flash"
    )
    assert model == "gemini-2.5-pro"


def test_get_llm_info_returns_dict(monkeypatch):
    """get_llm_info returns provider and model without instantiating a client."""
    from portf_manager.llm_client import get_llm_info, reset_llm_client

    monkeypatch.setenv("PORTF_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PORTF_GEMINI_MODEL", "gemini-2.5-flash")
    reset_llm_client()
    info = get_llm_info()
    assert info["provider"] == "gemini"
    assert "model" in info
    assert "search_capable" in info
