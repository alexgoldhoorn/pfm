"""
Net Worth Router — manual assets & liabilities + total net worth.

Combines the brokerage value (from tracked positions, EUR) with off-brokerage
items the user enters manually (cash, property, pension, mortgage, loans …) so
net worth reflects the whole picture, not just investments.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portf_manager.positions import compute_positions

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database
from .portfolios import _get_fx_rate as _fx

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


class ManualAssetBody(BaseModel):
    name: str
    category: str = "other"
    amount: float = 0.0
    currency: str = "EUR"
    is_liability: bool = False
    notes: Optional[str] = None


class ManualAssetUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    is_liability: Optional[bool] = None
    notes: Optional[str] = None


def _brokerage_value_eur(db) -> float:
    """EUR value of currently-held tracked positions."""
    positions, _ = compute_positions(db.get_all_transactions())
    total = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        total += pos["quantity"] * price * _fx(asset.get("currency", "EUR"))
    return total


@router.get("/")
def get_networth(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Brokerage value + manual assets/liabilities + total net worth (EUR)."""
    items = db.get_manual_assets()
    assets_eur = 0.0
    liabilities_eur = 0.0
    out = []
    for it in items:
        amt_eur = float(it["amount"] or 0) * _fx(it.get("currency", "EUR"))
        if it["is_liability"]:
            liabilities_eur += amt_eur
        else:
            assets_eur += amt_eur
        out.append({**it, "amount_eur": round(amt_eur, 2)})

    brokerage = round(_brokerage_value_eur(db), 2)
    net_worth = round(brokerage + assets_eur - liabilities_eur, 2)
    return {
        "brokerage_eur": brokerage,
        "manual_assets_eur": round(assets_eur, 2),
        "manual_liabilities_eur": round(liabilities_eur, 2),
        "net_worth_eur": net_worth,
        "items": out,
    }


@router.post("/")
def create_manual_asset(
    body: ManualAssetBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    new_id = db.create_manual_asset(
        name=body.name,
        category=body.category,
        amount=body.amount,
        currency=(body.currency or "EUR").upper(),
        is_liability=body.is_liability,
        notes=body.notes,
    )
    return {"id": new_id}


@router.put("/{asset_id}")
def update_manual_asset(
    asset_id: int,
    body: ManualAssetUpdate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    fields = body.model_dump(exclude_none=True)
    if "currency" in fields:
        fields["currency"] = fields["currency"].upper()
    if not db.update_manual_asset(asset_id, **fields):
        raise HTTPException(status_code=404, detail="Not found or nothing to update")
    return {"updated": True}


@router.delete("/{asset_id}")
def delete_manual_asset(
    asset_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    if not db.delete_manual_asset(asset_id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}
