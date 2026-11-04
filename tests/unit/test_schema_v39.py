"""Schema v39: append-only research history."""

import json
import sqlite3

from portf_manager.database import DATABASE_VERSION, Database


def test_fresh_db_has_research_history(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    with db.get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        idx = {r[1] for r in conn.execute("PRAGMA index_list(research_history)")}
        version = conn.execute("SELECT MAX(version) FROM database_version").fetchone()[
            0
        ]
    assert version == DATABASE_VERSION
    assert "research_history" in names
    assert "idx_research_history_symbol" in idx


def test_migration_from_v38_backfills_once(tmp_path):
    path = str(tmp_path / "t.db")
    db = Database(path)
    aid = db.create_asset("EXM", "Example Corp", "stock", currency="EUR")
    db.create_price(aid, 100.0, "2026-01-01")
    db.create_price(aid, 150.0, "2026-09-01")
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE research_history")
    raw.execute(
        "INSERT INTO research_reports (asset_id, symbol, fair_value, "
        "recommendation, confidence, summary, report_json, generated_at) "
        "VALUES (?, 'EXM', 130, 'BUY', 'high', 's', ?, '2026-03-05 10:00:00')",
        (aid, json.dumps({"buy_below": 95.0, "sell_above": 140.0})),
    )
    raw.execute("DELETE FROM database_version")
    raw.execute("INSERT INTO database_version (version) VALUES (38)")
    raw.commit()
    raw.close()

    db = Database(path)
    rows = db.get_research_history()
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "backfill" and r["symbol"] == "EXM"
    assert r["generated_at"] == "2026-03-05 10:00:00"
    # the last close on or before the report date
    assert r["price_at_report"] == 100.0
    assert r["buy_below"] == 95.0 and r["sell_above"] == 140.0
    assert r["currency"] == "EUR" and r["horizon_months"] == 12

    # a rerun does not duplicate
    with db.get_connection() as conn:
        db._migrate_to_v39(conn)
    assert len(db.get_research_history()) == 1
