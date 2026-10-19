"""Budget and spending tools."""

import calendar
import urllib.parse
from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency


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


@tool
def budget_summary() -> str:
    """
    The active budget's current month: planned vs actual per section (income,
    spending, debt, investments) and per line, unbudgeted spending, and net cash
    flow. Variance is signed so + is always favourable. Actuals exist only for
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
        lines.append(
            f"\n{(section.get('label') or '?').upper()}:"
            f" planned {_fmt_currency(float(section.get('planned_total') or 0))}"
            f"  actual {_fmt_currency(float(section.get('actual_total') or 0))}"
            f"{verdict(section)}"
        )
        for ln in section_lines:
            planned = _fmt_currency(float(ln.get("planned_total") or 0))
            actual = _fmt_currency(float(ln.get("actual_total") or 0))
            lines.append(
                f"  {ln.get('label') or '?':28s} planned {planned:>14s}"
                f"  actual {actual:>14s}{verdict(ln)}"
            )
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
                    "q": urllib.parse.quote(query) if query else None,
                    "category": urllib.parse.quote(category) if category else None,
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
