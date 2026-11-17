"""Output tests for the loan tools."""

from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import loans as loans_tools

_LIST = [{"id": 3, "name": "Example Mortgage"}]
_OVERVIEW = {
    "summary": {
        "id": 3,
        "name": "Example Mortgage",
        "lender": "Example Bank",
        "principal_eur": 100000.0,
        "start_date": "2020-01-01",
        "repaid_eur": 20000.0,
        "repaid_pct": 20.0,
        "balance_eur": 80000.0,
        "annual_rate_pct": 3.0,
        "rate_as_of": "2025-01-01",
        "monthly_payment_eur": 500.0,
        "end_date": "2040-01-01",
        "months_left": 180,
        "interest_paid_this_year_eur": 1200.0,
        "interest_scheduled_this_year_eur": 2000.0,
        "interest_remaining_eur": 30000.0,
        "anchored": True,
        "paid_to_date": {
            "complete": True,
            "principal_eur": 20000.0,
            "interest_eur": 9000.0,
            "payments_count": 77,
            "from_date": "2020-02-01",
            "anchor_gap_eur": 0.5,
        },
    },
    "rate_periods": [{"from_date": "2020-01-01", "annual_rate_pct": 2.4}],
    "equity": {"equity_eur": 120000.0, "ltv_pct": 40.0, "note": None},
    "reconciliation": {
        "rows": [
            {
                "month": "2026-04",
                "complete": True,
                "scheduled_eur": 500.0,
                "actual_eur": 500.0,
                "status": "ok",
            },
            {
                "month": "2026-05",
                "complete": True,
                "scheduled_eur": 500.0,
                "actual_eur": 530.0,
                "status": "differs",
            },
            {
                "month": "2026-06",
                "complete": False,
                "scheduled_eur": 500.0,
                "actual_eur": 0,
                "status": "pending",
            },
        ],
        "hint": "payment changed: the rate may have been revised",
    },
}


def _get(path, params=None):
    return reply(_LIST if path.endswith("/loans") else _OVERVIEW)


def test_loans_prints_every_headline_figure():
    with patch("pfm_mcp.client._get", side_effect=_get):
        out = loans_tools.loans()
    for text in (
        "Example Mortgage (Example Bank)",
        "80,000.00 EUR",
        "Borrowed:          100,000.00 EUR on 2020-01-01; 20,000.00 EUR repaid (20.0%)",
        "180 payments left, ends 2040-01-01",
        "3.00%  (from 2025-01-01)",
        "500.00 EUR",
        "1,200.00 EUR paid this year of 2,000.00 EUR",
        "30,000.00 EUR still to pay",
        "20,000.00 EUR principal + 9,000.00 EUR interest in 77 payments",
        "2020-01-01 2.40%",
        "120,000.00 EUR  (LTV 40.0%)",
        "Bank, 2026-05:",
        "charged 530.00 EUR -> differs",
        "payment changed",
    ):
        assert text in out, text


def test_loans_without_history_or_property():
    ov = {
        **_OVERVIEW,
        "rate_periods": [],
        "equity": {"equity_eur": None, "reason": "No property"},
        "reconciliation": {"rows": None, "reason": "No spending category"},
    }
    ov["summary"] = {**_OVERVIEW["summary"], "paid_to_date": {"complete": False}}
    with patch(
        "pfm_mcp.client._get",
        side_effect=lambda p, q=None: _LIST if p.endswith("/loans") else ov,
    ):
        out = loans_tools.loans()
    assert "Paid to date" not in out and "nothing earlier is known" in out
    assert "Equity:            n/a (No property)" in out
    assert "Bank:              n/a (No spending category)" in out


def test_loans_empty_and_error():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        assert "No loans entered" in loans_tools.loans()
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("down")):
        assert "Error fetching loans: down" in loans_tools.loans()


_WHAT_IF = {
    "lump_date": "2026-06-15",
    "baseline": {
        "end_date": "2040-01-01",
        "monthly_payment_eur": 500.0,
        "interest_remaining_eur": 30000.0,
    },
    "scenario": {
        "end_date": "2040-01-01",
        "monthly_payment_eur": 560.0,
        "interest_remaining_eur": 36000.0,
    },
    "interest_saved_eur": -6000.0,
    "months_saved": 0,
    "invest": None,
    "rate_change": {
        "previous_rate_pct": 3.0,
        "new_rate_pct": 4.0,
        "balance_eur": 80000.0,
        "payment_diff_eur": 60.0,
    },
}


def test_what_if_passes_the_rate_change_and_prints_the_cost():
    with patch("pfm_mcp.client._get", return_value=reply(_WHAT_IF)) as get:
        out = loans_tools.loan_what_if(
            loan_id=3, rate_change_pts=1.0, rate_from="2026-06-15"
        )
    url = requested_url(get)
    assert url.startswith("/api/v1/networth/loans/3/what-if?")
    assert "rate_change_pts=1.0" in url and "rate_from=2026-06-15" in url
    assert "lump_sum" not in url
    assert "Rate 3.00% -> 4.00%" in out
    assert "500.00 EUR -> 560.00 EUR" in out
    assert "Interest extra: 6,000.00 EUR" in out
    assert "+60.00 EUR a month" in out


def test_what_if_picks_the_only_loan_and_asks_when_several():
    calls = []

    def fake(path, params=None):
        calls.append(path)
        return reply(_LIST if path.endswith("/loans") else _WHAT_IF)

    with patch("pfm_mcp.client._get", side_effect=fake):
        loans_tools.loan_what_if(lump_sum=1000)
    assert calls[-1] == "/api/v1/networth/loans/3/what-if"
    two = _LIST + [{"id": 4, "name": "Example Car Loan"}]
    with patch("pfm_mcp.client._get", return_value=reply(two)):
        out = loans_tools.loan_what_if(lump_sum=1000)
    assert "Give loan_id" in out and "4: Example Car Loan" in out


def test_what_if_reports_an_api_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("HTTP 422")):
        out = loans_tools.loan_what_if(loan_id=3, lump_sum=1)
    assert out.startswith("Error calculating the what-if")
