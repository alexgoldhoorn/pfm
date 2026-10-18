"""Tests for the Indexa account valuation client and its refresh."""

from types import SimpleNamespace

import pytest

from portf_manager.services import indexa
from portf_manager.services.price_updater import run_price_update


def test_fetch_portfolio_reads_total_and_cash(monkeypatch):
    seen = {}

    def fake_get(path, token):
        seen["args"] = (path, token)
        return {
            "portfolio": {"total_amount": 10000, "cash_amount": 400},
            "date": "2026-10-15T08:00:00",
        }

    monkeypatch.setattr(indexa, "_get", fake_get)
    out = indexa.fetch_portfolio("EXAMPLE1", "tok")
    assert out == {"value": 10000.0, "cash": 400.0, "as_of": "2026-10-15"}
    assert seen["args"][1] == "tok" and "EXAMPLE1" in seen["args"][0]


def test_fetch_portfolio_defaults_cash_and_date(monkeypatch):
    monkeypatch.setattr(
        indexa, "_get", lambda p, t: {"portfolio": {"total_amount": 5000}}
    )
    out = indexa.fetch_portfolio("EXAMPLE1", "tok")
    assert out["cash"] == 0.0 and len(out["as_of"]) == 10


def _seed(db):
    pid = db.create_portfolio("Example Broker", base_currency="EUR")
    db.upsert_account_valuation(
        pid, "indexa", "EXAMPLE1", 9000.0, 100.0, "EUR", "2026-10-01", "x"
    )
    return pid


def test_refresh_updates_row(tmp_path, monkeypatch):
    from portf_manager.database import Database

    db = Database(str(tmp_path / "t.db"))
    pid = _seed(db)
    monkeypatch.setattr(
        indexa,
        "_get",
        lambda p, t: {
            "portfolio": {"total_amount": 9500, "cash_amount": 50},
            "date": "2026-10-16",
        },
    )
    res = indexa.refresh_account_valuations(db, "tok")
    assert res == [{"portfolio_id": pid, "ok": True, "error": None}]
    row = db.get_account_valuation(pid)
    assert (row["value"], row["cash"], row["as_of"]) == (9500.0, 50.0, "2026-10-16")
    assert row["last_error"] is None


def test_refresh_keeps_previous_value_on_error(tmp_path, monkeypatch):
    from portf_manager.database import Database

    db = Database(str(tmp_path / "t.db"))
    pid = _seed(db)

    def boom(path, token):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(indexa, "_get", boom)
    res = indexa.refresh_account_valuations(db, "tok")
    assert res[0]["ok"] is False and "HTTP 500" in res[0]["error"]
    row = db.get_account_valuation(pid)
    assert (row["value"], row["as_of"]) == (9000.0, "2026-10-01")
    assert "HTTP 500" in row["last_error"] and row["fetched_at"] != "x"


@pytest.mark.parametrize("token", [None, ""])
def test_refresh_without_token_does_nothing(tmp_path, monkeypatch, token):
    from portf_manager.database import Database

    db = Database(str(tmp_path / "t.db"))
    _seed(db)

    def boom(path, token):
        raise AssertionError("must not be called")

    monkeypatch.setattr(indexa, "_get", boom)
    assert indexa.refresh_account_valuations(db, token) == []


class _Client:
    def fetch_latest_prices(self, symbols):
        return {}

    def get_fx_rate(self, a, b):
        return None

    def get_quote_currency(self, s):
        return None


def test_run_price_update_reports_valuations(test_database, monkeypatch):
    monkeypatch.setattr("portf_manager.api_client.get_client", lambda: _Client())
    monkeypatch.setattr(
        "portf_server.settings.get_settings",
        lambda: SimpleNamespace(indexa_token="tok"),
    )
    _seed(test_database)
    test_database.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    monkeypatch.setattr(
        indexa, "_get", lambda p, t: {"portfolio": {"total_amount": 9100}}
    )
    result = run_price_update(test_database)
    assert result["valuations_refreshed"] == 1
    assert result["valuation_errors"] == []


def test_run_price_update_survives_valuation_failure(test_database, monkeypatch):
    monkeypatch.setattr("portf_manager.api_client.get_client", lambda: _Client())

    def broken():
        raise RuntimeError("settings broke")

    monkeypatch.setattr("portf_server.settings.get_settings", broken)
    test_database.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    result = run_price_update(test_database)
    assert result["valuations_refreshed"] == 0
    assert result["valuation_errors"] == ["settings broke"]
