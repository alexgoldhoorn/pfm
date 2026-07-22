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

    listed = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
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
    assert len(client.get("/api/v1/spending/", headers=HEADERS).json()["items"]) == 1


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
    assert len(client.get("/api/v1/spending/", headers=HEADERS).json()["items"]) == 2


def test_update_category(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "Transport"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert (
        client.get("/api/v1/spending/", headers=HEADERS).json()["items"][0]["category"]
        == "Transport"
    )


def test_update_category_missing_row(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.put("/api/v1/spending/999999", json={"category": "X"}, headers=HEADERS)
    assert r.status_code == 404


def test_update_category_blank_rejected(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "   "}, headers=HEADERS
    )
    assert r.status_code == 400
    unchanged = db.get_spending_transaction(tx_id)
    assert unchanged["category"] != ""


def test_update_category_trims_whitespace(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}",
        json={"category": "  Groceries  "},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["category"] == "Groceries"
    assert db.get_spending_transaction(tx_id)["category"] == "Groceries"


def test_update_category_clears_transfer_flag(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    id_a = db.create_spending_transaction(pid_a, "2026-01-05", "Transfer out", -100.0)
    id_b = db.create_spending_transaction(pid_b, "2026-01-06", "Transfer in", 100.0)
    # Link the two rows as a spending<->spending transfer pair, mirroring
    # what _run_transfer_matching does.
    db.update_spending_transaction(
        id_a,
        category="Transfer",
        is_transfer=True,
        transfer_link_type="spending",
        transfer_link_id=id_b,
    )
    db.update_spending_transaction(
        id_b,
        category="Transfer",
        is_transfer=True,
        transfer_link_type="spending",
        transfer_link_id=id_a,
    )

    r = client.put(
        f"/api/v1/spending/{id_a}", json={"category": "Groceries"}, headers=HEADERS
    )
    assert r.status_code == 200

    recategorized = db.get_spending_transaction(id_a)
    assert recategorized["category"] == "Groceries"
    assert recategorized["is_transfer"] == 0
    assert recategorized["transfer_link_type"] is None
    assert recategorized["transfer_link_id"] is None

    # The counterpart is deliberately left untouched by this endpoint.
    counterpart = db.get_spending_transaction(id_b)
    assert counterpart["is_transfer"] == 1
    assert counterpart["category"] == "Transfer"
    assert counterpart["transfer_link_id"] == id_a


def test_update_category_non_transfer_row_unaffected(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)

    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "Dining"}, headers=HEADERS
    )
    assert r.status_code == 200

    row = db.get_spending_transaction(tx_id)
    assert row["category"] == "Dining"
    assert row["is_transfer"] == 0
    assert row["transfer_link_type"] is None
    assert row["transfer_link_id"] is None


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

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
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


def test_rescan_categories_applies_new_rule_to_uncategorized_row(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "MERCADONA COMPRA", -24.50)

    before = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
    assert before[0]["category"] == "uncategorized"

    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["recategorized"] == 1

    after = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
    assert after[0]["category"] == "Groceries"


def test_rescan_categories_does_not_touch_already_categorized_row(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "MERCADONA COMPRA", -24.50, category="Dining"
    )
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.json()["recategorized"] == 0

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
    row = next(r for r in rows if r["id"] == tx_id)
    assert row["category"] == "Dining"


def test_rescan_categories_zero_matches(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "SOME SHOP", -10.0)

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.json()["recategorized"] == 0


def test_rescan_categories_scoped_to_ids(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    id_a = db.create_spending_transaction(pid, "2026-01-05", "MERCADONA COMPRA", -24.50)
    id_b = db.create_spending_transaction(
        pid, "2026-01-06", "MERCADONA COMPRA 2", -10.0
    )
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post(
        "/api/v1/spending/rescan-categories",
        json={"ids": [id_a]},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recategorized"] == 1

    rows = {
        r["id"]: r
        for r in client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
    }
    assert rows[id_a]["category"] == "Groceries"
    assert rows[id_b]["category"] == "uncategorized"


def test_rescan_categories_ids_scope_never_touches_already_categorized(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "MERCADONA COMPRA", -24.50, category="Dining"
    )
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post(
        "/api/v1/spending/rescan-categories",
        json={"ids": [tx_id]},
        headers=HEADERS,
    )
    assert resp.json()["recategorized"] == 0
    row = client.get("/api/v1/spending/", headers=HEADERS).json()["items"][0]
    assert row["category"] == "Dining"


def test_rescan_categories_empty_body_behaves_like_no_body(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "MERCADONA COMPRA", -24.50)
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post(
        "/api/v1/spending/rescan-categories",
        json={},
        headers=HEADERS,
    )
    assert resp.json()["recategorized"] == 1


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
    row = client.get("/api/v1/spending/", headers=HEADERS).json()["items"][0]
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

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
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


def test_create_rule_rejects_blank_pattern(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "   ", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_create_rule_rejects_exact_duplicate(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 409
    assert len(client.get("/api/v1/spending/rules", headers=HEADERS).json()) == 1


def test_create_rule_rejects_duplicate_case_insensitive_pattern(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "mercadona", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 409
    assert len(client.get("/api/v1/spending/rules", headers=HEADERS).json()) == 1


def test_create_rule_allows_same_pattern_different_category(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert len(client.get("/api/v1/spending/rules", headers=HEADERS).json()) == 2


def test_create_rule_allows_different_pattern_same_category(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "CARREFOUR", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert len(client.get("/api/v1/spending/rules", headers=HEADERS).json()) == 2


def test_blank_pattern_rule_does_not_match_everything(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    # Bypass the API's own validation to simulate a pre-existing blank-pattern
    # row (e.g. from before this fix existed) and confirm the matching
    # function itself is the defense, not just the API layer.
    db.create_spending_rule(pattern="", category="Groceries")
    db.create_spending_transaction(pid, "2026-01-05", "SOME UNRELATED SHOP", -10.0)

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.json()["recategorized"] == 0


def test_update_rule_pattern_and_category(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"pattern": "MERCAT", "category": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"id": rule_id, "pattern": "MERCAT", "category": "Food"}

    listed = client.get("/api/v1/spending/rules", headers=HEADERS).json()
    assert listed == [{"id": rule_id, "pattern": "MERCAT", "category": "Food"}]


def test_update_rule_pattern_only(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"pattern": "MERCAT"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"id": rule_id, "pattern": "MERCAT", "category": "Groceries"}


def test_update_rule_empty_body_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(f"/api/v1/spending/rules/{rule_id}", json={}, headers=HEADERS)
    assert r.status_code == 400


def test_update_rule_blank_pattern_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"pattern": "   "},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_update_rule_blank_category_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    rule_id = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    ).json()["id"]

    r = client.put(
        f"/api/v1/spending/rules/{rule_id}",
        json={"category": "   "},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_update_missing_rule(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.put(
        "/api/v1/spending/rules/999999",
        json={"pattern": "X"},
        headers=HEADERS,
    )
    assert r.status_code == 404


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
    assert client.get("/api/v1/spending/", headers=HEADERS).json()["items"] == []


def test_delete_spending_transaction_missing(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.delete("/api/v1/spending/999999", headers=HEADERS)
    assert r.status_code == 404


def test_upload_preview_includes_balance(tmp_path):
    client, _ = _make_client(tmp_path)
    csv_text = "date,description,amount,balance\n2026-01-05,MERCADONA,-24.50,475.50\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["rows"][0]["balance"] == 475.50


def test_upload_preview_balance_none_when_absent(tmp_path):
    client, _ = _make_client(tmp_path)
    csv_text = "date,description,amount\n2026-01-05,MERCADONA,-24.50\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.json()["rows"][0]["balance"] is None


def test_save_persists_balance(tmp_path):
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
                    "balance": 475.50,
                },
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["saved"] == 1
    listed = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
    assert listed[0]["balance"] == 475.50


def _aeb43_bytes(
    description: str, amount_cents: int, clave: str = "1", balance_cents: int = 100000
) -> bytes:
    """Build a minimal single-movement AEB43 file, encoded as Latin-1 bytes
    (real AEB43 exports are commonly Latin-1, not UTF-8)."""
    header = (
        "11"
        + "1234"
        + "0001"
        + "0000000001"
        + "260101"
        + "260101"
        + "2"
        + str(balance_cents).zfill(14)
        + "978"
        + "0"
        + "TEST".ljust(29)
    )
    movement = (
        "22"
        + "    "
        + "0000"
        + "260105"
        + "260105"
        + "00"
        + "000"
        + clave
        + str(amount_cents).zfill(14)
        + "0".zfill(8)
        + "0".zfill(12)
        + "0".zfill(18)
    )
    concept = "23" + "01" + description.ljust(76)[:76]
    trailer = "33" + " " * 78
    text = "\r\n".join([header, movement, concept, trailer]) + "\r\n"
    return text.encode("latin-1")


def test_upload_detects_aeb43_and_computes_balance(tmp_path):
    client, _ = _make_client(tmp_path)
    file_bytes = _aeb43_bytes("MERCADONA COMPRA", 2450, clave="1", balance_cents=100000)
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.n43", io.BytesIO(file_bytes), "text/plain")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert len(d["rows"]) == 1
    assert d["rows"][0]["amount"] == -24.50
    assert d["rows"][0]["balance"] == 975.50


def test_upload_aeb43_latin1_bytes_decoded_without_error(tmp_path):
    client, _ = _make_client(tmp_path)
    file_bytes = _aeb43_bytes("TRANSFERENCIA A: José González", 5000, clave="1")
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.n43", io.BytesIO(file_bytes), "text/plain")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["rows"][0]["description"] == "TRANSFERENCIA A: José González"


def test_suggest_prompt_instructs_stripping_transaction_noise():
    from portf_server.routers.spending import _build_suggest_prompt

    prompt = _build_suggest_prompt(["767002813178EXAMPLE MERCHANT\\CITY\\ES0000000019"])
    assert "card/transaction-reference" in prompt
    assert "location+date+reference" in prompt
    assert "767002813178EXAMPLE MERCHANT\\CITY\\ES0000000019" in prompt


def test_list_categories_includes_used_and_registered(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="Groceries"
    )
    db.create_spending_category("Vacation")

    r = client.get("/api/v1/spending/categories", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "Groceries" in body
    assert "Vacation" in body


def test_create_category(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Vacation"

    listed = client.get("/api/v1/spending/categories", headers=HEADERS).json()
    assert "Vacation" in listed


def test_create_category_rejects_blank_name(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories", json={"name": "   "}, headers=HEADERS
    )
    assert r.status_code == 400


def test_create_category_rejects_exact_duplicate(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    r = client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    assert r.status_code == 409


def test_rename_category_cascades_to_transactions_and_rules(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="Groceries"
    )
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["transactions_updated"] == 1
    assert body["rules_updated"] == 1

    assert db.get_spending_transaction(tx_id)["category"] == "Food"


def test_rename_category_rejects_blank_new_name(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_spending_category("Groceries")
    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "   "},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_rename_category_rejects_same_name(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_spending_category("Groceries")
    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_rename_category_merges_into_existing_name(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="Groceries"
    )
    db.create_spending_category("Food")

    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200


def test_list_spending_pagination_shape_and_total(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    for i in range(5):
        db.create_spending_transaction(pid, f"2026-01-{i + 1:02d}", f"Desc {i}", -10.0)

    r = client.get("/api/v1/spending/?limit=2&offset=0", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_list_spending_pagination_offset_advances(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    for i in range(5):
        db.create_spending_transaction(pid, f"2026-01-{i + 1:02d}", f"Desc {i}", -10.0)

    page1 = client.get(
        "/api/v1/spending/?limit=2&offset=0&sort_by=date&sort_dir=asc", headers=HEADERS
    ).json()
    page2 = client.get(
        "/api/v1/spending/?limit=2&offset=2&sort_by=date&sort_dir=asc", headers=HEADERS
    ).json()
    ids1 = {r["id"] for r in page1["items"]}
    ids2 = {r["id"] for r in page2["items"]}
    assert ids1.isdisjoint(ids2)


def test_list_spending_sort_by_amount_asc(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-01", "Big", -100.0)
    db.create_spending_transaction(pid, "2026-01-02", "Small", -5.0)

    r = client.get(
        "/api/v1/spending/?limit=10&offset=0&sort_by=amount&sort_dir=asc",
        headers=HEADERS,
    )
    items = r.json()["items"]
    assert [i["description"] for i in items] == ["Big", "Small"]


def test_list_spending_total_respects_filters(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-01", "A", -10.0, category="Groceries")
    db.create_spending_transaction(pid, "2026-01-02", "B", -10.0, category="Dining")

    r = client.get(
        "/api/v1/spending/?limit=10&offset=0&category=Groceries", headers=HEADERS
    )
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


def test_list_spending_invalid_sort_by_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/v1/spending/?sort_by=not_a_column", headers=HEADERS)
    assert r.status_code == 400


def test_list_spending_invalid_limit_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/v1/spending/?limit=0", headers=HEADERS)
    assert r.status_code == 422  # FastAPI Query validation
