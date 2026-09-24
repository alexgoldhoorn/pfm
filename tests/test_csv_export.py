"""Tests for the CLI's filtered-transactions CSV export."""

import csv
from datetime import date

import pytest

from portf_manager.auth import AuthManager
from portf_manager.csv_export import TransactionCSVExporter, create_csv_exporter
from portf_manager.database import Database
from portf_manager.transaction_filter import TransactionFilter


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "export.db"))
    pid = database.get_or_create_portfolio("Example Broker")
    rows = [
        ("EXA", "Example Corp", "2024-01-01", 10.0, 12.5),
        ("EXA", "Example Corp", "2024-12-31", 2.0, 13.0),
        ("OTH", "Other Fund", "2025-01-01", 1.5, 100.0),
    ]
    for symbol, name, day, qty, price in rows:
        asset = database.get_asset_by_symbol(symbol)
        aid = asset["id"] if asset else database.create_asset(symbol, name, "stock")
        # Imports never set user_id, so rows carry NULL like real data
        database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=qty,
            price=price,
            total_amount=qty * price,
            transaction_date=day,
            portfolio_id=pid,
            currency="EUR",
        )
    return database


@pytest.fixture
def exporter(db):
    auth = AuthManager(db)
    auth.register_user("alice", "alice@example.com", "s3cret-pass")
    auth.login("alice", "s3cret-pass")
    return create_csv_exporter(db, auth)


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_exports_all_rows_with_fixed_headers(exporter, tmp_path):
    out = tmp_path / "all.csv"
    result = exporter.export_with_summary(TransactionFilter(), str(out))

    assert result["success"] is True
    assert result["transaction_count"] == 3
    with open(out, encoding="utf-8") as f:
        assert next(csv.reader(f)) == TransactionCSVExporter.CSV_HEADERS
    rows = _read(out)
    assert {r["Portfolio Name"] for r in rows} == {"Example Broker"}
    assert {r["Date"] for r in rows} >= {"2024-01-01T00:00:00"}


def test_symbol_filter_is_case_insensitive(exporter, tmp_path):
    out = tmp_path / "exa.csv"
    exporter.export_with_summary(TransactionFilter(symbol="exa"), str(out))
    assert {r["Symbol"] for r in _read(out)} == {"EXA"}
    assert len(_read(out)) == 2


def test_date_range_is_inclusive_at_both_ends(exporter, tmp_path):
    out = tmp_path / "2024.csv"
    criteria = TransactionFilter(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        timezone="America/New_York",
    )
    result = exporter.export_with_summary(criteria, str(out))
    assert result["transaction_count"] == 2
    assert sorted(r["Date"][:10] for r in _read(out)) == ["2024-01-01", "2024-12-31"]


def test_no_match_writes_no_file(exporter, tmp_path):
    out = tmp_path / "none.csv"
    result = exporter.export_with_summary(TransactionFilter(symbol="NOPE"), str(out))
    assert result["success"] is False
    assert not out.exists()


def test_requires_login(db, tmp_path):
    exporter = create_csv_exporter(db, AuthManager(db))
    result = exporter.export_with_summary(TransactionFilter(), str(tmp_path / "x.csv"))
    assert result["success"] is False
    assert "authenticated" in result["message"]


def test_other_users_rows_are_excluded(db, exporter, tmp_path):
    bob = db.create_user(
        username="bob", email="bob@example.com", password_hash="x", salt="y"
    )
    aid = db.get_asset_by_symbol("EXA")["id"]
    db.create_transaction(
        asset_id=aid,
        transaction_type="sell",
        quantity=1.0,
        price=1.0,
        total_amount=1.0,
        transaction_date="2024-06-01",
        user_id=bob,
    )
    result = exporter.export_with_summary(TransactionFilter(), str(tmp_path / "a.csv"))
    assert result["transaction_count"] == 3
