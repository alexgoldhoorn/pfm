"""Output tests for research_scorecard."""

import urllib.error
from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import research

AGG = {
    "n": 4,
    "direction_hit_rate_pct": 75.0,
    "median_abs_target_error_pct": 12.5,
    "mean_implied_return_pct": 20.0,
    "mean_realised_return_pct": 8.0,
    "reached_target_rate_pct": 50.0,
}
EMPTY = {"n": 0}
ENTRY = {
    "symbol": "EXM",
    "date": "2025-01-10",
    "recommendation": "BUY",
    "source": "llm",
    "at_horizon": {
        "date": "2026-01-10",
        "price": 120.0,
        "realised_return_pct": 20.0,
        "target_error_pct": -4.0,
        "direction_hit": True,
    },
    "at_next": None,
}


def _payload():
    return {
        "as_of": "2026-11-04",
        "total": 5,
        "pending": 1,
        "totals": {"at_horizon": AGG, "at_next": EMPTY},
        "by_recommendation": {"BUY": {"at_horizon": AGG, "at_next": EMPTY}},
        "by_confidence": {},
        "by_source": {},
        "best": [ENTRY],
        "worst": [],
        "method": "Method sentence.",
    }


def test_scorecard_prints_totals_breakdown_and_best():
    with patch("pfm_mcp.client._get", return_value=reply(_payload())) as get:
        result = research.research_scorecard(symbol="exm")
    assert "RESEARCH SCORECARD as of 2026-11-04 (5 reports, 1 pending)" in result
    assert "at horizon: n=4, direction hit 75.0%" in result
    assert "median |target error| 12.5%" in result
    assert "at next report: n=0, direction hit n/a" in result
    assert "BUY (horizon)" in result
    assert "Best:" in result and "EXM 2025-01-10 BUY (llm)" in result
    assert "2026-01-10 @ 120.0 +20.0% (vs target -4.0%) hit" in result
    assert "next n/a" in result
    assert result.endswith("Method sentence.")
    assert requested_url(get) == "/api/v1/research/scorecard?symbol=exm"


def test_scorecard_empty_and_error():
    with patch("pfm_mcp.client._get", return_value=reply({"total": 0})):
        assert "No research history" in research.research_scorecard()
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        assert "Error fetching the research scorecard:" in research.research_scorecard()
