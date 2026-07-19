"""API tests for the spending router (upload/save/list/update/rescan/rules/summary)."""

import io
from datetime import date, timedelta

from fastapi.testclient import TestClient
from portf_manager.database import Database

_TEST_API_KEY = "test-key-spending-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.dependencies import get_database, get_api_key_manager
    from portf_server.auth_middleware import APIKeyManager

    db_instance = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db_instance)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db_instance
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db_instance


def _csv_bytes(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def test_upload_creates_bank_account_and_categorizes(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    csv_text = "date,description,amount\n2026-01-05,MERCADONA COMPRA,-24.50\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["rows"][0]["category"] == "Groceries"
    assert d["duplicate_count"] == 0
    assert d["account_portfolio_id"] > 0

    portfolios = client.get("/api/v1/portfolios/", headers=HEADERS).json()
    bank = next(p for p in portfolios if p["name"] == "Example Bank")
    assert bank["account_type"] == "bank"


def test_upload_requires_account(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/upload",
        files={"file": ("s.csv", _csv_bytes("date,description,amount\n"), "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_save_and_list(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    r = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid,
            "rows": [
                {
                    "date": "2026-01-05",
                    "description": "MERCADONA",
                    "amount": -24.50,
                    "currency": "EUR",
                    "category": "Groceries",
                },
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["saved"] == 1
    assert d["duplicates_skipped"] == 0

    listed = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert len(listed) == 1
    assert listed[0]["description"] == "MERCADONA"


def test_save_skips_duplicates_by_default(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    row = {
        "date": "2026-01-05",
        "description": "MERCADONA",
        "amount": -24.50,
        "currency": "EUR",
        "category": "Groceries",
    }
    client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row]},
        headers=HEADERS,
    )
    r2 = client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row]},
        headers=HEADERS,
    )
    d2 = r2.json()
    assert d2["saved"] == 0
    assert d2["duplicates_skipped"] == 1
    assert len(client.get("/api/v1/spending/", headers=HEADERS).json()) == 1


def test_save_add_duplicate_anyway(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    row = {
        "date": "2026-01-05",
        "description": "MERCADONA",
        "amount": -24.50,
        "currency": "EUR",
        "category": "Groceries",
    }
    client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row]},
        headers=HEADERS,
    )
    r2 = client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row], "duplicate_action": "add"},
        headers=HEADERS,
    )
    assert r2.json()["saved"] == 1
    assert len(client.get("/api/v1/spending/", headers=HEADERS).json()) == 2


def test_update_category(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "Transport"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert (
        client.get("/api/v1/spending/", headers=HEADERS).json()[0]["category"]
        == "Transport"
    )


def test_update_category_missing_row(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.put("/api/v1/spending/999999", json={"category": "X"}, headers=HEADERS)
    assert r.status_code == 404


def test_save_auto_links_transfer_between_two_accounts(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")

    client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid_a,
            "rows": [
                {
                    "date": "2026-01-10",
                    "description": "TRASPASO A AHORRO",
                    "amount": -500.0,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ],
        },
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid_b,
            "rows": [
                {
                    "date": "2026-01-11",
                    "description": "TRASPASO",
                    "amount": 500.0,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ],
        },
        headers=HEADERS,
    )
    assert r.json()["transfers_linked"] == 1

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert all(row["is_transfer"] for row in rows)
    assert all(row["category"] == "Transfer" for row in rows)


def test_rescan_transfers(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    db.create_spending_transaction(pid_a, "2026-01-10", "Out", -500.0)
    db.create_spending_transaction(pid_b, "2026-01-11", "In", 500.0)

    r = client.post("/api/v1/spending/rescan-transfers", headers=HEADERS)
    assert r.json()["transfers_linked"] == 1


def test_transfer_to_brokerage_booking(tmp_path):
    client, db = _make_client(tmp_path)
    pid_bank = db.create_portfolio("Bank A", account_type="bank")
    pid_broker = db.create_portfolio("Example Broker", account_type="brokerage")
    db.create_booking(
        date="2026-01-10",
        action="Deposit",
        amount=1000.0,
        currency="EUR",
        portfolio_id=pid_broker,
    )

    r = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid_bank,
            "rows": [
                {
                    "date": "2026-01-10",
                    "description": "TRANSFERENCIA A BROKER",
                    "amount": -1000.0,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ],
        },
        headers=HEADERS,
    )
    assert r.json()["transfers_linked"] == 1
    row = client.get("/api/v1/spending/", headers=HEADERS).json()[0]
    assert row["is_transfer"] is True
    assert row["transfer_link_type"] == "booking"


def test_booking_not_reused_across_separate_save_calls(tmp_path):
    """Regression test: a Deposit booking already claimed as a transfer
    counterpart in one /save call must not be claimed again by an unrelated
    outflow in a later /save call.

    Two different bank accounts each send a -1000 EUR outflow within the
    +/-3 day match window of a single brokerage Deposit booking. Only the
    first outflow (bank outflow A) may legitimately link to that booking —
    the second (bank outflow B, from a different account, a genuinely
    separate expense) must stay unlinked rather than wrongly reusing it.
    """
    client, db = _make_client(tmp_path)
    pid_bank_a = db.create_portfolio("Bank A", account_type="bank")
    pid_bank_b = db.create_portfolio("Bank B", account_type="bank")
    pid_broker = db.create_portfolio("Example Broker", account_type="brokerage")
    booking_id = db.create_booking(
        date="2026-01-10",
        action="Deposit",
        amount=1000.0,
        currency="EUR",
        portfolio_id=pid_broker,
    )

    r1 = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid_bank_a,
            "rows": [
                {
                    "date": "2026-01-10",
                    "description": "TRANSFERENCIA A BROKER",
                    "amount": -1000.0,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ],
        },
        headers=HEADERS,
    )
    assert r1.json()["transfers_linked"] == 1

    r2 = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid_bank_b,
            "rows": [
                {
                    "date": "2026-01-12",
                    "description": "UNRELATED EXPENSE",
                    "amount": -1000.0,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ],
        },
        headers=HEADERS,
    )
    # The second, unrelated outflow must NOT wrongly claim the
    # already-linked booking.
    assert r2.json()["transfers_linked"] == 0

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()
    linked_to_booking = [
        row
        for row in rows
        if row["transfer_link_type"] == "booking"
        and row["transfer_link_id"] == booking_id
    ]
    # Only bank outflow A should be linked to the booking.
    assert len(linked_to_booking) == 1
    assert linked_to_booking[0]["description"] == "TRANSFERENCIA A BROKER"

    unrelated = next(row for row in rows if row["description"] == "UNRELATED EXPENSE")
    assert unrelated["is_transfer"] is False
    assert unrelated["category"] == "uncategorized"


def test_rules_crud(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    rule_id = r.json()["id"]

    listed = client.get("/api/v1/spending/rules", headers=HEADERS).json()
    assert len(listed) == 1

    r2 = client.delete(f"/api/v1/spending/rules/{rule_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert client.get("/api/v1/spending/rules", headers=HEADERS).json() == []


def test_delete_missing_rule(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.delete("/api/v1/spending/rules/999999", headers=HEADERS)
    assert r.status_code == 404


def test_summary_excludes_transfers(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    # Dates relative to today so they stay inside the summary's `days=30`
    # window regardless of when the suite runs (the endpoint filters on
    # date.today() - timedelta(days=days)).
    d1 = (date.today() - timedelta(days=5)).isoformat()
    d2 = (date.today() - timedelta(days=4)).isoformat()
    d3 = (date.today() - timedelta(days=3)).isoformat()
    db.create_spending_transaction(pid, d1, "Groceries", -24.50, category="Groceries")
    db.create_spending_transaction(pid, d2, "Salary", 2000.0, category="uncategorized")
    tx_transfer = db.create_spending_transaction(pid, d3, "Transfer", -500.0)
    db.update_spending_transaction(tx_transfer, category="Transfer", is_transfer=True)

    r = client.get("/api/v1/spending/summary?days=30", headers=HEADERS)
    d = r.json()
    assert d["spent_eur"] == 24.50
    assert d["income_eur"] == 2000.0
    assert d["transferred_eur"] == 500.0
    assert d["by_category_eur"]["Groceries"] == 24.50


def test_suggest_categories(tmp_path, mocker):
    from unittest.mock import MagicMock

    client, _ = _make_client(tmp_path)
    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.return_value = (
        '[{"description": "MERCADONA COMPRA", "category": "Groceries", '
        '"suggested_pattern": "MERCADONA"}]'
    )
    mocker.patch("portf_server.routers.spending.get_llm_client", return_value=mock_llm)

    r = client.post(
        "/api/v1/spending/suggest-categories",
        json={
            "rows": [
                {
                    "date": "2026-01-05",
                    "description": "MERCADONA COMPRA",
                    "amount": -24.50,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ]
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["suggestions"][0]["category"] == "Groceries"
    assert d["suggestions"][0]["suggested_pattern"] == "MERCADONA"


def test_suggest_categories_empty_rows_skips_llm_call(tmp_path, mocker):
    client, _ = _make_client(tmp_path)
    spy = mocker.patch("portf_server.routers.spending.get_llm_client")
    r = client.post(
        "/api/v1/spending/suggest-categories", json={"rows": []}, headers=HEADERS
    )
    assert r.json()["suggestions"] == []
    spy.assert_not_called()


def test_suggest_categories_llm_failure_returns_502(tmp_path, mocker):
    from unittest.mock import MagicMock

    client, _ = _make_client(tmp_path)
    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.side_effect = RuntimeError("LLM unavailable")
    mocker.patch("portf_server.routers.spending.get_llm_client", return_value=mock_llm)

    r = client.post(
        "/api/v1/spending/suggest-categories",
        json={
            "rows": [
                {
                    "date": "2026-01-05",
                    "description": "X",
                    "amount": -1.0,
                    "currency": "EUR",
                    "category": "uncategorized",
                },
            ]
        },
        headers=HEADERS,
    )
    assert r.status_code == 502


def test_delete_spending_transaction(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.delete(f"/api/v1/spending/{tx_id}", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "id": tx_id}
    assert client.get("/api/v1/spending/", headers=HEADERS).json() == []


def test_delete_spending_transaction_missing(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.delete("/api/v1/spending/999999", headers=HEADERS)
    assert r.status_code == 404
