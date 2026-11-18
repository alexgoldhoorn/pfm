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
from typing import Any, Dict, List, Optional

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
# Charges in the flat run before the latest one, the anchor included.
PRICE_FLAT_MIN_RUN = 3
# ``cancelled`` and ``paid_elsewhere`` are set only by ``load_recurring`` from a
# stored resolution, never by detection.
_STATUS_ORDER = {
    "missed": 0,
    "active": 1,
    "paid_elsewhere": 2,
    "cancelled": 3,
    "ended": 4,
}
RESOLVED_STATUSES = ("cancelled", "paid_elsewhere")
# Statuses with no future charge to place in a forecast or a pace.
_NO_FUTURE_STATUSES = ("ended",) + RESOLVED_STATUSES


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
    # ``{status, since, note}`` of the stored resolution, when there is one.
    resolution: Optional[Dict[str, Any]] = None
    # A charge arrived after the resolution's ``since``: the series kept its
    # detected status and the resolution no longer applies.
    resolution_stale: bool = False


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
    """Percent change of the last charge versus the run of charges before it.

    Flatness is judged on the run of charges since the *last* price change,
    not the whole history: walk back from the charge before the last one
    while each earlier charge is within ``PRICE_FLAT_TOLERANCE`` of it, and
    require at least ``PRICE_FLAT_MIN_RUN`` charges in that run (the anchor
    plus two earlier ones; a run of two let varying utilities raise alerts). An earlier price change (e.g.
    8 -> 10) must not blind this to a later one (10 -> 12). A bill that
    varies (utilities) has no meaningful "price rise".

    Args:
        amounts: Charge amounts in date order (positive).

    Returns:
        The rounded percent change when it is at least
        ``PRICE_CHANGE_MIN_PCT`` in size, else None.
    """
    if len(amounts) < 2:
        return None
    anchor = amounts[-2]
    run_length = 1
    for a in reversed(amounts[:-2]):
        if anchor == 0 or abs(a - anchor) > PRICE_FLAT_TOLERANCE * anchor:
            break
        run_length += 1
    if run_length < PRICE_FLAT_MIN_RUN:
        return None
    # A sub-cent charge rounds to 0.00; there is no percentage to report.
    if anchor == 0:
        return None
    change = (amounts[-1] - anchor) / anchor * 100
    if abs(change) < PRICE_CHANGE_MIN_PCT:
        return None
    return round(change, 1)


def _second_due(last_day: date, cadence: str) -> date:
    """The due date two cycles after the last charge.

    Computed straight from the last charge, not by advancing the
    already-clamped ``next_expected`` a second time: chaining two clamped
    month-adds from a month-end charge can land a few days short (e.g. a
    monthly charge on Jan 31 clamps to Feb 28, and advancing *that* a month
    lands on Mar 28 rather than the correct Mar 31).
    """
    if cadence == "weekly":
        return last_day + timedelta(days=14)
    return _add_months(last_day, 2 * _MONTHS_AHEAD[cadence])


def _status(
    next_expected: date,
    cadence: str,
    tol: float,
    last_import: Optional[date],
    last_day: date,
) -> str:
    grace = timedelta(days=tol)
    if last_import is None or last_import <= next_expected + grace:
        return "active"
    if last_import <= _second_due(last_day, cadence) + grace:
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
        typical_amount = round(typical, 2)
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
                typical_amount=typical_amount,
                annual_amount=round(typical_amount * per_year, 2),
                next_expected=next_expected.isoformat(),
                status=_status(
                    next_expected,
                    name,
                    tol,
                    last_import_by_portfolio.get(portfolio_id),
                    last_day,
                ),
                price_change_pct=_price_change_pct(amounts),
                transaction_ids=[i for c in charges for i in c[2]],
            )
        )
    found.sort(key=lambda s: (_STATUS_ORDER[s.status], -s.annual_amount))
    return found


def last_import_dates(db: Any) -> Dict[int, date]:
    """Latest imported spending date per account (transfers count)."""
    last_import: Dict[int, date] = {}
    for pid, dates in db.get_portfolio_date_ranges().items():
        last = (dates or {}).get("last_spending_date")
        if last:
            last_import[pid] = date.fromisoformat(str(last)[:10])
    return last_import


def load_recurring(
    db: Any, portfolio_id: Optional[int] = None
) -> List[RecurringSeries]:
    """detect_recurring over the database's bank outflows, with resolutions.

    A stored resolution (``cancelled`` / ``paid_elsewhere``) applies to a series
    whose last charge is on or before the resolution's ``since``: the series
    takes that status and carries ``resolution``. A charge after ``since`` means
    the series came back: it keeps its detected status and gets
    ``resolution_stale``. ``detect_recurring`` itself stays pure.
    """
    rows = db.list_spending_transactions(
        portfolio_id=portfolio_id, is_transfer=False, amount_sign="negative"
    )
    series = detect_recurring(rows, last_import_dates(db))
    lister = getattr(db, "list_recurring_resolutions", None)
    resolutions = {
        (r["portfolio_id"], r["merchant"].strip().upper()): r
        for r in (lister(portfolio_id) if lister else [])
    }
    if not resolutions:
        return series
    for s in series:
        found = resolutions.get((s.portfolio_id, s.merchant.strip().upper()))
        if found is None:
            continue
        s.resolution = {
            "status": found["status"],
            "since": found["since"],
            "note": found.get("note"),
        }
        if s.last_date <= found["since"]:
            s.status = found["status"]
        else:
            s.resolution_stale = True
    series.sort(key=lambda s: (_STATUS_ORDER[s.status], -s.annual_amount))
    return series


def due_dates(series: RecurringSeries, after: date, until: date) -> List[date]:
    """Dates the series' next charges fall due in ``(after, until]``.

    Counted from the last actual charge in whole cadence steps (not by
    chaining month-end-clamped dates, which drift), oldest first. An ``ended``,
    ``cancelled`` or ``paid_elsewhere`` series has no future charges. A ``missed`` series is expected to resume, so its
    later cycles count, but the charge that failed to arrive does not: it is
    on or before the account's last import.

    Args:
        series: a detected series.
        after: exclusive lower bound, normally the account's last import.
        until: inclusive upper bound.
    """
    if series.status in _NO_FUTURE_STATUSES or until <= after:
        return []
    # Every cycle is counted from the last actual charge, never from the
    # already-clamped ``next_expected``: a charge on the 31st would otherwise
    # slide to the 28th for good once February clamped it.
    last = date.fromisoformat(series.last_date)
    out: List[date] = []
    for k in range(1, 5000):
        if series.cadence == "weekly":
            due = last + timedelta(days=7 * k)
        else:
            due = _add_months(last, k * _MONTHS_AHEAD[series.cadence])
        if due > until:
            break
        if due > after:
            out.append(due)
    return out


def upcoming_charges(
    series: List[RecurringSeries],
    last_import: Dict[int, date],
    until: date,
) -> List[tuple]:
    """Charges still to arrive up to ``until``, as ``(series, due_date)``.

    A charge is "still to arrive" when it falls due after its own account's
    last import: a statement past the due date would already hold it.
    """
    out = []
    for s in series:
        after = last_import.get(s.portfolio_id) or date.fromisoformat(s.last_date)
        for due in due_dates(s, after, until):
            out.append((s, due))
    out.sort(key=lambda pair: (pair[1], pair[0].merchant))
    return out
