"""
Portfolio Management Data Models

This module defines the core data models for portfolio management,
including Asset, Transaction, and Portfolio classes with domain logic
for cash flows, position calculations, and portfolio analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Protocol, Tuple
from decimal import Decimal
from enum import Enum


class AssetType(Enum):
    """Asset type enumeration."""

    STOCK = "stock"
    BOND = "bond"
    CRYPTO = "crypto"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"
    COMMODITY = "commodity"
    CASH = "cash"


class TransactionType(Enum):
    """Transaction type enumeration."""

    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    SPLIT = "split"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class PriceType(Enum):
    """Price type enumeration."""

    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    ADJUSTED_CLOSE = "adjusted_close"


class DatabaseAdapter(Protocol):
    """
    Protocol for database adapter interface.
    Allows models to be DB-agnostic by injecting database layer.
    """

    def get_asset(self, asset_id: int) -> Optional[Dict]:
        """Get asset by ID."""
        ...

    def get_asset_by_symbol(self, symbol: str) -> Optional[Dict]:
        """Get asset by symbol."""
        ...

    def get_transactions_by_asset(self, asset_id: int) -> List[Dict]:
        """Get all transactions for an asset."""
        ...

    def get_latest_price(
        self, asset_id: int, price_type: str = "close"
    ) -> Optional[Dict]:
        """Get latest price for an asset."""
        ...

    def get_price_history(
        self,
        asset_id: int,
        start_date: str = None,
        end_date: str = None,
        price_type: str = "close",
    ) -> List[Dict]:
        """Get price history for an asset."""
        ...

    def get_all_transactions(self, limit: int = None) -> List[Dict]:
        """Get all transactions."""
        ...

    def create_asset(self, **kwargs) -> int:
        """Create new asset."""
        ...

    def create_transaction(self, **kwargs) -> int:
        """Create new transaction."""
        ...

    def update_asset(self, asset_id: int, **kwargs) -> bool:
        """Update asset."""
        ...

    def update_transaction(self, transaction_id: int, **kwargs) -> bool:
        """Update transaction."""
        ...

    def get_entity(self, entity_id: int) -> Optional[Dict]:
        """Get entity by ID."""
        ...

    def get_entity_by_name(self, name: str) -> Optional[Dict]:
        """Get entity by name."""
        ...

    def create_entity(self, **kwargs) -> int:
        """Create new entity."""
        ...

    def update_entity(self, entity_id: int, **kwargs) -> bool:
        """Update entity."""
        ...

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict]:
        """Get portfolio by ID."""
        ...

    def get_portfolio_by_name(self, name: str) -> Optional[Dict]:
        """Get portfolio by name."""
        ...

    def create_portfolio(self, **kwargs) -> int:
        """Create new portfolio."""
        ...

    def update_portfolio(self, portfolio_id: int, **kwargs) -> bool:
        """Update portfolio."""
        ...

    def get_all_portfolios(self) -> List[Dict]:
        """Get all portfolios."""
        ...

    def get_transactions_by_portfolio(self, portfolio_id: int) -> List[Dict]:
        """Get all transactions for a portfolio."""
        ...


@dataclass
class Entity:
    """
    Entity data model representing brokers, banks, or other financial institutions.
    """

    id: int
    name: str
    entity_type: str  # e.g., 'broker', 'bank', 'platform'
    website: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "Entity":
        """Create Entity from dictionary data."""
        return cls(
            id=data["id"],
            name=data["name"],
            entity_type=data["entity_type"],
            website=data.get("website"),
            description=data.get("description"),
            is_active=data.get("is_active", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> Dict:
        """Convert Entity to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "website": self.website,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Asset:
    """
    Asset data model with domain logic for asset management.
    """

    id: int
    symbol: str
    name: str
    asset_type: AssetType
    exchange: Optional[str] = None
    currency: str = "USD"
    sector: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Database adapter for data access
    _db_adapter: Optional[DatabaseAdapter] = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """Post-initialization processing."""
        if isinstance(self.asset_type, str):
            self.asset_type = AssetType(self.asset_type)

    def set_db_adapter(self, adapter: DatabaseAdapter):
        """Set database adapter for data access."""
        self._db_adapter = adapter

    @classmethod
    def from_dict(
        cls, data: Dict, db_adapter: Optional[DatabaseAdapter] = None
    ) -> "Asset":
        """Create Asset from dictionary data."""
        asset = cls(
            id=data["id"],
            symbol=data["symbol"],
            name=data["name"],
            asset_type=AssetType(data["asset_type"]),
            exchange=data.get("exchange"),
            currency=data.get("currency", "USD"),
            sector=data.get("sector"),
            description=data.get("description"),
            is_active=data.get("is_active", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )
        if db_adapter:
            asset.set_db_adapter(db_adapter)
        return asset

    def to_dict(self) -> Dict:
        """Convert Asset to dictionary."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "asset_type": self.asset_type.value,
            "exchange": self.exchange,
            "currency": self.currency,
            "sector": self.sector,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def get_transactions(self) -> List["Transaction"]:
        """Get all transactions for this asset."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        transaction_dicts = self._db_adapter.get_transactions_by_asset(self.id)
        return [
            Transaction.from_dict(tx_dict, self._db_adapter)
            for tx_dict in transaction_dicts
        ]

    def get_current_price(
        self, price_type: PriceType = PriceType.CLOSE
    ) -> Optional[Decimal]:
        """Get current price for this asset."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        price_data = self._db_adapter.get_latest_price(self.id, price_type.value)
        return Decimal(str(price_data["price"])) if price_data else None

    def get_price_history(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        price_type: PriceType = PriceType.CLOSE,
    ) -> List[Dict]:
        """Get price history for this asset."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        start_str = start_date.isoformat() if start_date else None
        end_str = end_date.isoformat() if end_date else None

        return self._db_adapter.get_price_history(
            self.id, start_str, end_str, price_type.value
        )

    def calculate_position_size(self) -> Decimal:
        """Calculate current position size (total shares owned)."""
        transactions = self.get_transactions()
        position_size = Decimal("0")

        for transaction in transactions:
            if transaction.transaction_type == TransactionType.BUY:
                position_size += transaction.quantity
            elif transaction.transaction_type == TransactionType.SELL:
                position_size -= transaction.quantity
            elif transaction.transaction_type == TransactionType.SPLIT:
                # For splits, adjust position size based on split ratio
                # Assuming split ratio is stored in quantity field
                position_size *= transaction.quantity

        return position_size

    def calculate_average_cost(self) -> Optional[Decimal]:
        """Calculate average cost basis for current position."""
        transactions = self.get_transactions()
        total_cost = Decimal("0")
        total_shares = Decimal("0")

        for transaction in transactions:
            if transaction.transaction_type in [
                TransactionType.BUY,
                TransactionType.SELL,
            ]:
                if transaction.transaction_type == TransactionType.BUY:
                    total_cost += transaction.total_amount
                    total_shares += transaction.quantity
                else:  # SELL
                    # Use FIFO for cost basis calculation
                    if total_shares > 0:
                        avg_cost = total_cost / total_shares
                        total_cost -= avg_cost * transaction.quantity
                        total_shares -= transaction.quantity

        return total_cost / total_shares if total_shares > 0 else None

    def calculate_unrealized_gain_loss(
        self,
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Calculate unrealized gain/loss (amount and percentage)."""
        position_size = self.calculate_position_size()
        if position_size <= 0:
            return None, None

        current_price = self.get_current_price()
        avg_cost = self.calculate_average_cost()

        if not current_price or not avg_cost:
            return None, None

        current_value = position_size * current_price
        cost_basis = position_size * avg_cost

        unrealized_gain = current_value - cost_basis
        unrealized_percent = (unrealized_gain / cost_basis) * 100

        return unrealized_gain, unrealized_percent

    def get_dividend_history(self) -> List["Transaction"]:
        """Get dividend history for this asset."""
        transactions = self.get_transactions()
        return [
            tx for tx in transactions if tx.transaction_type == TransactionType.DIVIDEND
        ]

    def calculate_total_dividends(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> Decimal:
        """Calculate total dividends received in a period."""
        dividend_transactions = self.get_dividend_history()
        total_dividends = Decimal("0")

        for transaction in dividend_transactions:
            tx_date = transaction.transaction_date
            if start_date and tx_date < start_date:
                continue
            if end_date and tx_date > end_date:
                continue
            total_dividends += transaction.total_amount

        return total_dividends


@dataclass
class Transaction:
    """
    Transaction data model with domain logic for transaction management.
    """

    id: int
    asset_id: int
    transaction_type: TransactionType
    quantity: Decimal
    price: Decimal
    total_amount: Decimal
    # Currency field for the transaction (usually from the associated asset)
    currency: Optional[str] = None
    portfolio_id: Optional[int] = None
    fees: Decimal = Decimal("0")
    transaction_date: date = field(default_factory=date.today)
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Database adapter for data access
    _db_adapter: Optional[DatabaseAdapter] = field(default=None, init=False, repr=False)

    def __post_init__(self):
        """Post-initialization processing."""
        if isinstance(self.transaction_type, str):
            self.transaction_type = TransactionType(self.transaction_type)
        if not isinstance(self.quantity, Decimal):
            self.quantity = Decimal(str(self.quantity))
        if not isinstance(self.price, Decimal):
            self.price = Decimal(str(self.price))
        if not isinstance(self.total_amount, Decimal):
            self.total_amount = Decimal(str(self.total_amount))
        if not isinstance(self.fees, Decimal):
            self.fees = Decimal(str(self.fees))
        if isinstance(self.transaction_date, str):
            self.transaction_date = datetime.fromisoformat(self.transaction_date).date()

    def set_db_adapter(self, adapter: DatabaseAdapter):
        """Set database adapter for data access."""
        self._db_adapter = adapter

    @classmethod
    def from_dict(
        cls, data: Dict, db_adapter: Optional[DatabaseAdapter] = None
    ) -> "Transaction":
        """Create Transaction from dictionary data."""
        transaction = cls(
            id=data["id"],
            asset_id=data["asset_id"],
            portfolio_id=data.get("portfolio_id"),
            transaction_type=TransactionType(data["transaction_type"]),
            quantity=Decimal(str(data["quantity"])),
            price=Decimal(str(data["price"])),
            total_amount=Decimal(str(data["total_amount"])),
            fees=Decimal(str(data.get("fees", 0))),
            transaction_date=(
                datetime.fromisoformat(data["transaction_date"]).date()
                if isinstance(data["transaction_date"], str)
                else data["transaction_date"]
            ),
            description=data.get("description"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            # Include currency field from dictionary if available
            currency=data.get("currency"),
        )
        if db_adapter:
            transaction.set_db_adapter(db_adapter)
        return transaction

    def to_dict(self) -> Dict:
        """Convert Transaction to dictionary."""
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "portfolio_id": self.portfolio_id,
            "transaction_type": self.transaction_type.value,
            "quantity": float(self.quantity),
            "price": float(self.price),
            "total_amount": float(self.total_amount),
            "fees": float(self.fees),
            "transaction_date": self.transaction_date.isoformat(),
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "currency": self.currency,
        }

    def get_asset(self) -> Optional[Asset]:
        """Get the asset associated with this transaction."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        asset_data = self._db_adapter.get_asset(self.asset_id)
        return Asset.from_dict(asset_data, self._db_adapter) if asset_data else None

    def calculate_net_amount(self) -> Decimal:
        """Calculate net amount after fees."""
        return self.total_amount - self.fees

    def calculate_effective_price(self) -> Decimal:
        """Calculate effective price including fees."""
        if self.quantity == 0:
            return Decimal("0")

        if self.transaction_type == TransactionType.BUY:
            return (self.total_amount + self.fees) / self.quantity
        elif self.transaction_type == TransactionType.SELL:
            return (self.total_amount - self.fees) / self.quantity
        else:
            return self.price

    def is_cash_flow_positive(self) -> bool:
        """Check if transaction represents positive cash flow."""
        return self.transaction_type in [
            TransactionType.SELL,
            TransactionType.DIVIDEND,
            TransactionType.TRANSFER_IN,
        ]

    def get_cash_flow_impact(self) -> Decimal:
        """Get cash flow impact (positive for inflows, negative for outflows)."""
        if self.is_cash_flow_positive():
            return self.calculate_net_amount()
        else:
            return -self.calculate_net_amount()

    def validate(self) -> List[str]:
        """Validate transaction data and return list of errors."""
        errors = []

        if self.quantity <= 0 and self.transaction_type not in [
            TransactionType.DIVIDEND
        ]:
            errors.append("Quantity must be positive for buy/sell transactions")

        if self.price <= 0 and self.transaction_type in [
            TransactionType.BUY,
            TransactionType.SELL,
        ]:
            errors.append("Price must be positive for buy/sell transactions")

        if self.total_amount <= 0 and self.transaction_type != TransactionType.SPLIT:
            errors.append("Total amount must be positive")

        if self.fees < 0:
            errors.append("Fees cannot be negative")

        # Check if total_amount is consistent with quantity * price
        if self.transaction_type in [TransactionType.BUY, TransactionType.SELL]:
            expected_total = self.quantity * self.price
            if abs(self.total_amount - expected_total) > Decimal("0.01"):
                errors.append("Total amount doesn't match quantity × price")

        return errors


@dataclass
class Portfolio:
    """
    Portfolio data model with domain logic for portfolio analysis.
    """

    id: int
    name: str
    base_currency: str = "USD"
    entity_id: Optional[int] = None
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Database adapter for data access
    _db_adapter: Optional[DatabaseAdapter] = field(default=None, init=False, repr=False)

    def set_db_adapter(self, adapter: DatabaseAdapter):
        """Set database adapter for data access."""
        self._db_adapter = adapter

    @classmethod
    def from_dict(
        cls, data: Dict, db_adapter: Optional[DatabaseAdapter] = None
    ) -> "Portfolio":
        """Create Portfolio from dictionary data."""
        portfolio = cls(
            id=data["id"],
            name=data["name"],
            base_currency=data.get("base_currency", "USD"),
            entity_id=data.get("entity_id"),
            description=data.get("description"),
            is_active=data.get("is_active", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )
        if db_adapter:
            portfolio.set_db_adapter(db_adapter)
        return portfolio

    def to_dict(self) -> Dict:
        """Convert Portfolio to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "base_currency": self.base_currency,
            "entity_id": self.entity_id,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def get_entity(self) -> Optional[Entity]:
        """Get the entity associated with this portfolio."""
        if not self._db_adapter or not self.entity_id:
            return None

        entity_data = self._db_adapter.get_entity(self.entity_id)
        return Entity.from_dict(entity_data) if entity_data else None

    def get_all_assets(self) -> List[Asset]:
        """Get all assets in the portfolio."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        # This would need to be implemented in the database adapter
        # For now, we'll use a placeholder that gets assets with positions
        return self._get_assets_with_positions()

    def _get_assets_with_positions(self) -> List[Asset]:
        """Get assets that have current positions."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        # Get all transactions for this portfolio
        transactions = self.get_all_transactions()

        # Group transactions by asset_id and compute positions
        asset_positions = {}
        assets_cache = {}

        for transaction in transactions:
            asset_id = transaction.asset_id

            # Initialize position tracking for this asset
            if asset_id not in asset_positions:
                asset_positions[asset_id] = {
                    "quantity": Decimal("0"),
                    "total_cost": Decimal("0"),
                    "total_shares_bought": Decimal("0"),
                }

            position = asset_positions[asset_id]

            # Process transaction based on type
            if transaction.transaction_type == TransactionType.BUY:
                position["quantity"] += transaction.quantity
                position["total_cost"] += transaction.total_amount
                position["total_shares_bought"] += transaction.quantity
            elif transaction.transaction_type == TransactionType.SELL:
                position["quantity"] -= transaction.quantity
                # For sells, we reduce cost basis proportionally (FIFO-like)
                if position["total_shares_bought"] > 0:
                    cost_per_share = (
                        position["total_cost"] / position["total_shares_bought"]
                    )
                    position["total_cost"] -= cost_per_share * transaction.quantity
                    position["total_shares_bought"] -= transaction.quantity
            elif transaction.transaction_type == TransactionType.SPLIT:
                # For splits, multiply quantity by split ratio
                split_ratio = transaction.quantity
                position["quantity"] *= split_ratio
                position["total_shares_bought"] *= split_ratio
            elif transaction.transaction_type == TransactionType.TRANSFER_IN:
                position["quantity"] += transaction.quantity
                position["total_cost"] += transaction.total_amount
                position["total_shares_bought"] += transaction.quantity
            elif transaction.transaction_type == TransactionType.TRANSFER_OUT:
                position["quantity"] -= transaction.quantity
                if position["total_shares_bought"] > 0:
                    cost_per_share = (
                        position["total_cost"] / position["total_shares_bought"]
                    )
                    position["total_cost"] -= cost_per_share * transaction.quantity
                    position["total_shares_bought"] -= transaction.quantity

        # Return assets with positive positions
        result_assets = []
        for asset_id, position in asset_positions.items():
            if position["quantity"] > 0:
                # Get asset data if not cached
                if asset_id not in assets_cache:
                    asset_data = self._db_adapter.get_asset(asset_id)
                    if asset_data:
                        assets_cache[asset_id] = Asset.from_dict(
                            asset_data, self._db_adapter
                        )

                if asset_id in assets_cache:
                    result_assets.append(assets_cache[asset_id])

        return result_assets

    def get_all_transactions(self) -> List[Transaction]:
        """Get all transactions in the portfolio."""
        if not self._db_adapter:
            raise ValueError("Database adapter not set")

        transaction_dicts = self._db_adapter.get_transactions_by_portfolio(self.id)
        return [
            Transaction.from_dict(tx_dict, self._db_adapter)
            for tx_dict in transaction_dicts
        ]

    def calculate_total_value(self) -> Decimal:
        """Calculate total portfolio value at current prices."""
        total_value = Decimal("0")
        assets = self.get_all_assets()

        for asset in assets:
            position_size = asset.calculate_position_size()
            current_price = asset.get_current_price()

            if position_size > 0 and current_price:
                total_value += position_size * current_price

        return total_value

    def calculate_total_cost_basis(self) -> Decimal:
        """Calculate total cost basis of portfolio."""
        total_cost = Decimal("0")
        assets = self.get_all_assets()

        for asset in assets:
            position_size = asset.calculate_position_size()
            avg_cost = asset.calculate_average_cost()

            if position_size > 0 and avg_cost:
                total_cost += position_size * avg_cost

        return total_cost

    def calculate_total_unrealized_gain_loss(self) -> Tuple[Decimal, Decimal]:
        """Calculate total unrealized gain/loss for portfolio."""
        total_value = self.calculate_total_value()
        total_cost = self.calculate_total_cost_basis()

        unrealized_gain = total_value - total_cost
        unrealized_percent = (
            (unrealized_gain / total_cost * 100) if total_cost > 0 else Decimal("0")
        )

        return unrealized_gain, unrealized_percent

    def calculate_cash_flows(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> Dict[str, Decimal]:
        """Calculate cash flows for a period."""
        transactions = self.get_all_transactions()

        cash_flows = {
            "total_inflows": Decimal("0"),
            "total_outflows": Decimal("0"),
            "net_cash_flow": Decimal("0"),
            "dividends": Decimal("0"),
            "buys": Decimal("0"),
            "sells": Decimal("0"),
        }

        for transaction in transactions:
            if start_date and transaction.transaction_date < start_date:
                continue
            if end_date and transaction.transaction_date > end_date:
                continue

            cash_impact = transaction.get_cash_flow_impact()

            if cash_impact > 0:
                cash_flows["total_inflows"] += cash_impact
            else:
                cash_flows["total_outflows"] += abs(cash_impact)

            if transaction.transaction_type == TransactionType.DIVIDEND:
                cash_flows["dividends"] += transaction.total_amount
            elif transaction.transaction_type == TransactionType.BUY:
                cash_flows["buys"] += transaction.total_amount
            elif transaction.transaction_type == TransactionType.SELL:
                cash_flows["sells"] += transaction.total_amount

        cash_flows["net_cash_flow"] = (
            cash_flows["total_inflows"] - cash_flows["total_outflows"]
        )

        return cash_flows

    def get_asset_allocation(self) -> Dict[str, Dict[str, Any]]:
        """Get asset allocation breakdown."""
        total_value = self.calculate_total_value()
        allocation = {}

        if total_value <= 0:
            return allocation

        assets = self.get_all_assets()

        for asset in assets:
            position_size = asset.calculate_position_size()
            current_price = asset.get_current_price()

            if position_size > 0 and current_price:
                asset_value = position_size * current_price
                allocation[asset.symbol] = {
                    "value": asset_value,
                    "percentage": (asset_value / total_value * 100),
                    "shares": position_size,
                    "price": current_price,
                    "asset_type": asset.asset_type.value,
                    "sector": asset.sector,
                }

        return allocation

    def get_sector_allocation(self) -> Dict[str, Dict[str, Any]]:
        """Get sector allocation breakdown."""
        total_value = self.calculate_total_value()
        sector_allocation = {}

        if total_value <= 0:
            return sector_allocation

        assets = self.get_all_assets()

        for asset in assets:
            position_size = asset.calculate_position_size()
            current_price = asset.get_current_price()

            if position_size > 0 and current_price and asset.sector:
                asset_value = position_size * current_price
                sector = asset.sector

                if sector not in sector_allocation:
                    sector_allocation[sector] = {
                        "value": Decimal("0"),
                        "percentage": Decimal("0"),
                        "assets": [],
                    }

                sector_allocation[sector]["value"] += asset_value
                sector_allocation[sector]["assets"].append(asset.symbol)

        # Calculate percentages
        for sector in sector_allocation:
            sector_value = sector_allocation[sector]["value"]
            sector_allocation[sector]["percentage"] = sector_value / total_value * 100

        return sector_allocation

    def get_performance_metrics(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """Get portfolio performance metrics."""
        cash_flows = self.calculate_cash_flows(start_date, end_date)
        unrealized_gain, unrealized_percent = (
            self.calculate_total_unrealized_gain_loss()
        )

        return {
            "total_value": self.calculate_total_value(),
            "total_cost_basis": self.calculate_total_cost_basis(),
            "unrealized_gain": unrealized_gain,
            "unrealized_gain_percent": unrealized_percent,
            "cash_flows": cash_flows,
            "dividend_yield": self._calculate_dividend_yield(),
            "number_of_positions": len(
                [a for a in self.get_all_assets() if a.calculate_position_size() > 0]
            ),
        }

    def _calculate_dividend_yield(self) -> Decimal:
        """Calculate portfolio dividend yield."""
        total_value = self.calculate_total_value()
        if total_value <= 0:
            return Decimal("0")

        # Calculate annual dividends (simplified - uses last 12 months)
        from datetime import timedelta

        end_date = date.today()
        start_date = end_date - timedelta(days=365)

        cash_flows = self.calculate_cash_flows(start_date, end_date)
        annual_dividends = cash_flows["dividends"]

        return (
            (annual_dividends / total_value * 100) if total_value > 0 else Decimal("0")
        )
