# Transaction Filtering Service

This document describes the transaction filtering functionality implemented in Step 4 of the portfolio management system.

## Overview

The `TransactionFilterService` provides a comprehensive solution for retrieving and filtering transactions for the current user. It implements the following key requirements:

1. **Use transactions repository/service** - Loads transactions using the database adapter
2. **Apply symbol filter** - Case-insensitive matching for asset symbols
3. **Apply date range filter** - Inclusive date filtering with timezone-aware datetime conversion
4. **Graceful handling** - Warns user and exits gracefully when no transactions remain after filtering

## Key Components

### TransactionFilter (Data Class)
```python
@dataclass
class TransactionFilter:
    symbol: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    timezone: str = "UTC"
```

### TransactionFilterService (Main Service)
```python
class TransactionFilterService:
    def __init__(self, db_adapter: DatabaseAdapter, auth_manager: AuthManager)
    def get_user_transactions(self, filter_criteria: TransactionFilter) -> List[Transaction]
    def get_filtered_transaction_summary(self, filter_criteria: TransactionFilter) -> Dict[str, Any]
```

## Features

### 1. Current User Transaction Retrieval
- Ensures user is authenticated before retrieving transactions
- Uses `db_adapter.get_all_transactions(user_id=current_user["id"])` to load user's transactions
- Converts raw database dictionaries to `Transaction` objects with proper database adapter injection

### 2. Symbol Filter (Case-Insensitive)
- Matches asset symbols in a case-insensitive manner
- Handles both uppercase and lowercase input (e.g., "AAPL", "aapl", "Aapl")
- Retrieves asset information for each transaction to perform symbol matching

### 3. Date Range Filter (Timezone-Aware)
- Supports inclusive date range filtering
- Converts dates to timezone-aware datetimes using Python's `zoneinfo.ZoneInfo`
- Handles different timezones (UTC, America/New_York, etc.)
- Supports partial date ranges (start date only, end date only, or both)

### 4. Graceful Error Handling
- Warns user when no transactions are found for the current user
- Provides specific warnings when filters yield no results
- Includes helpful suggestions for adjusting filter criteria
- Returns empty lists instead of raising exceptions

## Usage Examples

### Basic Usage
```python
from portf_manager.database import Database
from portf_manager.auth import AuthManager
from portf_manager.transaction_filter import TransactionFilter, create_transaction_filter_service
from datetime import date

# Initialize components
db_manager = Database("portfolio.db")
auth_manager = AuthManager(db_manager)
filter_service = create_transaction_filter_service(db_manager, auth_manager)

# Get all transactions
all_transactions = filter_service.get_user_transactions(TransactionFilter())

# Filter by symbol (case-insensitive)
aapl_transactions = filter_service.get_user_transactions(
    TransactionFilter(symbol="AAPL")
)

# Filter by date range
transactions_2024 = filter_service.get_user_transactions(
    TransactionFilter(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        timezone="America/New_York"
    )
)

# Combine filters
combined_transactions = filter_service.get_user_transactions(
    TransactionFilter(
        symbol="TSLA",
        start_date=date(2024, 6, 1),
        end_date=date(2024, 6, 30),
        timezone="UTC"
    )
)
```

### Transaction Summary
```python
# Get summary statistics
summary = filter_service.get_filtered_transaction_summary(
    TransactionFilter(symbol="AAPL")
)

print(f"Total transactions: {summary['total_count']}")
print(f"Symbols: {summary['symbols']}")
print(f"Date range: {summary['date_range']}")
print(f"Transaction types: {summary['transaction_types']}")
print(f"Total value: ${summary['total_value']:,.2f}")
```

## Error Handling

### No Transactions Found
When no transactions are found, the service provides user-friendly warnings:

```
⚠️  No transactions found for symbol 'NONEXISTENT'
💡 Try adjusting your filter criteria or check if you have any transactions in the database.
```

### Authentication Required
```python
# Raises ValueError if user is not authenticated
if not auth_manager.is_authenticated():
    raise ValueError("User must be authenticated to retrieve transactions")
```

## Testing

### Demo Script
Run the demonstration script to see the filtering service in action:

```bash
python demo_transaction_filtering.py
```

### Test Script
Run the test script to verify functionality:

```bash
python test_transaction_filter.py
```

## Integration

The transaction filtering service is fully integrated into the portfolio management system:

1. **Database Integration**: Uses the existing `Database` class as the adapter
2. **Authentication Integration**: Works with the existing `AuthManager`
3. **Model Integration**: Returns proper `Transaction` objects with database adapter injection
4. **Package Integration**: Exported in `portf_manager.__init__.py`

## Implementation Details

### Timezone-Aware Date Filtering
The service converts date filters to timezone-aware datetimes:

```python
# Start of day in specified timezone
start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)

# End of day in specified timezone
end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=tz)
```

### Case-Insensitive Symbol Matching
Symbol matching is performed by normalizing to uppercase:

```python
symbol_upper = symbol.upper()
# ... later in filtering ...
if asset and asset.symbol.upper() == symbol_upper:
    filtered.append(tx)
```

### Database Adapter Pattern
The service uses the existing `DatabaseAdapter` protocol for database operations, ensuring consistency with the rest of the system.

## Dependencies

- `datetime` - Date and time handling
- `zoneinfo` - Timezone support (Python 3.9+)
- `typing` - Type hints
- `dataclasses` - Data class support
- `portf_manager.models` - Transaction and DatabaseAdapter
- `portf_manager.auth` - Authentication management

## File Structure

```
portf_manager/
├── transaction_filter.py     # Main implementation
├── models.py                 # Transaction and DatabaseAdapter
├── database.py              # Database implementation
├── auth.py                   # Authentication management
└── __init__.py              # Package exports

demo_transaction_filtering.py # Demonstration script
test_transaction_filter.py   # Test script
```

This implementation successfully completes Step 4 of the broader plan by providing a robust, user-friendly transaction filtering service that handles all the specified requirements.
