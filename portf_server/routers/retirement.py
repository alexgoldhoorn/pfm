"""Retirement Router — plan settings and retirement income streams."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from portf_manager.services.retirement import load_plan, save_plan

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


class IncomeBody(BaseModel):
    name: str
    owner: Literal["self", "partner"]
    monthly_eur: float
    from_age: int = Field(ge=0, le=120)
    to_age: Optional[int] = None
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
    body: dict,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Merge the given known keys over the stored plan and validate."""
    try:
        return save_plan(db, {**load_plan(db), **body})
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
