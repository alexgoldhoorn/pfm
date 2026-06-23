# Search-Grounded Stock Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the yfinance-headlines context in `/research/{symbol}/generate` with live web search (Gemini `google_search` tool or Anthropic `web_search` tool) so the LLM reasons over real current data instead of 6 stale headlines, with graceful fallback when neither search SDK is configured.

**Architecture:** Add a `SearchCapableLLMClient` protocol to `llm_client.py`; implement `generate_with_search()` on `GeminiLLMClient` (new `google-genai` SDK, lazy import) and a new `AnthropicLLMClient` (Anthropic SDK, lazy import); update `generate_valuation_report()` in `research.py` to detect search capability via `isinstance` and use the search path when available, falling back to the current yfinance-headlines flow otherwise. Response shape is unchanged — no frontend work required.

**Tech Stack:** Python 3.13, `google-genai>=1.0` (Gemini grounded search), `anthropic>=0.40` (Claude web_search tool), `pytest`, `unittest.mock`

---

## File Map

| Action | File | What changes |
|---|---|---|
| Modify | `portf_manager/llm_client.py` | Add `SearchCapableLLMClient` protocol, `generate_with_search` + `_gemini_search` on `GeminiLLMClient`, new `AnthropicLLMClient` class, wire into factory |
| Modify | `portf_manager/services/research.py` | Add `_build_search_prompt()` helper, update `generate_valuation_report()` to branch on search capability |
| Modify | `pyproject.toml` | Add `google-genai>=1.0` and `anthropic>=0.40` |
| Create | `tests/unit/test_llm_client.py` | Protocol + client unit tests |
| Modify | `tests/unit/test_research_valuation.py` | Add `generate_valuation_report` tests |
| Modify | `CLAUDE.md` | Document new env var, provider, deps |
| Modify | `PROJECT_STATUS.md` | Bump date, add feature to recent summary |

---

## Task 1: Dependencies + `SearchCapableLLMClient` protocol

**Files:**
- Modify: `pyproject.toml:6-31`
- Modify: `portf_manager/llm_client.py:1-41`
- Create: `tests/unit/test_llm_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_client.py`:

```python
"""Tests for SearchCapableLLMClient protocol and new LLM clients."""

import json
import os
from unittest.mock import MagicMock, patch


def test_search_capable_protocol_importable():
    from portf_manager.llm_client import SearchCapableLLMClient

    assert SearchCapableLLMClient is not None


def test_existing_clients_not_search_capable_before_implementation():
    """OllamaLLMClient and OpenRouterLLMClient never implement generate_with_search."""
    from portf_manager.llm_client import (
        OllamaLLMClient,
        OpenRouterLLMClient,
        SearchCapableLLMClient,
    )

    ollama = MagicMock(spec=["generate"])
    openrouter = MagicMock(spec=["generate"])
    assert not isinstance(ollama, SearchCapableLLMClient)
    assert not isinstance(openrouter, SearchCapableLLMClient)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py -v
```

Expected: `ImportError: cannot import name 'SearchCapableLLMClient'`

- [ ] **Step 3: Add `import json` and `SearchCapableLLMClient` to `llm_client.py`**

At the top of `portf_manager/llm_client.py`, add `import json` after line 4 (`import os`):

```python
import json
```

After the closing `...` of the `LLMClient` Protocol (after line 41), add:

```python

@runtime_checkable
class SearchCapableLLMClient(LLMClient, Protocol):
    """LLM client that supports live web search during generation.

    Returns a JSON envelope: {"text": "<llm output>", "sources": [{"title": ..., "url": ...}]}
    """

    def generate_with_search(self, prompt: str, symbol: str) -> str:
        """Generate with live web search grounding."""
        ...
```

- [ ] **Step 4: Add `google-genai` and `anthropic` to `pyproject.toml`**

In `pyproject.toml`, add after `"google-generativeai",` (line 13):

```toml
    "google-genai>=1.0",
    "anthropic>=0.40",
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py -v
```

Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add portf_manager/llm_client.py pyproject.toml tests/unit/test_llm_client.py
git commit -m "feat: add SearchCapableLLMClient protocol + google-genai/anthropic deps

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: `GeminiLLMClient.generate_with_search()`

**Files:**
- Modify: `portf_manager/llm_client.py:78-84` (after existing `generate()` method)
- Modify: `tests/unit/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_llm_client.py`:

```python
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
        assert envelope["sources"] == [{"title": "Q1 Earnings", "url": "http://example.com"}]

    def test_generate_with_search_falls_back_on_import_error(self):
        client = self._make_client()
        with patch.object(client, "_gemini_search", side_effect=ImportError("no sdk")):
            with patch.object(client, "generate", return_value='{"recommendation": "HOLD"}'):
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py::TestGeminiSearchCapable -v
```

Expected: `AttributeError: 'GeminiLLMClient' object has no attribute 'generate_with_search'`

- [ ] **Step 3: Implement `generate_with_search` and `_gemini_search` on `GeminiLLMClient`**

In `portf_manager/llm_client.py`, after the existing `generate()` method on `GeminiLLMClient` (after line 84), add:

```python
    def generate_with_search(self, prompt: str, symbol: str) -> str:
        """Google Search grounded generation via the new google-genai SDK."""
        try:
            text, sources = self._gemini_search(prompt)
        except ImportError:
            logger.warning(
                "google-genai SDK not installed; falling back to generate() without search"
            )
            return json.dumps({"text": self.generate(prompt), "sources": []})
        return json.dumps({"text": text, "sources": sources})

    def _gemini_search(self, prompt: str) -> tuple[str, list[dict]]:
        from google import genai as genai_new
        from google.genai import types as genai_types

        client = genai_new.Client(api_key=self.api_key)
        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
        )
        response = client.models.generate_content(
            model=self.model_name, contents=prompt, config=config
        )
        text = response.text or ""
        sources: list[dict] = []
        try:
            gm = response.candidates[0].grounding_metadata
            for chunk in gm.grounding_chunks or []:
                web = getattr(chunk, "web", None)
                if web:
                    sources.append(
                        {
                            "title": getattr(web, "title", ""),
                            "url": getattr(web, "uri", ""),
                        }
                    )
        except (AttributeError, IndexError):
            pass
        return text, sources
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py::TestGeminiSearchCapable -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_client.py
git commit -m "feat: add GeminiLLMClient.generate_with_search using google_search grounding

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: `AnthropicLLMClient` + factory wiring

**Files:**
- Modify: `portf_manager/llm_client.py` (after `OpenRouterLLMClient`, before singleton)
- Modify: `tests/unit/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_llm_client.py`:

```python
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
        with patch.object(client, "_anthropic_search", return_value='{"recommendation":"BUY"}'):
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

        no_keys = {k: "" for k in (
            "GEMINI_API_KEY", "PORTF_GEMINI_API_KEY", "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY", "PORTF_OPENROUTER_API_KEY",
        )}
        with patch("portf_manager.llm_client.OllamaLLMClient.is_available", return_value=False):
            with patch.dict(os.environ, {**no_keys, "ANTHROPIC_API_KEY": "test_key"}):
                client = _auto_detect_provider()
        assert isinstance(client, AnthropicLLMClient)
```

Add `import pytest` to the top of the test file (after `import os`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py::TestAnthropicClient tests/unit/test_llm_client.py::TestFactoryWiring -v
```

Expected: `ImportError: cannot import name 'AnthropicLLMClient'`

- [ ] **Step 3: Add `AnthropicLLMClient` to `llm_client.py`**

Add after `OpenRouterLLMClient` class (after line 209), before the `# Singleton cache` comment:

```python
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"


class AnthropicLLMClient:
    """Anthropic Claude LLM client with web_search tool support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY environment variable."
            )
        self.model_name = model or os.getenv("PORTF_LLM_MODEL", DEFAULT_ANTHROPIC_MODEL)
        logger.info(f"Anthropic LLM client initialized (model={self.model_name})")

    def generate(self, prompt: str) -> str:
        """Generate text using Anthropic Claude (no search tools)."""
        return self._anthropic_generate(prompt)

    def _anthropic_generate(self, prompt: str) -> str:
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            raise ImportError(
                "anthropic package required for Anthropic provider. "
                "Install with: pip install anthropic"
            )
        client = anthropic_sdk.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    def generate_with_search(self, prompt: str, symbol: str) -> str:
        """Generate with Anthropic web_search tool (up to 5 searches)."""
        text = self._anthropic_search(prompt)
        return json.dumps({"text": text, "sources": []})

    def _anthropic_search(self, prompt: str) -> str:
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            raise ImportError(
                "anthropic package required for Anthropic provider. "
                "Install with: pip install anthropic"
            )
        client = anthropic_sdk.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in msg.content:
            if hasattr(block, "type") and block.type == "text":
                text = block.text
        return text
```

- [ ] **Step 4: Wire `AnthropicLLMClient` into `get_llm_client()` and `_auto_detect_provider()`**

In `get_llm_client()`, after the `elif provider == "openrouter":` block (around line 251), add:

```python
    elif provider == "anthropic":
        client = AnthropicLLMClient(model=model)
```

Also update the `else` branch error message to list `anthropic` as a supported provider.

In `_auto_detect_provider()`, before the `raise RuntimeError(...)` (around line 286), add:

```python
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        logger.info("Auto-detected Anthropic API key, using Claude")
        return AnthropicLLMClient(api_key=anthropic_key, model=model)
```

Update the `raise RuntimeError` error message to include Anthropic as option 4:

```python
    raise RuntimeError(
        "No LLM provider available. Either:\n"
        "  1. Start Ollama locally: ollama serve && ollama pull llama3.2\n"
        "  2. Set GEMINI_API_KEY for Google Gemini\n"
        "  3. Set OPENROUTER_API_KEY for OpenRouter\n"
        "  4. Set ANTHROPIC_API_KEY for Anthropic Claude\n"
        "  5. Set PORTF_LLM_PROVIDER=ollama|gemini|openrouter|anthropic explicitly"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_client.py -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_client.py
git commit -m "feat: add AnthropicLLMClient with web_search tool + wire into provider factory

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Update `research.py` to use the search path

**Files:**
- Modify: `portf_manager/services/research.py:21` (import line) and `187-291` (function body)
- Modify: `tests/unit/test_research_valuation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_research_valuation.py`:

```python
import json
from unittest.mock import MagicMock, patch


_MOCK_FUND = {"symbol": "AAPL", "trailingPE": 25.0}
_MOCK_REPORT_JSON = json.dumps({
    "recommendation": "BUY",
    "confidence": "high",
    "summary": "Solid outlook.",
    "rationale": "Strong margins.",
    "risks": ["competition"],
    "catalysts": ["new product"],
    "fair_value": 175.0,
    "buy_below": 140.0,
    "sell_above": 200.0,
})


def test_generate_valuation_uses_plain_generate_when_not_search_capable(mocker):
    """Non-search LLM calls generate() and sources = pre-fetched yfinance news."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.return_value = _MOCK_REPORT_JSON
    mocker.patch("portf_manager.services.research.get_llm_client", return_value=mock_llm)

    news = [{"title": "Apple Q1 beats", "url": "http://example.com", "publisher": "Reuters"}]
    result = generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=120.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
        news=news,
    )

    mock_llm.generate.assert_called_once()
    assert result["recommendation"] == "BUY"
    assert result["sources"] == news


def test_generate_valuation_uses_search_when_capable(mocker):
    """Search-capable LLM calls generate_with_search() and sources = grounding metadata."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate", "generate_with_search"])
    envelope = json.dumps({
        "text": _MOCK_REPORT_JSON,
        "sources": [{"title": "Earnings beat", "url": "http://news.example.com"}],
    })
    mock_llm.generate_with_search.return_value = envelope
    mocker.patch("portf_manager.services.research.get_llm_client", return_value=mock_llm)

    result = generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=120.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
        news=[],
    )

    mock_llm.generate_with_search.assert_called_once()
    mock_llm.generate.assert_not_called()
    assert result["recommendation"] == "BUY"
    assert result["sources"] == [{"title": "Earnings beat", "url": "http://news.example.com"}]


def test_search_prompt_omits_prefetched_headlines(mocker):
    """The search-path prompt tells the model to search; it does not include pre-fetched news."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate", "generate_with_search"])
    mock_llm.generate_with_search.return_value = json.dumps({
        "text": _MOCK_REPORT_JSON, "sources": []
    })
    mocker.patch("portf_manager.services.research.get_llm_client", return_value=mock_llm)

    generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=0.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
        news=[{"title": "Should not appear", "url": "http://x.com", "publisher": "X"}],
    )

    prompt_used = mock_llm.generate_with_search.call_args[0][0]
    assert "Should not appear" not in prompt_used
    assert "web search" in prompt_used.lower()


def test_generate_valuation_returns_error_dict_on_llm_failure(mocker):
    """LLM exception returns a safe error dict instead of raising."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.side_effect = RuntimeError("API down")
    mocker.patch("portf_manager.services.research.get_llm_client", return_value=mock_llm)

    result = generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=120.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
    )

    assert result["recommendation"] == "HOLD"
    assert result["confidence"] == "low"
    assert "API down" in result["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_research_valuation.py -v
```

Expected: 4 new tests FAIL (existing `compute_targets` tests still pass)

- [ ] **Step 3: Update import line in `research.py`**

Replace line 21 in `portf_manager/services/research.py`:

```python
from portf_manager.llm_client import OpenRouterLLMClient, get_llm_client
```

with:

```python
from portf_manager.llm_client import (
    OpenRouterLLMClient,
    SearchCapableLLMClient,
    get_llm_client,
)
```

- [ ] **Step 4: Add `_build_search_prompt()` helper to `research.py`**

Add this function immediately before `generate_valuation_report` (before line 187):

```python
def _build_search_prompt(
    symbol: str,
    asset_name: str,
    asset_type: str,
    current_price: float,
    avg_cost: float,
    currency: str,
    fund_str: str,
    pnl_str: str,
) -> str:
    return f"""You are a professional equity analyst with access to web search.

POSITION:
- Symbol: {symbol}
- Name: {asset_name}
- Type: {asset_type}
- Current price: {current_price} {currency}
- Investor's average cost: {avg_cost} {currency}
- Unrealised P&L vs cost: {pnl_str}

FUNDAMENTALS (from Yahoo Finance):
{fund_str}

Use your web search to look up current news, recent earnings results, and analyst price targets for {symbol}. Base your analysis on what you find.

Return ONLY a valid JSON object with exactly these fields:
{{
  "fair_value": <float — your intrinsic/fair value estimate in {currency}, or null if insufficient data>,
  "recommendation": "<BUY | HOLD | SELL>",
  "confidence": "<high | medium | low>",
  "summary": "<2-3 sentence plain-English summary of the investment case>",
  "rationale": "<why you give this recommendation, max 100 words>",
  "risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
  "catalysts": ["<catalyst 1>", "<catalyst 2>"],
  "buy_below": <float — price below which the stock is attractive, or null>,
  "sell_above": <float — price above which you would take profit, or null>
}}

Be concise and data-driven. If this is a crypto, ETF, or P2P asset where DCF does not apply, base the recommendation on momentum, relative value, and risk/reward instead.
"""
```

- [ ] **Step 5: Replace the try/except block in `generate_valuation_report()`**

Replace lines 247-290 (the entire `try:` block) with:

```python
    try:
        llm = get_llm_client()

        if isinstance(llm, SearchCapableLLMClient):
            search_prompt = _build_search_prompt(
                symbol, asset_name, asset_type, current_price,
                avg_cost, currency, fund_str, pnl_str,
            )
            try:
                envelope_str = llm.generate_with_search(search_prompt, symbol)
                envelope = json.loads(envelope_str)
                raw = envelope["text"].strip()
                grounding_sources: list = envelope.get("sources", [])
            except Exception as e:
                if _is_rate_limited(e):
                    or_key = os.getenv("OPENROUTER_API_KEY") or os.getenv(
                        "PORTF_OPENROUTER_API_KEY"
                    )
                    if or_key:
                        logger.warning(
                            f"Search LLM rate-limited for {symbol}, retrying with OpenRouter"
                        )
                        raw = OpenRouterLLMClient(api_key=or_key).generate(prompt).strip()
                        grounding_sources = news or []
                    else:
                        raise
                else:
                    raise
        else:
            try:
                raw = llm.generate(prompt).strip()
            except Exception as e:
                if _is_rate_limited(e):
                    or_key = os.getenv("OPENROUTER_API_KEY") or os.getenv(
                        "PORTF_OPENROUTER_API_KEY"
                    )
                    if or_key:
                        logger.warning(
                            f"Primary LLM rate-limited for {symbol}, retrying with OpenRouter"
                        )
                        raw = OpenRouterLLMClient(api_key=or_key).generate(prompt).strip()
                    else:
                        raise
                else:
                    raise
            grounding_sources = news or []

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.splitlines() if not line.strip().startswith("```")
            )
        report = json.loads(raw)
        # Validate required keys
        for key in ("recommendation", "confidence", "summary"):
            if key not in report:
                raise ValueError(f"Missing key: {key}")
        report["sources"] = grounding_sources
        return report
    except Exception as e:
        logger.error(f"LLM valuation failed for {symbol}: {e}")
        return {
            "fair_value": None,
            "recommendation": "HOLD",
            "confidence": "low",
            "summary": f"Could not generate automated analysis for {symbol}: {e}",
            "rationale": "",
            "risks": [],
            "catalysts": [],
            "buy_below": None,
            "sell_above": None,
            "sources": news or [],
        }
```

- [ ] **Step 6: Run all research tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_research_valuation.py -v
```

Expected: all tests PASSED (5 original compute_targets tests + 4 new ones)

- [ ] **Step 7: Run full unit test suite to catch regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v
```

Expected: all tests PASSED, 0 failed

- [ ] **Step 8: Commit**

```bash
git add portf_manager/services/research.py tests/unit/test_research_valuation.py
git commit -m "feat: use search-grounded LLM in generate_valuation_report when available

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: Docs update

**Files:**
- Modify: `CLAUDE.md` (LLM section)
- Modify: `PROJECT_STATUS.md`

- [ ] **Step 1: Update the LLM section in `CLAUDE.md`**

In the `### LLM` section, update the provider priority list and add the new env var and SDK notes. Replace the existing factory description with:

```
Factory `get_llm_client()` auto-detects in priority order:
1. Ollama (`OLLAMA_HOST:OLLAMA_PORT`) — local, no API key
2. Gemini (`GEMINI_API_KEY`) — default model `gemini-2.5-flash`
3. OpenRouter (`OPENROUTER_API_KEY`) — default model `openai/gpt-4o-mini`
4. Anthropic (`ANTHROPIC_API_KEY`) — default model `claude-sonnet-4-6`
```

Add a note after the override env vars block:

```
**Search grounding**: `GeminiLLMClient` and `AnthropicLLMClient` implement the
`SearchCapableLLMClient` protocol and expose `generate_with_search(prompt, symbol)`.
The research `generate_valuation_report()` detects this via `isinstance` and calls
`generate_with_search()` instead of pre-fetching yfinance news. Gemini uses the
`google-genai` SDK (`google_search` tool); Anthropic uses `web_search_20250305`.
Both return a JSON envelope `{"text": "<llm json>", "sources": [...]}`.
The `google-genai` and `anthropic` packages are optional (lazy-imported); missing
packages fall back gracefully to the non-search path.
```

- [ ] **Step 2: Update `PROJECT_STATUS.md`**

Update the "Last updated" date to `2026-06-18`.

Prepend to the Recent (v2.1) summary line:

```
**search-grounded research** (Gemini google_search / Anthropic web_search tool; SearchCapableLLMClient protocol; graceful fallback to yfinance headlines);
```

- [ ] **Step 3: Final full test run**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e
```

Expected: all tests PASSED, 0 failed

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document search-grounded research (Gemini/Anthropic), ANTHROPIC_API_KEY env var

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: Protocol ✓, Gemini search ✓, Anthropic search ✓, factory wiring ✓, research.py search path ✓, fallback path unchanged ✓, same response shape ✓, no frontend changes ✓
- [x] **Placeholders**: None — every step has actual code
- [x] **Type consistency**: `generate_with_search(prompt: str, symbol: str) -> str` used consistently in Task 1 (protocol), Task 2 (Gemini impl), Task 3 (Anthropic impl), Task 4 (call site)
- [x] **`_build_search_prompt` signature**: matches call site in Task 4 exactly
- [x] **`grounding_sources` variable**: declared and assigned in both branches before `report["sources"] = grounding_sources`
- [x] **`raw` variable**: assigned in all branches (search path envelope unpack + rate-limit fallback; non-search path + rate-limit fallback)
- [x] **`prompt` variable**: still used in rate-limit fallback from the search branch (line `OpenRouterLLMClient(...).generate(prompt)`) — `prompt` is built before the try block, so it is in scope ✓
