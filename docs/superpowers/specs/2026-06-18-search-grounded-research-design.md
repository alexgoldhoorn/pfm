# Design: Search-Grounded Stock Research

_Date: 2026-06-18_

## Problem

The current `/research/{symbol}/generate` endpoint makes a single LLM call with
yfinance fundamentals and 6 yfinance news headlines as context. The model cannot
read beyond those headlines, so its analysis is shallow and may miss recent
earnings, analyst upgrades, or material news.

## Goal

Replace the yfinance-headline context with live web search (Gemini `google_search`
tool or Claude `web_search` tool), while keeping the same API response shape so
the frontend needs no changes.

## Scope

- Same endpoint: `POST /api/v1/research/{symbol}/generate`
- Same response shape: `recommendation`, `fair_value`, `confidence`, `summary`,
  `risks`, `catalysts`, `buy_below`, `sell_above`, `sources`
- No frontend changes required
- Fallback to current yfinance-headlines flow when no search-capable provider is
  configured

## Architecture

Two files change; `pyproject.toml` gains two optional dependencies.

### `portf_manager/llm_client.py`

Add `SearchCapableLLMClient` — a second `@runtime_checkable Protocol` that extends
`LLMClient` with one extra method:

```python
class SearchCapableLLMClient(LLMClient, Protocol):
    def generate_with_search(self, prompt: str, symbol: str) -> str: ...
```

**`GeminiLLMClient.generate_with_search()`** — uses the new `google-genai` SDK
(`from google import genai`) with `types.Tool(google_search=types.GoogleSearch())`
enabled. The existing `generate()` keeps the old `google.generativeai` SDK
unchanged. The new SDK is lazy-imported only in `generate_with_search()`.
Grounding metadata (search queries + cited URLs) is extracted from
`response.candidates[0].grounding_metadata` and returned as a JSON envelope:

```
{"text": "<raw LLM text>", "sources": [{"title": ..., "url": ...}, ...]}
```

`research.py` unpacks the envelope and populates `sources` from it.

**New `AnthropicLLMClient`** — regular `generate()` (plain messages, no tools) +
`generate_with_search()` (uses `web_search_20250305` built-in tool, up to 5 uses).
Final text is extracted by collecting all `text` content blocks after the last
`tool_result`. Default model: `claude-sonnet-4-6`.

Auto-detection order in `_auto_detect_provider()` gains a fourth slot:
1. Ollama (local, no key)
2. Gemini (`GEMINI_API_KEY`)
3. OpenRouter (`OPENROUTER_API_KEY`)
4. **Anthropic (`ANTHROPIC_API_KEY`)** ← new

`get_llm_client()` handles `provider="anthropic"` as a new explicit choice.

### `portf_manager/services/research.py`

In `generate_valuation_report()`:

1. Check `isinstance(llm, SearchCapableLLMClient)`
2. **Search path**: build a search-aware prompt that includes yfinance fundamentals
   but tells the model it has web search and should look up current news, earnings
   results, and analyst targets. Skip pre-fetching yfinance news (model fetches its
   own). Call `llm.generate_with_search(prompt, symbol)`. Parse the JSON from the
   response text. Populate `sources` from grounding metadata (Gemini) or empty list
   (Claude — sources are implicit in the model's tool calls).
3. **Fallback path**: unchanged — yfinance headlines + `llm.generate(prompt)`.

### `pyproject.toml`

```toml
# optional search providers — install only what you need
"google-genai>=1.0"          # Gemini search grounding (new SDK)
"anthropic>=0.40"            # Claude web_search tool
```

Both are soft dependencies: an `ImportError` in the lazy import falls back
gracefully to the non-search path.

## Data Flow

```
POST /research/{symbol}/generate
  │
  ├─ fetch yfinance fundamentals (cached 6h)
  │
  ├─ llm = get_llm_client()
  │
  ├─ isinstance(llm, SearchCapableLLMClient)?
  │    YES → search-aware prompt (no pre-fetched news)
  │          llm.generate_with_search(prompt, symbol)
  │          → Gemini: google_search tool (grounded)
  │          → Claude:  web_search tool (up to 5 calls)
  │          parse JSON + extract grounding sources
  │
  │    NO  → current flow: yfinance news + llm.generate(prompt)
  │
  └─ return {recommendation, fair_value, confidence, summary,
             risks, catalysts, buy_below, sell_above, sources}
```

## Error Handling

- `ImportError` on `google-genai` / `anthropic` → log warning, fall back to
  `generate()` (no search)
- LLM rate-limit on search path → existing OpenRouter fallback in
  `generate_valuation_report()` still applies (but via non-search `generate()`)
- JSON parse failure → same error return as today (`HOLD`, `low` confidence,
  error message in `summary`)

## What Doesn't Change

- `GET /research/{symbol}/lookup` — unchanged
- `POST /research/{symbol}/save` — unchanged
- Frontend — no changes
- Ollama / OpenRouter clients — no changes
- yfinance fundamentals fetch — still runs in all paths (structured data the
  model can use even when it has search)
- Rate-limit OpenRouter fallback in `research.py` — still applies

## Testing

- Unit tests mock `generate_with_search()` and verify: JSON parsed correctly,
  sources populated, fallback triggered when client lacks the method
- Existing `generate_report` tests continue to pass (they use mocked `generate()`)
- New `TestGeminiSearchClient` and `TestAnthropicClient` classes in
  `tests/unit/test_llm_client.py` covering: search response parsing, envelope
  unpacking, graceful ImportError fallback
