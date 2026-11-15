"""Loans & Mortgage page: overview, equity and payments-versus-bank.

Read-only. The maths of one loan lives in ``loans.py``; this module adds what
needs the database: the property value for equity and the bank debits for the
reconciliation.
"""

import calendar
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services import loans as loan_calc

# a payment within this many euros of the schedule counts as matching
RECONCILE_TOLERANCE_EUR = 1.0
# the last months that differ by the same amount before the hint is shown
HINT_MIN_MONTHS = 2
HINT_SAME_DIFF_EUR = 0.5
HINT_TEXT = "payment changed: the rate may have been revised"


def _month_end(month: str) -> date:
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, calendar.monthrange(year, mon)[1])


def default_category(db, loan: dict) -> Optional[str]:
    """The spending category a loan's debits are expected in.

    The category named like the loan (case-insensitive), else ``Mortgage``
    when it exists, else None.
    """
    names = db.list_spending_categories()
    lowered = {n.lower(): n for n in names if n}
    for wanted in (str(loan["name"]).lower(), "mortgage"):
        if wanted in lowered:
            return lowered[wanted]
    return None


def summary(loan: dict, today: date) -> Dict[str, Any]:
    """Headline figures of one loan, in the loan's currency."""
    rows = loan_calc.payment_rows(loan)
    anchor = loan_calc.anchor_info(loan)
    future = [r for r in rows if r["date"] > today]
    this_year = [r for r in rows if r["date"].year == today.year]
    paid = [r for r in this_year if r["date"] <= today]
    note = None
    if anchor["anchored"]:
        note = (
            f"The schedule runs from the balance recorded on {anchor['date']}; "
            "what was paid before that date is not known."
        )
    return {
        "id": loan["id"],
        "name": loan["name"],
        "lender": loan.get("lender"),
        "currency": loan.get("currency") or "EUR",
        "balance_eur": loan_calc.balance_on(loan, today),
        "annual_rate_pct": loan["annual_rate_pct"],
        "rate_as_of": loan.get("rate_as_of"),
        "monthly_payment_eur": loan_calc.payment_of(loan),
        "end_date": loan_calc.end_date(loan).isoformat(),
        "months_left": len(future),
        "interest_remaining_eur": round(sum(r["interest"] for r in future), 2),
        "principal_paid_this_year_eur": round(sum(r["principal"] for r in paid), 2),
        "interest_paid_this_year_eur": round(sum(r["interest"] for r in paid), 2),
        "principal_scheduled_this_year_eur": round(
            sum(r["principal"] for r in this_year), 2
        ),
        "interest_scheduled_this_year_eur": round(
            sum(r["interest"] for r in this_year), 2
        ),
        "anchored": anchor["anchored"],
        "anchor_date": anchor["date"].isoformat(),
        "anchor_note": note,
    }


def equity(db, loan: dict, today: date, fx: Callable[[str], float]) -> Dict[str, Any]:
    """Property value less the loan balance, with loan-to-value.

    Property is every manual asset in category ``property`` (not a liability).
    With several loans the balance of all of them is used, and the note says so.
    """
    props = [
        p
        for p in db.get_manual_assets()
        if p.get("category") == "property" and not p.get("is_liability")
    ]
    if not props:
        return {
            "equity_eur": None,
            "ltv_pct": None,
            "property_value_eur": None,
            "property": [],
            "reason": "No manual asset in category 'property': add one on Net Worth.",
            "note": None,
        }
    value = sum(float(p["amount"] or 0) * fx(p.get("currency") or "EUR") for p in props)
    loans = db.get_loans()
    several = len(loans) > 1
    balance = sum(
        loan_calc.balance_on(ln, today) * fx(ln.get("currency") or "EUR")
        for ln in (loans if several else [loan])
    )
    return {
        "property_value_eur": round(value, 2),
        "property": [
            {
                "name": p["name"],
                "amount_eur": round(
                    float(p["amount"] or 0) * fx(p.get("currency") or "EUR"), 2
                ),
                "as_of": p.get("as_of"),
            }
            for p in props
        ],
        "loans_balance_eur": round(balance, 2),
        "equity_eur": round(value - balance, 2),
        "ltv_pct": round(balance / value * 100, 1) if value > 0 else None,
        "reason": None,
        "note": (
            "Several loans exist, so the balance of all of them is used."
            if several
            else None
        ),
    }


def _last_import(db, category: str) -> Optional[date]:
    """Latest imported spending date over the accounts that hold the category."""
    from portf_manager.services.recurring import last_import_dates

    last = last_import_dates(db)
    if not last:
        return None
    holding = {
        r["portfolio_id"] for r in db.list_spending_transactions(category=category)
    }
    dates = [d for pid, d in last.items() if not holding or pid in holding]
    return max(dates) if dates else None


def reconciliation(
    db, loan: dict, today: date, category: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Scheduled payment against the bank debits, one row per month.

    Status per month: ``ok`` (within a euro), ``differs``, ``missing`` (no
    debit although an import reaches past the month) or ``pending`` (no debit,
    but the statement does not cover the month yet).

    Returns:
        A dict, or ``{"reason": ...}`` with ``rows`` None when no category can
        be used.
    """
    chosen = category or default_category(db, loan)
    if not chosen:
        return {
            "category": None,
            "rows": None,
            "before_anchor": [],
            "hint": None,
            "reason": (
                "No spending category to compare with: name one after the loan "
                "or use 'Mortgage', or pass ?category=."
            ),
        }
    actual: Dict[str, float] = {}
    for r in db.list_spending_transactions(category=chosen, is_transfer=False):
        amount = float(r["amount"] or 0)
        if amount < 0:
            month = str(r["date"])[:7]
            actual[month] = round(actual.get(month, 0.0) - amount, 2)
    covered_to = _last_import(db, chosen)

    rows: List[Dict[str, Any]] = []
    for pay in loan_calc.payment_rows(loan):
        if pay["date"] > today:
            break
        month = pay["date"].strftime("%Y-%m")
        got = actual.get(month, 0.0)
        diff = round(got - pay["payment"], 2)
        complete = covered_to is not None and covered_to > _month_end(month)
        if got == 0:
            status = "missing" if complete else "pending"
        elif abs(diff) <= RECONCILE_TOLERANCE_EUR:
            status = "ok"
        else:
            status = "differs"
        rows.append(
            {
                "month": month,
                "scheduled_eur": pay["payment"],
                "actual_eur": got,
                "difference_eur": diff,
                "status": status,
                "complete": complete,
            }
        )
    anchor = loan_calc.anchor_info(loan)
    first_month = rows[0]["month"] if rows else today.strftime("%Y-%m")
    start_month = loan_calc._to_date(loan["start_date"]).strftime("%Y-%m")
    before = (
        [
            {"month": m, "actual_eur": v}
            for m, v in sorted(actual.items())
            if start_month <= m < first_month
        ]
        if anchor["anchored"]
        else []
    )
    differing = [r for r in rows if r["status"] in ("ok", "differs") and r["complete"]]
    tail = differing[-HINT_MIN_MONTHS:]
    hint = None
    if (
        len(tail) == HINT_MIN_MONTHS
        and all(r["status"] == "differs" for r in tail)
        and max(r["difference_eur"] for r in tail)
        - min(r["difference_eur"] for r in tail)
        <= HINT_SAME_DIFF_EUR
    ):
        hint = HINT_TEXT
    return {
        "category": chosen,
        "rows": rows,
        "before_anchor": before,
        "hint": hint,
        "reason": None,
        "data_through": covered_to.isoformat() if covered_to else None,
    }


def latest_complete_row(recon: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The newest month with a debit whose statement month is complete."""
    if not recon or not recon.get("rows"):
        return None
    done = [r for r in recon["rows"] if r["complete"] and r["actual_eur"] > 0]
    return done[-1] if done else None


def overview(
    db,
    loan: dict,
    fx: Callable[[str], float],
    today: Optional[date] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything the Loans & Mortgage page shows for one loan."""
    today = today or date.today()
    return {
        "summary": summary(loan, today),
        "equity": equity(db, loan, today, fx),
        "schedule": loan_calc.schedule(loan),
        "reconciliation": reconciliation(db, loan, today, category),
    }
