"""Tests for SearchCapableLLMClient protocol and new LLM clients."""

import json
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
