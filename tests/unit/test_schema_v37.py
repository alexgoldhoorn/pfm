"""Schema v37: wealth_scenarios."""

import sqlite3

from portf_manager.database import DATABASE_VERSION, Database


def test_fresh_db_is_current_and_has_wealth_scenarios(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION == 37
    assert "wealth_scenarios" in names


def test_migration_from_v36_creates_the_table(tmp_path):
    path = str(tmp_path / "t.db")
    Database(path)
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE wealth_scenarios")
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (36)")
    raw.commit()
    raw.close()

    db = Database(path)
    sid = db.create_wealth_scenario("Base", "{}", "2026-01-01", 100.0, is_primary=True)
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == 37
    assert db.get_wealth_scenario(sid)["is_primary"] == 1


def test_crud_and_primary_exclusivity(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    a = db.create_wealth_scenario("A", "{}", "2026-01-01", None, is_primary=True)
    b = db.create_wealth_scenario("B", "{}", "2026-01-01", 5.0, is_primary=True)
    rows = {r["name"]: r for r in db.get_wealth_scenarios()}
    assert rows["A"]["is_primary"] == 0 and rows["B"]["is_primary"] == 1
    assert [r["name"] for r in db.get_wealth_scenarios()] == ["B", "A"]

    assert db.set_primary_wealth_scenario(a) is True
    assert db.get_wealth_scenario(a)["is_primary"] == 1
    assert db.get_wealth_scenario(b)["is_primary"] == 0
    assert db.set_primary_wealth_scenario(999) is False
    assert db.get_wealth_scenario(a)["is_primary"] == 1
    assert db.set_primary_wealth_scenario(None) is True
    assert not any(r["is_primary"] for r in db.get_wealth_scenarios())

    assert db.update_wealth_scenario(a, description="x", baseline_net_worth_eur=None)
    assert db.get_wealth_scenario(a)["description"] == "x"
    try:
        db.update_wealth_scenario(a, name="B")
        raise AssertionError("rename onto an existing name must fail")
    except ValueError:
        pass
    try:
        db.create_wealth_scenario("A", "{}", "2026-01-01")
        raise AssertionError("duplicate name must fail")
    except ValueError:
        pass
    assert db.delete_wealth_scenario(a) is True
    assert db.delete_wealth_scenario(a) is False
    assert db.get_wealth_scenario(a) is None
