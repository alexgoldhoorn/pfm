"""
PDT-format Google Sheets sync.

Reads and writes the Portfolio Dividend Tracker v2 Google Sheets template:
  - Transactions sheet  (buy/sell with costs and tax)
  - Dividends sheet     (dividend payments)
  - Bookings sheet      (deposits and withdrawals)

Column layout is identical to the PDT XLSX format so the same parsing
helpers (_to_date, _float_or_none, etc.) can be reused directly.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, date, time
from typing import Any, Iterator, List, Optional, Tuple

try:
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "google-api-python-client and google-auth are required for Sheets sync. "
        "Install: pip install google-api-python-client google-auth"
    ) from exc

from .pdt_xlsx_parser import (
    PDTBooking,
    PDTDividend,
    PDTParseResult,
    PDTTransaction,
    _TX_HEADER_ROW1,
    _TX_HEADER_ROW2,
    _TX_HEADER_ROW3,
    _DIV_HEADER_ROW1,
    _DIV_HEADER_ROW2,
    _DIV_HEADER_ROW3,
    _BOOK_HEADER_ROW1,
    _BOOK_HEADER_ROW2,
    _BOOK_HEADER_ROW3,
    _EXP_HEADER_ROW1,
    _EXP_HEADER_ROW2,
    _EXP_HEADER_ROW3,
    _PDT_SETTINGS_VERSION,
    _PDT_SETTINGS_URL,
    _asset_type_to_pdt_type,
    _pdt_exchange,
    _tx_type_to_pdt_action,
    _float_or_none,
    _str_or_none,
    _to_date,
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Number of header rows before data starts
_N_HEADERS = 3

# Minimum column widths expected per sheet (pad sparse rows to this)
_TX_NCOLS = 21
_DIV_NCOLS = 20
_BOOK_NCOLS = 6


# ---------------------------------------------------------------------------
# Date / value helpers for Google Sheets API responses
# ---------------------------------------------------------------------------


def _serial_to_date(value: Any) -> Optional[date]:
    """Convert a Google Sheets date serial number or string to a date."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and value > 0:
        # Google Sheets / Excel serial: days since 1899-12-30
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # Try ISO string formats returned by FORMATTED_STRING render option
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
        ):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
    return _to_date(value)


def _sheets_val(value: Any) -> Any:
    """Normalise a Google Sheets cell value: empty string → None."""
    if value == "" or value is None:
        return None
    return value


def _pad_row(row: list, width: int) -> list:
    """Right-pad a row with None so it reaches *width* columns."""
    if len(row) >= width:
        return row
    return row + [None] * (width - len(row))


def _iter_data_rows(
    rows: List[list], n_headers: int = _N_HEADERS
) -> Iterator[Tuple[int, list]]:
    """Yield (1-based row number, values list) for data rows."""
    for row_idx, row in enumerate(rows, start=1):
        if row_idx <= n_headers:
            continue
        if all(v is None or v == "" for v in row):
            continue
        yield row_idx, row


# ---------------------------------------------------------------------------
# Value formatter for writing back to Sheets
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> Any:
    """Prepare a Python value for USER_ENTERED write to Google Sheets."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return value


def _row_to_sheets(row: list) -> list:
    return [_fmt(v) for v in row]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _build_service(service_account_file: str):
    """Return an authenticated Google Sheets service object."""
    if not os.path.exists(service_account_file):
        raise FileNotFoundError(
            f"Service account file not found: {service_account_file}"
        )
    creds = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


# ---------------------------------------------------------------------------
# Core sync class
# ---------------------------------------------------------------------------


class PDTSheetsSync:
    """Pull from / push to a PDT-format Google Spreadsheet."""

    def __init__(
        self,
        spreadsheet_id: str,
        service_account_file: Optional[str] = None,
    ):
        self.spreadsheet_id = spreadsheet_id
        self.service_account_file = service_account_file or os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json"
        )
        self._service = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _svc(self):
        if self._service is None:
            self._service = _build_service(self.service_account_file)
        return self._service

    def _read_sheet(self, sheet_name: str) -> List[list]:
        """Return all rows from *sheet_name* as a list of lists."""
        result = (
            self._svc()
            .spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A:{chr(ord('A') + 30)}",
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="SERIAL_NUMBER",
            )
            .execute()
        )
        return result.get("values", [])

    def _write_sheet(self, sheet_name: str, rows: List[list]) -> None:
        """Overwrite *sheet_name* with *rows* (clears the rest first)."""
        svc = self._svc()
        sheets_api = svc.spreadsheets()

        # Ensure the sheet exists; create it if not
        meta = sheets_api.get(spreadsheetId=self.spreadsheet_id).execute()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        if sheet_name not in existing:
            sheets_api.batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [{"addSheet": {"properties": {"title": sheet_name}}}]
                },
            ).execute()

        # Clear then write
        col_end = chr(ord("A") + max(len(r) for r in rows) - 1) if rows else "Z"
        range_name = f"'{sheet_name}'!A1:{col_end}{len(rows) + 10}"

        sheets_api.values().clear(
            spreadsheetId=self.spreadsheet_id, range=range_name
        ).execute()

        if rows:
            sheets_api.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [_row_to_sheets(r) for r in rows]},
            ).execute()

    # ------------------------------------------------------------------
    # Pull (Google Sheet → PDTParseResult)
    # ------------------------------------------------------------------

    def pull(self) -> PDTParseResult:
        """Read all three PDT sheets and return a PDTParseResult."""
        result = PDTParseResult()

        try:
            tx_rows = self._read_sheet("Transactions")
            self._parse_transactions(tx_rows, result)
        except Exception as e:
            result.skipped.append(("Transactions", f"Sheet read error: {e}"))

        try:
            div_rows = self._read_sheet("Dividends")
            self._parse_dividends(div_rows, result)
        except Exception as e:
            result.skipped.append(("Dividends", f"Sheet read error: {e}"))

        try:
            book_rows = self._read_sheet("Bookings")
            self._parse_bookings(book_rows, result)
        except Exception as e:
            result.skipped.append(("Bookings", f"Sheet read error: {e}"))

        return result

    def _parse_transactions(self, rows: List[list], result: PDTParseResult) -> None:
        for row_idx, raw_row in _iter_data_rows(rows):
            row = [_sheets_val(v) for v in _pad_row(raw_row, _TX_NCOLS)]
            try:
                broker = _str_or_none(row[0])
                name = _str_or_none(row[1])
                pdt_type = _str_or_none(row[2]) or "Stock market"
                search = _str_or_none(row[3]) or ""
                exchange = _str_or_none(row[4]) or ""
                tx_date = _serial_to_date(row[5])
                action = _str_or_none(row[7])
                amount = _float_or_none(row[8])
                price = _float_or_none(row[9])
                price_currency = _str_or_none(row[10])

                if not all(
                    [broker, name, tx_date, action, amount, price, price_currency]
                ):
                    result.skipped.append(
                        ("Transactions", f"Row {row_idx}: missing required fields")
                    )
                    continue

                result.transactions.append(
                    PDTTransaction(
                        broker=broker,
                        name=name,
                        pdt_type=pdt_type,
                        search=search,
                        exchange=exchange,
                        date=tx_date,
                        action=action,
                        amount=amount,
                        price=price,
                        price_currency=price_currency,
                        price_exchange_rate=_float_or_none(row[11]),
                        price_exchange_rate_currency=_str_or_none(row[12]),
                        costs=_float_or_none(row[13]),
                        costs_currency=_str_or_none(row[14]),
                        costs_exchange_rate=_float_or_none(row[15]),
                        costs_exchange_rate_currency=_str_or_none(row[16]),
                        tax=_float_or_none(row[17]),
                        tax_currency=_str_or_none(row[18]),
                        tax_exchange_rate=_float_or_none(row[19]),
                        tax_exchange_rate_currency=_str_or_none(row[20]),
                    )
                )
            except Exception as e:
                result.skipped.append(("Transactions", f"Row {row_idx}: {e}"))

    def _parse_dividends(self, rows: List[list], result: PDTParseResult) -> None:
        for row_idx, raw_row in _iter_data_rows(rows):
            row = [_sheets_val(v) for v in _pad_row(raw_row, _DIV_NCOLS)]
            try:
                broker = _str_or_none(row[0])
                name = _str_or_none(row[1])
                pdt_type = _str_or_none(row[2]) or "Stock market"
                search = _str_or_none(row[3]) or ""
                exchange = _str_or_none(row[4]) or ""
                tx_date = _serial_to_date(row[5])
                action = _str_or_none(row[7])
                amount = _float_or_none(row[8])
                amount_currency = _str_or_none(row[9])

                if not all([broker, name, tx_date, action, amount, amount_currency]):
                    result.skipped.append(
                        ("Dividends", f"Row {row_idx}: missing required fields")
                    )
                    continue

                result.dividends.append(
                    PDTDividend(
                        broker=broker,
                        name=name,
                        pdt_type=pdt_type,
                        search=search,
                        exchange=exchange,
                        date=tx_date,
                        action=action,
                        amount=amount,
                        amount_currency=amount_currency,
                        amount_exchange_rate=_float_or_none(row[10]),
                        amount_exchange_rate_currency=_str_or_none(row[11]),
                        tax=_float_or_none(row[12]),
                        tax_currency=_str_or_none(row[13]),
                        tax_exchange_rate=_float_or_none(row[14]),
                        tax_exchange_rate_currency=_str_or_none(row[15]),
                        costs=_float_or_none(row[16]),
                        costs_currency=_str_or_none(row[17]),
                        costs_exchange_rate=_float_or_none(row[18]),
                        costs_exchange_rate_currency=_str_or_none(row[19]),
                    )
                )
            except Exception as e:
                result.skipped.append(("Dividends", f"Row {row_idx}: {e}"))

    def _parse_bookings(self, rows: List[list], result: PDTParseResult) -> None:
        for row_idx, raw_row in _iter_data_rows(rows):
            row = [_sheets_val(v) for v in _pad_row(raw_row, _BOOK_NCOLS)]
            try:
                broker = _str_or_none(row[0])
                tx_date = _serial_to_date(row[1])
                action = _str_or_none(row[3])
                amount = _float_or_none(row[4])
                currency = _str_or_none(row[5])

                if not all([broker, tx_date, action, amount, currency]):
                    result.skipped.append(
                        ("Bookings", f"Row {row_idx}: missing required fields")
                    )
                    continue

                result.bookings.append(
                    PDTBooking(
                        broker=broker,
                        date=tx_date,
                        action=action,
                        amount=amount,
                        currency=currency,
                    )
                )
            except Exception as e:
                result.skipped.append(("Bookings", f"Row {row_idx}: {e}"))

    # ------------------------------------------------------------------
    # Push (DB → Google Sheet)
    # ------------------------------------------------------------------

    def push(self, db_adapter: Any, portfolio_id: Optional[int] = None) -> dict:
        """Write all portfolio data to the PDT-format Google Spreadsheet."""
        tx_rows = self._build_transactions_rows(db_adapter, portfolio_id)
        div_rows = self._build_dividends_rows(db_adapter, portfolio_id)
        book_rows = self._build_bookings_rows(db_adapter, portfolio_id)

        self._write_sheet("Transactions", tx_rows)
        self._write_sheet("Dividends", div_rows)
        self._write_sheet("Bookings", book_rows)
        self._write_sheet(
            "Expenses", [_EXP_HEADER_ROW1, _EXP_HEADER_ROW2, _EXP_HEADER_ROW3]
        )
        self._write_sheet(
            "Settings", [["Version", "URL"], [_PDT_SETTINGS_VERSION, _PDT_SETTINGS_URL]]
        )

        return {
            "transactions": len(tx_rows) - _N_HEADERS,
            "dividends": len(div_rows) - _N_HEADERS,
            "bookings": len(book_rows) - _N_HEADERS,
        }

    def _get_transactions(self, db_adapter: Any, portfolio_id: Optional[int]) -> list:
        if portfolio_id is not None:
            return db_adapter.get_transactions_by_portfolio(portfolio_id)
        return db_adapter.get_all_transactions()

    def _resolve_broker(self, db_adapter: Any, tx: dict) -> str:
        pid = tx.get("portfolio_id")
        if pid:
            p = db_adapter.get_portfolio(pid)
            if p:
                return p.get("name", "")
        return ""

    def _parse_tx_date(self, tx: dict) -> Optional[date]:
        v = tx.get("transaction_date")
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d").date()
            except ValueError:
                return None
        return v

    def _build_transactions_rows(
        self, db_adapter: Any, portfolio_id: Optional[int]
    ) -> list:
        rows = [_TX_HEADER_ROW1, _TX_HEADER_ROW2, _TX_HEADER_ROW3]
        for tx in self._get_transactions(db_adapter, portfolio_id):
            pdt_action = _tx_type_to_pdt_action(tx.get("transaction_type", ""))
            if pdt_action is None:
                continue
            asset = db_adapter.get_asset(tx["asset_id"])
            if not asset:
                continue
            broker = self._resolve_broker(db_adapter, tx)
            asset_type = asset.get("asset_type", "stock")
            pdt_type = _asset_type_to_pdt_type(asset_type)
            tx_date = self._parse_tx_date(tx)
            fees = tx.get("fees") or 0
            tax = tx.get("tax") or 0
            currency = tx.get("currency") or asset.get("currency", "EUR")
            exchange = _pdt_exchange(asset.get("exchange"), asset_type)
            rows.append(
                [
                    broker,
                    asset.get("name", ""),
                    pdt_type,
                    asset.get("symbol", ""),
                    exchange,
                    tx_date,
                    time(0, 0),
                    pdt_action,
                    tx.get("quantity"),
                    tx.get("price"),
                    currency,
                    None,
                    None,
                    fees if fees else None,
                    currency if fees else None,
                    None,
                    None,
                    tax if tax else None,
                    currency if tax else None,
                    None,
                    None,
                ]
            )
        return rows

    def _build_dividends_rows(
        self, db_adapter: Any, portfolio_id: Optional[int]
    ) -> list:
        rows = [_DIV_HEADER_ROW1, _DIV_HEADER_ROW2, _DIV_HEADER_ROW3]
        for tx in self._get_transactions(db_adapter, portfolio_id):
            if tx.get("transaction_type") != "dividend":
                continue
            asset = db_adapter.get_asset(tx["asset_id"])
            if not asset:
                continue
            broker = self._resolve_broker(db_adapter, tx)
            asset_type = asset.get("asset_type", "stock")
            pdt_type = _asset_type_to_pdt_type(asset_type)
            tx_date = self._parse_tx_date(tx)
            currency = tx.get("currency") or asset.get("currency", "EUR")
            dividend_amount = tx.get("total_amount") or (
                (tx.get("price") or 0) * (tx.get("quantity") or 1)
            )
            tax = tx.get("tax") or 0
            exchange = _pdt_exchange(asset.get("exchange"), asset_type)
            rows.append(
                [
                    broker,
                    asset.get("name", ""),
                    pdt_type,
                    asset.get("symbol", ""),
                    exchange,
                    tx_date,
                    time(10, 0),
                    "Cash",
                    dividend_amount,
                    currency,
                    None,
                    None,
                    tax if tax else None,
                    currency if tax else None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
        return rows

    def _build_bookings_rows(
        self, db_adapter: Any, portfolio_id: Optional[int]
    ) -> list:
        rows = [_BOOK_HEADER_ROW1, _BOOK_HEADER_ROW2, _BOOK_HEADER_ROW3]
        try:
            bookings = db_adapter.get_all_bookings(portfolio_id)
        except Exception:
            bookings = []
        for bk in bookings:
            broker = ""
            pid = bk.get("portfolio_id")
            if pid:
                p = db_adapter.get_portfolio(pid)
                if p:
                    broker = p.get("name", "")
            bk_date = bk.get("date")
            if isinstance(bk_date, str):
                try:
                    bk_date = datetime.strptime(bk_date, "%Y-%m-%d").date()
                except ValueError:
                    pass
            rows.append(
                [
                    broker,
                    bk_date,
                    time(9, 0),
                    bk.get("action", "Deposit"),
                    bk.get("amount"),
                    bk.get("currency", "EUR"),
                ]
            )
        return rows


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def pull_from_sheets(
    spreadsheet_id: str,
    service_account_file: Optional[str] = None,
) -> PDTParseResult:
    """Pull PDT data from a Google Spreadsheet."""
    return PDTSheetsSync(spreadsheet_id, service_account_file).pull()


def push_to_sheets(
    db_adapter: Any,
    spreadsheet_id: str,
    service_account_file: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> dict:
    """Push portfolio data to a PDT-format Google Spreadsheet."""
    return PDTSheetsSync(spreadsheet_id, service_account_file).push(
        db_adapter, portfolio_id
    )
