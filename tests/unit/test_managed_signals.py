"""Managed (robo-advisor) portfolios stay quiet: no price signals, no research nags."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager.positions import self_managed_held_asset_ids
from portf_manager.services.action_items import (
    check_price_alerts,
    check_stale_research,
)
from portf_server.routers.research import compute_price_target_alerts


def _buy(db, aid, pid, qty=10.0, price=100.0, days_ago=30):
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=qty,
        price=price,
        total_amount=qty * price,
        transaction_date=(date.today() - timedelta(days=days_ago)).isoformat(),
        portfolio_id=pid,
        currency="EUR",
    )


def _seed(db):
    """A self-managed broker, a managed robo account, and three priced assets.

    ROBO is held only in the managed account, SELF only in the broker, BOTH in
    each. Every asset has a buy-below target far above its price.
    """
    self_pid = db.get_or_create_portfolio("Broker", base_currency="EUR")
    robo_pid = db.create_portfolio("Robo", base_currency="EUR", is_managed=True)
    ids = {}
    for sym in ("ROBO", "SELF", "BOTH"):
        aid = db.create_asset(sym, f"{sym} Fund", "etf", currency="EUR")
        db.create_price(aid, 50.0, date.today().isoformat())
        db.upsert_price_target(aid, buy_below=80.0)
        ids[sym] = aid
    _buy(db, ids["ROBO"], robo_pid)
    _buy(db, ids["SELF"], self_pid)
    _buy(db, ids["BOTH"], self_pid)
    _buy(db, ids["BOTH"], robo_pid)
    return self_pid, robo_pid, ids


class TestSelfManagedHeldAssetIds:
    def test_pure_helper(self):
        txns = [
            {
                "portfolio_id": 1,
                "asset_id": 10,
                "transaction_type": "buy",
                "quantity": 1,
                "total_amount": 1,
                "transaction_date": "2026-01-01",
            },
            {
                "portfolio_id": 2,
                "asset_id": 11,
                "transaction_type": "buy",
                "quantity": 1,
                "total_amount": 1,
                "transaction_date": "2026-01-01",
            },
            {
                "portfolio_id": 1,
                "asset_id": 12,
                "transaction_type": "buy",
                "quantity": 1,
                "total_amount": 1,
                "transaction_date": "2026-01-01",
            },
            {
                "portfolio_id": 1,
                "asset_id": 12,
                "transaction_type": "sell",
                "quantity": 1,
                "total_amount": 1,
                "transaction_date": "2026-02-01",
            },
        ]
        # 11 is in the managed portfolio; 12 was sold out
        assert self_managed_held_asset_ids(txns, {2}) == {10}
        assert self_managed_held_asset_ids(txns, set()) == {10, 11}


class TestPriceTargetAlerts:
    def test_managed_only_holding_raises_no_alert(self, test_database):
        _seed(test_database)
        symbols = {a["symbol"] for a in compute_price_target_alerts(test_database)}
        assert symbols == {"SELF", "BOTH"}

    def test_unheld_target_still_alerts(self, test_database):
        _seed(test_database)
        aid = test_database.create_asset("WISH", "Wish Corp", "stock", currency="EUR")
        test_database.create_price(aid, 50.0, date.today().isoformat())
        test_database.upsert_price_target(aid, buy_below=80.0)
        symbols = {a["symbol"] for a in compute_price_target_alerts(test_database)}
        assert "WISH" in symbols

    def test_unflagging_the_portfolio_brings_the_alert_back(self, test_database):
        _, robo_pid, _ = _seed(test_database)
        test_database.update_portfolio(robo_pid, is_managed=False)
        symbols = {a["symbol"] for a in compute_price_target_alerts(test_database)}
        assert symbols == {"ROBO", "SELF", "BOTH"}


class TestStaleResearch:
    def test_managed_only_holding_is_not_nagged(self, test_database):
        _seed(test_database)
        items = check_stale_research(test_database)
        assert len(items) == 1
        assert items[0]["context"]["symbols"] == ["BOTH", "SELF"]

    def test_only_managed_holdings_means_no_item(self, test_database):
        robo_pid = test_database.create_portfolio("Robo", "EUR", is_managed=True)
        aid = test_database.create_asset("ROBO", "Robo Fund", "etf", currency="EUR")
        _buy(test_database, aid, robo_pid)
        assert check_stale_research(test_database) == []


class TestPriceAlertItems:
    def test_watchlist_and_research_hit_on_one_symbol_is_one_item(self, test_database):
        _seed(test_database)
        test_database.add_watchlist("SELF", name="SELF Fund", buy_below=60.0)
        with patch(
            "portf_server.routers.watchlist._fetch_price_cached",
            return_value=(50.0, "2026-11-01T10:00:00"),
        ):
            items = check_price_alerts(test_database)
        by_symbol = {}
        for i in items:
            by_symbol.setdefault(i["context"]["symbol"], []).append(i)
        assert len(by_symbol["SELF"]) == 1
        assert by_symbol["SELF"][0]["id"] == "watchlist:SELF"
        assert "research target (BUY)" in by_symbol["SELF"][0]["detail"]
        # the research-only symbol keeps its own row, the managed one has none
        assert [i["id"] for i in by_symbol["BOTH"]] == ["research:BOTH"]
        assert "ROBO" not in by_symbol


class TestPortfolioApi:
    @pytest.mark.asyncio
    async def test_create_and_update_is_managed(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/portfolios/",
            json={"name": "Robo", "base_currency": "EUR", "is_managed": True},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        pid = resp.json()["id"]
        assert resp.json()["is_managed"] is True

        listing = await async_test_client.get(
            "/api/v1/portfolios/", headers=auth_headers
        )
        row = next(p for p in listing.json() if p["id"] == pid)
        assert row["is_managed"] is True

        resp = await async_test_client.put(
            f"/api/v1/portfolios/{pid}",
            json={"is_managed": False},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        listing = await async_test_client.get(
            "/api/v1/portfolios/", headers=auth_headers
        )
        row = next(p for p in listing.json() if p["id"] == pid)
        assert row["is_managed"] is False

    @pytest.mark.asyncio
    async def test_default_is_not_managed(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/portfolios/",
            json={"name": "Plain", "base_currency": "EUR"},
            headers=auth_headers,
        )
        assert resp.json()["is_managed"] is False
