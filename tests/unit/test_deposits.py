from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def _make_deposit(db, **overrides):
    defaults = dict(
        name="Superdepósito 1 mes",
        portfolio_id=None,
        principal=5000.0,
        currency="EUR",
        interest_rate=4.0,
        start_date="2026-06-12",
        maturity_date="2026-07-12",
        notes=None,
    )
    defaults.update(overrides)
    return db.create_fixed_deposit(**defaults)


def test_create_and_get_deposit(db):
    dep_id = _make_deposit(db)
    dep = db.get_fixed_deposit(dep_id)
    assert dep["name"] == "Superdepósito 1 mes"
    assert dep["principal"] == 5000.0
    assert dep["interest_rate"] == 4.0
    assert dep["status"] == "active"
    assert dep["interest_paid"] is None


def test_list_deposits(db):
    _make_deposit(db, name="Dep A")
    _make_deposit(db, name="Dep B")
    deps = db.get_fixed_deposits()
    assert len(deps) == 2


def test_update_deposit(db):
    dep_id = _make_deposit(db)
    ok = db.update_fixed_deposit(dep_id, status="matured", interest_paid=8.35)
    assert ok is True
    dep = db.get_fixed_deposit(dep_id)
    assert dep["status"] == "matured"
    assert dep["interest_paid"] == 8.35


def test_delete_deposit(db):
    dep_id = _make_deposit(db)
    assert db.delete_fixed_deposit(dep_id) is True
    assert db.get_fixed_deposit(dep_id) is None


def test_get_active_deposits(db):
    _make_deposit(db, name="Active")
    dep_id2 = _make_deposit(db, name="Matured")
    db.update_fixed_deposit(dep_id2, status="matured")
    active = db.get_fixed_deposits(status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active"


_TEST_API_KEY = "test-key-deposits-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.dependencies import get_database, get_api_key_manager
    from portf_manager.database import Database
    from portf_server.auth_middleware import APIKeyManager

    db_instance = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db_instance)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)

    app.dependency_overrides[get_database] = lambda: db_instance
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app)


def test_api_create_list_deposit(tmp_path):
    client = _make_client(tmp_path)
    payload = {
        "name": "Dep 1",
        "principal": 5000.0,
        "currency": "EUR",
        "interest_rate": 4.0,
        "start_date": "2026-06-12",
        "maturity_date": "2026-07-12",
    }
    r = client.post("/api/v1/deposits/", json=payload, headers=HEADERS)
    assert r.status_code == 200
    dep_id = r.json()["id"]

    r2 = client.get("/api/v1/deposits/", headers=HEADERS)
    assert r2.status_code == 200
    deps = r2.json()
    assert len(deps) == 1
    assert deps[0]["id"] == dep_id
    assert "projected_interest" in deps[0]


def test_api_delete_deposit(tmp_path):
    client = _make_client(tmp_path)
    r = client.post(
        "/api/v1/deposits/",
        json={
            "name": "X",
            "principal": 1000.0,
            "interest_rate": 2.0,
            "start_date": "2026-01-01",
            "maturity_date": "2026-07-01",
        },
        headers=HEADERS,
    )
    dep_id = r.json()["id"]
    r2 = client.delete(f"/api/v1/deposits/{dep_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["deleted"] is True


def test_api_mature_deposit(tmp_path):
    client = _make_client(tmp_path)
    r = client.post(
        "/api/v1/deposits/",
        json={
            "name": "Dep Mature",
            "principal": 5000.0,
            "interest_rate": 4.0,
            "start_date": "2026-06-12",
            "maturity_date": "2026-07-12",
        },
        headers=HEADERS,
    )
    dep_id = r.json()["id"]

    r2 = client.post(
        f"/api/v1/deposits/{dep_id}/mature",
        json={"interest_paid": 8.35, "date": "2026-07-12"},
        headers=HEADERS,
    )
    assert r2.status_code == 200
    body = r2.json()
    assert "transaction_id" in body
    assert body["deposit_id"] == dep_id


def test_networth_includes_deposits(tmp_path):
    client = _make_client(tmp_path)
    client.post(
        "/api/v1/deposits/",
        json={
            "name": "Active dep",
            "principal": 5000.0,
            "interest_rate": 4.0,
            "start_date": "2026-06-12",
            "maturity_date": "2026-07-12",
        },
        headers=HEADERS,
    )
    r = client.get("/api/v1/networth/", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "deposits_eur" in body
    assert body["deposits_eur"] == 5000.0


def test_extract_deposits_llm_endpoint(tmp_path):
    extracted = [
        {
            "name": "Superdepósito PREMIUM 1 mes",
            "principal": 5000.0,
            "currency": "EUR",
            "interest_rate": 4.0,
            "start_date": "2026-06-12",
            "maturity_date": "2026-07-12",
        }
    ]
    client = _make_client(tmp_path)
    with (
        patch(
            "portf_server.routers.llm.get_llm_client",
            return_value=MagicMock(),
        ),
        patch(
            "portf_server.routers.llm.GeminiClient.extract_deposits",
            return_value=extracted,
        ),
    ):
        r = client.post(
            "/api/v1/llm/extract-deposits",
            json={"text": "Superdepósito PREMIUM 1 mes 5000€ TAE 4% vence 12/07/2026"},
            headers=HEADERS,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["deposits"][0]["interest_rate"] == 4.0
