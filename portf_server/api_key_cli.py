#!/usr/bin/env python3
"""
API Key Management CLI Utility

Command-line utility for generating, listing, and managing API keys
for the Portfolio Management API.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # noqa: E402

from portf_manager.database import Database  # noqa: E402
from portf_server.auth_middleware import APIKeyManager, APIKeyError  # noqa: E402


def create_key(args):
    """Create a new API key."""
    try:
        # Initialize database and API key manager
        db = Database(args.database)
        api_key_manager = APIKeyManager(db)

        # Create the API key
        key_info = api_key_manager.create_api_key(
            key_name=args.name,
            description=args.description,
            expires_days=args.expires_days,
        )

        print("✅ API Key created successfully!")
        print(f"🔑 API Key: {key_info['api_key']}")
        print(f"📛 Name: {key_info['key_name']}")
        print(f"🏷️  Prefix: {key_info['key_prefix']}")

        if key_info.get("description"):
            print(f"📄 Description: {key_info['description']}")

        if key_info.get("expires_at"):
            print(f"⏰ Expires: {key_info['expires_at']}")

        print("\n⚠️  IMPORTANT: Save this API key now - it won't be shown again!")
        print("Usage: Include in requests as 'X-API-Key: <your-key>' header")

    except APIKeyError as e:
        print(f"❌ Error creating API key: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


def list_keys(args):
    """List all API keys."""
    try:
        # Initialize database and API key manager
        db = Database(args.database)
        api_key_manager = APIKeyManager(db)

        # Get all API keys
        keys = api_key_manager.list_api_keys()

        if not keys:
            print("📭 No API keys found.")
            return

        print(f"📋 Found {len(keys)} API key(s):\n")

        # Print header
        print(
            f"{'ID':<4} {'Name':<20} {'Prefix':<12} {'Status':<8} {'Created':<20} {'Last Used':<20}"
        )
        print("-" * 100)

        # Print each key
        for key in keys:
            status = "Active" if key["is_active"] else "Inactive"
            created = (
                datetime.fromisoformat(key["created_at"]).strftime("%Y-%m-%d %H:%M")
                if key["created_at"]
                else "Unknown"
            )
            last_used = (
                datetime.fromisoformat(key["last_used"]).strftime("%Y-%m-%d %H:%M")
                if key["last_used"]
                else "Never"
            )

            print(
                f"{key['id']:<4} {key['key_name']:<20} {key['key_prefix'] + '...':<12} {status:<8} {created:<20} {last_used:<20}"
            )

            if key.get("description"):
                print(f"     📄 {key['description']}")

            if key.get("expires_at"):
                expires = datetime.fromisoformat(key["expires_at"]).strftime(
                    "%Y-%m-%d %H:%M"
                )
                print(f"     ⏰ Expires: {expires}")

            print()

    except Exception as e:
        print(f"❌ Error listing API keys: {e}")
        sys.exit(1)


def deactivate_key(args):
    """Deactivate an API key."""
    try:
        # Initialize database and API key manager
        db = Database(args.database)
        api_key_manager = APIKeyManager(db)

        # Deactivate the key
        success = api_key_manager.deactivate_api_key(args.key_id)

        if success:
            print(f"✅ API key {args.key_id} deactivated successfully!")
        else:
            print(f"❌ Failed to deactivate API key {args.key_id}. Key may not exist.")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Error deactivating API key: {e}")
        sys.exit(1)


def delete_key(args):
    """Delete an API key permanently."""
    try:
        # Initialize database and API key manager
        db = Database(args.database)
        api_key_manager = APIKeyManager(db)

        # Confirm deletion if not forced
        if not args.force:
            confirm = input(
                f"⚠️  Are you sure you want to permanently delete API key {args.key_id}? (y/N): "
            )
            if confirm.lower() != "y":
                print("❌ Deletion cancelled.")
                return

        # Delete the key
        success = api_key_manager.delete_api_key(args.key_id)

        if success:
            print(f"✅ API key {args.key_id} deleted successfully!")
        else:
            print(f"❌ Failed to delete API key {args.key_id}. Key may not exist.")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Error deleting API key: {e}")
        sys.exit(1)


def create_default_database_schema(db_path: str):
    """Create the default database schema if it doesn't exist."""
    try:
        db = Database(db_path)

        # Check if api_keys table exists
        with db.get_connection() as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='api_keys'
            """)

            if not cursor.fetchone():
                # Create api_keys table
                conn.execute("""
                    CREATE TABLE api_keys (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key_name TEXT NOT NULL,
                        key_hash TEXT NOT NULL UNIQUE,
                        key_prefix TEXT NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        description TEXT,
                        last_used DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        expires_at DATETIME
                    )
                """)

                # Create indexes
                conn.execute("CREATE INDEX idx_api_keys_key_hash ON api_keys(key_hash)")
                conn.execute("CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix)")
                conn.execute("CREATE INDEX idx_api_keys_active ON api_keys(is_active)")

                conn.commit()
                print("📊 Created api_keys table in database.")

    except Exception as e:
        print(f"❌ Error creating database schema: {e}")
        sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="API Key Management CLI for Portfolio Management API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--database",
        default="portfolio.db",
        help="Path to SQLite database file (default: portfolio.db)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Create key command
    create_parser = subparsers.add_parser("create", help="Create a new API key")
    create_parser.add_argument("name", help="Name for the API key")
    create_parser.add_argument(
        "--description", help="Optional description for the API key"
    )
    create_parser.add_argument(
        "--expires-days", type=int, help="Number of days until expiration (optional)"
    )
    create_parser.set_defaults(func=create_key)

    # List keys command
    list_parser = subparsers.add_parser("list", help="List all API keys")
    list_parser.set_defaults(func=list_keys)

    # Deactivate key command
    deactivate_parser = subparsers.add_parser(
        "deactivate", help="Deactivate an API key"
    )
    deactivate_parser.add_argument(
        "key_id", type=int, help="ID of the API key to deactivate"
    )
    deactivate_parser.set_defaults(func=deactivate_key)

    # Delete key command
    delete_parser = subparsers.add_parser(
        "delete", help="Permanently delete an API key"
    )
    delete_parser.add_argument("key_id", type=int, help="ID of the API key to delete")
    delete_parser.add_argument(
        "--force", action="store_true", help="Skip confirmation prompt"
    )
    delete_parser.set_defaults(func=delete_key)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Ensure database schema exists
    create_default_database_schema(args.database)

    # Execute command
    args.func(args)


if __name__ == "__main__":
    main()
