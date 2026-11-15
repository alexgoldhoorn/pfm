"""Loan amortisation: annuity (French) schedule over a loan dict.

Pure maths, no database access. A payment falls on each monthly anniversary
of the anchor date. The anchor is (``start_date``, ``principal``) or, when
both ``balance_as_of`` and ``rate_as_of`` are set, (``rate_as_of``,
``balance_as_of``) over the term that remains at that date.
"""

import calendar
from datetime import date, datetime
from typing import Optional, Union

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

    Each entry has ``year``, ``balance_end_eur``, ``interest_paid_eur``,
    ``principal_paid_eur``, ``payments_eur`` (all payments in that year, extra
    included) and ``payments_count``.
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
        interest = round(sum(r["interest"] for r in in_year), 2)
        paid = round(sum(r["payment"] for r in in_year), 2)
        out.append(
            {
                "year": year,
                "balance_end_eur": balance,
                "interest_paid_eur": interest,
                "principal_paid_eur": round(paid - interest, 2),
                "payments_eur": paid,
                "payments_count": len(in_year),
            }
        )
    return out


def payments_in_year(loan: dict, year: int) -> float:
    """Return scheduled payments (extra included) falling in ``year``."""
    return round(
        sum(r["payment"] for r in _payments(loan) if r["date"].year == year), 2
    )


def payment_rows(loan: dict) -> list[dict]:
    """Return every scheduled payment: date, payment, interest, principal, balance.

    ``balance`` is the balance after the payment. The anchor is the loan start
    or, with a recorded balance and rate date, that date.
    """
    return [
        {**r, "principal": round(r["payment"] - r["interest"], 2)}
        for r in _payments(loan)
    ]


def payment_split(loan: dict, month: str) -> Optional[dict]:
    """Return the scheduled payment falling in ``month`` (``YYYY-MM``).

    Returns:
        ``{"date", "payment", "interest", "principal"}``, or None when no
        payment falls in that month (before the anchor, after the end).
    """
    for r in payment_rows(loan):
        if r["date"].strftime("%Y-%m") == month:
            return r
    return None


def anchor_info(loan: dict) -> dict:
    """Return the anchor of the schedule and whether it replaces the start.

    ``anchored`` is true when the schedule runs from a recorded balance, so
    the history before ``date`` is not known.
    """
    anchor, balance, remaining = _anchor(loan)
    return {
        "date": anchor,
        "balance_eur": balance,
        "payments_left_at_anchor": remaining,
        "anchored": anchor != _to_date(loan["start_date"]),
    }


def _simulate(
    balance: float, annual_rate_pct: float, payment: float, dates: list[date]
) -> list[dict]:
    """Pay ``balance`` down with a level ``payment`` on the given dates."""
    rate = annual_rate_pct / 1200
    rows = []
    for k, day in enumerate(dates, start=1):
        if balance <= 0.005:
            break
        interest = round(balance * rate, 2)
        due = balance + interest
        paid = due if (k == len(dates) or payment >= due) else payment
        balance = round(due - paid, 2)
        rows.append(
            {
                "date": day,
                "payment": round(paid, 2),
                "interest": interest,
                "balance": max(balance, 0.0),
            }
        )
    return rows


def _outcome(rows: list[dict], payment: float) -> dict:
    return {
        "end_date": rows[-1]["date"].isoformat() if rows else None,
        "months_left": len(rows),
        "monthly_payment_eur": round(payment, 2),
        "interest_remaining_eur": round(sum(r["interest"] for r in rows), 2),
    }


def what_if(
    loan: dict,
    lump_sum: float = 0.0,
    lump_date: Optional[DateLike] = None,
    extra_monthly: float = 0.0,
    mode: str = "term",
    invest_return_pct: Optional[float] = None,
    today: Optional[date] = None,
) -> dict:
    """Compare the loan as scheduled with a lump-sum and/or extra payments.

    The lump sum is applied to the balance as of ``lump_date`` (the last
    scheduled payment on or before it); later payments keep their dates.

    Args:
        loan: Loan dict.
        lump_sum: One-off extra repayment.
        lump_date: When it is paid; default today.
        extra_monthly: Extra on every later payment (``term`` mode only).
        mode: ``term`` keeps the payment and shortens the term; ``payment``
            keeps the end date and lowers the payment.
        invest_return_pct: Optional yearly return; adds the value of putting
            the same money in an investment until the baseline end date.
        today: Injectable clock.

    Returns:
        ``{"baseline", "scenario", "interest_saved_eur", "months_saved",
        "invest"}``; ``invest`` is None without a return.

    Raises:
        ValueError: for a negative amount, an unknown mode, a lump sum above
            the balance, extra monthly in ``payment`` mode, or a loan that has
            no payments left on ``lump_date``.
    """
    today = today or date.today()
    if mode not in ("term", "payment"):
        raise ValueError("mode must be 'term' or 'payment'")
    if lump_sum < 0 or extra_monthly < 0:
        raise ValueError("lump_sum and extra_monthly cannot be negative")
    if mode == "payment" and extra_monthly:
        raise ValueError("extra_monthly applies to mode 'term' only")
    day = _to_date(lump_date) if lump_date else today
    balance = balance_on(loan, day)
    if lump_sum > balance + 0.005:
        raise ValueError("lump_sum is above the balance on that date")
    future = [r for r in _payments(loan) if r["date"] > day]
    if not future:
        raise ValueError("The loan has no payments left after that date")
    dates = [r["date"] for r in future]
    rate = float(loan["annual_rate_pct"])
    base_pay = payment_of(loan)
    baseline = _outcome(future, base_pay)

    start = round(balance - lump_sum, 2)
    if mode == "term":
        pay = base_pay + extra_monthly
        rows = _simulate(start, rate, pay, dates)
    else:
        extra_fixed = float(loan.get("extra_payment_eur") or 0)
        pay = round(monthly_payment(start, rate, len(dates)), 2) + extra_fixed
        rows = _simulate(start, rate, pay, dates)
    scenario = _outcome(rows, pay)

    invest = None
    if invest_return_pct is not None:
        n = len(dates)
        r = (1 + invest_return_pct / 100) ** (1 / 12) - 1
        contributed_months = len(rows) if mode == "term" else 0
        value = lump_sum * (1 + r) ** n
        for j in range(1, contributed_months + 1):
            value += extra_monthly * (1 + r) ** (n - j)
        contributed = lump_sum + extra_monthly * contributed_months
        saved = round(
            baseline["interest_remaining_eur"] - scenario["interest_remaining_eur"], 2
        )
        invest = {
            "return_pct": invest_return_pct,
            "contributed_eur": round(contributed, 2),
            "value_at_baseline_end_eur": round(value, 2),
            "gain_eur": round(value - contributed, 2),
            "gain_minus_interest_saved_eur": round(value - contributed - saved, 2),
            "note": (
                "Before tax, costs and risk; not advice. The payment freed after "
                "an earlier end date is not invested."
            ),
        }
    return {
        "lump_date": day.isoformat(),
        "mode": mode,
        "baseline": baseline,
        "scenario": scenario,
        "interest_saved_eur": round(
            baseline["interest_remaining_eur"] - scenario["interest_remaining_eur"], 2
        ),
        "months_saved": baseline["months_left"] - scenario["months_left"],
        "invest": invest,
    }
