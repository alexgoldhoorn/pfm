"""Uncategorised queue, classify wrapper, review field and Action Item."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import action_items, spending_uncategorized
from portf_manager.services import spending_insights as si

_KEY = "test-key-uncategorized-abc123"
HEADERS = {"X-API-Key": _KEY}


def _last_month():
    return si.previous_month(date.today())


@pytest.fixture
def env(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "uncat.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km

    bank = db.create_portfolio("Example Bank", account_type="bank")
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Groceries", parent_id=spend)
    month = _last_month()
    add = db.create_spending_transaction
    add(bank, f"{month}-03", "EXAMPLE MARKET", -800.0, category="Groceries")
    add(bank, f"{month}-05", "EXAMPLE COFFEE", -30.0)
    add(bank, f"{month}-12", "EXAMPLE COFFEE", -30.0)
    add(bank, f"{month}-20", "UNKNOWN CORP", -170.0)
    add(bank, f"{month}-21", "EXAMPLE REFUND", 40.0)
    # data reaches into the current month, so last month is complete
    add(bank, date.today().isoformat(), "EXAMPLE MARKET", -10.0, category="Groceries")
    yield TestClient(app), db, bank
    app.dependency_overrides.clear()


def test_queue_groups_by_merchant_and_orders_by_money(env):
    client, _db, _bank = env
    d = client.get("/api/v1/spending/uncategorized-queue", headers=HEADERS).json()
    assert [g["merchant"] for g in d["groups"]] == [
        "UNKNOWN CORP",
        "EXAMPLE COFFEE",
        "EXAMPLE REFUND",
    ]
    unknown, coffee, refund = d["groups"]
    assert unknown["direction"] == "out" and unknown["total_eur"] == 170.0
    assert coffee["count"] == 2 and coffee["total_eur"] == 60.0
    assert len(coffee["transaction_ids"]) == 2
    assert coffee["proposed_pattern"] == "EXAMPLE COFFEE"
    assert coffee["account_names"] == ["Example Bank"]
    assert coffee["sample_descriptions"] == ["EXAMPLE COFFEE"]
    assert refund["direction"] == "in"
    assert d["total_count"] == 4
    assert d["total_eur"] == 270.0
    # outflows only: 230 of (800 + 230 + 10)
    assert d["share_of_spending_pct"] == pytest.approx(22.1, abs=0.1)
    last = d["last_complete_month"]
    assert last["month"] == _last_month()
    assert last["count"] == 3
    assert last["total_eur"] == 230.0
    assert last["share_pct"] == pytest.approx(22.3, abs=0.1)


def test_queue_limit_and_validation(env):
    client, _db, _bank = env
    d = client.get(
        "/api/v1/spending/uncategorized-queue?limit=1", headers=HEADERS
    ).json()
    assert len(d["groups"]) == 1 and d["total_count"] == 4
    assert (
        client.get(
            "/api/v1/spending/uncategorized-queue?limit=0", headers=HEADERS
        ).status_code
        == 422
    )


def test_groups_without_a_merchant_use_a_common_description_prefix(env):
    _client, db, bank = env
    rows = db.list_spending_transactions(category="uncategorized")
    for r in rows:
        db.update_spending_transaction(r["id"], merchant="")
    d = spending_uncategorized.build_queue(
        si.load_rows(db, lambda c: 1.0),
        {r["name"]: r for r in db.list_spending_categories_tree()},
        date.today(),
    )
    coffee = next(g for g in d["groups"] if "COFFEE" in g["key"])
    assert coffee["proposed_pattern"] == "EXAMPLE COFFEE"
    assert coffee["merchant"]


def test_classify_updates_rows_and_adds_a_rule(env):
    client, db, bank = env
    d = client.get("/api/v1/spending/uncategorized-queue", headers=HEADERS).json()
    coffee = next(g for g in d["groups"] if g["merchant"] == "EXAMPLE COFFEE")
    later = db.create_spending_transaction(
        bank, f"{_last_month()}-25", "EXAMPLE COFFEE", -5.0
    )
    r = client.post(
        "/api/v1/spending/uncategorized-queue/classify",
        json={
            "transaction_ids": coffee["transaction_ids"],
            "category": "Groceries",
            "create_rule": True,
            "pattern": "EXAMPLE COFFEE",
        },
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 2 and body["skipped"] == 0
    assert body["rule_created"] is True and body["rule_id"]
    # the new rule also files the coffee that was not in the request
    assert body["recategorized_by_rule"] == 1
    assert db.get_spending_transaction(later)["category"] == "Groceries"
    # a second call reuses the rule and skips rows that already have a category
    again = client.post(
        "/api/v1/spending/uncategorized-queue/classify",
        json={
            "transaction_ids": coffee["transaction_ids"],
            "category": "Groceries",
            "create_rule": True,
            "pattern": "example coffee",
        },
        headers=HEADERS,
    ).json()
    assert again["updated"] == 0 and again["skipped"] == 2
    assert again["rule_created"] is False and again["rule_id"] == body["rule_id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"transaction_ids": [], "category": "Groceries"},
        {"transaction_ids": [1], "category": "Transfer"},
        {"transaction_ids": [1], "category": "Groceries", "create_rule": True},
        {"transaction_ids": [99999], "category": "Groceries"},
    ],
)
def test_classify_rejects_bad_input_and_writes_nothing(env, payload):
    client, db, _bank = env
    before = db.list_spending_rules()
    r = client.post(
        "/api/v1/spending/uncategorized-queue/classify",
        json=payload,
        headers=HEADERS,
    )
    assert r.status_code == 400
    assert db.list_spending_rules() == before


def test_classify_checks_the_sign_before_writing_anything(env):
    client, db, _bank = env
    d = client.get("/api/v1/spending/uncategorized-queue", headers=HEADERS).json()
    ids = [i for g in d["groups"] for i in g["transaction_ids"]]
    # the refund is a credit: a Spend category cannot hold it
    r = client.post(
        "/api/v1/spending/uncategorized-queue/classify",
        json={"transaction_ids": ids, "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 400
    assert len(db.list_spending_transactions(category="uncategorized")) == 4


def test_review_carries_the_uncategorized_share_and_a_highlight(env):
    client, _db, _bank = env
    d = client.get("/api/v1/spending/review", headers=HEADERS).json()
    u = d["uncategorized"]
    assert u["count"] == 3 and u["total_eur"] == 230.0
    assert u["share_pct"] == pytest.approx(22.3, abs=0.1)
    assert "22%" in u["highlight"]


def test_review_has_no_uncategorized_figures_for_a_month_without_imports(env):
    client, _db, _bank = env
    month = si.shift_month(_last_month(), -3)
    d = client.get(f"/api/v1/spending/review?month={month}", headers=HEADERS).json()
    assert d["uncategorized"] is None


def test_action_item_is_raised_above_the_threshold(env):
    _client, db, _bank = env
    items = action_items.check_uncategorized_spending(db)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == f"spending:uncategorized:{_last_month()}"
    assert item["context"]["month"] == _last_month()
    assert item["context"]["total_eur"] == 230.0
    assert "UNKNOWN CORP" in item["detail"]
    assert action_items.check_uncategorized_spending in action_items.checks_for_tests()


def test_action_item_stays_quiet_when_the_share_and_total_are_small(env):
    _client, db, bank = env
    for r in db.list_spending_transactions(category="uncategorized"):
        db.update_spending_transaction(r["id"], category="Groceries")
    db.create_spending_transaction(bank, f"{_last_month()}-28", "EXAMPLE TINY", -10.0)
    assert action_items.check_uncategorized_spending(db) == []
