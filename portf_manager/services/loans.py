"""Loan amortisation: annuity (French) schedule over a loan dict.

Pure maths, no database access. A payment falls on each monthly anniversary
of the anchor date. The anchor is (``start_date``, ``principal``) or, when
both ``balance_as_of`` and ``rate_as_of`` are set, (``rate_as_of``,
``balance_as_of``) over the term that remains at that date.
"""

import calendar
from datetime import date, datetime
from typing import Union

DateLike = Union[str, date]


def _to_date(value: DateLike) -> date:
    """Coerce a ``YYYY-MM-DD`` string or a date to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _add_months(anchor: date, months: int) -> date:
    """Return ``anchor`` plus whole months, clipping to the month's last day."""
    index = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _months_between(start: date, end: date) -> int:
    """Whole months from ``start`` to ``end``; the anniversary day counts."""
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + end.month - start.month
    if _add_months(start, months) > end:
        months -= 1
    return months


def monthly_payment(
    principal: float, annual_rate_pct: float, term_months: int
) -> float:
    """Return the level monthly annuity payment.

    Args:
        principal: Amount borrowed.
        annual_rate_pct: Nominal annual rate in percent.
        term_months: Number of monthly payments.

    Returns:
        The payment, unrounded. A zero rate gives ``principal / term_months``.
    """
    if term_months <= 0:
        return float(principal)
    r = annual_rate_pct / 1200
    if r == 0:
        return principal / term_months
    return principal * r / (1 - (1 + r) ** -term_months)


def _anchor(loan: dict) -> tuple[date, float, int]:
    """Return (anchor date, anchor balance, remaining payments)."""
    start = _to_date(loan["start_date"])
    term = int(loan["term_months"])
    rate_as_of = loan.get("rate_as_of")
    balance_as_of = loan.get("balance_as_of")
    if rate_as_of and balance_as_of is not None:
        anchor = _to_date(rate_as_of)
        remaining = max(term - _months_between(start, anchor), 0)
        return anchor, float(balance_as_of), remaining
    return start, float(loan["principal"]), term


def _payments(loan: dict) -> list[dict]:
    """Simulate the loan month by month, rounded to cents.

    Returns one entry per payment: date, payment, interest and balance after.
    The last payment clears whatever is left.
    """
    anchor, balance, remaining = _anchor(loan)
    rate = float(loan["annual_rate_pct"]) / 1200
    pay = round(monthly_payment(balance, loan["annual_rate_pct"], remaining), 2)
    pay += float(loan.get("extra_payment_eur") or 0)
    rows: list[dict] = []
    for k in range(1, remaining + 1):
        if balance <= 0.005:
            break
        interest = round(balance * rate, 2)
        due = balance + interest
        paid = due if (k == remaining or pay >= due) else pay
        balance = round(due - paid, 2)
        rows.append(
            {
                "date": _add_months(anchor, k),
                "payment": round(paid, 2),
                "interest": interest,
                "balance": max(balance, 0.0),
            }
        )
    return rows


def payment_of(loan: dict) -> float:
    """Return the regular monthly payment, extra payment included.

    Computed on the anchor (start, or the recorded balance and rate date) over
    the payments that remain; 0 when none remain.
    """
    _, balance, remaining = _anchor(loan)
    if remaining <= 0:
        return 0.0
    pay = round(monthly_payment(balance, loan["annual_rate_pct"], remaining), 2)
    return round(pay + float(loan.get("extra_payment_eur") or 0), 2)


def balance_on(loan: dict, day: DateLike) -> float:
    """Return the outstanding balance on ``day``.

    Before the loan starts this is the principal; before the anchor date it
    is the anchor balance; after the final payment it is 0.
    """
    day = _to_date(day)
    if day < _to_date(loan["start_date"]):
        return float(loan["principal"])
    anchor, balance, remaining = _anchor(loan)
    # no payments left at the anchor: the loan has ended
    if remaining == 0 and day >= anchor:
        return 0.0
    for row in _payments(loan):
        if row["date"] > day:
            break
        balance = row["balance"]
    return balance


def end_date(loan: dict) -> date:
    """Return the date of the final payment."""
    rows = _payments(loan)
    if rows:
        return rows[-1]["date"]
    return _anchor(loan)[0]


def schedule(loan: dict) -> list[dict]:
    """Return one entry per calendar year from the anchor year to the last.

    Each entry has ``year``, ``balance_end_eur``, ``interest_paid_eur`` and
    ``payments_eur`` (all payments in that year, extra included).
    """
    anchor, opening, remaining = _anchor(loan)
    # a loan with no payments left is a single zero row in the anchor year
    if remaining == 0:
        opening = 0.0
    rows = _payments(loan)
    last_year = rows[-1]["date"].year if rows else anchor.year
    out = []
    balance = opening
    for year in range(anchor.year, last_year + 1):
        in_year = [r for r in rows if r["date"].year == year]
        if in_year:
            balance = in_year[-1]["balance"]
        out.append(
            {
                "year": year,
                "balance_end_eur": balance,
                "interest_paid_eur": round(sum(r["interest"] for r in in_year), 2),
                "payments_eur": round(sum(r["payment"] for r in in_year), 2),
            }
        )
    return out


def payments_in_year(loan: dict, year: int) -> float:
    """Return scheduled payments (extra included) falling in ``year``."""
    return round(
        sum(r["payment"] for r in _payments(loan) if r["date"].year == year), 2
    )
