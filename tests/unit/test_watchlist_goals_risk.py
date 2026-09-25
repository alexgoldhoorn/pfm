"""Tests for watchlist, goals, fees, diversification, and risk endpoints."""

import time

import pytest
from httpx import AsyncClient
from fastapi import status


class TestWatchlist:
    @pytest.mark.asyncio
    async def test_add_list_delete(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "portf_manager.market._fetch_quote_live",
            lambda symbol: {
                "symbol": symbol,
                "price": 45.0,
                "currency": "USD",
                "fetched_at": time.time(),
            },
        )
        # An explicit name skips the yfinance name lookup
        resp = await async_test_client.post(
            "/api/v1/watchlist/",
            json={
                "symbol": "TESTW",
                "name": "Test Watch",
                "asset_type": "stock",
                "buy_below": 50,
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED

        resp2 = await async_test_client.get("/api/v1/watchlist/", headers=auth_headers)
        assert resp2.status_code == status.HTTP_200_OK
        (entry,) = [e for e in resp2.json() if e["symbol"] == "TESTW"]
        assert entry["current_price"] == 45.0
        assert entry["distance_to_buy_pct"] == -10.0
        assert entry["in_buy_zone"] is True

        resp3 = await async_test_client.delete(
            "/api/v1/watchlist/TESTW", headers=auth_headers
        )
        assert resp3.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_list_without_a_price_has_no_buy_zone(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "portf_manager.market._fetch_quote_live", lambda symbol: None
        )
        await async_test_client.post(
            "/api/v1/watchlist/",
            json={"symbol": "NOPX", "name": "No Price", "buy_below": 50},
            headers=auth_headers,
        )
        resp = await async_test_client.get("/api/v1/watchlist/", headers=auth_headers)
        (entry,) = [e for e in resp.json() if e["symbol"] == "NOPX"]
        assert entry["current_price"] is None
        assert entry["distance_to_buy_pct"] is None
        assert entry["in_buy_zone"] is False

    @pytest.mark.asyncio
    async def test_delete_missing(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.delete(
            "/api/v1/watchlist/NOPE", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


class TestGoals:
    @pytest.mark.asyncio
    async def test_create_list_delete(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/goals/",
            json={
                "name": "Test Goal",
                "target_amount_eur": 100000,
                "target_date": "2035-01-01",
                "monthly_contribution_eur": 500,
                "expected_return_pct": 7,
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        gid = resp.json()["id"]

        resp2 = await async_test_client.get("/api/v1/goals/", headers=auth_headers)
        assert resp2.status_code == status.HTTP_200_OK
        goal = next(g for g in resp2.json() if g["id"] == gid)
        assert "progress_pct" in goal
        assert "projected_value_eur" in goal
        assert "on_track" in goal

        resp3 = await async_test_client.delete(
            f"/api/v1/goals/{gid}", headers=auth_headers
        )
        assert resp3.status_code == status.HTTP_200_OK


class TestFeesRiskDiversification:
    @pytest.mark.asyncio
    async def test_fees(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/analytics/fees", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert "total_fees_eur" in d and "by_broker" in d and "fee_drag_pct" in d

    @pytest.mark.asyncio
    async def test_risk_insufficient_data(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/risk", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        # Fresh DB has no snapshots → note returned
        assert "max_drawdown_pct" in resp.json()

    @pytest.mark.asyncio
    async def test_diversification(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/analytics/diversification", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        # The keys ~/mcp/scripts/finance_review.py reads must not move.
        for key in (
            "by_asset_type",
            "by_currency",
            "by_sector",
            "by_country",
            "concentration_hhi",
            "largest_position_pct",
            "largest_position_name",
        ):
            assert key in d
        # New look-through fields.
        for key in (
            "by_asset_class",
            "by_region",
            "by_region_equity",
            "by_currency_exposure",
            "coverage",
        ):
            assert key in d
        assert "classified_pct" in d["coverage"]
        assert "unprofiled" in d["coverage"]

    @pytest.mark.asyncio
    async def test_fund_overlap_empty_portfolio(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/fund-overlap", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["groups"] == []


class TestLoginKey:
    @staticmethod
    async def _register(client: AsyncClient) -> None:
        reg = await client.post(
            "/api/v1/auth/register",
            json={
                "username": "pwuser",
                "email": "pwuser@example.com",
                "password": "secret12345",
            },
        )
        assert reg.status_code == status.HTTP_201_CREATED

    @pytest.mark.asyncio
    async def test_login_key_returns_server_key(
        self, async_test_client: AsyncClient, monkeypatch
    ):
        monkeypatch.setenv("SERVER_API_KEY", "server-key-123")
        await self._register(async_test_client)
        resp = await async_test_client.post(
            "/api/v1/auth/login-key",
            json={"username": "pwuser", "password": "secret12345"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["api_key"] == "server-key-123"

    @pytest.mark.asyncio
    async def test_login_key_without_server_key_is_500(
        self, async_test_client: AsyncClient, monkeypatch
    ):
        monkeypatch.delenv("SERVER_API_KEY", raising=False)
        await self._register(async_test_client)
        resp = await async_test_client.post(
            "/api/v1/auth/login-key",
            json={"username": "pwuser", "password": "secret12345"},
        )
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    @pytest.mark.asyncio
    async def test_login_key_bad_password(self, async_test_client: AsyncClient):
        await async_test_client.post(
            "/api/v1/auth/register",
            json={
                "username": "pwuser2",
                "email": "pwuser2@example.com",
                "password": "secret12345",
            },
        )
        resp = await async_test_client.post(
            "/api/v1/auth/login-key",
            json={"username": "pwuser2", "password": "wrongpass"},
        )
        assert resp.status_code == 401
