# Demo Account Setup

This directory contains scripts to set up and verify a demo account for the Portfolio Manager CLI application.

## Files

- `create_demo_account.py` - Creates a demo account with sample data
- `verify_demo_account.py` - Verifies the demo account and displays data
- `portfolio_demo.db` - Demo database file (created by setup script)

## Demo Account Details

- **Username**: `demo`
- **Password**: `demo`
- **Database**: `portfolio_demo.db`

## Setup Instructions

1. **Create the demo account**:
   ```bash
   python create_demo_account.py
   ```

2. **Verify the setup**:
   ```bash
   python verify_demo_account.py
   ```

3. **Use the CLI with demo account**:
   ```bash
   # First login
   python -m portf_manager.cli --db-path portfolio_demo.db login --username demo --password demo
   
   # Then use other commands
   python -m portf_manager.cli --db-path portfolio_demo.db list-assets
   ```

## Sample Data Created

The demo account includes:

### Entities (4 brokers)
- Interactive Brokers
- Fidelity
- Charles Schwab
- TD Ameritrade

### Portfolios (4 different types)
- **Main Portfolio** - Mixed investments
- **Retirement 401k** - Long-term retirement savings
- **Tech Growth** - Technology-focused growth stocks
- **Dividend Income** - Dividend-paying stocks

### Assets (15 different securities)
- **Stocks**: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, NFLX, JNJ, PG
- **ETFs**: SPY, QQQ, VTI
- **Crypto**: BTC-USD, ETH-USD

### Transactions (31 sample transactions)
- Buy/Sell transactions across all portfolios
- Dividend payments
- Transactions spanning last 120 days
- Realistic prices and quantities

## Example CLI Commands to Try

Once logged in, try these commands:

```bash
# View all assets
python -m portf_manager.cli --db-path portfolio_demo.db list-assets

# View all transactions
python -m portf_manager.cli --db-path portfolio_demo.db list-transactions

# View portfolios
python -m portf_manager.cli --db-path portfolio_demo.db list-portfolios

# Show portfolio values
python -m portf_manager.cli --db-path portfolio_demo.db portfolio-value

# Export transactions to CSV
python -m portf_manager.cli --db-path portfolio_demo.db export-transactions --output demo_transactions.csv

# Export filtered transactions by symbol
python -m portf_manager.cli --db-path portfolio_demo.db export-transactions --symbol AAPL --output aapl_transactions.csv

# Export filtered transactions by date range
python -m portf_manager.cli --db-path portfolio_demo.db export-transactions --start-date 2025-01-01 --end-date 2025-06-30 --output q1_transactions.csv
```

## Portfolio Value Summary

The demo account has approximately **$135,000** in total portfolio value across:
- Main Portfolio: ~$74,000
- Retirement 401k: ~$41,000
- Tech Growth: ~$14,000
- Dividend Income: ~$7,000

## Cleaning Up

To remove the demo account:
```bash
rm portfolio_demo.db
rm .portf_session  # Clears any cached login session
```

## Notes

- The demo data uses realistic but fictional transaction data
- Prices are approximate and not real-time
- The demo account is completely isolated from any real data
- All transactions are dated within the last 120 days for realistic testing
