"""Server-side follow-up state for Action Items and Data Quality findings."""

from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from portf_manager.services.action_item_acks import (
    ack_is_active,
    annotate_items,
    effective_status,
    get_ack,
    list_acks,
    set_ack,
    summarize_items,
)
from portf_manager.services.action_items import (
    action_items_report,
    check_data_quality,
)


def _zero_price_buy(db, symbol="ZP"):
    """A buy at price 0 — one dq_suspicious ``zero_price`` finding."""
    pid = db.get_or_create_portfolio("Broker", base_currency="EUR")
    aid = db.create_asset(symbol, f"{symbol} Co", "stock", currency="EUR")
    tx_id = db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=10.0,
        price=0.0,
        total_amount=0.0,
        transaction_date=date.today().isoformat(),
        portfolio_id=pid,
        currency="EUR",
    )
    return tx_id


class TestAckStore:
    def test_checked_persists_with_note_and_lists(self, test_database):
        ack = set_ack(test_database, "dq:duplicates", "checked", note="partial fills")
        assert ack["status"] == "checked"
        assert ack["note"] == "partial fills"
        assert ack["at"]
        stored = list_acks(test_database)
        assert stored["dq:duplicates"]["note"] == "partial fills"
        assert get_ack(test_database, "dq:duplicates")["id"] == "dq:duplicates"

    def test_snooze_by_days_sets_until_and_expires(self, test_database):
        today = date(2026, 10, 24)
        ack = set_ack(test_database, "goals:3", "snoozed", days=7, today=today)
        assert ack["until"] == "2026-10-31"
        assert ack_is_active(ack, today)
        assert ack_is_active(ack, date(2026, 10, 31))
        assert not ack_is_active(ack, date(2026, 11, 1))
        assert effective_status(ack, date(2026, 11, 1)) == "open"

    def test_snooze_needs_a_date(self, test_database):
        with pytest.raises(ValueError):
            set_ack(test_database, "goals:3", "snoozed")
        with pytest.raises(ValueError):
            set_ack(
                test_database,
                "goals:3",
                "snoozed",
                until=(date.today() - timedelta(days=1)).isoformat(),
            )

    def test_unknown_status_rejected(self, test_database):
        with pytest.raises(ValueError):
            set_ack(test_database, "goals:3", "done")

    def test_open_clears_the_record(self, test_database):
        set_ack(test_database, "goals:3", "checked")
        assert set_ack(test_database, "goals:3", "open") is None
        assert "goals:3" not in list_acks(test_database)

    def test_backend_without_settings_store(self):
        class Bare:
            pass

        assert list_acks(Bare()) == {}
        with pytest.raises(NotImplementedError):
            set_ack(Bare(), "x", "checked")

    def test_annotate_and_summarize(self):
        items = [
            {"id": "a", "severity": "high", "category": "errors"},
            {"id": "b", "severity": "low", "category": "goals"},
            {"id": "c", "severity": "medium", "category": "goals"},
        ]
        acks = {
            "a": {"status": "checked"},
            "b": {"status": "snoozed", "until": "2026-12-01"},
        }
        annotate_items(items, acks, date(2026, 10, 24))
        assert [i["status"] for i in items] == ["checked", "snoozed", "open"]
        summary = summarize_items(items)
        assert summary["total"] == 3
        assert summary["open"] == 1
        assert summary["checked"] == 1
        assert summary["snoozed"] == 1
        assert summary["open_by_severity"] == {"high": 0, "medium": 1, "low": 0}
        assert summary["open_by_category"] == {"goals": 1}


class TestDataQualityHonoursChecks:
    def test_checked_finding_drops_out_of_the_summary_item(self, test_database):
        tx_id = _zero_price_buy(test_database)
        items = check_data_quality(test_database)
        susp = next(i for i in items if i["id"] == "dq:suspicious")
        finding_id = f"dq:susp:{tx_id}:zero_price"
        assert susp["context"]["finding_ids"] == [finding_id]
        assert susp["context"]["checked"] == 0

        set_ack(test_database, finding_id, "checked", note="known placeholder")
        assert check_data_quality(test_database) == []

    def test_partially_checked_counts_the_rest(self, test_database):
        tx_a = _zero_price_buy(test_database, "AA")
        _zero_price_buy(test_database, "BB")
        set_ack(test_database, f"dq:susp:{tx_a}:zero_price", "checked")
        items = check_data_quality(test_database)
        susp = next(i for i in items if i["id"] == "dq:suspicious")
        assert susp["context"]["count"] == 1
        assert susp["context"]["checked"] == 1
        assert "1 already checked" in susp["detail"]


class TestReport:
    def test_open_filters_but_summary_counts_everything(self, test_database):
        _zero_price_buy(test_database)
        full = action_items_report(test_database, status="all")
        assert any(i["id"] == "dq:suspicious" for i in full["items"])
        assert all("status" in i and "ack" in i for i in full["items"])

        set_ack(test_database, "dq:suspicious", "snoozed", days=30)
        open_only = action_items_report(test_database, status="open")
        assert not any(i["id"] == "dq:suspicious" for i in open_only["items"])
        assert open_only["summary"]["snoozed"] == 1
        assert open_only["acks"]["dq:suspicious"]["status"] == "snoozed"
        everything = action_items_report(test_database, status="all")
        snoozed = next(i for i in everything["items"] if i["id"] == "dq:suspicious")
        assert snoozed["status"] == "snoozed"

    def test_bad_status(self, test_database):
        with pytest.raises(ValueError):
            action_items_report(test_database, status="done")


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_ack_round_trip(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _zero_price_buy(test_database)
        resp = await async_test_client.post(
            "/api/v1/action-items/ack",
            json={"id": "dq:suspicious", "status": "checked", "note": "fine"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ack"]["note"] == "fine"

        resp = await async_test_client.get(
            "/api/v1/action-items/acks", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["acks"]["dq:suspicious"]["status"] == "checked"

        resp = await async_test_client.get(
            "/api/v1/action-items/", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert not any(i["id"] == "dq:suspicious" for i in data["items"])
        assert data["summary"]["checked"] == 1
        assert "generated_at" in data

        resp = await async_test_client.get(
            "/api/v1/action-items/?status=all", headers=auth_headers
        )
        checked = next(i for i in resp.json()["items"] if i["id"] == "dq:suspicious")
        assert checked["status"] == "checked"
        assert checked["ack"]["note"] == "fine"

        resp = await async_test_client.post(
            "/api/v1/action-items/ack",
            json={"id": "dq:suspicious", "status": "open"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ack"] is None

    @pytest.mark.asyncio
    async def test_snooze_without_date_is_422(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/action-items/ack",
            json={"id": "goals:1", "status": "snoozed"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_requires_api_key(self, async_test_client: AsyncClient):
        resp = await async_test_client.post(
            "/api/v1/action-items/ack", json={"id": "x", "status": "checked"}
        )
        assert resp.status_code == 401
