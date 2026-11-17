"""Schema v46: planned_items."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database, DatabaseError


def _make_v45_db(path):
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE planned_items")
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (45)")
    raw.commit()
    raw.close()


def test_version_is_at_least_46():
    assert DATABASE_VERSION >= 46


def test_fresh_db_columns(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(planned_items)")}
    assert {
        "id",
        "date",
        "description",
        "amount",
        "currency",
        "kind",
        "portfolio_id",
        "done",
        "notes",
        "created_at",
        "updated_at",
    } <= cols


def test_migration_adds_the_empty_table(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v45_db(path)
    db = Database(path)
    assert db.list_planned_items(include_done=True) == []
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION


def test_rerunning_the_migration_is_harmless(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.create_planned_item("2027-01-10", "Example Roof", -1200.0, "spending")
    with db.get_connection() as conn:
        db._migrate_to_v46(conn)
    assert len(db.list_planned_items()) == 1


def test_crud_roundtrip(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    item_id = db.create_planned_item("2027-01-10", "Example Roof", -1200.0, "spending")
    item = db.get_planned_item(item_id)
    assert item["currency"] == "EUR" and item["done"] == 0 and item["notes"] is None
    assert db.update_planned_item(item_id, done=1, notes="paid")
    assert db.list_planned_items() == []
    assert [i["id"] for i in db.list_planned_items(include_done=True)] == [item_id]
    assert db.update_planned_item(item_id, notes=None)
    assert db.get_planned_item(item_id)["notes"] is None
    assert db.delete_planned_item(item_id) is True
    assert db.delete_planned_item(item_id) is False


def test_check_constraints_refuse_a_wrong_sign_or_kind(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with pytest.raises(DatabaseError):
        db.create_planned_item("2027-01-10", "Example", 100.0, "spending")
    with pytest.raises(DatabaseError):
        db.create_planned_item("2027-01-10", "Example", -100.0, "income")
    with pytest.raises(DatabaseError):
        db.create_planned_item("2027-01-10", "Example", -100.0, "gift")
