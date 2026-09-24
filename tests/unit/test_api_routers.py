"""
Unit Tests for FastAPI Routers

This module contains comprehensive unit tests for all FastAPI router modules
including assets, transactions, portfolios, sectors, auth and llm.
"""

import json

import pytest
from httpx import AsyncClient
from fastapi import status


class TestAssetRouter:
    """Test cases for assets router."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_create_asset(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """Test creating a new asset."""
        response = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["symbol"] == sample_asset_data["symbol"]
        assert data["name"] == sample_asset_data["name"]

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_get_asset_by_id(self, async_test_client: AsyncClient, auth_headers):
        """Test retrieving asset by ID."""
        # Create asset first
        asset_data = {
            "symbol": "MSFT",
            "name": "Microsoft Corporation",
            "asset_type": "stock",
            "currency": "USD",
        }
        create_response = await async_test_client.post(
            "/api/v1/assets", json=asset_data, headers=auth_headers
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        asset_id = create_response.json()["id"]

        # Retrieve asset
        response = await async_test_client.get(
            f"/api/v1/assets/{asset_id}", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == asset_id
        assert data["symbol"] == "MSFT"

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_update_asset(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """Test updating an existing asset."""
        # Create asset first
        create_response = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = create_response.json()["id"]

        # Update asset
        update_data = {"description": "Updated description"}
        response = await async_test_client.put(
            f"/api/v1/assets/{asset_id}", json=update_data, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["description"] == "Updated description"

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_update_asset_toggles_auto_price(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """auto_price can be turned off and back on via the update endpoint.

        Adding a manual price auto-disables auto_price with no supported way
        back on before this field existed.
        """
        create_response = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = create_response.json()["id"]

        off = await async_test_client.put(
            f"/api/v1/assets/{asset_id}",
            json={"auto_price": False},
            headers=auth_headers,
        )
        assert off.status_code == status.HTTP_200_OK
        assert off.json()["auto_price"] is False

        on = await async_test_client.put(
            f"/api/v1/assets/{asset_id}",
            json={"auto_price": True, "ticker": "PRAB.DE"},
            headers=auth_headers,
        )
        assert on.status_code == status.HTTP_200_OK
        assert on.json()["auto_price"] is True
        assert on.json()["ticker"] == "PRAB.DE"

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_delete_asset(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """Test deleting an asset."""
        # Create asset first
        create_response = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = create_response.json()["id"]

        # Delete asset
        response = await async_test_client.delete(
            f"/api/v1/assets/{asset_id}", headers=auth_headers
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Soft delete: still readable by id, but inactive and out of the listing
        get_response = await async_test_client.get(
            f"/api/v1/assets/{asset_id}", headers=auth_headers
        )
        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.json()["is_active"] is False
        listing = await async_test_client.get("/api/v1/assets", headers=auth_headers)
        assert asset_id not in {a["id"] for a in listing.json()}

        # Deleting a missing asset is a 404, not a silent success
        missing = await async_test_client.delete(
            "/api/v1/assets/999999", headers=auth_headers
        )
        assert missing.status_code == status.HTTP_404_NOT_FOUND


class TestTransactionRouter:
    """Test cases for transactions router."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_create_transaction(
        self,
        async_test_client: AsyncClient,
        auth_headers,
        sample_transaction_data,
        sample_asset_data,
    ):
        """Test creating a new transaction."""
        # Create asset first
        asset_response = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_response.json()["id"]

        # Add asset_id to transaction data
        transaction_data = {**sample_transaction_data, "asset_id": asset_id}

        response = await async_test_client.post(
            "/api/v1/transactions", json=transaction_data, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["asset_id"] == asset_id
        assert data["quantity"] == sample_transaction_data["quantity"]

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_get_transactions(self, async_test_client: AsyncClient, auth_headers):
        """Test retrieving transactions."""
        response = await async_test_client.get(
            "/api/v1/transactions", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_get_transaction_by_id(
        self,
        async_test_client: AsyncClient,
        auth_headers,
        sample_transaction_data,
        sample_asset_data,
    ):
        """Test retrieving transaction by ID."""
        # Create asset and transaction first
        asset_response = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_response.json()["id"]

        transaction_data = {**sample_transaction_data, "asset_id": asset_id}
        create_response = await async_test_client.post(
            "/api/v1/transactions", json=transaction_data, headers=auth_headers
        )
        transaction_id = create_response.json()["id"]

        response = await async_test_client.get(
            f"/api/v1/transactions/{transaction_id}", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == transaction_id
        assert data["asset_id"] == asset_id

        missing = await async_test_client.get(
            "/api/v1/transactions/999999", headers=auth_headers
        )
        assert missing.status_code == status.HTTP_404_NOT_FOUND


class TestPortfolioRouter:
    """Test cases for portfolios router."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_create_portfolio(
        self, async_test_client: AsyncClient, auth_headers, sample_portfolio_data
    ):
        """Test creating a new portfolio."""
        response = await async_test_client.post(
            "/api/v1/portfolios", json=sample_portfolio_data, headers=auth_headers
        )
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
        data = response.json()
        assert data["name"] == sample_portfolio_data["name"]
        assert data["base_currency"] == sample_portfolio_data["base_currency"]

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_create_bank_account_portfolio(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test creating a bank-type portfolio via account_type."""
        response = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "Example Bank Checking", "account_type": "bank"},
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["account_type"] == "bank"

        listed = await async_test_client.get("/api/v1/portfolios", headers=auth_headers)
        created = next(p for p in listed.json() if p["name"] == "Example Bank Checking")
        assert created["account_type"] == "bank"

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_create_portfolio_defaults_to_brokerage(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test that account_type defaults to 'brokerage' when omitted."""
        response = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "Example Broker"},
            headers=auth_headers,
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["account_type"] == "brokerage"

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_get_portfolios(self, async_test_client: AsyncClient, auth_headers):
        """Test retrieving portfolios."""
        response = await async_test_client.get(
            "/api/v1/portfolios", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)


class TestSectorRouter:
    """Test cases for sectors router."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_get_sectors(self, async_test_client: AsyncClient, auth_headers):
        """Test retrieving available sectors."""
        response = await async_test_client.get("/api/v1/sectors", headers=auth_headers)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # The sectors endpoint returns a list directly
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_get_sector_allocation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test retrieving sector allocation for portfolio."""
        # Use a test symbol since the endpoint requires a symbol parameter
        response = await async_test_client.get(
            "/api/v1/sectors/ALLOCATION", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "sector" in data or "symbol" in data


class TestAuthRouter:
    """Test cases for authentication router."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_register_user(self, async_test_client: AsyncClient, test_user_data):
        """Test user registration."""
        response = await async_test_client.post(
            "/api/v1/auth/register", json=test_user_data
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["username"] == test_user_data["username"]
        assert data["email"] == test_user_data["email"]
        assert "id" in data

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_user(
        self, async_test_client: AsyncClient, test_user, test_user_data
    ):
        """Test user login."""
        login_data = {
            "username": test_user_data["username"],
            "password": test_user_data["password"],
        }
        response = await async_test_client.post("/api/v1/auth/login", json=login_data)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, async_test_client: AsyncClient):
        """Test login with invalid credentials."""
        login_data = {"username": "nonexistent", "password": "wrongpassword"}
        response = await async_test_client.post("/api/v1/auth/login", json=login_data)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class _FakeLLM:
    """Stands in for a provider: returns a canned reply, records the prompt."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class TestLLMRouter:
    """LLM endpoints, with the provider replaced by a fake (no API calls)."""

    @pytest.mark.asyncio
    async def test_extract_transactions_drops_invalid_rows(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        text = "Bought 10 shares of AAPL at $150.00 on 2024-01-15"
        valid = {
            "tx_type": "buy",
            "symbol": "AAPL",
            "asset_name": "Apple Inc.",
            "quantity": 10,
            "price": 150.0,
            "date": "2024-01-15",
            "currency": "USD",
            "raw_text": text,
        }
        invalid = {**valid, "tx_type": "transfer"}
        fake = _FakeLLM(json.dumps([valid, invalid]))
        monkeypatch.setattr("portf_server.routers.llm.get_llm_client", lambda: fake)

        response = await async_test_client.post(
            "/api/v1/llm/extract-transactions",
            json={"text": text},
            headers=auth_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        (tx,) = data["transactions"]
        assert (tx["symbol"], tx["quantity"], tx["price"]) == ("AAPL", 10, 150.0)
        assert text in fake.prompts[0]

    @pytest.mark.asyncio
    async def test_extract_transactions_unparseable_reply_is_empty(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        fake = _FakeLLM("sorry, I can't help with that")
        monkeypatch.setattr("portf_server.routers.llm.get_llm_client", lambda: fake)

        response = await async_test_client.post(
            "/api/v1/llm/extract-transactions",
            json={"text": "nothing here"},
            headers=auth_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"transactions": [], "count": 0}

    @pytest.mark.asyncio
    async def test_chat_without_provider_is_503(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        def no_provider():
            raise RuntimeError("No LLM provider available")

        monkeypatch.setattr("portf_server.routers.llm.get_llm_client", no_provider)
        monkeypatch.setattr("portf_server.routers.llm._enhanced_chat_engine", None)

        response = await async_test_client.post(
            "/api/v1/llm/chat", json={"message": "hi"}, headers=auth_headers
        )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


class TestAnalyticsRouter:
    """Test cases for analytics router."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_tax_report_shape_with_realised_gain(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Tax-report realised_lots are non-zero after a buy then sell."""
        portfolio_resp = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "Tax Test Broker", "base_currency": "EUR"},
            headers=auth_headers,
        )
        assert portfolio_resp.status_code in (200, 201)
        portfolio_id = portfolio_resp.json()["id"]

        asset_resp = await async_test_client.post(
            "/api/v1/assets",
            json={
                "symbol": "TAXTEST",
                "name": "Tax Test Corp",
                "asset_type": "stock",
                "currency": "EUR",
            },
            headers=auth_headers,
        )
        assert asset_resp.status_code == 201
        asset_id = asset_resp.json()["id"]

        # tax_report filters by user_id=1; test DB is fresh so the first user is id=1
        user_id = 1

        # Buy 10 shares @ 100 EUR on 2024-01-10
        buy_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 10,
                "price": 100.0,
                "total_amount": 1000.0,
                "transaction_date": "2024-01-10",
                "portfolio_id": portfolio_id,
                "currency": "EUR",
                "user_id": user_id,
            },
            headers=auth_headers,
        )
        assert buy_resp.status_code == 200

        # Sell 5 shares @ 150 EUR on 2024-06-01 → FIFO gain = 5 * (150-100) = 250
        sell_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "sell",
                "quantity": 5,
                "price": 150.0,
                "total_amount": 750.0,
                "transaction_date": "2024-06-01",
                "portfolio_id": portfolio_id,
                "currency": "EUR",
                "user_id": user_id,
            },
            headers=auth_headers,
        )
        assert sell_resp.status_code == 200

        response = await async_test_client.get(
            "/api/v1/analytics/tax-report?year=2024", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        assert "realised_lots" in data
        assert "realised_gain_total" in data
        assert data["lot_count"] >= 1

        lot = next(
            (lo for lo in data["realised_lots"] if lo["symbol"] == "TAXTEST"), None
        )
        assert lot is not None, "TAXTEST lot missing from realised_lots"
        # EUR asset → fx=1.0 so proceeds/cost/gain equal their _eur counterparts
        assert lot["quantity"] == pytest.approx(5.0)
        assert lot["proceeds_eur"] == pytest.approx(750.0, rel=0.01)
        assert lot["cost_basis_eur"] == pytest.approx(500.0, rel=0.01)
        assert lot["gain_loss_eur"] == pytest.approx(250.0, rel=0.01)

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_tax_report_uses_transaction_date_fx(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        """USD lot: proceeds at sell-date FX, cost basis at purchase-date FX."""
        from portf_server.routers import analytics as analytics_router

        rates = {"2024-01-10": 0.90, "2024-06-01": 0.80}

        def fake_fx_on(db, cur, on_date):
            if cur.upper() == "EUR":
                return 1.0
            return rates.get(str(on_date)[:10], 1.0)

        monkeypatch.setattr(analytics_router, "_fx_on", fake_fx_on)

        p = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "FX Lot Broker", "base_currency": "EUR"},
            headers=auth_headers,
        )
        portfolio_id = p.json()["id"]
        a = await async_test_client.post(
            "/api/v1/assets",
            json={
                "symbol": "FXLOT",
                "name": "Example FX Corp",
                "asset_type": "stock",
                "currency": "USD",
            },
            headers=auth_headers,
        )
        asset_id = a.json()["id"]
        # Buy 10 @ $100 on 2024-01-10, sell 5 @ $150 on 2024-06-01.
        for tx in [
            {
                "transaction_type": "buy",
                "quantity": 10,
                "price": 100.0,
                "total_amount": 1000.0,
                "transaction_date": "2024-01-10",
            },
            {
                "transaction_type": "sell",
                "quantity": 5,
                "price": 150.0,
                "total_amount": 750.0,
                "transaction_date": "2024-06-01",
            },
        ]:
            r = await async_test_client.post(
                "/api/v1/transactions",
                json={
                    **tx,
                    "asset_id": asset_id,
                    "portfolio_id": portfolio_id,
                    "currency": "USD",
                    "user_id": 1,
                },
                headers=auth_headers,
            )
            assert r.status_code == 200

        resp = await async_test_client.get(
            "/api/v1/analytics/tax-report?year=2024", headers=auth_headers
        )
        assert resp.status_code == 200
        lot = next(lo for lo in resp.json()["realised_lots"] if lo["symbol"] == "FXLOT")
        # $750 proceeds at sell-date 0.80 → €600.
        assert lot["proceeds_eur"] == pytest.approx(600.0, rel=0.01)
        # $500 cost at purchase-date 0.90 → €450.
        assert lot["cost_basis_eur"] == pytest.approx(450.0, rel=0.01)
        # Gain includes the FX loss: 600 - 450 = €150 (NOT $250 × one rate).
        assert lot["gain_loss_eur"] == pytest.approx(150.0, rel=0.01)
        assert lot["purchase_date"] == "2024-01-10"


# Performance and error handling tests
class TestErrorHandling:
    """Test cases for API error handling."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_unauthorized_access(self, async_test_client: AsyncClient):
        """Protected data endpoints reject requests with no API key."""
        response = await async_test_client.get("/api/v1/assets")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_invalid_api_key(self, async_test_client: AsyncClient):
        """Protected data endpoints reject requests with an invalid API key."""
        headers = {"X-API-Key": "invalid-key"}
        response = await async_test_client.get("/api/v1/assets", headers=headers)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_not_found_endpoint(self, async_test_client: AsyncClient):
        """Test access to non-existent endpoint."""
        response = await async_test_client.get("/api/v1/nonexistent")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_invalid_json_payload(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test endpoints with invalid JSON payload."""
        response = await async_test_client.post(
            "/api/v1/assets", data="invalid json", headers=auth_headers
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_validation_errors(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test validation errors for invalid data."""
        invalid_asset_data = {
            "symbol": "",  # Empty symbol should fail validation
            "name": "Test Asset",
            "asset_type": "invalid_type",  # Invalid asset type
        }
        response = await async_test_client.post(
            "/api/v1/assets", json=invalid_asset_data, headers=auth_headers
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestPerformance:
    """Test cases for API performance."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_bulk_asset_creation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test creating multiple assets for performance."""
        assets_data = [
            {
                "symbol": f"TEST{i:03d}",
                "name": f"Test Company {i}",
                "asset_type": "stock",
                "currency": "USD",
            }
            for i in range(10)  # Create 10 assets
        ]

        responses = []
        for asset_data in assets_data:
            response = await async_test_client.post(
                "/api/v1/assets", json=asset_data, headers=auth_headers
            )
            responses.append(response)

        # All should succeed
        for response in responses:
            assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_concurrent_requests(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Test concurrent API requests."""
        import asyncio

        async def make_request():
            return await async_test_client.get("/api/v1/assets", headers=auth_headers)

        # Make 5 concurrent requests
        tasks = [make_request() for _ in range(5)]
        responses = await asyncio.gather(*tasks)

        # All should succeed
        for response in responses:
            assert response.status_code == status.HTTP_200_OK


class TestPortfolioTransactionsClear:
    """Tests for DELETE /api/v1/portfolios/{id}/transactions."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_clear_transactions_returns_deleted_count(
        self, async_test_client: AsyncClient, auth_headers
    ):
        port_resp = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "ClearTest", "base_currency": "EUR"},
            headers=auth_headers,
        )
        assert port_resp.status_code == 201
        port_id = port_resp.json()["id"]

        asset_resp = await async_test_client.post(
            "/api/v1/assets",
            json={
                "symbol": "CLRT",
                "name": "ClearTest Asset",
                "asset_type": "stock",
                "currency": "EUR",
            },
            headers=auth_headers,
        )
        assert asset_resp.status_code == 201
        asset_id = asset_resp.json()["id"]

        tx_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "portfolio_id": port_id,
                "transaction_type": "buy",
                "quantity": 1.0,
                "price": 10.0,
                "total_amount": 10.0,
                "transaction_date": "2024-01-01",
                "currency": "EUR",
            },
            headers=auth_headers,
        )
        assert tx_resp.status_code in (200, 201)

        resp = await async_test_client.delete(
            f"/api/v1/portfolios/{port_id}/transactions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 1

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_clear_transactions_returns_zero_when_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        port_resp = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "EmptyClear", "base_currency": "EUR"},
            headers=auth_headers,
        )
        assert port_resp.status_code == 201
        port_id = port_resp.json()["id"]

        resp = await async_test_client.delete(
            f"/api/v1/portfolios/{port_id}/transactions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_clear_transactions_404_for_unknown_portfolio(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.delete(
            "/api/v1/portfolios/999999/transactions",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestSystemRestore:
    """Tests for POST /api/v1/system/restore."""

    def _make_valid_db(self, version: int | None = None) -> bytes:
        """Return bytes of a minimal SQLite DB with the given user_version."""
        import os
        import sqlite3
        import tempfile
        from portf_manager.database import DATABASE_VERSION

        if version is None:
            version = DATABASE_VERSION
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.close()
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_valid_db(self, async_test_client: AsyncClient, auth_headers):
        db_bytes = self._make_valid_db()
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("backup.db", db_bytes, "application/octet-stream")},
        )
        assert resp.status_code == 200
        assert resp.json()["restored"] is True

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_version_mismatch(
        self, async_test_client: AsyncClient, auth_headers
    ):
        db_bytes = self._make_valid_db(version=5)
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("old.db", db_bytes, "application/octet-stream")},
        )
        assert resp.status_code == 422
        assert "version" in resp.json()["detail"].lower()

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_invalid_file(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={
                "file": ("bad.db", b"not a sqlite file", "application/octet-stream")
            },
        )
        assert resp.status_code == 422
        assert "valid sqlite" in resp.json()["detail"].lower()

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_gzip_db(self, async_test_client: AsyncClient, auth_headers):
        import gzip

        db_bytes = self._make_valid_db()
        gz_bytes = gzip.compress(db_bytes)
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("backup.db.gz", gz_bytes, "application/gzip")},
        )
        assert resp.status_code == 200
        assert resp.json()["restored"] is True
