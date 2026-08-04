"""Unit tests for bulk research refresh: eligibility + the background worker."""

from datetime import date, timedelta

import pytest

from portf_manager.services.research import get_symbols_needing_refresh
from portf_server.routers.research import _BULK_RESEARCH, _run_bulk_research_refresh


def _held_asset(db, symbol="AAPL", name="Apple Inc.", qty=10.0, asset_type="stock"):
    aid = db.create_asset(symbol, name, asset_type, currency="USD")
    pid = db.get_or_create_portfolio("TestBroker", base_currency="EUR")
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=qty,
        price=100.0,
        total_amount=qty * 100.0,
        transaction_date=date.today().isoformat(),
        portfolio_id=pid,
        currency="USD",
    )
    return aid


def _age_note(db, note_id, days_ago):
    old_date = (date.today() - timedelta(days=days_ago)).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE research_notes SET created_at = ? WHERE id = ?",
            (old_date, note_id),
        )
        conn.commit()


class TestGetSymbolsNeedingRefresh:
    def test_includes_held_asset_with_no_research(self, test_database):
        _held_asset(test_database)
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL"]
        assert out[0]["asset_id"] is not None
        assert out[0]["name"] == "Apple Inc."

    def test_excludes_held_asset_with_fresh_research(self, test_database):
        aid = _held_asset(test_database)
        test_database.create_research_note(asset_id=aid, symbol="AAPL", thesis="x")
        assert get_symbols_needing_refresh(test_database) == []

    def test_includes_held_asset_with_stale_research(self, test_database):
        aid = _held_asset(test_database)
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=120)
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL"]

    def test_boundary_89_days_excluded_90_days_included(self, test_database):
        aid = _held_asset(test_database)
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=89)
        assert get_symbols_needing_refresh(test_database) == []

        _age_note(test_database, note_id, days_ago=90)
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL"]

    def test_includes_watchlist_only_symbol(self, test_database):
        test_database.add_watchlist(symbol="MSFT", name="Microsoft Corp.")
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["MSFT"]
        assert out[0]["asset_id"] is None
        assert out[0]["name"] == "Microsoft Corp."

    def test_dedupes_symbol_held_and_watchlisted(self, test_database):
        _held_asset(test_database, symbol="AAPL", name="Apple Inc.")
        test_database.add_watchlist(symbol="AAPL", name="Apple Inc.")
        out = get_symbols_needing_refresh(test_database)
        assert len(out) == 1
        assert out[0]["asset_id"] is not None

    def test_empty_when_nothing_held_or_watchlisted(self, test_database):
        assert get_symbols_needing_refresh(test_database) == []

    def test_sorted_by_symbol(self, test_database):
        _held_asset(test_database, symbol="MSFT", name="Microsoft Corp.")
        _held_asset(test_database, symbol="AAPL", name="Apple Inc.")
        out = get_symbols_needing_refresh(test_database)
        assert [c["symbol"] for c in out] == ["AAPL", "MSFT"]


_USABLE_RESULT = {
    "fair_value": 175.0,
    "buy_below": 140.0,
    "sell_above": 200.0,
    "recommendation": "BUY",
    "confidence": "high",
    "summary": "Solid outlook.",
    "rationale": "Strong margins.",
    "risks": [],
    "catalysts": [],
    "sources": [],
}
_NO_DATA_RESULT = {
    "fair_value": None,
    "buy_below": None,
    "sell_above": None,
    "recommendation": "HOLD",
    "confidence": "low",
    "summary": "Could not generate automated analysis for AAPL: boom",
    "rationale": "",
    "risks": [],
    "catalysts": [],
    "sources": [],
}


class TestRunBulkResearchRefresh:
    def test_writes_price_target_for_held_asset(self, test_database, mocker):
        aid = _held_asset(test_database)
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_USABLE_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 140.0
        assert target["sell_above"] == 200.0
        assert _BULK_RESEARCH["running"] is False
        assert _BULK_RESEARCH["done"] == 1
        assert _BULK_RESEARCH["results"][0]["status"] == "updated"

    def test_no_usable_data_does_not_overwrite_existing_target(
        self, test_database, mocker
    ):
        aid = _held_asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid, buy_below=90.0, sell_above=150.0
        )
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=120)
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_NO_DATA_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 90.0
        assert _BULK_RESEARCH["results"][0]["status"] == "no_data"

    def test_overwrites_stale_target_with_new_usable_data(self, test_database, mocker):
        aid = _held_asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid, buy_below=90.0, sell_above=150.0
        )
        note_id = test_database.create_research_note(
            asset_id=aid, symbol="AAPL", thesis="x"
        )
        _age_note(test_database, note_id, days_ago=120)
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_USABLE_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 140.0
        assert target["sell_above"] == 200.0
        assert _BULK_RESEARCH["results"][0]["status"] == "updated"

    def test_one_symbol_error_does_not_abort_batch(self, test_database, mocker):
        _held_asset(test_database, symbol="AAPL", name="Apple Inc.")
        _held_asset(test_database, symbol="MSFT", name="Microsoft Corp.")
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            side_effect=[RuntimeError("boom"), dict(_USABLE_RESULT)],
        )

        _run_bulk_research_refresh(test_database)

        assert _BULK_RESEARCH["done"] == 2
        statuses = {r["symbol"]: r["status"] for r in _BULK_RESEARCH["results"]}
        assert statuses["AAPL"] == "error"
        assert statuses["MSFT"] == "updated"

    def test_watchlist_only_symbol_syncs_buy_zone_not_price_target(
        self, test_database, mocker
    ):
        test_database.add_watchlist(symbol="MSFT", name="Microsoft Corp.")
        mocker.patch(
            "portf_manager.services.research.fetch_fundamentals", return_value={}
        )
        mocker.patch(
            "portf_manager.services.research.fetch_recent_news", return_value=[]
        )
        mocker.patch(
            "portf_manager.services.research.generate_valuation_report",
            return_value=dict(_USABLE_RESULT),
        )

        _run_bulk_research_refresh(test_database)

        watch = next(w for w in test_database.get_watchlist() if w["symbol"] == "MSFT")
        assert watch["buy_below"] == 140.0
        assert _BULK_RESEARCH["results"][0]["status"] == "updated"


class TestBulkRefreshEndpoints:
    @pytest.mark.asyncio
    async def test_start_and_status_endpoints_respond(
        self, async_test_client, auth_headers, test_database, mocker
    ):
        mocker.patch(
            "portf_manager.services.research.get_symbols_needing_refresh",
            return_value=[],
        )
        resp = await async_test_client.post(
            "/api/v1/research/bulk-refresh", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        status_resp = await async_test_client.get(
            "/api/v1/research/bulk-refresh-status", headers=auth_headers
        )
        assert status_resp.status_code == 200
        assert "running" in status_resp.json()
