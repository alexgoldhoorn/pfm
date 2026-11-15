"""Schema v43: spending_transactions.one_off, budget_lines.period and rollover."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database


def _columns(db, table):
    with db.get_connection() as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _make_v42_db(path):
    """A database shaped like v42: none of the three columns."""
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("ALTER TABLE spending_transactions DROP COLUMN one_off")
    raw.execute("ALTER TABLE budget_lines DROP COLUMN period")
    raw.execute("ALTER TABLE budget_lines DROP COLUMN rollover")
    raw.execute("INSERT INTO portfolios (name) VALUES ('Example Bank')")
    raw.execute(
        "INSERT INTO spending_transactions (portfolio_id, date, description, amount) "
        "VALUES (1, '2026-01-05', 'Example Gym', -30)"
    )
    raw.execute("INSERT INTO budgets (name) VALUES ('Example Budget')")
    raw.execute(
        "INSERT INTO budget_lines (budget_id, line_type, ref_key, monthly_amount) "
        "VALUES (1, 'spending', 'Groceries', 400)"
    )
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (42)")
    raw.commit()
    raw.close()


def test_fresh_db_has_all_three_columns(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert DATABASE_VERSION >= 43
    assert "one_off" in _columns(db, "spending_transactions")
    assert {"period", "rollover"} <= _columns(db, "budget_lines")


def test_migration_adds_columns_with_defaults_and_backfills_nothing(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v42_db(path)
    db = Database(path)
    assert "one_off" in _columns(db, "spending_transactions")
    assert db.get_spending_transaction(1)["one_off"] == 0
    line = db.get_budget_line(1)
    assert line["period"] == "monthly"
    assert line["rollover"] == 0
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION


def test_migration_is_rerunnable(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v42_db(path)
    db = Database(path)
    db.update_spending_transaction(1, one_off=1)
    with db.get_connection() as conn:
        db._migrate_to_v43(conn)
    assert db.get_spending_transaction(1)["one_off"] == 1


def test_period_is_checked(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    budget = db.create_budget("Example Budget")
    with pytest.raises(Exception):
        db.create_budget_line(budget, "spending", "Groceries", 10, period="weekly")


def test_budget_line_period_and_rollover_round_trip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    budget = db.create_budget("Example Budget")
    lid = db.create_budget_line(
        budget, "spending", "Insurance", 1200, period="yearly", rollover=True
    )
    line = db.get_budget_line(lid)
    assert (line["period"], line["rollover"]) == ("yearly", 1)
    # a grid save that omits the new keys leaves them alone
    db.upsert_budget_lines(
        budget, [{"line_type": "spending", "ref_key": "Insurance", "monthly_amount": 1}]
    )
    line = db.get_budget_line(lid)
    assert (line["period"], line["rollover"], line["monthly_amount"]) == (
        "yearly",
        1,
        1,
    )
    db.update_budget_line(lid, period="monthly", rollover=False)
    line = db.get_budget_line(lid)
    assert (line["period"], line["rollover"]) == ("monthly", 0)


def test_one_off_update_and_filter(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    bank = db.create_portfolio("Example Bank", account_type="bank")
    a = db.create_spending_transaction(bank, "2026-01-05", "Example Gym", -30.0)
    db.create_spending_transaction(bank, "2026-01-06", "Sample Energy", -50.0)
    assert db.update_spending_transaction(a, one_off=True) is True
    assert [r["id"] for r in db.list_spending_transactions(one_off=True)] == [a]
    assert len(db.list_spending_transactions(one_off=False)) == 1
    assert db.count_spending_transactions(one_off=True) == 1
