"""Research notes and price targets."""

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
