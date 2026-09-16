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

import calendar
import json
import os
import urllib.request
from datetime import date
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
    Portfolio exposure with funds looked through: asset class, region, sector,
    country and currency, plus concentration, how much of the book is actually
    classified, and any funds holding the same exposure.
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

    coverage = data.get("coverage") or {}
    classified = coverage.get("classified_pct")
    if classified is not None:
        lines.append(
            f"Coverage: {classified:.1f}% of value classified by region, "
            f"{coverage.get('sector_classified_pct', 0):.1f}% by sector"
        )
    for fund in coverage.get("unprofiled", []):
        lines.append(
            f"  ! No look-through profile: {fund['name']} "
            f"({_fmt_currency(fund['value_eur'])})"
        )

    lines += fmt_map(
        "By asset class (funds looked through)", data.get("by_asset_class", {})
    )
    lines += fmt_map(
        "By region (equity, funds looked through)", data.get("by_region_equity", {})
    )
    lines += fmt_map("By region (all)", data.get("by_region", {}))
    lines += fmt_map("By sector", data.get("by_sector", {}))
    lines += fmt_map(
        "By currency exposure (approximate)", data.get("by_currency_exposure", {})
    )
    lines += fmt_map("By quote currency", data.get("by_currency", {}))
    lines += fmt_map("By asset type", data.get("by_asset_type", {}))
    lines += fmt_map("By country (direct holdings)", data.get("by_country", {}))

    # Overlap is a second call, and a useful breakdown must not be lost if it
    # fails.
    try:
        overlap = _get("/api/v1/analytics/fund-overlap")
    except Exception as e:
        lines.append(f"\nFund overlap unavailable: {e}")
        return "\n".join(lines)

    groups = overlap.get("groups", [])
    if groups:
        lines.append("\nFunds holding the same exposure:")
        for group in groups:
            names = " + ".join(m["name"] for m in group["members"])
            tag = {
                "consolidation_candidate": "consolidation candidate",
                "similar": "similar",
                "informational": "nested, informational",
            }.get(group["kind"], group["kind"])
            lines.append(
                f"  [{tag}] {names} — {_fmt_currency(group['combined_value_eur'])} "
                f"({group['combined_pct']:.1f}%)"
            )
            lines.append(f"      {group['reason']}")
            if group.get("transferable"):
                lines.append(
                    "      Both are funds: a traspaso can merge them without "
                    "realising a gain."
                )
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
            extra.append(f"conviction {conviction}/5")
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


@mcp.tool()
def portfolio_health(portfolio_id: Optional[int] = None) -> str:
    """
    Return the AI-generated portfolio health analysis: five scored categories
    (diversification, risk_adjusted_return, income, fees, tax_efficiency each
    scored 1–10), prioritised recommendations, and a summary.

    The analysis is cached — run it first from the Research → Portfolio Health
    page in the web UI. Returns an error message if no cached result exists.

    Args:
        portfolio_id: Optional broker/account filter (uses cached key for that ID).
    """
    params = {"portfolio_id": portfolio_id} if portfolio_id else {}
    try:
        data = _get("/api/v1/research/portfolio-analysis", params or None)
    except Exception as e:
        return f"Error fetching portfolio health: {e}"

    if "error" in data:
        return data["error"]

    lines = ["PORTFOLIO HEALTH ANALYSIS:"]
    scores = data.get("scores", {})
    for category, info in scores.items():
        score = info.get("score", "?")
        reason = info.get("reason", "")
        lines.append(f"  {category.replace('_', ' ').title():30s} {score}/10  {reason}")

    summary = data.get("summary", "")
    if summary:
        lines.append(f"\nSummary: {summary}")

    recs = data.get("recommendations", [])
    if recs:
        lines.append("\nRecommendations:")
        for r in recs:
            lines.append(f"  • {r}")

    cached_at = data.get("cached_at") or data.get("generated_at", "")
    if cached_at:
        lines.append(f"\n(Analysis from {str(cached_at)[:10]})")

    return "\n".join(lines)


@mcp.tool()
def tax_estimate(year: Optional[int] = None) -> str:
    """
    Quick IRPF tax estimate for a given year: realised capital gains, dividend
    income, interest income, and the progressive tax on the savings base.

    Args:
        year: Tax year (defaults to the current calendar year).
    """
    params = {"year": year} if year else {}
    try:
        data = _get("/api/v1/analytics/tax-estimate", params or None)
    except Exception as e:
        return f"Error fetching tax estimate: {e}"

    yr = data.get("year", year or "?")
    lines = [f"IRPF TAX ESTIMATE — {yr}:"]
    lines.append(
        f"  Realised capital gains:  {_fmt_currency(data.get('realised_gain_eur', 0))}"
    )
    lines.append(
        f"  Dividend income:         {_fmt_currency(data.get('dividend_income_eur', 0))}"
    )
    lines.append(
        f"  Interest income:         {_fmt_currency(data.get('interest_income_eur', 0))}"
    )
    lines.append("  ─────────────────────────────────────────")
    lines.append(
        f"  Savings base total:      {_fmt_currency(data.get('savings_base_eur', 0))}"
    )
    tax = data.get("estimated_tax_eur", data.get("irpf_savings_tax_eur", 0))
    lines.append(f"  Estimated IRPF tax:      {_fmt_currency(tax)}")
    unrealised = data.get("unrealised_gain_eur")
    if unrealised is not None:
        lines.append(f"  Unrealised gain (info):  {_fmt_currency(unrealised)}")
    return "\n".join(lines)


@mcp.tool()
def goals() -> str:
    """
    List all financial goals with their current progress, projected final value,
    whether they are on track, and the required monthly contribution.
    """
    try:
        data = _get("/api/v1/goals/")
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

        track_str = (
            "  ✅ ON TRACK" if on_track else ("  ⚠️ BEHIND" if on_track is False else "")
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
    return "\n".join(lines)


def _today() -> date:
    """Today's date — a seam so tests can pin the calendar."""
    return date.today()


@mcp.tool()
def networth() -> str:
    """
    Total net worth: live brokerage positions + bank-account balances (from
    imported bank statements) + active fixed deposits + manually entered assets
    (home, pension, ...) minus manual liabilities (mortgage, loans). Lists each
    bank account, deposit and manual item. This is the tool for cash questions —
    portfolio_holdings only sees invested positions.
    """
    try:
        data = _get("/api/v1/networth/")
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
        f"  Fixed deposits (active):    {_fmt_currency(float(data.get('deposits_eur') or 0))}"
    )
    lines.append(
        f"  Other assets (manual):      {_fmt_currency(float(data.get('manual_assets_eur') or 0))}"
    )
    lines.append(
        f"  Liabilities (manual):       {_fmt_currency(-liabilities_total if liabilities_total else 0.0)}"
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
    return "\n".join(lines)


@mcp.tool()
def action_items(category: Optional[str] = None) -> str:
    """
    Open action items pfm has detected, most severe first: stale broker/bank
    imports (with the date to upload from), data-quality issues, failed price
    updates, stale research on held positions, off-track goals, budget overruns
    and watchlist/price-target alerts. Items dismissed in the web UI still show
    here (dismissal is per-browser), and the Net Worth setup checklist is not
    included (it is computed in the web client).

    Args:
        category: Optional filter — 'import', 'data_quality', 'errors', 'goals',
            'budget' or 'watchlist'.
    """
    try:
        data = _get("/api/v1/action-items/")
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


@mcp.tool()
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
        data = _get("/api/v1/budgets/summary")
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
        today = _today()
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


@mcp.tool()
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
        data = _get("/api/v1/spending/summary", {"days": days})
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
            trend = _get("/api/v1/spending/trend", {"months": trend_months})
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


@mcp.tool()
def rebalance_analysis() -> str:
    """
    Current allocation by asset type vs the target allocation set on the pfm
    Rebalance page: drift in percentage points and EUR, plus suggested buy/sell
    amounts to get back to target. Asset-type level only, and tax-unaware.
    """
    try:
        data = _get("/api/v1/rebalance/analysis")
    except Exception as e:
        return f"Error fetching rebalance analysis: {e}"

    allocations = data.get("allocations") or []
    if not allocations or not data.get("targets_sum_pct"):
        return "No allocation targets set (set them on the Rebalance page in the pfm web UI)."

    lines = [
        f"REBALANCE — total {_fmt_currency(float(data.get('total_value_eur') or 0))}:"
    ]
    targets_sum = float(data.get("targets_sum_pct") or 0)
    if abs(targets_sum - 100) > 0.5:
        lines.append(
            f"⚠️ Targets sum to {targets_sum:.1f}%, not 100% — drifts are measured"
            " against incomplete targets."
        )
    for a in sorted(allocations, key=lambda x: -abs(float(x.get("drift_pct") or 0))):
        lines.append(
            f"  {a.get('asset_type') or '?':14s}"
            f" now {float(a.get('current_pct') or 0):5.1f}%"
            f"  target {float(a.get('target_pct') or 0):5.1f}%"
            f"  drift {float(a.get('drift_pct') or 0):+.1f}pp"
            f" ({float(a.get('drift_eur') or 0):+,.2f} EUR)"
        )

    actions = data.get("actions") or []
    if actions:
        lines.append("\nSuggested moves:")
        for act in actions:
            lines.append(
                f"  {(act.get('action') or '?').upper():5s}"
                f" {act.get('asset_type') or '?':14s}"
                f" {_fmt_currency(float(act.get('amount_eur') or 0))}"
                f"  {act.get('reason') or ''}"
            )
    else:
        lines.append("\nWithin tolerance — no moves suggested.")
    lines.append(
        "\nIgnores taxes: selling to rebalance can realise gains — run"
        " wash_sale_check before selling anything at a loss."
    )
    return "\n".join(lines)


@mcp.tool()
def data_freshness(stale_days: int = 4) -> str:
    """
    How current pfm's price data is: when prices were last refreshed, the
    latest price date, and which auto-priced held assets are stale or have never
    been priced. Check this before quoting position values if in doubt.

    Args:
        stale_days: A price older than this many days counts as stale (default 4).
    """
    try:
        data = _get("/api/v1/analytics/data-freshness", {"stale_days": stale_days})
    except Exception as e:
        return f"Error fetching data freshness: {e}"

    lines = ["PRICE DATA FRESHNESS:"]
    last = str(data.get("last_refresh") or "never")[:16].replace("T", " ")
    age = data.get("refresh_age_hours")
    age_str = f" ({float(age):.1f}h ago)" if age is not None else ""
    lines.append(f"  Last price refresh: {last}{age_str}")
    lines.append(f"  Prices as of:       {str(data.get('prices_as_of') or '?')[:10]}")

    checked = data.get("checked") or 0
    threshold = data.get("stale_days_threshold", stale_days)
    stale = data.get("stale") or []
    if not stale:
        lines.append(f"\nAll {checked} priced assets are fresh (≤ {threshold} days).")
        return "\n".join(lines)

    lines.append(
        f"\n{data.get('stale_count', len(stale))} of {checked} auto-priced assets need"
        f" attention (older than {threshold} days, or never priced):"
    )
    for s in stale:
        if s.get("age_days") is None:
            detail = s.get("reason") or "no price data"
        else:
            detail = (
                f"{s['age_days']}d old (last {str(s.get('price_date') or '?')[:10]})"
            )
        lines.append(f"  {s.get('symbol') or '?':12s} {detail}  {s.get('name') or ''}")
    return "\n".join(lines)


# ── Antiaplicación (Spanish wash-sale rule, art. 33.5.f/g LIRPF) ─────────────
# A loss on selling securities is not deductible while homogeneous securities
# bought within 2 months before or after the sale (listed) — or 1 year
# (unlisted, e.g. most mutual-fund units) — are still held. Crypto is not
# "valores homogéneos" in the usual reading, so it is left out, as are cash and
# the synthetic MINTOS asset (P2P loan principal, not a security).
_WASH_LISTED_MONTHS = 2
_WASH_UNLISTED_MONTHS = 12
_WASH_EXCLUDED_TYPES = {"crypto", "cash"}
_WASH_EXCLUDED_SYMBOLS = {"MINTOS"}
_FUND_NAME_WORDS = {"fund", "idx", "fondo", "fonds"}


def _add_months(d: date, months: int) -> date:
    """Shift a date by calendar months, clamping to the target month's end."""
    index = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _parse_date(value: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _wash_window_months(asset: dict) -> int:
    """1 year for unlisted fund units, 2 months for everything else.

    pfm has no reliable fund type — heuristic imports store index funds as
    "stock", and Indexa's funds are "etf" on the "Funds" exchange — so a fund is
    recognised by type, by that exchange, or (for anything not typed etf) by a
    fund word in its name. Erring towards the year is the safe side: a 2-month
    answer where the law says a year costs the deduction.
    """
    asset_type = (asset.get("asset_type") or "").lower()
    if asset_type == "mutual_fund" or asset.get("exchange") == "Funds":
        return _WASH_UNLISTED_MONTHS
    name = (asset.get("name") or "").lower()
    words = set("".join(c if c.isalnum() else " " for c in name).split())
    if asset_type != "etf" and words & _FUND_NAME_WORDS:
        return _WASH_UNLISTED_MONTHS
    return _WASH_LISTED_MONTHS


def _wash_sale_analysis(
    lots: list,
    transactions: list,
    assets: dict,
    today: date,
    symbol: Optional[str] = None,
    holdings: Optional[list] = None,
) -> dict:
    """Classify loss sales and recent buys against the antiaplicación window.

    Args:
        lots: FIFO realised lots (tax-report ``realised_lots``) — each carries
            symbol, sell_date, purchase_date and gain_loss_eur.
        transactions: All transactions; only ``buy`` rows are used.
        assets: symbol -> asset dict (asset_type, exchange, name), to pick the
            window and skip what the rule does not cover.
        today: Reference date.
        symbol: Optional single-symbol filter (case-insensitive).
        holdings: Optional current positions (symbol, pnl_amount). When given,
            ``recent_buys`` is limited to positions still held, each flagged
            with ``at_loss``.

    Returns:
        ``blocked``: loss sales with a repurchase inside the window. A buy
        before the sale counts only if FIFO did not consume it for that sale
        (its date is not one of the sale's lot purchase dates).
        ``no_rebuy``: loss sales still inside their window with no repurchase
        yet — buying before ``until`` would block the loss.
        ``recent_buys``: symbols bought inside the last window — selling them
        at a loss before ``until`` would be blocked.
    """
    wanted = symbol.upper() if symbol else None
    by_symbol = {str(k).upper(): v or {} for k, v in assets.items()}

    def covered(sym: str) -> bool:
        asset_type = (by_symbol.get(sym, {}).get("asset_type") or "").lower()
        return (
            bool(sym)
            and (wanted is None or sym == wanted)
            and sym not in _WASH_EXCLUDED_SYMBOLS
            and asset_type not in _WASH_EXCLUDED_TYPES
        )

    def window_months(sym: str) -> int:
        return _wash_window_months(by_symbol.get(sym, {}))

    buys: dict[str, list[tuple[date, float]]] = {}
    names: dict[str, str] = {}
    for t in transactions:
        sym = str(t.get("symbol") or "").upper()
        if str(t.get("transaction_type") or "").lower() != "buy" or not covered(sym):
            continue
        bought = _parse_date(t.get("transaction_date"))
        if bought is None:
            continue
        buys.setdefault(sym, []).append((bought, float(t.get("quantity") or 0)))
        names.setdefault(sym, t.get("name") or "")

    # One sale can span several FIFO lots; judge the sale on their summed result.
    sales: dict[tuple[str, date], dict] = {}
    for lot in lots:
        sym = str(lot.get("symbol") or "").upper()
        sold = _parse_date(lot.get("sell_date"))
        if not covered(sym) or sold is None:
            continue
        gain = lot.get("gain_loss_eur")
        if gain is None:
            gain = lot.get("gain_loss")
        sale = sales.setdefault(
            (sym, sold),
            {
                "result": 0.0,
                "purchases": set(),
                "name": lot.get("name") or names.get(sym, ""),
            },
        )
        sale["result"] += float(gain or 0)
        purchased = _parse_date(lot.get("purchase_date"))
        if purchased:
            sale["purchases"].add(purchased)

    blocked, no_rebuy = [], []
    for (sym, sold), sale in sorted(sales.items(), key=lambda kv: kv[0][1]):
        if sale["result"] >= 0:
            continue
        window = window_months(sym)
        start, end = _add_months(sold, -window), _add_months(sold, window)
        blocking = sorted(
            (d, q)
            for d, q in buys.get(sym, [])
            if start <= d <= end and d not in sale["purchases"]
        )
        base = {
            "symbol": sym,
            "name": sale["name"],
            "sell_date": sold.isoformat(),
            "loss_eur": round(sale["result"], 2),
        }
        if blocking:
            base["blocking"] = [
                {"date": d.isoformat(), "quantity": q} for d, q in blocking
            ]
            blocked.append(base)
        elif end > today:
            base["until"] = end.isoformat()
            no_rebuy.append(base)
    no_rebuy.sort(key=lambda n: n["until"])

    # A recent buy only matters for a position still held; a symbol can sit in
    # several portfolios, so its P&L is summed across them.
    held: Optional[dict[str, float]] = None
    if holdings is not None:
        held = {}
        for h in holdings:
            sym = str(h.get("symbol") or "").upper()
            held[sym] = held.get(sym, 0.0) + float(h.get("pnl_amount") or 0)

    recent_buys = []
    for sym, rows in buys.items():
        if held is not None and sym not in held:
            continue
        last = max(d for d, _ in rows)
        until = _add_months(last, window_months(sym))
        if last <= today < until:
            item = {
                "symbol": sym,
                "name": names.get(sym, ""),
                "last_buy": last.isoformat(),
                "until": until.isoformat(),
            }
            if held is not None:
                item["at_loss"] = held[sym] < 0
            recent_buys.append(item)
    recent_buys.sort(key=lambda r: r["until"])
    return {"blocked": blocked, "no_rebuy": no_rebuy, "recent_buys": recent_buys}


@mcp.tool()
def wash_sale_check(symbol: Optional[str] = None) -> str:
    """
    Spanish antiaplicación check (art. 33.5 LIRPF — the "2-month rule"): which
    recent sales at a loss are blocked by a repurchase, which loss sales are
    still inside their window (don't rebuy yet, with the date it becomes safe),
    and which positions were bought recently (a sale at a loss now would be
    blocked). Window: 2 months for listed securities, 1 year for unlisted fund
    units; crypto not covered. Run this before advising a loss sale or a rebuy.

    Args:
        symbol: Optional ticker/ISIN to check just one position.
    """
    today = _today()
    try:
        lots: list = []
        for year in (today.year - 1, today.year):
            report = _get("/api/v1/analytics/tax-report", {"year": year})
            lots += report.get("realised_lots") or []
        transactions = _get("/api/v1/transactions/")
        assets = _get("/api/v1/assets/")
        holdings = _get("/api/v1/portfolios/holdings").get("holdings") or []
    except Exception as e:
        return f"Error fetching data for wash-sale check: {e}"

    asset_map = {a["symbol"]: a for a in assets if a.get("symbol")}
    out = _wash_sale_analysis(lots, transactions, asset_map, today, symbol, holdings)

    def label(item: dict) -> str:
        name = item.get("name")
        return (
            f"{item['symbol']} ({name})"
            if name and name != item["symbol"]
            else item["symbol"]
        )

    scope = f" for {symbol.upper()}" if symbol else ""
    lines = [
        f"WASH-SALE CHECK (antiaplicación, art. 33.5 LIRPF){scope} — as of {today.isoformat()}:"
    ]
    if out["blocked"]:
        lines.append(
            "\nLoss sales blocked by a repurchase (loss deferred while those units are held):"
        )
        for b in out["blocked"]:
            bought = ", ".join(
                f"{x['date']} ({x['quantity']:g} units)" for x in b["blocking"]
            )
            lines.append(
                f"  {label(b)}: sold {b['sell_date']}, loss {_fmt_currency(b['loss_eur'])}"
                f" — blocked by buy(s) on {bought}"
            )
    if out["no_rebuy"]:
        lines.append("\nLoss sales still inside the window — don't rebuy yet:")
        for n in out["no_rebuy"]:
            lines.append(
                f"  {label(n)}: sold {n['sell_date']}, loss {_fmt_currency(n['loss_eur'])}"
                f" — buying it again before {n['until']} would block that loss"
            )
    if out["recent_buys"]:
        lines.append(
            "\nHeld positions bought recently — a sale at a loss would be blocked:"
        )
        for r in out["recent_buys"]:
            loss_str = " (currently at a loss)" if r.get("at_loss") else ""
            lines.append(
                f"  {label(r)}{loss_str}: last bought {r['last_buy']}"
                f" — a sale at a loss before {r['until']} is not deductible yet"
            )
    if not any(out.values()):
        lines.append(
            "  Nothing affected: no recent loss sales and no held position bought"
            " inside an antiaplicación window."
        )
    lines.append(
        "\nWindows: 2 months before/after the sale for listed securities; 1 year for"
        ' unlisted fund units (typed mutual_fund, on the "Funds" exchange, or named'
        " as a fund). Crypto, cash and Mintos P2P are excluded. Repurchases are"
        " matched against FIFO lots — a screen, so confirm edge cases before filing."
    )
    return "\n".join(lines)


@mcp.tool()
def bookings(portfolio_id: Optional[int] = None, limit: int = 50) -> str:
    """
    List cash bookings (deposits and withdrawals) across all broker accounts,
    most recent first.

    Args:
        portfolio_id: Optional filter to a single broker/account.
        limit: Maximum number of bookings to return (default 50).
    """
    params: dict = {}
    if portfolio_id:
        params["portfolio_id"] = portfolio_id
    try:
        data = _get("/api/v1/bookings/", params or None)
    except Exception as e:
        return f"Error fetching bookings: {e}"

    if not data:
        return "No bookings found."

    rows = sorted(data, key=lambda b: b.get("date", ""), reverse=True)[:limit]
    total_dep = sum(
        float(b.get("amount", 0)) for b in data if b.get("action") == "Deposit"
    )
    total_wit = sum(
        float(b.get("amount", 0)) for b in data if b.get("action") == "Withdrawal"
    )

    lines = [f"CASH BOOKINGS ({len(data)} total):"]
    for b in rows:
        action = b.get("action", "?")
        sign = "+" if action == "Deposit" else "-"
        lines.append(
            f"  {str(b.get('date', '?'))[:10]}  {action:12s}"
            f"  {sign}{_fmt_currency(float(b.get('amount', 0)), b.get('currency', 'EUR'))}"
            f"  {b.get('portfolio_name') or b.get('broker', '')}"
        )

    lines.append(f"\nTotal deposited:   {_fmt_currency(total_dep)}")
    lines.append(f"Total withdrawn:   {_fmt_currency(total_wit)}")
    lines.append(f"Net cash in:       {_fmt_currency(total_dep - total_wit)}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
