"""Output tests for the stress_test tool."""

from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import analytics


def _payload(retirement):
    return {
        "scenario": "equity_bear",
        "label": "Equity bear market",
        "portfolio_value_eur": 1000.0,
        "loss_eur": 300.0,
        "loss_pct_of_portfolio": 30.0,
        "net_worth_before_eur": 5000.0,
        "net_worth_after_eur": 4700.0,
        "by_asset_type": [{"asset_type": "stock", "loss_eur": 300.0, "loss_pct": 30}],
        "by_portfolio": [{"portfolio": "Self", "loss_eur": 300.0, "loss_pct": 30}],
        "top_losers": [{"symbol": "EXCO", "loss_eur": 300.0, "loss_pct": 30}],
        "assumed_equity": ["XF1"],
        "retirement_after": retirement,
    }


def test_stress_test_prints_summary_and_retirement():
    ret = {
        "scenarios_before": [
            {"retirement_age": 65, "case": "base", "verdict": "lasts"}
        ],
        "scenarios_after": [
            {"retirement_age": 65, "case": "base", "verdict": "depleted"}
        ],
    }
    with patch("pfm_mcp.client._get", return_value=reply(_payload(ret))) as get:
        result = analytics.stress_test()
    assert "STRESS TEST — Equity bear market" in result
    assert "30.0% of" in result
    assert "→" in result and "lasts → depleted" in result
    assert "Top losers:" in result and "EXCO" in result
    assert "assumed all equity: XF1" in result
    assert requested_url(get) == "/api/v1/analytics/stress?scenario=equity_bear"


def test_stress_test_without_retirement_plan():
    with patch("pfm_mcp.client._get", return_value=reply(_payload(None))):
        result = analytics.stress_test(scenario="usd_down")
    assert "plan not set up" in result


def test_stress_test_reports_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        assert "Error running stress test" in analytics.stress_test()
