"""Tests for run_price_update: which assets are priced, FX, and failure handling."""

import pytest

from portf_manager.api_client import APIError
from portf_manager.services.price_updater import run_price_update


class FakeClient:
    """Stands in for APIClient: canned prices, FX rates and quote currencies."""

    def __init__(self, prices=None, fx=None, quote_ccy=None, error=None):
        self.prices = prices or {}
        self.fx = fx or {}
        self.quote_ccy = quote_ccy or {}
        self.error = error
        self.requested: list[str] = []

    def fetch_latest_prices(self, symbols):
        self.requested = list(symbols)
        if self.error:
            raise self.error
        return {s: p for s, p in self.prices.items() if s in symbols}

    def get_fx_rate(self, from_ccy, to_ccy):
        return self.fx.get(from_ccy)

    def get_quote_currency(self, symbol):
        return self.quote_ccy.get(symbol)


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr("portf_manager.api_client.get_client", lambda: fake)
    return fake


def _price(db, symbol):
    row = db.get_latest_price(db.get_asset_by_symbol(symbol)["id"])
    return row["price"] if row else None


def test_stores_prices_and_records_the_run(test_database, client):
    test_database.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    client.prices = {"EXA": 12.5}
    client.quote_ccy = {"EXA": "EUR"}

    result = run_price_update(test_database, source="cron")

    assert result["updated_count"] == 1 and result["error_count"] == 0
    assert _price(test_database, "EXA") == 12.5
    (run,) = test_database.get_price_update_runs()
    assert (run["source"], run["updated_count"]) == ("cron", 1)


def test_manual_price_assets_are_left_alone(test_database, client):
    aid = test_database.create_asset("MAN", "Manual Fund", "etf", currency="EUR")
    test_database.update_asset(aid, auto_price=0)
    client.prices = {"MAN": 99.0}

    assert run_price_update(test_database)["updated_count"] == 0
    assert client.requested == []


def test_explicit_symbols_ignore_unknown_ones(test_database, client):
    test_database.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    test_database.create_asset("OTH", "Other Corp", "stock", currency="EUR")
    client.prices = {"EXA": 1.0, "OTH": 2.0}

    run_price_update(test_database, symbols=["exa", "NOPE"])

    assert client.requested == ["EXA"]


def test_ticker_alias_is_fetched_but_stored_under_symbol(test_database, client):
    aid = test_database.create_asset("LU0000000001", "Example Fund", "etf", "EUR")
    test_database.update_asset(aid, ticker="EXF.DE")
    client.prices = {"EXF.DE": 50.0}

    run_price_update(test_database)

    assert client.requested == ["EXF.DE"]
    assert _price(test_database, "LU0000000001") == 50.0


def test_crypto_override_converts_usd_quote_to_eur(test_database, client):
    test_database.create_asset("SUI", "Sui", "crypto", currency="EUR")
    client.prices = {"SUI20947-USD": 2.0}
    client.fx = {"USD": 0.9}

    run_price_update(test_database)

    assert client.requested == ["SUI20947-USD"]
    assert _price(test_database, "SUI") == pytest.approx(1.8)


def test_missing_fx_rate_skips_instead_of_storing_usd_as_eur(test_database, client):
    test_database.create_asset("SUI", "Sui", "crypto", currency="EUR")
    client.prices = {"SUI20947-USD": 2.0}

    result = run_price_update(test_database)

    assert result["skipped_symbols"] == ["SUI"]
    assert _price(test_database, "SUI") is None
    assert any("FX rate unavailable" in e for e in result["api_errors"])


def test_mislabelled_currency_is_corrected(test_database, client):
    aid = test_database.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    client.prices = {"EXA": 10.0}
    client.quote_ccy = {"EXA": "USD"}

    run_price_update(test_database)

    assert test_database.get_asset(aid)["currency"] == "USD"


def test_crypto_currency_is_never_rewritten(test_database, client):
    aid = test_database.create_asset("BTC", "Bitcoin", "crypto", currency="EUR")
    client.prices = {"BTC-EUR": 50000.0}
    client.quote_ccy = {"BTC-EUR": "USD"}

    run_price_update(test_database)

    assert test_database.get_asset(aid)["currency"] == "EUR"


def test_api_failure_is_recorded_not_raised(test_database, client):
    test_database.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    client.error = APIError("Yahoo down")

    result = run_price_update(test_database)

    assert result["updated_count"] == 0
    assert result["api_errors"] == ["API error: Yahoo down"]
    assert test_database.get_price_update_runs()[0]["api_errors"] == [
        "API error: Yahoo down"
    ]


def test_nothing_to_update_still_records_a_run(test_database, client):
    result = run_price_update(test_database, symbols=["NOPE"])
    assert result["updated_count"] == 0
    assert len(test_database.get_price_update_runs()) == 1


# ── price-spike guard ──────────────────────────────────────────────────────


def _guard_setup(db, client, monkeypatch, prev=10.0):
    """An asset with a stored price of *prev* dated the day before 'today'."""
    from datetime import date, datetime

    monkeypatch.setattr(
        "portf_manager.services.price_updater._today", lambda: date(2026, 10, 18)
    )
    db.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    db.insert_price_record(
        "EXA", prev, datetime(2026, 10, 17, 9, 0), price_date="2026-10-17"
    )
    client.quote_ccy = {"EXA": "EUR"}


def test_out_of_band_price_is_held_not_stored(test_database, client, monkeypatch):
    _guard_setup(test_database, client, monkeypatch)
    client.prices = {"EXA": 1000.0}

    result = run_price_update(test_database)

    assert result["updated_count"] == 0
    assert result["held_symbols"] == ["EXA"]
    assert result["skipped_symbols"] == ["EXA"]
    assert "Price held for EXA" in result["api_errors"][0]
    assert _price(test_database, "EXA") == 10.0
    events = test_database.list_logs(event="price.guard.held")
    assert len(events) == 1


def test_held_price_is_stored_when_a_later_fetch_confirms_it(
    test_database, client, monkeypatch
):
    from datetime import date

    _guard_setup(test_database, client, monkeypatch)
    client.prices = {"EXA": 1000.0}
    run_price_update(test_database)

    # The next day the same level shows up again.
    monkeypatch.setattr(
        "portf_manager.services.price_updater._today", lambda: date(2026, 10, 19)
    )
    client.prices = {"EXA": 1010.0}
    result = run_price_update(test_database)

    assert result["updated_count"] == 1 and result["held_symbols"] == []
    assert _price(test_database, "EXA") == 1010.0
    assert test_database.list_logs(event="price.guard.confirmed")


def test_glitch_that_disappears_is_cleared(test_database, client, monkeypatch):
    from datetime import date

    _guard_setup(test_database, client, monkeypatch)
    client.prices = {"EXA": 1000.0}
    run_price_update(test_database)

    monkeypatch.setattr(
        "portf_manager.services.price_updater._today", lambda: date(2026, 10, 19)
    )
    client.prices = {"EXA": 10.4}
    result = run_price_update(test_database)

    assert result["updated_count"] == 1
    assert _price(test_database, "EXA") == 10.4
    assert test_database.list_logs(event="price.guard.cleared")


def test_second_fetch_the_same_day_stays_held(test_database, client, monkeypatch):
    _guard_setup(test_database, client, monkeypatch)
    client.prices = {"EXA": 1000.0}
    run_price_update(test_database)
    result = run_price_update(test_database)

    assert result["held_symbols"] == ["EXA"]
    assert _price(test_database, "EXA") == 10.0
    # Only the first sighting is logged.
    assert len(test_database.list_logs(event="price.guard.held")) == 1
