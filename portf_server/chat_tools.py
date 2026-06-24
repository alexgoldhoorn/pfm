"""
Portfolio tool catalog for AI chat tool-calling.

Each tool function takes a Database instance plus keyword arguments and
returns a compact JSON string. execute_tool() is the single dispatcher
used by EnhancedChatEngine.
"""

import json
import logging
from datetime import date
from typing import Optional

from portf_manager.database import Database
from portf_manager.llm_client import ToolDefinition

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────


def _j(obj) -> str:
    return json.dumps(obj, default=str)


def _fx(currency: str) -> float:
    try:
        from portf_server.routers.portfolios import _get_fx_rate

        return _get_fx_rate(currency)
    except Exception:
        return 1.0


# ── tool registry ──────────────────────────────────────────────────────────

_REGISTRY: dict[str, callable] = {}


def _tool(name: str):
    def decorator(fn):
        _REGISTRY[name] = fn
        return fn

    return decorator


def execute_tool(name: str, args: dict, db: Database) -> str:
    """Dispatch a tool call and return a compact JSON result string.

    Always returns a string — never raises. Exceptions become "Error: …".
    """
    handler = _REGISTRY.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"
    try:
        return handler(db, **{k: v for k, v in args.items() if v is not None})
    except Exception as e:
        logger.warning("Tool %s failed: %s", name, e)
        return f"Error: {e}"


# ── tool implementations ───────────────────────────────────────────────────


@_tool("get_holdings")
def _get_holdings(
    db: Database,
    portfolio_id: Optional[str] = None,
    symbol: Optional[str] = None,
    asset_type: Optional[str] = None,
) -> str:
    from portf_manager.positions import compute_positions

    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    pos_map, _ = compute_positions(txns)
    results = []
    for aid, p in pos_map.items():
        if p["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        if symbol and asset["symbol"].upper() != symbol.upper():
            continue
        if asset_type and asset.get("asset_type", "") != asset_type:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        fx = _fx(cur)
        value_eur = p["quantity"] * price * fx
        cost_eur = p["cost"] * fx
        gain_eur = value_eur - cost_eur
        gain_pct = (gain_eur / cost_eur * 100) if cost_eur else 0.0
        results.append(
            {
                "symbol": asset["symbol"],
                "name": asset.get("name"),
                "asset_type": asset.get("asset_type"),
                "quantity": round(p["quantity"], 6),
                "avg_cost": round(p["cost"] / p["quantity"], 4) if p["quantity"] else 0,
                "current_price": round(price, 4),
                "currency": cur,
                "value_eur": round(value_eur, 2),
                "cost_eur": round(cost_eur, 2),
                "gain_eur": round(gain_eur, 2),
                "gain_pct": round(gain_pct, 2),
            }
        )
    results.sort(key=lambda x: x["value_eur"], reverse=True)
    return _j({"holdings": results, "count": len(results)})


@_tool("get_kpis")
def _get_kpis(db: Database, portfolio_id: Optional[str] = None) -> str:
    from portf_manager.positions import compute_positions

    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    pos_map, realised = compute_positions(txns)
    total_value = total_cost = 0.0
    for aid, p in pos_map.items():
        if p["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        fx = _fx(cur)
        total_value += p["quantity"] * price * fx
        total_cost += p["cost"] * fx

    net_deposits = 0.0
    for b in db.get_all_bookings(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    ):
        amt = float(b.get("amount") or 0) * _fx(b.get("currency", "EUR"))
        net_deposits += amt if b.get("action") == "Deposit" else -amt

    unrealised = total_value - total_cost
    return _j(
        {
            "total_value_eur": round(total_value, 2),
            "invested_eur": round(total_cost, 2),
            "unrealised_gain_eur": round(unrealised, 2),
            "unrealised_gain_pct": (
                round(unrealised / total_cost * 100, 2) if total_cost else 0
            ),
            "realised_gain_eur": round(realised, 2),
            "net_deposits_eur": round(net_deposits, 2),
        }
    )


@_tool("get_performance")
def _get_performance(
    db: Database,
    portfolio_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    from portf_manager.positions import compute_positions

    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    pos_map, realised = compute_positions(txns)

    invested = current = 0.0
    inception_date = None
    for aid, p in pos_map.items():
        if p["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        fx = _fx(cur)
        invested += p["cost"] * fx
        current += p["quantity"] * price * fx

    all_txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None
    )
    dates = [
        t.get("transaction_date", "") for t in all_txns if t.get("transaction_date")
    ]
    if dates:
        inception_date = min(dates)

    total_return_pct = ((current - invested) / invested * 100) if invested else 0.0
    return _j(
        {
            "total_value_eur": round(current, 2),
            "invested_eur": round(invested, 2),
            "total_return_pct": round(total_return_pct, 2),
            "realised_gain_eur": round(realised, 2),
            "inception_date": str(inception_date)[:10] if inception_date else None,
        }
    )


@_tool("get_risk")
def _get_risk(db: Database, portfolio_id: Optional[str] = None) -> str:
    from portf_server.routers.analytics import get_risk as _risk_fn

    try:
        result = _risk_fn(db=db, api_key_info={})
        return _j(result)
    except Exception as e:
        return _j({"error": str(e)})


@_tool("get_diversification")
def _get_diversification(db: Database, portfolio_id: Optional[str] = None) -> str:
    from portf_server.routers.analytics import get_diversification as _div_fn

    try:
        result = _div_fn(db=db, api_key_info={})
        return _j(result)
    except Exception as e:
        return _j({"error": str(e)})


@_tool("get_health")
def _get_health(db: Database, portfolio_id: Optional[str] = None) -> str:
    cache_key = f"portf:advisor:{portfolio_id}" if portfolio_id else "portf:advisor:all"
    cached = db.cache_get(cache_key)
    if cached:
        return _j(cached)
    return _j(
        {
            "error": "No health analysis cached yet. Run from Portfolio Health page first."
        }
    )


@_tool("get_brokers")
def _get_brokers(db: Database) -> str:
    portfolios = db.get_all_portfolios()
    brokers = []
    for p in portfolios:
        brokers.append(
            {
                "id": str(p["id"]),
                "name": p.get("name"),
                "website": p.get("website"),
                "description": p.get("description"),
                "first_transaction_date": str(
                    p.get("first_transaction_date", "") or ""
                )[:10],
                "last_transaction_date": str(p.get("last_transaction_date", "") or "")[
                    :10
                ],
            }
        )
    return _j({"brokers": brokers, "count": len(brokers)})


@_tool("get_quote")
def _get_quote(db: Database, symbol: str) -> str:
    from portf_manager.market import get_quote as _mkt_quote

    try:
        q = _mkt_quote(db, symbol)
        return _j(q)
    except Exception as e:
        return _j({"symbol": symbol, "error": str(e)})


@_tool("get_price")
def _get_price(
    db: Database,
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    asset = db.get_asset_by_symbol(symbol)
    if not asset:
        return f"Error: symbol '{symbol}' not found in database"
    history = db.get_price_history(
        asset["id"],
        start_date=start_date,
        end_date=end_date,
    )
    prices = [
        {"date": str(p.get("price_date", ""))[:10], "price": float(p.get("price", 0))}
        for p in (history or [])
    ]
    return _j({"symbol": symbol, "prices": prices, "count": len(prices)})


@_tool("get_research")
def _get_research(db: Database, symbol: str) -> str:
    notes = db.get_research_notes(symbol)
    if not notes:
        return _j(
            {"symbol": symbol, "note": None, "message": "No research notes saved."}
        )
    latest = notes[0]
    return _j(
        {
            "symbol": symbol,
            "rating": latest.get("recommendation"),
            "fair_value": latest.get("fair_value"),
            "confidence": latest.get("confidence"),
            "summary": latest.get("analysis_summary"),
            "created_at": str(latest.get("created_at", ""))[:10],
        }
    )


@_tool("get_transactions")
def _get_transactions(
    db: Database,
    portfolio_id: Optional[str] = None,
    symbol: Optional[str] = None,
    tx_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = 20,
) -> str:
    txns = db.get_all_transactions(
        portfolio_id=int(portfolio_id) if portfolio_id else None,
        limit=int(limit) if limit else 20,
    )
    results = []
    for t in txns:
        tx_date = str(t.get("transaction_date", ""))[:10]
        if start_date and tx_date < start_date:
            continue
        if end_date and tx_date > end_date:
            continue
        if symbol and (t.get("symbol") or "").upper() != symbol.upper():
            continue
        if tx_type and (t.get("transaction_type") or "").lower() != tx_type.lower():
            continue
        results.append(
            {
                "date": tx_date,
                "type": t.get("transaction_type"),
                "symbol": t.get("symbol"),
                "quantity": t.get("quantity"),
                "price": t.get("price"),
                "currency": t.get("currency"),
                "portfolio": t.get("portfolio_name"),
            }
        )
    return _j({"transactions": results, "count": len(results)})


@_tool("get_tax_estimate")
def _get_tax_estimate(db: Database, year: Optional[int] = None) -> str:
    from portf_manager.tax_calculator import TaxCalculator
    from portf_manager.services.analytics_service import dividend_income

    yr = int(year) if year else date.today().year
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    calc = TaxCalculator(db)
    realised_gain = 0.0
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for txns in report.values():
            realised_gain += sum(float(getattr(t, "gain_loss", 0) or 0) for t in txns)
    except Exception:
        pass

    all_txns = db.get_all_transactions()
    div = dividend_income(all_txns)
    div_this_year = div["by_year"].get(str(yr), 0.0)

    interest_this_year = 0.0
    for tx in all_txns:
        if (tx.get("transaction_type") or "").lower() != "interest":
            continue
        tx_date = str(tx.get("transaction_date", ""))[:4]
        if tx_date != str(yr):
            continue
        interest_this_year += float(tx.get("price", 0) or 0) * float(
            tx.get("quantity", 1) or 1
        )

    return _j(
        {
            "year": yr,
            "realised_gain_eur": round(realised_gain, 2),
            "dividend_income_eur": round(div_this_year, 2),
            "interest_income_eur": round(interest_this_year, 2),
            "total_savings_base_eur": round(
                realised_gain + div_this_year + interest_this_year, 2
            ),
        }
    )


@_tool("asset_details")
def _asset_details(db: Database, symbol: str) -> str:
    asset = db.get_asset_by_symbol(symbol)
    if not asset:
        return f"Error: symbol '{symbol}' not found"
    return _j(
        {
            "symbol": asset["symbol"],
            "name": asset.get("name"),
            "asset_type": asset.get("asset_type"),
            "isin": asset.get("isin"),
            "ticker": asset.get("ticker"),
            "currency": asset.get("currency"),
            "exchange": asset.get("exchange"),
            "auto_price": asset.get("auto_price"),
        }
    )


@_tool("asset_news")
def _asset_news(db: Database, symbol: str) -> str:
    import yfinance as yf

    ticker_sym = symbol
    asset = db.get_asset_by_symbol(symbol)
    if asset and asset.get("ticker"):
        ticker_sym = asset["ticker"]

    try:
        ticker = yf.Ticker(ticker_sym)
        news = ticker.news or []
        items = [
            {
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "published": n.get("providerPublishTime"),
                "url": n.get("link"),
            }
            for n in news[:10]
        ]
        return _j({"symbol": symbol, "news": items, "count": len(items)})
    except Exception as e:
        return _j({"symbol": symbol, "error": str(e), "news": []})


@_tool("financial_news")
def _financial_news(db: Database, query: str) -> str:
    import yfinance as yf

    try:
        results = yf.Search(query, news_count=10).news or []
        items = [
            {
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "published": n.get("providerPublishTime"),
                "url": n.get("link"),
            }
            for n in results[:10]
        ]
        return _j({"query": query, "news": items, "count": len(items)})
    except Exception as e:
        return _j({"query": query, "error": str(e), "news": []})


# ── public catalog ─────────────────────────────────────────────────────────

TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_holdings",
        description="Return current open positions with quantity, value, cost basis, and unrealised gain.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter to a specific broker/portfolio by its numeric ID.",
                "required": False,
            },
            {
                "name": "symbol",
                "type": "string",
                "description": "Filter to a single asset by ticker symbol (e.g. AAPL, BTC-EUR).",
                "required": False,
            },
            {
                "name": "asset_type",
                "type": "string",
                "description": "Filter by type: stock, etf, fund, crypto, commodity, bond, index.",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_kpis",
        description="Return portfolio key performance indicators: total value, invested, unrealised/realised gain, net deposits.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter to a specific broker/portfolio.",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_performance",
        description="Return portfolio performance metrics: IRR, total return percentage, inception date.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter to a specific broker/portfolio.",
                "required": False,
            },
            {
                "name": "start_date",
                "type": "string",
                "description": "Period start date (YYYY-MM-DD).",
                "required": False,
            },
            {
                "name": "end_date",
                "type": "string",
                "description": "Period end date (YYYY-MM-DD).",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_risk",
        description="Return portfolio risk metrics: volatility, Sharpe ratio, Sortino ratio, max drawdown, beta.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter to a specific broker/portfolio.",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_diversification",
        description="Return portfolio diversification breakdown by asset type, sector, country, and currency, with Herfindahl concentration index.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter to a specific broker/portfolio.",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_health",
        description="Return the cached AI portfolio health analysis: scores and recommendations for diversification, risk, income, fees, and tax efficiency.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter to a specific broker/portfolio.",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_brokers",
        description="List all portfolios/brokers with their name, website, and activity dates.",
        parameters=[],
    ),
    ToolDefinition(
        name="get_quote",
        description="Get a live market quote for an asset: current price, previous close, and day change percentage.",
        parameters=[
            {
                "name": "symbol",
                "type": "string",
                "description": "Ticker symbol (e.g. AAPL, BTC-EUR, ^GSPC).",
                "required": True,
            },
        ],
    ),
    ToolDefinition(
        name="get_price",
        description="Return historical stored prices for an asset from the database.",
        parameters=[
            {
                "name": "symbol",
                "type": "string",
                "description": "Ticker symbol.",
                "required": True,
            },
            {
                "name": "start_date",
                "type": "string",
                "description": "Start date (YYYY-MM-DD).",
                "required": False,
            },
            {
                "name": "end_date",
                "type": "string",
                "description": "End date (YYYY-MM-DD).",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_research",
        description="Return the latest saved research note for an asset: rating (BUY/HOLD/SELL), fair value, confidence, and summary.",
        parameters=[
            {
                "name": "symbol",
                "type": "string",
                "description": "Ticker symbol.",
                "required": True,
            },
        ],
    ),
    ToolDefinition(
        name="get_transactions",
        description="Return a filtered list of transactions.",
        parameters=[
            {
                "name": "portfolio_id",
                "type": "string",
                "description": "Filter by broker/portfolio ID.",
                "required": False,
            },
            {
                "name": "symbol",
                "type": "string",
                "description": "Filter by asset ticker.",
                "required": False,
            },
            {
                "name": "tx_type",
                "type": "string",
                "description": "Filter by type: buy, sell, dividend, interest.",
                "required": False,
            },
            {
                "name": "start_date",
                "type": "string",
                "description": "Earliest date (YYYY-MM-DD).",
                "required": False,
            },
            {
                "name": "end_date",
                "type": "string",
                "description": "Latest date (YYYY-MM-DD).",
                "required": False,
            },
            {
                "name": "limit",
                "type": "integer",
                "description": "Maximum results (default 20).",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="get_tax_estimate",
        description="Return a Spanish IRPF tax estimate for a given year: realised gains, dividend income, and interest income.",
        parameters=[
            {
                "name": "year",
                "type": "integer",
                "description": "Tax year (default current year).",
                "required": False,
            },
        ],
    ),
    ToolDefinition(
        name="asset_details",
        description="Return stored metadata for an asset: name, type, ISIN, ticker, currency, exchange.",
        parameters=[
            {
                "name": "symbol",
                "type": "string",
                "description": "Ticker symbol.",
                "required": True,
            },
        ],
    ),
    ToolDefinition(
        name="asset_news",
        description="Return recent news articles for a specific asset via yfinance.",
        parameters=[
            {
                "name": "symbol",
                "type": "string",
                "description": "Ticker symbol.",
                "required": True,
            },
        ],
    ),
    ToolDefinition(
        name="financial_news",
        description="Search for recent financial or market news articles matching a query.",
        parameters=[
            {
                "name": "query",
                "type": "string",
                "description": "Search query, e.g. 'US Federal Reserve interest rates' or 'NVIDIA earnings'.",
                "required": True,
            },
        ],
    ),
]
