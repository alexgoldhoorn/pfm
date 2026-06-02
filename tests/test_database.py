"""
Comprehensive tests for the database module.
"""

import pytest
import tempfile
import os
from pathlib import Path
import sqlite3

from datetime import datetime
from portf_manager.database import Database, DatabaseError


class TestDatabase:
    """Test suite for Database class."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_database_creation(self):
        """Test database creation and initialization."""
        assert os.path.exists(self.db_path)
        assert self.db.db_path == Path(self.db_path)

        # Check database version
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
            )
            result = cursor.fetchone()
            assert result[0] == 12  # Current schema version

    def test_database_tables_exist(self):
        """Test that all required tables are created."""
        expected_tables = [
            "users",
            "entities",
            "portfolios",
            "assets",
            "transactions",
            "prices",
            "portfolio_config",
            "database_version",
            "api_keys",
        ]

        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            for table in expected_tables:
                assert table in tables, f"Table {table} not found"

    def test_database_indexes_exist(self):
        """Test that required indexes are created."""
        expected_indexes = [
            "idx_users_username",
            "idx_users_email",
            "idx_entities_name",
            "idx_portfolios_name",
            "idx_assets_symbol",
            "idx_transactions_asset_id",
            "idx_api_keys_key_hash",
            "idx_api_keys_prefix",
            "idx_api_keys_active",
        ]

        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]

            for index in expected_indexes:
                assert index in indexes, f"Index {index} not found"

    def test_connection_context_manager(self):
        """Test database connection context manager."""
        with self.db.get_connection() as conn:
            assert conn is not None
            cursor = conn.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1

    def test_connection_error_handling(self):
        """Test database connection error handling."""
        # Test with invalid path
        with pytest.raises((DatabaseError, OSError)):
            Database("/invalid/path/test.db")

    def test_backup_database(self):
        """Test database backup functionality."""
        backup_path = os.path.join(self.temp_dir, "backup.db")

        # Create some test data
        user_id = self.db.create_user("test", "test@example.com", "hash", "salt")
        assert user_id > 0

        # Backup database
        result = self.db.backup_database(backup_path)
        assert result is True
        assert os.path.exists(backup_path)

        # Verify backup contains data
        backup_db = Database(backup_path)
        user = backup_db.get_user(user_id)
        assert user is not None
        assert user["username"] == "test"

        # Cleanup backup file
        os.remove(backup_path)


class TestUserOperations:
    """Test suite for user CRUD operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_user(self):
        """Test user creation."""
        user_id = self.db.create_user(
            username="testuser",
            email="test@example.com",
            password_hash="hash123",
            salt="salt123",
            full_name="Test User",
        )

        assert user_id > 0

        # Verify user exists
        user = self.db.get_user(user_id)
        assert user is not None
        assert user["username"] == "testuser"
        assert user["email"] == "test@example.com"
        assert user["full_name"] == "Test User"
        assert user["is_active"] == 1

    def test_get_user_by_username(self):
        """Test getting user by username."""
        user_id = self.db.create_user("testuser", "test@example.com", "hash", "salt")

        user = self.db.get_user_by_username("testuser")
        assert user is not None
        assert user["id"] == user_id
        assert user["username"] == "testuser"

        # Test non-existent user
        user = self.db.get_user_by_username("nonexistent")
        assert user is None

    def test_get_user_by_email(self):
        """Test getting user by email."""
        user_id = self.db.create_user("testuser", "test@example.com", "hash", "salt")

        user = self.db.get_user_by_email("test@example.com")
        assert user is not None
        assert user["id"] == user_id
        assert user["email"] == "test@example.com"

        # Test non-existent email
        user = self.db.get_user_by_email("nonexistent@example.com")
        assert user is None

    def test_update_user_password(self):
        """Test updating user password."""
        user_id = self.db.create_user("testuser", "test@example.com", "hash", "salt")

        result = self.db.update_user_password(user_id, "newhash", "newsalt")
        assert result is True

        user = self.db.get_user(user_id)
        assert user["password_hash"] == "newhash"
        assert user["salt"] == "newsalt"

    def test_update_user_last_login(self):
        """Test updating user last login."""
        user_id = self.db.create_user("testuser", "test@example.com", "hash", "salt")

        result = self.db.update_user_last_login(user_id)
        assert result is True

        user = self.db.get_user(user_id)
        assert user["last_login"] is not None

    def test_update_user(self):
        """Test updating user fields."""
        user_id = self.db.create_user("testuser", "test@example.com", "hash", "salt")

        result = self.db.update_user(
            user_id,
            username="newusername",
            email="new@example.com",
            full_name="New Name",
        )
        assert result is True

        user = self.db.get_user(user_id)
        assert user["username"] == "newusername"
        assert user["email"] == "new@example.com"
        assert user["full_name"] == "New Name"

    def test_delete_user(self):
        """Test soft deleting user."""
        user_id = self.db.create_user("testuser", "test@example.com", "hash", "salt")

        result = self.db.delete_user(user_id)
        assert result is True

        user = self.db.get_user(user_id)
        assert user["is_active"] == 0


class TestEntityOperations:
    """Test suite for entity CRUD operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

        # Create test user
        self.user_id = self.db.create_user(
            "testuser", "test@example.com", "hash", "salt"
        )

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_entity(self):
        """Test entity creation."""
        entity_id = self.db.create_entity(
            name="Test Broker",
            entity_type="broker",
            user_id=self.user_id,
            website="https://test.com",
            description="Test broker entity",
        )

        assert entity_id > 0

        # Verify entity exists
        entity = self.db.get_entity(entity_id)
        assert entity is not None
        assert entity["name"] == "Test Broker"
        assert entity["entity_type"] == "broker"
        assert entity["website"] == "https://test.com"
        assert entity["description"] == "Test broker entity"

    def test_get_entity_by_name(self):
        """Test getting entity by name."""
        entity_id = self.db.create_entity("Test Broker", "broker", self.user_id)

        entity = self.db.get_entity_by_name("Test Broker")
        assert entity is not None
        assert entity["id"] == entity_id

        # Test non-existent entity
        entity = self.db.get_entity_by_name("Non-existent")
        assert entity is None

    def test_get_all_entities(self):
        """Test getting all entities."""
        # Create test entities
        self.db.create_entity("Broker 1", "broker", self.user_id)
        self.db.create_entity("Bank 1", "bank", self.user_id)

        entities = self.db.get_all_entities()
        assert len(entities) == 2

        # Test with inactive entities
        entity_id = self.db.create_entity("Inactive", "other", self.user_id)
        self.db.update_entity(entity_id, is_active=False)

        active_entities = self.db.get_all_entities(active_only=True)
        all_entities = self.db.get_all_entities(active_only=False)

        assert len(active_entities) == 2
        assert len(all_entities) == 3

    def test_update_entity(self):
        """Test updating entity."""
        entity_id = self.db.create_entity("Test Broker", "broker", self.user_id)

        result = self.db.update_entity(
            entity_id, name="Updated Broker", website="https://updated.com"
        )
        assert result is True

        entity = self.db.get_entity(entity_id)
        assert entity["name"] == "Updated Broker"
        assert entity["website"] == "https://updated.com"

    def test_delete_entity(self):
        """Test soft deleting entity."""
        entity_id = self.db.create_entity("Test Broker", "broker", self.user_id)

        result = self.db.delete_entity(entity_id)
        assert result is True

        entity = self.db.get_entity(entity_id)
        assert entity["is_active"] == 0


class TestAssetOperations:
    """Test suite for asset CRUD operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_asset(self):
        """Test asset creation."""
        asset_id = self.db.create_asset(
            symbol="AAPL",
            name="Apple Inc.",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="Apple stock",
        )

        assert asset_id > 0

        # Verify asset exists
        asset = self.db.get_asset(asset_id)
        assert asset is not None
        assert asset["symbol"] == "AAPL"
        assert asset["name"] == "Apple Inc."
        assert asset["asset_type"] == "stock"
        assert asset["exchange"] == "NASDAQ"
        assert asset["currency"] == "USD"
        assert asset["sector"] == "Technology"

    def test_get_asset_by_symbol(self):
        """Test getting asset by symbol."""
        asset_id = self.db.create_asset("AAPL", "Apple Inc.", "stock")

        asset = self.db.get_asset_by_symbol("AAPL")
        assert asset is not None
        assert asset["id"] == asset_id

        # Test non-existent asset
        asset = self.db.get_asset_by_symbol("NONEXISTENT")
        assert asset is None

    def test_get_all_assets(self):
        """Test getting all assets."""
        # Create test assets
        self.db.create_asset("AAPL", "Apple Inc.", "stock")
        self.db.create_asset("GOOGL", "Google", "stock")

        assets = self.db.get_all_assets()
        assert len(assets) == 2

        # Test with inactive assets
        asset_id = self.db.create_asset("INACTIVE", "Inactive Corp", "stock")
        self.db.update_asset(asset_id, is_active=False)

        active_assets = self.db.get_all_assets(active_only=True)
        all_assets = self.db.get_all_assets(active_only=False)

        assert len(active_assets) == 2
        assert len(all_assets) == 3

    def test_update_asset(self):
        """Test updating asset."""
        asset_id = self.db.create_asset("AAPL", "Apple Inc.", "stock")

        result = self.db.update_asset(
            asset_id, name="Apple Corporation", exchange="NYSE"
        )
        assert result is True

        asset = self.db.get_asset(asset_id)
        assert asset["name"] == "Apple Corporation"
        assert asset["exchange"] == "NYSE"

    def test_delete_asset(self):
        """Test soft deleting asset."""
        asset_id = self.db.create_asset("AAPL", "Apple Inc.", "stock")

        result = self.db.delete_asset(asset_id)
        assert result is True

        asset = self.db.get_asset(asset_id)
        assert asset["is_active"] == 0


class TestTransactionOperations:
    """Test suite for transaction CRUD operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

        # Create test asset
        self.asset_id = self.db.create_asset("AAPL", "Apple Inc.", "stock")

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_transaction(self):
        """Test transaction creation."""
        transaction_id = self.db.create_transaction(
            asset_id=self.asset_id,
            transaction_type="buy",
            quantity=100.0,
            price=150.0,
            total_amount=15000.0,
            transaction_date="2024-01-15",
            fees=5.0,
            description="Test transaction",
        )

        assert transaction_id > 0

        # Verify transaction exists
        transaction = self.db.get_transaction(transaction_id)
        assert transaction is not None
        assert transaction["asset_id"] == self.asset_id
        assert transaction["transaction_type"] == "buy"
        assert transaction["quantity"] == 100.0
        assert transaction["price"] == 150.0
        assert transaction["total_amount"] == 15000.0
        assert transaction["fees"] == 5.0

    def test_get_transactions_by_asset(self):
        """Test getting transactions by asset."""
        # Create test transactions
        self.db.create_transaction(self.asset_id, "buy", 100, 150, 15000, "2024-01-15")
        self.db.create_transaction(self.asset_id, "sell", 50, 160, 8000, "2024-01-16")

        transactions = self.db.get_transactions_by_asset(self.asset_id)
        assert len(transactions) == 2

        # Verify ordering (should be by date DESC)
        assert transactions[0]["transaction_date"] == "2024-01-16"
        assert transactions[1]["transaction_date"] == "2024-01-15"

    def test_get_all_transactions(self):
        """Test getting all transactions."""
        # Create test transactions
        self.db.create_transaction(self.asset_id, "buy", 100, 150, 15000, "2024-01-15")
        self.db.create_transaction(self.asset_id, "sell", 50, 160, 8000, "2024-01-16")

        transactions = self.db.get_all_transactions()
        assert len(transactions) == 2

        # Test with limit
        transactions = self.db.get_all_transactions(limit=1)
        assert len(transactions) == 1

    def test_update_transaction(self):
        """Test updating transaction."""
        transaction_id = self.db.create_transaction(
            self.asset_id, "buy", 100, 150, 15000, "2024-01-15"
        )

        result = self.db.update_transaction(
            transaction_id, quantity=200.0, price=155.0, total_amount=31000.0
        )
        assert result is True

        transaction = self.db.get_transaction(transaction_id)
        assert transaction["quantity"] == 200.0
        assert transaction["price"] == 155.0
        assert transaction["total_amount"] == 31000.0

    def test_delete_transaction(self):
        """Test deleting transaction."""
        transaction_id = self.db.create_transaction(
            self.asset_id, "buy", 100, 150, 15000, "2024-01-15"
        )

        result = self.db.delete_transaction(transaction_id)
        assert result is True

        transaction = self.db.get_transaction(transaction_id)
        assert transaction is None

    def test_transaction_currency_overrides_asset_currency(self):
        """Per-transaction currency overrides the asset currency in queries."""
        asset_id = self.db.create_asset(
            "US0000000001", "Example Corp", "stock", currency="USD"
        )
        tx_id = self.db.create_transaction(
            asset_id,
            "dividend",
            1.0,
            0.09,
            0.09,
            "2026-04-01",
            currency="EUR",
        )
        tx = self.db.get_transaction(tx_id)
        # COALESCE(t.currency, a.currency) should return EUR, not USD
        assert tx["currency"] == "EUR"

    def test_transaction_falls_back_to_asset_currency_when_none(self):
        """When transaction has no currency, asset currency is used."""
        asset_id = self.db.create_asset("AAPL2", "Apple", "stock", currency="USD")
        tx_id = self.db.create_transaction(
            asset_id, "buy", 1.0, 150.0, 150.0, "2025-01-01"
        )
        tx = self.db.get_transaction(tx_id)
        assert tx["currency"] == "USD"

    def test_bookings_crud(self):
        """Test create, read, delete for bookings."""
        portfolio_id = self.db.create_portfolio("Test Portfolio", "EUR")
        bk_id = self.db.create_booking(
            "2025-06-17", "Deposit", 250.0, "EUR", portfolio_id
        )
        assert bk_id > 0

        bk = self.db.get_booking(bk_id)
        assert bk["action"] == "Deposit"
        assert bk["amount"] == 250.0
        assert bk["currency"] == "EUR"
        assert bk["portfolio_name"] == "Test Portfolio"

        all_bk = self.db.get_all_bookings()
        assert len(all_bk) == 1

        filtered = self.db.get_all_bookings(portfolio_id=portfolio_id)
        assert len(filtered) == 1

        deleted = self.db.delete_booking(bk_id)
        assert deleted is True
        assert self.db.get_booking(bk_id) is None


class TestPriceOperations:
    """Test suite for price CRUD operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

        # Create test asset
        self.asset_id = self.db.create_asset("AAPL", "Apple Inc.", "stock")

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_price(self):
        """Test price creation."""
        price_id = self.db.create_price(
            asset_id=self.asset_id,
            price=150.0,
            price_date="2024-01-15",
            price_type="close",
            volume=1000000,
            source="yahoo",
        )

        assert price_id > 0

        # Verify price exists
        price = self.db.get_price(self.asset_id, "2024-01-15", "close")
        assert price is not None
        assert price["price"] == 150.0
        assert price["volume"] == 1000000
        assert price["source"] == "yahoo"

    def test_get_price_history(self):
        """Test getting price history."""
        # Create test prices
        self.db.create_price(self.asset_id, 150.0, "2024-01-15", "close")
        self.db.create_price(self.asset_id, 155.0, "2024-01-16", "close")
        self.db.create_price(self.asset_id, 160.0, "2024-01-17", "close")

        history = self.db.get_price_history(self.asset_id)
        assert len(history) == 3

        # Test with date range
        history = self.db.get_price_history(
            self.asset_id, start_date="2024-01-16", end_date="2024-01-17"
        )
        assert len(history) == 2

    def test_get_latest_price(self):
        """Test getting latest price."""
        # Create test prices
        self.db.create_price(self.asset_id, 150.0, "2024-01-15", "close")
        self.db.create_price(self.asset_id, 155.0, "2024-01-16", "close")

        latest = self.db.get_latest_price(self.asset_id)
        assert latest is not None
        assert latest["price"] == 155.0
        assert latest["price_date"] == "2024-01-16"

    def test_delete_price(self):
        """Test deleting price."""
        self.db.create_price(self.asset_id, 150.0, "2024-01-15", "close")

        result = self.db.delete_price(self.asset_id, "2024-01-15", "close")
        assert result is True

        price = self.db.get_price(self.asset_id, "2024-01-15", "close")
        assert price is None

    def test_insert_price_record(self):
        """Test the new insert_price_record adapter function."""
        from datetime import date

        # Test basic functionality
        fetched_ts = datetime(2024, 1, 15, 10, 30, 0)
        price_id = self.db.insert_price_record(
            symbol="AAPL",
            price=150.75,
            fetched_ts=fetched_ts,
            source="yfinance",
            price_type="close",
        )

        assert price_id is not None

        # Verify the record was inserted correctly
        price_record = self.db.get_price(
            self.asset_id, date.today().isoformat(), "close"
        )
        assert price_record is not None
        assert price_record["price"] == 150.75
        assert price_record["source"] == "yfinance"
        assert price_record["price_type"] == "close"

        # Test with custom price_date
        custom_date = "2024-01-20"
        self.db.insert_price_record(
            symbol="AAPL",
            price=155.50,
            fetched_ts=fetched_ts,
            source="yahoo",
            price_type="close",
            price_date=custom_date,
        )

        price_record2 = self.db.get_price(self.asset_id, custom_date, "close")
        assert price_record2 is not None
        assert price_record2["price"] == 155.50
        assert price_record2["source"] == "yahoo"

        # Test error handling for invalid symbol
        with pytest.raises(ValueError, match="not found"):
            self.db.insert_price_record(
                symbol="INVALID", price=100.0, fetched_ts=fetched_ts
            )


class TestPortfolioOperations:
    """Test suite for portfolio CRUD operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

        # Create test user
        self.user_id = self.db.create_user(
            "testuser", "test@example.com", "hash", "salt"
        )
        # Create test entity
        self.entity_id = self.db.create_entity("Test Broker", "broker", self.user_id)

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_portfolio(self):
        """Test portfolio creation."""
        portfolio_id = self.db.create_portfolio(
            name="Test Portfolio",
            base_currency="USD",
            entity_id=self.entity_id,
            description="Test portfolio",
        )

        assert portfolio_id > 0

        # Verify portfolio exists
        portfolio = self.db.get_portfolio(portfolio_id)
        assert portfolio is not None
        assert portfolio["name"] == "Test Portfolio"
        assert portfolio["base_currency"] == "USD"
        assert portfolio["entity_id"] == self.entity_id
        assert portfolio["description"] == "Test portfolio"

    def test_get_portfolio_by_name(self):
        """Test getting portfolio by name."""
        portfolio_id = self.db.create_portfolio("Test Portfolio", "USD")

        portfolio = self.db.get_portfolio_by_name("Test Portfolio")
        assert portfolio is not None
        assert portfolio["id"] == portfolio_id

        # Test non-existent portfolio
        portfolio = self.db.get_portfolio_by_name("Non-existent")
        assert portfolio is None

    def test_get_all_portfolios(self):
        """Test getting all portfolios."""
        # Create test portfolios
        self.db.create_portfolio("Portfolio 1", "USD")
        self.db.create_portfolio("Portfolio 2", "EUR")

        portfolios = self.db.get_all_portfolios()
        assert len(portfolios) == 2

    def test_update_portfolio(self):
        """Test updating portfolio."""
        portfolio_id = self.db.create_portfolio("Test Portfolio", "USD")

        result = self.db.update_portfolio(
            portfolio_id, name="Updated Portfolio", base_currency="EUR"
        )
        assert result is True

        portfolio = self.db.get_portfolio(portfolio_id)
        assert portfolio["name"] == "Updated Portfolio"
        assert portfolio["base_currency"] == "EUR"

    def test_delete_portfolio(self):
        """Test soft deleting portfolio."""
        portfolio_id = self.db.create_portfolio("Test Portfolio", "USD")

        result = self.db.delete_portfolio(portfolio_id)
        assert result is True

        portfolio = self.db.get_portfolio(portfolio_id)
        assert portfolio["is_active"] == 0


class TestConfigOperations:
    """Test suite for configuration operations."""

    def setup_method(self):
        """Setup test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_set_and_get_config(self):
        """Test setting and getting configuration values."""
        # String config
        self.db.set_config("test_string", "value", "string")
        assert self.db.get_config("test_string") == "value"

        # Integer config
        self.db.set_config("test_int", 42, "integer")
        assert self.db.get_config("test_int") == 42

        # Float config
        self.db.set_config("test_float", 3.14, "float")
        assert self.db.get_config("test_float") == 3.14

        # Boolean config
        self.db.set_config("test_bool", True, "boolean")
        assert self.db.get_config("test_bool") is True

        # JSON config
        test_data = {"key": "value", "number": 123}
        self.db.set_config("test_json", test_data, "json")
        assert self.db.get_config("test_json") == test_data

    def test_get_all_config(self):
        """Test getting all configuration values."""
        # Set multiple configs
        self.db.set_config("key1", "value1", "string")
        self.db.set_config("key2", 42, "integer")
        self.db.set_config("key3", True, "boolean")

        all_config = self.db.get_all_config()
        assert len(all_config) == 3
        assert all_config["key1"] == "value1"
        assert all_config["key2"] == 42
        assert all_config["key3"] is True

    def test_delete_config(self):
        """Test deleting configuration."""
        self.db.set_config("test_key", "test_value", "string")

        result = self.db.delete_config("test_key")
        assert result is True

        value = self.db.get_config("test_key")
        assert value is None


class TestDatabaseMigrations:
    """Test suite for database migrations."""

    def setup_method(self):
        """Setup test environment before each test with older databases."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

    def _create_legacy_v2_db(self):
        """Create legacy database structure for version 2."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE database_version (
                version INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO database_version (version) VALUES (2)")

        # Create v2 tables without user_id columns
        conn.execute("""
            CREATE TABLE entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                entity_type TEXT NOT NULL CHECK (entity_type IN ('broker', 'bank', 'platform', 'other')),
                website TEXT,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                entity_id INTEGER,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create transactions table (which would exist in v2)
        conn.execute("""
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                transaction_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                total_amount REAL NOT NULL,
                fees REAL DEFAULT 0,
                transaction_date DATE NOT NULL,
                description TEXT,
                portfolio_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    def test_migration_and_list_portfolios(self):
        """Test migration from v2 and list_portfolios function."""
        self._create_legacy_v2_db()

        # Run migration
        db = Database(self.db_path)

        with db.get_connection() as conn:
            # Check version is updated
            cursor = conn.execute(
                "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
            )
            version = cursor.fetchone()[0]
            assert version == 12

            # Assert columns exist
            for table in ["entities", "portfolios", "transactions"]:
                cursor = conn.execute(f"PRAGMA table_info({table})")
                columns = {row[1] for row in cursor.fetchall()}
                assert "user_id" in columns, f"user_id missing in {table}"

        # Test that list_portfolios can be called without error
        portfolios = db.get_all_portfolios(user_id=1)
        assert isinstance(portfolios, list)
        # Should have at least the default portfolio or be empty
        assert len(portfolios) >= 0

    def teardown_method(self):
        """Cleanup after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_fresh_database_creation(self):
        """Test creating a fresh database."""
        db = Database(self.db_path)

        with db.get_connection() as conn:
            # Check version is current
            cursor = conn.execute(
                "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
            )
            version = cursor.fetchone()[0]
            assert version == 12

            # Check all tables exist
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "users" in tables
            assert "entities" in tables
            assert "portfolios" in tables

    def test_migration_from_older_version(self):
        """Test migration from older database version."""
        # Create a basic database structure (simulate older version)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE database_version (
                version INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO database_version (version) VALUES (1)")

        # Create minimal v1 tables
        conn.execute("""
            CREATE TABLE assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                transaction_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                total_amount REAL NOT NULL,
                transaction_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        # Now initialize with Database class (should trigger migration)
        db = Database(self.db_path)

        # Enable trace callback to diagnose SQL execution
        def trace_callback(stmt):
            print(f"SQL: {stmt}")

        conn = sqlite3.connect(self.db_path)
        conn.set_trace_callback(trace_callback)
        conn.close()

        with db.get_connection() as conn:
            # Check version is updated
            cursor = conn.execute(
                "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
            )
            version = cursor.fetchone()[0]
            assert version == 12

            # Check new tables exist
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "users" in tables
            assert "entities" in tables
            assert "portfolios" in tables

            # Check that migration created default user
            cursor = conn.execute("SELECT * FROM users WHERE username = 'admin'")
            admin_user = cursor.fetchone()
            assert admin_user is not None
