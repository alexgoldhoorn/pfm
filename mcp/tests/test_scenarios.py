"""Output tests for the what-if scenario tools."""

from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import scenarios

_LEVEL = {
    "monthly_spend_eur": 3000.0,
    "monthly_gap_eur": 2000.0,
    "months_on_cash": 6,
    "months_with_investments": 16,
    "sustainable": False,
}
_CUT = {
    "category": "Restaurants",
    "tier": "optional",
    "cut_pct": 100.0,
    "saves_monthly_eur": 300.0,
}


def test_job_loss_prints_levels_cuts_and_warnings():
    payload = {
        "as_of": "2026-07-15",
        "income_lost_monthly_eur": 3000.0,
        "income_remaining_monthly_eur": 1000.0,
        "cash_available_eur": 12000.0,
        "investments_after_tax_eur": 20000.0,
        "levels": {
            "as_today": _LEVEL,
            "no_optional": {**_LEVEL, "months_with_investments": None},
            "lean": {**_LEVEL, "sustainable": True},
        },
        "cut_first": [_CUT],
        "warnings": ["Only 3 complete months"],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = scenarios.scenario_job_loss(benefit_monthly_eur=900, benefit_months=12)
    assert "JOB LOSS SCENARIO (as of 2026-07-15)" in result
    assert "cash lasts 6 months, with investments 16 months" in result
    assert "with investments more than 50 years" in result
    assert "income still covers it" in result
    assert "Restaurants" in result and "-100%" in result
    assert "! Only 3 complete months" in result
    url = requested_url(get)
    assert url.startswith("/api/v1/scenarios/job-loss?")
    assert "benefit_monthly_eur=900" in url and "benefit_months=12" in url
    assert "income_lost_monthly_eur" not in url


def test_job_loss_reports_an_api_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        assert scenarios.scenario_job_loss() == "Error running job-loss scenario: boom"


def test_income_change_with_a_surplus():
    payload = {
        "as_of": "2026-07-15",
        "income_monthly_eur": 4000.0,
        "new_income_monthly_eur": 3600.0,
        "net_monthly_before_eur": 1000.0,
        "net_monthly_after_eur": 600.0,
        "savings_rate_before_pct": 25.0,
        "savings_rate_after_pct": 16.7,
        "goals_planned_monthly_eur": 700.0,
        "goals_still_fit": False,
        "to_keep_saving_the_same": {
            "needed_monthly_eur": 400.0,
            "available_from_cuts_monthly_eur": 700.0,
            "cut_first": [_CUT],
        },
        "plan_impact": {
            "contribution_before_monthly_eur": 500.0,
            "contribution_after_monthly_eur": 100.0,
            "milestones": [
                {
                    "calendar_year": 2031,
                    "net_worth_before_eur": 90000.0,
                    "net_worth_after_eur": 60000.0,
                }
            ],
        },
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = scenarios.scenario_income_change(change_pct=-10)
    assert "savings rate 25.0% → 16.7%" in result
    assert "no longer fit" in result
    assert "To keep saving the same" in result and "Restaurants" in result
    assert "2031: net worth" in result
    assert "change_pct=-10" in requested_url(get)
    assert "new_income_monthly_eur" not in requested_url(get)


def test_income_change_with_a_shortfall():
    payload = {
        "net_monthly_after_eur": -1000.0,
        "savings_rate_after_pct": None,
        "shortfall": {"levels": {"as_today": _LEVEL}, "cut_first": []},
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = scenarios.scenario_income_change(new_income_monthly_eur=2000)
    assert "Spending now exceeds income" in result
    assert "Spending as today: cash lasts 6 months" in result
    assert "n/a" in result


def test_house_purchase_prints_funding_and_flags():
    payload = {
        "price_eur": 300000.0,
        "cash_needed_eur": 96000.0,
        "down_payment_eur": 60000.0,
        "purchase_costs_eur": 36000.0,
        "funding": {
            "from_cash_eur": 78000.0,
            "investments_sold_eur": 18000.0,
            "tax_on_sale_eur": 0.0,
            "shortfall_eur": 0.0,
        },
        "mortgage": {
            "principal_eur": 240000.0,
            "rate_pct": 3.0,
            "term_years": 30,
            "monthly_payment_eur": 1000.0,
            "total_interest_eur": 120000.0,
        },
        "monthly": {"net_before_eur": 1000.0, "net_after_eur": 0.0},
        "debt_ratio_pct": 25.0,
        "debt_ratio_limit_pct": 35.0,
        "max_price_at_debt_limit_eur": 400000.0,
        "after": {
            "liquid_eur": 22000.0,
            "emergency_months": 6.0,
            "investments_eur": 32000.0,
        },
        "flags": ["Needs selling investments"],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = scenarios.scenario_house_purchase(300000, existing_loans_repaid=True)
    assert "HOUSE PURCHASE SCENARIO" in result
    assert "at 3% for 30 years" in result
    assert "Loan payments / income: 25.0% (limit 35%)" in result
    assert "(6.0 months of spending)" in result
    assert "! Needs selling investments" in result
    url = requested_url(get)
    assert "price_eur=300000" in url and "existing_loans_repaid=true" in url


def test_market_crash_defaults_to_the_ai_bubble_preset():
    payload = {
        "scenario": "AI bubble bursts",
        "value_before_eur": 100000.0,
        "value_after_eur": 60000.0,
        "drop_pct": 40.0,
        "loss_eur": 40000.0,
        "net_worth_before_eur": 150000.0,
        "net_worth_after_eur": 110000.0,
        "gain_needed_to_recover_pct": 66.7,
        "monthly_contribution_eur": 500.0,
        "monthly_contribution_source": "primary Wealth Simulator scenario",
        "recovery": [
            {
                "case": "expected",
                "annual_return_pct": 6.0,
                "months_with_contributions": 48,
                "months_without_contributions": None,
            }
        ],
        "historical_reference": [
            {"name": "Covid (S&P 500, 2020)", "drop_pct": -34, "years_to_recover": 0.5}
        ],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = scenarios.scenario_market_crash()
    assert "MARKET CRASH SCENARIO — AI bubble bursts" in result
    assert "Gain needed to recover: 66.7%" in result
    assert "48 months with contributions, more than 50 years without" in result
    assert "Covid (S&P 500, 2020): -34%, 0.5 years to recover" in result
    assert "scenario=ai_bubble" in requested_url(get)
    assert "drop_pct" not in requested_url(get)

    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        scenarios.scenario_market_crash(drop_pct=40)
    assert "drop_pct=40" in requested_url(get)
    assert "scenario=" not in requested_url(get)
