"""
Portfolio Manager Package

A comprehensive portfolio management system for tracking assets, transactions, and performance.
"""

__version__ = "1.1.0"
__author__ = "Portfolio Manager Team"

from .models import (
    Asset,
    Transaction,
    Portfolio,
    Entity,
    AssetType,
    TransactionType,
    PriceType,
)
from .database import Database
from .sectors import resolve_sector, list_all_sectors, GICS_SECTOR_MAP
from .transaction_filter import (
    TransactionFilter,
    TransactionFilterService,
    create_transaction_filter_service,
)
from .readline_support import (
    setup_readline,
    enhanced_input,
    print_readline_help,
)

__all__ = [
    "Asset",
    "Transaction",
    "Portfolio",
    "Entity",
    "AssetType",
    "TransactionType",
    "PriceType",
    "Database",
    "resolve_sector",
    "list_all_sectors",
    "GICS_SECTOR_MAP",
    "TransactionFilter",
    "TransactionFilterService",
    "create_transaction_filter_service",
    "setup_readline",
    "enhanced_input",
    "print_readline_help",
]
