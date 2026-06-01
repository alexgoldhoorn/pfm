"""Tests for the rebalancing and research/valuation routers."""

import pytest
from httpx import AsyncClient
from fastapi import status


class TestRebalance:
    @pytest.mark.asyncio
    async def test_set_and_get_targets(
        self, async_test_client: AsyncClient, auth_headers
    ):
        targets = [
            {"asset_type": "stock", "target_pct": 60.0},
            {"asset_type": "etf", "target_pct": 40.0},
        ]
        resp = await async_test_client.put(
            "/api/v1/rebalance/targets", json=targets, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 2

        resp2 = await async_test_client.get(
            "/api/v1/rebalance/targets", headers=auth_headers
        )
        assert resp2.status_code == status.HTTP_200_OK
        types = {t["asset_type"] for t in resp2.json()}
        assert types == {"stock", "etf"}

    @pytest.mark.asyncio
    async def test_analysis_shape(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/rebalance/analysis", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "total_value_eur" in data
        assert "allocations" in data
        assert "actions" in data
        assert isinstance(data["allocations"], list)

    @pytest.mark.asyncio
    async def test_target_pct_validation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        # >100 should be rejected by pydantic
        resp = await async_test_client.put(
            "/api/v1/rebalance/targets",
            json=[{"asset_type": "stock", "target_pct": 150.0}],
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestResearch:
    @pytest.mark.asyncio
    async def test_report_404_when_none(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        # Create an asset first
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        resp = await async_test_client.get(
            f"/api/v1/research/{sample_asset_data['symbol']}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_unknown_symbol_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/research/NOPE", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_set_and_get_price_targets(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        sym = sample_asset_data["symbol"]
        resp = await async_test_client.put(
            f"/api/v1/research/{sym}/targets",
            json={"buy_below": 100.0, "sell_above": 130.0},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["buy_below"] == 100.0
        assert data["sell_above"] == 130.0

    @pytest.mark.asyncio
    async def test_alerts_check_shape(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/research/alerts/check", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "alerts" in data
        assert "total" in data
