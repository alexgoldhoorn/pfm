"""
Generic bank-statement CSV parser for spending tracking.

Canonical column layout (order doesn't matter; headers are case-insensitive):
  date, description, amount, balance, currency

Only date/description/amount are required. balance/currency are optional
(currency defaults to EUR).

Template::

    date,description,amount,currency
    2026-01-05,MERCADONA COMPRA,-24.50,EUR
    2026-01-06,NOMINA EMPRESA SL,2100.00,EUR
    2026-01-10,TRASPASO A AHORRO,-500.00,EUR

amount is signed: negative = money out, positive = money in (bank-statement
convention — NOT the bookings table's Deposit/Withdrawal-as-text convention).

Delimiter and EU/US date/decimal style are auto-detected by reusing the
detection helpers already in generic_csv_parser.py rather than duplicating
them (they are plain module-level functions there, safely importable).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .generic_csv_parser import (
    _DATE_FORMATS_EU,
    _DATE_FORMATS_US,
    _detect_decimal_style,
    _detect_delimiter,
    _detect_slash_date_style,
    _parse_date,
    _parse_number,
)

_HEADER_SYNONYMS: dict[str, set[str]] = {
    "date": {
        "date",
        "fecha",
        "datum",
        "value_date",
        "valuedate",
        "transaction_date",
        "transactiondate",
        "booking_date",
        "bookingdate",
    },
    "description": {
        "description",
        "descripcion",
        "descripción",
        "concepto",
        "concept",
        "omschrijving",
        "detail",
        "details",
        "memo",
        "movimiento",
    },
    "amount": {
        "amount",
        "importe",
        "bedrag",
        "value",
        "monto",
        "cantidad",
    },
    "balance": {
        "balance",
        "saldo",
        "running_balance",
        "runningbalance",
    },
    "currency": {
        "currency",
        "divisa",
        "moneda",
        "ccy",
        "valuta",
    },
}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _resolve_header(raw: str) -> Optional[str]:
    n = _norm_header(raw)
    for canonical, synonyms in _HEADER_SYNONYMS.items():
        if n in {_norm_header(s) for s in synonyms}:
            return canonical
    return None


@dataclass
class SpendingRow:
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    balance: Optional[float] = None


@dataclass
class BankParseResult:
    rows: List[SpendingRow] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def parse_generic_bank_csv(content: str) -> BankParseResult:
    """Parse a generic bank-statement CSV into signed SpendingRow objects.

    Args:
        content: Raw CSV text.

    Returns:
        BankParseResult with parsed rows and skipped rows with reasons.
    """
    result = BankParseResult()
    delimiter = _detect_delimiter(content)

    reader = csv.reader(io.StringIO(content.strip()), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        result.skipped.append(("file", "Empty file"))
        return result

    raw_headers = rows[0]
    col_map: dict[str, int] = {}
    for i, h in enumerate(raw_headers):
        canonical = _resolve_header(h)
        if canonical and canonical not in col_map:
            col_map[canonical] = i

    required = {"date", "description", "amount"}
    missing = required - col_map.keys()
    if missing:
        result.skipped.append(
            ("header", f"Missing required columns: {', '.join(sorted(missing))}")
        )
        return result

    date_formats = (
        _DATE_FORMATS_US
        if _detect_slash_date_style(rows, col_map["date"]) == "us"
        else _DATE_FORMATS_EU
    )
    num_col_indices = [col_map[c] for c in ("amount", "balance") if c in col_map]
    decimal_style = _detect_decimal_style(rows, num_col_indices)

    def _get(row: list[str], col: str, default: str = "") -> str:
        idx = col_map.get(col)
        if idx is None or idx >= len(row):
            return default
        return row[idx].strip()

    for row_num, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # skip blank lines
        try:
            date_str = _parse_date(_get(row, "date"), date_formats)
        except ValueError as e:
            result.skipped.append((f"row {row_num}", f"Date error: {e}"))
            continue

        description = _get(row, "description")
        if not description:
            result.skipped.append((f"row {row_num}", "Empty description"))
            continue

        try:
            amount = _parse_number(_get(row, "amount"), decimal_style)
        except ValueError:
            result.skipped.append(
                (f"row {row_num}", f"Invalid amount: {_get(row, 'amount')!r}")
            )
            continue
        if amount == 0:
            result.skipped.append((f"row {row_num}", "Amount is zero"))
            continue

        balance_raw = _get(row, "balance")
        balance = None
        if balance_raw:
            try:
                balance = _parse_number(balance_raw, decimal_style)
            except ValueError:
                balance = None

        currency = _get(row, "currency", "EUR").upper()[:3] or "EUR"

        result.rows.append(
            SpendingRow(
                date=date_str,
                description=description,
                amount=amount,
                currency=currency,
                balance=balance,
            )
        )

    return result
