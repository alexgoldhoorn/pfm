#
# Unit and integration tests for the `update-prices` CLI command.
#
# These tests verify:
#   - Correct parsing of CLI arguments.
#   - Mocked yfinance responses for successful and failing API calls.
#   - Graceful handling of invalid symbols.
#   - Accurate creation of database records with correct data.
#
import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock, ANY
from io import StringIO
from datetime import datetime
from decimal import Decimal

from portf_manager.cli import PortfolioManagerCLI
from portf_manager.config import PortfolioConfig
from portf_manager.api_client import APIClient, DataNotFoundError


class TestUpdatePricesCLI:
    """Test suite for the update-prices CLI command."""

    def setup_method(self):
        """Set up the test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        # The db_path is now a string, not a Path object
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = PortfolioConfig(db_path=self.db_path)
        self.cli = PortfolioManagerCLI(self.config)

        # Create a test user
        self.user_id = self.cli.db_manager.create_user(
            username="testuser",
            email="test@example.com",
            password_hash="testhash",
            salt="testsalt",
            full_name="Test User",
        )

        # Mock authentication
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        # Add a test asset
        self.cli.db_manager.create_asset(
            symbol="AAPL",
            name="Apple Inc.",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="An American multinational technology company.",
        )

    def teardown_method(self):
        """Clean up the test environment after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    @patch("portf_manager.cli.get_client")
    def test_update_prices_success(self, mock_get_client):
        """Test successful price update for a single symbol."""
        # Mock the API client and its fetch_latest_prices method
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices(symbols=["AAPL"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Successfully updated: 1 assets" in output

        # Verify that insert_price_record was called correctly
        self.cli.db_manager.insert_price_record.assert_called_once_with(
            symbol="AAPL",
            price=150.0,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.cli.get_client")
    def test_update_prices_invalid_symbol(self, mock_get_client):
        """Test price update with an invalid symbol."""
        # Mock the API client to return an empty dict for the invalid symbol
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.side_effect = DataNotFoundError(
            "Invalid Ticker"
        )
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices(symbols=["INVALID"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "No valid assets found" in output

        # Verify that insert_price_record was not called
        self.cli.db_manager.insert_price_record.assert_not_called()

    @patch("portf_manager.cli.get_client")
    def test_update_prices_partial_success(self, mock_get_client):
        """Test price update with a mix of valid and invalid symbols."""
        # Add another asset
        self.cli.db_manager.create_asset(
            symbol="GOOG",
            name="Google LLC",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="An American multinational technology company.",
        )

        # Mock the API client to return a price for one symbol but not the other
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices(symbols=["AAPL", "GOOG"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Successfully updated: 1 assets" in output
        assert "Skipped (no data): 1 assets" in output
        assert "GOOG" in output

        # Verify that insert_price_record was called for the valid symbol
        self.cli.db_manager.insert_price_record.assert_called_once_with(
            symbol="AAPL",
            price=150.0,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.cli.get_client")
    def test_update_prices_no_symbols_provided(self, mock_get_client):
        """Test price update when no symbols are provided (updates all assets)."""
        # Add another asset
        self.cli.db_manager.create_asset(
            symbol="GOOG",
            name="Google LLC",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="An American multinational technology company.",
        )

        # Mock the API client to return prices for all assets
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {
            "AAPL": 150.0,
            "GOOG": 2800.0,
        }
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices()

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Successfully updated: 2 assets" in output

        # Verify that insert_price_record was called for both symbols
        assert self.cli.db_manager.insert_price_record.call_count == 2
        self.cli.db_manager.insert_price_record.assert_any_call(
            symbol="AAPL",
            price=150.0,
            fetched_ts=ANY,
            source="yfinance",
        )
        self.cli.db_manager.insert_price_record.assert_any_call(
            symbol="GOOG",
            price=2800.0,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.cli.get_client")
    def test_update_prices_db_record_creation(self, mock_get_client):
        """
        Verify correct DB records creation with accurate created_at, price_date, price_type, source.
        """
        # Mock the API client to return a successful response
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 175.0}
        mock_get_client.return_value = mock_api_client

        with patch("sys.stdout", new_callable=StringIO):
            self.cli.update_prices(symbols=["AAPL"])

        # Check the database directly to verify the created record
        db = self.cli.db_manager
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT asset_id, price, source, price_date, created_at, price_type FROM prices WHERE asset_id = (SELECT id FROM assets WHERE symbol = 'AAPL')"
            )
            record = cursor.fetchone()

        assert record is not None, "Price record was not created in the database"
        # Fetches the results as a dictionary
        record_dict = dict(record)
        assert record_dict["price"] == 175.0
        assert record_dict["source"] == "yfinance"
        assert record_dict["price_date"] == datetime.now().strftime("%Y-%m-%d")
        assert record_dict["price_type"] == "close"

        # Verify created_at is a valid timestamp and is recent
        created_at_datetime = datetime.fromisoformat(record_dict["created_at"])
        assert (datetime.now() - created_at_datetime).total_seconds() < 5

    @patch("portf_manager.cli.get_client")
    def test_update_prices_db_error(self, mock_get_client):
        """Test graceful handling of a database error during price insertion."""
        # Mock the API client to return a successful response
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record to raise an exception
        self.cli.db_manager.insert_price_record = MagicMock(
            side_effect=Exception("DB error")
        )

        # A DB write failure is a real error: update_prices exits non-zero so
        # the cron wrapper alerts instead of silently reporting success.
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                self.cli.update_prices(symbols=["AAPL"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Failed (database errors): 1 assets" in output
        assert "AAPL" in output

        # Verify that insert_price_record was called
        self.cli.db_manager.insert_price_record.assert_called_once()
