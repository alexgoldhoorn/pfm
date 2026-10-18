"""Retirement Router — plan settings and retirement income streams."""

from datetime import date
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from portf_manager.services.retirement import load_plan, readiness, save_plan

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


_Age = Annotated[int, Field(strict=True, ge=0, le=120)]
_Pct = Annotated[float, Field(strict=True, ge=-50, le=50)]
_DATE_KEYS = {"birth_date", "partner_birth_date"}


class PlanUpdate(BaseModel):
    """Partial plan update; unknown keys are ignored, null clears a date only."""

    model_config = ConfigDict(extra="ignore")

    retirement_age: Optional[_Age] = None
    statutory_age: Optional[_Age] = None
    horizon_age: Optional[_Age] = None
    real_return_pct: Optional[_Pct] = None
    pessimistic_real_return_pct: Optional[_Pct] = None
    cash_real_return_pct: Optional[_Pct] = None
    withdrawal_tax_pct: Optional[_Pct] = None
    spending_months: Optional[Annotated[int, Field(strict=True, ge=1)]] = None
    spending_includes_loan_payments: Optional[bool] = None
    excluded_categories: Optional[list[str]] = None
    birth_date: Optional[date] = None
    partner_birth_date: Optional[date] = None

    @model_validator(mode="after")
    def _no_null_except_dates(self):
        for key in self.model_fields_set - _DATE_KEYS:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self

    def changes(self) -> dict:
        """The explicitly sent fields, dates as ISO strings."""
        out = {}
        for key in self.model_fields_set:
            value = getattr(self, key)
            out[key] = value.isoformat() if isinstance(value, date) else value
        return out


class IncomeBody(BaseModel):
    name: str
    owner: Literal["self", "partner"]
    monthly_eur: float = Field(ge=0)
    from_age: int = Field(ge=0, le=120)
    to_age: Optional[int] = Field(default=None, le=120)
    indexed: bool = True
    source: Optional[str] = None
    as_of: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _check_ages(self):
        if self.to_age is not None and self.to_age <= self.from_age:
            raise ValueError("to_age must be greater than from_age")
        return self


def _income_row(row: dict) -> dict:
    return {**row, "indexed": bool(row["indexed"])}


@router.get("/plan")
def get_plan(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """The retirement plan settings merged over the defaults."""
    return load_plan(db)


@router.put("/plan")
def put_plan(
    body: PlanUpdate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Merge the given known keys over the stored plan and validate."""
    try:
        return save_plan(db, {**load_plan(db), **body.changes()})
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/incomes")
def list_incomes(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """All retirement income streams."""
    return [_income_row(r) for r in db.get_retirement_incomes()]


@router.post("/incomes")
def create_income(
    body: IncomeBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Create a retirement income stream."""
    fields = body.model_dump()
    fields["indexed"] = 1 if body.indexed else 0
    return {"id": db.create_retirement_income(**fields)}


@router.put("/incomes/{income_id}")
def update_income(
    income_id: int,
    body: IncomeBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Replace an income stream's fields."""
    if not db.update_retirement_income(income_id, **body.model_dump()):
        raise HTTPException(status_code=404, detail="Income not found")
    return {"ok": True}


@router.delete("/incomes/{income_id}")
def delete_income(
    income_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete an income stream."""
    if not db.delete_retirement_income(income_id):
        raise HTTPException(status_code=404, detail="Income not found")
    return {"ok": True}


@router.get("/readiness")
def get_readiness(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Retirement readiness: four scenarios, inputs and warnings."""
    return readiness(db)
