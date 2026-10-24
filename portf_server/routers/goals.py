"""
Goals / FIRE Router — savings targets with on-track projection.

The numbers come from ``portf_manager.services.goals`` (shared with the chat
tool and Action Items); this module only maps HTTP to it.
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portf_manager.services.goals import goals_summary, list_goals_with_progress

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database
from .networth import net_worth_eur

router = APIRouter()
logger = logging.getLogger(__name__)


class GoalCreate(BaseModel):
    name: str
    target_amount_eur: float
    target_date: date
    monthly_contribution_eur: float = 0
    expected_return_pct: float = 7.0
    # Baseline the pace is measured from. On create, None = today's net worth;
    # on update, None = leave unchanged (set it to reset the baseline).
    start_networth_eur: Optional[float] = None
    # None = leave unchanged on update; "" clears.
    notes: Optional[str] = None


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/")
def list_goals(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List goals with progress, projection and saving pace.

    Sync (plain ``def``): net_worth_eur → _fx does blocking FX lookups that
    would stall the event loop on a cache miss in an async handler.
    """
    return list_goals_with_progress(db)


@router.get("/summary")
def get_goals_summary(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Goals with a one-line summary (counts per status, planned monthly).

    One call for the dashboard card and the page's summary strip. There is
    no ``GET /{goal_id}``, so the literal path can't be shadowed.
    """
    goals = list_goals_with_progress(db)
    return {"summary": goals_summary(goals), "goals": goals}


@router.post("/", status_code=201)
def create_goal(
    body: GoalCreate, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Create a savings goal, recording today's net worth as its baseline."""
    start = body.start_networth_eur
    if start is None:
        start = round(net_worth_eur(db), 2)
    gid = db.create_goal(
        name=body.name,
        target_amount_eur=body.target_amount_eur,
        target_date=body.target_date.isoformat(),
        monthly_contribution_eur=body.monthly_contribution_eur,
        expected_return_pct=body.expected_return_pct,
        start_networth_eur=start,
        notes=body.notes or None,
    )
    return db.get_goal(gid)


@router.put("/{goal_id}")
def update_goal(
    goal_id: int,
    body: GoalCreate,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Update a savings goal. Omitted baseline/notes stay as they are."""
    if not db.update_goal(
        goal_id=goal_id,
        name=body.name,
        target_amount_eur=body.target_amount_eur,
        target_date=body.target_date.isoformat(),
        monthly_contribution_eur=body.monthly_contribution_eur,
        expected_return_pct=body.expected_return_pct,
        start_networth_eur=body.start_networth_eur,
        notes=body.notes,
    ):
        raise HTTPException(status_code=404, detail="Goal not found")
    return db.get_goal(goal_id)


@router.delete("/{goal_id}")
async def delete_goal(
    goal_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a goal."""
    if not db.delete_goal(goal_id):
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"deleted": goal_id}
