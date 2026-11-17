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


def _find_category(db, wanted_names: List[str]) -> Optional[str]:
    """The first of ``wanted_names`` that exists as a category (any case)."""
    names = db.list_spending_categories()
    lowered = {n.lower(): n for n in names if n}
    for wanted in wanted_names:
        if wanted.lower() in lowered:
            return lowered[wanted.lower()]
    return None


def default_category(db, loan: dict) -> Optional[str]:
    """The spending category a loan's debits are expected in.

    The category named like the loan (case-insensitive), else ``Mortgage``
    when it exists, else None.
    """
    return _find_category(db, [str(loan["name"]), "mortgage"])


def with_periods(db, loan: dict) -> dict:
    """Return the loan dict with its rate periods under ``rate_periods``."""
    return {**loan, "rate_periods": db.get_loan_rate_periods(loan["id"])}


def summary(loan: dict, today: date) -> Dict[str, Any]:
    """Headline figures of one loan, in the loan's currency."""
    rows = loan_calc.payment_rows(loan)
    anchor = loan_calc.anchor_info(loan)
    future = [r for r in rows if r["date"] > today]
    this_year = [r for r in rows if r["date"].year == today.year]
    paid = [r for r in this_year if r["date"] <= today]
    note = None
    history = loan_calc.has_history(loan)
    if anchor["anchored"] and history:
        note = (
            f"The schedule runs from the balance recorded on {anchor['date']}; "
            "earlier years are rebuilt from the rate history."
        )
    elif anchor["anchored"]:
        note = (
            f"The schedule runs from the balance recorded on {anchor['date']}; "
            "what was paid before that date is not known. Add the earlier "
            "rates under Rate history to rebuild it."
        )
    balance = loan_calc.balance_on(loan, today)
    principal = float(loan["principal"])
    repaid = round(max(principal - balance, 0.0), 2)
    return {
        "id": loan["id"],
        "name": loan["name"],
        "principal_eur": round(principal, 2),
        "start_date": str(loan["start_date"])[:10],
        "term_months": int(loan["term_months"]),
        "repaid_eur": repaid,
        "repaid_pct": round(repaid / principal * 100, 1) if principal > 0 else None,
        "lender": loan.get("lender"),
        "currency": loan.get("currency") or "EUR",
        "balance_eur": balance,
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
        "has_history": history,
        "paid_to_date": loan_calc.paid_to_date(loan, today),
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


def _status(got: float, diff: float, complete: bool) -> str:
    """Status of one month: ok, differs, missing or pending."""
    if got == 0:
        return "missing" if complete else "pending"
    return "ok" if abs(diff) <= RECONCILE_TOLERANCE_EUR else "differs"


def _history_comparison(
    history: List[Dict[str, Any]],
    actual: Dict[str, float],
    covered_to: Optional[date],
    today: date,
    start_month: str,
    first_month: str,
) -> List[Dict[str, Any]]:
    """Bank debits before the anchor against the payments the rate history gives.

    One entry per month from the loan start to the month before the forward
    schedule: the bank amount, the history payment (None when the history has
    no payment that month) and a status as in the forward rows.
    """
    scheduled = {
        r["date"].strftime("%Y-%m"): r["payment"] for r in history if r["date"] <= today
    }
    out = []
    for month in sorted(set(scheduled) | set(actual)):
        if not (start_month <= month < first_month):
            continue
        got = actual.get(month, 0.0)
        sched = scheduled.get(month)
        if sched is None:
            out.append({"month": month, "actual_eur": got, "compared": False})
            continue
        diff = round(got - sched, 2)
        complete = covered_to is not None and covered_to > _month_end(month)
        out.append(
            {
                "month": month,
                "actual_eur": got,
                "scheduled_eur": sched,
                "difference_eur": diff,
                "status": _status(got, diff, complete),
                "complete": complete,
                "compared": True,
            }
        )
    return out


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
        status = _status(got, diff, complete)
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
    if anchor["anchored"] and loan_calc.has_history(loan):
        before = _history_comparison(
            loan_calc.history_rows(loan),
            actual,
            covered_to,
            today,
            start_month,
            first_month,
        )
    elif anchor["anchored"]:
        before = [
            {"month": m, "actual_eur": v}
            for m, v in sorted(actual.items())
            if start_month <= m < first_month
        ]
    else:
        before = []
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
    loan = with_periods(db, loan)
    return {
        "summary": summary(loan, today),
        "equity": equity(db, loan, today, fx),
        "schedule": loan_calc.schedule(loan),
        "payments": loan_calc.monthly_rows(loan, today),
        "rate_periods": [_period_out(p) for p in loan["rate_periods"]],
        "reconciliation": reconciliation(db, loan, today, category),
    }


def _period_out(period: Dict[str, Any]) -> Dict[str, Any]:
    """A rate period as the API returns it."""
    return {
        "id": period["id"],
        "loan_id": period["loan_id"],
        "from_date": period["from_date"],
        "annual_rate_pct": period["annual_rate_pct"],
        "balance_eur": period.get("balance_eur"),
        "note": period.get("note"),
    }


def digest(
    db, loan: Dict[str, Any], fx: Callable[[str], float], today: Optional[date] = None
) -> Dict[str, Any]:
    """One loan in a compact form for the chat and other readers.

    The headline figures of ``overview`` without the yearly table and the
    month-by-month reconciliation: the latest complete month only.
    """
    today = today or date.today()
    ov = overview(db, loan, fx, today)
    s, eq, recon = ov["summary"], ov["equity"], ov["reconciliation"]
    latest = latest_complete_row(recon)
    paid = s["paid_to_date"]
    return {
        "id": s["id"],
        "name": s["name"],
        "lender": s["lender"],
        "principal_eur": s["principal_eur"],
        "start_date": s["start_date"],
        "repaid_eur": s["repaid_eur"],
        "repaid_pct": s["repaid_pct"],
        "balance_eur": s["balance_eur"],
        "annual_rate_pct": s["annual_rate_pct"],
        "rate_as_of": s["rate_as_of"],
        "monthly_payment_eur": s["monthly_payment_eur"],
        "end_date": s["end_date"],
        "months_left": s["months_left"],
        "interest_remaining_eur": s["interest_remaining_eur"],
        "interest_paid_this_year_eur": s["interest_paid_this_year_eur"],
        "interest_scheduled_this_year_eur": s["interest_scheduled_this_year_eur"],
        "paid_to_date": paid if paid["complete"] else None,
        "rate_periods": ov["rate_periods"],
        "equity_eur": eq.get("equity_eur"),
        "ltv_pct": eq.get("ltv_pct"),
        "equity_note": eq.get("reason") or eq.get("note"),
        "latest_bank_month": (
            {k: latest[k] for k in ("month", "scheduled_eur", "actual_eur", "status")}
            if latest
            else None
        ),
        "bank_hint": (recon or {}).get("hint"),
        "bank_reason": (recon or {}).get("reason"),
        "anchor_note": s["anchor_note"],
    }


# ── Detect a loan from bank rows ───────────────────────────────────────────

# months with debits needed before a payment counts as recurring
DETECT_MIN_MONTHS = 3
# months in a row an amount must last to count as a stretch, not a one-off
DETECT_MIN_RUN = 2
# at least this share of the months between first and last debit must have one
DETECT_MIN_COVERAGE = 0.6
# an incoming row this many times the payment may be the disbursement
DETECT_DISBURSEMENT_FACTOR = 20


def _empty_proposal(reason: str, **extra: Any) -> Dict[str, Any]:
    """A proposal that found nothing, with the reason."""
    return {
        "found": False,
        "reason": reason,
        "monthly_payment_eur": None,
        "first_payment_date": None,
        "last_payment_date": None,
        "months_observed": 0,
        "stretches": [],
        "rate_periods": [],
        "principal_eur": None,
        "principal_source": None,
        "term_months": None,
        "start_date": None,
        "form": {},
        "notes": [],
        **extra,
    }


def _monthly_debits(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sum the debits per calendar month, oldest first.

    Each entry has ``month``, ``amount`` (positive euros), the first and last
    debit ``date`` of that month.
    """
    months: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        amount = float(r["amount"] or 0)
        if amount >= 0:
            continue
        day = str(r["date"])[:10]
        m = months.setdefault(
            day[:7], {"month": day[:7], "amount": 0.0, "first": day, "last": day}
        )
        m["amount"] = round(m["amount"] - amount, 2)
        m["first"], m["last"] = min(m["first"], day), max(m["last"], day)
    return [months[k] for k in sorted(months)]


def _stretches(months: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group the monthly debits into stretches of an unchanged amount.

    A run of fewer than ``DETECT_MIN_RUN`` months is a one-off and is left out;
    neighbouring stretches with the same amount are merged.
    """
    runs: List[List[Dict[str, Any]]] = []
    for m in months:
        if (
            runs
            and abs(m["amount"] - runs[-1][-1]["amount"]) <= RECONCILE_TOLERANCE_EUR
        ):
            runs[-1].append(m)
        else:
            runs.append([m])
    kept: List[List[Dict[str, Any]]] = []
    for run in runs:
        if len(run) < DETECT_MIN_RUN:
            continue
        amount = sorted(x["amount"] for x in run)[len(run) // 2]
        if kept and abs(amount - _run_amount(kept[-1])) <= RECONCILE_TOLERANCE_EUR:
            kept[-1].extend(run)
        else:
            kept.append(run)
    return [
        {
            "from_date": run[0]["first"],
            "to_date": run[-1]["last"],
            "months": len(run),
            "payment_eur": _run_amount(run),
        }
        for run in kept
    ]


def _run_amount(run: List[Dict[str, Any]]) -> float:
    """The median amount of a run of months."""
    return round(sorted(x["amount"] for x in run)[len(run) // 2], 2)


def _month_index(day: date) -> int:
    """Calendar months since year 0, so two dates compare by month only."""
    return day.year * 12 + day.month


def _solve_stretches(
    stretches: List[Dict[str, Any]],
    principal: float,
    term_months: int,
) -> None:
    """Fill ``annual_rate_pct`` and ``balance_at_start_eur`` of each stretch.

    The first stretch starts at the loan's first payment. Each stretch's
    payment is an annuity over the term that remains at its start, so its rate
    follows from the balance the earlier stretches leave. A stretch whose
    payment fits no rate gets None, and the later ones cannot be solved
    either. Months are counted by calendar month, so a debit that moves a few
    days does not shift the count.
    """
    first = _month_index(loan_calc._to_date(stretches[0]["from_date"]))
    balance: Optional[float] = principal
    paid = 0
    prev: Optional[Dict[str, Any]] = None
    for st in stretches:
        # payments made before this stretch's first payment
        before = _month_index(loan_calc._to_date(st["from_date"])) - first
        if balance is not None and prev is not None and before > paid:
            balance = loan_calc.balance_after(
                balance, prev["annual_rate_pct"], prev["payment_eur"], before - paid
            )
            paid = before
        st["balance_at_start_eur"] = None if balance is None else round(balance, 2)
        st["annual_rate_pct"] = (
            None
            if balance is None
            else loan_calc.solve_rate(balance, st["payment_eur"], term_months - before)
        )
        if st["annual_rate_pct"] is None:
            balance = None
        prev = st


def detect(
    db,
    category: Optional[str] = None,
    description: Optional[str] = None,
    principal: Optional[float] = None,
    term_months: Optional[int] = None,
) -> Dict[str, Any]:
    """Propose a loan from a recurring monthly debit in the bank rows.

    Read-only: nothing is created. The debits come from one category (given,
    else the category ``Mortgage``, else one named like an existing loan),
    narrowed by a description filter when given. The amounts per month give
    the monthly payment, the stretches of an unchanged amount (each change is
    a candidate rate revision) and the first payment date. With a ``principal``
    and ``term_months`` (or an incoming row that looks like the disbursement)
    the annual rate of each stretch is solved from its payment.

    Returns:
        A proposal dict; ``found`` is false with a ``reason`` when the rows
        do not look like a loan. Never raises on missing data.
    """
    chosen = category
    if not chosen and not description:
        chosen = _find_category(
            db, ["mortgage"] + [str(ln["name"]) for ln in db.get_loans()]
        )
        if not chosen:
            return _empty_proposal(
                "No category to look in: pass ?category= or ?description=, or "
                "name a category 'Mortgage'."
            )
    rows = db.list_spending_transactions(category=chosen, is_transfer=False)
    if description:
        needle = description.casefold()
        rows = [r for r in rows if needle in str(r.get("description") or "").casefold()]
    base = {"category": chosen, "description": description}
    months = _monthly_debits(rows)
    if len(months) < DETECT_MIN_MONTHS:
        return _empty_proposal(
            f"Only {len(months)} month(s) with a debit; at least "
            f"{DETECT_MIN_MONTHS} are needed to see a recurring payment.",
            **base,
        )
    first_m, last_m = months[0]["month"], months[-1]["month"]
    span = (
        (int(last_m[:4]) - int(first_m[:4])) * 12
        + int(last_m[5:7])
        - int(first_m[5:7])
        + 1
    )
    if len(months) / span < DETECT_MIN_COVERAGE:
        return _empty_proposal(
            "The debits are not monthly: too many months in between have none.",
            **base,
        )
    stretches = _stretches(months)
    if not stretches:
        return _empty_proposal(
            "No amount repeats for two months in a row, so no fixed payment "
            "stands out.",
            **base,
        )
    payment = stretches[-1]["payment_eur"]
    first_payment = date.fromisoformat(stretches[0]["from_date"])
    notes: List[str] = []
    if months[-1]["month"] > stretches[-1]["to_date"][:7]:
        notes.append(
            "The newest month differs from the last stretch and is not "
            "counted until it repeats."
        )

    principal_source = "given" if principal else None
    disbursement = None
    if not principal:
        incoming = [
            r
            for r in rows
            if float(r["amount"] or 0) >= payment * DETECT_DISBURSEMENT_FACTOR
        ]
        if incoming:
            big = max(incoming, key=lambda r: float(r["amount"]))
            principal = round(float(big["amount"]), 2)
            principal_source = "disbursement"
            disbursement = str(big["date"])[:10]
    start = loan_calc._add_months(first_payment, -1)
    if disbursement:
        notes.append(f"Principal taken from the incoming row on {disbursement}.")
    for st in stretches:
        st["annual_rate_pct"] = None
        st["balance_at_start_eur"] = None
    rates_reason = None
    if principal and term_months:
        _solve_stretches(stretches, float(principal), int(term_months))
        if any(st["annual_rate_pct"] is None for st in stretches):
            rates_reason = (
                "A payment does not fit the principal and term given, so "
                "some rates could not be solved."
            )
        notes.append(
            "Rates assume the first debit found is the loan's first payment "
            "and that the payment is an annuity over the remaining term."
        )
    else:
        rates_reason = "Give the principal and the term in months to solve the rates."

    periods = [
        {
            "from_date": (
                start.isoformat()
                if i == 0
                else loan_calc._add_months(
                    date.fromisoformat(st["from_date"]), -1
                ).isoformat()
            ),
            "annual_rate_pct": st["annual_rate_pct"],
            "balance_eur": st["balance_at_start_eur"] if i else None,
        }
        for i, st in enumerate(stretches[:-1])
        if st["annual_rate_pct"] is not None
    ]
    last = stretches[-1]
    form: Dict[str, Any] = {"start_date": start.isoformat()}
    if principal:
        form["principal"] = float(principal)
    if term_months:
        form["term_months"] = int(term_months)
    if last["annual_rate_pct"] is not None:
        form["annual_rate_pct"] = last["annual_rate_pct"]
    if len(stretches) > 1 and last["annual_rate_pct"] is not None:
        form["rate_as_of"] = loan_calc._add_months(
            date.fromisoformat(last["from_date"]), -1
        ).isoformat()
        form["balance_as_of"] = last["balance_at_start_eur"]
    return {
        "found": True,
        "reason": None,
        "category": chosen,
        "description": description,
        "monthly_payment_eur": payment,
        "first_payment_date": first_payment.isoformat(),
        "last_payment_date": stretches[-1]["to_date"],
        "months_observed": len(months),
        "stretches": stretches,
        "rate_periods": periods,
        "rates_reason": rates_reason,
        "principal_eur": float(principal) if principal else None,
        "principal_source": principal_source,
        "term_months": int(term_months) if term_months else None,
        "start_date": start.isoformat(),
        "form": form,
        "notes": notes,
    }
