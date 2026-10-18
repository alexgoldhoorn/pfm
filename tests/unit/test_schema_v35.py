"""Schema v35: manual_assets.as_of, loans, account_valuations, retirement_incomes."""

import sqlite3
from datetime import date

import pytest

from portf_manager.database import Database


def _db(tmp_path) -> Database:
    return Database(str(tmp_path / "t.db"))


def test_fresh_db_is_v35_and_has_tables(tmp_path):
    db = _db(tmp_path)
    with db.get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_assets)")}
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == 35
    assert {"loans", "account_valuations", "retirement_incomes"} <= names
    assert "as_of" in cols


def test_migration_backfills_as_of_from_updated_at(tmp_path):
    path = str(tmp_path / "t.db")
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE manual_assets DROP COLUMN as_of")
    raw.execute("DROP TABLE loans")
    raw.execute("DROP TABLE account_valuations")
    raw.execute("DROP TABLE retirement_incomes")
    raw.execute(
        "INSERT INTO manual_assets (name, updated_at) VALUES ('Old', '2026-03-04 10:00:00')"
    )
    raw.execute("INSERT INTO database_version (version) VALUES (34)")
    raw.execute("DELETE FROM database_version WHERE version = 35")
    raw.commit()
    raw.close()

    db = Database(path)
    assets = db.get_manual_assets()
    assert [a["as_of"] for a in assets] == ["2026-03-04"]
    assert db.get_loans() == []
    assert db.get_retirement_incomes() == []
    assert db.get_account_valuations() == []


def test_manual_asset_as_of_defaults_to_today(tmp_path):
    db = _db(tmp_path)
    a = db.create_manual_asset("Cash", "cash", 100)
    b = db.create_manual_asset("Flat", "property", 1, as_of="2025-01-02")
    rows = {r["id"]: r for r in db.get_manual_assets()}
    assert rows[a]["as_of"] == date.today().isoformat()
    assert rows[b]["as_of"] == "2025-01-02"
    assert db.update_manual_asset(b, as_of="2026-02-03")
    assert db.get_manual_assets()[1]["as_of"] == "2026-02-03"


def test_loan_crud_roundtrip(tmp_path):
    db = _db(tmp_path)
    mid = db.create_loan("Example Mortgage", 100000, 3.0, "2026-01-01", 240)
    assert db.get_loan(mid)["extra_payment_eur"] == 0.0
    assert db.update_loan(
        mid, annual_rate_pct=3.5, rate_as_of="2027-01-01", balance_as_of=96000
    )
    loan = db.get_loan(mid)
    assert loan["annual_rate_pct"] == 3.5 and loan["balance_as_of"] == 96000
    assert not db.update_loan(mid, bogus=1)
    assert db.delete_loan(mid) and db.get_loans() == []
    assert db.get_loan(mid) is None


def test_account_valuation_upsert_replaces_row(tmp_path):
    db = _db(tmp_path)
    pid = db.get_or_create_portfolio("Example Broker")
    db.upsert_account_valuation(
        pid, "indexa", "ABC", 1000.0, 50.0, "EUR", "2026-10-01", "2026-10-01T06:00"
    )
    db.upsert_account_valuation(
        pid,
        "indexa",
        "ABC",
        1200.0,
        60.0,
        "EUR",
        "2026-10-02",
        "2026-10-02T06:00",
        last_error="boom",
    )
    rows = db.get_account_valuations()
    assert len(rows) == 1
    assert rows[0]["value"] == 1200.0 and rows[0]["last_error"] == "boom"
    assert db.get_account_valuation(pid)["as_of"] == "2026-10-02"
    assert db.delete_account_valuation(pid)
    assert db.get_account_valuation(pid) is None


def test_retirement_income_crud(tmp_path):
    db = _db(tmp_path)
    iid = db.create_retirement_income("State pension", "self", 1000, 67)
    row = db.get_retirement_incomes()[0]
    assert row["indexed"] == 1 and row["to_age"] is None
    assert db.update_retirement_income(iid, monthly_eur=1100, to_age=90)
    assert db.get_retirement_incomes()[0]["monthly_eur"] == 1100
    assert db.delete_retirement_income(iid)


def test_retirement_income_owner_is_checked(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(Exception):
        db.create_retirement_income("x", "dog", 100, 67)


def test_update_retirement_income_none_clears_nullable(tmp_path):
    db = _db(tmp_path)
    iid = db.create_retirement_income("State pension", "self", 1000, 67)
    db.update_retirement_income(iid, to_age=90, notes="x")
    assert db.update_retirement_income(iid, to_age=None, notes=None)
    row = db.get_retirement_incomes()[0]
    assert row["to_age"] is None and row["notes"] is None


def test_update_loan_none_clears_nullable(tmp_path):
    db = _db(tmp_path)
    mid = db.create_loan("Example Mortgage", 100000, 3.0, "2026-01-01", 240)
    db.update_loan(mid, balance_as_of=96000)
    assert db.update_loan(mid, balance_as_of=None)
    assert db.get_loan(mid)["balance_as_of"] is None
