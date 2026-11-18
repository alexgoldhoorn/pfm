"""
Spending Router for Portfolio Management API

Bank-statement transaction import, rule-based categorization, and
inter-account transfer detection. Kept separate from the investment import
router (imports.py) — spending rows have no asset/quantity/price and use
different dedup + transfer semantics.
"""

import json
import logging
import threading
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
from portf_manager.services import (
    cashflow_forecast,
    planned_items,
    spending_insights,
    spending_review,
    spending_uncategorized,
    spending_year,
)
from portf_manager.services.spending_rules import (
    TRANSFER_CATEGORY,
    pick_category,
    rule_matches,
)
from portf_manager.llm_client import get_llm_client

from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager
from portf_manager.database import Database

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
    # False when transfer settling failed after the rows were saved (the rows
    # stay; ``settle_error`` says why). Always True on a normal save.
    transfers_settled: bool = True
    settle_error: Optional[str] = None


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
    one_off: bool = False


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
    # One-off rows still count in totals; averages and baselines skip them.
    one_off: Optional[bool] = None


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
    # Stored resolution (status, since, note) and whether a charge came back
    # after it; ``status`` is cancelled / paid_elsewhere while it is live.
    resolution: Optional[dict] = None
    resolution_stale: bool = False


class RecurringResponse(BaseModel):
    items: List[RecurringItem]
    monthly_total_eur: float
    # Active + missed + paid elsewhere (cancelled and ended are out).
    annual_total_eur: float
    # The paid-elsewhere share of the total, at its last typical amount.
    paid_elsewhere_annual_eur: float = 0.0
    # What the cancellations save a year (not part of ``annual_total_eur``).
    cancelled_annual_eur: float = 0.0


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


# One statement import at a time: the unattended job and a person may post
# together, and two interleaved imports would both pass the duplicate check.
_IMPORT_LOCK = threading.Lock()


class _ImportAborted(Exception):
    """A strict save failed; nothing from the call is left stored."""


def _parse_statement(
    file_bytes: bytes, fail_status: int = status.HTTP_422_UNPROCESSABLE_ENTITY
):
    """Parse a statement file with the format auto-detection.

    Args:
        file_bytes: The raw upload.
        fail_status: HTTP status for a file that cannot be parsed.

    Returns:
        The parser result (``rows``, ``skipped``).

    Raises:
        HTTPException: ``fail_status`` (default 422) when the file cannot be
            parsed.
    """
    try:
        # AEB43 exports commonly contain raw Latin-1 bytes (accented
        # characters); fall back when the file isn't valid UTF-8.
        try:
            content = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = file_bytes.decode("latin-1")
        if looks_like_aeb43(content):
            return parse_aeb43(content)
        return parse_generic_bank_csv(content)
    except Exception as e:
        logger.exception("Error parsing bank statement")
        raise HTTPException(
            status_code=fail_status,
            detail=f"Failed to parse file: {str(e)}",
        )


def _preview_rows(db, portfolio_id: int, result) -> tuple:
    """Rule-categorised preview rows for a parsed statement.

    Duplicates are looked up against the rows stored before the import, never
    within the file itself.

    Returns:
        ``(rows, duplicate_count)``.
    """
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
    return rows, dup_count


def _balance_breaks(db, portfolio_id: int, result) -> tuple:
    """Stated-balance checks for a parsed statement.

    Warn (never block) when stated balances don't add up: rows missing from
    this file, or between the previous import and this one.

    Returns:
        ``(balance_rows_checked, breaks)``.
    """
    balance_rows = [r for r in result.rows if r.balance is not None]
    breaks = []
    if balance_rows:
        first_date = min(str(r.date)[:10] for r in result.rows)
        prior = db.get_latest_bank_balance_before(portfolio_id, first_date)
        breaks = find_balance_breaks(
            result.rows, opening_balance=prior["balance"] if prior else None
        )
    return len(balance_rows), breaks


@router.post("/upload", response_model=SpendingUploadResponse)
async def upload_bank_statement(
    file: UploadFile = File(..., description="Bank statement CSV"),
    account_portfolio_id: Optional[int] = Form(None),
    account_name: Optional[str] = Form(None),
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Parse a bank statement CSV and return a rule-categorized preview. No DB write."""
    portfolio_id = _resolve_account(db, account_portfolio_id, account_name)

    result = _parse_statement(await file.read())
    rows, dup_count = _preview_rows(db, portfolio_id, result)
    checked, breaks = _balance_breaks(db, portfolio_id, result)

    skipped = [{"row": row, "reason": reason} for row, reason in result.skipped]
    return SpendingUploadResponse(
        account_portfolio_id=portfolio_id,
        rows=rows,
        skipped_count=len(skipped),
        skipped=skipped,
        duplicate_count=dup_count,
        balance_rows_checked=checked,
        balance_breaks=[BalanceBreakOut(**vars(b)) for b in breaks],
    )


def _save_rows(
    db,
    account_portfolio_id: int,
    rows,
    duplicate_action: str,
    strict: bool = False,
) -> SpendingSaveResponse:
    """Save spending rows, honoring ``duplicate_action``, then link transfers.

    Args:
        db: Database.
        account_portfolio_id: The bank account the rows belong to.
        rows: Objects with ``date``, ``description``, ``amount``, ``currency``,
            ``category`` and ``balance``.
        duplicate_action: ``skip``, ``overwrite`` or ``add``.
        strict: All or nothing. A row that fails to save deletes the rows this
            call already created and raises ``_ImportAborted``; a transfer
            settling failure keeps the rows and is reported in the response
            (``transfers_settled`` False). Meant for ``duplicate_action``
            ``add``, where every saved row is a new one.

    Raises:
        _ImportAborted: ``strict`` and a row failed.
    """
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

    for row in rows:
        try:
            existing = db.find_duplicate_spending_transaction(
                portfolio_id=account_portfolio_id,
                date=row.date,
                amount=row.amount,
                description=row.description,
            )
            if existing:
                if duplicate_action == "skip":
                    duplicates_skipped += 1
                    continue
                if duplicate_action == "overwrite":
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
                portfolio_id=account_portfolio_id,
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
            if strict:
                _rollback_import(db, saved_ids, f"{row.date} {row.description}: {e}")
            errors.append(f"{row.date} {row.description}: {str(e)}")
            logger.warning(f"Failed to save spending row: {e}")

    transfers_linked = 0
    transfers_settled = True
    settle_error = None
    if saved_ids:
        if strict:
            try:
                transfers_linked = settle_transfers(db, saved_ids)["linked"]
            except Exception as e:
                # the rows are saved and valid: keep them and say so
                transfers_settled = False
                settle_error = str(e)
                logger.warning(f"Transfer settling failed after import: {e}")
        else:
            transfers_linked = settle_transfers(db, saved_ids)["linked"]

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
        p = db.get_portfolio(account_portfolio_id)
        account_name = p.get("name") if p else None
        latest = db.get_latest_bank_balance(account_portfolio_id)
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
        transfers_settled=transfers_settled,
        settle_error=settle_error,
    )


def _rollback_import(db, saved_ids: List[int], reason: str) -> None:
    """Delete the rows an aborted strict import created, then raise.

    Raises:
        _ImportAborted: always, naming the failed row and any row that could
            not be removed.
    """
    stuck = []
    for sid in reversed(saved_ids):
        try:
            db.delete_spending_transaction(sid)
        except Exception as e:
            logger.error(f"Could not roll back spending row {sid}: {e}")
            stuck.append(sid)
    message = f"Import aborted, nothing was stored. Failed row {reason}"
    if stuck:
        message = (
            f"Import aborted but rows {stuck} could not be removed again. "
            f"Failed row {reason}"
        )
    raise _ImportAborted(message)


@router.post("/save", response_model=SpendingSaveResponse)
async def save_spending_transactions(
    body: SpendingSaveRequest,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save previewed spending rows, honoring duplicate_action, then auto-link transfers."""
    return _save_rows(db, body.account_portfolio_id, body.rows, body.duplicate_action)


@router.post("/import-file", response_model=dict)
async def import_statement_file(
    file: UploadFile = File(..., description="Bank statement file"),
    portfolio_id: int = Form(...),
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Parse, de-duplicate, categorise and save a statement in one call.

    The same parse, preview and save paths as ``/upload`` + ``/save``: rows
    already stored before the import are dropped (never rows repeated inside
    the file), rules and transfer matching apply as on a normal import.
    All or nothing: a bank-account, parse or input problem is a 400 and a row
    that fails to save is a 500; in both cases nothing from the call stays
    stored. Calls are serialised, so two posts never interleave. If transfer
    settling fails after the rows are saved, the rows stay and the response
    carries ``transfers_settled: false`` and a warning.

    ``balance_check`` compares the file's stated balances with each other and
    with the balance stored before the file's first row: ``ok``, ``mismatch``
    (rows may be missing; the import still goes ahead) or ``unknown`` (the
    file has no balance column). An unattended caller should alert on
    ``mismatch``.

    Returns:
        ``{parsed, imported, duplicates, uncategorized, date_from, date_to,
        data_through, balance_check, transfers_settled, warnings}``.
    """
    portfolio = db.get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("account_type") != "bank":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Portfolio {portfolio_id} is not a bank account",
        )
    content = await file.read()
    with _IMPORT_LOCK:
        return _import_statement(db, portfolio_id, content)


def _import_statement(db, portfolio_id: int, content: bytes) -> dict:
    """The body of ``/import-file``, run under the import lock."""
    result = _parse_statement(content, fail_status=status.HTTP_400_BAD_REQUEST)
    if not result.rows:
        reasons = sorted({str(reason) for _row, reason in result.skipped})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No rows found in the file"
            + (": " + "; ".join(reasons[:3]) if reasons else ""),
        )

    preview, dup_count = _preview_rows(db, portfolio_id, result)
    checked, breaks = _balance_breaks(db, portfolio_id, result)
    # "add": the duplicates were already dropped against what was stored
    # before, so twins inside the file are both kept.
    try:
        saved = _save_rows(
            db,
            portfolio_id,
            [r for r in preview if not r.is_duplicate],
            "add",
            strict=True,
        )
    except _ImportAborted as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )

    warnings: List[str] = []
    if result.skipped:
        warnings.append(f"{len(result.skipped)} line(s) in the file were skipped")
    if breaks:
        warnings.append(
            f"{len(breaks)} stated balance(s) do not add up: rows may be missing"
        )
    if not saved.transfers_settled:
        warnings.append(
            "Rows were saved but own-account transfers were not matched "
            f"({saved.settle_error}): run the transfer rescan."
        )
    data_through = None
    try:
        last = (db.get_portfolio_date_ranges().get(portfolio_id) or {}).get(
            "last_spending_date"
        )
        data_through = str(last)[:10] if last else None
    except Exception as e:
        warnings.append(f"data_through not available: {e}")
    if breaks:
        balance_check = "mismatch"
    elif checked:
        balance_check = "ok"
    else:
        balance_check = "unknown"
    return {
        "parsed": len(result.rows),
        "imported": saved.saved,
        "duplicates": dup_count,
        "uncategorized": saved.uncategorized,
        "date_from": saved.date_from,
        "date_to": saved.date_to,
        "data_through": data_through,
        "balance_check": balance_check,
        "transfers_settled": saved.transfers_settled,
        "warnings": warnings,
    }


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
    one_off: Optional[bool] = None,
    amount_sign: Optional[str] = None,
    min_abs_amount: Optional[float] = Query(default=None, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = "date",
    sort_dir: str = "desc",
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """List spending transactions with optional filters, paginated and sorted.

    `categories` is a repeatable query param (`?categories=A&categories=B`)
    matched with SQL `IN`; omit it entirely for no category filter (an
    empty/absent list means unfiltered, not "match nothing"). `amount_sign`
    filters to negative-only ("negative", i.e. expenses) or positive-only
    ("positive", i.e. income); `min_abs_amount` additionally requires
    `ABS(amount) >= min_abs_amount`, so it composes naturally with either
    sign or neither. `one_off=true|false` keeps only rows marked (or not
    marked) as one-off. `transfer_linked=true` keeps rows linked to a counterpart,
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
        one_off=one_off,
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
            SpendingTransactionResponse(
                **{
                    **r,
                    "is_transfer": bool(r["is_transfer"]),
                    "one_off": bool(r.get("one_off")),
                }
            )
            for r in rows
        ],
        total=total,
    )


@router.get("/own-account-patterns", response_model=dict)
async def get_own_account_patterns(
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
    """Text patterns that mark a row as a transfer to the user's own account."""
    return {"patterns": load_patterns(db)}


@router.put("/own-account-patterns", response_model=dict)
async def put_own_account_patterns(
    body: OwnAccountPatternsBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
    """Replace the pattern list, then flag every row that now matches."""
    try:
        stored = save_patterns(db, body.patterns)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"patterns": stored, "flagged": flag_own_account_rows(db)}


@router.post("/own-account-patterns/preview", response_model=dict)
async def preview_own_account_patterns(
    body: OwnAccountPreviewBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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


class PlannedItemCreate(BaseModel):
    """A planned item: signed amount, negative out and positive in."""

    date: str
    description: str
    amount: float
    kind: str
    currency: str = "EUR"
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None


class PlannedItemUpdate(BaseModel):
    """A partial update; an explicit null clears ``portfolio_id`` / ``notes``."""

    date: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    kind: Optional[str] = None
    currency: Optional[str] = None
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None
    done: Optional[bool] = None


def _check_planned_portfolio(db, portfolio_id: Optional[int]) -> None:
    """400 unless *portfolio_id* is None or a bank account."""
    if portfolio_id is None:
        return
    p = db.get_portfolio(portfolio_id)
    if not p or p.get("account_type") != "bank":
        raise HTTPException(
            status_code=400, detail="portfolio_id must be a bank account"
        )


def _planned_out(item: dict) -> dict:
    """A planned item as the API returns it."""
    return {**item, "done": bool(item["done"])}


@router.get("/planned", response_model=List[dict])
def list_planned_items(
    include_done: bool = False,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Planned items by date; done ones only with ``include_done=true``."""
    return [_planned_out(i) for i in db.list_planned_items(include_done=include_done)]


@router.post("/planned", response_model=dict, status_code=201)
def create_planned_item(
    body: PlannedItemCreate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Add a future one-off expense, saving or income to the forecast."""
    try:
        fields = planned_items.validate_item(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _check_planned_portfolio(db, fields.get("portfolio_id"))
    item_id = db.create_planned_item(
        date=fields["date"],
        description=fields["description"],
        amount=fields["amount"],
        kind=fields["kind"],
        currency=fields.get("currency", "EUR"),
        portfolio_id=fields.get("portfolio_id"),
        notes=fields.get("notes"),
    )
    return _planned_out(db.get_planned_item(item_id))


@router.put("/planned/{item_id}", response_model=dict)
def update_planned_item(
    item_id: int,
    body: PlannedItemUpdate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Partial update; ``done: true`` stops the forecast projecting the item."""
    existing = db.get_planned_item(item_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Planned item not found")
    sent = body.model_dump(exclude_unset=True)
    # null is only meaningful for the two clearable columns
    for key in ("date", "description", "amount", "kind", "currency", "done"):
        if key in sent and sent[key] is None:
            raise HTTPException(status_code=400, detail=f"{key} must not be null")
    try:
        fields = planned_items.validate_item(sent, existing)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _check_planned_portfolio(db, fields.get("portfolio_id"))
    if "done" in fields:
        fields["done"] = int(bool(fields["done"]))
    if fields:
        db.update_planned_item(item_id, **fields)
    return _planned_out(db.get_planned_item(item_id))


@router.delete("/planned/{item_id}", response_model=dict)
def delete_planned_item(
    item_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Delete a planned item."""
    if not db.delete_planned_item(item_id):
        raise HTTPException(status_code=404, detail="Planned item not found")
    return {"deleted": True, "id": item_id}


@router.put("/{spending_id}", response_model=dict)
async def update_spending_category(
    spending_id: int,
    body: CategoryUpdateBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    if body.category is None and body.is_transfer is None and body.one_off is None:
        raise HTTPException(
            status_code=400,
            detail="Nothing to update: pass category, is_transfer or one_off",
        )

    update_kwargs: dict = {}
    if body.one_off is not None:
        update_kwargs["one_off"] = 1 if body.one_off else 0
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
        "one_off": bool(updated["one_off"]),
    }


@router.delete("/{spending_id}", response_model=dict)
async def delete_spending(
    spending_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
    """Delete a spending transaction (hard delete)."""
    if not db.delete_spending_transaction(spending_id):
        raise HTTPException(status_code=404, detail="Spending transaction not found")
    return {"deleted": True, "id": spending_id}


@router.post("/rescan-transfers", response_model=dict)
async def rescan_transfers(
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
    """Re-apply current spending_rules to still-uncategorized rows.

    Scoped to `body.ids` when provided (only those rows, and only the ones
    among them still "uncategorized"); every uncategorized row otherwise.
    Never touches a row that already has a non-"uncategorized" category —
    covers the case where rules are added/edited after rows were imported.
    """
    return {"recategorized": _rescan_uncategorized(db, body.ids if body else None)}


def _rescan_uncategorized(db, ids: Optional[List[int]] = None) -> int:
    """Re-apply the rules to still-uncategorized rows (all, or only ``ids``).

    Returns:
        How many rows got a category.
    """
    rules = db.list_spending_rules()
    uncategorized = db.list_spending_transactions(category="uncategorized")
    if ids is not None:
        id_set = set(ids)
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
    return updated


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
async def list_rules(
    db: Database = Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """List all spending category rules."""
    return [SpendingRuleResponse(**r) for r in db.list_spending_rules()]


@router.post("/rules", response_model=SpendingRuleResponse, status_code=201)
async def create_rule(
    body: SpendingRuleBody,
    db: Database = Depends(get_database),
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
    db: Database = Depends(get_database),
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
    db: Database = Depends(get_database),
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
    rule_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
    """Delete a spending category rule."""
    if not db.delete_spending_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True, "id": rule_id}


@router.get("/categories", response_model=List[str])
async def list_categories(
    db: Database = Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """List every known spending category (used + explicitly registered, deduplicated)."""
    return db.list_spending_categories()


@router.post("/categories", response_model=dict, status_code=201)
async def create_category(
    body: SpendingCategoryBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    db: Database = Depends(get_database), api_key_info: dict = Depends(_auth)
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
    db: Database = Depends(get_database),
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
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
) -> dict:
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
    db: Database = Depends(get_database),
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
    include_resolved: bool = True,
    sort: Literal["status", "yearly_cost"] = "status",
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Recurring charges (subscriptions, bills) detected from bank outflows.

    Totals are EUR at today's rate (same convention as /summary). Ended series
    are hidden unless asked for. A series marked cancelled or paid another way
    (``PUT /recurring/resolution``) shows with that ``status`` unless
    ``include_resolved=false``; ``annual_total_eur`` counts it only when it is
    paid elsewhere (still a cost), ``cancelled_annual_eur`` is what the
    cancellations save and ``paid_elsewhere_annual_eur`` the paid-elsewhere
    part, both always over every resolved series. ``sort=status`` (default)
    is missed, active, paid elsewhere, cancelled, ended, each by yearly cost;
    ``sort=yearly_cost`` is the subscriptions view, dearest first.
    A plain ``def`` because the FX lookup can do blocking network I/O.
    """
    names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    items: List[RecurringItem] = []
    paid_elsewhere = cancelled = 0.0
    for s in load_recurring(db, portfolio_id=portfolio_id):
        annual_eur = round(s.annual_amount * _fx(s.currency), 2)
        if s.status == "paid_elsewhere":
            paid_elsewhere += annual_eur
        elif s.status == "cancelled":
            cancelled += annual_eur
        if s.status == "ended" and not include_ended:
            continue
        if s.status in ("cancelled", "paid_elsewhere") and not include_resolved:
            continue
        items.append(
            RecurringItem(
                **vars(s),
                account_name=names.get(s.portfolio_id),
                annual_amount_eur=annual_eur,
            )
        )
    if sort == "yearly_cost":
        items.sort(key=lambda i: -i.annual_amount_eur)
    annual = round(
        sum(
            i.annual_amount_eur for i in items if i.status not in ("ended", "cancelled")
        ),
        2,
    )
    return RecurringResponse(
        items=items,
        monthly_total_eur=round(annual / 12, 2),
        annual_total_eur=annual,
        paid_elsewhere_annual_eur=round(paid_elsewhere, 2),
        cancelled_annual_eur=round(cancelled, 2),
    )


class RecurringResolutionIn(BaseModel):
    """Key of a series (account + merchant), plus the resolution to store."""

    portfolio_id: int
    merchant: str = Field(..., min_length=1, max_length=200)
    status: Optional[str] = None
    note: Optional[str] = Field(None, max_length=500)


def _find_series(db: Database, portfolio_id: int, merchant: str):
    key = merchant.strip().upper()
    for s in load_recurring(db, portfolio_id=portfolio_id):
        if s.merchant.strip().upper() == key:
            return s
    return None


@router.put("/recurring/resolution")
def set_recurring_resolution(
    body: RecurringResolutionIn,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Mark a recurring series ``cancelled`` or ``paid_elsewhere``.

    ``since`` is set here from the series' last charge: a later charge makes
    the mark stale (``resolution_stale``) and raises an Action Item. The mark
    drops the series from the forecast's known bills, the budget pace and the
    monthly review's new/ended lists, and ``cancelled`` leaves the annual
    total. 400 for another status, 404 when no such series is detected.
    """
    if body.status not in ("cancelled", "paid_elsewhere"):
        raise HTTPException(
            status_code=400, detail="status must be 'cancelled' or 'paid_elsewhere'"
        )
    series = _find_series(db, body.portfolio_id, body.merchant)
    if series is None:
        raise HTTPException(
            status_code=404,
            detail="No recurring series for that account and merchant",
        )
    note = (body.note or "").strip() or None
    db.set_recurring_resolution(
        series.portfolio_id, series.merchant, body.status, series.last_date, note
    )
    return {
        "portfolio_id": series.portfolio_id,
        "merchant": series.merchant,
        "status": body.status,
        "since": series.last_date,
        "note": note,
    }


@router.delete("/recurring/resolution")
def clear_recurring_resolution(
    portfolio_id: int,
    merchant: str,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Clear a series' resolution; the key goes in the query string.

    404 when the series has none.
    """
    key = merchant.strip().upper()
    stored = [
        r["merchant"]
        for r in db.list_recurring_resolutions(portfolio_id)
        if r["merchant"].strip().upper() == key
    ]
    cleared = False
    for merchant in stored:
        cleared = db.delete_recurring_resolution(portfolio_id, merchant) or cleared
    if not cleared:
        raise HTTPException(status_code=404, detail="No resolution for that series")
    return {"portfolio_id": portfolio_id, "merchant": merchant, "cleared": True}


class SpendingTrendMonth(BaseModel):
    month: str
    spent_eur: float
    income_eur: float
    net_eur: float
    # Spending in the month that rows marked one-off account for (already
    # inside spent_eur: one-offs count in every total).
    one_off_eur: float = 0.0


@router.get("/trend", response_model=List[SpendingTrendMonth])
def get_spending_trend(
    months: int = 12,
    db: Database = Depends(get_database),
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
        b = buckets.setdefault(key, {"spent": 0.0, "income": 0.0, "one_off": 0.0})
        if amt_eur < 0:
            b["spent"] += abs(amt_eur)
            if r.get("one_off"):
                b["one_off"] += abs(amt_eur)
        else:
            b["income"] += amt_eur

    result = []
    y, m = start_year, start_month_num
    for _ in range(months):
        key = f"{y:04d}-{m:02d}"
        b = buckets.get(key, {"spent": 0.0, "income": 0.0, "one_off": 0.0})
        result.append(
            SpendingTrendMonth(
                month=key,
                spent_eur=round(b["spent"], 2),
                income_eur=round(b["income"], 2),
                net_eur=round(b["income"] - b["spent"], 2),
                one_off_eur=round(b["one_off"], 2),
            )
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def _category_tree(db) -> Dict[str, dict]:
    return {row["name"]: row for row in db.list_spending_categories_tree()}


@router.get("/compare", response_model=dict)
def compare_spending(
    month: Optional[str] = Query(default=None, pattern=_MONTH_PATTERN),
    baseline_months: int = Query(default=6, ge=1, le=24),
    group_by: Literal["category", "merchant"] = "category",
    portfolio_id: Optional[int] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """One month against its normal and against the same month a year ago.

    ``month`` defaults to the last complete month. The baseline is the
    average of up to ``baseline_months`` complete months before it that have
    imports (rows marked one-off left out; they still count in the actual).
    Only a complete month gets deltas: a month in progress, a month the data
    stops short of, or a month with no imports reports ``status`` and null
    deltas instead of a flattering number. Transfers and the Invest / House
    purchase categories are not spending (the latter is ``savings_eur``).
    Plain ``def`` because of the FX lookups.
    """
    from datetime import date

    today = date.today()
    if month is None:
        month = spending_insights.previous_month(today)
    # far enough back for the same month last year and for every baseline month
    start = f"{spending_insights.shift_month(month, -max(12, baseline_months))}-01"
    rows = spending_insights.load_rows(
        db, _fx, start_date=start, portfolio_id=portfolio_id
    )
    try:
        return spending_insights.compare_month(
            rows, _category_tree(db), month, today, baseline_months, group_by
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/anomalies", response_model=dict)
def get_spending_anomalies(
    days: int = Query(default=60, ge=1, le=365),
    portfolio_id: Optional[int] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Unusual charges in the last ``days`` days (deterministic, no LLM).

    Kinds: ``large_charge``, ``new_merchant``, ``category_spike`` and
    ``possible_duplicate``; each item has a stable ``id``. Rows marked
    one-off are never flagged. Plain ``def`` because of the FX lookups.
    """
    from datetime import date

    return spending_insights.load_anomalies(
        db, _fx, date.today(), days, portfolio_id=portfolio_id
    )


@router.get("/tiers", response_model=List[dict])
def get_spending_tiers(
    months: int = Query(default=12, ge=1, le=60),
    portfolio_id: Optional[int] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Per month, spending split by fixed / flexible / optional / unclassified.

    Uses the inherited category essentiality; an unmarked category stays
    ``unclassified``. ``total_eur + savings_eur`` equals ``/trend`` ``spent_eur``
    for the same month. Oldest month first, the month in progress last.
    """
    from datetime import date

    today = date.today()
    first = spending_insights.shift_month(today.strftime("%Y-%m"), -(months - 1))
    rows = spending_insights.load_rows(
        db, _fx, start_date=f"{first}-01", portfolio_id=portfolio_id
    )
    return spending_insights.tier_breakdown(rows, _category_tree(db), today, months)


@router.get("/forecast", response_model=dict)
def get_cashflow_forecast(
    months: int = Query(
        default=cashflow_forecast.DEFAULT_FORECAST_MONTHS,
        ge=1,
        le=cashflow_forecast.MAX_FORECAST_MONTHS,
    ),
    portfolio_id: Optional[int] = None,
    extra_amount: Optional[float] = Query(default=None, gt=0),
    extra_date: Optional[str] = None,
    extra_kind: str = "spending",
    include_tax: bool = True,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Where this month ends and the next ``months`` months (default 12).

    Deterministic, no LLM. ``this_month`` is month-to-date plus the recurring
    charges still to arrive plus an everyday-spending estimate for the days
    left (measured from the date the data runs to, ``data_through``, not from
    today), with a low/high range from the spread of past months.
    ``next_months`` places recurring charges (quarterly and yearly included)
    on their due months next to the income and everyday-spending baseline and
    projects the bank balance from the latest stored balances; a month whose
    balance falls below zero is flagged. ``known_bills`` lists every expected
    charge. Recurring charges never overlap the baseline, rows marked one-off
    are left out of it, and money moved to Invest / House purchase is
    ``savings_eur``, not spending. A source that cannot be read becomes a
    warning and ``null`` figures, never a zero or a 5xx. Plain ``def`` because
    of the FX lookups.

    Open planned items are placed on their dates (``planned_items`` lists them
    with a status) and the estimated quarterly tax payments are added as
    ``tax_payments`` / ``tax_eur`` when work-income merchants are ticked in the
    allocator settings; ``include_tax=false`` leaves the tax out.
    ``extra_amount`` (a positive magnitude), ``extra_date`` (``YYYY-MM``, taken
    as the 15th, or ``YYYY-MM-DD``) and ``extra_kind`` add one unsaved item and
    return a ``what_if`` block with the lowest balance with and without it.
    """
    from datetime import date

    what_if = None
    if extra_amount is not None or extra_date is not None:
        if extra_amount is None or not extra_date:
            raise HTTPException(
                status_code=400,
                detail="extra_amount and extra_date must be given together",
            )
        if extra_kind not in planned_items.KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"extra_kind must be one of {list(planned_items.KINDS)}",
            )
        day = extra_date if len(extra_date) != 7 else f"{extra_date}-15"
        try:
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="extra_date must be YYYY-MM or YYYY-MM-DD"
            )
        what_if = {"amount": extra_amount, "date": day, "kind": extra_kind}

    return cashflow_forecast.load_forecast(
        db,
        _fx,
        date.today(),
        months,
        portfolio_id=portfolio_id,
        include_tax=include_tax,
        what_if=what_if,
    )


@router.get("/year", response_model=dict)
def get_spending_year(
    year: Optional[int] = Query(default=None, ge=2000),
    depth: int = Query(default=1, ge=1, le=2),
    portfolio_id: Optional[int] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """One year of income and spending against the year before.

    Like for like: only months that are complete and have imports in both
    years are summed, on both sides. For a year in progress the previous year
    is cut to the same months (``compared_through``) and its full total is
    given too. ``months`` lists all 12 months of both years; a month without
    imports is ``has_imports: false`` (unknown, never zero) and named in a
    warning. ``categories`` are the top-level Spend categories (children with
    ``depth=2``), ``movers`` the biggest increases and decreases by category,
    ``merchants`` the largest by spend, ``one_offs`` the rows marked one-off
    (they count in the totals). Plain ``def`` because of the FX lookups.
    """
    from datetime import date

    today = date.today()
    target = year or today.year
    rows = spending_insights.load_rows(
        db, _fx, start_date=f"{target - 1}-01-01", portfolio_id=portfolio_id
    )
    try:
        return spending_year.build_year(
            rows, _category_tree(db), today, year=year, depth=depth
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/uncategorized-queue", response_model=dict)
def get_uncategorized_queue(
    limit: int = Query(default=spending_uncategorized.DEFAULT_LIMIT, ge=1, le=200),
    months: int = Query(default=spending_uncategorized.DEFAULT_MONTHS, ge=1, le=60),
    portfolio_id: Optional[int] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Uncategorised rows grouped by merchant, biggest money first.

    Non-transfer rows with category ``uncategorized`` in the last ``months``
    calendar months, grouped per merchant and direction (rows without a
    merchant by their normalised description). Each group carries a
    ``proposed_pattern`` for a rule and the ids to classify it with
    ``POST /uncategorized-queue/classify``. ``share_of_spending_pct`` and
    ``last_complete_month`` count outflows only. Plain ``def`` because of the
    FX lookups.
    """
    from datetime import date

    today = date.today()
    first = spending_insights.shift_month(today.strftime("%Y-%m"), -max(months, 13))
    rows = spending_insights.load_rows(
        db, _fx, start_date=f"{first}-01", portfolio_id=portfolio_id
    )
    return spending_uncategorized.build_queue(
        rows, _category_tree(db), today, limit=limit, months=months
    )


class ClassifyGroupBody(BaseModel):
    transaction_ids: List[int]
    category: str
    create_rule: bool = False
    pattern: Optional[str] = None


@router.post("/uncategorized-queue/classify", response_model=dict)
def classify_uncategorized_group(
    body: ClassifyGroupBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Give a group of uncategorised rows one category in a single request.

    A thin wrapper over the row edit and the rules: every id must exist, not be
    a transfer and be able to hold the category's sign (same check as editing
    one row), otherwise nothing is written (400). The rule is created or
    reused before any row is updated, so a rule failure (500) changes nothing. Rows that already have a
    category are left alone and counted in ``skipped``. With ``create_rule`` a
    rule for ``pattern`` is added (an identical rule is reused), then the rules
    are re-applied to the other still-uncategorised rows. ``Transfer`` cannot
    be chosen here.

    Returns:
        ``{updated, skipped, rule_id, rule_created, recategorized_by_rule}``.
    """
    category = body.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")
    if category == TRANSFER_CATEGORY:
        raise HTTPException(status_code=400, detail=_TRANSFER_RULE_DETAIL)
    if not body.transaction_ids:
        raise HTTPException(status_code=400, detail="transaction_ids cannot be empty")
    pattern = (body.pattern or "").strip()
    if body.create_rule and not pattern:
        raise HTTPException(
            status_code=400, detail="pattern is required to create a rule"
        )
    root = db.get_spending_category_root(category)
    todo: List[int] = []
    skipped = 0
    for tid in dict.fromkeys(body.transaction_ids):
        row = db.get_spending_transaction(tid)
        if not row:
            raise HTTPException(status_code=400, detail=f"Transaction {tid} not found")
        if row.get("is_transfer"):
            raise HTTPException(
                status_code=400, detail=f"Transaction {tid} is a transfer"
            )
        if not _sign_matches_root(root, row["amount"]):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{category}' is an {root} category; transaction {tid} is "
                    f"{'a debit' if row['amount'] < 0 else 'a credit'}"
                ),
            )
        if (row.get("category") or "uncategorized") != "uncategorized":
            skipped += 1
            continue
        todo.append(tid)

    # the rule first: if it cannot be created or reused, no row has changed
    rule_id = None
    rule_created = False
    if body.create_rule:
        try:
            existing = db.find_duplicate_spending_rule(pattern, category)
            if existing:
                rule_id = existing["id"]
            else:
                rule_id = db.create_spending_rule(pattern=pattern, category=category)
                rule_created = True
        except Exception as e:
            logger.warning(f"Classify: rule could not be created: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"The rule could not be created, nothing was changed: {e}",
            )
    updated = sum(
        1 for tid in todo if db.update_spending_transaction(tid, category=category)
    )
    rescanned = _rescan_uncategorized(db) if body.create_rule else 0
    return {
        "updated": updated,
        "skipped": skipped,
        "rule_id": rule_id,
        "rule_created": rule_created,
        "recategorized_by_rule": rescanned,
    }


@router.get("/review", response_model=dict)
def get_spending_review(
    month: Optional[str] = Query(default=None, pattern=_MONTH_PATTERN),
    portfolio_id: Optional[int] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """One month's review in a single payload (deterministic, no LLM).

    ``month`` defaults to the last complete month. Assembled only from the
    other spending services: totals against the baseline and last year, the
    biggest movers by category and merchant, the month's unusual charges, the
    fixed / flexible / optional split, recurring charges that are new, ended,
    missed or changed price, the active budget's result for the month (null
    without one), next month's forecast line with the upcoming known bills,
    and data freshness per account. Each part is built on its own: one that
    fails is null with a warning in ``warnings``, never a 5xx. A month in
    progress, a month the data stops short of and a month with no imports
    carry no deltas. Plain ``def`` because of the FX lookups.
    """
    from datetime import date

    try:
        return spending_review.build_review(
            db, _fx, date.today(), month, portfolio_id=portfolio_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    db: Database = Depends(get_database),
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
