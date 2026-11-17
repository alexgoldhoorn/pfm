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


def test_a_failing_row_removes_everything_this_call_saved(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    real = db.create_spending_transaction
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "create_spending_transaction", flaky)
    r = _post(client, pid, CSV)
    assert r.status_code == 500
    assert "nothing was stored" in r.json()["detail"]
    assert "disk full" in r.json()["detail"]
    assert db.list_spending_transactions(portfolio_id=pid) == []


def test_save_keeps_its_partial_behaviour(tmp_path, monkeypatch):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    real = db.create_spending_transaction
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(db, "create_spending_transaction", flaky)
    body = {
        "account_portfolio_id": pid,
        "duplicate_action": "add",
        "rows": [
            {
                "date": f"2026-01-0{i}",
                "description": f"EXAMPLE SHOP {i}",
                "amount": -5.0,
                "currency": "EUR",
                "category": "uncategorized",
            }
            for i in (1, 2, 3)
        ],
    }
    r = client.post("/api/v1/spending/save", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 2 and len(r.json()["errors"]) == 1
    assert len(db.list_spending_transactions(portfolio_id=pid)) == 2


def test_parse_failure_is_400_on_import_file_but_422_on_upload(tmp_path, monkeypatch):
    from portf_server.routers import spending

    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")

    def boom(_content):
        raise ValueError("bad file")

    monkeypatch.setattr(spending, "parse_generic_bank_csv", boom)
    r = _post(client, pid, CSV)
    assert r.status_code == 400 and "bad file" in r.json()["detail"]
    up = client.post(
        "/api/v1/spending/upload",
        data={"account_portfolio_id": str(pid)},
        files={"file": ("s.csv", io.BytesIO(CSV.encode()), "text/csv")},
        headers=HEADERS,
    )
    assert up.status_code == 422


def test_transfer_settling_failure_keeps_the_rows_and_says_so(tmp_path, monkeypatch):
    from portf_server.routers import spending

    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")

    def boom(_db, _ids):
        raise RuntimeError("matcher down")

    monkeypatch.setattr(spending, "settle_transfers", boom)
    r = _post(client, pid, CSV)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["imported"] == 4 and d["transfers_settled"] is False
    assert any("not matched" in w and "matcher down" in w for w in d["warnings"])
    assert len(db.list_spending_transactions(portfolio_id=pid)) == 4


def test_normal_import_reports_transfers_settled(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    assert _post(client, pid, CSV).json()["transfers_settled"] is True


def test_balance_check_ok_mismatch_and_unknown(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    assert _post(client, pid, CSV).json()["balance_check"] == "unknown"

    other = db.create_portfolio("Example Bank B", account_type="bank")
    good = (
        "date,description,amount,balance\n"
        "2026-02-01,EXAMPLE A,-10.00,990.00\n"
        "2026-02-02,EXAMPLE B,-20.00,970.00\n"
    )
    assert _post(client, other, good).json()["balance_check"] == "ok"

    third = db.create_portfolio("Example Bank C", account_type="bank")
    bad = (
        "date,description,amount,balance\n"
        "2026-02-01,EXAMPLE A,-10.00,990.00\n"
        "2026-02-02,EXAMPLE B,-20.00,500.00\n"
    )
    d = _post(client, third, bad).json()
    assert d["balance_check"] == "mismatch"
    assert d["imported"] == 2
    assert any("do not add up" in w for w in d["warnings"])


def test_import_runs_under_the_module_lock(tmp_path, monkeypatch):
    from portf_server.routers import spending

    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    seen = []
    real = spending._save_rows

    def watch(*args, **kwargs):
        seen.append(spending._IMPORT_LOCK.locked())
        return real(*args, **kwargs)

    monkeypatch.setattr(spending, "_save_rows", watch)
    assert _post(client, pid, CSV).status_code == 200
    assert seen == [True]
    assert not spending._IMPORT_LOCK.locked()
