"""Output tests for the networth tools; the API is patched at pfm_mcp.client._get."""

import urllib.error
from unittest.mock import patch

from helpers import reply
from pfm_mcp.tools import networth


def test_goals_lists_progress_and_on_track_status():
    # Field names as GET /api/v1/goals/ actually returns them. This test used to
    # pin target_amount/current_value/... which the API never sent, so the tool
    # printed "0.00 EUR / 0.00 EUR" for every goal while the test stayed green.
    payload = [
        {
            "name": "Retirement",
            "target_amount_eur": 500000.0,
            "current_networth_eur": 100000.0,
            "progress_pct": 20.0,
            "on_track": True,
            "target_date": "2045-01-01",
            "required_monthly_eur": 800.0,
            "projected_value_eur": 510000.0,
            "monthly_contribution_eur": 600.0,
            "shortfall_eur": 0.0,
        }
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = networth.goals()
    assert "Retirement: 100,000.00 EUR / 500,000.00 EUR" in result
    assert "projected 510,000.00 EUR" in result
    assert "✅ ON TRACK" in result
    assert "need 800.00 EUR/mo" in result


def test_goals_reports_shortfall_when_behind():
    payload = [
        {
            "name": "House",
            "target_amount_eur": 100000.0,
            "current_networth_eur": 20000.0,
            "progress_pct": 20.0,
            "on_track": False,
            "target_date": "2030-01-01",
            "required_monthly_eur": 1500.0,
            "projected_value_eur": 70000.0,
            "monthly_contribution_eur": 500.0,
            "shortfall_eur": 30000.0,
        }
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = networth.goals()
    assert "⚠️ BEHIND" in result
    assert "shortfall 30,000.00 EUR" in result
    assert "contributing 500.00 EUR/mo" in result


def test_goals_reports_status_pace_and_notes():
    payload = [
        {
            "name": "Car",
            "target_amount_eur": 10000.0,
            "current_networth_eur": 12000.0,
            "progress_pct": 120.0,
            "on_track": True,
            "status": "achieved",
            "target_date": "2027-01-01",
            "required_monthly_eur": 0,
            "projected_value_eur": 12000.0,
            "monthly_contribution_eur": 100.0,
            "shortfall_eur": 0.0,
            "start_date": "2026-01-01",
            "gained_since_start_eur": 4000.0,
            "gap_closed_pct": 100.0,
            "actual_monthly_eur": 410.0,
            "pace": "ahead",
            "notes": "Second-hand estate",
        },
        {
            "name": "Old",
            "target_amount_eur": 10000.0,
            "current_networth_eur": 2000.0,
            "progress_pct": 20.0,
            "on_track": False,
            "status": "overdue",
            "target_date": "2020-01-01",
            "required_monthly_eur": None,
            "projected_value_eur": 2000.0,
            "monthly_contribution_eur": 100.0,
            "shortfall_eur": 8000.0,
            "start_date": "2026-10-20",
            "gained_since_start_eur": -50.0,
            "gap_closed_pct": 0.0,
            "actual_monthly_eur": None,
            "pace": None,
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = networth.goals()
    assert "🏁 ACHIEVED" in result
    assert "since 2026-01-01: +4,000.00 EUR (100% of the gap closed)" in result
    assert "pace 410.00 EUR/mo actual — AHEAD of what is needed" in result
    assert "notes: Second-hand estate" in result
    assert "⏰ OVERDUE (date passed)" in result
    assert (
        "since 2026-10-20: -50.00 EUR (0% of the gap closed), pace after the first month"
        in result
    )


def test_goals_reports_none_defined():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        result = networth.goals()
    assert result == "No goals defined."


def test_goals_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = networth.goals()
    assert "Error fetching goals:" in result


def _networth_payload():
    return {
        "net_worth_eur": 250000.0,
        "brokerage_eur": 150000.0,
        "bank_accounts_eur": 20000.0,
        "bank_accounts": [
            {
                "portfolio_id": 5,
                "name": "Main Bank",
                "balance": 20000.0,
                "currency": "EUR",
                "balance_eur": 20000.0,
                "as_of": "2026-09-10",
            },
            {
                "portfolio_id": 6,
                "name": "Savings Bank",
                "balance": None,
                "currency": None,
                "balance_eur": None,
                "as_of": None,
            },
        ],
        "manual_assets_eur": 300000.0,
        "manual_liabilities_eur": 230000.0,
        "deposits_eur": 10000.0,
        "deposits": [
            {
                "name": "12m deposit",
                "principal": 10000.0,
                "currency": "EUR",
                "interest_rate": 3.0,
                "maturity_date": "2027-01-01",
                "status": "active",
            }
        ],
        "items": [
            {
                "name": "House",
                "category": "property",
                "amount": 300000.0,
                "currency": "EUR",
                "is_liability": 0,
                "amount_eur": 300000.0,
                "updated_at": "2026-01-01 10:00:00",
            },
            {
                "name": "Mortgage",
                "category": "mortgage",
                "amount": 230000.0,
                "currency": "EUR",
                "is_liability": 1,
                "amount_eur": 230000.0,
                "updated_at": "2026-09-01 10:00:00",
            },
        ],
    }


def test_networth_reports_total_components_and_accounts():
    with patch("pfm_mcp.client._get", return_value=reply(_networth_payload())):
        result = networth.networth()
    assert "NET WORTH: 250,000.00 EUR" in result
    assert "Main Bank" in result and "as of 2026-09-10" in result
    assert "12m deposit" in result
    assert "House" in result
    assert "Mortgage" in result and "-230,000.00 EUR" in result


def test_networth_flags_bank_account_without_imported_balance():
    with patch("pfm_mcp.client._get", return_value=reply(_networth_payload())):
        result = networth.networth()
    line = next(ln for ln in result.splitlines() if "Savings Bank" in ln)
    assert "no balance imported" in line


def test_networth_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = networth.networth()
    assert "Error fetching net worth:" in result


def _action_items_payload():
    return {
        "items": [
            {
                "id": "dq:duplicates",
                "category": "data_quality",
                "severity": "high",
                "title": "2 possible duplicates",
                "detail": "Review on the Diagnostics page",
                "link_page": "diagnostics",
                "context": {},
            },
            {
                "id": "watch:ASML",
                "category": "watchlist",
                "severity": "medium",
                "title": "ASML in buy zone",
                "detail": "",
                "link_page": "watchlist",
                "context": {},
            },
        ],
        "generated_at": "2026-09-15T10:00:00",
    }


def test_action_items_lists_items_with_severity():
    with patch("pfm_mcp.client._get", return_value=reply(_action_items_payload())):
        result = networth.action_items()
    assert "ACTION ITEMS (2)" in result
    assert "[HIGH] 2 possible duplicates" in result
    assert "Review on the Diagnostics page" in result
    assert "ASML in buy zone" in result


def test_action_items_filters_by_category():
    with patch("pfm_mcp.client._get", return_value=reply(_action_items_payload())):
        result = networth.action_items(category="data_quality")
    assert "2 possible duplicates" in result
    assert "ASML" not in result


def test_action_items_reports_none_open():
    with patch("pfm_mcp.client._get", return_value=reply({"items": []})):
        assert networth.action_items() == "No open action items."
    with patch("pfm_mcp.client._get", return_value=reply(_action_items_payload())):
        assert (
            networth.action_items(category="goals")
            == "No open action items in category 'goals'."
        )


def test_action_items_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = networth.action_items()
    assert "Error fetching action items:" in result


def _scenario(age, case, **over):
    base = {
        "retirement_age": age,
        "case": case,
        "verdict": "on_track",
        "lasts_until_age": None,
        "pot_at_retirement_eur": 500000,
        "pot_at_horizon_eur": 100000,
        "sustainable_monthly_eur": 3000,
        "gap_monthly_eur": -200,
        "extra_saving_needed_monthly_eur": 0,
    }
    base.update(over)
    return base


def _ok_payload():
    return {
        "status": "ok",
        "as_of": "2026-10-18",
        "plan": {"retirement_age": 65, "statutory_age": 67, "horizon_age": 95},
        "inputs": {
            "financial_assets_eur": 400000,
            "spending_target_monthly_eur": 2800,
            "spending_months_used": 12,
        },
        "scenarios": [
            _scenario(65, "base"),
            _scenario(65, "pessimistic", verdict="short", lasts_until_age=80),
            _scenario(
                67,
                "base",
                verdict="at_risk",
                lasts_until_age=72,
                gap_monthly_eur=500,
                extra_saving_needed_monthly_eur=None,
            ),
            _scenario(67, "pessimistic", verdict="at_risk", lasts_until_age=70),
        ],
        "warnings": ["One income line is a placeholder."],
    }


def test_retirement_readiness_prints_both_ages():
    with patch("pfm_mcp.client._get", return_value=_ok_payload()):
        out = networth.retirement_readiness()
    assert "2026-10-18" in out
    assert "12 months" in out
    assert "age 65" in out and "age 67" in out
    assert "ON TRACK" in out and "AT RISK" in out
    assert "past the horizon (age 95)" in out
    assert "surplus" in out and "short by" in out
    assert "not reachable by saving alone" in out
    assert "lasts until age 80" in out
    assert "Warnings:" in out and "placeholder" in out


def test_retirement_readiness_needs_setup_is_one_line():
    payload = {"status": "needs_setup", "scenarios": []}
    with patch("pfm_mcp.client._get", return_value=payload):
        out = networth.retirement_readiness()
    assert out.count("\n") == 0
    assert out.startswith("Retirement readiness: needs_setup")
    assert "enter a birth date on the Net Worth page" in out


def test_networth_mentions_stale_lines():
    nw = {"net_worth_eur": 100000}
    bs = {
        "totals": {
            "assets_eur": 120000,
            "liabilities_eur": 20000,
            "financial_assets_eur": 70000,
            "liquid_eur": 30000,
            "stale_lines": 2,
        },
        "groups": [
            {
                "key": "bank",
                "lines": [
                    {"label": "Example Bank", "stale": True, "note": "no import"},
                    {"label": "Example Fund", "stale": True, "age_days": 9},
                    {"label": "Fresh", "stale": False},
                ],
            }
        ],
    }

    def fake_get(path, params=None):
        return bs if "balance-sheet" in path else nw

    with patch("pfm_mcp.client._get", side_effect=fake_get):
        out = networth.networth()
    assert "Balance sheet: assets" in out
    assert "2 stale line(s)" in out
    assert "Example Bank — no import" in out
    assert "Example Fund — 9 days old" in out
    assert "Fresh" not in out

    def failing(path, params=None):
        if "balance-sheet" in path:
            raise RuntimeError("boom")
        return nw

    with patch("pfm_mcp.client._get", side_effect=failing):
        out = networth.networth()
    assert "NET WORTH" in out
    assert "Balance sheet: unavailable (boom)" in out


def test_retirement_readiness_missing_lasts_until_is_na():
    payload = _ok_payload()
    del payload["scenarios"][0]["lasts_until_age"]
    with patch("pfm_mcp.client._get", return_value=payload):
        out = networth.retirement_readiness()
    assert "Money lasts n/a" in out


def test_retirement_readiness_other_status_default_reason():
    with patch("pfm_mcp.client._get", return_value={"status": "insufficient_data"}):
        out = networth.retirement_readiness()
    assert out == "Retirement readiness: insufficient_data — see the Net Worth page"


def test_networth_missing_stale_lines_is_na_and_still_lists_stale():
    bs = {
        "totals": {"assets_eur": 1000},
        "groups": [{"lines": [{"label": "Old", "stale": True, "age_days": 12}]}],
    }
    with patch(
        "pfm_mcp.client._get",
        side_effect=lambda p, params=None: (
            bs if "balance-sheet" in p else {"net_worth_eur": 1}
        ),
    ):
        out = networth.networth()
    assert "n/a stale line(s)" in out
    assert "0 stale" not in out
    assert "Old — 12 days old" in out
