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


def test_fixed_deposits_table_exists(tmp_path):
    from portf_manager.database import Database

    db = Database(str(tmp_path / "test.db"))
    with db.get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "fixed_deposits" in tables


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
            assert result[0] == 28  # Current schema version

    def test_v18_assets_have_ticker_column(self):
        """v18 adds the nullable ticker alias column to assets."""
        with self.db.get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        assert "ticker" in cols

    def test_update_asset_accepts_ticker(self):
        """update_asset whitelist includes the new ticker column."""
        asset_id = self.db.create_asset(
            symbol="US0000000013", name="NVIDIA CP", asset_type="stock"
        )
        assert self.db.update_asset(asset_id, ticker="NVDA") is True
        asset = self.db.get_asset(asset_id)
        assert asset["ticker"] == "NVDA"

    def test_wal_mode_enabled(self):
        """Connections run in WAL journal mode for concurrent reader/writer."""
        with self.db.get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_purge_expired_cache(self):
        """purge_expired_cache removes only expired rows and returns the count."""
        # Unexpired (long TTL) + already-expired (negative TTL) + no-expiry.
        self.db.cache_set("fresh", {"v": 1}, ttl_seconds=3600)
        self.db.cache_set("stale", {"v": 2}, ttl_seconds=-1)
        self.db.cache_set("forever", {"v": 3}, ttl_seconds=None)

        removed = self.db.purge_expired_cache()

        assert removed == 1
        assert self.db.cache_get("fresh") == {"v": 1}
        assert self.db.cache_get("forever") == {"v": 3}
        # The stale row is gone from storage (not just hidden on read).
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT key FROM kv_cache WHERE key = 'stale'"
            ).fetchall()
        assert rows == []

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

    def test_get_asset_by_symbol_resolves_ticker_alias(self):
        """Ticker aliases resolve case-insensitively to the ISIN asset."""
        asset_id = self.db.create_asset(
            symbol="US0000000013", name="NVIDIA CP", asset_type="stock"
        )
        self.db.update_asset(asset_id, ticker="NVDA")
        assert self.db.get_asset_by_symbol("NVDA")["id"] == asset_id
        assert self.db.get_asset_by_symbol("nvda")["id"] == asset_id

    def test_get_asset_by_symbol_prefers_exact_symbol_over_ticker(self):
        """A direct symbol match wins over another asset's ticker alias."""
        btc_id = self.db.create_asset(symbol="BTC", name="Bitcoin", asset_type="crypto")
        other_id = self.db.create_asset(
            symbol="US0000000001", name="Decoy", asset_type="stock"
        )
        self.db.update_asset(other_id, ticker="BTC")
        assert self.db.get_asset_by_symbol("BTC")["id"] == btc_id

    def test_get_asset_by_symbol_unknown_returns_none(self):
        assert self.db.get_asset_by_symbol("ZZZZ.XX") is None

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
        conn.execute(
            """
            CREATE TABLE database_version (
                version INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.execute("INSERT INTO database_version (version) VALUES (2)")

        # Create v2 tables without user_id columns
        conn.execute(
            """
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
        """
        )

        conn.execute(
            """
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
        """
        )

        # Create transactions table (which would exist in v2)
        conn.execute(
            """
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
        """
        )

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
            assert version == 28

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
            assert version == 28

            # Check all tables exist
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "users" in tables
            assert "entities" in tables
            assert "portfolios" in tables
            assert "spending_transactions" in tables
            assert "spending_rules" in tables

    def test_migration_from_older_version(self):
        """Test migration from older database version."""
        # Create a basic database structure (simulate older version)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE database_version (
                version INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.execute("INSERT INTO database_version (version) VALUES (1)")

        # Create minimal v1 tables
        conn.execute(
            """
            CREATE TABLE assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.execute(
            """
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
        """
        )
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
            assert version == 28

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


def test_add_column_if_missing_reraises_unexpected_errors():
    """A locked DB (or any non-'no such table' error) must not be swallowed,
    otherwise migrations get version-stamped without being applied."""
    from unittest.mock import MagicMock
    from portf_manager.database import _add_column_if_missing

    conn = MagicMock()
    conn.execute.side_effect = sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError):
        _add_column_if_missing(conn, "assets", "ticker", "TEXT")


def test_add_column_if_missing_skips_missing_table():
    """When the table doesn't exist yet, the error should be silently ignored."""
    from unittest.mock import MagicMock
    from portf_manager.database import _add_column_if_missing

    conn = MagicMock()
    conn.execute.side_effect = sqlite3.OperationalError("no such table: assets")
    _add_column_if_missing(conn, "assets", "ticker", "TEXT")  # must not raise


class TestChatSessions:
    def test_create_and_get_session(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("sess1", "My Thread")
        s = db.get_chat_session("sess1")
        assert s is not None
        assert s["name"] == "My Thread"
        assert s["message_count"] == 0
        assert s["messages"] == []

    def test_list_sessions_ordered_by_last_message(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("a", "Alpha")
        import time

        time.sleep(0.01)
        db.create_chat_session("b", "Beta")
        sessions = db.list_chat_sessions()
        assert len(sessions) == 2
        assert sessions[0]["id"] == "b"  # most recent first

    def test_update_session_activity(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Thread")
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        db.update_chat_session_activity("s1", msgs)
        s = db.get_chat_session("s1")
        assert s["messages"] == msgs
        assert s["message_count"] == 2

    def test_delete_session(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Thread")
        result = db.delete_chat_session("s1")
        assert result is True
        assert db.get_chat_session("s1") is None

    def test_get_nonexistent_session_returns_none(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        assert db.get_chat_session("missing") is None

    def test_rename_session(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.create_chat_session("s1", "Original")
        result = db.rename_chat_session("s1", "Renamed")
        assert result is True
        s = db.get_chat_session("s1")
        assert s["name"] == "Renamed"

    def test_rename_nonexistent_session_returns_false(self, tmp_path):
        from portf_manager.database import Database

        db = Database(str(tmp_path / "t.db"))
        result = db.rename_chat_session("missing", "Whatever")
        assert result is False


class TestSpendingCategories:
    """v27 — spending_categories registry + CRUD/rename."""

    def setup_method(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.db = Database(self.db_path)

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_list_spending_categories_unions_transactions_rules_and_registry(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        self.db.create_spending_transaction(
            pid, "2026-01-05", "Desc", -10.0, category="Groceries"
        )
        self.db.create_spending_rule(pattern="NETFLIX", category="Subscriptions")
        self.db.create_spending_category("Vacation")

        cats = self.db.list_spending_categories()
        assert "Groceries" in cats
        assert "Subscriptions" in cats
        assert "Vacation" in cats
        assert len(cats) == len(set(cats))  # deduplicated

    def test_create_spending_category_returns_new_id(self):
        cat_id = self.db.create_spending_category("Vacation")
        assert isinstance(cat_id, int)
        assert cat_id > 0

    def test_find_spending_category_by_name(self):
        self.db.create_spending_category("Vacation")
        found = self.db.find_spending_category_by_name("Vacation")
        assert found is not None
        assert found["name"] == "Vacation"
        assert self.db.find_spending_category_by_name("Nonexistent") is None

    def test_rename_spending_category_updates_transactions_and_rules(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        tx_id = self.db.create_spending_transaction(
            pid, "2026-01-05", "Desc", -10.0, category="Groceries"
        )
        rule_id = self.db.create_spending_rule(
            pattern="MERCADONA", category="Groceries"
        )

        result = self.db.rename_spending_category("Groceries", "Food")
        assert result == {"transactions_updated": 1, "rules_updated": 1}

        assert self.db.get_spending_transaction(tx_id)["category"] == "Food"
        assert self.db.get_spending_rule(rule_id)["category"] == "Food"

    def test_rename_spending_category_registers_previously_unregistered_name(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        self.db.create_spending_transaction(
            pid, "2026-01-05", "Desc", -10.0, category="Groceries"
        )

        self.db.rename_spending_category("Groceries", "Food")

        assert self.db.find_spending_category_by_name("Food") is not None
        assert self.db.find_spending_category_by_name("Groceries") is None

    def test_rename_spending_category_renames_existing_registry_row(self):
        self.db.create_spending_category("Groceries")

        self.db.rename_spending_category("Groceries", "Food")

        assert self.db.find_spending_category_by_name("Food") is not None
        assert self.db.find_spending_category_by_name("Groceries") is None

    def test_rename_spending_category_merges_into_existing_name_without_error(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        tx_id = self.db.create_spending_transaction(
            pid, "2026-01-05", "Desc", -10.0, category="Groceries"
        )
        self.db.create_spending_category("Food")  # target already registered

        result = self.db.rename_spending_category("Groceries", "Food")
        assert result == {"transactions_updated": 1, "rules_updated": 0}

        assert self.db.get_spending_transaction(tx_id)["category"] == "Food"
        # Merge case: old_name's registry row (none here) is a no-op delete;
        # new_name's existing registry row is untouched, not duplicated.
        cats = self.db.list_spending_categories()
        assert cats.count("Food") == 1

    def test_rename_spending_category_to_same_name_is_a_noop(self):
        self.db.create_spending_category("Vacation")

        result = self.db.rename_spending_category("Vacation", "Vacation")
        assert result == {"transactions_updated": 0, "rules_updated": 0}

        # The category must still be registered -- a self-rename is a
        # no-op, not a delete.
        assert self.db.find_spending_category_by_name("Vacation") is not None

    def test_migration_seeds_income_and_spend_roots(self):
        # setup_method already created self.db via a fresh init, which runs
        # _create_all_tables (not the migration path) -- roots must exist
        # either way.
        cats = self.db.list_spending_categories_tree()
        income = next(c for c in cats if c["name"] == "Income")
        spend = next(c for c in cats if c["name"] == "Spend")
        assert income["is_root"] == 1
        assert income["parent_id"] is None
        assert spend["is_root"] == 1
        assert spend["parent_id"] is None

    def _build_v27_database(self, db_path, extra_sql=()):
        """Hand-build a v27-shaped database on disk: the pre-parent_id/is_root
        spending tables, stamped at schema version 27 (so constructing a real
        Database() against this file triggers _run_migrations automatically,
        exercising the actual upgrade path rather than invoking a migration
        method directly)."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE database_version (
                version INTEGER PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO database_version (version) VALUES (27)")
        conn.execute(
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, account_type TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE spending_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio_id INTEGER,
                date TEXT, description TEXT, amount REAL, currency TEXT DEFAULT 'EUR',
                category TEXT DEFAULT 'uncategorized', is_transfer INTEGER DEFAULT 0,
                transfer_link_type TEXT, transfer_link_id INTEGER, source TEXT, balance REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE spending_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT, category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE spending_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for sql in extra_sql:
            conn.execute(sql)
        conn.commit()
        conn.close()

    def test_migrate_to_v28_direct(self):
        db_path = tempfile.mktemp(suffix=".db")
        try:
            self._build_v27_database(
                db_path,
                extra_sql=[
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-05', 'D', -10.0, 'Groceries')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-06', 'D', -5.0, 'Groceries')",
                    "INSERT INTO spending_rules (pattern, category) VALUES ('NETFLIX', 'Subscriptions')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-07', 'D', 500.0, 'Salary')",
                ],
            )

            # Constructing Database() on an existing version-27 file triggers
            # _run_migrations automatically (27 < DATABASE_VERSION), which is
            # what actually runs _migrate_to_v28 -- this exercises the real
            # upgrade path an existing user's database goes through.
            db = Database(db_path)

            assert db.get_spending_category_root("Groceries") == "Spend"
            assert (
                db.get_spending_category_root("Subscriptions") == "Spend"
            )  # rule-only, no transactions -> defaults to Spend
            assert db.get_spending_category_root("Salary") == "Income"
            assert db.get_spending_category_root("uncategorized") is None
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_migrate_to_v28_promotes_preexisting_income_named_category(self):
        db_path = tempfile.mktemp(suffix=".db")
        try:
            # A user already created a category literally named "Income"
            # before this migration ever ran.
            self._build_v27_database(
                db_path,
                extra_sql=["INSERT INTO spending_categories (name) VALUES ('Income')"],
            )

            db = Database(db_path)

            tree = db.list_spending_categories_tree()
            income_rows = [c for c in tree if c["name"] == "Income"]
            assert len(income_rows) == 1  # promoted in place, not duplicated
            assert income_rows[0]["is_root"] == 1
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_migrate_to_v28_income_named_category_with_transactions_not_self_referenced(
        self,
    ):
        # Regression for a code-review finding: a user already had actual
        # *transactions* posted under a category literally named "Income"
        # (very plausible pre-migration name for a salary category), with
        # amounts whose majority sign is non-negative -- i.e. the category's
        # own majority-sign classification would resolve to the Income root
        # itself. _get_or_create_root("Income") promotes this pre-existing
        # row to a root (parent_id=NULL, is_root=1) *before* the main
        # per-category loop runs. That same row's name also appears in the
        # loop's `names` list (it's still selected out of
        # spending_transactions), and the loop must not then overwrite the
        # already-promoted root's parent_id with a majority-sign parent_id
        # -- which here would point at the root's own id, corrupting the
        # is_root=1 => parent_id IS NULL invariant. Symmetric "Spend" case
        # included since it's the same code path.
        db_path = tempfile.mktemp(suffix=".db")
        try:
            self._build_v27_database(
                db_path,
                extra_sql=[
                    "INSERT INTO spending_categories (name) VALUES ('Income')",
                    "INSERT INTO spending_categories (name) VALUES ('Spend')",
                    # Mostly non-negative amounts under "Income" -> majority
                    # sign resolves to the Income root itself.
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-01', 'Salary', 1000.0, 'Income')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-02', 'Bonus', 200.0, 'Income')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-03', 'Refund', -5.0, 'Income')",
                    # Mostly negative amounts under "Spend" -> majority sign
                    # resolves to the Spend root itself.
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-04', 'Rent', -800.0, 'Spend')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-05', 'Groceries', -50.0, 'Spend')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-06', 'Cashback', 3.0, 'Spend')",
                ],
            )

            db = Database(db_path)

            # Query the migrated schema directly rather than via the
            # not-yet-implemented list_spending_categories_tree() /
            # get_spending_category_root() accessors (those land in a later
            # task) -- this still exercises the real _run_migrations ->
            # _migrate_to_v28 upgrade path via Database(db_path) above.
            with db.get_connection() as conn:
                income_row = conn.execute(
                    "SELECT parent_id, is_root FROM spending_categories WHERE name = 'Income'"
                ).fetchone()
                spend_row = conn.execute(
                    "SELECT parent_id, is_root FROM spending_categories WHERE name = 'Spend'"
                ).fetchone()

            assert income_row is not None
            assert spend_row is not None
            assert income_row["parent_id"] is None
            assert income_row["is_root"] == 1
            assert spend_row["parent_id"] is None
            assert spend_row["is_root"] == 1
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_get_spending_category_root_resolves_nested_category(self):
        # "Income" already exists as a seeded root (Task 1) -- use its real
        # id rather than creating a second row of the same name, which
        # would violate the UNIQUE(name) constraint.
        income_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Income"
        )
        with self.db.get_connection() as conn:
            job_id = conn.execute(
                "INSERT INTO spending_categories (name, parent_id) VALUES ('Job', ?)",
                (income_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO spending_categories (name, parent_id) VALUES ('Salary', ?)",
                (job_id,),
            )
            conn.commit()
        assert self.db.get_spending_category_root("Salary") == "Income"
        assert self.db.get_spending_category_root("Job") == "Income"

    def test_get_spending_category_root_returns_none_for_unknown_name(self):
        assert self.db.get_spending_category_root("uncategorized") is None
        assert self.db.get_spending_category_root("Nonexistent") is None

    def test_list_spending_categories_tree_resolves_parent_name(self):
        spend_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)

        tree = self.db.list_spending_categories_tree()
        insurance = next(c for c in tree if c["name"] == "Insurance")
        assert insurance["parent_name"] == "Spend"
        spend = next(c for c in tree if c["name"] == "Spend")
        assert spend["parent_name"] is None

    def test_reparent_spending_category_moves_node(self):
        spend_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Spend"
        )
        self.db.create_spending_category("Car Insurance", parent_id=spend_id)
        self.db.create_spending_category("Insurance", parent_id=spend_id)

        self.db.reparent_spending_category("Car Insurance", "Insurance")

        tree = self.db.list_spending_categories_tree()
        car = next(c for c in tree if c["name"] == "Car Insurance")
        assert car["parent_name"] == "Insurance"

    def test_reparent_spending_category_rejects_root(self):
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Spend", "Income")

    def test_reparent_spending_category_rejects_unknown_names(self):
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Nonexistent", "Spend")
        self.db.create_spending_category("Vacation")
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Vacation", "AlsoNonexistent")

    def test_reparent_spending_category_rejects_cycle(self):
        spend_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)
        insurance_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Insurance"
        )
        self.db.create_spending_category("Car Insurance", parent_id=insurance_id)

        # Direct cycle: Insurance -> Car Insurance's parent, now try the reverse.
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Insurance", "Car Insurance")

    def test_rename_spending_category_merge_reparents_children(self):
        spend_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)
        self.db.create_spending_category("Cover", parent_id=spend_id)  # merge target
        insurance_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Insurance"
        )
        self.db.create_spending_category("Car Insurance", parent_id=insurance_id)

        self.db.rename_spending_category("Insurance", "Cover")  # merge case

        tree = self.db.list_spending_categories_tree()
        car = next(c for c in tree if c["name"] == "Car Insurance")
        assert car["parent_name"] == "Cover"

    def test_rename_spending_category_merge_into_direct_child_avoids_cycle(self):
        # Merging a category into its own direct child (e.g. "Insurance" ->
        # "Car Insurance") must not create a self-reference: the blanket
        # "reparent every child of old_name onto new_name" logic would
        # otherwise set Car Insurance's own parent_id to its own id, since
        # Car Insurance is itself a child of Insurance.
        spend_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)
        insurance_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Insurance"
        )
        self.db.create_spending_category("Car Insurance", parent_id=insurance_id)

        self.db.rename_spending_category("Insurance", "Car Insurance")  # merge case

        tree = {c["name"]: c for c in self.db.list_spending_categories_tree()}
        car = tree["Car Insurance"]
        # Car Insurance must take Insurance's former place under Spend, not
        # point at itself.
        assert car["parent_id"] == spend_id
        assert car["parent_id"] != car["id"]
        assert car["parent_name"] == "Spend"

        # get_spending_category_root must still terminate and resolve
        # correctly (would infinite-loop on a self-referencing row).
        assert self.db.get_spending_category_root("Car Insurance") == "Spend"

    def test_rename_spending_category_merge_into_grandchild_avoids_cycle(self):
        # Two-level case: Insurance -> Car Insurance -> Comprehensive.
        # Merging Insurance into Comprehensive (a grandchild) must promote
        # the intermediate node (Car Insurance) onto Insurance's old
        # parent, leaving Comprehensive's own parent_id untouched, with no
        # cycle anywhere in the chain.
        spend_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)
        insurance_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Insurance"
        )
        self.db.create_spending_category("Car Insurance", parent_id=insurance_id)
        car_insurance_id = next(
            c["id"]
            for c in self.db.list_spending_categories_tree()
            if c["name"] == "Car Insurance"
        )
        self.db.create_spending_category("Comprehensive", parent_id=car_insurance_id)

        self.db.rename_spending_category("Insurance", "Comprehensive")  # merge case

        tree = {c["name"]: c for c in self.db.list_spending_categories_tree()}
        car = tree["Car Insurance"]
        comprehensive = tree["Comprehensive"]

        # Car Insurance is promoted to Insurance's old parent.
        assert car["parent_id"] == spend_id
        assert car["parent_name"] == "Spend"
        # Comprehensive's own parent_id is untouched -- still Car Insurance.
        assert comprehensive["parent_id"] == car_insurance_id
        assert comprehensive["parent_name"] == "Car Insurance"
        # No cycle: neither node points at itself or at the other in a loop.
        assert car["parent_id"] != car["id"]
        assert comprehensive["parent_id"] != comprehensive["id"]

        assert self.db.get_spending_category_root("Comprehensive") == "Spend"
        assert self.db.get_spending_category_root("Car Insurance") == "Spend"

    def test_create_spending_category_without_parent_still_works(self):
        # Backward compatibility: existing callers that pass only a name
        # must keep working unchanged (parent_id defaults to None/NULL).
        cat_id = self.db.create_spending_category("Vacation")
        assert isinstance(cat_id, int)
        tree = self.db.list_spending_categories_tree()
        vacation = next(c for c in tree if c["name"] == "Vacation")
        assert vacation["parent_id"] is None
