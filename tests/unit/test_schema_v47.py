"""Schema v47: networth_snapshots, recurring_resolutions, month_closes."""

import sqlite3

import pytest

from portf_manager.database import DATABASE_VERSION, Database, DatabaseError

_NEW_TABLES = ("networth_snapshots", "recurring_resolutions", "month_closes")


def _make_v46_db(path):
    Database(path)
    raw = sqlite3.connect(path)
    for table in _NEW_TABLES:
        raw.execute(f"DROP TABLE {table}")
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (46)")
    raw.commit()
    raw.close()


def _snapshot(db, day="2027-01-05", **over):
    args = dict(
        snapshot_date=day,
        net_worth_eur=1000.0,
        investments_eur=800.0,
        cash_eur=300.0,
        property_eur=0.0,
        other_assets_eur=100.0,
        liabilities_eur=200.0,
        detail={"groups": {"cash": 300.0}},
        warnings=["stale line"],
    )
    args.update(over)
    db.upsert_networth_snapshot(**args)


def test_version_is_at_least_47():
    assert DATABASE_VERSION >= 47


def test_fresh_db_has_the_tables(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert set(_NEW_TABLES) <= names


def test_migration_adds_the_empty_tables(tmp_path):
    path = str(tmp_path / "t.db")
    _make_v46_db(path)
    db = Database(path)
    assert db.list_networth_snapshots() == []
    assert db.list_recurring_resolutions() == []
    assert db.list_month_closes() == []
    with db.get_connection() as conn:
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION


def test_rerunning_the_migration_is_harmless(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _snapshot(db)
    with db.get_connection() as conn:
        db._migrate_to_v47(conn)
    assert len(db.list_networth_snapshots()) == 1


def test_networth_snapshot_roundtrip_and_replace(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    _snapshot(db)
    snap = db.get_networth_snapshot("2027-01-05")
    assert snap["net_worth_eur"] == 1000.0
    assert snap["detail"] == {"groups": {"cash": 300.0}}
    assert snap["warnings"] == ["stale line"]
    assert snap["created_at"]
    # the same day replaces the row
    _snapshot(db, net_worth_eur=1100.0, detail=None, warnings=None)
    assert len(db.list_networth_snapshots()) == 1
    snap = db.get_networth_snapshot("2027-01-05")
    assert snap["net_worth_eur"] == 1100.0
    assert snap["detail"] == {} and snap["warnings"] == []
    assert db.get_networth_snapshot("2027-01-06") is None


def test_networth_snapshots_list_oldest_first_within_a_range(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    for day in ("2027-03-01", "2027-01-01", "2027-02-01"):
        _snapshot(db, day=day)
    assert [s["snapshot_date"] for s in db.list_networth_snapshots()] == [
        "2027-01-01",
        "2027-02-01",
        "2027-03-01",
    ]
    ranged = db.list_networth_snapshots(start="2027-02-01", end="2027-02-28")
    assert [s["snapshot_date"] for s in ranged] == ["2027-02-01"]
    assert db.delete_networth_snapshot("2027-02-01") is True
    assert db.delete_networth_snapshot("2027-02-01") is False


def test_recurring_resolution_upserts_on_account_and_merchant(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.set_recurring_resolution(1, "example streaming", "cancelled", "2026-12-01")
    db.set_recurring_resolution(2, "example streaming", "cancelled", "2026-12-01")
    db.set_recurring_resolution(
        1, "example streaming", "paid_elsewhere", "2026-12-15", note="on the card"
    )
    rows = db.list_recurring_resolutions()
    assert len(rows) == 2
    one = db.list_recurring_resolutions(portfolio_id=1)
    assert len(one) == 1
    assert one[0]["status"] == "paid_elsewhere"
    assert one[0]["since"] == "2026-12-15"
    assert one[0]["note"] == "on the card"
    assert db.delete_recurring_resolution(1, "example streaming") is True
    assert db.delete_recurring_resolution(1, "example streaming") is False


def test_recurring_resolution_refuses_an_unknown_status(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with pytest.raises(DatabaseError):
        db.set_recurring_resolution(1, "example", "paused", "2026-12-01")


def test_recurring_resolution_table_has_the_unique_key(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.set_recurring_resolution(1, "example", "cancelled", "2026-12-01")
    with db.get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO recurring_resolutions "
                "(portfolio_id, merchant, status, since) "
                "VALUES (1, 'example', 'cancelled', '2026-12-02')"
            )


def test_month_close_roundtrip_and_replace(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.upsert_month_close(
        "2026-10",
        {"income_eur": 3000.0, "top": ["Groceries"]},
        [{"key": "imports", "status": "ok"}],
        report_id=7,
        notes="done",
    )
    close = db.get_month_close("2026-10")
    assert close["figures"] == {"income_eur": 3000.0, "top": ["Groceries"]}
    assert close["checklist"] == [{"key": "imports", "status": "ok"}]
    assert close["report_id"] == 7 and close["notes"] == "done"
    assert close["closed_at"]
    db.upsert_month_close("2026-10", {"income_eur": 3100.0}, [])
    close = db.get_month_close("2026-10")
    assert close["figures"] == {"income_eur": 3100.0}
    assert close["checklist"] == [] and close["report_id"] is None
    db.upsert_month_close("2026-09", None, None)
    assert [c["month"] for c in db.list_month_closes()] == ["2026-09", "2026-10"]
    assert db.get_month_close("2026-08") is None
    assert db.delete_month_close("2026-10") is True
    assert db.delete_month_close("2026-10") is False
