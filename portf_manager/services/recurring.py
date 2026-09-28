"""Recurring-charge (subscription) detection over bank spending rows.

Groups outflows by account + merchant, keeps groups whose charges arrive at
a steady cadence for a steady amount, and says when the next one is due and
whether it failed to arrive. A charge only counts as missed once a statement
covering its due date has been imported — a missing statement is not a
cancelled subscription.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Dict, List, Optional

# (name, typical gap in days, tolerance in days, charges per year)
CADENCES = (
    ("weekly", 7.0, 2.0, 52),
    ("monthly", 30.44, 5.0, 12),
    ("quarterly", 91.3, 10.0, 4),
    ("yearly", 365.25, 20.0, 1),
)
_MONTHS_AHEAD = {"monthly": 1, "quarterly": 3, "yearly": 12}
AMOUNT_TOLERANCE = 0.25
REGULAR_SHARE = 0.75
PRICE_CHANGE_MIN_PCT = 5.0
PRICE_FLAT_TOLERANCE = 0.02
_STATUS_ORDER = {"missed": 0, "active": 1, "ended": 2}


@dataclass
class RecurringSeries:
    merchant: str
    portfolio_id: int
    currency: str
    category: str
    cadence: str
    occurrences: int
    first_date: str
    last_date: str
    last_amount: float
    typical_amount: float
    annual_amount: float
    next_expected: str
    status: str
    price_change_pct: Optional[float]
    transaction_ids: List[int]


def _add_months(day: date, months: int) -> date:
    """Same day-of-month ``months`` later, clamped to that month's end."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _advance(day: date, cadence: str) -> date:
    if cadence == "weekly":
        return day + timedelta(days=7)
    return _add_months(day, _MONTHS_AHEAD[cadence])


def _classify(gaps: List[int]) -> Optional[tuple]:
    """The cadence tuple the gaps fit, or None."""
    typical_gap = median(gaps)
    for cadence in CADENCES:
        _, days, tol, _ = cadence
        if abs(typical_gap - days) > tol:
            continue
        regular = sum(1 for g in gaps if abs(g - days) <= 2 * tol)
        if regular / len(gaps) >= REGULAR_SHARE:
            return cadence
    return None


def _price_change_pct(amounts: List[float]) -> Optional[float]:
    """Percent change of the last charge versus the one before it.

    Only reported for a flat series: at least two charges before the last
    one, all within ``PRICE_FLAT_TOLERANCE`` of their own median. A bill that
    varies (utilities) has no meaningful "price rise".

    Args:
        amounts: Charge amounts in date order (positive).

    Returns:
        The rounded percent change when it is at least
        ``PRICE_CHANGE_MIN_PCT`` in size, else None.
    """
    prior = amounts[:-1]
    if len(prior) < 2:
        return None
    prior_median = median(prior)
    if any(abs(a - prior_median) > PRICE_FLAT_TOLERANCE * prior_median for a in prior):
        return None
    # A sub-cent charge rounds to 0.00; there is no percentage to report.
    if amounts[-2] == 0:
        return None
    change = (amounts[-1] - amounts[-2]) / amounts[-2] * 100
    if abs(change) < PRICE_CHANGE_MIN_PCT:
        return None
    return round(change, 1)


def _status(
    next_expected: date, cadence: str, tol: float, last_import: Optional[date]
) -> str:
    grace = timedelta(days=tol)
    if last_import is None or last_import <= next_expected + grace:
        return "active"
    if last_import <= _advance(next_expected, cadence) + grace:
        return "missed"
    return "ended"


def detect_recurring(
    rows: List[dict],
    last_import_by_portfolio: Dict[int, date],
    min_occurrences: int = 3,
) -> List[RecurringSeries]:
    """Find steady recurring outflows.

    Args:
        rows: spending_transactions rows (dicts with id, portfolio_id, date,
            amount, currency, merchant, description, category, is_transfer).
        last_import_by_portfolio: Latest imported spending date per account.
        min_occurrences: Charges needed for a non-yearly cadence.

    Returns:
        Series sorted missed -> active -> ended, then by annual amount.
    """
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        if row["amount"] >= 0 or row.get("is_transfer"):
            continue
        name = (row.get("merchant") or row.get("description") or "").strip()
        if not name:
            continue
        currency = (row.get("currency") or "EUR").upper()
        groups[(row["portfolio_id"], name.upper(), currency)].append(row)

    found: List[RecurringSeries] = []
    for (portfolio_id, _, currency), items in groups.items():
        items.sort(key=lambda r: (str(r["date"])[:10], r["id"]))
        # One charge per day: [day, total amount, ids, latest row]
        charges: List[list] = []
        for r in items:
            day = date.fromisoformat(str(r["date"])[:10])
            if charges and charges[-1][0] == day:
                charges[-1][1] += -r["amount"]
                charges[-1][2].append(r["id"])
                charges[-1][3] = r
            else:
                charges.append([day, -r["amount"], [r["id"]], r])
        if len(charges) < 2:
            continue
        gaps = [(b[0] - a[0]).days for a, b in zip(charges, charges[1:])]
        cadence = _classify(gaps)
        if cadence is None:
            continue
        name, _, tol, per_year = cadence
        if name != "yearly" and len(charges) < min_occurrences:
            continue
        amounts = [round(c[1], 2) for c in charges]
        typical = median(amounts)
        steady = sum(
            1 for a in amounts if abs(a - typical) <= AMOUNT_TOLERANCE * typical
        )
        if steady / len(amounts) < REGULAR_SHARE:
            continue
        last_day = charges[-1][0]
        next_expected = _advance(last_day, name)
        latest = charges[-1][3]
        found.append(
            RecurringSeries(
                merchant=(latest.get("merchant") or latest.get("description")).strip(),
                portfolio_id=portfolio_id,
                currency=currency,
                category=latest.get("category") or "uncategorized",
                cadence=name,
                occurrences=len(charges),
                first_date=charges[0][0].isoformat(),
                last_date=last_day.isoformat(),
                last_amount=amounts[-1],
                typical_amount=round(typical, 2),
                annual_amount=round(typical * per_year, 2),
                next_expected=next_expected.isoformat(),
                status=_status(
                    next_expected,
                    name,
                    tol,
                    last_import_by_portfolio.get(portfolio_id),
                ),
                price_change_pct=_price_change_pct(amounts),
                transaction_ids=[i for c in charges for i in c[2]],
            )
        )
    found.sort(key=lambda s: (_STATUS_ORDER[s.status], -s.annual_amount))
    return found


def load_recurring(db, portfolio_id: Optional[int] = None) -> List[RecurringSeries]:
    """detect_recurring over the database's bank outflows."""
    rows = db.list_spending_transactions(
        portfolio_id=portfolio_id, is_transfer=False, amount_sign="negative"
    )
    last_import = {}
    for pid, dates in db.get_portfolio_date_ranges().items():
        last = (dates or {}).get("last_spending_date")
        if last:
            last_import[pid] = date.fromisoformat(str(last)[:10])
    return detect_recurring(rows, last_import)
