"""
Generic bookings (cash deposit / withdrawal) CSV parser.

Broker trade exports rarely include cash transfers, so this is a *generic*
format rather than broker-specific: one row per deposit/withdrawal with
flexible, case-insensitive headers.

Recognised columns (synonyms accepted):
  - date      : date | fecha | datum            (ISO, DD/MM/YYYY or MM/DD/YYYY)
  - action    : action | type | tipo | movimiento (deposit/withdrawal synonyms)
  - amount    : amount | importe | bedrag | value
  - currency  : currency | divisa | moneda        (default EUR)
  - broker    : broker | portfolio | cuenta | account (optional)

Delimiter (',' or ';') and decimal style (European "1.234,56" or "1,234.56")
are auto-detected. Returns ``BookingsParseResult`` with ``bookings`` (dicts
matching the import API's PreviewBooking) and ``skipped`` rows with reasons.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

_DEPOSIT_WORDS = {
    "deposit",
    "deposito",
    "depósito",
    "ingreso",
    "in",
    "buy",
    "fund",
    "funding",
    "storting",
}
_WITHDRAWAL_WORDS = {"withdrawal", "withdraw", "retirada", "reintegro", "out", "opname"}

_HEADER_SYNONYMS = {
    "date": {"date", "fecha", "datum", "valuedate", "value_date"},
    "action": {"action", "type", "tipo", "movimiento", "kind", "transaction"},
    "amount": {"amount", "importe", "bedrag", "value", "monto", "cantidad"},
    "currency": {"currency", "divisa", "moneda", "ccy", "valuta"},
    "broker": {"broker", "portfolio", "cuenta", "account", "platform", "rekening"},
}


@dataclass
class BookingsParseResult:
    bookings: List[dict] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _map_columns(fieldnames: List[str]) -> dict:
    """Map canonical field → actual CSV header using synonyms."""
    mapping = {}
    for actual in fieldnames or []:
        norm = _norm_header(actual)
        for canonical, syns in _HEADER_SYNONYMS.items():
            if norm in syns and canonical not in mapping:
                mapping[canonical] = actual
    return mapping


def _parse_amount(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace("€", "").replace("$", "").replace("£", "")
    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s:
        return None
    # Decide decimal separator: if both present, the last one is the decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Comma as decimal (European) when it looks like a decimal separator.
        s = s.replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return None


def _parse_date(raw: str) -> Optional[str]:
    s = (raw or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_action(raw: str) -> Optional[str]:
    w = re.sub(r"[^a-zàáéíóúñ]", "", (raw or "").strip().lower())
    if w in _DEPOSIT_WORDS:
        return "Deposit"
    if w in _WITHDRAWAL_WORDS:
        return "Withdrawal"
    return None


def parse_bookings_csv(content: str) -> BookingsParseResult:
    """Parse a generic bookings CSV into deposit/withdrawal records."""
    result = BookingsParseResult()
    text = content.lstrip("﻿")
    if not text.strip():
        return result

    # Sniff the delimiter from the header line.
    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    cols = _map_columns(reader.fieldnames or [])

    missing = [c for c in ("date", "action", "amount") if c not in cols]
    if missing:
        result.skipped.append(
            ("header", f"missing required column(s): {', '.join(missing)}")
        )
        return result

    for i, row in enumerate(reader, start=2):
        date = _parse_date(row.get(cols["date"], ""))
        action = _parse_action(row.get(cols["action"], ""))
        amount = _parse_amount(row.get(cols["amount"], ""))
        if not date or not action or not amount:
            result.skipped.append((f"row {i}", "unrecognised date / action / amount"))
            continue
        currency = (
            (row.get(cols["currency"], "") if "currency" in cols else "") or "EUR"
        ).strip().upper()[:3] or "EUR"
        broker = (
            row.get(cols["broker"], "") if "broker" in cols else ""
        ).strip() or None
        result.bookings.append(
            {
                "broker": broker,
                "date": date,
                "action": action,
                "amount": amount,
                "currency": currency,
            }
        )
    return result
