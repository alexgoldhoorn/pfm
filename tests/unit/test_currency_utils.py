"""Tests for GBX (pence) currency normalization on import."""

from unittest.mock import MagicMock, patch

from portf_manager import currency_utils
from portf_manager.currency_utils import normalize_gbx_amounts, is_gbx


def _mock_ticker(currency):
    t = MagicMock()
    t.fast_info.currency = currency
    return t


def test_gbx_symbol_normalized():
    currency_utils._GBX_CACHE.clear()
    with patch("yfinance.Ticker", return_value=_mock_ticker("GBp")):
        assert is_gbx("GB0000000001") is True
        price, total, fees, cur = normalize_gbx_amounts(
            "GB0000000001", 9759.0, 78072.0, 5.0, "GBP"
        )
        assert price == 97.59
        assert total == 780.72
        assert fees == 0.05
        assert cur == "GBP"


def test_non_gbx_symbol_unchanged():
    currency_utils._GBX_CACHE.clear()
    with patch("yfinance.Ticker", return_value=_mock_ticker("USD")):
        assert is_gbx("AAPL") is False
        price, total, fees, cur = normalize_gbx_amounts(
            "AAPL", 150.0, 1500.0, 1.0, "USD"
        )
        assert (price, total, fees, cur) == (150.0, 1500.0, 1.0, "USD")


def test_lookup_failure_is_safe():
    currency_utils._GBX_CACHE.clear()
    with patch("yfinance.Ticker", side_effect=Exception("network")):
        assert is_gbx("X") is False
        # unchanged on failure
        assert normalize_gbx_amounts("X", 10.0, None, None, "EUR") == (
            10.0,
            None,
            None,
            "EUR",
        )
