"""
Unit tests for API Client

These tests use mocks to avoid external API dependencies.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, date, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import json

from portf_manager.api_client import (
    APIClient,
    CacheStrategy,
    CacheEntry,
    RateLimiter,
    APIError,
    get_price,
    convert,
    get_metadata,
)


class TestRateLimiter:
    """Test the rate limiter functionality."""

    def test_rate_limiter_basic(self):
        limiter = RateLimiter(max_calls=2, window_seconds=60)

        # Should be able to make calls
        assert limiter.can_make_call()
        limiter.record_call()

        assert limiter.can_make_call()
        limiter.record_call()

        # Should hit limit
        assert not limiter.can_make_call()
        assert limiter.wait_time() > 0

    def test_rate_limiter_window_expiry(self):
        limiter = RateLimiter(max_calls=1, window_seconds=1)

        # Make one call
        limiter.record_call()
        assert not limiter.can_make_call()

        # Simulate time passage by manipulating calls list
        past_time = datetime.now() - timedelta(seconds=2)
        limiter.calls = [past_time]

        # Should be able to make call again
        assert limiter.can_make_call()


class TestCacheEntry:
    """Test the cache entry functionality."""

    def test_cache_entry_not_expired(self):
        entry = CacheEntry(data="test_data", timestamp=datetime.now(), ttl_seconds=3600)
        assert not entry.is_expired()

    def test_cache_entry_expired(self):
        entry = CacheEntry(
            data="test_data",
            timestamp=datetime.now() - timedelta(seconds=3700),
            ttl_seconds=3600,
        )
        assert entry.is_expired()


class TestAPIClient:
    """Test the API client functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.client = APIClient(
            cache_strategy=CacheStrategy.MEMORY,
            cache_dir=self.temp_dir,
            yfinance_rate_limit=10,
            fx_rate_limit=10,
            max_retries=3,
            retry_delay=0.1,
        )

    def test_cache_key_generation(self):
        key = self.client._get_cache_key("test", symbol="AAPL", date="2023-01-01")
        assert "test_" in key
        assert "symbol=AAPL" in key
        assert "date=2023-01-01" in key

    def test_memory_cache_operations(self):
        # Test storing and retrieving from memory cache
        test_data = {"price": 150.0}
        self.client._store_in_cache("test_key", test_data, 3600)

        retrieved = self.client._get_from_cache("test_key")
        assert retrieved == test_data

    def test_disk_cache_operations(self):
        # Test with disk cache strategy
        client = APIClient(cache_strategy=CacheStrategy.DISK, cache_dir=self.temp_dir)

        test_data = {"price": 150.0}
        client._store_in_cache("test_key", test_data, 3600)

        retrieved = client._get_from_cache("test_key")
        assert retrieved == test_data

    def test_cache_expiry(self):
        # Store data with very short TTL
        test_data = {"price": 150.0}
        self.client._store_in_cache("test_key", test_data, -1)  # Already expired

        # Should return None for expired data
        retrieved = self.client._get_from_cache("test_key")
        assert retrieved is None

    def test_currency_conversion_same_currency(self):
        """Test conversion with same source and target currency."""
        amount = Decimal("100")
        result = self.client.convert(amount, "USD", "USD")
        assert result == amount

    @patch("portf_manager.api_client.requests.get")
    def test_fx_rate_success(self, mock_get):
        """Test successful FX rate retrieval."""
        mock_response = Mock()
        mock_response.json.return_value = {"rates": {"EUR": 0.85}}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        rate = self.client.get_fx_rate("USD", "EUR")
        assert rate == Decimal("0.85")

    @patch("portf_manager.api_client.requests.get")
    def test_fx_rate_failure(self, mock_get):
        """Test FX rate retrieval failure."""
        mock_get.side_effect = Exception("Network error")

        with pytest.raises(APIError):
            self.client.get_fx_rate("USD", "EUR")

    @patch("portf_manager.api_client.yf.Ticker")
    def test_get_price_current_success(self, mock_ticker):
        """Test successful current price retrieval."""
        mock_ticker_instance = Mock()
        mock_ticker_instance.info = {"regularMarketPrice": 150.0}
        mock_ticker.return_value = mock_ticker_instance

        price = self.client.get_price("AAPL")
        assert price == Decimal("150.0")

    @patch("portf_manager.api_client.yf.Ticker")
    def test_get_price_historical_success(self, mock_ticker):
        """Test successful historical price retrieval."""
        import pandas as pd

        mock_ticker_instance = Mock()
        # Create mock historical data
        mock_hist = pd.DataFrame({"Close": [150.0]}, index=[datetime.now()])
        mock_ticker_instance.history.return_value = mock_hist
        mock_ticker.return_value = mock_ticker_instance

        price = self.client.get_price("AAPL", date=date.today())
        assert price == Decimal("150.0")

    @patch("portf_manager.api_client.yf.Ticker")
    def test_get_price_not_found(self, mock_ticker):
        """Test price retrieval when data not found."""
        mock_ticker_instance = Mock()
        mock_ticker_instance.info = {}
        mock_ticker.return_value = mock_ticker_instance

        price = self.client.get_price("INVALID")
        assert price is None

    @patch("portf_manager.api_client.yf.Ticker")
    def test_get_metadata_success(self, mock_ticker):
        """Test successful metadata retrieval."""
        mock_ticker_instance = Mock()
        mock_ticker_instance.info = {
            "symbol": "AAPL",
            "longName": "Apple Inc.",
            "sector": "Technology",
            "exchange": "NASDAQ",
            "currency": "USD",
        }
        mock_ticker.return_value = mock_ticker_instance

        metadata = self.client.get_metadata("AAPL")
        assert metadata["symbol"] == "AAPL"
        assert metadata["name"] == "Apple Inc."
        assert metadata["sector"] == "Technology"

    @patch("portf_manager.api_client.yf.Ticker")
    def test_get_metadata_not_found(self, mock_ticker):
        """Test metadata retrieval when not found."""
        mock_ticker_instance = Mock()
        mock_ticker_instance.info = {}
        mock_ticker.return_value = mock_ticker_instance

        metadata = self.client.get_metadata("INVALID")
        assert metadata is None

    @patch("portf_manager.api_client.yf.Ticker")
    def test_get_price_history_success(self, mock_ticker):
        """Test successful price history retrieval."""
        import pandas as pd

        mock_ticker_instance = Mock()
        # Create mock historical data
        dates = [datetime.now() - timedelta(days=i) for i in range(3)]
        mock_hist = pd.DataFrame(
            {
                "Open": [148.0, 149.0, 150.0],
                "High": [152.0, 153.0, 154.0],
                "Low": [146.0, 147.0, 148.0],
                "Close": [150.0, 151.0, 152.0],
                "Volume": [1000000, 1100000, 1200000],
            },
            index=dates,
        )
        mock_ticker_instance.history.return_value = mock_hist
        mock_ticker.return_value = mock_ticker_instance

        start_date = date.today() - timedelta(days=7)
        history = self.client.get_price_history("AAPL", start_date)

        assert len(history) == 3
        assert history[0]["close"] == 150.0
        assert history[0]["volume"] == 1000000

    def test_cache_stats(self):
        """Test cache statistics."""
        # Add some test data to cache
        self.client.memory_cache["test1"] = CacheEntry("data1", datetime.now(), 3600)
        self.client.memory_cache["test2"] = CacheEntry("data2", datetime.now(), 3600)

        stats = self.client.get_cache_stats()
        assert stats["memory_entries"] == 2
        assert "cache_directory" in stats
        assert "yfinance_calls_remaining" in stats
        assert "fx_calls_remaining" in stats

    def test_clear_cache(self):
        """Test cache clearing."""
        # Add test data
        self.client.memory_cache["test1"] = CacheEntry("data1", datetime.now(), 3600)
        self.client.memory_cache["other1"] = CacheEntry("data2", datetime.now(), 3600)

        # Clear with prefix
        self.client.clear_cache("test")
        assert "test1" not in self.client.memory_cache
        assert "other1" in self.client.memory_cache

        # Clear all
        self.client.clear_cache()
        assert len(self.client.memory_cache) == 0

    def test_retry_mechanism(self):
        """Test the retry mechanism with exponential backoff."""
        call_count = 0

        def failing_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary failure")
            return "success"

        result = self.client._retry_with_backoff(failing_function)
        assert result == "success"
        assert call_count == 2

    def test_retry_mechanism_all_fail(self):
        """Test retry mechanism when all attempts fail."""

        def always_failing_function():
            raise Exception("Permanent failure")

        with pytest.raises(Exception, match="Permanent failure"):
            self.client._retry_with_backoff(always_failing_function)


class TestConvenienceFunctions:
    """Test the convenience functions."""

    @patch("portf_manager.api_client.get_client")
    def test_get_price_convenience(self, mock_get_client):
        """Test the get_price convenience function."""
        mock_client = Mock()
        mock_client.get_price.return_value = Decimal("150.0")
        mock_get_client.return_value = mock_client

        price = get_price("AAPL")
        assert price == Decimal("150.0")
        mock_client.get_price.assert_called_once_with("AAPL", None, "USD")

    @patch("portf_manager.api_client.get_client")
    def test_convert_convenience(self, mock_get_client):
        """Test the convert convenience function."""
        mock_client = Mock()
        mock_client.convert.return_value = Decimal("85.0")
        mock_get_client.return_value = mock_client

        amount = Decimal("100")
        converted = convert(amount, "USD", "EUR")
        assert converted == Decimal("85.0")
        mock_client.convert.assert_called_once_with(amount, "USD", "EUR")

    @patch("portf_manager.api_client.get_client")
    def test_get_metadata_convenience(self, mock_get_client):
        """Test the get_metadata convenience function."""
        mock_client = Mock()
        mock_client.get_metadata.return_value = {"name": "Apple Inc."}
        mock_get_client.return_value = mock_client

        metadata = get_metadata("AAPL")
        assert metadata == {"name": "Apple Inc."}
        mock_client.get_metadata.assert_called_once_with("AAPL")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
