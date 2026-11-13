"""Schema v41: spending_transactions.transfer_source and the Transfer repair."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database, DatabaseError

# spending_transactions as it was at v40 (no transfer_source column)
V40_DDL = """
    CREATE TABLE spending_transactions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id       INTEGER NOT NULL,
        date               DATE NOT NULL,
        description        TEXT NOT NULL,
        amount             REAL NOT NULL,
        currency           TEXT NOT NULL DEFAULT 'EUR',
        category           TEXT NOT NULL DEFAULT 'uncategorized',
        is_transfer        INTEGER NOT NULL DEFAULT 0,
        transfer_link_type TEXT CHECK (transfer_link_type IN ('spending', 'booking')),
        transfer_link_id   INTEGER,
        source             TEXT,
        balance            REAL,
        merchant           TEXT,
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (portfolio_id) REFERENCES portfolios (id) ON DELETE CASCADE
    )
"""


def _make_v40_db(path):
    """Create a DB shaped like v40 with rows A-D and return their ids."""
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DROP TABLE spending_transactions")
    raw.execute(V40_DDL)
    raw.execute("INSERT INTO portfolios (name) VALUES ('Example Bank')")
    rows = [
        ("A", 1, "booking", 7, "Transfer"),
        ("B", 1, None, None, "Transfer"),
        ("C", 0, None, None, "Transfer"),
        ("D", 0, None, None, "Groceries"),
    ]
    for desc, flag, ltype, lid, cat in rows:
        raw.execute(
            "INSERT INTO spending_transactions (portfolio_id, date, description, "
            "amount, category, is_transfer, transfer_link_type, transfer_link_id) "
            "VALUES (1, '2026-01-05', ?, -10, ?, ?, ?, ?)",
            (desc, cat, flag, ltype, lid),
        )
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (40)")
    raw.commit()
    raw.close()


def _by_desc(db):
    return {r["description"]: r for r in db.list_spending_transactions()}


def test_fresh_db_has_transfer_source(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(spending_transactions)")}
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION >= 41
    assert "transfer_source" in cols


def test_migration_backfills_and_repairs(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v40_db(path)
    rows = _by_desc(Database(path))
    assert rows["A"]["transfer_source"] == "match"
    assert rows["A"]["category"] == "Transfer"
    assert rows["B"]["transfer_source"] == "manual"
    assert rows["C"]["category"] == "uncategorized"
    assert rows["C"]["transfer_source"] is None
    assert rows["D"]["category"] == "Groceries"
    assert rows["D"]["transfer_source"] is None


def test_migration_is_rerunnable(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v40_db(path)
    db = Database(path)
    before = _by_desc(db)
    with db.get_connection() as conn:
        db._migrate_to_v41(conn)
    assert _by_desc(db) == before


def test_bad_source_value_rejected(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    with pytest.raises(DatabaseError):
        db.update_spending_transaction(tx_id, transfer_source="guess")
    assert db.update_spending_transaction(tx_id, transfer_source="rejected")
    assert db.get_spending_transaction(tx_id)["transfer_source"] == "rejected"
