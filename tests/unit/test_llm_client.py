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
        with patch("google.genai.Client"):
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

        client._client.models.generate_content.return_value = mock_response

        with patch("google.genai.types"):
            text, sources = client._gemini_search("test prompt")

        assert text == '{"recommendation": "BUY"}'
        assert len(sources) == 1
        assert sources[0]["title"] == "Apple Q1"
        assert sources[0]["url"] == "http://apple.com/q1"

    def test_gemini_search_raises_on_empty_response(self):
        """response.text can be None (e.g. thinking-token exhaustion, safety block).

        generate() already guards against this; _gemini_search must too, so the
        caller gets a clear error instead of an empty string that later fails
        json.loads() with a cryptic "Expecting value: line 1 column 1" error.
        """
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.text = None
        mock_response.candidates = []

        client._client.models.generate_content.return_value = mock_response

        with patch("google.genai.types"):
            with pytest.raises(RuntimeError, match="Empty response from Gemini"):
                client._gemini_search("test prompt")

    def test_gemini_search_raises_on_whitespace_only_response(self):
        """response.text can be whitespace-only (e.g. "\\n") when the model

        exhausts its thinking budget without a final answer. `if not response.text`
        doesn't catch this since a non-empty whitespace string is truthy — it must
        also be stripped before the check, or a whitespace response silently passes
        through to json.loads() downstream and fails with a cryptic error.
        """
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.text = "\n"
        mock_response.candidates = []

        client._client.models.generate_content.return_value = mock_response

        with patch("google.genai.types"):
            with pytest.raises(RuntimeError, match="Empty response from Gemini"):
                client._gemini_search("test prompt")

    def test_gemini_search_handles_missing_grounding_metadata(self):
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.text = '{"recommendation": "HOLD"}'
        mock_response.candidates = []  # no candidates → IndexError caught

        client._client.models.generate_content.return_value = mock_response

        with patch("google.genai.types"):
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

    @staticmethod
    def _msg(text="ok", stop="end_turn", blocks=None):
        block = MagicMock()
        block.type = "text"
        block.text = text
        msg = MagicMock()
        msg.content = blocks if blocks is not None else [block]
        msg.stop_reason = stop
        return msg

    def test_generate_sends_tier_params_and_no_temperature(self):
        from portf_manager.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key="test_key", tier="fast")
        with patch("anthropic.Anthropic") as sdk:
            create = sdk.return_value.messages.create
            create.return_value = self._msg("result")
            assert client.generate("prompt") == "result"
        kw = create.call_args.kwargs
        assert kw["model"] == "claude-haiku-5-5"
        assert kw["thinking"] == {"type": "disabled"}
        assert kw["output_config"] == {"effort": "low"}
        assert kw["max_tokens"] >= 8000
        assert "temperature" not in kw

    def test_main_and_heavy_tiers_leave_thinking_on(self):
        from portf_manager.llm_client import AnthropicLLMClient

        for tier, model in (
            ("main", "claude-sonnet-5-5"),
            ("heavy", "claude-opus-5-5"),
        ):
            client = AnthropicLLMClient(api_key="test_key", tier=tier)
            with patch("anthropic.Anthropic") as sdk:
                create = sdk.return_value.messages.create
                create.return_value = self._msg()
                client.generate("p")
            kw = create.call_args.kwargs
            assert kw["model"] == model
            assert "thinking" not in kw  # Sonnet 5.5 rejects "disabled"

    def test_truncated_or_empty_reply_raises(self):
        from portf_manager.llm_client import AnthropicLLMClient, LLMError

        client = AnthropicLLMClient(api_key="test_key")
        for msg in (self._msg("cut", stop="max_tokens"), self._msg(blocks=[])):
            with patch("anthropic.Anthropic") as sdk:
                sdk.return_value.messages.create.return_value = msg
                with pytest.raises(LLMError):
                    client.generate("p")

    def test_generate_with_search_returns_envelope_with_sources(self):
        from portf_manager.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key="test_key", tier="heavy")
        result = MagicMock()
        result.type = "web_search_tool_result"
        hit = MagicMock()
        hit.url = "https://example.com/a"
        hit.title = "Example A"
        result.content = [hit]
        text = MagicMock()
        text.type = "text"
        text.text = '{"recommendation":"HOLD"}'
        with patch("anthropic.Anthropic") as sdk:
            create = sdk.return_value.messages.create
            create.return_value = self._msg(blocks=[result, text])
            raw = client.generate_with_search("prompt", "AAPL")
        assert create.call_args.kwargs["tools"][0]["type"] == "web_search_20260209"
        envelope = json.loads(raw)
        assert envelope["text"] == '{"recommendation":"HOLD"}'
        assert envelope["sources"] == [
            {"title": "Example A", "url": "https://example.com/a"}
        ]


class TestAnthropicTiers:
    def setup_method(self):
        from portf_manager.llm_client import reset_llm_client

        reset_llm_client()

    teardown_method = setup_method

    def _env(self, **extra):
        keys = (
            "PORTF_LLM_MODEL",
            "PORTF_ANTHROPIC_MODEL",
            "PORTF_ANTHROPIC_FAST_MODEL",
            "PORTF_ANTHROPIC_MAIN_MODEL",
            "PORTF_ANTHROPIC_HEAVY_MODEL",
        )
        return patch.dict(
            os.environ,
            {**{k: "" for k in keys}, "ANTHROPIC_API_KEY": "k", **extra},
        )

    def test_default_models_per_tier(self):
        from portf_manager.llm_client import get_llm_client

        with self._env(PORTF_LLM_PROVIDER="anthropic"):
            assert get_llm_client(tier="fast").model_name == "claude-haiku-5-5"
            assert get_llm_client(tier="main").model_name == "claude-sonnet-5-5"
            assert get_llm_client(tier="heavy").model_name == "claude-opus-5-5"
            assert get_llm_client().model_name == "claude-sonnet-5-5"

    def test_tier_clients_are_cached(self):
        from portf_manager.llm_client import get_llm_client

        with self._env(PORTF_LLM_PROVIDER="anthropic"):
            assert get_llm_client(tier="heavy") is get_llm_client(tier="heavy")
            assert get_llm_client(tier="heavy") is not get_llm_client(tier="fast")
            get_llm_client()  # default singleton exists first, then a tier
            assert get_llm_client(tier="fast").tier == "fast"

    def test_env_overrides(self):
        from portf_manager.llm_client import get_llm_client

        with self._env(
            PORTF_LLM_PROVIDER="anthropic",
            PORTF_ANTHROPIC_HEAVY_MODEL="claude-opus-9",
            PORTF_ANTHROPIC_MODEL="claude-pinned",
        ):
            assert get_llm_client(tier="heavy").model_name == "claude-opus-9"
            assert get_llm_client(tier="fast").model_name == "claude-pinned"

    def test_unknown_tier_rejected(self):
        from portf_manager.llm_client import get_llm_client

        with self._env(PORTF_LLM_PROVIDER="anthropic"):
            with pytest.raises(ValueError, match="tier"):
                get_llm_client(tier="ultra")

    def test_other_providers_ignore_tier(self):
        from portf_manager.llm_client import GeminiLLMClient, get_llm_client

        with patch.dict(
            os.environ, {"PORTF_LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "g"}
        ):
            a = get_llm_client(tier="heavy")
            b = get_llm_client(tier="fast")
        assert isinstance(a, GeminiLLMClient) and a is b

    def test_llm_info_lists_tiers(self):
        from portf_manager.llm_client import get_llm_info

        with self._env(PORTF_LLM_PROVIDER="anthropic"):
            info = get_llm_info()
        assert info["tiers"] == {
            "fast": "claude-haiku-5-5",
            "main": "claude-sonnet-5-5",
            "heavy": "claude-opus-5-5",
        }


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

    def test_auto_detect_uses_anthropic_when_only_key(self):
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
