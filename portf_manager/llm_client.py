"""
Provider-agnostic LLM client abstraction.

Supports Gemini, Ollama, OpenRouter and Anthropic Claude through a unified
interface. Configuration is driven by environment variables:

Global overrides (apply to all providers):
  PORTF_LLM_PROVIDER  — "auto" (default), "ollama", "gemini", "openrouter", "anthropic"
  PORTF_LLM_MODEL     — model name for the selected provider (provider defaults apply)

Per-provider model overrides (take precedence over PORTF_LLM_MODEL):
  PORTF_GEMINI_MODEL      — e.g. "gemini-2.5-pro"
  PORTF_ANTHROPIC_MODEL   — pins ONE model for every tier (e.g. "claude-opus-5-5")
  PORTF_ANTHROPIC_FAST_MODEL / _MAIN_MODEL / _HEAVY_MODEL — per tier
      (defaults claude-haiku-5-5 / claude-sonnet-5-5 / claude-opus-5-5); a call
      site asks for a tier with get_llm_client(tier="fast"|"main"|"heavy")
  PORTF_OPENROUTER_MODEL  — e.g. "openai/gpt-4o"
  PORTF_OLLAMA_MODEL      — e.g. "llama3.2"

Required API keys:
  GEMINI_API_KEY / PORTF_GEMINI_API_KEY / GOOGLE_API_KEY  — for Gemini
  OPENROUTER_API_KEY / PORTF_OPENROUTER_API_KEY           — for OpenRouter
  ANTHROPIC_API_KEY                                        — for Anthropic

Reliability (every provider, however it was constructed):
  PORTF_LLM_MAX_ATTEMPTS  — attempts per call for transient failures (default 3)
  PORTF_LLM_RETRY_DELAY   — first backoff in seconds, doubling each retry (default 2)
  A call that still fails raises LLMError naming provider, model, attempts and
  cause. Each call is logged once as an "llm.call" event (see event_log.py).

Default auto-detection order (provider=auto):
  1. Ollama on localhost:11434 — no API key needed
  2. Anthropic  — if ANTHROPIC_API_KEY is set
  3. Gemini   — if GEMINI_API_KEY is set
  4. OpenRouter — if OPENROUTER_API_KEY is set
"""

import functools
import os
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, runtime_checkable

import requests

from . import telemetry

logger = logging.getLogger(__name__)

# Default models per provider
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM clients. All providers must implement generate()."""

    def generate(self, prompt: str) -> str:
        """Send a prompt and return the generated text response."""
        ...


@runtime_checkable
class SearchCapableLLMClient(LLMClient, Protocol):
    """LLM client that supports live web search during generation.

    Returns a JSON envelope: {"text": "<llm output>", "sources": [{"title": ..., "url": ...}]}
    """

    def generate_with_search(self, prompt: str, symbol: str) -> str:
        """Generate with live web search grounding."""
        ...


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
    # Provider-specific raw assistant turn (Anthropic: content blocks incl.
    # thinking), replayed verbatim with the tool result.
    raw_content: Optional[list] = None


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
        tools: Optional[list["ToolDefinition"]] = None,
    ) -> str:
        """Second pass: given tool result, return final answer string."""
        ...


class LLMError(RuntimeError):
    """An LLM call failed, after retries where the failure looked transient.

    Attributes:
        provider: Provider name, e.g. "Gemini".
        model: Model name the client was configured with.
        operation: Client method, e.g. "generate".
        attempts: How many attempts were made.
        cause: The last underlying exception.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        operation: str,
        attempts: int,
        cause: Exception,
    ):
        self.provider = provider
        self.model = model
        self.operation = operation
        self.attempts = attempts
        self.cause = cause
        plural = "s" if attempts != 1 else ""
        super().__init__(
            f"{provider} ({model}) {operation} failed after {attempts} "
            f"attempt{plural}: {cause}"
        )


class LLMResponseError(RuntimeError):
    """The LLM answered, but its reply couldn't be used (e.g. not valid JSON)."""


_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504, 529}
_TRANSIENT_STATUS_RE = re.compile(
    r"\b(" + "|".join(str(c) for c in sorted(_TRANSIENT_STATUS)) + r")\b"
)
_TRANSIENT_WORDS = (
    "timed out",
    "timeout",
    "cannot connect",
    "connection",
    "temporarily",
    "unavailable",
    "overloaded",
    "rate limit",
    "resource_exhausted",
    "empty response",
)


def _status_of(exc: Exception) -> Optional[int]:
    """HTTP status carried by an SDK or requests exception, if any."""
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_transient_llm_error(exc: Exception) -> bool:
    """True when retrying could help: timeouts, connection errors, 429 and 5xx.

    Configuration and request errors (a missing key, a 400/401/403) are not
    retried; they fail the same way every time.
    """
    if isinstance(exc, (ValueError, ImportError, TypeError, KeyError)):
        return False
    status = _status_of(exc)
    if status is not None:
        return status in _TRANSIENT_STATUS
    if isinstance(
        exc,
        (requests.ConnectionError, requests.Timeout, TimeoutError, ConnectionError),
    ):
        return True
    msg = str(exc).lower()
    return any(word in msg for word in _TRANSIENT_WORDS) or bool(
        _TRANSIENT_STATUS_RE.search(msg)
    )


_INSTRUMENTED_METHODS = (
    "generate",
    "generate_with_search",
    "generate_with_tools",
    "complete_with_tool_result",
)
_call_depth = threading.local()


def _provider_name(client: object) -> str:
    return type(client).__name__.replace("LLMClient", "") or type(client).__name__


def _call_with_retry(client: object, operation: str, fn: Callable, args, kwargs):
    """Run one provider call with retries, logging a single llm.call event."""
    max_attempts = max(1, int(os.getenv("PORTF_LLM_MAX_ATTEMPTS", "3")))
    delay = float(os.getenv("PORTF_LLM_RETRY_DELAY", "2"))
    provider = _provider_name(client)
    model = getattr(client, "model_name", "?")
    errors: List[str] = []
    started = time.monotonic()
    attempt = 0
    with telemetry.llm_span(provider, model, operation, args) as span:
        while True:
            attempt += 1
            try:
                result = fn(client, *args, **kwargs)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if attempt < max_attempts and is_transient_llm_error(exc):
                    logger.info(
                        "%s %s attempt %d/%d failed, retrying in %.1fs: %s",
                        provider,
                        operation,
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                _log_llm_call(
                    logging.ERROR, provider, model, operation, attempt, started, errors
                )
                raise LLMError(provider, model, operation, attempt, exc) from exc
            level = logging.WARNING if attempt > 1 else logging.INFO
            _log_llm_call(level, provider, model, operation, attempt, started, errors)
            telemetry.finish_llm_span(span, attempt, result)
            return result


def _log_llm_call(
    level: int,
    provider: str,
    model: str,
    operation: str,
    attempts: int,
    started: float,
    errors: List[str],
) -> None:
    if level >= logging.ERROR:
        outcome = "failed"
    elif attempts > 1:
        outcome = "succeeded after retry"
    else:
        outcome = "ok"
    duration_s = time.monotonic() - started
    duration_ms = int(duration_s * 1000)
    telemetry.observe_llm_call(
        provider, model, operation, outcome, attempts, duration_s
    )
    logger.log(
        level,
        "LLM %s %s %s (%d attempt%s, %d ms)",
        provider,
        operation,
        outcome,
        attempts,
        "s" if attempts != 1 else "",
        duration_ms,
        extra={
            "pfm_event": "llm.call",
            "pfm_details": {
                "provider": provider,
                "model": model,
                "operation": operation,
                "outcome": outcome,
                "attempts": attempts,
                "duration_ms": duration_ms,
                "errors": errors,
            },
        },
    )


def _instrument(cls: type) -> type:
    """Wrap a provider's LLM methods with retry and llm.call logging.

    Only methods the class defines are wrapped, so ``hasattr(client,
    "generate_with_search")`` still tells providers apart. A call made from
    inside another instrumented call (Gemini's search falling back to
    ``generate``) runs unwrapped, so it isn't retried or logged twice.
    """
    for name in _INSTRUMENTED_METHODS:
        original = cls.__dict__.get(name)
        if original is None:
            continue

        def make(original: Callable, name: str) -> Callable:
            @functools.wraps(original)
            def wrapper(self, *args, **kwargs):
                if getattr(_call_depth, "n", 0):
                    return original(self, *args, **kwargs)
                _call_depth.n = 1
                try:
                    return _call_with_retry(self, name, original, args, kwargs)
                finally:
                    _call_depth.n = 0

            return wrapper

        setattr(cls, name, make(original, name))
    return cls


class GeminiLLMClient:
    """Google Gemini LLM client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("PORTF_GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "Gemini API key required. Set GEMINI_API_KEY environment variable."
            )

        self.model_name = (
            model
            or os.getenv("PORTF_GEMINI_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_GEMINI_MODEL
        )

        # Import lazily to avoid hard dependency when using Ollama
        try:
            from google import genai as genai_sdk

            self._client = genai_sdk.Client(api_key=self.api_key)
        except ImportError:
            raise ImportError(
                "google-genai package required for Gemini provider. "
                "Install with: pip install google-genai"
            )

        logger.info(f"Gemini LLM client initialized (model={self.model_name})")

    def generate(self, prompt: str) -> str:
        """Generate text using Google Gemini."""
        response = self._client.models.generate_content(
            model=self.model_name, contents=prompt
        )
        if not response.text or not response.text.strip():
            raise RuntimeError("Empty response from Gemini API")
        return response.text

    def generate_with_search(self, prompt: str, symbol: str) -> str:
        """Google Search grounded generation via the new google-genai SDK."""
        import json

        try:
            text, sources = self._gemini_search(prompt)
        except ImportError:
            logger.warning(
                "google-genai SDK not installed; falling back to generate() without search"
            )
            return json.dumps({"text": self.generate(prompt), "sources": []})
        return json.dumps({"text": text, "sources": sources})

    def _gemini_search(self, prompt: str) -> tuple[str, list[dict]]:
        """Call Gemini with Google Search grounding using the new google-genai SDK."""
        from google.genai import types as genai_types

        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
        )
        response = self._client.models.generate_content(
            model=self.model_name, contents=prompt, config=config
        )
        if not response.text or not response.text.strip():
            raise RuntimeError(
                "Empty response from Gemini API (search-grounded call) — "
                "the model may have exhausted its token budget on thinking/search "
                "without producing a final answer, or been safety-blocked"
            )
        text = response.text
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

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: returns tool call or final answer via Gemini function calling."""
        from google.genai import types as genai_types

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
                    required=[
                        p["name"] for p in t.parameters if p.get("required", False)
                    ],
                ),
            )
            for t in tools
        ]

        system_text = next(
            (m["content"] for m in messages if m["role"] == "system"), None
        )
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
        response = self._client.models.generate_content(
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
        tools: Optional[list["ToolDefinition"]] = None,
    ) -> str:
        """Second pass: inject function response and return final answer."""
        from google.genai import types as genai_types

        system_text = next(
            (m["content"] for m in messages if m["role"] == "system"), None
        )
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
                parts=[
                    genai_types.Part.from_function_call(
                        name=tool_call.name, args=tool_call.arguments
                    )
                ],
            )
        )
        contents.append(
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_function_response(
                        name=tool_call.name, response={"result": tool_result}
                    )
                ],
            )
        )

        config = genai_types.GenerateContentConfig(system_instruction=system_text)
        response = self._client.models.generate_content(
            model=self.model_name, contents=contents, config=config
        )
        return response.text or ""


class OllamaLLMClient:
    """Ollama local LLM client. Requires a running Ollama instance."""

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ):
        self.model_name = (
            model
            or os.getenv("PORTF_OLLAMA_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_OLLAMA_MODEL
        )
        self.host = host or os.getenv("OLLAMA_HOST", "localhost")
        self.port = int(port or os.getenv("OLLAMA_PORT", "11434"))
        self.base_url = f"http://{self.host}:{self.port}"

        logger.info(
            "Ollama LLM client initialized "
            f"(model={self.model_name}, url={self.base_url})"
        )

    @staticmethod
    def is_available(
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> bool:
        """Check if Ollama is reachable (quick connectivity test)."""
        h = host or os.getenv("OLLAMA_HOST", "localhost")
        p = int(port or os.getenv("OLLAMA_PORT", "11434"))
        try:
            resp = requests.get(f"http://{h}:{p}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str) -> str:
        """Generate text using a local Ollama model."""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
        }

        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
        except requests.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Is Ollama running? Start with: ollama serve"
            )
        except requests.Timeout:
            raise RuntimeError(
                f"Ollama request timed out. The model '{self.model_name}' "
                "may be too large or still loading."
            )
        except requests.HTTPError as e:
            raise RuntimeError(f"Ollama API error: {e}")

        data = resp.json()
        text = data.get("response", "")
        if not text:
            raise RuntimeError(
                "Empty response from Ollama. "
                f"Is model '{self.model_name}' pulled? "
                f"Run: ollama pull {self.model_name}"
            )
        return text

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: try native Ollama tool calling, fall back to JSON-in-prompt."""

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            p["name"]: {
                                "type": p["type"],
                                "description": p["description"],
                            }
                            for p in t.parameters
                        },
                        "required": [
                            p["name"] for p in t.parameters if p.get("required", False)
                        ],
                    },
                },
            }
            for t in tools
        ]

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model_name,
                    "messages": messages,
                    "tools": openai_tools,
                    "stream": False,
                },
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
                augmented.append(
                    {"role": "system", "content": m["content"] + "\n\n" + injection}
                )
                injected = True
            else:
                augmented.append(m)
        if not injected:
            augmented = [{"role": "system", "content": injection}] + list(messages)

        sys_content = next(
            (m["content"] for m in augmented if m["role"] == "system"), ""
        )
        body = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in augmented
            if m["role"] != "system"
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
        tools: Optional[list["ToolDefinition"]] = None,
    ) -> str:
        """Second pass: append tool result and get final answer."""
        extended = list(messages) + [
            {"role": "assistant", "content": f"[Called {tool_call.name}]"},
            {
                "role": "user",
                "content": f"Tool result: {tool_result}\n\nAnswer the original question using this data.",
            },
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

        body = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in extended
            if m["role"] != "system"
        )
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model_name,
                "prompt": body + "\nASSISTANT:",
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


class OpenRouterLLMClient:
    """OpenRouter LLM client. Supports any model available on openrouter.ai."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = (
            api_key
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("PORTF_OPENROUTER_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY environment variable."
            )
        self.model_name = (
            model
            or os.getenv("PORTF_OPENROUTER_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_OPENROUTER_MODEL
        )
        self.base_url = "https://openrouter.ai/api/v1"
        logger.info(f"OpenRouter LLM client initialized (model={self.model_name})")

    def generate(self, prompt: str) -> str:
        """Generate text using OpenRouter."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
        except requests.ConnectionError:
            raise RuntimeError("Cannot connect to OpenRouter API.")
        except requests.Timeout:
            raise RuntimeError(
                f"OpenRouter request timed out (model={self.model_name})."
            )
        except requests.HTTPError as e:
            raise RuntimeError(f"OpenRouter API error: {e}")

        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected OpenRouter response: {data}")
        if not text:
            raise RuntimeError("Empty response from OpenRouter API")
        return text

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
                            p["name"]: {
                                "type": p["type"],
                                "description": p["description"],
                            }
                            for p in t.parameters
                        },
                        "required": [
                            p["name"] for p in t.parameters if p.get("required", False)
                        ],
                    },
                },
            }
            for t in tools
        ]
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model_name,
                "messages": messages,
                "tools": openai_tools,
                "tool_choice": "auto",
            },
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
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
        tools: Optional[list["ToolDefinition"]] = None,
    ) -> str:
        """Second pass: append tool result message and get final answer."""
        import json as _json

        extended = messages + [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call.call_id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": _json.dumps(tool_call.arguments),
                        },
                    }
                ],
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
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content", "")


# ── Anthropic model tiers ───────────────────────────────────────────────────
# Every call site names a tier, never a model slug:
#   fast  — extraction / classification (statement parsing, categories)
#   main  — everyday structured generation
#   heavy — deep reasoning (research, stock reports, portfolio analysis, chat)
# A tier resolves to a model through env vars, so a model upgrade is a config
# change, not a code change.
TIERS = ("fast", "main", "heavy")
DEFAULT_TIER = "main"
DEFAULT_ANTHROPIC_MODELS = {
    "fast": "claude-haiku-5-5",
    "main": "claude-sonnet-5-5",
    "heavy": "claude-opus-5-5",
}
DEFAULT_ANTHROPIC_MODEL = DEFAULT_ANTHROPIC_MODELS[DEFAULT_TIER]
# Claude 5 models think by default and thinking tokens count against
# max_tokens, so every tier gets a generous cap; "fast" also switches thinking
# off (Haiku only) and runs at low effort. temperature is rejected with a 400
# on these models and is never sent.
_ANTHROPIC_TIER_PARAMS = {
    "fast": {"effort": "low", "max_tokens": 16000},
    "main": {"effort": "medium", "max_tokens": 16000},
    "heavy": {"effort": "high", "max_tokens": 16000},
}


def normalize_tier(tier: Optional[str]) -> str:
    tier = (tier or DEFAULT_TIER).lower()
    if tier not in TIERS:
        raise ValueError(f"Unknown LLM tier: '{tier}'. Supported: {', '.join(TIERS)}")
    return tier


def resolve_anthropic_model(tier: Optional[str] = None) -> str:
    """Model for a tier: PORTF_ANTHROPIC_<TIER>_MODEL, else the single-model
    pin (PORTF_ANTHROPIC_MODEL / PORTF_LLM_MODEL), else the tier default."""
    tier = normalize_tier(tier)
    return (
        os.getenv(f"PORTF_ANTHROPIC_{tier.upper()}_MODEL")
        or os.getenv("PORTF_ANTHROPIC_MODEL")
        or os.getenv("PORTF_LLM_MODEL")
        or DEFAULT_ANTHROPIC_MODELS[tier]
    )


def _anthropic_tools(tools: list["ToolDefinition"]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    p["name"]: {"type": p["type"], "description": p["description"]}
                    for p in t.parameters
                },
                "required": [p["name"] for p in t.parameters if p.get("required")],
            },
        }
        for t in tools
    ]


def _anthropic_text(msg) -> str:
    """All text blocks of a reply; a truncated, refused or empty reply raises."""
    stop = getattr(msg, "stop_reason", None)
    if stop == "refusal":
        raise LLMResponseError("Anthropic declined the request (stop_reason=refusal)")
    text = "".join(
        b.text for b in msg.content if getattr(b, "type", None) == "text"
    ).strip()
    if stop == "max_tokens":
        raise LLMResponseError(
            "Anthropic reply hit max_tokens (thinking and output share the cap)"
        )
    if not text:
        raise LLMResponseError("Anthropic returned no text content")
    return text


class AnthropicLLMClient:
    """Anthropic Claude client: tiered models, web_search, tool use."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        tier: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY environment variable."
            )
        self.tier = normalize_tier(tier)
        self.model_name = model or resolve_anthropic_model(self.tier)
        self._sdk = None
        logger.info(
            "Anthropic LLM client initialized (tier=%s, model=%s)",
            self.tier,
            self.model_name,
        )

    def _client(self):
        if self._sdk is None:
            try:
                import anthropic as anthropic_sdk
            except ImportError:
                raise ImportError(
                    "anthropic package required for Anthropic provider. "
                    "Install with: pip install anthropic"
                )
            self._sdk = anthropic_sdk.Anthropic(api_key=self.api_key)
        return self._sdk

    def _create(self, **kwargs):
        params = _ANTHROPIC_TIER_PARAMS[self.tier]
        kwargs.setdefault("max_tokens", params["max_tokens"])
        kwargs.setdefault("output_config", {"effort": params["effort"]})
        if self.tier == "fast" and "haiku" in self.model_name:
            kwargs.setdefault("thinking", {"type": "disabled"})
        return self._client().messages.create(model=self.model_name, **kwargs)

    def generate(self, prompt: str) -> str:
        """Generate text (no search tools)."""
        msg = self._create(messages=[{"role": "user", "content": prompt}])
        return _anthropic_text(msg)

    def generate_with_search(self, prompt: str, symbol: str) -> str:
        """Generate with the web_search server tool (up to 5 searches).

        Returns the JSON envelope research.py expects: {"text", "sources"}.
        """
        import json

        # Dynamic-filtering search needs Opus/Sonnet; Haiku keeps the basic one.
        search_type = (
            "web_search_20250305"
            if "haiku" in self.model_name
            else "web_search_20260209"
        )
        messages = [{"role": "user", "content": prompt}]
        tools = [{"type": search_type, "name": "web_search", "max_uses": 5}]
        msg = None
        for _ in range(4):  # a long server-tool turn can pause; resume it
            msg = self._create(messages=messages, tools=tools)
            if msg.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": msg.content}]
        sources: list = []
        for block in msg.content:
            if getattr(block, "type", None) == "web_search_tool_result":
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    for r in content:
                        url = getattr(r, "url", None)
                        if url and all(s["url"] != url for s in sources):
                            sources.append(
                                {"title": getattr(r, "title", "") or url, "url": url}
                            )
        return json.dumps({"text": _anthropic_text(msg), "sources": sources})

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list["ToolDefinition"],
    ) -> "ToolResponse":
        """First pass: returns a tool call or a final answer (native tool use)."""
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        kwargs: dict = {
            "tools": _anthropic_tools(tools),
            # The chat engine executes one tool per turn.
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "messages": [m for m in messages if m["role"] != "system"],
        }
        if system:
            kwargs["system"] = system
        msg = self._create(**kwargs)

        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return ToolResponse(
                    tool_call=ToolCallRequest(
                        name=block.name,
                        arguments=block.input,
                        call_id=block.id,
                        # The whole assistant turn (thinking blocks included)
                        # must be replayed with the tool result.
                        raw_content=[
                            b.model_dump(exclude_none=True) for b in msg.content
                        ],
                    )
                )
        return ToolResponse(text=_anthropic_text(msg))

    def complete_with_tool_result(
        self,
        messages: list[dict],
        tool_call: "ToolCallRequest",
        tool_result: str,
        tools: Optional[list["ToolDefinition"]] = None,
    ) -> str:
        """Second pass: send the tool result back and return the final answer."""
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        assistant_content = tool_call.raw_content or [
            {
                "type": "tool_use",
                "id": tool_call.call_id,
                "name": tool_call.name,
                "input": tool_call.arguments,
            }
        ]
        extended = [m for m in messages if m["role"] != "system"] + [
            {"role": "assistant", "content": assistant_content},
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
        kwargs: dict = {"messages": extended}
        if system:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = _anthropic_tools(tools)
            # tools must be declared while the history holds tool_use blocks,
            # but this pass has to end in prose, not another tool request.
            kwargs["tool_choice"] = {"type": "none"}
        return _anthropic_text(self._create(**kwargs))


for _provider_cls in (
    GeminiLLMClient,
    OllamaLLMClient,
    OpenRouterLLMClient,
    AnthropicLLMClient,
):
    _instrument(_provider_cls)


# Singleton cache. ``_llm_client`` is the default-tier client; Anthropic tier
# clients (same provider, different model) are cached per tier.
_llm_client: Optional[LLMClient] = None
_tier_clients: dict = {}


def get_llm_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    force_new: bool = False,
    tier: Optional[str] = None,
) -> LLMClient:
    """
    Factory function to get an LLM client instance.

    Uses singleton pattern by default. Pass force_new=True to create
    a fresh instance (useful for testing or switching providers at runtime).

    ``tier`` ("fast" | "main" | "heavy") picks the model class for the call:
    extraction/classification, everyday generation, or deep reasoning. Only the
    Anthropic provider maps tiers to different models (PORTF_ANTHROPIC_FAST_MODEL
    / _MAIN_MODEL / _HEAVY_MODEL); other providers ignore it.

    Config priority: explicit args > env vars > defaults.

    When provider is "auto" (the default), the first available of Ollama,
    Anthropic, Gemini, OpenRouter is used.
    """
    global _llm_client

    if tier is not None:
        tier = normalize_tier(tier)

    if tier is not None and tier in _tier_clients and not force_new:
        return _tier_clients[tier]

    if _llm_client is not None and not force_new:
        if (
            tier is None
            or tier == DEFAULT_TIER
            or not isinstance(_llm_client, AnthropicLLMClient)
        ):
            return _llm_client
        if tier not in _tier_clients:
            _tier_clients[tier] = AnthropicLLMClient(
                api_key=_llm_client.api_key, model=model, tier=tier
            )
        return _tier_clients[tier]

    provider = (provider or os.getenv("PORTF_LLM_PROVIDER", "auto")).lower()

    if provider == "auto":
        client = _auto_detect_provider(model, tier)
    elif provider == "gemini":
        client = GeminiLLMClient(model=model)
    elif provider == "ollama":
        client = OllamaLLMClient(model=model)
    elif provider == "openrouter":
        client = OpenRouterLLMClient(model=model)
    elif provider == "anthropic":
        client = AnthropicLLMClient(model=model, tier=tier)
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            "Supported: 'auto', 'gemini', 'ollama', 'openrouter', 'anthropic'"
        )

    if isinstance(client, AnthropicLLMClient) and client.tier != DEFAULT_TIER:
        _tier_clients[client.tier] = client
        return client
    _llm_client = client
    return _llm_client


def _auto_detect_provider(
    model: Optional[str] = None, tier: Optional[str] = None
) -> LLMClient:
    """
    Auto-detect the best available LLM provider.

    Priority: Ollama (local, no API key) > Anthropic > Gemini > OpenRouter.
    """
    # Try Ollama first — free, local, no API key
    if OllamaLLMClient.is_available():
        logger.info("Auto-detected Ollama running locally")
        return OllamaLLMClient(model=model)

    # Try Anthropic if API key is available
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        logger.info("Auto-detected Anthropic API key, using Claude")
        return AnthropicLLMClient(api_key=anthropic_key, model=model, tier=tier)

    # Try Gemini if API key is available
    gemini_key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("PORTF_GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    if gemini_key:
        logger.info("Auto-detected Gemini API key, using Gemini")
        return GeminiLLMClient(api_key=gemini_key, model=model)

    # Try OpenRouter if API key is available
    openrouter_key = os.getenv("OPENROUTER_API_KEY") or os.getenv(
        "PORTF_OPENROUTER_API_KEY"
    )
    if openrouter_key:
        logger.info("Auto-detected OpenRouter API key, using OpenRouter")
        return OpenRouterLLMClient(api_key=openrouter_key, model=model)

    raise RuntimeError(
        "No LLM provider available. Either:\n"
        "  1. Start Ollama locally: ollama serve && ollama pull llama3.2\n"
        "  2. Set GEMINI_API_KEY for Google Gemini\n"
        "  3. Set OPENROUTER_API_KEY for OpenRouter\n"
        "  4. Set ANTHROPIC_API_KEY for Anthropic Claude\n"
        "  5. Set PORTF_LLM_PROVIDER=ollama|gemini|openrouter|anthropic explicitly"
    )


def reset_llm_client() -> None:
    """Reset the cached LLM client. Useful for testing."""
    global _llm_client
    _llm_client = None
    _tier_clients.clear()


def get_llm_info() -> dict:
    """Return config info for the current (or would-be) LLM client.

    Does NOT instantiate a new client — reads env vars to infer what
    get_llm_client() would produce. Safe to call at startup for logging.

    Returns:
        dict with keys: provider, model, search_capable, singleton_active.
    """
    provider = os.getenv("PORTF_LLM_PROVIDER", "auto").lower()

    if provider == "gemini":
        model = (
            os.getenv("PORTF_GEMINI_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_GEMINI_MODEL
        )
        search_capable = True
    elif provider == "ollama":
        model = (
            os.getenv("PORTF_OLLAMA_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_OLLAMA_MODEL
        )
        search_capable = False
    elif provider == "openrouter":
        model = (
            os.getenv("PORTF_OPENROUTER_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_OPENROUTER_MODEL
        )
        search_capable = False
    elif provider == "anthropic":
        model = resolve_anthropic_model(DEFAULT_TIER)
        search_capable = True
    else:
        # "auto" — can't resolve without a network check
        model = os.getenv("PORTF_LLM_MODEL") or "auto-detected"
        search_capable = None

    # If the singleton is already live, read from it directly.
    if _llm_client is not None:
        provider = type(_llm_client).__name__.replace("LLMClient", "").lower()
        model = getattr(_llm_client, "model_name", model)
        search_capable = isinstance(_llm_client, SearchCapableLLMClient)

    info = {
        "provider": provider,
        "model": model,
        "search_capable": search_capable,
        "singleton_active": _llm_client is not None,
    }
    if provider == "anthropic":
        info["tiers"] = {t: resolve_anthropic_model(t) for t in TIERS}
    return info
