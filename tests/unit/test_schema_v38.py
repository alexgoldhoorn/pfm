"""Schema v38: portfolios.is_managed and the decision journal."""

import sqlite3

from portf_manager.database import DATABASE_VERSION, Database


def test_fresh_db_is_current_and_has_v38_objects(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolios)")}
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION == 39
    assert "decisions" in names
    assert "is_managed" in cols


def test_migration_from_v37_adds_column_and_table(tmp_path):
    path = str(tmp_path / "t.db")
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE decisions")
    # SQLite can drop a column directly since 3.35
    raw.execute("ALTER TABLE portfolios DROP COLUMN is_managed")
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (37)")
    raw.commit()
    raw.close()

    db = Database(path)
    pid = db.create_portfolio("Robo", "EUR", is_managed=True)
    did = db.create_decision(decided_on="2026-11-01", action="hold", title="Hold")
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION
    assert db.get_portfolio(pid)["is_managed"] == 1
    assert db.get_decision(did)["title"] == "Hold"

    # a rerun of the guarded migration is harmless
    with db.get_connection() as conn:
        db._migrate_to_v38(conn)


def test_decision_crud_and_validation(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    try:
        db.create_decision(decided_on="2026-11-01", action="panic", title="x")
        raise AssertionError("unknown action must fail")
    except ValueError:
        pass
    try:
        db.create_decision(action="hold", title="x")
        raise AssertionError("decided_on is required")
    except ValueError:
        pass
    a = db.create_decision(
        decided_on="2026-10-01", action="buy", title="A", symbol="aaa"
    )
    b = db.create_decision(decided_on="2026-11-01", action="sell", title="B")
    assert [d["id"] for d in db.get_decisions()] == [b, a]
    assert [d["id"] for d in db.get_decisions(symbol="AAA")] == [a]
    assert (
        db.update_decision(b, outcome_rating="good", reviewed_at="2026-11-02") is True
    )
    assert [d["id"] for d in db.get_decisions("open")] == [a]
    assert [d["id"] for d in db.get_decisions("reviewed")] == [b]
    try:
        db.update_decision(a, outcome_rating="meh")
        raise AssertionError("unknown rating must fail")
    except ValueError:
        pass
    assert db.update_decision(a, nonsense=1) is False
    assert db.delete_decision(a) is True
    assert db.delete_decision(a) is False
