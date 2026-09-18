"""
Database factory for Portfolio Management.

Selects between SQLite and PostgreSQL based on DATABASE_URL and provides
a process-wide singleton via get_database().
"""

import logging
import os
from typing import Optional, Union

from .database import Database as SQLiteDatabase
from .database_pg import PostgreSQLDatabase

logger = logging.getLogger(__name__)


def _sqlite_path_from_url(database_url: str) -> str:
    """Extract a sqlite DB path from a sqlite URL."""
    return database_url.replace("sqlite:///", "").replace("sqlite://", "")


def get_database_adapter(
    database_url: Optional[str] = None,
) -> Union[SQLiteDatabase, PostgreSQLDatabase]:
    """Return the database adapter for the configured URL/path.

    Resolution order when ``database_url`` is omitted:
    1) ``PORTF_DATABASE_URL``
    2) ``DATABASE_URL``
    3) ``SQLITE_DB_PATH`` (path only, defaults to ``portfolio.db``)
    """
    if database_url is None:
        database_url = os.getenv("PORTF_DATABASE_URL") or os.getenv("DATABASE_URL")

    if database_url:
        if database_url.startswith("sqlite://"):
            logger.info("Using SQLite database adapter from URL")
            return SQLiteDatabase(_sqlite_path_from_url(database_url))
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
