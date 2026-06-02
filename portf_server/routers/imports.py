"""
File Import Router for Portfolio Management API

Accepts broker statement files (CSV/XLSX) and returns a parsed preview.
Provides a separate save endpoint to commit the confirmed transactions.

Supported brokers:
  - indexacapital  — semicolon CSV (IndexaCapital exports)
  - coinbase        — comma CSV (Coinbase Advanced Trade exports)
  - pdt             — XLSX (Portfolio Dividend Tracker template)
"""

import logging
import tempfile
import os
from typing import List, Optional

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

SUPPORTED_BROKERS = ["indexacapital", "coinbase", "pdt", "bookings"]


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


class PreviewBooking(BaseModel):
    broker: Optional[str] = None
    date: str
    action: str  # "Deposit" or "Withdrawal"
    amount: float
    currency: str


class UploadPreviewResponse(BaseModel):
    broker: str
    transactions: List[PreviewTransaction]
    bookings: List[PreviewBooking] = []
    skipped_count: int
    skipped: List[dict]


class SaveRequest(BaseModel):
    transactions: List[PreviewTransaction]
    bookings: List[PreviewBooking] = []
    portfolio_id: Optional[int] = None
    force: bool = False  # if True, import even if a duplicate is detected


class SaveResponse(BaseModel):
    saved: int
    saved_bookings: int = 0
    duplicates_skipped: int = 0
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


def _parse_indexacapital(content: str) -> tuple[List[PreviewTransaction], List[dict]]:
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
    return previews, skipped


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
        )
        for tx in result.importable
    ]
    skipped = [{"type": t, "reason": r} for t, r in result.skipped]
    return previews, skipped


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


@router.post("/upload", response_model=UploadPreviewResponse)
async def upload_broker_file(
    broker: str = Form(..., description=f"Broker type: {', '.join(SUPPORTED_BROKERS)}"),
    file: UploadFile = File(..., description="Broker statement file (CSV or XLSX)"),
    api_key_info: dict = Depends(_auth),
):
    """
    Parse a broker statement file and return a preview of extracted transactions.
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
            previews, skipped = _parse_indexacapital(content)
            bookings: List[PreviewBooking] = []
        elif broker == "coinbase":
            content = file_bytes.decode("utf-8-sig")
            previews, skipped = _parse_coinbase(content)
            bookings = []
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

    return UploadPreviewResponse(
        broker=broker,
        transactions=previews,
        bookings=bookings,
        skipped_count=len(skipped),
        skipped=skipped,
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
    errors: List[str] = []

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

            # Duplicate check — skip unless caller explicitly sets force=True
            if not body.force:
                existing = db.find_duplicate_transaction(
                    asset_id=asset_id,
                    transaction_type=tx.tx_type,
                    quantity=tx.quantity,
                    price=price,
                    transaction_date=tx.date,
                    portfolio_id=tx_portfolio_id,
                )
                if existing:
                    duplicates_skipped += 1
                    errors.append(
                        f"DUPLICATE: {symbol} {tx.tx_type} {tx.quantity}@{price} on {tx.date} "
                        f"(existing id={existing['id']})"
                    )
                    continue

            total_amount = tx.quantity * price
            if tx.tx_type == "sell":
                total_amount -= fees
            else:
                total_amount += fees

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
        errors=errors,
    )
