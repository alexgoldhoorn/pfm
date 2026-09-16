"""Fund profile CRUD, refresh and AI suggestion."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import AsyncClient


async def _make_fund(client, auth_headers, symbol="IE0000000001"):
    resp = await client.post(
        "/api/v1/assets/",
        headers=auth_headers,
        json={
            "symbol": symbol,
            "name": "Example World Index Fund",
            "asset_type": "etf",
            "currency": "EUR",
        },
    )
    assert resp.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
    return resp.json()["id"]


class TestRouteOrder:
    @pytest.mark.asyncio
    async def test_benchmarks_is_not_matched_as_an_asset_id(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/fund-profiles/benchmarks", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        keys = {c["key"] for c in resp.json()["benchmarks"]}
        assert "msci_world" in keys


class TestList:
    @pytest.mark.asyncio
    async def test_lists_held_funds_with_profile_status(
        self, async_test_client: AsyncClient, auth_headers
    ):
        await _make_fund(async_test_client, auth_headers)
        resp = await async_test_client.get(
            "/api/v1/fund-profiles/", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        funds = resp.json()["funds"]
        assert funds[0]["symbol"] == "IE0000000001"
        assert funds[0]["has_profile"] is False


class TestPut:
    @pytest.mark.asyncio
    async def test_saves_a_manual_profile(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        resp = await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 0.7, "japan": 0.3},
                "sectors": {"Technology": 1.0},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["source"] == "manual"

    @pytest.mark.asyncio
    async def test_rejects_weights_that_do_not_sum_to_one(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        resp = await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": None,
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 0.5},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "regions" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_asset_is_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.put(
            "/api/v1/fund-profiles/99999",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 1.0},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


class TestRefresh:
    @pytest.mark.asyncio
    async def test_builds_a_profile_from_the_benchmark(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        composition = {"sectors": {"Technology": 1.0}, "asset_class": {"equity": 1.0}}
        with patch(
            "portf_manager.services.fund_profiles.market.get_fund_composition",
            return_value=composition,
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/refresh",
                headers=auth_headers,
                json={"benchmark_key": "msci_world"},
            )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["source"] == "benchmark"
        assert body["regions"]["north_america"] == 0.72

    @pytest.mark.asyncio
    async def test_refuses_to_overwrite_a_manual_profile(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 1.0},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        resp = await async_test_client.post(
            f"/api/v1/fund-profiles/{asset_id}/refresh",
            headers=auth_headers,
            json={"benchmark_key": "msci_world"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    @pytest.mark.asyncio
    async def test_force_overwrites_a_manual_profile(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        await async_test_client.put(
            f"/api/v1/fund-profiles/{asset_id}",
            headers=auth_headers,
            json={
                "benchmark_key": "msci_world",
                "asset_class": {"equity": 1.0},
                "regions": {"north_america": 1.0},
                "sectors": {},
                "currency_hedged": False,
                "as_of": "2026-09-01",
            },
        )
        with patch(
            "portf_manager.services.fund_profiles.market.get_fund_composition",
            return_value={"sectors": {}, "asset_class": {}},
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/refresh",
                headers=auth_headers,
                json={"benchmark_key": "msci_world", "force": True},
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["source"] == "benchmark"


class TestSuggest:
    @pytest.mark.asyncio
    async def test_returns_a_draft_without_saving(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        llm = MagicMock()
        llm.generate.return_value = (
            '{"regions": {"north_america": 0.6, "emerging": 0.4},'
            ' "asset_class": {"equity": 1.0},'
            ' "sectors": {"Technology": 1.0},'
            ' "benchmark_key": null, "notes": "drafted"}'
        )
        with patch(
            "portf_server.routers.fund_profiles.get_llm_client", return_value=llm
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/suggest", headers=auth_headers
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["suggestion"]["regions"]["emerging"] == 0.4
        # Nothing was written.
        get = await async_test_client.get(
            f"/api/v1/fund-profiles/{asset_id}", headers=auth_headers
        )
        assert get.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_an_llm_failure_is_502(
        self, async_test_client: AsyncClient, auth_headers
    ):
        asset_id = await _make_fund(async_test_client, auth_headers)
        with patch(
            "portf_server.routers.fund_profiles.get_llm_client",
            side_effect=RuntimeError("no key"),
        ):
            resp = await async_test_client.post(
                f"/api/v1/fund-profiles/{asset_id}/suggest", headers=auth_headers
            )
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
