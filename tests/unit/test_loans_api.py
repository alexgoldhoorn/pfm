"""Tests for the loan endpoints and account valuation endpoints."""

from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import indexa

_TEST_API_KEY = "test-key-loans-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}
LOAN = {
    "name": "Example Mortgage",
    "principal": 120000,
    "annual_rate_pct": 3.0,
    "start_date": "2020-01-01",
    "term_months": 240,
}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db


def test_loan_roundtrip_and_balance(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post("/api/v1/networth/loans", json=LOAN, headers=HEADERS)
    assert r.status_code == 200
    lid = r.json()["id"]
    rows = client.get("/api/v1/networth/loans", headers=HEADERS).json()
    assert len(rows) == 1 and rows[0]["id"] == lid
    row = rows[0]
    assert 0 < row["balance_eur"] < 120000
    assert row["monthly_payment_eur"] > 600
    assert row["end_date"].startswith("2039") or row["end_date"].startswith("2040")
    sched = client.get(f"/api/v1/networth/loans/{lid}/schedule", headers=HEADERS)
    assert sched.status_code == 200
    years = sched.json()
    assert years[0]["year"] == 2020
    assert {"balance_end_eur", "interest_paid_eur", "payments_eur"} <= set(years[0])
    assert (
        client.delete(f"/api/v1/networth/loans/{lid}", headers=HEADERS).status_code
        == 200
    )
    assert client.get("/api/v1/networth/loans", headers=HEADERS).json() == []


def test_loan_put_rate_change(tmp_path):
    client, _ = _make_client(tmp_path)
    lid = client.post("/api/v1/networth/loans", json=LOAN, headers=HEADERS).json()["id"]
    r = client.put(
        f"/api/v1/networth/loans/{lid}",
        json={**LOAN, "annual_rate_pct": 4.5, "rate_as_of": "2026-10-01"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    row = client.get("/api/v1/networth/loans", headers=HEADERS).json()[0]
    assert row["annual_rate_pct"] == 4.5 and row["rate_as_of"] == "2026-10-01"
    cleared = client.put(
        f"/api/v1/networth/loans/{lid}",
        json={**LOAN, "rate_as_of": None},
        headers=HEADERS,
    )
    assert cleared.status_code == 200
    row = client.get("/api/v1/networth/loans", headers=HEADERS).json()[0]
    assert row["rate_as_of"] is None
    missing = client.put("/api/v1/networth/loans/999", json=LOAN, headers=HEADERS)
    assert missing.status_code == 404


def test_loan_validation(tmp_path):
    client, _ = _make_client(tmp_path)
    for bad in (
        {"principal": 0},
        {"term_months": 0},
        {"annual_rate_pct": -1},
        {"start_date": "not-a-date"},
    ):
        r = client.post("/api/v1/networth/loans", json={**LOAN, **bad}, headers=HEADERS)
        assert r.status_code == 422, bad


def test_valuation_create_refreshes(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker", base_currency="EUR")
    monkeypatch.setattr(
        "portf_server.routers.networth.get_settings",
        lambda: type("S", (), {"indexa_token": "tok"})(),
    )
    monkeypatch.setattr(
        indexa,
        "_get",
        lambda p, t: {"portfolio": {"total_amount": 7000, "cash_amount": 10}},
    )
    body = {"portfolio_id": pid, "provider": "indexa", "account_ref": "EXAMPLE1"}
    r = client.post("/api/v1/networth/valuations", json=body, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["refreshed"] is True and r.json()["value"] == 7000.0
    listed = client.get("/api/v1/networth/valuations", headers=HEADERS).json()
    assert [v["portfolio_id"] for v in listed] == [pid]
    refresh = client.post("/api/v1/networth/valuations/refresh", headers=HEADERS)
    assert refresh.json() == [{"portfolio_id": pid, "ok": True, "error": None}]
    assert (
        client.delete(f"/api/v1/networth/valuations/{pid}", headers=HEADERS).status_code
        == 200
    )
    assert client.get("/api/v1/networth/valuations", headers=HEADERS).json() == []


def test_valuation_without_token_keeps_row(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker", base_currency="EUR")
    monkeypatch.setattr(
        "portf_server.routers.networth.get_settings",
        lambda: type("S", (), {"indexa_token": None})(),
    )
    body = {"portfolio_id": pid, "provider": "indexa", "account_ref": "EXAMPLE1"}
    r = client.post("/api/v1/networth/valuations", json=body, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["refreshed"] is False and r.json()["as_of"] == "1970-01-01"


def test_valuation_unknown_or_bank_portfolio(tmp_path):
    client, db = _make_client(tmp_path)
    bank = db.create_portfolio("Example Bank", base_currency="EUR", account_type="bank")
    body = {"portfolio_id": 999, "provider": "indexa", "account_ref": "X"}
    assert (
        client.post(
            "/api/v1/networth/valuations", json=body, headers=HEADERS
        ).status_code
        == 404
    )
    body["portfolio_id"] = bank
    assert (
        client.post(
            "/api/v1/networth/valuations", json=body, headers=HEADERS
        ).status_code
        == 422
    )


def test_valuation_repost_keeps_good_value_when_refresh_fails(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Broker", base_currency="EUR")
    monkeypatch.setattr(
        "portf_server.routers.networth.get_settings",
        lambda: type("S", (), {"indexa_token": "tok"})(),
    )
    monkeypatch.setattr(
        indexa, "_get", lambda p, t: {"portfolio": {"total_amount": 7000}}
    )
    body = {"portfolio_id": pid, "provider": "indexa", "account_ref": "EXAMPLE1"}
    client.post("/api/v1/networth/valuations", json=body, headers=HEADERS)

    def boom(path, token):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(indexa, "_get", boom)
    body["account_ref"] = "EXAMPLE2"
    r = client.post("/api/v1/networth/valuations", json=body, headers=HEADERS)
    assert r.json()["refreshed"] is False
    row = db.get_account_valuation(pid)
    assert row["value"] == 7000.0 and row["account_ref"] == "EXAMPLE2"
    assert row["as_of"] != "1970-01-01"
