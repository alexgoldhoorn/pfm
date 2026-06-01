"""
SQLAlchemy ORM Models for Portfolio Management

This module defines SQLAlchemy models that mirror the existing database structure,
providing PostgreSQL support while maintaining compatibility with the existing
domain models and business logic.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Date,
    Float,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    Index,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

Base = declarative_base()


class DatabaseVersion(Base):
    """Database version tracking table."""

    __tablename__ = "database_version"

    version = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=func.current_timestamp())


class User(Base):
    """User authentication table."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), nullable=False, unique=True)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    salt = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(
        DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    entities = relationship(
        "Entity", back_populates="user", cascade="all, delete-orphan"
    )
    portfolios = relationship(
        "Portfolio", back_populates="user", cascade="all, delete-orphan"
    )
    transactions = relationship(
        "Transaction", back_populates="user", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_users_username", "username"),
        Index("idx_users_email", "email"),
    )


class Entity(Base):
    """Entity table for brokers, banks, and other financial institutions."""

    __tablename__ = "entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    entity_type = Column(String(50), nullable=False)
    website = Column(String(255))
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(
        DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User", back_populates="entities")
    portfolios = relationship("Portfolio", back_populates="entity")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('broker', 'bank', 'platform', 'other')",
            name="check_entity_type",
        ),
        UniqueConstraint("user_id", "name", name="unique_user_entity_name"),
        Index("idx_entities_name", "name"),
        Index("idx_entities_type", "entity_type"),
        Index("idx_entities_user_id", "user_id"),
    )


class Portfolio(Base):
    """Portfolio table for organizing assets and transactions."""

    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    base_currency = Column(String(10), nullable=False, default="USD")
    entity_id = Column(Integer, ForeignKey("entities.id", ondelete="SET NULL"))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(
        DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    entity = relationship("Entity", back_populates="portfolios")
    user = relationship("User", back_populates="portfolios")
    transactions = relationship("Transaction", back_populates="portfolio")

    # Indexes
    __table_args__ = (
        Index("idx_portfolios_name", "name"),
        Index("idx_portfolios_entity_id", "entity_id"),
        Index("idx_portfolios_user_id", "user_id"),
    )


class Asset(Base):
    """Asset table for stocks, bonds, crypto, etc."""

    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    asset_type = Column(String(50), nullable=False)
    exchange = Column(String(100))
    currency = Column(String(10), nullable=False, default="USD")
    sector = Column(String(100))
    description = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(
        DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    transactions = relationship(
        "Transaction", back_populates="asset", cascade="all, delete-orphan"
    )
    prices = relationship("Price", back_populates="asset", cascade="all, delete-orphan")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('stock', 'bond', 'crypto', 'etf', 'mutual_fund', 'commodity', 'cash')",
            name="check_asset_type",
        ),
        Index("idx_assets_symbol", "symbol"),
        Index("idx_assets_type", "asset_type"),
    )


class Transaction(Base):
    """Transaction table for buy/sell/dividend transactions."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="SET NULL"))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    transaction_type = Column(String(50), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    fees = Column(Float, default=0)
    transaction_date = Column(Date, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(
        DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    asset = relationship("Asset", back_populates="transactions")
    portfolio = relationship("Portfolio", back_populates="transactions")
    user = relationship("User", back_populates="transactions")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('buy', 'sell', 'dividend', 'split', 'transfer_in', 'transfer_out')",
            name="check_transaction_type",
        ),
        Index("idx_transactions_asset_id", "asset_id"),
        Index("idx_transactions_portfolio_id", "portfolio_id"),
        Index("idx_transactions_date", "transaction_date"),
        Index("idx_transactions_type", "transaction_type"),
        Index("idx_transactions_user_id", "user_id"),
    )


class Price(Base):
    """Price table for historical asset prices."""

    __tablename__ = "prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    price = Column(Float, nullable=False)
    price_date = Column(Date, nullable=False)
    price_type = Column(String(50), nullable=False, default="close")
    volume = Column(Integer)
    source = Column(String(100))
    created_at = Column(DateTime, default=func.current_timestamp())

    # Relationships
    asset = relationship("Asset", back_populates="prices")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "price_type IN ('open', 'high', 'low', 'close', 'adjusted_close')",
            name="check_price_type",
        ),
        UniqueConstraint(
            "asset_id", "price_date", "price_type", name="unique_asset_price"
        ),
        Index("idx_prices_asset_id", "asset_id"),
        Index("idx_prices_date", "price_date"),
    )


class PortfolioConfig(Base):
    """Portfolio configuration table for application settings."""

    __tablename__ = "portfolio_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(255), nullable=False, unique=True)
    config_value = Column(Text, nullable=False)
    config_type = Column(String(50), nullable=False, default="string")
    description = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(
        DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "config_type IN ('string', 'integer', 'float', 'boolean', 'json')",
            name="check_config_type",
        ),
        Index("idx_portfolio_config_key", "config_key"),
    )


class ApiKey(Base):
    """API keys table for application authentication."""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_name = Column(String(255), nullable=False)
    key_hash = Column(String(255), nullable=False, unique=True)
    key_prefix = Column(String(20), nullable=False)  # First 8 chars for identification
    is_active = Column(Boolean, nullable=False, default=True)
    description = Column(Text)
    last_used = Column(DateTime)
    created_at = Column(DateTime, default=func.current_timestamp())
    expires_at = Column(DateTime)  # Optional expiration

    # Indexes
    __table_args__ = (
        Index("idx_api_keys_key_hash", "key_hash"),
        Index("idx_api_keys_prefix", "key_prefix"),
        Index("idx_api_keys_active", "is_active"),
    )
