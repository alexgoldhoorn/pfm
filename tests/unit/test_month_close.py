"""Month close: checklist, figures, stored close, report and Action Item."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import month_close as mc
from portf_manager.services.action_item_acks import set_ack
from portf_manager.services.action_items import checks_for_tests

TODAY = date(2026, 7, 15)
MONTH = "2026-06"
_KEY = "test-key-month-close-abc"
HEADERS = {"X-API-Key": _KEY}


def _fx(currency):
    return 1.0


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "close.db"))
    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    for name in ("Groceries", "Rent", "Hobby"):
        db.create_spending_category(name, parent_id=spend)
    db.create_spending_category("Salary", parent_id=income)
    return db


@pytest.fixture
def bank(db):
    return db.get_or_create_portfolio("Example Bank", account_type="bank")


def _tx(db, bank, day, description, amount, category, imported=None, **kw):
    """Add a row; by default it was imported on its own day."""
    new_id = db.create_spending_transaction(
        bank, day, description, amount, category=category, **kw
    )
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE spending_transactions SET created_at = ? WHERE id = ?",
            (f"{imported or day} 12:00:00", new_id),
        )
        conn.commit()
    return new_id


def _clean(db, bank):
    """Rent Dec-Jul, salary Jan-Jun: nothing to look at in June."""
    _tx(db, bank, "2025-12-01", "Example Landlord", -1000.0, "Rent")
    for m in range(1, 8):
        _tx(db, bank, f"2026-{m:02d}-01", "Example Landlord", -1000.0, "Rent")
    for m in range(1, 7):
        _tx(db, bank, f"2026-{m:02d}-25", "Example Employer", 3000.0, "Salary")


def _state(db, month=MONTH, today=TODAY):
    return mc.build_month_close(db, _fx, today, month)


def _step(state, key):
    return next(s for s in state["checklist"] if s["key"] == key)


def test_clean_month_is_ready_with_every_step_ok(db, bank):
    _clean(db, bank)
    state = _state(db)
    assert state["month"] == MONTH
    assert state["closed"] is None
    assert state["ready"] is True
    assert [s["key"] for s in state["checklist"]] == [
        "imports",
        "uncategorized",
        "transfers",
        "anomalies",
        "recurring",
        "one_offs",
    ]
    assert {s["status"] for s in state["checklist"]} == {"ok"}
    assert state["warnings"] == []
    figures = state["figures"]
    assert figures["income_eur"] == 3000.0
    assert figures["spending_eur"] == 1000.0
    assert figures["net_eur"] == 2000.0
    assert figures["savings_rate_pct"] == 66.7
    assert figures["savings_eur"] == 0.0
    assert figures["budget"] is None
    assert figures["top_categories"] == [
        {"name": "Rent", "actual_eur": 1000.0, "baseline_eur": 1000.0, "delta_eur": 0.0}
    ]
    assert figures["versus_normal"]["status"] == "complete"
    assert figures["versus_normal"]["baseline_months"] == 6
    assert figures["versus_normal"]["delta_eur"] == 0.0


def test_default_month_is_the_last_complete_one(db, bank):
    _clean(db, bank)
    assert mc.build_month_close(db, _fx, TODAY)["month"] == MONTH


def test_lagging_account_blocks_ready(db, bank):
    _clean(db, bank)
    other = db.get_or_create_portfolio("Example Savings", account_type="bank")
    _tx(db, other, "2026-06-20", "Example Interest", 5.0, "Salary")
    state = _state(db)
    step = _step(state, "imports")
    assert step["status"] == "todo"
    assert step["count"] == 1
    assert "Example Savings (through 2026-06-20)" in step["detail"]
    assert state["ready"] is False


def test_closed_account_does_not_block_and_is_named(db, bank):
    _clean(db, bank)
    old = db.get_or_create_portfolio("Example Closed", account_type="bank")
    _tx(db, old, "2025-09-10", "Example Old", -5.0, "Groceries")
    state = _state(db)
    assert state["ready"] is True
    assert (
        "Accounts not checked (no recent activity): Example Closed"
        in _step(state, "imports")["detail"]
    )


def test_quiet_account_fetched_after_month_end_is_fine(db, bank):
    _clean(db, bank)
    quiet = db.get_or_create_portfolio("Example Quiet", account_type="bank")
    # last row on the 28th, but the statement was imported in July
    _tx(
        db, quiet, "2026-06-28", "Example Fee", -2.0, "Groceries", imported="2026-07-03"
    )
    assert _state(db)["ready"] is True


def test_active_account_with_nothing_after_the_20th_blocks(db, bank):
    _clean(db, bank)
    active = db.get_or_create_portfolio("Example Active", account_type="bank")
    # imported on the 21st: the statement cannot cover the rest of the month
    _tx(db, active, "2026-06-20", "Example Fee", -2.0, "Groceries")
    state = _state(db)
    step = _step(state, "imports")
    assert step["status"] == "todo"
    assert "Example Active (through 2026-06-20)" in step["detail"]
    assert state["ready"] is False


def test_month_with_no_rows_is_todo_and_figures_are_null(db, bank):
    _clean(db, bank)
    state = _state(db, "2026-04", date(2026, 5, 10))
    # the account is imported through July, but April's own rows exist here
    assert state["ready"] is True
    empty = mc.build_month_close(db, _fx, date(2027, 1, 20), "2026-12")
    assert empty["ready"] is False
    assert _step(empty, "imports")["detail"] == "Nothing is imported for 2026-12."
    assert _step(empty, "uncategorized")["status"] == "unknown"
    assert empty["figures"]["income_eur"] is None
    assert empty["figures"]["net_eur"] is None


def test_uncategorized_step(db, bank):
    _clean(db, bank)
    _tx(db, bank, "2026-06-12", "Example Kiosk", -50.0, "uncategorized")
    step = _step(_state(db), "uncategorized")
    assert step["status"] == "warn"
    assert step["count"] == 1
    assert "50 EUR" in step["detail"]


def test_transfers_step(db, bank):
    _clean(db, bank)
    tid = _tx(db, bank, "2026-06-12", "Example Own Account", -100.0, "Transfer")
    db.update_spending_transaction(tid, is_transfer=1, transfer_source="pattern")
    step = _step(_state(db), "transfers")
    assert step["status"] == "warn"
    assert step["count"] == 1
    assert "1 flagged by a pattern" in step["detail"]


def _spike(db, bank):
    for m in range(1, 6):
        _tx(db, bank, f"2026-{m:02d}-10", "Example Market", -200.0, "Groceries")
    _tx(db, bank, "2026-06-10", "Example Market", -700.0, "Groceries")


def test_anomalies_step_counts_unchecked_and_honours_acks(db, bank):
    _clean(db, bank)
    _spike(db, bank)
    first = _step(_state(db), "anomalies")
    assert first["status"] == "warn"
    assert first["count"] == 2
    # a checked item is no longer open; a snoozed one still hides it too
    for item_id in ("anomaly:category:Groceries:2026-06",):
        set_ack(db, item_id, "checked", today=TODAY)
    after = _step(_state(db), "anomalies")
    assert after["count"] == 1
    large = next(
        i
        for i in mc.spending_insights.find_anomalies(
            *mc.load_rows_and_tree(db, _fx, MONTH), TODAY, 60
        )["items"]
        if i["kind"] == "large_charge"
    )
    set_ack(db, large["id"], "checked", today=TODAY)
    assert _step(_state(db), "anomalies")["status"] == "ok"


def test_recurring_step_and_resolution(db, bank):
    _clean(db, bank)
    for m in range(1, 6):
        _tx(db, bank, f"2026-{m:02d}-10", "Example Streaming", -15.0, "Hobby")
    step = _step(_state(db), "recurring")
    assert step["status"] == "warn"
    assert step["count"] == 1
    assert "Example Streaming" in step["detail"]
    db.set_recurring_resolution(bank, "Example Streaming", "cancelled", "2026-05-10")
    assert _step(_state(db), "recurring")["status"] == "ok"


def test_one_offs_step_is_a_reminder_for_new_merchants_only(db, bank):
    _clean(db, bank)
    big = _tx(db, bank, "2026-06-15", "Example Boatyard", -900.0, "Hobby")
    step = _step(_state(db), "one_offs")
    # the rent is large but a usual payment; the boatyard is new
    assert step["status"] == "warn"
    assert step["count"] == 1
    db.update_spending_transaction(big, one_off=1)
    assert _step(_state(db), "one_offs")["status"] == "ok"
    # a reminder never blocks
    assert _state(db)["ready"] is True


def test_failing_source_is_unknown_with_a_warning(db, bank, monkeypatch):
    _clean(db, bank)

    def boom(*a, **k):
        raise RuntimeError("series broke")

    monkeypatch.setattr(mc, "load_recurring", boom)
    state = _state(db)
    step = _step(state, "recurring")
    assert step["status"] == "unknown"
    assert step["count"] is None
    assert _step(state, "imports")["status"] == "ok"
    assert state["ready"] is True
    assert any("series broke" in w for w in state["warnings"])


def test_unreadable_rows_make_everything_unknown_and_not_ready(db, bank, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no rows")

    monkeypatch.setattr(mc.spending_insights, "load_rows", boom)
    state = _state(db)
    assert {s["status"] for s in state["checklist"]} == {"unknown"}
    assert state["ready"] is False
    assert state["figures"]["income_eur"] is None
    assert any("no rows" in w for w in state["warnings"])


def test_budget_figures_come_from_the_active_budget(db, bank):
    _clean(db, bank)
    budget = db.create_budget("Example Plan", is_active=True)
    db.create_budget_line(budget, "spending", "Rent", 900.0)
    figures = _state(db)["figures"]
    assert figures["budget"]["name"] == "Example Plan"
    spending = next(s for s in figures["budget"]["sections"] if s["key"] == "spending")
    assert spending["planned_total"] == 900.0
    assert spending["actual_total"] == 1000.0


def test_future_and_in_progress_months(db, bank):
    _clean(db, bank)
    with pytest.raises(mc.MonthCloseError) as future:
        mc.build_month_close(db, _fx, TODAY, "2026-08")
    assert future.value.status == 400
    # reading the month in progress is fine, closing it is not
    assert mc.build_month_close(db, _fx, TODAY, "2026-07")["ready"] is False
    with pytest.raises(mc.MonthCloseError) as current:
        mc.close_month(db, _fx, TODAY, "2026-07")
    assert current.value.status == 400


def test_close_stores_figures_checklist_and_report(db, bank):
    _clean(db, bank)
    state = mc.close_month(db, _fx, TODAY, MONTH, notes="all fine")
    stored = db.get_month_close(MONTH)
    assert stored["notes"] == "all fine"
    assert stored["figures"]["income_eur"] == 3000.0
    assert [s["key"] for s in stored["checklist"]][0] == "imports"
    report = db.get_report(stored["report_id"])
    assert report["title"] == "Month close 2026-06"
    assert report["content_md"] == mc.render_month_close_report(
        MONTH, stored["figures"], stored["checklist"]
    )
    assert state["closed"]["report_id"] == stored["report_id"]
    assert state["closed"]["stale"] is False
    assert _state(db)["closed"]["stale"] is False


def test_closing_again_updates_the_same_report(db, bank):
    _clean(db, bank)
    first = mc.close_month(db, _fx, TODAY, MONTH, notes="first")
    _tx(db, bank, "2026-06-20", "Example Shop", -250.0, "Groceries")
    second = mc.close_month(db, _fx, TODAY, MONTH)
    assert second["closed"]["report_id"] == first["closed"]["report_id"]
    assert len(db.list_reports()) == 1
    assert "1,250 EUR" in db.get_report(first["closed"]["report_id"])["content_md"]
    # the earlier note is kept when a re-close sends none
    assert db.get_month_close(MONTH)["notes"] == "first"
    assert len(db.list_month_closes()) == 1


def test_not_ready_needs_force(db, bank):
    _clean(db, bank)
    other = db.get_or_create_portfolio("Example Savings", account_type="bank")
    _tx(db, other, "2026-06-20", "Example Interest", 5.0, "Salary")
    with pytest.raises(mc.MonthCloseError) as err:
        mc.close_month(db, _fx, TODAY, MONTH)
    assert err.value.status == 409
    assert db.get_month_close(MONTH) is None
    mc.close_month(db, _fx, TODAY, MONTH, force=True)
    assert db.get_month_close(MONTH) is not None


def test_reopen_keeps_the_report(db, bank):
    _clean(db, bank)
    closed = mc.close_month(db, _fx, TODAY, MONTH)
    out = mc.reopen_month(db, MONTH)
    assert out == {
        "month": MONTH,
        "reopened": True,
        "report_id": closed["closed"]["report_id"],
    }
    assert db.get_month_close(MONTH) is None
    assert db.get_report(out["report_id"]) is not None
    with pytest.raises(mc.MonthCloseError) as err:
        mc.reopen_month(db, MONTH)
    assert err.value.status == 404


def test_close_goes_stale_when_rows_change_after_it(db, bank):
    _clean(db, bank)
    mc.close_month(db, _fx, TODAY, MONTH)
    _tx(db, bank, "2026-06-28", "Example Late Row", -0.5, "Groceries")
    assert _state(db)["closed"]["stale"] is False
    _tx(db, bank, "2026-06-29", "Example Late Row", -40.0, "Groceries")
    assert _state(db)["closed"]["stale"] is True


def test_is_stale_rules():
    stored = {"income_eur": 100.0, "spending_eur": 50.0, "net_eur": 50.0}
    assert not mc.is_stale(stored, dict(stored, net_eur=50.9))
    assert mc.is_stale(stored, dict(stored, spending_eur=51.5))
    assert mc.is_stale(stored, dict(stored, income_eur=None))
    assert not mc.is_stale({"income_eur": None}, {"income_eur": None})


def test_render_report_is_whole_euros_markdown():
    figures = {
        "income_eur": 3000.4,
        "spending_eur": 1000.6,
        "savings_eur": 200.0,
        "net_eur": 1999.8,
        "savings_rate_pct": 66.7,
        "budget": {
            "name": "Example Plan",
            "sections": [
                {"label": "Spending", "planned_total": 900.0, "actual_total": 1000.6}
            ],
        },
        "top_categories": [{"name": "Rent", "actual_eur": 1000.0}],
        "versus_normal": {
            "actual_eur": 1000.6,
            "baseline_eur": 900.0,
            "baseline_months": 5,
            "delta_eur": 100.6,
            "delta_pct": 11.2,
        },
    }
    checklist = [
        {"key": "imports", "label": "Imported", "status": "ok", "detail": "fine"},
        {
            "key": "uncategorized",
            "label": "Categorised",
            "status": "warn",
            "detail": "2 left",
        },
    ]
    text = mc.render_month_close_report("2026-06", figures, checklist)
    assert text.startswith("# Month close 2026-06\n")
    assert "June 2026" in text
    assert "- Income: 3,000 EUR" in text
    assert "- Net: 2,000 EUR (67% of income)" in text
    assert "+101 EUR (+11%)" in text
    assert "- Rent: 1,000 EUR" in text
    assert "## Budget: Example Plan" in text
    assert "- Imported: done" in text
    assert "- 2 left" in text
    # empty parts do not break it
    bare = mc.render_month_close_report(
        "2026-06", {"income_eur": None, "spending_eur": None}, []
    )
    assert "- Income: n/a" in bare


# ── Action Item ──────────────────────────────────────────────────────────────


def _item(db, today):
    return mc.check_month_close_ready(db, today)


def test_action_item_from_the_fifth_for_the_last_month_only(db, bank):
    _clean(db, bank)
    assert _item(db, date(2026, 7, 4)) == []
    items = _item(db, date(2026, 7, 5))
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "spending:month-close:2026-06"
    assert item["title"] == "June is ready to close"
    assert item["severity"] == "low"
    assert item["category"] == "spending"
    assert item["link_page"] == "budget"
    # older months never raise one
    assert all(i["id"] != "spending:month-close:2026-05" for i in items)


def test_action_item_needs_ready_and_not_closed(db, bank):
    _clean(db, bank)
    other = db.get_or_create_portfolio("Example Savings", account_type="bank")
    _tx(db, other, "2026-06-20", "Example Interest", 5.0, "Salary")
    assert _item(db, TODAY) == []
    mc.close_month(db, _fx, TODAY, MONTH, force=True)
    # closed: nothing, even once the lagging account catches up
    _tx(db, other, "2026-07-02", "Example Interest", 5.0, "Salary")
    assert _item(db, TODAY) == []
    mc.reopen_month(db, MONTH)
    assert len(_item(db, TODAY)) == 1


def test_check_is_registered_in_the_aggregator():
    assert mc.check_month_close_ready in checks_for_tests()


def test_item_priority_uses_the_spending_weight():
    from portf_manager.services import action_item_groups

    item = {"category": "spending", "severity": "low", "id": "spending:month-close:x"}
    assert action_item_groups.priority(item, TODAY) == 110


# ── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database
    from portf_server.routers import spending as spending_router

    inst = Database(str(tmp_path / "api.db"))
    spend = inst.find_spending_category_by_name("Spend")["id"]
    income = inst.find_spending_category_by_name("Income")["id"]
    inst.create_spending_category("Rent", parent_id=spend)
    inst.create_spending_category("Salary", parent_id=income)
    km = APIKeyManager(inst)
    km.create_api_key(key_name="t", description="t", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: inst
    app.dependency_overrides[get_api_key_manager] = lambda: km
    monkeypatch.setattr(spending_router, "_fx", _fx)
    monkeypatch.setattr(spending_router, "_today", lambda: TODAY)
    return TestClient(app), inst


def test_routes_get_post_delete(api):
    client, inst = api
    bank = inst.get_or_create_portfolio("Example Bank", account_type="bank")
    _clean(inst, bank)
    url = "/api/v1/spending/month-close"

    got = client.get(url, headers=HEADERS)
    assert got.status_code == 200
    body = got.json()
    assert set(body) == {"month", "closed", "ready", "checklist", "figures", "warnings"}
    assert body["month"] == MONTH
    assert body["closed"] is None
    assert body["ready"] is True
    assert set(body["checklist"][0]) == {
        "key",
        "label",
        "status",
        "count",
        "detail",
        "link_page",
    }

    posted = client.post(url, json={"month": MONTH, "notes": "done"}, headers=HEADERS)
    assert posted.status_code == 200
    closed = posted.json()["closed"]
    assert closed["notes"] == "done"
    assert closed["stale"] is False
    report = client.get(f"/api/v1/saved-reports/{closed['report_id']}", headers=HEADERS)
    assert report.status_code == 200
    assert report.json()["title"] == "Month close 2026-06"

    again = client.get(url, params={"month": MONTH}, headers=HEADERS).json()
    assert again["closed"]["report_id"] == closed["report_id"]

    # registered before /{spending_id}: DELETE does not read "month-close" as an id
    gone = client.delete(url, params={"month": MONTH}, headers=HEADERS)
    assert gone.status_code == 200
    assert gone.json() == {
        "month": MONTH,
        "reopened": True,
        "report_id": closed["report_id"],
    }
    assert inst.get_report(closed["report_id"]) is not None
    assert (
        client.delete(url, params={"month": MONTH}, headers=HEADERS).status_code == 404
    )


def test_route_errors(api):
    client, inst = api
    bank = inst.get_or_create_portfolio("Example Bank", account_type="bank")
    _clean(inst, bank)
    other = inst.get_or_create_portfolio("Example Savings", account_type="bank")
    _tx(inst, other, "2026-06-20", "Example Interest", 5.0, "Salary")
    url = "/api/v1/spending/month-close"
    assert client.post(url, json={"month": MONTH}, headers=HEADERS).status_code == 409
    forced = client.post(url, json={"month": MONTH, "force": True}, headers=HEADERS)
    assert forced.status_code == 200
    assert (
        client.post(url, json={"month": "2026-07"}, headers=HEADERS).status_code == 400
    )
    assert (
        client.post(url, json={"month": "2026-09"}, headers=HEADERS).status_code == 400
    )
    assert (
        client.get(url, params={"month": "2026-09"}, headers=HEADERS).status_code == 400
    )
    assert client.get(url, params={"month": "June"}, headers=HEADERS).status_code == 422


def test_close_reopen_close_keeps_one_report(db, bank):
    _clean(db, bank)
    first = mc.close_month(db, _fx, TODAY, MONTH)
    mc.reopen_month(db, MONTH)
    second = mc.close_month(db, _fx, TODAY, MONTH)
    assert second["closed"]["report_id"] == first["closed"]["report_id"]
    titles = [r["title"] for r in db.list_reports()]
    assert titles == [f"Month close {MONTH}"]
