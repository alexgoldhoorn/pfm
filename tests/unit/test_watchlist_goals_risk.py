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

        # the baseline is today's net worth (an empty test DB: 0)
        assert resp.json()["start_networth_eur"] == 0
        assert resp.json()["notes"] is None

        resp2 = await async_test_client.get("/api/v1/goals/", headers=auth_headers)
        assert resp2.status_code == status.HTTP_200_OK
        goal = next(g for g in resp2.json() if g["id"] == gid)
        assert "progress_pct" in goal
        assert "projected_value_eur" in goal
        assert "on_track" in goal
        assert goal["status"] == "off_track"
        assert goal["start_date"] is not None
        # younger than a month: no pace yet, but the gain is known
        assert goal["actual_monthly_eur"] is None
        assert goal["gained_since_start_eur"] == 0

        resp3 = await async_test_client.delete(
            f"/api/v1/goals/{gid}", headers=auth_headers
        )
        assert resp3.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_update_keeps_baseline_unless_sent(
        self, async_test_client: AsyncClient, auth_headers
    ):
        body = {
            "name": "Example Fund",
            "target_amount_eur": 20000,
            "target_date": "2030-06-01",
            "monthly_contribution_eur": 100,
            "expected_return_pct": 4,
            "start_networth_eur": 5000,
            "notes": "Starter",
        }
        resp = await async_test_client.post(
            "/api/v1/goals/", json=body, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_201_CREATED
        gid = resp.json()["id"]
        assert resp.json()["start_networth_eur"] == 5000

        # the original five fields only: baseline and notes survive
        plain = {
            k: v for k, v in body.items() if k not in ("start_networth_eur", "notes")
        }
        plain["monthly_contribution_eur"] = 250
        resp = await async_test_client.put(
            f"/api/v1/goals/{gid}", json=plain, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["monthly_contribution_eur"] == 250
        assert resp.json()["start_networth_eur"] == 5000
        assert resp.json()["notes"] == "Starter"

        # an explicit baseline resets it; an empty string clears the notes
        resp = await async_test_client.put(
            f"/api/v1/goals/{gid}",
            json={**plain, "start_networth_eur": 0, "notes": ""},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["start_networth_eur"] == 0
        assert resp.json()["notes"] == ""

        resp = await async_test_client.put(
            "/api/v1/goals/999999", json=plain, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_summary_counts_statuses(
        self, async_test_client: AsyncClient, auth_headers
    ):
        for name, target, when in (
            ("Done", 0.01, "2040-01-01"),
            ("Late", 1_000_000, "2020-01-01"),
            ("Far", 1_000_000_000, "2040-01-01"),
        ):
            resp = await async_test_client.post(
                "/api/v1/goals/",
                json={
                    "name": name,
                    "target_amount_eur": target,
                    "target_date": when,
                    "monthly_contribution_eur": 10,
                    "expected_return_pct": 1,
                },
                headers=auth_headers,
            )
            assert resp.status_code == status.HTTP_201_CREATED
        resp = await async_test_client.get(
            "/api/v1/goals/summary", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        by_name = {g["name"]: g["status"] for g in data["goals"]}
        # an empty DB has net worth 0, so a positive target is never achieved,
        # but 10 EUR a month reaches a cent-sized one
        assert by_name == {"Done": "on_track", "Late": "overdue", "Far": "off_track"}
        assert data["summary"]["total"] == 3
        assert data["summary"]["on_track"] == 1
        assert data["summary"]["overdue"] == 1
        assert data["summary"]["off_track"] == 1
        assert data["summary"]["planned_monthly_eur"] == 30
        assert data["summary"]["next_target_date"] == "2020-01-01"


class TestFeesRiskDiversification:
    @pytest.mark.asyncio
    async def test_fees(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/analytics/fees", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert "total_fees_eur" in d and "by_broker" in d and "fee_drag_pct" in d
        # Empty portfolio: the block is present, with nothing covered.
        oc = d["ongoing_charges"]
        assert oc["weighted_avg_pct"] is None and oc["projection"] == []
        assert oc["uncovered"] == []

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
