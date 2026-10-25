"""Net worth, goals, retirement readiness and action items."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency, _na


@tool
def goals() -> str:
    """
    List all financial goals with their current progress, projected final value,
    whether they are on track, and the required monthly contribution.
    """
    try:
        data = client._get("/api/v1/goals/")
    except Exception as e:
        return f"Error fetching goals: {e}"

    if not data:
        return "No goals defined."

    lines = ["FINANCIAL GOALS (current = total net worth, same basis as `networth`):"]
    for g in data:
        name = g.get("name", "?")
        # Field names as GET /api/v1/goals/ returns them. Reading the pre-EUR
        # names (target_amount, current_value, ...) silently printed 0.00 EUR.
        target = g.get("target_amount_eur") or 0
        current = g.get("current_networth_eur") or 0
        progress = g.get("progress_pct") or 0
        on_track = g.get("on_track")
        horizon = g.get("target_date", "?")
        monthly = g.get("required_monthly_eur")
        projected = g.get("projected_value_eur")
        contributing = g.get("monthly_contribution_eur")
        shortfall = g.get("shortfall_eur")

        # status (since 2026-10-24) refines on_track: achieved / overdue
        status = g.get("status")
        if status == "achieved":
            track_str = "  🏁 ACHIEVED"
        elif status == "overdue":
            track_str = "  ⏰ OVERDUE (date passed)"
        else:
            track_str = (
                "  ✅ ON TRACK"
                if on_track
                else ("  ⚠️ BEHIND" if on_track is False else "")
            )
        monthly_str = ""
        if monthly:
            contrib_str = (
                f", contributing {_fmt_currency(contributing)}/mo"
                if contributing
                else ""
            )
            monthly_str = f"  (need {_fmt_currency(monthly)}/mo{contrib_str})"
        proj_str = f"  → projected {_fmt_currency(projected)}" if projected else ""
        short_str = (
            f"  shortfall {_fmt_currency(shortfall)}"
            if shortfall and shortfall > 0
            else ""
        )
        lines.append(
            f"  {name}: {_fmt_currency(current)} / {_fmt_currency(target)}"
            f"  ({progress:.1f}%)  by {str(horizon)[:10]}"
            f"{proj_str}{monthly_str}{short_str}{track_str}"
        )
        # Follow-up since the goal was set (goals with a stored baseline only).
        gained = g.get("gained_since_start_eur")
        if gained is not None:
            since = str(g.get("start_date") or "start")[:10]
            sign = "+" if gained >= 0 else "-"
            pace_line = f"    since {since}: {sign}{_fmt_currency(abs(gained))}"
            closed = g.get("gap_closed_pct")
            if closed is not None:
                pace_line += f" ({closed:.0f}% of the gap closed)"
            actual = g.get("actual_monthly_eur")
            if actual is not None:
                pace_line += f", pace {_fmt_currency(actual)}/mo actual"
                pace = g.get("pace")
                if pace:
                    pace_line += f" — {pace.upper()} of what is needed"
            else:
                pace_line += ", pace after the first month"
            lines.append(pace_line)
        notes = g.get("notes")
        if notes:
            lines.append(f"    notes: {notes}")
    return "\n".join(lines)


@tool
def networth() -> str:
    """
    Total net worth: live brokerage positions + bank-account balances (from
    imported bank statements) + active fixed deposits + manually entered assets
    (home, pension, ...) minus manual liabilities (mortgage, loans). Lists each
    bank account, deposit and manual item. This is the tool for cash questions —
    portfolio_holdings only sees invested positions.
    """
    try:
        data = client._get("/api/v1/networth/")
    except Exception as e:
        return f"Error fetching net worth: {e}"

    liabilities_total = float(data.get("manual_liabilities_eur") or 0)
    lines = [f"NET WORTH: {_fmt_currency(float(data.get('net_worth_eur') or 0))}"]
    lines.append(
        f"  Brokerage (live positions): {_fmt_currency(float(data.get('brokerage_eur') or 0))}"
    )
    lines.append(
        f"  Bank accounts:              {_fmt_currency(float(data.get('bank_accounts_eur') or 0))}"
    )
    lines.append(
        f"  Broker cash (ledger):       {_fmt_currency(float(data.get('broker_cash_eur') or 0))}"
    )
    lines.append(
        f"  Fixed deposits (active):    {_fmt_currency(float(data.get('deposits_eur') or 0))}"
    )
    lines.append(
        f"  Other assets (manual):      {_fmt_currency(float(data.get('manual_assets_eur') or 0))}"
    )
    lines.append(
        f"  Liabilities (manual):       {_fmt_currency(-liabilities_total if liabilities_total else 0.0)}"
    )
    loans_total = float(data.get("loans_eur") or 0)
    lines.append(
        f"  Loans:                      {_fmt_currency(-loans_total if loans_total else 0.0)}"
    )

    accounts = data.get("bank_accounts") or []
    if accounts:
        lines.append("\nBank accounts:")
        for a in accounts:
            name = a.get("name") or "?"
            if a.get("balance_eur") is None:
                lines.append(f"  {name:24s}  no balance imported — not in the total")
                continue
            native = ""
            if a.get("currency") and a["currency"] != "EUR":
                native = (
                    f" ({_fmt_currency(float(a.get('balance') or 0), a['currency'])})"
                )
            lines.append(
                f"  {name:24s}  {_fmt_currency(float(a['balance_eur']))}{native}"
                f"  as of {str(a.get('as_of') or '?')[:10]}"
            )

    deposits = data.get("deposits") or []
    if deposits:
        lines.append("\nFixed deposits:")
        for d in deposits:
            rate = d.get("interest_rate")
            rate_str = f"  {float(rate):.2f}%" if rate is not None else ""
            lines.append(
                f"  {d.get('name') or '?':24s}"
                f"  {_fmt_currency(float(d.get('principal') or 0), d.get('currency') or 'EUR')}"
                f"{rate_str}  matures {str(d.get('maturity_date') or '?')[:10]}"
            )

    items = data.get("items") or []
    groups = (
        ("Manual assets", [i for i in items if not i.get("is_liability")]),
        ("Liabilities", [i for i in items if i.get("is_liability")]),
    )
    for title, group in groups:
        if not group:
            continue
        lines.append(f"\n{title}:")
        for i in sorted(group, key=lambda x: -float(x.get("amount_eur") or 0)):
            amount = float(i.get("amount_eur") or 0)
            if i.get("is_liability"):
                amount = -amount
            lines.append(
                f"  {i.get('name') or '?':24s}  [{i.get('category') or '?'}]"
                f"  {_fmt_currency(amount)}"
                f"  updated {str(i.get('updated_at') or '?')[:10]}"
            )

    lines.append(
        "\nBank balances come from imported statements; manual items are only as"
        " current as their 'updated' date."
    )
    lines.extend(_balance_sheet_lines())
    return "\n".join(lines)


def _balance_sheet_lines() -> list[str]:
    """Summary lines from the balance sheet; never raises.

    Returns:
        The balance-sheet totals line plus one bullet per stale line, or a
        single "unavailable" line when the call fails.
    """
    try:
        sheet = client._get("/api/v1/networth/balance-sheet")
        totals = sheet.get("totals") or {}

        def money(key: str) -> str:
            value = totals.get(key)
            return "n/a" if value is None else _fmt_currency(float(value))

        stale = totals.get("stale_lines", "n/a")
        out = [
            f"\nBalance sheet: assets {money('assets_eur')}, "
            f"liabilities {money('liabilities_eur')}, "
            f"financial assets (excl. property) {money('financial_assets_eur')}, "
            f"liquid {money('liquid_eur')}; {stale} stale line(s)"
        ]
        for group in sheet.get("groups") or []:
            for line in group.get("lines") or []:
                if not line.get("stale"):
                    continue
                age = line.get("age_days")
                detail = line.get("note") or (
                    f"{age} days old" if age is not None else "stale"
                )
                out.append(f"  - {line.get('label') or '?'} — {detail}")
        return out
    except Exception as e:
        return [f"\nBalance sheet: unavailable ({e})"]


_VERDICT_WORDS = {"on_track": "ON TRACK", "short": "SHORT", "at_risk": "AT RISK"}


def _lasts(sc: dict, horizon) -> str:
    """Describe how long the money lasts in one scenario."""
    if "lasts_until_age" not in sc:
        return "n/a"
    if sc["lasts_until_age"] is None:
        return f"past the horizon (age {_na(horizon)})"
    return f"until age {sc['lasts_until_age']}"


def _gap(sc: dict) -> str:
    """Describe the monthly gap: negative is a surplus."""
    gap = sc.get("gap_monthly_eur")
    if gap is None:
        return "n/a"
    amount = _fmt_currency(abs(float(gap)))
    return f"surplus {amount}/month" if gap <= 0 else f"short by {amount}/month"


@tool
def retirement_readiness() -> str:
    """
    Retirement readiness in today's euros: whether the household's financial
    assets, measured spending and entered pensions last from the retirement age
    (65) and the statutory age (67) to the horizon; verdict, money-lasts-until
    age, sustainable monthly spend, gap and extra saving needed, base and
    pessimistic cases, plus the warnings. Needs the plan set up on the Net Worth
    page. Read-only.
    """
    try:
        data = client._get("/api/v1/retirement/readiness")
    except Exception as e:
        return f"Error fetching retirement readiness: {e}"

    status = data.get("status")
    if status != "ok":
        default = (
            "enter a birth date on the Net Worth page"
            if status == "needs_setup"
            else "see the Net Worth page"
        )
        reason = data.get("reason") or default
        return f"Retirement readiness: {_na(status)} — {reason}"

    inputs = data.get("inputs") or {}
    horizon = (data.get("plan") or {}).get("horizon_age")
    money = lambda v: _fmt_currency(float(v))  # noqa: E731
    lines = [
        f"RETIREMENT READINESS (today's euros, as of {_na(data.get('as_of'))})",
        f"  Spending target: "
        f"{_na(inputs.get('spending_target_monthly_eur'), money)}/month "
        f"(from {_na(inputs.get('spending_months_used'))} months of spending)",
        f"  Financial assets: {_na(inputs.get('financial_assets_eur'), money)}",
    ]
    scenarios = data.get("scenarios") or []
    ages = []
    for sc in scenarios:
        if sc.get("retirement_age") not in ages:
            ages.append(sc.get("retirement_age"))
    for age in ages:
        for sc in (s for s in scenarios if s.get("retirement_age") == age):
            verdict = _VERDICT_WORDS.get(sc.get("verdict"), _na(sc.get("verdict")))
            if sc.get("case") == "base":
                extra = sc.get("extra_saving_needed_monthly_eur")
                if "extra_saving_needed_monthly_eur" not in sc:
                    saving = "n/a"
                elif extra is None:
                    saving = "not reachable by saving alone"
                elif extra <= 0:
                    saving = "none needed"
                else:
                    saving = f"save {money(extra)}/month more"
                lines.append(f"\nRetire at age {_na(age)}: {verdict}")
                lines.append(f"  Money lasts {_lasts(sc, horizon)}")
                lines.append(
                    "  Sustainable spend: "
                    f"{_na(sc.get('sustainable_monthly_eur'), money)}/month"
                )
                lines.append(f"  Gap: {_gap(sc)}")
                lines.append(f"  Extra saving: {saving}")
            else:
                lines.append(
                    f"  Pessimistic: {verdict}, money lasts {_lasts(sc, horizon)}, "
                    f"sustainable "
                    f"{_na(sc.get('sustainable_monthly_eur'), money)}/month, "
                    f"{_gap(sc)}"
                )
    warnings = data.get("warnings") or []
    if warnings:
        lines.append("\nWarnings:")
        lines.extend(f"  - {w}" for w in warnings)
    return "\n".join(lines)


@tool
def action_items(category: Optional[str] = None) -> str:
    """
    Open action items pfm has detected, most severe first: stale broker/bank
    imports (with the date to upload from), data-quality issues, failed price
    updates, stale research on held positions, off-track goals, budget overruns,
    missed recurring charges / price rises on bank accounts and
    watchlist/price-target alerts. Items dismissed in the web UI still show
    here (dismissal is per-browser), and the Net Worth setup checklist is not
    included (it is computed in the web client).

    Args:
        category: Optional filter — 'import', 'data_quality', 'errors', 'goals',
            'budget', 'spending' (missed recurring charges / price rises) or
            'watchlist'.
    """
    try:
        data = client._get("/api/v1/action-items/")
    except Exception as e:
        return f"Error fetching action items: {e}"

    items = data.get("items") or []
    if category:
        items = [
            i for i in items if (i.get("category") or "").lower() == category.lower()
        ]
    if not items:
        if category:
            return f"No open action items in category '{category}'."
        return "No open action items."

    lines = [f"ACTION ITEMS ({len(items)}):"]
    for i in items:
        lines.append(
            f"  [{(i.get('severity') or '?').upper()}] {i.get('title') or ''}"
            f"  ({i.get('category') or '?'})"
        )
        if i.get("detail"):
            lines.append(f"      {i['detail']}")
    return "\n".join(lines)
