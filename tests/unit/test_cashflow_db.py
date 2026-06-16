import pytest
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_create_and_list_cashflow(db):
    entry_id = db.create_monthly_cashflow("Salary", "salary", 3500.0, "EUR")
    assert entry_id > 0
    items = db.get_monthly_cashflow()
    assert len(items) == 1
    assert items[0]["label"] == "Salary"
    assert items[0]["category"] == "salary"
    assert items[0]["amount"] == 3500.0
    assert items[0]["currency"] == "EUR"


def test_cashflow_multiple_entries(db):
    db.create_monthly_cashflow("Salary", "salary", 3500.0, "EUR")
    db.create_monthly_cashflow("Mortgage", "mortgage", 1200.0, "EUR")
    items = db.get_monthly_cashflow()
    assert len(items) == 2


def test_delete_cashflow(db):
    entry_id = db.create_monthly_cashflow("Loan", "loan", 300.0, "EUR")
    assert db.delete_monthly_cashflow(entry_id) is True
    assert db.get_monthly_cashflow() == []


def test_delete_cashflow_missing_returns_false(db):
    assert db.delete_monthly_cashflow(999) is False


def test_cashflow_notes_optional(db):
    db.create_monthly_cashflow("Rest", "rest", 800.0, "EUR", notes="Groceries etc")
    items = db.get_monthly_cashflow()
    assert items[0]["notes"] == "Groceries etc"
