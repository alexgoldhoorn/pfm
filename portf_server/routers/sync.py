"""
PDT Google Sheets Sync Router.

Endpoints:
  GET  /api/v1/sync/pdt-config   – return configured spreadsheet ID / service account status
  PUT  /api/v1/sync/pdt-config   – persist spreadsheet ID to DB (app_settings)
  POST /api/v1/sync/pdt-pull     – read from Google Sheet → save to DB
  POST /api/v1/sync/pdt-push     – write DB data → Google Sheet
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database
from portf_manager.parsers.pdt_xlsx_parser import (
    _detect_asset_type,
    _pdt_action_to_tx_type,
)
from portf_manager.currency_utils import normalize_gbx_amounts

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _google_service_account_file() -> str:
    return os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json")


def _default_spreadsheet_id() -> Optional[str]:
    return os.getenv("GOOGLE_SPREADSHEET_ID") or None


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _google_error_detail(e: Exception) -> str:
    """Extract a human-readable string from a googleapiclient HttpError.

    HttpError.__str__ wraps the message in angle brackets which become invisible
    when rendered as innerHTML.  This pulls the plain message out of the JSON body.
    """
    try:
        from googleapiclient.errors import HttpError as _HttpError
        import json as _json

        if isinstance(e, _HttpError):
            content = _json.loads(e.content or b"{}").get("error", {})
            msg = content.get("message") or f"HTTP {e.status_code}"
            return f"Google API {e.status_code}: {msg}"
    except Exception:
        pass
    return str(e) or repr(e)


def _get_sync(spreadsheet_id: str):
    """Return a PDTSheetsSync instance; raises 503 if Google libs missing."""
    try:
        from portf_manager.parsers.pdt_sheets_sync import PDTSheetsSync
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Google API libraries not installed: {e}",
        )
    return PDTSheetsSync(spreadsheet_id, _google_service_account_file())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SyncConfigResponse(BaseModel):
    service_account_configured: bool
    service_account_email: Optional[str] = None
    default_spreadsheet_id: Optional[str] = None


class SyncConfigUpdate(BaseModel):
    spreadsheet_id: str


class PullResponse(BaseModel):
    spreadsheet_id: str
    imported_transactions: int
    imported_dividends: int
    imported_bookings: int
    skipped: int
    errors: list[str] = []


class PushResponse(BaseModel):
    spreadsheet_id: str
    transactions_written: int
    dividends_written: int
    bookings_written: int
    spreadsheet_url: str


class BackupResponse(BaseModel):
    backup_url: str
    title: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/pdt-config", response_model=SyncConfigResponse)
async def get_sync_config(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Return current Google Sheets sync configuration."""
    sa_file = _google_service_account_file()
    email = None
    configured = os.path.exists(sa_file)
    if configured:
        try:
            import json

            with open(sa_file) as f:
                email = json.load(f).get("client_email")
        except Exception:
            pass
    # DB-stored ID takes precedence over env var
    sheet_id = db.get_setting("google_spreadsheet_id") or _default_spreadsheet_id()
    return SyncConfigResponse(
        service_account_configured=configured,
        service_account_email=email,
        default_spreadsheet_id=sheet_id,
    )


@router.put("/pdt-config", response_model=SyncConfigResponse)
async def update_sync_config(
    body: SyncConfigUpdate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Persist the Google Spreadsheet ID to the database."""
    db.set_setting("google_spreadsheet_id", body.spreadsheet_id.strip())
    sa_file = _google_service_account_file()
    email = None
    configured = os.path.exists(sa_file)
    if configured:
        try:
            import json

            with open(sa_file) as f:
                email = json.load(f).get("client_email")
        except Exception:
            pass
    return SyncConfigResponse(
        service_account_configured=configured,
        service_account_email=email,
        default_spreadsheet_id=body.spreadsheet_id.strip(),
    )


@router.post("/pdt-pull", response_model=PullResponse)
async def pull_from_sheets(
    spreadsheet_id: Optional[str] = Query(
        default=None,
        description="Google Spreadsheet ID (falls back to GOOGLE_SPREADSHEET_ID env var)",
    ),
    portfolio_id: Optional[int] = Query(
        default=None, description="Assign all imported records to this portfolio"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """
    Pull all data from a PDT-format Google Spreadsheet and save it to the DB.
    Transactions, dividends, and bookings are imported; assets and portfolios
    are auto-created where needed.
    """
    sheet_id = (
        spreadsheet_id
        or db.get_setting("google_spreadsheet_id")
        or _default_spreadsheet_id()
    )
    if not sheet_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No spreadsheet_id provided and GOOGLE_SPREADSHEET_ID is not set. "
                "Pass ?spreadsheet_id=<ID> or set the env var."
            ),
        )

    sync = _get_sync(sheet_id)

    try:
        result = sync.pull()
    except Exception as e:
        logger.exception("Google Sheets pull failed")
        raise HTTPException(
            status_code=502, detail=f"Sheets pull failed: {_google_error_detail(e)}"
        )

    saved_tx = saved_div = saved_bk = 0
    errors: list[str] = []

    asset_cache: dict = {}

    def get_or_create_asset(
        symbol: str, name: str, asset_type: str, exchange: str, currency: str
    ) -> int:
        sym = symbol.upper()
        if sym not in asset_cache:
            a = db.get_asset_by_symbol(sym)
            asset_cache[sym] = (
                a["id"]
                if a
                else db.create_asset(
                    symbol=sym,
                    name=name,
                    asset_type=asset_type,
                    exchange=exchange,
                    currency=currency,
                    description="Auto-created from PDT Sheets sync",
                )
            )
        return asset_cache[sym]

    for tx in result.transactions:
        try:
            tx_type = _pdt_action_to_tx_type(tx.action)
            if not tx_type:
                continue
            symbol = tx.search if tx.search else tx.name[:20]
            asset_id = get_or_create_asset(
                symbol,
                tx.name,
                _detect_asset_type(tx.name, tx.pdt_type),
                tx.exchange,
                tx.price_currency or "EUR",
            )
            pid = (
                db.get_or_create_portfolio(tx.broker, tx.price_currency or "EUR")
                if tx.broker
                else portfolio_id
            )
            # Normalize GBX (pence) → GBP for UK-listed symbols
            price, _t, fees, currency = normalize_gbx_amounts(
                symbol, tx.price, None, tx.costs or 0.0, tx.price_currency
            )
            base_amount = tx.amount * price
            total_amount = (
                base_amount - fees if tx_type == "sell" else base_amount + fees
            )
            db.create_transaction(
                asset_id=asset_id,
                transaction_type=tx_type,
                quantity=tx.amount,
                price=price,
                total_amount=total_amount,
                fees=fees,
                tax=tx.tax or 0.0,
                currency=currency,
                transaction_date=tx.date.isoformat(),
                portfolio_id=pid,
                description="Imported from PDT Sheets sync",
            )
            saved_tx += 1
        except Exception as e:
            errors.append(f"TX {tx.search} {tx.date}: {e}")

    for div in result.dividends:
        try:
            symbol = div.search if div.search else div.name[:20]
            asset_id = get_or_create_asset(
                symbol,
                div.name,
                _detect_asset_type(div.name, div.pdt_type),
                div.exchange,
                div.amount_currency or "EUR",
            )
            pid = (
                db.get_or_create_portfolio(div.broker, div.amount_currency or "EUR")
                if div.broker
                else portfolio_id
            )
            db.create_transaction(
                asset_id=asset_id,
                transaction_type="dividend",
                quantity=1.0,
                price=div.amount,
                total_amount=div.amount,
                fees=div.costs or 0.0,
                tax=div.tax or 0.0,
                currency=div.amount_currency,
                transaction_date=div.date.isoformat(),
                portfolio_id=pid,
                description="Dividend from PDT Sheets sync",
            )
            saved_div += 1
        except Exception as e:
            errors.append(f"DIV {div.search} {div.date}: {e}")

    for bk in result.bookings:
        try:
            pid = (
                db.get_or_create_portfolio(bk.broker, bk.currency)
                if bk.broker
                else portfolio_id
            )
            db.create_booking(
                date=bk.date.isoformat(),
                action=bk.action,
                amount=bk.amount,
                currency=bk.currency,
                portfolio_id=pid,
            )
            saved_bk += 1
        except Exception as e:
            errors.append(f"Booking {bk.date}: {e}")

    return PullResponse(
        spreadsheet_id=sheet_id,
        imported_transactions=saved_tx,
        imported_dividends=saved_div,
        imported_bookings=saved_bk,
        skipped=len(result.skipped),
        errors=errors,
    )


@router.post("/pdt-push", response_model=PushResponse)
async def push_to_sheets(
    spreadsheet_id: Optional[str] = Query(
        default=None,
        description="Google Spreadsheet ID (falls back to GOOGLE_SPREADSHEET_ID env var)",
    ),
    portfolio_id: Optional[int] = Query(
        default=None, description="Export only this portfolio (default: all)"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """
    Push all portfolio data to a PDT-format Google Spreadsheet.
    Overwrites the Transactions, Dividends, and Bookings sheets.
    """
    sheet_id = (
        spreadsheet_id
        or db.get_setting("google_spreadsheet_id")
        or _default_spreadsheet_id()
    )
    if not sheet_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No spreadsheet_id provided and GOOGLE_SPREADSHEET_ID is not set. "
                "Pass ?spreadsheet_id=<ID> or set the env var."
            ),
        )

    sync = _get_sync(sheet_id)

    try:
        counts = sync.push(db, portfolio_id)
    except Exception as e:
        logger.exception("Google Sheets push failed")
        raise HTTPException(
            status_code=502, detail=f"Sheets push failed: {_google_error_detail(e)}"
        )

    return PushResponse(
        spreadsheet_id=sheet_id,
        transactions_written=counts["transactions"],
        dividends_written=counts["dividends"],
        bookings_written=counts["bookings"],
        spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{sheet_id}/",
    )


@router.post("/pdt-backup", response_model=BackupResponse)
async def backup_sheet(
    spreadsheet_id: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """
    Overwrite the fixed 'Backup — Transactions/Dividends/Bookings' tabs in the
    same spreadsheet with a snapshot of the current data tabs.
    """
    sheet_id = (
        spreadsheet_id
        or db.get_setting("google_spreadsheet_id")
        or _default_spreadsheet_id()
    )
    if not sheet_id:
        raise HTTPException(status_code=400, detail="No spreadsheet_id configured.")

    sync = _get_sync(sheet_id)
    try:
        backup_url = sync.backup_in_place(title)
    except Exception as e:
        logger.exception("Google Sheets backup failed")
        raise HTTPException(
            status_code=502, detail=f"Sheets backup failed: {_google_error_detail(e)}"
        )

    from datetime import date

    backup_title = title or f"PFM Backup {date.today().isoformat()}"
    return BackupResponse(backup_url=backup_url, title=backup_title)


@router.get("/pdt-download")
async def download_sheet(
    spreadsheet_id: Optional[str] = Query(default=None),
    fmt: str = Query(default="xlsx"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """
    Download the Google Spreadsheet via Drive API export (read-only, no quota consumed).
    fmt: xlsx (default) | ods | pdf
    """
    from fastapi.responses import Response

    MIME_TYPES = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ods": "application/vnd.oasis.opendocument.spreadsheet",
        "pdf": "application/pdf",
    }
    if fmt not in MIME_TYPES:
        fmt = "xlsx"
    mime = MIME_TYPES[fmt]

    sheet_id = (
        spreadsheet_id
        or db.get_setting("google_spreadsheet_id")
        or _default_spreadsheet_id()
    )
    if not sheet_id:
        raise HTTPException(status_code=400, detail="No spreadsheet_id configured.")

    sync = _get_sync(sheet_id)
    try:
        content = sync.export_bytes(mime)
    except Exception as e:
        logger.exception("Google Sheets download failed")
        raise HTTPException(
            status_code=502, detail=f"Sheets download failed: {_google_error_detail(e)}"
        )

    from datetime import date

    filename = f"pdt-backup-{date.today().isoformat()}.{fmt}"
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
