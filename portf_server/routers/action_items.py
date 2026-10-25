"""Action Items Router — cross-cutting maintenance checklist.

GET /api/v1/action-items/ — aggregated stale-import, data-quality,
price-update-failure, stale-research, off-track-goal, and price-alert
checks, each carrying its follow-up ``status`` (open / checked / snoozed).
GET /api/v1/action-items/acks and POST /api/v1/action-items/ack read and
write that follow-up state (``services/action_item_acks.py``), shared by
every browser, the AI chat and the MCP tools. See
portf_manager/services/action_items.py for the checks themselves and
docs/features/action-items.md for the design.
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from portf_manager.services.action_item_acks import list_acks, set_ack
from portf_manager.services.action_items import action_items_report

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/")
def list_action_items(
    status: Literal["open", "all"] = Query(
        "open",
        description=(
            "open: only items nobody has checked or snoozed (default). "
            "all: every item, annotated with its status and ack."
        ),
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Aggregated cross-cutting maintenance checklist.

    Plain ``def``: every check calls another router's plain-``def``
    function directly (dq_duplicates, dq_suspicious, list_goals,
    check_watchlist_alerts, compute_price_target_alerts) — none of them are
    coroutines, and there's no yfinance call in this endpoint's own path.

    ``summary`` counts every item whatever ``status`` filters; ``acks`` is
    the whole stored follow-up map, so the web client can apply it to the
    Net Worth checklist items it computes on its own.
    """
    report = action_items_report(db, status=status)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return report


@router.get("/acks")
def list_action_item_acks(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Every stored follow-up record, keyed by finding id."""
    return {"acks": list_acks(db)}


class AckIn(BaseModel):
    """Follow-up state for one finding."""

    id: str = Field(..., min_length=1, max_length=300, description="Finding id")
    status: Literal["open", "checked", "snoozed"]
    note: Optional[str] = Field(None, max_length=2000)
    until: Optional[str] = Field(
        None, description="Snooze until this date (YYYY-MM-DD, inclusive)"
    )
    days: Optional[int] = Field(
        None, ge=1, le=3650, description="Or snooze this many days from today"
    )


@router.post("/ack")
def ack_action_item(
    body: AckIn, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Mark a finding checked or snoozed, or reopen it (``status: open``).

    Returns ``{"id", "status", "ack"}``; ``ack`` is null once reopened.
    """
    try:
        ack = set_ack(
            db,
            body.id,
            body.status,
            note=body.note,
            until=body.until,
            days=body.days,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return {"id": body.id.strip(), "status": body.status, "ack": ack}
