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


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _resolve_asset(db, symbol: str) -> dict:
    asset = db.get_asset_by_symbol(symbol.upper())
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset '{symbol}' not found")
    return asset


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
async def generate_report(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Run LLM valuation analysis and cache the result. Takes ~10 seconds."""
    from portf_manager.services.research import (
        fetch_fundamentals,
        generate_valuation_report,
    )

    asset = _resolve_asset(db, symbol)

    # Calculate current position stats
    transactions = db.get_transactions_by_asset(asset["id"])
    qty = cost = 0.0
    for tx in transactions:
        t = tx["transaction_type"].lower()
        q = float(tx["quantity"])
        total = float(tx["total_amount"])
        if t == "buy":
            qty += q
            cost += total
        elif t == "sell":
            if qty > 0:
                cost *= (qty - q) / qty
            qty -= q
    avg_cost = cost / qty if qty > 0 else 0.0

    price_data = db.get_latest_price(asset["id"])
    current_price = float(price_data["price"]) if price_data else 0.0
    currency = asset.get("currency", "EUR")

    # Fetch fundamentals from yfinance
    fundamentals = fetch_fundamentals(symbol.upper())

    # Call LLM
    result = generate_valuation_report(
        symbol=symbol.upper(),
        asset_name=asset.get("name", symbol),
        asset_type=asset.get("asset_type", "stock"),
        current_price=current_price,
        avg_cost=avg_cost,
        currency=currency,
        fundamentals=fundamentals,
    )

    # Persist
    db.upsert_research_report(
        asset_id=asset["id"],
        symbol=symbol.upper(),
        fair_value=result.get("fair_value"),
        recommendation=result.get("recommendation", "HOLD"),
        confidence=result.get("confidence", "low"),
        summary=result.get("summary", ""),
        report_json=json.dumps(result),
    )

    # Auto-save price targets from report if not already set
    existing = db.get_price_target(asset["id"])
    if not existing and (result.get("buy_below") or result.get("sell_above")):
        db.upsert_price_target(
            asset_id=asset["id"],
            buy_below=result.get("buy_below"),
            sell_above=result.get("sell_above"),
            fair_value=result.get("fair_value"),
        )

    result["symbol"] = symbol.upper()
    result["current_price"] = current_price
    result["avg_cost"] = round(avg_cost, 4)
    return result


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
