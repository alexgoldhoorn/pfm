"""Fixed deposits CRUD + maturity action."""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portf_manager.database import Database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _projected_interest(
    principal: float, rate: float, start: str, maturity: str
) -> float:
    d1 = date.fromisoformat(start)
    d2 = date.fromisoformat(maturity)
    days = max((d2 - d1).days, 0)
    return round(principal * (rate / 100) * (days / 365), 2)


def _enrich(dep: dict) -> dict:
    dep["projected_interest"] = _projected_interest(
        dep["principal"], dep["interest_rate"], dep["start_date"], dep["maturity_date"]
    )
    dep["days_remaining"] = max(
        (date.fromisoformat(dep["maturity_date"]) - date.today()).days, 0
    )
    return dep


class DepositBody(BaseModel):
    name: str
    principal: float
    currency: str = "EUR"
    interest_rate: float
    start_date: str
    maturity_date: str
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None


class DepositUpdate(BaseModel):
    name: Optional[str] = None
    principal: Optional[float] = None
    currency: Optional[str] = None
    interest_rate: Optional[float] = None
    start_date: Optional[str] = None
    maturity_date: Optional[str] = None
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class MatureBody(BaseModel):
    interest_paid: float
    date: str


@router.get("/")
def list_deposits(
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    return [_enrich(d) for d in db.get_fixed_deposits()]


@router.post("/")
def create_deposit(
    body: DepositBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    dep_id = db.create_fixed_deposit(
        name=body.name,
        principal=body.principal,
        currency=(body.currency or "EUR").upper(),
        interest_rate=body.interest_rate,
        start_date=body.start_date,
        maturity_date=body.maturity_date,
        portfolio_id=body.portfolio_id,
        notes=body.notes,
    )
    return {"id": dep_id}


@router.put("/{deposit_id}")
def update_deposit(
    deposit_id: int,
    body: DepositUpdate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    fields = body.model_dump(exclude_none=True)
    if "currency" in fields:
        fields["currency"] = fields["currency"].upper()
    if not db.update_fixed_deposit(deposit_id, **fields):
        raise HTTPException(status_code=404, detail="Not found or nothing to update")
    return {"updated": True}


@router.delete("/{deposit_id}")
def delete_deposit(
    deposit_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    if not db.delete_fixed_deposit(deposit_id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


@router.post("/{deposit_id}/mature")
def mature_deposit(
    deposit_id: int,
    body: MatureBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    dep = db.get_fixed_deposit(deposit_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deposit not found")
    if dep["status"] != "active":
        raise HTTPException(status_code=400, detail="Deposit is not active")

    asset = db.get_asset_by_symbol("DEPOSITS")
    if asset:
        asset_id = asset["id"]
    else:
        asset_id = db.create_asset(
            symbol="DEPOSITS",
            name="Fixed Deposits Interest",
            asset_type="cash",
            currency=dep["currency"],
        )
        db.update_asset(asset_id, auto_price=0)

    tx_id = db.create_transaction(
        asset_id=asset_id,
        transaction_type="interest",
        quantity=1.0,
        price=body.interest_paid,
        total_amount=body.interest_paid,
        transaction_date=body.date,
        portfolio_id=dep["portfolio_id"],
        currency=dep["currency"],
        description=f"Interest from {dep['name']}",
    )
    db.update_fixed_deposit(
        deposit_id, status="matured", interest_paid=body.interest_paid
    )
    return {"transaction_id": tx_id, "deposit_id": deposit_id}
