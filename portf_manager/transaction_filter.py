"""
Transaction Filtering Service

This module provides functionality to retrieve and filter transactions based on:
- Symbol filter (case-insensitive match)
- Date range filter (inclusive, with timezone-aware datetime conversion)
"""

from datetime import datetime, date
from typing import List, Optional, Dict, Any
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from .models import Transaction, DatabaseAdapter
from .auth import AuthManager


@dataclass
class TransactionFilter:
    """Filter criteria for transaction retrieval."""

    symbol: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    timezone: str = "UTC"


class TransactionFilterService:
    """Service for filtering transactions based on various criteria."""

    def __init__(self, db_adapter: DatabaseAdapter, auth_manager: AuthManager):
        self.db_adapter = db_adapter
        self.auth_manager = auth_manager

    def get_user_transactions(
        self, filter_criteria: TransactionFilter
    ) -> List[Transaction]:
        """
        Retrieve and filter transactions for the current user.

        Args:
            filter_criteria: TransactionFilter object containing filter parameters

        Returns:
            List of filtered Transaction objects

        Raises:
            AuthenticationError: If user is not authenticated
            ValueError: If filter criteria are invalid
        """
        # Ensure user is authenticated
        if not self.auth_manager.is_authenticated():
            raise ValueError("User must be authenticated to retrieve transactions")

        current_user = self.auth_manager.get_current_user()
        if not current_user:
            raise ValueError("Unable to retrieve current user information")

        # Get all transactions for current user
        transaction_dicts = self.db_adapter.get_all_transactions(
            user_id=current_user["id"]
        )

        if not transaction_dicts:
            print("⚠️  No transactions found for current user")
            return []

        # Convert to Transaction objects
        transactions = [
            Transaction.from_dict(tx_dict, self.db_adapter)
            for tx_dict in transaction_dicts
        ]

        # Apply filters
        filtered_transactions = self._apply_filters(transactions, filter_criteria)

        # Check if no transactions remain after filtering
        if not filtered_transactions:
            self._warn_no_transactions_found(filter_criteria)
            return []

        return filtered_transactions

    def _apply_filters(
        self, transactions: List[Transaction], filter_criteria: TransactionFilter
    ) -> List[Transaction]:
        """Apply filtering criteria to transactions."""

        filtered = transactions

        # Apply symbol filter (case-insensitive)
        if filter_criteria.symbol:
            filtered = self._apply_symbol_filter(filtered, filter_criteria.symbol)

        # Apply date range filter
        if filter_criteria.start_date or filter_criteria.end_date:
            filtered = self._apply_date_range_filter(
                filtered,
                filter_criteria.start_date,
                filter_criteria.end_date,
                filter_criteria.timezone,
            )

        return filtered

    def _apply_symbol_filter(
        self, transactions: List[Transaction], symbol: str
    ) -> List[Transaction]:
        """Apply case-insensitive symbol filter."""
        symbol_upper = symbol.upper()

        filtered = []
        for tx in transactions:
            # Get asset information for this transaction
            asset = tx.get_asset()
            if asset and asset.symbol.upper() == symbol_upper:
                filtered.append(tx)

        return filtered

    def _apply_date_range_filter(
        self,
        transactions: List[Transaction],
        start_date: Optional[date],
        end_date: Optional[date],
        timezone: str,
    ) -> List[Transaction]:
        """Apply inclusive date range filter with timezone-aware datetime conversion."""

        filtered = []

        # Convert filter dates to timezone-aware datetimes
        tz = ZoneInfo(timezone)

        start_datetime = None
        end_datetime = None

        if start_date:
            # Start of day in specified timezone
            start_datetime = datetime.combine(start_date, datetime.min.time()).replace(
                tzinfo=tz
            )

        if end_date:
            # End of day in specified timezone
            end_datetime = datetime.combine(end_date, datetime.max.time()).replace(
                tzinfo=tz
            )

        for tx in transactions:
            # Convert transaction date to timezone-aware datetime
            tx_datetime = datetime.combine(
                tx.transaction_date, datetime.min.time()
            ).replace(tzinfo=tz)

            # Apply inclusive date range filter
            if start_datetime and tx_datetime < start_datetime:
                continue
            if end_datetime and tx_datetime > end_datetime:
                continue

            filtered.append(tx)

        return filtered

    def _warn_no_transactions_found(self, filter_criteria: TransactionFilter):
        """Warn user when no transactions remain after filtering."""

        warning_parts = ["⚠️  No transactions found"]

        if filter_criteria.symbol:
            warning_parts.append(f"for symbol '{filter_criteria.symbol.upper()}'")

        if filter_criteria.start_date and filter_criteria.end_date:
            warning_parts.append(
                f"between {filter_criteria.start_date} and {filter_criteria.end_date}"
            )
        elif filter_criteria.start_date:
            warning_parts.append(f"from {filter_criteria.start_date} onwards")
        elif filter_criteria.end_date:
            warning_parts.append(f"up to {filter_criteria.end_date}")

        if filter_criteria.timezone != "UTC":
            warning_parts.append(f"(timezone: {filter_criteria.timezone})")

        print(" ".join(warning_parts))
        print(
            "💡 Try adjusting your filter criteria or check if you have any transactions in the database."
        )

    def get_filtered_transaction_summary(
        self, filter_criteria: TransactionFilter
    ) -> Dict[str, Any]:
        """
        Get a summary of filtered transactions.

        Args:
            filter_criteria: TransactionFilter object containing filter parameters

        Returns:
            Dictionary containing transaction summary statistics
        """

        transactions = self.get_user_transactions(filter_criteria)

        if not transactions:
            return {
                "total_count": 0,
                "symbols": [],
                "date_range": None,
                "transaction_types": {},
                "total_value": 0.0,
            }

        # Calculate summary statistics
        symbols = set()
        transaction_types = {}
        total_value = 0.0

        earliest_date = None
        latest_date = None

        for tx in transactions:
            # Get asset symbol
            asset = tx.get_asset()
            if asset:
                symbols.add(asset.symbol)

            # Count transaction types
            tx_type = tx.transaction_type.value
            transaction_types[tx_type] = transaction_types.get(tx_type, 0) + 1

            # Sum total values
            total_value += float(tx.total_amount)

            # Track date range
            if earliest_date is None or tx.transaction_date < earliest_date:
                earliest_date = tx.transaction_date
            if latest_date is None or tx.transaction_date > latest_date:
                latest_date = tx.transaction_date

        return {
            "total_count": len(transactions),
            "symbols": sorted(list(symbols)),
            "date_range": {
                "earliest": earliest_date.isoformat() if earliest_date else None,
                "latest": latest_date.isoformat() if latest_date else None,
            },
            "transaction_types": transaction_types,
            "total_value": total_value,
        }


def create_transaction_filter_service(
    db_adapter: DatabaseAdapter, auth_manager: AuthManager
) -> TransactionFilterService:
    """
    Factory function to create a TransactionFilterService instance.

    Args:
        db_adapter: Database adapter for data access
        auth_manager: Authentication manager for user context

    Returns:
        TransactionFilterService instance
    """
    return TransactionFilterService(db_adapter, auth_manager)


def example_usage():
    """
    Example usage of the TransactionFilterService.

    This function demonstrates how to:
    1. Initialize the service with database adapter and auth manager
    2. Create filter criteria with symbol and date range
    3. Retrieve and filter transactions
    4. Handle the case when no transactions are found
    """
    from .database import Database
    from .auth import AuthManager
    from datetime import date

    # Initialize database and auth manager
    db_manager = Database("portfolio.db")
    auth_manager = AuthManager(db_manager)

    # Create the transaction filter service
    filter_service = create_transaction_filter_service(db_manager, auth_manager)

    # Example 1: Filter by symbol only
    print("=== Example 1: Filter by symbol 'AAPL' ===")
    filter_criteria = TransactionFilter(symbol="AAPL")
    transactions = filter_service.get_user_transactions(filter_criteria)
    print(f"Found {len(transactions)} transactions for AAPL")

    # Example 2: Filter by date range only
    print("\n=== Example 2: Filter by date range ===")
    filter_criteria = TransactionFilter(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        timezone="America/New_York",
    )
    transactions = filter_service.get_user_transactions(filter_criteria)
    print(f"Found {len(transactions)} transactions in 2024")

    # Example 3: Filter by both symbol and date range
    print("\n=== Example 3: Filter by symbol and date range ===")
    filter_criteria = TransactionFilter(
        symbol="TSLA",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 30),
        timezone="America/New_York",
    )
    transactions = filter_service.get_user_transactions(filter_criteria)
    print(f"Found {len(transactions)} TSLA transactions in June 2024")

    # Example 4: Get transaction summary
    print("\n=== Example 4: Transaction summary ===")
    filter_criteria = TransactionFilter()  # No filters - all transactions
    summary = filter_service.get_filtered_transaction_summary(filter_criteria)
    print(f"Summary: {summary}")

    # Example 5: Handle case with no results
    print("\n=== Example 5: Filter with no results ===")
    filter_criteria = TransactionFilter(symbol="NONEXISTENT")
    transactions = filter_service.get_user_transactions(filter_criteria)
    # This will print a warning and return an empty list
