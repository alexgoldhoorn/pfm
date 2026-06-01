"""
PostgreSQL Database Adapter for Portfolio Management

This module provides PostgreSQL database functionality using SQLAlchemy ORM,
implementing the same DatabaseAdapter protocol as the existing SQLite implementation.
It provides seamless compatibility with existing business logic.
"""

import os
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
import json

from .models_sqlalchemy import (
    Base,
    User,
    Entity,
    Portfolio,
    Asset,
    Transaction,
    Price,
    PortfolioConfig,
    DatabaseVersion,
)

# Database version for migration tracking
DATABASE_VERSION = 3


class DatabaseError(Exception):
    """Custom exception for database-related errors."""


class PostgreSQLDatabase:
    """
    PostgreSQL database manager for portfolio management using SQLAlchemy.

    Provides connection management, schema creation, migrations,
    and CRUD operations for all portfolio-related tables.

    Implements the DatabaseAdapter protocol for use with domain models.
    """

    def __init__(self, database_url: str = None):
        """
        Initialize PostgreSQL database manager.

        Args:
            database_url: PostgreSQL connection URL. If None, uses DATABASE_URL env var.
        """
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable is required")

        self.database_url = database_url
        self.logger = logging.getLogger(__name__)

        # Create SQLAlchemy engine
        self.engine = create_engine(
            database_url,
            echo=False,  # Set to True for SQL debugging
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,  # Verify connections before use
        )

        # Create session factory
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )

        # Initialize database
        self._initialize_database()

    def _initialize_database(self):
        """Initialize database with schema and version tracking."""
        try:
            # Create all tables
            Base.metadata.create_all(bind=self.engine)

            with self.get_session() as session:
                # Check current version
                current_version = self._get_database_version(session)

                if current_version == 0:
                    # Fresh database - set version
                    self._set_database_version(session, DATABASE_VERSION)
                    self.logger.info(
                        f"Created new database with version {DATABASE_VERSION}"
                    )
                elif current_version < DATABASE_VERSION:
                    # Run migrations if needed
                    self._run_migrations(session, current_version)
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
    def get_session(self) -> Session:
        """
        Context manager for database sessions.

        Yields:
            Session: SQLAlchemy session
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            self.logger.error(f"Database session error: {e}")
            raise DatabaseError(f"Database session failed: {e}")
        finally:
            session.close()

    def _get_database_version(self, session: Session) -> int:
        """Get current database version."""
        try:
            result = (
                session.query(DatabaseVersion)
                .order_by(DatabaseVersion.version.desc())
                .first()
            )
            return result.version if result else 0
        except SQLAlchemyError:
            # Table doesn't exist yet
            return 0

    def _set_database_version(self, session: Session, version: int):
        """Set database version."""
        db_version = DatabaseVersion(version=version)
        session.add(db_version)
        session.commit()

    def _run_migrations(self, session: Session, current_version: int):
        """Run database migrations from current version to latest."""
        # For PostgreSQL, migrations are handled by SQLAlchemy's create_all
        # which creates tables and constraints that don't exist
        # Additional migration logic can be added here if needed
        self._set_database_version(session, DATABASE_VERSION)

    def _orm_to_dict(self, orm_obj) -> Dict:
        """Convert SQLAlchemy ORM object to dictionary with proper date handling."""
        from datetime import date, datetime

        if orm_obj is None:
            return None
        result = {}
        for column in orm_obj.__table__.columns:
            value = getattr(orm_obj, column.name)
            # Convert date objects to strings for proper serialization
            if isinstance(value, (date, datetime)):
                result[column.name] = (
                    value.isoformat() if hasattr(value, "isoformat") else str(value)
                )
            else:
                result[column.name] = value
        return result

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
        with self.get_session() as session:
            user = User(
                username=username,
                email=email,
                password_hash=password_hash,
                salt=salt,
                full_name=full_name,
            )
            session.add(user)
            session.flush()
            return user.id

    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID."""
        with self.get_session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            return self._orm_to_dict(user)

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username."""
        with self.get_session() as session:
            user = session.query(User).filter(User.username == username).first()
            return self._orm_to_dict(user)

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user by email."""
        with self.get_session() as session:
            user = session.query(User).filter(User.email == email).first()
            return self._orm_to_dict(user)

    def update_user_password(self, user_id: int, password_hash: str, salt: str) -> bool:
        """Update user password."""
        with self.get_session() as session:
            result = (
                session.query(User)
                .filter(User.id == user_id)
                .update({"password_hash": password_hash, "salt": salt})
            )
            return result > 0

    def update_user_last_login(self, user_id: int) -> bool:
        """Update user's last login timestamp."""
        with self.get_session() as session:
            result = (
                session.query(User)
                .filter(User.id == user_id)
                .update({"last_login": datetime.utcnow()})
            )
            return result > 0

    def update_user(self, user_id: int, **kwargs) -> bool:
        """Update user fields."""
        if not kwargs:
            return False

        valid_fields = {"username", "email", "full_name", "is_active"}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_session() as session:
            result = (
                session.query(User).filter(User.id == user_id).update(update_fields)
            )
            return result > 0

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
        with self.get_session() as session:
            entity = Entity(
                name=name,
                entity_type=entity_type,
                user_id=user_id,
                website=website,
                description=description,
            )
            session.add(entity)
            session.flush()
            return entity.id

    def get_entity(self, entity_id: int) -> Optional[Dict]:
        """Get entity by ID."""
        with self.get_session() as session:
            entity = session.query(Entity).filter(Entity.id == entity_id).first()
            return self._orm_to_dict(entity)

    def get_entity_by_name(self, name: str) -> Optional[Dict]:
        """Get entity by name."""
        with self.get_session() as session:
            entity = session.query(Entity).filter(Entity.name == name).first()
            return self._orm_to_dict(entity)

    def get_all_entities(
        self, active_only: bool = True, user_id: int = None
    ) -> List[Dict]:
        """Get all entities."""
        with self.get_session() as session:
            query = session.query(Entity)

            if active_only:
                query = query.filter(Entity.is_active is True)

            if user_id is not None:
                query = query.filter(Entity.user_id == user_id)

            query = query.order_by(Entity.name)
            entities = query.all()
            return [self._orm_to_dict(entity) for entity in entities]

    def update_entity(self, entity_id: int, **kwargs) -> bool:
        """Update entity fields."""
        if not kwargs:
            return False

        valid_fields = {"name", "entity_type", "website", "description", "is_active"}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_session() as session:
            result = (
                session.query(Entity)
                .filter(Entity.id == entity_id)
                .update(update_fields)
            )
            return result > 0

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
        with self.get_session() as session:
            portfolio = Portfolio(
                name=name,
                base_currency=base_currency,
                entity_id=entity_id,
                description=description,
                user_id=user_id,
            )
            session.add(portfolio)
            session.flush()
            return portfolio.id

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict]:
        """Get portfolio by ID."""
        with self.get_session() as session:
            portfolio = (
                session.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            )
            result = self._orm_to_dict(portfolio)
            if result and portfolio.entity:
                result["entity_name"] = portfolio.entity.name
                result["entity_type"] = portfolio.entity.entity_type
                result["website"] = portfolio.entity.website
            return result

    def get_portfolio_by_name(self, name: str) -> Optional[Dict]:
        """Get portfolio by name."""
        with self.get_session() as session:
            portfolio = session.query(Portfolio).filter(Portfolio.name == name).first()
            result = self._orm_to_dict(portfolio)
            if result and portfolio.entity:
                result["entity_name"] = portfolio.entity.name
                result["entity_type"] = portfolio.entity.entity_type
                result["website"] = portfolio.entity.website
            return result

    def get_all_portfolios(
        self, active_only: bool = True, user_id: int = None
    ) -> List[Dict]:
        """Get all portfolios."""
        with self.get_session() as session:
            query = session.query(Portfolio)

            if active_only:
                query = query.filter(Portfolio.is_active is True)

            if user_id is not None:
                query = query.filter(Portfolio.user_id == user_id)

            query = query.order_by(Portfolio.name)
            portfolios = query.all()

            results = []
            for portfolio in portfolios:
                result = self._orm_to_dict(portfolio)
                if portfolio.entity:
                    result["entity_name"] = portfolio.entity.name
                    result["entity_type"] = portfolio.entity.entity_type
                    result["website"] = portfolio.entity.website
                results.append(result)

            return results

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

        with self.get_session() as session:
            result = (
                session.query(Portfolio)
                .filter(Portfolio.id == portfolio_id)
                .update(update_fields)
            )
            return result > 0

    def delete_portfolio(self, portfolio_id: int) -> bool:
        """Delete portfolio (soft delete by setting is_active = False)."""
        return self.update_portfolio(portfolio_id, is_active=False)

    def get_transactions_by_portfolio(self, portfolio_id: int) -> List[Dict]:
        """Get all transactions for a portfolio."""
        with self.get_session() as session:
            transactions = (
                session.query(Transaction)
                .join(Asset)
                .filter(Transaction.portfolio_id == portfolio_id)
                .order_by(Transaction.transaction_date.desc())
                .all()
            )

            results = []
            for transaction in transactions:
                result = self._orm_to_dict(transaction)
                result["symbol"] = transaction.asset.symbol
                result["name"] = transaction.asset.name
                # Include currency field from associated asset
                result["currency"] = transaction.asset.currency
                results.append(result)

            return results

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
        """Create a new asset."""
        with self.get_session() as session:
            asset = Asset(
                symbol=symbol,
                name=name,
                asset_type=asset_type,
                exchange=exchange,
                currency=currency,
                sector=sector,
                description=description,
            )
            session.add(asset)
            session.flush()
            return asset.id

    def get_asset(self, asset_id: int) -> Optional[Dict]:
        """Get asset by ID."""
        with self.get_session() as session:
            asset = session.query(Asset).filter(Asset.id == asset_id).first()
            return self._orm_to_dict(asset)

    def get_asset_by_symbol(self, symbol: str) -> Optional[Dict]:
        """Get asset by symbol."""
        with self.get_session() as session:
            asset = session.query(Asset).filter(Asset.symbol == symbol).first()
            return self._orm_to_dict(asset)

    def get_all_assets(self, active_only: bool = True) -> List[Dict]:
        """Get all assets."""
        with self.get_session() as session:
            query = session.query(Asset)

            if active_only:
                query = query.filter(Asset.is_active is True)

            query = query.order_by(Asset.symbol)
            assets = query.all()
            return [self._orm_to_dict(asset) for asset in assets]

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

        with self.get_session() as session:
            result = (
                session.query(Asset).filter(Asset.id == asset_id).update(update_fields)
            )
            return result > 0

    def delete_asset(self, asset_id: int) -> bool:
        """Delete asset (soft delete by setting is_active = False)."""
        return self.update_asset(asset_id, is_active=False)

    # CRUD Operations for Transactions
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
        with self.get_session() as session:
            transaction = Transaction(
                asset_id=asset_id,
                portfolio_id=portfolio_id,
                transaction_type=transaction_type,
                quantity=quantity,
                price=price,
                total_amount=total_amount,
                fees=fees,
                tax=tax,
                currency=currency,
                transaction_date=transaction_date,
                description=description,
                user_id=user_id,
            )
            session.add(transaction)
            session.flush()
            return transaction.id

    def get_transaction(self, transaction_id: int) -> Optional[Dict]:
        """Get transaction by ID."""
        with self.get_session() as session:
            transaction = (
                session.query(Transaction)
                .join(Asset)
                .filter(Transaction.id == transaction_id)
                .first()
            )
            if transaction:
                result = self._orm_to_dict(transaction)
                result["symbol"] = transaction.asset.symbol
                result["name"] = transaction.asset.name
                # Include currency field from associated asset
                result["currency"] = transaction.asset.currency
                return result
            return None

    def get_transactions_by_asset(self, asset_id: int) -> List[Dict]:
        """Get all transactions for an asset."""
        with self.get_session() as session:
            transactions = (
                session.query(Transaction)
                .join(Asset)
                .filter(Transaction.asset_id == asset_id)
                .order_by(Transaction.transaction_date.desc())
                .all()
            )

            results = []
            for transaction in transactions:
                result = self._orm_to_dict(transaction)
                result["symbol"] = transaction.asset.symbol
                result["name"] = transaction.asset.name
                result["currency"] = transaction.asset.currency
                results.append(result)

            return results

    def get_all_transactions(
        self, limit: int = None, user_id: int = None
    ) -> List[Dict]:
        """Get all transactions."""
        with self.get_session() as session:
            query = session.query(Transaction).join(Asset)

            if user_id is not None:
                query = query.filter(Transaction.user_id == user_id)

            query = query.order_by(Transaction.transaction_date.desc())

            if limit:
                query = query.limit(limit)

            transactions = query.all()

            results = []
            for transaction in transactions:
                result = self._orm_to_dict(transaction)
                result["symbol"] = transaction.asset.symbol
                result["name"] = transaction.asset.name
                result["currency"] = transaction.asset.currency
                results.append(result)

            return results

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
            "transaction_date",
            "description",
        }
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}

        if not update_fields:
            return False

        with self.get_session() as session:
            result = (
                session.query(Transaction)
                .filter(Transaction.id == transaction_id)
                .update(update_fields)
            )
            return result > 0

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete transaction."""
        with self.get_session() as session:
            result = (
                session.query(Transaction)
                .filter(Transaction.id == transaction_id)
                .delete()
            )
            return result > 0

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
        with self.get_session() as session:
            # Use merge to handle INSERT OR REPLACE behavior
            price_obj = Price(
                asset_id=asset_id,
                price=price,
                price_date=price_date,
                price_type=price_type,
                volume=volume,
                source=source,
            )
            session.merge(price_obj)
            session.flush()
            return price_obj.id

    def get_price(
        self, asset_id: int, price_date: str, price_type: str = "close"
    ) -> Optional[Dict]:
        """Get price for specific asset and date."""
        with self.get_session() as session:
            price = (
                session.query(Price)
                .join(Asset)
                .filter(
                    Price.asset_id == asset_id,
                    Price.price_date == price_date,
                    Price.price_type == price_type,
                )
                .first()
            )
            if price:
                result = self._orm_to_dict(price)
                result["symbol"] = price.asset.symbol
                result["name"] = price.asset.name
                return result
            return None

    def get_price_history(
        self,
        asset_id: int,
        start_date: str = None,
        end_date: str = None,
        price_type: str = "close",
    ) -> List[Dict]:
        """Get price history for an asset."""
        with self.get_session() as session:
            query = (
                session.query(Price)
                .join(Asset)
                .filter(
                    Price.asset_id == asset_id,
                    Price.price_type == price_type,
                )
            )

            if start_date:
                query = query.filter(Price.price_date >= start_date)
            if end_date:
                query = query.filter(Price.price_date <= end_date)

            query = query.order_by(Price.price_date.desc())
            prices = query.all()

            results = []
            for price in prices:
                result = self._orm_to_dict(price)
                result["symbol"] = price.asset.symbol
                result["name"] = price.asset.name
                results.append(result)

            return results

    def get_latest_price(
        self, asset_id: int, price_type: str = "close"
    ) -> Optional[Dict]:
        """Get latest price for an asset."""
        with self.get_session() as session:
            price = (
                session.query(Price)
                .join(Asset)
                .filter(
                    Price.asset_id == asset_id,
                    Price.price_type == price_type,
                )
                .order_by(Price.price_date.desc())
                .first()
            )
            if price:
                result = self._orm_to_dict(price)
                result["symbol"] = price.asset.symbol
                result["name"] = price.asset.name
                return result
            return None

    def delete_price(
        self, asset_id: int, price_date: str, price_type: str = "close"
    ) -> bool:
        """Delete price record."""
        with self.get_session() as session:
            result = (
                session.query(Price)
                .filter(
                    Price.asset_id == asset_id,
                    Price.price_date == price_date,
                    Price.price_type == price_type,
                )
                .delete()
            )
            return result > 0

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

        # Use existing transaction pattern from create_price with SQLAlchemy ORM
        with self.get_session() as session:
            price_record = Price(
                asset_id=asset["id"],
                price=price,
                price_date=price_date,
                price_type=price_type,
                source=source,
                created_at=fetched_ts,
            )

            # Handle conflicts using merge (upsert)
            existing = (
                session.query(Price)
                .filter(
                    Price.asset_id == asset["id"],
                    Price.price_date == price_date,
                    Price.price_type == price_type,
                )
                .first()
            )

            if existing:
                existing.price = price
                existing.source = source
                existing.created_at = fetched_ts
                session.commit()
                return existing.id
            else:
                session.add(price_record)
                session.commit()
                return price_record.id

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
            str_value = json.dumps(config_value)
        else:
            str_value = str(config_value)

        with self.get_session() as session:
            config = PortfolioConfig(
                config_key=config_key,
                config_value=str_value,
                config_type=config_type,
                description=description,
            )
            session.merge(config)
            session.flush()
            return config.id

    def get_config(self, config_key: str) -> Any:
        """Get configuration value."""
        with self.get_session() as session:
            config = (
                session.query(PortfolioConfig)
                .filter(PortfolioConfig.config_key == config_key)
                .first()
            )

            if not config:
                return None

            value = config.config_value
            config_type = config.config_type

            # Convert string back to appropriate type
            if config_type == "integer":
                return int(value)
            elif config_type == "float":
                return float(value)
            elif config_type == "boolean":
                return value.lower() in ("true", "1", "yes", "on")
            elif config_type == "json":
                return json.loads(value)
            else:
                return value

    def get_all_config(self) -> Dict[str, Any]:
        """Get all configuration values."""
        with self.get_session() as session:
            configs = session.query(PortfolioConfig).all()

            result = {}
            for config in configs:
                key = config.config_key
                value = config.config_value
                config_type = config.config_type

                # Convert string back to appropriate type
                if config_type == "integer":
                    result[key] = int(value)
                elif config_type == "float":
                    result[key] = float(value)
                elif config_type == "boolean":
                    result[key] = value.lower() in ("true", "1", "yes", "on")
                elif config_type == "json":
                    result[key] = json.loads(value)
                else:
                    result[key] = value

            return result

    def delete_config(self, config_key: str) -> bool:
        """Delete configuration value."""
        with self.get_session() as session:
            result = (
                session.query(PortfolioConfig)
                .filter(PortfolioConfig.config_key == config_key)
                .delete()
            )
            return result > 0

    # Utility methods
    def get_portfolio_summary(self) -> Dict:
        """Get portfolio summary statistics."""
        with self.get_session() as session:
            # Get total assets
            total_assets = session.query(Asset).filter(Asset.is_active is True).count()

            # Get total transactions
            total_transactions = session.query(Transaction).count()

            # Get asset types breakdown
            from sqlalchemy import func

            asset_types = {}
            results = (
                session.query(Asset.asset_type, func.count(Asset.id))
                .filter(Asset.is_active is True)
                .group_by(Asset.asset_type)
                .all()
            )

            for asset_type, count in results:
                asset_types[asset_type] = count

            # Get database version
            current_version = self._get_database_version(session)

            return {
                "total_assets": total_assets,
                "total_transactions": total_transactions,
                "asset_types": asset_types,
                "database_version": current_version,
            }

    def backup_database(self, backup_path: str) -> bool:
        """Create database backup (PostgreSQL-specific implementation would be needed)."""
        # This would require pg_dump or similar PostgreSQL-specific tools
        # For now, just log that backup is not implemented
        self.logger.warning("Database backup not implemented for PostgreSQL adapter")
        return False


# Convenience function to get database instance
def get_database(database_url: str = None) -> PostgreSQLDatabase:
    """Get PostgreSQL database instance."""
    return PostgreSQLDatabase(database_url)
