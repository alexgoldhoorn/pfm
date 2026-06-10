"""
Targeted unit tests to improve coverage for low-coverage FastAPI routers:
- transactions: update + delete + symbol filter
- portfolios: holdings + update + delete
- tax: report endpoint (CSV)
- assets: price add endpoint
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_server.dependencies import get_current_user_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tx_row(
    id=1,
    asset_id=1,
    portfolio_id=None,
    portfolio_name=None,
    transaction_type="buy",
    quantity=10.0,
    price=100.0,
    total_amount=1000.0,
    fees=0.0,
    transaction_date=date(2024, 1, 15),
    description=None,
    symbol="AAPL",
    name="Apple Inc.",
    currency="USD",
):
    return {
        "id": id,
        "asset_id": asset_id,
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio_name,
        "transaction_type": transaction_type,
        "quantity": quantity,
        "price": price,
        "total_amount": total_amount,
        "fees": fees,
        "transaction_date": transaction_date,
        "description": description,
        "symbol": symbol,
        "name": name,
        "currency": currency,
    }


# ---------------------------------------------------------------------------
# Transactions — symbol filter  (lines 59-75)
# ---------------------------------------------------------------------------


class TestTransactionSymbolFilter:
    @pytest.mark.asyncio
    async def test_filter_by_known_symbol(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """GET /transactions/?symbol=AAPL returns only AAPL transactions."""
        # Create asset + transaction via real API so they exist in test DB.
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        assert asset_resp.status_code == status.HTTP_201_CREATED
        asset_id = asset_resp.json()["id"]

        tx_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 5.0,
                "price": 150.0,
                "total_amount": 750.0,
                "transaction_date": "2024-03-01",
            },
            headers=auth_headers,
        )
        assert tx_resp.status_code == status.HTTP_200_OK

        resp = await async_test_client.get(
            "/api/v1/transactions/",
            params={"symbol": sample_asset_data["symbol"]},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        rows = resp.json()
        assert len(rows) >= 1
        assert all(r["symbol"] == sample_asset_data["symbol"] for r in rows)

    @pytest.mark.asyncio
    async def test_filter_by_unknown_symbol_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """GET /transactions/?symbol=UNKNOWN returns 404 when symbol doesn't exist."""
        resp = await async_test_client.get(
            "/api/v1/transactions/",
            params={"symbol": "ZZZUNKNOWN"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Transactions — update  (lines 196-268)
# ---------------------------------------------------------------------------


class TestTransactionUpdate:
    @pytest.mark.asyncio
    async def test_update_existing_transaction(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """PUT /transactions/{id} updates quantity/price and recalculates total."""
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]

        tx_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 10.0,
                "price": 100.0,
                "total_amount": 1000.0,
                "transaction_date": "2024-04-01",
            },
            headers=auth_headers,
        )
        tx_id = tx_resp.json()["id"]

        update_resp = await async_test_client.put(
            f"/api/v1/transactions/{tx_id}",
            json={"quantity": 20.0, "price": 110.0},
            headers=auth_headers,
        )
        assert update_resp.status_code == status.HTTP_200_OK
        data = update_resp.json()
        assert data["id"] == tx_id
        assert "quantity" in data["updated_fields"]
        assert "price" in data["updated_fields"]
        assert "total_amount" in data["updated_fields"]

    @pytest.mark.asyncio
    async def test_update_nonexistent_transaction_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """PUT /transactions/{id} returns 404 when transaction doesn't exist."""
        resp = await async_test_client.put(
            "/api/v1/transactions/999999",
            json={"quantity": 5.0},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_update_with_no_fields_returns_400(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """PUT /transactions/{id} with empty body returns 400."""
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]

        tx_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 1.0,
                "price": 50.0,
                "total_amount": 50.0,
                "transaction_date": "2024-04-02",
            },
            headers=auth_headers,
        )
        tx_id = tx_resp.json()["id"]

        resp = await async_test_client.put(
            f"/api/v1/transactions/{tx_id}",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Transactions — delete  (lines 271-301)
# ---------------------------------------------------------------------------


class TestTransactionDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_transaction(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """DELETE /transactions/{id} removes the transaction."""
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]

        tx_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 3.0,
                "price": 200.0,
                "total_amount": 600.0,
                "transaction_date": "2024-05-01",
            },
            headers=auth_headers,
        )
        tx_id = tx_resp.json()["id"]

        del_resp = await async_test_client.delete(
            f"/api/v1/transactions/{tx_id}", headers=auth_headers
        )
        assert del_resp.status_code == status.HTTP_200_OK
        data = del_resp.json()
        assert data["id"] == tx_id
        assert "deleted" in data["message"].lower()

        # Confirm it's gone.
        get_resp = await async_test_client.get(
            f"/api/v1/transactions/{tx_id}", headers=auth_headers
        )
        assert get_resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_delete_nonexistent_transaction_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """DELETE /transactions/{id} returns 404 when transaction doesn't exist."""
        resp = await async_test_client.delete(
            "/api/v1/transactions/999999", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Portfolios — holdings  (lines 80-187)
# ---------------------------------------------------------------------------


class TestPortfolioHoldings:
    @pytest.mark.asyncio
    async def test_holdings_empty_when_no_transactions(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """GET /portfolios/holdings returns holdings + summary keys when empty."""
        resp = await async_test_client.get(
            "/api/v1/portfolios/holdings", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "holdings" in data
        assert "summary" in data
        summary = data["summary"]
        assert "total_value" in summary
        assert "total_cost" in summary
        assert "total_pnl" in summary
        assert "total_pnl_pct" in summary

    @pytest.mark.asyncio
    async def test_holdings_with_buy_transaction(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """Holdings endpoint reflects a buy transaction with a known price."""
        # Create asset
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]

        # Record a buy
        await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 5.0,
                "price": 150.0,
                "total_amount": 750.0,
                "transaction_date": "2024-06-01",
            },
            headers=auth_headers,
        )

        # Add a price so holdings can compute market value
        await async_test_client.post(
            f"/api/v1/assets/{asset_id}/prices",
            json={"price": "155.00", "price_date": "2024-06-02"},
            headers=auth_headers,
        )

        resp = await async_test_client.get(
            "/api/v1/portfolios/holdings", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        symbols_in_holdings = [h["symbol"] for h in data["holdings"]]
        assert sample_asset_data["symbol"] in symbols_in_holdings


# ---------------------------------------------------------------------------
# Portfolios — update / delete  (lines 217-246)
# ---------------------------------------------------------------------------


class TestPortfolioUpdateDelete:
    @pytest.mark.asyncio
    async def test_update_portfolio(
        self, async_test_client: AsyncClient, auth_headers, sample_portfolio_data
    ):
        """PUT /portfolios/{id} updates the portfolio name."""
        create_resp = await async_test_client.post(
            "/api/v1/portfolios", json=sample_portfolio_data, headers=auth_headers
        )
        port_id = create_resp.json()["id"]

        update_resp = await async_test_client.put(
            f"/api/v1/portfolios/{port_id}",
            json={"name": "Renamed Portfolio"},
            headers=auth_headers,
        )
        assert update_resp.status_code == status.HTTP_200_OK
        assert update_resp.json()["name"] == "Renamed Portfolio"

    @pytest.mark.asyncio
    async def test_update_portfolio_no_fields_returns_400(
        self, async_test_client: AsyncClient, auth_headers, sample_portfolio_data
    ):
        """PUT /portfolios/{id} with no fields returns 400."""
        create_resp = await async_test_client.post(
            "/api/v1/portfolios", json=sample_portfolio_data, headers=auth_headers
        )
        port_id = create_resp.json()["id"]

        resp = await async_test_client.put(
            f"/api/v1/portfolios/{port_id}",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_update_nonexistent_portfolio_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """PUT /portfolios/999999 returns 404."""
        resp = await async_test_client.put(
            "/api/v1/portfolios/999999",
            json={"name": "Ghost"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_delete_portfolio(
        self, async_test_client: AsyncClient, auth_headers, sample_portfolio_data
    ):
        """DELETE /portfolios/{id} removes the portfolio."""
        create_resp = await async_test_client.post(
            "/api/v1/portfolios", json=sample_portfolio_data, headers=auth_headers
        )
        port_id = create_resp.json()["id"]

        del_resp = await async_test_client.delete(
            f"/api/v1/portfolios/{port_id}", headers=auth_headers
        )
        assert del_resp.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.asyncio
    async def test_delete_nonexistent_portfolio_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """DELETE /portfolios/999999 returns 404."""
        resp = await async_test_client.delete(
            "/api/v1/portfolios/999999", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Tax — report endpoint  (lines 77-349)
# ---------------------------------------------------------------------------


class TestTaxReport:
    """
    The /tax/report endpoint uses get_current_user_id (JWT bearer).
    We override that dependency to return a fixed user id, and mock the
    TaxCalculator so we don't need real sell transactions in the DB.
    """

    @pytest.fixture(autouse=True)
    def _override_user_id(self, test_app):
        """Inject a dummy user id so JWT dependency doesn't fire."""
        test_app.dependency_overrides[get_current_user_id] = lambda: 1
        yield
        # Remove override after each test in this class.
        test_app.dependency_overrides.pop(get_current_user_id, None)

    @pytest.mark.asyncio
    async def test_tax_report_invalid_date_range_returns_400(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """start_date > end_date → 400."""
        resp = await async_test_client.get(
            "/api/v1/tax/report",
            params={"start_date": "2025-12-31", "end_date": "2025-01-01"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "start date" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_tax_report_invalid_format_returns_400(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """format=xml → 400."""
        resp = await async_test_client.get(
            "/api/v1/tax/report",
            params={
                "start_date": "2025-01-01",
                "end_date": "2025-12-31",
                "format": "xml",
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "format" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_tax_report_no_transactions_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """When calculator returns empty dict → 404."""
        with patch("portf_server.routers.tax.TaxCalculator") as MockCalc:
            MockCalc.return_value.calculate_tax_report.return_value = {}
            resp = await async_test_client.get(
                "/api/v1/tax/report",
                params={"start_date": "2025-01-01", "end_date": "2025-12-31"},
                headers=auth_headers,
            )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_tax_report_csv_success(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Valid request with mocked data returns CSV file."""
        from portf_manager.tax_calculator import TaxTransaction

        fake_tx = TaxTransaction(
            symbol="AAPL",
            asset_name="Apple Inc.",
            sell_date=date(2025, 6, 1),
            sell_quantity=Decimal("5"),
            sell_price=Decimal("180"),
            sell_amount=Decimal("900"),
            purchase_date=date(2024, 1, 1),
            purchase_price=Decimal("150"),
            purchase_amount=Decimal("750"),
            gain_loss=Decimal("150"),
            holding_period_days=517,
            is_long_term=True,
            sell_transaction_id=2,
            buy_transaction_id=1,
            portfolio_name="Test",
        )

        fake_report = {"AAPL": [fake_tx]}
        fake_summary = {
            "total_gain_loss": Decimal("150"),
            "total_long_term_gain_loss": Decimal("150"),
            "total_short_term_gain_loss": Decimal("0"),
        }

        with patch("portf_server.routers.tax.TaxCalculator") as MockCalc:
            mock_inst = MockCalc.return_value
            mock_inst.calculate_tax_report.return_value = fake_report
            mock_inst.generate_tax_summary.return_value = fake_summary

            resp = await async_test_client.get(
                "/api/v1/tax/report",
                params={
                    "start_date": "2025-01-01",
                    "end_date": "2025-12-31",
                    "format": "csv",
                },
                headers=auth_headers,
            )

        assert resp.status_code == status.HTTP_200_OK
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
        assert len(resp.content) > 0

    @pytest.mark.asyncio
    async def test_tax_report_with_symbol_filter(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """symbols query param is parsed and forwarded to the calculator."""
        from portf_manager.tax_calculator import TaxTransaction

        fake_tx = TaxTransaction(
            symbol="MSFT",
            asset_name="Microsoft",
            sell_date=date(2025, 3, 1),
            sell_quantity=Decimal("2"),
            sell_price=Decimal("300"),
            sell_amount=Decimal("600"),
            purchase_date=date(2024, 1, 1),
            purchase_price=Decimal("250"),
            purchase_amount=Decimal("500"),
            gain_loss=Decimal("100"),
            holding_period_days=425,
            is_long_term=True,
            sell_transaction_id=4,
            buy_transaction_id=3,
            portfolio_name="Test",
        )

        fake_report = {"MSFT": [fake_tx]}
        fake_summary = {
            "total_gain_loss": Decimal("100"),
            "total_long_term_gain_loss": Decimal("100"),
            "total_short_term_gain_loss": Decimal("0"),
        }

        with patch("portf_server.routers.tax.TaxCalculator") as MockCalc:
            mock_inst = MockCalc.return_value
            mock_inst.calculate_tax_report.return_value = fake_report
            mock_inst.generate_tax_summary.return_value = fake_summary

            resp = await async_test_client.get(
                "/api/v1/tax/report",
                params={
                    "start_date": "2025-01-01",
                    "end_date": "2025-12-31",
                    "symbols": "MSFT",
                    "format": "csv",
                },
                headers=auth_headers,
            )

        assert resp.status_code == status.HTTP_200_OK
        # Verify calculator was called with the parsed symbol list.
        call_kwargs = mock_inst.calculate_tax_report.call_args
        assert call_kwargs.kwargs.get("symbols") == ["MSFT"]

    @pytest.mark.asyncio
    async def test_tax_info_endpoint(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """GET /api/v1/tax/ returns info dict."""
        resp = await async_test_client.get("/api/v1/tax/", headers=auth_headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "methodology" in data
        assert data["methodology"] == "FIFO (First In First Out)"


# ---------------------------------------------------------------------------
# Assets — price add endpoint  (lines 325-388)
# ---------------------------------------------------------------------------


class TestAssetPriceAdd:
    @pytest.mark.asyncio
    async def test_add_price_to_existing_asset(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """POST /assets/{id}/prices creates a price record."""
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]

        price_resp = await async_test_client.post(
            f"/api/v1/assets/{asset_id}/prices",
            json={"price": "175.50", "price_date": "2024-07-01"},
            headers=auth_headers,
        )
        assert price_resp.status_code == status.HTTP_201_CREATED
        data = price_resp.json()
        assert data["asset_id"] == asset_id
        assert float(data["price"]) == pytest.approx(175.50)
        assert data["price_date"] == "2024-07-01"
        assert data["price_type"] == "close"

    @pytest.mark.asyncio
    async def test_add_price_to_nonexistent_asset_returns_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """POST /assets/999999/prices returns 404 when asset doesn't exist."""
        resp = await async_test_client.post(
            "/api/v1/assets/999999/prices",
            json={"price": "100.00", "price_date": "2024-07-01"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_add_price_with_volume_and_source(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """POST /assets/{id}/prices stores optional volume and source fields."""
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]

        price_resp = await async_test_client.post(
            f"/api/v1/assets/{asset_id}/prices",
            json={
                "price": "200.00",
                "price_date": "2024-08-01",
                "price_type": "close",
                "volume": 50000,
                "source": "yfinance",
            },
            headers=auth_headers,
        )
        assert price_resp.status_code == status.HTTP_201_CREATED
        data = price_resp.json()
        assert data["volume"] == 50000
        assert data["source"] == "yfinance"
