# CSV Export Functionality

This document describes the CSV export functionality for filtered transactions in the Portfolio Manager.

## Overview

The CSV export functionality allows users to export filtered transactions to CSV files with proper formatting and headers. The exported CSV files follow strict formatting requirements:

- **Headers**: Deterministic column ordering with proper names
- **Date Format**: ISO-8601 format using `.isoformat()`
- **Numeric Format**: Full precision (no trailing zeros stripped)
- **Encoding**: UTF-8 with `newline=""` parameter

## Headers

The CSV files contain the following headers in this exact order:

1. **Transaction ID** - Unique identifier for the transaction
2. **Date** - Transaction date in ISO-8601 format
3. **Symbol** - Asset symbol (e.g., AAPL, TSLA)
4. **Asset Name** - Full name of the asset
5. **Transaction Type** - Type of transaction (buy, sell, dividend, etc.)
6. **Quantity** - Number of shares/units
7. **Price** - Price per share/unit
8. **Total Amount** - Total transaction amount
9. **Currency** - Currency of the transaction
10. **Portfolio Name** - Name of the portfolio (if applicable)
11. **Description** - Transaction description

## Usage

### Basic Usage

```python
from portf_manager.csv_export import create_csv_exporter
from portf_manager.transaction_filter import TransactionFilter
from portf_manager.database import Database
from portf_manager.auth import AuthManager

# Initialize components
db_manager = Database("portfolio.db")
auth_manager = AuthManager(db_manager)
csv_exporter = create_csv_exporter(db_manager, auth_manager)

# Export all transactions
filter_criteria = TransactionFilter()
success = csv_exporter.export_transactions_to_csv(
    filter_criteria, "all_transactions.csv"
)
```

### Filtered Export

```python
from datetime import date

# Export with symbol filter
filter_criteria = TransactionFilter(symbol="AAPL")
success = csv_exporter.export_transactions_to_csv(
    filter_criteria, "aapl_transactions.csv"
)

# Export with date range filter
filter_criteria = TransactionFilter(
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    timezone="America/New_York"
)
success = csv_exporter.export_transactions_to_csv(
    filter_criteria, "2024_transactions.csv"
)

# Export with combined filters
filter_criteria = TransactionFilter(
    symbol="TSLA",
    start_date=date(2024, 6, 1),
    end_date=date(2024, 6, 30),
    timezone="UTC"
)
success = csv_exporter.export_transactions_to_csv(
    filter_criteria, "tsla_june_2024.csv"
)
```

### Export with Summary

```python
# Export and get summary statistics
result = csv_exporter.export_with_summary(
    filter_criteria, "transactions_with_summary.csv"
)

print(f"Success: {result['success']}")
print(f"Message: {result['message']}")
print(f"Transaction count: {result['transaction_count']}")
print(f"Summary: {result['summary']}")
```

## Format Examples

### Sample CSV Output

```csv
Transaction ID,Date,Symbol,Asset Name,Transaction Type,Quantity,Price,Total Amount,Currency,Portfolio Name,Description
1,2024-01-15T00:00:00,AAPL,Apple Inc.,buy,100,150.25,15025.00,USD,My Portfolio,Stock purchase
2,2024-01-16T00:00:00,TSLA,Tesla Inc.,sell,50,245.80,12290.00,USD,My Portfolio,Profit taking
3,2024-01-17T00:00:00,AAPL,Apple Inc.,dividend,100,0.24,24.00,USD,My Portfolio,Quarterly dividend
```

### Date Format

All dates are formatted using ISO-8601 standard:
- Format: `YYYY-MM-DDTHH:MM:SS`
- Example: `2024-01-15T00:00:00`

### Numeric Format

Numeric values preserve full precision:
- Quantity: `100` (not `100.0`)
- Price: `150.25` (not `150.250000`)
- Total Amount: `15025.00` (preserves decimal places)

## Error Handling

The CSV export functionality includes comprehensive error handling:

```python
try:
    success = csv_exporter.export_transactions_to_csv(
        filter_criteria, "output.csv"
    )
    if success:
        print("✅ Export successful")
    else:
        print("❌ Export failed")
except Exception as e:
    print(f"❌ Error: {e}")
```

## Testing

Run the test script to verify functionality:

```bash
python test_csv_export.py
```

The test script will:
1. Export all transactions
2. Export with symbol filter
3. Export with date range filter
4. Export with combined filters
5. Verify CSV format and headers

## Requirements

- Python 3.12+ (or 3.9+ depending on codebase)
- `csv` module (standard library)
- `datetime` module (standard library)
- Portfolio Manager database and authentication

## Files

- `portf_manager/csv_export.py` - Main CSV export implementation
- `test_csv_export.py` - Test script for CSV export functionality
- `CSV_EXPORT_README.md` - This documentation file

## Integration

The CSV export functionality integrates with:
- **Transaction Filter Service** - For filtering transactions
- **Database Adapter** - For data access
- **Authentication Manager** - For user context
- **Transaction Models** - For data structure

This ensures consistent data access and proper authentication checks before exporting sensitive financial data.
