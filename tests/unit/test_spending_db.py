"""Tests for the spending-tracking DB layer (spending_transactions, spending_rules,
portfolios.account_type)."""

import pytest
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_portfolio_defaults_to_brokerage(db):
    pid = db.create_portfolio("Example Broker")
    p = db.get_portfolio(pid)
    assert p["account_type"] == "brokerage"


def test_create_bank_portfolio(db):
    pid = db.create_portfolio("Example Bank Checking", account_type="bank")
    p = db.get_portfolio(pid)
    assert p["account_type"] == "bank"


def test_get_or_create_portfolio_bank_type(db):
    pid1 = db.get_or_create_portfolio("Example Bank", account_type="bank")
    pid2 = db.get_or_create_portfolio("Example Bank", account_type="bank")
    assert pid1 == pid2
    assert db.get_portfolio(pid1)["account_type"] == "bank"


def test_create_and_list_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        portfolio_id=pid,
        date="2026-01-05",
        description="MERCADONA COMPRA",
        amount=-24.50,
        currency="EUR",
        category="Groceries",
        source="generic",
    )
    assert tx_id > 0
    rows = db.list_spending_transactions()
    assert len(rows) == 1
    assert rows[0]["description"] == "MERCADONA COMPRA"
    assert rows[0]["amount"] == -24.50
    assert rows[0]["category"] == "Groceries"
    assert rows[0]["is_transfer"] == 0
    assert rows[0]["portfolio_name"] == "Example Bank"


def test_spending_transaction_defaults(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        portfolio_id=pid,
        date="2026-01-05",
        description="NOMINA",
        amount=2100.0,
    )
    row = db.get_spending_transaction(tx_id)
    assert row["currency"] == "EUR"
    assert row["category"] == "uncategorized"


def test_list_spending_transactions_filters(db):
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    db.create_spending_transaction(
        pid_a, "2026-01-01", "Groceries A", -10.0, category="Groceries"
    )
    db.create_spending_transaction(
        pid_b, "2026-02-01", "Dining B", -20.0, category="Dining"
    )

    assert len(db.list_spending_transactions(portfolio_id=pid_a)) == 1
    assert len(db.list_spending_transactions(category="Dining")) == 1
    assert len(db.list_spending_transactions(start_date="2026-02-01")) == 1
    assert len(db.list_spending_transactions(end_date="2026-01-01")) == 1
    assert len(db.list_spending_transactions()) == 2


def test_find_duplicate_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "MERCADONA COMPRA", -24.50)
    dup = db.find_duplicate_spending_transaction(
        portfolio_id=pid,
        date="2026-01-05",
        amount=-24.50,
        description="MERCADONA COMPRA",
    )
    assert dup is not None
    no_dup = db.find_duplicate_spending_transaction(
        portfolio_id=pid,
        date="2026-01-06",
        amount=-24.50,
        description="MERCADONA COMPRA",
    )
    assert no_dup is None


def test_update_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    assert db.update_spending_transaction(tx_id, category="Transport") is True
    assert db.get_spending_transaction(tx_id)["category"] == "Transport"
    assert db.update_spending_transaction(999999, category="X") is False


def test_update_spending_transaction_transfer_link(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    db.update_spending_transaction(
        tx_id,
        category="Transfer",
        is_transfer=True,
        transfer_link_type="booking",
        transfer_link_id=42,
    )
    row = db.get_spending_transaction(tx_id)
    assert row["is_transfer"] == 1
    assert row["transfer_link_type"] == "booking"
    assert row["transfer_link_id"] == 42


def test_list_unlinked_spending_transactions(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    id1 = db.create_spending_transaction(pid, "2026-01-05", "A", -10.0)
    id2 = db.create_spending_transaction(pid, "2026-01-06", "B", -20.0)
    db.update_spending_transaction(id1, is_transfer=True)
    unlinked = db.list_unlinked_spending_transactions()
    ids = [r["id"] for r in unlinked]
    assert id1 not in ids
    assert id2 in ids


def test_spending_rules_crud(db):
    rule_id = db.create_spending_rule(pattern="MERCADONA", category="Groceries")
    assert rule_id > 0
    rules = db.list_spending_rules()
    assert len(rules) == 1
    assert rules[0]["pattern"] == "MERCADONA"
    assert rules[0]["category"] == "Groceries"
    assert db.delete_spending_rule(rule_id) is True
    assert db.list_spending_rules() == []


def test_delete_spending_rule_missing_returns_false(db):
    assert db.delete_spending_rule(999999) is False


def test_delete_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    assert db.delete_spending_transaction(tx_id) is True
    assert db.get_spending_transaction(tx_id) is None


def test_delete_spending_transaction_missing_returns_false(db):
    assert db.delete_spending_transaction(999999) is False
