"""What-if scenarios on the household's own numbers.

Thin HTTP wrappers over ``portf_manager.services.life_scenarios``; the AI
chat's scenario tools call the same service functions. Every endpoint is a
read-only GET whose inputs are query parameters, and a plain ``def`` because
the FX and balance-sheet lookups block.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from portf_manager.database import Database
from portf_manager.services import life_scenarios

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()

_EXCLUDE = Field(
    default=None,
    max_length=500,
    description="Comma-separated spending categories to leave out (with their "
    "sub-categories), e.g. a one-off renovation.",
)


def split_categories(value: Optional[str]) -> Optional[list[str]]:
    """``"A, B"`` -> ``["A", "B"]``; None or blank -> None."""
    names = [part.strip() for part in (value or "").split(",") if part.strip()]
    return names or None


class JobLossRequest(BaseModel):
    exclude_categories: Optional[str] = _EXCLUDE
    income_lost_monthly_eur: Optional[float] = Field(default=None, ge=0)
    benefit_monthly_eur: float = Field(default=0.0, ge=0)
    benefit_months: int = Field(default=0, ge=0, le=60)
    severance_eur: float = Field(default=0.0, ge=0)
    flexible_cut_pct: float = Field(
        default=life_scenarios.DEFAULT_FLEXIBLE_CUT_PCT, ge=0, le=100
    )
    market_drop_pct: float = Field(default=0.0, ge=0, lt=100)


class IncomeChangeRequest(BaseModel):
    exclude_categories: Optional[str] = _EXCLUDE
    change_pct: Optional[float] = Field(default=None, ge=-100, le=1000)
    new_income_monthly_eur: Optional[float] = Field(default=None, ge=0)
    flexible_cut_pct: float = Field(
        default=life_scenarios.DEFAULT_FLEXIBLE_CUT_PCT, ge=0, le=100
    )


class HousePurchaseRequest(BaseModel):
    exclude_categories: Optional[str] = _EXCLUDE
    price_eur: float = Field(gt=0)
    down_payment_pct: float = Field(default=20.0, ge=0, le=100)
    rate_pct: float = Field(default=3.0, ge=0, le=50)
    term_years: int = Field(default=30, ge=1, le=50)
    purchase_costs_pct: float = Field(
        default=life_scenarios.DEFAULT_PURCHASE_COSTS_PCT, ge=0, le=30
    )
    monthly_running_eur: float = Field(default=0.0, ge=0)
    sale_proceeds_eur: float = Field(default=0.0, ge=0)
    existing_loans_repaid: bool = False
    rent_saved_monthly_eur: float = Field(default=0.0, ge=0)
    buffer_months: int = Field(
        default=life_scenarios.DEFAULT_BUFFER_MONTHS, ge=0, le=60
    )


class MarketCrashRequest(BaseModel):
    drop_pct: Optional[float] = Field(default=None, gt=0, lt=100)
    scenario: Optional[str] = None
    expected_return_pct: float = Field(default=6.0, ge=-20, le=30)
    monthly_contribution_eur: Optional[float] = Field(default=None, ge=0)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _run(fn, db: Database, body: BaseModel) -> dict:
    args = body.model_dump()
    if "exclude_categories" in args:
        args["exclude_categories"] = split_categories(args["exclude_categories"])
    try:
        return fn(db, **args)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/baseline")
def get_baseline(
    exclude_categories: Optional[str] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """The numbers every scenario starts from: income, spending by tier, money."""
    return life_scenarios.household_baseline(
        db, exclude_categories=split_categories(exclude_categories)
    )


@router.get("/job-loss")
def get_job_loss(
    body: JobLossRequest = Depends(),
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Runway after losing income, and what to cut first."""
    return _run(life_scenarios.run_job_loss, db, body)


@router.get("/income-change")
def get_income_change(
    body: IncomeChangeRequest = Depends(),
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """The monthly picture after income changes."""
    return _run(life_scenarios.run_income_change, db, body)


@router.get("/house-purchase")
def get_house_purchase(
    body: HousePurchaseRequest = Depends(),
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Cash needed, funding, mortgage and the new month for a home purchase."""
    return _run(life_scenarios.run_house_purchase, db, body)


@router.get("/market-crash")
def get_market_crash(
    body: MarketCrashRequest = Depends(),
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """A crash on the held portfolio and the time to get back to today's value."""
    return _run(life_scenarios.run_market_crash, db, body)
