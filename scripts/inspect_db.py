#!/usr/bin/env python3
"""
Script to inspect the existing database state and identify potential issues.
"""

import sqlite3
import sys
from pathlib import Path


def inspect_database(db_path):
    """Inspect database state and identify potential issues."""
    print(f"=== Inspecting {db_path} ===")

    if not Path(db_path).exists():
        print(f"Database file {db_path} does not exist.")
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check database version
        print("\n1. Version Information:")
        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='database_version'"
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
                )
                version = cursor.fetchone()
                if version:
                    print(f"   Current version: {version[0]}")
                else:
                    print("   No version records found")
            else:
                print("   database_version table does not exist")
        except Exception as e:
            print(f"   Error checking version: {e}")

        # Check SQLite user_version pragma
        print("\n2. SQLite PRAGMA user_version:")
        try:
            cursor.execute("PRAGMA user_version")
            pragma_version = cursor.fetchone()[0]
            print(f"   PRAGMA user_version: {pragma_version}")
        except Exception as e:
            print(f"   Error checking PRAGMA user_version: {e}")

        # List all tables
        print("\n3. Tables:")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"   Tables: {tables}")

        # Check if users table exists and has data
        print("\n4. Users Table:")
        if "users" in tables:
            cursor.execute("PRAGMA table_info(users)")
            columns = [dict(row) for row in cursor.fetchall()]
            print(f"   Users table columns: {len(columns)}")

            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            print(f"   Users count: {count}")

            if count > 0:
                cursor.execute("SELECT username, email, is_active FROM users LIMIT 5")
                users = cursor.fetchall()
                print("   Sample users:")
                for user in users:
                    print(
                        f"     {user['username']} ({user['email']}) - Active: {user['is_active']}"
                    )
        else:
            print("   Users table does not exist")

        # Check for missing user_id columns in existing tables
        print("\n5. User ID Integration:")
        for table in ["entities", "portfolios", "transactions"]:
            if table in tables:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [col["name"] for col in cursor.fetchall()]
                has_user_id = "user_id" in columns
                print(f"   {table} has user_id column: {has_user_id}")

                if has_user_id:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
                    )
                    null_count = cursor.fetchone()[0]
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    total_count = cursor.fetchone()[0]
                    print(
                        f"   {table} records with NULL user_id: {null_count}/{total_count}"
                    )

        # Check for foreign key constraints
        print("\n6. Foreign Key Constraints:")
        cursor.execute("PRAGMA foreign_keys")
        fk_enabled = cursor.fetchone()[0]
        print(f"   Foreign keys enabled: {fk_enabled}")

        # Look for any constraints violations
        cursor.execute("PRAGMA foreign_key_check")
        violations = cursor.fetchall()
        if violations:
            print(f"   Foreign key violations: {len(violations)}")
            for violation in violations:
                print(f"     {violation}")
        else:
            print("   No foreign key violations found")

        # Check for any transaction/rollback issues
        print("\n7. Transaction State:")
        cursor.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        print(f"   Journal mode: {journal_mode}")

        cursor.execute("PRAGMA synchronous")
        synchronous = cursor.fetchone()[0]
        print(f"   Synchronous: {synchronous}")

        conn.close()

    except Exception as e:
        print(f"Error inspecting database: {e}")


def main():
    """Main function to inspect databases."""
    db_files = ["portfolio.db", "test_portfolio.db"]

    for db_file in db_files:
        if Path(db_file).exists():
            inspect_database(db_file)
            print()


if __name__ == "__main__":
    main()
