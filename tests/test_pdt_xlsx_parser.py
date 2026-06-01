"""Unit tests for the Portfolio Dividend Tracker XLSX import/export."""

import io
import os
import tempfile
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pytest

# openpyxl is required — skip all tests gracefully if not installed
openpyxl = pytest.importorskip("openpyxl")

from portf_manager.parsers.pdt_xlsx_parser import (
    PDTBooking,
    PDTDividend,
    PDTParseResult,
    PDTTransaction,
    PDTXLSXExporter,
    PDTXLSXParser,
    _detect_asset_type,
    _pdt_action_to_tx_type,
    _pdt_exchange,
    _tx_type_to_pdt_action,
    _asset_type_to_pdt_type,
    _to_date,
    _float_or_none,
    export_pdt_xlsx,
    parse_pdt_xlsx,
)

# ---------------------------------------------------------------------------
# Helper to build a minimal PDT XLSX in memory
# ---------------------------------------------------------------------------


def _make_pdt_workbook(
    transactions=(),
    dividends=(),
    bookings=(),
):
    """Create a minimal PDT v2 workbook with 3-row headers + provided data rows."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # --- Transactions sheet ---
    tx_ws = wb.create_sheet("Transactions")
    tx_ws.append(
        [
            "broker",
            "name",
            "type",
            "search",
            "exchange",
            "date",
            "time",
            "transaction-action",
            "transaction-amount",
            "transaction-price",
            "transaction-price-currency",
            "transaction-price-exchange-rate",
            "transaction-price-exchange-rate-currency",
            "transaction-costs",
            "transaction-costs-currency",
            "transaction-costs-exchange-rate",
            "transaction-costs-exchange-rate-currency",
            "transaction-tax",
            "transaction-tax-currency",
            "transaction-tax-exchange-rate",
            "transaction-tax-exchange-rate-currency",
        ]
    )
    tx_ws.append(
        [
            None,
            "Effect",
            None,
            None,
            None,
            "Transaction",
            None,
            None,
            None,
            "Transaction price",
            None,
            None,
            None,
            "Transaction cost",
            None,
            None,
            None,
            "Transaction tax",
            None,
            None,
            None,
        ]
    )
    tx_ws.append(
        [
            "Broker",
            "Name",
            "Type",
            "Search",
            "Exchange",
            "Date",
            "Time",
            "Action",
            "Amount",
            "Value",
            "Currency from",
            "Exchange rate",
            "Currency to",
            "Value",
            "Currency from",
            "Exchange rate",
            "Currency to",
            "Value",
            "Currency from",
            "Exchange rate",
            "Currency to",
        ]
    )
    for row in transactions:
        tx_ws.append(row)

    # --- Dividends sheet ---
    div_ws = wb.create_sheet("Dividends")
    div_ws.append(
        [
            "broker",
            "name",
            "type",
            "search",
            "exchange",
            "date",
            "time",
            "dividend-action",
            "dividend-amount",
            "dividend-amount-currency",
            "dividend-amount-exchange-rate",
            "dividend-amount-exchange-rate-currency",
            "dividend-tax",
            "dividend-tax-currency",
            "dividend-tax-exchange-rate",
            "dividend-tax-exchange-rate-currency",
            "dividend-costs",
            "dividend-costs-currency",
            "dividend-costs-exchange-rate",
            "dividend-costs-exchange-rate-currency",
        ]
    )
    div_ws.append(
        [
            None,
            "Effect",
            None,
            None,
            None,
            "Dividend",
            None,
            None,
            "Dividend amount",
            None,
            None,
            None,
            "Dividend tax",
            None,
            None,
            None,
            "Dividend cost",
            None,
            None,
            None,
        ]
    )
    div_ws.append(
        [
            "Broker",
            "Name",
            "Type",
            "Search",
            "Exchange",
            "Date",
            "Time",
            "Action",
            "Amount",
            "Currency from",
            "Exchange rate",
            "Currency to",
            "Value",
            "Currency from",
            "Exchange rate",
            "Currency to",
            "Value",
            "Currency from",
            "Exchange rate",
            "Currency to",
        ]
    )
    for row in dividends:
        div_ws.append(row)

    # --- Bookings sheet ---
    book_ws = wb.create_sheet("Bookings")
    book_ws.append(
        [
            "broker",
            "date",
            "time",
            "booking-action",
            "booking-amount",
            "booking-amount-currency",
        ]
    )
    book_ws.append([None, "Booking", None, None, "Booking amount", None])
    book_ws.append([" ", "Date", "Time", "Action", "Value", "Currency"])
    for row in bookings:
        book_ws.append(row)

    return wb


def _save_and_parse(wb) -> PDTParseResult:
    """Save workbook to a temp file and parse it."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        tmp_path = f.name
    try:
        wb.save(tmp_path)
        return parse_pdt_xlsx(tmp_path)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


class TestDetectAssetType:
    def test_crypto(self):
        assert _detect_asset_type("Bitcoin", "Crypto") == "crypto"

    def test_commodity(self):
        assert _detect_asset_type("Gold", "Commodity") == "commodity"

    def test_etf_by_keyword(self):
        assert (
            _detect_asset_type("iShares MSCI World UCITS ETF", "Stock market") == "etf"
        )

    def test_etf_fund_keyword(self):
        assert _detect_asset_type("Vanguard Index Fund", "Stock market") == "etf"

    def test_stock_without_keyword(self):
        assert _detect_asset_type("Example Corp", "Stock market") == "stock"


class TestActionMappers:
    def test_buy(self):
        assert _pdt_action_to_tx_type("Buy") == "buy"
        assert _pdt_action_to_tx_type("buy") == "buy"

    def test_sell(self):
        assert _pdt_action_to_tx_type("Sell") == "sell"

    def test_unknown_returns_none(self):
        assert _pdt_action_to_tx_type("Dividend") is None

    def test_tx_type_to_pdt_buy(self):
        assert _tx_type_to_pdt_action("buy") == "Buy"

    def test_tx_type_to_pdt_sell(self):
        assert _tx_type_to_pdt_action("sell") == "Sell"

    def test_dividend_not_in_tx_actions(self):
        assert _tx_type_to_pdt_action("dividend") is None


class TestToDate:
    def test_datetime_object(self):
        assert _to_date(datetime(2025, 6, 15)) == date(2025, 6, 15)

    def test_date_object(self):
        assert _to_date(date(2025, 6, 15)) == date(2025, 6, 15)

    def test_iso_string(self):
        assert _to_date("2025-06-15") == date(2025, 6, 15)

    def test_none(self):
        assert _to_date(None) is None

    def test_unparseable_string(self):
        assert _to_date("not-a-date") is None


class TestFloatOrNone:
    def test_float(self):
        assert _float_or_none(3.14) == pytest.approx(3.14)

    def test_int(self):
        assert _float_or_none(10) == pytest.approx(10.0)

    def test_none(self):
        assert _float_or_none(None) is None

    def test_invalid_string(self):
        assert _float_or_none("abc") is None


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestPDTXLSXParserTransactions:
    def test_single_buy(self):
        wb = _make_pdt_workbook(
            transactions=[
                [
                    "MyInvestor",
                    "Example Corp",
                    "Stock market",
                    "US0000000001",
                    "Nasdaq",
                    datetime(2025, 8, 28),
                    time(0, 0),
                    "Buy",
                    3.0,
                    180.93,
                    "USD",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.broker == "MyInvestor"
        assert tx.name == "Example Corp"
        assert tx.search == "US0000000001"
        assert tx.action == "Buy"
        assert tx.amount == pytest.approx(3.0)
        assert tx.price == pytest.approx(180.93)
        assert tx.price_currency == "USD"
        assert tx.date == date(2025, 8, 28)

    def test_buy_with_costs(self):
        wb = _make_pdt_workbook(
            transactions=[
                [
                    "MyInvestor",
                    "AMD",
                    "Stock market",
                    "US0000000002",
                    "Nasdaq",
                    datetime(2025, 8, 28),
                    time(0, 0),
                    "Buy",
                    4.0,
                    168.6,
                    "USD",
                    None,
                    None,
                    1.5,
                    "USD",  # costs
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.transactions) == 1
        assert result.transactions[0].costs == pytest.approx(1.5)
        assert result.transactions[0].costs_currency == "USD"

    def test_sell_action(self):
        wb = _make_pdt_workbook(
            transactions=[
                [
                    "MyInvestor",
                    "Example Corp",
                    "Stock market",
                    "US0000000001",
                    "Nasdaq",
                    datetime(2025, 11, 1),
                    time(0, 0),
                    "Sell",
                    1.0,
                    200.0,
                    "USD",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert result.transactions[0].action == "Sell"

    def test_skips_empty_rows(self):
        wb = _make_pdt_workbook(
            transactions=[
                [
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
                [
                    "MyInvestor",
                    "Example Corp",
                    "Stock market",
                    "US0000000001",
                    "Nasdaq",
                    datetime(2025, 8, 28),
                    time(0, 0),
                    "Buy",
                    3.0,
                    180.93,
                    "USD",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.transactions) == 1

    def test_missing_price_skipped(self):
        wb = _make_pdt_workbook(
            transactions=[
                [
                    "MyInvestor",
                    "BadRow",
                    "Stock market",
                    "XX999",
                    "Nasdaq",
                    datetime(2025, 8, 28),
                    time(0, 0),
                    "Buy",
                    3.0,
                    None,
                    "USD",  # price is None
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.transactions) == 0
        assert any("Transactions" in s for s, _ in result.skipped)

    def test_multiple_transactions(self):
        rows = [
            [
                "MyInvestor",
                f"Asset{i}",
                "Stock market",
                f"ISIN{i:04d}",
                "NYSE",
                datetime(2025, 1, i + 1),
                time(0, 0),
                "Buy",
                float(i + 1),
                10.0,
                "EUR",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ]
            for i in range(5)
        ]
        wb = _make_pdt_workbook(transactions=rows)
        result = _save_and_parse(wb)
        assert len(result.transactions) == 5


class TestPDTXLSXParserDividends:
    def test_cash_dividend(self):
        wb = _make_pdt_workbook(
            dividends=[
                [
                    "MyInvestor",
                    "ROVI",
                    "Stock market",
                    "ES0000000001",
                    "Bolsa de Madrid",
                    datetime(2025, 5, 2),
                    time(10, 0),
                    "Cash",
                    6.82,
                    "EUR",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.dividends) == 1
        div = result.dividends[0]
        assert div.action == "Cash"
        assert div.amount == pytest.approx(6.82)
        assert div.amount_currency == "EUR"

    def test_skips_incomplete_dividend(self):
        wb = _make_pdt_workbook(
            dividends=[
                [
                    "MyInvestor",
                    "ROVI",
                    "Stock market",
                    "ES0000000001",
                    "Bolsa de Madrid",
                    datetime(2025, 5, 2),
                    time(10, 0),
                    "Cash",
                    None,
                    "EUR",  # amount is None
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.dividends) == 0


class TestPDTXLSXParserBookings:
    def test_deposit(self):
        wb = _make_pdt_workbook(
            bookings=[
                [
                    "MyInvestor",
                    datetime(2025, 6, 17),
                    time(9, 0),
                    "Deposit",
                    250.0,
                    "EUR",
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert len(result.bookings) == 1
        b = result.bookings[0]
        assert b.action == "Deposit"
        assert b.amount == pytest.approx(250.0)
        assert b.currency == "EUR"

    def test_withdrawal(self):
        wb = _make_pdt_workbook(
            bookings=[
                [
                    "MyInvestor",
                    datetime(2025, 7, 1),
                    time(9, 0),
                    "Withdrawal",
                    500.0,
                    "EUR",
                ],
            ]
        )
        result = _save_and_parse(wb)
        assert result.bookings[0].action == "Withdrawal"


# ---------------------------------------------------------------------------
# Exporter tests
# ---------------------------------------------------------------------------


def _make_db_adapter(transactions=(), assets=(), portfolios=(), bookings=()):
    """Return a mock DatabaseAdapter pre-configured with test data."""
    adapter = MagicMock()

    asset_map = {a["id"]: a for a in assets}
    portfolio_map = {p["id"]: p for p in portfolios}
    bookings_list = list(bookings)

    adapter.get_all_transactions.return_value = list(transactions)
    adapter.get_transactions_by_portfolio.side_effect = lambda pid: [
        t for t in transactions if t.get("portfolio_id") == pid
    ]
    adapter.get_asset.side_effect = lambda aid: asset_map.get(aid)
    adapter.get_portfolio.side_effect = lambda pid: portfolio_map.get(pid)
    adapter.get_all_bookings.side_effect = lambda portfolio_id=None: [
        b
        for b in bookings_list
        if portfolio_id is None or b.get("portfolio_id") == portfolio_id
    ]

    return adapter


class TestPDTXLSXExporter:
    def test_export_creates_file(self):
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 3.0,
                    "price": 180.93,
                    "total_amount": 542.79,
                    "fees": 0,
                    "transaction_date": "2025-08-28",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "US0000000001",
                    "name": "Example Corp",
                    "asset_type": "stock",
                    "exchange": "Nasdaq",
                    "currency": "USD",
                }
            ],
            portfolios=[{"id": 1, "name": "MyInvestor"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            assert os.path.exists(tmp_path)
            wb = openpyxl.load_workbook(tmp_path)
            assert "Transactions" in wb.sheetnames
            assert "Dividends" in wb.sheetnames
            assert "Bookings" in wb.sheetnames
            assert "Expenses" in wb.sheetnames
            assert "Settings" in wb.sheetnames
        finally:
            os.unlink(tmp_path)

    def test_export_transactions_sheet_has_three_header_rows(self):
        adapter = _make_db_adapter()

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            wb = openpyxl.load_workbook(tmp_path)
            ws = wb["Transactions"]
            rows = list(ws.iter_rows(values_only=True))
            assert rows[0][0] == "broker"
            assert rows[1][1] == "Effect"
            assert rows[2][0] == "Broker"
        finally:
            os.unlink(tmp_path)

    def test_exported_buy_roundtrip(self):
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 5.0,
                    "price": 50.0,
                    "total_amount": 250.0,
                    "fees": 1.5,
                    "transaction_date": "2025-06-15",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "ISIN0001",
                    "name": "Test ETF UCITS",
                    "asset_type": "etf",
                    "exchange": "XETRA Exchange",
                    "currency": "EUR",
                }
            ],
            portfolios=[{"id": 1, "name": "TestBroker"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            # Re-parse the exported file
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.transactions) == 1
            tx = result.transactions[0]
            assert tx.broker == "TestBroker"
            assert tx.search == "ISIN0001"
            assert tx.action == "Buy"
            assert tx.amount == pytest.approx(5.0)
            assert tx.price == pytest.approx(50.0)
            assert tx.costs == pytest.approx(1.5)
        finally:
            os.unlink(tmp_path)

    def test_export_dividends_roundtrip(self):
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 2,
                    "asset_id": 11,
                    "portfolio_id": 1,
                    "transaction_type": "dividend",
                    "quantity": 1.0,
                    "price": 6.82,
                    "total_amount": 6.82,
                    "fees": 0,
                    "transaction_date": "2025-05-02",
                }
            ],
            assets=[
                {
                    "id": 11,
                    "symbol": "ES0000000001",
                    "name": "ROVI SA",
                    "asset_type": "stock",
                    "exchange": "Bolsa de Madrid",
                    "currency": "EUR",
                }
            ],
            portfolios=[{"id": 1, "name": "MyInvestor"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.dividends) == 1
            div = result.dividends[0]
            assert div.action == "Cash"
            assert div.amount == pytest.approx(6.82)
            assert div.broker == "MyInvestor"
        finally:
            os.unlink(tmp_path)

    def test_split_and_transfer_rows_not_exported_to_tx_sheet(self):
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 3,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "split",
                    "quantity": 2.0,
                    "price": 1.0,
                    "total_amount": 2.0,
                    "fees": 0,
                    "transaction_date": "2025-01-01",
                },
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "ISIN0001",
                    "name": "Test Stock",
                    "asset_type": "stock",
                    "exchange": "NYSE",
                    "currency": "USD",
                }
            ],
            portfolios=[{"id": 1, "name": "TestBroker"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            # split should not appear in either sheet
            assert len(result.transactions) == 0
            assert len(result.dividends) == 0
        finally:
            os.unlink(tmp_path)

    def test_portfolio_filter(self):
        """Only transactions from the requested portfolio are exported."""
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 1.0,
                    "price": 100.0,
                    "total_amount": 100.0,
                    "fees": 0,
                    "transaction_date": "2025-01-01",
                },
                {
                    "id": 2,
                    "asset_id": 10,
                    "portfolio_id": 2,
                    "transaction_type": "buy",
                    "quantity": 2.0,
                    "price": 100.0,
                    "total_amount": 200.0,
                    "fees": 0,
                    "transaction_date": "2025-01-02",
                },
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "ISIN0001",
                    "name": "Test Stock",
                    "asset_type": "stock",
                    "exchange": "NYSE",
                    "currency": "USD",
                }
            ],
            portfolios=[
                {"id": 1, "name": "Portfolio A"},
                {"id": 2, "name": "Portfolio B"},
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path, portfolio_id=1)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.transactions) == 1
            assert result.transactions[0].amount == pytest.approx(1.0)
        finally:
            os.unlink(tmp_path)

    def test_bookings_display_header_uses_space_not_broker(self):
        """PDT template uses ' ' (space) as the broker display label in Bookings sheet."""
        adapter = _make_db_adapter()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            wb = openpyxl.load_workbook(tmp_path)
            rows = list(wb["Bookings"].iter_rows(values_only=True))
            assert rows[2][0] == " "
        finally:
            os.unlink(tmp_path)

    def test_settings_sheet_has_version_and_url(self):
        adapter = _make_db_adapter()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            wb = openpyxl.load_workbook(tmp_path)
            rows = list(wb["Settings"].iter_rows(values_only=True))
            assert rows[0] == ("Version", "URL")
            assert rows[1][0] == pytest.approx(2.0)
            assert "portfoliodividendtracker.com" in rows[1][1]
        finally:
            os.unlink(tmp_path)

    def test_expenses_sheet_has_headers(self):
        adapter = _make_db_adapter()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            wb = openpyxl.load_workbook(tmp_path)
            rows = list(wb["Expenses"].iter_rows(values_only=True))
            assert rows[0][0] == "broker"
            assert rows[0][3] == "description"
            assert rows[2][0] == "Broker"
        finally:
            os.unlink(tmp_path)

    def test_crypto_asset_exports_empty_exchange(self):
        """Crypto assets must have empty exchange per PDT spec."""
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 0.01,
                    "price": 42000.0,
                    "total_amount": 420.0,
                    "fees": 0,
                    "tax": 0,
                    "currency": "USD",
                    "transaction_date": "2024-01-10",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "asset_type": "crypto",
                    "exchange": "Crypto",
                    "currency": "USD",
                }
            ],
            portfolios=[{"id": 1, "name": "Coinbase"}],
        )
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.transactions) == 1
            assert result.transactions[0].exchange == ""
            assert result.transactions[0].pdt_type == "Crypto"
        finally:
            os.unlink(tmp_path)

    def test_export_bookings_roundtrip(self):
        """Bookings stored in DB are exported to the Bookings sheet and can be re-parsed."""
        adapter = _make_db_adapter(
            portfolios=[{"id": 1, "name": "MyInvestor"}],
            bookings=[
                {
                    "id": 1,
                    "portfolio_id": 1,
                    "date": "2025-06-17",
                    "action": "Deposit",
                    "amount": 250.0,
                    "currency": "EUR",
                },
                {
                    "id": 2,
                    "portfolio_id": 1,
                    "date": "2025-07-03",
                    "action": "Deposit",
                    "amount": 1000.0,
                    "currency": "EUR",
                },
            ],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.bookings) == 2
            assert result.bookings[0].action == "Deposit"
            assert result.bookings[0].amount == pytest.approx(250.0)
            assert result.bookings[1].amount == pytest.approx(1000.0)
        finally:
            os.unlink(tmp_path)

    def test_export_transaction_tax_roundtrip(self):
        """Transaction tax is exported to the tax column and re-parsed correctly."""
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 5.0,
                    "price": 50.0,
                    "total_amount": 252.5,
                    "fees": 1.5,
                    "tax": 1.0,
                    "transaction_date": "2025-06-15",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "ISIN0001",
                    "name": "Test ETF UCITS",
                    "asset_type": "etf",
                    "exchange": "XETRA Exchange",
                    "currency": "EUR",
                }
            ],
            portfolios=[{"id": 1, "name": "TestBroker"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.transactions) == 1
            tx = result.transactions[0]
            assert tx.costs == pytest.approx(1.5)
            assert tx.tax == pytest.approx(1.0)
        finally:
            os.unlink(tmp_path)

    def test_export_transaction_currency_overrides_asset_currency(self):
        """Per-transaction currency is used instead of asset currency when set."""
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 35.0,
                    "price": 145.3,
                    "total_amount": 5085.5,
                    "fees": 0,
                    "tax": 0,
                    "currency": "SEK",  # per-transaction currency
                    "transaction_date": "2025-07-15",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "SE0000000001",
                    "name": "Securitas AB",
                    "asset_type": "stock",
                    "exchange": "Stockholm Exchange",
                    "currency": "GBP",  # asset has different currency
                }
            ],
            portfolios=[{"id": 1, "name": "MyInvestor"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.transactions) == 1
            assert result.transactions[0].price_currency == "SEK"
        finally:
            os.unlink(tmp_path)

    def test_export_dividend_currency_preserved(self):
        """Dividend paid in EUR for a USD asset uses the transaction currency (EUR)."""
        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "dividend",
                    "quantity": 1.0,
                    "price": 0.09,
                    "total_amount": 0.09,
                    "fees": 0,
                    "tax": 0,
                    "currency": "EUR",  # dividend paid in EUR, not USD
                    "transaction_date": "2026-04-01",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "US0000000001",
                    "name": "Example Corp",
                    "asset_type": "stock",
                    "exchange": "Nasdaq",
                    "currency": "USD",  # asset trades in USD
                }
            ],
            portfolios=[{"id": 1, "name": "MyInvestor"}],
        )

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            tmp_path = f.name
        try:
            export_pdt_xlsx(adapter, tmp_path)
            result = parse_pdt_xlsx(tmp_path)
            assert len(result.dividends) == 1
            # dividend amount_currency should be EUR (the transaction currency)
            assert result.dividends[0].amount_currency == "EUR"
        finally:
            os.unlink(tmp_path)


class TestPdtExchange:
    def test_crypto_returns_empty(self):
        assert _pdt_exchange("Crypto", "crypto") == ""

    def test_commodity_returns_empty(self):
        assert _pdt_exchange("Some Exchange", "commodity") == ""

    def test_stock_returns_exchange(self):
        assert _pdt_exchange("XETRA Exchange", "stock") == "XETRA Exchange"

    def test_etf_returns_exchange(self):
        assert _pdt_exchange("Nasdaq", "etf") == "Nasdaq"

    def test_none_exchange_returns_empty_string(self):
        assert _pdt_exchange(None, "stock") == ""


class TestAssetTypePDTMapping:
    def test_etf_maps_to_stock_market(self):
        assert _asset_type_to_pdt_type("etf") == "Stock market"

    def test_crypto_maps_to_crypto(self):
        assert _asset_type_to_pdt_type("crypto") == "Crypto"

    def test_commodity_maps_to_commodity(self):
        assert _asset_type_to_pdt_type("commodity") == "Commodity"

    def test_unknown_defaults_to_stock_market(self):
        assert _asset_type_to_pdt_type("unknown_type") == "Stock market"
