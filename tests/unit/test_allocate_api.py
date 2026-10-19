"""Tests for the allocate endpoints."""

from datetime import date

from tests.unit.test_retirement_api import HEADERS, _make_client

URL = "/api/v1/allocate"


def test_settings_roundtrip_and_422(tmp_path):
    client = _make_client(tmp_path)
    s = client.get(f"{URL}/settings", headers=HEADERS).json()
    assert s["buffer_months_target"] == 6 and s["income_merchants"] == []
    r = client.put(
        f"{URL}/settings",
        json={"buffer_months_target": 7, "bogus": 1},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["buffer_months_target"] == 7
    assert (
        client.get(f"{URL}/settings", headers=HEADERS).json()["buffer_months_target"]
        == 7
    )
    r = client.put(f"{URL}/settings", json={"buffer_months_min": 9}, headers=HEADERS)
    assert r.status_code == 422 and "buffer months" in r.json()["detail"]
    assert (
        client.get(f"{URL}/settings", headers=HEADERS).json()["buffer_months_min"] == 4
    )


def test_settings_lists_income_merchants(tmp_path):
    from portf_server.dependencies import get_database
    from portf_server.app import app

    client = _make_client(tmp_path)
    db = app.dependency_overrides[get_database]()
    pid = db.get_or_create_portfolio("Example Bank", account_type="bank")
    day = date.today().isoformat()
    for merchant, amount, cat in [
        ("Beta Client", 100.0, "Income"),
        ("Alpha Client", 100.0, "Income"),
        ("Alpha Client", 50.0, "Income"),
        ("Shop", -20.0, "Income"),
        ("Other", 10.0, "Gifts"),
    ]:
        db.create_spending_transaction(
            pid, day, merchant, amount, category=cat, merchant=merchant
        )
    s = client.get(f"{URL}/settings", headers=HEADERS).json()
    assert s["income_merchants"] == ["Alpha Client", "Beta Client"]


def test_deployable_endpoint_shape(tmp_path, monkeypatch):
    import portf_server.routers.portfolios as pf

    monkeypatch.setattr(pf, "_get_fx_rate", lambda c: 1.0)
    monkeypatch.setattr(pf, "_get_fx_rate_on", lambda db, c, d: 1.0)
    client = _make_client(tmp_path)
    r = client.get(f"{URL}/deployable", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "status",
        "liquid_eur",
        "liquid_lines",
        "tax_reserve",
        "buffer",
        "coming_in",
        "stale_lines",
        "warnings",
    ):
        assert key in body
    assert body["status"] == "insufficient_data"
    assert client.get(f"{URL}/deployable").status_code in (401, 403)


def test_next_euro_endpoint(tmp_path):
    client = _make_client(tmp_path)
    r = client.get(f"{URL}/next-euro", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["status"] == "insufficient_data"
    assert "chat_summary" in r.json()
    r = client.get(f"{URL}/next-euro?amount=-5", headers=HEADERS)
    assert r.status_code == 422
    r = client.get(f"{URL}/next-euro?amount=500", headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "targets_incomplete"


def _candidates_client(tmp_path, monkeypatch):
    from datetime import datetime

    import portf_server.routers.portfolios as pf
    from portf_manager.services import allocator
    from portf_server.app import app
    from portf_server.dependencies import get_database

    monkeypatch.setattr(pf, "_get_fx_rate", lambda c: 1.0)
    monkeypatch.setattr(pf, "_get_fx_rate_on", lambda db, c, d: 1.0)
    monkeypatch.setattr(allocator, "blocked_assets", lambda *a, **k: {})
    monkeypatch.setattr(
        "portf_manager.services.exposure._resolve_sector_country",
        lambda db, asset: ("Unknown", "Unknown"),
    )
    client = _make_client(tmp_path)
    db = app.dependency_overrides[get_database]()
    pid = db.get_or_create_portfolio("Example Broker")
    for symbol, name, atype, qty, price in [
        ("FUNDA", "Fund A", "etf", 10, 100.0),
        ("FUNDB", "Fund B", "etf", 5, 100.0),
        ("STKA", "Example Corp", "stock", 10, 10.0),
    ]:
        aid = db.create_asset(symbol, name, atype, currency="EUR")
        db.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=qty,
            price=price,
            total_amount=qty * price,
            transaction_date="2026-01-02",
            portfolio_id=pid,
            currency="EUR",
        )
        db.insert_price_record(symbol, price, datetime(2026, 10, 15, 9, 0))
    db.set_allocation_target("etf", 60)
    db.set_allocation_target("stock", 40)
    return client


def test_candidates_endpoint_bucket_filter_and_fields(tmp_path, monkeypatch):
    client = _candidates_client(tmp_path, monkeypatch)
    r = client.get(f"{URL}/candidates?bucket=stock", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "stock" and body["target_pct"] == 40
    assert body["total_value_eur"] == 1600.0
    [row] = body["candidates"]
    assert row["symbol"] == "STKA" and row["portfolio_name"] == "Example Broker"
    # 5 % of 1600 is 80, 100 already held: no room
    assert row["cap_room_eur"] == 0.0
    assert "single-stock cap" in row["rank_reason"]
    assert "overlap_group" in row and row["blocked"] is None

    etf = client.get(f"{URL}/candidates?bucket=etf", headers=HEADERS).json()
    assert [c["symbol"] for c in etf["candidates"]] == ["FUNDA", "FUNDB"]
    assert all(c["cap_room_eur"] is None for c in etf["candidates"])
    assert "TER unknown" in etf["candidates"][0]["rank_reason"]


def test_candidates_endpoint_all_buckets_and_404(tmp_path, monkeypatch):
    client = _candidates_client(tmp_path, monkeypatch)
    body = client.get(f"{URL}/candidates", headers=HEADERS).json()
    assert body["bucket"] is None and len(body["candidates"]) == 3
    r = client.get(f"{URL}/candidates?bucket=nope", headers=HEADERS)
    assert r.status_code == 404
    assert client.get(f"{URL}/candidates").status_code in (401, 403)
