"""
Test script for API Client

This script demonstrates and tests the API client functionality.
"""

from datetime import date, timedelta
from decimal import Decimal
from portf_manager.api_client import DataNotFoundError, APIError
from portf_manager.api_client import (
    APIClient,
    CacheStrategy,
    get_price,
    convert,
    get_metadata,
)


def test_api_client():
    """Test the API client functionality."""
    print("Testing API Client...")

    # Create client with memory caching for faster testing
    client = APIClient(
        cache_strategy=CacheStrategy.MEMORY,
        yfinance_rate_limit=5,  # Lower for testing
        max_retries=2,
    )

    print(f"Cache strategy: {client.cache_strategy}")
    print(f"Cache directory: {client.cache_dir}")

    # Test current price
    print("\n=== Testing Current Price ===")
    try:
        aapl_price = client.get_price("AAPL")
        print(f"AAPL current price: ${aapl_price}")

        # Test with currency conversion
        aapl_price_eur = client.get_price("AAPL", currency="EUR")
        print(f"AAPL current price in EUR: €{aapl_price_eur}")

    except Exception as e:
        print(f"Error getting current price: {e}")

    # Test historical price
    print("\n=== Testing Historical Price ===")
    try:
        yesterday = date.today() - timedelta(days=1)
        aapl_hist_price = client.get_price("AAPL", date=yesterday)
        print(f"AAPL price on {yesterday}: ${aapl_hist_price}")

    except Exception as e:
        print(f"Error getting historical price: {e}")

    # Test metadata
    print("\n=== Testing Metadata ===")
    try:
        aapl_metadata = client.get_metadata("AAPL")
        if aapl_metadata:
            print(f"AAPL metadata:")
            print(f"  Name: {aapl_metadata.get('name')}")
            print(f"  Sector: {aapl_metadata.get('sector')}")
            print(f"  Exchange: {aapl_metadata.get('exchange')}")
            print(f"  Market Cap: {aapl_metadata.get('market_cap')}")
            print(f"  Currency: {aapl_metadata.get('currency')}")
        else:
            print("No metadata found for AAPL")

    except Exception as e:
        print(f"Error getting metadata: {e}")

    # Test FX rates
    print("\n=== Testing FX Rates ===")
    try:
        usd_to_eur = client.get_fx_rate("USD", "EUR")
        print(f"USD to EUR rate: {usd_to_eur}")

        eur_to_usd = client.get_fx_rate("EUR", "USD")
        print(f"EUR to USD rate: {eur_to_usd}")

    except Exception as e:
        print(f"Error getting FX rates: {e}")

    # Test currency conversion
    print("\n=== Testing Currency Conversion ===")
    try:
        amount = Decimal("100")
        converted = client.convert(amount, "USD", "EUR")
        print(f"${amount} USD = €{converted} EUR")

        # Test same currency (should return original amount)
        same_currency = client.convert(amount, "USD", "USD")
        print(f"${amount} USD = ${same_currency} USD (same currency)")

    except Exception as e:
        print(f"Error converting currency: {e}")

    # Test price history
    print("\n=== Testing Price History ===")
    try:
        start_date = date.today() - timedelta(days=7)
        end_date = date.today() - timedelta(days=1)

        history = client.get_price_history("AAPL", start_date, end_date)
        print(f"AAPL price history from {start_date} to {end_date}:")
        for entry in history[:3]:  # Show first 3 entries
            print(
                f"  {entry['date']}: Close=${entry['close']:.2f}, Volume={entry['volume']:,}"
            )
        if len(history) > 3:
            print(f"  ... and {len(history) - 3} more entries")

    except Exception as e:
        print(f"Error getting price history: {e}")

    # Test cache statistics
    print("\n=== Cache Statistics ===")
    stats = client.get_cache_stats()
    print(f"Memory cache entries: {stats['memory_entries']}")
    print(f"Disk cache entries: {stats['disk_entries']}")
    print(f"YFinance calls remaining: {stats['yfinance_calls_remaining']}")
    print(f"FX calls remaining: {stats['fx_calls_remaining']}")

    # Test convenience functions
    print("\n=== Testing Convenience Functions ===")
    try:
        tsla_price = get_price("TSLA")
        print(f"TSLA price (convenience function): ${tsla_price}")

        converted_amount = convert(Decimal("50"), "USD", "GBP")
        print(f"$50 USD = £{converted_amount} GBP (convenience function)")

        msft_metadata = get_metadata("MSFT")
        if msft_metadata:
            print(f"MSFT name (convenience function): {msft_metadata.get('name')}")

    except Exception as e:
        print(f"Error with convenience functions: {e}")

    print("\n=== Test Complete ===")


if __name__ == "__main__":
    test_api_client()


def test_fetch_latest_prices():
    """Test the fetch_latest_prices method."""
    print("\n=== Testing fetch_latest_prices method ===")

    # Create client with memory caching for faster testing
    client = APIClient(
        cache_strategy=CacheStrategy.MEMORY,
        yfinance_rate_limit=5,  # Lower for testing
        max_retries=2,
    )

    # Test 1: Empty list
    print("Testing empty list...")
    result = client.fetch_latest_prices([])
    print(f"Empty list result: {result}")
    assert result == {}
    print("✓ Empty list test passed")

    # Test 2: Single valid symbol
    print("Testing single valid symbol...")
    try:
        result = client.fetch_latest_prices(["AAPL"])
        print(f"Single symbol result: {result}")
        assert "AAPL" in result
        assert isinstance(result["AAPL"], float)
        assert result["AAPL"] > 0
        print("✓ Single valid symbol test passed")
    except Exception as e:
        print(f"✗ Single valid symbol test failed: {e}")

    # Test 3: Multiple valid symbols
    print("Testing multiple valid symbols...")
    try:
        result = client.fetch_latest_prices(["AAPL", "MSFT"])
        print(f"Multiple symbols result: {result}")
        assert len(result) >= 1  # At least one should succeed
        for symbol, price in result.items():
            assert isinstance(price, float)
            assert price > 0
        print("✓ Multiple valid symbols test passed")
    except Exception as e:
        print(f"✗ Multiple valid symbols test failed: {e}")

    # Test 4: Invalid symbol only
    print("Testing invalid symbol only...")
    try:
        result = client.fetch_latest_prices(["INVALID_TICKER_XYZ123"])
        print(f"Invalid symbol result: {result}")
        # Should either return empty dict or raise DataNotFoundError
        if result != {}:
            print("✗ Expected empty result or DataNotFoundError")
        else:
            print("✓ Invalid symbol test passed (empty result)")
    except DataNotFoundError as e:
        print(f"✓ Invalid symbol test passed (DataNotFoundError): {e}")
    except Exception as e:
        print(f"✗ Invalid symbol test failed with unexpected error: {e}")

    # Test 5: Mixed valid and invalid symbols
    print("Testing mixed valid and invalid symbols...")
    try:
        result = client.fetch_latest_prices(["AAPL", "INVALID_TICKER"])
        print(f"Mixed symbols result: {result}")
        # Should have AAPL but not INVALID_TICKER
        assert "AAPL" in result
        assert "INVALID_TICKER" not in result
        print("✓ Mixed symbols test passed")
    except Exception as e:
        print(f"✗ Mixed symbols test failed: {e}")

    print("fetch_latest_prices tests completed!")


if __name__ == "__main__":
    test_api_client()
    test_fetch_latest_prices()
