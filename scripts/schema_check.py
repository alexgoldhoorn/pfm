#!/usr/bin/env python3
"""
Script to examine table schemas and identify missing columns from v3 migration.
"""

import sqlite3
from pathlib import Path


def check_table_schema(db_path):
    """Check the schema of critical tables."""
    print(f"=== Schema Check for {db_path} ===")

    if not Path(db_path).exists():
        print(f"Database file {db_path} does not exist.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Expected columns for each table after v3 migration
    expected_columns = {
        "entities": [
            "id",
            "user_id",
            "name",
            "entity_type",
            "website",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ],
        "portfolios": [
            "id",
            "name",
            "base_currency",
            "entity_id",
            "user_id",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ],
        "transactions": [
            "id",
            "asset_id",
            "portfolio_id",
            "transaction_type",
            "quantity",
            "price",
            "total_amount",
            "fees",
            "transaction_date",
            "user_id",
            "description",
            "created_at",
            "updated_at",
        ],
        "users": [
            "id",
            "username",
            "email",
            "password_hash",
            "salt",
            "full_name",
            "is_active",
            "last_login",
            "created_at",
            "updated_at",
        ],
    }

    for table_name, expected_cols in expected_columns.items():
        print(f"\n{table_name.upper()} Table:")

        # Check if table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not cursor.fetchone():
            print(f"   Table {table_name} does not exist")
            continue

        # Get actual columns
        cursor.execute(f"PRAGMA table_info({table_name})")
        actual_columns = [col["name"] for col in cursor.fetchall()]

        print(f"   Actual columns: {actual_columns}")
        print(f"   Expected columns: {expected_cols}")

        # Check for missing columns
        missing_cols = [col for col in expected_cols if col not in actual_columns]
        if missing_cols:
            print(f"   ❌ Missing columns: {missing_cols}")
        else:
            print(f"   ✅ All expected columns present")

        # Check for extra columns
        extra_cols = [col for col in actual_columns if col not in expected_cols]
        if extra_cols:
            print(f"   ⚠️  Extra columns: {extra_cols}")

        # If table has data, check for NULL user_id values
        if "user_id" in actual_columns:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            total_count = cursor.fetchone()[0]
            if total_count > 0:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE user_id IS NULL"
                )
                null_count = cursor.fetchone()[0]
                print(f"   Records: {total_count}, NULL user_id: {null_count}")

    # Check database version history
    print(f"\nVERSION HISTORY:")
    cursor.execute("SELECT version, created_at FROM database_version ORDER BY version")
    versions = cursor.fetchall()
    for version in versions:
        print(f"   Version {version['version']}: {version['created_at']}")

    conn.close()


def main():
    """Main function."""
    db_files = ["portfolio.db", "test_portfolio.db"]

    for db_file in db_files:
        if Path(db_file).exists():
            check_table_schema(db_file)
            print()


if __name__ == "__main__":
    main()
