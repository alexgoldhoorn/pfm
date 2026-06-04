"""
Mintos P2P account-statement parser.

A Mintos statement is tens of thousands of micro-rows (cents of interest per
loan, principal repayments, reinvestments, secondary-market trades). Importing
them individually is pointless — what matters for tracking is the **interest
income** (taxable in the Spanish savings base) and the **withholding tax**
already paid. So we keep only the interest-type rows and the withholding rows,
and aggregate them by month into a handful of ``interest`` transactions.

Columns (comma-delimited, dot decimals):
    Fecha, Identificación de la operación:, Detalles, Volumen de negocios,
    Saldo, Divisa, Tipo de pago

Classification on ``Tipo de pago``:
  - contains "interes"  → interest income (summed per month)
  - contains "retenci"  → tax withheld (summed per month, stored as tax)
  - everything else (capital/inversión/secondary market/fees) → ignored, but
    counted in ``ignored_summary`` for transparency.
"""

import csv
from dataclasses import dataclass, field
from io import StringIO
from typing import Dict, List, Tuple

# The synthetic asset the aggregated P2P interest is booked against.
MINTOS_SYMBOL = "MINTOS"
MINTOS_NAME = "Mintos P2P"


@dataclass
class MintosParseResult:
    # one dict per month: {date, amount, tax, count, currency}
    interest: List[dict] = field(default_factory=list)
    # {payment_type: (row_count, summed_eur)} for the rows we skipped
    ignored_summary: Dict[str, Tuple[int, float]] = field(default_factory=dict)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _num(raw: str) -> float:
    try:
        return float((raw or "").strip())
    except ValueError:
        return 0.0


def parse_mintos_csv(csv_content: str) -> MintosParseResult:
    """Aggregate a Mintos statement into monthly interest-income entries."""
    res = MintosParseResult()
    reader = csv.DictReader(StringIO(csv_content.strip()))
    # month -> [interest_sum, withholding_sum, row_count, last_date, currency]
    months: Dict[str, list] = {}
    ignored: Dict[str, list] = {}

    for row in reader:
        ptype = (row.get("Tipo de pago") or "").strip()
        date = (row.get("Fecha") or "").strip()[:10]
        amt = _num(row.get("Volumen de negocios"))
        cur = (row.get("Divisa") or "EUR").strip() or "EUR"
        low = ptype.lower()
        month = date[:7]  # YYYY-MM

        if "retenci" in low:  # withholding tax (negative)
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

    res.ignored_summary = {k: (v[0], round(v[1], 2)) for k, v in ignored.items()}
    return res
