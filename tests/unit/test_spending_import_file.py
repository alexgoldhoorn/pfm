"""POST /spending/import-file: parse, de-duplicate, categorise and save in one call."""

import io

from fastapi.testclient import TestClient

from portf_manager.database import Database

_KEY = "test-key-import-file-abc123"
HEADERS = {"X-API-Key": _KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "import_file.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db


def _post(client, pid, text, name="s.csv"):
    return client.post(
        "/api/v1/spending/import-file",
        data={"portfolio_id": str(pid)},
        files={"file": (name, io.BytesIO(text.encode()), "text/csv")},
        headers=HEADERS,
    )


CSV = (
    "date,description,amount\n"
    "2026-01-05,EXAMPLE MARKET,-24.50\n"
    "2026-01-06,EXAMPLE COFFEE,-3.00\n"
    "2026-01-06,EXAMPLE COFFEE,-3.00\n"
    "2026-01-07,UNKNOWN CORP,-10.00\n"
)


def test_import_file_saves_categorises_and_reports(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_rule(pattern="EXAMPLE MARKET", category="Groceries")

    r = _post(client, pid, CSV)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["parsed"] == 4
    assert d["imported"] == 4
    assert d["duplicates"] == 0
    # coffee x2 and the unknown corp have no rule
    assert d["uncategorized"] == 3
    assert d["date_from"] == "2026-01-05"
    assert d["date_to"] == "2026-01-07"
    assert d["data_through"] == "2026-01-07"
    assert d["warnings"] == []
    rows = db.list_spending_transactions(portfolio_id=pid)
    assert len(rows) == 4
    assert {r["category"] for r in rows if r["description"] == "EXAMPLE MARKET"} == {
        "Groceries"
    }


def test_import_file_drops_rows_stored_before_but_keeps_twins(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    assert _post(client, pid, CSV).status_code == 200

    again = _post(client, pid, CSV).json()
    assert again["parsed"] == 4
    assert again["imported"] == 0
    assert again["duplicates"] == 4
    assert len(db.list_spending_transactions(portfolio_id=pid)) == 4


def test_import_file_refuses_a_non_bank_account(tmp_path):
    client, db = _make_client(tmp_path)
    broker = db.create_portfolio("Example Broker")
    r = _post(client, broker, CSV)
    assert r.status_code == 400
    assert "bank account" in r.json()["detail"]
    assert db.list_spending_transactions() == []


def test_import_file_with_nothing_parsed_is_400_and_writes_nothing(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    r = _post(client, pid, "foo,bar\n1,2\n")
    assert r.status_code == 400
    assert "Missing required columns" in r.json()["detail"]
    assert db.list_spending_transactions() == []
