# Transaction Management Commands

The Portfolio Manager CLI now includes comprehensive transaction management commands for creating, updating, and deleting transactions.

## Available Commands

### List Transactions

List recent transactions with optional filtering:

```bash
# List recent transactions (default: last 10)
python -m portf_manager list-transactions

# List more transactions
python -m portf_manager list-transactions --limit 20

# Filter by asset symbol
python -m portf_manager list-transactions --symbol AAPL
```

### Add Transactions

Add new buy/sell/dividend transactions:

```bash
# Add a buy transaction
python -m portf_manager add-transaction --symbol AAPL --amount 100 --price 150.00 --currency USD --type buy --date 2024-01-15

# Add a sell transaction
python -m portf_manager add-transaction --symbol AAPL --amount 50 --price 160.00 --currency USD --type sell --date 2024-02-15

# Add to specific portfolio
python -m portf_manager add-transaction --symbol AAPL --amount 100 --price 150.00 --currency USD --type buy --date 2024-01-15 --portfolio "Growth Portfolio"
```

### Update Transactions

Update existing transactions by ID:

```bash
# Update transaction quantity
python -m portf_manager update-transaction 123 --quantity 150

# Update transaction price
python -m portf_manager update-transaction 123 --price 155.50

# Update multiple fields at once
python -m portf_manager update-transaction 123 --quantity 120 --price 152.75

# Change transaction type
python -m portf_manager update-transaction 123 --type sell

# Update transaction date
python -m portf_manager update-transaction 123 --date 2024-02-20

# Add or update description
python -m portf_manager update-transaction 123 --description "Profit taking"
```

### Delete Transactions

Delete transactions by ID with confirmation:

```bash
# Delete a transaction (will prompt for confirmation)
python -m portf_manager delete-transaction 123
```

## Update Command Options

The update command supports the following options:

- `--quantity QUANTITY`: Update the number of shares/units
- `--price PRICE`: Update the price per share
- `--date DATE`: Update the transaction date (YYYY-MM-DD format)
- `--type TYPE`: Change transaction type (buy, sell, dividend)
- `--description DESCRIPTION`: Add or update transaction description

## Examples

### Example 1: Correcting a Transaction

If you made a mistake when entering a transaction:

```bash
# First, list transactions to find the ID
python -m portf_manager list-transactions --limit 5

# Update the incorrect quantity and price
python -m portf_manager update-transaction 17 --quantity 100 --price 148.50
```

### Example 2: Converting Buy to Sell

If you accidentally entered a buy instead of a sell:

```bash
# Change transaction type
python -m portf_manager update-transaction 17 --type sell
```

### Example 3: Bulk Updates

For multiple changes to the same transaction:

```bash
# Update multiple fields in one command
python -m portf_manager update-transaction 17 --quantity 200 --price 149.25 --date 2024-01-20 --description "Increased position"
```

### Example 4: Safe Deletion

To delete a transaction safely:

```bash
# The command will show transaction details and ask for confirmation
python -m portf_manager delete-transaction 17

# Output will be:
# 🗑️ Transaction to delete:
#    ID: 17
#    Asset: AAPL
#    Quantity: 100.0
#    Price: 150.0
#    Date: 2024-01-15
# Are you sure you want to delete this transaction? (y/N):
```

## Error Handling

The commands include comprehensive error handling:

### Transaction Not Found

```bash
$ python -m portf_manager update-transaction 999 --quantity 100
❌ Transaction with ID 999 not found.
```

### Invalid Transaction Type

```bash
$ python -m portf_manager update-transaction 17 --type invalid
error: argument --type: invalid choice: 'invalid' (choose from 'buy', 'sell', 'dividend')
```

### No Fields to Update

```bash
$ python -m portf_manager update-transaction 17
❌ No fields specified for update.
```

## Server Mode Support

These commands work seamlessly in both local and server modes:

- **Local Mode**: Commands interact directly with the SQLite database
- **Server Mode**: Commands make HTTP requests to the Portfolio Manager API server

The user experience is identical regardless of the mode.

## Security Features

- **User Authentication**: In local mode, commands require user login
- **Transaction Ownership**: Users can only modify their own transactions
- **Confirmation Prompts**: Delete operations require explicit confirmation
- **Input Validation**: All inputs are validated before processing

## Integration with Existing Workflows

These commands integrate perfectly with existing portfolio management workflows:

1. **Import Transactions**: Use `import-csv` to bulk import transactions
2. **Review and Correct**: Use `list-transactions` to review imported data
3. **Update as Needed**: Use `update-transaction` to fix any issues
4. **Clean Up**: Use `delete-transaction` to remove duplicates or errors
5. **Export Results**: Use `export-transactions` to export corrected data

## Technical Notes

### Automatic Calculations

When updating quantity or price, the system automatically recalculates the total amount:

```bash
# Original: 100 shares × $150 = $15,000
python -m portf_manager update-transaction 17 --quantity 120
# Result: 120 shares × $150 = $18,000 (total_amount updated automatically)

python -m portf_manager update-transaction 17 --price 155
# Result: 120 shares × $155 = $18,600 (total_amount updated automatically)
```

### Date Format

Transaction dates should be provided in ISO format (YYYY-MM-DD):

```bash
# Correct
python -m portf_manager update-transaction 17 --date 2024-01-15

# Incorrect (will cause validation error)
python -m portf_manager update-transaction 17 --date 15/01/2024
```

### Batch Operations

For multiple updates, consider using the API directly or creating a script that calls the CLI commands in sequence.


## Asset Management Commands

In addition to transaction management, you can also update and delete assets.

### Update Assets

Update existing assets by ID:

```bash
# Update asset name
python -m portf_manager update-asset 5 --name "Apple Inc. (Updated)"

# Update exchange and currency
python -m portf_manager update-asset 5 --exchange NYSE --currency USD

# Update description
python -m portf_manager update-asset 5 --description "Updated Apple stock information"

# Update sector
python -m portf_manager update-asset 5 --sector "Technology"

# Set asset as inactive
python -m portf_manager update-asset 5 --active False
```

### Delete Assets

Delete assets by ID (soft delete - marks as inactive):

```bash
# Delete an asset (will prompt for confirmation)
python -m portf_manager delete-asset 5
```

### Asset Update Options

The update-asset command supports the following options:

- `--name NAME`: Update the asset name
- `--exchange EXCHANGE`: Update the exchange where it's traded
- `--currency CURRENCY`: Update the base currency
- `--sector SECTOR`: Update the sector classification
- `--description DESCRIPTION`: Add or update asset description
- `--active {True,False}`: Set active status (True/False)

### Asset Management Examples

#### Example 1: Updating Asset Information

```bash
# First, list assets to find the ID
python -m portf_manager list-assets

# Update the asset details
python -m portf_manager update-asset 5 --name "Apple Inc." --exchange NASDAQ --sector "Technology"
```

#### Example 2: Deactivating an Asset

```bash
# Mark an asset as inactive (soft delete)
python -m portf_manager update-asset 5 --active False

# To reactivate later
python -m portf_manager update-asset 5 --active True
```

#### Example 3: Safe Asset Deletion

```bash
# The command will show asset details and ask for confirmation
python -m portf_manager delete-asset 5

# Output will be:
# 🗑️ Asset to delete (soft delete - will be marked as inactive):
#    ID: 5
#    Symbol: AAPL
#    Name: Apple Inc.
#    Type: stock
# Are you sure you want to delete this asset? (y/N):
```

### Important Notes about Asset Management

#### Soft Delete Design
- Assets are **never permanently deleted** from the database
- `delete-asset` marks assets as `inactive` but preserves all data
- Inactive assets can be reactivated using `update-asset --active True`
- This design protects against data loss and maintains transaction history integrity

#### Transaction Safety
- Assets referenced by existing transactions cannot cause data integrity issues
- All historical transaction data remains intact even for inactive assets
- You can safely manage assets without worrying about breaking transaction records

#### ID vs Symbol
- Asset update/delete commands use **asset ID**, not symbol
- Use `list-assets` to find the ID of the asset you want to modify
- This ensures precision when multiple assets might have similar symbols

### Error Handling

Asset commands include comprehensive error handling:

```bash
$ python -m portf_manager update-asset 999 --name "Test"
❌ Asset with ID 999 not found.

$ python -m portf_manager delete-asset 5
⚠️ Asset 5 (AAPL) is already inactive.
```

### Integration with Transaction Management

Asset and transaction management work together seamlessly:

1. **Update Asset Information**: Use `update-asset` to fix or improve asset data
2. **Manage Transactions**: Use `update-transaction` and `delete-transaction` for transaction edits
3. **Deactivate Unused Assets**: Use `delete-asset` to clean up unused assets
4. **Maintain Data Integrity**: Both systems preserve historical data and relationships

The combined asset and transaction management system provides complete control over your portfolio data while maintaining safety and data integrity.

