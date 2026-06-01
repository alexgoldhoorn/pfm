# LLM Import Command Integration Design

## Overview

This document outlines the integration points for adding a new LLM-based import command to the Portfolio Manager CLI. The command will leverage AI to parse transaction data from various formats and create portfolio transactions.

## Current CLI Structure Analysis

### Command Group Organization
The CLI is structured using `argparse` with subparsers for different command categories:
- **Asset Management**: `add-asset`, `remove-asset`, `list-assets`
- **Transaction Management**: `add-transaction`, `list-transactions`
- **Data Import**: `import-csv`
- **Portfolio Management**: `add-portfolio`, `list-portfolios`
- **Entity Management**: `add-entity`, `list-entities`
- **Analysis**: `portfolio-value`, `show-mapping`, `list-sectors`

### Common Helper Functions and Patterns

#### Database Manager Usage
- **Pattern**: `self.db_manager = Database(db_path)` in `__init__`
- **Usage**: All commands use `self.db_manager` for database operations
- **Methods Used**:
  - `get_asset_by_symbol()` - Asset lookup
  - `get_portfolio_by_name()` - Portfolio lookup
  - `create_transaction()` - Transaction creation
  - `create_asset()` - Asset creation
  - `get_all_assets()` - Asset listing

#### Option Decorators and Argument Patterns
- **Required Arguments**: `symbol`, `amount`, `price` for transactions
- **Optional Arguments**: `--portfolio`, `--currency`, `--description`
- **File Arguments**: `csv_file` for import operations
- **Validation**: Asset existence checks before transaction creation

### Database Helpers for Transaction Creation

The `import_csv()` method demonstrates the standard pattern for creating transactions:

```python
# 1. Portfolio Resolution
portfolio_id = None
if portfolio_name:
    portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
    if portfolio_data:
        portfolio_id = portfolio_data["id"]

# 2. Asset Creation/Lookup
asset_data = self.db_manager.get_asset_by_symbol(symbol)
if not asset_data:
    asset_id = self.db_manager.create_asset(...)
    asset_data = self.db_manager.get_asset(asset_id)

# 3. Transaction Creation
transaction_id = self.db_manager.create_transaction(
    asset_id=asset_data["id"],
    transaction_type="buy",
    quantity=shares,
    price=price_per_share,
    total_amount=total_amount,
    transaction_date=transaction_date,
    portfolio_id=portfolio_id,
    description=f"Imported from CSV: {concepto}"
)
```

### Database Layer Integration Points

#### Required Database Methods (from `database.py`)
- `create_transaction()` - Main transaction creation (lines 649-683)
- `get_asset_by_symbol()` - Asset lookup (lines 595-601)
- `create_asset()` - Asset creation if not exists (lines 552-587)
- `get_portfolio_by_name()` - Portfolio lookup (lines 473-487)
- `get_all_assets()` - Asset listing for validation (lines 602-614)

#### Transaction Creation Schema
```python
create_transaction(
    asset_id: int,
    transaction_type: str,  # 'buy', 'sell', 'dividend', 'split', 'transfer_in', 'transfer_out'
    quantity: float,
    price: float,
    total_amount: float,
    transaction_date: str,  # YYYY-MM-DD format
    portfolio_id: int = None,
    fees: float = 0,
    description: str = None,
)
```

## Recommended Integration Points

### 1. CLI Command Placement
**Location**: Add to the import command group alongside `import-csv`

**Command Name**: `import-llm`

**Parser Definition**:
```python
# Import LLM command
import_llm_parser = subparsers.add_parser(
    "import-llm", help="Import transactions using LLM parsing"
)
import_llm_parser.add_argument(
    "input_data",
    help="Transaction data (text, file path, or '-' for stdin)"
)
import_llm_parser.add_argument(
    "--portfolio", help="Portfolio name to import transactions to (optional)"
)
import_llm_parser.add_argument(
    "--format-hint", help="Hint about the data format (optional)"
)
import_llm_parser.add_argument(
    "--dry-run", action="store_true", help="Parse and validate without creating transactions"
)
```

### 2. Method Implementation Location
**Location**: Add `import_llm()` method to `PortfolioManagerCLI` class (after line 617)

**Method Signature**:
```python
def import_llm(self, input_data: str, portfolio_name: str = None, format_hint: str = None, dry_run: bool = False) -> None:
    """Import transactions using LLM parsing."""
```

### 3. Integration with Existing Patterns

#### Portfolio Resolution
Reuse the existing portfolio resolution pattern from `import_csv()` (lines 321-343):
```python
portfolio_id = None
if portfolio_name:
    portfolio_data = self.db_manager.get_portfolio_by_name(portfolio_name)
    if portfolio_data:
        portfolio_id = portfolio_data["id"]
    else:
        print(f"❌ Portfolio '{portfolio_name}' not found. Available portfolios:")
        self.list_portfolios()
        return
```

#### Asset Creation/Lookup
Reuse the `_get_or_create_asset()` helper method (lines 502-535) or similar pattern.

#### Transaction Creation
Use the same `self.db_manager.create_transaction()` pattern as in `import_csv()` (lines 428-437).

### 4. Error Handling and Validation

#### Follow Existing Patterns
- **Asset Validation**: Check if asset exists before transaction creation
- **Portfolio Validation**: Validate portfolio existence and show available options
- **Transaction Validation**: Use existing validation patterns from `add_asset_transaction()`
- **Error Messages**: Use consistent emoji-based error messages (❌, ✅, ⚠️)

#### Import Summary Pattern
Follow the import summary pattern from `import_csv()` (lines 449-453):
```python
print(f"\n📊 Import Summary:")
print(f"   ✅ Imported: {imported_count} transactions")
print(f"   🆕 Created: {created_assets} new assets")
print(f"   ⏭️  Skipped: {skipped_count} entries")
```

### 5. Main Function Integration
**Location**: Add to the main command dispatcher (around line 937)

```python
elif args.command == "import-llm":
    cli.import_llm(
        input_data=args.input_data,
        portfolio_name=args.portfolio,
        format_hint=args.format_hint,
        dry_run=args.dry_run
    )
```

## Dependencies and Extensions

### Required Additional Dependencies
- LLM client library (e.g., OpenAI, Anthropic)
- Text processing utilities for input handling
- JSON/structured data parsing for LLM responses

### Configuration Integration
Consider using the existing configuration system (lines 873-966 in `database.py`) for:
- LLM API keys
- Default parsing parameters
- Model selection

### Future Extensions
- Support for different LLM providers
- Custom prompt templates
- Transaction validation rules
- Integration with external data sources

## Implementation Considerations

1. **Security**: Ensure sensitive data is not logged or exposed
2. **Performance**: Consider batch processing for large datasets
3. **Reliability**: Implement retry logic for LLM API calls
4. **Validation**: Add comprehensive validation for LLM-parsed data
5. **Testing**: Follow existing test patterns in the codebase

## Conclusion

The new LLM import command should integrate seamlessly with the existing CLI structure by:
- Following established argument parsing patterns
- Reusing existing database helper methods
- Maintaining consistent error handling and user feedback
- Leveraging the existing portfolio and asset management infrastructure

This design ensures minimal disruption to the existing codebase while providing powerful new functionality for transaction import.
