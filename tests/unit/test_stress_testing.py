"""Tests for portfolio stress testing — data tables, helper, endpoint."""

import pandas as pd
from unittest.mock import patch

from portf_server.routers.analytics import (
    _STRESS_SCENARIOS,
    _STRESS_FALLBACKS,
    _get_ticker_return,
)


class TestStressFallbacks:
    def test_all_preset_scenarios_defined(self):
        for key in ("2008", "2020", "2022", "dotcom"):
            assert key in _STRESS_SCENARIOS
            meta = _STRESS_SCENARIOS[key]
            assert "label" in meta
            assert "from_date" in meta
            assert "to_date" in meta

    def test_fallback_covers_all_asset_types(self):
        required = {
            "stock",
            "etf",
            "index",
            "fund",
            "bond",
            "crypto",
            "commodity",
            "interest",
            "deposit",
        }
        for scenario_key, table in _STRESS_FALLBACKS.items():
            for asset_type in required:
                assert asset_type in table, f"{scenario_key} missing '{asset_type}'"

    def test_2008_equity_loss_is_severe(self):
        assert _STRESS_FALLBACKS["2008"]["stock"] <= -40.0

    def test_2022_bonds_are_negative(self):
        assert _STRESS_FALLBACKS["2022"]["bond"] < 0

    def test_deposits_always_zero_in_all_scenarios(self):
        for table in _STRESS_FALLBACKS.values():
            assert table["deposit"] == 0.0
            assert table["interest"] == 0.0


class TestGetTickerReturn:
    def test_returns_correct_pct_for_valid_history(self):
        hist = pd.DataFrame(
            {"Close": [100.0, 50.0]},
            index=pd.to_datetime(["2007-10-01", "2009-03-09"]),
        )
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = hist
            result = _get_ticker_return("AAPL", "2007-10-01", "2009-03-09")
        assert result == -50.0

    def test_returns_none_when_history_is_empty(self):
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = pd.DataFrame()
            result = _get_ticker_return("NOPE", "2007-10-01", "2009-03-09")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch(
            "portf_server.routers.analytics.yf.Ticker",
            side_effect=Exception("network down"),
        ):
            result = _get_ticker_return("AAPL", "2007-10-01", "2009-03-09")
        assert result is None

    def test_returns_none_when_only_one_row(self):
        hist = pd.DataFrame(
            {"Close": [100.0]},
            index=pd.to_datetime(["2007-10-01"]),
        )
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = hist
            result = _get_ticker_return("AAPL", "2007-10-01", "2007-10-01")
        assert result is None
