"""
Decision journal — what was decided, why, what was expected, and how it
turned out.

A decision is written when it is made (web page, chat tool or API) with the
stored price at the time, and reviewed later: ``POST /{id}/review`` records
the outcome and a rating and stamps ``reviewed_at``. Reads add the current
stored price and the change since the decision, so a review is judged against
numbers rather than memory. An open decision whose ``review_on`` has passed
raises an Action Item (``services/action_items.py``).
"""

import logging
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from portf_manager.database import Database

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

Action = Literal["buy", "sell", "hold", "dca", "rebalance", "traspaso", "other"]
Rating = Literal["good", "bad", "neutral", "unclear"]


class DecisionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    action: Action
    decided_on: Optional[date] = None
    symbol: Optional[str] = None
    rationale: Optional[str] = None
    expectation: Optional[str] = None
    review_on: Optional[date] = None
    # None = the asset's latest stored price when a known symbol is given
    price_at_decision: Optional[float] = None
    source: str = "web"


class DecisionUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    action: Optional[Action] = None
    decided_on: Optional[date] = None
    symbol: Optional[str] = None
    rationale: Optional[str] = None
    expectation: Optional[str] = None
    review_on: Optional[date] = None
    price_at_decision: Optional[float] = None


class DecisionReview(BaseModel):
    outcome: Optional[str] = None
    outcome_rating: Rating
    # False re-opens: clears the outcome, rating and review stamp
    reviewed: bool = True


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _resolve_asset(db: Database, symbol: Optional[str]) -> tuple[Optional[dict], str]:
    """``(asset or None, normalised symbol)`` for a user-typed symbol."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None, ""
    asset = db.get_asset_by_symbol(sym)
    return asset, (asset or {}).get("symbol") or sym


def enrich_decision(db: Database, row: dict, today: Optional[date] = None) -> dict:
    """Add the current stored price, the move since the decision and the status.

    ``status`` is ``reviewed``, ``due`` (open and the review date has passed)
    or ``open``. The price fields are None without an asset or a stored price.
    """
    today = today or date.today()
    out = dict(row)
    out["status"] = "reviewed" if row.get("reviewed_at") else "open"
    if out["status"] == "open" and row.get("review_on"):
        try:
            if date.fromisoformat(str(row["review_on"])[:10]) <= today:
                out["status"] = "due"
        except ValueError:
            pass
    out["price_now"] = None
    out["price_now_date"] = None
    out["price_change_pct"] = None
    if row.get("asset_id"):
        latest = db.get_latest_price(row["asset_id"])
        if latest:
            out["price_now"] = float(latest["price"])
            out["price_now_date"] = latest.get("price_date")
            then = row.get("price_at_decision")
            if then:
                out["price_change_pct"] = round(
                    (out["price_now"] - float(then)) / float(then) * 100, 2
                )
    return out


def list_decisions_enriched(
    db: Database, status: str = "all", symbol: Optional[str] = None
) -> list[dict]:
    """Journal entries with price context; shared by the router and the chat tool."""
    return [
        enrich_decision(db, r) for r in db.get_decisions(status=status, symbol=symbol)
    ]


def create_decision_entry(db: Database, data: DecisionCreate) -> dict:
    """Validate, resolve the symbol and price, insert; returns the stored row."""
    asset, sym = _resolve_asset(db, data.symbol)
    price = data.price_at_decision
    currency = None
    if asset:
        currency = asset.get("currency")
        if price is None:
            latest = db.get_latest_price(asset["id"])
            price = float(latest["price"]) if latest else None
    decision_id = db.create_decision(
        decided_on=(data.decided_on or date.today()).isoformat(),
        symbol=sym or None,
        asset_id=asset["id"] if asset else None,
        action=data.action,
        title=data.title.strip(),
        rationale=data.rationale,
        expectation=data.expectation,
        review_on=data.review_on.isoformat() if data.review_on else None,
        price_at_decision=price,
        currency=currency,
        source=data.source,
    )
    return enrich_decision(db, db.get_decision(decision_id))


@router.get("/")
def list_decisions(
    status: str = "all",
    symbol: Optional[str] = None,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Journal entries, newest first. ``status``: open | reviewed | all."""
    if status not in ("open", "reviewed", "all"):
        raise HTTPException(
            status_code=400, detail="status must be open, reviewed or all"
        )
    items = list_decisions_enriched(db, status=status, symbol=symbol)
    return {
        "items": items,
        "total": len(items),
        "due": sum(1 for i in items if i["status"] == "due"),
    }


@router.post("/", status_code=201)
def create_decision(
    data: DecisionCreate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Record a decision; a known symbol gets its latest stored price attached."""
    try:
        return create_decision_entry(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{decision_id}")
def get_decision(
    decision_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    row = db.get_decision(decision_id)
    if not row:
        raise HTTPException(status_code=404, detail="Decision not found")
    return enrich_decision(db, row)


@router.put("/{decision_id}")
def update_decision(
    decision_id: int,
    data: DecisionUpdate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit the decision itself (not its review). Omitted fields stay as they are."""
    if not db.get_decision(decision_id):
        raise HTTPException(status_code=404, detail="Decision not found")
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if "symbol" in fields:
        asset, sym = _resolve_asset(db, fields["symbol"])
        fields["symbol"] = sym or None
        fields["asset_id"] = asset["id"] if asset else None
        fields["currency"] = (asset or {}).get("currency")
    for key in ("decided_on", "review_on"):
        if key in fields:
            fields[key] = fields[key].isoformat()
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        db.update_decision(decision_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return enrich_decision(db, db.get_decision(decision_id))


@router.post("/{decision_id}/review")
def review_decision(
    decision_id: int,
    data: DecisionReview,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Close the loop: what happened and whether the call was good.

    ``reviewed: false`` re-opens the decision and clears the review.
    """
    if not db.get_decision(decision_id):
        raise HTTPException(status_code=404, detail="Decision not found")
    if data.reviewed:
        db.update_decision(
            decision_id,
            outcome=data.outcome,
            outcome_rating=data.outcome_rating,
            reviewed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    else:
        db.update_decision(
            decision_id, outcome=None, outcome_rating=None, reviewed_at=None
        )
    return enrich_decision(db, db.get_decision(decision_id))


@router.delete("/{decision_id}", status_code=204)
def delete_decision(
    decision_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    if not db.delete_decision(decision_id):
        raise HTTPException(status_code=404, detail="Decision not found")
