"""Tests for the /api/v1/wealth-scenarios endpoints."""

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from portf_manager.database import Database

_TEST_API_KEY = "test-key-wealth-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}
BASE = "/api/v1/wealth-scenarios/"
PARAMS = {
    "cash_amount": 20000,
    "stocks_amount": 100000,
    "stocks_rate_pct": 6,
    "stocks_contribution_monthly": 500,
    "mortgage_principal": 150000,
    "mortgage_rate_pct": 3,
    "mortgage_payment_monthly": 900,
    "years": 20,
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


def test_requires_api_key(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.get(BASE).status_code in (401, 403)


def test_create_list_get_delete(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(BASE, json={"name": "Base case", "params": PARAMS}, headers=HEADERS)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["name"] == "Base case"
    assert item["is_primary"] is False
    assert item["baseline_date"] == date.today().isoformat()
    # an empty database still has a balance sheet: net worth 0
    assert item["baseline_net_worth_eur"] == 0
    assert item["params"]["years"] == 20 and item["params"]["cash_rate_pct"] == 1.5
    assert item["summary"]["mortgage_paid_off_year"] == 18
    assert item["tracking"]["status"] == "too_early"
    assert "projection" not in item

    listed = client.get(BASE, headers=HEADERS).json()
    assert [s["id"] for s in listed] == [item["id"]]

    one = client.get(f"{BASE}{item['id']}", headers=HEADERS).json()
    assert len(one["projection"]) == 21
    assert one["projection"][20]["net_worth"] == one["summary"]["final_net_worth_eur"]

    assert client.delete(f"{BASE}{item['id']}", headers=HEADERS).json() == {"ok": True}
    assert client.get(f"{BASE}{item['id']}", headers=HEADERS).status_code == 404
    assert client.delete(f"{BASE}{item['id']}", headers=HEADERS).status_code == 404


def test_duplicate_name_is_409_and_bad_params_422(tmp_path):
    client, _ = _make_client(tmp_path)
    assert (
        client.post(BASE, json={"name": "A", "params": {}}, headers=HEADERS).status_code
        == 201
    )
    assert (
        client.post(BASE, json={"name": "A", "params": {}}, headers=HEADERS).status_code
        == 409
    )
    r = client.post(BASE, json={"name": "B", "params": {"years": 99}}, headers=HEADERS)
    assert r.status_code == 422
    assert "years" in r.json()["detail"]
    assert (
        client.post(BASE, json={"name": "", "params": {}}, headers=HEADERS).status_code
        == 422
    )


def test_primary_is_exclusive_and_listed_first(tmp_path):
    client, _ = _make_client(tmp_path)
    a = client.post(BASE, json={"name": "Zeta", "params": {}}, headers=HEADERS).json()
    b = client.post(
        BASE, json={"name": "Alpha", "params": {}, "is_primary": True}, headers=HEADERS
    ).json()
    assert b["is_primary"] is True
    names = [s["name"] for s in client.get(BASE, headers=HEADERS).json()]
    assert names == ["Alpha", "Zeta"]

    r = client.put(f"{BASE}{a['id']}", json={"is_primary": True}, headers=HEADERS)
    assert r.json()["is_primary"] is True
    flags = {
        s["name"]: s["is_primary"] for s in client.get(BASE, headers=HEADERS).json()
    }
    assert flags == {"Zeta": True, "Alpha": False}

    # demoting the primary leaves none
    client.put(f"{BASE}{a['id']}", json={"is_primary": False}, headers=HEADERS)
    assert not any(s["is_primary"] for s in client.get(BASE, headers=HEADERS).json())


def test_update_params_rebaselines_and_rename_conflicts(tmp_path):
    client, db = _make_client(tmp_path)
    a = client.post(BASE, json={"name": "A", "params": {}}, headers=HEADERS).json()
    client.post(BASE, json={"name": "B", "params": {}}, headers=HEADERS)
    # age the baseline so a params change visibly resets it
    db.update_wealth_scenario(
        a["id"], baseline_date="2020-01-01", baseline_net_worth_eur=123.0
    )

    r = client.put(f"{BASE}{a['id']}", json={"description": "notes"}, headers=HEADERS)
    assert r.json()["description"] == "notes"
    assert r.json()["baseline_date"] == "2020-01-01"

    r = client.put(f"{BASE}{a['id']}", json={"params": {"years": 10}}, headers=HEADERS)
    assert r.json()["params"]["years"] == 10
    assert r.json()["baseline_date"] == date.today().isoformat()
    assert r.json()["baseline_net_worth_eur"] == 0

    assert (
        client.put(
            f"{BASE}{a['id']}", json={"params": {"years": -1}}, headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        client.put(f"{BASE}{a['id']}", json={"name": "B"}, headers=HEADERS).status_code
        == 409
    )
    assert (
        client.put(f"{BASE}999", json={"name": "C"}, headers=HEADERS).status_code == 404
    )


def test_rebaseline_resets_date_and_net_worth(tmp_path):
    client, db = _make_client(tmp_path)
    a = client.post(BASE, json={"name": "A", "params": {}}, headers=HEADERS).json()
    db.update_wealth_scenario(
        a["id"], baseline_date="2020-01-01", baseline_net_worth_eur=123.0
    )
    with patch(
        "portf_manager.services.wealth_scenarios.current_net_worth", return_value=42.0
    ):
        r = client.post(f"{BASE}{a['id']}/rebaseline", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["baseline_date"] == date.today().isoformat()
    assert r.json()["baseline_net_worth_eur"] == 42.0
    assert client.post(f"{BASE}999/rebaseline", headers=HEADERS).status_code == 404


def test_tracking_uses_the_live_net_worth(tmp_path):
    client, db = _make_client(tmp_path)
    with patch(
        "portf_manager.services.wealth_scenarios.current_net_worth", return_value=1000.0
    ):
        a = client.post(
            BASE, json={"name": "A", "params": PARAMS}, headers=HEADERS
        ).json()
    assert a["baseline_net_worth_eur"] == 1000.0
    db.update_wealth_scenario(a["id"], baseline_date="2025-01-01")
    with patch(
        "portf_manager.services.wealth_scenarios.current_net_worth", return_value=600.0
    ):
        tr = client.get(BASE, headers=HEADERS).json()[0]["tracking"]
    assert tr["status"] == "behind"
    assert tr["actual_net_worth_eur"] == 600.0
    assert tr["actual_change_eur"] == -400.0


def test_net_worth_failure_gives_no_baseline_not_500(tmp_path):
    client, _ = _make_client(tmp_path)
    with patch(
        "portf_manager.services.balance_sheet.build_balance_sheet",
        side_effect=RuntimeError("boom"),
    ):
        r = client.post(BASE, json={"name": "A", "params": {}}, headers=HEADERS)
        assert r.status_code == 201
        assert r.json()["baseline_net_worth_eur"] is None
        assert r.json()["tracking"]["status"] == "no_baseline"
