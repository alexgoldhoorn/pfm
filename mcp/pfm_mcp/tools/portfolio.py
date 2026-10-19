"""Holdings, portfolios, assets, transactions, quotes, watchlist and bookings."""

from typing import Optional

from pfm_mcp import client, tool
from pfm_mcp.client import FULL_HISTORY_LIMIT, _fmt_currency


@tool
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
        data = client._get("/api/v1/portfolios/holdings", params or None)
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


@tool
def list_portfolios() -> str:
    """
    List all portfolios (broker accounts) tracked in the system, with their
    names, currencies, and IDs.
    """
    try:
        data = client._get("/api/v1/portfolios/")
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


@tool
def list_assets(asset_type: Optional[str] = None) -> str:
    """
    List all tracked assets with their latest known price.

    Args:
        asset_type: Optional filter — 'stock', 'etf', 'crypto', 'p2p', etc.
    """
    try:
        data = client._get("/api/v1/assets/")
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


@tool
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
        asset_symbol: Filter to one symbol exactly as portfolio_holdings shows it,
            e.g. 'NVDA', 'BTC-EUR'. European stocks are stored by ISIN,
            not by exchange ticker such as 'XYZ.DE'.
        date_from: Earliest date to include, YYYY-MM-DD (e.g. '2025-01-01').
        date_to: Latest date to include, YYYY-MM-DD (e.g. '2025-12-31').
        portfolio_id: Filter by portfolio/broker ID.
    """
    # Any client-side filter must see the full history, or it silently filters only
    # the newest rows. Pass an explicit limit: omitting it gets the API's default 100.
    needs_all = (
        order.lower() == "oldest"
        or date_from
        or date_to
        or asset_type
        or transaction_type
        or asset_symbol
    )
    try:
        params: dict = {
            "limit": FULL_HISTORY_LIMIT if needs_all else min(max(limit, 1), 500)
        }
        if portfolio_id:
            params["portfolio_id"] = portfolio_id
        data = client._get("/api/v1/transactions/", params)
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


@tool
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
        data = client._get(
            "/api/v1/market/quotes", {"symbols": symbols, "max_age": max_age}
        )
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


@tool
def watchlist() -> str:
    """
    Show all tickers on the watchlist with current price, buy-below target,
    and distance to the buy zone.
    """
    try:
        data = client._get("/api/v1/watchlist/")
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


@tool
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
        data = client._get("/api/v1/bookings/", params or None)
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
