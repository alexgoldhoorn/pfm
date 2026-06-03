"""
Research & Valuation Router

GET    /api/v1/research/{symbol}            — get cached report (or 404)
POST   /api/v1/research/{symbol}/generate   — run LLM analysis, cache result
GET    /api/v1/research/{symbol}/targets    — get price targets
PUT    /api/v1/research/{symbol}/targets    — set price targets
GET    /api/v1/research/alerts/check        — check all targets vs latest prices
"""

import json
import logging
from typing import Optional

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


class PriceTargetBody(BaseModel):
    buy_below: Optional[float] = None
    sell_above: Optional[float] = None
    fair_value: Optional[float] = None
    notes: Optional[str] = None


class ResearchSaveBody(BaseModel):
    thesis: Optional[str] = None
    conviction: Optional[int] = None
    method: Optional[str] = None
    assumptions: Optional[dict] = None
    fair_value: Optional[float] = None
    buy_below: Optional[float] = None
    sell_above: Optional[float] = None
    llm_summary: Optional[str] = None
    sources: Optional[list] = None
    current_price: Optional[float] = None


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _resolve_asset(db, symbol: str) -> dict:
    asset = db.get_asset_by_symbol(symbol.upper())
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset '{symbol}' not found")
    return asset


def _position_stats(db, asset: Optional[dict]) -> dict:
    """Current quantity, average cost and remaining cost basis for a held asset.

    Walks transactions chronologically (the DB returns them DESC, so reverse).
    ``cost_basis`` is the cost of the shares still held; ``realised`` is the
    realised P&L from sells (FIFO-ish proportional cost reduction).
    """
    if not asset:
        return {"quantity": 0.0, "avg_cost": 0.0, "cost_basis": 0.0, "realised": 0.0}
    qty = cost = realised = 0.0
    for tx in reversed(db.get_transactions_by_asset(asset["id"])):
        t = tx["transaction_type"].lower()
        q = float(tx["quantity"])
        total = float(tx["total_amount"])
        if t == "buy":
            qty += q
            cost += total
        elif t == "sell":
            if qty > 0:
                avg = cost / qty
                realised += total - avg * q
                cost *= (qty - q) / qty
            qty -= q
        elif t == "split" and q > 0:
            qty *= q
    return {
        "quantity": qty,
        "avg_cost": cost / qty if qty > 0 else 0.0,
        "cost_basis": cost,
        "realised": realised,
    }


def _cost_evolution(db, asset: Optional[dict]) -> tuple[list, list]:
    """Return (transactions, cost_evolution_series) for the research panel.

    transactions: most-recent-first list of {date, type, quantity, price,
    total, currency}. cost_evolution: chronological points of
    {date, quantity, avg_cost, invested} after each buy/sell/split.
    """
    if not asset:
        return [], []
    rows = db.get_transactions_by_asset(asset["id"])
    txns = [
        {
            "date": str(tx["transaction_date"])[:10],
            "type": tx["transaction_type"].lower(),
            "quantity": float(tx["quantity"]),
            "price": float(tx["price"] or 0),
            "total": float(tx["total_amount"] or 0),
            "currency": tx.get("currency", "EUR"),
        }
        for tx in rows
    ]
    qty = cost = 0.0
    series = []
    for tx in reversed(rows):
        t = tx["transaction_type"].lower()
        q = float(tx["quantity"])
        total = float(tx["total_amount"] or 0)
        if t == "buy":
            qty += q
            cost += total
        elif t == "sell":
            if qty > 0:
                cost *= (qty - q) / qty
            qty -= q
        elif t == "split" and q > 0:
            qty *= q
        series.append(
            {
                "date": str(tx["transaction_date"])[:10],
                "quantity": round(qty, 6),
                "avg_cost": round(cost / qty, 4) if qty > 0 else 0.0,
                "invested": round(cost, 2),
                # the price/type of THIS transaction, so the chart can mark entries
                "tx_type": t,
                "tx_price": float(tx["price"] or 0),
            }
        )
    return txns, series


def _current_price(db, asset: Optional[dict], symbol: str) -> tuple[float, str]:
    """Latest price + currency: stored price for held assets, else live yfinance."""
    if asset:
        pd_ = db.get_latest_price(asset["id"])
        if pd_:
            return float(pd_["price"]), asset.get("currency", "EUR")
    try:
        fi = yf.Ticker(symbol.upper()).fast_info
        return float(fi["last_price"]), (fi.get("currency") or "EUR")
    except Exception:
        return 0.0, (asset.get("currency", "EUR") if asset else "EUR")


@router.get("/compare")
async def compare(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Latest saved research per symbol: price, fair value, upside, conviction."""
    out = []
    for n in db.get_latest_research_notes():
        sym = n["symbol"]
        asset = db.get_asset_by_symbol(sym)
        price, cur = _current_price(db, asset, sym)
        fair = n.get("fair_value")
        upside = round((fair - price) / price * 100, 1) if (fair and price) else None
        out.append(
            {
                "symbol": sym,
                "current_price": round(price, 4),
                "currency": cur,
                "fair_value": fair,
                "buy_below": n.get("buy_below"),
                "sell_above": n.get("sell_above"),
                "conviction": n.get("conviction"),
                "upside_pct": upside,
                "updated_at": n.get("created_at"),
            }
        )
    out.sort(key=lambda x: (x["upside_pct"] is None, -(x["upside_pct"] or 0)))
    return out


@router.get("/{symbol}")
async def get_report(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Return the cached research report for a symbol, or 404 if not yet generated."""
    asset = _resolve_asset(db, symbol)
    report = db.get_research_report(asset["id"])
    if not report:
        raise HTTPException(status_code=404, detail="No report yet — POST to /generate")
    if report.get("report_json"):
        report["details"] = json.loads(report["report_json"])
    return report


@router.post("/{symbol}/generate")
def generate_report(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Run web-augmented LLM valuation. Works for any symbol (held or not)."""
    from portf_manager.services.research import (
        fetch_fundamentals,
        fetch_recent_news,
        generate_valuation_report,
    )

    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)  # may be None (research anything)
    pos = _position_stats(db, asset)
    current_price, currency = _current_price(db, asset, sym)
    fundamentals = fetch_fundamentals(sym, db)
    news = fetch_recent_news(sym)

    result = generate_valuation_report(
        symbol=sym,
        asset_name=asset.get("name", sym) if asset else sym,
        asset_type=asset.get("asset_type", "stock") if asset else "stock",
        current_price=current_price,
        avg_cost=pos["avg_cost"],
        currency=currency,
        fundamentals=fundamentals,
        news=news,
    )

    # Cache the LLM report only for assets we actually hold/track.
    if asset:
        db.upsert_research_report(
            asset_id=asset["id"],
            symbol=sym,
            fair_value=result.get("fair_value"),
            recommendation=result.get("recommendation", "HOLD"),
            confidence=result.get("confidence", "low"),
            summary=result.get("summary", ""),
            report_json=json.dumps(result),
        )

    result["symbol"] = sym
    result["current_price"] = current_price
    result["currency"] = currency
    result["avg_cost"] = round(pos["avg_cost"], 4)
    result["fundamentals"] = fundamentals
    return result


@router.get("/{symbol}/lookup")
def lookup(symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Snapshot for the research workbench (no LLM): price, position,
    fundamentals, news, existing targets and the latest saved research."""
    from portf_manager.services.research import fetch_fundamentals, fetch_recent_news

    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)
    pos = _position_stats(db, asset)
    price, currency = _current_price(db, asset, sym)
    targets = db.get_price_target(asset["id"]) if asset else None
    notes = db.get_research_notes(sym)
    transactions, cost_evolution = _cost_evolution(db, asset)

    # Watchlist status (independent of whether we hold it)
    watch = next(
        (w for w in db.get_watchlist() if (w.get("symbol") or "").upper() == sym), None
    )

    # Position economics
    qty = pos["quantity"]
    cost_basis = pos["cost_basis"]
    market_value = qty * price
    unrealised = market_value - cost_basis if qty > 0 else 0.0
    unrealised_pct = (unrealised / cost_basis * 100) if cost_basis > 0 else None

    return {
        "symbol": sym,
        "name": asset.get("name") if asset else sym,
        "held": bool(asset and qty > 0),
        "on_watchlist": bool(watch),
        "watch_buy_below": watch.get("buy_below") if watch else None,
        "quantity": round(qty, 6),
        "avg_cost": round(pos["avg_cost"], 4),
        "cost_basis": round(cost_basis, 2),
        "market_value": round(market_value, 2),
        "unrealised_gain": round(unrealised, 2),
        "unrealised_pct": (
            round(unrealised_pct, 2) if unrealised_pct is not None else None
        ),
        "realised_gain": round(pos["realised"], 2),
        "current_price": round(price, 4),
        "currency": currency,
        "fundamentals": fetch_fundamentals(sym, db),
        "news": fetch_recent_news(sym),
        "targets": targets,
        "transactions": transactions,
        "cost_evolution": cost_evolution,
        "latest_note": notes[0] if notes else None,
    }


@router.post("/{symbol}/save")
async def save_research(
    symbol: str,
    body: ResearchSaveBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save a versioned research record and (for held assets) push targets so
    price alerts fire."""
    sym = symbol.upper()
    asset = db.get_asset_by_symbol(sym)
    note_id = db.create_research_note(
        asset_id=asset["id"] if asset else None,
        symbol=sym,
        thesis=body.thesis,
        conviction=body.conviction,
        method=body.method,
        assumptions=json.dumps(body.assumptions) if body.assumptions else None,
        fair_value=body.fair_value,
        buy_below=body.buy_below,
        sell_above=body.sell_above,
        price_at_save=body.current_price,
        llm_summary=body.llm_summary,
        sources=json.dumps(body.sources) if body.sources else None,
    )
    if asset and (body.buy_below or body.sell_above or body.fair_value):
        db.upsert_price_target(
            asset_id=asset["id"],
            buy_below=body.buy_below,
            sell_above=body.sell_above,
            fair_value=body.fair_value,
            notes=(body.thesis or "")[:500] or None,
        )
    return {"id": note_id, "symbol": sym, "targets_updated": bool(asset)}


@router.get("/{symbol}/history")
async def history(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Version history of saved research for a symbol."""
    notes = db.get_research_notes(symbol.upper())
    for n in notes:
        if n.get("assumptions"):
            try:
                n["assumptions"] = json.loads(n["assumptions"])
            except (TypeError, ValueError):
                pass
        if n.get("sources"):
            try:
                n["sources"] = json.loads(n["sources"])
            except (TypeError, ValueError):
                pass
    return notes


@router.get("/{symbol}/targets")
async def get_targets(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Return price targets for a symbol."""
    asset = _resolve_asset(db, symbol)
    targets = db.get_price_target(asset["id"])
    return targets or {}


@router.put("/{symbol}/targets")
async def set_targets(
    symbol: str,
    body: PriceTargetBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Create or update price targets for a symbol."""
    asset = _resolve_asset(db, symbol)
    db.upsert_price_target(
        asset_id=asset["id"],
        buy_below=body.buy_below,
        sell_above=body.sell_above,
        fair_value=body.fair_value,
        notes=body.notes,
    )
    return db.get_price_target(asset["id"])


@router.get("/alerts/check")
async def check_alerts(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """
    Compare all price targets against latest stored prices.
    Returns triggered alerts (does NOT send Telegram — use the cron for that).
    """
    alerts = []
    for pt in db.get_all_price_targets():
        asset_id = pt["asset_id"]
        price_data = db.get_latest_price(asset_id)
        if not price_data:
            continue
        price = float(price_data["price"])
        symbol = pt["symbol"]
        triggered = []
        if pt.get("buy_below") and price <= pt["buy_below"]:
            triggered.append(
                {"type": "BUY", "threshold": pt["buy_below"], "price": price}
            )
        if pt.get("sell_above") and price >= pt["sell_above"]:
            triggered.append(
                {"type": "SELL", "threshold": pt["sell_above"], "price": price}
            )
        if triggered:
            alerts.append({"symbol": symbol, "triggers": triggered})
    return {"alerts": alerts, "total": len(alerts)}
