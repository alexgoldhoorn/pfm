"""Tests for date-accurate FX in the /portfolios/values cash and cost basis
computation — regression coverage for the "cash_eur re-prices a year of
historical trades at today's live rate" bug found while reconciling a real
MyInvestor portfolio against its own EUR-denominated statement."""

from datetime import date as _date

import pytest

from portf_manager.database import Database
from portf_server.routers import portfolios as portfolios_router


class TestGetFxRateOn:
    def test_eur_is_one_without_any_lookup(self):
        # db=None would crash any real lookup — EUR must short-circuit first.
        assert portfolios_router._get_fx_rate_on(None, "EUR", "2024-06-01") == 1.0

    def test_parses_string_dates(self, monkeypatch):
        seen = {}

        def fake_market_fx(db, cur, on_date):
            seen["on_date"] = on_date
            return 0.77, False

        monkeypatch.setattr(portfolios_router.market, "get_fx_eur_on", fake_market_fx)
        assert (
            portfolios_router._get_fx_rate_on(None, "usd", "2024-06-01 15:30:00")
            == 0.77
        )
        assert seen["on_date"] == _date(2024, 6, 1)

    def test_bad_date_falls_back_to_current(self, monkeypatch):
        monkeypatch.setattr(portfolios_router, "_get_fx_rate", lambda cur: 0.5)
        assert portfolios_router._get_fx_rate_on(None, "USD", "not-a-date") == 0.5

    def test_memoizes_non_stale_rate_and_skips_second_lookup(self, monkeypatch):
        monkeypatch.setattr(
            portfolios_router.market,
            "get_fx_eur_on",
            lambda db, cur, d: (0.7, False),
        )
        assert portfolios_router._get_fx_rate_on(None, "USD", "2024-06-02") == 0.7
        assert portfolios_router._FX_HIST_MEMO[("USD", "2024-06-02")] == 0.7

        def _boom(db, cur, d):
            raise AssertionError(
                "market.get_fx_eur_on should not be called on a memo hit"
            )

        monkeypatch.setattr(portfolios_router.market, "get_fx_eur_on", _boom)
        assert portfolios_router._get_fx_rate_on(None, "USD", "2024-06-02") == 0.7

    def test_does_not_memoize_stale_rate(self, monkeypatch):
        monkeypatch.setattr(
            portfolios_router.market,
            "get_fx_eur_on",
            lambda db, cur, d: (0.6, True),
        )
        assert portfolios_router._get_fx_rate_on(None, "USD", "2024-07-16") == 0.6
        assert ("USD", "2024-07-16") not in portfolios_router._FX_HIST_MEMO


class TestPortfolioValuesUsesHistoricalFx:
    """cash_eur/cost_eur must use each transaction's own date's FX rate, not
    today's live rate — otherwise both drift on every page load as the live
    rate ticks, and diverge from what the broker's own EUR statement shows."""

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        database = Database(str(tmp_path / "test.db"))

        # Two different historical USD/EUR rates on two different dates, well
        # away from whatever "live" rate a stray unmocked call would return.
        def fake_fx_on(db_arg, cur, on_date):
            if cur == "USD" and str(on_date) == "2020-01-01":
                return 0.90, False
            if cur == "USD" and str(on_date) == "2020-06-01":
                return 0.80, False
            raise AssertionError(f"unexpected historical FX lookup: {cur} {on_date}")

        monkeypatch.setattr(portfolios_router.market, "get_fx_eur_on", fake_fx_on)
        monkeypatch.setattr(
            portfolios_router,
            "_get_fx_rate",
            lambda cur: 1.0 if cur == "EUR" else 0.5,
        )
        yield database

    def test_cost_eur_uses_each_buy_own_date_rate(self, db):
        pid = db.get_or_create_portfolio("Test Broker")
        asset_id = db.create_asset(
            symbol="TEST", name="Test Stock", asset_type="stock", currency="USD"
        )
        db.create_transaction(
            portfolio_id=pid,
            asset_id=asset_id,
            transaction_type="buy",
            quantity=10,
            price=100.0,
            total_amount=1000.0,
            transaction_date="2020-01-01",
            currency="USD",
        )
        db.create_transaction(
            portfolio_id=pid,
            asset_id=asset_id,
            transaction_type="buy",
            quantity=10,
            price=100.0,
            total_amount=1000.0,
            transaction_date="2020-06-01",
            currency="USD",
        )
        db.insert_price_record(symbol="TEST", price=100.0, fetched_ts="2020-06-01")

        result = portfolios_router.get_portfolio_values(database=db)
        row = next(r for r in result["portfolios"] if r["portfolio_id"] == pid)

        # 1000*0.90 + 1000*0.80 = 1700, NOT 2000*0.5 (today's mocked live rate)
        assert row["cost_eur"] == pytest.approx(1700.0)

    def test_cash_eur_uses_buy_own_date_rate_not_live_rate(self, db):
        pid = db.get_or_create_portfolio("Test Broker 2")
        asset_id = db.create_asset(
            symbol="TEST2", name="Test Stock 2", asset_type="stock", currency="USD"
        )
        db.create_transaction(
            portfolio_id=pid,
            asset_id=asset_id,
            transaction_type="buy",
            quantity=10,
            price=100.0,
            total_amount=1000.0,
            transaction_date="2020-01-01",
            currency="USD",
        )
        db.create_booking(
            portfolio_id=pid,
            date="2020-01-01",
            action="Deposit",
            amount=2000.0,
            currency="EUR",
        )
        db.insert_price_record(symbol="TEST2", price=100.0, fetched_ts="2020-06-01")

        result = portfolios_router.get_portfolio_values(database=db)
        row = next(r for r in result["portfolios"] if r["portfolio_id"] == pid)

        # deposit 2000 (EUR booking, untouched) - buy 1000*0.90 = 1100,
        # NOT 2000 - 1000*0.5 = 1500 (what today's mocked live rate would give)
        assert row["cash_eur"] == pytest.approx(1100.0)
