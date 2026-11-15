"""Schema v44: budget_lines.link_type."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database


def _columns(db, table):
    with db.get_connection() as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _make_v43_db(path):
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("ALTER TABLE budget_lines DROP COLUMN link_type")
    raw.execute("INSERT INTO budgets (name) VALUES ('Example Budget')")
    raw.execute(
        "INSERT INTO budget_lines "
        "(budget_id, line_type, ref_key, monthly_amount, link_id) "
        "VALUES (1, 'debt', 'Housing', 900, 7)"
    )
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (43)")
    raw.commit()
    raw.close()


def test_fresh_db_has_link_type(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert DATABASE_VERSION >= 44
    assert "link_type" in _columns(db, "budget_lines")


def test_migration_defaults_existing_lines_to_manual(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v43_db(path)
    db = Database(path)
    line = db.get_budget_line(1)
    assert line["link_type"] == "manual"
    assert line["link_id"] == 7
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION


def test_migration_is_rerunnable(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v43_db(path)
    db = Database(path)
    db.update_budget_line(1, link_type="loan")
    with db.get_connection() as conn:
        db._migrate_to_v44(conn)
    assert db.get_budget_line(1)["link_type"] == "loan"


def test_link_type_is_checked(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    budget = db.create_budget("Example Budget")
    with pytest.raises(Exception):
        db.create_budget_line(budget, "debt", "Housing", 10, link_type="other")
