"""Decision journal: router, Action Item and chat tools."""

import json
from datetime import date, timedelta

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager.services.action_items import (
    check_decision_reviews,
    checks_for_tests,
)
from portf_server.chat_tools import execute_tool

TODAY = date.today()


def _asset_with_price(db, symbol="EXM", price=100.0):
    aid = db.create_asset(symbol, "Example Corp", "stock", currency="EUR")
    db.create_price(aid, price, TODAY.isoformat())
    return aid


class TestDecisionsApi:
    @pytest.mark.asyncio
    async def test_create_attaches_latest_price_and_lists(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        aid = _asset_with_price(test_database)
        resp = await async_test_client.post(
            "/api/v1/decisions/",
            json={
                "title": "Hold Example below the sell gate",
                "action": "hold",
                "symbol": "exm",
                "rationale": "Still below the 30% gate",
                "review_on": (TODAY + timedelta(days=90)).isoformat(),
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        row = resp.json()
        assert row["symbol"] == "EXM"
        assert row["asset_id"] == aid
        assert row["price_at_decision"] == 100.0
        assert row["currency"] == "EUR"
        assert row["decided_on"] == TODAY.isoformat()
        assert row["status"] == "open"
        assert row["source"] == "web"

        # the price moves: the listing shows the change since the decision
        test_database.create_price(aid, 110.0, (TODAY + timedelta(days=1)).isoformat())
        listing = await async_test_client.get(
            "/api/v1/decisions/", params={"status": "open"}, headers=auth_headers
        )
        assert listing.status_code == status.HTTP_200_OK
        data = listing.json()
        assert data["total"] == 1 and data["due"] == 0
        assert data["items"][0]["price_now"] == 110.0
        assert data["items"][0]["price_change_pct"] == 10.0

    @pytest.mark.asyncio
    async def test_unknown_symbol_is_kept_without_price(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/decisions/",
            json={"title": "Keep the DCA going", "action": "dca", "symbol": "nope"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["symbol"] == "NOPE"
        assert resp.json()["asset_id"] is None
        assert resp.json()["price_at_decision"] is None

    @pytest.mark.asyncio
    async def test_review_closes_reopen_clears_and_filters(
        self, async_test_client: AsyncClient, auth_headers
    ):
        created = await async_test_client.post(
            "/api/v1/decisions/",
            json={"title": "Sell the dust position", "action": "sell"},
            headers=auth_headers,
        )
        did = created.json()["id"]
        resp = await async_test_client.post(
            f"/api/v1/decisions/{did}/review",
            json={"outcome": "Sold, freed cash", "outcome_rating": "good"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["status"] == "reviewed"
        assert resp.json()["outcome_rating"] == "good"

        open_ = await async_test_client.get(
            "/api/v1/decisions/", params={"status": "open"}, headers=auth_headers
        )
        assert open_.json()["total"] == 0
        reviewed = await async_test_client.get(
            "/api/v1/decisions/", params={"status": "reviewed"}, headers=auth_headers
        )
        assert reviewed.json()["total"] == 1

        resp = await async_test_client.post(
            f"/api/v1/decisions/{did}/review",
            json={"outcome_rating": "unclear", "reviewed": False},
            headers=auth_headers,
        )
        assert resp.json()["status"] == "open"
        assert resp.json()["outcome"] is None
        assert resp.json()["reviewed_at"] is None

    @pytest.mark.asyncio
    async def test_update_delete_and_validation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        created = await async_test_client.post(
            "/api/v1/decisions/",
            json={"title": "Rebalance bonds", "action": "rebalance"},
            headers=auth_headers,
        )
        did = created.json()["id"]
        resp = await async_test_client.put(
            f"/api/v1/decisions/{did}",
            json={"expectation": "Bonds back to 10%", "review_on": "2027-01-15"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["expectation"] == "Bonds back to 10%"
        assert resp.json()["review_on"] == "2027-01-15"
        # the title was not sent, so it stays
        assert resp.json()["title"] == "Rebalance bonds"

        bad = await async_test_client.post(
            "/api/v1/decisions/",
            json={"title": "x", "action": "panic"},
            headers=auth_headers,
        )
        assert bad.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        bad = await async_test_client.get(
            "/api/v1/decisions/", params={"status": "later"}, headers=auth_headers
        )
        assert bad.status_code == status.HTTP_400_BAD_REQUEST

        resp = await async_test_client.delete(
            f"/api/v1/decisions/{did}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        resp = await async_test_client.get(
            f"/api/v1/decisions/{did}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


class TestDecisionReviewActionItem:
    def test_registered(self):
        assert check_decision_reviews in checks_for_tests()

    def test_due_open_decision_raises_item_with_price_move(self, test_database):
        aid = _asset_with_price(test_database, price=100.0)
        test_database.create_decision(
            decided_on=(TODAY - timedelta(days=100)).isoformat(),
            action="hold",
            title="Hold Example",
            symbol="EXM",
            asset_id=aid,
            price_at_decision=80.0,
            review_on=(TODAY - timedelta(days=1)).isoformat(),
        )
        items = check_decision_reviews(test_database)
        assert len(items) == 1
        assert items[0]["id"].startswith("decision:review:")
        assert items[0]["title"] == "Review your decision: Hold Example"
        assert "+25.0% since then" in items[0]["detail"]
        assert items[0]["link_page"] == "decisions"

    def test_future_review_reviewed_and_undated_are_silent(self, test_database):
        test_database.create_decision(
            decided_on=TODAY.isoformat(),
            action="dca",
            title="Future",
            review_on=(TODAY + timedelta(days=30)).isoformat(),
        )
        test_database.create_decision(
            decided_on=TODAY.isoformat(), action="other", title="No date"
        )
        did = test_database.create_decision(
            decided_on=TODAY.isoformat(),
            action="sell",
            title="Done",
            review_on=(TODAY - timedelta(days=5)).isoformat(),
        )
        test_database.update_decision(
            did, outcome_rating="good", reviewed_at="2026-10-01T10:00:00"
        )
        assert check_decision_reviews(test_database) == []


class TestDecisionChatTools:
    def test_record_then_get(self, test_database):
        _asset_with_price(test_database, price=50.0)
        out = json.loads(
            execute_tool(
                "record_decision",
                {
                    "title": "Keep buying Example monthly",
                    "action": "dca",
                    "symbol": "EXM",
                    "rationale": "Below fair value",
                    "review_on": (TODAY + timedelta(days=60)).isoformat(),
                },
                test_database,
            )
        )
        assert out["recorded"] is True
        assert out["price_at_decision"] == 50.0
        stored = test_database.get_decision(out["id"])
        assert stored["source"] == "chat"

        listing = json.loads(execute_tool("get_decisions", {}, test_database))
        assert listing["count"] == 1
        assert listing["decisions"][0]["action"] == "dca"
        assert listing["decisions"][0]["status"] == "open"

    def test_record_rejects_unknown_action(self, test_database):
        out = json.loads(
            execute_tool(
                "record_decision", {"title": "x", "action": "yolo"}, test_database
            )
        )
        assert "error" in out
        assert test_database.get_decisions() == []

    def test_get_decisions_bad_status(self, test_database):
        out = json.loads(
            execute_tool("get_decisions", {"status": "soon"}, test_database)
        )
        assert "error" in out


class TestAttributionChatTool:
    def test_ytd_attribution_shape(self, test_database):
        pid = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
        aid = _asset_with_price(test_database, price=120.0)
        last_year = date(TODAY.year - 1, 12, 1)
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="buy",
            quantity=10.0,
            price=90.0,
            total_amount=900.0,
            transaction_date=last_year.isoformat(),
            portfolio_id=pid,
            currency="EUR",
        )
        test_database.create_price(aid, 100.0, date(TODAY.year, 1, 1).isoformat())
        out = json.loads(
            execute_tool("get_return_attribution", {"period": "ytd"}, test_database)
        )
        assert out["period"]["start"] == f"{TODAY.year}-01-01"
        assert out["totals"]["gain_eur"] == 200.0
        assert out["top_contributors"][0]["symbol"] == "EXM"
        assert out["top_detractors"] == []
        assert "method" in out

    def test_unknown_period(self, test_database):
        out = json.loads(
            execute_tool("get_return_attribution", {"period": "2w"}, test_database)
        )
        assert "error" in out
