"""Allocate Router — deployable cash and allocator settings."""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from portf_manager.services import allocator
from portf_manager.services import deployable as dep

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


class SettingsUpdate(BaseModel):
    """Partial allocator settings; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    buffer_months_target: Optional[float] = None
    buffer_months_min: Optional[float] = None
    buffer_months_max: Optional[float] = None
    iva_pct: Optional[float] = None
    irpf_pct: Optional[float] = None
    irpf_retention_pct: Optional[float] = None
    income_category: Optional[str] = None
    work_income_merchants: Optional[list[str]] = None
    tax_paid_categories: Optional[list[str]] = None
    managed_portfolio_ids: Optional[list[int]] = None
    min_line_eur: Optional[float] = None
    max_lines_per_bucket: Optional[int] = None
    max_single_stock_pct: Optional[float] = None
    traspaso_threshold_pp: Optional[float] = None


def _income_merchants(db, settings: dict) -> list[str]:
    """Distinct merchants of income rows over the last 365 days."""
    start = (date.today() - timedelta(days=365)).isoformat()
    rows = db.list_spending_transactions(start_date=start, is_transfer=False)
    return sorted(
        {
            r["merchant"]
            for r in rows
            if r.get("merchant")
            and r.get("category") == settings["income_category"]
            and float(r["amount"]) > 0
        }
    )


@router.get("/deployable")
def get_deployable(db=Depends(get_database), _: dict = Depends(_auth)):
    """Cash that can be invested now, after tax reserve and buffer."""
    return dep.deployable(db)


@router.get("/settings")
def get_settings(db=Depends(get_database), _: dict = Depends(_auth)):
    """Allocator settings plus the merchants the form can tick."""
    settings = dep.load_settings(db)
    return {**settings, "income_merchants": _income_merchants(db, settings)}


@router.put("/settings")
def put_settings(
    body: SettingsUpdate, db=Depends(get_database), _: dict = Depends(_auth)
):
    """Merge a partial update into the stored settings."""
    try:
        return dep.save_settings(db, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/next-euro")
def get_next_euro(
    amount: float | None = Query(None, ge=0),
    db=Depends(get_database),
    _: dict = Depends(_auth),
):
    """Where the next euro should go, with reasons."""
    return allocator.next_euro(db, amount_eur=amount)


@router.get("/candidates")
def get_candidates(
    bucket: Optional[str] = Query(None),
    db=Depends(get_database),
    _: dict = Depends(_auth),
):
    """Everything the next euro could buy, with cap room and rank reasons."""
    try:
        return allocator.candidates_view(db, bucket)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown bucket: {bucket}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
