"""
Test suite for Portfolio Snapshot functionality
"""

import pytest
import tempfile
import os
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from portf_manager.database import Database
from portf_manager.models import AssetType, TransactionType
from portf_manager.portfolio_snapshot import (
    PortfolioSnapshot,
    PositionSummary,
    PortfolioSummary,
    create_snapshot_from_db_path,
    get_portfolio_context_for_chat,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        db = Database(db_path)

        # Create test data
        # Add test assets
        asset1_id = db.create_asset(
            symbol="AAPL",
            name="Apple Inc.",
            asset_type=AssetType.STOCK.value,
            sector="Technology",
        )
        asset2_id = db.create_asset(
            symbol="GOOGL",
            name="Alphabet Inc.",
            asset_type=AssetType.STOCK.value,
            sector="Technology",
        )

        # Add test transactions
        # AAPL: Buy 10 shares at $150
        db.create_transaction(
            asset_id=asset1_id,
            transaction_type=TransactionType.BUY.value,
            quantity=Decimal("10"),
            price=Decimal("150.00"),
            total_amount=Decimal("1500.00"),
            transaction_date=datetime.now().date().isoformat(),
            description="Test purchase",
        )

        # GOOGL: Buy 5 shares at $2000
        db.create_transaction(
            asset_id=asset2_id,
            transaction_type=TransactionType.BUY.value,
            quantity=Decimal("5"),
            price=Decimal("2000.00"),
            total_amount=Decimal("10000.00"),
            transaction_date=datetime.now().date().isoformat(),
            description="Test purchase",
        )

        # AAPL: Sell 2 shares at $160
        db.create_transaction(
            asset_id=asset1_id,
            transaction_type=TransactionType.SELL.value,
            quantity=Decimal("2"),
            price=Decimal("160.00"),
            total_amount=Decimal("320.00"),
            transaction_date=datetime.now().date().isoformat(),
            description="Test sale",
        )

        yield db_path, db

    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_portfolio_snapshot_creation(temp_db):
    """Test basic portfolio snapshot creation"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    assert snapshot is not None
    assert snapshot.max_positions == 100
    assert snapshot.days_recent == 30


def test_build_current_positions(temp_db):
    """Test building current positions"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    positions = snapshot.build_current_positions()

    # Should have 2 positions
    assert len(positions) == 2

    # Find AAPL position (should have 8 shares after buy 10, sell 2)
    aapl_pos = next(pos for pos in positions if pos.ticker == "AAPL")
    assert aapl_pos.current_shares == Decimal("8")  # 10 - 2
    assert aapl_pos.asset_type == "stock"
    assert aapl_pos.sector == "Technology"

    # Find GOOGL position
    googl_pos = next(pos for pos in positions if pos.ticker == "GOOGL")
    assert googl_pos.current_shares == Decimal("5")
    assert googl_pos.total_invested == Decimal("10000.00")


def test_build_recent_transactions(temp_db):
    """Test building recent transactions"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    transactions = snapshot.build_recent_transactions()

    # Should have 3 transactions
    assert len(transactions) == 3

    # Check transaction structure
    txn = transactions[0]
    assert "date" in txn
    assert "ticker" in txn
    assert "type" in txn
    assert "quantity" in txn
    assert "price" in txn


def test_calculate_cash_balance(temp_db):
    """Test cash balance calculation"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    cash_balance = snapshot.calculate_cash_balance()

    # Should be negative (spent more than received)
    # Bought $1500 + $10000 = $11500, sold $320, so -$11180
    expected = Decimal("320.00") - Decimal("11500.00")  # sell - buy
    assert cash_balance == expected


def test_build_portfolio_summary(temp_db):
    """Test building complete portfolio summary"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    summary = snapshot.build_portfolio_summary()

    assert isinstance(summary, PortfolioSummary)
    assert summary.total_positions == 2
    assert len(summary.positions) == 2
    assert len(summary.recent_transactions) == 3
    assert summary.as_of is not None


def test_build_compact_json(temp_db):
    """Test building compact JSON representation"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    json_data = snapshot.build_compact_json()

    assert "timestamp" in json_data
    assert "summary" in json_data
    assert "positions" in json_data
    assert "recent_activity" in json_data

    # Check summary structure
    summary = json_data["summary"]
    assert "positions_count" in summary
    assert "total_invested" in summary
    assert "cash_balance" in summary

    # Check positions structure
    positions = json_data["positions"]
    assert len(positions) == 2
    assert all("ticker" in pos for pos in positions)
    assert all("shares" in pos for pos in positions)


def test_estimate_token_count(temp_db):
    """Test token count estimation"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    json_data = snapshot.build_compact_json()
    token_count = snapshot.estimate_token_count(json_data)

    assert isinstance(token_count, int)
    assert token_count > 0


def test_build_prompt_context(temp_db):
    """Test building prompt context string"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    context = snapshot.build_prompt_context()

    assert isinstance(context, str)
    assert "Current Portfolio" in context
    assert "Portfolio Summary" in context
    assert "Current Holdings" in context
    assert "AAPL" in context
    assert "GOOGL" in context


def test_token_limit_truncation(temp_db):
    """Test that context is truncated when token limit is exceeded"""
    db_path, db = temp_db
    snapshot = PortfolioSnapshot(db)

    # Test with very low token limit
    context = snapshot.build_prompt_context(max_tokens=50)

    # Should still be valid but truncated
    assert isinstance(context, str)
    assert "Current Portfolio" in context


def test_utility_functions(temp_db):
    """Test utility functions"""
    db_path, db = temp_db

    # Test create_snapshot_from_db_path
    snapshot = create_snapshot_from_db_path(db_path)
    assert isinstance(snapshot, PortfolioSnapshot)

    # Test get_portfolio_context_for_chat
    context = get_portfolio_context_for_chat(db_path)
    assert isinstance(context, str)
    assert "Current Portfolio" in context


def test_error_handling():
    """Test error handling with invalid database path"""
    # Test with non-existent database
    context = get_portfolio_context_for_chat("/non/existent/path.db")
    assert "Error loading portfolio data" in context


if __name__ == "__main__":
    pytest.main([__file__])
