"""Schema v36: goals.start_networth_eur and goals.notes."""

import sqlite3

from portf_manager.database import DATABASE_VERSION, Database


def test_fresh_db_has_goal_baseline_and_notes(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION == 36
    assert {"start_networth_eur", "notes"} <= cols


def test_migration_adds_columns_and_keeps_old_goals(tmp_path):
    path = str(tmp_path / "t.db")
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE goals DROP COLUMN start_networth_eur")
    raw.execute("ALTER TABLE goals DROP COLUMN notes")
    raw.execute(
        "INSERT INTO goals (name, target_amount_eur, target_date, "
        "monthly_contribution_eur, expected_return_pct) "
        "VALUES ('Old Goal', 1000, '2030-01-01', 10, 5)"
    )
    raw.execute("INSERT INTO database_version (version) VALUES (35)")
    raw.execute("DELETE FROM database_version WHERE version > 35")
    raw.commit()
    raw.close()

    db = Database(path)
    (goal,) = db.get_goals()
    # nothing is backfilled: an old goal has no baseline and no notes
    assert goal["name"] == "Old Goal"
    assert goal["start_networth_eur"] is None
    assert goal["notes"] is None
    # and the migration is a no-op on a second run
    with db.get_connection() as conn:
        db._migrate_to_v36(conn)


def test_update_goal_keeps_baseline_and_notes_when_none(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    gid = db.create_goal(
        "Example", 1000, "2030-01-01", 10, 5, start_networth_eur=250, notes="why"
    )
    assert db.update_goal(gid, "Example", 2000, "2031-01-01", 20, 5)
    goal = db.get_goal(gid)
    assert goal["target_amount_eur"] == 2000
    assert goal["start_networth_eur"] == 250
    assert goal["notes"] == "why"
    assert db.update_goal(gid, "Example", 2000, "2031-01-01", 20, 5, 0, "")
    goal = db.get_goal(gid)
    assert goal["start_networth_eur"] == 0
    assert goal["notes"] == ""
    assert db.update_goal(999, "x", 1, "2030-01-01", 0, 0) is False
