"""
Watchlist Router — track tickers you don't own yet, with buy-price alerts.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portf_manager import market

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

_PRICE_CACHE_TTL = 600  # seconds — 10 minutes


def _fetch_price_cached(db, symbol: str) -> Tuple[Optional[float], Optional[str]]:
    """Return (price, fetched_at_iso) via the shared market-data cache."""
    q = market.get_quote(db, symbol, max_age=_PRICE_CACHE_TTL)
    if not q.get("price"):
        return None, None
    fetched_at = datetime.fromtimestamp(q["fetched_at"], tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return float(q["price"]), fetched_at


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
        price, fetched_at = _fetch_price_cached(db, e["symbol"])
        e["current_price"] = round(price, 2) if price else None
        e["price_fetched_at"] = fetched_at
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
def add_watchlist(
    body: WatchlistAdd, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Add a symbol to the watchlist (auto-fills name/type from yfinance if omitted).

    Sync (plain ``def``) so FastAPI runs it in the threadpool: the yfinance
    ``.info`` lookup is blocking and would stall the event loop in an async
    handler.
    """
    name, asset_type = body.name, body.asset_type
    if not name:
        try:
            fund = market.get_fundamentals(db, body.symbol.upper())
            name = fund.get("shortName") or fund.get("longName")
            qt = (fund.get("quoteType") or "").lower()
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
        price, fetched_at = _fetch_price_cached(db, e["symbol"])
        if price is None:
            continue
        if price <= e["buy_below"]:
            alerts.append(
                {
                    "symbol": e["symbol"],
                    "name": e.get("name"),
                    "price": round(price, 2),
                    "buy_below": e["buy_below"],
                    "price_fetched_at": fetched_at,
                }
            )
    return {"alerts": alerts, "total": len(alerts)}
