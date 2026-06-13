#!/usr/bin/env python3
"""
pfm (Portfolio Manager) MCP server — exposes holdings, transactions, assets,
and tax report as tools, backed by the local pfm FastAPI server.

Canonical location: this file lives in the pfm repo (``~/repos/pfm/mcp/server.py``)
so it is versioned alongside the API it talks to. ``~/mcp/pfm/server.py`` is a
symlink to this file, so the existing Claude registration (which points at the
``~/mcp`` path) keeps working unchanged.

Credentials are read from ~/repos/pfm/.env.local at startup.
"""

import json
import os
import urllib.request
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ── Credentials ───────────────────────────────────────────────────────────────
_ENV_PATH = os.path.expanduser("~/repos/pfm/.env.local")
_cfg: dict[str, str] = {}
try:
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if "=" in _line and not _line.startswith("#"):
                k, _, v = _line.partition("=")
                _cfg[k.strip()] = v.strip()
except FileNotFoundError:
    pass

SERVER_URL = _cfg.get("PORTF_SERVER_URL", "http://127.0.0.1:8000")
API_KEY = _cfg.get("SERVER_API_KEY", "")

mcp = FastMCP("pfm")


# ── Internal helpers ──────────────────────────────────────────────────────────
def _get(path: str, params: Optional[dict] = None) -> dict | list:
    url = f"{SERVER_URL}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _fmt_currency(value: float, currency: str = "EUR") -> str:
    return f"{value:,.2f} {currency}"


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def portfolio_holdings(
    portfolio_id: Optional[int] = None,
    asset_type: Optional[str] = None,
    sort_by: str = "value",
    limit: Optional[int] = None,
) -> str:
    """
    Get current portfolio holdings with P&L. Supports filtering and sorting
    to answer questions like 'show my best performers', 'how much in crypto',
    'top 5 positions'.

    Args:
        portfolio_id: Optional broker/account filter.
        asset_type: Optional type filter — 'stock', 'etf', 'crypto', 'p2p', etc.
        sort_by: How to order results:
            'value'    — largest position first (default)
            'pnl_pct'  — best % return first (winners → losers)
            'pnl_amt'  — biggest absolute P&L first
            'symbol'   — alphabetical
            'cost'     — most invested first
        limit: Return only the top N holdings after sorting.
    """
    try:
        params = {"portfolio_id": portfolio_id} if portfolio_id else {}
        data = _get("/api/v1/portfolios/holdings", params or None)
    except Exception as e:
        return f"Error fetching holdings: {e}"

    holdings = data.get("holdings", [])
    summary = data.get("summary", {})

    if not holdings:
        return "No holdings found."

    # Filter
    if asset_type:
        holdings = [
            h for h in holdings if h.get("asset_type", "").lower() == asset_type.lower()
        ]
        if not holdings:
            return f"No {asset_type} holdings found."

    # Sort
    sort_key = {
        "value": lambda h: h.get("total_value_eur", h.get("total_value", 0)),
        "pnl_pct": lambda h: h.get("pnl_pct", 0),
        "pnl_amt": lambda h: h.get("pnl_amount", 0),
        "symbol": lambda h: h.get("symbol", ""),
        "cost": lambda h: h.get("cost_basis", 0),
    }.get(sort_by, lambda h: h.get("total_value_eur", 0))
    reverse = sort_by != "symbol"
    holdings = sorted(holdings, key=sort_key, reverse=reverse)

    if limit:
        holdings = holdings[:limit]

    filter_desc = f" [{asset_type}]" if asset_type else ""
    sort_desc = {
        "value": "by value",
        "pnl_pct": "by % return",
        "pnl_amt": "by P&L",
        "symbol": "A-Z",
        "cost": "by cost",
    }.get(sort_by, "")
    lines = [f"HOLDINGS{filter_desc} — {len(holdings)} positions ({sort_desc}):"]

    for h in holdings:
        pnl_sign = "+" if h["pnl_amount"] >= 0 else ""
        name = h.get("name", "")
        name_str = f"  {name}" if name else ""
        lines.append(
            f"  {h['symbol']:12s} [{h['asset_type']:6s}]"
            f"  {h['quantity']:>12.4f} units"
            f"  avg {_fmt_currency(h['avg_price'], h['currency'])}"
            f"  now {_fmt_currency(h['current_price'], h['currency'])}"
            f"  value {_fmt_currency(h['total_value'], h['currency'])}"
            f"  P&L {pnl_sign}{_fmt_currency(h['pnl_amount'], h['currency'])} ({pnl_sign}{h['pnl_pct']:.1f}%)"
            f"{name_str}"
        )

    # Summary — recompute from filtered set if filtered, else use API totals
    if asset_type:
        filt_value = sum(
            h.get("total_value_eur", h.get("total_value", 0)) for h in holdings
        )
        filt_cost = sum(h.get("cost_basis", 0) for h in holdings)
        filt_pnl = filt_value - filt_cost
        filt_pnl_pct = (filt_pnl / filt_cost * 100) if filt_cost else 0
        pnl_sign = "+" if filt_pnl >= 0 else ""
        lines.append(f"\n{asset_type.upper()} TOTAL VALUE: {_fmt_currency(filt_value)}")
        lines.append(
            f"{asset_type.upper()} TOTAL P&L:   {pnl_sign}{_fmt_currency(filt_pnl)} ({pnl_sign}{filt_pnl_pct:.1f}%)"
        )
    else:
        pnl_sign = "+" if summary.get("total_pnl", 0) >= 0 else ""
        lines.append(f"\nTOTAL VALUE:  {_fmt_currency(summary.get('total_value', 0))}")
        lines.append(f"TOTAL COST:   {_fmt_currency(summary.get('total_cost', 0))}")
        lines.append(
            f"TOTAL P&L:    {pnl_sign}{_fmt_currency(summary.get('total_pnl', 0))}"
            f" ({pnl_sign}{summary.get('total_pnl_pct', 0):.1f}%)"
        )
    return "\n".join(lines)


@mcp.tool()
def list_portfolios() -> str:
    """
    List all portfolios (broker accounts) tracked in the system, with their
    names, currencies, and IDs.
    """
    try:
        data = _get("/api/v1/portfolios/")
    except Exception as e:
        return f"Error fetching portfolios: {e}"

    if not data:
        return "No portfolios found."

    lines = ["PORTFOLIOS:"]
    for p in data:
        lines.append(
            f"  [{p.get('id')}] {p.get('name', 'unnamed'):20s}"
            f"  {p.get('currency', 'EUR'):4s}"
            f"  {p.get('description', '')}"
        )
    return "\n".join(lines)


@mcp.tool()
def list_assets(asset_type: Optional[str] = None) -> str:
    """
    List all tracked assets with their latest known price.

    Args:
        asset_type: Optional filter — 'stock', 'etf', 'crypto', 'p2p', etc.
    """
    try:
        data = _get("/api/v1/assets/")
    except Exception as e:
        return f"Error fetching assets: {e}"

    if asset_type:
        data = [
            a for a in data if a.get("asset_type", "").lower() == asset_type.lower()
        ]

    if not data:
        return f"No assets found{' for type ' + asset_type if asset_type else ''}."

    lines = [f"ASSETS ({len(data)}):"]
    for a in sorted(data, key=lambda x: x.get("symbol", "")):
        price_str = ""
        if a.get("latest_price"):
            price_str = (
                f"  price {_fmt_currency(a['latest_price'], a.get('currency', 'EUR'))}"
            )
        lines.append(
            f"  {a.get('symbol', '?'):12s} [{a.get('asset_type', '?'):6s}]"
            f"  {a.get('name', ''):30s}{price_str}"
        )
    return "\n".join(lines)


@mcp.tool()
def list_transactions(
    limit: int = 20,
    order: str = "recent",
    transaction_type: Optional[str] = None,
    asset_type: Optional[str] = None,
    asset_symbol: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> str:
    """
    List transactions with flexible filtering. Handles questions like:
    'what did I buy this year', 'first stocks I ever bought', 'sells in 2024',
    'all crypto buys', 'NVDA transaction history'.

    Args:
        limit: Number of transactions to return (default 20).
        order: 'recent' (default, newest first) or 'oldest' (chronological first).
            Use 'oldest' to find first-ever buys of a symbol or asset type.
        transaction_type: Filter — 'BUY', 'SELL', 'DIVIDEND', 'INTEREST'.
        asset_type: Filter — 'stock', 'etf', 'crypto', 'p2p', etc.
        asset_symbol: Filter to one ticker, e.g. 'NVDA', 'BTC-EUR'.
        date_from: Earliest date to include, YYYY-MM-DD (e.g. '2025-01-01').
        date_to: Latest date to include, YYYY-MM-DD (e.g. '2025-12-31').
        portfolio_id: Filter by portfolio/broker ID.
    """
    # Fetch all when: oldest order (need to reverse full history), or date filtering
    # (API has no date filter), or asset_type/transaction_type filtering.
    # For recent-only with no client-side filters, use server limit for speed.
    needs_all = (
        order.lower() == "oldest"
        or date_from
        or date_to
        or asset_type
        or transaction_type
    )
    try:
        params: dict = {"limit": min(max(limit, 1), 500)} if not needs_all else {}
        if portfolio_id:
            params["portfolio_id"] = portfolio_id
        data = _get("/api/v1/transactions/", params)
    except Exception as e:
        return f"Error fetching transactions: {e}"

    # Client-side filters
    if asset_symbol:
        sym = asset_symbol.upper()
        data = [
            t
            for t in data
            if t.get("symbol", "").upper() == sym
            or t.get("asset_symbol", "").upper() == sym
        ]
    if transaction_type:
        tt = transaction_type.upper()
        data = [t for t in data if t.get("transaction_type", "").upper() == tt]
    if asset_type:
        at = asset_type.lower()
        data = [
            t
            for t in data
            if (t.get("asset", {}) or {}).get("asset_type", "").lower() == at
            or t.get("asset_type", "").lower() == at
        ]
    if date_from:
        data = [
            t
            for t in data
            if str(t.get("transaction_date", t.get("date", "")))[:10] >= date_from
        ]
    if date_to:
        data = [
            t
            for t in data
            if str(t.get("transaction_date", t.get("date", "")))[:10] <= date_to
        ]

    # API returns DESC; reverse for oldest-first, then take limit
    if order.lower() == "oldest":
        data = list(reversed(data))
    data = data[:limit]

    if not data:
        return "No transactions found matching the criteria."

    # Build header describing what's shown
    parts = []
    if transaction_type:
        parts.append(transaction_type.upper())
    if asset_type:
        parts.append(asset_type)
    if asset_symbol:
        parts.append(asset_symbol.upper())
    if date_from or date_to:
        parts.append(f"{date_from or '...'} → {date_to or 'now'}")
    direction = "oldest" if order.lower() == "oldest" else "most recent"
    desc = " · ".join(parts) if parts else "all"
    lines = [f"TRANSACTIONS ({desc}, {direction} {len(data)}):"]

    for t in data:
        tx_type = t.get("transaction_type", t.get("type", "?")).upper()
        symbol = t.get("symbol", t.get("asset_symbol", "?"))
        name = (t.get("asset", {}) or {}).get("name", "")
        atype = (t.get("asset", {}) or {}).get("asset_type", t.get("asset_type", ""))
        date = str(t.get("transaction_date", t.get("date", "?")))[:10]
        qty = float(t.get("quantity", 0))
        price = float(t.get("price", 0))
        total = float(t.get("total_amount", qty * price))
        currency = t.get("currency", "EUR")
        meta = f"  {name}" if name else ""
        if atype and not asset_type:
            meta += f" [{atype}]"
        lines.append(
            f"  {date}  {tx_type:8s}  {symbol:12s}"
            f"  {qty:>10.4f} @ {_fmt_currency(price, currency)}"
            f"  = {_fmt_currency(total, currency)}{meta}"
        )
    return "\n".join(lines)


@mcp.tool()
def tax_report(year: Optional[int] = None) -> str:
    """
    Generate a Spanish IRPF tax summary for a year: per-lot realised capital
    gains (FIFO), dividend income and withholding, plus an estimated savings-base
    tax. Backed by /analytics/tax-report and /analytics/tax-estimate.

    Args:
        year: Tax year (defaults to the current calendar year).
    """
    params = {"year": year} if year else {}
    try:
        data = _get("/api/v1/analytics/tax-report", params or None)
    except Exception as e:
        return f"Error fetching tax report: {e}"

    # Estimate is best-effort — don't fail the whole report if it errors.
    try:
        est = _get("/api/v1/analytics/tax-estimate", params or None)
    except Exception:
        est = {}

    yr = data.get("year", year or "N/A")
    lines = [f"IRPF TAX REPORT — {yr}:"]

    lots = data.get("realised_lots", [])
    if lots:
        lines.append("\nRealised capital gains (FIFO, Box 27):")
        for lot in lots:
            gain = float(lot.get("gain_loss", 0) or 0)
            sign = "+" if gain >= 0 else ""
            lines.append(
                f"  {lot.get('symbol', '?'):12s}"
                f"  sold {str(lot.get('sell_date', '?'))[:10]}"
                f"  qty {float(lot.get('quantity', 0)):.4f}"
                f"  cost {_fmt_currency(float(lot.get('cost_basis', 0)))}"
                f"  proceeds {_fmt_currency(float(lot.get('proceeds', 0)))}"
                f"  G/L {sign}{_fmt_currency(gain)}"
            )

    sign = "+" if data.get("realised_gain_total", 0) >= 0 else ""
    lines.append("")
    lines.append(
        f"Realised gain total:  {sign}{_fmt_currency(data.get('realised_gain_total', 0))}"
        f"  ({data.get('lot_count', 0)} lot(s))"
    )
    lines.append(
        f"Dividends (gross):    {_fmt_currency(data.get('dividends_gross_eur', 0))}"
    )
    lines.append(
        f"Dividend withholding: {_fmt_currency(data.get('dividend_withholding_eur', 0))}"
    )

    if est:
        lines.append("\nIRPF savings-base estimate:")
        lines.append(
            f"  Interest income:    {_fmt_currency(est.get('interest_income_eur', 0))}"
        )
        lines.append(
            f"  Savings base:       {_fmt_currency(est.get('savings_base_eur', 0))}"
        )
        lines.append(
            f"  Estimated tax:      {_fmt_currency(est.get('estimated_tax_eur', 0))}"
        )
        lines.append(
            f"  Unrealised gain:    {_fmt_currency(est.get('unrealised_gain_eur', 0))}"
        )

    note = data.get("note")
    if note:
        lines.append(f"\n{note}")

    return "\n".join(lines)


@mcp.tool()
def quote(symbols: str, max_age: int = 86400) -> str:
    """
    Market quotes (price, daily change %, currency) for one or more
    Yahoo-format tickers, served from pfm's shared market-data cache.

    Args:
        symbols: Comma-separated Yahoo tickers, e.g. 'NVDA,ASML.AS,BTC-EUR'.
        max_age: Maximum acceptable data age in seconds (default 1 day).
            Lower it (e.g. 900) when intraday freshness matters.
    """
    try:
        data = _get("/api/v1/market/quotes", {"symbols": symbols, "max_age": max_age})
    except Exception as e:
        return f"Error fetching quotes: {e}"

    lines = ["QUOTES:"]
    for q in data.get("quotes", []):
        if q.get("error"):
            lines.append(f"  {q.get('symbol', '?'):12s}  unavailable ({q['error']})")
            continue
        chg = f"{q['change_pct']:+.2f}%" if q.get("change_pct") is not None else "n/a"
        stale = "  [stale]" if q.get("stale") else ""
        lines.append(
            f"  {q['symbol']:12s} {q['price']:>12.4f} {q.get('currency') or '':3s}"
            f"  {chg}{stale}"
        )
    return "\n".join(lines)


@mcp.tool()
def performance(period: str = "all", benchmark: str = "^GSPC") -> str:
    """
    Portfolio performance: total return, money-weighted IRR, and period return
    vs a benchmark.

    Args:
        period: Return window — 'ytd', '1m', '1y', or 'all' (default).
        benchmark: Yahoo ticker for comparison, default '^GSPC' (S&P 500).
    """
    try:
        data = _get(
            "/api/v1/analytics/performance", {"period": period, "benchmark": benchmark}
        )
    except Exception as e:
        return f"Error fetching performance: {e}"

    lines = [f"PERFORMANCE ({period.upper()}):"]
    lines.append(f"  Invested:         {_fmt_currency(data.get('invested_eur', 0))}")
    lines.append(
        f"  Current value:    {_fmt_currency(data.get('current_value_eur', 0))}"
    )
    lines.append(
        f"  Realised P&L:     {_fmt_currency(data.get('realised_pnl_eur', 0))}"
    )
    tr = data.get("total_return_pct")
    lines.append(
        f"  Total return:     {tr:+.2f}%"
        if tr is not None
        else "  Total return:     n/a"
    )
    irr = data.get("money_weighted_irr_pct")
    lines.append(
        f"  IRR (MWRR):       {irr:+.2f}%"
        if irr is not None
        else "  IRR (MWRR):       n/a"
    )
    pr = data.get("period_return_pct")
    lines.append(
        f"  Period return:    {pr:+.2f}%"
        if pr is not None
        else "  Period return:    n/a"
    )
    br = data.get("benchmark_return_pct")
    bname = data.get("benchmark", benchmark)
    lines.append(
        f"  {bname} return:    {br:+.2f}%"
        if br is not None
        else f"  {bname}:          n/a"
    )
    return "\n".join(lines)


@mcp.tool()
def dividends() -> str:
    """
    Dividend income history by year and by symbol, trailing-12-month totals,
    yield-on-cost per position, and projected forward annual income.
    """
    try:
        data = _get("/api/v1/analytics/dividends")
    except Exception as e:
        return f"Error fetching dividends: {e}"

    lines = ["DIVIDENDS:"]
    by_year = data.get("by_year", {})
    if by_year:
        lines.append("\nBy year:")
        for yr, amt in sorted(by_year.items()):
            lines.append(f"  {yr}:  {_fmt_currency(float(amt))}")

    lines.append(f"\nTrailing 12m total:  {_fmt_currency(data.get('ttm', 0))}")
    lines.append(
        f"Projected annual:    {_fmt_currency(data.get('projected_annual', 0))}"
    )

    ttm_sym = data.get("ttm_by_symbol", {})
    names = data.get("names", {})
    yoc = data.get("yield_on_cost", {})
    if ttm_sym:
        lines.append("\nTTM by symbol:")
        for sym, amt in sorted(ttm_sym.items(), key=lambda x: -x[1]):
            name = names.get(sym, sym)
            yoc_str = f"  YoC {yoc[sym]:.1f}%" if sym in yoc else ""
            lines.append(
                f"  {sym:12s}  {_fmt_currency(float(amt)):>14s}  {name}{yoc_str}"
            )
    return "\n".join(lines)


@mcp.tool()
def diversification() -> str:
    """
    Portfolio diversification breakdown by asset type, sector, currency,
    and country. Includes concentration Herfindahl index and largest position.
    """
    try:
        data = _get("/api/v1/analytics/diversification")
    except Exception as e:
        return f"Error fetching diversification: {e}"

    def fmt_map(label: str, d: dict) -> list[str]:
        out = [f"\n{label}:"]
        for k, pct in d.items():
            out.append(f"  {k:25s}  {pct:.1f}%")
        return out

    lines = [
        f"DIVERSIFICATION  (total {_fmt_currency(data.get('total_value_eur', 0))})"
    ]
    largest = data.get("largest_position_symbol")
    if largest:
        lines.append(
            f"Largest position: {largest} ({data.get('largest_position_name', largest)})  "
            f"{data.get('largest_position_pct', 0):.1f}%"
        )
    hhi = data.get("concentration_hhi")
    if hhi is not None:
        lines.append(f"Concentration HHI: {hhi:.0f}  (10 000 = single asset)")
    lines += fmt_map("By asset type", data.get("by_asset_type", {}))
    lines += fmt_map("By currency", data.get("by_currency", {}))
    lines += fmt_map("By sector", data.get("by_sector", {}))
    lines += fmt_map("By country", data.get("by_country", {}))
    return "\n".join(lines)


@mcp.tool()
def risk() -> str:
    """
    Portfolio risk metrics derived from daily snapshot history: max drawdown,
    annualised volatility, and Sharpe ratio.
    """
    try:
        data = _get("/api/v1/analytics/risk")
    except Exception as e:
        return f"Error fetching risk metrics: {e}"

    if data.get("note"):
        return f"RISK:\n  {data['note']}"

    lines = ["RISK METRICS:"]
    dd = data.get("max_drawdown_pct")
    vol = data.get("volatility_pct")
    sr = data.get("sharpe_ratio")
    lines.append(
        f"  Max drawdown:     {dd:.2f}%"
        if dd is not None
        else "  Max drawdown:     n/a"
    )
    lines.append(
        f"  Volatility (ann): {vol:.2f}%"
        if vol is not None
        else "  Volatility:       n/a"
    )
    lines.append(
        f"  Sharpe ratio:     {sr:.2f}" if sr is not None else "  Sharpe ratio:     n/a"
    )
    lines.append(f"  Snapshots used:   {data.get('snapshots_used', '?')}")
    return "\n".join(lines)


@mcp.tool()
def fundamentals(symbol: str) -> str:
    """
    Key yfinance fundamentals for any ticker (held or not): P/E, market cap,
    dividend yield, 52-week range, sector, beta, and more.

    Args:
        symbol: Yahoo-format ticker, e.g. 'NVDA', 'ASML.AS', 'BTC-EUR'.
    """
    try:
        data = _get(f"/api/v1/market/fundamentals/{symbol.upper()}")
    except Exception as e:
        return f"Error fetching fundamentals for {symbol}: {e}"

    if not data:
        return f"No fundamentals available for {symbol.upper()}."

    lines = [f"FUNDAMENTALS — {symbol.upper()}:"]
    fields = [
        ("shortName", "Name"),
        ("sector", "Sector"),
        ("industry", "Industry"),
        ("country", "Country"),
        ("marketCap", "Market cap"),
        ("trailingPE", "Trailing P/E"),
        ("forwardPE", "Forward P/E"),
        ("priceToBook", "Price/Book"),
        ("dividendYield", "Div yield"),
        ("trailingAnnualDividendYield", "Div yield (TTM)"),
        ("fiftyTwoWeekLow", "52w low"),
        ("fiftyTwoWeekHigh", "52w high"),
        ("fiftyDayAverage", "50d MA"),
        ("twoHundredDayAverage", "200d MA"),
        ("beta", "Beta"),
    ]
    for key, label in fields:
        val = data.get(key)
        if val is None:
            continue
        if key == "marketCap":
            val = f"${val / 1e9:.1f}B" if val >= 1e9 else f"${val / 1e6:.0f}M"
        elif key in ("dividendYield", "trailingAnnualDividendYield") and isinstance(
            val, float
        ):
            val = f"{val * 100:.2f}%"
        elif isinstance(val, float):
            val = f"{val:.4g}"
        lines.append(f"  {label:22s}  {val}")
    return "\n".join(lines)


@mcp.tool()
def research_lookup(symbol: str) -> str:
    """
    Comprehensive research snapshot for any ticker (held or not): current
    price, full position data, fundamentals, recent news headlines, and saved
    price targets.

    Args:
        symbol: Yahoo-format ticker, e.g. 'NVDA', 'ASML.AS', 'BTC-EUR'.
    """
    try:
        data = _get(f"/api/v1/research/{symbol.upper()}/lookup")
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


@mcp.tool()
def research_compare() -> str:
    """
    Compare all tickers with saved research: current price vs fair value,
    upside %, buy-below and sell-above targets, conviction level.
    Sorted by upside descending (best opportunities first).
    """
    try:
        data = _get("/api/v1/research/compare")
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
            extra.append(conviction)
        extra_str = "  " + "  ".join(extra) if extra else ""
        lines.append(
            f"  {sym:12s}  {_fmt_currency(price, cur):>14s}  fv {fv_str:>14s}  {upside_str:>8s}{extra_str}"
        )
    return "\n".join(lines)


@mcp.tool()
def watchlist() -> str:
    """
    Show all tickers on the watchlist with current price, buy-below target,
    and distance to the buy zone.
    """
    try:
        data = _get("/api/v1/watchlist/")
    except Exception as e:
        return f"Error fetching watchlist: {e}"

    if not data:
        return "Watchlist is empty."

    lines = ["WATCHLIST:"]
    for e in data:
        sym = e.get("symbol", "?")
        name = e.get("name") or ""
        price = e.get("current_price")
        buy_below = e.get("buy_below")
        dist = e.get("distance_to_buy_pct")
        in_zone = e.get("in_buy_zone", False)

        price_str = _fmt_currency(price) if price else "n/a"
        buy_str = _fmt_currency(buy_below) if buy_below else "no target"
        dist_str = f"  {dist:+.1f}% to target" if dist is not None else ""
        zone_flag = "  🟢 IN BUY ZONE" if in_zone else ""
        lines.append(
            f"  {sym:12s}  {price_str:>12s}  buy<{buy_str}{dist_str}{zone_flag}  {name}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
