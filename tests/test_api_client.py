"""
Live-network smoke tests for the API client.

These hit Yahoo Finance and exchangerate-api.com for real, so they are skipped
unless PFM_LIVE_NETWORK_TESTS is set. Mocked coverage of the same behaviour
lives in tests/test_api_client_unit.py.

Run with: PFM_LIVE_NETWORK_TESTS=1 uv run pytest tests/test_api_client.py
"""

import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

from portf_manager.api_client import APIClient, CacheStrategy

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not os.getenv("PFM_LIVE_NETWORK_TESTS"),
        reason="live network test; set PFM_LIVE_NETWORK_TESTS=1 to run",
    ),
]


@pytest.fixture
def client() -> APIClient:
    """A memory-cached client that fails fast instead of sleeping on retries."""
    return APIClient(
        cache_strategy=CacheStrategy.MEMORY,
        max_retries=2,
        retry_delay=0,
    )


def test_get_price(client: APIClient) -> None:
    """Current and EUR-converted prices are positive."""
    assert client.get_price("AAPL") > 0
    assert client.get_price("AAPL", currency="EUR") > 0


def test_get_price_historical(client: APIClient) -> None:
    """A price on a recent trading day is positive."""
    # Last weekday before today, so the market was open
    day = date.today() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    assert client.get_price("AAPL", date=day) > 0


def test_get_metadata(client: APIClient) -> None:
    """Metadata carries a name and a currency."""
    metadata = client.get_metadata("AAPL")
    assert metadata
    assert metadata.get("name")
    assert metadata.get("currency")


def test_fx_and_convert(client: APIClient) -> None:
    """FX rates are positive and a same-currency conversion is a no-op."""
    assert client.get_fx_rate("USD", "EUR") > 0
    assert client.get_fx_rate("EUR", "USD") > 0
    assert client.convert(Decimal("100"), "USD", "EUR") > 0
    assert client.convert(Decimal("100"), "USD", "USD") == Decimal("100")


def test_get_price_history(client: APIClient) -> None:
    """A week of history returns at least one row with a positive close."""
    history = client.get_price_history(
        "AAPL", date.today() - timedelta(days=7), date.today() - timedelta(days=1)
    )
    assert history
    assert all(entry["close"] > 0 for entry in history)


def test_fetch_latest_prices(client: APIClient) -> None:
    """Valid symbols get positive float prices; invalid ones are left out."""
    result = client.fetch_latest_prices(["AAPL", "MSFT"])
    assert set(result) == {"AAPL", "MSFT"}
    assert all(isinstance(p, float) and p > 0 for p in result.values())

    mixed = client.fetch_latest_prices(["AAPL", "INVALID_TICKER_XYZ123"])
    assert "AAPL" in mixed
    assert "INVALID_TICKER_XYZ123" not in mixed
