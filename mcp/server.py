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
def portfolio_holdings(portfolio_id: Optional[int] = None) -> str:
    """
    Get current portfolio holdings: positions, quantities, cost basis, current
    value, and P&L for each asset. Includes an overall portfolio summary.

    Args:
        portfolio_id: Optional portfolio ID to filter by a specific broker/account.
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

    lines = ["HOLDINGS:"]
    for h in holdings:
        pnl_sign = "+" if h["pnl_amount"] >= 0 else ""
        lines.append(
            f"  {h['symbol']:12s} [{h['asset_type']}]  {h['quantity']:>12.4f} units"
            f"  avg {_fmt_currency(h['avg_price'], h['currency'])}"
            f"  now {_fmt_currency(h['current_price'], h['currency'])}"
            f"  value {_fmt_currency(h['total_value'], h['currency'])}"
            f"  P&L {pnl_sign}{_fmt_currency(h['pnl_amount'], h['currency'])} ({pnl_sign}{h['pnl_pct']:.1f}%)"
        )

    lines.append("")
    pnl_sign = "+" if summary.get("total_pnl", 0) >= 0 else ""
    lines.append(f"TOTAL VALUE:  {_fmt_currency(summary.get('total_value', 0))}")
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
    portfolio_id: Optional[int] = None,
    asset_symbol: Optional[str] = None,
) -> str:
    """
    List recent transactions (buys, sells, dividends, interest).

    Args:
        limit: Number of transactions to return (default 20, max 200).
        portfolio_id: Optional filter by portfolio/broker ID.
        asset_symbol: Optional filter by asset symbol (e.g. 'VWCE', 'BTC').
    """
    limit = min(max(limit, 1), 200)
    try:
        params: dict = {"limit": limit}
        if portfolio_id:
            params["portfolio_id"] = portfolio_id
        data = _get("/api/v1/transactions/", params)
    except Exception as e:
        return f"Error fetching transactions: {e}"

    if asset_symbol:
        sym = asset_symbol.upper()
        data = [
            t
            for t in data
            if t.get("symbol", "").upper() == sym
            or t.get("asset_symbol", "").upper() == sym
        ]

    if not data:
        return "No transactions found."

    lines = [f"TRANSACTIONS (last {len(data)}):"]
    for t in data:
        tx_type = t.get("transaction_type", t.get("type", "?")).upper()
        symbol = t.get("symbol", t.get("asset_symbol", "?"))
        date = str(t.get("transaction_date", t.get("date", "?")))[:10]
        qty = float(t.get("quantity", 0))
        price = float(t.get("price", 0))
        total = float(t.get("total_amount", qty * price))
        currency = t.get("currency", "EUR")
        lines.append(
            f"  {date}  {tx_type:8s}  {symbol:12s}"
            f"  {qty:>12.4f} @ {_fmt_currency(price, currency)}"
            f"  = {_fmt_currency(total, currency)}"
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


if __name__ == "__main__":
    mcp.run()
