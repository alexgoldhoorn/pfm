"""
Tests for the CLI module.
"""

import pytest
import tempfile
import os
import sys
from unittest.mock import patch, MagicMock
from io import StringIO

from portf_manager.cli import (
    PortfolioManagerCLI,
    execute_command,
    AuthenticationRequiredError,
)
from portf_manager.database import Database
from portf_manager.models import AssetType, TransactionType
from portf_manager.auth import AuthManager


from portf_manager.config import PortfolioConfig


class TestPortfolioManagerCLI:
    """Test suite for PortfolioManagerCLI class."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = PortfolioConfig(db_path=self.db_path)
        self.cli = PortfolioManagerCLI(self.config)

        # Create default user for entity operations
        self.user_id = self.cli.db_manager.create_user(
            username="admin",
            email="admin@localhost",
            password_hash="dummy_hash",
            salt="dummy_salt",
            full_name="Default Admin User",
        )

        # Mock authentication to always return True for these tests
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_cli_initialization(self):
        """Test CLI initialization."""
        assert self.cli.db_manager is not None
        assert self.cli.db_manager.db_path.name == "test.db"

    def test_add_asset(self):
        """Test adding an asset."""
        # Capture output
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset(
                "AAPL", "Apple Inc.", "stock", "NASDAQ", "USD", "Test asset"
            )
            output = mock_stdout.getvalue()

        # Verify asset was added
        assert "Asset added successfully" in output
        assert "AAPL" in output

        # Verify asset exists in database
        asset = self.cli.db_manager.get_asset_by_symbol("AAPL")
        assert asset is not None
        assert asset["name"] == "Apple Inc."
        assert asset["asset_type"] == "stock"
        assert asset["exchange"] == "NASDAQ"

    def test_add_asset_invalid_type(self):
        """Test adding asset with invalid type."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset("AAPL", "Apple Inc.", "invalid_type")
            output = mock_stdout.getvalue()

        assert "Error" in output
        assert "Valid asset types" in output

    def test_remove_asset(self):
        """Test removing an asset."""
        # First add an asset
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")

        # Remove the asset
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.remove_asset("AAPL")
            output = mock_stdout.getvalue()

        assert "removed successfully" in output

        # Verify asset is inactive
        asset = self.cli.db_manager.get_asset_by_symbol("AAPL")
        assert asset["is_active"] == 0  # SQLite stores boolean as integer

    def test_remove_nonexistent_asset(self):
        """Test removing non-existent asset."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.remove_asset("NONEXISTENT")
            output = mock_stdout.getvalue()

        assert "not found" in output

    def test_list_assets_empty(self):
        """Test listing assets when none exist."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_assets()
            output = mock_stdout.getvalue()

        assert "No active assets found" in output

    def test_list_assets_with_data(self):
        """Test listing assets with data."""
        # Add test assets
        self.cli.add_asset("AAPL", "Apple Inc.", "stock", "NASDAQ")
        self.cli.add_asset("GOOGL", "Google", "stock", "NASDAQ")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_assets()
            output = mock_stdout.getvalue()

        assert "AAPL" in output
        assert "GOOGL" in output
        assert "Apple Inc." in output
        assert "Google" in output
        assert "Total: 2 assets" in output

    def test_add_asset_transaction(self):
        """Test adding an asset transaction."""
        # First add an asset
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")

        # Add transaction
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset_transaction(
                "AAPL", 100, 150.0, "USD", "buy", "2024-01-15"
            )
            output = mock_stdout.getvalue()

        assert "Transaction added successfully" in output
        assert "AAPL" in output
        assert "100" in output
        assert "150.0" in output

    def test_add_transaction_nonexistent_asset(self):
        """Test adding transaction for non-existent asset."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset_transaction(
                "NONEXISTENT", 100, 150.0, "USD", "buy", "2024-01-15"
            )
            output = mock_stdout.getvalue()

        assert "not found" in output

    def test_add_transaction_invalid_type(self):
        """Test adding transaction with invalid type."""
        # First add an asset
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset_transaction(
                "AAPL", 100, 150.0, "USD", "invalid_type", "2024-01-15"
            )
            output = mock_stdout.getvalue()

        assert "Error" in output

    def test_list_transactions_empty(self):
        """Test listing transactions when none exist."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_transactions()
            output = mock_stdout.getvalue()

        assert "No transactions found" in output

    def test_list_transactions_with_data(self):
        """Test listing transactions with data."""
        # Add asset and transactions
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")
        self.cli.add_asset_transaction("AAPL", 100, 150.0, "USD", "buy", "2024-01-15")
        self.cli.add_asset_transaction("AAPL", 50, 160.0, "USD", "sell", "2024-01-16")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_transactions()
            output = mock_stdout.getvalue()

        assert "AAPL" in output
        assert "buy" in output
        assert "sell" in output
        assert "Currency" in output
        assert "100.0000" in output
        assert "50.0000" in output

    def test_list_sectors(self):
        """Test listing sectors."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_sectors()
            output = mock_stdout.getvalue()

        assert "Available GICS Sectors" in output
        assert "Technology" in output or "Information Technology" in output

    def test_show_sector_mapping(self):
        """Test showing sector mapping."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_sector_mapping()
            output = mock_stdout.getvalue()

        assert "GICS Sector Mapping" in output
        assert "Ticker" in output
        assert "Sector" in output

    def test_show_portfolio_value_no_transactions(self):
        """Test showing portfolio value with no transactions."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_portfolio_value()
            output = mock_stdout.getvalue()

        assert "No transactions found" in output

    def test_show_portfolio_value_with_data(self):
        """Test showing portfolio value with data."""
        # Add asset and transaction
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")
        self.cli.add_asset_transaction("AAPL", 100, 150.0, "USD", "buy", "2024-01-15")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_portfolio_value()
            output = mock_stdout.getvalue()

        # Should show grouped portfolio output with positions
        assert "Subtotal:" in output
        assert "Currency" in output
        assert "Apple" in output

    def test_add_entity(self):
        """Test adding an entity."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_entity(
                "Test Broker", "broker", "https://test.com", "Test description"
            )
            output = mock_stdout.getvalue()

        assert "Entity added successfully" in output
        assert "Test Broker" in output
        assert "broker" in output

        # Verify entity exists in database
        entity = self.cli.db_manager.get_entity_by_name("Test Broker")
        assert entity is not None
        assert entity["entity_type"] == "broker"

    def test_add_entity_invalid_type(self):
        """Test adding entity with invalid type."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_entity("Test Entity", "invalid_type")
            output = mock_stdout.getvalue()

        assert "Invalid entity type" in output

    def test_list_entities_empty(self):
        """Test listing entities when none exist."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_entities()
            output = mock_stdout.getvalue()

        assert "No entities found" in output

    def test_list_entities_with_data(self):
        """Test listing entities with data."""
        # Add test entities
        self.cli.add_entity("Broker 1", "broker")
        self.cli.add_entity("Bank 1", "bank")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_entities()
            output = mock_stdout.getvalue()

        assert "Broker 1" in output
        assert "Bank 1" in output
        assert "Total: 2 entities" in output

    def test_add_portfolio(self):
        """Test adding a portfolio."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_portfolio("Test Portfolio", "USD", None, "Test description")
            output = mock_stdout.getvalue()

        assert "Portfolio added successfully" in output
        assert "Test Portfolio" in output
        assert "USD" in output

        # Verify portfolio exists in database
        portfolio = self.cli.db_manager.get_portfolio_by_name("Test Portfolio")
        assert portfolio is not None
        assert portfolio["base_currency"] == "USD"

    def test_add_portfolio_with_entity(self):
        """Test adding portfolio with entity."""
        # First add an entity
        self.cli.add_entity("Test Broker", "broker")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_portfolio(
                "Test Portfolio", "USD", "Test Broker", "Test description"
            )
            output = mock_stdout.getvalue()

        assert "Portfolio added successfully" in output
        assert "Test Broker" in output

    def test_add_portfolio_nonexistent_entity(self):
        """Test adding portfolio with non-existent entity."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_portfolio("Test Portfolio", "USD", "Nonexistent Entity")
            output = mock_stdout.getvalue()

        assert "not found" in output

    def test_list_portfolios_empty(self):
        """Test listing portfolios when none exist."""
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_portfolios()
            output = mock_stdout.getvalue()

        assert "No portfolios found" in output

    def test_list_portfolios_with_data(self):
        """Test listing portfolios with data."""
        # Add test portfolios
        self.cli.add_portfolio("Portfolio 1", "USD")
        self.cli.add_portfolio("Portfolio 2", "EUR")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_portfolios()
            output = mock_stdout.getvalue()

        assert "Portfolio 1" in output
        assert "Portfolio 2" in output
        assert "Total: 2 portfolios" in output

        assert "Currency" in output


# class TestCLIParser:
#    """Test suite for CLI parser functionality."""
#
#    def test_create_parser(self):
#        """Test creating argument parser."""
#        parser = create_parser()
#        assert parser is not None
#        assert parser.description is not None
#
#    def test_parse_add_asset(self):
#        """Test parsing add-asset command."""
#        parser = create_parser()
#        args = parser.parse_args(
#            ["add-asset", "AAPL", "Apple Inc.", "stock", "--exchange", "NASDAQ"]
#        )
#
#        assert args.command == "add-asset"
#        assert args.symbol == "AAPL"
#        assert args.name == "Apple Inc."
#        assert args.asset_type == "stock"
#        assert args.exchange == "NASDAQ"
#
#    def test_parse_add_transaction(self):
#        """Test parsing add-transaction command."""
#        parser = create_parser()
#        args = parser.parse_args(
#            [
#                "add-transaction",
#                "--symbol",
#                "AAPL",
#                "--amount",
#                "100",
#                "--price",
#                "150.0",
#                "--currency",
#                "USD",
#                "--type",
#                "buy",
#                "--date",
#                "2024-01-15",
#            ]
#        )
#
#        assert args.command == "add-transaction"
#        assert args.symbol == "AAPL"
#        assert args.amount == 100.0
#        assert args.price == 150.0
#        assert args.currency == "USD"
#        assert args.type == "buy"
#        assert args.date == "2024-01-15"
#
#    def test_parse_list_assets(self):
#        """Test parsing list-assets command."""
#        parser = create_parser()
#        args = parser.parse_args(["list-assets"])
#
#        assert args.command == "list-assets"
#        assert args.all is False
#
#        # Test with --all flag
#        args = parser.parse_args(["list-assets", "--all"])
#        assert args.all is True
#
#    def test_parse_list_transactions(self):
#        """Test parsing list-transactions command."""
#        parser = create_parser()
#        args = parser.parse_args(["list-transactions"])
#
#        assert args.command == "list-transactions"
#        assert args.limit == 10
#
#        # Test with options
#        args = parser.parse_args(
#            ["list-transactions", "--symbol", "AAPL", "--limit", "5"]
#        )
#        assert args.symbol == "AAPL"
#        assert args.limit == 5
#
#    def test_parse_add_entity(self):
#        """Test parsing add-entity command."""
#        parser = create_parser()
#        args = parser.parse_args(
#            ["add-entity", "Test Broker", "broker", "--website", "https://test.com"]
#        )
#
#        assert args.command == "add-entity"
#        assert args.name == "Test Broker"
#        assert args.entity_type == "broker"
#        assert args.website == "https://test.com"
#
#    def test_parse_add_portfolio(self):
#        """Test parsing add-portfolio command."""
#        parser = create_parser()
#        args = parser.parse_args(
#            ["add-portfolio", "Test Portfolio", "--currency", "EUR"]
#        )
#
#        assert args.command == "add-portfolio"
#        assert args.name == "Test Portfolio"
#        assert args.currency == "EUR"
#
#
##class TestCLIExecution:
#    """Test suite for CLI command execution."""
#
#    def setup_method(self):
#        """Setup test environment before each test."""
#        self.temp_dir = tempfile.mkdtemp()
#        self.db_path = os.path.join(self.temp_dir, "test.db")
#        self.config = PortfolioConfig(db_path=self.db_path)
#        self.cli = PortfolioManagerCLI(self.config)
#
#        # Create test user
#        self.user_id = self.cli.db_manager.create_user(
#            username="testuser",
#            email="test@example.com",
#            password_hash="dummy_hash",
#            salt="dummy_salt",
#            full_name="Test User",
#        )
#
#        # Mock authentication to always return True for these tests
#        mock_auth_manager = MagicMock()
#        mock_auth_manager.is_authenticated.return_value = True
#        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
#        self.cli.auth_manager = mock_auth_manager
#
#    def teardown_method(self):
#        """Cleanup after each test."""
#        if os.path.exists(self.db_path):
#            os.remove(self.db_path)
#        os.rmdir(self.temp_dir)
#
#    def test_execute_add_asset_command(self):
#        """Test executing add-asset command."""
#        parser = create_parser()
#        args = parser.parse_args(["add-asset", "AAPL", "Apple Inc.", "stock"])
#
#        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
#            execute_command(self.cli, args)
#            output = mock_stdout.getvalue()
#
#        assert "Asset added successfully" in output
#
#        # Verify asset exists
#        asset = self.cli.db_manager.get_asset_by_symbol("AAPL")
#        assert asset is not None
#
#    def test_execute_list_assets_command(self):
#        """Test executing list-assets command."""
#        # Add test asset
#        self.cli.add_asset("AAPL", "Apple Inc.", "stock")
#
#        parser = create_parser()
#        args = parser.parse_args(["list-assets"])
#
#        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
#            execute_command(self.cli, args)
#            output = mock_stdout.getvalue()
#
#        assert "AAPL" in output
#
#    def test_execute_add_transaction_command(self):
#        """Test executing add-transaction command."""
#        # Add test asset first
#        self.cli.add_asset("AAPL", "Apple Inc.", "stock")
#
#        parser = create_parser()
#        args = parser.parse_args(
#            [
#                "add-transaction",
#                "--symbol",
#                "AAPL",
#                "--amount",
#                "100",
#                "--price",
#                "150.0",
#                "--currency",
#                "USD",
#                "--type",
#                "buy",
#                "--date",
#                "2024-01-15",
#            ]
#        )
#
#        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
#            execute_command(self.cli, args)
#            output = mock_stdout.getvalue()
#
#        assert "Transaction added successfully" in output
#
#    def test_execute_unknown_command(self):
#        """Test executing unknown command."""
#
#        # Mock args with unknown command
#        class MockArgs:
#            command = "unknown-command"
#
#        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
#            with pytest.raises(SystemExit):
#                execute_command(self.cli, MockArgs())
#            output = mock_stdout.getvalue()
#
#        assert "Unknown command" in output
#
#
class TestCLIAuthentication:
    """Test suite for CLI authentication behavior."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = PortfolioConfig(db_path=self.db_path)
        self.cli = PortfolioManagerCLI(self.config)

        # Create test user
        self.user_id = self.cli.db_manager.create_user(
            username="testuser",
            email="test@example.com",
            password_hash="dummy_hash",
            salt="dummy_salt",
            full_name="Test User",
        )

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_list_assets_not_authenticated(self):
        """Test list-assets command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        # Should raise AuthenticationRequiredError
        with pytest.raises(AuthenticationRequiredError):
            self.cli.list_assets()

    def test_list_assets_authenticated(self):
        """Test list-assets command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_assets()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows asset list)
        assert "❌ Please login first." not in output
        assert "📋 No active assets found." in output  # Expected when no assets exist

    def test_list_sectors_not_authenticated(self):
        """Test list-sectors command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_sectors()
            output = mock_stdout.getvalue()

        # Verify error message is printed and no sectors are listed
        assert "❌ Please login first." in output
        assert "Available GICS Sectors" not in output
        assert "Total:" not in output

    def test_list_sectors_authenticated(self):
        """Test list-sectors command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_sectors()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows sector list)
        assert "❌ Please login first." not in output
        assert "📊 Available GICS Sectors:" in output
        assert "Total:" in output

    def test_show_sector_mapping_not_authenticated(self):
        """Test show-sector-mapping command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_sector_mapping()
            output = mock_stdout.getvalue()

        # Verify error message is printed and no mapping is shown
        assert "❌ Please login first." in output
        assert "GICS Sector Mapping" not in output
        assert "Total:" not in output

    def test_show_sector_mapping_authenticated(self):
        """Test show-sector-mapping command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_sector_mapping()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows mapping)
        assert "❌ Please login first." not in output
        assert "🗺️  GICS Sector Mapping:" in output
        assert "Total:" in output

    def test_add_asset_not_authenticated(self):
        """Test add-asset command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset("AAPL", "Apple Inc.", "stock")
            output = mock_stdout.getvalue()

        # Verify error message is printed and asset is not added
        assert "❌ Please login first." in output
        assert "Asset added successfully" not in output

    def test_add_asset_authenticated(self):
        """Test add-asset command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset("AAPL", "Apple Inc.", "stock")
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, asset is added)
        assert "❌ Please login first." not in output
        assert "✅ Asset added successfully!" in output

    def test_remove_asset_not_authenticated(self):
        """Test remove-asset command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.remove_asset("AAPL")
            output = mock_stdout.getvalue()

        # Verify error message is printed and asset is not removed
        assert "❌ Please login first." in output
        assert "removed successfully" not in output

    def test_remove_asset_authenticated(self):
        """Test remove-asset command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        # First add an asset (while authenticated)
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.remove_asset("AAPL")
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, asset is removed)
        assert "❌ Please login first." not in output
        assert "✅ Asset 'AAPL' removed successfully!" in output

    def test_add_asset_transaction_not_authenticated(self):
        """Test add-asset-transaction command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset_transaction(
                "AAPL", 100, 150.0, "USD", "buy", "2024-01-15"
            )
            output = mock_stdout.getvalue()

        # Verify asset not found message is printed
        assert (
            "❌ Asset with symbol 'AAPL' not found, please add the asset first."
            in output
        )

    def test_add_asset_transaction_with_asset_not_authenticated(self):
        """Test add-asset-transaction command when asset exists but not authenticated."""
        # First create an asset without authentication requirement
        # by temporarily setting authenticated to True to create asset
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")

        # Now set authentication to False for the transaction test
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset_transaction(
                "AAPL", 100, 150.0, "USD", "buy", "2024-01-15"
            )
            output = mock_stdout.getvalue()

        # Verify authentication error message is printed
        assert "❌ Please login first." in output
        assert "Transaction added successfully" not in output

    def test_add_asset_transaction_authenticated(self):
        """Test add-asset-transaction command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        # First add an asset (while authenticated)
        self.cli.add_asset("AAPL", "Apple Inc.", "stock")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_asset_transaction(
                "AAPL", 100, 150.0, "USD", "buy", "2024-01-15"
            )
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, transaction is added)
        assert "❌ Please login first." not in output
        assert "✅ Transaction added successfully!" in output

    def test_show_portfolio_value_not_authenticated(self):
        """Test show-portfolio-value command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_portfolio_value()
            output = mock_stdout.getvalue()

        # Verify error message is printed and portfolio value is not shown
        assert "❌ Please login first." in output
        assert "Portfolio Overview" not in output

    def test_show_portfolio_value_authenticated(self):
        """Test show-portfolio-value command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.show_portfolio_value()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows expected message)
        assert "❌ Please login first." not in output
        assert (
            "📊 No transactions found" in output
        )  # Expected when no transactions exist

    def test_list_transactions_not_authenticated(self):
        """Test list-transactions command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_transactions()
            output = mock_stdout.getvalue()

        # Verify error message is printed and transactions are not listed
        assert "❌ Please login first." in output
        assert "Recent Transactions" not in output

    def test_list_transactions_authenticated(self):
        """Test list-transactions command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_transactions()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows expected message)
        assert "❌ Please login first." not in output
        assert (
            "📋 No transactions found" in output
        )  # Expected when no transactions exist

    def test_import_csv_not_authenticated(self):
        """Test import-csv command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.import_csv("fake_file.csv")
            output = mock_stdout.getvalue()

        # Verify error message is printed and import is not executed
        assert "❌ Please login first." in output
        assert "Importing transactions from" not in output

    def test_import_csv_authenticated(self):
        """Test import-csv command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.import_csv("fake_file.csv")
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no auth error, but file not found error)
        assert "❌ Please login first." not in output
        assert "❌ CSV file 'fake_file.csv' not found." in output

    def test_add_entity_not_authenticated(self):
        """Test add-entity command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_entity("Test Broker", "broker")
            output = mock_stdout.getvalue()

        # Verify error message is printed and entity is not added
        assert "❌ Please login first." in output
        assert "Entity added successfully" not in output

    def test_add_entity_authenticated(self):
        """Test add-entity command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_entity("Test Broker", "broker")
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, entity is added)
        assert "❌ Please login first." not in output
        assert "✅ Entity added successfully!" in output

    def test_list_entities_not_authenticated(self):
        """Test list-entities command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_entities()
            output = mock_stdout.getvalue()

        # Verify error message is printed and entities are not listed
        assert "❌ Please login first." in output
        assert "Entities" not in output

    def test_list_entities_authenticated(self):
        """Test list-entities command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_entities()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows entities list)
        assert "❌ Please login first." not in output
        assert "📋 No entities found." in output  # Expected when no entities exist

    def test_add_portfolio_not_authenticated(self):
        """Test add-portfolio command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_portfolio("Test Portfolio")
            output = mock_stdout.getvalue()

        # Verify error message is printed and portfolio is not added
        assert "❌ Please login first." in output
        assert "Portfolio added successfully" not in output

    def test_add_portfolio_authenticated(self):
        """Test add-portfolio command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.add_portfolio("Test Portfolio")
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, portfolio is added)
        assert "❌ Please login first." not in output
        assert "✅ Portfolio added successfully!" in output

    def test_list_portfolios_not_authenticated(self):
        """Test list-portfolios command when not authenticated."""
        # Create CLI with stubbed auth manager that returns False for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = False
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_portfolios()
            output = mock_stdout.getvalue()

        # Verify error message is printed and portfolios are not listed
        assert "❌ Please login first." in output
        assert "Portfolios" not in output

    def test_list_portfolios_authenticated(self):
        """Test list-portfolios command when authenticated."""
        # Create CLI with stubbed auth manager that returns True for is_authenticated
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.list_portfolios()
            output = mock_stdout.getvalue()

        # Verify normal output path executes (no error, shows portfolios list)
        assert "❌ Please login first." not in output
        assert "📋 No portfolios found." in output  # Expected when no portfolios exist
