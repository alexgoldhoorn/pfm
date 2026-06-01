"""
Rebalancing Router

GET  /api/v1/rebalance/targets          — list allocation targets
PUT  /api/v1/rebalance/targets          — bulk upsert targets
GET  /api/v1/rebalance/analysis         — current vs target + actions needed
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


class AllocationTarget(BaseModel):
    asset_type: str
    target_pct: float = Field(..., ge=0, le=100)


class RebalanceAnalysis(BaseModel):
    total_value_eur: float
    allocations: List[dict]
    actions: List[dict]
    targets_sum_pct: float


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/targets")
async def get_targets(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Return current allocation targets."""
    return db.get_allocation_targets()


@router.put("/targets")
async def set_targets(
    targets: List[AllocationTarget],
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Bulk-set allocation targets (replaces all existing)."""
    # Clear all first, then set each
    for t in db.get_allocation_targets():
        db.delete_allocation_target(t["asset_type"])
    for t in targets:
        db.set_allocation_target(t.asset_type, t.target_pct)
    return db.get_allocation_targets()


@router.get("/analysis")
async def get_rebalance_analysis(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """
    Compare current holdings allocation vs targets and return
    buy/sell amounts needed to rebalance.
    """
    import yfinance as yf

    # ── 1. Get holdings ──────────────────────────────────────────────────────
    transactions = db.get_all_transactions()
    positions: dict = {}
    for tx in transactions:
        aid = tx["asset_id"]
        qty = float(tx["quantity"])
        total = float(tx["total_amount"])
        t = tx["transaction_type"].lower()
        if aid not in positions:
            positions[aid] = {"quantity": 0.0, "cost": 0.0}
        if t == "buy":
            positions[aid]["quantity"] += qty
            positions[aid]["cost"] += total
        elif t == "sell":
            pos = positions[aid]
            if pos["quantity"] > 0:
                pos["cost"] *= (pos["quantity"] - qty) / pos["quantity"]
            pos["quantity"] -= qty

    # ── 2. Build per-asset-type EUR values ───────────────────────────────────
    _fx: dict[str, float] = {}

    def to_eur(amount: float, currency: str) -> float:
        if currency == "EUR" or amount == 0:
            return amount
        if currency not in _fx:
            try:
                _fx[currency] = float(
                    yf.Ticker(f"{currency}EUR=X").fast_info.last_price or 1.0
                )
            except Exception:
                _fx[currency] = 1.0
        return amount * _fx[currency]

    type_values: dict[str, float] = {}
    for asset_id, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        price_data = db.get_latest_price(asset_id)
        price = float(price_data["price"]) if price_data else 0.0
        value_eur = to_eur(pos["quantity"] * price, asset.get("currency", "EUR"))
        atype = asset.get("asset_type", "other")
        type_values[atype] = type_values.get(atype, 0.0) + value_eur

    total_eur = sum(type_values.values())

    # ── 3. Fetch targets ─────────────────────────────────────────────────────
    targets = {t["asset_type"]: t["target_pct"] for t in db.get_allocation_targets()}
    targets_sum = sum(targets.values())

    # ── 4. Compute drift and actions ─────────────────────────────────────────
    all_types = set(type_values) | set(targets)
    allocations = []
    actions = []

    for atype in sorted(all_types):
        current_val = type_values.get(atype, 0.0)
        current_pct = (current_val / total_eur * 100) if total_eur else 0.0
        target_pct = targets.get(atype, 0.0)
        target_val = (target_pct / 100) * total_eur
        drift_eur = target_val - current_val
        drift_pct = current_pct - target_pct

        allocations.append(
            {
                "asset_type": atype,
                "current_value_eur": round(current_val, 2),
                "current_pct": round(current_pct, 1),
                "target_pct": target_pct,
                "drift_pct": round(drift_pct, 1),
                "drift_eur": round(drift_eur, 2),
            }
        )

        if abs(drift_eur) > 10:  # ignore sub-€10 noise
            actions.append(
                {
                    "asset_type": atype,
                    "action": "BUY" if drift_eur > 0 else "SELL",
                    "amount_eur": round(abs(drift_eur), 2),
                    "reason": f"{abs(drift_pct):.1f}% {'under' if drift_eur > 0 else 'over'} target",
                }
            )

    actions.sort(key=lambda x: -x["amount_eur"])

    return {
        "total_value_eur": round(total_eur, 2),
        "allocations": allocations,
        "actions": actions,
        "targets_sum_pct": round(targets_sum, 1),
    }
