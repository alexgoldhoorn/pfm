"""Tests for portf_manager.market — shared market-data service."""

import time
from datetime import date, datetime, timedelta

import pytest

from portf_manager import market
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def _patch_ticker(monkeypatch, fast_info=None, raise_exc=False):
    """Replace market.yf.Ticker with a fake returning *fast_info* (a dict)."""

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def fast_info(self):
            if raise_exc:
                raise RuntimeError("yahoo down")
            return fast_info

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)


class TestGetQuote:
    def test_live_fetch_stores_and_returns(self, db, monkeypatch):
        _patch_ticker(
            monkeypatch,
            {"last_price": 100.0, "previous_close": 98.0, "currency": "USD"},
        )
        q = market.get_quote(db, "NVDA", max_age=60)
        assert q["price"] == 100.0
        assert q["prev_close"] == 98.0
        assert q["change_pct"] == pytest.approx(2.04, abs=0.01)
        assert q["currency"] == "USD"
        assert q["source"] == "live"
        assert q["stale"] is False
        # Second call within max_age must not hit yfinance at all.
        _patch_ticker(monkeypatch, raise_exc=True)
        q2 = market.get_quote(db, "NVDA", max_age=3600)
        assert q2["price"] == 100.0
        assert q2["source"] == "cache"
        assert q2["stale"] is False

    def test_expired_cache_refetches(self, db, monkeypatch):
        db.cache_set(
            "mkt:quote:NVDA",
            {
                "symbol": "NVDA",
                "price": 90.0,
                "prev_close": 89.0,
                "change_pct": 1.12,
                "currency": "USD",
                "name": None,
                "fetched_at": time.time() - 90000,
            },
            7 * 24 * 3600,
        )
        _patch_ticker(
            monkeypatch,
            {"last_price": 100.0, "previous_close": 98.0, "currency": "USD"},
        )
        q = market.get_quote(db, "NVDA", max_age=86400)
        assert q["price"] == 100.0
        assert q["source"] == "live"

    def test_stale_served_on_fetch_failure(self, db, monkeypatch):
        db.cache_set(
            "mkt:quote:NVDA",
            {
                "symbol": "NVDA",
                "price": 90.0,
                "prev_close": 89.0,
                "change_pct": 1.12,
                "currency": "USD",
                "name": None,
                "fetched_at": time.time() - 90000,
            },
            7 * 24 * 3600,
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "NVDA", max_age=86400)
        assert q["price"] == 90.0
        assert q["stale"] is True
        assert q["source"] == "cache"

    def test_error_when_nothing_available(self, db, monkeypatch):
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "NOPE", max_age=60)
        assert q.get("error")
        assert q["price"] is None

    def test_gbx_normalised_to_gbp(self, db, monkeypatch):
        _patch_ticker(
            monkeypatch,
            {"last_price": 4500.0, "previous_close": 4400.0, "currency": "GBp"},
        )
        q = market.get_quote(db, "GAW.L", max_age=60)
        assert q["price"] == 45.0
        assert q["prev_close"] == 44.0
        assert q["currency"] == "GBP"

    def test_none_db_degrades_to_live(self, monkeypatch):
        _patch_ticker(
            monkeypatch,
            {"last_price": 10.0, "previous_close": 10.0, "currency": "EUR"},
        )
        q = market.get_quote(None, "BTC-EUR", max_age=60)
        assert q["price"] == 10.0
        assert q["source"] == "live"


class TestGetQuotes:
    def test_batch_partial_failure(self, db, monkeypatch):
        db.cache_set(
            "mkt:quote:GOOD",
            {
                "symbol": "GOOD",
                "price": 5.0,
                "prev_close": 5.0,
                "change_pct": 0.0,
                "currency": "EUR",
                "name": None,
                "fetched_at": time.time(),
            },
            7 * 24 * 3600,
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        out = market.get_quotes(db, ["GOOD", "BAD"], max_age=3600)
        assert out[0]["price"] == 5.0
        assert out[1].get("error")


class TestQuoteFromDb:
    def _seed_asset_with_prices(self, db):
        db.create_asset(
            symbol="ASML.AS",
            name="ASML Holding",
            asset_type="stock",
            currency="EUR",
        )
        db.insert_price_record(
            symbol="ASML.AS",
            price=600.0,
            fetched_ts=datetime.now(),
            price_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        db.insert_price_record(
            symbol="ASML.AS",
            price=610.0,
            fetched_ts=datetime.now(),
            price_date=date.today().isoformat(),
        )

    def test_held_asset_served_from_db(self, db, monkeypatch):
        self._seed_asset_with_prices(db)
        # Yahoo unreachable: the DB path must satisfy a 2-day max_age alone.
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "ASML.AS", max_age=2 * 86400)
        assert q["price"] == 610.0
        assert q["prev_close"] == 600.0
        assert q["change_pct"] == pytest.approx(1.67, abs=0.01)
        assert q["source"] == "db"
        assert q["stale"] is False
        assert q["name"] == "ASML Holding"

    def test_stale_db_price_flagged_when_live_fails(self, db, monkeypatch):
        db.create_asset(
            symbol="OLD.AS", name="Old Co", asset_type="stock", currency="EUR"
        )
        db.insert_price_record(
            symbol="OLD.AS",
            price=10.0,
            fetched_ts=datetime.now(),
            price_date=(date.today() - timedelta(days=5)).isoformat(),
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "OLD.AS", max_age=86400)
        assert q["price"] == 10.0
        assert q["stale"] is True
        assert q["source"] == "db"


class TestGetFxEur:
    def test_eur_short_circuit(self, db):
        assert market.get_fx_eur(db, "EUR") == (1.0, False)

    def test_live_fetch_and_cache(self, db, monkeypatch):
        _patch_ticker(monkeypatch, {"last_price": 0.93})
        rate, stale = market.get_fx_eur(db, "USD", max_age=60)
        assert rate == 0.93
        assert stale is False
        # Fresh cache: no fetch on the second call.
        _patch_ticker(monkeypatch, raise_exc=True)
        rate2, stale2 = market.get_fx_eur(db, "USD", max_age=3600)
        assert rate2 == 0.93
        assert stale2 is False

    def test_stale_cache_on_failure(self, db, monkeypatch):
        db.cache_set(
            "mkt:fx:USD",
            {"rate": 0.91, "fetched_at": time.time() - 90000},
            7 * 24 * 3600,
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        rate, stale = market.get_fx_eur(db, "USD", max_age=3600)
        assert rate == 0.91
        assert stale is True

    def test_fallback_default_when_nothing(self, db, monkeypatch):
        _patch_ticker(monkeypatch, raise_exc=True)
        rate, stale = market.get_fx_eur(db, "USD", max_age=3600)
        assert rate == pytest.approx(0.92)
        assert stale is True


class TestGetFundamentals:
    def test_live_then_cached(self, db, monkeypatch):
        calls = []

        def fake_live(symbol):
            calls.append(symbol)
            return {"symbol": symbol, "trailingPE": 30.0, "marketCap": 1e12}

        monkeypatch.setattr(
            "portf_manager.services.research._fetch_fundamentals_live", fake_live
        )
        f = market.get_fundamentals(db, "NVDA", max_age=3600)
        assert f["trailingPE"] == 30.0
        assert f["source"] == "live"
        f2 = market.get_fundamentals(db, "NVDA", max_age=3600)
        assert f2["source"] == "cache"
        assert calls == ["NVDA"]

    def test_miss_not_cached_then_error(self, db, monkeypatch):
        monkeypatch.setattr(
            "portf_manager.services.research._fetch_fundamentals_live",
            lambda s: {"symbol": s},
        )
        f = market.get_fundamentals(db, "NOPE", max_age=3600)
        assert f.get("error")
