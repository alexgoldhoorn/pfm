"""Tests for SearchCapableLLMClient protocol and new LLM clients."""

from unittest.mock import MagicMock


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
