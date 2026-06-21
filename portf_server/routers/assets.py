"""
Assets Router for Portfolio Management API

Handles asset management, price data, and asset-related operations.
"""

from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request

from portf_manager.database import Database

from ..dependencies import get_database
from ..auth_middleware import require_api_key, APIKeyManager
from ..dependencies import get_api_key_manager
from ..schemas.assets import (
    AssetCreateRequest,
    AssetUpdateRequest,
    AssetResponse,
    AssetWithPriceResponse,
    PriceCreateRequest,
    PriceResponse,
    PriceType,
)

router = APIRouter()


# API Key authentication dependency
async def get_api_key_auth_for_assets(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    """Helper function for API key authentication in asset endpoints."""
    return await require_api_key(api_key_manager)(request)


def _parse_date_from_db(date_value) -> Optional[date]:
    """
    Convert database date value to Python date object.

    Args:
        date_value: Date value from database (can be str, date, or datetime)

    Returns:
        date object or None if conversion fails
    """
    if date_value is None:
        return None

    if isinstance(date_value, date):
        return date_value

    if isinstance(date_value, datetime):
        return date_value.date()

    if isinstance(date_value, str):
        try:
            # Try parsing ISO format date string
            return datetime.fromisoformat(date_value.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            try:
                # Try parsing simple date format
                return datetime.strptime(date_value, "%Y-%m-%d").date()
            except ValueError:
                # If all else fails, return None
                return None

    return None


@router.post("/", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def create_asset(
    asset_data: AssetCreateRequest,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_assets),
):
    """
    Create a new asset.

    Args:
        asset_data: Asset creation data
        db: Database instance
        current_user_id: Current user ID

    Returns:
        AssetResponse: Created asset information
    """
    try:
        # Check if asset with this symbol already exists
        existing_asset = db.get_asset_by_symbol(asset_data.symbol)
        if existing_asset:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Asset with symbol '{asset_data.symbol}' already exists",
            )

        asset_id = db.create_asset(
            symbol=asset_data.symbol,
            name=asset_data.name,
            asset_type=asset_data.asset_type.value,
            exchange=asset_data.exchange,
            currency=asset_data.currency,
            sector=asset_data.sector,
            description=asset_data.description,
        )

        # Get the created asset
        asset_dict = db.get_asset(asset_id)
        if not asset_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created asset",
            )

        return AssetResponse(**asset_dict)

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create asset",
        )


@router.get("/", response_model=List[AssetWithPriceResponse])
async def list_assets(
    active_only: bool = Query(True, description="Only return active assets"),
    asset_type: Optional[str] = Query(None, description="Filter by asset type"),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    db: Database = Depends(get_database),
):
    """
    Get all assets with optional filtering.

    Args:
        active_only: Whether to only return active assets
        asset_type: Filter by asset type
        sector: Filter by sector
        db: Database instance

    Returns:
        List[AssetWithPriceResponse]: List of assets with current prices
    """
    try:
        assets = db.get_all_assets(active_only=active_only)

        result = []
        for asset_dict in assets:
            # Apply additional filters
            if asset_type and asset_dict["asset_type"] != asset_type:
                continue
            if sector and asset_dict.get("sector") != sector:
                continue

            # Get current price
            price_data = db.get_latest_price(asset_dict["id"])

            asset_response = AssetWithPriceResponse(**asset_dict)
            if price_data:
                asset_response.current_price = Decimal(str(price_data["price"]))
                # Convert string date to proper date object
                asset_response.price_date = _parse_date_from_db(
                    price_data["price_date"]
                )

            result.append(asset_response)

        return result

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve assets",
        )


@router.get("/{asset_id}", response_model=AssetWithPriceResponse)
async def get_asset(
    asset_id: int,
    db: Database = Depends(get_database),
):
    """
    Get a specific asset by ID.

    Args:
        asset_id: Asset ID
        db: Database instance

    Returns:
        AssetWithPriceResponse: Asset information with current price
    """
    try:
        asset_dict = db.get_asset(asset_id)
        if not asset_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        # Get current price
        price_data = db.get_latest_price(asset_id)

        asset_response = AssetWithPriceResponse(**asset_dict)
        if price_data:
            asset_response.current_price = Decimal(str(price_data["price"]))
            # Convert string date to proper date object
            asset_response.price_date = _parse_date_from_db(price_data["price_date"])

        return asset_response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve asset",
        )


@router.put("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: int,
    asset_data: AssetUpdateRequest,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_assets),
):
    """
    Update an asset.

    Args:
        asset_id: Asset ID
        asset_data: Asset update data
        db: Database instance
        current_user_id: Current user ID

    Returns:
        AssetResponse: Updated asset information
    """
    try:
        # Check if asset exists
        asset_dict = db.get_asset(asset_id)
        if not asset_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        # Prepare update data
        update_data = {}
        for field, value in asset_data.model_dump(exclude_unset=True).items():
            if value is not None:
                if field == "asset_type" and hasattr(value, "value"):
                    update_data[field] = value.value
                else:
                    update_data[field] = value

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No update data provided",
            )

        success = db.update_asset(asset_id, **update_data)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update asset",
            )

        # Get updated asset
        updated_asset = db.get_asset(asset_id)
        return AssetResponse(**updated_asset)

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update asset",
        )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_assets),
):
    """
    Delete an asset (soft delete).

    Args:
        asset_id: Asset ID
        db: Database instance
        current_user_id: Current user ID
    """
    try:
        # Check if asset exists
        asset_dict = db.get_asset(asset_id)
        if not asset_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        success = db.delete_asset(asset_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete asset",
            )

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete asset",
        )


@router.post(
    "/{asset_id}/prices",
    response_model=PriceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_price(
    asset_id: int,
    price_data: PriceCreateRequest,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_assets),
):
    """
    Add a price record for an asset.

    Args:
        asset_id: Asset ID
        price_data: Price data
        db: Database instance
        current_user_id: Current user ID

    Returns:
        PriceResponse: Created price record
    """
    try:
        # Check if asset exists
        asset_dict = db.get_asset(asset_id)
        if not asset_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        db.create_price(
            asset_id=asset_id,
            price=float(price_data.price),
            price_date=price_data.price_date.isoformat(),
            price_type=price_data.price_type.value,
            volume=price_data.volume,
            source=price_data.source,
        )

        # A manually-entered price marks the asset so the daily price cron skips
        # it (won't overwrite manual prices for unlisted / P2P / illiquid assets).
        if (price_data.source or "").lower() == "manual":
            db.update_asset(asset_id, auto_price=0)

        # Get created price
        price_dict = db.get_price(
            asset_id=asset_id,
            price_date=price_data.price_date.isoformat(),
            price_type=price_data.price_type.value,
        )

        if not price_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created price",
            )

        return PriceResponse(**price_dict)

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add price",
        )


@router.get("/{asset_id}/prices", response_model=List[PriceResponse])
async def get_asset_prices(
    asset_id: int,
    start_date: Optional[date] = Query(
        None, description="Start date for price history"
    ),
    end_date: Optional[date] = Query(None, description="End date for price history"),
    price_type: PriceType = Query(PriceType.CLOSE, description="Type of price"),
    db: Database = Depends(get_database),
):
    """
    Get price history for an asset.

    Args:
        asset_id: Asset ID
        start_date: Start date for price history
        end_date: End date for price history
        price_type: Type of price
        db: Database instance

    Returns:
        List[PriceResponse]: Price history
    """
    try:
        # Check if asset exists
        asset_dict = db.get_asset(asset_id)
        if not asset_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        start_str = start_date.isoformat() if start_date else None
        end_str = end_date.isoformat() if end_date else None

        prices = db.get_price_history(
            asset_id=asset_id,
            start_date=start_str,
            end_date=end_str,
            price_type=price_type.value,
        )

        return [PriceResponse(**price) for price in prices]

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve price history",
        )


@router.post("/resolve-tickers")
def resolve_tickers(
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(get_api_key_auth_for_assets),
):
    """Auto-resolve Yahoo Finance tickers for ISIN-keyed assets via OpenFIGI.

    Finds all assets whose symbol is an ISIN and ticker is unset, queries
    OpenFIGI for the best matching exchange ticker, verifies it works in
    yfinance, then writes it back to the asset. Returns a per-asset summary.
    """
    from portf_manager.ticker_resolver import resolve_tickers_bulk, is_isin

    assets = db.get_all_assets(active_only=False)
    candidates = [
        a
        for a in assets
        if is_isin(a.get("symbol", "")) and not (a.get("ticker") or "").strip()
    ]

    resolved_map = resolve_tickers_bulk(candidates)

    resolved, failed = [], []
    for asset in candidates:
        ticker = resolved_map.get(asset["id"])
        if ticker:
            db.update_asset(asset["id"], ticker=ticker)
            # Clear stale sector/country cache so next diversification call
            # re-fetches with the new ticker.
            db.cache_clear(f"yf:sectorcountry:{asset['symbol']}")
            resolved.append(
                {
                    "id": asset["id"],
                    "symbol": asset["symbol"],
                    "name": asset.get("name", ""),
                    "ticker": ticker,
                }
            )
        else:
            failed.append(
                {
                    "id": asset["id"],
                    "symbol": asset["symbol"],
                    "name": asset.get("name", ""),
                }
            )

    return {
        "scanned": len(candidates),
        "resolved": resolved,
        "failed": failed,
    }
