"""Month close: a checklist, the month's figures, a stored mark and a report.

No maths of its own and no LLM. The checklist and the figures come from the
existing spending services (``spending_insights``, ``spending_uncategorized``,
``spending_year``, ``recurring``, ``budget`` through ``spending_review``); this
module picks, sequences and stores. A close is a mark, not a freeze: nothing
is locked, and ``closed.stale`` says when the figures recomputed now differ
from the stored ones.

Every step and figure is built on its own: a source that fails becomes an
``unknown`` step or a ``None`` part plus a warning, never an exception.
"""

import calendar
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services import (
    spending_insights,
    spending_review,
    spending_uncategorized,
    spending_year,
)
from portf_manager.services.action_item_acks import ack_is_active, list_acks
from portf_manager.services.recurring import load_recurring

# The imports step looks at every account with rows this many months back.
IMPORT_LOOKBACK_MONTHS = 12
# Figures that moved by more than this many euros since the close make it stale.
STALE_TOLERANCE_EUR = 1.0
# The one-offs reminder: the month's largest outflows from this size up (the
# anomaly rule's "large first charge" threshold), at most this many.
ONE_OFF_REMINDER_MIN_EUR = spending_insights.NEW_MERCHANT_MIN_EUR
ONE_OFF_REMINDER_TOP = 5
# A merchant charged within this many days before the month counts as usual.
ONE_OFF_USUAL_DAYS = 90
# The Action Item appears from this day of the following month.
ITEM_FROM_DAY = 5
TOP_CATEGORIES = 5

STEP_LABELS = {
    "imports": "Every bank account is imported through the month end",
    "uncategorized": "No uncategorised outflows",
    "transfers": "Transfers are linked",
    "anomalies": "Unusual charges are checked",
    "recurring": "No recurring charge missing",
    "one_offs": "Large one-time charges are marked one-off",
}
STEP_LINKS = {
    "imports": "importexport",
    "uncategorized": "spending",
    "transfers": "spending",
    "anomalies": "spending",
    "recurring": "spending",
    "one_offs": "spending",
}


class MonthCloseError(Exception):
    """A close that cannot proceed; ``status`` is the HTTP code to answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def check_month(month: str, today: date, complete_only: bool = False) -> None:
    """Validate a ``YYYY-MM`` month.

    Args:
        month: The month to check.
        today: Reference date.
        complete_only: Also refuse the month in progress (needed to close).

    Raises:
        MonthCloseError: 400 for a future month, or for the month in progress
            when ``complete_only``.
    """
    current = today.strftime("%Y-%m")
    if month > current:
        raise MonthCloseError(400, "month cannot be after the month in progress")
    if complete_only and month == current:
        raise MonthCloseError(400, f"{month} is still in progress and cannot be closed")


def _step(key: str, status: str, count: Optional[int], detail: str) -> dict:
    return {
        "key": key,
        "label": STEP_LABELS[key],
        "status": status,
        "count": count,
        "detail": detail,
        "link_page": STEP_LINKS[key],
    }


def _unknown(key: str, why: str) -> dict:
    return _step(key, "unknown", None, why)


def _names(items: List[str], top: int = 3) -> str:
    """'A, B, C and 2 more'."""
    shown = items[:top]
    more = len(items) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def imports_step(rows: List[dict], month: str) -> dict:
    """Step 1: every account with recent rows reaches the month's last day.

    Args:
        rows: ``spending_insights.load_rows`` output (no upper date bound).
        month: ``YYYY-MM``.

    Returns:
        The ``imports`` step; ``todo`` (which blocks) when an account lags or
        nothing is imported.
    """
    end = spending_insights._month_end(month).isoformat()
    # a month nothing was imported for is never ready, however fresh the accounts
    if not any(r["date"][:7] == month for r in rows):
        return _step("imports", "todo", 0, f"Nothing is imported for {month}.")
    start = f"{spending_insights.shift_month(month, -IMPORT_LOOKBACK_MONTHS)}-01"
    info = spending_insights.account_data_through(rows, start=start)
    counted = [info["by_account"][pid] for pid in info["accounts"]]
    if not counted:
        return _step("imports", "todo", 0, "No bank account has recent imports.")
    lagging = [a for a in counted if a["data_through"] < end]
    if lagging:
        who = _names([f"{a['name']} (through {a['data_through']})" for a in lagging])
        return _step(
            "imports", "todo", len(lagging), f"Import statements up to {end}: {who}."
        )
    return _step("imports", "ok", 0, f"All {len(counted)} account(s) reach {end}.")


def _no_rows(key: str) -> dict:
    return _unknown(key, "Nothing is imported for this month.")


def _uncategorized_step(rows, tree, month) -> dict:
    figures = spending_uncategorized.month_figures(rows, tree, month)
    if not figures["count"]:
        return _step("uncategorized", "ok", 0, "Every outflow has a category.")
    share = figures["share_pct"]
    return _step(
        "uncategorized",
        "warn",
        figures["count"],
        f"{figures['count']} outflow(s) worth {figures['total_eur']:,.0f} EUR"
        + (f", {share:.0f}% of the month's spending," if share is not None else "")
        + " have no category.",
    )


def _transfers_step(db, month) -> dict:
    start = f"{month}-01"
    end = spending_insights._month_end(month).isoformat()
    found = db.list_spending_transactions(
        is_transfer=True, transfer_linked=False, start_date=start, end_date=end
    )
    if not found:
        return _step("transfers", "ok", 0, "Every transfer is linked.")
    pattern = sum(1 for r in found if r.get("transfer_source") == "pattern")
    return _step(
        "transfers",
        "warn",
        len(found),
        f"{len(found)} transfer(s) have no matching leg"
        + (f", {pattern} flagged by a pattern" if pattern else "")
        + ".",
    )


def _anomalies_step(db, rows, tree, month, today) -> dict:
    start = date.fromisoformat(f"{month}-01")
    found = spending_insights.find_anomalies(
        rows, tree, today, max(1, (today - start).days)
    )
    acks = list_acks(db)
    open_items = [
        i
        for i in found["items"]
        if i["date"][:7] == month and not ack_is_active(acks.get(i["id"]), today)
    ]
    if not open_items:
        return _step("anomalies", "ok", 0, "No unchecked unusual charge.")
    label = [i.get("merchant") or i.get("category") or "?" for i in open_items]
    return _step(
        "anomalies",
        "warn",
        len(open_items),
        f"{len(open_items)} unusual charge(s) not checked: {_names(label)}.",
    )


def _recurring_step(db, month) -> dict:
    start = date.fromisoformat(f"{month}-01")
    end = spending_insights._month_end(month)
    # a resolved series carries status cancelled / paid_elsewhere, never missed
    missed = [
        s
        for s in load_recurring(db)
        if s.status == "missed" and start <= date.fromisoformat(s.next_expected) <= end
    ]
    if not missed:
        return _step("recurring", "ok", 0, "No recurring charge is missing.")
    return _step(
        "recurring",
        "warn",
        len(missed),
        f"{len(missed)} recurring charge(s) did not arrive: "
        f"{_names([s.merchant for s in missed])}.",
    )


def _one_offs_step(rows, tree, month) -> dict:
    start = f"{month}-01"
    usual_from = (
        date.fromisoformat(start) - timedelta(days=ONE_OFF_USUAL_DAYS)
    ).isoformat()
    spend = spending_insights._spend_rows(rows, tree)
    # a merchant charged in the months before is a usual payment (the rent),
    # not a candidate for "this will not repeat"
    usual = {
        spending_insights._merchant_key(r)
        for r in spend
        if usual_from <= r["date"] < start
    }
    big = sorted(
        (
            r
            for r in spend
            if r["date"][:7] == month
            and not r["one_off"]
            and -r["amount_eur"] >= ONE_OFF_REMINDER_MIN_EUR
            and spending_insights._merchant_key(r) not in usual
        ),
        key=lambda r: r["amount_eur"],
    )[:ONE_OFF_REMINDER_TOP]
    if not big:
        return _step("one_offs", "ok", 0, "No large charge waits to be judged.")
    return _step(
        "one_offs",
        "warn",
        len(big),
        f"{len(big)} large charge(s) of {ONE_OFF_REMINDER_MIN_EUR:,.0f} EUR or "
        "more from merchants not charged in the last 3 months are not marked "
        "one-off: mark the ones that will not repeat.",
    )


def build_checklist(
    db: Any,
    fx: Callable[[str], float],
    month: str,
    today: date,
    warnings: List[str],
    rows: Optional[List[dict]] = None,
    tree: Optional[Dict[str, dict]] = None,
) -> List[dict]:
    """The six checklist steps; a failing source is ``unknown`` + a warning.

    Args:
        db: Database handle.
        fx: currency -> EUR rate.
        month: ``YYYY-MM``.
        today: Reference date.
        warnings: Appended to when a step cannot be built.
        rows: Already loaded rows, or None to load them here.
        tree: Already loaded category tree, or None to load it here.
    """
    if rows is None or tree is None:
        try:
            rows, tree = load_rows_and_tree(db, fx, month)
        except Exception as e:
            warnings.append(f"Bank rows could not be read: {e}")
            return [_unknown(k, "Bank rows could not be read.") for k in STEP_LABELS]
    has_rows = any(r["date"][:7] == month for r in rows)

    def run(key: str, build: Callable[[], dict], needs_rows: bool = True) -> dict:
        if needs_rows and not has_rows:
            return _no_rows(key)
        try:
            return build()
        except Exception as e:
            warnings.append(f"{STEP_LABELS[key]}: could not be checked ({e})")
            return _unknown(key, "Could not be checked.")

    return [
        run("imports", lambda: imports_step(rows, month), needs_rows=False),
        run("uncategorized", lambda: _uncategorized_step(rows, tree, month)),
        run("transfers", lambda: _transfers_step(db, month)),
        run("anomalies", lambda: _anomalies_step(db, rows, tree, month, today)),
        run("recurring", lambda: _recurring_step(db, month), needs_rows=False),
        run("one_offs", lambda: _one_offs_step(rows, tree, month)),
    ]


def load_rows_and_tree(db: Any, fx: Callable[[str], float], month: str) -> tuple:
    """Rows from 400 days before the month on (no upper bound), and the tree."""
    start = date.fromisoformat(f"{month}-01") - timedelta(days=400)
    rows = spending_insights.load_rows(db, fx, start_date=start.isoformat())
    tree = {r["name"]: r for r in db.list_spending_categories_tree()}
    return rows, tree


def is_ready(checklist: List[dict]) -> bool:
    """A month is ready to close when the imports step is ``ok``."""
    return any(s["key"] == "imports" and s["status"] == "ok" for s in checklist)


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def build_figures(
    db: Any,
    fx: Callable[[str], float],
    month: str,
    today: date,
    warnings: List[str],
    rows: Optional[List[dict]] = None,
    tree: Optional[Dict[str, dict]] = None,
) -> dict:
    """The month's figures, all from existing services.

    Returns:
        ``income_eur``, ``spending_eur``, ``savings_eur``, ``net_eur``,
        ``savings_rate_pct``, ``budget`` (the active budget's planned and
        actual, or None), ``top_categories`` and ``versus_normal``. A part that
        fails, or a month with nothing imported, is ``None`` (never zero).
    """
    figures: Dict[str, Any] = {
        "income_eur": None,
        "spending_eur": None,
        "savings_eur": None,
        "net_eur": None,
        "savings_rate_pct": None,
        "budget": None,
        "top_categories": None,
        "versus_normal": None,
    }
    try:
        if rows is None or tree is None:
            rows, tree = load_rows_and_tree(db, fx, month)
    except Exception as e:
        warnings.append(f"Bank rows could not be read: {e}")
        rows = tree = None
    if rows is not None:
        try:
            entry = spending_year._month_entry(
                month, [r for r in rows if r["date"][:7] == month], tree, True
            )
            if entry["has_imports"]:
                income, spending = entry["income_eur"], entry["spending_eur"]
                net = round(income - spending, 2)
                figures.update(
                    income_eur=income,
                    spending_eur=spending,
                    savings_eur=entry["savings_eur"],
                    net_eur=net,
                    savings_rate_pct=(
                        round(100 * net / income, 1) if income > 0 else None
                    ),
                )
        except Exception as e:
            warnings.append(f"Month totals could not be built: {e}")
        try:
            cmp = spending_insights.compare_month(rows, tree, month, today)
            warnings.extend(cmp["warnings"])
            totals = cmp["totals"]
            figures["versus_normal"] = {
                "status": cmp["status"],
                "baseline_months": len(cmp["baseline_months"]),
                "actual_eur": totals["actual_eur"],
                "baseline_eur": totals["baseline_eur"],
                "delta_eur": totals["delta_eur"],
                "delta_pct": totals["delta_pct"],
            }
            ranked = sorted(
                (i for i in cmp["items"] if i["actual_eur"]),
                key=lambda i: (-i["actual_eur"], i["name"]),
            )[:TOP_CATEGORIES]
            figures["top_categories"] = [
                {
                    "name": i["name"],
                    "actual_eur": i["actual_eur"],
                    "baseline_eur": i["baseline_eur"],
                    "delta_eur": i["delta_eur"],
                }
                for i in ranked
            ]
        except Exception as e:
            warnings.append(f"The comparison could not be built: {e}")
    try:
        budget, budget_warnings = spending_review._budget_part(db, fx, month, today)
        warnings.extend(budget_warnings)
        if budget is not None and not budget["no_activity"]:
            figures["budget"] = {
                "name": budget["budget_name"],
                "sections": [
                    {
                        "key": s["key"],
                        "label": s["label"],
                        "planned_total": s["planned_total"],
                        "actual_total": s["actual_total"],
                        "variance_eur": s["variance_eur"],
                    }
                    for s in budget["sections"]
                ],
            }
    except Exception as e:
        warnings.append(f"The budget could not be built: {e}")
    return figures


def is_stale(stored: Optional[dict], current: dict) -> bool:
    """Whether income, spending or net moved by more than a euro since the close."""
    for key in ("income_eur", "spending_eur", "net_eur"):
        a, b = (stored or {}).get(key), current.get(key)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > STALE_TOLERANCE_EUR:
            return True
    return False


def default_month(today: date) -> str:
    """The last complete calendar month."""
    return spending_insights.previous_month(today)


def build_month_close(
    db: Any,
    fx: Callable[[str], float],
    today: date,
    month: Optional[str] = None,
) -> Dict[str, Any]:
    """The close state of a month: checklist, figures, and the stored mark.

    Args:
        db: Database handle.
        fx: currency -> EUR rate.
        today: Reference date.
        month: ``YYYY-MM``; default the last complete month.

    Returns:
        ``{month, closed, ready, checklist, figures, warnings}``. ``closed`` is
        None or ``{closed_at, notes, report_id, stale}``.

    Raises:
        MonthCloseError: 400 for a month after the one in progress.
    """
    month = month or default_month(today)
    check_month(month, today)
    warnings: List[str] = []
    rows = tree = None
    try:
        rows, tree = load_rows_and_tree(db, fx, month)
    except Exception as e:
        warnings.append(f"Bank rows could not be read: {e}")
    checklist = build_checklist(db, fx, month, today, warnings, rows, tree)
    figures = build_figures(db, fx, month, today, warnings, rows, tree)
    closed = None
    stored = db.get_month_close(month)
    if stored:
        closed = {
            "closed_at": stored["closed_at"],
            "notes": stored.get("notes"),
            "report_id": stored.get("report_id"),
            "stale": is_stale(stored.get("figures"), figures),
        }
    return {
        "month": month,
        "closed": closed,
        "ready": is_ready(checklist),
        "checklist": checklist,
        "figures": figures,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _eur(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:,.0f} EUR"


def render_month_close_report(month: str, figures: dict, checklist: List[dict]) -> str:
    """The month-close report as Markdown (whole euros, no LLM).

    Args:
        month: ``YYYY-MM``.
        figures: The stored figures.
        checklist: The checklist as it stood at close.
    """
    name = f"{calendar.month_name[int(month[5:7])]} {month[:4]}"
    lines = [f"# Month close {month}", "", f"Closing report for {name}.", ""]
    lines += ["## Figures", ""]
    rate = figures.get("savings_rate_pct")
    lines += [
        f"- Income: {_eur(figures.get('income_eur'))}",
        f"- Spending: {_eur(figures.get('spending_eur'))}",
        f"- Moved to savings: {_eur(figures.get('savings_eur'))}",
        f"- Net: {_eur(figures.get('net_eur'))}"
        + (f" ({rate:.0f}% of income)" if rate is not None else ""),
        "",
    ]
    normal = figures.get("versus_normal")
    if normal and normal.get("delta_eur") is not None:
        pct = normal.get("delta_pct")
        lines += [
            "## Against a normal month",
            "",
            f"Spending was {_eur(normal['actual_eur'])} against "
            f"{_eur(normal['baseline_eur'])} on average over "
            f"{normal['baseline_months']} month(s): "
            f"{normal['delta_eur']:+,.0f} EUR"
            + (f" ({pct:+.0f}%)" if pct is not None else "")
            + ".",
            "",
        ]
    top = figures.get("top_categories")
    if top:
        lines += ["## Top categories", ""]
        lines += [f"- {c['name']}: {_eur(c['actual_eur'])}" for c in top]
        lines.append("")
    budget = figures.get("budget")
    if budget:
        lines += [f"## Budget: {budget['name']}", ""]
        for s in budget["sections"]:
            lines.append(
                f"- {s['label']}: planned {_eur(s['planned_total'])}, "
                f"actual {_eur(s['actual_total'])}"
            )
        lines.append("")
    lines += ["## Checklist", ""]
    mark = {"ok": "done", "todo": "TODO", "warn": "to look at", "unknown": "unknown"}
    lines += [
        f"- {s['label']}: {mark.get(s['status'], s['status'])}" for s in checklist
    ]
    todo = [s for s in checklist if s["status"] in ("todo", "warn")]
    if todo:
        lines += ["", "## Left open", ""]
        lines += [f"- {s['detail']}" for s in todo]
    return "\n".join(lines) + "\n"


def close_month(
    db: Any,
    fx: Callable[[str], float],
    today: date,
    month: str,
    notes: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Close a month: store the figures and checklist and write the report.

    Closing again replaces the row and updates the same saved report.

    Args:
        db: Database handle.
        fx: currency -> EUR rate.
        today: Reference date.
        month: ``YYYY-MM``, a complete month.
        notes: Free text kept with the close (an earlier note is kept when
            omitted on a re-close).
        force: Close even when the imports step is not ``ok``.

    Returns:
        The same shape as ``build_month_close``, after the close.

    Raises:
        MonthCloseError: 400 for a month in progress or in the future, 409
            when the month is not ready and ``force`` is false.
    """
    check_month(month, today, complete_only=True)
    state = build_month_close(db, fx, today, month)
    if not state["ready"] and not force:
        step = next(s for s in state["checklist"] if s["key"] == "imports")
        raise MonthCloseError(409, f"{month} is not ready to close: {step['detail']}")
    previous = db.get_month_close(month)
    report_id = (previous or {}).get("report_id")
    title = f"Month close {month}"
    body = render_month_close_report(month, state["figures"], state["checklist"])
    if report_id is not None and db.get_report(report_id):
        db.update_report(report_id, title=title, content_md=body)
    else:
        report_id = db.create_report(title, body)
    if notes is None and previous:
        notes = previous.get("notes")
    db.upsert_month_close(
        month, state["figures"], state["checklist"], report_id=report_id, notes=notes
    )
    stored = db.get_month_close(month)
    state["closed"] = {
        "closed_at": stored["closed_at"],
        "notes": stored.get("notes"),
        "report_id": report_id,
        "stale": False,
    }
    return state


def reopen_month(db: Any, month: str) -> Dict[str, Any]:
    """Reopen a month: the stored close goes, the saved report stays.

    Raises:
        MonthCloseError: 404 when the month is not closed.
    """
    stored = db.get_month_close(month)
    if not stored:
        raise MonthCloseError(404, f"{month} is not closed")
    db.delete_month_close(month)
    return {"month": month, "reopened": True, "report_id": stored.get("report_id")}


def check_month_close_ready(
    db: Any, today: Optional[date] = None
) -> List[Dict[str, Any]]:
    """Action Item: the last complete month is ready to close.

    Low severity, from the 5th of the following month, only for that one month
    and only while it is ready (imports reach its end) and not closed.
    """
    from portf_manager.services import exposure

    today = today or date.today()
    if today.day < ITEM_FROM_DAY:
        return []
    month = default_month(today)
    if db.get_month_close(month):
        return []
    rows, _ = load_rows_and_tree(db, exposure._default_fx, month)
    if imports_step(rows, month)["status"] != "ok":
        return []
    name = calendar.month_name[int(month[5:7])]
    return [
        {
            "id": f"spending:month-close:{month}",
            "category": "spending",
            "severity": "low",
            "title": f"{name} is ready to close",
            "detail": (
                f"Every bank account is imported through the end of {month}. "
                "Go through the checklist and close the month."
            ),
            "link_page": "budget",
            "context": {"month": month},
        }
    ]
