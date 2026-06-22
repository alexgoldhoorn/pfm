"""
MyInvestor CSV parser — "Movimientos Mi Cuenta" account-movements export.

Columns (semicolon-delimited, European comma decimals):
    Fecha de operación;Fecha de valor;Concepto;Importe;Divisa

The ``Concepto`` encodes the movement; we classify by pattern + amount sign:
  - ``INVEST``                       → cash deposit into the account (booking)
  - ``NAME @ QTY`` with Importe < 0  → BUY      (QTY units, cost = |Importe|)
  - ``NAME @ QTY`` with Importe > 0  → DIVIDEND (QTY = shares held; amount = payout)
  - ``NAME`` (no @), Importe > 0     → DIVIDEND (lump-sum payout or fund redemption)
  - anything else (e.g. SUSCRIPCIÓN PREMIUM, a platform fee) → skipped

NOTE: MyInvestor gives no ISIN, truncates names (~30 chars) and reports only an
EUR amount with no fee breakdown — so buy/sell rows are approximate (price =
amount/qty, currency EUR, fees 0) and won't auto-reconcile with ISIN-keyed
holdings. They're surfaced in the preview so the user decides per row.
"""

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import List, Tuple

from portf_manager.llm_types import LLMTransaction
from portf_manager.parsers.utils import parse_european_number as _num

# "NAME @ 12" or "NAME @ 12,5" → (name, quantity)
_TRADE_RE = re.compile(r"^(.*?)\s*@\s*([0-9]+(?:[.,][0-9]+)?)\s*$")
_DEPOSIT_CONCEPTS = {"INVEST", "INGRESO", "APORTACIÓN", "APORTACION"}
# Platform fees / charges (negative, no security) — not imported.
_FEE_KEYWORDS = re.compile(
    r"suscripci[oó]n premium|comisi[oó]n|comision|tarifa|fee|custodia|coste",
    re.IGNORECASE,
)


@dataclass
class MyInvestorParseResult:
    transactions: List[LLMTransaction] = field(default_factory=list)
    bookings: List[dict] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _date(raw: str) -> str:
    s = (raw or "").strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def parse_myinvestor_csv(csv_content: str) -> MyInvestorParseResult:
    """Parse a MyInvestor 'Movimientos' CSV into transactions + bookings."""
    res = MyInvestorParseResult()
    reader = csv.reader(StringIO(csv_content.strip()), delimiter=";")
    rows = list(reader)
    if not rows:
        return res

    # Skip the header row if present (first cell looks like a date label).
    start = 1 if rows and "fecha" in (rows[0][0] or "").strip().lower() else 0
    for i, row in enumerate(rows[start:], start=start + 1):
        if len(row) < 4:
            if any(c.strip() for c in row):
                res.skipped.append((f"Row {i}", f"too few columns ({len(row)})"))
            continue
        date = _date(row[0])
        concepto = (row[2] or "").strip()
        try:
            importe = _num(row[3])
        except ValueError:
            res.skipped.append((f"Row {i}", f"bad amount '{row[3]}'"))
            continue
        currency = row[4].strip() if len(row) > 4 and row[4].strip() else "EUR"
        raw = ";".join(row)

        if concepto.upper() in _DEPOSIT_CONCEPTS:
            res.bookings.append(
                {
                    "broker": "MyInvestor",
                    "date": date,
                    "action": "Deposit" if importe >= 0 else "Withdrawal",
                    "amount": abs(importe),
                    "currency": currency,
                }
            )
            continue

        m = _TRADE_RE.match(concepto)
        if m:
            name = m.group(1).strip()
            qty = abs(_num(m.group(2)))
            if qty <= 0:
                res.skipped.append((f"Row {i}", f"zero quantity: {concepto}"))
                continue
            total = abs(importe)
            res.transactions.append(
                LLMTransaction(
                    tx_type="dividend" if importe > 0 else "buy",
                    symbol=name,
                    asset_name=name,
                    quantity=qty,
                    price=round(total / qty, 6),
                    date=date,
                    currency=currency,
                    raw_text=raw,
                )
            )
            continue

        # No "@" and positive → cash dividend for that holding. (Large amounts
        # on fund names could be redemptions rather than dividends — MyInvestor
        # doesn't distinguish — so these are flagged for review downstream.)
        if importe > 0:
            res.transactions.append(
                LLMTransaction(
                    tx_type="dividend",
                    symbol=concepto,
                    asset_name=concepto,
                    quantity=1.0,
                    price=abs(importe),
                    date=date,
                    currency=currency,
                    raw_text=raw,
                )
            )
            continue

        # Negative, no "@", not a deposit. Either a platform fee, or a fund buy
        # MyInvestor recorded by amount with no unit count — can't import a
        # unit-based trade from it, so skip with an honest reason.
        if _FEE_KEYWORDS.search(concepto):
            res.skipped.append((f"Row {i}", f"fee/charge not imported: {concepto}"))
        else:
            res.skipped.append(
                (f"Row {i}", f"buy without unit detail (no '@ qty'): {concepto}")
            )

    return res
