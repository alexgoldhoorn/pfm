#!/usr/bin/env python3
"""
Script to create a new API key for the Portfolio Management API server.
"""

from portf_manager.database import Database
from portf_server.auth_middleware import APIKeyManager
from portf_server.settings import get_settings


def main():
    # Get settings and initialize database
    settings = get_settings()

    # Extract database path from settings
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.replace("sqlite:///", "").replace(
            "sqlite://", ""
        )
        database = Database(db_path)
    else:
        database = Database("portfolio.db")

    # Initialize API key manager
    api_key_manager = APIKeyManager(database)

    # Create a new API key
    key_info = api_key_manager.create_api_key(
        key_name="development_key",
        description="Development API key for local testing",
        expires_days=None,  # No expiration
    )

    print(f"API Key created successfully!")
    print(f"Key Name: {key_info['key_name']}")
    print(f"Key Prefix: {key_info['key_prefix']}")
    print(f"API Key: {key_info['api_key']}")
    print(f"\nAdd this to your .env.local file:")
    print(f"SERVER_API_KEY={key_info['api_key']}")
    print(f"\nUse it in requests with:")
    print(f'curl -H "X-API-Key: {key_info['api_key']}" ...')


if __name__ == "__main__":
    main()
