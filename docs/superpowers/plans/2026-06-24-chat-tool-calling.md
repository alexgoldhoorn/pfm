# Chat Tool Calling — Agentic Loop with Portfolio Tools

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider-native function calling to the chat engine so the LLM can query
live portfolio data mid-conversation instead of relying on a static context snapshot.

**Architecture:** `llm_client.py` gains a `ToolCapableLLMClient` protocol with
`generate_with_tools()` / `complete_with_tool_result()` implemented on all four
providers (Anthropic + Gemini natively, OpenRouter via OpenAI-compatible tools,
Ollama via `/api/chat` tools with JSON-in-prompt fallback). A new
`portf_server/chat_tools.py` defines 15 tools backed by existing DB methods and
a single `execute_tool()` dispatcher. `EnhancedChatEngine` branches on
`isinstance(self.llm, ToolCapableLLMClient)` to run a max-2-LLM-call tool loop
per user message; the old static-context `generate()` path is kept unchanged.

**Tech Stack:** Python 3.13, FastAPI, SQLite, `anthropic` SDK, `google-genai` SDK,
`requests`, pytest + `unittest.mock`.

## Global Constraints

- Python target: 3.13. Run all tooling with `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run …`
- Black line length 88. Pre-commit runs black + flake8 + autoflake on every commit.
- Conventional commits (`feat:`, `fix:`, `test:`). Co-author: `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Unit tests: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v`
- After any `portf_server/` or `portf_manager/` Python change: `docker exec portf_backend_dev kill -HUP 1`
- No HTTP round-trips inside tool handlers — call DB methods and internal services directly.
- `execute_tool()` must catch all exceptions and return `"Error: <message>"` — never raise.
- Tool `parameters` list uses dicts with keys: `name`, `type` (JSON Schema type string), `description`, `required` (bool).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `portf_manager/llm_client.py` | Modify | Add `ToolDefinition`, `ToolCallRequest`, `ToolResponse` dataclasses; `ToolCapableLLMClient` protocol; implement on all 4 providers |
| `portf_server/chat_tools.py` | **Create** | 15 tool `ToolDefinition` objects, 15 handler functions, `execute_tool()` dispatcher, `TOOLS` list |
| `portf_server/routers/llm.py` | Modify | Add `_generate_with_tool_loop()` to `EnhancedChatEngine`; branch in `_generate_enhanced_response()` |
| `tests/unit/test_llm_tool_calling.py` | **Create** | Protocol checks + per-provider mock tests |
| `tests/unit/test_chat_tools.py` | **Create** | `execute_tool()` tests using in-memory DB |

---

### Task 1: Data model + `ToolCapableLLMClient` protocol

**Files:**
- Modify: `portf_manager/llm_client.py` (after line 31 imports; after `SearchCapableLLMClient` class ~line 63)
- Create: `tests/unit/test_llm_tool_calling.py`

**Interfaces:**
- Produces:
  - `ToolDefinition(name: str, description: str, parameters: list[dict])`
  - `ToolCallRequest(name: str, arguments: dict, call_id: Optional[str] = None)`
  - `ToolResponse(text: Optional[str] = None, tool_call: Optional[ToolCallRequest] = None)`
  - `ToolCapableLLMClient` — `@runtime_checkable` Protocol with `generate_with_tools()` and `complete_with_tool_result()`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_llm_tool_calling.py`:

```python
"""Tests for ToolCapableLLMClient protocol and tool data model."""

from unittest.mock import MagicMock


def test_tool_definition_importable():
    from portf_manager.llm_client import ToolDefinition

    t = ToolDefinition(
        name="get_quote",
        description="Get live price for a symbol.",
        parameters=[{"name": "symbol", "type": "string", "description": "Ticker", "required": True}],
    )
    assert t.name == "get_quote"
    assert t.parameters[0]["name"] == "symbol"


def test_tool_call_request_has_optional_call_id():
    from portf_manager.llm_client import ToolCallRequest

    tc = ToolCallRequest(name="get_holdings", arguments={"symbol": "AAPL"})
    assert tc.call_id is None

    tc2 = ToolCallRequest(name="get_holdings", arguments={}, call_id="abc123")
    assert tc2.call_id == "abc123"


def test_tool_response_text_only():
    from portf_manager.llm_client import ToolResponse

    r = ToolResponse(text="Here is the answer.")
    assert r.text == "Here is the answer."
    assert r.tool_call is None


def test_tool_response_tool_call_only():
    from portf_manager.llm_client import ToolCallRequest, ToolResponse

    tc = ToolCallRequest(name="get_holdings", arguments={})
    r = ToolResponse(tool_call=tc)
    assert r.text is None
    assert r.tool_call.name == "get_holdings"


def test_mock_without_tool_methods_is_not_tool_capable():
    from portf_manager.llm_client import ToolCapableLLMClient

    plain = MagicMock(spec=["generate"])
    assert not isinstance(plain, ToolCapableLLMClient)


def test_mock_with_tool_methods_is_tool_capable():
    from portf_manager.llm_client import ToolCapableLLMClient

    capable = MagicMock(spec=["generate", "generate_with_tools", "complete_with_tool_result"])
    assert isinstance(capable, ToolCapableLLMClient)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py -v
```

Expected: `ImportError` or `AttributeError` — `ToolDefinition` not yet defined.

- [ ] **Step 3: Add dataclasses and protocol to `llm_client.py`**

After the existing imports (around line 31), add:

```python
from dataclasses import dataclass, field
```

After the `SearchCapableLLMClient` class (around line 63), add:

```python
@dataclass
class ToolDefinition:
    """Schema for a tool the LLM can call."""

    name: str
    description: str
    parameters: list[dict]


@dataclass
class ToolCallRequest:
    """A tool invocation returned by the LLM."""

    name: str
    arguments: dict
    call_id: Optional[str] = None


@dataclass
class ToolResponse:
    """Result of generate_with_tools() — either a final answer or a tool call.

    Exactly one of ``text`` or ``tool_call`` is non-None.
    """

    text: Optional[str] = None
    tool_call: Optional[ToolCallRequest] = None


@runtime_checkable
class ToolCapableLLMClient(LLMClient, Protocol):
    """LLM client that supports provider-native function/tool calling."""

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
    ) -> ToolResponse:
        """First pass: returns either a final answer or a tool call request."""
        ...

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: ToolCallRequest,
        tool_result: str,
    ) -> str:
        """Second pass: given tool result, return final answer string."""
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_tool_calling.py
git commit -m "feat: add ToolCapableLLMClient protocol and data model

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: Anthropic `generate_with_tools` + `complete_with_tool_result`

**Files:**
- Modify: `portf_manager/llm_client.py` — add two methods to `AnthropicLLMClient` (~line 290)
- Modify: `tests/unit/test_llm_tool_calling.py` — add `TestAnthropicToolCalling` class

**Interfaces:**
- Consumes: `ToolDefinition`, `ToolCallRequest`, `ToolResponse` (Task 1)
- Produces: `AnthropicLLMClient` satisfies `ToolCapableLLMClient`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_llm_tool_calling.py`:

```python
class TestAnthropicToolCalling:
    def _make_client(self):
        from portf_manager.llm_client import AnthropicLLMClient

        return AnthropicLLMClient(api_key="test_key")

    def test_anthropic_satisfies_tool_capable_protocol(self):
        from portf_manager.llm_client import ToolCapableLLMClient

        client = self._make_client()
        assert isinstance(client, ToolCapableLLMClient)

    def test_generate_with_tools_returns_tool_call(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition, ToolResponse

        client = self._make_client()
        tools = [
            ToolDefinition(
                name="get_holdings",
                description="Get open positions.",
                parameters=[],
            )
        ]
        messages = [{"role": "user", "content": "What are my holdings?"}]

        fake_block = MagicMock()
        fake_block.type = "tool_use"
        fake_block.name = "get_holdings"
        fake_block.input = {}
        fake_block.id = "toolu_123"

        fake_msg = MagicMock()
        fake_msg.content = [fake_block]

        with patch("anthropic.Anthropic") as mock_sdk:
            mock_sdk.return_value.messages.create.return_value = fake_msg
            response = client.generate_with_tools(messages, tools)

        assert response.tool_call is not None
        assert response.tool_call.name == "get_holdings"
        assert response.tool_call.call_id == "toolu_123"
        assert response.text is None

    def test_generate_with_tools_returns_text_when_no_tool(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_holdings", description=".", parameters=[])]
        messages = [{"role": "user", "content": "Hello"}]

        fake_block = MagicMock()
        fake_block.type = "text"
        fake_block.text = "Hi there!"

        fake_msg = MagicMock()
        fake_msg.content = [fake_block]

        with patch("anthropic.Anthropic") as mock_sdk:
            mock_sdk.return_value.messages.create.return_value = fake_msg
            response = client.generate_with_tools(messages, tools)

        assert response.text == "Hi there!"
        assert response.tool_call is None

    def test_complete_with_tool_result_returns_string(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolCallRequest

        client = self._make_client()
        messages = [{"role": "user", "content": "What are my holdings?"}]
        tool_call = ToolCallRequest(name="get_holdings", arguments={}, call_id="toolu_123")

        fake_block = MagicMock()
        fake_block.type = "text"
        fake_block.text = "You hold 10 AAPL."

        fake_msg = MagicMock()
        fake_msg.content = [fake_block]

        with patch("anthropic.Anthropic") as mock_sdk:
            mock_sdk.return_value.messages.create.return_value = fake_msg
            result = client.complete_with_tool_result(
                messages, tool_call, '{"holdings": [{"symbol": "AAPL", "quantity": 10}]}'
            )

        assert result == "You hold 10 AAPL."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestAnthropicToolCalling -v
```

Expected: `AttributeError: 'AnthropicLLMClient' object has no attribute 'generate_with_tools'`

- [ ] **Step 3: Add methods to `AnthropicLLMClient`**

In `portf_manager/llm_client.py`, add these two methods to `AnthropicLLMClient` after `_anthropic_search()`:

```python
    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: returns tool call or final answer using Anthropic tool use."""
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            raise ImportError("anthropic package required. Install with: pip install anthropic")

        client = anthropic_sdk.Anthropic(api_key=self.api_key)
        anthropic_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        p["name"]: {"type": p["type"], "description": p["description"]}
                        for p in t.parameters
                    },
                    "required": [p["name"] for p in t.parameters if p.get("required", False)],
                },
            }
            for t in tools
        ]
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        user_messages = [m for m in messages if m["role"] != "system"]

        kwargs: dict = {
            "model": self.model_name,
            "max_tokens": 4096,
            "tools": anthropic_tools,
            "messages": user_messages,
        }
        if system:
            kwargs["system"] = system

        msg = client.messages.create(**kwargs)

        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return ToolResponse(
                    tool_call=ToolCallRequest(
                        name=block.name,
                        arguments=block.input,
                        call_id=block.id,
                    )
                )

        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text
        return ToolResponse(text=text)

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: "ToolCallRequest",
        tool_result: str,
    ) -> str:
        """Second pass: send tool result back to Anthropic and return final answer."""
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            raise ImportError("anthropic package required.")

        client = anthropic_sdk.Anthropic(api_key=self.api_key)
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        user_messages = [m for m in messages if m["role"] != "system"]

        extended = user_messages + [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_call.call_id,
                        "name": tool_call.name,
                        "input": tool_call.arguments,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.call_id,
                        "content": tool_result,
                    }
                ],
            },
        ]
        kwargs: dict = {"model": self.model_name, "max_tokens": 4096, "messages": extended}
        if system:
            kwargs["system"] = system

        msg = client.messages.create(**kwargs)
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestAnthropicToolCalling -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_tool_calling.py
git commit -m "feat: add tool calling to AnthropicLLMClient

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: Gemini `generate_with_tools` + `complete_with_tool_result`

**Files:**
- Modify: `portf_manager/llm_client.py` — add two methods to `GeminiLLMClient` (~line 64)
- Modify: `tests/unit/test_llm_tool_calling.py` — add `TestGeminiToolCalling` class

**Interfaces:**
- Consumes: `ToolDefinition`, `ToolCallRequest`, `ToolResponse` (Task 1)
- Produces: `GeminiLLMClient` satisfies `ToolCapableLLMClient`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_llm_tool_calling.py`:

```python
class TestGeminiToolCalling:
    def _make_client(self):
        from unittest.mock import patch
        with patch("google.generativeai.GenerativeModel"):
            from portf_manager.llm_client import GeminiLLMClient
            return GeminiLLMClient(api_key="test_key")

    def test_gemini_satisfies_tool_capable_protocol(self):
        from portf_manager.llm_client import ToolCapableLLMClient

        client = self._make_client()
        assert isinstance(client, ToolCapableLLMClient)

    def test_generate_with_tools_returns_tool_call(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_kpis", description="Get KPIs.", parameters=[])]
        messages = [{"role": "user", "content": "How is my portfolio?"}]

        fake_fc = MagicMock()
        fake_fc.name = "get_kpis"
        fake_fc.args = {}

        fake_part = MagicMock()
        fake_part.function_call = fake_fc

        fake_candidate = MagicMock()
        fake_candidate.content.parts = [fake_part]

        fake_response = MagicMock()
        fake_response.candidates = [fake_candidate]
        fake_response.text = None

        with patch("google.genai.Client") as mock_client:
            mock_client.return_value.models.generate_content.return_value = fake_response
            with patch("google.genai.types"):
                response = client.generate_with_tools(messages, tools)

        assert response.tool_call is not None
        assert response.tool_call.name == "get_kpis"
        assert response.text is None

    def test_generate_with_tools_returns_text_when_no_tool(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_kpis", description=".", parameters=[])]
        messages = [{"role": "user", "content": "Hello"}]

        fake_part = MagicMock()
        fake_part.function_call = None

        fake_candidate = MagicMock()
        fake_candidate.content.parts = [fake_part]

        fake_response = MagicMock()
        fake_response.candidates = [fake_candidate]
        fake_response.text = "Hello back!"

        with patch("google.genai.Client") as mock_client:
            mock_client.return_value.models.generate_content.return_value = fake_response
            with patch("google.genai.types"):
                response = client.generate_with_tools(messages, tools)

        assert response.text == "Hello back!"
        assert response.tool_call is None

    def test_complete_with_tool_result_returns_string(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolCallRequest

        client = self._make_client()
        messages = [{"role": "user", "content": "KPIs?"}]
        tool_call = ToolCallRequest(name="get_kpis", arguments={})

        fake_response = MagicMock()
        fake_response.text = "Your portfolio is worth €10,000."

        with patch("google.genai.Client") as mock_client:
            mock_client.return_value.models.generate_content.return_value = fake_response
            with patch("google.genai.types"):
                result = client.complete_with_tool_result(messages, tool_call, '{"total_eur": 10000}')

        assert result == "Your portfolio is worth €10,000."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestGeminiToolCalling -v
```

Expected: `AttributeError` — `generate_with_tools` not yet defined on `GeminiLLMClient`.

- [ ] **Step 3: Add methods to `GeminiLLMClient`**

In `portf_manager/llm_client.py`, add these two methods to `GeminiLLMClient` after `_gemini_search()`:

```python
    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: returns tool call or final answer via Gemini function calling."""
        from google import genai as genai_new
        from google.genai import types as genai_types

        client = genai_new.Client(api_key=self.api_key)

        function_declarations = [
            genai_types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=genai_types.Schema(
                    type="OBJECT",
                    properties={
                        p["name"]: genai_types.Schema(
                            type=p["type"].upper(),
                            description=p["description"],
                        )
                        for p in t.parameters
                    },
                    required=[p["name"] for p in t.parameters if p.get("required", False)],
                ),
            )
            for t in tools
        ]

        system_text = next((m["content"] for m in messages if m["role"] == "system"), None)
        contents = [
            genai_types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[genai_types.Part.from_text(text=m["content"])],
            )
            for m in messages
            if m["role"] != "system"
        ]

        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(function_declarations=function_declarations)],
            system_instruction=system_text,
        )
        response = client.models.generate_content(
            model=self.model_name, contents=contents, config=config
        )

        for part in response.candidates[0].content.parts:
            fc = getattr(part, "function_call", None)
            if fc and fc.name:
                return ToolResponse(
                    tool_call=ToolCallRequest(
                        name=fc.name,
                        arguments=dict(fc.args),
                    )
                )

        return ToolResponse(text=response.text or "")

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: "ToolCallRequest",
        tool_result: str,
    ) -> str:
        """Second pass: inject function response and return final answer."""
        from google import genai as genai_new
        from google.genai import types as genai_types

        client = genai_new.Client(api_key=self.api_key)

        system_text = next((m["content"] for m in messages if m["role"] == "system"), None)
        contents = [
            genai_types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[genai_types.Part.from_text(text=m["content"])],
            )
            for m in messages
            if m["role"] != "system"
        ]
        contents.append(
            genai_types.Content(
                role="model",
                parts=[genai_types.Part.from_function_call(
                    name=tool_call.name, args=tool_call.arguments
                )],
            )
        )
        contents.append(
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_function_response(
                    name=tool_call.name, response={"result": tool_result}
                )],
            )
        )

        config = genai_types.GenerateContentConfig(system_instruction=system_text)
        response = client.models.generate_content(
            model=self.model_name, contents=contents, config=config
        )
        return response.text or ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestGeminiToolCalling -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_tool_calling.py
git commit -m "feat: add tool calling to GeminiLLMClient

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: OpenRouter `generate_with_tools` + `complete_with_tool_result`

**Files:**
- Modify: `portf_manager/llm_client.py` — add two methods to `OpenRouterLLMClient` (~line 228)
- Modify: `tests/unit/test_llm_tool_calling.py` — add `TestOpenRouterToolCalling` class

**Interfaces:**
- Consumes: `ToolDefinition`, `ToolCallRequest`, `ToolResponse` (Task 1)
- Produces: `OpenRouterLLMClient` satisfies `ToolCapableLLMClient`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_llm_tool_calling.py`:

```python
class TestOpenRouterToolCalling:
    def _make_client(self):
        from portf_manager.llm_client import OpenRouterLLMClient
        return OpenRouterLLMClient(api_key="test_key")

    def test_openrouter_satisfies_tool_capable_protocol(self):
        from portf_manager.llm_client import ToolCapableLLMClient

        client = self._make_client()
        assert isinstance(client, ToolCapableLLMClient)

    def test_generate_with_tools_returns_tool_call(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_quote", description="Get price.", parameters=[
            {"name": "symbol", "type": "string", "description": "Ticker", "required": True}
        ])]
        messages = [{"role": "user", "content": "Price of AAPL?"}]

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "function": {
                            "name": "get_quote",
                            "arguments": '{"symbol": "AAPL"}',
                        },
                    }],
                }
            }]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=fake_response):
            response = client.generate_with_tools(messages, tools)

        assert response.tool_call is not None
        assert response.tool_call.name == "get_quote"
        assert response.tool_call.arguments == {"symbol": "AAPL"}
        assert response.tool_call.call_id == "call_abc"

    def test_generate_with_tools_returns_text_when_no_tool(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_quote", description=".", parameters=[])]
        messages = [{"role": "user", "content": "Hello"}]

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "choices": [{"message": {"content": "Hi!", "tool_calls": None}}]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=fake_response):
            response = client.generate_with_tools(messages, tools)

        assert response.text == "Hi!"
        assert response.tool_call is None

    def test_complete_with_tool_result_returns_string(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolCallRequest

        client = self._make_client()
        messages = [{"role": "user", "content": "Price of AAPL?"}]
        tool_call = ToolCallRequest(name="get_quote", arguments={"symbol": "AAPL"}, call_id="call_abc")

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "choices": [{"message": {"content": "AAPL is $200."}}]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=fake_response):
            result = client.complete_with_tool_result(messages, tool_call, '{"price": 200}')

        assert result == "AAPL is $200."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestOpenRouterToolCalling -v
```

Expected: `AttributeError` — `generate_with_tools` not yet on `OpenRouterLLMClient`.

- [ ] **Step 3: Add methods to `OpenRouterLLMClient`**

In `portf_manager/llm_client.py`, add these two methods to `OpenRouterLLMClient` after `generate()`:

```python
    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: OpenAI-compatible tool calling via OpenRouter."""
        import json as _json

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            p["name"]: {"type": p["type"], "description": p["description"]}
                            for p in t.parameters
                        },
                        "required": [p["name"] for p in t.parameters if p.get("required", False)],
                    },
                },
            }
            for t in tools
        ]
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model_name, "messages": messages, "tools": openai_tools, "tool_choice": "auto"},
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]

        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            return ToolResponse(
                tool_call=ToolCallRequest(
                    name=tc["function"]["name"],
                    arguments=_json.loads(tc["function"]["arguments"]),
                    call_id=tc["id"],
                )
            )
        return ToolResponse(text=msg.get("content", ""))

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: "ToolCallRequest",
        tool_result: str,
    ) -> str:
        """Second pass: append tool result message and get final answer."""
        import json as _json

        extended = messages + [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": _json.dumps(tool_call.arguments),
                    },
                }],
            },
            {
                "role": "tool",
                "content": tool_result,
                "tool_call_id": tool_call.call_id,
            },
        ]
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model_name, "messages": extended},
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content", "")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestOpenRouterToolCalling -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_tool_calling.py
git commit -m "feat: add tool calling to OpenRouterLLMClient

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: Ollama `generate_with_tools` with JSON-in-prompt fallback

**Files:**
- Modify: `portf_manager/llm_client.py` — add methods to `OllamaLLMClient` (~line 154)
- Modify: `tests/unit/test_llm_tool_calling.py` — add `TestOllamaToolCalling` class

**Interfaces:**
- Consumes: `ToolDefinition`, `ToolCallRequest`, `ToolResponse` (Task 1)
- Produces: `OllamaLLMClient` satisfies `ToolCapableLLMClient`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_llm_tool_calling.py`:

```python
class TestOllamaToolCalling:
    def _make_client(self):
        from portf_manager.llm_client import OllamaLLMClient
        return OllamaLLMClient(model="llama3.2")

    def test_ollama_satisfies_tool_capable_protocol(self):
        from portf_manager.llm_client import ToolCapableLLMClient

        client = self._make_client()
        assert isinstance(client, ToolCapableLLMClient)

    def test_generate_with_tools_native_tool_call(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_holdings", description="Get positions.", parameters=[])]
        messages = [{"role": "user", "content": "My holdings?"}]

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "get_holdings", "arguments": {}}}],
            }
        }

        with patch("requests.post", return_value=fake_resp):
            response = client.generate_with_tools(messages, tools)

        assert response.tool_call is not None
        assert response.tool_call.name == "get_holdings"

    def test_generate_with_tools_fallback_returns_tool_call_from_json(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_holdings", description="Get positions.", parameters=[])]
        messages = [{"role": "user", "content": "My holdings?"}]

        # Primary /api/chat fails → fallback /api/generate returns JSON tool call
        chat_resp = MagicMock()
        chat_resp.raise_for_status.side_effect = Exception("tools not supported")

        gen_resp = MagicMock()
        gen_resp.raise_for_status = MagicMock()
        gen_resp.json.return_value = {
            "response": '{"tool_call": {"name": "get_holdings", "arguments": {}}}'
        }

        def _side_effect(url, **kwargs):
            if "/api/chat" in url:
                return chat_resp
            return gen_resp

        with patch("requests.post", side_effect=_side_effect):
            response = client.generate_with_tools(messages, tools)

        assert response.tool_call is not None
        assert response.tool_call.name == "get_holdings"

    def test_generate_with_tools_fallback_unparseable_returns_text(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolDefinition

        client = self._make_client()
        tools = [ToolDefinition(name="get_holdings", description=".", parameters=[])]
        messages = [{"role": "user", "content": "Hello"}]

        chat_resp = MagicMock()
        chat_resp.raise_for_status.side_effect = Exception("tools not supported")

        gen_resp = MagicMock()
        gen_resp.raise_for_status = MagicMock()
        gen_resp.json.return_value = {"response": "I cannot call tools right now. Here is my answer."}

        def _side_effect(url, **kwargs):
            if "/api/chat" in url:
                return chat_resp
            return gen_resp

        with patch("requests.post", side_effect=_side_effect):
            response = client.generate_with_tools(messages, tools)

        assert response.tool_call is None
        assert "answer" in response.text

    def test_complete_with_tool_result_returns_string(self):
        from unittest.mock import MagicMock, patch
        from portf_manager.llm_client import ToolCallRequest

        client = self._make_client()
        messages = [{"role": "user", "content": "Holdings?"}]
        tool_call = ToolCallRequest(name="get_holdings", arguments={})

        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"message": {"content": "You hold 10 AAPL."}}

        with patch("requests.post", return_value=fake_resp):
            result = client.complete_with_tool_result(messages, tool_call, '{"holdings": []}')

        assert result == "You hold 10 AAPL."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestOllamaToolCalling -v
```

Expected: `AttributeError` — `generate_with_tools` not yet on `OllamaLLMClient`.

- [ ] **Step 3: Add methods to `OllamaLLMClient`**

In `portf_manager/llm_client.py`, add these methods to `OllamaLLMClient` after `generate()`:

```python
    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: try native Ollama tool calling, fall back to JSON-in-prompt."""
        import json as _json

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            p["name"]: {"type": p["type"], "description": p["description"]}
                            for p in t.parameters
                        },
                        "required": [p["name"] for p in t.parameters if p.get("required", False)],
                    },
                },
            }
            for t in tools
        ]

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model_name, "messages": messages, "tools": openai_tools, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {})
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                return ToolResponse(
                    tool_call=ToolCallRequest(
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                )
            content = msg.get("content", "")
            if content:
                return ToolResponse(text=content)
        except Exception:
            pass

        return self._generate_with_tools_fallback(messages, tools)

    def _generate_with_tools_fallback(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """JSON-in-prompt fallback for Ollama models that don't support tool calling."""
        import json as _json

        tool_desc = "\n".join(
            f"- {t.name}: {t.description}"
            + (
                f" Args: {_json.dumps({p['name']: p['description'] for p in t.parameters})}"
                if t.parameters
                else ""
            )
            for t in tools
        )
        injection = (
            "You have access to tools. If you need data, respond ONLY with valid JSON:\n"
            '{"tool_call": {"name": "<tool_name>", "arguments": {<args>}}}\n'
            "Otherwise answer normally.\n\nAvailable tools:\n" + tool_desc
        )

        augmented: list[dict] = []
        injected = False
        for m in messages:
            if m["role"] == "system" and not injected:
                augmented.append({"role": "system", "content": m["content"] + "\n\n" + injection})
                injected = True
            else:
                augmented.append(m)
        if not injected:
            augmented = [{"role": "system", "content": injection}] + list(messages)

        sys_content = next((m["content"] for m in augmented if m["role"] == "system"), "")
        body = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in augmented if m["role"] != "system"
        )
        prompt = f"{sys_content}\n\n{body}\nASSISTANT:"

        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model_name, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")

        try:
            data = _json.loads(raw.strip())
            if "tool_call" in data:
                tc = data["tool_call"]
                return ToolResponse(
                    tool_call=ToolCallRequest(
                        name=tc["name"],
                        arguments=tc.get("arguments", {}),
                    )
                )
        except (_json.JSONDecodeError, KeyError):
            pass

        return ToolResponse(text=raw)

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: "ToolCallRequest",
        tool_result: str,
    ) -> str:
        """Second pass: append tool result and get final answer."""
        extended = list(messages) + [
            {"role": "assistant", "content": f"[Called {tool_call.name}]"},
            {"role": "user", "content": f"Tool result: {tool_result}\n\nAnswer the original question using this data."},
        ]
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model_name, "messages": extended, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
        except Exception:
            pass

        body = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in extended if m["role"] != "system")
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model_name, "prompt": body + "\nASSISTANT:", "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestOllamaToolCalling -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run full unit suite to check no regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v
```

Expected: all tests pass (635+ passing).

- [ ] **Step 6: Commit**

```bash
git add portf_manager/llm_client.py tests/unit/test_llm_tool_calling.py
git commit -m "feat: add tool calling to OllamaLLMClient with JSON-in-prompt fallback

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 6: `chat_tools.py` — 15 tool implementations + `execute_tool()`

**Files:**
- Create: `portf_server/chat_tools.py`
- Create: `tests/unit/test_chat_tools.py`

**Interfaces:**
- Consumes: `ToolDefinition` (Task 1); `Database` from `portf_manager.database`
- Produces:
  - `TOOLS: list[ToolDefinition]` — the full catalog
  - `execute_tool(name: str, args: dict, db: Database) -> str`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_chat_tools.py`:

```python
"""Tests for chat tool catalog and execute_tool() dispatcher."""

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def db(tmp_path):
    from portf_manager.database import Database

    d = Database(str(tmp_path / "test.db"))
    # Create a portfolio + asset + transaction for tests
    pid = d.get_or_create_portfolio("Test Broker")
    d.create_asset("AAPL", "Apple Inc", "stock", "USD")
    asset = d.get_asset_by_symbol("AAPL")
    d.create_transaction(
        portfolio_id=pid,
        asset_id=asset["id"],
        transaction_type="buy",
        quantity=10.0,
        price=150.0,
        transaction_date="2024-01-15",
        currency="USD",
    )
    d.update_asset_price(asset["id"], 200.0)
    return d


def test_tools_list_has_15_entries():
    from portf_server.chat_tools import TOOLS

    assert len(TOOLS) == 15


def test_all_tool_names_unique():
    from portf_server.chat_tools import TOOLS

    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))


def test_execute_tool_unknown_name_returns_error():
    from portf_server.chat_tools import execute_tool

    db = MagicMock()
    result = execute_tool("nonexistent_tool", {}, db)
    assert result.startswith("Error: unknown tool")


def test_execute_tool_get_holdings_returns_json(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw = execute_tool("get_holdings", {}, db)

    data = json.loads(raw)
    assert "holdings" in data
    assert data["count"] >= 1
    assert data["holdings"][0]["symbol"] == "AAPL"


def test_execute_tool_get_holdings_symbol_filter(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw = execute_tool("get_holdings", {"symbol": "AAPL"}, db)

    data = json.loads(raw)
    assert data["count"] == 1

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw2 = execute_tool("get_holdings", {"symbol": "MSFT"}, db)

    data2 = json.loads(raw2)
    assert data2["count"] == 0


def test_execute_tool_get_brokers_returns_list(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_brokers", {}, db)
    data = json.loads(raw)
    assert "brokers" in data
    assert len(data["brokers"]) >= 1


def test_execute_tool_get_kpis_returns_total_value(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.routers.portfolios._get_fx_rate", return_value=1.0):
        raw = execute_tool("get_kpis", {}, db)

    data = json.loads(raw)
    assert "total_value_eur" in data
    assert data["total_value_eur"] > 0


def test_execute_tool_get_transactions_returns_list(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_transactions", {"limit": 5}, db)
    data = json.loads(raw)
    assert "transactions" in data
    assert len(data["transactions"]) >= 1


def test_execute_tool_get_transactions_symbol_filter(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_transactions", {"symbol": "AAPL"}, db)
    data = json.loads(raw)
    assert all(t["symbol"] == "AAPL" for t in data["transactions"])


def test_execute_tool_asset_details_known_symbol(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("asset_details", {"symbol": "AAPL"}, db)
    data = json.loads(raw)
    assert data["symbol"] == "AAPL"
    assert data["name"] == "Apple Inc"


def test_execute_tool_asset_details_unknown_symbol(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("asset_details", {"symbol": "ZZZ_UNKNOWN"}, db)
    assert raw.startswith("Error:")


def test_execute_tool_get_price_no_history(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_price", {"symbol": "AAPL"}, db)
    data = json.loads(raw)
    assert "symbol" in data


def test_execute_tool_exception_returns_error_string():
    from portf_server.chat_tools import execute_tool

    bad_db = MagicMock()
    bad_db.get_all_transactions.side_effect = RuntimeError("DB exploded")

    result = execute_tool("get_holdings", {}, bad_db)
    assert result.startswith("Error:")


def test_execute_tool_get_tax_estimate_returns_year(db):
    from portf_server.chat_tools import execute_tool

    raw = execute_tool("get_tax_estimate", {"year": 2024}, db)
    data = json.loads(raw)
    assert "year" in data
    assert data["year"] == 2024
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_chat_tools.py -v
```

Expected: `ModuleNotFoundError: No module named 'portf_server.chat_tools'`

- [ ] **Step 3: Create `portf_server/chat_tools.py`**

```python
"""
Portfolio tool catalog for AI chat tool-calling.

Each tool function takes a Database instance plus keyword arguments and
returns a compact JSON string. execute_tool() is the single dispatcher
used by EnhancedChatEngine.
"""

import json
import logging
from datetime import date
from typing import Optional

from portf_manager.database import Database
from portf_manager.llm_client import ToolDefinition

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────


def _j(obj) -> str:
    return json.dumps(obj, default=str)


def _fx(currency: str) -> float:
    try:
        from portf_server.routers.portfolios import _get_fx_rate

        return _get_fx_rate(currency)
    except Exception:
        return 1.0


# ── tool registry ──────────────────────────────────────────────────────────

_REGISTRY: dict[str, callable] = {}


def _tool(name: str):
    def decorator(fn):
        _REGISTRY[name] = fn
        return fn

    return decorator


def execute_tool(name: str, args: dict, db: Database) -> str:
    """Dispatch a tool call and return a compact JSON result string.

    Always returns a string — never raises. Exceptions become "Error: …".
    """
    handler = _REGISTRY.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"
    try:
        return handler(db, **{k: v for k, v in args.items() if v is not None})
    except Exception as e:
        logger.warning("Tool %s failed: %s", name, e)
        return f"Error: {e}"


# ── tool implementations ───────────────────────────────────────────────────


@_tool("get_holdings")
def _get_holdings(
    db: Database,
    portfolio_id: Optional[str] = None,
    symbol: Optional[str] = None,
    asset_type: Optional[str] = None,
) -> str:
    from portf_manager.positions import compute_positions

    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    pos_map, _ = compute_positions(txns)
    results = []
    for aid, p in pos_map.items():
        if p["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        if symbol and asset["symbol"].upper() != symbol.upper():
            continue
        if asset_type and asset.get("asset_type", "") != asset_type:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        fx = _fx(cur)
        value_eur = p["quantity"] * price * fx
        cost_eur = p["cost"] * fx
        gain_eur = value_eur - cost_eur
        gain_pct = (gain_eur / cost_eur * 100) if cost_eur else 0.0
        results.append(
            {
                "symbol": asset["symbol"],
                "name": asset.get("name"),
                "asset_type": asset.get("asset_type"),
                "quantity": round(p["quantity"], 6),
                "avg_cost": round(p["cost"] / p["quantity"], 4) if p["quantity"] else 0,
                "current_price": round(price, 4),
                "currency": cur,
                "value_eur": round(value_eur, 2),
                "cost_eur": round(cost_eur, 2),
                "gain_eur": round(gain_eur, 2),
                "gain_pct": round(gain_pct, 2),
            }
        )
    results.sort(key=lambda x: x["value_eur"], reverse=True)
    return _j({"holdings": results, "count": len(results)})


@_tool("get_kpis")
def _get_kpis(db: Database, portfolio_id: Optional[str] = None) -> str:
    from portf_manager.positions import compute_positions

    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    pos_map, realised = compute_positions(txns)
    total_value = total_cost = 0.0
    for aid, p in pos_map.items():
        if p["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        fx = _fx(cur)
        total_value += p["quantity"] * price * fx
        total_cost += p["cost"] * fx

    net_deposits = 0.0
    for b in db.get_all_bookings(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    ):
        amt = float(b.get("amount") or 0) * _fx(b.get("currency", "EUR"))
        net_deposits += amt if b.get("action") == "Deposit" else -amt

    unrealised = total_value - total_cost
    return _j(
        {
            "total_value_eur": round(total_value, 2),
            "invested_eur": round(total_cost, 2),
            "unrealised_gain_eur": round(unrealised, 2),
            "unrealised_gain_pct": round(unrealised / total_cost * 100, 2) if total_cost else 0,
            "realised_gain_eur": round(realised, 2),
            "net_deposits_eur": round(net_deposits, 2),
        }
    )


@_tool("get_performance")
def _get_performance(
    db: Database,
    portfolio_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    from portf_manager.positions import compute_positions

    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    pos_map, realised = compute_positions(txns)

    invested = current = 0.0
    inception_date = None
    for aid, p in pos_map.items():
        if p["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        fx = _fx(cur)
        invested += p["cost"] * fx
        current += p["quantity"] * price * fx

    all_txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    dates = [t.get("transaction_date", "") for t in all_txns if t.get("transaction_date")]
    if dates:
        inception_date = min(dates)

    total_return_pct = ((current - invested) / invested * 100) if invested else 0.0
    return _j(
        {
            "total_value_eur": round(current, 2),
            "invested_eur": round(invested, 2),
            "total_return_pct": round(total_return_pct, 2),
            "realised_gain_eur": round(realised, 2),
            "inception_date": str(inception_date)[:10] if inception_date else None,
        }
    )


@_tool("get_risk")
def _get_risk(db: Database, portfolio_id: Optional[str] = None) -> str:
    from portf_server.routers.analytics import get_risk as _risk_fn

    try:
        result = _risk_fn(db=db, api_key_info={})
        return _j(result)
    except Exception as e:
        return _j({"error": str(e)})


@_tool("get_diversification")
def _get_diversification(db: Database, portfolio_id: Optional[str] = None) -> str:
    from portf_server.routers.analytics import get_diversification as _div_fn

    try:
        result = _div_fn(db=db, api_key_info={})
        return _j(result)
    except Exception as e:
        return _j({"error": str(e)})


@_tool("get_health")
def _get_health(db: Database, portfolio_id: Optional[str] = None) -> str:
    cache_key = f"portf:advisor:{portfolio_id}" if portfolio_id else "portf:advisor:all"
    cached = db.cache_get(cache_key)
    if cached:
        return _j(cached)
    return _j({"error": "No health analysis cached yet. Run from Portfolio Health page first."})


@_tool("get_brokers")
def _get_brokers(db: Database) -> str:
    portfolios = db.get_all_portfolios()
    brokers = []
    for p in portfolios:
        brokers.append(
            {
                "id": str(p["id"]),
                "name": p.get("name"),
                "website": p.get("website"),
                "description": p.get("description"),
                "first_transaction_date": str(p.get("first_transaction_date", "") or "")[:10],
                "last_transaction_date": str(p.get("last_transaction_date", "") or "")[:10],
            }
        )
    return _j({"brokers": brokers, "count": len(brokers)})


@_tool("get_quote")
def _get_quote(db: Database, symbol: str) -> str:
    from portf_manager.market import get_quote as _mkt_quote

    try:
        q = _mkt_quote(db, symbol)
        return _j(q)
    except Exception as e:
        return _j({"symbol": symbol, "error": str(e)})


@_tool("get_price")
def _get_price(
    db: Database,
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    asset = db.get_asset_by_symbol(symbol)
    if not asset:
        return f"Error: symbol '{symbol}' not found in database"
    history = db.get_price_history(
        asset["id"],
        start_date=start_date,
        end_date=end_date,
    )
    prices = [
        {"date": str(p.get("price_date", ""))[:10], "price": float(p.get("price", 0))}
        for p in (history or [])
    ]
    return _j({"symbol": symbol, "prices": prices, "count": len(prices)})


@_tool("get_research")
def _get_research(db: Database, symbol: str) -> str:
    notes = db.get_research_notes(symbol)
    if not notes:
        return _j({"symbol": symbol, "note": None, "message": "No research notes saved."})
    latest = notes[0]
    return _j(
        {
            "symbol": symbol,
            "rating": latest.get("recommendation"),
            "fair_value": latest.get("fair_value"),
            "confidence": latest.get("confidence"),
            "summary": latest.get("analysis_summary"),
            "created_at": str(latest.get("created_at", ""))[:10],
        }
    )


@_tool("get_transactions")
def _get_transactions(
    db: Database,
    portfolio_id: Optional[str] = None,
    symbol: Optional[str] = None,
    tx_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = 20,
) -> str:
    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None,
        limit=int(limit) if limit else 20,
    )
    results = []
    for t in txns:
        tx_date = str(t.get("transaction_date", ""))[:10]
        if start_date and tx_date < start_date:
            continue
        if end_date and tx_date > end_date:
            continue
        if symbol and (t.get("symbol") or "").upper() != symbol.upper():
            continue
        if tx_type and (t.get("transaction_type") or "").lower() != tx_type.lower():
            continue
        results.append(
            {
                "date": tx_date,
                "type": t.get("transaction_type"),
                "symbol": t.get("symbol"),
                "quantity": t.get("quantity"),
                "price": t.get("price"),
                "currency": t.get("currency"),
                "portfolio": t.get("portfolio_name"),
            }
        )
    return _j({"transactions": results, "count": len(results)})


@_tool("get_tax_estimate")
def _get_tax_estimate(db: Database, year: Optional[int] = None) -> str:
    from portf_manager.tax_calculator import TaxCalculator
    from portf_manager.services.analytics_service import dividend_income

    yr = int(year) if year else date.today().year
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    calc = TaxCalculator(db)
    realised_gain = 0.0
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for txns in report.values():
            realised_gain += sum(float(getattr(t, "gain_loss", 0) or 0) for t in txns)
    except Exception:
        pass

    all_txns = db.get_all_transactions()
    div = dividend_income(all_txns)
    div_this_year = div["by_year"].get(str(yr), 0.0)

    interest_this_year = 0.0
    for tx in all_txns:
        if (tx.get("transaction_type") or "").lower() != "interest":
            continue
        tx_date = str(tx.get("transaction_date", ""))[:4]
        if tx_date != str(yr):
            continue
        interest_this_year += float(tx.get("price", 0) or 0) * float(tx.get("quantity", 1) or 1)

    return _j(
        {
            "year": yr,
            "realised_gain_eur": round(realised_gain, 2),
            "dividend_income_eur": round(div_this_year, 2),
            "interest_income_eur": round(interest_this_year, 2),
            "total_savings_base_eur": round(realised_gain + div_this_year + interest_this_year, 2),
        }
    )


@_tool("asset_details")
def _asset_details(db: Database, symbol: str) -> str:
    asset = db.get_asset_by_symbol(symbol)
    if not asset:
        return f"Error: symbol '{symbol}' not found"
    return _j(
        {
            "symbol": asset["symbol"],
            "name": asset.get("name"),
            "asset_type": asset.get("asset_type"),
            "isin": asset.get("isin"),
            "ticker": asset.get("ticker"),
            "currency": asset.get("currency"),
            "exchange": asset.get("exchange"),
            "auto_price": asset.get("auto_price"),
        }
    )


@_tool("asset_news")
def _asset_news(db: Database, symbol: str) -> str:
    import yfinance as yf

    ticker_sym = symbol
    asset = db.get_asset_by_symbol(symbol)
    if asset and asset.get("ticker"):
        ticker_sym = asset["ticker"]

    try:
        ticker = yf.Ticker(ticker_sym)
        news = ticker.news or []
        items = [
            {
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "published": n.get("providerPublishTime"),
                "url": n.get("link"),
            }
            for n in news[:10]
        ]
        return _j({"symbol": symbol, "news": items, "count": len(items)})
    except Exception as e:
        return _j({"symbol": symbol, "error": str(e), "news": []})


@_tool("financial_news")
def _financial_news(db: Database, query: str) -> str:
    import yfinance as yf

    try:
        results = yf.Search(query, news_count=10).news or []
        items = [
            {
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "published": n.get("providerPublishTime"),
                "url": n.get("link"),
            }
            for n in results[:10]
        ]
        return _j({"query": query, "news": items, "count": len(items)})
    except Exception as e:
        return _j({"query": query, "error": str(e), "news": []})


# ── public catalog ─────────────────────────────────────────────────────────

TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_holdings",
        description="Return current open positions with quantity, value, cost basis, and unrealised gain.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter to a specific broker/portfolio by its numeric ID.", "required": False},
            {"name": "symbol", "type": "string", "description": "Filter to a single asset by ticker symbol (e.g. AAPL, BTC-EUR).", "required": False},
            {"name": "asset_type", "type": "string", "description": "Filter by type: stock, etf, fund, crypto, commodity, bond, index.", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_kpis",
        description="Return portfolio key performance indicators: total value, invested, unrealised/realised gain, net deposits.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter to a specific broker/portfolio.", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_performance",
        description="Return portfolio performance metrics: IRR, total return percentage, inception date.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter to a specific broker/portfolio.", "required": False},
            {"name": "start_date", "type": "string", "description": "Period start date (YYYY-MM-DD).", "required": False},
            {"name": "end_date", "type": "string", "description": "Period end date (YYYY-MM-DD).", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_risk",
        description="Return portfolio risk metrics: volatility, Sharpe ratio, Sortino ratio, max drawdown, beta.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter to a specific broker/portfolio.", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_diversification",
        description="Return portfolio diversification breakdown by asset type, sector, country, and currency, with Herfindahl concentration index.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter to a specific broker/portfolio.", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_health",
        description="Return the cached AI portfolio health analysis: scores and recommendations for diversification, risk, income, fees, and tax efficiency.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter to a specific broker/portfolio.", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_brokers",
        description="List all portfolios/brokers with their name, website, and activity dates.",
        parameters=[],
    ),
    ToolDefinition(
        name="get_quote",
        description="Get a live market quote for an asset: current price, previous close, and day change percentage.",
        parameters=[
            {"name": "symbol", "type": "string", "description": "Ticker symbol (e.g. AAPL, BTC-EUR, ^GSPC).", "required": True},
        ],
    ),
    ToolDefinition(
        name="get_price",
        description="Return historical stored prices for an asset from the database.",
        parameters=[
            {"name": "symbol", "type": "string", "description": "Ticker symbol.", "required": True},
            {"name": "start_date", "type": "string", "description": "Start date (YYYY-MM-DD).", "required": False},
            {"name": "end_date", "type": "string", "description": "End date (YYYY-MM-DD).", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_research",
        description="Return the latest saved research note for an asset: rating (BUY/HOLD/SELL), fair value, confidence, and summary.",
        parameters=[
            {"name": "symbol", "type": "string", "description": "Ticker symbol.", "required": True},
        ],
    ),
    ToolDefinition(
        name="get_transactions",
        description="Return a filtered list of transactions.",
        parameters=[
            {"name": "portfolio_id", "type": "string", "description": "Filter by broker/portfolio ID.", "required": False},
            {"name": "symbol", "type": "string", "description": "Filter by asset ticker.", "required": False},
            {"name": "tx_type", "type": "string", "description": "Filter by type: buy, sell, dividend, interest.", "required": False},
            {"name": "start_date", "type": "string", "description": "Earliest date (YYYY-MM-DD).", "required": False},
            {"name": "end_date", "type": "string", "description": "Latest date (YYYY-MM-DD).", "required": False},
            {"name": "limit", "type": "integer", "description": "Maximum results (default 20).", "required": False},
        ],
    ),
    ToolDefinition(
        name="get_tax_estimate",
        description="Return a Spanish IRPF tax estimate for a given year: realised gains, dividend income, and interest income.",
        parameters=[
            {"name": "year", "type": "integer", "description": "Tax year (default current year).", "required": False},
        ],
    ),
    ToolDefinition(
        name="asset_details",
        description="Return stored metadata for an asset: name, type, ISIN, ticker, currency, exchange.",
        parameters=[
            {"name": "symbol", "type": "string", "description": "Ticker symbol.", "required": True},
        ],
    ),
    ToolDefinition(
        name="asset_news",
        description="Return recent news articles for a specific asset via yfinance.",
        parameters=[
            {"name": "symbol", "type": "string", "description": "Ticker symbol.", "required": True},
        ],
    ),
    ToolDefinition(
        name="financial_news",
        description="Search for recent financial or market news articles matching a query.",
        parameters=[
            {"name": "query", "type": "string", "description": "Search query, e.g. 'US Federal Reserve interest rates' or 'NVIDIA earnings'.", "required": True},
        ],
    ),
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_chat_tools.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Run full unit suite to check no regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add portf_server/chat_tools.py tests/unit/test_chat_tools.py
git commit -m "feat: add chat tool catalog with 15 portfolio tools and execute_tool dispatcher

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 7: `EnhancedChatEngine` tool loop

**Files:**
- Modify: `portf_server/routers/llm.py` — add `_generate_with_tool_loop()`, update `_generate_enhanced_response()`
- Modify: `tests/unit/test_llm_tool_calling.py` — add integration test class

**Interfaces:**
- Consumes:
  - `ToolCapableLLMClient` protocol + `ToolResponse`, `ToolCallRequest` (Task 1)
  - `TOOLS`, `execute_tool()` from `portf_server.chat_tools` (Task 6)
- Produces: updated `EnhancedChatEngine` that branches to a 2-LLM-call tool loop when the provider is tool-capable

- [ ] **Step 1: Write the integration test**

Append to `tests/unit/test_llm_tool_calling.py`:

```python
class TestEnhancedChatEngineToolLoop:
    """Integration test: verifies the tool loop branches and executes tools."""

    def test_tool_loop_called_when_provider_is_tool_capable(self, tmp_path):
        from unittest.mock import MagicMock, patch, AsyncMock
        from portf_manager.llm_client import ToolCallRequest, ToolResponse
        from portf_manager.database import Database

        db = Database(str(tmp_path / "test.db"))

        mock_llm = MagicMock()
        mock_llm.generate_with_tools.return_value = ToolResponse(
            tool_call=ToolCallRequest(name="get_brokers", arguments={})
        )
        mock_llm.complete_with_tool_result.return_value = "You have 1 broker."

        with (
            patch("portf_manager.llm_client.get_llm_client", return_value=mock_llm),
            patch("portf_manager.services.market_data.get_market_data_service"),
            patch("portf_manager.services.analytics.screener.get_stock_screener"),
            patch("portf_manager.services.analytics.technical.get_technical_analysis_engine"),
            patch("portf_manager.services.analytics.fundamental.get_fundamental_analysis_engine"),
        ):
            from portf_server.routers.llm import EnhancedChatEngine, ChatRequest

            engine = EnhancedChatEngine()
            engine.llm = mock_llm

        import asyncio
        from portf_manager.llm_client import ToolCapableLLMClient

        mock_llm.__class__ = type(
            "ToolCapableMock",
            (MagicMock,),
            {
                "generate": mock_llm.generate,
                "generate_with_tools": mock_llm.generate_with_tools,
                "complete_with_tool_result": mock_llm.complete_with_tool_result,
            },
        )

        with patch("portf_manager.llm_client.ToolCapableLLMClient.__instancecheck__", return_value=True):
            with patch("portf_server.chat_tools.execute_tool", return_value='{"brokers": []}') as mock_execute:
                request = ChatRequest(message="List my brokers.", session_id="test_sess")
                result = asyncio.run(engine.process_chat_request(request, db))

        assert result.answer == "You have 1 broker."
        mock_execute.assert_called_once_with("get_brokers", {}, db)
        mock_llm.complete_with_tool_result.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestEnhancedChatEngineToolLoop -v
```

Expected: test fails — `_generate_with_tool_loop` not yet defined.

- [ ] **Step 3: Update `EnhancedChatEngine` in `portf_server/routers/llm.py`**

Add the import at the top of `llm.py` (after existing imports, around line 35):

```python
from portf_server.chat_tools import TOOLS, execute_tool
```

Add `_generate_with_tool_loop` method to `EnhancedChatEngine` (after `_build_portfolio_context`, before `_generate_enhanced_response`):

```python
    def _build_compact_context(self, db) -> str:
        """Build a brief portfolio summary for the tool-path system message."""
        try:
            from portf_manager.positions import compute_positions

            txns = db.get_all_transactions()
            pos_map, realised = compute_positions(txns)
            total_value = total_cost = 0.0
            top_holdings = []
            for aid, p in pos_map.items():
                if p["quantity"] <= 0:
                    continue
                asset = db.get_asset(aid)
                if not asset:
                    continue
                cur = asset.get("currency", "EUR")
                try:
                    from portf_server.routers.portfolios import _get_fx_rate as _fx
                except Exception:
                    def _fx(_c):
                        return 1.0
                pd_ = db.get_latest_price(aid)
                price = float(pd_["price"]) if pd_ else 0.0
                fx = _fx(cur)
                value_eur = p["quantity"] * price * fx
                total_value += value_eur
                total_cost += p["cost"] * fx
                top_holdings.append({"symbol": asset["symbol"], "value_eur": round(value_eur, 2)})

            top_holdings.sort(key=lambda x: x["value_eur"], reverse=True)
            return json.dumps(
                {
                    "total_value_eur": round(total_value, 2),
                    "invested_eur": round(total_cost, 2),
                    "unrealised_gain_eur": round(total_value - total_cost, 2),
                    "top_5_holdings": top_holdings[:5],
                }
            )
        except Exception as e:
            logger.warning("compact context failed: %s", e)
            return "{}"

    async def _generate_with_tool_loop(
        self, message: str, context: Dict[str, Any], session_id: str, db
    ) -> str:
        """Tool-calling path: up to 2 LLM calls per user message.

        Pass 1: LLM may request a tool call.
        Pass 2 (if tool call): execute tool, send result back, get final answer.
        """
        from portf_manager.llm_client import ToolCapableLLMClient

        compact = self._build_compact_context(db)
        system_content = (
            "You are the portfolio assistant for THIS user's Portfolio Manager app.\n"
            "You have access to tools that can fetch live portfolio data. "
            "Use them when the user asks for specific data you don't already have.\n"
            "All monetary values are in EUR unless stated otherwise.\n"
            "This is informational — not regulated financial advice.\n\n"
            f"Portfolio summary (top 5 holdings shown; call get_holdings for full list):\n{compact}"
        )

        history = _get_history(db, session_id)
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
        for msg in history[-4:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})

        try:
            response = await asyncio.to_thread(self.llm.generate_with_tools, messages, TOOLS)
        except Exception as e:
            logger.error("generate_with_tools failed: %s", e)
            return "I'm having trouble accessing the AI service right now. Please try again."

        if response.text:
            return response.text

        if response.tool_call:
            tool_result = execute_tool(response.tool_call.name, response.tool_call.arguments, db)
            try:
                final = await asyncio.to_thread(
                    self.llm.complete_with_tool_result, messages, response.tool_call, tool_result
                )
                return final
            except Exception as e:
                logger.error("complete_with_tool_result failed: %s", e)
                return f"I retrieved the data but couldn't generate a response: {tool_result}"

        return "I wasn't able to generate a response. Please try again."
```

Update `_generate_enhanced_response` in `EnhancedChatEngine` — replace the existing method body with:

```python
    async def _generate_enhanced_response(
        self, message: str, context: Dict[str, Any], session_id: str, db
    ) -> str:
        """Route to tool loop when provider is tool-capable, else use static-context path."""
        from portf_manager.llm_client import ToolCapableLLMClient

        if isinstance(self.llm, ToolCapableLLMClient):
            return await self._generate_with_tool_loop(message, context, session_id, db)

        # Existing static-context path (unchanged)
        prompt = self._build_enhanced_prompt(message, context, session_id, db)
        try:
            response = await asyncio.to_thread(self.llm.generate, prompt)
            return response
        except Exception as e:
            logger.error(f"LLM API error: {e}")
            return "I apologize, but I'm having trouble accessing the AI service right now. Please try again later."
```

- [ ] **Step 4: Run the integration test**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_llm_tool_calling.py::TestEnhancedChatEngineToolLoop -v
```

Expected: PASS.

- [ ] **Step 5: Run full unit suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v
```

Expected: all tests pass.

- [ ] **Step 6: Reload the backend and smoke-test via the web chat**

```bash
docker exec portf_backend_dev kill -HUP 1
```

Open the chat UI. Ask: "What are my top holdings?" — the LLM should call `get_holdings` and return real data.

Ask: "What's the price of AAPL?" — should call `get_quote`.

Ask: "Give me a general market summary" — should answer without a tool call (just from context).

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/llm.py portf_server/chat_tools.py tests/unit/test_llm_tool_calling.py
git commit -m "feat: add tool-calling agentic loop to EnhancedChatEngine

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
