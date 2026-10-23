"""Chat features behind the web chat UI: model tiers, tool tracking, auto-title.

The engine is driven with a mocked LLM; nothing here reaches a provider.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from portf_manager.database import Database
from portf_manager.llm_client import LLMError, ToolCallRequest, ToolResponse
from portf_server.routers import llm as llm_router
from portf_server.routers.llm import (
    ChatRequest,
    EnhancedChatEngine,
    clean_chat_title,
    is_default_session_name,
)


def _tool_capable_mock(answer: str = "Done.", tool: str = "get_kpis") -> MagicMock:
    """A ToolCapableLLMClient stand-in that requests one tool, then answers."""
    m = MagicMock()
    m.model_name = "mock-model"
    m.generate_with_tools.return_value = ToolResponse(
        tool_call=ToolCallRequest(name=tool, arguments={"portfolio_id": "p1"})
    )
    m.complete_with_tool_result.return_value = answer
    m.generate.return_value = "Portfolio KPIs"
    m.__class__ = type(
        "ToolCapableMock",
        (MagicMock,),
        {
            "generate": m.generate,
            "generate_with_tools": m.generate_with_tools,
            "complete_with_tool_result": m.complete_with_tool_result,
        },
    )
    return m


def _engine(mock_llm: MagicMock) -> EnhancedChatEngine:
    with (
        patch("portf_server.routers.llm.get_llm_client", return_value=mock_llm),
        patch("portf_server.routers.llm.get_market_data_service"),
        patch("portf_server.routers.llm.get_stock_screener"),
        patch("portf_server.routers.llm.get_technical_analysis_engine"),
        patch("portf_server.routers.llm.get_fundamental_analysis_engine"),
    ):
        engine = EnhancedChatEngine()
    engine.llm = mock_llm
    return engine


def _run(engine, db, message, session_id, tier=None, clients=None):
    """Run one chat turn with the tool-capable check and the factory patched."""
    factory = MagicMock(side_effect=lambda tier=None, **kw: (clients or {}).get(tier))
    if clients is None:
        factory = MagicMock(return_value=engine.llm)
    with (
        patch(
            "portf_manager.llm_client.ToolCapableLLMClient.__instancecheck__",
            return_value=True,
        ),
        patch("portf_server.routers.llm.get_llm_client", factory),
        patch("portf_server.routers.llm.execute_tool", return_value='{"ok": 1}'),
    ):
        req = ChatRequest(message=message, session_id=session_id, tier=tier)
        return asyncio.run(engine.process_chat_request(req, db)), factory


class TestTitleHelpers:
    @pytest.mark.parametrize("name", ["New Chat", "New Chat 1", " New Chat 12 "])
    def test_default_names(self, name):
        assert is_default_session_name(name)

    @pytest.mark.parametrize("name", ["Research: EXA", "New Chat about tax", "", None])
    def test_user_names(self, name):
        assert not is_default_session_name(name)

    def test_clean_title_strips_quotes_prefix_and_period(self):
        assert clean_chat_title('Title: "Dividend income review."') == (
            "Dividend income review"
        )

    def test_clean_title_keeps_first_line_only(self):
        assert clean_chat_title("\nTax reserve check\nSecond line") == (
            "Tax reserve check"
        )

    def test_clean_title_caps_at_word_boundary(self):
        long = "word " * 30
        title = clean_chat_title(long)
        assert len(title) <= 60
        assert not title.endswith(" ")

    def test_clean_title_empty(self):
        assert clean_chat_title("  \n ") == ""


class TestAutoTitle:
    def test_first_exchange_names_a_default_thread(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "New Chat 2")
        engine = _engine(_tool_capable_mock())
        result, _ = _run(engine, db, "How are my KPIs?", "s1")
        assert result.session_name == "Portfolio KPIs"
        assert db.get_chat_session("s1")["name"] == "Portfolio KPIs"
        prompt = engine.llm.generate.call_args.args[0]
        assert "How are my KPIs?" in prompt and "Done." in prompt

    def test_user_named_thread_is_left_alone(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Research: EXA")
        engine = _engine(_tool_capable_mock())
        result, _ = _run(engine, db, "Hi", "s1")
        assert result.session_name == "Research: EXA"
        engine.llm.generate.assert_not_called()

    def test_second_exchange_does_not_retitle(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "New Chat")
        engine = _engine(_tool_capable_mock())
        engine.llm.generate.return_value = ""
        _run(engine, db, "first", "s1")
        assert db.get_chat_session("s1")["name"] == "New Chat"
        engine.llm.generate.return_value = "Late title"
        result, _ = _run(engine, db, "second", "s1")
        assert result.session_name == "New Chat"
        assert engine.llm.generate.call_count == 1

    def test_title_failure_keeps_the_answer(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "New Chat")
        engine = _engine(_tool_capable_mock(answer="Fine."))
        engine.llm.generate.side_effect = LLMError(
            "Mock", "mock-model", "generate", 3, RuntimeError("boom")
        )
        result, _ = _run(engine, db, "q", "s1")
        assert result.answer == "Fine."
        assert result.session_name == "New Chat"
        assert db.get_chat_session("s1")["messages"][-1]["content"] == "Fine."


class TestTierAndTools:
    def test_default_tier_uses_the_engine_client(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        engine = _engine(_tool_capable_mock())
        result, factory = _run(engine, db, "q", "s1")
        assert result.tier == "heavy"
        # Only the title call asked the factory for a client.
        assert [c.kwargs.get("tier") for c in factory.call_args_list] == ["fast"]

    def test_requested_tier_picks_that_client(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        heavy = _tool_capable_mock(answer="heavy answer")
        fast = _tool_capable_mock(answer="fast answer")
        fast.model_name = "fast-model"
        engine = _engine(heavy)
        result, _ = _run(engine, db, "q", "s1", tier="fast", clients={"fast": fast})
        assert result.answer == "fast answer"
        assert result.tier == "fast"
        assert result.model == "fast-model"
        heavy.generate_with_tools.assert_not_called()
        assert db.get_chat_session("s1")["messages"][-1]["tier"] == "fast"

    def test_unknown_tier_is_422(self, tmp_path):
        from fastapi import HTTPException

        db = Database(str(tmp_path / "t.db"))
        engine = _engine(_tool_capable_mock())
        with pytest.raises(HTTPException) as exc:
            _run(engine, db, "q", "s1", tier="turbo")
        assert exc.value.status_code == 422
        assert "turbo" in exc.value.detail

    def test_tools_used_records_name_arguments_and_outcome(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        engine = _engine(_tool_capable_mock())
        result, _ = _run(engine, db, "q", "s1")
        (use,) = result.tools_used
        assert use.name == "get_kpis"
        assert use.arguments == {"portfolio_id": "p1"}
        assert use.ok is True
        assert use.ms >= 0

    def test_tool_error_is_flagged(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        engine = _engine(_tool_capable_mock())
        with (
            patch(
                "portf_manager.llm_client.ToolCapableLLMClient.__instancecheck__",
                return_value=True,
            ),
            patch("portf_server.routers.llm.get_llm_client", return_value=engine.llm),
            patch(
                "portf_server.routers.llm.execute_tool",
                return_value="Error: unknown tool 'get_kpis'",
            ),
        ):
            req = ChatRequest(message="q", session_id="s1")
            result = asyncio.run(engine.process_chat_request(req, db))
        assert result.tools_used[0].ok is False

    def test_run_tool_loop_path_collects_every_call(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))

        class LoopClient:
            """Anthropic-shaped client: run_tool_loop runs several tools."""

            model_name = "loop-model"

            def __init__(self):
                self.generate = MagicMock(return_value="Loop title")

            def generate_with_tools(self, messages, tools):
                raise AssertionError("the loop path must not use the two-pass API")

            def complete_with_tool_result(self, *args):
                raise AssertionError("the loop path must not use the two-pass API")

            def run_tool_loop(self, messages, tools, execute, max_rounds=6):
                execute("get_kpis", {})
                execute("get_holdings", {"portfolio_id": "p1"})
                return "Two tools."

        engine = _engine(LoopClient())
        result, _ = _run(engine, db, "q", "s1")
        assert result.answer == "Two tools."
        assert [t.name for t in result.tools_used] == ["get_kpis", "get_holdings"]
        assert result.tools_used[1].arguments == {"portfolio_id": "p1"}

    def test_plain_client_has_no_tools(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        plain = MagicMock()
        plain.model_name = "plain"
        plain.generate.return_value = "Static answer."
        engine = _engine(plain)
        with patch("portf_server.routers.llm.get_llm_client", return_value=plain):
            req = ChatRequest(message="q", session_id="s1")
            result = asyncio.run(engine.process_chat_request(req, db))
        assert result.answer == "Static answer."
        assert result.tools_used == []


@pytest.mark.asyncio
class TestChatModelsEndpoint:
    async def test_anthropic_lists_three_tiers(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        from portf_manager.llm_client import AnthropicLLMClient

        monkeypatch.setenv("PORTF_ANTHROPIC_FAST_MODEL", "fast-x")
        client = AnthropicLLMClient(api_key="k", tier="main")
        with patch.object(llm_router, "get_llm_client", return_value=client):
            r = await async_test_client.get(
                "/api/v1/llm/chat/models", headers=auth_headers
            )
        assert r.status_code == 200
        data = r.json()
        assert data["provider"] == "Anthropic"
        assert data["default_tier"] == "heavy"
        assert data["tiered"] is True
        by_tier = {t["tier"]: t["model"] for t in data["tiers"]}
        assert by_tier["fast"] == "fast-x"
        assert set(by_tier) == {"fast", "main", "heavy"}

    async def test_other_provider_shows_one_model(
        self, async_test_client: AsyncClient, auth_headers
    ):
        client = MagicMock()
        client.model_name = "llama3.2"
        type(client).__name__ = "OllamaLLMClient"
        with patch.object(llm_router, "get_llm_client", return_value=client):
            r = await async_test_client.get(
                "/api/v1/llm/chat/models", headers=auth_headers
            )
        assert r.status_code == 200
        data = r.json()
        assert data["tiered"] is False
        assert {t["model"] for t in data["tiers"]} == {"llama3.2"}

    async def test_no_provider_lists_no_tiers(
        self, async_test_client: AsyncClient, auth_headers
    ):
        with patch.object(
            llm_router, "get_llm_client", side_effect=RuntimeError("no provider")
        ):
            r = await async_test_client.get(
                "/api/v1/llm/chat/models", headers=auth_headers
            )
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is False
        assert data["tiers"] == []
        assert "no provider" in data["error"]

    async def test_messages_endpoint_returns_turn_metadata(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        create = await async_test_client.post(
            "/api/v1/llm/chat/sessions", json={"name": "Meta"}, headers=auth_headers
        )
        sid = create.json()["id"]
        db = test_database
        llm_router._append_history(db, sid, "user", "q")
        llm_router._append_history(
            db,
            sid,
            "assistant",
            "a",
            {"model": "m", "tier": "fast", "tools_used": [{"name": "get_kpis"}]},
        )
        r = await async_test_client.get(
            f"/api/v1/llm/chat/sessions/{sid}/messages", headers=auth_headers
        )
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert "model" not in msgs[0]
        assert msgs[1]["model"] == "m"
        assert msgs[1]["tools_used"][0]["name"] == "get_kpis"
