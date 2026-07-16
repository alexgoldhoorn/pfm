"""Action Items Router — cross-cutting maintenance checklist.

GET /api/v1/action-items/ — aggregated stale-import, data-quality,
price-update-failure, stale-research, off-track-goal, and price-alert
checks. See portf_manager/services/action_items.py for the checks
themselves and docs/superpowers/specs/2026-07-16-action-items-design.md
for the design.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from portf_manager.services.action_items import get_action_items

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/")
def list_action_items(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Aggregated cross-cutting maintenance checklist.

    Plain ``def``: every check calls another router's plain-``def``
    function directly (dq_duplicates, dq_suspicious, list_goals,
    check_watchlist_alerts, compute_price_target_alerts) — none of them are
    coroutines, and there's no yfinance call in this endpoint's own path.
    """
    return {
        "items": get_action_items(db),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
