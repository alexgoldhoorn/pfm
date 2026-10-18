"""Tests for the retirement plan and income endpoints."""

from fastapi.testclient import TestClient

from portf_manager.database import Database

_TEST_API_KEY = "test-key-retire-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}
INCOME = {
    "name": "Example State Pension",
    "owner": "self",
    "monthly_eur": 1500,
    "from_age": 67,
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
    return TestClient(app)


def test_plan_defaults(tmp_path):
    client = _make_client(tmp_path)
    plan = client.get("/api/v1/retirement/plan", headers=HEADERS).json()
    assert plan["retirement_age"] == 65 and plan["horizon_age"] == 95
    assert plan["excluded_categories"] == [
        "Income Tax",
        "Social Security",
        "Invest",
        "House purchase",
    ]


def test_plan_put_merges_and_persists(tmp_path):
    client = _make_client(tmp_path)
    r = client.put(
        "/api/v1/retirement/plan",
        json={"retirement_age": 62, "birth_date": "1980-05-01", "bogus": 1},
        headers=HEADERS,
    )
    assert r.status_code == 200
    plan = client.get("/api/v1/retirement/plan", headers=HEADERS).json()
    assert plan["retirement_age"] == 62 and plan["birth_date"] == "1980-05-01"
    assert plan["horizon_age"] == 95 and "bogus" not in plan


def test_plan_put_rejects_bad_ages(tmp_path):
    client = _make_client(tmp_path)
    r = client.put("/api/v1/retirement/plan", json={"horizon_age": 60}, headers=HEADERS)
    assert r.status_code == 422
    assert (
        client.get("/api/v1/retirement/plan", headers=HEADERS).json()["horizon_age"]
        == 95
    )


def test_income_crud_and_validation(tmp_path):
    client = _make_client(tmp_path)
    r = client.post("/api/v1/retirement/incomes", json=INCOME, headers=HEADERS)
    assert r.status_code == 200
    iid = r.json()["id"]
    row = client.get("/api/v1/retirement/incomes", headers=HEADERS).json()[0]
    assert row["indexed"] is True and row["to_age"] is None
    r = client.put(
        f"/api/v1/retirement/incomes/{iid}",
        json={**INCOME, "indexed": False, "to_age": 80},
        headers=HEADERS,
    )
    assert r.status_code == 200
    row = client.get("/api/v1/retirement/incomes", headers=HEADERS).json()[0]
    assert row["indexed"] is False and row["to_age"] == 80
    assert (
        client.put(
            "/api/v1/retirement/incomes/999", json=INCOME, headers=HEADERS
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/retirement/incomes/{iid}", headers=HEADERS).status_code
        == 200
    )
    assert client.get("/api/v1/retirement/incomes", headers=HEADERS).json() == []

    for bad in ({"to_age": 67}, {"owner": "child"}, {"from_age": 130}):
        r = client.post(
            "/api/v1/retirement/incomes", json={**INCOME, **bad}, headers=HEADERS
        )
        assert r.status_code == 422, bad
