"""Tests for SearchCapableLLMClient protocol and new LLM clients."""

from unittest.mock import MagicMock


def test_search_capable_protocol_importable():
    from portf_manager.llm_client import SearchCapableLLMClient

    assert SearchCapableLLMClient is not None


def test_existing_clients_not_search_capable_before_implementation():
    """OllamaLLMClient and OpenRouterLLMClient never implement generate_with_search."""
    from portf_manager.llm_client import (
        SearchCapableLLMClient,
    )

    ollama = MagicMock(spec=["generate"])
    openrouter = MagicMock(spec=["generate"])
    assert not isinstance(ollama, SearchCapableLLMClient)
    assert not isinstance(openrouter, SearchCapableLLMClient)
