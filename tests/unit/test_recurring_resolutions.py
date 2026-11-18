"""Recurring resolutions: cancelled / paid elsewhere, stale marks, effects."""

from datetime import date

import pytest
from httpx import AsyncClient

from portf_manager.database import Database
from portf_manager.services import budget as bs
from portf_manager.services import cashflow_forecast as cf
from portf_manager.services import spending_review as sr
from portf_manager.services.action_items import check_recurring_charges
from portf_manager.services.recurring import (
    due_dates,
    last_import_dates,
    load_recurring,
    upcoming_charges,
)

TODAY = date(2026, 7, 15)


def _fx(currency):
    return 1.0


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "res.db"))


@pytest.fixture
def pid(db):
    return db.create_portfolio("Example Bank", account_type="bank")


def _charges(db, pid, merchant, days, amount=9.99):
    for day in days:
        db.create_spending_transaction(
            portfolio_id=pid, date=day, description=merchant, amount=-amount
        )


def _streaming(db, pid, days=("2026-01-15", "2026-02-15", "2026-03-15")):
    _charges(db, pid, "EXAMPLE STREAMING", days)


def _series(db, name="EXAMPLE STREAMING"):
    return next(s for s in load_recurring(db) if s.merchant == name)


class TestLoadRecurring:
    def test_live_resolution_sets_status_and_resolution(self, db, pid):
        _streaming(db, pid)
        db.set_recurring_resolution(
            pid, "EXAMPLE STREAMING", "cancelled", "2026-03-15", "x"
        )
        s = _series(db)
        assert s.status == "cancelled"
        assert s.resolution == {
            "status": "cancelled",
            "since": "2026-03-15",
            "note": "x",
        }
        assert s.resolution_stale is False

    def test_paid_elsewhere_status(self, db, pid):
        _streaming(db, pid)
        db.set_recurring_resolution(
            pid, "example streaming", "paid_elsewhere", "2026-03-15"
        )
        assert _series(db).status == "paid_elsewhere"

    def test_a_later_charge_makes_it_stale_and_keeps_the_detected_status(self, db, pid):
        _streaming(db, pid, ("2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"))
        db.set_recurring_resolution(pid, "EXAMPLE STREAMING", "cancelled", "2026-03-15")
        s = _series(db)
        assert s.resolution_stale is True
        assert s.status in ("active", "missed", "ended")
        assert s.status != "cancelled"
        assert s.resolution["status"] == "cancelled"

    def test_no_resolution_leaves_defaults(self, db, pid):
        _streaming(db, pid)
        s = _series(db)
        assert s.resolution is None and s.resolution_stale is False

    def test_resolution_is_per_account(self, db, pid):
        other = db.create_portfolio("Other Bank", account_type="bank")
        _streaming(db, pid)
        _streaming(db, other)
        db.set_recurring_resolution(pid, "EXAMPLE STREAMING", "cancelled", "2026-03-15")
        by_pid = {s.portfolio_id: s.status for s in load_recurring(db)}
        assert by_pid[pid] == "cancelled" and by_pid[other] != "cancelled"


class TestEffects:
    def test_no_missed_item_but_a_returned_item_when_stale(self, db, pid):
        _streaming(db, pid)
        db.create_spending_transaction(
            portfolio_id=pid, date="2026-04-25", description="OTHER", amount=-1.0
        )
        items = check_recurring_charges(db, today=date(2026, 4, 26))
        assert [i["id"].split(":")[1] for i in items] == ["missed"]
        db.set_recurring_resolution(pid, "EXAMPLE STREAMING", "cancelled", "2026-03-15")
        assert check_recurring_charges(db, today=date(2026, 4, 26)) == []

        _charges(db, pid, "EXAMPLE STREAMING", ["2026-04-15"])
        (item,) = check_recurring_charges(db, today=date(2026, 4, 20))
        assert item["id"] == f"recurring:returned:{pid}:EXAMPLE STREAMING:2026-04-15"
        assert item["severity"] == "low"
        assert "marked cancelled" in item["title"]

    def test_due_dates_and_upcoming_charges_skip_resolved_series(self, db, pid):
        _streaming(db, pid)
        db.create_spending_transaction(
            portfolio_id=pid, date="2026-03-20", description="OTHER", amount=-1.0
        )
        last = last_import_dates(db)
        assert len(upcoming_charges(load_recurring(db), last, date(2026, 6, 30))) > 0
        for status in ("cancelled", "paid_elsewhere"):
            db.set_recurring_resolution(pid, "EXAMPLE STREAMING", status, "2026-03-15")
            series = load_recurring(db)
            assert due_dates(series[0], date(2026, 3, 20), date(2026, 6, 30)) == []
            assert upcoming_charges(series, last, date(2026, 6, 30)) == []

    def test_forecast_does_not_place_a_resolved_series(self, db, pid):
        _streaming(db, pid)
        db.create_spending_transaction(
            portfolio_id=pid, date="2026-03-20", description="OTHER", amount=-1.0
        )
        account = {
            "portfolio_id": pid,
            "name": "Example Bank",
            "currency": "EUR",
            "data_through": date(2026, 3, 20),
            "balance_eur": 1000.0,
            "balance_date": "2026-03-20",
        }
        through = {pid: date(2026, 3, 20)}
        horizon = date(2026, 6, 30)
        placed = cf._bills(load_recurring(db), [account], through, {}, horizon, _fx)
        assert [b["merchant"] for b in placed][:1] == ["EXAMPLE STREAMING"]
        db.set_recurring_resolution(pid, "EXAMPLE STREAMING", "cancelled", "2026-03-15")
        assert cf._bills(load_recurring(db), [account], through, {}, horizon, _fx) == []

    def test_budget_pace_drops_the_resolved_charge_still_due(self, db, pid):
        spend = db.find_spending_category_by_name("Spend")["id"]
        income = db.find_spending_category_by_name("Income")["id"]
        db.create_spending_category("Rent", parent_id=spend)
        db.create_spending_category("Gym", parent_id=spend)
        db.create_spending_category("Salary", parent_id=income)
        for m in range(1, 8):
            db.create_spending_transaction(
                pid, f"2026-{m:02d}-01", "Example Landlord", -1000.0, category="Rent"
            )
        for m in range(1, 7):
            db.create_spending_transaction(
                pid, f"2026-{m:02d}-25", "Example Employer", 3000.0, category="Salary"
            )
        for m in range(4, 7):
            db.create_spending_transaction(
                pid, f"2026-{m:02d}-20", "Example Gym", -30.0, category="Gym"
            )
        budget = db.create_budget("Base", is_active=True)
        db.create_budget_line(budget, "spending", "Gym", 30.0)

        def gym_pace():
            result = bs.compute_budget_variance(
                db, budget, ["2026-07"], _fx, today=TODAY
            )
            section = next(s for s in result["sections"] if s["key"] == "spending")
            line = next(i for i in section["lines"] if i["ref_key"] == "Gym")
            return line["pace"]

        assert gym_pace()["expected_recurring_remaining_eur"] == 30.0
        db.set_recurring_resolution(pid, "Example Gym", "cancelled", "2026-06-20")
        assert gym_pace()["expected_recurring_remaining_eur"] == 0.0

    def test_monthly_review_leaves_resolved_series_out_of_new_and_ended(self, db, pid):
        for m in range(1, 8):
            db.create_spending_transaction(
                pid, f"2026-{m:02d}-25", "Example Employer", 3000.0
            )
        for day in ("2026-04-18", "2026-05-18", "2026-06-18"):
            db.create_spending_transaction(pid, day, "Example Streaming", -10.0)
        for day in ("2026-01-05", "2026-02-05", "2026-03-05"):
            db.create_spending_transaction(pid, day, "Example Old Club", -25.0)
        db.create_spending_transaction(pid, "2026-07-05", "Example Market", -60.0)
        before = sr.build_review(db, _fx, TODAY)["recurring"]
        assert [r["merchant"] for r in before["new"]] == ["Example Streaming"]
        assert [r["merchant"] for r in before["ended"]] == ["Example Old Club"]
        db.set_recurring_resolution(pid, "Example Streaming", "cancelled", "2026-06-18")
        db.set_recurring_resolution(
            pid, "Example Old Club", "paid_elsewhere", "2026-03-05"
        )
        after = sr.build_review(db, _fx, TODAY)["recurring"]
        assert after["new"] == [] and after["ended"] == []


def _bank_with_series(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _streaming(db, pid)
    _charges(db, pid, "EXAMPLE GYM", ["2026-01-02", "2026-02-02", "2026-03-02"], 50.0)
    _charges(db, pid, "EXAMPLE PAPER", ["2026-01-03", "2026-02-03", "2026-03-03"], 20.0)
    return pid


class TestApi:
    @pytest.mark.asyncio
    async def test_totals_sort_and_include_resolved(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = _bank_with_series(test_database)
        base = "/api/v1/spending/recurring"
        # statements run to the 20th of March: nothing is missed or ended
        test_database.create_spending_transaction(
            portfolio_id=pid, date="2026-03-20", description="OTHER", amount=-1.0
        )
        plain = (await async_test_client.get(base, headers=auth_headers)).json()
        assert plain["annual_total_eur"] == round((9.99 + 50.0 + 20.0) * 12, 2)
        assert plain["paid_elsewhere_annual_eur"] == 0.0
        assert plain["cancelled_annual_eur"] == 0.0
        assert [i["merchant"] for i in plain["items"]][0] == "EXAMPLE GYM"

        for merchant, status in (
            ("EXAMPLE GYM", "paid_elsewhere"),
            ("EXAMPLE PAPER", "cancelled"),
        ):
            resp = await async_test_client.put(
                base + "/resolution",
                json={
                    "portfolio_id": pid,
                    "merchant": merchant,
                    "status": status,
                    "note": "n",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.json() == {
                "portfolio_id": pid,
                "merchant": merchant,
                "status": status,
                "since": {"EXAMPLE GYM": "2026-03-02", "EXAMPLE PAPER": "2026-03-03"}[
                    merchant
                ],
                "note": "n",
            }

        data = (await async_test_client.get(base, headers=auth_headers)).json()
        assert data["annual_total_eur"] == round((9.99 + 50.0) * 12, 2)
        assert data["monthly_total_eur"] == round((9.99 + 50.0), 2)
        assert data["paid_elsewhere_annual_eur"] == 600.0
        assert data["cancelled_annual_eur"] == 240.0
        # default order: active first, then paid elsewhere, then cancelled
        assert [i["status"] for i in data["items"]] == [
            "active",
            "paid_elsewhere",
            "cancelled",
        ]
        gym = next(i for i in data["items"] if i["merchant"] == "EXAMPLE GYM")
        assert gym["resolution"]["status"] == "paid_elsewhere"
        assert gym["resolution_stale"] is False

        by_cost = (
            await async_test_client.get(
                base + "?sort=yearly_cost", headers=auth_headers
            )
        ).json()
        assert [i["merchant"] for i in by_cost["items"]] == [
            "EXAMPLE GYM",
            "EXAMPLE PAPER",
            "EXAMPLE STREAMING",
        ]

        hidden = (
            await async_test_client.get(
                base + "?include_resolved=false", headers=auth_headers
            )
        ).json()
        assert [i["merchant"] for i in hidden["items"]] == ["EXAMPLE STREAMING"]
        assert hidden["annual_total_eur"] == round(9.99 * 12, 2)
        assert hidden["cancelled_annual_eur"] == 240.0

    @pytest.mark.asyncio
    async def test_put_errors(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = _bank_with_series(test_database)
        url = "/api/v1/spending/recurring/resolution"
        resp = await async_test_client.put(
            url,
            json={
                "portfolio_id": pid,
                "merchant": "NO SUCH SHOP",
                "status": "cancelled",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404
        resp = await async_test_client.put(
            url,
            json={"portfolio_id": pid, "merchant": "EXAMPLE GYM", "status": "ended"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert test_database.list_recurring_resolutions() == []

    @pytest.mark.asyncio
    async def test_clear(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = _bank_with_series(test_database)
        url = "/api/v1/spending/recurring/resolution"
        await async_test_client.put(
            url,
            json={
                "portfolio_id": pid,
                "merchant": "EXAMPLE GYM",
                "status": "cancelled",
            },
            headers=auth_headers,
        )
        resp = await async_test_client.delete(
            url,
            params={"portfolio_id": pid, "merchant": "example gym"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert test_database.list_recurring_resolutions() == []
        resp = await async_test_client.delete(
            url,
            params={"portfolio_id": pid, "merchant": "EXAMPLE GYM"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
