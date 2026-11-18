"""The stored dashboard layout: GET/PUT /api/v1/system/dashboard-layout."""

import pytest
from fastapi import status
from httpx import AsyncClient

URL = "/api/v1/system/dashboard-layout"


@pytest.mark.asyncio
async def test_layout_defaults_to_empty(async_test_client: AsyncClient, auth_headers):
    resp = await async_test_client.get(URL, headers=auth_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"order": [], "hidden": []}


@pytest.mark.asyncio
async def test_layout_round_trip_drops_repeats(
    async_test_client: AsyncClient, auth_headers
):
    body = {"order": ["risk", "activity", "risk"], "hidden": ["goals"]}
    put = await async_test_client.put(URL, json=body, headers=auth_headers)
    assert put.status_code == status.HTTP_200_OK
    expected = {"order": ["risk", "activity"], "hidden": ["goals"]}
    assert put.json() == expected

    got = await async_test_client.get(URL, headers=auth_headers)
    assert got.json() == expected

    reset = await async_test_client.put(
        URL, json={"order": [], "hidden": []}, headers=auth_headers
    )
    assert reset.status_code == status.HTTP_200_OK
    got = await async_test_client.get(URL, headers=auth_headers)
    assert got.json() == {"order": [], "hidden": []}


@pytest.mark.asyncio
async def test_layout_rejects_a_malformed_id(
    async_test_client: AsyncClient, auth_headers
):
    resp = await async_test_client.put(
        URL, json={"order": ["<script>"], "hidden": []}, headers=auth_headers
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_unreadable_stored_layout_reads_as_default(
    async_test_client: AsyncClient, auth_headers, test_database
):
    test_database.set_setting("dashboard_layout", "not json")
    resp = await async_test_client.get(URL, headers=auth_headers)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"order": [], "hidden": []}


@pytest.mark.asyncio
async def test_layout_requires_auth(async_test_client: AsyncClient):
    resp = await async_test_client.get(URL)
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
