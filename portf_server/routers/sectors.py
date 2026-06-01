"""
Sectors Router for Portfolio Management API

Handles sector classification and analysis.
"""

from typing import List
from fastapi import APIRouter

from portf_manager.sectors import list_all_sectors, resolve_sector

router = APIRouter()


@router.get("/", response_model=List[str])
async def get_all_sectors():
    """
    Get all available GICS sectors.

    Returns:
        List[str]: List of all unique sectors
    """
    return list_all_sectors()


@router.get("/{symbol}")
async def get_asset_sector(symbol: str):
    """
    Get the sector for a specific asset symbol.

    Args:
        symbol: Asset symbol

    Returns:
        dict: Asset symbol and sector information
    """
    sector = resolve_sector(symbol.upper())
    return {"symbol": symbol.upper(), "sector": sector}
