"""Tests for the broker cash ledger service."""

from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services.cash_ledger import cash_by_portfolio

_TEST_API_KEY = "test-key-cash-ledger-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _fx(currency, on_date):
    return 1.0


def _tx(db, pid, asset_id, kind, amount, day):
    db.create_transaction(
        asset_id=asset_id,
        transaction_type=kind,
        quantity=1,
        price=amount,
        total_amount=amount,
        transaction_date=day,
        portfolio_id=pid,
        currency="EUR",
    )


def test_cash_from_deposit_and_buy(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    _tx(db, pid, aid, "buy", 400.0, "2026-01-05")
    result = cash_by_portfolio(db, _fx)
    assert result[pid]["cash_eur"] == 600.0
    assert result[pid]["as_of"] == "2026-01-05"


def test_cash_type_asset_transactions_are_ignored(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.get_or_create_portfolio("Example Broker")
    cash_asset = db.create_asset("CASH", "Cash", "cash", currency="EUR")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    _tx(db, pid, cash_asset, "buy", 400.0, "2026-01-05")
    result = cash_by_portfolio(db, _fx)
    assert result[pid]["cash_eur"] == 1000.0
    assert result[pid]["as_of"] == "2026-01-02"


def test_portfolio_without_activity_is_absent(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.get_or_create_portfolio("Example Broker")
    assert pid not in cash_by_portfolio(db, _fx)


def test_portfolios_values_endpoint_unchanged(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "api.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    pid = db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    _tx(db, pid, aid, "buy", 400.0, "2026-01-05")
    r = TestClient(app).get("/api/v1/portfolios/values", headers=HEADERS)
    assert r.status_code == 200
    row = next(p for p in r.json()["portfolios"] if p["portfolio_id"] == pid)
    assert row["cash_eur"] == cash_by_portfolio(db, _fx)[pid]["cash_eur"] == 600.0


def test_interest_on_cash_type_asset_counts_net_of_withholding(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.get_or_create_portfolio("Example Broker")
    cash_asset = db.create_asset("EX-CASH", "Example Cash", "cash", currency="EUR")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    db.create_transaction(
        asset_id=cash_asset,
        transaction_type="interest",
        quantity=1,
        price=30.0,
        total_amount=30.0,
        tax=5.7,
        transaction_date="2026-02-01",
        portfolio_id=pid,
        currency="EUR",
    )
    result = cash_by_portfolio(db, _fx)
    assert round(result[pid]["cash_eur"], 2) == 1024.3
    assert result[pid]["as_of"] == "2026-02-01"


def test_negative_dividend_reversal_reduces_cash(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset("EXA", "Example Corp", "stock", currency="EUR")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    _tx(db, pid, aid, "dividend", 2.5, "2026-01-05")
    _tx(db, pid, aid, "dividend", -2.5, "2026-01-06")
    assert round(cash_by_portfolio(db, _fx)[pid]["cash_eur"], 2) == 1000.0


def test_amount_converts_from_the_transactions_own_currency(tmp_path):
    # A UCITS ETF whose base currency is USD, bought in EUR: the EUR amount
    # must not be converted as if it were dollars.
    db = Database(str(tmp_path / "t.db"))
    pid = db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset("EXETF", "Example World ETF", "etf", currency="USD")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    _tx(db, pid, aid, "buy", 400.0, "2026-01-05")

    def fx(currency, on_date):
        return 0.5 if currency == "USD" else 1.0

    assert cash_by_portfolio(db, fx)[pid]["cash_eur"] == 600.0


def test_values_cost_converts_from_the_transactions_own_currency(tmp_path, monkeypatch):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database
    from portf_server.routers import portfolios

    monkeypatch.setattr(
        portfolios,
        "_get_fx_rate_on",
        lambda db, c, d: 0.5 if c == "USD" else 1.0,
    )
    monkeypatch.setattr(
        portfolios, "_get_fx_rate", lambda c: 0.5 if c == "USD" else 1.0
    )
    db = Database(str(tmp_path / "api.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    pid = db.get_or_create_portfolio("Example Broker")
    aid = db.create_asset("EXETF", "Example World ETF", "etf", currency="USD")
    db.create_booking("2026-01-02", "Deposit", 1000.0, "EUR", portfolio_id=pid)
    _tx(db, pid, aid, "buy", 400.0, "2026-01-05")
    try:
        r = TestClient(app).get("/api/v1/portfolios/values", headers=HEADERS)
    finally:
        app.dependency_overrides.clear()
    row = next(p for p in r.json()["portfolios"] if p["portfolio_id"] == pid)
    assert row["cost_eur"] == 400.0
    assert row["cash_eur"] == 600.0
