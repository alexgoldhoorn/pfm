"""
Budget Router — named monthly plans and budget-vs-actual variance.

A budget is a set of planned monthly amounts keyed to things pfm already
tracks: spending and income categories, per-broker investment contributions,
and debt payments. Budgets are open-ended (their amounts apply to any month you
look at) and several can coexist as scenarios, exactly one flagged active.

All the interesting logic — month expansion, tree walks, the variance sign
convention, the seed proposals — lives in ``portf_manager.services.budget`` so
it is testable without a database or an FX lookup. This module is validation,
HTTP shape, and wiring.
"""

import logging
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portf_manager.services import budget as budget_service

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

# How many months the variance view covers by default.
DEFAULT_VARIANCE_MONTHS = 6


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _fx(currency: str) -> float:
    """EUR conversion rate — delegates to the portfolios router helper.

    Lazy import to avoid a circular import at module load, same shim as the
    spending router uses.
    """
    from portf_server.routers.portfolios import _get_fx_rate

    return _get_fx_rate(currency)


# ── Schemas ────────────────────────────────────────────────────────────────


class BudgetBody(BaseModel):
    name: str
    description: Optional[str] = None
    is_active: bool = False
    copy_from_budget_id: Optional[int] = None


class BudgetUpdateBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class BudgetResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    line_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class BudgetLineBody(BaseModel):
    line_type: Literal["income", "spending", "debt", "investment"]
    ref_key: str
    monthly_amount: float = 0
    overrides: Optional[Dict[str, float]] = None
    link_id: Optional[int] = None
    notes: Optional[str] = None


class BudgetLineUpdateBody(BaseModel):
    line_type: Optional[Literal["income", "spending", "debt", "investment"]] = None
    monthly_amount: Optional[float] = None
    overrides: Optional[Dict[str, float]] = None
    link_id: Optional[int] = None
    notes: Optional[str] = None


class BudgetLineResponse(BaseModel):
    id: int
    budget_id: int
    line_type: str
    ref_key: str
    label: str
    monthly_amount: float
    overrides: Dict[str, float] = {}
    link_id: Optional[int] = None
    notes: Optional[str] = None


class BudgetLinesBulkBody(BaseModel):
    lines: List[BudgetLineBody]


class BudgetVarianceLine(BaseModel):
    line_id: int
    line_type: str
    ref_key: str
    label: str
    notes: Optional[str] = None
    link_id: Optional[int] = None
    link_label: Optional[str] = None
    link_amount_eur: Optional[float] = None
    monthly_amount: float
    planned_eur: Dict[str, float]
    actual_eur: Dict[str, float]
    planned_total: float
    actual_total: float
    variance_eur: float
    variance_pct: Optional[float] = None
    favourable: bool


class BudgetVarianceUnbudgeted(BaseModel):
    ref_key: str
    label: str
    actual_eur: Dict[str, float]
    actual_total: float


class BudgetVarianceSection(BaseModel):
    key: str
    label: str
    favourable_when_under: bool
    lines: List[BudgetVarianceLine]
    unbudgeted: List[BudgetVarianceUnbudgeted]
    unbudgeted_suppressed: bool = False
    planned_total: float
    actual_total: float
    variance_eur: float
    variance_pct: Optional[float] = None
    favourable: bool


class BudgetVarianceNet(BaseModel):
    planned_total: float
    actual_total: float
    variance_eur: float
    variance_pct: Optional[float] = None
    favourable: bool


class BudgetVarianceResponse(BaseModel):
    budget_id: int
    budget_name: str
    is_active: bool
    months: List[str]
    sections: List[BudgetVarianceSection]
    net: BudgetVarianceNet


class BudgetSeedProposal(BaseModel):
    line_type: str
    ref_key: str
    label: str
    monthly_amount: float
    months_seen: int
    total_eur: float


# ── Helpers ────────────────────────────────────────────────────────────────


def _line_label(db, line: Dict, tree: List[Dict] = None) -> str:
    """Human label for a line: a portfolio name, or a category breadcrumb.

    Only a broker-keyed investment line names a portfolio; a category-keyed one
    reads like any other category line.
    """
    if line["line_type"] == "investment" and budget_service.is_broker_ref(
        line["ref_key"]
    ):
        portfolio = db.get_portfolio(int(line["ref_key"]))
        return portfolio["name"] if portfolio else f"Portfolio {line['ref_key']}"
    tree = tree if tree is not None else db.list_spending_categories_tree()
    return budget_service.category_path(tree, line["ref_key"])


def _serialize_line(db, line: Dict, tree: List[Dict] = None) -> BudgetLineResponse:
    return BudgetLineResponse(
        id=line["id"],
        budget_id=line["budget_id"],
        line_type=line["line_type"],
        ref_key=line["ref_key"],
        label=_line_label(db, line, tree),
        monthly_amount=float(line["monthly_amount"] or 0),
        overrides=budget_service.parse_overrides(line.get("overrides")),
        link_id=line.get("link_id"),
        notes=line.get("notes"),
    )


def _require_budget(db, budget_id: int) -> Dict:
    budget = db.get_budget(budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail=f"Budget {budget_id} not found")
    return budget


def _category_ref_keys(db, budget_id: int) -> set:
    """The categories a budget already budgets, across income/spending/debt.

    All three line types resolve against the same category tree, so they share
    one coverage space — budgeting "Housing" as spending and "Housing" as debt
    would count the same charges twice.
    """
    return {
        line["ref_key"]
        for line in db.list_budget_lines(budget_id)
        if budget_service.line_uses_category(line["line_type"], line["ref_key"])
    }


def _validate_ref_key(db, line_type: str, ref_key: str, sibling_ref_keys: set) -> None:
    """Reject a line that can't be reconciled, or that would double-count.

    Three rules, all 400s:

    - an investment line's ref_key must name a real portfolio;
    - a category line's category must exist and sit under the tree root its
      line type implies (income under Income, spending/debt under Spend) —
      the same root/sign invariant PUT /spending/{id} enforces;
    - no two category lines in one budget may sit on the same branch, since a
      parent's actual already sums its children's subtree.

    Args:
        sibling_ref_keys: Every other category this budget will budget once the
            change lands — for a bulk save that includes the rest of the batch,
            not just what's already stored.
    """
    if line_type == "investment" and budget_service.is_broker_ref(ref_key):
        # Broker-keyed: measured from Deposit bookings.
        if db.get_portfolio(int(ref_key)) is None:
            raise HTTPException(
                status_code=400, detail=f"Portfolio {ref_key} not found"
            )
        return

    if line_type == "investment" and str(ref_key).isdigit():
        # Unreachable via is_broker_ref above, kept explicit: an all-digits
        # name can't be a category here, since that's how a broker ref is told
        # apart from a category one.
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{ref_key}' is all digits, which an investment line reads as "
                f"a broker id. Rename the category, or budget the broker."
            ),
        )

    category = db.find_spending_category_by_name(ref_key)
    if category is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown spending category '{ref_key}'"
        )
    # A category-keyed investment line budgets an outflow, so it lives under
    # Spend like spending and debt do.
    expected_root = budget_service.LINE_TYPE_ROOT.get(line_type, "Spend")
    actual_root = db.get_spending_category_root(ref_key)
    if actual_root != expected_root:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{ref_key}' is {'an unfiled' if not actual_root else 'an ' + actual_root}"
                f" category; a "
                f"{line_type} line must budget one under {expected_root}"
            ),
        )

    tree = db.list_spending_categories_tree()
    conflict = budget_service.coverage_conflict(tree, sorted(sibling_ref_keys), ref_key)
    if conflict:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{ref_key}' overlaps the budgeted category '{conflict}' — one "
                f"sits above the other, so both lines would count the same "
                f"spending. Budget one or the other."
            ),
        )


# ── Budgets ────────────────────────────────────────────────────────────────


@router.get("/", response_model=List[BudgetResponse])
async def list_budgets(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every budget, active one first."""
    return [
        BudgetResponse(**{**b, "is_active": bool(b["is_active"])})
        for b in db.list_budgets()
    ]


@router.post("/", response_model=BudgetResponse, status_code=201)
async def create_budget(
    body: BudgetBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Create a budget, optionally duplicating another one's lines.

    Duplicating is how scenarios are meant to be made: copy the base budget,
    then adjust the handful of lines that differ in the best/worst case.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Budget name cannot be blank")
    if db.get_budget_by_name(name):
        raise HTTPException(
            status_code=409, detail=f"A budget named '{name}' already exists"
        )

    source_lines = []
    if body.copy_from_budget_id is not None:
        _require_budget(db, body.copy_from_budget_id)
        source_lines = db.list_budget_lines(body.copy_from_budget_id)

    budget_id = db.create_budget(name, body.description, is_active=body.is_active)
    for line in source_lines:
        db.create_budget_line(
            budget_id,
            line["line_type"],
            line["ref_key"],
            line["monthly_amount"],
            line.get("overrides"),
            line.get("link_id"),
            line.get("notes"),
        )
    created = db.get_budget(budget_id)
    return BudgetResponse(
        **{
            **created,
            "is_active": bool(created["is_active"]),
            "line_count": len(source_lines),
        }
    )


@router.get("/summary", response_model=Optional[BudgetVarianceResponse])
def get_budget_summary(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """This month's variance for the active budget, or null if there is none.

    One call for the Dashboard card and the Action Items check. Registered
    before ``/{budget_id}`` — FastAPI matches first, so a single-segment route
    declared after it would be swallowed as a budget id.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    budget = db.get_active_budget()
    if budget is None:
        return None
    return budget_service.compute_budget_variance(
        db, budget["id"], [budget_service.current_month()], _fx
    )


@router.get("/{budget_id}", response_model=dict)
async def get_budget(
    budget_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """One budget with all of its lines."""
    budget = _require_budget(db, budget_id)
    tree = db.list_spending_categories_tree()
    lines = [
        _serialize_line(db, line, tree).model_dump()
        for line in db.list_budget_lines(budget_id)
    ]
    return {
        **budget,
        "is_active": bool(budget["is_active"]),
        "line_count": len(lines),
        "lines": lines,
    }


@router.put("/{budget_id}", response_model=dict)
async def update_budget(
    budget_id: int,
    body: BudgetUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rename a budget or change its description."""
    _require_budget(db, budget_id)
    fields = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Budget name cannot be blank")
        existing = db.get_budget_by_name(name)
        if existing and existing["id"] != budget_id:
            raise HTTPException(
                status_code=409, detail=f"A budget named '{name}' already exists"
            )
        fields["name"] = name
    if body.description is not None:
        fields["description"] = body.description
    if fields:
        db.update_budget(budget_id, **fields)
    return db.get_budget(budget_id)


@router.post("/{budget_id}/activate", response_model=dict)
async def activate_budget(
    budget_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Make this the active budget, clearing the flag on every other one."""
    _require_budget(db, budget_id)
    db.set_active_budget(budget_id)
    return {"budget_id": budget_id, "is_active": True}


@router.delete("/{budget_id}", response_model=dict)
async def delete_budget(
    budget_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a budget and its lines."""
    _require_budget(db, budget_id)
    db.delete_budget(budget_id)
    return {"deleted": budget_id}


# ── Lines ──────────────────────────────────────────────────────────────────


@router.get("/{budget_id}/lines", response_model=List[BudgetLineResponse])
async def list_budget_lines(
    budget_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """A budget's lines."""
    _require_budget(db, budget_id)
    tree = db.list_spending_categories_tree()
    return [_serialize_line(db, line, tree) for line in db.list_budget_lines(budget_id)]


@router.post("/{budget_id}/lines", response_model=BudgetLineResponse, status_code=201)
async def create_budget_line(
    budget_id: int,
    body: BudgetLineBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Add a line to a budget."""
    _require_budget(db, budget_id)
    ref_key = body.ref_key.strip()
    if not ref_key:
        raise HTTPException(status_code=400, detail="ref_key cannot be blank")
    if db.find_budget_line(budget_id, body.line_type, ref_key):
        raise HTTPException(
            status_code=409,
            detail=f"This budget already has a {body.line_type} line for '{ref_key}'",
        )
    _validate_ref_key(
        db, body.line_type, ref_key, _category_ref_keys(db, budget_id) - {ref_key}
    )
    line_id = db.create_budget_line(
        budget_id,
        body.line_type,
        ref_key,
        body.monthly_amount,
        budget_service.serialize_overrides(body.overrides),
        body.link_id,
        body.notes,
    )
    return _serialize_line(db, db.get_budget_line(line_id))


@router.post("/{budget_id}/lines/bulk", response_model=dict)
async def bulk_upsert_budget_lines(
    budget_id: int,
    body: BudgetLinesBulkBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Insert-or-update many lines in one call — the grid save and the
    apply step of "seed from actuals".

    Existing lines matching (line_type, ref_key) are updated in place so their
    ids survive, and every incoming line is validated the same way a single
    create is. Validation runs over the whole batch first, so a rejected line
    doesn't leave half the batch written.
    """
    _require_budget(db, budget_id)
    seen = set()
    prepared = []
    for line in body.lines:
        ref_key = line.ref_key.strip()
        if not ref_key:
            raise HTTPException(status_code=400, detail="ref_key cannot be blank")
        key = (line.line_type, ref_key)
        if key in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate {line.line_type} line for '{ref_key}' in one request",
            )
        seen.add(key)

    # The coverage check has to see the budget as it will be AFTER the save:
    # two lines arriving in the same batch can overlap each other even though
    # neither conflicts with anything stored yet. Upserts never delete, so the
    # final state is simply the stored categories plus the incoming ones.
    incoming = {
        line.ref_key.strip()
        for line in body.lines
        if line.line_type in ("income", "spending", "debt")
    }
    final_keys = _category_ref_keys(db, budget_id) | incoming

    for line in body.lines:
        ref_key = line.ref_key.strip()
        _validate_ref_key(db, line.line_type, ref_key, final_keys - {ref_key})
        prepared.append(
            {
                "line_type": line.line_type,
                "ref_key": ref_key,
                "monthly_amount": line.monthly_amount,
                "overrides": budget_service.serialize_overrides(line.overrides),
                "link_id": line.link_id,
                "notes": line.notes,
            }
        )
    return db.upsert_budget_lines(budget_id, prepared)


@router.put("/{budget_id}/lines/{line_id}", response_model=BudgetLineResponse)
async def update_budget_line(
    budget_id: int,
    line_id: int,
    body: BudgetLineUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Update a line's type, monthly amount, per-month overrides, link or notes.

    ``line_type`` IS editable — reclassifying an outflow as Debt or Investment
    is how spending stops counting money you merely moved. ``ref_key`` is not:
    retargeting a line at a different category is a delete plus a create, so
    the coverage check can't be sidestepped.
    """
    _require_budget(db, budget_id)
    line = db.get_budget_line(line_id)
    if line is None or line["budget_id"] != budget_id:
        raise HTTPException(status_code=404, detail=f"Budget line {line_id} not found")
    fields = {}
    if body.line_type is not None and body.line_type != line["line_type"]:
        # Reclassifying a line is the whole point of "treat as": a mortgage
        # charge is Debt, money moved to a broker or a pension is Investment,
        # and only Spending should swell the spending total. The category is
        # unchanged, so the coverage space is identical -- only the section it
        # reports under moves. Re-validate anyway: an Income category must not
        # become a spending line, and a category-keyed line must not claim to
        # be a broker one.
        _validate_ref_key(
            db,
            body.line_type,
            line["ref_key"],
            _category_ref_keys(db, budget_id) - {line["ref_key"]},
        )
        fields["line_type"] = body.line_type
    if body.monthly_amount is not None:
        fields["monthly_amount"] = body.monthly_amount
    if body.overrides is not None:
        fields["overrides"] = budget_service.serialize_overrides(body.overrides)
    if body.link_id is not None:
        # An omitted field means "unchanged", so 0 is the way to say "no
        # liability" -- normalize it to NULL rather than storing a sentinel.
        fields["link_id"] = body.link_id or None
    if body.notes is not None:
        fields["notes"] = body.notes
    if fields:
        db.update_budget_line(line_id, **fields)
    return _serialize_line(db, db.get_budget_line(line_id))


@router.delete("/{budget_id}/lines/{line_id}", response_model=dict)
async def delete_budget_line(
    budget_id: int,
    line_id: int,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Remove a line from a budget."""
    _require_budget(db, budget_id)
    line = db.get_budget_line(line_id)
    if line is None or line["budget_id"] != budget_id:
        raise HTTPException(status_code=404, detail=f"Budget line {line_id} not found")
    db.delete_budget_line(line_id)
    return {"deleted": line_id}


# ── Variance & seeding ─────────────────────────────────────────────────────


@router.get("/{budget_id}/variance", response_model=BudgetVarianceResponse)
def get_budget_variance(
    budget_id: int,
    months: int = DEFAULT_VARIANCE_MONTHS,
    end_month: Optional[str] = None,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Planned vs actual over the last N calendar months.

    Amounts are positive magnitudes within a section, EUR-converted at today's
    rate and excluding transfers — the same conventions as
    ``/api/v1/spending/trend``, so the two reconcile. ``variance_eur`` is signed
    so positive always means favourable.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    _require_budget(db, budget_id)
    if months < 1 or months > 36:
        raise HTTPException(status_code=400, detail="months must be between 1 and 36")
    end = end_month or budget_service.current_month()
    try:
        period = budget_service.month_range(end, months)
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=400, detail=f"'{end_month}' is not a YYYY-MM month"
        )
    return budget_service.compute_budget_variance(db, budget_id, period, _fx)


@router.get("/{budget_id}/seed-proposals", response_model=List[BudgetSeedProposal])
def get_seed_proposals(
    budget_id: int,
    months: int = 12,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Budget lines suggested from the last N complete months of real activity.

    Writes nothing — the caller reviews these and applies the ones it wants via
    ``POST /{budget_id}/lines/bulk``.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    _require_budget(db, budget_id)
    if months < 1 or months > 36:
        raise HTTPException(status_code=400, detail="months must be between 1 and 36")
    return budget_service.propose_budget_lines(db, months, _fx)
