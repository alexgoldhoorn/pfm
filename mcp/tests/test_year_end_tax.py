"""Output tests for year_end_tax."""

import urllib.error
from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import analytics

PAYLOAD = {
    "year": 2026,
    "realised_gain_eur": 500.0,
    "income_eur": 0.0,
    "harvestable_loss_eur": -400.0,
    "estimated_tax_now_eur": 95.0,
    "estimated_tax_after_harvest_eur": 19.0,
    "estimated_tax_saved_eur": 76.0,
    "candidates": [
        {
            "symbol": "LOS",
            "unrealised_loss_eur": -400.0,
            "tax_saved_eur": 76.0,
            "wash_sale_risk": False,
        },
        {
            "symbol": "REC",
            "unrealised_loss_eur": -100.0,
            "tax_saved_eur": 19.0,
            "wash_sale_risk": True,
        },
    ],
    "carry_forward": {"total_eur": 200.0, "by_year": {"2024": 200.0}},
    "base_after_carry_forward_eur": 300.0,
    "deadline": {"date": "2026-12-31", "days_left": 51},
    "wash_blocked": [
        {"symbol": "REC", "last_buy": "2026-10-31", "until": "2026-12-31"}
    ],
    "notes": ["carry-forward assumes the losses were declared"],
}


def test_year_end_tax_output_and_url():
    with patch("pfm_mcp.client._get", return_value=reply(PAYLOAD)) as g:
        out = analytics.year_end_tax(2026)
    assert requested_url(g) == "/api/v1/analytics/tax-optimizer?year=2026"
    assert "YEAR-END TAX 2026" in out
    assert "51 days left" in out
    assert "LOS" in out and "2-month rule" in out
    assert "from 2024" in out
    assert "Blocked: REC" in out
    assert "declared" in out


def test_year_end_tax_default_year_sends_no_param():
    with patch("pfm_mcp.client._get", return_value=reply(PAYLOAD)) as g:
        analytics.year_end_tax()
    assert requested_url(g) == "/api/v1/analytics/tax-optimizer"


def test_year_end_tax_error():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("down")):
        assert analytics.year_end_tax().startswith("Error fetching year-end tax")
