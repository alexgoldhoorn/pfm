"""Tests for the networth cashflow API endpoints."""

from fastapi.testclient import TestClient

from portf_manager.database import Database

_TEST_API_KEY = "test-key-networth-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db_instance = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db_instance)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db_instance
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app)


def test_cashflow_empty(tmp_path):
    client = _make_client(tmp_path)
    r = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["items"] == []
    assert d["net_monthly_eur"] == 0.0
    assert d["income_eur"] == 0.0
    assert d["expenses_eur"] == 0.0
    assert set(d["by_category"]) == {
        "salary",
        "other_income",
        "mortgage",
        "loan",
        "rest",
    }


def test_cashflow_net_monthly_eur(tmp_path):
    client = _make_client(tmp_path)
    client.post(
        "/api/v1/networth/cashflow",
        json={
            "label": "Salary",
            "category": "salary",
            "amount": 3500.0,
            "currency": "EUR",
        },
        headers=HEADERS,
    )
    client.post(
        "/api/v1/networth/cashflow",
        json={
            "label": "Mortgage",
            "category": "mortgage",
            "amount": 1200.0,
            "currency": "EUR",
        },
        headers=HEADERS,
    )
    client.post(
        "/api/v1/networth/cashflow",
        json={
            "label": "Living",
            "category": "rest",
            "amount": 800.0,
            "currency": "EUR",
        },
        headers=HEADERS,
    )
    r = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["income_eur"] == 3500.0
    assert d["expenses_eur"] == 2000.0
    assert d["net_monthly_eur"] == 1500.0
    assert d["by_category"]["salary"] == 3500.0
    assert d["by_category"]["mortgage"] == 1200.0
    assert d["by_category"]["rest"] == 800.0
    assert d["by_category"]["loan"] == 0.0
    assert len(d["items"]) == 3
    assert "amount_eur" in d["items"][0]


def test_cashflow_create_invalid_category(tmp_path):
    client = _make_client(tmp_path)
    r = client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Foo", "category": "bad_category", "amount": 100.0},
        headers=HEADERS,
    )
    assert r.status_code == 422


def test_cashflow_delete(tmp_path):
    client = _make_client(tmp_path)
    r = client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Loan", "category": "loan", "amount": 300.0},
        headers=HEADERS,
    )
    assert r.status_code == 200
    entry_id = r.json()["id"]

    r2 = client.delete(f"/api/v1/networth/cashflow/{entry_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["deleted"] is True

    r3 = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    assert r3.json()["items"] == []


def test_cashflow_delete_missing(tmp_path):
    client = _make_client(tmp_path)
    r = client.delete("/api/v1/networth/cashflow/999", headers=HEADERS)
    assert r.status_code == 404


def test_cashflow_other_income_is_income(tmp_path):
    client = _make_client(tmp_path)
    client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Rental", "category": "other_income", "amount": 500.0},
        headers=HEADERS,
    )
    r = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    d = r.json()
    assert d["income_eur"] == 500.0
    assert d["expenses_eur"] == 0.0
    assert d["net_monthly_eur"] == 500.0
