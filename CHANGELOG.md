# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-06

### Added
- **AI chat with real portfolio access**: the assistant reads your holdings, performance and recent transactions from the database and renders Markdown answers (dark-mode aware).
- **Research workbench**: ticker autocomplete (name + exchange), a "Your position" panel (cost basis, unrealised/realised P/L, a sell calculator, average-cost-vs-price chart), Yahoo fundamentals with explicit source labels, LLM valuation, and a downloadable Markdown research report per ticker (`/research/{symbol}/report`).
- **Analytics tabs**: split into lazy-loaded tabs (Performance & Net Worth, Dividends, Gain/Loss, Tax, Risk & Diversification, Fees) so only the active tab loads.
  - **Gain/Loss leaderboard**: top unrealised winners/losers by € and %, plus realised gains/losses per holding.
  - **Dividend forward income & calendar**: projected annual income per holding + income-by-calendar-month.
  - **Detailed tax report**: per-lot FIFO realised gains + dividend gross/withholding, with CSV export for IRPF filing.
- **Dashboard alerts banner**: price targets crossed and watchlist buy zones, in-app.
- **Index fund asset type** (`index`) distinct from ETFs; cash deposits/withdrawals shown inline in Transactions.
- **Per-user settings**: default currency, default broker, holdings sort, hide-tiny-positions, plus in-app change password (`/auth/change-password-key`).
- **New pages**: Help (guides + glossary), What's New, About, and a curated Resources page; grouped, collapsible sidebar.

### Changed
- **yfinance caching** (`kv_cache`, DB schema v14): sector/country, fundamentals, news and benchmark history are cached, cutting the diversification load from ~25s to sub-second when warm.
- Blocking-yfinance endpoints run as sync handlers (FastAPI threadpool) so a slow Yahoo call no longer freezes the event loop.
- Real dark mode across the app (keyed off `data-bs-theme`), themed sidebar and chat.

## [1.3.0] - 2024-08-27

### Added
- **MAJOR: Advanced Transaction Filtering System**: Revolutionary enhancement to `list-transactions` command
  - 8 comprehensive filter options with intuitive syntax
  - `--symbol`: Asset symbol with wildcard support (`BTC*`, `*EUR`)
  - `--name`: Asset name with wildcard support (`*Crypto*`, `Apple*`)  
  - `--price`: Unit price filtering (`>100`, `<500`, `100-500`, `=250`)
  - `--total`: Total amount filtering (`>1000`, `500-2000`)
  - `--quantity`: Quantity filtering (`>0.1`, `<10`)
  - `--from-date/--to-date`: Date range filtering (`2025-06-01` to `2025-08-01`)
  - `--type`: Transaction type filtering (`buy`, `sell`)
  - Multi-filter combinations for sophisticated queries

### Enhanced
- **Professional-Grade Pattern Matching**: Wildcard support with `*` and `?` characters
- **Smart Numeric Parsing**: Comparison operators (`>`, `<`, `>=`, `<=`, `=`) and ranges (`100-500`)
- **Date Range Validation**: Full ISO date parsing with proper error handling
- **User Experience**: Clear filter feedback and transaction count summaries
- **Backward Compatibility**: Existing `--symbol` filter preserved and enhanced

### Technical
- Added `TransactionFilterEngine` class with comprehensive filtering logic
- Enhanced CLI argument parser with 7 new filter arguments  
- Implemented regex-based wildcard pattern matching
- Added numeric range parsing with multiple comparison formats
- Complete test suite with real-world filtering scenarios

### Examples
```bash
# Find all crypto transactions
portf list-transactions --name "*Crypto*"

# Find expensive BTC transactions  
portf list-transactions --symbol "BTC" --price ">80000"

# Find recent medium-value buy orders
portf list-transactions --from-date 2025-08-01 --total "20-100" --type buy

# Find small quantity transactions
portf list-transactions --quantity "<1" --to-date 2025-07-01
```

### Impact
- Enterprise-level transaction querying capabilities
- Dramatic improvement in data analysis workflows
- Time savings through precise filtering instead of manual scanning
- Professional financial software feature parity

- Migration successfully tested on production data (2 transactions corrected)

### Breaking Changes
- **Data Impact**: Existing Coinbase transaction data will show different (correct) values after migration
- Users should run the migration script on their database to fix historical data
- Portfolio valuations will be more accurate after the fix is applied

### Added
- **Price Update Functionality**: New `update-prices` command for fetching latest asset prices
  - Support for updating all portfolio assets or specific symbols
  - Real-time price display with `--show-values` option
  - Integration with external market data APIs
  - Timestamped price storage for historical tracking
  - Environment configuration with `.env.example` template (yfinance requires no API key)
  - User authentication requirement for price updates in local mode

### Enhanced
- **Market Data Integration**: Improved price tracking with persistent storage
  - Each price update is timestamped and tagged with data source
  - Historical price data retention for better portfolio analysis
  - Enhanced portfolio valuation accuracy with latest market prices

### Technical
- Extended CLI interface with comprehensive price update commands
- Robust error handling for API failures and authentication issues
- Performance optimizations for bulk price updates


## [1.1.0] - 2024-01-XX

### Added
- **User Authentication System**: Complete multi-user support with secure authentication
  - User registration and login functionality
  - Password hashing with salt for security
  - Session management with user context
  - User-specific data isolation for all entities, portfolios, and transactions
- **Database Migration System**: Version-controlled database schema migrations
  - Automatic detection of database version
  - Safe migration from version 2 to version 3
  - Rollback protection and error handling
- **Repair Script**: Standalone database repair utility for existing installations
  - Automatic backup creation before any changes
  - Safe migration of existing data to user-based schema
  - Default admin user creation for immediate access

### Changed
- **BREAKING CHANGE**: Database schema upgraded to version 3 with user authentication
  - Added `user_id` columns to `entities`, `portfolios`, and `transactions` tables
  - All existing data is automatically migrated to reference a default admin user
  - Database structure now supports multi-user isolation

### Fixed
- Database integrity improved with proper foreign key constraints
- User data isolation ensures secure multi-user operations
- Comprehensive error handling and rollback mechanisms

### Migration Instructions

**For existing installations**, the database schema change is **breaking** and requires migration. Follow these steps:

#### Option 1: Automatic Migration (Recommended)
The application will automatically detect older database versions and prompt for migration on first run after upgrade.

#### Option 2: Manual Migration with Repair Script
For more control over the migration process, use the included repair script:

```bash
# Make sure to backup your database first
cp portfolio.db portfolio.db.backup

# Run the repair script (creates automatic backup)
python repair_database.py portfolio.db

# Verify the migration was successful
python -m portf_manager list-portfolios
```

#### What the Migration Does
1. **Backup Creation**: Creates a timestamped backup file (e.g., `portfolio.20240116_143000.bak`)
2. **Schema Updates**: Adds `users` table and `user_id` columns to existing tables
3. **Data Migration**: Creates a default admin user and links all existing data to this user
4. **Index Creation**: Adds performance indexes for new columns
5. **Version Update**: Sets database version to 3

#### Default Admin User
After migration, a default admin user is created with these credentials:
- **Username**: `admin`
- **Email**: `admin@localhost`
- **Password**: Placeholder values (update as needed)

#### Verification
After migration, verify everything works correctly:
```bash
# Check database version
python -c "import sqlite3; conn = sqlite3.connect('portfolio.db'); print('Version:', conn.execute('PRAGMA user_version').fetchone()[0])"

# Test basic operations
python -m portf_manager list-portfolios
python -m portf_manager list-assets
```

#### Troubleshooting
- **Permission Errors**: Ensure write permissions to database file and directory
- **Migration Failures**: Check the backup file (`.bak`) and error messages
- **Data Integrity**: Run verification commands after migration
- **Rollback**: If needed, restore from the automatically created backup file

For detailed migration documentation, see `README_REPAIR.md`.

## [1.0.0] - 2024-01-XX

### Added
- Initial release of Portfolio Manager
- Complete portfolio management system with CLI and API
- Asset tracking and transaction management
- Multi-portfolio support
- Market data integration with Yahoo Finance
- Currency conversion support
- GICS sector classification
- Comprehensive test suite
- LLM-powered transaction parsing
- Import/export functionality

## [Latest] - Transaction Management Enhancement

### Added
- **New CLI Commands**:
  - `delete-transaction <id>` - Delete a transaction with confirmation prompt
  - `update-transaction <id> [options]` - Update transaction fields (quantity, price, date, type, description)

### Enhanced
- **Transaction List Output**: Added "Name" column showing asset names (truncated to 24 chars)
- **Server API**: Added DELETE and PUT endpoints for transaction management
- **HTTP Client**: Added `delete_transaction()` and `update_transaction()` methods
- **Error Handling**: Comprehensive validation and user-friendly error messages

### Features
- **Automatic Calculations**: Total amount recalculated when quantity or price updated
- **Confirmation Prompts**: Safe deletion with transaction details preview
- **Multi-field Updates**: Update multiple transaction fields in single command
- **Cross-mode Support**: Works in both local SQLite and server modes
- **Input Validation**: Transaction type validation, date format checking
- **User Security**: Transaction ownership verification in local mode

### Examples
```bash
# Update transaction quantity
python -m portf_manager update-transaction 17 --quantity 120

# Update multiple fields
python -m portf_manager update-transaction 17 --quantity 100 --price 148.50 --type sell

# Delete with confirmation
python -m portf_manager delete-transaction 17
```

### Documentation
- Added comprehensive transaction management guide: `TRANSACTION_MANAGEMENT.md`
- Updated API documentation with new endpoints
- Enhanced CLI help text and examples


### Asset Management Enhancement

#### Added
- **New CLI Commands**:
  - `update-asset <id> [options]` - Update asset information (name, exchange, currency, sector, description, active status)
  - `delete-asset <id>` - Delete asset with soft delete (marks as inactive)

#### Enhanced  
- **Server API Support**: Asset update/delete commands work in both local and server modes
- **HTTP Client**: Added `delete_asset()` method to complement existing `update_asset()` 
- **Interactive Console**: Added autocomplete and help text for asset management commands
- **Safe Operations**: Asset deletion uses soft delete to preserve data integrity

#### Features
- **Comprehensive Options**: Update name, exchange, currency, sector, description, and active status
- **Confirmation Prompts**: Safe deletion with asset details preview
- **Data Integrity**: Soft delete design protects transaction history
- **Error Handling**: Asset existence validation and user-friendly error messages
- **Cross-mode Support**: Works identically in local SQLite and server modes

#### Examples
```bash
# Update asset information
python -m portf_manager update-asset 5 --name "Apple Inc." --exchange NASDAQ

# Soft delete asset
python -m portf_manager delete-asset 5
```

#### Documentation
- Enhanced `TRANSACTION_MANAGEMENT.md` with comprehensive asset management guide
- Added asset management examples and best practices

