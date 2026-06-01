"""Unit tests for PDT Google Sheets sync (pdt_sheets_sync.py)."""

from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pytest

from portf_manager.parsers.pdt_sheets_sync import (
    PDTSheetsSync,
    _fmt,
    _iter_data_rows,
    _pad_row,
    _row_to_sheets,
    _serial_to_date,
    _sheets_val,
    pull_from_sheets,
    push_to_sheets,
)
from portf_manager.parsers.pdt_xlsx_parser import PDTParseResult

# ---------------------------------------------------------------------------
# Helper: mock PDTSheetsSync._svc so no real Google API is called
# ---------------------------------------------------------------------------


def _make_sync(spreadsheet_id="FAKE_ID"):
    sync = PDTSheetsSync(spreadsheet_id, service_account_file="fake.json")
    sync._service = MagicMock()
    return sync


# ---------------------------------------------------------------------------
# _serial_to_date
# ---------------------------------------------------------------------------


class TestSerialToDate:
    def test_serial_number(self):
        # 45171 = 2023-09-02  (days since 1899-12-30)
        result = _serial_to_date(45171)
        assert result == date(2023, 9, 2)

    def test_iso_string(self):
        assert _serial_to_date("2025-09-02") == date(2025, 9, 2)

    def test_iso_datetime_string(self):
        assert _serial_to_date("2025-09-02T00:00:00") == date(2025, 9, 2)

    def test_datetime_object(self):
        assert _serial_to_date(datetime(2025, 6, 17)) == date(2025, 6, 17)

    def test_date_object(self):
        assert _serial_to_date(date(2025, 6, 17)) == date(2025, 6, 17)

    def test_none(self):
        assert _serial_to_date(None) is None

    def test_empty_string(self):
        assert _serial_to_date("") is None

    def test_zero_serial(self):
        assert _serial_to_date(0) is None


# ---------------------------------------------------------------------------
# _sheets_val / _pad_row / _iter_data_rows
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_sheets_val_empty_to_none(self):
        assert _sheets_val("") is None
        assert _sheets_val(None) is None

    def test_sheets_val_keeps_zero(self):
        assert _sheets_val(0) == 0

    def test_sheets_val_keeps_string(self):
        assert _sheets_val("hello") == "hello"

    def test_pad_row_extends(self):
        assert _pad_row([1, 2], 5) == [1, 2, None, None, None]

    def test_pad_row_no_op_when_long_enough(self):
        assert _pad_row([1, 2, 3], 3) == [1, 2, 3]

    def test_iter_data_rows_skips_headers(self):
        rows = [["h1"], ["h2"], ["h3"], ["data_row"]]
        result = list(_iter_data_rows(rows))
        assert len(result) == 1
        assert result[0] == (4, ["data_row"])

    def test_iter_data_rows_skips_empty_rows(self):
        rows = [["h"], ["h"], ["h"], [""], [None, None], ["real"]]
        result = list(_iter_data_rows(rows))
        assert len(result) == 1
        assert result[0][1] == ["real"]


# ---------------------------------------------------------------------------
# _fmt / _row_to_sheets
# ---------------------------------------------------------------------------


class TestFmt:
    def test_none_to_empty_string(self):
        assert _fmt(None) == ""

    def test_date_to_iso(self):
        assert _fmt(date(2025, 9, 2)) == "2025-09-02"

    def test_datetime_to_iso_date(self):
        assert _fmt(datetime(2025, 9, 2, 10, 30)) == "2025-09-02"

    def test_time_to_string(self):
        assert _fmt(time(9, 0)) == "09:00:00"

    def test_number_passthrough(self):
        assert _fmt(3.14) == pytest.approx(3.14)

    def test_string_passthrough(self):
        assert _fmt("EUR") == "EUR"

    def test_row_to_sheets_converts_all(self):
        row = [date(2025, 1, 1), None, 42.0, "Buy"]
        result = _row_to_sheets(row)
        assert result == ["2025-01-01", "", 42.0, "Buy"]


# ---------------------------------------------------------------------------
# Pull: parse Transactions sheet
# ---------------------------------------------------------------------------


def _tx_rows():
    """Minimal 3-header + 1 data row for Transactions sheet."""
    return [
        # row 0: machine keys
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
        ],
        # row 1: group labels
        [None, "Effect", None, None, None, "Transaction"],
        # row 2: display labels
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
            "Currency",
        ],
        # row 3: data (date as serial)
        [
            "MyInvestor",
            "Example Corp",
            "Stock market",
            "US0000000001",
            "Nasdaq",
            45532,
            0.0,  # 45532 = 2024-08-27
            "Buy",
            3.0,
            180.93,
            "USD",
        ],
    ]


def _div_rows():
    return [
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
        ],
        [None, "Effect", None, None, None, "Dividend"],
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
            "Currency",
        ],
        [
            "MyInvestor",
            "ROVI",
            "Stock market",
            "ES0000000001",
            "Bolsa de Madrid",
            45779,
            0.0,
            "Cash",
            6.82,
            "EUR",
        ],
    ]


def _book_rows():
    return [
        [
            "broker",
            "date",
            "time",
            "booking-action",
            "booking-amount",
            "booking-amount-currency",
        ],
        [None, "Booking", None, None, "Booking amount"],
        [" ", "Date", "Time", "Action", "Value", "Currency"],
        ["MyInvestor", 45824, 0.375, "Deposit", 250.0, "EUR"],
    ]


class TestPullTransactions:
    def _sync_with_data(self):
        sync = _make_sync()
        sync._read_sheet = MagicMock(
            side_effect=lambda name: {
                "Transactions": _tx_rows(),
                "Dividends": [],
                "Bookings": [],
            }[name]
        )
        return sync

    def test_parses_one_transaction(self):
        result = self._sync_with_data().pull()
        assert len(result.transactions) == 1
        tx = result.transactions[0]
        assert tx.broker == "MyInvestor"
        assert tx.search == "US0000000001"
        assert tx.action == "Buy"
        assert tx.amount == pytest.approx(3.0)
        assert tx.price == pytest.approx(180.93)
        assert tx.price_currency == "USD"

    def test_date_from_serial(self):
        result = self._sync_with_data().pull()
        tx = result.transactions[0]
        assert tx.date == _serial_to_date(45532)

    def test_skips_missing_required_fields(self):
        sync = _make_sync()
        rows = _tx_rows()
        rows.append(
            [
                "MyInvestor",
                "BadRow",
                "Stock market",
                "XX999",
                "NYSE",
                45532,
                0.0,
                "Buy",
                3.0,
                None,
                "EUR",
            ]  # price is None
        )
        sync._read_sheet = MagicMock(
            side_effect=lambda name: {
                "Transactions": rows,
                "Dividends": [],
                "Bookings": [],
            }[name]
        )
        result = sync.pull()
        assert len(result.transactions) == 1  # only first row valid
        assert any("Transactions" in s for s, _ in result.skipped)


class TestPullDividends:
    def _sync_with_data(self):
        sync = _make_sync()
        sync._read_sheet = MagicMock(
            side_effect=lambda name: {
                "Transactions": [],
                "Dividends": _div_rows(),
                "Bookings": [],
            }[name]
        )
        return sync

    def test_parses_one_dividend(self):
        result = self._sync_with_data().pull()
        assert len(result.dividends) == 1
        div = result.dividends[0]
        assert div.action == "Cash"
        assert div.amount == pytest.approx(6.82)
        assert div.amount_currency == "EUR"
        assert div.search == "ES0000000001"


class TestPullBookings:
    def _sync_with_data(self):
        sync = _make_sync()
        sync._read_sheet = MagicMock(
            side_effect=lambda name: {
                "Transactions": [],
                "Dividends": [],
                "Bookings": _book_rows(),
            }[name]
        )
        return sync

    def test_parses_one_booking(self):
        result = self._sync_with_data().pull()
        assert len(result.bookings) == 1
        bk = result.bookings[0]
        assert bk.broker == "MyInvestor"
        assert bk.action == "Deposit"
        assert bk.amount == pytest.approx(250.0)
        assert bk.currency == "EUR"

    def test_date_from_serial(self):
        result = self._sync_with_data().pull()
        assert result.bookings[0].date == _serial_to_date(45824)


class TestPullSheetError:
    def test_sheet_read_error_is_recorded_as_skipped(self):
        sync = _make_sync()
        sync._read_sheet = MagicMock(side_effect=Exception("API error"))
        result = sync.pull()
        assert result.transactions == []
        assert any("Sheet read error" in r for _, r in result.skipped)


# ---------------------------------------------------------------------------
# Push: build rows and write to sheets
# ---------------------------------------------------------------------------


def _make_db_adapter(transactions=(), assets=(), portfolios=(), bookings=()):
    adapter = MagicMock()
    asset_map = {a["id"]: a for a in assets}
    portfolio_map = {p["id"]: p for p in portfolios}
    adapter.get_all_transactions.return_value = list(transactions)
    adapter.get_transactions_by_portfolio.side_effect = lambda pid: [
        t for t in transactions if t.get("portfolio_id") == pid
    ]
    adapter.get_asset.side_effect = lambda aid: asset_map.get(aid)
    adapter.get_portfolio.side_effect = lambda pid: portfolio_map.get(pid)
    adapter.get_all_bookings.side_effect = lambda portfolio_id=None: [
        b
        for b in bookings
        if portfolio_id is None or b.get("portfolio_id") == portfolio_id
    ]
    return adapter


class TestPushTransactions:
    def test_writes_three_sheets(self):
        sync = _make_sync()
        written = {}
        sync._write_sheet = lambda name, rows: written.update({name: rows})

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
                    "tax": 0,
                    "currency": "USD",
                    "transaction_date": "2024-08-27",
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

        counts = sync.push(adapter)
        assert "Transactions" in written
        assert "Dividends" in written
        assert "Bookings" in written
        assert "Expenses" in written
        assert "Settings" in written
        assert counts["transactions"] == 1
        assert counts["dividends"] == 0
        assert counts["bookings"] == 0

    def test_crypto_exchange_is_empty_in_push(self):
        """Crypto assets must export with empty exchange."""
        sync = _make_sync()
        written = {}
        sync._write_sheet = lambda name, rows: written.update({name: rows})
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
        sync.push(adapter)
        data_row = written["Transactions"][3]
        assert _fmt(data_row[4]) == ""  # exchange column must be empty for crypto

    def test_transaction_row_values(self):
        sync = _make_sync()
        written = {}
        sync._write_sheet = lambda name, rows: written.update({name: rows})

        adapter = _make_db_adapter(
            transactions=[
                {
                    "id": 1,
                    "asset_id": 10,
                    "portfolio_id": 1,
                    "transaction_type": "buy",
                    "quantity": 5.0,
                    "price": 50.0,
                    "total_amount": 252.0,
                    "fees": 2.0,
                    "tax": 0,
                    "currency": "EUR",
                    "transaction_date": "2025-06-15",
                }
            ],
            assets=[
                {
                    "id": 10,
                    "symbol": "ISIN0001",
                    "name": "Test ETF",
                    "asset_type": "etf",
                    "exchange": "XETRA Exchange",
                    "currency": "EUR",
                }
            ],
            portfolios=[{"id": 1, "name": "TestBroker"}],
        )
        sync.push(adapter)
        tx_rows = written["Transactions"]
        data_row = tx_rows[3]  # row index 3 = first data row (after 3 headers)
        # [broker, name, type, search, exchange, date, time, action, qty, price, cur, ...]
        assert _fmt(data_row[0]) == "TestBroker"
        assert _fmt(data_row[3]) == "ISIN0001"
        assert _fmt(data_row[7]) == "Buy"
        assert data_row[8] == pytest.approx(5.0)
        assert data_row[9] == pytest.approx(50.0)
        assert _fmt(data_row[10]) == "EUR"
        assert data_row[13] == pytest.approx(2.0)  # fees

    def test_dividend_uses_transaction_currency_not_asset(self):
        """Dividend paid in EUR for a USD stock uses EUR."""
        sync = _make_sync()
        written = {}
        sync._write_sheet = lambda name, rows: written.update({name: rows})

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
                    "currency": "EUR",  # EUR, not USD
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
                    "currency": "USD",
                }
            ],
            portfolios=[{"id": 1, "name": "MyInvestor"}],
        )
        sync.push(adapter)
        div_rows = written["Dividends"]
        data_row = div_rows[3]
        # [broker, name, type, search, exchange, date, time, action, amount, currency, ...]
        assert _fmt(data_row[9]) == "EUR"  # amount currency

    def test_bookings_pushed(self):
        sync = _make_sync()
        written = {}
        sync._write_sheet = lambda name, rows: written.update({name: rows})

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
                }
            ],
        )
        counts = sync.push(adapter)
        assert counts["bookings"] == 1
        bk_rows = written["Bookings"]
        data_row = bk_rows[3]
        assert _fmt(data_row[3]) == "Deposit"
        assert data_row[4] == pytest.approx(250.0)
        assert _fmt(data_row[5]) == "EUR"


# ---------------------------------------------------------------------------
# Convenience function wrappers
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    def test_pull_from_sheets_delegates(self):
        with patch("portf_manager.parsers.pdt_sheets_sync.PDTSheetsSync") as MockCls:
            mock_sync = MagicMock()
            mock_sync.pull.return_value = PDTParseResult()
            MockCls.return_value = mock_sync
            result = pull_from_sheets("SHEET_ID", service_account_file="fake.json")
        MockCls.assert_called_once_with("SHEET_ID", "fake.json")
        mock_sync.pull.assert_called_once()
        assert isinstance(result, PDTParseResult)

    def test_push_to_sheets_delegates(self):
        with patch("portf_manager.parsers.pdt_sheets_sync.PDTSheetsSync") as MockCls:
            mock_sync = MagicMock()
            mock_sync.push.return_value = {
                "transactions": 5,
                "dividends": 2,
                "bookings": 1,
            }
            MockCls.return_value = mock_sync
            adapter = MagicMock()
            counts = push_to_sheets(
                adapter, "SHEET_ID", service_account_file="fake.json"
            )
        MockCls.assert_called_once_with("SHEET_ID", "fake.json")
        mock_sync.push.assert_called_once_with(adapter, None)
        assert counts["transactions"] == 5
