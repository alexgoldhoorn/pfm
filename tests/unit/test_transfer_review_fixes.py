"""Tests for the final-review fixes of the internal-transfers feature."""

import json

from tests.unit.test_spending_api import HEADERS, _make_client

SAVE = "/api/v1/spending/save"


def _row(category="Groceries", description="MERCADONA", amount=-24.5):
    return {
        "date": "2026-01-05",
        "description": description,
        "amount": amount,
        "currency": "EUR",
        "category": category,
    }


def _save(client, pid, rows, **extra):
    return client.post(
        SAVE, json={"account_portfolio_id": pid, "rows": rows, **extra}, headers=HEADERS
    )


def test_save_transfer_category_is_a_hand_marked_transfer(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    r = _save(client, pid, [_row("Transfer", "Move to Sam Sample", -100.0)])
    assert r.status_code == 200
    assert r.json()["uncategorized"] == 0
    row = db.list_spending_transactions()[0]
    assert row["category"] == "Transfer"
    assert row["is_transfer"] == 1
    assert row["transfer_source"] == "manual"
    assert row["transfer_link_id"] is None


def test_overwrite_keeps_transfer_fields_of_an_existing_transfer(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _save(client, pid, [_row("Transfer", "Move", -100.0)])
    r = _save(
        client,
        pid,
        [_row("Groceries", "Move", -100.0)],
        duplicate_action="overwrite",
    )
    assert r.json()["overwritten"] == 1
    row = db.list_spending_transactions()[0]
    assert row["category"] == "Transfer"
    assert row["is_transfer"] == 1
    assert row["transfer_source"] == "manual"


def test_overwrite_still_changes_category_of_a_normal_row(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _save(client, pid, [_row("Groceries")])
    _save(client, pid, [_row("Dining")], duplicate_action="overwrite")
    assert db.list_spending_transactions()[0]["category"] == "Dining"


def _rename(client, old, new):
    return client.put(
        f"/api/v1/spending/categories/{old}",
        json={"new_name": new},
        headers=HEADERS,
    )


def test_rename_to_transfer_is_refused(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    sid = db.create_spending_transaction(pid, "2026-01-05", "X", -5.0, category="Food")
    r = _rename(client, "Food", "Transfer")
    assert r.status_code == 400
    assert r.json()["detail"] == (
        '"Transfer" is reserved for moves between your own accounts '
        "and can't be renamed or merged into."
    )
    assert db.get_spending_transaction(sid)["category"] == "Food"


def test_rename_from_transfer_is_refused(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    sid = db.create_spending_transaction(pid, "2026-01-05", "X", -5.0)
    db.update_spending_transaction(
        sid, category="Transfer", is_transfer=True, transfer_source="manual"
    )
    r = _rename(client, "Transfer", "Moves")
    assert r.status_code == 400
    assert db.get_spending_transaction(sid)["category"] == "Transfer"


def test_parse_suggestions_maps_transfer_to_uncategorized():
    from portf_server.routers.spending import _parse_suggestions

    out = _parse_suggestions(
        [
            {"description": "A", "category": " transfer ", "suggested_pattern": "A"},
            {"description": "B", "category": "Groceries", "suggested_pattern": "B"},
        ]
    )
    assert [s.category for s in out] == ["uncategorized", "Groceries"]


def test_load_patterns_drops_blank_and_short_entries(tmp_path):
    from portf_manager.services.own_accounts import SETTING_KEY, load_patterns

    _, db = _make_client(tmp_path)
    db.set_setting(SETTING_KEY, json.dumps(["ab", "  Sam Sample  ", ""]))
    assert load_patterns(db) == ["Sam Sample"]


def _put(client, sid, **body):
    return client.put(f"/api/v1/spending/{sid}", json=body, headers=HEADERS)


def test_marking_an_existing_transfer_keeps_source_and_link(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    other = db.create_portfolio("Example Bank 2", account_type="bank")
    a = db.create_spending_transaction(pid, "2026-07-01", "Out", -100.0)
    b = db.create_spending_transaction(other, "2026-07-01", "In", 100.0)
    for x, y in ((a, b), (b, a)):
        db.update_spending_transaction(
            x,
            category="Transfer",
            is_transfer=True,
            transfer_link_type="spending",
            transfer_link_id=y,
            transfer_source="match",
        )
    assert _put(client, a, is_transfer=True).status_code == 200
    row = db.get_spending_transaction(a)
    assert row["transfer_source"] == "match"
    assert row["transfer_link_id"] == b


def test_marking_a_pattern_transfer_keeps_pattern_source(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    sid = db.create_spending_transaction(pid, "2026-07-01", "Out", -100.0)
    db.update_spending_transaction(
        sid, category="Transfer", is_transfer=True, transfer_source="pattern"
    )
    _put(client, sid, category="Transfer")
    assert db.get_spending_transaction(sid)["transfer_source"] == "pattern"


def test_marking_a_plain_row_sets_manual(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    sid = db.create_spending_transaction(pid, "2026-07-01", "Out", -100.0)
    _put(client, sid, is_transfer=True)
    assert db.get_spending_transaction(sid)["transfer_source"] == "manual"
