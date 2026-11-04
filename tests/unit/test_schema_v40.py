"""Schema v40: position_caps."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database, DatabaseError


def test_fresh_db_has_position_caps(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(position_caps)")}
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION >= 40
    assert {"scope", "key", "max_pct", "note", "created_at", "updated_at"} <= cols


def test_migration_from_v39_creates_table_and_is_rerunnable(tmp_path):
    path = str(tmp_path / "t.db")
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE position_caps")
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (39)")
    raw.commit()
    raw.close()
    db = Database(path)
    cap_id = db.create_position_cap("symbol", "exm", 4.0)
    assert db.get_position_cap(cap_id)["key"] == "EXM"
    # running the migration again changes nothing
    with db.get_connection() as conn:
        db._migrate_to_v40(conn)
    assert len(db.get_position_caps()) == 1


def test_crud_validation_and_uniqueness(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    cap_id = db.create_position_cap("sector", "Technology", 25, note="limit")
    with pytest.raises(ValueError):
        db.create_position_cap("sector", "Technology", 30)
    with pytest.raises(ValueError):
        db.create_position_cap("bogus", "X", 10)
    with pytest.raises(ValueError):
        db.create_position_cap("symbol", "EXM", 0)
    with pytest.raises(ValueError):
        db.create_position_cap("symbol", "EXM", 101)
    assert db.update_position_cap(cap_id, max_pct=20.0, note="tighter")
    row = db.get_position_cap(cap_id)
    assert row["max_pct"] == 20.0 and row["note"] == "tighter"
    with pytest.raises(ValueError):
        db.update_position_cap(cap_id, max_pct=500)
    other = db.create_position_cap("sector", "Energy", 10)
    with pytest.raises(ValueError):
        db.update_position_cap(other, key="Technology")
    assert [c["key"] for c in db.get_position_caps("sector")] == [
        "Energy",
        "Technology",
    ]
    assert db.delete_position_cap(cap_id) and not db.delete_position_cap(cap_id)


def test_check_constraint_rejects_bad_scope(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with pytest.raises(DatabaseError):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO position_caps (scope, key, max_pct) "
                "VALUES ('bogus', 'X', 5)"
            )
