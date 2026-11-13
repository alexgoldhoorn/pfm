"""
Spending Router for Portfolio Management API

Bank-statement transaction import, rule-based categorization, and
inter-account transfer detection. Kept separate from the investment import
router (imports.py) — spending rows have no asset/quantity/price and use
different dedup + transfer semantics.
"""

import json
import logging
from typing import Callable, Dict, List, Literal, Optional

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
from pydantic import BaseModel, Field

from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv
from portf_manager.parsers.aeb43_parser import looks_like_aeb43, parse_aeb43
from portf_manager.services.balance_check import find_balance_breaks
from portf_manager.services.own_accounts import (
    clean_patterns,
    load_patterns,
    save_patterns,
)
from portf_manager.services.transfer_linking import (
    flag_own_account_rows,
    preview_own_account_pattern,
    settle_transfers,
)
from portf_manager.services.budget import build_children_index, subtree_names
from portf_manager.services.merchant import normalize_merchant
from portf_manager.services.recurring import load_recurring
from portf_manager.services.spending_rules import (
    TRANSFER_CATEGORY,
    pick_category,
    rule_matches,
)
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
    merchant: Optional[str] = None


class BalanceBreakOut(BaseModel):
    date: str
    description: str
    currency: str
    expected: float
    actual: float
    kind: str


class SpendingUploadResponse(BaseModel):
    account_portfolio_id: int
    rows: List[PreviewSpendingRow]
    skipped_count: int
    skipped: List[dict]
    duplicate_count: int
    balance_rows_checked: int = 0
    balance_breaks: List[BalanceBreakOut] = []


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
    # Detail for the "Import complete" summary (additive, optional).
    account_name: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    money_in: Dict[str, float] = {}
    money_out: Dict[str, float] = {}
    uncategorized: int = 0
    latest_balance: Optional[float] = None
    latest_balance_currency: Optional[str] = None
    latest_balance_date: Optional[str] = None


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
    transfer_source: Optional[str] = None
    source: Optional[str] = None
    balance: Optional[float] = None
    merchant: Optional[str] = None


class RulePreviewResponse(BaseModel):
    match_count: int
    sample: List[SpendingTransactionResponse]


class SpendingTransactionListResponse(BaseModel):
    items: List[SpendingTransactionResponse]
    total: int


class OwnAccountPatternsBody(BaseModel):
    patterns: List[str]


class OwnAccountPreviewBody(BaseModel):
    pattern: str


class CategoryUpdateBody(BaseModel):
    category: Optional[str] = None
    is_transfer: Optional[bool] = None


class SpendingRuleBody(BaseModel):
    pattern: str
    category: str
    portfolio_id: Optional[int] = None
    amount_sign: Optional[Literal["positive", "negative"]] = None
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    priority: int = Field(default=100, ge=0, le=10000)


class SpendingRuleResponse(BaseModel):
    id: int
    pattern: str
    category: str
    portfolio_id: Optional[int] = None
    amount_sign: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    priority: int = 100


class SpendingRuleUpdateBody(BaseModel):
    pattern: Optional[str] = None
    category: Optional[str] = None
    portfolio_id: Optional[int] = None
    amount_sign: Optional[Literal["positive", "negative"]] = None
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    priority: Optional[int] = Field(default=None, ge=0, le=10000)


class RulePreviewBody(BaseModel):
    pattern: str
    category: Optional[str] = None
    portfolio_id: Optional[int] = None
    amount_sign: Optional[Literal["positive", "negative"]] = None
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    only_uncategorized: bool = True
    exclude_ids: List[int] = []


class SpendingCategoryBody(BaseModel):
    name: str
    parent_name: str


class SpendingCategoryReparentBody(BaseModel):
    new_parent_name: str


class SpendingCategoryRenameBody(BaseModel):
    new_name: str


class SpendingSummaryResponse(BaseModel):
    spent_eur: float
    income_eur: float
    transferred_eur: float
    by_category_eur: dict


class RecurringItem(BaseModel):
    merchant: str
    portfolio_id: int
    account_name: Optional[str] = None
    currency: str
    category: str
    cadence: str
    occurrences: int
    first_date: str
    last_date: str
    last_amount: float
    typical_amount: float
    annual_amount: float
    annual_amount_eur: float
    next_expected: str
    status: str
    price_change_pct: Optional[float] = None
    transaction_ids: List[int]


class RecurringResponse(BaseModel):
    items: List[RecurringItem]
    monthly_total_eur: float
    annual_total_eur: float


def _sign_matches_root(root: Optional[str], amount: float) -> bool:
    """True if a category's tree root is consistent with a transaction's
    amount sign. A category outside the tree (root is None) is exempt."""
    if root is None:
        return True
    return (root == "Spend") == (amount < 0)


def _category_fits(db) -> Callable[[str, float], bool]:
    """A ``category_fits(category, amount)`` check with memoised root lookups.

    Build one per request: every row of an upload or rescan asks about the
    same handful of rule categories, and each root lookup walks the tree.

    Args:
        db: Database used for ``get_spending_category_root``.

    Returns:
        A function telling whether ``category`` can hold ``amount``'s sign.
    """
    roots: Dict[str, Optional[str]] = {}

    def fits(category: str, amount: float) -> bool:
        if category not in roots:
            roots[category] = db.get_spending_category_root(category)
        return _sign_matches_root(roots[category], amount)

    return fits


def _apply_rules(
    description: str,
    rules: List[dict],
    amount: float,
    category_fits: Callable[[str, float], bool],
    merchant: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> str:
    """Category for a row from the rules, or "uncategorized".

    Rule order, conditions and the blank-pattern guard live in
    services.spending_rules. A rule whose category's tree root doesn't match
    the amount's sign is skipped — this runs unattended over many rows, so it
    must never apply a wrong-signed category or raise.

    Args:
        description: Raw bank description.
        rules: Rules from ``db.list_spending_rules()``.
        amount: Signed amount.
        category_fits: From ``_category_fits(db)``, built once per request.
        merchant: Normalised merchant name, if known.
        portfolio_id: The row's bank account.

    Returns:
        The first fitting rule's category, or "uncategorized".
    """
    return pick_category(
        rules, description, merchant, amount, portfolio_id, category_fits
    )


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
    fits = _category_fits(db)
    dup_count = 0
    rows: List[PreviewSpendingRow] = []
    for r in result.rows:
        merchant = normalize_merchant(r.description)
        category = _apply_rules(
            r.description,
            rules,
            r.amount,
            fits,
            merchant=merchant,
            portfolio_id=portfolio_id,
        )
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
                merchant=merchant,
            )
        )

    # Warn (never block) when stated balances don't add up: rows missing from
    # this file, or between the previous import and this one.
    balance_rows = [r for r in result.rows if r.balance is not None]
    breaks = []
    if balance_rows:
        first_date = min(str(r.date)[:10] for r in result.rows)
        prior = db.get_latest_bank_balance_before(portfolio_id, first_date)
        breaks = find_balance_breaks(
            result.rows, opening_balance=prior["balance"] if prior else None
        )

    skipped = [{"row": row, "reason": reason} for row, reason in result.skipped]
    return SpendingUploadResponse(
        account_portfolio_id=portfolio_id,
        rows=rows,
        skipped_count=len(skipped),
        skipped=skipped,
        duplicate_count=dup_count,
        balance_rows_checked=len(balance_rows),
        balance_breaks=[BalanceBreakOut(**vars(b)) for b in breaks],
    )


@router.post("/save", response_model=SpendingSaveResponse)
async def save_spending_transactions(
    body: SpendingSaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save previewed spending rows, honoring duplicate_action, then auto-link transfers."""

    fits = _category_fits(db)

    def _resolve_row_category(row) -> str:
        return row.category if fits(row.category, row.amount) else "uncategorized"

    saved = 0
    duplicates_skipped = 0
    overwritten = 0
    errors: List[str] = []
    saved_ids: List[int] = []
    saved_dates: List[str] = []
    money_in: Dict[str, float] = {}
    money_out: Dict[str, float] = {}
    uncategorized_ids: List[int] = []

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
                    # A transfer keeps its category and transfer fields: the
                    # "Transfer" category and the flag must stay in agreement.
                    current = db.get_spending_transaction(existing["id"]) or {}
                    if not current.get("is_transfer"):
                        new_category = _resolve_row_category(row)
                        fields: dict = {"category": new_category}
                        if new_category == TRANSFER_CATEGORY:
                            # Same as the insert path: a hand-marked transfer.
                            fields.update(is_transfer=True, transfer_source="manual")
                        db.update_spending_transaction(existing["id"], **fields)
                    overwritten += 1
                    saved_ids.append(existing["id"])
                    continue
                # "add": fall through and insert a second copy

            category = _resolve_row_category(row)
            new_id = db.create_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                description=row.description,
                amount=row.amount,
                currency=row.currency,
                category=category,
                source="generic",
                balance=row.balance,
            )
            if category == TRANSFER_CATEGORY:
                # Chosen at import: a transfer marked by hand, with no link.
                db.update_spending_transaction(
                    new_id, is_transfer=True, transfer_source="manual"
                )
            saved += 1
            saved_ids.append(new_id)
            saved_dates.append(str(row.date)[:10])
            ccy = (row.currency or "EUR").upper()
            bucket = money_in if row.amount > 0 else money_out
            bucket[ccy] = round(bucket.get(ccy, 0.0) + abs(float(row.amount)), 2)
            if category == "uncategorized":
                uncategorized_ids.append(new_id)
        except Exception as e:
            errors.append(f"{row.date} {row.description}: {str(e)}")
            logger.warning(f"Failed to save spending row: {e}")

    transfers_linked = settle_transfers(db, saved_ids)["linked"] if saved_ids else 0

    # Summary-only lookups: never fail a save because of them.
    account_name = None
    latest = None
    uncategorized = 0
    try:
        # Recount after transfer matching, which re-files matched rows as
        # "Transfer" — the pre-match count would overstate the work left.
        for sid in uncategorized_ids:
            r = db.get_spending_transaction(sid)
            if r and r.get("category") == "uncategorized":
                uncategorized += 1
        p = db.get_portfolio(body.account_portfolio_id)
        account_name = p.get("name") if p else None
        latest = db.get_latest_bank_balance(body.account_portfolio_id)
    except Exception as e:
        logger.debug(f"Spending save summary lookup failed: {e}")

    return SpendingSaveResponse(
        saved=saved,
        duplicates_skipped=duplicates_skipped,
        overwritten=overwritten,
        transfers_linked=transfers_linked,
        errors=errors,
        account_name=account_name,
        date_from=min(saved_dates) if saved_dates else None,
        date_to=max(saved_dates) if saved_dates else None,
        money_in=money_in,
        money_out=money_out,
        uncategorized=uncategorized,
        latest_balance=(latest or {}).get("balance"),
        latest_balance_currency=(latest or {}).get("currency"),
        latest_balance_date=(latest or {}).get("date"),
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
    transfer_linked: Optional[bool] = None,
    amount_sign: Optional[str] = None,
    min_abs_amount: Optional[float] = Query(default=None, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
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
    sign or neither. `transfer_linked=true` keeps rows linked to a counterpart,
    `false` keeps flagged rows with no link. `q` matches description, merchant
    or category name (its own or one above it in the tree), case-insensitively
    and literally.
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
        transfer_linked=transfer_linked,
        amount_sign=amount_sign,
        min_abs_amount=min_abs_amount,
        q=(q or "").strip() or None,
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


@router.get("/own-account-patterns", response_model=dict)
async def get_own_account_patterns(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Text patterns that mark a row as a transfer to the user's own account."""
    return {"patterns": load_patterns(db)}


@router.put("/own-account-patterns", response_model=dict)
async def put_own_account_patterns(
    body: OwnAccountPatternsBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Replace the pattern list, then flag every row that now matches."""
    try:
        stored = save_patterns(db, body.patterns)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"patterns": stored, "flagged": flag_own_account_rows(db)}


@router.post("/own-account-patterns/preview", response_model=dict)
async def preview_own_account_patterns(
    body: OwnAccountPreviewBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rows a pattern would flag, without saving it (first 20 returned)."""
    try:
        pattern = clean_patterns([body.pattern])[0]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    rows = preview_own_account_pattern(db, pattern)
    return {
        "count": len(rows),
        "rows": [
            SpendingTransactionResponse(**{**r, "is_transfer": bool(r["is_transfer"])})
            for r in rows[:20]
        ],
    }


@router.put("/{spending_id}", response_model=dict)
async def update_spending_category(
    spending_id: int,
    body: CategoryUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit a spending row's category and/or its transfer flag.

    The "Transfer" category and the ``is_transfer`` flag always agree:

    - Marking (``is_transfer: true``, or ``category: "Transfer"`` with
      ``is_transfer`` absent) sets the flag and ``transfer_source = "manual"``.
      A row that is already a transfer keeps its link; any other row gets no
      link, because by-hand marking exists for a counterpart that was never
      imported (an untracked account) and so has nothing to point at.
    - Un-marking (``is_transfer: false``, or any other category on a flagged
      row) clears the flag and the link and sets ``transfer_source =
      "rejected"``, so neither a rescan nor an own-account pattern re-flags
      it. A rejected row is never linked or flagged automatically again; only
      marking it by hand makes it a transfer. A genuine reciprocal
      counterpart is reset as if the row had been deleted (its source goes
      back to NULL, not "rejected": the user only judged this row).

    Either way every surface that excludes transfers (/summary, /trend, the
    Budget) sees the row consistently.
    """
    existing = db.get_spending_transaction(spending_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Spending transaction not found")
    if body.category is None and body.is_transfer is None:
        raise HTTPException(
            status_code=400,
            detail="Nothing to update: pass category, is_transfer, or both",
        )

    update_kwargs: dict = {}
    category = None
    if body.category is not None:
        category = body.category.strip()
        if not category:
            raise HTTPException(status_code=400, detail="Category cannot be empty")
        # "Transfer" sits outside the Income/Spend tree, so it fits any sign.
        root = db.get_spending_category_root(category)
        if not _sign_matches_root(root, existing["amount"]):
            raise HTTPException(
                status_code=400,
                detail=f"'{category}' is an {root} category; this transaction is {'a debit' if existing['amount'] < 0 else 'a credit'}",
            )
        update_kwargs["category"] = category

    if body.is_transfer is False and category == TRANSFER_CATEGORY:
        raise HTTPException(
            status_code=400,
            detail='A row can\'t be in the "Transfer" category without being a transfer',
        )
    was_transfer = bool(existing.get("is_transfer"))
    mark = body.is_transfer is True or (
        body.is_transfer is None and category == TRANSFER_CATEGORY
    )
    unmark = body.is_transfer is False or (
        body.is_transfer is None
        and category is not None
        and category != TRANSFER_CATEGORY
        and was_transfer
    )
    if mark:
        update_kwargs["is_transfer"] = True
        if not was_transfer:
            # An existing transfer keeps how it became one (match, pattern...).
            update_kwargs["transfer_source"] = "manual"
        # The category always follows the flag, whatever was sent with it.
        update_kwargs["category"] = TRANSFER_CATEGORY
        if not was_transfer:
            # No counterpart exists to link to, so the link fields stay empty.
            update_kwargs["transfer_link_type"] = None
            update_kwargs["transfer_link_id"] = None
    elif unmark:
        update_kwargs["is_transfer"] = False
        update_kwargs["transfer_link_type"] = None
        update_kwargs["transfer_link_id"] = None
        update_kwargs["transfer_source"] = "rejected"
        if existing["category"] == TRANSFER_CATEGORY:
            # A flag-less row must not keep the "Transfer" category.
            update_kwargs.setdefault("category", "uncategorized")
        db.reset_transfer_counterpart(spending_id)

    db.update_spending_transaction(spending_id, **update_kwargs)
    updated = db.get_spending_transaction(spending_id)
    return {
        "id": spending_id,
        "category": updated["category"],
        "is_transfer": bool(updated["is_transfer"]),
        "transfer_source": updated["transfer_source"],
    }


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
    result = settle_transfers(db, ids)
    return {
        "transfers_linked": result["linked"],
        "transfers_flagged": result["flagged"],
    }


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
    fits = _category_fits(db)
    updated = 0
    for row in uncategorized:
        category = _apply_rules(
            row["description"],
            rules,
            row["amount"],
            fits,
            merchant=row.get("merchant"),
            portfolio_id=row["portfolio_id"],
        )
        if category != "uncategorized":
            if db.update_spending_transaction(row["id"], category=category):
                updated += 1
    return {"recategorized": updated}


def _validate_rule_conditions(
    db,
    portfolio_id: Optional[int],
    min_amount: Optional[float],
    max_amount: Optional[float],
) -> None:
    """400 on an unknown or non-bank account, or an inverted amount range."""
    if portfolio_id is not None:
        portfolio = db.get_portfolio(portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=400, detail="Unknown account for this rule")
        if portfolio.get("account_type") != "bank":
            raise HTTPException(
                status_code=400,
                detail="Rules only apply to bank accounts, not this account type",
            )
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(
            status_code=400, detail="Minimum amount is above maximum amount"
        )


_TRANSFER_RULE_DETAIL = (
    'Rules can\'t file rows as "Transfer". '
    'Add the text to "My own accounts" instead.'
)


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
    if category == TRANSFER_CATEGORY:
        raise HTTPException(status_code=400, detail=_TRANSFER_RULE_DETAIL)
    _validate_rule_conditions(db, body.portfolio_id, body.min_amount, body.max_amount)
    conditions = dict(
        portfolio_id=body.portfolio_id,
        amount_sign=body.amount_sign,
        min_amount=body.min_amount,
        max_amount=body.max_amount,
    )
    if db.find_duplicate_spending_rule(pattern, category, **conditions):
        raise HTTPException(
            status_code=409,
            detail=f"A rule with pattern '{pattern}' and category '{category}' and the same conditions already exists",
        )
    rule_id = db.create_spending_rule(
        pattern=pattern, category=category, priority=body.priority, **conditions
    )
    return SpendingRuleResponse(**db.get_spending_rule(rule_id))


@router.post("/rules/preview", response_model=RulePreviewResponse)
async def preview_rule(
    body: RulePreviewBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rows a rule would match, without saving it.

    Counts rows this rule matches on its own; a higher-priority rule could
    still claim some of them first on a rescan. With ``category`` given, rows
    whose sign that category can't hold are left out, as a rescan would.
    Transfers are never counted.
    """
    pattern = body.pattern.strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="Pattern cannot be empty")
    rule = {
        "pattern": pattern,
        "portfolio_id": body.portfolio_id,
        "amount_sign": body.amount_sign,
        "min_amount": body.min_amount,
        "max_amount": body.max_amount,
    }
    candidates = db.list_spending_transactions(
        category="uncategorized" if body.only_uncategorized else None,
        portfolio_id=body.portfolio_id,
        is_transfer=False,
    )
    excluded = set(body.exclude_ids)
    fits = _category_fits(db)
    matches = [
        r
        for r in candidates
        if r["id"] not in excluded
        and rule_matches(
            rule, r["description"], r.get("merchant"), r["amount"], r["portfolio_id"]
        )
        and (not body.category or fits(body.category, r["amount"]))
    ]
    return RulePreviewResponse(
        match_count=len(matches),
        sample=[
            SpendingTransactionResponse(**{**r, "is_transfer": bool(r["is_transfer"])})
            for r in matches[:10]
        ],
    )


@router.put("/rules/{rule_id}", response_model=SpendingRuleResponse)
async def update_rule(
    rule_id: int,
    body: SpendingRuleUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit a rule. Pattern/category/priority change when given; a condition
    field present in the body is set, and present-as-null clears it."""
    existing = db.get_spending_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    sent = body.model_fields_set
    update_kwargs: dict = {}
    if body.pattern is not None:
        pattern = body.pattern.strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="Pattern cannot be empty")
        update_kwargs["pattern"] = pattern
    if body.category is not None:
        category = body.category.strip()
        if not category:
            raise HTTPException(status_code=400, detail="Category cannot be empty")
        if category == TRANSFER_CATEGORY:
            raise HTTPException(status_code=400, detail=_TRANSFER_RULE_DETAIL)
        update_kwargs["category"] = category
    if body.priority is not None:
        update_kwargs["priority"] = body.priority
    for field in ("portfolio_id", "amount_sign", "min_amount", "max_amount"):
        if field in sent:
            update_kwargs[field] = getattr(body, field)
    if not update_kwargs:
        raise HTTPException(status_code=400, detail="Nothing to update")
    merged = {**existing, **update_kwargs}
    _validate_rule_conditions(
        db, merged["portfolio_id"], merged["min_amount"], merged["max_amount"]
    )
    # Only an edit that changes the rule's identity (pattern, category or a
    # condition; priority isn't part of it) can create a duplicate. Excluding
    # this rule alone isn't enough: with two pre-existing identical rules a
    # priority-only edit would still find the twin and 409.
    identity_fields = (
        "pattern",
        "category",
        "portfolio_id",
        "amount_sign",
        "min_amount",
        "max_amount",
    )
    identity_changed = any(merged[f] != existing[f] for f in identity_fields)
    if identity_changed:
        duplicate = db.find_duplicate_spending_rule(
            merged["pattern"],
            merged["category"],
            portfolio_id=merged["portfolio_id"],
            amount_sign=merged["amount_sign"],
            min_amount=merged["min_amount"],
            max_amount=merged["max_amount"],
            exclude_id=rule_id,
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A rule with pattern '{merged['pattern']}' and category "
                    f"'{merged['category']}' and the same conditions already exists"
                ),
            )
    db.update_spending_rule(rule_id, **update_kwargs)
    return SpendingRuleResponse(**db.get_spending_rule(rule_id))


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
    parent = db.find_spending_category_by_name(body.parent_name.strip())
    if not parent:
        raise HTTPException(
            status_code=400, detail=f"Parent category '{body.parent_name}' not found"
        )
    category_id = db.create_spending_category(name, parent_id=parent["id"])
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
    if TRANSFER_CATEGORY in (old_name.strip(), new_name):
        raise HTTPException(
            status_code=400,
            detail=(
                f'"{TRANSFER_CATEGORY}" is reserved for moves between your own '
                "accounts and can't be renamed or merged into."
            ),
        )
    result = db.rename_spending_category(old_name, new_name)
    return {"old_name": old_name, "new_name": new_name, **result}


@router.get("/categories/tree", response_model=List[dict])
async def list_categories_tree(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Every category with its tree position, for building paths/indented views."""
    return db.list_spending_categories_tree()


class SpendingCategoryBreakdownChild(BaseModel):
    name: str
    amount_eur: float
    has_children: bool


class SpendingCategoryBreakdownResponse(BaseModel):
    parent: str
    children: List[SpendingCategoryBreakdownChild]


@router.get("/categories/breakdown", response_model=SpendingCategoryBreakdownResponse)
def get_spending_category_breakdown(
    parent: str = "Spend",
    days: int = 30,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Immediate children of a category tree node, each with its full
    subtree total for the period (EUR, today's rate) and a has_children
    flag, so the caller knows whether the next click should drill further
    or show transactions. Powers the Spending page's Analytics tab
    category chart drill-down. 400 if `parent` has no children (including
    an unknown `parent` name).

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    from datetime import date, timedelta

    tree = db.list_spending_categories_tree()
    children_by_parent = build_children_index(tree)

    direct_children = children_by_parent.get(parent, [])
    if not direct_children:
        raise HTTPException(status_code=400, detail=f"'{parent}' has no sub-categories")

    start_date = (date.today() - timedelta(days=days)).isoformat()
    rows = db.list_spending_transactions(start_date=start_date, is_transfer=False)

    result = []
    for child in direct_children:
        names = set(subtree_names(children_by_parent, child["name"]))
        amount_eur = sum(
            abs(float(r["amount"]) * _fx(r.get("currency", "EUR")))
            for r in rows
            if r["category"] in names
        )
        result.append(
            SpendingCategoryBreakdownChild(
                name=child["name"],
                amount_eur=round(amount_eur, 2),
                has_children=bool(children_by_parent.get(child["name"])),
            )
        )

    # Spend's tree-defined children don't include "uncategorized" spend --
    # the Dashboard's Spending card (still reading /summary's
    # by_category_eur) does include it, so surface it here too at the
    # actual root (not when drilling into a sub-group) to keep the two
    # surfaces in sync.
    if parent == "Spend":
        uncategorized_amount = sum(
            abs(float(r["amount"]) * _fx(r.get("currency", "EUR")))
            for r in rows
            if r["category"] == "uncategorized"
            and float(r["amount"]) * _fx(r.get("currency", "EUR")) < 0
        )
        if uncategorized_amount > 0:
            result.append(
                SpendingCategoryBreakdownChild(
                    name="uncategorized",
                    amount_eur=round(uncategorized_amount, 2),
                    has_children=False,
                )
            )

    result.sort(key=lambda c: -c.amount_eur)
    return SpendingCategoryBreakdownResponse(parent=parent, children=result)


@router.put("/categories/{name}/parent", response_model=dict)
async def reparent_category(
    name: str,
    body: SpendingCategoryReparentBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Move a category under a different parent."""
    try:
        db.reparent_spending_category(name, body.new_parent_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": name, "new_parent_name": body.new_parent_name}


class CategoryEssentialityBody(BaseModel):
    """``fixed`` | ``flexible`` | ``optional``, or null to clear the mark."""

    essentiality: Optional[Literal["fixed", "flexible", "optional"]] = None


_ESSENTIALITY_PROMPT = """Classify household spending categories by how \
easily the spending can be reduced if income falls.

Levels:
- fixed: contractual or unavoidable, hard to change within months (mortgage,
  rent, insurance, school fees, taxes, loan payments, utilities' standing part)
- flexible: needed, but the amount can be reduced (groceries, energy use,
  transport, clothing, household goods, health)
- optional: can stop without hardship (restaurants, travel, hobbies,
  subscriptions, entertainment, gifts)

Categories (name, with its parent category in brackets):
{categories}

Return ONLY a JSON array, one object per category, in the same order:
[{{"name": "<exact name>", "essentiality": "fixed|flexible|optional", \
"reason": "<max 12 words>"}}]
"""


def spend_categories_without_essentiality(tree: List[dict]) -> List[dict]:
    """Non-root categories under the Spend root that carry no mark."""
    by_name = {row["name"]: row for row in tree}

    def root_of(row: dict) -> Optional[str]:
        for _ in range(100):
            if row.get("is_root"):
                return row["name"]
            row = by_name.get(row.get("parent_name") or "")
            if row is None:
                return None
        return None

    return [
        row
        for row in tree
        if not row.get("is_root")
        and not row.get("essentiality")
        and root_of(row) == "Spend"
    ]


def parse_essentiality_suggestions(data: object, names: set[str]) -> List[dict]:
    """Keep well-formed suggestions for known categories; drop the rest."""
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        name, level = item.get("name"), item.get("essentiality")
        if name in names and level in ("fixed", "flexible", "optional"):
            out.append(
                {
                    "name": name,
                    "essentiality": level,
                    "reason": str(item.get("reason") or "")[:200],
                }
            )
    return out


@router.put("/categories/{name}/essentiality", response_model=dict)
def set_category_essentiality(
    name: str,
    body: CategoryEssentialityBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Mark a category fixed, flexible or optional (null clears the mark).

    The mark drives the what-if scenarios' "what to cut first"; a category
    without one inherits its nearest marked ancestor's.
    """
    row = db.find_spending_category_by_name(name)
    if not row:
        raise HTTPException(status_code=404, detail=f"Category '{name}' not found")
    if row.get("is_root"):
        raise HTTPException(
            status_code=400, detail="A root category cannot carry a mark"
        )
    db.set_spending_category_essentiality(name, body.essentiality)
    return {"name": name, "essentiality": body.essentiality}


@router.post("/categories/suggest-essentiality", response_model=dict)
def suggest_category_essentiality(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """LLM suggestions for Spend categories that have no mark yet.

    Nothing is saved: the user accepts suggestions one by one or all at once.
    Only category names go to the LLM, never transactions.
    """
    pending = spend_categories_without_essentiality(db.list_spending_categories_tree())
    if not pending:
        return {"suggestions": []}
    listing = "\n".join(
        f"- {row['name']} ({row.get('parent_name') or 'Spend'})" for row in pending
    )
    try:
        llm = get_llm_client(tier="fast")
        text = llm.generate(_ESSENTIALITY_PROMPT.format(categories=listing)).strip()
        if text.startswith("```"):
            text = "\n".join(
                ln for ln in text.split("\n") if not ln.strip().startswith("```")
            )
        data = json.loads(text)
    except Exception as e:
        logger.warning("Essentiality suggestion LLM call failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Suggestion failed: {e}",
        )
    return {
        "suggestions": parse_essentiality_suggestions(
            data, {row["name"] for row in pending}
        )
    }


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

    tree = {row["name"]: row for row in db.list_spending_categories_tree()}

    def _rollup_key(category: str) -> str:
        node = tree.get(category)
        if node is None:
            return category
        for _ in range(100):
            if node["parent_name"] is None or node["parent_name"] == "Spend":
                return node["name"]
            node = tree.get(node["parent_name"])
            if node is None:
                return category
        # Defensive guard: a real category tree is never this deep. If we
        # get here, a cycle exists somewhere upstream -- bail out safely
        # with the original category rather than looping forever.
        return category

    for r in rows:
        amt_eur = float(r["amount"]) * _fx(r.get("currency", "EUR"))
        if r["is_transfer"]:
            transferred_eur += abs(amt_eur)
            continue
        if amt_eur < 0:
            spent_eur += abs(amt_eur)
            key = _rollup_key(r["category"])
            by_category_eur[key] = by_category_eur.get(key, 0.0) + abs(amt_eur)
        else:
            income_eur += amt_eur

    return SpendingSummaryResponse(
        spent_eur=round(spent_eur, 2),
        income_eur=round(income_eur, 2),
        transferred_eur=round(transferred_eur, 2),
        by_category_eur={k: round(v, 2) for k, v in by_category_eur.items()},
    )


@router.get("/recurring", response_model=RecurringResponse)
def list_recurring(
    portfolio_id: Optional[int] = None,
    include_ended: bool = False,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Recurring charges (subscriptions, bills) detected from bank outflows.

    Totals are EUR at today's rate (same convention as /summary) over
    series that haven't ended. Ended series are hidden unless asked for.
    A plain ``def`` because the FX lookup can do blocking network I/O.
    """
    names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    items: List[RecurringItem] = []
    for s in load_recurring(db, portfolio_id=portfolio_id):
        if s.status == "ended" and not include_ended:
            continue
        items.append(
            RecurringItem(
                **vars(s),
                account_name=names.get(s.portfolio_id),
                annual_amount_eur=round(s.annual_amount * _fx(s.currency), 2),
            )
        )
    annual = round(sum(i.annual_amount_eur for i in items if i.status != "ended"), 2)
    return RecurringResponse(
        items=items, monthly_total_eur=round(annual / 12, 2), annual_total_eur=annual
    )


class SpendingTrendMonth(BaseModel):
    month: str
    spent_eur: float
    income_eur: float
    net_eur: float


@router.get("/trend", response_model=List[SpendingTrendMonth])
def get_spending_trend(
    months: int = 12,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Monthly spent/income/net for the last N calendar months, EUR-converted
    at today's rate (same convention as /summary). Transfers excluded.
    Zero-filled for months with no matching rows, oldest month first.
    Powers the Spending page's Analytics tab trend chart.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    from datetime import date

    today = date.today()
    first_of_this_month = today.replace(day=1)
    start_year = first_of_this_month.year
    start_month_num = first_of_this_month.month - (months - 1)
    while start_month_num <= 0:
        start_month_num += 12
        start_year -= 1
    start_date = date(start_year, start_month_num, 1).isoformat()

    rows = db.list_spending_transactions(start_date=start_date, is_transfer=False)
    buckets: dict = {}
    for r in rows:
        key = r["date"][:7]
        amt_eur = float(r["amount"]) * _fx(r.get("currency", "EUR"))
        b = buckets.setdefault(key, {"spent": 0.0, "income": 0.0})
        if amt_eur < 0:
            b["spent"] += abs(amt_eur)
        else:
            b["income"] += amt_eur

    result = []
    y, m = start_year, start_month_num
    for _ in range(months):
        key = f"{y:04d}-{m:02d}"
        b = buckets.get(key, {"spent": 0.0, "income": 0.0})
        result.append(
            SpendingTrendMonth(
                month=key,
                spent_eur=round(b["spent"], 2),
                income_eur=round(b["income"], 2),
                net_eur=round(b["income"] - b["spent"], 2),
            )
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


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
numeric card/transaction-reference number (e.g. "123456789012EXAMPLE
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
        # Rules can't file rows as "Transfer", so never suggest it.
        if category.lower() == TRANSFER_CATEGORY.lower():
            category = "uncategorized"
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
        llm = get_llm_client(tier="fast")
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
