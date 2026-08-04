"""Unit tests for bulk research refresh: eligibility + the background worker."""

from datetime import date, timedelta

from portf_manager.services.research import get_symbols_needing_refresh


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
