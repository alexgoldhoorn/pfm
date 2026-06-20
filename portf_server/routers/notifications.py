"""Push notification subscription management."""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from portf_manager.database import Database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

logger = logging.getLogger(__name__)
router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    """Enforce API key authentication for protected endpoints."""
    return await require_api_key(api_key_manager)(request)


class SubscribeBody(BaseModel):
    """Push subscription keys from the browser's PushManager."""

    endpoint: str
    p256dh: str
    auth: str


@router.get("/vapid-key")
async def get_vapid_key(db: Database = Depends(get_database)):
    """Return the VAPID public key (no auth required — needed before subscribe)."""
    key = db.get_setting("vapid_public_key") or ""
    return {"public_key": key}


@router.post("/subscribe")
async def subscribe(
    body: SubscribeBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save or update a push subscription."""
    db.add_push_subscription(body.endpoint, body.p256dh, body.auth)
    return {"status": "subscribed"}


@router.delete("/subscribe")
async def unsubscribe(
    body: SubscribeBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Remove a push subscription."""
    db.delete_push_subscription(body.endpoint)
    return {"status": "unsubscribed"}
