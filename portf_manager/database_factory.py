"""
Database Factory for Portfolio Management

This module provides a unified database factory that automatically chooses
between SQLite and PostgreSQL implementations based on the DATABASE_URL
environment variable, ensuring compatibility with existing business logic.
"""

import os
import logging
from typing import Union
from .database import Database as SQLiteDatabase
from .database_pg import PostgreSQLDatabase

logger = logging.getLogger(__name__)


def get_database_adapter() -> Union[SQLiteDatabase, PostgreSQLDatabase]:
    """
    Get the appropriate database adapter based on environment configuration.

    Returns:
        Database adapter instance (SQLite or PostgreSQL)

    Raises:
        ValueError: If DATABASE_URL is set but invalid
        Exception: If database initialization fails
    """
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        # PostgreSQL URL detected
        if database_url.startswith("postgresql://") or database_url.startswith(
            "postgres://"
        ):
            logger.info("Using PostgreSQL database adapter")
            return PostgreSQLDatabase(database_url)
        else:
            raise ValueError(f"Unsupported database URL format: {database_url}")
    else:
        # Default to SQLite
        logger.info("Using SQLite database adapter")
        db_path = os.getenv("SQLITE_DB_PATH", "portfolio.db")
        return SQLiteDatabase(db_path)


# Singleton instance for application-wide use
_database_instance = None


def get_database() -> Union[SQLiteDatabase, PostgreSQLDatabase]:
    """
    Get singleton database instance.

    Returns:
        Database adapter instance (SQLite or PostgreSQL)
    """
    global _database_instance
    if _database_instance is None:
        _database_instance = get_database_adapter()
    return _database_instance


def reset_database_instance():
    """Reset the singleton database instance (useful for testing)."""
    global _database_instance
    _database_instance = None


# Compatibility functions for existing code
def create_asset(*args, **kwargs):
    """Create asset using the configured database adapter."""
    db = get_database()
    return db.create_asset(*args, **kwargs)


def get_asset(*args, **kwargs):
    """Get asset using the configured database adapter."""
    db = get_database()
    return db.get_asset(*args, **kwargs)


def get_asset_by_symbol(*args, **kwargs):
    """Get asset by symbol using the configured database adapter."""
    db = get_database()
    return db.get_asset_by_symbol(*args, **kwargs)


def create_transaction(*args, **kwargs):
    """Create transaction using the configured database adapter."""
    db = get_database()
    return db.create_transaction(*args, **kwargs)


def get_transactions_by_asset(*args, **kwargs):
    """Get transactions by asset using the configured database adapter."""
    db = get_database()
    return db.get_transactions_by_asset(*args, **kwargs)


def get_all_transactions(*args, **kwargs):
    """Get all transactions using the configured database adapter."""
    db = get_database()
    return db.get_all_transactions(*args, **kwargs)


def get_latest_price(*args, **kwargs):
    """Get latest price using the configured database adapter."""
    db = get_database()
    return db.get_latest_price(*args, **kwargs)


def get_price_history(*args, **kwargs):
    """Get price history using the configured database adapter."""
    db = get_database()
    return db.get_price_history(*args, **kwargs)


def create_portfolio(*args, **kwargs):
    """Create portfolio using the configured database adapter."""
    db = get_database()
    return db.create_portfolio(*args, **kwargs)


def get_portfolio(*args, **kwargs):
    """Get portfolio using the configured database adapter."""
    db = get_database()
    return db.get_portfolio(*args, **kwargs)


def get_portfolio_by_name(*args, **kwargs):
    """Get portfolio by name using the configured database adapter."""
    db = get_database()
    return db.get_portfolio_by_name(*args, **kwargs)


def get_all_portfolios(*args, **kwargs):
    """Get all portfolios using the configured database adapter."""
    db = get_database()
    return db.get_all_portfolios(*args, **kwargs)


def get_transactions_by_portfolio(*args, **kwargs):
    """Get transactions by portfolio using the configured database adapter."""
    db = get_database()
    return db.get_transactions_by_portfolio(*args, **kwargs)


def create_entity(*args, **kwargs):
    """Create entity using the configured database adapter."""
    db = get_database()
    return db.create_entity(*args, **kwargs)


def get_entity(*args, **kwargs):
    """Get entity using the configured database adapter."""
    db = get_database()
    return db.get_entity(*args, **kwargs)


def get_entity_by_name(*args, **kwargs):
    """Get entity by name using the configured database adapter."""
    db = get_database()
    return db.get_entity_by_name(*args, **kwargs)


def update_asset(*args, **kwargs):
    """Update asset using the configured database adapter."""
    db = get_database()
    return db.update_asset(*args, **kwargs)


def update_transaction(*args, **kwargs):
    """Update transaction using the configured database adapter."""
    db = get_database()
    return db.update_transaction(*args, **kwargs)


def update_portfolio(*args, **kwargs):
    """Update portfolio using the configured database adapter."""
    db = get_database()
    return db.update_portfolio(*args, **kwargs)


def update_entity(*args, **kwargs):
    """Update entity using the configured database adapter."""
    db = get_database()
    return db.update_entity(*args, **kwargs)


def create_price(*args, **kwargs):
    """Create price using the configured database adapter."""
    db = get_database()
    return db.create_price(*args, **kwargs)


def get_config(*args, **kwargs):
    """Get configuration using the configured database adapter."""
    db = get_database()
    return db.get_config(*args, **kwargs)


def set_config(*args, **kwargs):
    """Set configuration using the configured database adapter."""
    db = get_database()
    return db.set_config(*args, **kwargs)


def get_portfolio_summary(*args, **kwargs):
    """Get portfolio summary using the configured database adapter."""
    db = get_database()
    return db.get_portfolio_summary(*args, **kwargs)


def insert_price_record(*args, **kwargs):
    """Insert price record using symbol using the configured database adapter."""
    db = get_database()
    return db.insert_price_record(*args, **kwargs)
