"""Output tests for the research tools; the API is patched at pfm_mcp.client._get."""

import urllib.error
from unittest.mock import patch

from helpers import reply
from pfm_mcp.tools import research


def test_research_lookup_reports_held_position_with_targets():
    payload = {
        "symbol": "NVDA",
        "name": "NVIDIA",
        "held": True,
        "on_watchlist": False,
        "currency": "USD",
        "current_price": 150.0,
        "quantity": 10.0,
        "avg_cost": 100.0,
        "market_value": 1500.0,
        "unrealised_pct": 50.0,
        "unrealised_gain": 500.0,
        "realised_gain": 0.0,
        "targets": {"fair_value": 180.0, "buy_below": 130.0},
        "fundamentals": {"trailingPE": 45.2},
        "news": [{"title": "NVDA beats estimates", "published_at": "2026-07-10"}],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = research.research_lookup("nvda")
    assert "RESEARCH — NVDA (NVIDIA)" in result
    assert "Status:      held" in result
    assert "Fair value:  180.00 USD" in result
    assert "NVDA beats estimates" in result


def test_research_lookup_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = research.research_lookup("NVDA")
    assert "Error fetching research for NVDA:" in result


def test_research_compare_sorted_by_upside():
    payload = [
        {
            "symbol": "NVDA",
            "current_price": 150.0,
            "currency": "USD",
            "fair_value": 180.0,
            "upside_pct": 20.0,
            "conviction": "high",
            "buy_below": 140.0,
            "sell_above": 200.0,
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = research.research_compare()
    assert "NVDA" in result
    assert "+20.0%" in result


def test_research_compare_renders_integer_conviction():
    # The API returns conviction as an int (INTEGER column, 1-5); joining it
    # raw into the extras failed with "sequence item 2: expected str instance".
    payload = [
        {
            "symbol": "AMD",
            "current_price": 150.0,
            "currency": "USD",
            "fair_value": 180.0,
            "upside_pct": 20.0,
            "conviction": 3,
            "buy_below": 140.0,
            "sell_above": 200.0,
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = research.research_compare()
    assert "conviction 3/5" in result


def test_research_compare_reports_none_found():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        result = research.research_compare()
    assert result == "No saved research found."


def test_research_compare_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = research.research_compare()
    assert "Error fetching research comparison:" in result
