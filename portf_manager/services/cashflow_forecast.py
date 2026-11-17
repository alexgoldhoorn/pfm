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
- Planned items (entered by hand) and the estimated quarterly tax payments are
  placed on their dates, once each, and never enter a baseline. When the tax
  estimate is in use, the quarterly recurring series in the allocator's
  tax-paid categories are taken out of the known bills, and the rows of those
  categories out of the forecast's everyday baseline, so the tax is not counted
  twice. A yearly or other-cadence series in those categories stays a known
  bill. A one-time annual tax settlement is not estimated.
"""

import calendar
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services import deployable
from portf_manager.services.life_scenarios import complete_months
from portf_manager.services.recurring import (
    RecurringSeries,
    due_dates,
    last_import_dates,
    load_recurring,
)
from portf_manager.services.spending_insights import (
    data_through,
    effective_data_through,
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


def baseline_month_list(
    rows: List[dict], through: Optional[date], today: date
) -> List[str]:
    """Baseline months: complete, with imported rows, and covered by the data.

    ``through`` is the effective data-through date (the earliest last import
    over the accounts in the window); a month ending after it is partial.
    The month in progress never counts. Shared by the forecast and the budget
    pace so both read the same months.
    """
    return [
        m
        for m in complete_months(rows, today, BASELINE_MONTHS)
        if through is not None and _month_end(m) <= through
    ]


def everyday_outflows(
    rows: List[dict],
    tree: Dict[str, dict],
    recurring_ids: set,
    months: List[str],
    exclude_categories: frozenset = frozenset(),
) -> Dict[str, Dict[str, float]]:
    """Everyday spending per month and category: the baseline rule, once.

    Non-transfer outflows outside the savings categories, with one-off rows
    and rows that belong to a recurring series left out (recurring charges are
    added back on the dates they fall due, so counting them here too would
    count them twice). ``exclude_categories`` (default none) also leaves out the
    rows of those categories; only the forecast uses it, to keep the tax-paid
    rows out of the baseline while the tax estimate stands in for them.

    Returns:
        ``{month: {category: amount}}`` for each of ``months``.
    """
    saved = savings_names(tree)
    out: Dict[str, Dict[str, float]] = {m: {} for m in months}
    for r in rows:
        m = r["date"][:7]
        if (
            m not in out
            or r["is_transfer"]
            or r["one_off"]
            or r["amount_eur"] >= 0
            or r["id"] in recurring_ids
            or r["category"] in saved
            or r["category"] in exclude_categories
        ):
            continue
        cats = out[m]
        cats[r["category"]] = cats.get(r["category"], 0.0) - r["amount_eur"]
    return out


def baseline(
    rows: List[dict],
    tree: Dict[str, dict],
    recurring_ids: set,
    through: Optional[date],
    today: date,
    exclude_categories: frozenset = frozenset(),
) -> Dict[str, Any]:
    """Monthly averages of what is left once recurring charges are taken out.

    Args:
        rows: ``load_rows`` output (transfers included).
        tree: ``{name: row}`` category tree.
        recurring_ids: ids of every row that belongs to a recurring series.
        through: effective data-through date (see ``baseline_month_list``).
        today: reference date; the month in progress never counts.
        exclude_categories: categories left out of the everyday spending
            (see ``everyday_outflows``).

    Returns:
        ``{months, variable, income, savings}``: the baseline months used and,
        for each figure, ``{avg, low, high}`` (low and high are the smallest
        and largest single month), or ``None`` for each when no month
        qualifies.
    """
    used = baseline_month_list(rows, through, today)
    if not used:
        return {"months": [], "variable": None, "income": None, "savings": None}
    saved = savings_names(tree)
    variable = {
        m: sum(cats.values())
        for m, cats in everyday_outflows(
            rows, tree, recurring_ids, used, exclude_categories
        ).items()
    }
    income = {m: 0.0 for m in used}
    savings = {m: 0.0 for m in used}
    for r in rows:
        m = r["date"][:7]
        if m not in income or r["is_transfer"] or r["one_off"]:
            continue
        amount = r["amount_eur"]
        if amount > 0:
            if not _is_refund(tree, r["category"]):
                income[m] += amount
        elif r["id"] not in recurring_ids and r["category"] in saved:
            savings[m] += -amount

    def stat(values: Dict[str, float]) -> Dict[str, float]:
        vals = list(values.values())
        return {"avg": sum(vals) / len(vals), "low": min(vals), "high": max(vals)}

    return {
        "months": used,
        "variable": stat(variable),
        "income": stat(income),
        "savings": stat(savings),
    }


# ── tax payments and planned items ──────────────────────────────────────────


def _quarter_back(year: int, q: int, n: int) -> tuple:
    """The quarter ``n`` quarters before (year, q)."""
    for _ in range(n):
        year, q = deployable._previous(year, q)
    return year, q


def estimated_tax_payments(
    rows: List[dict],
    tax_rows: List[dict],
    settings: dict,
    today: date,
    horizon_end: date,
    through: Optional[date] = None,
    warnings: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Quarterly tax payments still to come, from the allocator's reserve maths.

    Every figure comes from ``deployable.quarter_reserve``. The previous
    quarter contributes what is still unpaid (basis ``accrued``); the current
    quarter the larger of its accrual so far and the average accrual of the
    last up to four complete quarters; later quarters that average.

    Args:
        rows: Non-transfer rows ``{date, amount_eur, category, merchant}``.
        tax_rows: The rows in the tax-paid categories.
        settings: Allocator settings (``deployable.load_settings``).
        today: Reference date.
        horizon_end: Last day of the forecast horizon.
        through: Effective data-through date; payments due before it are
            already in the statements. Defaults to ``today``.
        warnings: Receives a message when nothing can be estimated.

    Returns:
        ``[{quarter, due, amount_eur, basis}]`` for each quarter whose due
        date lies between ``through`` and ``horizon_end``.
    """
    start = through or today
    # the past-due rule is judged at the data-through date: the balance starts
    # there, so a quarter still unpaid in the statements is still owed
    ref = start
    year, q = deployable.quarter_of(today)
    # a complete quarter counts only when every month of it has imported rows
    months_with_rows = {r["date"][:7] for r in rows}

    def covered(y: int, qq: int) -> bool:
        first, _ = deployable.quarter_bounds(y, qq)
        return all(
            shift_month(first.strftime("%Y-%m"), k) in months_with_rows
            for k in range(3)
        )

    history = []
    for n in range(1, 9):
        y, qq = _quarter_back(year, q, n)
        if covered(y, qq):
            history.append(
                deployable.quarter_reserve(rows, tax_rows, y, qq, settings, ref)
            )
        if len(history) == 4:
            break
    if not any(h["gross_eur"] > 0 for h in history):
        if warnings is not None:
            warnings.append(
                "No complete quarter of imported statements with work income: "
                "the quarterly tax is not estimated."
            )
        return []
    average = sum(h["accrued_eur"] for h in history) / len(history)

    out: List[Dict[str, Any]] = []
    py, pq = deployable._previous(year, q)
    prev = deployable.quarter_reserve(rows, tax_rows, py, pq, settings, ref)
    prev_due = date.fromisoformat(prev["due"])
    if start <= prev_due <= horizon_end and prev["remaining_eur"] > 0.005:
        out.append(
            {
                "quarter": prev["quarter"],
                "due": prev["due"],
                "amount_eur": round(prev["remaining_eur"], 2),
                "basis": "accrued",
            }
        )
    y, qq = year, q
    for _ in range(20):
        due = deployable.due_date(y, qq)
        if due > horizon_end:
            break
        if due >= start:
            amount, basis = average, "average"
            if (y, qq) == (year, q):
                cur = deployable.quarter_reserve(rows, tax_rows, y, qq, settings, ref)
                if cur["accrued_eur"] >= average:
                    amount, basis = cur["accrued_eur"], "accrued"
            if amount > 0.005:
                out.append(
                    {
                        "quarter": f"{y}-Q{qq}",
                        "due": due.isoformat(),
                        "amount_eur": round(amount, 2),
                        "basis": basis,
                    }
                )
        y, qq = (y, qq + 1) if qq < 4 else (y + 1, 1)
    out.sort(key=lambda p: p["due"])
    return out


def _planned_entries(
    planned: List[dict],
    fx: Callable[[str], float],
    cutoff: date,
    horizon_end: date,
    warnings: List[str],
) -> tuple:
    """Split planned items into the ones to project and the full listing.

    Args:
        planned: Open items as stored (``what_if`` marks an unsaved one).
        fx: currency -> EUR rate.
        cutoff: The data-through date; an item dated before it is overdue.
        horizon_end: Last day of the horizon.
        warnings: Receives the overdue / unconvertible messages.

    Returns:
        ``(projected, listing)``: projected entries are ``{date, amount_eur,
        kind}`` with a positive magnitude; the listing is the response's
        ``planned_items``.
    """
    projected: List[Dict[str, Any]] = []
    listing: List[Dict[str, Any]] = []
    overdue = 0
    for it in sorted(planned, key=lambda i: (i["date"], i.get("id") or 0)):
        what_if = bool(it.get("what_if"))
        entry = {
            "id": None if what_if else it["id"],
            "date": it["date"],
            "description": it["description"],
            "amount_eur": None,
            "kind": it["kind"],
            "status": "what_if" if what_if else "projected",
        }
        try:
            amount = float(it["amount"]) * fx((it.get("currency") or "EUR").upper())
        except Exception as e:
            warnings.append(f"Planned item '{it['description']}' not converted: {e}")
            entry["status"] = "unconverted"
            listing.append(entry)
            continue
        entry["amount_eur"] = round(amount, 2)
        listing.append(entry)
        if it["date"] < cutoff.isoformat():
            if what_if:
                warnings.append(
                    "The what-if date is before the data-through date: it is "
                    "not counted."
                )
            else:
                entry["status"] = "overdue"
                overdue += 1
            continue
        if it["date"] > horizon_end.isoformat():
            if what_if:
                warnings.append("The what-if date is beyond the forecast horizon.")
            else:
                entry["status"] = "beyond_horizon"
            continue
        projected.append(
            {"date": it["date"], "amount_eur": abs(amount), "kind": it["kind"]}
        )
    if overdue:
        warnings.append(
            f"{overdue} planned item(s) are dated before the data-through date "
            "and are not projected: mark them done or move their date."
        )
    return projected, listing


def _lowest_balance(out: Dict[str, Any]) -> tuple:
    """(lowest month-end balance, its month) of a forecast, or (None, None)."""
    this = out["this_month"]
    points = [(this["month"], this["projected_balance_end_eur"])]
    points += [(m["month"], m["projected_balance_eur"]) for m in out["next_months"]]
    known = [(b, m) for m, b in points if b is not None]
    if not known:
        return None, None
    return min(known)


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
    planned: Optional[List[dict]] = None,
    tax: Optional[Dict[str, Any]] = None,
    what_if: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The forecast from already-loaded sources; any source may be ``None``.

    Adds, on top of the sources below, the open ``planned`` items, the
    estimated quarterly ``tax`` payments and an optional unsaved ``what_if``
    item (see ``_forecast`` for the figures). With none of the three the
    result is the plain forecast plus empty planned fields.

    Args:
        planned: Open planned items as stored (``planned_items`` rows), or
            None.
        tax: ``{settings, rows}`` for the tax estimate (allocator settings and
            the non-transfer rows of the last five quarters), or None to leave
            tax out.
        what_if: ``{amount, date, kind}`` of one unsaved item; ``amount`` is a
            positive magnitude and ``kind`` decides the sign. The response then
            carries a ``what_if`` block comparing the lowest balance with and
            without it.

    Returns:
        The same dict as ``_forecast``, plus ``what_if`` when one was given.
    """
    if what_if is None:
        return _forecast(
            rows, tree, series, accounts, fx, today, months, warnings, planned, tax
        )
    sign = 1.0 if what_if["kind"] == "income" else -1.0
    extra = {
        "id": None,
        "date": what_if["date"],
        "description": "What-if",
        "amount": sign * abs(float(what_if["amount"])),
        "currency": "EUR",
        "kind": what_if["kind"],
        "what_if": True,
    }
    without = _forecast(
        rows, tree, series, accounts, fx, today, months, warnings, planned, tax
    )
    out = _forecast(
        rows,
        tree,
        series,
        accounts,
        fx,
        today,
        months,
        warnings,
        list(planned or []) + [extra],
        tax,
    )
    low, low_month = _lowest_balance(out)
    low_without, _ = _lowest_balance(without)
    out["what_if"] = {
        "amount_eur": round(extra["amount"], 2),
        "date": extra["date"],
        "lowest_balance_eur": _r(low),
        "lowest_balance_month": low_month,
        "lowest_balance_without_eur": _r(low_without),
        "goes_below_zero": None if low is None else low < 0,
    }
    return out


def _forecast(
    rows: Optional[List[dict]],
    tree: Optional[Dict[str, dict]],
    series: Optional[List[RecurringSeries]],
    accounts: Optional[List[dict]],
    fx: Callable[[str], float],
    today: date,
    months: int,
    warnings: Optional[List[str]],
    planned: Optional[List[dict]],
    tax: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """One forecast pass; see ``compute_forecast``.

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
        next_months, known_bills, planned_items, tax_payments,
        first_below_zero, assumptions, warnings}``. Figures a missing source
        makes unknowable are ``None``.
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
    # The data reaches only as far as the account that imported least: the
    # earliest last-import date over the accounts that have rows in the
    # baseline window (an account with none there is ignored).
    window_start = f"{shift_month(month, -BASELINE_MONTHS)}-01"
    with_rows = (
        None
        if rows is None
        else {r["portfolio_id"] for r in rows if r["date"] >= window_start}
    )
    counted = {
        pid: t.isoformat()
        for pid, t in through_by_account.items()
        if with_rows is None or pid in with_rows
    }
    eff = effective_data_through(counted, window_start)
    through = date.fromisoformat(eff["effective"]) if eff["effective"] else None
    if through is None and through_by_account:
        through = max(through_by_account.values())
    if through is None and rows is not None:
        found = data_through(rows)
        through = date.fromisoformat(found) if found else None
    if through is None:
        warnings.append("No bank statement is imported: nothing to project from.")
    elif eff["lagging"] is not None:
        names = {a["portfolio_id"]: a["name"] for a in accounts or []}
        warnings.append(
            f"{names.get(eff['lagging'], 'One account')} has imported only "
            f"through {through.isoformat()}, behind the others: the rest of the "
            "month and the baseline months are measured from there."
        )

    horizon_end = _month_end(shift_month(month, months))
    cutoff = through or today

    # ── tax payments ──
    tax_payments: List[Dict[str, Any]] = []
    tax_cats: frozenset = frozenset()
    tax_settings = (tax or {}).get("settings") or {}
    if tax is not None:
        if not tax_settings.get("work_income_merchants"):
            warnings.append(
                "No work-income merchants are ticked in the allocator settings "
                "(Settings > Allocator), so the quarterly tax is not estimated."
            )
        else:
            try:
                paid = set(tax_settings["tax_paid_categories"])
                tax_payments = estimated_tax_payments(
                    tax["rows"],
                    [r for r in tax["rows"] if r["category"] in paid],
                    tax_settings,
                    today,
                    horizon_end,
                    through=through,
                    warnings=warnings,
                )
                if tax_payments:
                    tax_cats = frozenset(paid)
            except Exception as e:
                tax_payments = []
                warnings.append(f"The quarterly tax could not be estimated: {e}")
    # the estimate stands in for the quarterly tax series (the pagos
    # fraccionados) only; a yearly or other-cadence series stays a known bill
    if tax_cats and series is not None:
        series = [
            s
            for s in series
            if not (s.category in tax_cats and s.cadence == "quarterly")
        ]

    # ── planned items ──
    planned_proj, planned_list = _planned_entries(
        list(planned or []), fx, cutoff, horizon_end, warnings
    )
    extras = [
        {"date": p["due"], "amount_eur": p["amount_eur"], "kind": "tax"}
        for p in tax_payments
    ] + planned_proj

    def extras_sum(kind: str, lo: str, hi: str) -> float:
        return sum(
            e["amount_eur"]
            for e in extras
            if e["kind"] == kind and lo <= e["date"] <= hi
        )

    # ── recurring bills ──
    bills: Optional[List[Dict[str, Any]]] = None
    recurring_ids: set = set()
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
        base = baseline(rows, tree, recurring_ids, through, today, tax_cats)
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

    # Bills dated before this month (the latest import is in the previous
    # month) still move the balance but are not this month's spending.
    month_start = f"{month}-01"
    rec_spend = rec_spend_all = rec_save_all = prior_bills = None
    if bills is not None:
        until = month_end.isoformat()
        due = [b for b in bills if b["date"] <= until]
        in_this = [b for b in due if b["date"] >= month_start]
        rec_spend = sum(b["amount_eur"] for b in in_this if b["kind"] == "spending")
        rec_spend_all = sum(b["amount_eur"] for b in due if b["kind"] == "spending")
        rec_save_all = sum(b["amount_eur"] for b in due if b["kind"] == "savings")
        prior_bills = sum(
            b["amount_eur"]
            for b in due
            if b["date"] < month_start and b["kind"] == "spending"
        )

    # Planned items and tax payments from the data-through date to month end
    # move the balance; those dated in the month also count in its figures.
    ext_lo, ext_hi = cutoff.isoformat(), month_end.isoformat()
    ext_all = {
        k: extras_sum(k, ext_lo, ext_hi)
        for k in ("spending", "savings", "income", "tax")
    }
    ext_mon = {
        k: extras_sum(k, max(ext_lo, month_start), ext_hi)
        for k in ("spending", "savings", "income", "tax")
    }
    ext_spend_mon = ext_mon["spending"] + ext_mon["tax"]

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

    proj_spend = _add(spent, rec_spend, var_rem, ext_spend_mon)
    proj_income = None if inc_rem is None else income_in + inc_rem + ext_mon["income"]
    proj_net = _add(proj_income, None if proj_spend is None else -proj_spend)
    spend_low = _add(spent, rec_spend, scaled(avg_var, "low"), ext_spend_mon)
    spend_high = _add(spent, rec_spend, scaled(avg_var, "high"), ext_spend_mon)
    # what is still to happen between the data-through date and month end
    remaining_change = _add(
        None if inc_rem is None else inc_rem + ext_all["income"],
        _neg(rec_spend_all),
        _neg(var_rem),
        _neg(rec_save_all),
        _neg(sav_rem),
        -(ext_all["spending"] + ext_all["tax"] + ext_all["savings"]),
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
        "prior_month_bills_eur": _r(prior_bills),
        "planned_spending_eur": _r(ext_mon["spending"]),
        "planned_savings_eur": _r(ext_mon["savings"]),
        "planned_income_eur": _r(ext_mon["income"]),
        "tax_eur": _r(ext_mon["tax"]),
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
        p_spend = extras_sum("spending", lo, hi)
        p_sav = extras_sum("savings", lo, hi)
        p_inc = extras_sum("income", lo, hi)
        p_tax = extras_sum("tax", lo, hi)
        inc = None if avg_inc is None else avg_inc["avg"] + p_inc
        var = None if avg_var is None else avg_var["avg"]
        sv = None if avg_sav is None else avg_sav["avg"]
        spending = _add(rec, var, p_spend, p_tax)
        net = _add(inc, None if spending is None else -spending)
        rec_sv = None if rec_sv is None else rec_sv + p_sav
        change = _add(net, _neg(rec_sv), _neg(sv))
        balance = None if balance is None or change is None else balance + change
        below = balance is not None and balance < 0
        if below and first_below is None:
            first_below = m
        next_months.append(
            {
                "month": m,
                "income_eur": _r(inc),
                "planned_spending_eur": _r(p_spend),
                "planned_savings_eur": _r(p_sav),
                "planned_income_eur": _r(p_inc),
                "tax_eur": _r(p_tax),
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
                                else -(rec + avg_var["high"] + p_spend + p_tax)
                            ),
                        )
                    ),
                    "net_high_eur": _r(
                        _add(
                            inc,
                            (
                                None
                                if rec is None or avg_var is None
                                else -(rec + avg_var["low"] + p_spend + p_tax)
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
    if planned_list:
        assumptions.append(
            "Planned items are placed once, on their dates, and never enter an "
            "average; an item dated before the data-through date is not projected."
        )
    if tax_payments:
        assumptions.append(
            "Quarterly tax is an estimate from the allocator settings: "
            f"IVA {tax_settings['iva_pct']:g}%, IRPF {tax_settings['irpf_pct']:g}%. "
            "The quarterly tax charges and the tax-paid rows are left out of "
            "the bills and the everyday baseline so it is not counted twice. "
            "A one-time annual tax settlement is not estimated."
        )
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
        "planned_items": planned_list,
        "tax_payments": tax_payments,
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
    include_tax: bool = True,
    what_if: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``compute_forecast`` over the database; every source fails on its own.

    Args:
        db: Database.
        fx: currency -> EUR rate.
        today: Reference date.
        months: Months to project after the current one.
        portfolio_id: Limit to one bank account (planned items tied to
            another account are left out).
        include_tax: Add the estimated quarterly tax payments.
        what_if: ``{amount, date, kind}`` of one unsaved item.
    """
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

    planned = None
    try:
        planned = [
            i
            for i in db.list_planned_items(include_done=False)
            if portfolio_id is None or i.get("portfolio_id") in (None, portfolio_id)
        ]
    except Exception as e:
        warnings.append(f"Planned items could not be read: {e}")
    tax = (
        _load_tax_inputs(db, fx, today, portfolio_id, warnings) if include_tax else None
    )

    return compute_forecast(
        rows,
        tree,
        series,
        accounts,
        fx,
        today,
        months,
        warnings=warnings,
        planned=planned,
        tax=tax,
        what_if=what_if,
    )


def _load_tax_inputs(
    db: Any,
    fx: Callable[[str], float],
    today: date,
    portfolio_id: Optional[int],
    warnings: List[str],
) -> Optional[Dict[str, Any]]:
    """Allocator settings and the last five quarters of rows, or None.

    The rows are the non-transfer bank rows from the start of the quarter four
    quarters before the current one (the average needs four complete quarters).
    """
    try:
        settings = deployable.load_settings(db)
        if not settings.get("work_income_merchants"):
            return {"settings": settings, "rows": []}
        year, q = deployable.quarter_of(today)
        y, qq = _quarter_back(year, q, 4)
        start = deployable.quarter_bounds(y, qq)[0].isoformat()
        rows = [
            r
            for r in load_rows(db, fx, start_date=start, portfolio_id=portfolio_id)
            if not r["is_transfer"]
        ]
        return {"settings": settings, "rows": rows}
    except Exception as e:
        warnings.append(f"The quarterly tax could not be estimated: {e}")
        return None


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
