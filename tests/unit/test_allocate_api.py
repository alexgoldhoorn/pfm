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
