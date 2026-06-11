"""
Public Router — shareable, read-only %-allocation view.

Exposes ONLY percentages and returns — never absolute amounts, quantities, or
costs. Disabled by default; enable with PORTF_PUBLIC_VIEW=true.
"""

import logging
import os
import time

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_database

router = APIRouter()
logger = logging.getLogger(__name__)

_FX = {"USD": 0.92, "GBP": 1.17, "SEK": 0.088, "DKK": 0.134, "CHF": 1.05}
_FX_TS: dict[str, float] = {}


def _fx(currency: str) -> float:
    if currency == "EUR":
        return 1.0
    now = time.time()
    if currency in _FX and now - _FX_TS.get(currency, 0) < 1800:
        return _FX[currency]
    try:
        _FX[currency] = float(
            yf.Ticker(f"{currency}EUR=X").fast_info.last_price or _FX.get(currency, 1.0)
        )
    except Exception:
        pass
    _FX_TS[currency] = now
    return _FX.get(currency, 1.0)


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
        fx = _fx(cur)
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
