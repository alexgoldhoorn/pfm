"""
Market Data Router — shared, cached Yahoo Finance data (quotes, FX,
fundamentals). Contains NO portfolio data; key-auth like the rest of the API.

All handlers are plain ``def`` (not ``async``): the underlying service may do
blocking yfinance I/O, which must run in FastAPI's threadpool.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from portf_manager import market

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

# Floor for max_age so a misconfigured client can't hammer Yahoo.
_MIN_MAX_AGE = 60
_MAX_BATCH = 50


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/quotes")
def batch_quotes(
    symbols: str = Query(..., description="Comma-separated Yahoo-format symbols"),
    max_age: int = Query(86400, ge=0, description="Max data age in seconds"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Batch quotes; per-symbol errors inside the response, never a batch 500."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No symbols supplied")
    if len(syms) > _MAX_BATCH:
        raise HTTPException(
            status_code=400, detail=f"Too many symbols (max {_MAX_BATCH})"
        )
    return {"quotes": market.get_quotes(db, syms, max_age=max(max_age, _MIN_MAX_AGE))}


@router.get("/quote/{symbol}")
def single_quote(
    symbol: str,
    max_age: int = Query(86400, ge=0),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Quote for one Yahoo-format symbol (e.g. NVDA, ASML.AS, BTC-EUR)."""
    return market.get_quote(db, symbol, max_age=max(max_age, _MIN_MAX_AGE))


@router.get("/fx")
def fx_rates(
    currencies: str = Query(..., description="Comma-separated ISO currency codes"),
    max_age: int = Query(3600, ge=0),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """EUR conversion rates for the given currencies."""
    curs = [c.strip().upper() for c in currencies.split(",") if c.strip()]
    if not curs:
        raise HTTPException(status_code=400, detail="No currencies supplied")
    rates = {}
    for cur in curs:
        rate, stale = market.get_fx_eur(db, cur, max_age=max(max_age, _MIN_MAX_AGE))
        rates[cur] = {"rate": rate, "stale": stale}
    return {"rates": rates}


@router.get("/fundamentals/{symbol}")
def fundamentals(
    symbol: str,
    max_age: int = Query(21600, ge=0),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Key yfinance fundamentals (PE, market cap, dividend yield, 52w range...)."""
    return market.get_fundamentals(db, symbol, max_age=max(max_age, _MIN_MAX_AGE))
