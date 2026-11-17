"""Regression tests for the 2026-11 review fixes: failures surface as errors."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from portf_manager.gemini_client import GeminiClient, _as_list
from portf_manager.llm_client import LLMResponseError, ToolResponse
from portf_server.auth_middleware import APIKeyManager


class TestAsList:
    def test_unwraps_a_single_list_wrapper(self):
        assert _as_list({"bookings": [{"a": 1}]}, "x") == [{"a": 1}]

    def test_single_object_becomes_one_item(self):
        assert _as_list({"a": 1}, "x") == [{"a": 1}]

    def test_drops_non_dict_items(self):
        assert _as_list([{"a": 1}, "junk", 3], "x") == [{"a": 1}]

    def test_other_shapes_raise(self):
        with pytest.raises(LLMResponseError):
            _as_list("text", "x")


def test_extract_bookings_reads_a_wrapped_reply():
    llm = MagicMock()
    llm.generate.return_value = (
        '{"bookings": [{"date": "2026-01-05", "action": "deposit", '
        '"amount": 100, "currency": "EUR"}]}'
    )
    rows = GeminiClient(llm=llm).extract_bookings("statement text")
    assert len(rows) == 1
    assert rows[0]["action"] == "Deposit"


def test_validate_api_key_db_fault_is_503_not_invalid():
    db = MagicMock()
    db.get_connection.side_effect = RuntimeError("database is locked")
    with pytest.raises(HTTPException) as exc:
        APIKeyManager(db).validate_api_key("some-key")
    assert exc.value.status_code == 503


def _engine_with(llm):
    with (
        patch("portf_server.routers.llm.get_llm_client", return_value=llm),
        patch("portf_server.routers.llm.get_market_data_service"),
        patch("portf_server.routers.llm.get_stock_screener"),
        patch("portf_server.routers.llm.get_technical_analysis_engine"),
        patch("portf_server.routers.llm.get_fundamental_analysis_engine"),
    ):
        from portf_server.routers.llm import EnhancedChatEngine

        engine = EnhancedChatEngine()
        engine.llm = llm
    return engine


def test_two_pass_chat_without_text_or_tool_raises(tmp_path):
    from portf_manager.database import Database

    # A plain class without run_tool_loop, so the two-pass path is taken.
    class TwoPass:
        def generate_with_tools(self, messages, tools):
            return ToolResponse(text="")

    llm = TwoPass()
    engine = _engine_with(llm)
    db = Database(str(tmp_path / "t.db"))
    with pytest.raises(LLMResponseError):
        asyncio.run(engine._generate_with_tool_loop("hi", {}, "s1", db, llm, {}))


def test_cache_delete_is_exact_and_clear_prefix_is_literal(tmp_path):
    from portf_manager.database import Database

    db = Database(str(tmp_path / "c.db"))
    for k in ("adv:1", "adv:10", "a_b:x", "axb:x"):
        db.cache_set(k, 1, 3600)
    assert db.cache_delete("adv:1") == 1
    assert db.cache_get("adv:10") == 1
    # "_" is a LIKE wildcard; it must match only itself.
    assert db.cache_clear("a_b") == 1
    assert db.cache_get("axb:x") == 1


def test_settings_errors_propagate_but_missing_table_heals(tmp_path):
    import sqlite3

    from portf_manager.database import Database

    db = Database(str(tmp_path / "s.db"))
    with db.get_connection() as conn:
        conn.execute("DROP TABLE app_settings")
        conn.commit()
    assert db.get_setting("k") is None
    db.set_setting("k", "v")
    assert db.get_setting("k") == "v"

    with patch.object(
        db, "get_connection", side_effect=sqlite3.OperationalError("database is locked")
    ):
        with pytest.raises(sqlite3.OperationalError):
            db.get_setting("k")
