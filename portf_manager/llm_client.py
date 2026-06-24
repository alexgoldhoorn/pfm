"""
Provider-agnostic LLM client abstraction.

Supports Gemini, Ollama, OpenRouter and Anthropic Claude through a unified
interface. Configuration is driven by environment variables:

Global overrides (apply to all providers):
  PORTF_LLM_PROVIDER  — "auto" (default), "ollama", "gemini", "openrouter", "anthropic"
  PORTF_LLM_MODEL     — model name for the selected provider (provider defaults apply)

Per-provider model overrides (take precedence over PORTF_LLM_MODEL):
  PORTF_GEMINI_MODEL      — e.g. "gemini-2.5-pro"
  PORTF_ANTHROPIC_MODEL   — e.g. "claude-opus-4-8"
  PORTF_OPENROUTER_MODEL  — e.g. "openai/gpt-4o"
  PORTF_OLLAMA_MODEL      — e.g. "llama3.2"

Required API keys:
  GEMINI_API_KEY / PORTF_GEMINI_API_KEY / GOOGLE_API_KEY  — for Gemini
  OPENROUTER_API_KEY / PORTF_OPENROUTER_API_KEY           — for OpenRouter
  ANTHROPIC_API_KEY                                        — for Anthropic

Default auto-detection order (provider=auto):
  1. Ollama on localhost:11434 — no API key needed
  2. Gemini   — if GEMINI_API_KEY is set
  3. OpenRouter — if OPENROUTER_API_KEY is set
  4. Anthropic  — if ANTHROPIC_API_KEY is set
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import requests

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
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(self.model_name)
        except ImportError:
            raise ImportError(
                "google-generativeai package required for Gemini provider. "
                "Install with: pip install google-generativeai"
            )

        logger.info(f"Gemini LLM client initialized (model={self.model_name})")

    def generate(self, prompt: str) -> str:
        """Generate text using Google Gemini."""
        response = self._model.generate_content(prompt)
        if not response.text:
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
        self.model_name = (
            model
            or os.getenv("PORTF_ANTHROPIC_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_ANTHROPIC_MODEL
        )
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
        import json

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
            tools=[
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in msg.content:
            if hasattr(block, "type") and block.type == "text":
                text = block.text
        return text


# Singleton cache
_llm_client: Optional[LLMClient] = None


def get_llm_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    force_new: bool = False,
) -> LLMClient:
    """
    Factory function to get an LLM client instance.

    Uses singleton pattern by default. Pass force_new=True to create
    a fresh instance (useful for testing or switching providers at runtime).

    Config priority: explicit args > env vars > defaults.

    When provider is "auto" (the default):
      1. Try Ollama on localhost — works without API keys
      2. Fall back to Gemini if GEMINI_API_KEY is set
      3. Raise error if neither is available
    """
    global _llm_client

    if _llm_client is not None and not force_new:
        return _llm_client

    provider = (provider or os.getenv("PORTF_LLM_PROVIDER", "auto")).lower()

    if provider == "auto":
        client = _auto_detect_provider(model)
    elif provider == "gemini":
        client = GeminiLLMClient(model=model)
    elif provider == "ollama":
        client = OllamaLLMClient(model=model)
    elif provider == "openrouter":
        client = OpenRouterLLMClient(model=model)
    elif provider == "anthropic":
        client = AnthropicLLMClient(model=model)
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            "Supported: 'auto', 'gemini', 'ollama', 'openrouter', 'anthropic'"
        )

    _llm_client = client
    return _llm_client


def _auto_detect_provider(model: Optional[str] = None) -> LLMClient:
    """
    Auto-detect the best available LLM provider.

    Priority: Ollama (local, no API key) > Gemini (needs key).
    """
    # Try Ollama first — free, local, no API key
    if OllamaLLMClient.is_available():
        logger.info("Auto-detected Ollama running locally")
        return OllamaLLMClient(model=model)

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

    # Try Anthropic if API key is available
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        logger.info("Auto-detected Anthropic API key, using Claude")
        return AnthropicLLMClient(api_key=anthropic_key, model=model)

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
        model = (
            os.getenv("PORTF_ANTHROPIC_MODEL")
            or os.getenv("PORTF_LLM_MODEL")
            or DEFAULT_ANTHROPIC_MODEL
        )
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

    return {
        "provider": provider,
        "model": model,
        "search_capable": search_capable,
        "singleton_active": _llm_client is not None,
    }
