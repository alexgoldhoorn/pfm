"""
Generic fixed-deposit CSV parser.

Column synonyms (case-insensitive, punctuation-stripped):
  name         : name | nombre | deposit | product | descripcion
  principal    : principal | amount | importe | capital | nominal | value
  currency     : currency | divisa | moneda | ccy          (default EUR)
  interest_rate: interestrate | rate | tasa | tipo | tae | tin | apr | yield
  start_date   : startdate | start | fechainicio | apertura | valuedate | opendate | fecha
  maturity_date: maturitydate | maturity | vencimiento | fechavencimiento | expiry | enddate
  portfolio    : portfolio | broker | cuenta | account | platform
  notes        : notes | notas | comment | remarks | observaciones

Delimiter (',' or ';') and decimal style (European or US) are auto-detected.
Returns ``DepositsParseResult`` with ``deposits`` (dicts matching the import
API's ``PreviewDeposit``) and ``skipped`` rows with reasons.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

_HEADER_SYNONYMS = {
    "name": {
        "name",
        "nombre",
        "deposit",
        "product",
        "descripcion",
        "productname",
        "depositname",
        "denominacion",
    },
    "principal": {
        "principal",
        "amount",
        "importe",
        "capital",
        "nominal",
        "value",
        "cantidad",
        "monto",
        "saldo",
    },
    "currency": {"currency", "divisa", "moneda", "ccy", "valuta"},
    "interest_rate": {
        "interestrate",
        "rate",
        "tasa",
        "tipo",
        "tae",
        "tin",
        "apr",
        "yield",
        "interest",
        "rentabilidad",
    },
    "start_date": {
        "startdate",
        "start",
        "fechainicio",
        "apertura",
        "valuedate",
        "opendate",
        "fecha",
        "openingdate",
        "fechaapertura",
    },
    "maturity_date": {
        "maturitydate",
        "maturity",
        "vencimiento",
        "fechavencimiento",
        "expiry",
        "enddate",
        "duedate",
        "expirydate",
        "fechavence",
    },
    "portfolio": {
        "portfolio",
        "broker",
        "cuenta",
        "account",
        "platform",
        "rekening",
        "entidad",
    },
    "notes": {
        "notes",
        "notas",
        "comment",
        "remarks",
        "observaciones",
        "descripcion",
    },
}

REQUIRED_COLUMNS = ("name", "principal", "interest_rate", "start_date", "maturity_date")


@dataclass
class DepositsParseResult:
    deposits: List[dict] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _map_columns(fieldnames: List[str]) -> dict:
    mapping: dict = {}
    for actual in fieldnames or []:
        norm = _norm_header(actual)
        for canonical, syns in _HEADER_SYNONYMS.items():
            if norm in syns and canonical not in mapping:
                mapping[canonical] = actual
    return mapping


def _parse_amount(raw: str) -> Optional[float]:
    s = (raw or "").strip()
    for sym in ("€", "$", "£", "USD", "EUR", "GBP"):
        s = s.replace(sym, "")
    s = re.sub(r"[^0-9,.\-]", "", s.strip())
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return None


def _parse_rate(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace("%", "").replace(",", ".").strip()
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


def parse_deposits_csv(content: str) -> DepositsParseResult:
    """Parse a generic fixed-deposit CSV into deposit records."""
    result = DepositsParseResult()
    text = content.lstrip("﻿")
    if not text.strip():
        return result

    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    cols = _map_columns(reader.fieldnames or [])

    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        result.skipped.append(
            ("header", f"missing required column(s): {', '.join(missing)}")
        )
        return result

    for i, row in enumerate(reader, start=2):
        name = (row.get(cols["name"], "") or "").strip()
        principal = _parse_amount(row.get(cols["principal"], ""))
        interest_rate = _parse_rate(row.get(cols["interest_rate"], ""))
        start_date = _parse_date(row.get(cols["start_date"], ""))
        maturity_date = _parse_date(row.get(cols["maturity_date"], ""))

        if (
            not name
            or principal is None
            or interest_rate is None
            or not start_date
            or not maturity_date
        ):
            result.skipped.append(
                (f"row {i}", "missing or unrecognised required field(s)")
            )
            continue

        currency = (
            (row.get(cols["currency"], "") if "currency" in cols else "") or "EUR"
        ).strip().upper()[:3] or "EUR"
        portfolio = (
            (row.get(cols["portfolio"], "") if "portfolio" in cols else "") or ""
        ).strip() or None
        notes = (
            (row.get(cols["notes"], "") if "notes" in cols else "") or ""
        ).strip() or None

        result.deposits.append(
            {
                "name": name,
                "principal": principal,
                "currency": currency,
                "interest_rate": interest_rate,
                "start_date": start_date,
                "maturity_date": maturity_date,
                "broker": portfolio,
                "notes": notes,
            }
        )

    return result
