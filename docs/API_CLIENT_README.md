# API Client for Portfolio Management

The `api_client.py` module provides a comprehensive external API client for retrieving stock price data and currency conversion rates. It wraps yfinance for stock data and exchangerate-api for FX rates, with built-in caching, error handling, and rate limiting.

## Features

- **Yahoo Finance Integration**: Stock prices, metadata, and historical data
- **Currency Conversion**: Real-time exchange rates via ExchangeRate-API
- **Flexible Caching**: Memory, disk, or both with configurable TTL
- **Rate Limiting**: Automatic throttling to respect API limits
- **Error Handling**: Comprehensive error handling with retry logic
- **Convenience Functions**: Simple interface for common operations

## Quick Start

```python
from portf_manager.api_client import APIClient, get_price, convert
from decimal import Decimal
from datetime import date

# Basic usage with convenience functions
price = get_price("AAPL")  # Current AAPL price in USD
converted = convert(Decimal("100"), "USD", "EUR")  # Convert $100 to EUR

# Using the client directly
client = APIClient()
metadata = client.get_metadata("AAPL")
historical = client.get_price_history("AAPL", start_date=date(2023, 1, 1))
```

## API Reference

### Main Functions

#### `get_price(symbol, date=None, currency="USD")`
Get stock price for a symbol on a specific date.

**Parameters:**
- `symbol` (str): Stock symbol (e.g., 'AAPL', 'TSLA')
- `date` (date, optional): Date for price lookup (default: latest)
- `currency` (str): Target currency (default: USD)

**Returns:** `Decimal` or `None`

#### `convert(amount, from_currency, to_currency)`
Convert amount between currencies.

**Parameters:**
- `amount` (Decimal): Amount to convert
- `from_currency` (str): Source currency code
- `to_currency` (str): Target currency code

**Returns:** `Decimal` or `None`

#### `get_metadata(symbol)`
Get metadata for a stock symbol.

**Parameters:**
- `symbol` (str): Stock symbol

**Returns:** `Dict` with metadata or `None`

### APIClient Class

#### Initialization
```python
client = APIClient(
    cache_strategy=CacheStrategy.BOTH,  # NONE, MEMORY, DISK, BOTH
    cache_dir="~/.cache/portf_manager",  # Cache directory
    price_cache_ttl=3600,               # Price cache TTL (seconds)
    fx_cache_ttl=14400,                 # FX cache TTL (seconds)
    metadata_cache_ttl=86400,           # Metadata cache TTL (seconds)
    max_retries=3,                      # Maximum retry attempts
    retry_delay=1.0,                    # Base retry delay (seconds)
    yfinance_rate_limit=20,             # Yahoo Finance calls per minute
    fx_rate_limit=100,                  # FX API calls per month
    fx_api_key=None                     # ExchangeRate-API key (optional)
)
```

#### Methods

- `get_price(symbol, date=None, currency="USD")` - Get stock price
- `get_metadata(symbol)` - Get stock metadata
- `get_fx_rate(from_currency, to_currency)` - Get exchange rate
- `convert(amount, from_currency, to_currency)` - Convert currency
- `get_price_history(symbol, start_date, end_date=None, currency="USD")` - Get price history
- `clear_cache(prefix=None)` - Clear cache entries
- `get_cache_stats()` - Get cache statistics

## Configuration

### Environment Variables

- `EXCHANGE_RATE_API_KEY`: Your ExchangeRate-API key for higher rate limits

### Cache Strategies

- `NONE`: No caching
- `MEMORY`: In-memory caching only
- `DISK`: Disk-based caching only
- `BOTH`: Both memory and disk caching (recommended)

### Rate Limiting

The client automatically handles rate limiting for both APIs:
- **Yahoo Finance**: 20 calls per minute (configurable)
- **ExchangeRate-API**: 100 calls per month for free tier

## Error Handling

The client handles various error conditions:
- Network connectivity issues
- API rate limit exceeded
- Invalid symbols or data not found
- Malformed responses

All errors are wrapped in descriptive exceptions:
- `APIError`: General API-related errors
- `RateLimitError`: Rate limit exceeded
- `DataNotFoundError`: Requested data not available

## Examples

### Basic Price Retrieval
```python
from portf_manager.api_client import get_price
from datetime import date

# Current price
current = get_price("AAPL")
print(f"AAPL: ${current}")

# Historical price
yesterday = date.today() - timedelta(days=1)
historical = get_price("AAPL", date=yesterday)
print(f"AAPL yesterday: ${historical}")

# Price in different currency
eur_price = get_price("AAPL", currency="EUR")
print(f"AAPL: €{eur_price}")
```

### Currency Conversion
```python
from portf_manager.api_client import convert
from decimal import Decimal

# Convert $1000 USD to EUR
amount = Decimal("1000")
converted = convert(amount, "USD", "EUR")
print(f"${amount} USD = €{converted} EUR")
```

### Advanced Usage
```python
from portf_manager.api_client import APIClient, CacheStrategy

# Custom configuration
client = APIClient(
    cache_strategy=CacheStrategy.MEMORY,
    price_cache_ttl=1800,  # 30 minutes
    max_retries=5
)

# Get detailed metadata
metadata = client.get_metadata("AAPL")
if metadata:
    print(f"Company: {metadata['name']}")
    print(f"Sector: {metadata['sector']}")
    print(f"Market Cap: ${metadata['market_cap']:,}")

# Get price history
from datetime import date, timedelta
start_date = date.today() - timedelta(days=30)
history = client.get_price_history("AAPL", start_date)

for entry in history[:5]:  # Show first 5 days
    print(f"{entry['date']}: ${entry['close']:.2f}")

# Cache management
stats = client.get_cache_stats()
print(f"Cache entries: {stats['memory_entries']}")
client.clear_cache("price")  # Clear only price cache
```

## Testing

Run the unit tests to verify functionality:

```bash
# Run all tests
python -m pytest test_api_client_unit.py -v

# Run integration test (requires network)
python test_api_client.py
```

## Dependencies

- `yfinance`: Yahoo Finance data
- `requests`: HTTP requests for FX API
- `pandas`: Data manipulation (required by yfinance)

The API client is designed to be robust and production-ready with comprehensive error handling, caching, and rate limiting to ensure reliable operation in portfolio management applications.
