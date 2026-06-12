"""
Public Router — shareable, read-only %-allocation view.

Exposes ONLY percentages and returns — never absolute amounts, quantities, or
costs. Disabled by default; enable with PORTF_PUBLIC_VIEW=true.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from portf_manager import market

from ..dependencies import get_database

router = APIRouter()
logger = logging.getLogger(__name__)


def _fx(db, currency: str) -> float:
    """EUR rate via the shared market-data cache (30-min freshness)."""
    rate, _stale = market.get_fx_eur(db, currency, max_age=1800)
    return rate


def _enabled() -> bool:
    return os.getenv("PORTF_PUBLIC_VIEW", "false").lower() in ("1", "true", "yes")


@router.get("/summary")
def public_summary(db=Depends(get_database)):
    """Return allocation percentages + lifetime return %. No absolute amounts.

    Disabled unless PORTF_PUBLIC_VIEW is truthy.
    """
    if not _enabled():
        raise HTTPException(status_code=404, detail="Public view is disabled")

    # Build open positions (value in EUR only used for ratios — never returned)
    positions: dict = {}
    for tx in db.get_all_transactions():
        aid = tx["asset_id"]
        t = tx["transaction_type"].lower()
        qty = float(tx["quantity"])
        total = float(tx["total_amount"])
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

    by_type: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    total_value = 0.0
    total_cost = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        fx = _fx(db, cur)
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value = pos["quantity"] * price * fx
        if value <= 0:
            continue
        total_value += value
        total_cost += pos["cost"] * fx
        by_type[asset.get("asset_type", "other")] = (
            by_type.get(asset.get("asset_type", "other"), 0) + value
        )
        # Use the asset NAME (not amount) for the holdings breakdown
        by_symbol[asset.get("name") or asset.get("symbol", "?")] = (
            by_symbol.get(asset.get("name") or asset.get("symbol", "?"), 0) + value
        )

    def pct(d):
        return (
            {
                k: round(v / total_value * 100, 1)
                for k, v in sorted(d.items(), key=lambda x: -x[1])
            }
            if total_value
            else {}
        )

    total_return = (
        round((total_value - total_cost) / total_cost * 100, 1) if total_cost else None
    )

    top = list(pct(by_symbol).items())[:10]

    return {
        "allocation_by_type_pct": pct(by_type),
        "top_holdings_pct": [{"name": k, "pct": v} for k, v in top],
        "total_return_pct": total_return,
        "positions": sum(1 for p in positions.values() if p["quantity"] > 0),
        "note": "Percentages only — no absolute values are exposed.",
    }
