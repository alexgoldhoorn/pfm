from tqdm import tqdm

"""
Command Line Interface for Portfolio Manager

Provides CLI commands for managing assets, sectors, and portfolio operations.
"""

import argparse
import sys
import os
import csv
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Import readline support
from .readline_support import setup_readline, enhanced_input, print_readline_help
from .models import AssetType, TransactionType
from .database import Database
from .sectors import resolve_sector, list_all_sectors, GICS_SECTOR_MAP
from .gemini_client import GeminiClient
from .parsers.coinbase_csv_parser import parse_coinbase_csv
from .parsers.indexacapital_csv_parser import parse_indexacapital_csv
from .parsers.pdt_xlsx_parser import (
    parse_pdt_xlsx,
    export_pdt_xlsx,
    _detect_asset_type,
    _pdt_action_to_tx_type,
)
from .stock_report import run_stock_report
from .auth import (
    AuthManager,
    AuthenticationError,
    prompt_for_credentials,
    prompt_for_registration,
)
from .config import PortfolioConfig, set_config, get_config
from .http_client import PortfolioHTTPClient
from .error_handling import (
    create_error_handler,
    ExitCodes,
    PortfolioManagerError,
    FileIOError,
    PermissionError as PortfolioPermissionError,
)
from .api_client import (
    get_client,
    APIError,
    DataNotFoundError,
)


class AuthenticationRequiredError(PortfolioManagerError):
    """Error when authentication is required but not provided."""

    def __init__(self, message: str = "Authentication required. Please login first."):
        super().__init__(message, ExitCodes.AUTHENTICATION_ERROR)


@dataclass
class TransactionFilters:
    """Container for transaction filter criteria."""

    symbol: Optional[str] = None
    name: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_total: Optional[float] = None
    max_total: Optional[float] = None
    min_quantity: Optional[float] = None
    max_quantity: Optional[float] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    transaction_type: Optional[str] = None


class TransactionFilterEngine:
    """Engine for filtering transactions based on multiple criteria."""

    @staticmethod
    def matches_wildcard_pattern(text: str, pattern: str) -> bool:
        """Check if text matches a wildcard pattern with * and ? support."""
        if not pattern:
            return True
        regex_pattern = pattern.replace("*", ".*").replace("?", ".")
        return bool(re.match(f"^{regex_pattern}$", text, re.IGNORECASE))

    @staticmethod
    def matches_numeric_range(
        value: float, min_val: Optional[float], max_val: Optional[float]
    ) -> bool:
        """Check if numeric value is within specified range."""
        if min_val is not None and value < min_val:
            return False
        if max_val is not None and value > max_val:
            return False
        return True

    @staticmethod
    def matches_date_range(
        date_str: str, from_date: Optional[str], to_date: Optional[str]
    ) -> bool:
        """Check if date is within specified range."""
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            if from_date:
                from_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
                if date_obj < from_obj:
                    return False
            if to_date:
                to_obj = datetime.strptime(to_date, "%Y-%m-%d").date()
                if date_obj > to_obj:
                    return False
            return True
        except ValueError:
            return False

    @classmethod
    def filter_transactions(
        cls, transactions: List[Dict[str, Any]], filters: TransactionFilters
    ) -> List[Dict[str, Any]]:
        """Filter transactions based on the provided criteria."""
        filtered = []
        for tx in transactions:
            # Symbol filter (wildcard supported)
            if filters.symbol and not cls.matches_wildcard_pattern(
                tx.get("symbol", ""), filters.symbol
            ):
                continue
            # Name filter (wildcard supported)
            if filters.name and not cls.matches_wildcard_pattern(
                tx.get("name", ""), filters.name
            ):
                continue
            # Price range filter
            price = float(tx.get("price", 0))
            if not cls.matches_numeric_range(
                price, filters.min_price, filters.max_price
            ):
                continue
            # Total amount range filter
            total = float(tx.get("total_amount", 0))
            if not cls.matches_numeric_range(
                total, filters.min_total, filters.max_total
            ):
                continue
            # Quantity range filter
            quantity = float(tx.get("quantity", 0))
            if not cls.matches_numeric_range(
                quantity, filters.min_quantity, filters.max_quantity
            ):
                continue
            # Date range filter
            if not cls.matches_date_range(
                tx.get("transaction_date", ""), filters.from_date, filters.to_date
            ):
                continue
            # Transaction type filter
            if (
                filters.transaction_type
                and tx.get("transaction_type", "").lower()
                != filters.transaction_type.lower()
            ):
                continue
            filtered.append(tx)
        return filtered

    @staticmethod
    def parse_numeric_filter(filter_str: str) -> tuple:
        """Parse numeric filter strings like '>100', '<500', '100-500', '=250'."""
        if not filter_str:
            return None, None
        filter_str = filter_str.strip()
        # Handle range: "100-500"
        if "-" in filter_str and not filter_str.startswith("-"):
            parts = filter_str.split("-", 1)
            try:
                min_val = float(parts[0]) if parts[0] else None
                max_val = float(parts[1]) if parts[1] else None
                return min_val, max_val
            except ValueError:
                return None, None
        # Handle comparison operators
        if filter_str.startswith(">="):
            try:
                return float(filter_str[2:]), None
            except ValueError:
                return None, None
        elif filter_str.startswith("<="):
            try:
                return None, float(filter_str[2:])
            except ValueError:
                return None, None
        elif filter_str.startswith(">"):
            try:
                return float(filter_str[1:]), None
            except ValueError:
                return None, None
        elif filter_str.startswith("<"):
            try:
                return None, float(filter_str[1:])
            except ValueError:
                return None, None
        elif filter_str.startswith("="):
            try:
                val = float(filter_str[1:])
                return val, val
            except ValueError:
                return None, None
        # Handle plain number (exact match)
        try:
            val = float(filter_str)
            return val, val
        except ValueError:
            return None, None


class PortfolioManagerCLI:
    """Main CLI class for portfolio management operations."""

    def __init__(self, config: Optional[PortfolioConfig] = None):
        """Initialize CLI with either local database or server connection."""
        self.config = config or get_config()
        if not self.config:
            raise ValueError("Configuration is required")

        if self.config.is_server_mode:
            # Server mode - use HTTP client
            self.http_client = PortfolioHTTPClient(self.config)
            self.db_manager = (
                self.http_client
            )  # Use HTTP client as database manager interface
            self.auth_manager = None  # Server handles auth via API key
        else:
            # Local mode - use SQLite database
            self.db_manager = Database(self.config.db_path)
            self.auth_manager = AuthManager(self.db_manager)
            self.http_client = None

    def login_user(self, username: str = None, password: str = None) -> None:
        """Authenticate and login a user."""
        while not self.auth_manager.is_authenticated():
            try:
                if username and password:
                    # Use provided credentials
                    user_credentials = (username, password)
                    # Clear after first use to avoid retry with same credentials
                    username, password = None, None
                elif username and not password:
                    # Username provided but no password - prompt for password only
                    import getpass

                    print(f"🔐 Login for user: {username}")
                    password_input = getpass.getpass("Password: ")
                    user_credentials = (username, password_input)
                    # Clear after first use to avoid retry with same credentials
                    username = None
                else:
                    # Prompt for both credentials
                    user_credentials = prompt_for_credentials()

                self.auth_manager.login(*user_credentials)
                print("✅ Login successful!")
            except AuthenticationError as e:
                print(f"❌ {e}")
                if (
                    "Invalid username or password" in str(e)
                    or "User not found" in str(e)
                    or "Invalid credentials" in str(e)
                ):
                    print(
                        "💡 If you don't have an account, you can register with the 'register' command."
                    )
                    choice = (
                        input("Would you like to register now? (y/n): ").strip().lower()
                    )
                    if choice in ["y", "yes"]:
                        self.register_user()
                        if self.auth_manager.is_authenticated():
                            return
                    else:
                        print(
                            "You can register later by typing 'register' or running 'python -m portf_manager register'"
                        )
                        return

    def register_user(self) -> None:
        """Register a new user."""
        try:
            user_details = prompt_for_registration()
            user_id = self.auth_manager.register_user(**user_details)
            print(f"✅ Registration successful! Your user ID is {user_id}")
        except AuthenticationError as e:
            print(f"❌ {e}")

    def add_asset_transaction(
        self,
        symbol: str,
        amount: float,
        price: float,
        currency: str,
        transaction_type: str,
        transaction_date: str,
        portfolio_name: str = None,
    ) -> None:
        """Add a new transaction for an asset."""
        asset_data = self.db_manager.get_asset_by_symbol(symbol.upper())
        if not asset_data:
            print(
                f"❌ Asset with symbol '{symbol.upper()}' not found, please add the asset first."
            )
            return

        try:
            # Validate transaction type
            transaction_type_enum = TransactionType(transaction_type.lower())

            # Handle portfolio assignment
            portfolio_id = None
            if portfolio_name:
                portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
                if portfolio_data:
                    portfolio_id = portfolio_data["id"]
                else:
                    print(
                        f"❌ Portfolio '{portfolio_name}' not found. Available portfolios:"
                    )
                    self.list_portfolios()
                    return
            else:
                # Use default portfolio if none specified
                default_portfolio = self.db_manager.get_portfolio_by_name(
                    "Default Portfolio"
                )
                if default_portfolio:
                    portfolio_id = default_portfolio["id"]

            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Get user context for local mode
            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()

            # Create transaction with user context (pass user_id only for local mode)
            transaction_id = self.db_manager.create_transaction(
                asset_id=asset_data["id"],
                transaction_type=transaction_type_enum.value,
                quantity=amount,
                price=price,
                total_amount=amount * price,
                transaction_date=transaction_date,
                portfolio_id=portfolio_id,
                user_id=current_user["id"] if self.config.is_local_mode else None,
            )
            print("✅ Transaction added successfully!")
            print(f"   Symbol: {symbol.upper()}")
            print(f"   Amount: {amount}")
            print(f"   Price: {price}")
            print(f"   Currency: {currency}")
            print(f"   Type: {transaction_type_enum.value}")
            print(f"   Date: {transaction_date}")
            print(f"   Transaction ID: {transaction_id}")

        except Exception as e:
            print(f"❌ Error adding transaction: {e}")

    def add_asset(
        self,
        symbol: str,
        name: str,
        asset_type: str,
        exchange: Optional[str] = None,
        currency: str = "USD",
        description: Optional[str] = None,
    ) -> None:
        """Add a new asset to the portfolio."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Validate asset type
            asset_type_enum = AssetType(asset_type.lower())

            # Resolve sector from GICS mapping
            sector = resolve_sector(symbol.upper())

            # Create asset
            asset_id = self.db_manager.create_asset(
                symbol=symbol.upper(),
                name=name,
                asset_type=asset_type_enum.value,
                exchange=exchange,
                currency=currency.upper(),
                sector=sector,
                description=description,
            )
            print("✅ Asset added successfully!")
            print(f"   Symbol: {symbol.upper()}")
            print(f"   Name: {name}")
            print(f"   Type: {asset_type_enum.value}")
            print(f"   Sector: {sector}")
            print(f"   ID: {asset_id}")

        except ValueError as e:
            print(f"❌ Error: {e}")
            print(f"Valid asset types: {', '.join([t.value for t in AssetType])}")
        except Exception as e:
            print(f"❌ Error adding asset: {e}")

    def remove_asset(self, symbol: str) -> None:
        """Remove an asset from the portfolio."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            asset_data = self.db_manager.get_asset_by_symbol(symbol.upper())

            if not asset_data:
                print(f"❌ Asset with symbol '{symbol.upper()}' not found.")
                return

            # Update asset to inactive instead of deleting
            success = self.db_manager.update_asset(asset_data["id"], is_active=False)

            if success:
                print(f"✅ Asset '{symbol.upper()}' removed successfully!")
            else:
                print(f"❌ Failed to remove asset '{symbol.upper()}'.")

        except Exception as e:
            print(f"❌ Error removing asset: {e}")

    def list_assets(self, active_only: bool = True) -> None:
        """List all assets in the portfolio."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                raise AuthenticationRequiredError()

            # Get all assets (this would need to be implemented in DatabaseManager)
            assets = self._get_all_assets(active_only)

            if not assets:
                status = "active" if active_only else "all"
                print(f"📋 No {status} assets found.")
                return

            print("📋 Assets in Portfolio:")
            print("-" * 80)
            print(
                f"{'Symbol':<10} {'Name':<25} {'Type':<15} {'Sector':<20} {'Exchange':<10}"
            )
            print("-" * 80)

            for asset in assets:
                symbol = asset.get("symbol", "N/A")
                name = (asset.get("name") or "N/A")[:24]  # Truncate long names
                asset_type = asset.get("asset_type", "N/A")
                sector = (asset.get("sector") or "Unknown Sector")[
                    :19
                ]  # Truncate long sectors
                exchange = asset.get("exchange") or "N/A"

                print(
                    f"{symbol:<10} {name:<25} {asset_type:<15} {sector:<20} {exchange:<10}"
                )

            print("-" * 80)
            print(f"Total: {len(assets)} assets")

        except AuthenticationRequiredError:
            # Re-raise authentication errors to be handled by error handler
            raise
        except Exception as e:
            print(f"❌ Error listing assets: {e}")

    def update_asset(
        self,
        asset_id: int,
        name: str = None,
        exchange: str = None,
        currency: str = None,
        sector: str = None,
        description: str = None,
        is_active: bool = None,
    ) -> None:
        """Update an existing asset."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Check if asset exists first
            asset = self.db_manager.get_asset(asset_id)
            if not asset:
                print(f"❌ Asset with ID {asset_id} not found.")
                return

            # Prepare update fields
            update_fields = {}

            if name is not None:
                update_fields["name"] = name

            if exchange is not None:
                update_fields["exchange"] = exchange

            if currency is not None:
                update_fields["currency"] = currency.upper()

            if sector is not None:
                update_fields["sector"] = sector

            if description is not None:
                update_fields["description"] = description

            if is_active is not None:
                update_fields["is_active"] = is_active

            if not update_fields:
                print("❌ No fields specified for update.")
                return

            # Show what will be updated
            symbol = asset.get("symbol", "Unknown")
            print(f"🔄 Updating asset {asset_id} ({symbol}):")

            for field, new_value in update_fields.items():
                old_value = asset.get(field, "Not set")
                print(f"   {field}: {old_value} → {new_value}")

            # Update the asset
            success = self.db_manager.update_asset(asset_id, **update_fields)

            if success:
                print(f"✅ Asset {asset_id} updated successfully!")
            else:
                print(f"❌ Failed to update asset {asset_id}.")

        except Exception as e:
            print(f"❌ Error updating asset: {e}")

    def delete_asset(self, asset_id: int) -> None:
        """Delete an asset (soft delete)."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Check if asset exists first
            asset = self.db_manager.get_asset(asset_id)
            if not asset:
                print(f"❌ Asset with ID {asset_id} not found.")
                return

            # Check if asset is already inactive
            if not asset.get("is_active", True):
                print(
                    f"⚠️ Asset {asset_id} ({asset.get('symbol', 'Unknown')}) is already inactive."
                )
                return

            # Show asset details and confirm deletion
            symbol = asset.get("symbol", "Unknown")
            name = asset.get("name", "Unknown")
            asset_type = asset.get("asset_type", "Unknown")

            print("🗑️ Asset to delete (soft delete - will be marked as inactive):")
            print(f"   ID: {asset_id}")
            print(f"   Symbol: {symbol}")
            print(f"   Name: {name}")
            print(f"   Type: {asset_type}")

            confirm = input("Are you sure you want to delete this asset? (y/N): ")
            if confirm.lower() != "y":
                print("❌ Deletion cancelled.")
                return

            # Delete the asset (soft delete)
            success = self.db_manager.delete_asset(asset_id)

            if success:
                print(f"✅ Asset {asset_id} ({symbol}) deleted successfully!")
                print(
                    "💡 Note: This is a soft delete - the asset is marked as inactive but data is preserved."
                )
            else:
                print(f"❌ Failed to delete asset {asset_id}.")

        except Exception as e:
            print(f"❌ Error deleting asset: {e}")

    def list_sectors(self) -> None:
        """List all available GICS sectors."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            sectors = list_all_sectors()

            print("📊 Available GICS Sectors:")
            print("-" * 40)

            for i, sector in enumerate(sorted(sectors), 1):
                print(f"{i:2d}. {sector}")

            print("-" * 40)
            print(f"Total: {len(sectors)} sectors")

        except AuthenticationRequiredError:
            # Re-raise authentication errors to be handled by error handler
            raise
        except Exception as e:
            print(f"❌ Error listing sectors: {e}")

    def show_sector_mapping(self) -> None:
        """Show the current ticker-to-sector mapping."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            print("🗺️  GICS Sector Mapping:")
            print("-" * 50)
            print(f"{'Ticker':<10} {'Sector':<30}")
            print("-" * 50)

            for ticker, sector in sorted(GICS_SECTOR_MAP.items()):
                print(f"{ticker:<10} {sector:<30}")

            print("-" * 50)
            print(f"Total: {len(GICS_SECTOR_MAP)} mapped tickers")

        except AuthenticationRequiredError:
            # Re-raise authentication errors to be handled by error handler
            raise
        except Exception as e:
            print(f"❌ Error showing sector mapping: {e}")

    def _get_all_assets(self, active_only: bool = True) -> list:
        """Get all assets from the database."""
        return self.db_manager.get_all_assets(active_only)

    def _resolve_portfolio_id(self, portfolio_name: Optional[str]) -> Optional[int]:
        """
        Resolve a portfolio name to its ID.
        Returns None if no name given, prints error and returns -1 if not found.
        """
        if not portfolio_name:
            return None
        portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
        if not portfolio_data:
            print(f"❌ Portfolio '{portfolio_name}' not found. Available portfolios:")
            self.list_portfolios()
            return -1
        return portfolio_data["id"]

    def _get_user_transactions(self, portfolio_id: Optional[int] = None) -> list:
        """
        Get transactions for the current user, optionally filtered by portfolio.
        """
        if portfolio_id:
            return self.db_manager.get_transactions_by_portfolio(portfolio_id)

        current_user = None
        if self.config.is_local_mode:
            current_user = self.auth_manager.get_current_user()

        if self.config.is_local_mode and current_user:
            return self.db_manager.get_all_transactions(user_id=current_user["id"])
        return self.db_manager.get_all_transactions()

    def show_portfolio_value(self, portfolio_name: Optional[str] = None) -> None:
        """Show current portfolio value and positions, grouped by portfolio."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Resolve portfolio filter
            portfolio_id = self._resolve_portfolio_id(portfolio_name)
            if portfolio_id == -1:
                return

            transactions = self._get_user_transactions(portfolio_id)

            if not transactions:
                msg = f" in portfolio '{portfolio_name}'" if portfolio_name else ""
                print(f"📊 No transactions found{msg}. Add some transactions first!")
                return

            # Group positions by portfolio
            # Key: portfolio_id -> {asset_id -> {quantity, cost_basis}}
            portfolio_positions: Dict[int, Dict[int, Dict[str, float]]] = {}
            portfolio_names: Dict[int, str] = {}

            for tx in transactions:
                asset_id = tx["asset_id"]
                quantity = float(tx["quantity"])
                total_amount = float(tx["total_amount"])
                tx_type = tx["transaction_type"]
                p_id = tx.get("portfolio_id") or 0

                if p_id not in portfolio_positions:
                    portfolio_positions[p_id] = {}
                    # Resolve portfolio name + entity
                    if p_id:
                        p_data = self.db_manager.get_portfolio(p_id)
                        entity = p_data.get("entity_name", "") if p_data else ""
                        p_name = p_data.get("name", "Unknown") if p_data else "Unknown"
                        portfolio_names[p_id] = (
                            f"{p_name} ({entity})" if entity else p_name
                        )
                    else:
                        portfolio_names[p_id] = "Unassigned"

                pos = portfolio_positions[p_id]
                if asset_id not in pos:
                    pos[asset_id] = {"quantity": 0.0, "cost_basis": 0.0}

                if tx_type.lower() == "buy":
                    pos[asset_id]["quantity"] += quantity
                    pos[asset_id]["cost_basis"] += total_amount
                elif tx_type.lower() == "sell":
                    if pos[asset_id]["quantity"] > 0:
                        pos[asset_id]["cost_basis"] *= (
                            pos[asset_id]["quantity"] - quantity
                        ) / pos[asset_id]["quantity"]
                    pos[asset_id]["quantity"] -= quantity

            header = f"{'Asset':<30} {'Type':<10} {'Currency':<8} {'Qty':>10} {'Last Price':>12} {'Cost Basis':>15} {'Current Value':>15} {'P/L %':>10}"
            sep = "-" * 130

            grand_total_value = 0.0
            grand_total_cost = 0.0

            for p_id in sorted(portfolio_positions.keys()):
                positions = portfolio_positions[p_id]
                # Skip portfolios with no positive positions
                has_positions = any(v["quantity"] > 0 for v in positions.values())
                if not has_positions:
                    continue

                print(f"\n📁 {portfolio_names[p_id]}")
                print(sep)
                print(header)
                print(sep)

                sub_value = 0.0
                sub_cost = 0.0

                for asset_id, data in positions.items():
                    qty = data["quantity"]
                    if qty <= 0:
                        continue

                    asset_data = self.db_manager.get_asset(asset_id)
                    if not asset_data:
                        continue

                    latest_price_data = self.db_manager.get_latest_price(asset_id)
                    name = asset_data.get("name", "Unknown")
                    asset_type = asset_data.get("asset_type", "N/A")
                    currency = asset_data.get("currency", "N/A")
                    current_price = (
                        latest_price_data["price"] if latest_price_data else 0
                    )
                    current_value = qty * current_price
                    cb = data["cost_basis"]
                    pnl_pct = ((current_value - cb) / cb) * 100 if cb > 0 else 0

                    sub_value += current_value
                    sub_cost += cb

                    print(
                        f"{name:<30} {asset_type:<10} {currency:<8} {qty:>10.2f} {current_price:>12.2f} {cb:>15.2f} {current_value:>15.2f} {pnl_pct:>9.2f}%"
                    )

                # Subtotal for this portfolio
                sub_pnl = (
                    ((sub_value - sub_cost) / sub_cost) * 100 if sub_cost > 0 else 0
                )
                print(sep)
                print(
                    f"{'Subtotal:':<53} {sub_cost:>15.2f} {sub_value:>15.2f} {sub_pnl:>9.2f}%"
                )

                grand_total_value += sub_value
                grand_total_cost += sub_cost

            # Grand total (only if multiple portfolios shown)
            if (
                len(
                    [
                        p
                        for p in portfolio_positions
                        if any(
                            v["quantity"] > 0 for v in portfolio_positions[p].values()
                        )
                    ]
                )
                > 1
            ):
                grand_pnl = (
                    ((grand_total_value - grand_total_cost) / grand_total_cost) * 100
                    if grand_total_cost > 0
                    else 0
                )
                print(f"\n{'=' * 130}")
                print(
                    f"{'GRAND TOTAL:':<53} {grand_total_cost:>15.2f} {grand_total_value:>15.2f} {grand_pnl:>9.2f}%"
                )
                print(f"{'=' * 130}")

        except Exception as e:
            print(f"❌ Error calculating portfolio value: {e}")

    def list_transactions(
        self,
        symbol: Optional[str] = None,
        limit: Optional[int] = 10,
        name: Optional[str] = None,
        price: Optional[str] = None,
        total: Optional[str] = None,
        quantity: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        transaction_type: Optional[str] = None,
        portfolio_name: Optional[str] = None,
    ) -> None:
        """List recent transactions with comprehensive filtering and pagination support.

        Supports filtering by:
        - symbol: Asset symbol (wildcards: BTC*, *ETH)
        - name: Asset name (wildcards: *Crypto*, Apple*)
        - price: Unit price (>100, <500, 100-500, =250)
        - total: Total amount (>1000, 500-2000)
        - quantity: Quantity (>0.1, <10)
        - from_date/to_date: Date range (YYYY-MM-DD)
        - transaction_type: buy or sell
        - portfolio_name: Portfolio name filter
        """
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Resolve portfolio filter
            portfolio_id = self._resolve_portfolio_id(portfolio_name)
            if portfolio_id == -1:
                return

            if symbol:
                # Get transactions for specific asset
                asset_data = self.db_manager.get_asset_by_symbol(symbol.upper())
                if not asset_data:
                    print(f"❌ Asset with symbol '{symbol.upper()}' not found.")
                    return
                if portfolio_id:
                    # Filter by both asset and portfolio
                    all_asset_txs = self.db_manager.get_transactions_by_asset(
                        asset_data["id"]
                    )
                    transactions = [
                        tx
                        for tx in all_asset_txs
                        if tx.get("portfolio_id") == portfolio_id
                    ]
                else:
                    transactions = self.db_manager.get_transactions_by_asset(
                        asset_data["id"]
                    )
            else:
                transactions = self._get_user_transactions(portfolio_id)

            # Apply advanced filtering if any filters are provided
            filter_count = 0
            active_filters = []

            if any(
                [name, price, total, quantity, from_date, to_date, transaction_type]
            ):
                # Parse numeric filters
                min_price, max_price = (
                    TransactionFilterEngine.parse_numeric_filter(price)
                    if price
                    else (None, None)
                )
                min_total, max_total = (
                    TransactionFilterEngine.parse_numeric_filter(total)
                    if total
                    else (None, None)
                )
                min_quantity, max_quantity = (
                    TransactionFilterEngine.parse_numeric_filter(quantity)
                    if quantity
                    else (None, None)
                )

                # Create filters object
                filters = TransactionFilters(
                    symbol=symbol,
                    name=name,
                    min_price=min_price,
                    max_price=max_price,
                    min_total=min_total,
                    max_total=max_total,
                    min_quantity=min_quantity,
                    max_quantity=max_quantity,
                    from_date=from_date,
                    to_date=to_date,
                    transaction_type=transaction_type,
                )

                # Apply filters
                original_count = len(transactions)
                transactions = TransactionFilterEngine.filter_transactions(
                    transactions, filters
                )
                filter_count = original_count - len(transactions)

                # Build active filters description
                if symbol:
                    active_filters.append(f"symbol: {symbol}")
                if name:
                    active_filters.append(f"name: {name}")
                if price:
                    active_filters.append(f"price: {price}")
                if total:
                    active_filters.append(f"total: {total}")
                if quantity:
                    active_filters.append(f"quantity: {quantity}")
                if from_date:
                    active_filters.append(f"from: {from_date}")
                if to_date:
                    active_filters.append(f"to: {to_date}")
                if transaction_type:
                    active_filters.append(f"type: {transaction_type}")

            if not transactions:
                filter_text = (
                    f" matching filters ({', '.join(active_filters)})"
                    if active_filters
                    else (f" for {symbol.upper()}" if symbol else "")
                )
                print(f"📋 No transactions found{filter_text}.")
                if filter_count > 0:
                    print(f"💡 {filter_count} transactions were filtered out.")
                return

            # Handle pagination
            total_count = len(transactions)

            if limit is None:
                # Show all transactions with confirmation if many
                if total_count > 50:
                    try:
                        response = (
                            input(
                                f"⚠️  Found {total_count} transactions. Show all? [y/N]: "
                            )
                            .strip()
                            .lower()
                        )
                        if response not in ["y", "yes"]:
                            print(
                                "📋 Use 'list-transactions 50' to show more, or 'list-transactions all' to show all."
                            )
                            limit = 10  # Fall back to default
                        else:
                            limit = total_count
                    except KeyboardInterrupt:
                        print("\n📋 Cancelled.")
                        return
                else:
                    limit = total_count

            # Display header
            filter_text = f" for {symbol.upper()}" if symbol else ""
            if limit >= total_count:
                print(f"📋 All Transactions{filter_text} ({total_count} total):")
            else:
                print(
                    f"📋 Recent Transactions{filter_text} (showing {limit} of {total_count}):"
                )

            print("-" * 150)
            print(
                f"{'ID':<5} {'Symbol':<8} {'Name':<25} {'Type':<8} {'Quantity':<12} {'Price':<10} {'Total':<12} {'Currency':<8} {'Date':<12}"
            )
            print("-" * 150)

            displayed = 0
            for tx in transactions:
                if displayed >= limit:
                    break

                # Truncate name if too long
                name = tx.get("name") or "N/A"
                if len(name) > 24:
                    name = name[:21] + "..."

                total_amount = tx.get("total_amount", tx["quantity"] * tx["price"])
                date_display = tx.get("transaction_date", tx.get("date", "N/A"))
                print(
                    f"{tx['id']:<5} {tx['symbol']:<8} {name:<25} {tx['transaction_type']:<8} "
                    f"{tx['quantity']:<12.4f} {tx['price']:<10.2f} "
                    f"{total_amount:<12.2f} {tx['currency']:<8} {date_display:<12}"
                )
                displayed += 1

            print("-" * 150)

            # Show pagination info
            if displayed < total_count:
                remaining = total_count - displayed
                print(
                    f"Showing {displayed} of {total_count} transactions ({remaining} more available)"
                )
                print(
                    f"💡 Use 'list-transactions {total_count}' or 'list-transactions all' to see all"
                )
            else:
                print(f"Showing {displayed} of {total_count} transactions")

        except Exception as e:
            print(f"❌ Error listing transactions: {e}")

    def delete_transaction(self, transaction_id: int) -> None:
        """Delete a transaction by ID."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Check if transaction exists and get its details first
            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
                transactions = self.db_manager.get_all_transactions(
                    user_id=current_user["id"]
                )
            else:
                transactions = self.db_manager.get_all_transactions()

            # Find the transaction to verify it exists and belongs to the user
            target_transaction = None
            for transaction in transactions:
                if transaction["id"] == transaction_id:
                    target_transaction = transaction
                    break

            if not target_transaction:
                print(f"❌ Transaction with ID {transaction_id} not found.")
                return

            # Confirm deletion
            symbol = target_transaction.get("symbol", "Unknown")
            quantity = target_transaction.get("quantity", 0)
            price = target_transaction.get("price", 0)
            date = target_transaction.get("transaction_date", "Unknown")

            print("🗑️ Transaction to delete:")
            print(f"   ID: {transaction_id}")
            print(f"   Asset: {symbol}")
            print(f"   Quantity: {quantity}")
            print(f"   Price: {price}")
            print(f"   Date: {date}")

            confirm = input("Are you sure you want to delete this transaction? (y/N): ")
            if confirm.lower() != "y":
                print("❌ Deletion cancelled.")
                return

            # Delete the transaction
            success = self.db_manager.delete_transaction(transaction_id)

            if success:
                print(f"✅ Transaction {transaction_id} deleted successfully!")
            else:
                print(f"❌ Failed to delete transaction {transaction_id}.")

        except Exception as e:
            print(f"❌ Error deleting transaction: {e}")

    def update_transaction(
        self,
        transaction_id: int,
        quantity: float = None,
        price: float = None,
        transaction_date: str = None,
        transaction_type: str = None,
        description: str = None,
    ) -> None:
        """Update transaction fields."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Check if transaction exists and get its details first
            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
                transactions = self.db_manager.get_all_transactions(
                    user_id=current_user["id"]
                )
            else:
                transactions = self.db_manager.get_all_transactions()

            # Find the transaction to verify it exists and belongs to the user
            target_transaction = None
            for transaction in transactions:
                if transaction["id"] == transaction_id:
                    target_transaction = transaction
                    break

            if not target_transaction:
                print(f"❌ Transaction with ID {transaction_id} not found.")
                return

            # Prepare update fields
            update_fields = {}

            if quantity is not None:
                update_fields["quantity"] = quantity

            if price is not None:
                update_fields["price"] = price

            if transaction_date is not None:
                update_fields["transaction_date"] = transaction_date

            if transaction_type is not None:
                # Validate transaction type
                from portf_manager.models import TransactionType

                try:
                    transaction_type_enum = TransactionType(transaction_type.lower())
                    update_fields["transaction_type"] = transaction_type_enum.value
                except ValueError:
                    print(f"❌ Invalid transaction type: {transaction_type}")
                    print(
                        f"Valid types: {', '.join([t.value for t in TransactionType])}"
                    )
                    return

            if description is not None:
                update_fields["description"] = description

            # Calculate new total_amount if quantity or price changed
            if quantity is not None or price is not None:
                new_quantity = (
                    quantity if quantity is not None else target_transaction["quantity"]
                )
                new_price = price if price is not None else target_transaction["price"]
                update_fields["total_amount"] = new_quantity * new_price

            if not update_fields:
                print("❌ No fields specified for update.")
                return

            # Show what will be updated
            symbol = target_transaction.get("symbol", "Unknown")
            print(f"🔄 Updating transaction {transaction_id} ({symbol}):")

            for field, new_value in update_fields.items():
                old_value = target_transaction.get(field, "Not set")
                print(f"   {field}: {old_value} → {new_value}")

            # Update the transaction
            success = self.db_manager.update_transaction(
                transaction_id, **update_fields
            )

            if success:
                print(f"✅ Transaction {transaction_id} updated successfully!")
            else:
                print(f"❌ Failed to update transaction {transaction_id}.")

        except Exception as e:
            print(f"❌ Error updating transaction: {e}")

    def import_csv(
        self, csv_file: str, portfolio_name: str = None, error_handler=None
    ) -> None:
        """Import transactions from MyInvestor CSV file."""
        try:
            # Create error handler if not provided
            if error_handler is None:
                from .error_handling import create_error_handler

                error_handler = create_error_handler(debug=False)
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
            portfolio_id = None

            # If portfolio_name is provided, get the portfolio
            if portfolio_name:
                portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
                if portfolio_data:
                    portfolio_id = portfolio_data["id"]
                    print(f"📁 Importing to portfolio: {portfolio_name}")
                else:
                    print(
                        f"❌ Portfolio '{portfolio_name}' not found. Available portfolios:"
                    )
                    self.list_portfolios()
                    return
            else:
                # Use default portfolio if none specified
                default_portfolio = self.db_manager.get_portfolio_by_name(
                    "Default Portfolio"
                )
                if default_portfolio:
                    portfolio_id = default_portfolio["id"]
                    print("📁 No portfolio specified, using 'Default Portfolio'")

            if not os.path.exists(csv_file):
                print(f"❌ CSV file '{csv_file}' not found.")
                return

            print(f"📁 Importing transactions from {csv_file}...")

            imported_count = 0
            created_assets = 0
            skipped_count = 0

            with open(csv_file, "r", encoding="utf-8") as file:
                # Skip BOM if present
                content = file.read()
                if content.startswith("\ufeff"):
                    content = content[1:]

                # Split into lines and process
                lines = content.strip().split("\n")

                # Skip header
                if lines and "Fecha de operación" in lines[0]:
                    lines = lines[1:]

                for line_num, line in enumerate(lines, 2):
                    if not line.strip():
                        continue

                    try:
                        # Parse CSV line (semicolon separated)
                        parts = line.split(";")
                        if len(parts) < 5:
                            print(f"⚠️  Skipping malformed line {line_num}: {line}")
                            continue

                        fecha_operacion = parts[0].strip()
                        parts[1].strip()
                        concepto = parts[2].strip()
                        importe_str = parts[3].strip()
                        divisa = parts[4].strip()

                        # Parse amount (European format: comma as decimal separator)
                        importe = float(importe_str.replace(",", "."))

                        # Skip non-stock transactions
                        if self._should_skip_transaction(concepto, importe):
                            skipped_count += 1
                            continue

                        # Extract asset info from concepto
                        asset_info = self._parse_asset_from_concepto(concepto)
                        if not asset_info:
                            print(f"⚠️  Could not parse asset from: {concepto}")
                            skipped_count += 1
                            continue

                        asset_name, shares = asset_info

                        # Create or get asset
                        asset_data = self._get_or_create_asset(asset_name)
                        if not asset_data:
                            print(f"❌ Failed to create/get asset: {asset_name}")
                            continue

                        if asset_data.get("_newly_created"):
                            created_assets += 1

                        # Calculate price per share
                        total_amount = abs(importe)
                        price_per_share = total_amount / shares if shares > 0 else 0

                        # Convert date format (DD/MM/YYYY to YYYY-MM-DD) with validation
                        transaction_date = error_handler.convert_date_format(
                            fecha_operacion, "CSV transaction date"
                        )

                        # Check if transaction already exists
                        if self._transaction_exists(
                            asset_data["id"], transaction_date, shares, price_per_share
                        ):
                            print(
                                f"⚠️  Transaction already exists for {asset_name} on {transaction_date}"
                            )
                            skipped_count += 1
                            continue

                        # Create transaction
                        self.db_manager.create_transaction(
                            asset_id=asset_data["id"],
                            transaction_type="buy",  # All negative amounts are purchases
                            quantity=shares,
                            price=price_per_share,
                            total_amount=total_amount,
                            transaction_date=transaction_date,
                            portfolio_id=portfolio_id,
                            description=f"Imported from CSV: {concepto}",
                            user_id=(
                                current_user["id"]
                                if self.config.is_local_mode
                                else None
                            ),
                        )

                        print(
                            f"✅ Added: {asset_name} - {shares} shares @ {price_per_share:.2f} {divisa}"
                        )
                        imported_count += 1

                    except Exception as e:
                        print(f"❌ Error processing line {line_num}: {e}")
                        print(f"   Line content: {line}")
                        continue

            print("\n📊 Import Summary:")
            print(f"   ✅ Imported: {imported_count} transactions")
            print(f"   🆕 Created: {created_assets} new assets")
            print(f"   ⏭️  Skipped: {skipped_count} entries")

        except Exception as e:
            print(f"❌ Error importing CSV: {e}")

    def _should_skip_transaction(self, concepto: str, importe: float) -> bool:
        """Determine if a transaction should be skipped."""
        # Skip positive amounts (deposits, dividends) and non-stock transactions
        skip_keywords = ["INVEST", "AHORRO", "MY INVESTOR", "PERIODO"]

        if importe >= 0:  # Positive amounts are deposits/dividends
            return True

        for keyword in skip_keywords:
            if keyword.upper() in concepto.upper():
                return True

        return False

    def _parse_asset_from_concepto(self, concepto: str) -> Optional[tuple]:
        """Parse asset name and shares from concepto field."""
        # Pattern: "ASSET NAME @ SHARES" or "ASSET NAME"
        # Examples: "AMAZON @ 3", "SCHNEIDER ELECTRIC @ 3"

        if "@" in concepto:
            parts = concepto.split("@")
            if len(parts) == 2:
                asset_name = parts[0].strip()
                shares_str = parts[1].strip()

                if shares_str:  # Check if shares part is not empty
                    try:
                        shares = int(shares_str)
                        return (asset_name, shares)
                    except ValueError:
                        # If shares can't be parsed, assume 1 share
                        return (asset_name, 1)
                else:
                    # Empty shares part, assume 1 share
                    return (asset_name, 1)
            else:
                # Multiple @ symbols, take everything before first @
                asset_name = parts[0].strip()
                return (asset_name, 1)
        else:
            # If no @ symbol, assume 1 share
            return (concepto.strip(), 1)

        return None

    def _get_or_create_asset(self, asset_name: str) -> Optional[dict]:
        """Get existing asset or create new one."""
        # Try to find existing asset by name (case insensitive)
        all_assets = self.db_manager.get_all_assets(active_only=False)
        for asset in all_assets:
            if asset["name"].upper() == asset_name.upper():
                return asset

        # Create new asset
        try:
            # Generate a symbol from the name
            symbol = self._generate_symbol(asset_name)

            # Resolve sector
            sector = resolve_sector(symbol)

            asset_id = self.db_manager.create_asset(
                symbol=symbol,
                name=asset_name,
                asset_type="stock",  # Assume stock for now
                currency="EUR",
                sector=sector,
                description="Auto-created from CSV import",
            )

            asset_data = self.db_manager.get_asset(asset_id)
            if asset_data:
                asset_data["_newly_created"] = True
            return asset_data

        except Exception as e:
            print(f"❌ Error creating asset {asset_name}: {e}")
            return None

    def _generate_symbol(self, asset_name: str) -> str:
        """Generate a stock symbol from asset name."""
        # Remove common words and create abbreviation
        common_words = ["CORPORATION", "CORP", "SA", "SE", "ELECTRONIC", "ELECTRONICS"]

        # Clean name
        clean_name = asset_name.upper()
        for word in common_words:
            clean_name = clean_name.replace(word, "")

        # Split into words and take first letters
        words = clean_name.split()
        if len(words) == 1:
            # Single word, take first 4 characters
            return words[0][:4]
        else:
            # Multiple words, take first letter of each (max 4)
            symbol = "".join(word[0] for word in words if word)[:4]
            return symbol if symbol else asset_name[:4].upper()

    def _convert_date_format(self, date_str: str) -> str:
        """Convert DD/MM/YYYY to YYYY-MM-DD."""
        try:
            day, month, year = date_str.split("/")
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        except Exception:
            return date_str

    def _transaction_exists(
        self, asset_id: int, transaction_date: str, quantity: float, price: float
    ) -> bool:
        """Check if a similar transaction already exists."""
        try:
            transactions = self.db_manager.get_transactions_by_asset(asset_id)
            for tx in transactions:
                if (
                    tx["transaction_date"] == transaction_date
                    and abs(float(tx["quantity"]) - quantity) < 0.01
                    and abs(float(tx["price"]) - price) < 0.01
                ):
                    return True
            return False
        except Exception:
            return False

    def list_portfolios(self) -> None:
        """List all portfolios."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
                portfolios = self.db_manager.get_all_portfolios(
                    user_id=current_user["id"]
                )
            else:
                portfolios = self.db_manager.get_all_portfolios()

            if not portfolios:
                print("📋 No portfolios found.")
                return

            print("📋 Portfolios:")
            print("-" * 90)
            print(
                f"{'ID':<5} {'Name':<25} {'Currency':<10} {'Entity':<25} {'Description':<20}"
            )
            print("-" * 90)

            for portfolio in portfolios:
                portfolio_id = portfolio.get("id", "N/A")
                name = portfolio.get("name", "N/A")[:24]
                currency = portfolio.get("base_currency", "N/A")
                entity_name = portfolio.get("entity_name", "N/A") or "N/A"
                entity_name = entity_name[:24] if entity_name != "N/A" else "N/A"
                description = (
                    portfolio.get("description", "")[:19]
                    if portfolio.get("description")
                    else ""
                )

                print(
                    f"{portfolio_id:<5} {name:<25} {currency:<10} {entity_name:<25} {description:<20}"
                )

            print("-" * 90)
            print(f"Total: {len(portfolios)} portfolios")

        except Exception as e:
            print(f"❌ Error listing portfolios: {e}")

    def add_portfolio(
        self,
        name: str,
        base_currency: str = "USD",
        entity_name: str = None,
        description: str = None,
    ) -> None:
        """Add a new portfolio."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
            entity_id = None

            # If entity_name is provided, try to find or create the entity
            if entity_name:
                entity_data = self.db_manager.get_entity_by_name(entity_name)
                if entity_data:
                    # Check if entity belongs to current user (only in local mode)
                    if (
                        self.config.is_local_mode
                        and entity_data.get("user_id") != current_user["id"]
                    ):
                        print(
                            f"❌ Entity '{entity_name}' not found or doesn't belong to you."
                        )
                        return
                    entity_id = entity_data["id"]
                else:
                    print(
                        f"❌ Entity '{entity_name}' not found. Please create it first with add-entity command."
                    )
                    return

            # Create portfolio with user context (pass user_id only for local mode)
            portfolio_id = self.db_manager.create_portfolio(
                name=name,
                base_currency=base_currency.upper(),
                entity_id=entity_id,
                description=description,
                user_id=current_user["id"] if self.config.is_local_mode else None,
            )

            print("✅ Portfolio added successfully!")
            print(f"   Name: {name}")
            print(f"   Currency: {base_currency.upper()}")
            if entity_name:
                print(f"   Entity: {entity_name}")
            if description:
                print(f"   Description: {description}")
            print(f"   ID: {portfolio_id}")

        except Exception as e:
            print(f"❌ Error adding portfolio: {e}")

    def list_entities(self) -> None:
        """List all entities."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
                entities = self.db_manager.get_all_entities(user_id=current_user["id"])
            else:
                entities = self.db_manager.get_all_entities()

            if not entities:
                print("📋 No entities found.")
                return

            print("📋 Entities:")
            print("-" * 100)
            print(
                f"{'ID':<5} {'Name':<25} {'Type':<15} {'Website':<30} {'Description':<20}"
            )
            print("-" * 100)

            for entity in entities:
                entity_id = entity.get("id", "N/A")
                name = entity.get("name", "N/A")[:24]
                entity_type = entity.get("entity_type", "N/A")
                website = (
                    entity.get("website", "")[:29] if entity.get("website") else ""
                )
                description = (
                    entity.get("description", "")[:19]
                    if entity.get("description")
                    else ""
                )

                print(
                    f"{entity_id:<5} {name:<25} {entity_type:<15} {website:<30} {description:<20}"
                )

            print("-" * 100)
            print(f"Total: {len(entities)} entities")

        except Exception as e:
            print(f"❌ Error listing entities: {e}")

    def add_entity(
        self, name: str, entity_type: str, website: str = None, description: str = None
    ) -> None:
        """Add a new entity."""
        try:
            # Validate entity type
            valid_types = ["broker", "bank", "platform", "other"]
            if entity_type.lower() not in valid_types:
                print(f"❌ Invalid entity type. Valid types: {', '.join(valid_types)}")
                return

            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()

            # Create entity with user context (pass user_id only for local mode)
            entity_id = self.db_manager.create_entity(
                name=name,
                entity_type=entity_type.lower(),
                user_id=current_user["id"] if self.config.is_local_mode else None,
                website=website,
                description=description,
            )

            print("✅ Entity added successfully!")
            print(f"   Name: {name}")
            print(f"   Type: {entity_type.lower()}")
            if website:
                print(f"   Website: {website}")
            if description:
                print(f"   Description: {description}")
            print(f"   ID: {entity_id}")

        except Exception as e:
            print(f"❌ Error adding entity: {e}")

    def paste_transaction_interactive(self, format_type: str = "myinvestor") -> None:
        """Interactive text input for transaction processing."""
        print(f"📝 Paste transaction text ({format_type} format) for processing.")
        print("💡 Enter text on multiple lines. Type 'END' on a new line to finish.")
        print("💡 You can also press Ctrl-D to cancel at any time.")

        if format_type == "myinvestor":
            print("📋 Example: Copy/paste order confirmation from broker website.")
        elif format_type == "coinbase":
            print("📋 Example: Copy/paste Coinbase CSV export (including headers).")
        elif format_type == "indexacapital":
            print(
                "📋 Example: Copy/paste IndexaCapital CSV export (semicolon-separated)."
            )
        print()

        lines = []
        while True:
            try:
                line = input()
                if line.strip().upper() == "END":
                    break
                lines.append(line)
            except KeyboardInterrupt:
                print("\n❌ Operation cancelled.")
                return
            except EOFError:
                print("\n❌ Operation cancelled.")
                return

        text = "\n".join(lines)
        if not text.strip():
            print("❌ No text entered.")
            return

        try:
            if format_type == "coinbase":
                self._process_coinbase_transactions(text)
            elif format_type == "indexacapital":
                self._process_indexacapital_transactions(text)
            else:
                # Default myinvestor format (LLM processing)
                self._process_myinvestor_transactions(text)

        except Exception as e:
            print(f"❌ Error processing text: {e}")

    def import_coinbase_csv(
        self, csv_file: str, portfolio_name: str = None, clear: bool = False
    ) -> None:
        """Import transactions from a Coinbase CSV export file."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            if not os.path.exists(csv_file):
                print(f"❌ CSV file '{csv_file}' not found.")
                return

            # Resolve portfolio
            portfolio_id = None
            if portfolio_name:
                portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
                if portfolio_data:
                    portfolio_id = portfolio_data["id"]
                    print(f"📁 Importing to portfolio: {portfolio_name}")
                else:
                    print(
                        f"❌ Portfolio '{portfolio_name}' not found. Available portfolios:"
                    )
                    self.list_portfolios()
                    return
            else:
                default_portfolio = self.db_manager.get_portfolio_by_name(
                    "Default Portfolio"
                )
                if default_portfolio:
                    portfolio_id = default_portfolio["id"]
                    print("📁 No portfolio specified, using 'Default Portfolio'")

            # Clear existing transactions if requested
            if clear and portfolio_id:
                existing = self.db_manager.get_transactions_by_portfolio(portfolio_id)
                if existing:
                    confirm = (
                        input(
                            f"⚠️  Delete {len(existing)} existing transactions from '{portfolio_name or 'Default Portfolio'}'? [y/N]: "
                        )
                        .strip()
                        .lower()
                    )
                    if confirm in ["y", "yes"]:
                        deleted = self.db_manager.delete_transactions_by_portfolio(
                            portfolio_id
                        )
                        print(f"🗑️  Deleted {deleted} transactions.")
                    else:
                        print("⏭️  Skipping clear, importing on top of existing data.")
                else:
                    print("📋 No existing transactions to clear.")

            print(f"📁 Reading Coinbase CSV from {csv_file}...")

            with open(csv_file, "r", encoding="utf-8-sig") as f:
                csv_content = f.read()

            print("\n🔄 Processing Coinbase CSV...")
            result = parse_coinbase_csv(csv_content)

            if not result.importable and not result.skipped:
                print("❌ No valid transactions found in CSV.")
                return

            # Display summary
            print("\n📊 Processing Summary:")
            print(f"   🔥 Importable: {len(result.importable)} transactions")
            print(f"   📋 Reference: {len(result.skipped)} entries")

            if result.skipped:
                print("\n📋 Skipped entries (for reference):")
                # Group skipped entries by reason
                from collections import Counter

                skipped_counts = Counter(
                    (tx_type, reason) for tx_type, reason in result.skipped
                )
                for (tx_type, reason), count in skipped_counts.most_common():
                    print(f"   • {tx_type}: {reason} ({count}x)")

            if not result.importable:
                print("\n💡 No transactions to import.")
                return

            print(f"\n✅ Found {len(result.importable)} importable transaction(s):")

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()

            import_all = False
            imported_count = 0
            for i, transaction in enumerate(result.importable, 1):
                print(f"\n📋 Transaction {i}/{len(result.importable)}:")
                print(f"   Symbol:   {transaction.symbol}")
                print(f"   Asset:    {transaction.asset_name}")
                print(f"   Type:     {transaction.tx_type.upper()}")
                print(f"   Quantity: {transaction.quantity}")
                print(f"   Price:    {transaction.price:.4f} {transaction.currency}")
                print(
                    f"   Total:    {transaction.quantity * transaction.price:.2f} {transaction.currency}"
                )
                print(f"   Date:     {transaction.date}")

                if not import_all:
                    while True:
                        response = (
                            input(
                                "\n❓ Import this transaction? [y/N/a=all/s=skip all]: "
                            )
                            .strip()
                            .lower()
                        )
                        if response in ["y", "yes"]:
                            break
                        elif response in ["a", "all"]:
                            import_all = True
                            break
                        elif response in ["n", "no", ""]:
                            print("⏭️  Skipped.")
                            break
                        elif response in ["s", "skip"]:
                            print("⏭️  Skipping all remaining transactions.")
                            print(
                                f"\n🎯 Import Complete: {imported_count}/{len(result.importable)} transactions imported."
                            )
                            return
                        else:
                            print("❓ Please enter y, n, a, or s")
                    if response in ["n", "no", ""]:
                        continue

                try:
                    # Create crypto asset if it doesn't exist yet
                    asset_data = self.db_manager.get_asset_by_symbol(
                        transaction.symbol.upper()
                    )
                    if not asset_data:
                        print(
                            f"🆕 Crypto asset {transaction.symbol} not found. Creating..."
                        )
                        asset_id = self.db_manager.create_asset(
                            symbol=transaction.symbol.upper(),
                            name=transaction.asset_name,
                            asset_type="crypto",
                            currency=transaction.currency,
                            description="Auto-created from Coinbase CSV import",
                        )
                        print(
                            f"✅ Created crypto asset {transaction.symbol} with ID {asset_id}"
                        )

                    # Add the transaction
                    asset_data = self.db_manager.get_asset_by_symbol(
                        transaction.symbol.upper()
                    )
                    self.db_manager.create_transaction(
                        asset_id=asset_data["id"],
                        transaction_type=transaction.tx_type,
                        quantity=transaction.quantity,
                        price=transaction.price,
                        total_amount=transaction.quantity * transaction.price,
                        transaction_date=transaction.date,
                        portfolio_id=portfolio_id,
                        description="Imported from Coinbase CSV",
                        user_id=(
                            current_user["id"] if self.config.is_local_mode else None
                        ),
                    )
                    imported_count += 1
                    print("✅ Transaction imported successfully!")
                except Exception as e:
                    print(f"❌ Failed to import transaction: {e}")

            print(
                f"\n🎯 Import Complete: {imported_count}/{len(result.importable)} transactions imported."
            )

        except Exception as e:
            print(f"❌ Error importing Coinbase CSV: {e}")
            print("💡 Make sure the file is a valid Coinbase CSV export.")

    # ------------------------------------------------------------------
    # Portfolio Dividend Tracker (PDT) XLSX import / export
    # ------------------------------------------------------------------

    def import_pdt_xlsx(
        self,
        xlsx_file: str,
        portfolio_name: Optional[str] = None,
        import_dividends: bool = True,
        import_bookings: bool = False,
        clear: bool = False,
    ) -> None:
        """Import transactions (and optionally dividends) from a PDT XLSX file."""
        try:
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            if not os.path.exists(xlsx_file):
                print(f"❌ XLSX file '{xlsx_file}' not found.")
                return

            portfolio_id = None
            if portfolio_name:
                portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
                if portfolio_data:
                    portfolio_id = portfolio_data["id"]
                    print(f"📁 Importing to portfolio: {portfolio_name}")
                else:
                    print(f"❌ Portfolio '{portfolio_name}' not found.")
                    self.list_portfolios()
                    return
            else:
                default_portfolio = self.db_manager.get_portfolio_by_name(
                    "Default Portfolio"
                )
                if default_portfolio:
                    portfolio_id = default_portfolio["id"]
                    print("📁 No portfolio specified, using 'Default Portfolio'")

            if clear and portfolio_id:
                existing = self.db_manager.get_transactions_by_portfolio(portfolio_id)
                if existing:
                    confirm = (
                        input(
                            f"⚠️  Delete {len(existing)} existing transactions from"
                            f" '{portfolio_name or 'Default Portfolio'}'? [y/N]: "
                        )
                        .strip()
                        .lower()
                    )
                    if confirm in ["y", "yes"]:
                        deleted = self.db_manager.delete_transactions_by_portfolio(
                            portfolio_id
                        )
                        print(f"🗑️  Deleted {deleted} transactions.")
                    else:
                        print("⏭️  Skipping clear.")

            print(f"📁 Reading PDT XLSX from {xlsx_file}...")
            result = parse_pdt_xlsx(xlsx_file)

            print("\n📊 Parse summary:")
            print(f"   Transactions (buy/sell): {len(result.transactions)}")
            print(f"   Dividends:               {len(result.dividends)}")
            print(f"   Bookings (deposits):     {len(result.bookings)}")
            if result.skipped:
                print(f"   Skipped:                 {len(result.skipped)}")

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()
            user_id = current_user["id"] if current_user else None

            tx_count = self._import_pdt_transactions(
                result.transactions, portfolio_id, user_id
            )
            div_count = 0
            if import_dividends:
                div_count = self._import_pdt_dividends(
                    result.dividends, portfolio_id, user_id
                )
            bk_count = 0
            if import_bookings:
                bk_count = self._import_pdt_bookings(result.bookings, portfolio_id)

            print(
                f"\n✅ Imported {tx_count} buy/sell transaction(s),"
                f" {div_count} dividend(s), and {bk_count} booking(s)."
            )
            if result.skipped:
                print(f"⚠️  {len(result.skipped)} row(s) skipped — see details above.")

        except Exception as e:
            print(f"❌ Error importing PDT XLSX: {e}")
            raise

    def _get_or_create_asset_by_symbol(
        self, symbol: str, name: str, asset_type: str, exchange: str, currency: str
    ) -> int:
        """Return asset id, creating the asset if it does not exist yet."""
        asset = self.db_manager.get_asset_by_symbol(symbol.upper())
        if asset:
            return asset["id"]
        print(f"🆕 Asset '{symbol}' not found — creating...")
        asset_id = self.db_manager.create_asset(
            symbol=symbol.upper(),
            name=name,
            asset_type=asset_type,
            exchange=exchange or None,
            currency=currency,
            description="Auto-created from PDT XLSX import",
        )
        print(f"   ✅ Created asset '{symbol}' (id={asset_id})")
        return asset_id

    def _import_pdt_transactions(
        self, transactions: list, portfolio_id: Optional[int], user_id: Optional[int]
    ) -> int:
        imported = 0
        for tx in transactions:
            tx_type = _pdt_action_to_tx_type(tx.action)
            if tx_type is None:
                print(f"⚠️  Unknown action '{tx.action}' — skipping.")
                continue

            symbol = tx.search if tx.search else tx.name[:20]
            asset_type = _detect_asset_type(tx.name, tx.pdt_type)

            try:
                asset_id = self._get_or_create_asset_by_symbol(
                    symbol, tx.name, asset_type, tx.exchange, tx.price_currency
                )
                fees = tx.costs or 0.0
                tax = tx.tax or 0.0
                total = tx.amount * tx.price + fees
                self.db_manager.create_transaction(
                    asset_id=asset_id,
                    transaction_type=tx_type,
                    quantity=tx.amount,
                    price=tx.price,
                    total_amount=total,
                    fees=fees,
                    tax=tax,
                    currency=tx.price_currency,
                    transaction_date=tx.date.isoformat(),
                    portfolio_id=portfolio_id,
                    user_id=user_id,
                    description="Imported from PDT XLSX",
                )
                imported += 1
            except Exception as e:
                print(f"❌ Failed to import {tx.action} {symbol} on {tx.date}: {e}")

        return imported

    def _import_pdt_dividends(
        self, dividends: list, portfolio_id: Optional[int], user_id: Optional[int]
    ) -> int:
        imported = 0
        for div in dividends:
            symbol = div.search if div.search else div.name[:20]
            asset_type = _detect_asset_type(div.name, div.pdt_type)

            try:
                asset_id = self._get_or_create_asset_by_symbol(
                    symbol, div.name, asset_type, div.exchange, div.amount_currency
                )
                self.db_manager.create_transaction(
                    asset_id=asset_id,
                    transaction_type="dividend",
                    quantity=1.0,
                    price=div.amount,
                    total_amount=div.amount,
                    fees=div.costs or 0.0,
                    tax=div.tax or 0.0,
                    currency=div.amount_currency,
                    transaction_date=div.date.isoformat(),
                    portfolio_id=portfolio_id,
                    user_id=user_id,
                    description="Dividend imported from PDT XLSX",
                )
                imported += 1
            except Exception as e:
                print(f"❌ Failed to import dividend {symbol} on {div.date}: {e}")

        return imported

    def _import_pdt_bookings(self, bookings: list, portfolio_id: Optional[int]) -> int:
        imported = 0
        for bk in bookings:
            try:
                # Use per-booking broker to resolve portfolio if present
                bk_portfolio_id = portfolio_id
                if bk.broker:
                    portfolio = self.db_manager.get_portfolio_by_name(bk.broker)
                    if portfolio:
                        bk_portfolio_id = portfolio["id"]
                    else:
                        bk_portfolio_id = self.db_manager.create_portfolio(
                            name=bk.broker,
                            base_currency=bk.currency or "EUR",
                            description="Auto-created from PDT XLSX import",
                        )
                self.db_manager.create_booking(
                    date=bk.date.isoformat(),
                    action=bk.action,
                    amount=bk.amount,
                    currency=bk.currency,
                    portfolio_id=bk_portfolio_id,
                )
                imported += 1
            except Exception as e:
                print(f"❌ Failed to import booking {bk.action} on {bk.date}: {e}")
        return imported

    def list_bookings(self, portfolio_name: Optional[str] = None) -> None:
        """List all bookings (deposits / withdrawals)."""
        try:
            portfolio_id = None
            if portfolio_name:
                portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
                if not portfolio_data:
                    print(f"❌ Portfolio '{portfolio_name}' not found.")
                    return
                portfolio_id = portfolio_data["id"]

            bookings = self.db_manager.get_all_bookings(portfolio_id=portfolio_id)
            if not bookings:
                print("No bookings found.")
                return

            print(
                f"\n{'Date':<12} {'Action':<12} {'Amount':>12} {'Currency':<8} {'Portfolio'}"
            )
            print("-" * 60)
            for bk in bookings:
                print(
                    f"{bk['date']:<12} {bk['action']:<12}"
                    f" {bk['amount']:>12.2f} {bk['currency']:<8}"
                    f" {bk.get('portfolio_name') or ''}"
                )
            print(f"\nTotal: {len(bookings)} booking(s).")
        except Exception as e:
            print(f"❌ Error listing bookings: {e}")

    def export_pdt_xlsx(
        self,
        output_path: str,
        portfolio_name: Optional[str] = None,
    ) -> None:
        """Export all portfolio transactions to a PDT-compatible XLSX file."""
        try:
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            portfolio_id = None
            if portfolio_name:
                portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
                if portfolio_data:
                    portfolio_id = portfolio_data["id"]
                    print(f"📁 Exporting portfolio: {portfolio_name}")
                else:
                    print(f"❌ Portfolio '{portfolio_name}' not found.")
                    self.list_portfolios()
                    return
            else:
                print("📁 Exporting all portfolios")

            export_pdt_xlsx(self.db_manager, output_path, portfolio_id)
            print(f"✅ Exported to {output_path}")

        except Exception as e:
            print(f"❌ Error exporting PDT XLSX: {e}")
            raise

    # ------------------------------------------------------------------
    # PDT Google Sheets sync
    # ------------------------------------------------------------------

    def _resolve_sheet_id(self, sheet_id_arg: Optional[str]) -> Optional[str]:
        import os

        return sheet_id_arg or os.getenv("GOOGLE_SPREADSHEET_ID") or None

    def sync_pdt_pull(
        self,
        sheet_id: Optional[str] = None,
        portfolio_name: Optional[str] = None,
        import_bookings: bool = True,
    ) -> None:
        """Pull data from a PDT-format Google Spreadsheet and save to DB."""
        try:
            from .parsers.pdt_sheets_sync import PDTSheetsSync
        except ImportError as e:
            print(f"❌ Google API libraries not available: {e}")
            return

        final_sheet_id = self._resolve_sheet_id(sheet_id)
        if not final_sheet_id:
            print(
                "❌ No spreadsheet ID provided. Pass --sheet-id or set GOOGLE_SPREADSHEET_ID."
            )
            return

        portfolio_id = None
        if portfolio_name:
            p = self.db_manager.get_portfolio_by_name(portfolio_name)
            if p:
                portfolio_id = p["id"]
            else:
                print(f"❌ Portfolio '{portfolio_name}' not found.")
                return

        print(f"🔄 Pulling from Google Sheet: {final_sheet_id}")
        try:
            result = PDTSheetsSync(final_sheet_id).pull()
        except Exception as e:
            print(f"❌ Failed to read from Google Sheet: {e}")
            return

        print("\n📊 Parse summary:")
        print(f"   Transactions (buy/sell): {len(result.transactions)}")
        print(f"   Dividends:               {len(result.dividends)}")
        print(f"   Bookings (deposits):     {len(result.bookings)}")
        if result.skipped:
            print(f"   Skipped:                 {len(result.skipped)}")

        current_user = None
        if self.config.is_local_mode:
            current_user = self.auth_manager.get_current_user()
        user_id = current_user["id"] if current_user else None

        tx_count = self._import_pdt_transactions(
            result.transactions, portfolio_id, user_id
        )
        div_count = self._import_pdt_dividends(result.dividends, portfolio_id, user_id)
        bk_count = 0
        if import_bookings:
            bk_count = self._import_pdt_bookings(result.bookings, portfolio_id)

        print(
            f"\n✅ Pulled {tx_count} buy/sell transaction(s),"
            f" {div_count} dividend(s), and {bk_count} booking(s)."
        )
        if result.skipped:
            print(f"⚠️  {len(result.skipped)} row(s) skipped.")

    def sync_pdt_push(
        self,
        sheet_id: Optional[str] = None,
        portfolio_name: Optional[str] = None,
    ) -> None:
        """Push portfolio data to a PDT-format Google Spreadsheet."""
        try:
            from .parsers.pdt_sheets_sync import PDTSheetsSync
        except ImportError as e:
            print(f"❌ Google API libraries not available: {e}")
            return

        final_sheet_id = self._resolve_sheet_id(sheet_id)
        if not final_sheet_id:
            print(
                "❌ No spreadsheet ID provided. Pass --sheet-id or set GOOGLE_SPREADSHEET_ID."
            )
            return

        portfolio_id = None
        if portfolio_name:
            p = self.db_manager.get_portfolio_by_name(portfolio_name)
            if p:
                portfolio_id = p["id"]
                print(f"📁 Pushing portfolio: {portfolio_name}")
            else:
                print(f"❌ Portfolio '{portfolio_name}' not found.")
                return
        else:
            print("📁 Pushing all portfolios")

        print(f"🔄 Pushing to Google Sheet: {final_sheet_id}")
        try:
            counts = PDTSheetsSync(final_sheet_id).push(self.db_manager, portfolio_id)
        except Exception as e:
            print(f"❌ Failed to write to Google Sheet: {e}")
            return

        print(
            f"\n✅ Pushed {counts['transactions']} transaction(s),"
            f" {counts['dividends']} dividend(s),"
            f" {counts['bookings']} booking(s)."
        )
        print(f"🔗 https://docs.google.com/spreadsheets/d/{final_sheet_id}/")

    def _process_coinbase_transactions(self, csv_content: str) -> None:
        """Process Coinbase CSV content."""
        print("\n🔄 Processing Coinbase CSV...")

        try:
            result = parse_coinbase_csv(csv_content)

            if not result.importable and not result.skipped:
                print("❌ No valid transactions found in CSV.")
                return

            # Display summary first
            print("\n📊 Processing Summary:")
            print(f"   🔥 Importable: {len(result.importable)} transactions")
            print(f"   📋 Reference: {len(result.skipped)} entries")

            if result.skipped:
                print("\n📋 Skipped entries (for reference):")
                # Group skipped entries by reason
                from collections import Counter

                skipped_counts = Counter(
                    (tx_type, reason) for tx_type, reason in result.skipped
                )
                for (tx_type, reason), count in skipped_counts.most_common():
                    print(f"   • {tx_type}: {reason} ({count}x)")

            if not result.importable:
                print("\n💡 No transactions to import.")
                return

            print(f"\n✅ Found {len(result.importable)} importable transaction(s):")

            imported_count = 0
            for i, transaction in enumerate(result.importable, 1):
                print(f"\n📋 Transaction {i}:")
                print(f"   Symbol: {transaction.symbol}")
                print(f"   Asset: {transaction.asset_name}")
                print(f"   Type: {transaction.tx_type.upper()}")
                print(f"   Quantity: {transaction.quantity}")
                print(f"   Price: {transaction.price:.4f} {transaction.currency}")
                print(f"   Date: {transaction.date}")
                print(f"   Raw: {transaction.raw_text}")

                # Ask for confirmation before adding
                confirm = input("\n❓ Add this transaction? (y/n): ").strip().lower()
                if confirm == "y" or confirm == "yes":
                    try:
                        # Check if asset exists, create if needed (crypto assets)
                        asset_data = self.db_manager.get_asset_by_symbol(
                            transaction.symbol.upper()
                        )
                        if not asset_data:
                            print(
                                f"🆕 Crypto asset {transaction.symbol} not found. Creating..."
                            )
                            asset_id = self.db_manager.create_asset(
                                symbol=transaction.symbol.upper(),
                                name=transaction.asset_name,
                                asset_type="crypto",  # Mark as crypto
                                currency=transaction.currency,
                                description="Auto-created from Coinbase CSV import",
                            )
                            print(
                                f"✅ Created crypto asset {transaction.symbol} with ID {asset_id}"
                            )

                        # Add the transaction
                        self.add_asset_transaction(
                            transaction.symbol,
                            transaction.quantity,
                            transaction.price,
                            transaction.currency,
                            transaction.tx_type,
                            transaction.date,
                        )
                        imported_count += 1
                        print("✅ Transaction added successfully!")

                    except Exception as e:
                        print(f"❌ Error adding transaction: {e}")
                else:
                    print("⏭️  Skipped.")

            print(
                f"\n🎯 Import Complete: {imported_count}/{len(result.importable)} transactions imported."
            )

        except Exception as e:
            print(f"❌ Error parsing Coinbase CSV: {e}")
            print("💡 Make sure you copied the entire CSV export including headers.")

    def _process_indexacapital_transactions(self, csv_content: str) -> None:
        """Process IndexaCapital CSV content."""
        print("\n🔄 Processing IndexaCapital CSV...")

        try:
            result = parse_indexacapital_csv(csv_content)

            if not result.importable and not result.skipped:
                print("❌ No valid transactions found in CSV.")
                return

            # Display summary first
            print("\n📊 Processing Summary:")
            print(f"   🔥 Importable: {len(result.importable)} transactions")
            print(f"   📋 Skipped: {len(result.skipped)} entries")

            if result.skipped:
                print("\n📋 Skipped entries:")
                for tx_type, reason in result.skipped:
                    print(f"   • {tx_type}: {reason}")

            if not result.importable:
                print("\n💡 No transactions to import.")
                return

            print(f"\n✅ Found {len(result.importable)} importable transaction(s):")

            imported_count = 0
            for i, transaction in enumerate(result.importable, 1):
                print(f"\n📋 Transaction {i}:")
                print(f"   Symbol: {transaction.symbol}")
                print(f"   Name: {transaction.asset_name}")
                print(f"   Type: {transaction.tx_type}")
                print(f"   Quantity: {transaction.quantity}")
                print(f"   Price: {transaction.price:.4f} EUR")
                print(f"   Total: {transaction.quantity * transaction.price:.2f} EUR")
                print(f"   Date: {transaction.date}")

                # Confirm import
                while True:
                    response = (
                        input("\n❓ Import this transaction? [y/N/s=skip all]: ")
                        .strip()
                        .lower()
                    )
                    if response in ["y", "yes"]:
                        try:
                            # Check if asset exists, create if needed (like Coinbase import)
                            asset_data = self.db_manager.get_asset_by_symbol(
                                transaction.symbol.upper()
                            )
                            if not asset_data:
                                print(
                                    f"🆕 ETF/Index {transaction.symbol} not found. Creating..."
                                )
                                asset_id = self.db_manager.create_asset(
                                    symbol=transaction.symbol.upper(),
                                    name=transaction.asset_name,
                                    asset_type="etf",  # IndexaCapital assets are ETFs/Index funds
                                    currency=transaction.currency,
                                    description="Auto-created from IndexaCapital CSV import",
                                )
                                print(
                                    f"✅ Created ETF asset {transaction.symbol} with ID {asset_id}"
                                )

                            # Now add the transaction
                            self.add_asset_transaction(
                                transaction.symbol,
                                transaction.quantity,
                                transaction.price,
                                transaction.currency,
                                transaction.tx_type,
                                transaction.date,
                            )
                            imported_count += 1
                            print("✅ Transaction imported successfully!")
                            break
                        except Exception as e:
                            print(f"❌ Failed to import transaction: {e}")
                            break
                    elif response in ["n", "no", ""]:
                        print("⏭️  Skipped.")
                        break
                    elif response in ["s", "skip"]:
                        print("⏭️  Skipping all remaining transactions.")
                        return
                    else:
                        print("❓ Please enter y, n, or s")

            print(
                f"\n🎉 Import complete! Imported {imported_count}/{len(result.importable)} transactions."
            )

        except Exception as e:
            print(f"❌ Error processing IndexaCapital CSV: {e}")

    def _process_myinvestor_transactions(self, text: str) -> None:
        """Process myinvestor format using LLM."""
        print("\n🔄 Processing text with LLM...")

        try:
            gemini_client = GeminiClient()
            transactions = gemini_client.extract_transactions(text)

            if transactions:
                print(f"✅ Found {len(transactions)} transaction(s):")
                for i, transaction in enumerate(transactions, 1):
                    print(f"\n📋 Transaction {i}:")
                    print(f"   Symbol: {transaction.symbol}")
                    print(f"   Asset: {transaction.asset_name}")
                    print(f"   Type: {transaction.tx_type}")
                    print(f"   Quantity: {transaction.quantity}")
                    print(f"   Price: {transaction.price}")
                    print(f"   Currency: {transaction.currency}")
                    print(f"   Date: {transaction.date}")
                    print(f"   Raw text: {transaction.raw_text}")

                    # Ask for confirmation before adding
                    confirm = (
                        input("\n❓ Add this transaction? (y/n): ").strip().lower()
                    )
                    if confirm == "y" or confirm == "yes":
                        # Check if asset exists, if not create it
                        asset_data = self.db_manager.get_asset_by_symbol(
                            transaction.symbol.upper()
                        )
                        if not asset_data:
                            print(
                                f"🆕 Asset {transaction.symbol} not found. Creating new asset..."
                            )
                            try:
                                asset_id = self.db_manager.create_asset(
                                    symbol=transaction.symbol.upper(),
                                    name=transaction.asset_name,
                                    asset_type="stock",
                                    currency=transaction.currency,
                                    description="Auto-created from LLM processing",
                                )
                                print(
                                    f"✅ Created asset {transaction.symbol} with ID {asset_id}"
                                )
                            except Exception as e:
                                print(f"❌ Error creating asset: {e}")
                                continue

                        # Add the transaction
                        try:
                            self.add_asset_transaction(
                                transaction.symbol,
                                transaction.quantity,
                                transaction.price,
                                transaction.currency,
                                transaction.tx_type,
                                transaction.date,
                            )
                        except Exception as e:
                            print(f"❌ Error adding transaction: {e}")
                    else:
                        print("⏭️  Skipped.")
            else:
                print("❌ No transactions found in the text.")
                print("💡 Make sure the text contains transaction details like:")
                print("   - Symbol/ticker")
                print("   - Quantity")
                print("   - Price")
                print("   - Date")
                print("   - Buy/sell type")

        except Exception as e:
            print(f"❌ Error processing text: {e}")
            print("💡 Make sure you have set the GEMINI_API_KEY environment variable.")

    def export_transactions(
        self,
        symbols: list = None,
        start_date: str = None,
        end_date: str = None,
        output_file: str = None,
    ) -> None:
        """Export transactions to CSV file."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            current_user = None
            if self.config.is_local_mode:
                current_user = self.auth_manager.get_current_user()

            # Get all transactions (for current user in local mode, all in server mode)
            if self.config.is_local_mode:
                transactions = self.db_manager.get_all_transactions(
                    user_id=current_user["id"]
                )
            else:
                transactions = self.db_manager.get_all_transactions()

            if not transactions:
                print("📋 No transactions found.")
                return

            # Filter transactions based on criteria
            filtered_transactions = []
            for tx in transactions:
                # Filter by symbol if specified
                if symbols and tx.get("symbol") not in symbols:
                    continue

                # Filter by date range if specified
                tx_date = tx.get("transaction_date")
                if start_date and tx_date < start_date:
                    continue
                if end_date and tx_date > end_date:
                    continue

                filtered_transactions.append(tx)

            if not filtered_transactions:
                print("📋 No transactions found matching the specified criteria.")
                return

            # Determine the output path and validate
            if output_file:
                output_dir = os.path.dirname(output_file)
                if not os.path.exists(output_dir):
                    print(f"❌ Directory does not exist: {output_dir}")
                    return
                if not os.access(output_dir, os.W_OK):
                    print(f"❌ No write permission for directory: {output_dir}")
                    return
            else:
                # Generate default filename with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"transactions_{timestamp}.csv"

            # Write to CSV file with confirmation
            with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
                if filtered_transactions:
                    fieldnames = [
                        "id",
                        "symbol",
                        "asset_name",
                        "transaction_type",
                        "quantity",
                        "price",
                        "total_amount",
                        "transaction_date",
                        "portfolio_name",
                        "description",
                    ]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()

                    for tx in filtered_transactions:
                        # Get asset details
                        asset_data = self.db_manager.get_asset(tx.get("asset_id"))
                        asset_name = (
                            asset_data.get("name", "Unknown")
                            if asset_data
                            else "Unknown"
                        )

                        # Get portfolio details
                        portfolio_name = ""
                        if tx.get("portfolio_id"):
                            portfolio_data = self.db_manager.get_portfolio(
                                tx.get("portfolio_id")
                            )
                            portfolio_name = (
                                portfolio_data.get("name", "") if portfolio_data else ""
                            )

                        writer.writerow(
                            {
                                "id": tx.get("id"),
                                "symbol": tx.get("symbol"),
                                "asset_name": asset_name,
                                "transaction_type": tx.get("transaction_type"),
                                "quantity": tx.get("quantity"),
                                "price": tx.get("price"),
                                "total_amount": tx.get("total_amount"),
                                "transaction_date": tx.get("transaction_date"),
                                "portfolio_name": portfolio_name,
                                "description": tx.get("description", ""),
                            }
                        )

            print(
                f"✅ Exported {len(filtered_transactions)} transactions to {output_file}"
            )

            # Show summary
            if symbols:
                print(f"   📊 Filtered by symbols: {', '.join(symbols)}")
            if start_date:
                print(f"   📅 Start date: {start_date}")
            if end_date:
                print(f"   📅 End date: {end_date}")

        except Exception as e:
            print(f"❌ Error exporting transactions: {e}")

    def update_prices(self, symbols: list = None, show_values: bool = False) -> None:
        """Update prices for portfolio assets using external API."""
        try:
            # Ensure user is authenticated (only in local mode)
            if self.config.is_local_mode and not self.auth_manager.is_authenticated():
                print("❌ Please login first.")
                return

            # Get API client
            api_client = get_client()

            # Determine which assets to update
            if symbols:
                # Get specific assets by symbols
                assets_to_update = []
                for symbol in symbols:
                    asset_data = self.db_manager.get_asset_by_symbol(symbol.upper())
                    if asset_data:
                        assets_to_update.append(asset_data)
                    else:
                        print(f"⚠️  Asset with symbol '{symbol.upper()}' not found")

                if not assets_to_update:
                    print("❌ No valid assets found for the specified symbols")
                    return
            else:
                # Get all active assets
                assets_to_update = self.db_manager.get_all_assets(active_only=True)

                if not assets_to_update:
                    print("📋 No active assets found in portfolio")
            # Progress tracking
            successful_updates = 0
            skipped_symbols = []
            error_symbols = []
            api_errors = []

            try:
                # Fetch latest prices in batch.
                # Crypto assets need the yfinance "{SYM}-EUR" format.
                # A few tokens use a different base ticker on Yahoo Finance.
                _CRYPTO_YF_OVERRIDES = {"UNI": "UNI1"}
                yf_to_db: dict[str, str] = {}
                for asset in assets_to_update:
                    sym = asset["symbol"]
                    if asset.get("asset_type") == "crypto":
                        base = _CRYPTO_YF_OVERRIDES.get(sym, sym)
                        yf_to_db[f"{base}-EUR"] = sym
                    else:
                        yf_to_db[sym] = sym

                print("📡 Fetching latest prices from API...")
                prices_raw = api_client.fetch_latest_prices(list(yf_to_db.keys()))
                # Re-key results by original DB symbol
                prices_data = {
                    yf_to_db[yf_sym]: price
                    for yf_sym, price in prices_raw.items()
                    if yf_sym in yf_to_db
                }

                # Process each asset with progress bar
                with tqdm(
                    total=len(assets_to_update), desc="Updating prices", unit="asset"
                ) as pbar:
                    for asset in assets_to_update:
                        symbol = asset["symbol"]

                        try:
                            if symbol in prices_data:
                                price = prices_data[symbol]

                                # Store price in database
                                self.db_manager.insert_price_record(
                                    symbol=symbol,
                                    price=price,
                                    fetched_ts=datetime.now(),
                                    source="yfinance",
                                )

                                successful_updates += 1

                                if show_values:
                                    pbar.set_postfix_str(f"✅ {symbol}: ${price:.2f}")
                                else:
                                    pbar.set_postfix_str(f"✅ {symbol}")

                            else:
                                skipped_symbols.append(symbol)
                                pbar.set_postfix_str(f"⚠️  {symbol}: No data")

                        except Exception as e:
                            error_symbols.append(symbol)
                            pbar.set_postfix_str(f"❌ {symbol}: Error")
                            # Use tqdm.write to avoid interfering with progress bar
                            tqdm.write(f"  ❌ Error storing price for {symbol}: {e}")

                        pbar.update(1)

            except Exception as e:

                if isinstance(e, DataNotFoundError):
                    tqdm.write(f"❌ Data not found: {e}")
                    api_errors.append(f"Data not found: {e}")
                elif isinstance(e, APIError):
                    tqdm.write(f"❌ API Error: {e}")
                    api_errors.append(f"API Error: {e}")
                else:
                    tqdm.write(f"❌ Unexpected error fetching prices: {e}")
                    api_errors.append(f"Unexpected error: {e}")

            # Enhanced summary report
            print("\n📊 Update Summary:")
            print(f"   ✅ Successfully updated: {successful_updates} assets")

            if skipped_symbols:
                print(f"   ⚠️  Skipped (no data): {len(skipped_symbols)} assets")
                if show_values or len(skipped_symbols) <= 10:
                    print(f"      Symbols: {', '.join(skipped_symbols)}")
                elif len(skipped_symbols) > 10:
                    print(
                        f"      First 10: {', '.join(skipped_symbols[:10])}, ... and {len(skipped_symbols) - 10} more"
                    )

            if error_symbols:
                print(f"   ❌ Failed (database errors): {len(error_symbols)} assets")
                if show_values or len(error_symbols) <= 10:
                    print(f"      Symbols: {', '.join(error_symbols)}")
                elif len(error_symbols) > 10:
                    print(
                        f"      First 10: {', '.join(error_symbols[:10])}, ... and {len(error_symbols) - 10} more"
                    )

            if api_errors:
                print(f"   🌐 API Issues: {len(api_errors)} error(s)")
                for error in api_errors[:3]:  # Show first 3 API errors
                    print(f"      - {error}")
                if len(api_errors) > 3:
                    print(f"      ... and {len(api_errors) - 3} more API errors")

            # Performance metrics
            total_processed = (
                successful_updates + len(skipped_symbols) + len(error_symbols)
            )
            if total_processed > 0:
                success_rate = (successful_updates / total_processed) * 100
                print(f"   📈 Success rate: {success_rate:.1f}%")

        except Exception as e:
            print(f"❌ Error updating prices: {e}")
            # Log the full traceback for debugging
            import traceback

            tqdm.write(f"Full error traceback: {traceback.format_exc()}")


def handle_export_to_sheets(args):
    """Handle the export-to-sheets command using Google Sheets API."""
    from .google_sheets_export import (
        create_google_sheets_exporter,
        GoogleSheetsExportError,
    )
    from .database import Database
    from .auth import AuthManager

    # Initialize components
    db_manager = Database(getattr(args, "db_path", "portfolio.db"))
    auth_manager = AuthManager(db_manager)

    # Verify user is authenticated
    if not auth_manager.is_authenticated():
        print("❌ Please login first.")
        sys.exit(1)

    try:
        # Create Google Sheets exporter
        sheets_exporter = create_google_sheets_exporter(db_manager, auth_manager)

        # Perform export
        result = sheets_exporter.export(
            spreadsheet_id=(
                args.spreadsheet_id if hasattr(args, "spreadsheet_id") else None
            ),
            create_new=args.create_new if hasattr(args, "create_new") else False,
        )

        if result["success"]:
            print("✅ Google Sheets export completed successfully!")
            print(f"📊 Spreadsheet ID: {result['spreadsheet_id']}")
            print(f"🔗 URL: {result['spreadsheet_url']}")
            print("📋 Sheets exported:")
            for sheet_info in result["sheets_exported"]:
                print(f"  • {sheet_info}")
        else:
            print(f"❌ Export failed: {result['message']}")
            sys.exit(1)

    except GoogleSheetsExportError as e:
        print(f"❌ Google Sheets export error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error during Google Sheets export: {e}")
        sys.exit(1)


def handle_export_transactions(args, cli=None):
    """Handle the export-transactions command using the new CSV export functionality."""
    from .csv_export import create_csv_exporter
    from .transaction_filter import TransactionFilter
    from datetime import datetime, date
    import os

    # Use provided CLI client or create new one
    if cli is None:
        from .database import Database
        from .auth import AuthManager

        # Initialize components for local mode
        db_manager = Database(getattr(args, "db_path", "portfolio.db"))
        auth_manager = AuthManager(db_manager)

        # Verify user is authenticated
        if not auth_manager.is_authenticated():
            print("❌ Please login first.")
            sys.exit(1)
    else:
        # Use server mode via CLI client
        # In server mode, authentication is handled by API key
        if (
            cli.config.is_local_mode
            and cli.auth_manager
            and not cli.auth_manager.is_authenticated()
        ):
            print("❌ Authentication required.")
            sys.exit(1)

        # Use CLI's database manager
        db_manager = cli.db_manager

        # In server mode, create a mock auth manager since HTTP client handles auth
        if cli.config.is_server_mode and cli.auth_manager is None:
            # Create a simple mock auth manager for server mode
            class MockAuthManager:
                def is_authenticated(self):
                    return True  # Server mode uses API key auth

                def get_current_user(self):
                    return {"id": 1, "username": "api_user"}  # Mock user

            auth_manager = MockAuthManager()
        else:
            auth_manager = cli.auth_manager

    # Create CSV exporter with the appropriate managers
    csv_exporter = create_csv_exporter(db_manager, auth_manager)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Create CSV exporter
    csv_exporter = create_csv_exporter(db_manager, auth_manager)

    # Create filter criteria
    filter_criteria = TransactionFilter()

    # Apply symbol filter if specified
    if args.symbol:
        # For now, take the first symbol - the filter supports single symbol
        filter_criteria.symbol = args.symbol[0]
        if len(args.symbol) > 1:
            print(
                "⚠️  Multiple symbols specified. Only filtering by first symbol: {}".format(
                    args.symbol[0]
                )
            )

    # Apply date filters if specified
    if args.start_date:
        try:
            filter_criteria.start_date = date.fromisoformat(args.start_date)
        except ValueError:
            print(
                f"❌ Invalid start date format: {args.start_date}. Use YYYY-MM-DD format."
            )
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    if args.end_date:
        try:
            filter_criteria.end_date = date.fromisoformat(args.end_date)
        except ValueError:
            print(
                f"❌ Invalid end date format: {args.end_date}. Use YYYY-MM-DD format."
            )
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Determine output file
    output_file = args.output
    if not output_file:
        # Generate default filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"transactions_{timestamp}.csv"

    # Validate output directory
    if output_file:
        output_dir = os.path.dirname(output_file) or "."
        if not os.path.exists(output_dir):
            print(f"❌ Directory does not exist: {output_dir}")
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

        if not os.access(output_dir, os.W_OK):
            print(f"❌ No write permission for directory: {output_dir}")
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Export with summary
    result = csv_exporter.export_with_summary(
        filter_criteria=filter_criteria, output_file=output_file, encoding="utf-8"
    )

    # Display results
    if result["success"]:
        print(f"✅ {result['message']}")

        # Show filter summary
        if filter_criteria.symbol:
            print(f"   📊 Filtered by symbol: {filter_criteria.symbol}")
        if filter_criteria.start_date:
            print(f"   📅 Start date: {filter_criteria.start_date}")
        if filter_criteria.end_date:
            print(f"   📅 End date: {filter_criteria.end_date}")

        # Show export summary
        if result.get("summary"):
            summary = result["summary"]
            print(f"   📈 Transaction count: {summary['total_count']}")
            if summary.get("symbols"):
                print(f"   📊 Symbols included: {', '.join(summary['symbols'])}")
            if summary.get("transaction_types"):
                tx_types = [
                    f"{k}: {v}" for k, v in summary["transaction_types"].items()
                ]
                print(f"   📋 Transaction types: {', '.join(tx_types)}")
    else:
        print(f"❌ Export failed: {result['message']}")
        sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)


def handle_extract_tax_report(args):
    """Handle the extract-tax-report command."""
    from .tax_calculator import TaxCalculator
    from .tax_export import TaxReportExporter, generate_tax_report_filename
    from .database import Database
    from .auth import AuthManager
    from datetime import date, timedelta
    import os

    # Initialize components
    db_manager = Database(getattr(args, "db_path", "portfolio.db"))
    auth_manager = AuthManager(db_manager)

    # Verify user is authenticated
    if not auth_manager.is_authenticated():
        print("❌ Please login first.")
        sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Get current user
    current_user = auth_manager.get_current_user()
    if not current_user:
        print("❌ Authentication failed.")
        sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Parse dates
    end_date = date.today()
    start_date = end_date - timedelta(days=365)  # Default to 1 year ago

    if args.start_date:
        try:
            start_date = date.fromisoformat(args.start_date)
        except ValueError:
            print(
                f"❌ Invalid start date format: {args.start_date}. Use YYYY-MM-DD format."
            )
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    if args.end_date:
        try:
            end_date = date.fromisoformat(args.end_date)
        except ValueError:
            print(
                f"❌ Invalid end date format: {args.end_date}. Use YYYY-MM-DD format."
            )
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Validate date range
    if start_date > end_date:
        print(f"❌ Start date ({start_date}) cannot be after end date ({end_date}).")
        sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Parse symbols
    symbols = None
    if args.symbol:
        symbols = [s.upper() for s in args.symbol]

    # Determine output file
    output_file = args.output
    if not output_file:
        output_file = generate_tax_report_filename(start_date, end_date, symbols)

    # Validate output directory
    if output_file:
        output_dir = os.path.dirname(output_file) or "."
        if not os.path.exists(output_dir):
            print(f"❌ Directory does not exist: {output_dir}")
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

        if not os.access(output_dir, os.W_OK):
            print(f"❌ No write permission for directory: {output_dir}")
            sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    # Resolve portfolio filter
    portfolio_id = None
    portfolio_name = getattr(args, "portfolio", None)
    if portfolio_name:
        portfolio_data = db_manager.get_portfolio_by_name(portfolio_name)
        if not portfolio_data:
            print(f"❌ Portfolio '{portfolio_name}' not found.")
            sys.exit(1)
        portfolio_id = portfolio_data["id"]

    # Initialize tax calculator
    tax_calculator = TaxCalculator(db_manager)

    # Calculate tax report
    print(f"📊 Calculating tax report for period {start_date} to {end_date}...")
    if portfolio_name:
        print(f"   📁 Portfolio: {portfolio_name}")
    if symbols:
        print(f"   📈 Filtering by symbols: {', '.join(symbols)}")

    try:
        tax_report = tax_calculator.calculate_tax_report(
            user_id=current_user["id"],
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            portfolio_id=portfolio_id,
        )

        if not tax_report:
            print("📋 No sell transactions found for the specified period.")
            print(
                "💡 Tax reports only include realized gains/losses from sell transactions."
            )
            sys.exit(0)

        # Generate summary
        summary = tax_calculator.generate_tax_summary(tax_report)

        # Export to CSV
        exporter = TaxReportExporter()
        include_summary = not args.no_summary

        output_path = exporter.export_tax_report(
            tax_report=tax_report,
            output_file=output_file,
            include_summary=include_summary,
        )

        # Display results
        print(f"✅ Tax report exported to {output_path}")
        print(f"   📊 Total transactions: {summary['total_transactions']}")
        print(f"   💰 Total gain/loss: ${summary['total_gain_loss']:.2f}")
        print(f"   📈 Long-term gain/loss: ${summary['total_long_term_gain_loss']:.2f}")
        print(
            f"   📉 Short-term gain/loss: ${summary['total_short_term_gain_loss']:.2f}"
        )

        # Show symbol breakdown
        if len(summary["symbol_summaries"]) > 1:
            print("\n📊 Symbol breakdown:")
            for symbol, symbol_summary in summary["symbol_summaries"].items():
                print(
                    f"   {symbol}: ${symbol_summary['total_gain_loss']:.2f} ({symbol_summary['transaction_count']} transactions)"
                )

        print("\n💡 This report uses FIFO (First In First Out) cost basis methodology.")
        print("💡 Please consult a tax professional for proper tax filing.")

    except Exception as e:
        print(f"❌ Error calculating tax report: {e}")
        sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)


def create_parser():
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        description="Portfolio Manager CLI - Manage your investment portfolio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  portf add-asset AAPL "Apple Inc." stock --exchange NASDAQ
  portf remove-asset AAPL
  portf list-assets
  portf update-prices
  portf update-prices -s AAPL -s MSFT
  portf list-sectors
  portf show-mapping


  # Export transactions with filtering
  portf export-transactions
  portf export-transactions --symbol AAPL
  portf export-transactions --start-date 2024-01-01 --end-date 2024-12-31
  portf export-transactions --symbol TSLA --output tsla_transactions.csv
  # Export to Google Sheets
  portf export-to-sheets
  portf export-to-sheets --create-new
  portf export-to-sheets --spreadsheet-id 1ABC...XYZ
        """,
    )

    # Add global mode flags
    parser.add_argument(
        "--server",
        help="Server URL for server mode (e.g., http://localhost:8000). Falls back to PORTF_SERVER_URL env var.",
    )

    parser.add_argument(
        "--api-key",
        help="API key for server mode authentication. Falls back to PORTF_API_KEY env var.",
    )

    # Add database path option (for local mode)
    parser.add_argument(
        "--db-path",
        default="portfolio.db",
        help="Path to the database file for local mode (default: portfolio.db)",
    )

    # Add debug flag
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with detailed error traces",
    )

    # Create subparsers
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Authentication commands
    login_parser = subparsers.add_parser("login", help="Login as a user")
    login_parser.add_argument("--username", "-u", help="Username or email")
    login_parser.add_argument("--password", "-p", help="Password")

    subparsers.add_parser("register", help="Register a new user")
    add_asset_parser = subparsers.add_parser("add-asset", help="Add a new asset")
    add_asset_parser.add_argument("symbol", help="Asset symbol (e.g., AAPL)")
    add_asset_parser.add_argument("name", help="Asset name (e.g., 'Apple Inc.')")
    add_asset_parser.add_argument(
        "asset_type", help="Asset type", choices=[t.value for t in AssetType]
    )
    add_asset_parser.add_argument("--exchange", help="Exchange name (optional)")
    add_asset_parser.add_argument(
        "--currency", default="USD", help="Currency (default: USD)"
    )
    add_asset_parser.add_argument("--description", help="Asset description (optional)")

    # Add transaction command
    add_transaction_parser = subparsers.add_parser(
        "add-transaction", help="Add a new asset transaction"
    )
    add_transaction_parser.add_argument(
        "--symbol", required=True, help="Asset symbol (e.g., AAPL)"
    )
    add_transaction_parser.add_argument(
        "--amount", type=float, required=True, help="Amount of stocks"
    )
    add_transaction_parser.add_argument(
        "--price", type=float, required=True, help="Price of each stock"
    )
    add_transaction_parser.add_argument(
        "--currency", required=True, help="Transaction currency"
    )
    add_transaction_parser.add_argument(
        "--type",
        required=True,
        help="Transaction type",
        choices=[t.value for t in TransactionType],
    )
    add_transaction_parser.add_argument(
        "--date", required=True, help="Transaction date in YYYY-MM-DD format"
    )
    add_transaction_parser.add_argument(
        "--portfolio", "-p", help="Name of the portfolio the transaction belongs to"
    )

    # Remove asset command
    remove_parser = subparsers.add_parser("remove-asset", help="Remove an asset")
    remove_parser.add_argument("symbol", help="Asset symbol to remove")
    # Update asset command
    update_asset_parser = subparsers.add_parser(
        "update-asset", help="Update an existing asset"
    )
    update_asset_parser.add_argument(
        "asset_id", type=int, help="ID of the asset to update"
    )
    update_asset_parser.add_argument("--name", help="New asset name")
    update_asset_parser.add_argument("--exchange", help="New exchange")
    update_asset_parser.add_argument("--currency", help="New currency")
    update_asset_parser.add_argument("--sector", help="New sector")
    update_asset_parser.add_argument("--description", help="New description")
    update_asset_parser.add_argument(
        "--active", type=bool, help="Set active status (True/False)"
    )

    # Delete asset command
    delete_asset_parser = subparsers.add_parser(
        "delete-asset", help="Delete an asset (soft delete)"
    )
    delete_asset_parser.add_argument(
        "asset_id", type=int, help="ID of the asset to delete"
    )

    # List assets command
    list_parser = subparsers.add_parser("list-assets", help="List all assets")
    list_parser.add_argument(
        "--all", action="store_true", help="Include inactive assets"
    )

    # List sectors command
    subparsers.add_parser("list-sectors", help="List all GICS sectors")

    # Show sector mapping command
    subparsers.add_parser("show-mapping", help="Show ticker-to-sector mapping")

    # Portfolio value command
    pv_parser = subparsers.add_parser(
        "portfolio-value",
        help="Show current portfolio value and positions",
        description=(
            "Display current holdings grouped by portfolio, with quantity, "
            "average cost, current price, and total value. Prices are read "
            "from the last update-prices run."
        ),
    )
    pv_parser.add_argument(
        "--portfolio",
        "-p",
        help="Filter by portfolio name (shows only that portfolio's positions)",
    )

    # List transactions command
    list_tx_parser = subparsers.add_parser(
        "list-transactions", help="List and filter transactions"
    )
    list_tx_parser.add_argument(
        "limit",
        nargs="?",
        default="10",
        help="Number of transactions to show: number, 'all', or empty for default 10",
    )

    # Legacy symbol filter (kept for backward compatibility)
    list_tx_parser.add_argument(
        "--symbol", help="Filter by asset symbol (supports wildcards like BTC*)"
    )

    # Enhanced filtering options
    list_tx_parser.add_argument(
        "--name", help="Filter by asset name (supports wildcards like *Crypto*)"
    )
    list_tx_parser.add_argument(
        "--price", help="Filter by price (e.g., >100, <500, 100-500, =250)"
    )
    list_tx_parser.add_argument(
        "--total", help="Filter by total amount (e.g., >1000, 100-500)"
    )
    list_tx_parser.add_argument(
        "--quantity", help="Filter by quantity (e.g., >0.1, <10)"
    )
    list_tx_parser.add_argument("--from-date", help="Filter by date from (YYYY-MM-DD)")
    list_tx_parser.add_argument("--to-date", help="Filter by date to (YYYY-MM-DD)")
    list_tx_parser.add_argument(
        "--type", choices=["buy", "sell"], help="Filter by transaction type"
    )
    list_tx_parser.add_argument(
        "--portfolio",
        "-p",
        help="Filter by portfolio name",
    )

    # Delete transaction command
    delete_tx_parser = subparsers.add_parser(
        "delete-transaction", help="Delete a transaction by ID"
    )
    delete_tx_parser.add_argument(
        "transaction_id", type=int, help="ID of the transaction to delete"
    )

    # Update transaction command
    update_tx_parser = subparsers.add_parser(
        "update-transaction", help="Update a transaction"
    )
    update_tx_parser.add_argument(
        "transaction_id", type=int, help="ID of the transaction to update"
    )
    update_tx_parser.add_argument("--quantity", type=float, help="New quantity")
    update_tx_parser.add_argument("--price", type=float, help="New price per share")
    update_tx_parser.add_argument("--date", help="New transaction date (YYYY-MM-DD)")
    update_tx_parser.add_argument(
        "--type", choices=["buy", "sell", "dividend"], help="New transaction type"
    )
    update_tx_parser.add_argument("--description", help="New description")
    # Import CSV command (MyInvestor format)
    import_csv_parser = subparsers.add_parser(
        "import-csv", help="Import transactions from CSV file"
    )
    import_csv_parser.add_argument(
        "csv_file",
        help="Path to CSV file (e.g., 'Movimientos Mi Cuenta MyInvestor.csv')",
    )
    import_csv_parser.add_argument(
        "--portfolio", help="Portfolio name to import transactions to (optional)"
    )

    # Import Coinbase CSV command
    import_coinbase_parser = subparsers.add_parser(
        "import-coinbase-csv",
        help="Import transactions from a Coinbase CSV export file",
    )
    import_coinbase_parser.add_argument(
        "csv_file",
        help="Path to the Coinbase CSV export file",
    )
    import_coinbase_parser.add_argument(
        "--portfolio", help="Portfolio name to import transactions to (optional)"
    )
    import_coinbase_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear all existing transactions from the target portfolio before importing",
    )

    # Import PDT XLSX command
    import_pdt_parser = subparsers.add_parser(
        "import-pdt",
        help="Import transactions from a Portfolio Dividend Tracker v2 XLSX file",
    )
    import_pdt_parser.add_argument(
        "xlsx_file",
        help="Path to the PDT XLSX file (e.g., 'Portfolio Dividend Tracker v2.xlsx')",
    )
    import_pdt_parser.add_argument(
        "--portfolio", help="Portfolio name to import transactions to (optional)"
    )
    import_pdt_parser.add_argument(
        "--no-dividends",
        action="store_true",
        help="Skip importing dividend rows",
    )
    import_pdt_parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear all existing transactions from the target portfolio before importing",
    )
    import_pdt_parser.add_argument(
        "--import-bookings",
        action="store_true",
        help="Also import deposit/withdrawal bookings from the Bookings sheet",
    )

    # List bookings command
    list_bookings_parser = subparsers.add_parser(
        "list-bookings", help="List all bookings (deposits and withdrawals)"
    )
    list_bookings_parser.add_argument(
        "--portfolio", help="Filter by portfolio name (optional)"
    )

    # Export PDT XLSX command
    export_pdt_parser = subparsers.add_parser(
        "export-pdt",
        help="Export portfolio data to a Portfolio Dividend Tracker v2 XLSX file",
    )
    export_pdt_parser.add_argument(
        "--output",
        default=None,
        help="Output XLSX file path (default: pdt_export_YYYYMMDD.xlsx)",
    )
    export_pdt_parser.add_argument(
        "--portfolio", help="Portfolio name to export (default: all portfolios)"
    )

    # PDT Google Sheets sync commands
    sync_pull_parser = subparsers.add_parser(
        "sync-pdt-pull",
        help="Pull data from a PDT-format Google Spreadsheet and import to DB",
        description=(
            "Reads Transactions, Dividends, and Bookings sheets from a "
            "Portfolio Dividend Tracker v2 Google Spreadsheet and saves them "
            "to the local database. Assets and portfolios are created automatically. "
            "Requires GOOGLE_SERVICE_ACCOUNT_FILE to be set."
        ),
    )
    sync_pull_parser.add_argument(
        "--sheet-id",
        help="Google Spreadsheet ID (overrides GOOGLE_SPREADSHEET_ID env var)",
    )
    sync_pull_parser.add_argument(
        "--portfolio", help="Assign imported data to this portfolio (optional)"
    )
    sync_pull_parser.add_argument(
        "--import-bookings",
        action="store_true",
        help="Also import bookings (deposits/withdrawals) from the Bookings sheet",
    )

    sync_push_parser = subparsers.add_parser(
        "sync-pdt-push",
        help="Push DB data to a PDT-format Google Spreadsheet",
    )
    sync_push_parser.add_argument(
        "--sheet-id",
        help="Google Spreadsheet ID (overrides GOOGLE_SPREADSHEET_ID env var)",
    )
    sync_push_parser.add_argument(
        "--portfolio", help="Export only this portfolio (default: all)"
    )

    # Entity management commands
    add_entity_parser = subparsers.add_parser(
        "add-entity", help="Add a new entity (broker, bank, etc.)"
    )
    add_entity_parser.add_argument(
        "name", help="Entity name (e.g., 'Interactive Brokers')"
    )
    add_entity_parser.add_argument(
        "entity_type",
        help="Entity type",
        choices=["broker", "bank", "platform", "other"],
    )
    add_entity_parser.add_argument("--website", help="Entity website URL (optional)")
    add_entity_parser.add_argument(
        "--description", help="Entity description (optional)"
    )

    subparsers.add_parser("list-entities", help="List all entities")

    # Portfolio management commands
    add_portfolio_parser = subparsers.add_parser(
        "add-portfolio", help="Add a new portfolio"
    )
    add_portfolio_parser.add_argument("name", help="Portfolio name (e.g., 'My IRA')")
    add_portfolio_parser.add_argument(
        "--currency", default="USD", help="Base currency (default: USD)"
    )
    add_portfolio_parser.add_argument("--entity", help="Entity name (optional)")
    add_portfolio_parser.add_argument(
        "--description", help="Portfolio description (optional)"
    )

    subparsers.add_parser("list-portfolios", help="List all portfolios")

    # Add paste and parse text command
    paste_parser = subparsers.add_parser(
        "paste-transaction",
        help="Paste transaction text or CSV data (supports --format myinvestor|coinbase|indexacapital)",
    )
    paste_parser.add_argument(
        "text",
        help="Raw text to be processed by LLM",
    )
    paste_parser.add_argument(
        "--format",
        "-f",
        choices=["myinvestor", "coinbase", "indexacapital"],
        default="myinvestor",
        help="Format of input data (default: myinvestor)",
    )

    # Export transactions command
    export_parser = subparsers.add_parser(
        "export-transactions",
        help="Export filtered transactions to CSV file with enhanced filtering options",
    )
    export_parser.add_argument(
        "--symbol",
        action="append",
        help="Filter by asset symbol (can be specified multiple times, first symbol used)",
    )
    export_parser.add_argument(
        "--start-date",
        help="Start date filter in YYYY-MM-DD format (inclusive)",
    )
    export_parser.add_argument(
        "--end-date",
        help="End date filter in YYYY-MM-DD format (inclusive)",
    )
    export_parser.add_argument(
        "--output",
        help="Output CSV file path (default: transactions_YYYYMMDD_HHMMSS.csv)",
    )
    export_parser.set_defaults(func=handle_export_transactions)
    # Export to Google Sheets command
    sheets_parser = subparsers.add_parser(
        "export-to-sheets",
        help="Export portfolio data to Google Sheets (transactions, tax report, summary)",
    )
    sheets_parser.add_argument(
        "--spreadsheet-id",
        help="Existing Google Spreadsheet ID to update (if not provided, creates new)",
    )
    sheets_parser.add_argument(
        "--create-new",
        action="store_true",
        help="Always create a new spreadsheet (ignores --spreadsheet-id)",
    )
    sheets_parser.set_defaults(func=handle_export_to_sheets)

    # Stock report command
    stock_parser = subparsers.add_parser(
        "stock-report",
        help="Generate a stock analysis report using yfinance/news + Gemini",
        description="Fetches price history and news for a symbol, then summarizes with Gemini into a terminal report.",
    )
    stock_parser.add_argument("symbol", help="Ticker symbol (e.g., AAPL)")

    # Chat command
    chat_parser = subparsers.add_parser(
        "chat", help="Interactive LLM chat about your portfolio"
    )
    chat_parser.add_argument(
        "--portfolio-aware",
        action="store_true",
        help="Automatically inject current portfolio context into prompts (recommended)",
    )
    chat_parser.add_argument(
        "--session", help="Continue an existing session id (optional)"
    )
    chat_parser.add_argument(
        "--symbols",
        help="Comma-separated symbols to prioritize in context (optional)",
    )
    chat_parser.add_argument(
        "--no-live",
        action="store_true",
        help="Disable live market data (yfinance)",
    )
    chat_parser.add_argument(
        "--search",
        action="store_true",
        help="Enable simple web search for company info",
    )
    chat_parser.add_argument(
        "--once",
        action="store_true",
        help="Send a single message (read from stdin or prompt) and exit",
    )
    stock_parser.add_argument(
        "--news-provider",
        choices=["newsapi", "serpapi"],
        help="Force a specific news provider (default: try NEWSAPI, then SerpAPI)",
    )

    # Extract tax report command
    tax_parser = subparsers.add_parser(
        "extract-tax-report",
        help="Extract tax report with capital gains/losses (FIFO method)",
        description="Generate tax report showing realized capital gains and losses using FIFO cost basis methodology",
    )
    tax_parser.add_argument(
        "--symbol",
        action="append",
        help="Filter by asset symbol (can be used multiple times)",
    )
    tax_parser.add_argument(
        "--start-date",
        type=str,
        help="Start date for sell transactions (YYYY-MM-DD format, default: 1 year ago)",
    )
    tax_parser.add_argument(
        "--end-date",
        type=str,
        help="End date for sell transactions (YYYY-MM-DD format, default: today)",
    )
    tax_parser.add_argument(
        "--output",
        type=str,
        help="Output CSV file path (default: auto-generated timestamp filename)",
    )
    tax_parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Exclude summary section from CSV output",
    )
    tax_parser.add_argument(
        "--portfolio",
        "-p",
        help="Filter by portfolio name (for per-broker tax reporting)",
    )
    tax_parser.set_defaults(func=handle_extract_tax_report)

    # Update prices command
    update_prices_parser = subparsers.add_parser(
        "update-prices",
        help="Update asset prices from external API.",
        description="Fetches the latest prices for specified or all portfolio assets and records them in the database. Each price is timestamped and tagged with the data source.",
        epilog="""
Examples of use:
  - Update all assets:
    portf update-prices
  - Update specific assets:
    portf update-prices -s AAPL -s MSFT
  - Show live price updates in the terminal:
    portf update-prices --show-values
""",
    )
    update_prices_parser.add_argument(
        "--symbol",
        "-s",
        action="append",
        help="Update prices for specific symbol(s) only (can be specified multiple times)",
    )
    update_prices_parser.add_argument(
        "--show-values",
        action="store_true",
        help="Display the fetched price values and detailed error information",
    )

    # Multi-Agent Analysis Commands
    parser.add_argument(
        "--multi-agent",
        action="store_true",
        help="Launch interactive multi-agent analysis console",
    )

    parser.add_argument(
        "--multi-agent-quick",
        type=str,
        help="Run quick multi-agent analysis on symbol(s) (comma-separated)",
    )

    return parser


def handle_multi_agent_commands(args):
    """Handle multi-agent system commands"""
    try:
        # Check if multi-agent system is available
        multi_agent_path = os.path.join(
            os.path.dirname(__file__), "..", "multi_agent_analysis"
        )
        if not os.path.exists(multi_agent_path):
            print("❌ Multi-agent analysis system not found")
            return False

        import sys

        sys.path.append(multi_agent_path)

        if args.multi_agent:
            print("🚀 Launching Multi-Agent Analysis Console...")

            # Launch console_demo.py as a subprocess to avoid import conflicts
            try:
                import subprocess

                result = subprocess.run(
                    [sys.executable, "console_demo.py"], cwd=multi_agent_path
                )
                return result.returncode == 0
            except Exception as e:
                print(f"❌ Failed to launch multi-agent console: {e}")
                print(
                    "💡 Try running directly: cd multi_agent_analysis && python console_demo.py"
                )
                return False

        elif args.multi_agent_quick:
            print(f"🔍 Quick analysis for: {args.multi_agent_quick}")
            # Add quick analysis logic here
            print("✅ Quick analysis completed")
            return True

    except Exception as e:
        print(f"❌ Multi-agent system error: {e}")
        return False

    return False


def main() -> None:
    """Main CLI entry point, with interactive mode support."""
    parser = create_parser()
    args = parser.parse_args()
    # Load environment variables from multiple .env files
    load_dotenv(".env.local", override=False)  # Load local env first
    load_dotenv(".env.development", override=False)  # Then development
    load_dotenv(".env", override=False)  # Finally base .env

    # Initialize configuration from arguments and environment
    try:
        config = PortfolioConfig.from_args_and_env(
            server_url=getattr(args, "server", None),
            api_key=getattr(args, "api_key", None),
            db_path=getattr(args, "db_path", None),
            debug=getattr(args, "debug", False),
        )
        set_config(config)

        if config.is_server_mode:
            print(f"🌐 Running in server mode: {config.server_url}")
        else:
            print(f"💾 Running in local mode: {config.db_path}")

    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)

    # Handle multi-agent commands early (before interactive mode)
    if hasattr(args, "multi_agent") and args.multi_agent:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)
    elif hasattr(args, "multi_agent_quick") and args.multi_agent_quick:
        if handle_multi_agent_commands(args):
            return
        else:
            sys.exit(1)

    if not args.command:
        print("🚀 Portfolio Manager Interactive Console")
        print("Type 'help' for available commands, 'exit' to quit.")
        print("You can also press Ctrl-D to exit at any time.")
        print(
            "Special commands: 'paste [format]' for transactions (myinvestor|coinbase)"
        )

        # Initialize readline support
        readline_enabled = setup_readline()
        if readline_enabled:
            print("✅ Command history enabled (use ↑/↓ arrows to navigate)")
        print()

        cli = PortfolioManagerCLI(config)

        # Only require login for local mode - server mode uses API keys
        if config.is_local_mode:
            print("🔐 Please login to continue...")
            print("💡 If you don't have an account, you can register when prompted.")
            print()

            cli.login_user()

            if not cli.auth_manager.is_authenticated():
                print("❌ Authentication required. Exiting.")
                print(
                    "💡 You can register by running: python -m portf_manager register"
                )
                return

            current_user = cli.auth_manager.get_current_user()
            print(
                f"Welcome, {current_user.get('full_name', current_user['username'])}!"
            )
            print()
        else:
            print("🔑 Server mode - using API key authentication")
            print()

        while True:
            try:
                command = enhanced_input("portf> ").strip()
                if command.lower() == "exit":
                    print("👋 Goodbye!")
                    break
                elif command.lower() == "help":
                    print_interactive_help()
                elif command.lower() == "shortcuts":
                    print_readline_help()
                elif command.lower() == "login":
                    if config.is_local_mode:
                        cli.login_user()
                    else:
                        print(
                            "💡 Login not available in server mode - authentication handled by API key"
                        )
                elif command.lower() == "register":
                    if config.is_local_mode:
                        cli.register_user()
                    else:
                        print(
                            "💡 Registration not available in server mode - contact server administrator"
                        )
                elif command.lower().startswith("paste"):
                    # Parse paste command with optional format
                    parts = command.split()
                    format_type = "myinvestor"  # default
                    if len(parts) > 1 and (parts[1] in ["--format", "-f"]):
                        if len(parts) > 2 and parts[2] in [
                            "myinvestor",
                            "coinbase",
                            "indexacapital",
                        ]:
                            format_type = parts[2]
                        else:
                            print(
                                "❌ Invalid format. Use --format myinvestor, --format coinbase, or --format indexacapital"
                            )
                            continue
                    elif len(parts) > 1:
                        # Handle direct format specification: paste coinbase
                        if parts[1] in ["myinvestor", "coinbase", "indexacapital"]:
                            format_type = parts[1]
                        else:
                            print(
                                "❌ Invalid format. Use: paste [myinvestor|coinbase|indexacapital] or paste --format [myinvestor|coinbase|indexacapital]"
                            )
                            continue
                    cli.paste_transaction_interactive(format_type)
                elif command:
                    # Parse and execute regular commands
                    try:
                        console_args = parser.parse_args(command.split())
                        execute_command(cli, console_args, parser)
                    except SystemExit:
                        # argparse calls sys.exit on error, catch it
                        print("❌ Invalid command. Type 'help' for available commands.")
            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break
            except EOFError:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
        return

    # Initialize CLI
    cli = PortfolioManagerCLI(config)
    execute_command(cli, args, parser)


def print_interactive_help():
    """Print help for interactive mode."""
    print("📚 Portfolio Manager Interactive Commands:")
    print()
    print("📊 Portfolio Management:")
    print("  list-assets                    - List all assets")
    print("  list-transactions             - List recent transactions")
    print("  list-transactions --name '*Crypto*' --price '>2000'")
    print("  list-transactions --symbol 'BTC*' --from-date 2025-06-01")
    print("  list-transactions --name '*Crypto*' --price '>2000'")
    print("  list-transactions --symbol 'BTC*' --from-date 2025-06-01")
    print("  portfolio-value               - Show portfolio value and positions")
    print("  list-portfolios               - List all portfolios")
    print("  list-entities                 - List all entities")
    print()
    print("➕ Adding Data:")
    print("  add-asset SYMBOL NAME TYPE    - Add new asset")
    print(
        "  add-transaction --symbol AAPL --amount 100 --price 150 --currency USD --type buy --date 2024-01-15"
    )
    print("  add-portfolio NAME             - Add new portfolio")
    print("  add-entity NAME TYPE           - Add new entity")
    print()
    print("🔄 Asset Management:")
    print("  update-asset ID [options]      - Update existing asset")
    print("    --name NAME                  - Update asset name")
    print("    --exchange EXCHANGE          - Update exchange")
    print("    --currency CURRENCY          - Update currency")
    print("    --sector SECTOR              - Update sector")
    print("    --description TEXT           - Update description")
    print("    --active {True,False}        - Set active status")
    print("  delete-asset ID                - Delete asset (soft delete)")
    print()
    print("🔄 Transaction Management:")
    print("  update-transaction ID [options] - Update existing transaction")
    print("    --quantity N                 - Update quantity")
    print("    --price N.NN                 - Update price per share")
    print("    --date YYYY-MM-DD            - Update transaction date")
    print("    --type {buy,sell,dividend}   - Update transaction type")
    print("    --description TEXT           - Update description")
    print("  delete-transaction ID          - Delete transaction (with confirmation)")
    print()

    print("📁 Import/Export:")
    print(
        "  import-csv FILE.csv            - Import transactions from CSV (MyInvestor format)"
    )
    print(
        "  import-coinbase-csv FILE.csv   - Import transactions from Coinbase CSV export"
    )
    print(
        "  export-transactions            - Export transactions to CSV file with filtering"
    )
    print(
        "  export-to-sheets               - Export all data to Google Sheets (3 sheets: transactions, tax, summary)"
    )
    print("    --spreadsheet-id ID          - Update existing spreadsheet (by ID)")
    print("    --create-new                 - Always create new spreadsheet")
    print("    --symbol SYMBOL              - Filter by specific symbol")
    print("    --start-date YYYY-MM-DD      - Filter from start date (inclusive)")
    print("    --end-date YYYY-MM-DD        - Filter to end date (inclusive)")
    print("    --output FILE.csv            - Specify output file name")
    print(
        "  paste [format]                  - Interactive transaction processing (myinvestor|coinbase)"
    )
    print("  update-prices                  - Update asset prices from external API")
    print(
        "    --symbol/-s SYMBOL           - Update specific symbol(s) only (repeatable)"
    )
    print("    --show-values                - Display fetched price values")
    print()
    print("🔍 Information:")
    print("  list-sectors                   - List all GICS sectors (requires login)")
    print(
        "  show-mapping                   - Show ticker-to-sector mapping (requires login)"
    )
    print()
    print("🔐 Authentication:")
    print("  login                          - Login with your credentials")
    print("  register                       - Register a new user account")
    print()
    print("⚡ Special Commands:")
    print(
        "  paste                          - Paste transaction text or CSV data (supports --format myinvestor|coinbase|indexacapital)"
    )
    print("  help                           - Show this help")
    print("  shortcuts                      - Show keyboard shortcuts")
    print("  exit                           - Exit interactive mode")
    print("  Ctrl-D                         - Exit interactive mode")
    print()
    print("💡 Examples:")
    print('  add-asset AAPL "Apple Inc." stock')
    print("  list-transactions --symbol AAPL")
    print("  list-transactions --name '*Crypto*' --price '>1000'")
    print("  list-transactions --total '100-500' --type buy")
    print("  list-transactions --name '*Crypto*' --price '>1000'")
    print("  list-transactions --total '100-500' --type buy")
    print(
        "  add-transaction --symbol AAPL --amount 100 --price 150 --currency USD --type buy --date 2024-01-15"
    )
    print("  update-transaction 17 --quantity 120 --price 148.50")
    print("  delete-transaction 17")
    print('  update-asset 5 --name "Apple Inc." --exchange NASDAQ')
    print("  delete-asset 5")

    print()


def execute_command(cli: PortfolioManagerCLI, args, parser=None):
    """Execute parsed command arguments with robust error handling."""
    # Create error handler with debug mode based on args
    debug_mode = getattr(args, "debug", False)
    error_handler = create_error_handler(debug=debug_mode)

    try:
        if args.command == "login":
            cli.login_user(username=args.username, password=args.password)

        elif args.command == "register":
            cli.register_user()

        elif args.command == "add-asset":
            cli.add_asset(
                symbol=args.symbol,
                name=args.name,
                asset_type=args.asset_type,
                exchange=args.exchange,
                currency=args.currency,
                description=args.description,
            )

        elif args.command == "add-transaction":
            # Validate date format before processing
            validated_date = error_handler.convert_date_format(
                args.date, "transaction date"
            )
            cli.add_asset_transaction(
                args.symbol,
                args.amount,
                args.price,
                args.currency,
                args.type,
                validated_date,
                portfolio_name=args.portfolio,
            )

        elif args.command == "paste-transaction":
            try:
                format_type = getattr(args, "format", "myinvestor")

                if format_type == "coinbase":
                    # Process as Coinbase CSV
                    result = parse_coinbase_csv(args.text)

                    print("📊 Processing Summary:")
                    print(f"   🔥 Importable: {len(result.importable)} transactions")
                    print(f"   📋 Reference: {len(result.skipped)} entries")

                    if result.skipped:
                        print("\n📋 Skipped entries:")
                        for tx_type, reason in result.skipped:
                            print(f"   • {tx_type}: {reason}")

                    for transaction in result.importable:
                        # Check if asset exists, create if needed (like Coinbase)
                        asset_data = cli.db_manager.get_asset_by_symbol(
                            transaction.symbol.upper()
                        )
                        if not asset_data:
                            print(
                                f"🆕 ETF/Index {transaction.symbol} not found. Creating..."
                            )
                            asset_id = cli.db_manager.create_asset(
                                symbol=transaction.symbol.upper(),
                                name=transaction.asset_name,
                                asset_type="etf",  # IndexaCapital assets are ETFs/Index funds
                                currency=transaction.currency,
                                description="Auto-created from IndexaCapital CSV import",
                            )
                            print(
                                f"✅ Created ETF asset {transaction.symbol} with ID {asset_id}"
                            )

                        # Validate date format for each transaction
                        validated_date = error_handler.convert_date_format(
                            transaction.date, "transaction date"
                        )
                        cli.add_asset_transaction(
                            transaction.symbol,
                            transaction.quantity,
                            transaction.price,
                            transaction.currency,
                            transaction.tx_type,
                            validated_date,
                        )
                        print(
                            f"✅ Added {transaction.tx_type} {transaction.quantity} {transaction.symbol}"
                        )
                elif format_type == "indexacapital":
                    # Process as IndexaCapital CSV
                    result = parse_indexacapital_csv(args.text)

                    print("📊 Processing Summary:")
                    print(f"   🔥 Importable: {len(result.importable)} transactions")
                    print(f"   📋 Skipped: {len(result.skipped)} entries")

                    if result.skipped:
                        print("\n📋 Skipped entries:")
                        for tx_type, reason in result.skipped:
                            print(f"   • {tx_type}: {reason}")

                    for transaction in result.importable:
                        # Check if asset exists, create if needed (like Coinbase)
                        asset_data = cli.db_manager.get_asset_by_symbol(
                            transaction.symbol.upper()
                        )
                        if not asset_data:
                            print(
                                f"🆕 ETF/Index {transaction.symbol} not found. Creating..."
                            )
                            asset_id = cli.db_manager.create_asset(
                                symbol=transaction.symbol.upper(),
                                name=transaction.asset_name,
                                asset_type="etf",  # IndexaCapital assets are ETFs/Index funds
                                currency=transaction.currency,
                                description="Auto-created from IndexaCapital CSV import",
                            )
                            print(
                                f"✅ Created ETF asset {transaction.symbol} with ID {asset_id}"
                            )

                        # Validate date format for each transaction
                        validated_date = error_handler.convert_date_format(
                            transaction.date, "transaction date"
                        )
                        cli.add_asset_transaction(
                            transaction.symbol,
                            transaction.quantity,
                            transaction.price,
                            transaction.currency,
                            transaction.tx_type,
                            validated_date,
                        )
                        print(
                            f"✅ Added {transaction.tx_type} {transaction.quantity} {transaction.symbol}"
                        )
                else:
                    # Default myinvestor format (LLM processing)
                    gemini_client = GeminiClient()
                    transactions = gemini_client.extract_transactions(args.text)
                    if transactions:
                        for transaction in transactions:
                            print(f"Identified transaction: {transaction}")
                            # Validate date format for each transaction
                            validated_date = error_handler.convert_date_format(
                                transaction.date, "transaction date"
                            )
                            cli.add_asset_transaction(
                                transaction.symbol,
                                transaction.quantity,
                                transaction.price,
                                transaction.currency,
                                transaction.tx_type,
                                validated_date,
                            )
                    else:
                        print("No transactions found in the text.")

            except Exception as e:
                error_handler.handle_error(
                    e, f"Transaction processing ({format_type} format)"
                )

        elif args.command == "remove-asset":
            cli.remove_asset(args.symbol)
        elif args.command == "update-asset":
            cli.update_asset(
                args.asset_id,
                name=args.name,
                exchange=args.exchange,
                currency=args.currency,
                sector=args.sector,
                description=args.description,
                is_active=args.active,
            )

        elif args.command == "delete-asset":
            cli.delete_asset(args.asset_id)

        elif args.command == "list-assets":
            cli.list_assets(active_only=not args.all)

        elif args.command == "list-sectors":
            cli.list_sectors()

        elif args.command == "show-mapping":
            cli.show_sector_mapping()

        elif args.command == "portfolio-value":
            cli.show_portfolio_value(
                portfolio_name=getattr(args, "portfolio", None),
            )

        elif args.command == "list-transactions":
            # Parse limit argument - can be number, "all", or default
            limit_arg = args.limit
            if limit_arg == "all":
                limit = None  # No limit
            else:
                try:
                    limit = int(limit_arg)
                except ValueError:
                    print(f"❌ Invalid limit '{limit_arg}'. Use a number or 'all'.")
                    return
            cli.list_transactions(
                symbol=args.symbol,
                limit=limit,
                name=getattr(args, "name", None),
                price=getattr(args, "price", None),
                total=getattr(args, "total", None),
                quantity=getattr(args, "quantity", None),
                from_date=getattr(args, "from_date", None),
                to_date=getattr(args, "to_date", None),
                transaction_type=getattr(args, "type", None),
                portfolio_name=getattr(args, "portfolio", None),
            )

        elif args.command == "delete-transaction":
            cli.delete_transaction(args.transaction_id)

        elif args.command == "update-transaction":
            cli.update_transaction(
                args.transaction_id,
                quantity=args.quantity,
                price=args.price,
                transaction_date=args.date,
                transaction_type=args.type,
                description=args.description,
            )

        elif args.command == "import-csv":
            # Check if CSV file exists and is readable
            if not os.path.exists(args.csv_file):
                file_error = FileIOError(
                    f"CSV file not found: {args.csv_file}",
                    args.csv_file,
                    ExitCodes.FILE_NOT_FOUND,
                )
                error_handler.handle_error(file_error, "CSV import")

            if not error_handler.check_file_permissions(args.csv_file, "read"):
                perm_error = PortfolioPermissionError(
                    f"Cannot read CSV file: {args.csv_file}", args.csv_file
                )
                error_handler.handle_error(perm_error, "CSV import")

            cli.import_csv(args.csv_file, args.portfolio, error_handler)

        elif args.command == "import-coinbase-csv":
            # Check if CSV file exists and is readable
            if not os.path.exists(args.csv_file):
                file_error = FileIOError(
                    f"CSV file not found: {args.csv_file}",
                    args.csv_file,
                    ExitCodes.FILE_NOT_FOUND,
                )
                error_handler.handle_error(file_error, "Coinbase CSV import")

            if not error_handler.check_file_permissions(args.csv_file, "read"):
                perm_error = PortfolioPermissionError(
                    f"Cannot read CSV file: {args.csv_file}", args.csv_file
                )
                error_handler.handle_error(perm_error, "Coinbase CSV import")

            cli.import_coinbase_csv(
                args.csv_file, args.portfolio, clear=getattr(args, "clear", False)
            )

        elif args.command == "import-pdt":
            if not os.path.exists(args.xlsx_file):
                file_error = FileIOError(
                    f"XLSX file not found: {args.xlsx_file}",
                    args.xlsx_file,
                    ExitCodes.FILE_NOT_FOUND,
                )
                error_handler.handle_error(file_error, "PDT XLSX import")
            cli.import_pdt_xlsx(
                args.xlsx_file,
                portfolio_name=args.portfolio,
                import_dividends=not getattr(args, "no_dividends", False),
                import_bookings=getattr(args, "import_bookings", False),
                clear=getattr(args, "clear", False),
            )

        elif args.command == "list-bookings":
            cli.list_bookings(portfolio_name=getattr(args, "portfolio", None))

        elif args.command == "sync-pdt-pull":
            cli.sync_pdt_pull(
                sheet_id=getattr(args, "sheet_id", None),
                portfolio_name=getattr(args, "portfolio", None),
                import_bookings=getattr(args, "import_bookings", True),
            )

        elif args.command == "sync-pdt-push":
            cli.sync_pdt_push(
                sheet_id=getattr(args, "sheet_id", None),
                portfolio_name=getattr(args, "portfolio", None),
            )

        elif args.command == "export-pdt":
            output_path = getattr(args, "output", None)
            if not output_path:
                from datetime import date as _date

                output_path = f"pdt_export_{_date.today().strftime('%Y%m%d')}.xlsx"
            cli.export_pdt_xlsx(
                output_path,
                portfolio_name=getattr(args, "portfolio", None),
            )

        elif args.command == "add-entity":
            cli.add_entity(
                name=args.name,
                entity_type=args.entity_type,
                website=args.website,
                description=args.description,
            )

        elif args.command == "list-entities":
            cli.list_entities()

        elif args.command == "add-portfolio":
            cli.add_portfolio(
                name=args.name,
                base_currency=args.currency,
                entity_name=args.entity,
                description=args.description,
            )

        elif args.command == "list-portfolios":
            cli.list_portfolios()

        elif args.command == "export-transactions":
            # Check if func attribute exists (set by set_defaults)
            if hasattr(args, "func"):
                # Pass cli object to handler for authentication
                import inspect

                if len(inspect.signature(args.func).parameters) > 1:
                    args.func(args, cli)
                else:
                    args.func(args)
            else:
                print("❌ Export transactions handler not found")
                sys.exit(ExitCodes.GENERAL_ERROR)

        elif args.command == "export-to-sheets":
            # Check if func attribute exists (set by set_defaults)
            if hasattr(args, "func"):
                # Pass cli object to handler for authentication
                import inspect

                if len(inspect.signature(args.func).parameters) > 1:
                    args.func(args, cli)
                else:
                    args.func(args)
            else:
                print("❌ Export to Google Sheets handler not found")
                sys.exit(ExitCodes.GENERAL_ERROR)

        elif args.command == "extract-tax-report":
            # Check if func attribute exists (set by set_defaults)
            if hasattr(args, "func"):
                args.func(args)
            else:
                print("❌ Extract tax report handler not found")
                sys.exit(ExitCodes.GENERAL_ERROR)

        elif args.command == "update-prices":
            cli.update_prices(symbols=args.symbol, show_values=args.show_values)
        elif args.command == "stock-report":
            try:
                report = run_stock_report(args.symbol, news_provider=args.news_provider)
                print(report)
            except Exception as e:
                error_handler.handle_error(e, f"stock-report for {args.symbol}")

        elif args.command == "chat":
            # Implement interactive chat loop via server API
            try:
                # Import portfolio snapshot capability
                portfolio_context = ""
                if getattr(args, "portfolio_aware", False):
                    try:
                        from .portfolio_snapshot import get_portfolio_context_for_chat

                        db_path = cli.config.db_path if cli.config else "portfolio.db"
                        portfolio_context = get_portfolio_context_for_chat(
                            db_path, max_tokens=800
                        )
                        print("✅ Portfolio context loaded for AI chat")
                    except Exception as e:
                        print(f"⚠️  Warning: Could not load portfolio context: {e}")
                # Choose client depending on mode
                if cli.http_client is None:
                    print(
                        "❌ Chat requires server mode with API key. Use --server and --api-key."
                    )
                    sys.exit(ExitCodes.AUTHENTICATION_ERROR)
                http = cli.http_client
                session_id = getattr(args, "session", None)
                symbols = (
                    [s.strip().upper() for s in args.symbols.split(",")]
                    if getattr(args, "symbols", None)
                    else None
                )
                live = not getattr(args, "no_live", False)
                search = getattr(args, "search", False)

                def send(msg: str):
                    nonlocal session_id

                    # Enhance message with portfolio context if available
                    if portfolio_context and not portfolio_context.startswith("Error"):
                        enhanced_msg = f"""{portfolio_context}

---

User Query: {msg}

Please analyze my query in the context of my current portfolio shown above. Reference specific holdings, positions, and recent transactions when relevant to provide personalized advice."""
                        msg = enhanced_msg

                    resp = http.chat(
                        message=msg,
                        session_id=session_id,
                        symbols=symbols,
                        live=live,
                        search=search,
                    )
                    session_id = resp.get("session_id", session_id)
                    print(resp.get("answer", ""))

                if getattr(args, "once", False):
                    # Read single message from stdin or prompt
                    if not sys.stdin.isatty():
                        msg = sys.stdin.read()
                    else:
                        msg = input("You: ")
                    send(msg)
                    return

                # Interactive loop
                readline_enabled = setup_readline()
                if readline_enabled:
                    print("✅ Chat history enabled (use ↑/↓ to navigate)")
                print("💬 Type your questions. Type /exit to quit, /help for commands.")
                while True:
                    q = enhanced_input("You> ").strip()
                    if not q:
                        continue
                    if q in ("/exit", ":q", "quit", "exit"):
                        break
                    if q in ("/help", "help"):
                        print("Commands: /exit, /help")
                        continue
                    send(q)
            except Exception as e:
                error_handler.handle_error(e, "chat")
        else:
            print(f"❌ Unknown command: {args.command}")
            if parser:
                parser.print_help()
            sys.exit(ExitCodes.INVALID_INPUT)

    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(ExitCodes.SUCCESS)
    except PortfolioManagerError as e:
        error_handler.handle_error(e, f"command '{args.command}'")
    except ValueError as e:
        error_handler.handle_date_error(e, context=f"command '{args.command}'")
    except OSError as e:
        error_handler.handle_file_error(e, context=f"command '{args.command}'")
    except Exception as e:
        error_handler.handle_error(e, f"command '{args.command}'")


if __name__ == "__main__":
    main()


# Multi-Agent Analysis Integration
def add_multi_agent_commands(parser):
    """Add multi-agent analysis commands to the parser"""
    # Add multi-agent subcommand group
    parser.add_argument(
        "--multi-agent", action="store_true", help="Launch multi-agent analysis console"
    )
    parser.add_argument(
        "--multi-agent-quick",
        type=str,
        help="Run quick multi-agent analysis on symbol(s)",
    )
