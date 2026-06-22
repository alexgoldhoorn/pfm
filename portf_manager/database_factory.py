"""
Database factory for Portfolio Management.

Selects between SQLite and PostgreSQL based on DATABASE_URL and provides
a process-wide singleton via get_database().
"""

import os
import logging
from typing import Union

from .database import Database as SQLiteDatabase
from .database_pg import PostgreSQLDatabase

logger = logging.getLogger(__name__)


def get_database_adapter() -> Union[SQLiteDatabase, PostgreSQLDatabase]:
    """Return the appropriate database adapter based on environment configuration."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgresql://") or database_url.startswith(
            "postgres://"
        ):
            logger.info("Using PostgreSQL database adapter")
            return PostgreSQLDatabase(database_url)
        raise ValueError(f"Unsupported database URL format: {database_url}")
    logger.info("Using SQLite database adapter")
    db_path = os.getenv("SQLITE_DB_PATH", "portfolio.db")
    return SQLiteDatabase(db_path)


_database_instance = None


def get_database() -> Union[SQLiteDatabase, PostgreSQLDatabase]:
    """Return the process-wide singleton database instance."""
    global _database_instance
    if _database_instance is None:
        _database_instance = get_database_adapter()
    return _database_instance


def reset_database_instance() -> None:
    """Reset the singleton (useful for testing)."""
    global _database_instance
    _database_instance = None
