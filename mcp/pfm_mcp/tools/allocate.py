"""The next-euro allocator tools."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _na


@tool
def next_euro(amount: Optional[float] = None) -> str:
    """
    What to do with new money: deployable cash this month (liquid cash minus the
    autónomo tax reserve minus a 6-month household buffer, with the 4–8 month
    band), and a proposal of concrete buys with a reason for each line,
    traspasos between funds, antiaplicación blocks and stocks that need
    research. Read this before advising on investing new money; then discuss the
    proposal. Use with deployable_cash, allocation_targets and
    allocation_candidates to discuss alternatives. Read-only.
    """
    params = {"amount": amount} if amount is not None else None
    try:
        data = client._get("/api/v1/allocate/next-euro", params)
    except Exception as e:
        return f"Error fetching next euro: {e}"

    # the breakdown is printed whenever there is one, whatever the status
    if data.get("chat_summary"):
        return data["chat_summary"]
    status = data.get("status")
    if status != "ok":
        reasons = data.get("reasons")
        if isinstance(reasons, list) and reasons:
            reason = "; ".join(str(r) for r in reasons)
        else:
            reason = data.get("reason") or "see the Assets page"
        return f"Next euro: {_na(status)} — {reason}"
    return "n/a"


def _eur_na(value) -> str:
    """EUR amount, or 'n/a' when missing (never 0)."""
    return _na(value, lambda v: f"{float(v):,.2f} EUR")


@tool
def deployable_cash() -> str:
    """
    How much cash can be invested now and how that figure is built: the liquid
    cash lines, the autónomo tax reserve per quarter (gross income, IVA, IRPF,
    already paid, remaining, due date, which merchants were counted), the
    household buffer (months of spending covered, the min/target/max band,
    status, idle amount), the deployable amount, money coming in soon and the
    warnings. Call it when asked "how much can I invest" or to check the inputs
    behind next_euro. Read-only.
    """
    try:
        d = client._get("/api/v1/allocate/deployable")
    except Exception as e:
        return f"Error fetching deployable cash: {e}"

    lines = [f"DEPLOYABLE CASH (as of {_na(d.get('as_of'))}):"]
    lines.append(f"Liquid: {_eur_na(d.get('liquid_eur'))}")
    for ln in d.get("liquid_lines") or []:
        stale = " (stale)" if ln.get("stale") else ""
        lines.append(
            f"  {_na(ln.get('label'))}: {_eur_na(ln.get('amount_eur'))}{stale}"
        )
    reserve = d.get("tax_reserve") or {}
    lines.append(f"Tax reserve: {_eur_na(reserve.get('reserve_eur'))}")
    for q in reserve.get("quarters") or []:
        lines.append(
            f"  {_na(q.get('quarter'))}: "
            f"gross {_eur_na(q.get('gross_eur'))}, IVA {_eur_na(q.get('iva_eur'))}, "
            f"IRPF {_eur_na(q.get('irpf_eur'))}, paid {_eur_na(q.get('paid_eur'))}, "
            f"remaining {_eur_na(q.get('remaining_eur'))}, due {_na(q.get('due'))}"
        )
    merchants = reserve.get("merchants_counted")
    lines.append(
        "  Merchants counted: " + (", ".join(merchants) if merchants else "n/a")
    )
    buf = d.get("buffer") or {}
    lines.append(
        f"Buffer: {_na(buf.get('months'), lambda v: f'{float(v):.1f}')} months "
        f"(min {_na(buf.get('min_months'))}, target {_na(buf.get('target_months'))}, "
        f"max {_na(buf.get('max_months'))}), status {_na(buf.get('status'))}, "
        f"monthly spend {_eur_na(buf.get('monthly_spend_eur'))}, "
        f"target {_eur_na(buf.get('target_eur'))}, idle {_eur_na(buf.get('idle_eur'))}"
    )
    lines.append(f"Deployable: {_eur_na(d.get('deployable_eur'))}")
    if d.get("status") not in (None, "ok"):
        lines.append(f"Status: {d['status']} - {_na(d.get('reason'))}")
    for c in d.get("coming_in") or []:
        lines.append(
            f"Coming in: {_na(c.get('label'))} {_eur_na(c.get('amount_eur'))} "
            f"on {_na(c.get('date'))}"
        )
    for w in d.get("warnings") or []:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


@tool
def allocation_targets() -> str:
    """
    Target vs current allocation per asset type (percent and EUR drift), plus a
    line naming held asset types that have no target. Call it to see which
    buckets are under- or overweight before discussing where new money should
    go; it is the bucket-level input of next_euro. Read-only.
    """
    try:
        data = client._get("/api/v1/rebalance/analysis")
    except Exception as e:
        return f"Error fetching allocation targets: {e}"

    allocations = data.get("allocations") or []
    lines = [
        "ALLOCATION TARGETS - total "
        f"{_eur_na(data.get('total_value_eur'))}, targets sum "
        f"{_na(data.get('targets_sum_pct'), lambda v: f'{float(v):.1f}%')}:"
    ]
    for a in sorted(allocations, key=lambda x: -abs(float(x.get("drift_pct") or 0))):
        lines.append(
            f"  {_na(a.get('asset_type')):14s}"
            f" now {_na(a.get('current_pct'), lambda v: f'{float(v):5.1f}%')}"
            f"  target {_na(a.get('target_pct'), lambda v: f'{float(v):5.1f}%')}"
            f"  drift {_na(a.get('drift_pct'), lambda v: f'{float(v):+.1f}pp')}"
            f" ({_na(a.get('drift_eur'), lambda v: f'{float(v):+,.2f} EUR')})"
        )
    missing = [
        _na(a.get("asset_type"))
        for a in allocations
        if float(a.get("current_value_eur") or 0) > 0
        and not float(a.get("target_pct") or 0)
    ]
    lines.append("Held with no target: " + (", ".join(missing) if missing else "none"))
    return "\n".join(lines)


@tool
def allocation_candidates(bucket: Optional[str] = None) -> str:
    """
    Everything the next euro could buy, one line each: symbol, name, portfolio,
    value and weight, TER, overlap group, buy-below vs price, research rating,
    blocked ones with the date they are safe to buy again (antiaplicación),
    the room left under the single-stock cap, and why it ranks where it does.
    Pass a bucket (asset type: etf, mutual_fund, index, stock, crypto, bond) to
    see one bucket, or none for all. Call it to discuss alternatives to a
    next_euro line. Read-only.
    """
    params = {"bucket": bucket} if bucket else None
    try:
        data = client._get("/api/v1/allocate/candidates", params)
    except Exception as e:
        return f"Error fetching allocation candidates: {e}"

    head = f"ALLOCATION CANDIDATES - bucket {_na(data.get('bucket'), str) if data.get('bucket') else 'all'}"
    if data.get("bucket"):
        head += (
            f" (target {_na(data.get('target_pct'), lambda v: f'{float(v):.1f}%')},"
            f" now {_na(data.get('current_pct'), lambda v: f'{float(v):.1f}%')})"
        )
    lines = [head + f", portfolio {_eur_na(data.get('total_value_eur'))}"]
    for c in data.get("candidates") or []:
        is_fund = c.get("is_fund") or c.get("bucket") in ("etf", "mutual_fund", "index")
        kind = "fund" if is_fund else "stock"
        tags = [kind]
        if c.get("managed"):
            tags.append("managed")
        if c.get("is_watchlist"):
            tags.append("watchlist")
        lines.append(
            f"- {_na(c.get('symbol'))} ({_na(c.get('name'))}) [{', '.join(tags)}]"
            f" in {_na(c.get('portfolio_name'))}: "
            f"{_eur_na(c.get('value_eur'))}, "
            f"{_na(c.get('weight_pct'), lambda v: f'{float(v):.1f}%')} of portfolio"
        )
        detail = [
            "TER " + _na(c.get("ter_pct"), lambda v: f"{float(v):.2f}%"),
            "rating " + _na(c.get("recommendation")),
            "conviction " + _na(c.get("conviction")),
            "buy-below "
            + _na(c.get("buy_below"), lambda v: f"{float(v):,.2f}")
            + " vs price "
            + _na(c.get("price_native"), lambda v: f"{float(v):,.2f}")
            + f" {_na(c.get('currency'))}",
            "fair value " + _na(c.get("fair_value")),
        ]
        lines.append("    " + "; ".join(detail))
        if c.get("overlap_group"):
            lines.append("    overlaps a larger fund (consolidation candidate)")
        if c.get("cap_room_eur") is not None or not is_fund:
            lines.append(f"    cap room {_eur_na(c.get('cap_room_eur'))}")
        blocked = c.get("blocked")
        if blocked:
            lines.append(
                f"    BLOCKED until {_na(blocked.get('safe_from'))}: "
                f"{_na(blocked.get('reason'))}"
            )
        lines.append(f"    rank: {_na(c.get('rank_reason'))}")
    if len(lines) == 1:
        lines.append("No candidates.")
    for w in data.get("warnings") or []:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


@tool
def allocator_settings() -> str:
    """
    The allocator's settings (buffer months band, IVA/IRPF rates, tax-paid
    categories, managed portfolios, minimum line, lines per bucket,
    single-stock cap, traspaso threshold) and the income merchants seen in the
    last year, marking which are ticked as work income. Call it to explain why
    deployable_cash or next_euro gave a figure, or before suggesting a settings
    change. Read-only.
    """
    try:
        d = client._get("/api/v1/allocate/settings")
    except Exception as e:
        return f"Error fetching allocator settings: {e}"

    def listed(key: str) -> str:
        v = d.get(key)
        return ", ".join(str(x) for x in v) if v else "n/a"

    lines = [
        "ALLOCATOR SETTINGS:",
        f"Buffer months: min {_na(d.get('buffer_months_min'))}, "
        f"target {_na(d.get('buffer_months_target'))}, "
        f"max {_na(d.get('buffer_months_max'))}",
        f"IVA {_na(d.get('iva_pct'))}%, IRPF {_na(d.get('irpf_pct'))}%, "
        f"retention {_na(d.get('irpf_retention_pct'))}%",
        f"Income category: {_na(d.get('income_category'))}",
        f"Tax-paid categories: {listed('tax_paid_categories')}",
        f"Managed portfolio ids: {listed('managed_portfolio_ids')}",
        f"Minimum line: {_eur_na(d.get('min_line_eur'))}, "
        f"max lines per bucket {_na(d.get('max_lines_per_bucket'))}",
        f"Single-stock cap: {_na(d.get('max_single_stock_pct'))}%, "
        f"traspaso threshold {_na(d.get('traspaso_threshold_pp'))}pp",
    ]
    ticked = set(d.get("work_income_merchants") or [])
    lines.append(
        "Work income merchants ticked: "
        + (", ".join(sorted(ticked)) if ticked else "none (all income counted)")
    )
    seen = d.get("income_merchants")
    if seen:
        lines.append("Income merchants seen (last 365 days):")
        for m in seen:
            lines.append(f"  [{'x' if m in ticked else ' '}] {m}")
    else:
        lines.append("Income merchants seen (last 365 days): n/a")
    return "\n".join(lines)
