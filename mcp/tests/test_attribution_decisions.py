"""Output tests for return_attribution, decisions and the managed-portfolio marker."""

import urllib.error
from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import analytics, portfolio, research


def test_return_attribution_prints_totals_and_tops():
    payload = {
        "period": {"start": "2026-01-01", "end": "2026-06-30", "days": 180},
        "totals": {
            "start_value_eur": 2000.0,
            "end_value_eur": 2600.0,
            "bought_eur": 500.0,
            "sold_eur": 0.0,
            "income_eur": 4.25,
            "gain_eur": 104.25,
            "return_pct": 4.63,
            "positions": 3,
        },
        "top_contributors": [
            {
                "symbol": "UPX",
                "gain_eur": 200.0,
                "contribution_pct": 10.0,
                "priced": True,
            }
        ],
        "top_detractors": [
            {
                "symbol": "DNX",
                "gain_eur": -100.0,
                "contribution_pct": -5.0,
                "priced": False,
            }
        ],
        "by_type": [
            {"asset_type": "stock", "gain_eur": 200.0, "contribution_pct": 10.0}
        ],
        "by_portfolio": [
            {"portfolio": "Self", "gain_eur": 204.25, "contribution_pct": 10.21}
        ],
        "unpriced": ["DNX"],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = analytics.return_attribution(period="ytd", portfolio_id=2)
    assert "RETURN ATTRIBUTION 2026-01-01 → 2026-06-30" in result
    assert "+4.63%, simple Dietz" in result
    assert "Top contributors:" in result and "UPX" in result
    assert "Top detractors:" in result and "(at cost: no price stored)" in result
    assert "By asset type:" in result and "stock" in result
    assert "Unpriced at a boundary: DNX" in result
    assert (
        requested_url(get) == "/api/v1/analytics/attribution?period=ytd&portfolio_id=2"
    )


def test_return_attribution_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.return_attribution()
    assert "Error fetching attribution:" in result


def test_decisions_prints_entries_with_price_move():
    payload = {
        "items": [
            {
                "id": 3,
                "decided_on": "2026-09-01",
                "action": "hold",
                "title": "Hold Example below the sell gate",
                "symbol": "EXM",
                "status": "due",
                "price_at_decision": 80.0,
                "price_now": 100.0,
                "price_change_pct": 25.0,
                "rationale": "Still under the gate",
                "expectation": "Gate reached in a year",
                "review_on": "2026-10-30",
            },
            {
                "id": 4,
                "decided_on": "2026-08-01",
                "action": "sell",
                "title": "Sell the dust",
                "status": "reviewed",
                "outcome_rating": "good",
                "outcome": "Sold",
            },
        ],
        "total": 2,
        "due": 1,
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = research.decisions(status="all", symbol="EXM")
    assert "DECISIONS (all, 2; 1 due for review)" in result
    assert "HOLD      Hold Example below the sell gate  [EXM]  ⏰ review due" in result
    assert "price then 80.0, now 100.0 (+25.0%)" in result
    assert "why: Still under the gate" in result
    assert "outcome (good): Sold" in result
    assert requested_url(get) == "/api/v1/decisions/?status=all&symbol=EXM"


def test_decisions_empty_and_error():
    with patch("pfm_mcp.client._get", return_value=reply({"items": [], "total": 0})):
        assert research.decisions() == "No open decisions in the journal."
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        assert "Error fetching decisions:" in research.decisions()


def test_list_portfolios_marks_managed_accounts():
    payload = [
        {
            "id": 1,
            "name": "Broker",
            "currency": "EUR",
            "description": "",
            "is_managed": False,
        },
        {
            "id": 2,
            "name": "Robo",
            "currency": "EUR",
            "description": "",
            "is_managed": True,
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.list_portfolios()
    lines = result.splitlines()
    assert "[managed: no price signals]" not in lines[1]
    assert "[managed: no price signals]" in lines[2]
