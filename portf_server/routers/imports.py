"""
File Import Router for Portfolio Management API

Accepts broker statement files (CSV/XLSX) and returns a parsed preview.
Provides a separate save endpoint to commit the confirmed transactions.

Supported brokers:
  - indexacapital  — semicolon CSV (IndexaCapital exports)
  - coinbase        — comma CSV (Coinbase Advanced Trade exports)
  - pdt             — XLSX (Portfolio Dividend Tracker template)
"""

import csv
import logging
import re
import tempfile
import os
from io import StringIO
from typing import List, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel

from portf_manager.currency_utils import normalize_gbx_amounts
from portf_manager.parsers.indexacapital_csv_parser import parse_indexacapital_csv
from portf_manager.parsers.coinbase_csv_parser import parse_coinbase_csv
from portf_manager.parsers.bookings_csv_parser import parse_bookings_csv
from portf_manager.parsers.myinvestor_csv_parser import parse_myinvestor_csv
from portf_manager.parsers.mintos_csv_parser import (
    parse_mintos_csv,
    MINTOS_SYMBOL,
    MINTOS_NAME,
)
from portf_manager.parsers.pdt_xlsx_parser import (
    PDTXLSXParser,
    _detect_asset_type,
    _pdt_action_to_tx_type,
)

from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

router = APIRouter()
logger = logging.getLogger(__name__)

SUPPORTED_BROKERS = [
    "indexacapital",
    "coinbase",
    "pdt",
    "bookings",
    "myinvestor",
    "mintos",
]


# ---------------------------------------------------------------------------
# Shared schemas
# ---------------------------------------------------------------------------


class PreviewTransaction(BaseModel):
    symbol: str
    name: str
    asset_type: str
    tx_type: str
    date: str
    quantity: float
    price: float
    currency: str
    fees: float = 0.0
    tax: float = 0.0
    exchange: Optional[str] = None
    notes: str = ""
    broker: Optional[str] = None
    # Set by the preview endpoint when a matching transaction already exists.
    is_duplicate: bool = False


class PreviewBooking(BaseModel):
    broker: Optional[str] = None
    date: str
    action: str  # "Deposit" or "Withdrawal"
    amount: float
    currency: str
    is_duplicate: bool = False


class UploadPreviewResponse(BaseModel):
    broker: str
    transactions: List[PreviewTransaction]
    bookings: List[PreviewBooking] = []
    skipped_count: int
    skipped: List[dict]
    duplicate_count: int = 0


# What to do when an imported row matches an existing one.
DuplicateAction = Literal["skip", "add", "overwrite"]


class SaveRequest(BaseModel):
    transactions: List[PreviewTransaction]
    bookings: List[PreviewBooking] = []
    portfolio_id: Optional[int] = None
    # skip = ignore duplicates (default), add = import anyway, overwrite = update
    # the existing row. `force` is kept for backwards compatibility (== "add").
    duplicate_action: DuplicateAction = "skip"
    force: bool = False


class SaveResponse(BaseModel):
    saved: int
    saved_bookings: int = 0
    duplicates_skipped: int = 0
    overwritten: int = 0
    errors: List[str]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _eur_amount(raw: str) -> float:
    """Parse a Spanish-formatted EUR amount like '-1.500,20 €' → -1500.20."""
    s = re.sub(r"[^0-9,.\-]", "", (raw or "").strip())
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    return float(s) if s not in ("", "-") else 0.0


def _parse_indexacapital_movimientos(
    content: str,
) -> tuple[List[PreviewBooking], List[dict]]:
    """IndexaCapital 'Movimientos' cash statement (Fecha;Movimiento;Importe;Saldo):
    SEPA transfers → deposit/withdrawal bookings. Fund subscriptions carry no
    unit/fund detail (the funds export has that), so they're skipped."""
    bookings: List[PreviewBooking] = []
    skipped: List[dict] = []
    reader = csv.reader(StringIO(content.strip()), delimiter=";")
    rows = list(reader)
    for i, row in enumerate(rows[1:], start=2):  # skip header
        if len(row) < 3:
            continue
        date, movimiento, importe_raw = row[0].strip(), row[1].strip(), row[2]
        if "SEPA" in movimiento.upper() or "TRANSFEREN" in movimiento.upper():
            amt = _eur_amount(importe_raw)
            bookings.append(
                PreviewBooking(
                    broker="Indexa Capital",
                    date=date[:10],
                    action="Deposit" if amt >= 0 else "Withdrawal",
                    amount=abs(amt),
                    currency="EUR",
                )
            )
        else:
            skipped.append(
                {"type": f"Row {i}", "reason": f"not a cash transfer: {movimiento}"}
            )
    return bookings, skipped


def _parse_indexacapital(
    content: str,
) -> tuple[List[PreviewTransaction], List[PreviewBooking], List[dict]]:
    # Auto-detect the cash "Movimientos" export vs the ISIN trades export.
    first = content.splitlines()[0] if content.strip() else ""
    if "Movimiento" in first and "Saldo" in first:
        bookings, skipped = _parse_indexacapital_movimientos(content)
        return [], bookings, skipped

    result = parse_indexacapital_csv(content)
    previews = [
        PreviewTransaction(
            symbol=tx.symbol,
            name=tx.asset_name,
            asset_type="etf",
            tx_type=tx.tx_type,
            date=tx.date,
            quantity=tx.quantity,
            price=tx.price,
            currency=tx.currency or "EUR",
            fees=tx.fees,
            notes=tx.raw_text or "",
        )
        for tx in result.importable
    ]
    skipped = [{"type": t, "reason": r} for t, r in result.skipped]
    return previews, [], skipped


def _parse_coinbase(content: str) -> tuple[List[PreviewTransaction], List[dict]]:
    result = parse_coinbase_csv(content)
    previews = [
        PreviewTransaction(
            symbol=tx.symbol,
            name=tx.asset_name,
            asset_type="crypto",
            tx_type=tx.tx_type,
            date=tx.date,
            quantity=tx.quantity,
            price=tx.price,
            currency=tx.currency or "USD",
            fees=tx.fees,
            notes=tx.raw_text or "",
            # Tag with the broker so the save step resolves the Coinbase
            # portfolio (get_or_create_portfolio). Without this the rows save
            # with portfolio_id=NULL and vanish under the broker filter — the
            # other parsers all set this; Coinbase was the omission.
            broker="Coinbase",
        )
        for tx in result.importable
    ]
    skipped = [{"type": t, "reason": r} for t, r in result.skipped]
    return previews, skipped


def _parse_myinvestor(
    content: str,
) -> tuple[List[PreviewTransaction], List[PreviewBooking], List[dict]]:
    """MyInvestor 'Movimientos' → trades + dividends + deposit bookings.

    Buy/sell rows are approximate (no ISIN, EUR amount only, no fee detail), so
    they're surfaced for the user to review rather than trusted blindly.
    """
    result = parse_myinvestor_csv(content)
    previews = [
        PreviewTransaction(
            symbol=tx.symbol,
            name=tx.asset_name,
            asset_type=_detect_asset_type(tx.asset_name, ""),
            tx_type=tx.tx_type,
            date=tx.date,
            quantity=tx.quantity,
            price=tx.price,
            currency=tx.currency or "EUR",
            fees=tx.fees,
            broker="MyInvestor",
            notes=(tx.raw_text or "")
            + (
                " · review: MyInvestor gives no ISIN/fees"
                if tx.tx_type in ("buy", "sell")
                else ""
            ),
        )
        for tx in result.transactions
    ]
    bookings = [PreviewBooking(**bk) for bk in result.bookings]
    skipped = [{"type": t, "reason": r} for t, r in result.skipped]
    return previews, bookings, skipped


def _parse_mintos(
    content: str,
) -> tuple[List[PreviewTransaction], List[PreviewBooking], List[dict]]:
    """Mintos statement → monthly aggregated P2P interest (savings-base income).

    The thousands of loan/principal/secondary-market rows are ignored (they net
    out); only interest + withholding are kept, summed per month and booked as
    'interest' transactions against a synthetic MINTOS asset.
    """
    result = parse_mintos_csv(content)
    previews = [
        PreviewTransaction(
            symbol=MINTOS_SYMBOL,
            name=MINTOS_NAME,
            asset_type="bond",
            tx_type="interest",
            date=e["date"],
            quantity=1.0,
            price=e["amount"],
            tax=e["tax"],
            currency=e.get("currency", "EUR"),
            broker="Mintos",
            notes=f"Mintos P2P interest for {e['date'][:7]} (aggregated from {e['count']} rows)",
        )
        for e in result.interest
    ]
    # Surface the ignored internal activity as informational "skipped" rows.
    skipped = [
        {
            "type": ptype,
            "reason": f"internal P2P activity ({n} rows, {eur:.2f} EUR) — not imported",
        }
        for ptype, (n, eur) in sorted(
            result.ignored_summary.items(), key=lambda x: -x[1][0]
        )
    ]
    return previews, [], skipped


def _parse_pdt(
    file_bytes: bytes,
) -> tuple[List[PreviewTransaction], List[PreviewBooking], List[dict]]:
    previews: List[PreviewTransaction] = []
    bookings: List[PreviewBooking] = []
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = PDTXLSXParser().parse(tmp_path)
    finally:
        os.unlink(tmp_path)

    for tx in result.transactions:
        tx_type = _pdt_action_to_tx_type(tx.action)
        if tx_type is None:
            continue
        symbol = tx.search if tx.search else tx.name[:20]
        previews.append(
            PreviewTransaction(
                symbol=symbol.upper(),
                name=tx.name,
                asset_type=_detect_asset_type(tx.name, tx.pdt_type),
                tx_type=tx_type,
                date=tx.date.isoformat(),
                quantity=tx.amount,
                price=tx.price,
                currency=tx.price_currency or "EUR",
                fees=tx.costs or 0.0,
                tax=tx.tax or 0.0,
                exchange=tx.exchange or None,
                notes="Imported from PDT XLSX",
                broker=tx.broker or None,
            )
        )

    for div in result.dividends:
        symbol = div.search if div.search else div.name[:20]
        previews.append(
            PreviewTransaction(
                symbol=symbol.upper(),
                name=div.name,
                asset_type=_detect_asset_type(div.name, div.pdt_type),
                tx_type="dividend",
                date=div.date.isoformat(),
                quantity=1.0,
                price=div.amount,
                currency=div.amount_currency or "EUR",
                fees=div.costs or 0.0,
                tax=div.tax or 0.0,
                exchange=div.exchange or None,
                notes="Dividend imported from PDT XLSX",
                broker=div.broker or None,
            )
        )

    for bk in result.bookings:
        bookings.append(
            PreviewBooking(
                broker=bk.broker or None,
                date=bk.date.isoformat(),
                action=bk.action,
                amount=bk.amount,
                currency=bk.currency,
            )
        )

    skipped = [{"sheet": s, "reason": r} for s, r in result.skipped]
    return previews, bookings, skipped


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _flag_duplicates(
    db,
    previews: List[PreviewTransaction],
    bookings: List[PreviewBooking],
    portfolio_id: Optional[int] = None,
) -> int:
    """Mark preview rows that already exist in the DB (best-effort). Returns count."""
    n = 0
    for tx in previews:
        try:
            symbol = tx.symbol.upper()
            asset = db.get_asset_by_symbol(symbol)
            if not asset:
                continue  # new asset → can't be a duplicate yet
            price, _t, _fees, _cur = normalize_gbx_amounts(
                symbol, tx.price, None, tx.fees, tx.currency
            )
            pid = portfolio_id
            if tx.broker:
                existing_pf = db.get_portfolio_by_name(tx.broker)
                pid = existing_pf["id"] if existing_pf else None
            if db.find_duplicate_transaction(
                asset_id=asset["id"],
                transaction_type=tx.tx_type,
                quantity=tx.quantity,
                price=price,
                transaction_date=tx.date,
                portfolio_id=pid,
            ):
                tx.is_duplicate = True
                n += 1
        except Exception:
            continue
    for bk in bookings:
        try:
            pid = portfolio_id
            if bk.broker:
                existing_pf = db.get_portfolio_by_name(bk.broker)
                pid = existing_pf["id"] if existing_pf else None
            if db.find_duplicate_booking(
                date=bk.date,
                action=bk.action,
                amount=bk.amount,
                currency=bk.currency,
                portfolio_id=pid,
            ):
                bk.is_duplicate = True
                n += 1
        except Exception:
            continue
    return n


@router.post("/upload", response_model=UploadPreviewResponse)
async def upload_broker_file(
    broker: str = Form(..., description=f"Broker type: {', '.join(SUPPORTED_BROKERS)}"),
    file: UploadFile = File(..., description="Broker statement file (CSV or XLSX)"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """
    Parse a broker statement file and return a preview of extracted transactions.
    Rows that already exist in the DB are flagged (``is_duplicate``).
    No data is saved — call POST /save to commit.
    """
    broker = broker.lower().strip()
    if broker not in SUPPORTED_BROKERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported broker '{broker}'. Supported: {', '.join(SUPPORTED_BROKERS)}",
        )

    file_bytes = await file.read()

    try:
        if broker == "indexacapital":
            content = file_bytes.decode("utf-8-sig")
            previews, bookings, skipped = _parse_indexacapital(content)
        elif broker == "coinbase":
            content = file_bytes.decode("utf-8-sig")
            previews, skipped = _parse_coinbase(content)
            bookings = []
        elif broker == "myinvestor":
            content = file_bytes.decode("utf-8-sig")
            previews, bookings, skipped = _parse_myinvestor(content)
        elif broker == "mintos":
            content = file_bytes.decode("utf-8-sig")
            previews, bookings, skipped = _parse_mintos(content)
        elif broker == "pdt":
            previews, bookings, skipped = _parse_pdt(file_bytes)
        elif broker == "bookings":
            content = file_bytes.decode("utf-8-sig")
            previews = []
            result = parse_bookings_csv(content)
            bookings = [PreviewBooking(**bk) for bk in result.bookings]
            skipped = [{"row": r, "reason": reason} for r, reason in result.skipped]
        else:
            raise HTTPException(status_code=500, detail="Unreachable broker branch")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error parsing {broker} file")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file: {str(e)}",
        )

    dup_count = _flag_duplicates(db, previews, bookings)

    return UploadPreviewResponse(
        broker=broker,
        transactions=previews,
        bookings=bookings,
        skipped_count=len(skipped),
        skipped=skipped,
        duplicate_count=dup_count,
    )


@router.post("/save", response_model=SaveResponse)
async def save_imported_transactions(
    body: SaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """
    Save a list of preview transactions (and optional bookings) to the database.
    Assets are created automatically if they don't exist yet.
    """
    saved = 0
    saved_bookings = 0
    duplicates_skipped = 0
    overwritten = 0
    errors: List[str] = []

    # force=True is the legacy way to say "import duplicates anyway".
    action = "add" if body.force else body.duplicate_action

    for tx in body.transactions:
        try:
            symbol = tx.symbol.upper()

            # Normalize GBX (pence) → GBP for UK-listed symbols so cost basis
            # isn't stored 100× too high for GBX-quoted UK stocks.
            price, _t, fees, currency = normalize_gbx_amounts(
                symbol, tx.price, None, tx.fees, tx.currency
            )

            # Find or create asset
            asset = db.get_asset_by_symbol(symbol)
            if asset:
                asset_id = asset["id"]
            else:
                asset_id = db.create_asset(
                    symbol=symbol,
                    name=tx.name,
                    asset_type=tx.asset_type,
                    exchange=tx.exchange,
                    currency=currency,
                    description="Auto-created from broker file import",
                )

            # Resolve portfolio: per-transaction broker overrides the request-level portfolio_id
            tx_portfolio_id = body.portfolio_id
            if tx.broker:
                tx_portfolio_id = db.get_or_create_portfolio(
                    tx.broker, base_currency=currency or "EUR"
                )

            total_amount = tx.quantity * price
            if tx.tx_type == "sell":
                total_amount -= fees
            else:
                total_amount += fees

            # Duplicate handling: skip (default) / add anyway / overwrite.
            existing = db.find_duplicate_transaction(
                asset_id=asset_id,
                transaction_type=tx.tx_type,
                quantity=tx.quantity,
                price=price,
                transaction_date=tx.date,
                portfolio_id=tx_portfolio_id,
            )
            if existing:
                if action == "skip":
                    duplicates_skipped += 1
                    errors.append(
                        f"DUPLICATE: {symbol} {tx.tx_type} {tx.quantity}@{price} "
                        f"on {tx.date} (existing id={existing['id']})"
                    )
                    continue
                if action == "overwrite":
                    db.update_transaction(
                        existing["id"],
                        total_amount=total_amount,
                        fees=fees,
                        tax=tx.tax,
                        currency=currency,
                        description=tx.notes or None,
                    )
                    overwritten += 1
                    continue
                # action == "add": fall through and insert a second copy

            db.create_transaction(
                asset_id=asset_id,
                transaction_type=tx.tx_type,
                quantity=tx.quantity,
                price=price,
                total_amount=total_amount,
                fees=fees,
                tax=tx.tax,
                currency=currency,
                transaction_date=tx.date,
                portfolio_id=tx_portfolio_id,
                description=tx.notes or None,
            )
            saved += 1

        except Exception as e:
            errors.append(f"{tx.symbol} ({tx.date}): {str(e)}")
            logger.warning(f"Failed to save transaction {tx.symbol}: {e}")

    for bk in body.bookings:
        try:
            bk_portfolio_id = body.portfolio_id
            if bk.broker:
                bk_portfolio_id = db.get_or_create_portfolio(
                    bk.broker, base_currency=bk.currency or "EUR"
                )

            # Bookings dedup: skip exact matches unless importing anyway.
            # (Overwrite is a no-op for a booking — the match is already exact.)
            if action != "add" and db.find_duplicate_booking(
                date=bk.date,
                action=bk.action,
                amount=bk.amount,
                currency=bk.currency,
                portfolio_id=bk_portfolio_id,
            ):
                duplicates_skipped += 1
                errors.append(
                    f"DUPLICATE: {bk.action} {bk.amount} {bk.currency} on {bk.date}"
                )
                continue

            db.create_booking(
                date=bk.date,
                action=bk.action,
                amount=bk.amount,
                currency=bk.currency,
                portfolio_id=bk_portfolio_id,
            )
            saved_bookings += 1

        except Exception as e:
            errors.append(f"Booking {bk.action} {bk.date}: {str(e)}")
            logger.warning(f"Failed to save booking {bk.date}: {e}")

    return SaveResponse(
        saved=saved,
        saved_bookings=saved_bookings,
        duplicates_skipped=duplicates_skipped,
        overwritten=overwritten,
        errors=errors,
    )


@router.post("/check-duplicates")
async def check_duplicates(
    body: SaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Flag which of the supplied transactions/bookings already exist (no write).

    Lets the text/LLM import preview show duplicates before saving, the same way
    the file-upload preview does.
    """
    n = _flag_duplicates(db, body.transactions, body.bookings, body.portfolio_id)
    return {
        "transactions": body.transactions,
        "bookings": body.bookings,
        "duplicate_count": n,
    }
