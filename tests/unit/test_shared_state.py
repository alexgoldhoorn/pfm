"""Phase 4: chat history and FX rates are shared via the DB-backed kv_cache.

Chat history must survive a process restart (it used to live in a module dict,
lost on restart and not shared across gunicorn workers). FX rates must be served
from kv_cache so workers don't each re-hit yfinance.
"""

import os
import tempfile
from unittest.mock import patch

from portf_manager import market
from portf_manager.database import Database
from portf_server.routers import llm as llm_router
from portf_server.routers import portfolios as portfolios_router


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return Database(path), path


class TestChatHistoryPersistence:
    def test_history_round_trips_and_trims(self):
        db, path = _fresh_db()
        try:
            sid = "sess-1"
            # Insert more messages than _CHAT_HISTORY_MAX to trigger trimming.
            n = llm_router._CHAT_HISTORY_MAX + 2
            for i in range(n):
                llm_router._append_history(db, sid, "user", f"msg {i}")
            history = llm_router._get_history(db, sid)
            # Trimmed to the configured maximum.
            assert len(history) == llm_router._CHAT_HISTORY_MAX
            # Keeps the most recent messages (ts field is also present but ignored here).
            last = history[-1]
            assert last["role"] == "user"
            assert last["content"] == f"msg {n - 1}"
            assert "ts" in last
        finally:
            os.unlink(path)

    def test_history_survives_restart(self):
        """A new Database on the same file still sees the stored history."""
        db, path = _fresh_db()
        try:
            llm_router._append_history(db, "sess-2", "assistant", "remember me")
            # Simulate a restart / a different worker: brand new handle, same file.
            db2 = Database(path)
            history = llm_router._get_history(db2, "sess-2")
            assert len(history) == 1
            assert history[0]["role"] == "assistant"
            assert history[0]["content"] == "remember me"
            assert "ts" in history[0]
        finally:
            os.unlink(path)

    def test_missing_session_returns_empty(self):
        db, path = _fresh_db()
        try:
            assert llm_router._get_history(db, "never-seen") == []
        finally:
            os.unlink(path)


class TestFxSharedCache:
    def test_rate_cached_in_kv_and_reused_without_yfinance(self):
        db, path = _fresh_db()
        try:
            portfolios_router.set_shared_db(db)
            # Make sure no stale in-process entry masks the test.
            portfolios_router._FX_CACHE.pop("XYZ", None)
            portfolios_router._FX_CACHE_TS.pop("XYZ", None)

            class _FastInfo(dict):
                # market.get_fx_eur uses subscript access: fi["last_price"]
                def __init__(self):
                    super().__init__(last_price=1.25)

            class _Ticker:
                def __init__(self, *_a, **_k):
                    self.fast_info = _FastInfo()

            # market.get_fx_eur now owns the live fetch and the kv_cache write.
            with patch.object(market.yf, "Ticker", _Ticker):
                rate = portfolios_router._get_fx_rate("XYZ")
            assert rate == 1.25
            # Written through to the shared kv_cache layer by market.get_fx_eur.
            cached = db.cache_get("mkt:fx:XYZ")
            assert cached is not None and float(cached["rate"]) == 1.25

            # Drop the in-process entry; a second call must hit kv_cache, not yf.
            portfolios_router._FX_CACHE.pop("XYZ", None)
            portfolios_router._FX_CACHE_TS.pop("XYZ", None)

            def _boom(*_a, **_k):
                raise AssertionError("yfinance should not be called on kv_cache hit")

            with patch.object(market.yf, "Ticker", _boom):
                rate2 = portfolios_router._get_fx_rate("XYZ")
            assert rate2 == 1.25
        finally:
            portfolios_router.set_shared_db(None)
            portfolios_router._FX_CACHE.pop("XYZ", None)
            portfolios_router._FX_CACHE_TS.pop("XYZ", None)
            os.unlink(path)

    def test_eur_is_identity(self):
        assert portfolios_router._get_fx_rate("EUR") == 1.0
