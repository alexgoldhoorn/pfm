#!/usr/bin/env python3
"""
Test script for CSV Export functionality

This script demonstrates the CSV export functionality for filtered transactions.
"""

from datetime import date
from portf_manager.csv_export import create_csv_exporter
from portf_manager.transaction_filter import TransactionFilter
from portf_manager.database import Database
from portf_manager.auth import AuthManager


def test_csv_export():
    """Test the CSV export functionality."""
    print("🧪 Testing CSV Export Functionality")
    print("=" * 50)

    try:
        # Initialize database and auth manager
        db_manager = Database("portfolio.db")
        auth_manager = AuthManager(db_manager)

        # Check if user is authenticated
        if not auth_manager.is_authenticated():
            print("❌ User not authenticated. Please login first.")
            print("💡 You can login using: python -m portf_manager login")
            return

        # Create CSV exporter
        csv_exporter = create_csv_exporter(db_manager, auth_manager)

        # Test 1: Export all transactions
        print("\n📋 Test 1: Export all transactions")
        filter_criteria = TransactionFilter()
        result = csv_exporter.export_with_summary(
            filter_criteria, "test_all_transactions.csv"
        )
        print(f"Result: {result['success']}")
        print(f"Message: {result['message']}")
        if result["success"]:
            print(f"📁 Output file: {result['output_file']}")
            print(f"📊 Transaction count: {result['transaction_count']}")

        # Test 2: Export with symbol filter
        print("\n📋 Test 2: Export with symbol filter (AAPL)")
        filter_criteria = TransactionFilter(symbol="AAPL")
        result = csv_exporter.export_with_summary(
            filter_criteria, "test_aapl_transactions.csv"
        )
        print(f"Result: {result['success']}")
        print(f"Message: {result['message']}")
        if result["success"]:
            print(f"📁 Output file: {result['output_file']}")
            print(f"📊 Transaction count: {result['transaction_count']}")

        # Test 3: Export with date range filter
        print("\n📋 Test 3: Export with date range filter (2024)")
        filter_criteria = TransactionFilter(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            timezone="America/New_York",
        )
        result = csv_exporter.export_with_summary(
            filter_criteria, "test_2024_transactions.csv"
        )
        print(f"Result: {result['success']}")
        print(f"Message: {result['message']}")
        if result["success"]:
            print(f"📁 Output file: {result['output_file']}")
            print(f"📊 Transaction count: {result['transaction_count']}")

        # Test 4: Export with combined filters
        print("\n📋 Test 4: Export with combined filters (TSLA in 2024)")
        filter_criteria = TransactionFilter(
            symbol="TSLA",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            timezone="UTC",
        )
        result = csv_exporter.export_with_summary(
            filter_criteria, "test_tsla_2024_transactions.csv"
        )
        print(f"Result: {result['success']}")
        print(f"Message: {result['message']}")
        if result["success"]:
            print(f"📁 Output file: {result['output_file']}")
            print(f"📊 Transaction count: {result['transaction_count']}")

        print("\n✅ CSV Export tests completed!")
        print("\n💡 Check the generated CSV files to verify:")
        print(
            "   - Headers match: Transaction ID, Date, Symbol, Asset Name, Transaction Type, Quantity, Price, Total Amount, Currency, Portfolio Name, Description"
        )
        print("   - Dates are in ISO-8601 format")
        print("   - Numeric values have full precision")
        print("   - Column ordering is deterministic")

    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_csv_export()
