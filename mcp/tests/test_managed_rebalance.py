"""Output test for the managed line in rebalance_analysis."""

from unittest.mock import patch

from helpers import reply
from pfm_mcp.tools import analytics


def _payload(managed):
    return {
        "total_value_eur": 1300.0,
        "targets_sum_pct": 100.0,
        "allocations": [
            {
                "asset_type": "etf",
                "current_pct": 90.0,
                "target_pct": 40.0,
                "drift_pct": 50.0,
                "drift_eur": -650.0,
            }
        ],
        "actions": [],
        "managed": managed,
    }


def test_prints_managed_line():
    managed = {"value_eur": 1000.0, "pct": 76.9, "portfolios": ["Robo"]}
    with patch("pfm_mcp.client._get", return_value=reply(_payload(managed))):
        result = analytics.rebalance_analysis()
    assert "Managed (not tradable)" in result
    assert "76.9%" in result
    assert "in Robo" in result


def test_no_managed_line_when_zero_or_missing():
    zero = {"value_eur": 0.0, "pct": 0.0, "portfolios": []}
    for managed in (zero, None):
        with patch("pfm_mcp.client._get", return_value=reply(_payload(managed))):
            assert "Managed" not in analytics.rebalance_analysis()
