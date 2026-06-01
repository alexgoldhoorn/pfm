"""
Portfolio Management Database Module

This module provides SQLite database functionality for portfolio management,
including asset tracking, transaction recording, price history, and configuration.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

# Database version for migration tracking
DATABASE_VERSION = 9


# black
def _add_column_if_missing(conn, table, column, ddl):
    # Query column list
    try:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError:
        # Table doesn't exist, skip adding column
        pass


class DatabaseError(Exception):
    """Custom exception for database-related errors."""


class Database:
    """
    SQLite database manager for portfolio management.

    Provides connection management, schema creation, migrations,
    and CRUD operations for all portfolio-related tables.

    Implements the DatabaseAdapter protocol for use with domain models.
    """

    def __init__(self, db_path: str = "portfolio.db"):
        """
        Initialize database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.logger = logging.getLogger(__name__)

        # Ensure database directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database if it doesn't exist
        self._initialize_database()

    def _initialize_database(self):
        """Initialize database with schema and version tracking."""
        try:
            with self.get_connection() as conn:
                # Create version table first
                self._create_version_table(conn)

                # Check current version
                current_version = self._get_database_version(conn)

                if current_version == 0:
                    # Fresh database - create all tables
                    self._create_all_tables(conn)
                    self._set_database_version(conn, DATABASE_VERSION)
                    self.logger.info(
                        f"Created new database with version {DATABASE_VERSION}"
                    )
                elif current_version < DATABASE_VERSION:
                    # Run migrations
                    self._run_migrations(conn, current_version)
                    self.logger.info(
                        f"Migrated database from version {current_version} to {DATABASE_VERSION}"
                    )
                elif current_version > DATABASE_VERSION:
                    raise DatabaseError(
                        f"Database version {current_version} is newer than supported version {DATABASE_VERSION}"
                    )

        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise DatabaseError(f"Database initialization failed: {e}")

    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections.

        Yields:
            sqlite3.Connection: Database connection with row factory
        """
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            # Enable foreign key constraints
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Database connection error: {e}")
            raise DatabaseError(f"Database connection failed: {e}")
        finally:
            if conn:
                conn.close()

    def _create_version_table(self, conn: sqlite3.Connection):
        """Create database version tracking table."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS database_version (
                version INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def _get_database_version(self, conn: sqlite3.Connection) -> int:
        """Get current database version."""
        cursor = conn.execute(
            "SELECT version FROM database_version ORDER BY version DESC LIMIT 1"
        )
        result = cursor.fetchone()
        return result[0] if result else 0

    def _set_database_version(self, conn: sqlite3.Connection, version: int):
        """Set database version."""
        conn.execute("INSERT INTO database_version (version) VALUES (?)", (version,))
        conn.commit()

    def _create_all_tables(self, conn: sqlite3.Connection):
        """Create all database tables."""
        # Users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                full_name TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Entities table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL CHECK (entity_type IN ('broker', 'bank', 'platform', 'other')),
                website TEXT,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                UNIQUE (user_id, name)
            )
        """)

        # Portfolios table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                entity_id INTEGER,
                user_id INTEGER,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities (id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        # Assets table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'bond', 'crypto', 'etf', 'mutual_fund', 'commodity', 'cash')),
                exchange TEXT,
                currency TEXT NOT NULL DEFAULT 'USD',
                sector TEXT,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Transactions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                portfolio_id INTEGER,
                user_id INTEGER,
                transaction_type TEXT NOT NULL CHECK (transaction_type IN ('buy', 'sell', 'dividend', 'split', 'transfer_in', 'transfer_out')),
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                total_amount REAL NOT NULL,
                fees REAL DEFAULT 0,
                tax REAL DEFAULT 0,
                currency TEXT,
                transaction_date DATE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios (id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        # Bookings table (deposits and withdrawals — no asset involved)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER,
                date DATE NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('Deposit', 'Withdrawal')),
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios (id) ON DELETE SET NULL
            )
        """)

        # Prices table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL,
                price REAL NOT NULL,
                price_date DATE NOT NULL,
                price_type TEXT NOT NULL DEFAULT 'close' CHECK (price_type IN ('open', 'high', 'low', 'close', 'adjusted_close')),
                volume INTEGER,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE,
                UNIQUE(asset_id, price_date, price_type)
            )
        """)

        # Portfolio configuration table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_key TEXT NOT NULL UNIQUE,
                config_value TEXT NOT NULL,
                config_type TEXT NOT NULL DEFAULT 'string' CHECK (config_type IN ('string', 'integer', 'float', 'boolean', 'json')),
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # API Keys table for authentication
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                description TEXT,
                last_used DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME
            )
        """)

        # Create indexes for better performance
        conn.execute("CREATE INDEX idx_users_username ON users (username)")
        conn.execute("CREATE INDEX idx_users_email ON users (email)")
        conn.execute("CREATE INDEX idx_entities_name ON entities (name)")
        conn.execute("CREATE INDEX idx_entities_type ON entities (entity_type)")
        conn.execute("CREATE INDEX idx_entities_user_id ON entities (user_id)")
        conn.execute("CREATE INDEX idx_portfolios_name ON portfolios (name)")
        conn.execute("CREATE INDEX idx_portfolios_entity_id ON portfolios (entity_id)")
        conn.execute("CREATE INDEX idx_portfolios_user_id ON portfolios (user_id)")
        conn.execute("CREATE INDEX idx_assets_symbol ON assets (symbol)")
        conn.execute("CREATE INDEX idx_assets_type ON assets (asset_type)")
        conn.execute(
            "CREATE INDEX idx_transactions_asset_id ON transactions (asset_id)"
        )
        conn.execute(
            "CREATE INDEX idx_transactions_portfolio_id ON transactions (portfolio_id)"
        )
        conn.execute(
            "CREATE INDEX idx_transactions_date ON transactions (transaction_date)"
        )
        conn.execute(
            "CREATE INDEX idx_transactions_type ON transactions (transaction_type)"
        )
        conn.execute("CREATE INDEX idx_transactions_user_id ON transactions (user_id)")
        conn.execute("CREATE INDEX idx_prices_asset_id ON prices (asset_id)")
        conn.execute("CREATE INDEX idx_prices_date ON prices (price_date)")
        conn.execute(
            "CREATE INDEX idx_portfolio_config_key ON portfolio_config (config_key)"
        )

        # API keys indexes
        conn.execute("CREATE INDEX idx_api_keys_key_hash ON api_keys (key_hash)")
        conn.execute("CREATE INDEX idx_api_keys_prefix ON api_keys (key_prefix)")
        conn.execute("CREATE INDEX idx_api_keys_active ON api_keys (is_active)")

        # Bookings indexes
        conn.execute(
            "CREATE INDEX idx_bookings_portfolio_id ON bookings (portfolio_id)"
        )
        conn.execute("CREATE INDEX idx_bookings_date ON bookings (date)")

        # Allocation targets, price targets, research reports (v7)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS allocation_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type TEXT NOT NULL,
                target_pct REAL NOT NULL CHECK (target_pct >= 0 AND target_pct <= 100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(asset_type)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL UNIQUE,
                buy_below REAL,
                sell_above REAL,
                fair_value REAL,
                notes TEXT,
                alert_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                fair_value REAL,
                recommendation TEXT,
                confidence TEXT,
                summary TEXT,
                report_json TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date DATE NOT NULL UNIQUE,
                total_value_eur REAL NOT NULL,
                total_cost_eur REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT,
                asset_type TEXT,
                buy_below REAL,
                notes TEXT,
                alert_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_amount_eur REAL NOT NULL,
                target_date DATE NOT NULL,
                monthly_contribution_eur REAL DEFAULT 0,
                expected_return_pct REAL DEFAULT 7.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create triggers for updated_at timestamps
        for table in [
            "entities",
            "portfolios",
            "assets",
            "transactions",
            "portfolio_config",
        ]:
            conn.execute(f"""
                CREATE TRIGGER update_{table}_timestamp
                AFTER UPDATE ON {table}
                BEGIN
                    UPDATE {table} SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)

        conn.commit()

    def _run_migrations(self, conn: sqlite3.Connection, current_version: int):
        """Run database migrations from current version to latest."""
        if current_version < 2:
            self._migrate_to_v2(conn)
        if current_version < 3:
            self._migrate_to_v3(conn)
        if current_version < 4:
            self._migrate_to_v4(conn)
        if current_version < 5:
            self._migrate_to_v5(conn)
        if current_version < 6:
            self._migrate_to_v6(conn)
        if current_version < 7:
            self._migrate_to_v7(conn)
        if current_version < 8:
            self._migrate_to_v8(conn)
        if current_version < 9:
            self._migrate_to_v9(conn)

        self._set_database_version(conn, DATABASE_VERSION)

    def _migrate_to_v2(self, conn: sqlite3.Connection):
        """Migrate database from v1 to v2 - add entities and portfolios support."""
        # Add entities table
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

        # Add portfolios table
        conn.execute("""
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                entity_id INTEGER,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities (id) ON DELETE SET NULL
            )
        """)

        # Create a default portfolio for existing transactions
        conn.execute("""
            INSERT INTO portfolios (name, description)
            VALUES ('Default Portfolio', 'Automatically created for existing transactions')
        """)

        # Add portfolio_id column to transactions table
        conn.execute("ALTER TABLE transactions ADD COLUMN portfolio_id INTEGER")

        # Set all existing transactions to the default portfolio
        conn.execute("""
            UPDATE transactions
            SET portfolio_id = (SELECT id FROM portfolios WHERE name = 'Default Portfolio')
        """)

        # Add indexes for new tables
        conn.execute("CREATE INDEX idx_entities_name ON entities (name)")
        conn.execute("CREATE INDEX idx_entities_type ON entities (entity_type)")
        conn.execute("CREATE INDEX idx_portfolios_name ON portfolios (name)")
        conn.execute("CREATE INDEX idx_portfolios_entity_id ON portfolios (entity_id)")
        conn.execute(
            "CREATE INDEX idx_transactions_portfolio_id ON transactions (portfolio_id)"
        )

        # Add triggers for new tables
        for table in ["entities", "portfolios"]:
            conn.execute(f"""
                CREATE TRIGGER update_{table}_timestamp
                AFTER UPDATE ON {table}
                BEGIN
                    UPDATE {table} SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)

        conn.commit()

    def _migrate_to_v3(self, conn: sqlite3.Connection):
        """Migrate database from v2 to v3 - add user authentication system."""
        # Add users table
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                full_name TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Ensure user_id columns exist before updating data
        _add_column_if_missing(conn, "entities", "user_id", "INTEGER")
        _add_column_if_missing(conn, "portfolios", "user_id", "INTEGER")
        _add_column_if_missing(conn, "transactions", "user_id", "INTEGER")

        # Create a default user for existing data
        conn.execute("""
            INSERT INTO users (username, email, password_hash, salt, full_name)
            VALUES ('admin', 'admin@localhost', 'dummy_hash', 'dummy_salt', 'Default Admin User')
        """)

        # Set all existing entities to the default user
        try:
            cursor = conn.execute("""
                UPDATE entities
                SET user_id = (SELECT id FROM users WHERE username = 'admin')
            """)
            if cursor.rowcount == 0:
                self.logger.warning(
                    "No entities found to update with user_id during migration"
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to update entities with user_id during migration: {e}"
            )

        # Set all existing portfolios to the default user
        try:
            cursor = conn.execute("""
                UPDATE portfolios
                SET user_id = (SELECT id FROM users WHERE username = 'admin')
            """)
            if cursor.rowcount == 0:
                self.logger.warning(
                    "No portfolios found to update with user_id during migration"
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to update portfolios with user_id during migration: {e}"
            )

        # Set all existing transactions to the default user
        try:
            cursor = conn.execute("""
                UPDATE transactions
                SET user_id = (SELECT id FROM users WHERE username = 'admin')
            """)
            if cursor.rowcount == 0:
                self.logger.warning(
                    "No transactions found to update with user_id during migration"
                )
        except Exception as e:
            self.logger.warning(
                f"Failed to update transactions with user_id during migration: {e}"
            )

        # Add indexes for new tables and columns
        conn.execute("CREATE INDEX idx_users_username ON users (username)")
        conn.execute("CREATE INDEX idx_users_email ON users (email)")

        # Only create indexes for tables that exist
        try:
            conn.execute("CREATE INDEX idx_entities_user_id ON entities (user_id)")
        except sqlite3.OperationalError:
            # Table or column doesn't exist, skip index creation
            pass

        try:
            conn.execute("CREATE INDEX idx_portfolios_user_id ON portfolios (user_id)")
        except sqlite3.OperationalError:
            # Table or column doesn't exist, skip index creation
            pass

        try:
            conn.execute(
                "CREATE INDEX idx_transactions_user_id ON transactions (user_id)"
            )
        except sqlite3.OperationalError:
            # Table or column doesn't exist, skip index creation
            pass

        # Add foreign key constraints (recreate tables with proper constraints)
        # Note: SQLite doesn't support adding foreign key constraints to existing tables
        # For production, you might want to recreate tables with proper foreign keys

        # Add trigger for users table
        conn.execute("""
            CREATE TRIGGER update_users_timestamp
            AFTER UPDATE ON users
            BEGIN
                UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        conn.commit()

    def _migrate_to_v4(self, conn: sqlite3.Connection):
        """Migrate database from v3 to v4 - add API keys table."""
        # Check if api_keys table already exists
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='api_keys'
        """)

        if not cursor.fetchone():
            # Create api_keys table
            conn.execute("""
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    key_prefix TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    description TEXT,
                    last_used DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME
                )
            """)

            # Create indexes for api_keys table
            conn.execute("CREATE INDEX idx_api_keys_key_hash ON api_keys (key_hash)")
            conn.execute("CREATE INDEX idx_api_keys_prefix ON api_keys (key_prefix)")
            conn.execute("CREATE INDEX idx_api_keys_active ON api_keys (is_active)")

            self.logger.info("Added api_keys table with indexes")
        else:
            self.logger.info("api_keys table already exists, skipping creation")

        conn.commit()

    def _migrate_to_v5(self, conn: sqlite3.Connection):
        """Migrate database from v4 to v5 — add tax to transactions, add bookings table."""
        _add_column_if_missing(conn, "transactions", "tax", "REAL DEFAULT 0")

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bookings'"
        )
        if not cursor.fetchone():
            conn.execute("""
                CREATE TABLE bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    portfolio_id INTEGER,
                    date DATE NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('Deposit', 'Withdrawal')),
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'EUR',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios (id) ON DELETE SET NULL
                )
            """)
            conn.execute(
                "CREATE INDEX idx_bookings_portfolio_id ON bookings (portfolio_id)"
            )
            conn.execute("CREATE INDEX idx_bookings_date ON bookings (date)")
            self.logger.info("Added bookings table")

        conn.commit()

    def _migrate_to_v6(self, conn: sqlite3.Connection):
        """Migrate database from v5 to v6 — add per-transaction currency column."""
        _add_column_if_missing(conn, "transactions", "currency", "TEXT")
        conn.commit()

    def _migrate_to_v7(self, conn: sqlite3.Connection):
        """Migrate database from v6 to v7 — add allocation_targets, price_targets, research_reports."""
        for ddl in [
            """CREATE TABLE IF NOT EXISTS allocation_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type TEXT NOT NULL,
                target_pct REAL NOT NULL CHECK (target_pct >= 0 AND target_pct <= 100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(asset_type)
            )""",
            """CREATE TABLE IF NOT EXISTS price_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL UNIQUE,
                buy_below REAL,
                sell_above REAL,
                fair_value REAL,
                notes TEXT,
                alert_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS research_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                fair_value REAL,
                recommendation TEXT,
                confidence TEXT,
                summary TEXT,
                report_json TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE
            )""",
        ]:
            conn.execute(ddl)
        conn.commit()

    def _migrate_to_v8(self, conn: sqlite3.Connection):
        """Migrate database from v7 to v8 — add portfolio_snapshots table."""
        conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date DATE NOT NULL UNIQUE,
                total_value_eur REAL NOT NULL,
                total_cost_eur REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.commit()

    def _migrate_to_v9(self, conn: sqlite3.Connection):
        """Migrate database from v8 to v9 — add watchlist and goals tables."""
        conn.execute("""CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT,
                asset_type TEXT,
                buy_below REAL,
                notes TEXT,
                alert_sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_amount_eur REAL NOT NULL,
                target_date DATE NOT NULL,
                monthly_contribution_eur REAL DEFAULT 0,
                expected_return_pct REAL DEFAULT 7.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.commit()

    # CRUD Operations for Users
    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        salt: str,
        full_name: str = None,
    ) -> int:
        """Create a new user."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, email, password_hash, salt, full_name)
                VALUES (?, ?, ?, ?, ?)
            """,
                (username, email, password_hash, salt, full_name),
            )
            conn.commit()
            return cursor.lastrowid

    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user by email."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_user_password(self, user_id: int, password_hash: str, salt: str) -> bool:
        """Update user password."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                (password_hash, salt, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_user_last_login(self, user_id: int) -> bool:
        """Update user's last login timestamp."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_user(self, user_id: int, **kwargs) -> bool:
        """Update user fields."""
        if not kwargs:
            return False

        valid_fields = {"username", "email", "full_name", "is_active"}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
            values = list(update_fields.values()) + [user_id]

            cursor = conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return cursor.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        """Delete user (soft delete by setting is_active = False)."""
        return self.update_user(user_id, is_active=False)

    # CRUD Operations for Entities
    def create_entity(
        self,
        name: str,
        entity_type: str,
        user_id: int,
        website: str = None,
        description: str = None,
    ) -> int:
        """Create a new entity."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO entities (name, entity_type, user_id, website, description)
                VALUES (?, ?, ?, ?, ?)
            """,
                (name, entity_type, user_id, website, description),
            )
            conn.commit()
            return cursor.lastrowid

    def get_entity(self, entity_id: int) -> Optional[Dict]:
        """Get entity by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_entity_by_name(self, name: str) -> Optional[Dict]:
        """Get entity by name."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM entities WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_entities(
        self, active_only: bool = True, user_id: int = None
    ) -> List[Dict]:
        """Get all entities."""
        with self.get_connection() as conn:
            query = "SELECT * FROM entities"
            params = []
            conditions = []

            if active_only:
                conditions.append("is_active = ?")
                params.append(True)

            if user_id is not None:
                conditions.append("user_id = ?")
                params.append(user_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY name"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_entity(self, entity_id: int, **kwargs) -> bool:
        """Update entity fields."""
        if not kwargs:
            return False

        valid_fields = {"name", "entity_type", "website", "description", "is_active"}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
            values = list(update_fields.values()) + [entity_id]

            cursor = conn.execute(
                f"UPDATE entities SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_entity(self, entity_id: int) -> bool:
        """Delete entity (soft delete by setting is_active = False)."""
        return self.update_entity(entity_id, is_active=False)

    # CRUD Operations for Portfolios
    def create_portfolio(
        self,
        name: str,
        base_currency: str = "USD",
        entity_id: int = None,
        description: str = None,
        user_id: int = None,
    ) -> int:
        """Create a new portfolio."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO portfolios (name, base_currency, entity_id, description, user_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (name, base_currency, entity_id, description, user_id),
            )
            conn.commit()
            return cursor.lastrowid

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict]:
        """Get portfolio by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.*, e.name as entity_name, e.entity_type, e.website
                FROM portfolios p
                LEFT JOIN entities e ON p.entity_id = e.id
                WHERE p.id = ?
            """,
                (portfolio_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_portfolio_by_name(self, name: str) -> Optional[Dict]:
        """Get portfolio by name."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.*, e.name as entity_name, e.entity_type, e.website
                FROM portfolios p
                LEFT JOIN entities e ON p.entity_id = e.id
                WHERE p.name = ?
            """,
                (name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_or_create_portfolio(
        self, name: str, base_currency: str = "EUR", description: str = None
    ) -> int:
        """Return the portfolio ID for *name*, creating it if it does not exist.

        Args:
            name: Portfolio / broker name.
            base_currency: Currency used when creating a new portfolio.
            description: Description used only when creating a new portfolio.

        Returns:
            int: Portfolio ID (existing or newly created).
        """
        existing = self.get_portfolio_by_name(name)
        if existing:
            return existing["id"]
        return self.create_portfolio(
            name=name,
            base_currency=base_currency,
            description=description or "Auto-created from import",
        )

    def get_all_portfolios(
        self, active_only: bool = True, user_id: int = None
    ) -> List[Dict]:
        """Get all portfolios."""
        with self.get_connection() as conn:
            query = """
                SELECT p.*, e.name as entity_name, e.entity_type, e.website
                FROM portfolios p
                LEFT JOIN entities e ON p.entity_id = e.id
            """
            params = []
            conditions = []

            if active_only:
                conditions.append("p.is_active = ?")
                params.append(True)

            if user_id is not None:
                conditions.append("p.user_id = ?")
                params.append(user_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY p.name"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_portfolio(self, portfolio_id: int, **kwargs) -> bool:
        """Update portfolio fields."""
        if not kwargs:
            return False

        valid_fields = {
            "name",
            "base_currency",
            "entity_id",
            "description",
            "is_active",
        }
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
            values = list(update_fields.values()) + [portfolio_id]

            cursor = conn.execute(
                f"UPDATE portfolios SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_portfolio(self, portfolio_id: int) -> bool:
        """Delete portfolio (soft delete by setting is_active = False)."""
        return self.update_portfolio(portfolio_id, is_active=False)

    def get_transactions_by_portfolio(self, portfolio_id: int) -> List[Dict]:
        """Get all transactions for a portfolio."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT {self._TX_COLS}
                FROM transactions t
                JOIN assets a ON t.asset_id = a.id
                WHERE t.portfolio_id = ?
                ORDER BY t.transaction_date DESC
            """,
                (portfolio_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # CRUD Operations for Assets
    def create_asset(
        self,
        symbol: str,
        name: str,
        asset_type: str,
        exchange: str = None,
        currency: str = "USD",
        sector: str = None,
        description: str = None,
    ) -> int:
        """
        Create a new asset.

        Args:
            symbol: Asset symbol (e.g., 'AAPL')
            name: Asset name (e.g., 'Apple Inc.')
            asset_type: Type of asset
            exchange: Trading exchange
            currency: Asset currency
            sector: Asset sector
            description: Asset description

        Returns:
            int: ID of created asset
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO assets (symbol, name, asset_type, exchange, currency, sector, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (symbol, name, asset_type, exchange, currency, sector, description),
            )
            conn.commit()
            return cursor.lastrowid

    def get_asset(self, asset_id: int) -> Optional[Dict]:
        """Get asset by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_asset_by_symbol(self, symbol: str) -> Optional[Dict]:
        """Get asset by symbol."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM assets WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_assets(self, active_only: bool = True) -> List[Dict]:
        """Get all assets."""
        with self.get_connection() as conn:
            query = "SELECT * FROM assets"
            params = []
            if active_only:
                query += " WHERE is_active = ?"
                params.append(True)
            query += " ORDER BY symbol"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_asset(self, asset_id: int, **kwargs) -> bool:
        """Update asset fields."""
        if not kwargs:
            return False

        valid_fields = {
            "name",
            "asset_type",
            "exchange",
            "currency",
            "sector",
            "description",
            "is_active",
        }
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
            values = list(update_fields.values()) + [asset_id]

            cursor = conn.execute(
                f"UPDATE assets SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_asset(self, asset_id: int) -> bool:
        """Delete asset (soft delete by setting is_active = False)."""
        return self.update_asset(asset_id, is_active=False)

    # CRUD Operations for Transactions
    def find_duplicate_transaction(
        self,
        asset_id: int,
        transaction_type: str,
        quantity: float,
        price: float,
        transaction_date: str,
        portfolio_id: int = None,
    ) -> Optional[Dict]:
        """Return an existing transaction that matches on all key fields, or None.

        Used before inserting to detect accidental re-imports. Matches on
        asset_id, type, quantity, price (±0.001%), date, and portfolio.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, transaction_date, transaction_type, quantity, price, portfolio_id
                FROM transactions
                WHERE asset_id = ?
                  AND transaction_type = ?
                  AND transaction_date = ?
                  AND ABS(quantity - ?) < 0.0001
                  AND ABS(price - ?) / NULLIF(price, 0) < 0.00001
                  AND (portfolio_id IS ? OR portfolio_id = ?)
                LIMIT 1
                """,
                (
                    asset_id,
                    transaction_type,
                    transaction_date,
                    quantity,
                    price,
                    portfolio_id,
                    portfolio_id,
                ),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_transaction(
        self,
        asset_id: int,
        transaction_type: str,
        quantity: float,
        price: float,
        total_amount: float,
        transaction_date: str,
        portfolio_id: int = None,
        fees: float = 0,
        tax: float = 0,
        currency: str = None,
        description: str = None,
        user_id: int = None,
    ) -> int:
        """Create a new transaction."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO transactions (asset_id, portfolio_id, transaction_type, quantity, price,
                                        total_amount, fees, tax, currency, transaction_date, description, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    asset_id,
                    portfolio_id,
                    transaction_type,
                    quantity,
                    price,
                    total_amount,
                    fees,
                    tax,
                    currency,
                    transaction_date,
                    description,
                    user_id,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    # Explicit column list for transaction queries so COALESCE(t.currency, a.currency)
    # is not shadowed by t.* (sqlite3.Row dict uses the first occurrence of a column name).
    _TX_COLS = """
        t.id, t.asset_id, t.portfolio_id, t.user_id,
        t.transaction_type, t.quantity, t.price, t.total_amount,
        t.fees, t.tax, t.transaction_date, t.description,
        t.created_at, t.updated_at,
        a.symbol, a.name,
        COALESCE(t.currency, a.currency) AS currency
    """

    def get_transaction(self, transaction_id: int) -> Optional[Dict]:
        """Get transaction by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT {self._TX_COLS}
                FROM transactions t
                JOIN assets a ON t.asset_id = a.id
                WHERE t.id = ?
            """,
                (transaction_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_transactions_by_asset(self, asset_id: int) -> List[Dict]:
        """Get all transactions for an asset."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT {self._TX_COLS}
                FROM transactions t
                JOIN assets a ON t.asset_id = a.id
                WHERE t.asset_id = ?
                ORDER BY t.transaction_date DESC
            """,
                (asset_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_transactions(
        self, limit: int = None, user_id: int = None, portfolio_id: int = None
    ) -> List[Dict]:
        """Get all transactions."""
        with self.get_connection() as conn:
            query = f"""
                SELECT {self._TX_COLS}, p.name as portfolio_name
                FROM transactions t
                JOIN assets a ON t.asset_id = a.id
                LEFT JOIN portfolios p ON t.portfolio_id = p.id
            """
            params = []
            conditions = []

            if user_id is not None:
                conditions.append("t.user_id = ?")
                params.append(user_id)

            if portfolio_id is not None:
                conditions.append("t.portfolio_id = ?")
                params.append(portfolio_id)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY t.transaction_date DESC"

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_transaction(self, transaction_id: int, **kwargs) -> bool:
        """Update transaction fields."""
        if not kwargs:
            return False

        valid_fields = {
            "asset_id",
            "portfolio_id",
            "transaction_type",
            "quantity",
            "price",
            "total_amount",
            "fees",
            "tax",
            "currency",
            "transaction_date",
            "description",
        }
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields.keys())
            values = list(update_fields.values()) + [transaction_id]

            cursor = conn.execute(
                f"UPDATE transactions SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete transaction."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM transactions WHERE id = ?", (transaction_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_transactions_by_portfolio(self, portfolio_id: int) -> int:
        """Delete all transactions for a portfolio. Returns number of deleted rows."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM transactions WHERE portfolio_id = ?",
                (portfolio_id,),
            )
            conn.commit()
            return cursor.rowcount

    # CRUD Operations for Bookings (deposits and withdrawals)

    def create_booking(
        self,
        date: str,
        action: str,
        amount: float,
        currency: str,
        portfolio_id: int = None,
    ) -> int:
        """Create a new booking (deposit or withdrawal)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO bookings (portfolio_id, date, action, amount, currency)
                VALUES (?, ?, ?, ?, ?)
            """,
                (portfolio_id, date, action, amount, currency),
            )
            conn.commit()
            return cursor.lastrowid

    def get_booking(self, booking_id: int) -> Optional[Dict]:
        """Get a booking by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT b.*, p.name as portfolio_name
                FROM bookings b
                LEFT JOIN portfolios p ON b.portfolio_id = p.id
                WHERE b.id = ?
            """,
                (booking_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_bookings(self, portfolio_id: int = None) -> List[Dict]:
        """Get all bookings, optionally filtered by portfolio."""
        with self.get_connection() as conn:
            query = """
                SELECT b.*, p.name as portfolio_name
                FROM bookings b
                LEFT JOIN portfolios p ON b.portfolio_id = p.id
            """
            params = []
            if portfolio_id is not None:
                query += " WHERE b.portfolio_id = ?"
                params.append(portfolio_id)
            query += " ORDER BY b.date DESC"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_bookings_by_portfolio(self, portfolio_id: int) -> List[Dict]:
        """Get all bookings for a given portfolio."""
        return self.get_all_bookings(portfolio_id=portfolio_id)

    def delete_booking(self, booking_id: int) -> bool:
        """Delete a booking by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            conn.commit()
            return cursor.rowcount > 0

    # CRUD Operations for Prices
    def create_price(
        self,
        asset_id: int,
        price: float,
        price_date: str,
        price_type: str = "close",
        volume: int = None,
        source: str = None,
    ) -> int:
        """Create a new price record."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO prices (asset_id, price, price_date, price_type, volume, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (asset_id, price, price_date, price_type, volume, source),
            )
            conn.commit()
            return cursor.lastrowid

    def get_price(
        self, asset_id: int, price_date: str, price_type: str = "close"
    ) -> Optional[Dict]:
        """Get price for specific asset and date."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.*, a.symbol, a.name
                FROM prices p
                JOIN assets a ON p.asset_id = a.id
                WHERE p.asset_id = ? AND p.price_date = ? AND p.price_type = ?
            """,
                (asset_id, price_date, price_type),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_price_history(
        self,
        asset_id: int,
        start_date: str = None,
        end_date: str = None,
        price_type: str = "close",
    ) -> List[Dict]:
        """Get price history for an asset."""
        with self.get_connection() as conn:
            query = """
                SELECT p.*, a.symbol, a.name
                FROM prices p
                JOIN assets a ON p.asset_id = a.id
                WHERE p.asset_id = ? AND p.price_type = ?
            """
            params = [asset_id, price_type]

            if start_date:
                query += " AND p.price_date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND p.price_date <= ?"
                params.append(end_date)

            query += " ORDER BY p.price_date DESC"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_price(
        self, asset_id: int, price_type: str = "close"
    ) -> Optional[Dict]:
        """Get latest price for an asset."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT p.*, a.symbol, a.name
                FROM prices p
                JOIN assets a ON p.asset_id = a.id
                WHERE p.asset_id = ? AND p.price_type = ?
                ORDER BY p.price_date DESC
                LIMIT 1
            """,
                (asset_id, price_type),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_price(
        self, asset_id: int, price_date: str, price_type: str = "close"
    ) -> bool:
        """Delete price record."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM prices
                WHERE asset_id = ? AND price_date = ? AND price_type = ?
            """,
                (asset_id, price_date, price_type),
            )
            conn.commit()
            return cursor.rowcount > 0

    def insert_price_record(
        self,
        symbol: str,
        price: float,
        fetched_ts: datetime,
        source: str = "yfinance",
        price_type: str = "close",
        price_date: Optional[str] = None,
    ) -> int:
        """
        Insert price record using symbol instead of asset_id.

        This adapter function accepts a symbol and resolves it to an asset_id,
        then inserts the price record with proper timestamp handling.

        Args:
            symbol: Asset symbol to insert price for
            price: Price value
            fetched_ts: Timestamp when price was fetched
            source: Data source (default: "yfinance")
            price_type: Type of price (default: "close")
            price_date: Date for the price (default: today)

        Returns:
            int: ID of the created price record

        Raises:
            ValueError: If asset with symbol doesn't exist
        """
        from datetime import date

        # Use today's date if not provided
        if price_date is None:
            price_date = date.today().isoformat()

        # Get asset by symbol
        asset = self.get_asset_by_symbol(symbol)
        if not asset:
            raise ValueError(f"Asset with symbol '{symbol}' not found")

        # Convert datetime to string for storage if needed
        fetched_ts_str = (
            fetched_ts.isoformat()
            if isinstance(fetched_ts, datetime)
            else str(fetched_ts)
        )

        # Use existing transaction pattern from create_price
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO prices (asset_id, price, price_date, price_type, volume, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    asset["id"],
                    price,
                    price_date,
                    price_type,
                    None,
                    source,
                    fetched_ts_str,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    # CRUD Operations for Portfolio Configuration
    def set_config(
        self,
        config_key: str,
        config_value: Any,
        config_type: str = "string",
        description: str = None,
    ) -> int:
        """Set configuration value."""
        # Convert value to string based on type
        if config_type == "json":
            import json

            str_value = json.dumps(config_value)
        else:
            str_value = str(config_value)

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO portfolio_config (config_key, config_value, config_type, description)
                VALUES (?, ?, ?, ?)
            """,
                (config_key, str_value, config_type, description),
            )
            conn.commit()
            return cursor.lastrowid

    def get_config(self, config_key: str) -> Any:
        """Get configuration value."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT config_value, config_type FROM portfolio_config WHERE config_key = ?
            """,
                (config_key,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            value, config_type = row

            # Convert string back to appropriate type
            if config_type == "integer":
                return int(value)
            elif config_type == "float":
                return float(value)
            elif config_type == "boolean":
                return value.lower() in ("true", "1", "yes", "on")
            elif config_type == "json":
                import json

                return json.loads(value)
            else:
                return value

    def get_all_config(self) -> Dict[str, Any]:
        """Get all configuration values."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT config_key, config_value, config_type FROM portfolio_config"
            )
            config = {}

            for row in cursor.fetchall():
                key, value, config_type = row

                # Convert string back to appropriate type
                if config_type == "integer":
                    config[key] = int(value)
                elif config_type == "float":
                    config[key] = float(value)
                elif config_type == "boolean":
                    config[key] = value.lower() in ("true", "1", "yes", "on")
                elif config_type == "json":
                    import json

                    config[key] = json.loads(value)
                else:
                    config[key] = value

            return config

    def delete_config(self, config_key: str) -> bool:
        """Delete configuration value."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM portfolio_config WHERE config_key = ?", (config_key,)
            )
            conn.commit()
            return cursor.rowcount > 0

    # Utility methods
    def get_portfolio_summary(self) -> Dict:
        """Get portfolio summary statistics."""
        with self.get_connection() as conn:
            # Get total assets
            cursor = conn.execute("SELECT COUNT(*) FROM assets WHERE is_active = TRUE")
            total_assets = cursor.fetchone()[0]

            # Get total transactions
            cursor = conn.execute("SELECT COUNT(*) FROM transactions")
            total_transactions = cursor.fetchone()[0]

            # Get asset types breakdown
            cursor = conn.execute("""
                SELECT asset_type, COUNT(*) as count
                FROM assets
                WHERE is_active = TRUE
                GROUP BY asset_type
            """)
            asset_types = {row[0]: row[1] for row in cursor.fetchall()}

            return {
                "total_assets": total_assets,
                "total_transactions": total_transactions,
                "asset_types": asset_types,
                "database_version": self._get_database_version(conn),
            }

    # ── Allocation Targets ────────────────────────────────────────────────────

    def get_allocation_targets(self) -> List[Dict]:
        """Return all asset-type allocation targets."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, asset_type, target_pct, updated_at FROM allocation_targets ORDER BY asset_type"
            ).fetchall()
            return [dict(r) for r in rows]

    def set_allocation_target(self, asset_type: str, target_pct: float) -> None:
        """Upsert the target % for an asset type."""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO allocation_targets (asset_type, target_pct)
                   VALUES (?, ?)
                   ON CONFLICT(asset_type) DO UPDATE SET
                       target_pct = excluded.target_pct,
                       updated_at = CURRENT_TIMESTAMP""",
                (asset_type, target_pct),
            )
            conn.commit()

    def delete_allocation_target(self, asset_type: str) -> None:
        """Remove an allocation target."""
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM allocation_targets WHERE asset_type = ?", (asset_type,)
            )
            conn.commit()

    # ── Price Targets ─────────────────────────────────────────────────────────

    def get_price_target(self, asset_id: int) -> Optional[Dict]:
        """Get price target for an asset."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM price_targets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_price_targets(self) -> List[Dict]:
        """Get all price targets with asset symbol."""
        with self.get_connection() as conn:
            rows = conn.execute("""SELECT pt.*, a.symbol, a.name
                   FROM price_targets pt JOIN assets a ON pt.asset_id = a.id
                   ORDER BY a.symbol""").fetchall()
            return [dict(r) for r in rows]

    def upsert_price_target(
        self,
        asset_id: int,
        buy_below: Optional[float] = None,
        sell_above: Optional[float] = None,
        fair_value: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Create or update price targets for an asset."""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO price_targets (asset_id, buy_below, sell_above, fair_value, notes)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(asset_id) DO UPDATE SET
                       buy_below = COALESCE(excluded.buy_below, buy_below),
                       sell_above = COALESCE(excluded.sell_above, sell_above),
                       fair_value = COALESCE(excluded.fair_value, fair_value),
                       notes = COALESCE(excluded.notes, notes),
                       updated_at = CURRENT_TIMESTAMP""",
                (asset_id, buy_below, sell_above, fair_value, notes),
            )
            conn.commit()

    def update_price_target_alert_sent(self, asset_id: int) -> None:
        """Record when an alert was last sent for this asset."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE price_targets SET alert_sent_at = CURRENT_TIMESTAMP WHERE asset_id = ?",
                (asset_id,),
            )
            conn.commit()

    # ── Research Reports ──────────────────────────────────────────────────────

    def get_research_report(self, asset_id: int) -> Optional[Dict]:
        """Get the latest research report for an asset."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_reports WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_research_report(
        self,
        asset_id: int,
        symbol: str,
        fair_value: Optional[float],
        recommendation: str,
        confidence: str,
        summary: str,
        report_json: str,
    ) -> None:
        """Store or refresh a research report."""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO research_reports
                       (asset_id, symbol, fair_value, recommendation, confidence, summary, report_json, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(asset_id) DO UPDATE SET
                       symbol = excluded.symbol,
                       fair_value = excluded.fair_value,
                       recommendation = excluded.recommendation,
                       confidence = excluded.confidence,
                       summary = excluded.summary,
                       report_json = excluded.report_json,
                       generated_at = CURRENT_TIMESTAMP""",
                (
                    asset_id,
                    symbol,
                    fair_value,
                    recommendation,
                    confidence,
                    summary,
                    report_json,
                ),
            )
            conn.commit()

    # ── Portfolio Snapshots ───────────────────────────────────────────────────

    def record_snapshot(
        self, snapshot_date: str, total_value_eur: float, total_cost_eur: float
    ) -> None:
        """Upsert a daily portfolio value snapshot."""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO portfolio_snapshots (snapshot_date, total_value_eur, total_cost_eur)
                   VALUES (?, ?, ?)
                   ON CONFLICT(snapshot_date) DO UPDATE SET
                       total_value_eur = excluded.total_value_eur,
                       total_cost_eur = excluded.total_cost_eur""",
                (snapshot_date, total_value_eur, total_cost_eur),
            )
            conn.commit()

    def get_snapshots(self, limit: int = 730) -> List[Dict]:
        """Return portfolio snapshots ordered by date ascending (default ~2 years)."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT snapshot_date, total_value_eur, total_cost_eur
                   FROM portfolio_snapshots ORDER BY snapshot_date DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # ── Watchlist ─────────────────────────────────────────────────────────────

    def get_watchlist(self) -> List[Dict]:
        """Return all watchlist entries."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM watchlist ORDER BY symbol").fetchall()
            return [dict(r) for r in rows]

    def add_watchlist(
        self,
        symbol: str,
        name: str = None,
        asset_type: str = None,
        buy_below: float = None,
        notes: str = None,
    ) -> int:
        """Add (or update) a watchlist symbol."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO watchlist (symbol, name, asset_type, buy_below, notes)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                       name = COALESCE(excluded.name, name),
                       asset_type = COALESCE(excluded.asset_type, asset_type),
                       buy_below = COALESCE(excluded.buy_below, buy_below),
                       notes = COALESCE(excluded.notes, notes)""",
                (symbol.upper(), name, asset_type, buy_below, notes),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_watchlist(self, symbol: str) -> bool:
        """Remove a symbol from the watchlist."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),)
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_watchlist_alert_sent(self, symbol: str) -> None:
        """Record when a buy alert was last sent for a watchlist symbol."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE watchlist SET alert_sent_at = CURRENT_TIMESTAMP WHERE symbol = ?",
                (symbol.upper(),),
            )
            conn.commit()

    # ── Goals ─────────────────────────────────────────────────────────────────

    def get_goals(self) -> List[Dict]:
        """Return all savings goals."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM goals ORDER BY target_date").fetchall()
            return [dict(r) for r in rows]

    def get_goal(self, goal_id: int) -> Optional[Dict]:
        """Get a goal by ID."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_goal(
        self,
        name: str,
        target_amount_eur: float,
        target_date: str,
        monthly_contribution_eur: float = 0,
        expected_return_pct: float = 7.0,
    ) -> int:
        """Create a savings goal."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO goals
                       (name, target_amount_eur, target_date, monthly_contribution_eur, expected_return_pct)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    name,
                    target_amount_eur,
                    target_date,
                    monthly_contribution_eur,
                    expected_return_pct,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_goal(self, goal_id: int) -> bool:
        """Delete a goal."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
            conn.commit()
            return cursor.rowcount > 0

    def backup_database(self, backup_path: str) -> bool:
        """Create database backup."""
        try:
            import shutil

            shutil.copy2(self.db_path, backup_path)
            self.logger.info(f"Database backed up to {backup_path}")
            return True
        except Exception as e:
            self.logger.error(f"Database backup failed: {e}")
            return False


# Convenience function to get database instance
def get_database(db_path: str = "portfolio.db") -> Database:
    """Get database instance."""
    return Database(db_path)


if __name__ == "__main__":
    # Example usage
    db = get_database()

    # Create sample asset
    asset_id = db.create_asset(
        symbol="AAPL",
        name="Apple Inc.",
        asset_type="stock",
        exchange="NASDAQ",
        sector="Technology",
    )

    # Create sample transaction
    transaction_id = db.create_transaction(
        asset_id=asset_id,
        transaction_type="buy",
        quantity=100,
        price=150.00,
        total_amount=15000.00,
        transaction_date="2024-01-15",
    )

    # Create sample price
    price_id = db.create_price(asset_id=asset_id, price=155.00, price_date="2024-01-15")

    # Set configuration
    db.set_config("default_currency", "USD", "string", "Default portfolio currency")
    db.set_config("risk_tolerance", 0.7, "float", "Risk tolerance level (0-1)")

    # Get portfolio summary
    summary = db.get_portfolio_summary()
    print(f"Portfolio Summary: {summary}")
