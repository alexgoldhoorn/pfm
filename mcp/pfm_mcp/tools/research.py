"""Research notes, price targets and the decision journal."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import _fmt_currency


@tool
def research_lookup(symbol: str) -> str:
    """
    Comprehensive research snapshot for any ticker (held or not): current
    price, full position data, fundamentals, recent news headlines, and saved
    price targets.

    Args:
        symbol: Yahoo-format ticker, e.g. 'NVDA', 'ASML.AS', 'BTC-EUR'.
    """
    try:
        data = client._get(f"/api/v1/research/{symbol.upper()}/lookup")
    except Exception as e:
        return f"Error fetching research for {symbol}: {e}"

    sym = data.get("symbol", symbol.upper())
    name = data.get("name", sym)
    held = data.get("held", False)
    on_watch = data.get("on_watchlist", False)
    cur = data.get("currency", "EUR")

    lines = [f"RESEARCH — {sym} ({name})"]
    status = "held" if held else "not held"
    if on_watch:
        status += ", on watchlist"
        if data.get("watch_buy_below"):
            status += f" (buy below {_fmt_currency(data['watch_buy_below'], cur)})"
    lines.append(f"  Status:      {status}")
    lines.append(f"  Price:       {_fmt_currency(data.get('current_price', 0), cur)}")

    if held:
        lines.append(f"  Quantity:    {data.get('quantity', 0):.4f}")
        lines.append(f"  Avg cost:    {_fmt_currency(data.get('avg_cost', 0), cur)}")
        lines.append(
            f"  Value:       {_fmt_currency(data.get('market_value', 0), cur)}"
        )
        upct = data.get("unrealised_pct")
        uamt = data.get("unrealised_gain", 0)
        sign = "+" if uamt >= 0 else ""
        lines.append(
            f"  Unrealised:  {sign}{_fmt_currency(uamt, cur)} ({sign}{upct:.1f}%)"
            if upct is not None
            else f"  Unrealised:  {_fmt_currency(uamt, cur)}"
        )
        lines.append(
            f"  Realised:    {_fmt_currency(data.get('realised_gain', 0), cur)}"
        )

    targets = data.get("targets")
    if targets:
        fv = targets.get("fair_value")
        bb = targets.get("buy_below")
        lines.append(f"  Fair value:  {_fmt_currency(fv, cur) if fv else 'n/a'}")
        lines.append(f"  Buy below:   {_fmt_currency(bb, cur) if bb else 'n/a'}")

    fund = data.get("fundamentals") or {}
    if fund:
        lines.append("\nKey fundamentals:")
        for key, label in [
            ("trailingPE", "P/E"),
            ("forwardPE", "Fwd P/E"),
            ("marketCap", "Mkt cap"),
            ("dividendYield", "Div yield"),
            ("sector", "Sector"),
            ("beta", "Beta"),
        ]:
            val = fund.get(key)
            if val is None:
                continue
            if key == "marketCap":
                val = f"${val / 1e9:.1f}B" if val >= 1e9 else f"${val / 1e6:.0f}M"
            elif key == "dividendYield" and isinstance(val, float):
                val = f"{val * 100:.2f}%"
            elif isinstance(val, float):
                val = f"{val:.4g}"
            lines.append(f"  {label:12s}  {val}")

    news = data.get("news") or []
    if news:
        lines.append("\nRecent news:")
        for item in news[:5]:
            title = item.get("title") or item.get("headline", "")
            pub = str(item.get("published_at") or item.get("date", ""))[:10]
            lines.append(f"  [{pub}] {title}")

    note = data.get("latest_note")
    if note:
        lines.append(
            f"\nLatest research note ({str(note.get('created_at', ''))[:10]}):"
        )
        lines.append(f"  {note.get('content', '')[:300]}")

    return "\n".join(lines)


@tool
def research_compare() -> str:
    """
    Compare all tickers with saved research: current price vs fair value,
    upside %, buy-below and sell-above targets, conviction level.
    Sorted by upside descending (best opportunities first).
    """
    try:
        data = client._get("/api/v1/research/compare")
    except Exception as e:
        return f"Error fetching research comparison: {e}"

    if not data:
        return "No saved research found."

    lines = ["RESEARCH COMPARISON (sorted by upside):"]
    for r in data:
        sym = r.get("symbol", "?")
        price = r.get("current_price", 0)
        cur = r.get("currency", "EUR")
        fv = r.get("fair_value")
        upside = r.get("upside_pct")
        conviction = r.get("conviction") or ""
        buy_below = r.get("buy_below")
        sell_above = r.get("sell_above")
        upside_str = f"{upside:+.1f}%" if upside is not None else "   n/a"
        fv_str = _fmt_currency(fv, cur) if fv else "n/a"
        extra = []
        if buy_below:
            extra.append(f"buy<{_fmt_currency(buy_below, cur)}")
        if sell_above:
            extra.append(f"sell>{_fmt_currency(sell_above, cur)}")
        if conviction:
            extra.append(f"conviction {conviction}/5")
        extra_str = "  " + "  ".join(extra) if extra else ""
        lines.append(
            f"  {sym:12s}  {_fmt_currency(price, cur):>14s}  fv {fv_str:>14s}  {upside_str:>8s}{extra_str}"
        )
    return "\n".join(lines)


@tool
def decisions(status: str = "open", symbol: Optional[str] = None) -> str:
    """
    The decision journal: what was decided (buy/sell/hold/DCA/rebalance/
    traspaso), why, what was expected, when to review it, the price at the
    time and the move since. Read it before advising on a holding the owner
    already made a call on. Read-only: decisions are recorded in the web app
    or the pfm chat.

    Args:
        status: 'open' (default), 'reviewed' or 'all'.
        symbol: Only decisions about this symbol.
    """
    try:
        data = client._get("/api/v1/decisions/", {"status": status, "symbol": symbol})
    except Exception as e:
        return f"Error fetching decisions: {e}"

    items = data.get("items", [])
    if not items:
        return f"No {status} decisions in the journal."
    lines = [
        f"DECISIONS ({status}, {len(items)}; {data.get('due', 0)} due for review):"
    ]
    for d in items:
        head = f"  #{d.get('id')} {d.get('decided_on')}  {str(d.get('action', '')).upper():9s} {d.get('title')}"
        if d.get("symbol"):
            head += f"  [{d['symbol']}]"
        if d.get("status") == "due":
            head += "  ⏰ review due"
        lines.append(head)
        if d.get("price_at_decision") is not None:
            move = d.get("price_change_pct")
            lines.append(
                f"      price then {d['price_at_decision']}"
                + (f", now {d['price_now']} ({move:+.1f}%)" if move is not None else "")
            )
        if d.get("rationale"):
            lines.append(f"      why: {d['rationale']}")
        if d.get("expectation"):
            lines.append(f"      expected: {d['expectation']}")
        if d.get("review_on") and d.get("status") != "reviewed":
            lines.append(f"      review on {d['review_on']}")
        if d.get("outcome_rating"):
            lines.append(
                f"      outcome ({d['outcome_rating']}): {d.get('outcome') or '—'}"
            )
    return "\n".join(lines)


def _point_text(point: Optional[dict]) -> str:
    """One comparison point as 'date price ret% err% hit' text."""
    if not point:
        return "n/a"
    mark = {True: "hit", False: "miss", None: "-"}[point.get("direction_hit")]
    ret = point.get("realised_return_pct")
    err = point.get("target_error_pct")
    return (
        f"{point.get('date')} @ {point.get('price')}"
        + (f" {ret:+.1f}%" if ret is not None else "")
        + (f" (vs target {err:+.1f}%)" if err is not None else "")
        + f" {mark}"
    )


def _agg_text(label: str, agg: dict) -> str:
    """One aggregate as a single line."""

    def pct(key: str) -> str:
        v = agg.get(key)
        return "n/a" if v is None else f"{v:.1f}%"

    return (
        f"  {label}: n={agg.get('n', 0)}, direction hit {pct('direction_hit_rate_pct')}, "
        f"median |target error| {pct('median_abs_target_error_pct')}, "
        f"implied {pct('mean_implied_return_pct')} vs realised "
        f"{pct('mean_realised_return_pct')}, target reached "
        f"{pct('reached_target_rate_pct')}"
    )


def _calibration_lines(cal: dict) -> list[str]:
    """Lines for the calibration block of the scorecard."""

    def one(label: str, b: dict) -> str:
        hit, err = b.get("direction_hit_rate_pct"), b.get("median_abs_target_error_pct")
        shrink = b.get("shrink_factor")
        return (
            f"  {label}: n={b.get('n', 0)} ({b.get('basis')}), hit rate "
            f"{'n/a' if hit is None else f'{hit:.1f}%'}, median |target error| "
            f"{'n/a' if err is None else f'{err:.1f}%'}, shrink factor "
            f"{'n/a' if shrink is None else f'{shrink:.2f}'}"
        )

    lines = ["Calibration (all symbols):"]
    if cal.get("overall"):
        lines.append(one("overall", cal["overall"]))
    for name, block in (cal.get("by_confidence") or {}).items():
        lines.append(one(f"{name} confidence", block))
    return lines


@tool
def research_scorecard(symbol: Optional[str] = None) -> str:
    """
    Track record of the stored research reports (LLM reports and the owner's
    manual notes): how often BUY/SELL/HOLD pointed the right way and how far
    the fair value was from the price, at each report's horizon and at the
    next report for the same symbol. Pure arithmetic, no verdicts.

    Args:
        symbol: Only this symbol.
    """
    try:
        data = client._get("/api/v1/research/scorecard", {"symbol": symbol})
    except Exception as e:
        return f"Error fetching the research scorecard: {e}"

    if not data.get("total"):
        return "No research history stored yet."
    lines = [
        f"RESEARCH SCORECARD as of {data.get('as_of')}"
        f" ({data.get('total')} reports, {data.get('pending')} pending)",
        _agg_text("at horizon", data["totals"]["at_horizon"]),
        _agg_text("at next report", data["totals"]["at_next"]),
    ]
    cal = data.get("calibration")
    if cal:
        lines.extend(_calibration_lines(cal))
    for title, key in (
        ("By recommendation", "by_recommendation"),
        ("By confidence", "by_confidence"),
        ("By source", "by_source"),
    ):
        group = data.get(key) or {}
        if group:
            lines.append(f"{title}:")
            for name, agg in group.items():
                lines.append(_agg_text(f"{name} (horizon)", agg["at_horizon"]))
    for title, key in (("Best", "best"), ("Worst", "worst")):
        rows = data.get(key) or []
        if rows:
            lines.append(f"{title}:")
            for e in rows:
                lines.append(
                    f"  {e.get('symbol')} {e.get('date')} {e.get('recommendation') or '?'}"
                    f" ({e.get('source')}): horizon {_point_text(e.get('at_horizon'))};"
                    f" next {_point_text(e.get('at_next'))}"
                )
    lines.append(data.get("method", ""))
    return "\n".join(lines)
