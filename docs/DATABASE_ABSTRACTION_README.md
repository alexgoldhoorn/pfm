# Database Abstraction Layer for Portfolio Management

This document describes the database abstraction layer that provides seamless support for both SQLite and PostgreSQL databases in the Portfolio Management system.

## Overview

The database abstraction layer consists of several components that work together to provide a unified interface for database operations:

- **SQLAlchemy ORM Models** (`portf_manager/models_sqlalchemy.py`): Database models that mirror the existing SQLite schema
- **PostgreSQL Database Adapter** (`portf_manager/database_pg.py`): PostgreSQL implementation of the database interface
- **Database Factory** (`portf_manager/database_factory.py`): Automatic database selection based on environment variables
- **Unified Interface**: Compatible with existing business logic through the `DatabaseAdapter` protocol

## Features

### ✅ Complete Feature Parity
- All existing SQLite functionality is preserved
- Identical API for both SQLite and PostgreSQL
- No changes required to existing business logic
- Seamless migration path

### ✅ Automatic Database Selection
- Uses `DATABASE_URL` environment variable to detect PostgreSQL
- Falls back to SQLite when `DATABASE_URL` is not set
- Supports both `postgresql://` and `postgres://` URL formats

### ✅ Production-Ready PostgreSQL Support
- Connection pooling for better performance
- Proper transaction handling
- Error handling and logging
- Schema migration support

### ✅ Maintained Constraints and Enums
- All CHECK constraints from SQLite preserved
- Proper foreign key relationships
- Enum validation for asset types, transaction types, etc.
- Indexes for optimal performance

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

The new dependencies include:
- `sqlalchemy>=2.0.0`: Modern ORM for PostgreSQL support
- `psycopg2-binary`: PostgreSQL driver for Python

### 2. Using SQLite (Default)

No changes needed! The system will continue to use SQLite by default:

```python
from portf_manager.database_factory import get_database

# Automatically uses SQLite
db = get_database()
```

### 3. Using PostgreSQL

Set the `DATABASE_URL` environment variable:

```bash
export DATABASE_URL="postgresql://username:password@localhost:5432/portfolio_db"
```

The system will automatically detect and use PostgreSQL:

```python
from portf_manager.database_factory import get_database

# Automatically uses PostgreSQL
db = get_database()
```

## Database URL Format

The PostgreSQL URL follows the standard format:

```
postgresql://[username[:password]@]host[:port]/database
```

Examples:
- `postgresql://portf_user:portf_password@localhost:5432/portf_db`
- `postgresql://user@localhost/portfolio`
- `postgres://user:pass@db.example.com:5432/prod_db`

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection URL | None (uses SQLite) |
| `SQLITE_DB_PATH` | SQLite database file path | `portfolio.db` |

## Docker Setup

A Docker Compose configuration is provided for easy PostgreSQL setup:

```yaml
# docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:16
    container_name: portf_postgres
    environment:
      POSTGRES_USER: portf_user
      POSTGRES_PASSWORD: portf_password
      POSTGRES_DB: portf_db
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  postgres_data:
```

Start PostgreSQL:
```bash
docker-compose up -d postgres
```

Set environment variable:
```bash
export DATABASE_URL="postgresql://portf_user:portf_password@localhost:5432/portf_db"
```

## Migration from SQLite to PostgreSQL

### Option 1: Fresh Start
1. Set up PostgreSQL database
2. Set `DATABASE_URL` environment variable
3. Run your application - tables will be created automatically

### Option 2: Data Migration
For migrating existing SQLite data to PostgreSQL:

1. Export data from SQLite:
```python
from portf_manager.database import get_database as get_sqlite_db

# Get all data from SQLite
sqlite_db = get_sqlite_db("portfolio.db")
assets = sqlite_db.get_all_assets()
transactions = sqlite_db.get_all_transactions()
# ... export other data
```

2. Import data to PostgreSQL:
```python
import os
os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/db"
from portf_manager.database_factory import get_database

# Import data to PostgreSQL
pg_db = get_database()
for asset in assets:
    pg_db.create_asset(**asset)
# ... import other data
```

## Code Examples

### Creating Assets and Transactions

```python
from portf_manager.database_factory import get_database

# Works with both SQLite and PostgreSQL
db = get_database()

# Create an asset
asset_id = db.create_asset(
    symbol="AAPL",
    name="Apple Inc.",
    asset_type="stock",
    exchange="NASDAQ",
    currency="USD",
    sector="Technology"
)

# Create a transaction
transaction_id = db.create_transaction(
    asset_id=asset_id,
    transaction_type="buy",
    quantity=100.0,
    price=150.0,
    total_amount=15000.0,
    transaction_date="2024-01-15",
    fees=9.99
)
```

### Using with Domain Models

```python
from portf_manager.models import Asset
from portf_manager.database_factory import get_database

# Set up database adapter
db = get_database()

# Create domain model with database adapter
asset_data = db.get_asset_by_symbol("AAPL")
asset = Asset.from_dict(asset_data, db)

# Use domain model methods
current_price = asset.get_current_price()
position_size = asset.calculate_position_size()
```

## Testing

Run the test suite to verify both implementations:

```bash
# Test SQLite (default)
python test_postgresql.py

# Test PostgreSQL (set TEST_DATABASE_URL)
export TEST_DATABASE_URL="postgresql://user:pass@localhost:5432/test_db"
python test_postgresql.py
```

## Performance Considerations

### SQLite
- Single-file database
- No network overhead
- Limited concurrent writes
- Good for single-user applications

### PostgreSQL
- Full-featured database server
- Better concurrent access
- Advanced features (JSON, full-text search, etc.)
- Better for multi-user applications

## Schema Compatibility

The PostgreSQL schema is designed to be fully compatible with the existing SQLite schema:

| Table | SQLite | PostgreSQL | Notes |
|-------|--------|------------|-------|
| users | ✅ | ✅ | User authentication |
| entities | ✅ | ✅ | Brokers, banks, platforms |
| portfolios | ✅ | ✅ | Portfolio organization |
| assets | ✅ | ✅ | Stocks, bonds, crypto, etc. |
| transactions | ✅ | ✅ | Buy/sell/dividend transactions |
| prices | ✅ | ✅ | Historical price data |
| portfolio_config | ✅ | ✅ | Application configuration |

## Troubleshooting

### Common Issues

1. **Connection refused**
   - Ensure PostgreSQL is running
   - Check host and port in DATABASE_URL
   - Verify firewall settings

2. **Authentication failed**
   - Check username and password in DATABASE_URL
   - Ensure user has database permissions

3. **Database does not exist**
   - Create the database manually:
   ```sql
   CREATE DATABASE portfolio_db;
   ```

4. **Module not found errors**
   - Install dependencies: `pip install -r requirements.txt`
   - Check Python path

### Debug Mode

Enable SQL logging for debugging:

```python
# In database_pg.py, set echo=True
self.engine = create_engine(
    database_url,
    echo=True,  # Shows all SQL queries
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Business Logic                           │
│  (Asset, Transaction, Portfolio domain models)             │
└─────────────────────────────────────────────────────────────┘
                                │
                                │ DatabaseAdapter Protocol
                                │
┌─────────────────────────────────────────────────────────────┐
│                Database Factory                             │
│            (database_factory.py)                           │
└─────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
┌─────────────────────────────┐  ┌─────────────────────────────┐
│     SQLite Database         │  │   PostgreSQL Database       │
│      (database.py)          │  │    (database_pg.py)         │
│                             │  │                             │
│  • File-based               │  │  • Server-based             │
│  • Single user              │  │  • Multi-user               │
│  • No network               │  │  • Connection pooling       │
│  • Simple setup             │  │  • Advanced features        │
└─────────────────────────────┘  └─────────────────────────────┘
```

## Contributing

When adding new database features:

1. **Update both implementations**: Add the method to both `database.py` and `database_pg.py`
2. **Update the factory**: Add compatibility functions to `database_factory.py`
3. **Update tests**: Add test cases to `test_postgresql.py`
4. **Update documentation**: Update this README

## Future Enhancements

- [ ] Database backup/restore for PostgreSQL
- [ ] Connection pooling configuration
- [ ] Read replicas support
- [ ] Database sharding
- [ ] Advanced PostgreSQL features (JSON columns, full-text search)
- [ ] Async database operations
- [ ] Database migration tools

## Support

For issues or questions:
1. Check the troubleshooting section
2. Run the test suite to verify your setup
3. Check the logs for error messages
4. Review the PostgreSQL server logs

The database abstraction layer is designed to be robust and maintainable, providing a solid foundation for the Portfolio Management system to scale from single-user SQLite databases to multi-user PostgreSQL deployments.
