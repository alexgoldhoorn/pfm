"""
Portfolios Router for Portfolio Management API

Handles portfolio management and analysis.
"""

import time
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, Field
import logging

import yfinance as yf

from portf_manager.database import Database
from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level FX rate cache — persists across requests, refreshed every 30 min.
# Pre-seeded with typical rates so the first request never calls yfinance live.
_FX_CACHE: dict[str, float] = {
    "USD": 0.858,
    "GBP": 1.153,
    "SEK": 0.092,
    "DKK": 0.134,
    "CHF": 1.062,
    "NOK": 0.086,
    "JPY": 0.0059,
}
_FX_TTL = 1800  # 30 minutes
# Seed timestamps so pre-seeded values are valid immediately (not expired)
_FX_CACHE_TS: dict[str, float] = {k: time.time() for k in _FX_CACHE}


def _get_fx_rate(currency: str) -> float:
    """Return EUR/currency rate with 30-minute in-process cache."""
    if currency == "EUR":
        return 1.0
    now = time.time()
    if currency in _FX_CACHE and now - _FX_CACHE_TS.get(currency, 0) < _FX_TTL:
        return _FX_CACHE[currency]
    try:
        rate = yf.Ticker(f"{currency}EUR=X").fast_info.last_price
        _FX_CACHE[currency] = float(rate) if rate else _FX_CACHE.get(currency, 1.0)
    except Exception:
        logger.warning(f"FX rate {currency}→EUR unavailable, using cached/default")
    _FX_CACHE_TS[currency] = now
    return _FX_CACHE.get(currency, 1.0)


class PortfolioCreate(BaseModel):
    """Schema for creating a portfolio."""

    name: str = Field(..., description="Portfolio name")
    base_currency: str = Field("EUR", description="Base currency")
    entity_id: Optional[int] = Field(None, description="Linked entity/broker ID")
    description: Optional[str] = Field(None, description="Portfolio description")


class PortfolioUpdate(BaseModel):
    """Schema for updating a portfolio."""

    name: Optional[str] = None
    base_currency: Optional[str] = None
    description: Optional[str] = None


class PortfolioResponse(BaseModel):
    """Schema for portfolio response."""

    id: int
    name: str
    base_currency: str
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


async def _get_api_key_auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    """API key authentication dependency."""
    return await require_api_key(api_key_manager)(request)


@router.get("/")
async def list_portfolios(
    database: Database = Depends(get_database),
):
    """Get all portfolios."""
    portfolios = database.get_all_portfolios()
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "base_currency": p.get("base_currency", "EUR"),
            "entity_id": p.get("entity_id"),
            "entity_name": p.get("entity_name"),
            "description": p.get("description"),
            "is_active": p.get("is_active", True),
        }
        for p in portfolios
    ]


@router.get("/values")
async def get_portfolio_values(
    database: Database = Depends(get_database),
):
    """Current EUR value, cost, and P&L per portfolio, plus a grand total.

    Powers the value column + totals footer on the Portfolios page. Detailed
    positions remain on the Holdings page.
    """
    transactions = database.get_all_transactions()

    # Accumulate cost basis + quantity per (portfolio_id, asset_id)
    positions: dict = {}
    for tx in transactions:
        pid = tx.get("portfolio_id")
        aid = tx["asset_id"]
        key = (pid, aid)
        qty = float(tx["quantity"])
        total = float(tx["total_amount"])
        t = tx["transaction_type"].lower()
        if key not in positions:
            positions[key] = {"quantity": 0.0, "cost": 0.0}
        if t == "buy":
            positions[key]["quantity"] += qty
            positions[key]["cost"] += total
        elif t == "sell":
            pos = positions[key]
            if pos["quantity"] > 0:
                pos["cost"] *= (pos["quantity"] - qty) / pos["quantity"]
            pos["quantity"] -= qty

    names = {p["id"]: p["name"] for p in database.get_all_portfolios()}
    by_portfolio: dict = {}
    for (pid, aid), pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = database.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        price_data = database.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value_eur = pos["quantity"] * price * _get_fx_rate(cur)
        cost_eur = pos["cost"] * _get_fx_rate(cur)
        label = names.get(pid, "Unassigned")
        slot = by_portfolio.setdefault(
            label, {"portfolio_id": pid, "value_eur": 0.0, "cost_eur": 0.0}
        )
        slot["value_eur"] += value_eur
        slot["cost_eur"] += cost_eur

    result = []
    total_value = total_cost = 0.0
    for name, d in by_portfolio.items():
        total_value += d["value_eur"]
        total_cost += d["cost_eur"]
        pnl = d["value_eur"] - d["cost_eur"]
        result.append(
            {
                "name": name,
                "portfolio_id": d["portfolio_id"],
                "value_eur": round(d["value_eur"], 2),
                "cost_eur": round(d["cost_eur"], 2),
                "pnl_eur": round(pnl, 2),
                "pnl_pct": (
                    round(pnl / d["cost_eur"] * 100, 1) if d["cost_eur"] else 0.0
                ),
            }
        )
    result.sort(key=lambda x: -x["value_eur"])

    return {
        "portfolios": result,
        "total_value_eur": round(total_value, 2),
        "total_cost_eur": round(total_cost, 2),
        "total_pnl_eur": round(total_value - total_cost, 2),
    }


@router.get("/holdings")
async def get_holdings(
    database: Database = Depends(get_database),
):
    """Get current holdings (positions with total value) computed from transactions."""
    transactions = database.get_all_transactions()

    positions: dict = {}
    for tx in transactions:
        asset_id = tx["asset_id"]
        qty = float(tx["quantity"])
        total = float(tx["total_amount"])
        tx_type = tx["transaction_type"].lower()

        if asset_id not in positions:
            positions[asset_id] = {"quantity": 0.0, "cost_basis": 0.0}

        if tx_type == "buy":
            positions[asset_id]["quantity"] += qty
            positions[asset_id]["cost_basis"] += total
        elif tx_type == "sell":
            pos = positions[asset_id]
            if pos["quantity"] > 0:
                pos["cost_basis"] *= (pos["quantity"] - qty) / pos["quantity"]
            pos["quantity"] -= qty

    def to_eur(amount: float, currency: str) -> float:
        if amount == 0.0:
            return 0.0
        return amount * _get_fx_rate(currency)

    result = []
    grand_total_value_eur = 0.0
    grand_total_cost_eur = 0.0

    for asset_id, data in positions.items():
        qty = data["quantity"]
        if qty <= 0:
            continue

        asset = database.get_asset(asset_id)
        if not asset:
            continue

        currency = asset.get("currency", "EUR")
        price_data = database.get_latest_price(asset_id)
        current_price = float(price_data["price"]) if price_data else 0.0
        cost_basis = data["cost_basis"]
        total_value = qty * current_price
        avg_price = cost_basis / qty if qty > 0 else 0.0
        pnl_amount = total_value - cost_basis
        pnl_pct = (pnl_amount / cost_basis * 100) if cost_basis > 0 else 0.0

        total_value_eur = to_eur(total_value, currency)
        cost_basis_eur = to_eur(cost_basis, currency)

        grand_total_value_eur += total_value_eur
        grand_total_cost_eur += cost_basis_eur

        result.append(
            {
                "asset_id": asset_id,
                "symbol": asset.get("symbol", ""),
                "name": asset.get("name", ""),
                "asset_type": asset.get("asset_type", ""),
                "currency": currency,
                "quantity": qty,
                "avg_price": round(avg_price, 4),
                "cost_basis": round(cost_basis, 2),
                "current_price": current_price,
                "total_value": round(total_value, 2),
                "total_value_eur": round(total_value_eur, 2),
                "pnl_amount": round(pnl_amount, 2),
                "pnl_pct": round(pnl_pct, 2),
            }
        )

    result.sort(key=lambda x: x["total_value_eur"], reverse=True)

    return {
        "holdings": result,
        "summary": {
            "total_value": round(grand_total_value_eur, 2),
            "total_cost": round(grand_total_cost_eur, 2),
            "total_pnl": round(grand_total_value_eur - grand_total_cost_eur, 2),
            "total_pnl_pct": round(
                (
                    (grand_total_value_eur - grand_total_cost_eur)
                    / grand_total_cost_eur
                    * 100
                    if grand_total_cost_eur > 0
                    else 0.0
                ),
                2,
            ),
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    portfolio: PortfolioCreate,
    database: Database = Depends(get_database),
):
    """Create a new portfolio."""
    try:
        portfolio_id = database.create_portfolio(
            name=portfolio.name,
            base_currency=portfolio.base_currency,
            entity_id=portfolio.entity_id,
            description=portfolio.description,
        )
        return {
            "id": portfolio_id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "entity_id": portfolio.entity_id,
            "description": portfolio.description,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create portfolio: {str(e)}",
        )


@router.put("/{portfolio_id}")
async def update_portfolio(
    portfolio_id: int,
    update: PortfolioUpdate,
    request: Request,
    database: Database = Depends(get_database),
    api_key_info: dict = Depends(_get_api_key_auth),
):
    """Update a portfolio."""
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    ok = database.update_portfolio(portfolio_id, **fields)
    if not ok:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"id": portfolio_id, **fields}


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio(
    portfolio_id: int,
    request: Request,
    database: Database = Depends(get_database),
    api_key_info: dict = Depends(_get_api_key_auth),
):
    """Delete a portfolio (soft-delete; its transactions keep their data)."""
    ok = database.delete_portfolio(portfolio_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Portfolio not found")
