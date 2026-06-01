"""
Export Router for Portfolio Management API

Provides file-download endpoints for portfolio data:
  GET /csv   — all transactions as UTF-8 CSV (Excel-compatible with BOM)
  GET /pdt   — all transactions as PDT-format XLSX
"""

import csv
import io
import logging
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from portf_manager.parsers.pdt_xlsx_parser import PDTXLSXExporter

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "id",
    "date",
    "symbol",
    "name",
    "type",
    "quantity",
    "price",
    "total_amount",
    "fees",
    "currency",
    "notes",
]


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/csv")
async def export_transactions_csv(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Download all transactions as a CSV file."""
    if portfolio_id is not None:
        transactions = db.get_transactions_by_portfolio(portfolio_id)
    else:
        transactions = db.get_all_transactions(limit=100_000)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()

    for tx in transactions:
        writer.writerow(
            {
                "id": tx.get("id", ""),
                "date": tx.get("transaction_date", ""),
                "symbol": tx.get("symbol", ""),
                "name": tx.get("name", ""),
                "type": tx.get("transaction_type", ""),
                "quantity": tx.get("quantity", ""),
                "price": tx.get("price", ""),
                "total_amount": tx.get("total_amount", ""),
                "fees": tx.get("fees", 0),
                "currency": tx.get("currency", ""),
                "notes": tx.get("description", ""),
            }
        )

    # UTF-8 BOM makes Excel auto-detect encoding
    csv_bytes = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@router.get("/pdt")
async def export_pdt_xlsx(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Download all transactions in Portfolio Dividend Tracker XLSX format."""
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        PDTXLSXExporter().export(db, tmp_path, portfolio_id)
        with open(tmp_path, "rb") as f:
            xlsx_bytes = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=portfolio_pdt.xlsx"},
    )
