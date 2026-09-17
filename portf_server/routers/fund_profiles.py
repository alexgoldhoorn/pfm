"""Fund profile router — the weight maps that make a fund see-through.

Every handler is a plain ``def``: they make blocking yfinance and LLM calls, so
FastAPI runs them in a threadpool rather than on the event loop.
"""

import json
import logging
from datetime import date
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from portf_manager.llm_client import get_llm_client
from portf_manager.services import benchmarks as benchmarks_service
from portf_manager.services import fund_profiles as fp_service

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


class ProfileBody(BaseModel):
    benchmark_key: Optional[str] = None
    asset_class: Dict[str, float]
    regions: Dict[str, float]
    sectors: Dict[str, float] = {}
    currency_hedged: bool = False
    hedge_currency: Optional[str] = None
    as_of: str
    notes: Optional[str] = None


class RefreshBody(BaseModel):
    benchmark_key: str
    force: bool = False


def _asset_or_404(db, asset_id: int) -> dict:
    asset = db.get_asset(asset_id)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Asset {asset_id} not found"
        )
    return asset


def _save(db, profile: dict) -> dict:
    problems = fp_service.validate_profile(profile)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="; ".join(problems)
        )
    row = db.upsert_fund_profile(**fp_service.serialize_profile(profile))
    return fp_service.parse_profile(row)


# Registered before /{asset_id}: FastAPI matches in declaration order, so a
# literal route declared after a path parameter is swallowed by it.
@router.get("/benchmarks")
def list_benchmarks(api_key_info: dict = Depends(_auth)):
    """The benchmark index table, as dropdown options."""
    return {"benchmarks": benchmarks_service.benchmark_choices()}


@router.get("/")
def list_fund_assets(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every fund-like asset, with whether it has a profile and if it is stale."""
    today = date.today()
    funds = []
    for asset in db.get_all_assets():
        if asset.get("asset_type") not in fp_service.FUND_ASSET_TYPES:
            continue
        profile = fp_service.parse_profile(db.get_fund_profile(asset["id"]))
        funds.append(
            {
                "asset_id": asset["id"],
                "symbol": asset["symbol"],
                "name": asset.get("name"),
                "asset_type": asset.get("asset_type"),
                "ticker": asset.get("ticker"),
                "has_profile": profile is not None,
                "source": profile.get("source") if profile else None,
                "benchmark_key": profile.get("benchmark_key") if profile else None,
                "as_of": profile.get("as_of") if profile else None,
                "is_stale": fp_service.is_stale(profile, today) if profile else None,
            }
        )
    return {"funds": funds}


@router.get("/{asset_id}")
def get_profile(
    asset_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """One fund profile."""
    _asset_or_404(db, asset_id)
    profile = fp_service.parse_profile(db.get_fund_profile(asset_id))
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No profile for asset {asset_id}",
        )
    return profile


@router.put("/{asset_id}")
def put_profile(
    asset_id: int,
    body: ProfileBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save a hand-edited profile. Editing always marks it manual."""
    _asset_or_404(db, asset_id)
    profile = {**body.model_dump(), "asset_id": asset_id, "source": "manual"}
    return _save(db, profile)


@router.post("/{asset_id}/refresh")
def refresh_profile(
    asset_id: int,
    body: RefreshBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rebuild a profile from its benchmark plus yfinance composition."""
    asset = _asset_or_404(db, asset_id)
    existing = fp_service.parse_profile(db.get_fund_profile(asset_id))
    if existing and existing.get("source") == "manual" and not body.force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This profile was edited by hand. Pass force=true to replace it "
                "with benchmark data."
            ),
        )
    try:
        profile = fp_service.build_from_benchmark(db, asset, body.benchmark_key)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _save(db, profile)


def _build_suggest_prompt(asset: dict) -> str:
    """Prompt for a fund whose index we do not know."""
    return (
        "You are a fund analyst. For the fund below, give its approximate "
        "geographic and sector breakdown.\n\n"
        f"Name: {asset.get('name')}\n"
        f"Identifier: {asset.get('symbol')}\n"
        f"Ticker: {asset.get('ticker') or 'unknown'}\n\n"
        "Return ONLY valid JSON, no markdown fences, in this shape:\n"
        '{"regions": {"north_america": 0.0, "europe_ex_uk": 0.0, "uk": 0.0, '
        '"japan": 0.0, "pacific_ex_japan": 0.0, "emerging": 0.0}, '
        '"asset_class": {"equity": 0.0, "bond": 0.0, "cash": 0.0}, '
        '"sectors": {"Technology": 0.0}, "benchmark_key": null, '
        '"notes": "one sentence on what this is based on"}\n\n'
        "Rules: regions must sum to 1.0 and use only those keys; asset_class "
        "must sum to 1.0; omit sectors entirely for a bond fund; if you are not "
        "reasonably confident, say so in notes rather than inventing precision."
    )


@router.post("/{asset_id}/suggest")
def suggest_profile(
    asset_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Ask the LLM to draft a profile. Writes nothing — the user applies it."""
    asset = _asset_or_404(db, asset_id)
    try:
        llm = get_llm_client()
        raw = llm.generate(_build_suggest_prompt(asset)).strip()
        if raw.startswith("```"):
            raw = "\n".join(
                line for line in raw.split("\n") if not line.strip().startswith("```")
            )
        suggestion = json.loads(raw)
    except Exception as e:
        logger.warning(f"Fund profile suggestion failed for {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Profile suggestion failed: {e}",
        )

    # Report the problems rather than rejecting: the user reviews this in a form
    # and can correct a weight the model got wrong.
    candidate = {
        "asset_id": asset_id,
        "source": "llm",
        "benchmark_key": suggestion.get("benchmark_key"),
        "asset_class": suggestion.get("asset_class", {}),
        "regions": suggestion.get("regions", {}),
        "sectors": suggestion.get("sectors", {}),
        "currency_hedged": False,
        "hedge_currency": None,
        "as_of": date.today().isoformat(),
        "notes": suggestion.get("notes"),
    }
    return {
        "suggestion": candidate,
        "problems": fp_service.validate_profile(candidate),
    }
