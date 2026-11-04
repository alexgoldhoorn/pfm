"""Position, sector and asset-type caps (limits on how much one thing may weigh)."""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from portf_manager.database import Database
from portf_manager.services.caps import cap_status

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

Scope = Literal["symbol", "sector", "asset_type"]


class CapCreate(BaseModel):
    scope: Scope
    key: str = Field(..., min_length=1, max_length=100)
    max_pct: float = Field(..., gt=0, le=100)
    note: Optional[str] = None


class CapUpdate(BaseModel):
    key: Optional[str] = Field(None, min_length=1, max_length=100)
    max_pct: Optional[float] = Field(None, gt=0, le=100)
    note: Optional[str] = None


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/")
def list_caps(
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """The stored caps (no exposure maths; see ``/status``)."""
    items = db.get_position_caps()
    return {"items": items, "total": len(items)}


# registered before "/{cap_id}" so "status" is not read as an id
@router.get("/status")
def caps_status(
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Each cap with its current weight, headroom and ok / near / breached."""
    return cap_status(db)


@router.post("/", status_code=201)
def create_cap(
    data: CapCreate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    try:
        cap_id = db.create_position_cap(data.scope, data.key, data.max_pct, data.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return db.get_position_cap(cap_id)


@router.put("/{cap_id}")
def update_cap(
    cap_id: int,
    data: CapUpdate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Change the percentage, note or key. Omitted fields stay as they are."""
    if not db.get_position_cap(cap_id):
        raise HTTPException(status_code=404, detail="Cap not found")
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        db.update_position_cap(cap_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return db.get_position_cap(cap_id)


@router.delete("/{cap_id}", status_code=204)
def delete_cap(
    cap_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    if not db.delete_position_cap(cap_id):
        raise HTTPException(status_code=404, detail="Cap not found")
