"""Schema v45: loan_rate_periods."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database, DatabaseError


def _columns(db, table):
    with db.get_connection() as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _make_v44_db(path):
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DROP TABLE loan_rate_periods")
    raw.execute(
        "INSERT INTO loans (name, principal, annual_rate_pct, start_date, "
        "term_months) VALUES ('Example Mortgage', 100000, 3, '2020-01-01', 240)"
    )
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (44)")
    raw.commit()
    raw.close()


def test_fresh_db_has_the_table():
    assert DATABASE_VERSION >= 45


def test_fresh_db_columns(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert {
        "id",
        "loan_id",
        "from_date",
        "annual_rate_pct",
        "balance_eur",
        "note",
        "created_at",
    } <= _columns(db, "loan_rate_periods")


def test_migration_adds_the_table_and_keeps_loans(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v44_db(path)
    db = Database(path)
    with db.get_connection() as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "loan_rate_periods" in tables
    # nothing is backfilled and the existing loan is untouched
    assert db.get_loan_rate_periods(1) == []
    assert db.get_loan(1)["name"] == "Example Mortgage"
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION


def test_unique_loan_and_date(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    lid = db.create_loan("Example Mortgage", 1000, 3, "2020-01-01", 12)
    db.create_loan_rate_period(lid, "2020-01-01", 2.0)
    with pytest.raises(DatabaseError):
        db.create_loan_rate_period(lid, "2020-01-01", 2.5)


def test_rerunning_the_migration_is_harmless(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        db._migrate_to_v45(conn)
    assert db.get_loan_rate_periods(1) == []
