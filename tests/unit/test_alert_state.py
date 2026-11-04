"""Price-target alerts fire once per crossing: state in app_settings."""

import json
from datetime import date, timedelta

import pytest

from portf_manager.services.action_items import check_price_alerts
from portf_server.routers.research import check_alerts, compute_price_target_alerts

D1 = date(2026, 11, 3)
D2 = D1 + timedelta(days=1)
D3 = D1 + timedelta(days=2)


def _setup(db, price=50.0):
    aid = db.create_asset("WISH", "Wish Corp", "stock", currency="EUR")
    db.create_price(aid, price, D1.isoformat())
    db.upsert_price_target(aid, buy_below=80.0)
    return aid


def _set_price(db, aid, price, day):
    db.create_price(aid, price, day.isoformat())


def _triggers(db, today, record=False):
    alerts = compute_price_target_alerts(db, today=today, record=record)
    return [t for a in alerts for t in a["triggers"]]


def _state(db):
    return json.loads(db.get_setting("price_alert_state") or "{}")


def test_first_run_is_new_and_recorded(test_database):
    aid = _setup(test_database)
    (t,) = _triggers(test_database, D1, record=True)
    assert t["is_new"] is True and t["first_seen"] == D1.isoformat()
    assert _state(test_database)[f"{aid}:BUY"] == {
        "first_seen": D1.isoformat(),
        "last_seen": D1.isoformat(),
        "notified": None,
    }


def test_second_run_same_day_is_still_new(test_database):
    _setup(test_database)
    _triggers(test_database, D1, record=True)
    (t,) = _triggers(test_database, D1, record=True)
    assert t["is_new"] is True


def test_next_day_is_standing_and_state_kept(test_database):
    aid = _setup(test_database)
    _triggers(test_database, D1, record=True)
    (t,) = _triggers(test_database, D2, record=True)
    assert t["is_new"] is False and t["first_seen"] == D1.isoformat()
    entry = _state(test_database)[f"{aid}:BUY"]
    assert entry["first_seen"] == D1.isoformat()
    assert entry["last_seen"] == D2.isoformat()


def test_cleared_trigger_is_removed_and_recrossing_is_new(test_database):
    aid = _setup(test_database)
    _triggers(test_database, D1, record=True)
    _set_price(test_database, aid, 90.0, D2)
    assert _triggers(test_database, D2, record=True) == []
    assert _state(test_database) == {}
    _set_price(test_database, aid, 50.0, D3)
    (t,) = _triggers(test_database, D3, record=True)
    assert t["is_new"] is True and t["first_seen"] == D3.isoformat()


def test_record_false_writes_nothing(test_database):
    _setup(test_database)
    (t,) = _triggers(test_database, D1, record=False)
    assert t["is_new"] is True
    assert test_database.get_setting("price_alert_state") is None
    _triggers(test_database, D1, record=True)
    before = test_database.get_setting("price_alert_state")
    _triggers(test_database, D2, record=False)
    assert test_database.get_setting("price_alert_state") == before


def test_corrupt_state_is_treated_as_empty(test_database):
    _setup(test_database)
    test_database.set_setting("price_alert_state", "not json")
    (t,) = _triggers(test_database, D1)
    assert t["is_new"] is True


@pytest.mark.asyncio
async def test_endpoint_record_flag_and_counts(test_database):
    aid = _setup(test_database)
    today = date.today()
    _set_price(test_database, aid, 50.0, today)
    # a standing crossing from yesterday
    yesterday = (today - timedelta(days=1)).isoformat()
    test_database.set_setting(
        "price_alert_state",
        json.dumps(
            {
                f"{aid}:BUY": {
                    "first_seen": yesterday,
                    "last_seen": yesterday,
                    "notified": None,
                }
            }
        ),
    )
    other = test_database.create_asset("NEWB", "Newb Corp", "stock", currency="EUR")
    test_database.create_price(other, 10.0, today.isoformat())
    test_database.upsert_price_target(other, buy_below=20.0)

    res = await check_alerts(record=False, db=test_database, api_key_info={})
    assert res["new"] == 1 and res["standing"] == 1 and res["total"] == 2
    assert f"{other}:BUY" not in _state(test_database)

    await check_alerts(record=True, db=test_database, api_key_info={})
    assert f"{other}:BUY" in _state(test_database)


def test_action_item_mentions_since_for_old_crossing(test_database):
    aid = _setup(test_database)
    today = date.today()
    _set_price(test_database, aid, 50.0, today)
    old = (today - timedelta(days=5)).isoformat()
    test_database.set_setting(
        "price_alert_state",
        json.dumps({f"{aid}:BUY": {"first_seen": old, "last_seen": old}}),
    )
    (item,) = [
        i for i in check_price_alerts(test_database) if i["id"] == "research:WISH"
    ]
    assert f"Since {old}." in item["detail"]
