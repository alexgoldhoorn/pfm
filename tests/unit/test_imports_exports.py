"""
Unit tests for the import and export routers, and OpenRouterLLMClient.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager.llm_client import (
    OpenRouterLLMClient,
    get_llm_client,
    reset_llm_client,
)
from portf_manager.llm_types import LLMTransaction

# ---------------------------------------------------------------------------
# OpenRouterLLMClient
# ---------------------------------------------------------------------------

MINIMAL_CSV = (
    "2024-01-15;2024-01-17;Apple Inc.;US0378331005;"
    "SUSCRIPCIÓN;14,0;138,21 €;0,00 €;EUR\n"
)

COINBASE_CSV = """\
You can use this transaction report to inform your likely tax obligations. \
For US customers, Sells, Converts, and certain other transactions are taxable events that may need to be reported.

Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price Currency,Spot Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes
2024-01-10T12:00:00Z,Buy,BTC,0.01,USD,42000,420.00,424.99,4.99,Bought Bitcoin
"""


class TestOpenRouterLLMClient:
    def setup_method(self):
        reset_llm_client()

    def test_raises_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="OpenRouter API key required"):
                OpenRouterLLMClient()

    def test_initializes_with_explicit_key(self):
        client = OpenRouterLLMClient(api_key="sk-or-test")
        assert client.api_key == "sk-or-test"

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-env"})
    def test_initializes_from_env(self):
        client = OpenRouterLLMClient()
        assert client.api_key == "sk-or-env"

    @patch.dict(os.environ, {"PORTF_LLM_MODEL": "google/gemma-3-27b"})
    def test_model_override_from_env(self):
        client = OpenRouterLLMClient(api_key="sk-or-x")
        assert client.model_name == "google/gemma-3-27b"

    def test_generate_returns_text(self):
        client = OpenRouterLLMClient(api_key="sk-or-x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello from OpenRouter"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            result = client.generate("Say hello")
        assert result == "Hello from OpenRouter"

    def test_generate_raises_on_empty_content(self):
        client = OpenRouterLLMClient(api_key="sk-or-x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Empty response"):
                client.generate("prompt")

    @patch("portf_manager.llm_client.OllamaLLMClient.is_available", return_value=False)
    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-auto"}, clear=True)
    def test_auto_detect_falls_back_to_openrouter(self, _mock_ollama):
        reset_llm_client()
        client = get_llm_client(force_new=True)
        assert isinstance(client, OpenRouterLLMClient)

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-x"})
    def test_factory_explicit_openrouter(self):
        reset_llm_client()
        client = get_llm_client(provider="openrouter", force_new=True)
        assert isinstance(client, OpenRouterLLMClient)

    def test_factory_unknown_provider_raises(self):
        reset_llm_client()
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm_client(provider="badprovider", force_new=True)


# ---------------------------------------------------------------------------
# Import router — /api/v1/import/upload
# ---------------------------------------------------------------------------


class TestImportUpload:
    @pytest.mark.asyncio
    async def test_upload_unsupported_broker(
        self, async_test_client: AsyncClient, auth_headers
    ):
        response = await async_test_client.post(
            "/api/v1/import/upload",
            headers=auth_headers,
            data={"broker": "unknown_broker"},
            files={"file": ("data.csv", b"col1,col2\nval1,val2", "text/csv")},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Unsupported broker" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_indexacapital(
        self, async_test_client: AsyncClient, auth_headers
    ):
        fake_tx = LLMTransaction(
            tx_type="buy",
            symbol="US0378331005",
            asset_name="Apple Inc.",
            quantity=14.0,
            price=9.87,
            date="2024-01-17",
            currency="EUR",
            raw_text="row",
        )
        mock_result = MagicMock()
        mock_result.importable = [fake_tx]
        mock_result.skipped = []

        with patch(
            "portf_server.routers.imports.parse_indexacapital_csv",
            return_value=mock_result,
        ):
            response = await async_test_client.post(
                "/api/v1/import/upload",
                headers=auth_headers,
                data={"broker": "indexacapital"},
                files={"file": ("ic.csv", MINIMAL_CSV.encode(), "text/csv")},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["broker"] == "indexacapital"
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["symbol"] == "US0378331005"
        assert data["transactions"][0]["asset_type"] == "etf"
        assert data["skipped_count"] == 0

    @pytest.mark.asyncio
    async def test_upload_coinbase(self, async_test_client: AsyncClient, auth_headers):
        fake_tx = LLMTransaction(
            tx_type="buy",
            symbol="BTC",
            asset_name="Bitcoin",
            quantity=0.01,
            price=42000.0,
            date="2024-01-10",
            currency="USD",
            raw_text="row",
        )
        mock_result = MagicMock()
        mock_result.importable = [fake_tx]
        mock_result.skipped = [("Send", "non-trade")]
        mock_result.bookings = []

        with patch(
            "portf_server.routers.imports.parse_coinbase_csv",
            return_value=mock_result,
        ):
            response = await async_test_client.post(
                "/api/v1/import/upload",
                headers=auth_headers,
                data={"broker": "coinbase"},
                files={"file": ("cb.csv", COINBASE_CSV.encode(), "text/csv")},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["transactions"][0]["asset_type"] == "crypto"
        # Coinbase previews must carry the broker so the save step tags them to
        # the Coinbase portfolio (else they land with portfolio_id=NULL and are
        # invisible under the broker filter). Regression guard for that bug.
        assert data["transactions"][0]["broker"] == "Coinbase"
        assert data["skipped_count"] == 1

    @pytest.mark.asyncio
    async def test_upload_coinbase_deposit_booking(
        self, async_test_client: AsyncClient, auth_headers
    ):
        csv = (
            "Transactions\nuser@example.com\n"
            "Timestamp,Transaction Type,Asset,Quantity Transacted,"
            "Price Currency,Price at Transaction,"
            "Total (inclusive of fees and/or spread),Notes\n"
            "2025-08-20 10:00:00 UTC,Deposit,EUR,500,EUR,,500,sepa in\n"
        )
        response = await async_test_client.post(
            "/api/v1/import/upload",
            headers=auth_headers,
            data={"broker": "coinbase"},
            files={"file": ("cb.csv", csv.encode(), "text/csv")},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["bookings"]) == 1
        bk = data["bookings"][0]
        assert bk["action"] == "Deposit"
        assert bk["amount"] == 500.0
        assert bk["currency"] == "EUR"
        assert bk["broker"] == "Coinbase"

    @pytest.mark.asyncio
    async def test_upload_pdt(self, async_test_client: AsyncClient, auth_headers):
        from datetime import date

        from portf_manager.parsers.pdt_xlsx_parser import PDTTransaction

        fake_tx = PDTTransaction(
            broker="TestBroker",
            name="Apple Inc.",
            pdt_type="Stock market",
            search="AAPL",
            exchange="NASDAQ",
            date=date(2024, 3, 1),
            action="Buy",
            amount=10.0,
            price=170.0,
            price_currency="USD",
        )
        mock_result = MagicMock()
        mock_result.transactions = [fake_tx]
        mock_result.dividends = []
        mock_result.bookings = []
        mock_result.skipped = []

        with patch("portf_server.routers.imports.PDTXLSXParser") as mock_parser_cls:
            mock_parser_cls.return_value.parse.return_value = mock_result
            response = await async_test_client.post(
                "/api/v1/import/upload",
                headers=auth_headers,
                data={"broker": "pdt"},
                files={
                    "file": (
                        "portfolio.xlsx",
                        b"fakexlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["transactions"][0]["symbol"] == "AAPL"
        assert data["transactions"][0]["tx_type"] == "buy"

    @pytest.mark.asyncio
    async def test_upload_mintos_deposit_booking(
        self, async_test_client: AsyncClient, auth_headers
    ):
        csv = (
            "Fecha,Identificación de la operación:,Detalles,"
            "Volumen de negocios,Saldo,Divisa,Tipo de pago\n"
            '"2025-11-02 09:00:00",d1,"Ingreso de fondos",100.00,100.00,EUR,"Depósito"\n'
        )
        response = await async_test_client.post(
            "/api/v1/import/upload",
            headers=auth_headers,
            data={"broker": "mintos"},
            files={"file": ("mintos.csv", csv.encode(), "text/csv")},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["bookings"]) == 1
        bk = data["bookings"][0]
        assert bk["action"] == "Deposit"
        assert bk["amount"] == 100.0
        assert bk["broker"] == "Mintos"


# ---------------------------------------------------------------------------
# Import router — /api/v1/import/save
# ---------------------------------------------------------------------------


class TestImportSave:
    @pytest.mark.asyncio
    async def test_save_creates_asset_and_transaction(
        self, async_test_client: AsyncClient, auth_headers
    ):
        payload = {
            "transactions": [
                {
                    "symbol": "NEWTICKER",
                    "name": "New Corp",
                    "asset_type": "stock",
                    "tx_type": "buy",
                    "date": "2024-06-01",
                    "quantity": 5.0,
                    "price": 100.0,
                    "currency": "USD",
                    "fees": 1.0,
                    "notes": "test",
                }
            ]
        }
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["saved"] == 1
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_save_reuses_existing_asset(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        # Create asset first
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )

        payload = {
            "transactions": [
                {
                    "symbol": sample_asset_data["symbol"],
                    "name": sample_asset_data["name"],
                    "asset_type": "stock",
                    "tx_type": "buy",
                    "date": "2024-06-15",
                    "quantity": 2.0,
                    "price": 155.0,
                    "currency": "USD",
                    "fees": 0.0,
                    "notes": "",
                }
            ]
        }
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["saved"] == 1

    @pytest.mark.asyncio
    async def test_save_empty_list(self, async_test_client: AsyncClient, auth_headers):
        response = await async_test_client.post(
            "/api/v1/import/save", json={"transactions": []}, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["saved"] == 0

    @pytest.mark.asyncio
    async def test_save_bookings(self, async_test_client: AsyncClient, auth_headers):
        payload = {
            "transactions": [],
            "bookings": [
                {
                    "broker": "MyInvestor",
                    "date": "2025-06-17",
                    "action": "Deposit",
                    "amount": 250.0,
                    "currency": "EUR",
                }
            ],
        }
        response = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["saved"] == 0
        assert data["saved_bookings"] == 1
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_save_bookings_roundtrip_via_api(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Bookings saved via import/save are retrievable via /api/v1/bookings/."""
        payload = {
            "transactions": [],
            "bookings": [
                {
                    "date": "2025-07-01",
                    "action": "Deposit",
                    "amount": 500.0,
                    "currency": "EUR",
                },
                {
                    "date": "2025-07-10",
                    "action": "Withdrawal",
                    "amount": 100.0,
                    "currency": "EUR",
                },
            ],
        }
        save_resp = await async_test_client.post(
            "/api/v1/import/save", json=payload, headers=auth_headers
        )
        assert save_resp.status_code == status.HTTP_200_OK
        assert save_resp.json()["saved_bookings"] == 2

        list_resp = await async_test_client.get(
            "/api/v1/bookings/", headers=auth_headers
        )
        assert list_resp.status_code == status.HTTP_200_OK
        bookings = list_resp.json()
        assert len(bookings) >= 2
        actions = [b["action"] for b in bookings]
        assert "Deposit" in actions
        assert "Withdrawal" in actions


# ---------------------------------------------------------------------------
# Export router
# ---------------------------------------------------------------------------


class TestExportCSV:
    @pytest.mark.asyncio
    async def test_export_csv_empty(self, async_test_client: AsyncClient, auth_headers):
        response = await async_test_client.get(
            "/api/v1/export/csv", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        # BOM + header row at minimum
        text = response.content.decode("utf-8-sig")
        assert "symbol" in text.lower() or "date" in text.lower()

    @pytest.mark.asyncio
    async def test_export_csv_with_data(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        # Create an asset and a transaction
        asset_resp = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset_resp.json()["id"]
        await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "buy",
                "quantity": 3.0,
                "price": 150.0,
                "total_amount": 450.0,
                "transaction_date": "2024-01-20",
            },
            headers=auth_headers,
        )

        response = await async_test_client.get(
            "/api/v1/export/csv", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        text = response.content.decode("utf-8-sig")
        assert sample_asset_data["symbol"] in text


class TestExportPDT:
    @pytest.mark.asyncio
    async def test_export_pdt_returns_xlsx(
        self, async_test_client: AsyncClient, auth_headers
    ):
        with patch("portf_server.routers.exports.PDTXLSXExporter") as mock_exporter_cls:
            # Write a minimal valid xlsx to the temp file
            def fake_export(db, path, portfolio_id=None):
                import openpyxl

                wb = openpyxl.Workbook()
                wb.save(path)

            mock_exporter_cls.return_value.export.side_effect = fake_export

            response = await async_test_client.get(
                "/api/v1/export/pdt", headers=auth_headers
            )

        assert response.status_code == status.HTTP_200_OK
        assert (
            "spreadsheetml" in response.headers["content-type"]
            or "octet-stream" in response.headers["content-type"]
        )
        assert "attachment" in response.headers["content-disposition"]
        assert len(response.content) > 0


# ---------------------------------------------------------------------------
# Sync router
# ---------------------------------------------------------------------------


class TestSyncConfig:
    @pytest.mark.asyncio
    async def test_pdt_config_returns_status(
        self, async_test_client: AsyncClient, auth_headers
    ):
        response = await async_test_client.get(
            "/api/v1/sync/pdt-config", headers=auth_headers
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "service_account_configured" in data
        assert "default_spreadsheet_id" in data


class TestSyncPullEndpoint:
    @pytest.mark.asyncio
    async def test_pull_requires_spreadsheet_id(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Without sheet ID or env var, returns 400."""
        with patch.dict("os.environ", {}, clear=False):
            import os

            saved = os.environ.pop("GOOGLE_SPREADSHEET_ID", None)
            try:
                resp = await async_test_client.post(
                    "/api/v1/sync/pdt-pull", headers=auth_headers
                )
                assert resp.status_code == status.HTTP_400_BAD_REQUEST
            finally:
                if saved is not None:
                    os.environ["GOOGLE_SPREADSHEET_ID"] = saved

    @pytest.mark.asyncio
    async def test_pull_with_mocked_sync(
        self, async_test_client: AsyncClient, auth_headers
    ):
        """Pull endpoint saves data when sync returns results."""
        from portf_manager.parsers.pdt_xlsx_parser import PDTTransaction
        from datetime import date as _date

        fake_tx = PDTTransaction(
            broker="MyInvestor",
            name="Example Corp",
            pdt_type="Stock market",
            search="US0000000001",
            exchange="Nasdaq",
            date=_date(2025, 8, 28),
            action="Buy",
            amount=3.0,
            price=180.93,
            price_currency="USD",
        )
        mock_result = MagicMock()
        mock_result.transactions = [fake_tx]
        mock_result.dividends = []
        mock_result.bookings = []
        mock_result.skipped = []

        with patch("portf_manager.parsers.pdt_sheets_sync.PDTSheetsSync") as MockCls:
            MockCls.return_value.pull.return_value = mock_result
            resp = await async_test_client.post(
                "/api/v1/sync/pdt-pull?spreadsheet_id=FAKE123", headers=auth_headers
            )

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["imported_transactions"] == 1
        assert data["spreadsheet_id"] == "FAKE123"


class TestSyncPushEndpoint:
    @pytest.mark.asyncio
    async def test_push_requires_spreadsheet_id(
        self, async_test_client: AsyncClient, auth_headers
    ):
        import os

        saved = os.environ.pop("GOOGLE_SPREADSHEET_ID", None)
        try:
            resp = await async_test_client.post(
                "/api/v1/sync/pdt-push", headers=auth_headers
            )
            assert resp.status_code == status.HTTP_400_BAD_REQUEST
        finally:
            if saved is not None:
                os.environ["GOOGLE_SPREADSHEET_ID"] = saved

    @pytest.mark.asyncio
    async def test_push_with_mocked_sync(
        self, async_test_client: AsyncClient, auth_headers
    ):
        with patch("portf_manager.parsers.pdt_sheets_sync.PDTSheetsSync") as MockCls:
            MockCls.return_value.push.return_value = {
                "transactions": 10,
                "dividends": 3,
                "bookings": 5,
            }
            resp = await async_test_client.post(
                "/api/v1/sync/pdt-push?spreadsheet_id=FAKE123", headers=auth_headers
            )

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["transactions_written"] == 10
        assert data["dividends_written"] == 3
        assert data["bookings_written"] == 5
        assert "spreadsheet_url" in data
