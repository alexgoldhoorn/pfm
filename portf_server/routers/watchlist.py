"""
Watchlist Router — track tickers you don't own yet, with buy-price alerts.
"""

import logging
from typing import Optional

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


class WatchlistAdd(BaseModel):
    symbol: str
    name: Optional[str] = None
    asset_type: Optional[str] = None
    buy_below: Optional[float] = None
    notes: Optional[str] = None


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/")
def list_watchlist(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List watchlist entries with current price + distance to buy target."""
    entries = db.get_watchlist()
    for e in entries:
        price = None
        try:
            price = float(yf.Ticker(e["symbol"]).fast_info.last_price)
        except Exception:
            pass
        e["current_price"] = round(price, 2) if price else None
        if price and e.get("buy_below"):
            e["distance_to_buy_pct"] = round(
                (price - e["buy_below"]) / e["buy_below"] * 100, 1
            )
            e["in_buy_zone"] = price <= e["buy_below"]
        else:
            e["distance_to_buy_pct"] = None
            e["in_buy_zone"] = False
    return entries


@router.post("/", status_code=201)
async def add_watchlist(
    body: WatchlistAdd, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Add a symbol to the watchlist (auto-fills name/type from yfinance if omitted)."""
    name, asset_type = body.name, body.asset_type
    if not name:
        try:
            info = yf.Ticker(body.symbol.upper()).info
            name = info.get("shortName") or info.get("longName")
            qt = info.get("quoteType", "").lower()
            asset_type = asset_type or {
                "equity": "stock",
                "etf": "etf",
                "cryptocurrency": "crypto",
            }.get(qt, "stock")
        except Exception:
            pass
    db.add_watchlist(
        symbol=body.symbol.upper(),
        name=name,
        asset_type=asset_type,
        buy_below=body.buy_below,
        notes=body.notes,
    )
    return {"symbol": body.symbol.upper(), "name": name}


@router.delete("/{symbol}")
async def delete_watchlist(
    symbol: str, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Remove a symbol from the watchlist."""
    if not db.delete_watchlist(symbol):
        raise HTTPException(status_code=404, detail="Symbol not on watchlist")
    return {"deleted": symbol.upper()}


@router.get("/alerts/check")
def check_watchlist_alerts(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Return watchlist symbols that have dropped into their buy zone."""
    alerts = []
    for e in db.get_watchlist():
        if not e.get("buy_below"):
            continue
        try:
            price = float(yf.Ticker(e["symbol"]).fast_info.last_price)
        except Exception:
            continue
        if price <= e["buy_below"]:
            alerts.append(
                {
                    "symbol": e["symbol"],
                    "name": e.get("name"),
                    "price": round(price, 2),
                    "buy_below": e["buy_below"],
                }
            )
    return {"alerts": alerts, "total": len(alerts)}
