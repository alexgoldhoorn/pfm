"""Budget and spending tools."""

import calendar
from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency, _na


def _budget_months_without_activity(summary: dict) -> list[str]:
    """Months with no imported activity at all in a budget variance report.

    Python twin of portf_manager.services.budget.months_without_activity: a
    month nobody has imported reads as zero actuals everywhere, which looks
    like heroic underspending. Any verdict on such a month is noise.
    """
    out = []
    for month in summary.get("months") or []:
        total = 0.0
        for section in summary.get("sections") or []:
            entries = (section.get("lines") or []) + (section.get("unbudgeted") or [])
            total += sum(
                float((e.get("actual_eur") or {}).get(month, 0) or 0) for e in entries
            )
        if total == 0:
            out.append(month)
    return out


def _carries(line: dict) -> bool:
    """A line whose available amount differs from its plan (rollover, yearly)."""
    return bool(line.get("rollover")) or line.get("period") == "yearly"


def _pace_text(pace: Optional[dict]) -> str:
    """One line for a pace block, or '' when there is none."""
    if not pace:
        return ""
    status = (pace.get("status") or "?").replace("_", " ")
    if pace.get("status") == "too_early":
        return (
            f"too early to project (no baseline yet and little of the month has"
            f" passed); spent {_na(pace.get('actual_to_date_eur'), _fmt_currency)}"
            f" to {pace.get('data_through') or 'n/a'}"
            f" against {_na(pace.get('limit_eur'), _fmt_currency)}"
        )
    return (
        f"{status}; spent {_na(pace.get('actual_to_date_eur'), _fmt_currency)}"
        f" to {pace.get('data_through') or 'n/a'},"
        f" projected month end {_na(pace.get('projected_month_end_eur'), _fmt_currency)}"
        f" (estimate) against {_na(pace.get('limit_eur'), _fmt_currency)}"
    )


def _budget_line_notes(line: dict) -> list[str]:
    """Extra lines under a budget line: yearly fund, carry and pace."""
    out = []
    if line.get("period") == "yearly":
        fund = line.get("fund")
        if fund:
            out.append(
                f"    yearly {_fmt_currency(float(fund.get('yearly_amount_eur') or 0))}"
                f" ({_fmt_currency(float(line.get('set_aside_eur') or 0))} a month"
                f" set aside; planned is only the set-aside): last 12 months"
                f" ({fund.get('window_start')} to {fund.get('window_end')}) spent"
                f" {_fmt_currency(float(fund.get('spent_eur') or 0))},"
                f" left {_fmt_currency(float(fund.get('remaining_eur') or 0))}"
            )
        else:
            out.append("    yearly line (fund not available)")
    elif line.get("rollover") and line.get("carried_total") is not None:
        out.append(
            f"    carried in {_fmt_currency(float(line['carried_total']))}"
            f" (plan {_fmt_currency(float(line.get('planned_total') or 0))})"
        )
    pace = _pace_text(line.get("pace"))
    if pace:
        out.append(f"    pace: {pace}")
    return out


@tool
def budget_summary() -> str:
    """
    The active budget's current month: planned vs actual per section (income,
    spending, debt, investments) and per line, unbudgeted spending, and net cash
    flow. "available" is plan plus anything carried in (a rollover line, a
    yearly line's fund): variance is measured against it, not against the
    plain plan. For the month in progress each cost line prints its pace, a
    projected month end (an estimate). Variance is signed so + is always
    favourable. The data-through date is printed. Actuals exist only for
    imported bank statements — the output says when a month has no imported
    activity or is still in progress; relay that instead of calling it
    "under budget".
    """
    try:
        data = client._get("/api/v1/budgets/summary")
    except Exception as e:
        return f"Error fetching budget summary: {e}"

    if not data:
        return "No active budget."

    months = data.get("months") or []
    idle = _budget_months_without_activity(data)
    judged = not idle
    lines = [f"BUDGET — {data.get('budget_name') or '?'} ({', '.join(months)})"]
    if idle:
        lines.append(
            f"⚠️ No imported activity for {', '.join(idle)} yet — every actual reads as"
            " zero, so the variances are not meaningful. Import bank statements on the"
            " Spending page first."
        )
    else:
        today = client._today()
        if months and months[-1] == today.strftime("%Y-%m"):
            month_days = calendar.monthrange(today.year, today.month)[1]
            if today.day < month_days:
                lines.append(
                    f"Month in progress (day {today.day} of {month_days}): actuals are"
                    " month-to-date against a full-month plan, so costs read under"
                    " budget and income/contributions behind plan until the month closes."
                )

    def verdict(obj: dict) -> str:
        if not judged or obj.get("variance_eur") is None:
            return ""
        mark = "✓" if obj.get("favourable") else "✗"
        return f"  {mark} {float(obj['variance_eur']):+,.2f} EUR"

    for section in data.get("sections") or []:
        section_lines = section.get("lines") or []
        unbudgeted = section.get("unbudgeted") or []
        if not (
            section_lines
            or unbudgeted
            or section.get("planned_total")
            or section.get("actual_total")
        ):
            continue
        available = section.get("available_total")
        if available is None:
            available = section.get("planned_total")
        lines.append(
            f"\n{(section.get('label') or '?').upper()}:"
            f" available {_fmt_currency(float(available or 0))}"
            f"  actual {_fmt_currency(float(section.get('actual_total') or 0))}"
            f"{verdict(section)}"
        )
        pace = _pace_text(section.get("pace"))
        if pace:
            lines.append(f"  pace: {pace}")
        for ln in section_lines:
            available = ln.get("available_total")
            if available is None:
                available = ln.get("planned_total")
            planned = _fmt_currency(float(available or 0))
            actual = _fmt_currency(float(ln.get("actual_total") or 0))
            label = "available" if _carries(ln) else "planned"
            lines.append(
                f"  {ln.get('label') or '?':28s} {label} {planned:>14s}"
                f"  actual {actual:>14s}{verdict(ln)}"
            )
            lines.extend(_budget_line_notes(ln))
            if ln.get("link_label") and ln.get("link_amount_eur") is not None:
                lines.append(
                    f"    (against {ln['link_label']}:"
                    f" {_fmt_currency(float(ln['link_amount_eur']))} outstanding)"
                )
        for u in unbudgeted:
            lines.append(
                f"  unbudgeted: {u.get('label') or '?'}"
                f" {_fmt_currency(float(u.get('actual_total') or 0))}"
            )
        if section.get("unbudgeted_suppressed"):
            lines.append(
                "  (broker-side unbudgeted deposits hidden: this budget measures"
                " investments from bank outflows, and counting both would double the"
                " same money)"
            )

    through = data.get("data_through")
    lines.append(f"\nData through: {through or 'n/a (nothing imported)'}.")
    for warning in data.get("warnings") or []:
        lines.append(f"⚠️ {warning}")
    net = data.get("net") or {}
    if net:
        lines.append(
            "\nNet cash flow (income − spending − debt − investment):"
            f" planned {_fmt_currency(float(net.get('planned_total') or 0))}"
            f"  actual {_fmt_currency(float(net.get('actual_total') or 0))}{verdict(net)}"
        )
        lines.append(
            "  Debt repayments and investment contributions count as outflows here,"
            " so a negative net is not the same as losing money."
        )
    return "\n".join(lines)


# ── cost control: comparison, anomalies, forecast, review ─────────────────────


def _clean(text, limit: int = 40) -> str:
    """A merchant or category name, single line and short."""
    return " ".join(str(text or "?").split())[:limit]


def _eur(value) -> str:
    return _na(value, lambda v: _fmt_currency(float(v)))


def _signed(value) -> str:
    return _na(value, lambda v: f"{float(v):+,.2f}")


def _pct(value) -> str:
    return "" if value is None else f" ({float(value):+.1f}%)"


def _warnings(items) -> list[str]:
    return [f"⚠️ {w}" for w in items or []]


def _month_arg(month: Optional[str]) -> Optional[str]:
    """Reject anything but YYYY-MM before it reaches the URL."""
    if month is None:
        return None
    if not (len(month) == 7 and month[4] == "-" and month[:4].isdigit()):
        raise ValueError("month must look like 2026-05")
    return month


@tool
def spending_compare(
    month: Optional[str] = None,
    group_by: str = "category",
    baseline_months: int = 6,
    top: int = 15,
) -> str:
    """
    One month's spending against its normal and against the same month a year
    earlier, by category or by merchant, the biggest moves first. The baseline
    is the average of up to N complete earlier months with imports (rows marked
    one-off are left out of it), so it is an estimate of "normal". Deltas
    exist only for a complete month; a month in progress, a month the data
    stops short of and a month with nothing imported print no deltas. Transfers
    and money moved to Invest / House purchase are not spending.

    Args:
        month: YYYY-MM (default: the last complete month).
        group_by: "category" (default) or "merchant".
        baseline_months: Complete months to average (default 6).
        top: Rows to print (default 15).
    """
    try:
        data = client._get(
            "/api/v1/spending/compare",
            {
                "month": _month_arg(month),
                "group_by": group_by,
                "baseline_months": baseline_months,
            },
        )
    except Exception as e:
        return f"Error fetching spending comparison: {e}"

    totals = data.get("totals") or {}
    items = data.get("items") or []
    lines = [
        f"SPENDING COMPARISON — {data.get('month')} by {data.get('group_by')}"
        f" ({data.get('status')}; data through {_na(data.get('data_through'))}):",
        f"  Actual:    {_eur(totals.get('actual_eur'))}",
        f"  Normal:    {_eur(totals.get('baseline_eur'))}"
        f"  (average of {len(data.get('baseline_months') or [])} complete month(s);"
        " an estimate)",
        f"  Change:    {_signed(totals.get('delta_eur'))} EUR"
        f"{_pct(totals.get('delta_pct'))}",
        f"  Last year: {_eur(totals.get('last_year_eur'))}",
        f"  One-off included in the actual: {_eur(totals.get('one_off_eur'))};"
        f" moved to savings/investment (not spending): {_eur(totals.get('savings_eur'))}",
    ]
    if items:
        lines.append("")
    for it in items[: max(1, top)]:
        tier = f" [{it['tier']}]" if it.get("tier") else ""
        lines.append(
            f"  {_clean(it.get('name')):40s} actual {_eur(it.get('actual_eur')):>14s}"
            f"  normal {_eur(it.get('baseline_eur')):>14s}"
            f"  change {_signed(it.get('delta_eur')):>10s}{_pct(it.get('delta_pct'))}"
            f"  last year {_eur(it.get('last_year_eur'))}{tier}"
        )
    if len(items) > top:
        lines.append(f"  ... and {len(items) - top} more.")
    lines.extend(_warnings(data.get("warnings")))
    return "\n".join(lines)


@tool
def spending_anomalies(days: int = 60) -> str:
    """
    Unusual charges in the last N days, found by fixed rules (no AI): a charge
    far above that merchant's usual amount, a large first charge from a new
    merchant, a category far above its normal month, and a possible double
    charge. These are flags to look at, not verdicts. Rows marked one-off are
    never flagged. Only imported statements up to the data-through date are seen.

    Args:
        days: Look-back window in days (default 60).
    """
    try:
        data = client._get("/api/v1/spending/anomalies", {"days": days})
    except Exception as e:
        return f"Error fetching spending anomalies: {e}"

    items = data.get("items") or []
    lines = [
        f"SPENDING ANOMALIES — last {data.get('days', days)} days"
        f" (data through {_na(data.get('data_through'))}): {len(items)} flagged"
    ]
    for it in items:
        lines.append(
            f"  {it.get('date') or '?':10s} {str(it.get('kind') or '?'):18s}"
            f" {_eur(it.get('amount_eur')):>14s}"
            f"  usual {_eur(it.get('usual_eur')):>12s}"
            f"  {_clean(it.get('merchant') or it.get('category'))}"
            f"  — {it.get('detail') or ''}"[:200]
        )
    if not items:
        lines.append("  Nothing unusual.")
    lines.append("Flags are not verdicts; a flagged charge may be fine.")
    return "\n".join(lines)


@tool
def cashflow_forecast(months: int = 12) -> str:
    """
    Where this month ends and the next N months: expected income, recurring
    charges placed on their due dates (quarterly and yearly included),
    everyday spending, net, and the projected bank balance, plus the known
    upcoming bills and the first month the balance would go below zero. Every
    figure is an estimate from past months; the balance covers bank accounts
    only (moves to brokers are not projected). The rest of this month is
    measured from the data-through date, not from today. A missing source
    prints "n/a", never zero.

    Args:
        months: Months after the current one (default 12).
    """
    try:
        data = client._get("/api/v1/spending/forecast", {"months": months})
    except Exception as e:
        return f"Error fetching cash-flow forecast: {e}"

    tm = data.get("this_month") or {}
    rng = tm.get("range") or {}
    lines = [
        f"CASH-FLOW FORECAST — as of {data.get('as_of')}, data through"
        f" {_na(data.get('data_through'))} (all figures are estimates)",
        "",
        f"This month ({tm.get('month')}, {tm.get('status')}):",
        f"  Spent so far:        {_eur(tm.get('spent_to_date_eur'))}",
        f"  Recurring still due: {_eur(tm.get('recurring_remaining_eur'))}",
        f"  Everyday, rest:      {_eur(tm.get('variable_remaining_eur'))}",
        f"  Projected spending:  {_eur(tm.get('projected_spending_eur'))}"
        f"  (range {_eur(rng.get('spending_low_eur'))} to"
        f" {_eur(rng.get('spending_high_eur'))})",
        f"  Projected income:    {_eur(tm.get('projected_income_eur'))}",
        f"  Projected net:       {_signed(tm.get('projected_net_eur'))} EUR",
        f"  Balance at month end: {_eur(tm.get('projected_balance_end_eur'))}",
    ]
    accounts = data.get("accounts") or []
    if accounts:
        lines.append("\nData freshness:")
        for a in accounts:
            stale = "  STALE" if a.get("stale") else ""
            lines.append(
                f"  {_clean(a.get('name'), 30):30s} data through"
                f" {_na(a.get('data_through'))}, balance {_eur(a.get('balance_eur'))}"
                f"{stale}"
            )
    nxt = data.get("next_months") or []
    if nxt:
        lines.append("\nNext months:")
        for m in nxt:
            flag = "  BELOW ZERO" if m.get("balance_below_zero") else ""
            lines.append(
                f"  {m.get('month')}  income {_eur(m.get('income_eur')):>12s}"
                f"  recurring {_eur(m.get('recurring_eur')):>12s}"
                f"  everyday {_eur(m.get('variable_eur')):>12s}"
                f"  net {_signed(m.get('net_eur')):>10s}"
                f"  balance {_eur(m.get('projected_balance_eur')):>14s}{flag}"
            )
    if data.get("first_below_zero"):
        lines.append(
            f"\nThe balance first goes below zero in {data['first_below_zero']}."
        )
    bills = data.get("known_bills") or []
    if bills:
        lines.append("\nKnown upcoming bills (next 15):")
        for b in bills[:15]:
            lines.append(
                f"  {b.get('date')}  {_eur(b.get('amount_eur')):>12s}"
                f"  {_clean(b.get('merchant'))}"
            )
    if data.get("assumptions"):
        lines.append("\nAssumptions:")
        lines.extend(f"  - {a}" for a in data["assumptions"])
    lines.extend(_warnings(data.get("warnings")))
    return "\n".join(lines)


def _review_recurring(recurring: dict) -> list[str]:
    out = []
    for key, label in (
        ("new", "New"),
        ("ended", "Ended"),
        ("missed", "Missed (due, not seen)"),
        ("price_change", "Price change"),
    ):
        entries = recurring.get(key) or []
        if not entries:
            continue
        out.append(f"  {label}:")
        for r in entries:
            extra = (
                f", {float(r['price_change_pct']):+.1f}% (now {_eur(r.get('last_eur'))})"
                if r.get("price_change_pct") is not None
                else ""
            )
            out.append(
                f"    {_clean(r.get('merchant'))} {_eur(r.get('typical_eur'))}"
                f" {r.get('cadence')}{extra}"
            )
    return out


@tool
def spending_review(month: Optional[str] = None) -> str:
    """
    A monthly spending review in one call: totals against normal and against
    last year, the biggest movers by category and merchant, the month's
    unusual charges, the fixed / flexible / optional split, recurring charges
    that are new, ended, missed or changed price, the active budget's result
    for the month, next month's forecast with the upcoming bills, and data
    freshness per account. Baselines and the forecast are estimates. A part
    that could not be built prints "n/a" with a warning. A month in progress
    or with nothing imported has no deltas.

    Args:
        month: YYYY-MM (default: the last complete month).
    """
    try:
        data = client._get("/api/v1/spending/review", {"month": _month_arg(month)})
    except Exception as e:
        return f"Error fetching spending review: {e}"

    lines = [
        f"SPENDING REVIEW — {data.get('month')} ({_na(data.get('status'))};"
        f" data through {_na(data.get('data_through'))})"
    ]
    totals = (data.get("totals") or {}).get("totals")
    if totals is None:
        lines.append("\nTotals: n/a")
    else:
        lines.append(
            f"\nTotals: actual {_eur(totals.get('actual_eur'))}, normal"
            f" {_eur(totals.get('baseline_eur'))} (estimate), change"
            f" {_signed(totals.get('delta_eur'))} EUR{_pct(totals.get('delta_pct'))},"
            f" last year {_eur(totals.get('last_year_eur'))}"
        )
    movers = data.get("movers")
    if movers:
        for key, label in (("categories", "Categories"), ("merchants", "Merchants")):
            rows = movers.get(key) or []
            if rows:
                lines.append(f"\nBiggest movers — {label}:")
                for r in rows:
                    lines.append(
                        f"  {_clean(r.get('name')):40s} actual {_eur(r.get('actual_eur')):>14s}"
                        f"  change {_signed(r.get('delta_eur')):>10s}{_pct(r.get('delta_pct'))}"
                    )
    anomalies = data.get("anomalies")
    if anomalies is not None:
        lines.append(f"\nUnusual charges: {anomalies.get('count', 0)}")
        for it in anomalies.get("items") or []:
            lines.append(
                f"  {it.get('date')} {it.get('kind')} {_eur(it.get('amount_eur'))}"
                f" {_clean(it.get('merchant') or it.get('category'))}"
            )
    tiers = (data.get("tiers") or {}).get("month")
    if tiers:
        lines.append(
            "\nTiers: fixed "
            + _eur(tiers.get("fixed_eur"))
            + ", flexible "
            + _eur(tiers.get("flexible_eur"))
            + ", optional "
            + _eur(tiers.get("optional_eur"))
            + ", unclassified "
            + _eur(tiers.get("unclassified_eur"))
            + "; moved to savings/investment "
            + _eur(tiers.get("savings_eur"))
        )
    recurring = data.get("recurring")
    if recurring is not None:
        body = _review_recurring(recurring)
        lines.append("\nRecurring changes:" + ("" if body else " none"))
        lines.extend(body)
    budget = data.get("budget")
    if budget:
        lines.append(f"\nBudget — {_clean(budget.get('budget_name'))}:")
        for sec in budget.get("sections") or []:
            if not (sec.get("available_total") or sec.get("actual_total")):
                continue
            lines.append(
                f"  {sec.get('label')}: available {_eur(sec.get('available_total'))}"
                f" actual {_eur(sec.get('actual_total'))}"
                f" variance {_signed(sec.get('variance_eur'))} EUR"
            )
            for ln in sec.get("over_lines") or []:
                lines.append(
                    f"    over: {_clean(ln.get('label'))}"
                    f" {_signed(ln.get('variance_eur'))} EUR"
                )
        if budget.get("no_activity"):
            lines.append(
                "  (nothing imported for this month: variances are not meaningful)"
            )
    elif "budget" in data:
        lines.append("\nBudget: no active budget.")
    forecast = data.get("forecast")
    if forecast:
        tm = forecast.get("this_month") or {}
        nm = forecast.get("next_month") or {}
        lines.append(
            f"\nForecast (estimate): {tm.get('month')} ends at net"
            f" {_signed(tm.get('projected_net_eur'))} EUR, balance"
            f" {_eur(tm.get('projected_balance_end_eur'))}"
        )
        if nm:
            lines.append(
                f"  {nm.get('month')}: income {_eur(nm.get('income_eur'))},"
                f" recurring {_eur(nm.get('recurring_eur'))}, everyday"
                f" {_eur(nm.get('variable_eur'))}, net {_signed(nm.get('net_eur'))} EUR,"
                f" balance {_eur(nm.get('projected_balance_eur'))}"
            )
        for b in forecast.get("upcoming_bills") or []:
            lines.append(
                f"  bill {b.get('date')} {_eur(b.get('amount_eur'))} {_clean(b.get('merchant'))}"
            )
    else:
        lines.append("\nForecast: n/a")
    for a in data.get("freshness") or []:
        stale = "  STALE" if a.get("stale") else ""
        lines.append(
            f"Data freshness: {_clean(a.get('name'), 30)} through"
            f" {_na(a.get('data_through'))}{stale}"
        )
    lines.extend(_warnings(data.get("warnings")))
    return "\n".join(lines)


@tool
def spending_summary(days: int = 30, trend_months: int = 6) -> str:
    """
    Bank-account spending: spent / income / transferred over the last N days,
    the category breakdown, and a monthly spent/income/net trend. Transfers
    between own accounts and to brokers are excluded from spending. Only covers
    imported bank statements.

    Args:
        days: Look-back window for the totals and categories (default 30).
        trend_months: Calendar months of trend to include (default 6; 0 = none).
    """
    try:
        data = client._get("/api/v1/spending/summary", {"days": days})
    except Exception as e:
        return f"Error fetching spending summary: {e}"

    lines = [f"SPENDING — last {days} days (imported bank accounts):"]
    lines.append(f"  Spent:        {_fmt_currency(float(data.get('spent_eur') or 0))}")
    lines.append(f"  Income:       {_fmt_currency(float(data.get('income_eur') or 0))}")
    lines.append(
        f"  Transferred:  {_fmt_currency(float(data.get('transferred_eur') or 0))}"
        "  (between own accounts / to brokers — not spending)"
    )

    categories = data.get("by_category_eur") or {}
    if categories:
        lines.append("\nBy category (absolute EUR, largest first):")
        ranked = sorted(categories.items(), key=lambda kv: -abs(float(kv[1] or 0)))
        for cat, amount in ranked[:20]:
            lines.append(f"  {cat:24s} {_fmt_currency(float(amount or 0)):>14s}")

    if trend_months and trend_months > 0:
        # Best-effort — the totals above are still worth returning without it.
        try:
            trend = client._get("/api/v1/spending/trend", {"months": trend_months})
        except Exception:
            trend = []
        if trend:
            lines.append("\nMONTHLY TREND (transfers excluded):")
            for m in trend:
                spent = _fmt_currency(float(m.get("spent_eur") or 0))
                income = _fmt_currency(float(m.get("income_eur") or 0))
                lines.append(
                    f"  {m.get('month') or '?'}  spent {spent:>14s}"
                    f"  income {income:>14s}  net {float(m.get('net_eur') or 0):+,.2f} EUR"
                )

    lines.append(
        "\nOnly imported statements count: a month not imported yet reads as zero,"
        " and the current month is partial."
    )
    return "\n".join(lines)


# The spending API returns at most this many rows per page.
SPENDING_PAGE_SIZE = 200


# Most rows one call reads; totals beyond this are reported as partial.
SPENDING_MAX_ROWS = 5000


# Most rows one call prints.
SPENDING_MAX_SHOWN = 500


@tool
def spending_transactions(
    query: Optional[str] = None,
    category: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    limit: int = 50,
) -> str:
    """
    Individual bank-account transactions from imported statements, newest
    first, with money-out and money-in totals over every matching row.
    Transfers between own accounts and to brokers are listed but kept out of
    the totals. Use spending_summary for the category breakdown and trend.

    Args:
        query: Text to find in the description, merchant or category name,
            ignoring case and accents. A parent category's name also finds rows
            filed under its sub-categories (e.g. "taxes").
        category: Exact category name. It does not include sub-categories; use
            query for that.
        start_date: First date to include, YYYY-MM-DD.
        end_date: Last date to include, YYYY-MM-DD.
        portfolio_id: Bank account id (see list_portfolios). Omit for all.
        limit: Rows to print (default 50, at most 500). Totals always cover
            every matching row.
    """
    shown = max(1, min(limit, SPENDING_MAX_SHOWN))
    rows: list[dict] = []
    total = 0
    try:
        while True:
            page = client._get(
                "/api/v1/spending/",
                {
                    "q": query or None,
                    "category": category or None,
                    "start_date": start_date,
                    "end_date": end_date,
                    "portfolio_id": portfolio_id,
                    "sort_by": "date",
                    "sort_dir": "desc",
                    "limit": SPENDING_PAGE_SIZE,
                    "offset": len(rows),
                },
            )
            items = page.get("items") or []
            total = int(page.get("total") or 0)
            rows.extend(items)
            if not items or len(rows) >= min(total, SPENDING_MAX_ROWS):
                break
    except Exception as e:
        return f"Error fetching spending transactions: {e}"

    if not rows:
        return "No spending transactions match."

    filters = [
        f"{label} {value}"
        for label, value in (
            ("query", repr(query) if query else None),
            ("category", repr(category) if category else None),
            ("from", start_date),
            ("to", end_date),
            ("account", portfolio_id),
        )
        if value is not None
    ]
    header = f"SPENDING TRANSACTIONS — {total} matching"
    if filters:
        header += f" ({', '.join(filters)})"
    lines = [header + ":"]

    # Sum per currency: the API returns each row in its account's currency.
    money_out: dict[str, float] = {}
    money_in: dict[str, float] = {}
    transfers = 0
    for r in rows:
        if r.get("is_transfer"):
            transfers += 1
            continue
        amount = float(r.get("amount") or 0)
        bucket = money_out if amount < 0 else money_in
        currency = r.get("currency") or "EUR"
        bucket[currency] = bucket.get(currency, 0.0) + amount
    for label, bucket in (("Money out:", money_out), ("Money in: ", money_in)):
        amounts = ", ".join(
            _fmt_currency(v, c) for c, v in sorted(bucket.items())
        ) or _fmt_currency(0.0)
        lines.append(f"  {label} {amounts}")
    if transfers:
        noun = "transfer" if transfers == 1 else "transfers"
        lines.append(f"  {transfers} {noun} listed below but not in the totals.")
    if total > len(rows):
        lines.append(
            f"  Totals cover only the newest {len(rows)} of {total} rows;"
            " narrow the dates for a complete total."
        )

    lines.append("")
    for r in rows[:shown]:
        amount = _fmt_currency(float(r.get("amount") or 0), r.get("currency") or "EUR")
        tag = " [transfer]" if r.get("is_transfer") else ""
        lines.append(
            f"  {r.get('date') or '?'}  {(r.get('portfolio_name') or '?')[:16]:16s}"
            f" {amount:>16s}  {(r.get('category') or '?')[:18]:18s}"
            f" {(r.get('description') or '')[:70]}{tag}"
        )
    if total > shown:
        lines.append(f"\nShowing the newest {min(shown, len(rows))} of {total}.")
    return "\n".join(lines)
