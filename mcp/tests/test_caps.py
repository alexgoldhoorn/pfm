"""Output test for the position_caps tool."""

import urllib.error
from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import portfolio


def test_position_caps_lists_status_headroom_and_default_marker():
    payload = {
        "as_of": "2026-11-04",
        "total_value_eur": 1000.0,
        "breached": 1,
        "near": 0,
        "caps": [
            {
                "id": 1,
                "scope": "symbol",
                "key": "EXA",
                "max_pct": 50.0,
                "current_pct": 60.0,
                "headroom_pct": -10.0,
                "headroom_eur": -100.0,
                "status": "breached",
                "implicit": False,
                "note": "core holding",
            },
            {
                "id": None,
                "scope": "symbol",
                "key": "EXC",
                "max_pct": 5.0,
                "current_pct": 2.0,
                "headroom_pct": 3.0,
                "headroom_eur": 30.0,
                "status": "ok",
                "implicit": True,
                "note": None,
            },
        ],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = portfolio.position_caps()
    assert requested_url(get) == "/api/v1/caps/status"
    assert "1 breached, 0 near" in result
    assert (
        "[BREACHED] symbol EXA: 60.0% of 50%  headroom -10.0 pts (-100 EUR)" in result
    )
    assert "core holding" in result
    assert "symbol EXC (default): 2.0% of 5%" in result


def test_position_caps_empty():
    with patch("pfm_mcp.client._get", return_value=reply({"caps": []})):
        assert "No position caps" in portfolio.position_caps()


def test_position_caps_reports_errors():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("down")):
        assert portfolio.position_caps().startswith("Error fetching position caps")
