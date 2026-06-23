# Chat Tool Calling — Agentic Loop with Portfolio Tools

**Date:** 2026-06-24
**Status:** Approved

## Problem

The current `EnhancedChatEngine` builds a single static context snapshot (positions,
transactions, prices) at request time, dumps it all into one big prompt, and calls
`llm.generate()` once. This causes three problems:

1. **Stale data** — prices and values are baked in at request time.
2. **Token waste** — the full positions list is included in every prompt regardless of
   what the user asked.
3. **No targeted lookups** — the LLM can't fetch specific data mid-reasoning (e.g.
   compare two tickers, look up a company not in the portfolio).

Tool/function calling solves all three: the LLM can request exactly the data it needs,
when it needs it, and get a live result.

## Scope

- Add a `ToolCapableLLMClient` protocol to `llm_client.py` (all 4 providers implement it).
- Add a `chat_tools.py` service with 15 tools and a single `execute_tool()` dispatcher.
- Update `EnhancedChatEngine` to run a max-2-LLM-call tool loop when the provider
  supports it; fall back to the existing static-context path otherwise.
- Keep the existing static context snapshot as a compact summary (totals + top 5
  holdings) — the LLM calls tools for detail.
- **Not** MCP: in-process tools are simpler, avoid a circular server → MCP → HTTP →
  server dependency, and work uniformly across all 4 providers.

## Data Model

Three dataclasses added to `portf_manager/llm_client.py`:

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: list[dict]  # JSON Schema style: {name, type, description, required}

@dataclass
class ToolCallRequest:
    name: str
    arguments: dict
    call_id: Optional[str] = None  # Anthropic needs tool_use_id for the follow-up

@dataclass
class ToolResponse:
    text: Optional[str] = None         # set when LLM answered directly
    tool_call: Optional[ToolCallRequest] = None  # set when LLM wants a tool
    # exactly one of text or tool_call is non-None
```

New protocol (alongside the existing `SearchCapableLLMClient`):

```python
@runtime_checkable
class ToolCapableLLMClient(LLMClient, Protocol):
    def generate_with_tools(
        self,
        messages: list[dict],        # [{"role": "user"|"assistant"|"system", "content": str}]
        tools: list[ToolDefinition],
    ) -> ToolResponse: ...

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: ToolCallRequest,
        tool_result: str,
    ) -> str: ...
```

`messages` uses plain `{role, content}` dicts — no provider-specific types leak out.

## Tool Catalog

Defined in `portf_server/services/chat_tools.py`. Each tool returns a compact JSON
string with only the fields the LLM needs.

| Tool | Required args | Optional filters | Returns |
|---|---|---|---|
| `get_holdings` | — | `portfolio_id`, `symbol`, `asset_type` | Open positions: qty, value_eur, gain_eur, gain_pct |
| `get_performance` | — | `portfolio_id`, `start_date`, `end_date` | IRR, CAGR, total return, inception date |
| `get_risk` | — | `portfolio_id` | Volatility, Sharpe, Sortino, max drawdown, beta |
| `get_diversification` | — | `portfolio_id` | Sector/country/currency/type breakdown + HHI |
| `get_kpis` | — | `portfolio_id` | Total value, invested, unrealised/realised gain, net deposits, yield |
| `get_health` | — | `portfolio_id` | Cached AI health scores + recommendations (reads `kv_cache`, no extra LLM call) |
| `get_brokers` | — | — | Portfolios list: name, website, total value, first/last activity |
| `get_quote` | `symbol` | — | Live price, prev_close, day_change_pct |
| `get_price` | `symbol` | `start_date`, `end_date` | Historical stored prices from DB |
| `get_research` | `symbol` | — | Latest research note: rating, fair value, summary |
| `get_transactions` | — | `portfolio_id`, `symbol`, `tx_type`, `start_date`, `end_date`, `limit` | Filtered transaction list |
| `get_tax_estimate` | — | `year` | Realised gains + dividends + interest for the year |
| `asset_details` | `symbol` | — | Name, asset_type, ISIN, ticker, currency, exchange |
| `asset_news` | `symbol` | — | Recent news items via yfinance `Ticker.news` |
| `financial_news` | `query` | — | Market/financial news for the query via yfinance or search grounding |

`execute_tool(name: str, args: dict, db: Database) -> str` is a single dispatcher
function. All tools call DB methods and existing service functions directly — no HTTP
round-trips. Tool execution exceptions are caught and returned as `"Error: <message>"`
so the LLM can communicate the failure gracefully.

## Per-Provider Implementation

### Anthropic

`generate_with_tools()` maps `ToolDefinition` → Anthropic `input_schema` format, calls
`client.messages.create(tools=[...])`, and checks for a `tool_use` content block.
Returns `ToolResponse(tool_call=ToolCallRequest(..., call_id=tool_use.id))`.

`complete_with_tool_result()` appends an `assistant` message with the `tool_use` block
and a `user` message with a `tool_result` content block (using `call_id`), then makes a
second `messages.create()` call.

### Gemini

`generate_with_tools()` maps `ToolDefinition` → `genai_types.FunctionDeclaration`,
passes `GenerateContentConfig(tools=[Tool(function_declarations=[...])])`. Checks
response candidates for `function_call` parts.

`complete_with_tool_result()` injects a `FunctionResponse` part into the conversation
and calls `generate_content()` again.

### OpenRouter

`generate_with_tools()` maps `ToolDefinition` → OpenAI-compatible
`{"type": "function", "function": {"name", "description", "parameters": JSON Schema}}`.
Posts to `/chat/completions` with a `tools` field. Checks
`choices[0].message.tool_calls`.

`complete_with_tool_result()` appends `{"role": "tool", "content": result}` to the
messages list and makes a second completions call.

### Ollama

Primary path: `/api/chat` with `tools` parameter (supported by llama3.1+, llama3.2).
Checks the response for a `tool_calls` field.

Fallback (if model doesn't support tools or returns plain text): JSON-in-prompt. Tool
schemas are described in the system message; the LLM is asked to reply with either
`{"tool_call": {"name": "...", "arguments": {...}}}` or plain prose. Response is parsed
with `json.loads` on the first JSON block found. Unparseable output is treated as a
plain answer (`ToolResponse(text=raw_output)`).

## Chat Engine Changes

`EnhancedChatEngine._generate_enhanced_response()` gains a new branch:

```python
async def _generate_enhanced_response(self, message, context, session_id, db):
    if isinstance(self.llm, ToolCapableLLMClient):
        return await self._generate_with_tool_loop(message, context, session_id, db)
    # existing generate(prompt) path unchanged
    ...
```

The tool loop (`_generate_with_tool_loop`):

1. Build `messages`: system message (role + instructions) + compact context summary
   (portfolio totals + top 5 holdings) + last 4 conversation history messages + user message.
2. `response = await asyncio.to_thread(self.llm.generate_with_tools, messages, TOOLS)`
3. If `response.text` → return it (LLM answered directly).
4. If `response.tool_call` → `result = execute_tool(name, args, db)`.
5. `final = await asyncio.to_thread(self.llm.complete_with_tool_result, messages, response.tool_call, result)`
6. Return `final`.

The static context snapshot is reduced to a compact summary for the tool path (totals +
top 5 holdings by value). The LLM calls `get_holdings` if it needs the full list.

Slow tools (diversification, risk, health) run inside `asyncio.to_thread` in step 4 —
they don't block the event loop and are bounded by the same yfinance budget the existing
analytics endpoints already tolerate.

## Error Handling

| Failure | Behaviour |
|---|---|
| Tool execution raises | `execute_tool()` catches and returns `"Error: <message>"` — LLM acknowledges gracefully |
| Ollama JSON parse fails | Raw text treated as `ToolResponse(text=raw_output)` |
| Provider not tool-capable | `isinstance` check fails → existing static-context `generate()` path |
| Unknown tool name | Returns `"Error: unknown tool '<name>'"` |
| Slow tools (diversification, risk) | Run in threadpool, no additional timeout beyond yfinance defaults |

## Testing

- **Unit:** `generate_with_tools()` + `complete_with_tool_result()` for each provider
  with mocked SDKs — verify tool call detected, `ToolResponse` fields correct, second
  pass returns final text string.
- **Unit:** `execute_tool()` for each of the 15 tools using an in-memory test DB.
- **Unit:** Ollama JSON-in-prompt fallback — verify unparseable output returns
  `ToolResponse(text=raw_output)`.
- **Integration:** Full `_generate_with_tool_loop` in `EnhancedChatEngine` — mock LLM
  returns `ToolResponse(tool_call=...)`, verify tool executed and second LLM call made.

## Files Changed

| File | Change |
|---|---|
| `portf_manager/llm_client.py` | Add `ToolDefinition`, `ToolCallRequest`, `ToolResponse` dataclasses; `ToolCapableLLMClient` protocol; implement on all 4 clients |
| `portf_server/services/chat_tools.py` | New file: 15 tool definitions + `execute_tool()` dispatcher |
| `portf_server/routers/llm.py` | Add `_generate_with_tool_loop()` to `EnhancedChatEngine`; update `_generate_enhanced_response()` to branch on `ToolCapableLLMClient` |
| `tests/unit/test_llm_tool_calling.py` | New file: provider unit tests |
| `tests/unit/test_chat_tools.py` | New file: tool executor unit tests |
