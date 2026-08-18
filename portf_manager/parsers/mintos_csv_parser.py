"""
Mintos P2P account-statement parser.

A Mintos statement is tens of thousands of micro-rows (cents of interest per
loan, principal repayments, reinvestments, secondary-market trades). Importing
them individually is pointless — what matters for tracking is:

  - **interest income** (taxable in the Spanish savings base) and the
    **withholding tax** already paid — kept, aggregated by month.
  - **P2P loan principal moving in/out** (new investment, natural repayment,
    buyback-guarantee repayment, secondary-market trading) — the buy side
    (new investment + secondary-market purchases) and sell side (repayments,
    buybacks, secondary-market sales) are each summed separately per month
    and booked as a buy/sell against the synthetic MINTOS position (kept
    separate, not netted against each other, same treatment as ordinary
    buy/sell activity elsewhere). This position is modelled at a fixed price
    of 1.0 (1 unit = €1 of principal outstanding) since P2P loans have no
    market price — Mintos itself values them at par on its own dashboard,
    only the separate "return" figure carries the interest earned.
  - **Mintos's ETF/Bonds sleeves** (newer Mintos features, distinct from
    core P2P lending): bond transfers carry their own ISIN in ``Detalles``
    and are tracked as real buy/interest transactions against that ISIN.
    The ETF sleeve funding ("Pago de la cartera ETF saliente") carries no
    per-fund ISIN in this export at all — it's recorded as a withdrawal
    booking, same as before; attributing it to specific ETF ISINs needs a
    one-off manual allocation (see the MyInvestor-style reconciliation
    playbook in CLAUDE.md), not something a stateless parser can do.

Columns (comma-delimited, dot decimals):
    Fecha, Identificación de la operación:, Detalles, Volumen de negocios,
    Saldo, Divisa, Tipo de pago

Classification on ``Tipo de pago`` (checked in this order):
  - "Mintos Core fee"                        → withdrawal booking
  - "Pago de la cartera ETF saliente"        → withdrawal booking (see above)
  - "Transferencia a inversiones en bonos"   → bond buy (ISIN from Detalles)
  - "Transferencia desde inversiones en bonos" → bond interest/coupon (ISIN from Detalles)
  - contains "retenci"                       → tax withheld (summed per month)
  - contains "interes"                       → interest income (summed per month;
    covers "Intereses recibidos", buyback interest, delayed/pending interest —
    they all contain this substring)
  - deposit/withdrawal keywords              → booking (unchanged)
  - "Inversión" / "Capital recibido" / buyback principal / secondary-market
    → P2P principal moving in/out, summed separately per month into a MINTOS
    buy (invested) and/or sell (returned) — not netted against each other
  - everything else                          → ignored, counted in
    ``ignored_summary`` for transparency
"""

import csv
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import Dict, List, Tuple

# The synthetic asset the aggregated P2P principal/interest is booked against.
MINTOS_SYMBOL = "MINTOS"
MINTOS_NAME = "Mintos P2P"

_ISIN_RE = re.compile(r"ISIN:\s*([A-Z0-9]{12})")

_PRINCIPAL_BUY_TYPES = {"inversión", "inversion"}
_PRINCIPAL_SELL_TYPES = {
    "capital recibido",
    "ingresos del principal recibidos por la recompra del préstamo",
}
_SECONDARY_MARKET_TYPE = "operación del mercado secundario"


@dataclass
class MintosParseResult:
    # one dict per month: {date, amount, tax, count, currency}
    interest: List[dict] = field(default_factory=list)
    # one dict per month: {date, buy_amount, sell_amount, currency} — P2P
    # principal moving in/out (new investment, repayments, buybacks,
    # secondary-market), buy/sell summed separately (not netted), at
    # price=1.0 per unit.
    principal: List[dict] = field(default_factory=list)
    # one dict per row: {date, isin, amount, currency} — Mintos Bonds sleeve
    bond_buys: List[dict] = field(default_factory=list)
    bond_income: List[dict] = field(default_factory=list)
    # one dict per cash deposit/withdrawal: {date, action, amount, currency}
    bookings: List[dict] = field(default_factory=list)
    # {payment_type: (row_count, summed_eur)} for the rows we skipped
    ignored_summary: Dict[str, Tuple[int, float]] = field(default_factory=dict)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _num(raw: str) -> float:
    try:
        return float((raw or "").strip())
    except ValueError:
        return 0.0


def parse_mintos_csv(csv_content: str) -> MintosParseResult:
    """Aggregate a Mintos statement into monthly interest/principal entries."""
    res = MintosParseResult()
    reader = csv.DictReader(StringIO(csv_content.strip()))
    # month -> [interest_sum, withholding_sum, row_count, last_date, currency]
    months: Dict[str, list] = {}
    # month -> [buy_sum, sell_sum, last_date, currency]
    principal_months: Dict[str, list] = {}
    ignored: Dict[str, list] = {}

    for row in reader:
        ptype = (row.get("Tipo de pago") or "").strip()
        date = (row.get("Fecha") or "").strip()[:10]
        amt = _num(row.get("Volumen de negocios"))
        cur = (row.get("Divisa") or "EUR").strip() or "EUR"
        details = (row.get("Detalles") or "").strip()
        low = ptype.lower()
        month = date[:7]  # YYYY-MM

        if low == "mintos core fee":
            res.bookings.append(
                {
                    "date": date,
                    "action": "Withdrawal",
                    "amount": abs(amt),
                    "currency": cur,
                }
            )
        elif low == "pago de la cartera etf saliente":
            # No per-fund ISIN in this export — see module docstring.
            res.bookings.append(
                {
                    "date": date,
                    "action": "Withdrawal",
                    "amount": abs(amt),
                    "currency": cur,
                }
            )
        elif low == "transferencia a inversiones en bonos":
            m = _ISIN_RE.search(details)
            if m:
                res.bond_buys.append(
                    {
                        "date": date,
                        "isin": m.group(1),
                        "amount": abs(amt),
                        "currency": cur,
                    }
                )
            else:
                res.skipped.append(
                    (f"{date} bond transfer", f"no ISIN found: {details}")
                )
        elif low == "transferencia desde inversiones en bonos":
            m = _ISIN_RE.search(details)
            if m:
                res.bond_income.append(
                    {
                        "date": date,
                        "isin": m.group(1),
                        "amount": abs(amt),
                        "currency": cur,
                    }
                )
            else:
                res.skipped.append(
                    (f"{date} bond transfer", f"no ISIN found: {details}")
                )
        elif "retenci" in low:  # withholding tax (negative)
            m = months.setdefault(month, [0.0, 0.0, 0, date, cur])
            m[1] += abs(amt)
            m[2] += 1
            if date > m[3]:
                m[3] = date
        elif "interes" in low:  # interest income
            m = months.setdefault(month, [0.0, 0.0, 0, date, cur])
            m[0] += amt
            m[2] += 1
            if date > m[3]:
                m[3] = date
        elif any(k in low for k in ("depósit", "deposit", "incoming client")):
            res.bookings.append(
                {"date": date, "action": "Deposit", "amount": abs(amt), "currency": cur}
            )
        elif any(k in low for k in ("retirada", "withdrawal", "outgoing", "saliente")):
            res.bookings.append(
                {
                    "date": date,
                    "action": "Withdrawal",
                    "amount": abs(amt),
                    "currency": cur,
                }
            )
        elif low in _PRINCIPAL_BUY_TYPES:
            p = principal_months.setdefault(month, [0.0, 0.0, date, cur])
            p[0] += abs(amt)
            if date > p[2]:
                p[2] = date
        elif low in _PRINCIPAL_SELL_TYPES:
            p = principal_months.setdefault(month, [0.0, 0.0, date, cur])
            p[1] += abs(amt)
            if date > p[2]:
                p[2] = date
        elif low == _SECONDARY_MARKET_TYPE:
            p = principal_months.setdefault(month, [0.0, 0.0, date, cur])
            if amt < 0:
                p[0] += abs(amt)
            else:
                p[1] += amt
            if date > p[2]:
                p[2] = date
        else:
            agg = ignored.setdefault(ptype or "(unknown)", [0, 0.0])
            agg[0] += 1
            agg[1] += amt

    for month, (interest, tax, count, last_date, cur) in sorted(months.items()):
        if interest <= 0:
            continue
        res.interest.append(
            {
                "date": last_date or f"{month}-28",
                "amount": round(interest, 2),
                "tax": round(tax, 2),
                "count": count,
                "currency": cur,
            }
        )

    for month, (buy, sell, last_date, cur) in sorted(principal_months.items()):
        if round(buy, 2) <= 0 and round(sell, 2) <= 0:
            continue
        res.principal.append(
            {
                "date": last_date or f"{month}-28",
                "buy_amount": round(buy, 2),
                "sell_amount": round(sell, 2),
                "currency": cur,
            }
        )

    res.ignored_summary = {k: (v[0], round(v[1], 2)) for k, v in ignored.items()}
    return res
