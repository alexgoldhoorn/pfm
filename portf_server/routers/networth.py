"""
Net Worth Router — manual assets & liabilities + total net worth.

Combines the brokerage value (from tracked positions, EUR) with off-brokerage
items the user enters manually (cash, property, pension, mortgage, loans …) so
net worth reflects the whole picture, not just investments.
"""

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from portf_manager.services import loans as loan_calc
from portf_manager.services.balance_sheet import build_balance_sheet
from portf_manager.services.indexa import refresh_account_valuations

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database
from ..settings import get_settings
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
    as_of: Optional[date] = None


class ManualAssetUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    is_liability: Optional[bool] = None
    notes: Optional[str] = None
    as_of: Optional[date] = None


_CF_INCOME_CATS = {"salary", "other_income"}
_CF_ALL_CATS = {"salary", "other_income", "mortgage", "loan", "rest"}


class CashflowBody(BaseModel):
    label: str
    category: str
    amount: float = 0.0
    currency: str = "EUR"
    notes: Optional[str] = None


def _bank_accounts_eur(db) -> tuple:
    """EUR total + per-account detail for bank-type portfolios.

    Each account's balance is derived from the most recent imported
    spending-statement row that has one (see
    `Database.get_latest_bank_balance`) — mirroring how brokerage value is
    derived from transactions rather than manually re-entered. An account
    with no balance-bearing import yet is excluded from the EUR total (not
    silently counted as zero) and reported with `balance: None` so the
    frontend's setup checklist can flag it instead.

    Returns:
        (total_eur, accounts) where accounts is a list of dicts with
        portfolio_id, name, balance, currency, balance_eur, as_of — the
        latter four are None for an account with no balance data yet.
    """
    bank_portfolios = [
        p for p in db.get_all_portfolios() if p.get("account_type") == "bank"
    ]
    total_eur = 0.0
    accounts = []
    for p in bank_portfolios:
        latest = db.get_latest_bank_balance(p["id"])
        if latest is None:
            accounts.append(
                {
                    "portfolio_id": p["id"],
                    "name": p["name"],
                    "balance": None,
                    "currency": None,
                    "balance_eur": None,
                    "as_of": None,
                }
            )
            continue
        amt_eur = float(latest["balance"]) * _fx(latest.get("currency", "EUR"))
        total_eur += amt_eur
        accounts.append(
            {
                "portfolio_id": p["id"],
                "name": p["name"],
                "balance": latest["balance"],
                "currency": latest.get("currency", "EUR"),
                "balance_eur": round(amt_eur, 2),
                "as_of": latest["date"],
            }
        )
    return total_eur, accounts


def net_worth_eur(db) -> float:
    """Total net worth in EUR, taken from the balance sheet.

    Shared with Goals projections (see goals.py) and Action Items so the
    figures never drift apart.
    """
    return build_balance_sheet(db)["totals"]["net_worth_eur"]


class LoanBody(BaseModel):
    name: str
    principal: float = Field(gt=0)
    annual_rate_pct: float = Field(ge=0)
    start_date: date
    term_months: int = Field(gt=0)
    currency: str = "EUR"
    lender: Optional[str] = None
    rate_as_of: Optional[str] = None
    balance_as_of: Optional[float] = None
    extra_payment_eur: float = 0.0
    notes: Optional[str] = None

    def fields(self) -> dict:
        return {**self.model_dump(), "start_date": self.start_date.isoformat()}


class ValuationBody(BaseModel):
    portfolio_id: int
    provider: str
    account_ref: str


def _loan_row(loan: dict) -> dict:
    return {
        **loan,
        "balance_eur": loan_calc.balance_on(loan, date.today()),
        "monthly_payment_eur": loan_calc.payment_of(loan),
        "end_date": loan_calc.end_date(loan).isoformat(),
    }


def _get_loan_or_404(db, loan_id: int) -> dict:
    loan = db.get_loan(loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    return loan


@router.get("/loans")
def list_loans(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """All loans with today's balance, monthly payment and end date."""
    return [_loan_row(loan) for loan in db.get_loans()]


@router.post("/loans")
def create_loan(
    body: LoanBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Create a loan."""
    return {"id": db.create_loan(**body.fields())}


@router.put("/loans/{loan_id}")
def update_loan(
    loan_id: int,
    body: LoanBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Replace a loan's fields."""
    _get_loan_or_404(db, loan_id)
    db.update_loan(loan_id, **body.fields())
    return {"ok": True}


@router.delete("/loans/{loan_id}")
def delete_loan(
    loan_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a loan."""
    if not db.delete_loan(loan_id):
        raise HTTPException(status_code=404, detail="Loan not found")
    return {"ok": True}


@router.get("/loans/{loan_id}/schedule")
def loan_schedule(
    loan_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Yearly amortisation rows of one loan."""
    return loan_calc.schedule(_get_loan_or_404(db, loan_id))


@router.get("/valuations")
def list_valuations(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Provider-reported account valuations."""
    return db.get_account_valuations()


@router.post("/valuations")
def create_valuation(
    body: ValuationBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Link a brokerage portfolio to a provider account and refresh once."""
    portfolio = db.get_portfolio(body.portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.get("account_type", "brokerage") != "brokerage":
        raise HTTPException(
            status_code=422, detail="Valuations apply to brokerage portfolios only"
        )
    db.upsert_account_valuation(
        body.portfolio_id,
        body.provider,
        body.account_ref,
        0.0,
        0.0,
        "EUR",
        "1970-01-01",
        datetime.now().isoformat(timespec="seconds"),
    )
    token = get_settings().indexa_token
    refreshed = False
    if token and body.provider == "indexa":
        results = refresh_account_valuations(
            db, token, only_portfolio=body.portfolio_id
        )
        refreshed = bool(results and results[0]["ok"])
    return {**db.get_account_valuation(body.portfolio_id), "refreshed": refreshed}


@router.post("/valuations/refresh")
def refresh_valuations(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Refresh every provider valuation now."""
    return refresh_account_valuations(db, get_settings().indexa_token)


@router.delete("/valuations/{portfolio_id}")
def delete_valuation(
    portfolio_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Unlink a portfolio from its provider account."""
    if not db.delete_account_valuation(portfolio_id):
        raise HTTPException(status_code=404, detail="Valuation not found")
    return {"ok": True}


@router.get("/balance-sheet")
def get_balance_sheet(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every asset and liability as a sourced, dated line, with totals."""
    return build_balance_sheet(db)


@router.get("/")
def get_networth(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Brokerage value + manual assets/liabilities + deposits + total net worth (EUR)."""
    sheet = build_balance_sheet(db)
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

    # Investments minus manual investment lines (pensions, external accounts).
    investments = next(g for g in sheet["groups"] if g["key"] == "investments")
    brokerage = round(
        sum(
            ln["amount_eur"] or 0.0
            for ln in investments["lines"]
            if ln["source"] != "manual"
        ),
        2,
    )
    bank_eur, bank_accounts = _bank_accounts_eur(db)
    return {
        "brokerage_eur": brokerage,
        "bank_accounts_eur": round(bank_eur, 2),
        "bank_accounts": bank_accounts,
        "manual_assets_eur": round(assets_eur, 2),
        "manual_liabilities_eur": round(liabilities_eur, 2),
        "deposits_eur": round(deposits_eur, 2),
        "deposits": [_enrich_deposit(dict(d)) for d in raw_deposits],
        "net_worth_eur": sheet["totals"]["net_worth_eur"],
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
        as_of=body.as_of.isoformat() if body.as_of else None,
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
    if "as_of" in fields:
        fields["as_of"] = fields["as_of"].isoformat()
    if "currency" in fields:
        fields["currency"] = fields["currency"].upper()
    # a changed amount is a fresh confirmation unless the caller dates it
    if "amount" in fields and "as_of" not in fields:
        fields["as_of"] = date.today().isoformat()
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
