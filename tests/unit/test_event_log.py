"""Tests for the persistent application log: DB store, handler and API."""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager import event_log
from portf_manager.event_log import DatabaseLogHandler


@pytest.fixture
def handler(test_database):
    """The DB handler attached to the root logger for one test."""
    installed = event_log.install_db_log_handler(test_database)
    yield installed
    event_log.remove_db_log_handler()


def test_log_store_round_trip_and_filters(test_database):
    test_database.log_event("INFO", "portf_manager.llm_client", "ok", event="llm.call")
    test_database.log_event("ERROR", "portf_server.routers.x", "boom", details={"a": 1})
    test_database.log_event("WARNING", "portf_manager.market", "slow", event="mkt.q")

    newest_first = test_database.list_logs()
    assert [r["message"] for r in newest_first] == ["slow", "boom", "ok"]
    assert newest_first[1]["details"] == {"a": 1}

    assert [r["message"] for r in test_database.list_logs(min_level="WARNING")] == [
        "slow",
        "boom",
    ]
    assert [r["message"] for r in test_database.list_logs(event="llm.*")] == ["ok"]
    assert [r["message"] for r in test_database.list_logs(source="portf_server")] == [
        "boom"
    ]
    assert len(test_database.list_logs(limit=1)) == 1


def test_prune_removes_only_old_rows(test_database):
    old_id = test_database.log_event("ERROR", "portf_manager", "old")
    test_database.log_event("ERROR", "portf_manager", "new")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with test_database.get_connection() as conn:
        conn.execute("UPDATE app_logs SET created_at = ? WHERE id = ?", (old, old_id))
        conn.commit()

    assert test_database.prune_logs(30) == 1
    assert [r["message"] for r in test_database.list_logs()] == ["new"]


def test_handler_keeps_pfm_warnings_and_events_only(handler, test_database):
    logging.getLogger("portf_manager.market").warning("price fetch failed")
    logging.getLogger("portf_manager.market").info("routine info")
    logging.getLogger("yfinance").error("third-party noise")
    logging.getLogger("portf_manager.llm_client").info(
        "LLM call", extra={"pfm_event": "llm.call", "pfm_details": {"attempts": 2}}
    )

    rows = test_database.list_logs()
    assert [(r["message"], r["event"]) for r in rows] == [
        ("LLM call", "llm.call"),
        ("price fetch failed", None),
    ]
    assert rows[0]["details"] == {"attempts": 2}


def test_handler_records_exceptions_and_truncates(handler, test_database):
    try:
        raise ValueError("bad value")
    except ValueError:
        logging.getLogger("portf_server.x").exception("x" * 5000)

    (row,) = test_database.list_logs()
    assert len(row["message"]) < 2100
    assert "more chars" in row["message"]
    assert "bad value" in row["details"]["exception"]


def test_handler_never_raises_when_the_store_fails(test_database, monkeypatch):
    handler = DatabaseLogHandler(test_database)

    def broken(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(test_database, "log_event", broken)
    record = logging.LogRecord("portf_manager", logging.ERROR, "", 0, "m", None, None)
    handler.emit(record)


def test_install_skips_a_backend_without_a_log_store():
    assert event_log.install_db_log_handler(object()) is None


def test_install_replaces_the_previous_handler(test_database):
    first = event_log.install_db_log_handler(test_database)
    second = event_log.install_db_log_handler(test_database)
    try:
        root_handlers = logging.getLogger().handlers
        assert second in root_handlers and first not in root_handlers
    finally:
        event_log.remove_db_log_handler()


def test_llm_failure_lands_in_the_log(handler, test_database, monkeypatch):
    import requests

    from portf_manager.llm_client import LLMError, OllamaLLMClient

    def refused(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr("portf_manager.llm_client.requests.post", refused)
    with pytest.raises(LLMError):
        OllamaLLMClient(model="m").generate("hi")

    (row,) = test_database.list_logs(event="llm.call")
    assert row["level"] == "ERROR"
    assert row["details"]["attempts"] == 3
    assert row["details"]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_logs_endpoint(
    async_test_client: AsyncClient, auth_headers, test_database
):
    test_database.log_event("INFO", "portf_manager.llm_client", "ok", event="llm.call")
    test_database.log_event("ERROR", "portf_server.x", "boom")

    resp = await async_test_client.get(
        "/api/v1/system/logs?level=ERROR", headers=auth_headers
    )
    assert resp.status_code == status.HTTP_200_OK
    assert [r["message"] for r in resp.json()["items"]] == ["boom"]

    bad = await async_test_client.get(
        "/api/v1/system/logs?level=LOUD", headers=auth_headers
    )
    assert bad.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_logs_endpoint_requires_auth(async_test_client: AsyncClient):
    resp = await async_test_client.get("/api/v1/system/logs")
    assert resp.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


def test_migration_adds_app_logs_to_a_v30_database(tmp_path):
    import sqlite3

    from portf_manager.database import Database

    path = str(tmp_path / "v30.db")
    Database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE app_logs")
        conn.execute("UPDATE database_version SET version = 30")
    db = Database(path)
    db.log_event("ERROR", "portf_manager", "after upgrade")
    assert db.list_logs()[0]["message"] == "after upgrade"
