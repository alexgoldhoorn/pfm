"""Chat deep-dive features: follow-ups, web search, sources, thread reports."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.llm_client import LLMError
from portf_server.routers.llm import (
    ChatRequest,
    EnhancedChatEngine,
    report_title,
    split_followups,
)

_KEY = "test-key-deepdive-abc123"
HEADERS = {"X-API-Key": _KEY}


class TestSplitFollowups:
    def test_trailing_block_is_cut_and_parsed(self):
        text = 'Answer.\n\n```followups\n["What about rent?", "And food?"]\n```\n'
        assert split_followups(text) == ("Answer.", ["What about rent?", "And food?"])

    def test_no_block_leaves_the_text(self):
        assert split_followups("Just an answer.") == ("Just an answer.", [])

    def test_malformed_block_is_dropped(self):
        assert split_followups("A.\n```followups\nnot json\n```") == ("A.", [])
        assert split_followups('A.\n```followups\n{"a": 1}\n```') == ("A.", [])

    def test_keeps_at_most_four_short_strings(self):
        items = ["q1", "", 7, "x" * 200, "q2", "q3", "q4", "q5"]
        import json

        body, out = split_followups(f"A.\n```followups\n{json.dumps(items)}\n```")
        assert body == "A." and out == ["q1", "q2", "q3", "q4"]

    def test_a_block_in_the_middle_is_not_touched(self):
        text = '```followups\n["q"]\n```\nmore text'
        assert split_followups(text) == (text, [])

    def test_chart_block_before_it_survives(self):
        text = '```chart\n{"type":"bar"}\n```\n```followups\n["q"]\n```'
        assert split_followups(text) == ('```chart\n{"type":"bar"}\n```', ["q"])


def test_report_title():
    assert report_title("# Money review\n\nBody", "Fallback") == "Money review"
    assert report_title("\n\n# Late title\n", "Fallback") == "Late title"
    assert report_title("Body first\n# Not a title", "Fallback") == "Fallback"
    assert report_title("", "Fallback") == "Fallback"


class LoopClient:
    """Anthropic-shaped client whose loop reports what it was asked to do."""

    model_name = "loop-model"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.generate = MagicMock(return_value="A title")

    def generate_with_tools(self, messages, tools):
        raise AssertionError("the loop path must not use the two-pass API")

    def complete_with_tool_result(self, *args):
        raise AssertionError("the loop path must not use the two-pass API")

    def run_tool_loop(
        self, messages, tools, execute, max_rounds=6, web_search=False, sources=None
    ):
        self.calls.append(
            {"messages": messages, "max_rounds": max_rounds, "web_search": web_search}
        )
        if sources is not None:
            sources.append(
                {"title": "Example comparison", "url": "https://example.com/a"}
            )
            sources.append({"title": "Bad", "url": "javascript:alert(1)"})
        return self.answer


def _engine(llm) -> EnhancedChatEngine:
    with (
        patch("portf_server.routers.llm.get_llm_client", return_value=llm),
        patch("portf_server.routers.llm.get_market_data_service"),
        patch("portf_server.routers.llm.get_stock_screener"),
        patch("portf_server.routers.llm.get_technical_analysis_engine"),
        patch("portf_server.routers.llm.get_fundamental_analysis_engine"),
    ):
        engine = EnhancedChatEngine()
    engine.llm = llm
    return engine


def _turn(engine, db, message, session_id="s1", **req):
    with (
        patch(
            "portf_manager.llm_client.ToolCapableLLMClient.__instancecheck__",
            return_value=True,
        ),
        patch("portf_server.routers.llm.get_llm_client", return_value=engine.llm),
    ):
        request = ChatRequest(message=message, session_id=session_id, **req)
        return asyncio.run(engine.process_chat_request(request, db))


class TestChatTurn:
    def test_followups_are_returned_stored_and_cut_from_the_answer(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        llm = LoopClient('You last 9 months.\n```followups\n["And with cuts?"]\n```')
        result = _turn(_engine(llm), db, "what if I lose my job")
        assert result.answer == "You last 9 months."
        assert result.followups == ["And with cuts?"]
        assert result.sources == []
        stored = db.get_chat_session("s1")["messages"][-1]
        assert stored["content"] == "You last 9 months."
        assert stored["followups"] == ["And with cuts?"]
        assert stored["sources"] == []

    def test_web_search_is_off_by_default(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        llm = LoopClient("Answer.")
        _turn(_engine(llm), db, "q")
        call = llm.calls[0]
        assert call["web_search"] is False
        assert "Web search is ON" not in call["messages"][0]["content"]
        assert call["max_rounds"] == 10

    def test_web_search_passes_sources_through(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        llm = LoopClient("Cheaper insurers exist.")
        result = _turn(_engine(llm), db, "find cheaper insurance", web_search=True)
        call = llm.calls[0]
        assert call["web_search"] is True
        system = call["messages"][0]["content"]
        assert "Web search is ON" in system and "PRIVACY" in system
        # only http(s) links reach the client
        assert [s.url for s in result.sources] == ["https://example.com/a"]
        stored = db.get_chat_session("s1")["messages"][-1]
        assert stored["sources"] == [
            {"title": "Example comparison", "url": "https://example.com/a"}
        ]

    def test_system_prompt_names_the_scenario_tools(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        llm = LoopClient("Answer.")
        _turn(_engine(llm), db, "q")
        system = llm.calls[0]["messages"][0]["content"]
        for name in (
            "run_job_loss_scenario",
            "run_income_change_scenario",
            "run_house_purchase_scenario",
            "run_market_crash_scenario",
            "get_spending_cut_candidates",
            "get_spending_matrix",
        ):
            assert name in system

    def test_twelve_earlier_messages_are_replayed(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        llm = LoopClient("Answer.")
        engine = _engine(llm)
        for i in range(8):
            _turn(engine, db, f"question {i}")
        roles = [m["role"] for m in llm.calls[-1]["messages"]]
        # system + 12 replayed + the new question
        assert len(roles) == 14 and roles[0] == "system" and roles[-1] == "user"


class TestAnthropicLoopWebSearch:
    @staticmethod
    def _client():
        from portf_manager.llm_client import AnthropicLLMClient

        return AnthropicLLMClient(api_key="k", tier="heavy")

    @staticmethod
    def _search_result(url, title):
        block = MagicMock(type="web_search_tool_result")
        block.content = [MagicMock(url=url, title=title)]
        block.model_dump.return_value = {"type": "web_search_tool_result"}
        return block

    @staticmethod
    def _text(value):
        block = MagicMock(type="text", text=value)
        block.model_dump.return_value = {"type": "text", "text": value}
        return block

    def _run(self, replies, **kwargs):
        from portf_manager.llm_client import ToolDefinition

        client = self._client()
        tools = [ToolDefinition(name="get_kpis", description=".", parameters=[])]
        with patch("anthropic.Anthropic") as sdk:
            create = sdk.return_value.messages.create
            create.side_effect = [
                MagicMock(content=blocks, stop_reason=stop) for blocks, stop in replies
            ]
            answer = client.run_tool_loop(
                [{"role": "user", "content": "q"}], tools, lambda n, a: "{}", **kwargs
            )
        return answer, [c.kwargs for c in create.call_args_list]

    def test_search_tool_is_added_and_sources_collected(self):
        sources = []
        answer, calls = self._run(
            [
                (
                    [
                        self._search_result("https://example.com/a", "Page A"),
                        self._search_result("https://example.com/a", "Page A again"),
                        self._search_result("https://example.com/b", ""),
                        self._text("Found two offers."),
                    ],
                    "end_turn",
                )
            ],
            web_search=True,
            sources=sources,
        )
        assert answer == "Found two offers."
        assert calls[0]["tools"][-1] == {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 5,
        }
        assert sources == [
            {"title": "Page A", "url": "https://example.com/a"},
            {"title": "https://example.com/b", "url": "https://example.com/b"},
        ]

    def test_without_web_search_no_server_tool_is_sent(self):
        _, calls = self._run([([self._text("Hi.")], "end_turn")])
        assert all(t.get("name") != "web_search" for t in calls[0]["tools"])

    def test_paused_turn_is_resumed_without_using_a_round(self):
        sources = []
        answer, calls = self._run(
            [
                ([self._search_result("https://example.com/a", "A")], "pause_turn"),
                ([self._text("Done.")], "end_turn"),
            ],
            max_rounds=1,
            web_search=True,
            sources=sources,
        )
        assert answer == "Done."
        assert len(calls) == 2
        # the paused assistant turn is replayed, and tools stay allowed
        assert calls[1]["messages"][-1]["role"] == "assistant"
        assert calls[1]["tool_choice"] == {"type": "auto"}
        assert len(sources) == 1


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "api.db"))


@pytest.fixture
def client(db):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestSavedReports:
    def test_crud(self, client):
        base = "/api/v1/saved-reports/"
        assert client.get(base, headers=HEADERS).json() == {"items": []}

        r = client.post(
            base,
            json={
                "title": "  Money   review ",
                "content_md": "# Hi",
                "source_session_id": "s1",
            },
            headers=HEADERS,
        )
        assert r.status_code == 201
        report = r.json()
        assert report["title"] == "Money review"
        assert report["content_md"] == "# Hi"
        assert report["source_session_id"] == "s1"
        rid = report["id"]

        items = client.get(base, headers=HEADERS).json()["items"]
        assert [i["id"] for i in items] == [rid]
        assert "content_md" not in items[0]

        r = client.put(f"{base}{rid}", json={"title": "Renamed"}, headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed" and r.json()["content_md"] == "# Hi"

        r = client.put(f"{base}{rid}", json={"content_md": "New body"}, headers=HEADERS)
        assert r.json()["title"] == "Renamed" and r.json()["content_md"] == "New body"

        assert client.delete(f"{base}{rid}", headers=HEADERS).status_code == 204
        assert client.get(f"{base}{rid}", headers=HEADERS).status_code == 404
        assert client.delete(f"{base}{rid}", headers=HEADERS).status_code == 404
        assert (
            client.put(f"{base}{rid}", json={"title": "x"}, headers=HEADERS).status_code
            == 404
        )

    def test_validation_and_auth(self, client):
        base = "/api/v1/saved-reports/"
        bad = client.post(base, json={"title": "", "content_md": "x"}, headers=HEADERS)
        assert bad.status_code == 422
        bad = client.post(
            base, json={"title": "   ", "content_md": "x"}, headers=HEADERS
        )
        assert bad.status_code == 422
        bad = client.post(base, json={"title": "t"}, headers=HEADERS)
        assert bad.status_code == 422
        assert client.get(base).status_code in (401, 403)

    def test_the_pdf_report_route_is_still_there(self, client):
        from portf_server.app import app

        paths = {route.path for route in app.routes}
        assert "/api/v1/reports/portfolio" in paths
        assert "/api/v1/saved-reports/{report_id}" in paths


class TestThreadReport:
    URL = "/api/v1/llm/chat/sessions/{}/report"

    def _thread(self, db):
        db.create_chat_session("s1", "Job loss check")
        db.update_chat_session_activity(
            "s1",
            [
                {"role": "user", "content": "What if I lose my job?"},
                {"role": "assistant", "content": "You last 9 months."},
            ],
        )

    def test_writes_and_saves_a_report(self, client, db):
        self._thread(db)
        llm = MagicMock()
        llm.generate.return_value = (
            "# Runway after a job loss\n\n## Summary\n- 9 months"
        )
        with patch("portf_server.routers.llm.get_llm_client", return_value=llm) as get:
            r = client.post(self.URL.format("s1"), json={}, headers=HEADERS)
        assert r.status_code == 200
        report = r.json()
        assert report["title"] == "Runway after a job loss"
        assert report["source_session_id"] == "s1"
        assert report["content_md"].startswith("# Runway")
        assert get.call_args.kwargs == {"tier": "heavy"}
        prompt = llm.generate.call_args.args[0]
        assert "USER:\nWhat if I lose my job?" in prompt
        assert "ASSISTANT:\nYou last 9 months." in prompt
        assert db.get_report(report["id"])["title"] == "Runway after a job loss"

    def test_given_title_wins_and_no_heading_falls_back_to_the_thread_name(
        self, client, db
    ):
        self._thread(db)
        llm = MagicMock()
        llm.generate.return_value = "No heading here."
        with patch("portf_server.routers.llm.get_llm_client", return_value=llm):
            r = client.post(
                self.URL.format("s1"), json={"title": "Mine"}, headers=HEADERS
            )
            assert r.json()["title"] == "Mine"
            r = client.post(self.URL.format("s1"), headers=HEADERS)
            assert r.json()["title"] == "Job loss check"

    def test_llm_failure_is_a_502_and_saves_nothing(self, client, db):
        self._thread(db)
        llm = MagicMock()
        llm.generate.side_effect = LLMError(
            "Anthropic", "m", "generate", 3, RuntimeError("overloaded")
        )
        with patch("portf_server.routers.llm.get_llm_client", return_value=llm):
            r = client.post(self.URL.format("s1"), json={}, headers=HEADERS)
        assert r.status_code == 502
        assert "overloaded" in r.json()["detail"]
        assert db.list_reports() == []

    def test_unknown_thread_empty_thread_and_bad_tier(self, client, db):
        assert client.post(self.URL.format("nope"), headers=HEADERS).status_code == 404
        db.create_chat_session("s2", "Empty")
        assert client.post(self.URL.format("s2"), headers=HEADERS).status_code == 400
        self._thread(db)
        r = client.post(self.URL.format("s1"), json={"tier": "huge"}, headers=HEADERS)
        assert r.status_code == 422


class TestCategoryEssentiality:
    @pytest.fixture
    def cats(self, db):
        spend = db.find_spending_category_by_name("Spend")["id"]
        income = db.find_spending_category_by_name("Income")["id"]
        leisure = db.create_spending_category("Leisure", spend)
        db.create_spending_category("Cinema", leisure)
        db.create_spending_category("Rent", spend)
        db.create_spending_category("Salary", income)
        return db

    def test_set_clear_and_tree(self, client, cats):
        url = "/api/v1/spending/categories/{}/essentiality"
        r = client.put(
            url.format("Rent"), json={"essentiality": "fixed"}, headers=HEADERS
        )
        assert r.status_code == 200
        assert r.json() == {"name": "Rent", "essentiality": "fixed"}
        tree = client.get("/api/v1/spending/categories/tree", headers=HEADERS).json()
        by_name = {row["name"]: row for row in tree}
        assert by_name["Rent"]["essentiality"] == "fixed"
        assert by_name["Cinema"]["essentiality"] is None

        r = client.put(url.format("Rent"), json={"essentiality": None}, headers=HEADERS)
        assert r.json()["essentiality"] is None
        assert cats.find_spending_category_by_name("Rent")["essentiality"] is None

    def test_rejections(self, client, cats):
        url = "/api/v1/spending/categories/{}/essentiality"
        r = client.put(
            url.format("Rent"), json={"essentiality": "vital"}, headers=HEADERS
        )
        assert r.status_code == 422
        r = client.put(
            url.format("Nope"), json={"essentiality": "fixed"}, headers=HEADERS
        )
        assert r.status_code == 404
        r = client.put(
            url.format("Spend"), json={"essentiality": "fixed"}, headers=HEADERS
        )
        assert r.status_code == 400

    def test_suggestions_cover_only_unmarked_spend_categories(self, client, cats):
        cats.set_spending_category_essentiality("Rent", "fixed")
        llm = MagicMock()
        llm.generate.return_value = (
            '```json\n[{"name": "Leisure", "essentiality": "optional", "reason": "fun"},'
            ' {"name": "Cinema", "essentiality": "luxury"},'
            ' {"name": "Invented", "essentiality": "fixed"}]\n```'
        )
        with patch(
            "portf_server.routers.spending.get_llm_client", return_value=llm
        ) as get:
            r = client.post(
                "/api/v1/spending/categories/suggest-essentiality", headers=HEADERS
            )
        assert r.status_code == 200
        assert r.json() == {
            "suggestions": [
                {"name": "Leisure", "essentiality": "optional", "reason": "fun"}
            ]
        }
        assert get.call_args.kwargs == {"tier": "fast"}
        prompt = llm.generate.call_args.args[0]
        assert "- Cinema (Leisure)" in prompt and "- Leisure (Spend)" in prompt
        assert "- Rent" not in prompt and "Salary" not in prompt
        # nothing is saved by a suggestion
        assert cats.find_spending_category_by_name("Leisure")["essentiality"] is None

    def test_suggestions_with_nothing_pending_skip_the_llm(self, client, db):
        with patch("portf_server.routers.spending.get_llm_client") as get:
            r = client.post(
                "/api/v1/spending/categories/suggest-essentiality", headers=HEADERS
            )
        assert r.json() == {"suggestions": []}
        get.assert_not_called()

    def test_suggestion_failure_is_a_502(self, client, cats):
        llm = MagicMock()
        llm.generate.side_effect = RuntimeError("provider down")
        with patch("portf_server.routers.spending.get_llm_client", return_value=llm):
            r = client.post(
                "/api/v1/spending/categories/suggest-essentiality", headers=HEADERS
            )
        assert r.status_code == 502 and "provider down" in r.json()["detail"]
