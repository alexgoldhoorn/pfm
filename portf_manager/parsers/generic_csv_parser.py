"""
Generic CSV transaction parser for unsupported brokers.

Canonical column layout (order doesn't matter; headers are case-insensitive):
  date, symbol, name, type, quantity, price, currency, fees, asset_type, notes

All columns except date/symbol/type/quantity/price/currency are optional.

Template::

    date,symbol,name,type,quantity,price,currency,fees,asset_type,notes
    2024-01-15,AAPL,Apple Inc,buy,10,185.50,USD,1.00,stock,
    2024-01-20,BTC-EUR,Bitcoin,buy,0.5,40000,EUR,0,crypto,
    2024-02-01,AAPL,Apple Inc,dividend,0.24,1,USD,0,stock,Q1 dividend

Delimiter (',' or ';') and decimal style (European vs US) are auto-detected.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from ..llm_types import LLMTransaction

_HEADER_SYNONYMS: dict[str, set[str]] = {
    "date": {
        "date",
        "fecha",
        "datum",
        "trade_date",
        "tradedate",
        "value_date",
        "valuedate",
        "transaction_date",
        "transactiondate",
        "settlement_date",
        "settlementdate",
    },
    "symbol": {
        "symbol",
        "ticker",
        "isin",
        "código",
        "codigo",
        "code",
        "asset",
        "security",
        "instrumento",
    },
    "name": {
        "name",
        "nombre",
        "description",
        "asset_name",
        "assetname",
        "descripcion",
        "descripción",
        "title",
    },
    "type": {
        "type",
        "tipo",
        "action",
        "transaction_type",
        "transactiontype",
        "kind",
        "tx_type",
        "txtype",
        "operacion",
        "operación",
    },
    "quantity": {
        "quantity",
        "qty",
        "shares",
        "units",
        "amount",
        "cantidad",
        "participaciones",
        "num_shares",
        "numshares",
    },
    "price": {
        "price",
        "precio",
        "unit_price",
        "unitprice",
        "price_per_share",
        "pricepershare",
        "nav",
    },
    "currency": {
        "currency",
        "divisa",
        "moneda",
        "ccy",
        "valuta",
    },
    "fees": {
        "fees",
        "fee",
        "commission",
        "comisión",
        "comision",
        "costs",
        "cost",
        "charges",
        "gastos",
    },
    "asset_type": {
        "asset_type",
        "assettype",
        "type_of_asset",
        "typeofasset",
        "instrument_type",
        "instrumenttype",
        "clase",
        "class",
    },
    "notes": {
        "notes",
        "notas",
        "memo",
        "remarks",
        "comment",
        "comments",
        "observations",
        "observaciones",
    },
}

_TYPE_MAP: dict[str, str] = {
    "buy": "buy",
    "compra": "buy",
    "purchase": "buy",
    "suscripcion": "buy",
    "suscripción": "buy",
    "subscribe": "buy",
    "subscription": "buy",
    "sell": "sell",
    "venta": "sell",
    "sale": "sell",
    "reembolso": "sell",
    "redeem": "sell",
    "redemption": "sell",
    "dividend": "dividend",
    "dividendo": "dividend",
    "divid": "dividend",
    "interest": "interest",
    "interés": "interest",
    "interes": "interest",
    "coupon": "interest",
    "cupón": "interest",
    "cupon": "interest",
}

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%Y/%m/%d",
    "%d.%m.%Y",
]


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _resolve_header(raw: str) -> Optional[str]:
    n = _norm_header(raw)
    for canonical, synonyms in _HEADER_SYNONYMS.items():
        if n in {_norm_header(s) for s in synonyms}:
            return canonical
    return None


def _detect_delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text.strip() else ""
    return ";" if first_line.count(";") >= first_line.count(",") else ","


def _parse_number(s: str) -> float:
    """Parse European ('1.234,56') or US ('1,234.56') formatted numbers."""
    s = s.strip().replace(" ", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Could be decimal comma (European) or thousands separator
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    return float(s)


def _parse_date(s: str) -> str:
    """Return ISO date string or raise ValueError."""
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {s!r}")


@dataclass
class GenericCSVParseResult:
    importable: List[LLMTransaction] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def parse_generic_csv(content: str) -> GenericCSVParseResult:
    """Parse a generic broker CSV into LLMTransaction objects.

    Args:
        content: Raw CSV text.

    Returns:
        GenericCSVParseResult with importable and skipped rows.
    """
    result = GenericCSVParseResult()
    delimiter = _detect_delimiter(content)

    reader = csv.reader(io.StringIO(content.strip()), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        result.skipped.append(("file", "Empty file"))
        return result

    # Resolve header row
    raw_headers = rows[0]
    col_map: dict[str, int] = {}
    for i, h in enumerate(raw_headers):
        canonical = _resolve_header(h)
        if canonical and canonical not in col_map:
            col_map[canonical] = i

    required = {"date", "symbol", "type", "quantity", "price", "currency"}
    missing = required - col_map.keys()
    if missing:
        result.skipped.append(
            ("header", f"Missing required columns: {', '.join(sorted(missing))}")
        )
        return result

    def _get(row: list[str], col: str, default: str = "") -> str:
        idx = col_map.get(col)
        if idx is None or idx >= len(row):
            return default
        return row[idx].strip()

    for row_num, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # skip blank lines
        raw = delimiter.join(row)
        try:
            date_str = _parse_date(_get(row, "date"))
        except ValueError as e:
            result.skipped.append((f"row {row_num}", f"Date error: {e}"))
            continue

        symbol = _get(row, "symbol").upper()
        if not symbol:
            result.skipped.append((f"row {row_num}", "Empty symbol"))
            continue

        raw_type = _get(row, "type").strip().lower()
        tx_type = _TYPE_MAP.get(raw_type)
        if not tx_type:
            result.skipped.append(
                (f"row {row_num}", f"Unknown transaction type: {raw_type!r}")
            )
            continue

        try:
            quantity = _parse_number(_get(row, "quantity"))
        except ValueError:
            result.skipped.append(
                (f"row {row_num}", f"Invalid quantity: {_get(row, 'quantity')!r}")
            )
            continue
        if quantity <= 0:
            result.skipped.append((f"row {row_num}", "Quantity must be > 0"))
            continue

        try:
            price = _parse_number(_get(row, "price"))
        except ValueError:
            result.skipped.append(
                (f"row {row_num}", f"Invalid price: {_get(row, 'price')!r}")
            )
            continue
        if price <= 0:
            result.skipped.append((f"row {row_num}", "Price must be > 0"))
            continue

        currency = _get(row, "currency", "EUR").upper() or "EUR"
        fees_raw = _get(row, "fees", "0")
        try:
            fees = _parse_number(fees_raw) if fees_raw else 0.0
        except ValueError:
            fees = 0.0

        name = _get(row, "name") or symbol
        notes = _get(row, "notes")
        raw_with_notes = f"{raw} | {notes}" if notes else raw

        result.importable.append(
            LLMTransaction(
                tx_type=tx_type,
                symbol=symbol,
                asset_name=name,
                quantity=quantity,
                price=price,
                date=date_str,
                currency=currency,
                fees=fees,
                raw_text=raw_with_notes,
            )
        )

    return result
