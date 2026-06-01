"""
CSV Export Service for Filtered Transactions

This module provides functionality to export filtered transactions to CSV format
with proper headers, formatting, and deterministic column ordering.
"""

import csv
from datetime import datetime

from .transaction_filter import TransactionFilterService, TransactionFilter
from .models import Transaction, DatabaseAdapter
from .auth import AuthManager


class TransactionCSVExporter:
    """Service for exporting filtered transactions to CSV format."""

    # Define deterministic column ordering
    CSV_HEADERS = [
        "Transaction ID",
        "Date",
        "Symbol",
        "Asset Name",
        "Transaction Type",
        "Quantity",
        "Price",
        "Total Amount",
        "Currency",
        "Portfolio Name",
        "Description",
    ]

    def __init__(self, db_adapter: DatabaseAdapter, auth_manager: AuthManager):
        self.db_adapter = db_adapter
        self.auth_manager = auth_manager
        self.filter_service = TransactionFilterService(db_adapter, auth_manager)

    def export_transactions_to_csv(
        self,
        filter_criteria: TransactionFilter,
        output_file: str,
        encoding: str = "utf-8",
    ) -> bool:
        """
        Export filtered transactions to CSV file.

        Args:
            filter_criteria: TransactionFilter object containing filter parameters
            output_file: Path to output CSV file
            encoding: File encoding (default: utf-8)

        Returns:
            True if export successful, False otherwise

        Raises:
            AuthenticationError: If user is not authenticated
            ValueError: If filter criteria are invalid
            IOError: If file cannot be written
        """
        try:
            # Get filtered transactions
            transactions = self.filter_service.get_user_transactions(filter_criteria)

            if not transactions:
                print("⚠️  No transactions found matching the filter criteria.")
                return False

            # Write to CSV with proper formatting
            with open(output_file, "w", newline="", encoding=encoding) as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.CSV_HEADERS)
                writer.writeheader()

                for transaction in transactions:
                    # Convert transaction to CSV row
                    csv_row = self._transaction_to_csv_row(transaction)
                    writer.writerow(csv_row)

            print(
                f"✅ Successfully exported {len(transactions)} transactions to {output_file}"
            )
            return True

        except Exception as e:
            print(f"❌ Error exporting transactions to CSV: {e}")
            return False

    def _transaction_to_csv_row(self, transaction: Transaction) -> dict:
        """
        Convert a Transaction object to CSV row dictionary.

        Args:
            transaction: Transaction object to convert

        Returns:
            Dictionary with CSV column names as keys and formatted values
        """
        # Get asset information
        asset = transaction.get_asset()
        asset_name = asset.name if asset else "Unknown"
        symbol = asset.symbol if asset else "Unknown"
        currency = asset.currency if asset else "USD"

        # Get portfolio information
        portfolio_name = ""
        if transaction.portfolio_id:
            portfolio_data = self.db_adapter.get_portfolio(transaction.portfolio_id)
            portfolio_name = portfolio_data.get("name", "") if portfolio_data else ""

        # Format datetime to ISO-8601
        transaction_date = transaction.transaction_date
        if isinstance(transaction_date, datetime):
            date_str = transaction_date.isoformat()
        else:
            # If it's a date object, convert to datetime at midnight and format
            date_str = datetime.combine(
                transaction_date, datetime.min.time()
            ).isoformat()

        # Format numerics with full precision (no trailing zeros stripped)
        # Using str() to preserve full decimal precision
        quantity_str = str(transaction.quantity)
        price_str = str(transaction.price)
        total_amount_str = str(transaction.total_amount)

        return {
            "Transaction ID": transaction.id,
            "Date": date_str,
            "Symbol": symbol,
            "Asset Name": asset_name,
            "Transaction Type": transaction.transaction_type.value,
            "Quantity": quantity_str,
            "Price": price_str,
            "Total Amount": total_amount_str,
            "Currency": currency,
            "Portfolio Name": portfolio_name,
            "Description": transaction.description or "",
        }

    def export_with_summary(
        self,
        filter_criteria: TransactionFilter,
        output_file: str,
        encoding: str = "utf-8",
    ) -> dict:
        """
        Export filtered transactions to CSV and return summary statistics.

        Args:
            filter_criteria: TransactionFilter object containing filter parameters
            output_file: Path to output CSV file
            encoding: File encoding (default: utf-8)

        Returns:
            Dictionary containing export results and summary statistics
        """
        try:
            # Get filtered transactions and summary
            transactions = self.filter_service.get_user_transactions(filter_criteria)
            summary = self.filter_service.get_filtered_transaction_summary(
                filter_criteria
            )

            if not transactions:
                return {
                    "success": False,
                    "message": "No transactions found matching the filter criteria",
                    "summary": summary,
                }

            # Export to CSV
            export_success = self.export_transactions_to_csv(
                filter_criteria, output_file, encoding
            )

            return {
                "success": export_success,
                "message": f"Exported {len(transactions)} transactions to {output_file}",
                "output_file": output_file,
                "transaction_count": len(transactions),
                "summary": summary,
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"Error during export: {e}",
                "summary": None,
            }


def create_csv_exporter(
    db_adapter: DatabaseAdapter, auth_manager: AuthManager
) -> TransactionCSVExporter:
    """
    Factory function to create a TransactionCSVExporter instance.

    Args:
        db_adapter: Database adapter for data access
        auth_manager: Authentication manager for user context

    Returns:
        TransactionCSVExporter instance
    """
    return TransactionCSVExporter(db_adapter, auth_manager)


def example_usage():
    """
    Example usage of the TransactionCSVExporter.

    This function demonstrates how to:
    1. Initialize the exporter with database adapter and auth manager
    2. Create filter criteria
    3. Export transactions to CSV
    4. Handle export results
    """
    from .database import Database
    from .auth import AuthManager
    from datetime import date

    # Initialize database and auth manager
    db_manager = Database("portfolio.db")
    auth_manager = AuthManager(db_manager)

    # Create the CSV exporter
    csv_exporter = create_csv_exporter(db_manager, auth_manager)

    # Example 1: Export all transactions
    print("=== Example 1: Export all transactions ===")
    filter_criteria = TransactionFilter()
    result = csv_exporter.export_with_summary(filter_criteria, "all_transactions.csv")
    print(f"Export result: {result}")

    # Example 2: Export filtered transactions
    print("\n=== Example 2: Export filtered transactions ===")
    filter_criteria = TransactionFilter(
        symbol="AAPL",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        timezone="America/New_York",
    )
    result = csv_exporter.export_with_summary(
        filter_criteria, "aapl_transactions_2024.csv"
    )
    print(f"Export result: {result}")

    # Example 3: Simple export without summary
    print("\n=== Example 3: Simple export ===")
    filter_criteria = TransactionFilter(symbol="TSLA")
    success = csv_exporter.export_transactions_to_csv(
        filter_criteria, "tsla_transactions.csv"
    )
    print(f"Export success: {success}")
