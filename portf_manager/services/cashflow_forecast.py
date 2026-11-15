"""Cash-flow forecast: where this month ends and the next N months.

Deterministic, no LLM. The forecast stands on four sources, each of which
fails on its own: a source that cannot be read becomes a warning and ``None``
in the figures that need it, never a zero and never an error.

- the bank rows (``rows``): this month so far and the baseline months;
- the recurring series (``series``): bills placed on the dates they fall due;
- the category tree (``tree``): tells money moved to ``Invest`` / ``House
  purchase`` (not spending) from spending, and refunds from income;
- the bank accounts (``accounts``): their data-through dates and balances.

Rules that keep it honest:

- Recurring charges and the variable baseline never overlap. The variable
  baseline is the average of the complete months' non-transfer outflows
  *minus the rows that belong to a recurring series*, one-offs excluded; the
  recurring charges are added back on the dates they fall due. A charge due
  later this month is counted once, as a recurring charge.
- Complete months that have imported rows are the only baseline months; the
  month in progress never counts in an average, and neither does a month the
  data stops short of.
- "Data through" is the date of the latest imported row of an account. The
  rest of the month is measured from there, not from today.
- A bank account with no stored balance is left out of the starting balance
  (not counted as zero) and named in a warning.
- Transfers to the household's own accounts (a broker, say) are not projected:
  the balance line is a bank-account projection, not a net-worth projection.
"""

import calendar
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services.life_scenarios import complete_months
from portf_manager.services.recurring import (
    RecurringSeries,
    due_dates,
    last_import_dates,
    load_recurring,
)
from portf_manager.services.spending_insights import (
    data_through,
    load_rows,
    savings_names,
    shift_month,
)

DEFAULT_FORECAST_MONTHS = 12
MAX_FORECAST_MONTHS = 36
# Complete months behind the variable / income / savings averages.
BASELINE_MONTHS = 6
# Fewer baseline months than this get a "rough" warning.
MIN_RELIABLE_BASELINE_MONTHS = 3
# An account whose latest import is older than this many days is stale.
STALE_DAYS = 7
# How much history the baseline reads (the baseline months plus slack).
_HISTORY_MONTHS = BASELINE_MONTHS + 2


# ── small helpers ───────────────────────────────────────────────────────────


def _month_end(month: str) -> date:
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, calendar.monthrange(year, mon)[1])


def _root_of(tree: Dict[str, dict], name: str) -> Optional[str]:
    """The topmost ancestor's name (the category's own when it has none)."""
    node = tree.get(name)
    top = name
    for _ in range(100):
        if node is None:
            break
        top = node["name"]
        node = tree.get(node.get("parent_name") or "")
    return top


def _is_refund(tree: Dict[str, dict], category: str) -> bool:
    """A positive row filed under Spend is a refund, not income."""
    return _root_of(tree, category) == "Spend" and category != "Spend"


def _avg(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def _neg(value: Optional[float]) -> Optional[float]:
    return None if value is None else -value


def _add(*values: Optional[float]) -> Optional[float]:
    """Sum, or None when any part is unknown."""
    if any(v is None for v in values):
        return None
    return sum(values)  # type: ignore[arg-type]


# ── baseline ────────────────────────────────────────────────────────────────


def baseline(
    rows: List[dict],
    tree: Dict[str, dict],
    recurring_ids: set,
    through: Optional[date],
    today: date,
) -> Dict[str, Any]:
    """Monthly averages of what is left once recurring charges are taken out.

    Args:
        rows: ``load_rows`` output (transfers included).
        tree: ``{name: row}`` category tree.
        recurring_ids: ids of every row that belongs to a recurring series.
        through: latest imported date; a month ending after it is partial.
        today: reference date; the month in progress never counts.

    Returns:
        ``{months, variable, income, savings}``: the baseline months used and,
        for each figure, ``{avg, low, high}`` (low and high are the smallest
        and largest single month), or ``None`` for each when no month
        qualifies.
    """
    used = [
        m
        for m in complete_months(rows, today, BASELINE_MONTHS)
        if through is not None and _month_end(m) <= through
    ]
    if not used:
        return {"months": [], "variable": None, "income": None, "savings": None}
    saved = savings_names(tree)
    variable = {m: 0.0 for m in used}
    income = {m: 0.0 for m in used}
    savings = {m: 0.0 for m in used}
    for r in rows:
        m = r["date"][:7]
        if m not in variable or r["is_transfer"] or r["one_off"]:
            continue
        amount = r["amount_eur"]
        if amount > 0:
            if not _is_refund(tree, r["category"]):
                income[m] += amount
        elif r["id"] not in recurring_ids:
            if r["category"] in saved:
                savings[m] += -amount
            else:
                variable[m] += -amount

    def stat(values: Dict[str, float]) -> Dict[str, float]:
        vals = list(values.values())
        return {"avg": sum(vals) / len(vals), "low": min(vals), "high": max(vals)}

    return {
        "months": used,
        "variable": stat(variable),
        "income": stat(income),
        "savings": stat(savings),
    }


# ── forecast ────────────────────────────────────────────────────────────────


def _bills(
    series: List[RecurringSeries],
    accounts: Optional[List[dict]],
    rows_through: Dict[int, date],
    tree: Dict[str, dict],
    until: date,
    fx: Callable[[str], float],
) -> List[Dict[str, Any]]:
    """Every recurring charge still to arrive up to ``until``, oldest first."""
    saved = savings_names(tree) if tree else frozenset()
    names = {a["portfolio_id"]: a["name"] for a in (accounts or [])}
    out = []
    for s in series:
        after = rows_through.get(s.portfolio_id) or date.fromisoformat(s.last_date)
        amount = s.typical_amount * fx(s.currency)
        for due in due_dates(s, after, until):
            out.append(
                {
                    "date": due.isoformat(),
                    "merchant": s.merchant,
                    "amount_eur": round(amount, 2),
                    "category": s.category,
                    "cadence": s.cadence,
                    "portfolio_id": s.portfolio_id,
                    "account_name": names.get(s.portfolio_id),
                    "kind": "savings" if s.category in saved else "spending",
                }
            )
    out.sort(key=lambda b: (b["date"], b["merchant"]))
    return out


def compute_forecast(
    rows: Optional[List[dict]],
    tree: Optional[Dict[str, dict]],
    series: Optional[List[RecurringSeries]],
    accounts: Optional[List[dict]],
    fx: Callable[[str], float],
    today: date,
    months: int = DEFAULT_FORECAST_MONTHS,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """The forecast from already-loaded sources; any source may be ``None``.

    Args:
        rows: ``load_rows`` output, or None when the rows could not be read.
        tree: ``{name: row}`` category tree, or None.
        series: ``detect_recurring`` output, or None.
        accounts: bank accounts as ``{portfolio_id, name, currency,
            data_through (date | None), balance_eur (float | None),
            balance_date (str | None)}``, or None.
        fx: currency -> EUR rate (for the recurring amounts).
        today: reference date.
        months: how many months after the current one to project.
        warnings: messages already collected while loading the sources.

    Returns:
        ``{as_of, data_through, accounts, balance, baseline, this_month,
        next_months, known_bills, first_below_zero, assumptions, warnings}``.
        Figures a missing source makes unknowable are ``None``.
    """
    warnings = list(warnings or [])
    month = today.strftime("%Y-%m")
    month_end = _month_end(month)
    dim = month_end.day

    # ── freshness ──
    through_by_account: Dict[int, date] = {}
    account_out = []
    for a in accounts or []:
        t = a.get("data_through")
        if t:
            through_by_account[a["portfolio_id"]] = t
        behind = (today - t).days if t else None
        stale = t is None or behind > STALE_DAYS
        account_out.append(
            {
                "portfolio_id": a["portfolio_id"],
                "name": a["name"],
                "data_through": t.isoformat() if t else None,
                "days_behind": behind,
                "stale": stale,
                "balance_eur": _r(a.get("balance_eur")),
                "balance_date": a.get("balance_date"),
            }
        )
        if t is None:
            warnings.append(f"{a['name']}: no statement is imported yet.")
        elif stale:
            warnings.append(
                f"{a['name']}: the latest import is from {t.isoformat()}, "
                f"{behind} days ago; the forecast starts from there."
            )
    through = max(through_by_account.values(), default=None)
    if through is None and rows is not None:
        found = data_through(rows)
        through = date.fromisoformat(found) if found else None
    if through is None:
        warnings.append("No bank statement is imported: nothing to project from.")

    # ── recurring bills ──
    bills: Optional[List[Dict[str, Any]]] = None
    recurring_ids: set = set()
    horizon_end = _month_end(shift_month(month, months))
    if series is not None:
        try:
            recurring_ids = {i for s in series for i in s.transaction_ids}
            bills = _bills(
                series, accounts, through_by_account, tree or {}, horizon_end, fx
            )
        except Exception as e:  # a failing rate must not take the forecast down
            bills = None
            warnings.append(f"Recurring charges could not be placed: {e}")

    # ── baseline ──
    base: Optional[Dict[str, Any]] = None
    if rows is not None and tree is not None and series is not None:
        base = baseline(rows, tree, recurring_ids, through, today)
        if not base["months"]:
            warnings.append(
                "No complete month of imported statements: there is no baseline "
                "for income and everyday spending."
            )
        elif len(base["months"]) < MIN_RELIABLE_BASELINE_MONTHS:
            warnings.append(
                f"Only {len(base['months'])} complete month(s) of statements: "
                "the baseline is rough."
            )
    elif rows is not None and (tree is None or series is None):
        warnings.append(
            "The baseline needs the category tree and the recurring charges; "
            "income and everyday spending are not projected."
        )
    avg_var = (base or {}).get("variable")
    avg_inc = (base or {}).get("income")
    avg_sav = (base or {}).get("savings")

    saved = savings_names(tree) if tree else frozenset()

    # ── starting balance ──
    with_balance = [a for a in accounts or [] if a.get("balance_eur") is not None]
    without = [a for a in accounts or [] if a.get("balance_eur") is None]
    start_balance: Optional[float] = None
    if accounts is None:
        warnings.append("Bank accounts could not be read: no balance projection.")
    elif not with_balance:
        warnings.append(
            "No bank account has a stored balance: no balance projection. "
            "Import a statement that carries a balance column."
        )
    else:
        start_balance = sum(a["balance_eur"] for a in with_balance)
        if without:
            warnings.append(
                "Left out of the starting balance (no stored balance): "
                + ", ".join(a["name"] for a in without)
                + "."
            )

    # ── this month ──
    in_month = [r for r in (rows or []) if r["date"][:7] == month]
    has_imports = bool(in_month)
    if through is None:
        rem_days = dim
    else:
        rem_days = max(0, (month_end - through).days)
    rem_share = min(1.0, rem_days / dim)
    elapsed = 1.0 - rem_share

    spent = income_in = income_in_regular = savings_in = 0.0
    for r in in_month:
        if r["is_transfer"]:
            continue
        a = r["amount_eur"]
        if a > 0:
            if tree is not None and _is_refund(tree, r["category"]):
                continue
            income_in += a
            if not r["one_off"]:
                income_in_regular += a
        elif tree is not None and r["category"] in saved:
            savings_in += -a
        else:
            spent += -a

    rec_spend = rec_save = None
    if bills is not None:
        rec_spend = sum(
            b["amount_eur"]
            for b in bills
            if b["kind"] == "spending" and b["date"] <= month_end.isoformat()
        )
        rec_save = sum(
            b["amount_eur"]
            for b in bills
            if b["kind"] == "savings" and b["date"] <= month_end.isoformat()
        )

    def scaled(stat: Optional[Dict[str, float]], key: str) -> Optional[float]:
        return None if stat is None else stat[key] * rem_share

    var_rem = scaled(avg_var, "avg")
    sav_rem = scaled(avg_sav, "avg")
    inc_rem = (
        None
        if avg_inc is None
        else (max(0.0, avg_inc["avg"] - income_in_regular) if rem_days > 0 else 0.0)
    )
    if not has_imports and rows is not None:
        status = "no_imports"
        warnings.append(
            f"Nothing is imported for {month} yet: the month is projected from "
            "the baseline and the recurring charges alone."
        )
    elif avg_var is None or bills is None:
        status = "no_baseline"
    else:
        status = "ok"

    proj_spend = _add(spent, rec_spend, var_rem)
    proj_income = None if inc_rem is None else income_in + inc_rem
    proj_net = _add(proj_income, None if proj_spend is None else -proj_spend)
    spend_low = _add(spent, rec_spend, scaled(avg_var, "low"))
    spend_high = _add(spent, rec_spend, scaled(avg_var, "high"))
    # what is still to happen between the data-through date and month end
    remaining_change = _add(
        inc_rem, _neg(rec_spend), _neg(var_rem), _neg(rec_save), _neg(sav_rem)
    )
    end_balance = (
        None
        if start_balance is None or remaining_change is None
        else start_balance + remaining_change
    )
    this_month = {
        "month": month,
        "status": status,
        "data_through": through.isoformat() if through else None,
        "elapsed_share": round(elapsed, 3),
        "spent_to_date_eur": _r(spent) if has_imports else None,
        "income_to_date_eur": _r(income_in) if has_imports else None,
        "recurring_remaining_eur": _r(rec_spend),
        "variable_remaining_eur": _r(var_rem),
        "income_remaining_eur": _r(inc_rem),
        "projected_spending_eur": _r(proj_spend),
        "projected_income_eur": _r(proj_income),
        "projected_net_eur": _r(proj_net),
        "range": {
            "spending_low_eur": _r(spend_low),
            "spending_high_eur": _r(spend_high),
            "net_low_eur": _r(
                None
                if spend_high is None or proj_income is None
                else proj_income - spend_high
            ),
            "net_high_eur": _r(
                None
                if spend_low is None or proj_income is None
                else proj_income - spend_low
            ),
        },
        "projected_balance_end_eur": _r(end_balance),
    }

    # ── next months ──
    next_months = []
    balance = end_balance
    first_below = None
    for k in range(1, months + 1):
        m = shift_month(month, k)
        lo, hi = f"{m}-01", _month_end(m).isoformat()
        in_m = None if bills is None else [b for b in bills if lo <= b["date"] <= hi]
        rec = (
            None
            if in_m is None
            else sum(b["amount_eur"] for b in in_m if b["kind"] == "spending")
        )
        rec_sv = (
            None
            if in_m is None
            else sum(b["amount_eur"] for b in in_m if b["kind"] == "savings")
        )
        inc = None if avg_inc is None else avg_inc["avg"]
        var = None if avg_var is None else avg_var["avg"]
        sv = None if avg_sav is None else avg_sav["avg"]
        spending = _add(rec, var)
        net = _add(inc, None if spending is None else -spending)
        change = _add(net, _neg(rec_sv), _neg(sv))
        balance = None if balance is None or change is None else balance + change
        below = balance is not None and balance < 0
        if below and first_below is None:
            first_below = m
        next_months.append(
            {
                "month": m,
                "income_eur": _r(inc),
                "recurring_eur": _r(rec),
                "variable_eur": _r(var),
                "savings_eur": _r(_add(rec_sv, sv)),
                "spending_eur": _r(spending),
                "net_eur": _r(net),
                "balance_change_eur": _r(change),
                "projected_balance_eur": _r(balance),
                "balance_below_zero": below,
                "range": {
                    "net_low_eur": _r(
                        _add(
                            inc,
                            (
                                None
                                if rec is None or avg_var is None
                                else -(rec + avg_var["high"])
                            ),
                        )
                    ),
                    "net_high_eur": _r(
                        _add(
                            inc,
                            (
                                None
                                if rec is None or avg_var is None
                                else -(rec + avg_var["low"])
                            ),
                        )
                    ),
                },
                "known_bill_count": 0 if in_m is None else len(in_m),
            }
        )

    assumptions = [
        "Income is the average income of the complete months below, and "
        "everyday spending is their average spending once recurring charges "
        "are taken out; rows marked one-off are left out of both.",
        "Recurring charges (subscriptions, bills, quarterly and yearly) are "
        "placed on the dates the detection expects them, once each; a charge "
        "that was due but never imported is not counted.",
        "Money moved to Invest / House purchase is shown as savings, not "
        "spending. Transfers to your own accounts, such as a broker, are not "
        "projected, so the balance is a bank-account balance.",
        "Amounts are in EUR at today's exchange rate.",
        "The rest of the current month is measured from the date the data "
        "runs to, not from today.",
    ]
    if base and base["months"]:
        assumptions.append("Baseline months: " + ", ".join(base["months"]) + ".")
    return {
        "as_of": today.isoformat(),
        "data_through": through.isoformat() if through else None,
        "accounts": account_out,
        "balance": {
            "start_eur": _r(start_balance),
            "included": [a["name"] for a in with_balance],
            "excluded": [a["name"] for a in without],
        },
        "baseline": (
            None
            if base is None
            else {
                "months": base["months"],
                "variable_eur": _stat_out(avg_var),
                "income_eur": _stat_out(avg_inc),
                "savings_eur": _stat_out(avg_sav),
            }
        ),
        "this_month": this_month,
        "next_months": next_months,
        "known_bills": [] if bills is None else bills,
        "first_below_zero": first_below,
        "assumptions": assumptions,
        "warnings": warnings,
    }


def _stat_out(stat: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if stat is None:
        return None
    return {k: round(v, 2) for k, v in stat.items()}


# ── loading ─────────────────────────────────────────────────────────────────


def load_forecast(
    db: Any,
    fx: Callable[[str], float],
    today: date,
    months: int = DEFAULT_FORECAST_MONTHS,
    portfolio_id: Optional[int] = None,
) -> Dict[str, Any]:
    """``compute_forecast`` over the database; every source fails on its own."""
    warnings: List[str] = []

    rows = tree = series = accounts = None
    try:
        start = shift_month(today.strftime("%Y-%m"), -_HISTORY_MONTHS)
        rows = load_rows(db, fx, start_date=f"{start}-01", portfolio_id=portfolio_id)
    except Exception as e:
        warnings.append(f"Bank rows could not be read: {e}")
    try:
        tree = {r["name"]: r for r in db.list_spending_categories_tree()}
    except Exception as e:
        warnings.append(f"The category tree could not be read: {e}")
    try:
        series = load_recurring(db, portfolio_id=portfolio_id)
    except Exception as e:
        warnings.append(f"Recurring charges could not be read: {e}")
    try:
        accounts = _load_accounts(db, fx, portfolio_id)
    except Exception as e:
        warnings.append(f"Bank accounts could not be read: {e}")

    return compute_forecast(
        rows, tree, series, accounts, fx, today, months, warnings=warnings
    )


def _load_accounts(
    db: Any, fx: Callable[[str], float], portfolio_id: Optional[int]
) -> List[dict]:
    """Bank accounts with their data-through date and latest stored balance."""
    last = last_import_dates(db)
    out = []
    for p in db.get_all_portfolios():
        if p.get("account_type") != "bank":
            continue
        if portfolio_id is not None and p["id"] != portfolio_id:
            continue
        latest = db.get_latest_bank_balance(p["id"])
        balance = balance_date = None
        if latest is not None:
            balance = float(latest["balance"]) * fx(latest.get("currency") or "EUR")
            balance_date = str(latest["date"])[:10]
        out.append(
            {
                "portfolio_id": p["id"],
                "name": p["name"],
                "currency": p.get("base_currency") or "EUR",
                "data_through": last.get(p["id"]),
                "balance_eur": balance,
                "balance_date": balance_date,
            }
        )
    return out
