"""
Goals / FIRE Router — savings targets with on-track projection.
"""

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


class GoalCreate(BaseModel):
    name: str
    target_amount_eur: float
    target_date: date
    monthly_contribution_eur: float = 0
    expected_return_pct: float = 7.0


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _current_networth(db) -> float:
    """Latest snapshot value, or compute from holdings if none."""
    snaps = db.get_snapshots(limit=1)
    if snaps:
        return snaps[-1]["total_value_eur"]
    return 0.0


def _project(
    current: float, monthly: float, annual_return: float, months: int
) -> float:
    """Future value of current + monthly contributions compounded monthly."""
    r = annual_return / 100 / 12
    fv = current * ((1 + r) ** months)
    if r > 0:
        fv += monthly * (((1 + r) ** months - 1) / r)
    else:
        fv += monthly * months
    return fv


@router.get("/")
async def list_goals(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List goals with progress and on-track projection."""
    current_nw = _current_networth(db)
    out = []
    today = date.today()
    for g in db.get_goals():
        target_dt = datetime.strptime(str(g["target_date"])[:10], "%Y-%m-%d").date()
        months_left = max(
            0, (target_dt.year - today.year) * 12 + (target_dt.month - today.month)
        )
        projected = _project(
            current_nw,
            g["monthly_contribution_eur"],
            g["expected_return_pct"],
            months_left,
        )
        progress_pct = (
            round(current_nw / g["target_amount_eur"] * 100, 1)
            if g["target_amount_eur"]
            else 0
        )
        on_track = projected >= g["target_amount_eur"]
        # Required monthly contribution to hit the goal
        r = g["expected_return_pct"] / 100 / 12
        required_monthly = None
        if months_left > 0:
            fv_current = current_nw * ((1 + r) ** months_left)
            gap = g["target_amount_eur"] - fv_current
            if gap > 0 and r > 0:
                required_monthly = round(gap / (((1 + r) ** months_left - 1) / r), 2)
            elif gap > 0:
                required_monthly = round(gap / months_left, 2)
            else:
                required_monthly = 0
        out.append(
            {
                **g,
                "current_networth_eur": round(current_nw, 2),
                "progress_pct": progress_pct,
                "months_left": months_left,
                "projected_value_eur": round(projected, 2),
                "on_track": on_track,
                "shortfall_eur": round(max(0, g["target_amount_eur"] - projected), 2),
                "required_monthly_eur": required_monthly,
            }
        )
    return out


@router.post("/", status_code=201)
async def create_goal(
    body: GoalCreate, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Create a savings goal."""
    gid = db.create_goal(
        name=body.name,
        target_amount_eur=body.target_amount_eur,
        target_date=body.target_date.isoformat(),
        monthly_contribution_eur=body.monthly_contribution_eur,
        expected_return_pct=body.expected_return_pct,
    )
    return db.get_goal(gid)


@router.delete("/{goal_id}")
async def delete_goal(
    goal_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a goal."""
    if not db.delete_goal(goal_id):
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"deleted": goal_id}
