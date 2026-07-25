"""
Spending Router for Portfolio Management API

Bank-statement transaction import, rule-based categorization, and
inter-account transfer detection. Kept separate from the investment import
router (imports.py) — spending rows have no asset/quantity/price and use
different dedup + transfer semantics.
"""

import json
import logging
from typing import List, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel

from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv
from portf_manager.parsers.aeb43_parser import looks_like_aeb43, parse_aeb43
from portf_manager.services.transfer_matcher import find_all_transfer_matches
from portf_manager.llm_client import get_llm_client

from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _fx(currency: str) -> float:
    """EUR conversion rate — delegates to the portfolios router helper.

    Lazy import to avoid a circular import (portfolios.py doesn't import
    this module), matching the pattern already used in portfolio_advisor.py.
    """
    from portf_server.routers.portfolios import _get_fx_rate

    return _get_fx_rate(currency)


class PreviewSpendingRow(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    category: str = "uncategorized"
    is_duplicate: bool = False
    balance: Optional[float] = None


class SpendingUploadResponse(BaseModel):
    account_portfolio_id: int
    rows: List[PreviewSpendingRow]
    skipped_count: int
    skipped: List[dict]
    duplicate_count: int


class SpendingSaveRequest(BaseModel):
    account_portfolio_id: int
    rows: List[PreviewSpendingRow]
    duplicate_action: Literal["skip", "add", "overwrite"] = "skip"


class SpendingSaveResponse(BaseModel):
    saved: int
    duplicates_skipped: int
    overwritten: int
    transfers_linked: int
    errors: List[str]


class SpendingTransactionResponse(BaseModel):
    id: int
    portfolio_id: int
    portfolio_name: Optional[str] = None
    date: str
    description: str
    amount: float
    currency: str
    category: str
    is_transfer: bool
    transfer_link_type: Optional[str] = None
    transfer_link_id: Optional[int] = None
    source: Optional[str] = None
    balance: Optional[float] = None


class SpendingTransactionListResponse(BaseModel):
    items: List[SpendingTransactionResponse]
    total: int


class CategoryUpdateBody(BaseModel):
    category: str


class SpendingRuleBody(BaseModel):
    pattern: str
    category: str


class SpendingRuleResponse(BaseModel):
    id: int
    pattern: str
    category: str


class SpendingRuleUpdateBody(BaseModel):
    pattern: Optional[str] = None
    category: Optional[str] = None


class SpendingCategoryBody(BaseModel):
    name: str


class SpendingCategoryRenameBody(BaseModel):
    new_name: str


class SpendingSummaryResponse(BaseModel):
    spent_eur: float
    income_eur: float
    transferred_eur: float
    by_category_eur: dict


def _sign_matches_root(root: Optional[str], amount: float) -> bool:
    """True if a category's tree root is consistent with a transaction's
    amount sign. A category outside the tree (root is None) is exempt."""
    if root is None:
        return True
    return (root == "Spend") == (amount < 0)


def _apply_rules(description: str, rules: List[dict], amount: float, db) -> str:
    """First-match-wins, case-insensitive substring match.

    Rules are already ordered by id (oldest = highest priority) by
    db.list_spending_rules(). A blank pattern is skipped rather than
    treated as a match-everything wildcard — "" is a substring of every
    string in Python, so an unguarded empty pattern would silently
    recategorize an entire backlog to one category. A rule matching a
    category whose tree root doesn't match the transaction's amount sign
    is treated as a non-match (falls back to uncategorized) rather than
    applied incorrectly or raising -- this runs unattended over many rows.
    """
    desc_lower = description.lower()
    for rule in rules:
        pattern = rule["pattern"].strip()
        if pattern and pattern.lower() in desc_lower:
            category = rule["category"]
            if _sign_matches_root(db.get_spending_category_root(category), amount):
                return category
            return "uncategorized"
    return "uncategorized"


def _resolve_account(
    db, account_portfolio_id: Optional[int], account_name: Optional[str]
) -> int:
    if account_portfolio_id:
        return account_portfolio_id
    if account_name:
        return db.get_or_create_portfolio(
            account_name, base_currency="EUR", account_type="bank"
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide either account_portfolio_id or account_name",
    )


@router.post("/upload", response_model=SpendingUploadResponse)
async def upload_bank_statement(
    file: UploadFile = File(..., description="Bank statement CSV"),
    account_portfolio_id: Optional[int] = Form(None),
    account_name: Optional[str] = Form(None),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Parse a bank statement CSV and return a rule-categorized preview. No DB write."""
    portfolio_id = _resolve_account(db, account_portfolio_id, account_name)

    file_bytes = await file.read()
    try:
        # AEB43 exports commonly contain raw Latin-1 bytes (accented
        # characters); fall back when the file isn't valid UTF-8.
        try:
            content = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = file_bytes.decode("latin-1")
        if looks_like_aeb43(content):
            result = parse_aeb43(content)
        else:
            result = parse_generic_bank_csv(content)
    except Exception as e:
        logger.exception("Error parsing bank statement")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file: {str(e)}",
        )

    rules = db.list_spending_rules()
    dup_count = 0
    rows: List[PreviewSpendingRow] = []
    for r in result.rows:
        category = _apply_rules(r.description, rules, r.amount, db)
        is_dup = (
            db.find_duplicate_spending_transaction(
                portfolio_id=portfolio_id,
                date=r.date,
                amount=r.amount,
                description=r.description,
            )
            is not None
        )
        if is_dup:
            dup_count += 1
        rows.append(
            PreviewSpendingRow(
                date=r.date,
                description=r.description,
                amount=r.amount,
                currency=r.currency,
                category=category,
                is_duplicate=is_dup,
                balance=r.balance,
            )
        )

    skipped = [{"row": row, "reason": reason} for row, reason in result.skipped]
    return SpendingUploadResponse(
        account_portfolio_id=portfolio_id,
        rows=rows,
        skipped_count=len(skipped),
        skipped=skipped,
        duplicate_count=dup_count,
    )


def _run_transfer_matching(db, saved_ids: List[int]) -> int:
    """Run transfer auto-linking over the given spending row ids. Returns count linked."""
    if not saved_ids:
        return 0
    unlinked = db.list_unlinked_spending_transactions()
    rows = [r for r in unlinked if r["id"] in saved_ids]
    if not rows:
        return 0
    # Bookings already claimed as a transfer counterpart in a prior save/
    # rescan call must be excluded — otherwise an unrelated later spending
    # row with the same amount/currency in the date window could wrongly
    # link to the same already-used Deposit booking (bookings have no
    # per-call "consumed" tracking of their own, unlike spending rows which
    # drop out of the unlinked pool once is_transfer=1).
    already_linked_booking_ids = {
        r["transfer_link_id"]
        for r in db.list_spending_transactions(is_transfer=True)
        if r.get("transfer_link_type") == "booking"
    }
    deposit_bookings = [
        b
        for b in db.get_all_bookings()
        if b.get("action") == "Deposit" and b["id"] not in already_linked_booking_ids
    ]
    matches = find_all_transfer_matches(rows, unlinked, deposit_bookings)
    for m in matches:
        db.update_spending_transaction(
            m.spending_id,
            category="Transfer",
            is_transfer=True,
            transfer_link_type=m.link_type,
            transfer_link_id=m.link_id,
        )
        if m.link_type == "spending":
            db.update_spending_transaction(
                m.link_id,
                category="Transfer",
                is_transfer=True,
                transfer_link_type="spending",
                transfer_link_id=m.spending_id,
            )
    return len(matches)


@router.post("/save", response_model=SpendingSaveResponse)
async def save_spending_transactions(
    body: SpendingSaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save previewed spending rows, honoring duplicate_action, then auto-link transfers."""

    def _resolve_row_category(row) -> str:
        root = db.get_spending_category_root(row.category)
        return row.category if _sign_matches_root(root, row.amount) else "uncategorized"

    saved = 0
    duplicates_skipped = 0
    overwritten = 0
    errors: List[str] = []
    saved_ids: List[int] = []

    for row in body.rows:
        try:
            existing = db.find_duplicate_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                amount=row.amount,
                description=row.description,
            )
            if existing:
                if body.duplicate_action == "skip":
                    duplicates_skipped += 1
                    continue
                if body.duplicate_action == "overwrite":
                    db.update_spending_transaction(
                        existing["id"], category=_resolve_row_category(row)
                    )
                    overwritten += 1
                    saved_ids.append(existing["id"])
                    continue
                # "add": fall through and insert a second copy

            new_id = db.create_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                description=row.description,
                amount=row.amount,
                currency=row.currency,
                category=_resolve_row_category(row),
                source="generic",
                balance=row.balance,
            )
            saved += 1
            saved_ids.append(new_id)
        except Exception as e:
            errors.append(f"{row.date} {row.description}: {str(e)}")
            logger.warning(f"Failed to save spending row: {e}")

    transfers_linked = _run_transfer_matching(db, saved_ids)

    return SpendingSaveResponse(
        saved=saved,
        duplicates_skipped=duplicates_skipped,
        overwritten=overwritten,
        transfers_linked=transfers_linked,
        errors=errors,
    )


_SPENDING_SORT_BY_VALUES = {
    "date",
    "portfolio_name",
    "description",
    "category",
    "amount",
}
_SPENDING_SORT_DIR_VALUES = {"asc", "desc"}
_SPENDING_AMOUNT_SIGN_VALUES = {"positive", "negative"}


@router.get("/", response_model=SpendingTransactionListResponse)
async def list_spending(
    portfolio_id: Optional[int] = None,
    category: Optional[str] = None,
    categories: Optional[List[str]] = Query(default=None),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_transfer: Optional[bool] = None,
    amount_sign: Optional[str] = None,
    min_abs_amount: Optional[float] = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = "date",
    sort_dir: str = "desc",
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """List spending transactions with optional filters, paginated and sorted.

    `categories` is a repeatable query param (`?categories=A&categories=B`)
    matched with SQL `IN`; omit it entirely for no category filter (an
    empty/absent list means unfiltered, not "match nothing"). `amount_sign`
    filters to negative-only ("negative", i.e. expenses) or positive-only
    ("positive", i.e. income); `min_abs_amount` additionally requires
    `ABS(amount) >= min_abs_amount`, so it composes naturally with either
    sign or neither.
    """
    if sort_by not in _SPENDING_SORT_BY_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"sort_by must be one of {sorted(_SPENDING_SORT_BY_VALUES)}",
        )
    if sort_dir not in _SPENDING_SORT_DIR_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"sort_dir must be one of {sorted(_SPENDING_SORT_DIR_VALUES)}",
        )
    if amount_sign is not None and amount_sign not in _SPENDING_AMOUNT_SIGN_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"amount_sign must be one of {sorted(_SPENDING_AMOUNT_SIGN_VALUES)}",
        )
    filters = dict(
        portfolio_id=portfolio_id,
        category=category,
        categories=categories,
        start_date=start_date,
        end_date=end_date,
        is_transfer=is_transfer,
        amount_sign=amount_sign,
        min_abs_amount=min_abs_amount,
    )
    rows = db.list_spending_transactions(
        limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir, **filters
    )
    total = db.count_spending_transactions(**filters)
    return SpendingTransactionListResponse(
        items=[
            SpendingTransactionResponse(**{**r, "is_transfer": bool(r["is_transfer"])})
            for r in rows
        ],
        total=total,
    )


@router.put("/{spending_id}", response_model=dict)
async def update_spending_category(
    spending_id: int,
    body: CategoryUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit a spending row's category (inline edit from the UI table).

    Recategorizing a row away from "Transfer" also clears its transfer flag
    and link fields — otherwise the row would keep showing the "Transfer"
    badge and stay excluded from spent_eur/income_eur even though the user
    just said it isn't a transfer. Only this row is reset; its counterpart
    (the other leg of the pair, if any) is left untouched — a known,
    accepted limitation for this pass.

    Recategorizing TO "Transfer" does not itself set is_transfer=True — that
    flag is only meant to reflect a genuine match made by the transfer
    matcher, not a manual category edit.
    """
    existing = db.get_spending_transaction(spending_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Spending transaction not found")

    category = body.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")

    root = db.get_spending_category_root(category)
    if not _sign_matches_root(root, existing["amount"]):
        raise HTTPException(
            status_code=400,
            detail=f"'{category}' is an {root} category; this transaction is {'a debit' if existing['amount'] < 0 else 'a credit'}",
        )

    update_kwargs = {"category": category}
    if category != "Transfer" and existing.get("is_transfer"):
        update_kwargs["is_transfer"] = False
        update_kwargs["transfer_link_type"] = None
        update_kwargs["transfer_link_id"] = None

    db.update_spending_transaction(spending_id, **update_kwargs)
    return {"id": spending_id, "category": category}


@router.delete("/{spending_id}", response_model=dict)
async def delete_spending(
    spending_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a spending transaction (hard delete)."""
    if not db.delete_spending_transaction(spending_id):
        raise HTTPException(status_code=404, detail="Spending transaction not found")
    return {"deleted": True, "id": spending_id}


@router.post("/rescan-transfers", response_model=dict)
async def rescan_transfers(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Re-run transfer matching over all currently-unlinked spending rows.

    Covers the case where a matching leg is imported later, from a different
    account's statement.
    """
    unlinked = db.list_unlinked_spending_transactions()
    ids = [r["id"] for r in unlinked]
    linked = _run_transfer_matching(db, ids)
    return {"transfers_linked": linked}


class RescanCategoriesBody(BaseModel):
    ids: Optional[List[int]] = None


@router.post("/rescan-categories", response_model=dict)
async def rescan_categories(
    body: Optional[RescanCategoriesBody] = None,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Re-apply current spending_rules to still-uncategorized rows.

    Scoped to `body.ids` when provided (only those rows, and only the ones
    among them still "uncategorized"); every uncategorized row otherwise.
    Never touches a row that already has a non-"uncategorized" category —
    covers the case where rules are added/edited after rows were imported.
    """
    rules = db.list_spending_rules()
    uncategorized = db.list_spending_transactions(category="uncategorized")
    if body and body.ids is not None:
        id_set = set(body.ids)
        uncategorized = [row for row in uncategorized if row["id"] in id_set]
    updated = 0
    for row in uncategorized:
        category = _apply_rules(row["description"], rules, row["amount"], db)
        if category != "uncategorized":
            if db.update_spending_transaction(row["id"], category=category):
                updated += 1
    return {"recategorized": updated}


@router.get("/rules", response_model=List[SpendingRuleResponse])
async def list_rules(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List all spending category rules."""
    return [SpendingRuleResponse(**r) for r in db.list_spending_rules()]


@router.post("/rules", response_model=SpendingRuleResponse, status_code=201)
async def create_rule(
    body: SpendingRuleBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Create a spending category rule."""
    pattern = body.pattern.strip()
    category = body.category.strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="Pattern cannot be empty")
    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")
    if db.find_duplicate_spending_rule(pattern, category):
        raise HTTPException(
            status_code=409,
            detail=f"A rule with pattern '{pattern}' and category '{category}' already exists",
        )
    rule_id = db.create_spending_rule(pattern=pattern, category=category)
    return SpendingRuleResponse(id=rule_id, pattern=pattern, category=category)


@router.put("/rules/{rule_id}", response_model=SpendingRuleResponse)
async def update_rule(
    rule_id: int,
    body: SpendingRuleUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit an existing spending category rule's pattern and/or category."""
    update_kwargs = {}
    if body.pattern is not None:
        pattern = body.pattern.strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="Pattern cannot be empty")
        update_kwargs["pattern"] = pattern
    if body.category is not None:
        category = body.category.strip()
        if not category:
            raise HTTPException(status_code=400, detail="Category cannot be empty")
        update_kwargs["category"] = category
    if not update_kwargs:
        raise HTTPException(
            status_code=400, detail="Provide at least one of pattern or category"
        )
    if not db.update_spending_rule(rule_id, **update_kwargs):
        raise HTTPException(status_code=404, detail="Rule not found")
    updated = db.get_spending_rule(rule_id)
    return SpendingRuleResponse(**updated)


@router.delete("/rules/{rule_id}", response_model=dict)
async def delete_rule(
    rule_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a spending category rule."""
    if not db.delete_spending_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True, "id": rule_id}


@router.get("/categories", response_model=List[str])
async def list_categories(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """List every known spending category (used + explicitly registered, deduplicated)."""
    return db.list_spending_categories()


@router.post("/categories", response_model=dict, status_code=201)
async def create_category(
    body: SpendingCategoryBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Register a new, initially-unused spending category."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if db.find_spending_category_by_name(name):
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category_id = db.create_spending_category(name)
    return {"id": category_id, "name": name}


@router.put("/categories/{old_name}", response_model=dict)
async def rename_category(
    old_name: str,
    body: SpendingCategoryRenameBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rename a category everywhere it's used (transactions, rules, registry)."""
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if new_name == old_name:
        raise HTTPException(
            status_code=400, detail="New name is the same as the current name"
        )
    result = db.rename_spending_category(old_name, new_name)
    return {"old_name": old_name, "new_name": new_name, **result}


@router.get("/summary", response_model=SpendingSummaryResponse)
def get_spending_summary(
    days: int = 30,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Aggregate spending/income/transfers across all bank accounts for the
    last N days, converted to EUR. Powers the Spending page summary cards and
    the Net Worth page's read-only comparison widget.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    from datetime import date, timedelta

    start_date = (date.today() - timedelta(days=days)).isoformat()
    rows = db.list_spending_transactions(start_date=start_date)

    spent_eur = 0.0
    income_eur = 0.0
    transferred_eur = 0.0
    by_category_eur: dict = {}

    for r in rows:
        amt_eur = float(r["amount"]) * _fx(r.get("currency", "EUR"))
        if r["is_transfer"]:
            transferred_eur += abs(amt_eur)
            continue
        if amt_eur < 0:
            spent_eur += abs(amt_eur)
            by_category_eur[r["category"]] = by_category_eur.get(
                r["category"], 0.0
            ) + abs(amt_eur)
        else:
            income_eur += amt_eur

    return SpendingSummaryResponse(
        spent_eur=round(spent_eur, 2),
        income_eur=round(income_eur, 2),
        transferred_eur=round(transferred_eur, 2),
        by_category_eur={k: round(v, 2) for k, v in by_category_eur.items()},
    )


class SuggestCategoriesRequest(BaseModel):
    rows: List[PreviewSpendingRow]


class CategorySuggestion(BaseModel):
    description: str
    category: str
    suggested_pattern: str


class SuggestCategoriesResponse(BaseModel):
    suggestions: List[CategorySuggestion]


def _build_suggest_prompt(descriptions: List[str]) -> str:
    lines = "\n".join(f"- {d}" for d in descriptions)
    return f"""
You categorize bank statement transaction descriptions into everyday spending
categories. For each description below, suggest ONE category from this set
(or a similarly short new one if none fit): Groceries, Dining, Transport,
Utilities, Housing, Health, Entertainment, Shopping, Income, Subscriptions,
Other.

Also suggest a short "pattern" — a distinctive substring of the description
(e.g. the merchant name) that could be reused to auto-match future rows with
the same category. Keep it as short as possible while still being specific
to this merchant (avoid matching unrelated transactions).

Real bank descriptions often carry noise around the merchant name: a leading
numeric card/transaction-reference number (e.g. "767002813178EXAMPLE
MERCHANT...") and/or a trailing location+date+reference code (e.g.
"...\\CITY\\ES0000000019"). Ignore that noise — the pattern must be just the
clean merchant name (e.g. "EXAMPLE MERCHANT"), never the numeric prefix or
the trailing location/date/reference suffix.

Return ONLY a JSON array, one object per description, in the same order:
[{{"description": "...", "category": "...", "suggested_pattern": "..."}}]

Descriptions:
{lines}
"""


def _parse_suggestions(data: object) -> List[CategorySuggestion]:
    """Turn parsed LLM JSON into validated suggestions, tolerating junk items.

    The LLM is asked for a JSON array of objects, but syntactically valid
    JSON can still mis-shape the payload (e.g. a flat list of strings). Any
    element that isn't a dict is skipped rather than raising, matching the
    existing tolerant handling of blank descriptions below.

    Args:
        data: The `json.loads()` result of the LLM response.

    Returns:
        Validated suggestions built only from well-formed dict items.
    """
    suggestions: List[CategorySuggestion] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description") or "").strip()
        category = str(item.get("category") or "").strip() or "Other"
        pattern = str(item.get("suggested_pattern") or "").strip() or desc[:20]
        if not desc:
            continue
        suggestions.append(
            CategorySuggestion(
                description=desc, category=category, suggested_pattern=pattern
            )
        )
    return suggestions


@router.post("/suggest-categories", response_model=SuggestCategoriesResponse)
async def suggest_categories(
    body: SuggestCategoriesRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """LLM-assisted category suggestions for rows no rule matched.

    Explicit user-triggered action (a button in the import preview) — not
    run automatically on every upload, since LLM calls are slow/costly.
    """
    if not body.rows:
        return SuggestCategoriesResponse(suggestions=[])

    descriptions = [r.description for r in body.rows]
    prompt = _build_suggest_prompt(descriptions)

    try:
        llm = get_llm_client()
        response_text = llm.generate(prompt).strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(
                ln for ln in lines if not ln.strip().startswith("```")
            )
        data = json.loads(response_text)
    except Exception as e:
        logger.warning(f"Category suggestion LLM call failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Category suggestion failed: {str(e)}",
        )

    return SuggestCategoriesResponse(suggestions=_parse_suggestions(data))
