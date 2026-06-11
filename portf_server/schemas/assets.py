"""
Asset Schemas for Portfolio Management API

Pydantic models for asset-related requests and responses.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from enum import Enum


class AssetType(str, Enum):
    """Asset type enumeration."""

    STOCK = "stock"
    BOND = "bond"
    CRYPTO = "crypto"
    ETF = "etf"
    INDEX = "index"
    MUTUAL_FUND = "mutual_fund"
    COMMODITY = "commodity"
    CASH = "cash"


class PriceType(str, Enum):
    """Price type enumeration."""

    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    ADJUSTED_CLOSE = "adjusted_close"


class AssetCreateRequest(BaseModel):
    """Schema for asset creation request."""

    symbol: str = Field(..., max_length=20, description="Asset symbol (e.g., AAPL)")
    name: str = Field(..., max_length=200, description="Asset name")
    asset_type: AssetType = Field(..., description="Type of asset")
    exchange: Optional[str] = Field(None, max_length=50, description="Trading exchange")
    currency: str = Field(default="USD", max_length=3, description="Asset currency")
    sector: Optional[str] = Field(None, max_length=100, description="Asset sector")
    description: Optional[str] = Field(None, description="Asset description")


class AssetUpdateRequest(BaseModel):
    """Schema for asset update request."""

    name: Optional[str] = Field(None, max_length=200, description="Asset name")
    asset_type: Optional[AssetType] = Field(None, description="Type of asset")
    exchange: Optional[str] = Field(None, max_length=50, description="Trading exchange")
    currency: Optional[str] = Field(None, max_length=3, description="Asset currency")
    sector: Optional[str] = Field(None, max_length=100, description="Asset sector")
    description: Optional[str] = Field(None, description="Asset description")
    ticker: Optional[str] = Field(
        None, max_length=50, description="Market ticker alias (e.g., NVDA, ASML.AS)"
    )
    is_active: Optional[bool] = Field(None, description="Whether asset is active")


class AssetResponse(BaseModel):
    """Schema for asset response."""

    id: int
    symbol: str
    ticker: Optional[str] = None
    name: str
    asset_type: AssetType
    exchange: Optional[str] = None
    currency: str
    sector: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    auto_price: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PriceCreateRequest(BaseModel):
    """Schema for price creation request."""

    price: Decimal = Field(..., gt=0, description="Price value")
    price_date: date = Field(..., description="Price date")
    price_type: PriceType = Field(default=PriceType.CLOSE, description="Type of price")
    volume: Optional[int] = Field(None, ge=0, description="Trading volume")
    source: Optional[str] = Field(None, max_length=50, description="Price data source")


class PriceResponse(BaseModel):
    """Schema for price response."""

    id: int
    asset_id: int
    price: Decimal
    price_date: date
    price_type: PriceType
    volume: Optional[int] = None
    source: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AssetWithPriceResponse(AssetResponse):
    """Schema for asset response with current price."""

    current_price: Optional[Decimal] = None
    price_date: Optional[date] = None


class AssetPositionResponse(BaseModel):
    """Schema for asset position response."""

    asset: AssetResponse
    position_size: Decimal
    average_cost: Optional[Decimal] = None
    current_price: Optional[Decimal] = None
    current_value: Optional[Decimal] = None
    unrealized_gain: Optional[Decimal] = None
    unrealized_gain_percent: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)
