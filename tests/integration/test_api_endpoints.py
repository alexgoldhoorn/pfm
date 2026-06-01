"""
Integration tests for API endpoints
"""

import pytest
from httpx import AsyncClient
from fastapi import status


@pytest.mark.asyncio
@pytest.mark.api
async def test_assets_endpoint(async_test_client: AsyncClient, auth_headers):
    """Test assets endpoint for retrieval."""
    response = await async_test_client.get("/api/v1/assets", headers=auth_headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)  # Assets endpoint returns a list


@pytest.mark.asyncio
@pytest.mark.api
async def test_transactions_endpoint(async_test_client: AsyncClient, auth_headers):
    """Test transactions endpoint to ensure transactions retrieval."""
    response = await async_test_client.get("/api/v1/transactions", headers=auth_headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)  # Transactions endpoint returns a list


@pytest.mark.asyncio
@pytest.mark.api
async def test_sectors_endpoint(async_test_client: AsyncClient, auth_headers):
    """Test sectors endpoint.
    This endpoint retrieves available sectors for classification."""
    response = await async_test_client.get("/api/v1/sectors", headers=auth_headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)  # Sectors endpoint returns a list of sector names
    # Should contain some standard sectors
    expected_sectors = [
        "Information Technology",
        "Financials",
        "Consumer Discretionary",
    ]
    for sector in expected_sectors:
        assert sector in data


@pytest.mark.asyncio
@pytest.mark.api
async def test_portfolios_endpoint(async_test_client: AsyncClient, auth_headers):
    """Test portfolios endpoint.
    Retrieves available portfolios including performance metrics."""
    response = await async_test_client.get("/api/v1/portfolios", headers=auth_headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "message" in data and "under construction" in data["message"]


@pytest.mark.asyncio
@pytest.mark.api
async def test_health_endpoint(async_test_client: AsyncClient):
    """Test health endpoint without authentication."""
    response = await async_test_client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
