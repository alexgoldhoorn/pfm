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
from .deposits import _enrich as _enrich_deposit
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


_CF_INCOME_CATS = {"salary", "other_income"}
_CF_ALL_CATS = {"salary", "other_income", "mortgage", "loan", "rest"}


class CashflowBody(BaseModel):
    label: str
    category: str
    amount: float = 0.0
    currency: str = "EUR"
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
    """Brokerage value + manual assets/liabilities + deposits + total net worth (EUR)."""
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

    raw_deposits = db.get_fixed_deposits(status="active")
    deposits_eur = sum(
        float(d["principal"]) * _fx(d.get("currency", "EUR")) for d in raw_deposits
    )

    brokerage = round(_brokerage_value_eur(db), 2)
    net_worth = round(brokerage + assets_eur - liabilities_eur + deposits_eur, 2)
    return {
        "brokerage_eur": brokerage,
        "manual_assets_eur": round(assets_eur, 2),
        "manual_liabilities_eur": round(liabilities_eur, 2),
        "deposits_eur": round(deposits_eur, 2),
        "deposits": [_enrich_deposit(dict(d)) for d in raw_deposits],
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


@router.get("/cashflow")
def get_cashflow(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List monthly cash flow entries with income/expense summary."""
    items = db.get_monthly_cashflow()
    out = []
    income_eur = 0.0
    expenses_eur = 0.0
    by_category = {cat: 0.0 for cat in _CF_ALL_CATS}
    for it in items:
        amt_eur = float(it["amount"] or 0) * _fx(it.get("currency", "EUR"))
        out.append({**it, "amount_eur": round(amt_eur, 2)})
        if it["category"] in _CF_INCOME_CATS:
            income_eur += amt_eur
        else:
            expenses_eur += amt_eur
        if it["category"] in by_category:
            by_category[it["category"]] += amt_eur
    return {
        "items": out,
        "income_eur": round(income_eur, 2),
        "expenses_eur": round(expenses_eur, 2),
        "net_monthly_eur": round(income_eur - expenses_eur, 2),
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
    }


@router.post("/cashflow")
def create_cashflow(
    body: CashflowBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Create a monthly cash flow entry."""
    if body.category not in _CF_ALL_CATS:
        raise HTTPException(
            status_code=422, detail=f"Invalid category '{body.category}'"
        )
    new_id = db.create_monthly_cashflow(
        label=body.label,
        category=body.category,
        amount=body.amount,
        currency=(body.currency or "EUR").upper(),
        notes=body.notes,
    )
    return {"id": new_id}


@router.delete("/cashflow/{entry_id}")
def delete_cashflow(
    entry_id: int,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Delete a monthly cash flow entry."""
    if not db.delete_monthly_cashflow(entry_id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}
