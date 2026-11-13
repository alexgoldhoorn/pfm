"""Schema v42: spending_categories.essentiality and the reports table."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database


def _columns(db, table):
    with db.get_connection() as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _make_v41_db(path):
    """A database shaped like v41: no essentiality column, no reports table."""
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute("DROP TABLE reports")
    raw.execute("ALTER TABLE spending_categories DROP COLUMN essentiality")
    raw.execute(
        "INSERT INTO spending_categories (name, parent_id) "
        "SELECT 'Groceries', id FROM spending_categories WHERE name = 'Spend'"
    )
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (41)")
    raw.commit()
    raw.close()


def test_fresh_db_has_both(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    assert DATABASE_VERSION >= 42
    assert "essentiality" in _columns(db, "spending_categories")
    assert _columns(db, "reports") == {
        "id",
        "title",
        "content_md",
        "source_session_id",
        "created_at",
        "updated_at",
    }


def test_migration_adds_both_and_guesses_nothing(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v41_db(path)
    db = Database(path)
    assert "essentiality" in _columns(db, "spending_categories")
    assert db.find_spending_category_by_name("Groceries")["essentiality"] is None
    assert db.list_reports() == []
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION


def test_migration_is_rerunnable(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v41_db(path)
    db = Database(path)
    db.set_spending_category_essentiality("Groceries", "flexible")
    rid = db.create_report("Title", "Body")
    with db.get_connection() as conn:
        db._migrate_to_v42(conn)
    assert db.find_spending_category_by_name("Groceries")["essentiality"] == "flexible"
    assert db.get_report(rid)["title"] == "Title"


def test_essentiality_values(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Groceries", spend)
    assert db.set_spending_category_essentiality("Groceries", "flexible") is True
    assert db.set_spending_category_essentiality("Missing", "fixed") is False
    with pytest.raises(ValueError):
        db.set_spending_category_essentiality("Groceries", "vital")
    assert db.set_spending_category_essentiality("Groceries", None) is True
    assert db.find_spending_category_by_name("Groceries")["essentiality"] is None


def test_report_crud(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    first = db.create_report("First", "Body one", "s1")
    second = db.create_report("Second", "Body two")
    assert [r["id"] for r in db.list_reports()] == [second, first]
    assert "content_md" not in db.list_reports()[0]
    assert db.get_report(first)["source_session_id"] == "s1"
    assert db.update_report(first, title="Renamed") is True
    assert db.get_report(first)["content_md"] == "Body one"
    assert db.update_report(first) is True
    assert db.update_report(999, title="x") is False
    assert db.delete_report(first) is True
    assert db.delete_report(first) is False
    assert db.get_report(first) is None
