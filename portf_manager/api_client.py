"""
External API Client for Portfolio Management

This module provides a client interface for retrieving stock price data
and currency conversion rates from external APIs, with built-in caching,
error handling, and rate limiting.
"""

import time
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Optional, Any, List
from decimal import Decimal
from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path

import yfinance as yf
import requests
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base exception for API-related errors."""


class RateLimitError(APIError):
    """Exception raised when rate limit is exceeded."""


class DataNotFoundError(APIError):
    """Exception raised when requested data is not found."""


class CacheStrategy(Enum):
    """Cache strategy enumeration."""

    NONE = "none"
    MEMORY = "memory"
    DISK = "disk"
    BOTH = "both"


@dataclass
class CacheEntry:
    """Cache entry data structure."""

    data: Any
    timestamp: datetime
    ttl_seconds: int

    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        return datetime.now() > self.timestamp + timedelta(seconds=self.ttl_seconds)


@dataclass
class RateLimiter:
    """Rate limiter for API calls."""

    max_calls: int = 60
    window_seconds: int = 60
    calls: List[datetime] = field(default_factory=list)

    def can_make_call(self) -> bool:
        """Check if we can make another API call."""
        now = datetime.now()

        # Remove old calls outside the window
        cutoff = now - timedelta(seconds=self.window_seconds)
        self.calls = [call_time for call_time in self.calls if call_time > cutoff]

        return len(self.calls) < self.max_calls

    def wait_time(self) -> float:
        """Calculate wait time before next call is allowed."""
        if self.can_make_call():
            return 0.0

        # Find the oldest call that needs to expire
        oldest_call = min(self.calls)
        wait_until = oldest_call + timedelta(seconds=self.window_seconds)
        return (wait_until - datetime.now()).total_seconds()

    def record_call(self):
        """Record that an API call was made."""
        self.calls.append(datetime.now())


class APIClient:
    """
    External API client for price data and currency conversion.

    Features:
    - Yahoo Finance integration for stock prices and metadata
    - ExchangeRate-API for currency conversion
    - Configurable caching (memory/disk)
    - Rate limiting with automatic throttling
    - Comprehensive error handling
    - Retry logic with exponential backoff
    """

    def __init__(
        self,
        cache_strategy: CacheStrategy = CacheStrategy.BOTH,
        cache_dir: Optional[str] = None,
        price_cache_ttl: int = 3600,  # 1 hour
        fx_cache_ttl: int = 14400,  # 4 hours
        metadata_cache_ttl: int = 86400,  # 24 hours
        max_retries: int = 3,
        retry_delay: float = 1.0,
        yfinance_rate_limit: int = 20,  # calls per minute
        fx_rate_limit: int = 100,  # calls per month (free tier)
        fx_api_key: Optional[str] = None,
    ):
        """
        Initialize API client.

        Args:
            cache_strategy: Caching strategy to use
            cache_dir: Directory for disk cache (default: ~/.cache/portf_manager)
            price_cache_ttl: Cache TTL for price data in seconds
            fx_cache_ttl: Cache TTL for FX rates in seconds
            metadata_cache_ttl: Cache TTL for metadata in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Base delay between retries in seconds
            yfinance_rate_limit: Yahoo Finance rate limit (calls per minute)
            fx_rate_limit: FX API rate limit (calls per month)
            fx_api_key: ExchangeRate-API key (optional, for higher limits)
        """
        self.cache_strategy = cache_strategy
        self.cache_dir = Path(cache_dir or os.path.expanduser("~/.cache/portf_manager"))
        self.price_cache_ttl = price_cache_ttl
        self.fx_cache_ttl = fx_cache_ttl
        self.metadata_cache_ttl = metadata_cache_ttl
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.fx_api_key = fx_api_key or os.getenv("EXCHANGE_RATE_API_KEY")

        # Initialize caches
        self.memory_cache: Dict[str, CacheEntry] = {}

        # Initialize rate limiters
        self.yfinance_limiter = RateLimiter(
            max_calls=yfinance_rate_limit, window_seconds=60
        )
        self.fx_limiter = RateLimiter(
            max_calls=fx_rate_limit, window_seconds=2592000  # 30 days
        )

        # Create cache directory if using disk cache
        if self.cache_strategy in [CacheStrategy.DISK, CacheStrategy.BOTH]:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Cache directory: {self.cache_dir}")

    def _get_cache_key(self, prefix: str, **kwargs) -> str:
        """Generate cache key from prefix and parameters."""
        params = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return f"{prefix}_{params}"

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get data from cache."""
        # Try memory cache first
        if self.cache_strategy in [CacheStrategy.MEMORY, CacheStrategy.BOTH]:
            if key in self.memory_cache:
                entry = self.memory_cache[key]
                if not entry.is_expired():
                    logger.debug(f"Cache hit (memory): {key}")
                    return entry.data
                else:
                    # Remove expired entry
                    del self.memory_cache[key]

        # Try disk cache
        if self.cache_strategy in [CacheStrategy.DISK, CacheStrategy.BOTH]:
            cache_file = self.cache_dir / f"{key}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as f:
                        cache_data = json.load(f)

                    entry = CacheEntry(
                        data=cache_data["data"],
                        timestamp=datetime.fromisoformat(cache_data["timestamp"]),
                        ttl_seconds=cache_data["ttl_seconds"],
                    )

                    if not entry.is_expired():
                        logger.debug(f"Cache hit (disk): {key}")
                        # Also store in memory cache for faster access
                        if self.cache_strategy == CacheStrategy.BOTH:
                            self.memory_cache[key] = entry
                        return entry.data
                    else:
                        # Remove expired file
                        cache_file.unlink()
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.warning(f"Invalid cache file {cache_file}: {e}")
                    cache_file.unlink(missing_ok=True)

        return None

    def _store_in_cache(self, key: str, data: Any, ttl_seconds: int):
        """Store data in cache."""
        entry = CacheEntry(data=data, timestamp=datetime.now(), ttl_seconds=ttl_seconds)

        # Store in memory cache
        if self.cache_strategy in [CacheStrategy.MEMORY, CacheStrategy.BOTH]:
            self.memory_cache[key] = entry

        # Store in disk cache
        if self.cache_strategy in [CacheStrategy.DISK, CacheStrategy.BOTH]:
            cache_file = self.cache_dir / f"{key}.json"
            try:
                cache_data = {
                    "data": data,
                    "timestamp": entry.timestamp.isoformat(),
                    "ttl_seconds": ttl_seconds,
                }
                with open(cache_file, "w") as f:
                    json.dump(cache_data, f, indent=2, default=str)
                logger.debug(f"Cache stored (disk): {key}")
            except Exception as e:
                logger.warning(f"Failed to store cache file {cache_file}: {e}")

    def _wait_for_rate_limit(self, limiter: RateLimiter):
        """Wait for rate limit if necessary."""
        wait_time = limiter.wait_time()
        if wait_time > 0:
            logger.info(f"Rate limit reached, waiting {wait_time:.2f} seconds")
            time.sleep(wait_time)

    def _retry_with_backoff(self, func, *args, **kwargs):
        """Execute function with retry logic and exponential backoff."""
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"All {self.max_retries} attempts failed")

        raise last_exception

    def get_price(
        self, symbol: str, date: Optional[date] = None, currency: str = "USD"
    ) -> Optional[Decimal]:
        """
        Get price for a symbol on a specific date.

        Args:
            symbol: Stock symbol (e.g., 'AAPL', 'TSLA')
            date: Date for price lookup (default: latest)
            currency: Target currency (default: USD)

        Returns:
            Price as Decimal, or None if not found

        Raises:
            APIError: If API call fails
            RateLimitError: If rate limit is exceeded
        """
        # Generate cache key
        cache_key = self._get_cache_key(
            "price",
            symbol=symbol,
            date=date.isoformat() if date else "latest",
            currency=currency,
        )

        # Check cache first
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return Decimal(str(cached_data)) if cached_data else None

        try:
            # Rate limiting
            self._wait_for_rate_limit(self.yfinance_limiter)

            # Fetch data from Yahoo Finance
            def fetch_price():
                ticker = yf.Ticker(symbol)

                if date:
                    # Get historical data
                    start_date = date
                    end_date = date + timedelta(days=1)
                    hist = ticker.history(start=start_date, end=end_date)

                    if hist.empty:
                        return None

                    price = hist["Close"].iloc[0]
                else:
                    # Get current price
                    info = ticker.info
                    price = info.get("regularMarketPrice") or info.get("currentPrice")

                    if price is None:
                        return None

                return float(price)

            price = self._retry_with_backoff(fetch_price)
            self.yfinance_limiter.record_call()

            if price is None:
                logger.warning(f"No price data found for {symbol}")
                self._store_in_cache(cache_key, None, self.price_cache_ttl)
                return None

            # Convert currency if needed
            if currency != "USD":
                fx_rate = self.get_fx_rate("USD", currency)
                if fx_rate:
                    price = price * float(fx_rate)

            # Store in cache and return
            self._store_in_cache(cache_key, price, self.price_cache_ttl)
            return Decimal(str(price))

        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            raise APIError(f"Failed to fetch price for {symbol}: {e}")

    def get_metadata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Metadata dictionary, or None if not found
        """
        cache_key = self._get_cache_key("metadata", symbol=symbol)

        # Check cache first
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return cached_data

        try:
            # Rate limiting
            self._wait_for_rate_limit(self.yfinance_limiter)

            def fetch_metadata():
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info or "symbol" not in info:
                    return None

                # Extract relevant metadata
                metadata = {
                    "symbol": info.get("symbol"),
                    "name": info.get("longName") or info.get("shortName"),
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "exchange": info.get("exchange"),
                    "currency": info.get("currency", "USD"),
                    "market_cap": info.get("marketCap"),
                    "description": info.get("longBusinessSummary"),
                    "website": info.get("website"),
                    "employees": info.get("fullTimeEmployees"),
                    "pe_ratio": info.get("forwardPE"),
                    "dividend_yield": info.get("dividendYield"),
                    "beta": info.get("beta"),
                    "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                    "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                    "avg_volume": info.get("averageVolume"),
                    "updated_at": datetime.now().isoformat(),
                }

                return metadata

            metadata = self._retry_with_backoff(fetch_metadata)
            self.yfinance_limiter.record_call()

            if metadata is None:
                logger.warning(f"No metadata found for {symbol}")
                return None

            # Store in cache and return
            self._store_in_cache(cache_key, metadata, self.metadata_cache_ttl)
            return metadata

        except Exception as e:
            logger.error(f"Error fetching metadata for {symbol}: {e}")
            raise APIError(f"Failed to fetch metadata for {symbol}: {e}")

    def get_fx_rate(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """
        Get foreign exchange rate.

        Args:
            from_currency: Source currency code (e.g., 'USD')
            to_currency: Target currency code (e.g., 'EUR')

        Returns:
            Exchange rate as Decimal, or None if not found
        """
        if from_currency == to_currency:
            return Decimal("1.0")

        cache_key = self._get_cache_key(
            "fx", from_cur=from_currency, to_cur=to_currency
        )

        # Check cache first
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return Decimal(str(cached_data))

        try:
            # Rate limiting
            self._wait_for_rate_limit(self.fx_limiter)

            def fetch_fx_rate():
                # Use ExchangeRate-API
                if self.fx_api_key:
                    url = f"https://v6.exchangerate-api.com/v6/{self.fx_api_key}/pair/{from_currency}/{to_currency}"
                else:
                    url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"

                response = requests.get(url, timeout=10)
                response.raise_for_status()

                data = response.json()

                if self.fx_api_key:
                    # Paid API response format
                    if data.get("result") == "success":
                        return data.get("conversion_rate")
                else:
                    # Free API response format
                    rates = data.get("rates", {})
                    return rates.get(to_currency)

                return None

            rate = self._retry_with_backoff(fetch_fx_rate)
            self.fx_limiter.record_call()

            if rate is None:
                logger.warning(f"No FX rate found for {from_currency}/{to_currency}")
                return None

            # Store in cache and return
            self._store_in_cache(cache_key, rate, self.fx_cache_ttl)
            return Decimal(str(rate))

        except Exception as e:
            logger.error(f"Error fetching FX rate {from_currency}/{to_currency}: {e}")
            raise APIError(
                f"Failed to fetch FX rate {from_currency}/{to_currency}: {e}"
            )

    def convert(
        self, amount: Decimal, from_currency: str, to_currency: str
    ) -> Optional[Decimal]:
        """
        Convert amount from one currency to another.

        Args:
            amount: Amount to convert
            from_currency: Source currency code
            to_currency: Target currency code

        Returns:
            Converted amount as Decimal, or None if conversion fails
        """
        if from_currency == to_currency:
            return amount

        fx_rate = self.get_fx_rate(from_currency, to_currency)
        if fx_rate is None:
            return None

        return amount * fx_rate

    def fetch_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """
        Fetch the most recent close prices for multiple symbols.

        Args:
            symbols: List of stock symbols (e.g., ['AAPL', 'TSLA', 'MSFT'])

        Returns:
            Dictionary mapping symbol to latest close price

        Raises:
            APIError: If API call fails
            DataNotFoundError: If one or more symbols are invalid or inactive
        """
        if not symbols:
            return {}

        # Rate limiting
        self._wait_for_rate_limit(self.yfinance_limiter)

        results = {}
        invalid_symbols = []
        api_errors = []

        def fetch_prices_batch():
            """Fetch prices for all symbols in a single call for efficiency."""
            try:
                # Use yfinance's download function for bulk operations
                data = yf.download(
                    tickers=" ".join(symbols),
                    period="2d",  # Get last 2 days to ensure we have recent data
                    interval="1d",
                    progress=False,
                    threads=True,
                    auto_adjust=False,
                )

                if data.empty:
                    return {}, symbols, []

                # Handle single symbol case (different data structure)
                if len(symbols) == 1:
                    if "Close" in data.columns and len(data["Close"].dropna()) > 0:
                        latest_price = data["Close"].iloc[-1]
                        if not pd.isna(latest_price):
                            return {symbols[0]: float(latest_price)}, [], []
                    return {}, symbols, []

                # Handle multiple symbols case
                batch_results = {}
                batch_invalid = []

                for symbol in symbols:
                    try:
                        if ("Close", symbol) in data.columns:
                            close_prices = data[("Close", symbol)]

                            # Get the most recent non-NaN price
                            valid_prices = close_prices.dropna()
                            if not valid_prices.empty:
                                latest_price = valid_prices.iloc[-1]
                                batch_results[symbol] = float(latest_price)
                            else:
                                batch_invalid.append(symbol)
                        else:
                            batch_invalid.append(symbol)
                    except (KeyError, IndexError, ValueError) as e:
                        logger.warning(f"Error processing {symbol}: {e}")
                        batch_invalid.append(symbol)

                return batch_results, batch_invalid, []

            except Exception as e:
                logger.error(f"Batch price fetch failed: {e}")
                return {}, [], [str(e)]

        try:
            batch_results, batch_invalid, batch_errors = self._retry_with_backoff(
                fetch_prices_batch
            )
            self.yfinance_limiter.record_call()

            # Normalize GBX (pence) → GBP: yfinance returns UK-listed securities
            # in pence. fast_info.currency == "GBp" signals this; divide by 100.
            for symbol in list(batch_results.keys()):
                try:
                    currency = yf.Ticker(symbol).fast_info.currency
                    if currency == "GBp":
                        batch_results[symbol] = batch_results[symbol] / 100.0
                        logger.debug(
                            f"{symbol}: GBX→GBP ÷100 → {batch_results[symbol]:.4f}"
                        )
                except Exception:
                    pass

            results.update(batch_results)
            invalid_symbols.extend(batch_invalid)
            api_errors.extend(batch_errors)

        except Exception as e:
            logger.error(f"Error in batch price fetch: {e}")
            api_errors.append(str(e))

        # If batch fetch failed completely, try individual fetches as fallback
        if not results and not invalid_symbols and api_errors:
            logger.info("Batch fetch failed, falling back to individual fetches")

            for symbol in symbols:
                try:
                    price = self.get_price(symbol)
                    if price is not None:
                        results[symbol] = float(price)
                    else:
                        invalid_symbols.append(symbol)
                except APIError:
                    invalid_symbols.append(symbol)
                except Exception as e:
                    logger.warning(f"Individual fetch failed for {symbol}: {e}")
                    invalid_symbols.append(symbol)

        # Cache results for individual symbols
        for symbol, price in results.items():
            cache_key = self._get_cache_key(
                "price", symbol=symbol, date="latest", currency="USD"
            )
            self._store_in_cache(cache_key, price, self.price_cache_ttl)

        # Raise structured errors if needed
        if invalid_symbols:
            error_msg = f"Invalid or inactive tickers: {', '.join(invalid_symbols)}"
            if results:
                # Partial success - log warning but don't raise exception
                logger.warning(error_msg)
            else:
                # Complete failure - raise exception
                raise DataNotFoundError(error_msg)

        if api_errors and not results:
            error_msg = f"API errors occurred: {'; '.join(api_errors)}"
            raise APIError(error_msg)

        return results

    def fetch_symbols_with_progress(
        self,
        symbols: list[str],
        operation_name: str = "Processing",
        show_progress: bool = True,
    ) -> dict:
        """
        Fetch data for multiple symbols with progress feedback and comprehensive error handling.

        Args:
            symbols: List of stock symbols to process
            operation_name: Name of the operation for progress display
            show_progress: Whether to show progress bar

        Returns:
            Dictionary with results, errors, and summary statistics
        """
        if not symbols:
            return {
                "results": {},
                "errors": {},
                "skipped": [],
                "summary": {"total": 0, "successful": 0, "errors": 0, "skipped": 0},
            }

        results = {}
        errors = {}
        skipped = []

        # Create progress bar if requested
        if show_progress:
            try:
                from tqdm import tqdm

                progress_bar = tqdm(
                    total=len(symbols), desc=operation_name, unit="symbol"
                )
            except ImportError:
                logger.warning("tqdm not available, falling back to simple logging")
                progress_bar = None
        else:
            progress_bar = None

        try:
            # First attempt batch fetch for efficiency
            logger.info(f"Attempting batch fetch for {len(symbols)} symbols")

            try:
                batch_results = self.fetch_latest_prices(symbols)
                results.update(batch_results)

                # Update progress for successful batch results
                if progress_bar:
                    progress_bar.update(len(batch_results))
                    progress_bar.set_postfix_str(f"Batch: {len(batch_results)} symbols")

            except (APIError, DataNotFoundError, RateLimitError) as e:
                logger.warning(
                    f"Batch fetch failed: {e}, falling back to individual fetches"
                )

                # Fall back to individual symbol processing
                for symbol in symbols:
                    try:
                        # Rate limiting is handled in get_price
                        price = self.get_price(symbol)

                        if price is not None:
                            results[symbol] = float(price)
                            if progress_bar:
                                progress_bar.set_postfix_str(f"✅ {symbol}")
                        else:
                            skipped.append(symbol)
                            if progress_bar:
                                progress_bar.set_postfix_str(f"⚠️ {symbol}: No data")

                    except RateLimitError as e:
                        logger.warning(f"Rate limit hit for {symbol}, waiting...")
                        errors[symbol] = f"Rate limit: {e}"
                        if progress_bar:
                            progress_bar.set_postfix_str(f"⏱️ {symbol}: Rate limited")

                    except DataNotFoundError:
                        skipped.append(symbol)
                        if progress_bar:
                            progress_bar.set_postfix_str(f"⚠️ {symbol}: Not found")

                    except APIError as e:
                        errors[symbol] = f"API error: {e}"
                        if progress_bar:
                            progress_bar.set_postfix_str(f"❌ {symbol}: API error")

                    except Exception as e:
                        errors[symbol] = f"Unexpected error: {e}"
                        logger.error(f"Unexpected error for {symbol}: {e}")
                        if progress_bar:
                            progress_bar.set_postfix_str(f"❌ {symbol}: Error")

                    if progress_bar:
                        progress_bar.update(1)

        finally:
            if progress_bar:
                progress_bar.close()

        # Generate summary statistics
        summary = {
            "total": len(symbols),
            "successful": len(results),
            "errors": len(errors),
            "skipped": len(skipped),
        }

        logger.info(
            f"Operation '{operation_name}' completed: "
            f"{summary['successful']}/{summary['total']} successful, "
            f"{summary['errors']} errors, {summary['skipped']} skipped"
        )

        return {
            "results": results,
            "errors": errors,
            "skipped": skipped,
            "summary": summary,
        }

    def get_price_history(
        self,
        symbol: str,
        start_date: date,
        end_date: Optional[date] = None,
        currency: str = "USD",
    ) -> List[Dict[str, Any]]:
        """
        Get price history for a symbol.

        Args:
            symbol: Stock symbol
            start_date: Start date for history
            end_date: End date for history (default: today)
            currency: Target currency

        Returns:
            List of price history dictionaries
        """
        if end_date is None:
            end_date = date.today()

        cache_key = self._get_cache_key(
            "price_history",
            symbol=symbol,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            currency=currency,
        )

        # Check cache first
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return cached_data

        try:
            # Rate limiting
            self._wait_for_rate_limit(self.yfinance_limiter)

            def fetch_history():
                ticker = yf.Ticker(symbol)
                hist = ticker.history(
                    start=start_date, end=end_date + timedelta(days=1)
                )

                if hist.empty:
                    return []

                history = []
                for date_idx, row in hist.iterrows():
                    price_data = {
                        "date": date_idx.date().isoformat(),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "adj_close": float(
                            row["Close"]
                        ),  # Yahoo Finance doesn't separate these
                        "volume": (
                            int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
                        ),
                    }
                    history.append(price_data)

                return history

            history = self._retry_with_backoff(fetch_history)
            self.yfinance_limiter.record_call()

            # Convert currency if needed
            if currency != "USD":
                fx_rate = self.get_fx_rate("USD", currency)
                if fx_rate:
                    fx_rate_float = float(fx_rate)
                    for entry in history:
                        entry["open"] *= fx_rate_float
                        entry["high"] *= fx_rate_float
                        entry["low"] *= fx_rate_float
                        entry["close"] *= fx_rate_float
                        entry["adj_close"] *= fx_rate_float

            # Store in cache and return
            self._store_in_cache(cache_key, history, self.price_cache_ttl)
            return history

        except Exception as e:
            logger.error(f"Error fetching price history for {symbol}: {e}")
            raise APIError(f"Failed to fetch price history for {symbol}: {e}")

    def clear_cache(self, prefix: Optional[str] = None):
        """
        Clear cache entries.

        Args:
            prefix: Clear only entries with this prefix (default: clear all)
        """
        # Clear memory cache
        if prefix:
            keys_to_remove = [
                k for k in self.memory_cache.keys() if k.startswith(prefix)
            ]
            for key in keys_to_remove:
                del self.memory_cache[key]
        else:
            self.memory_cache.clear()

        # Clear disk cache
        if self.cache_strategy in [CacheStrategy.DISK, CacheStrategy.BOTH]:
            cache_files = self.cache_dir.glob("*.json")
            for cache_file in cache_files:
                if prefix is None or cache_file.stem.startswith(prefix):
                    cache_file.unlink()

        logger.info(f"Cache cleared{' with prefix: ' + prefix if prefix else ''}")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        memory_count = len(self.memory_cache)
        disk_count = 0

        if self.cache_strategy in [CacheStrategy.DISK, CacheStrategy.BOTH]:
            disk_count = len(list(self.cache_dir.glob("*.json")))

        return {
            "memory_entries": memory_count,
            "disk_entries": disk_count,
            "cache_directory": str(self.cache_dir),
            "yfinance_calls_remaining": self.yfinance_limiter.max_calls
            - len(self.yfinance_limiter.calls),
            "fx_calls_remaining": self.fx_limiter.max_calls
            - len(self.fx_limiter.calls),
        }


# Global client instance
_client_instance: Optional[APIClient] = None


def get_client() -> APIClient:
    """Get global API client instance."""
    global _client_instance
    if _client_instance is None:
        _client_instance = APIClient(cache_strategy=CacheStrategy.MEMORY)
    return _client_instance


def set_client(client: APIClient):
    """Set global API client instance."""
    global _client_instance
    _client_instance = client


# Convenience functions for direct access
def get_price(
    symbol: str, date: Optional[date] = None, currency: str = "USD"
) -> Optional[Decimal]:
    """Get price for a symbol (convenience function)."""
    return get_client().get_price(symbol, date, currency)


def convert(amount: Decimal, from_currency: str, to_currency: str) -> Optional[Decimal]:
    """Convert amount between currencies (convenience function)."""
    return get_client().convert(amount, from_currency, to_currency)


def get_metadata(symbol: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a symbol (convenience function)."""
    return get_client().get_metadata(symbol)


def fetch_latest_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch latest prices for multiple symbols (convenience function)."""
    return get_client().fetch_latest_prices(symbols)
