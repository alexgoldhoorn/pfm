"""Output tests for the spending tools; the API is patched at pfm_mcp.client._get."""

import urllib.error
from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.tools import spending


def _budget_line(label, planned, actual, favourable_when_under, line_type, **extra):
    variance = planned - actual if favourable_when_under else actual - planned
    line = {
        "line_id": 1,
        "line_type": line_type,
        "ref_key": label,
        "label": label,
        "notes": None,
        "link_id": None,
        "link_label": None,
        "link_amount_eur": None,
        "monthly_amount": planned,
        "planned_eur": {"2026-09": planned},
        "actual_eur": {"2026-09": actual},
        "planned_total": planned,
        "actual_total": actual,
        "variance_eur": variance,
        "variance_pct": (variance / planned * 100) if planned else None,
        "favourable": variance >= 0,
    }
    line.update(extra)
    return line


def _budget_section(key, label, lines, favourable_when_under, unbudgeted=()):
    planned = sum(ln["planned_total"] for ln in lines)
    actual = sum(ln["actual_total"] for ln in lines) + sum(
        u["actual_total"] for u in unbudgeted
    )
    variance = planned - actual if favourable_when_under else actual - planned
    return {
        "key": key,
        "label": label,
        "favourable_when_under": favourable_when_under,
        "lines": lines,
        "unbudgeted": list(unbudgeted),
        "unbudgeted_suppressed": False,
        "planned_total": planned,
        "actual_total": actual,
        "variance_eur": variance,
        "variance_pct": None,
        "favourable": variance >= 0,
    }


def _budget_payload(active=True):
    salary = 3000.0 if active else 0.0
    groceries = 650.0 if active else 0.0
    mortgage = 900.0 if active else 0.0
    unbudgeted = (
        [
            {
                "label": "Pets",
                "ref_key": "Pets",
                "actual_eur": {"2026-09": 40.0},
                "actual_total": 40.0,
            }
        ]
        if active
        else []
    )
    return {
        "budget_id": 1,
        "budget_name": "Baseline",
        "is_active": True,
        "months": ["2026-09"],
        "sections": [
            _budget_section(
                "income",
                "Income",
                [_budget_line("Salary", 3000.0, salary, False, "income")],
                False,
            ),
            _budget_section(
                "spending",
                "Spending",
                [_budget_line("Groceries", 500.0, groceries, True, "spending")],
                True,
                unbudgeted,
            ),
            _budget_section(
                "debt",
                "Debt",
                [
                    _budget_line(
                        "Mortgage",
                        900.0,
                        mortgage,
                        True,
                        "debt",
                        link_id=3,
                        link_label="Home loan",
                        link_amount_eur=180000.0,
                    )
                ],
                True,
            ),
            _budget_section("investment", "Investments", [], False),
        ],
        "net": {
            "planned_total": 1600.0,
            "actual_total": salary - groceries - 40.0 - mortgage,
            "variance_eur": -190.0,
            "variance_pct": None,
            "favourable": False,
        },
    }


def test_budget_summary_reports_lines_unbudgeted_and_net():
    from datetime import date

    with (
        patch("pfm_mcp.client._get", return_value=reply(_budget_payload())),
        patch("pfm_mcp.client._today", return_value=date(2026, 9, 30)),
    ):
        result = spending.budget_summary()
    assert "BUDGET — Baseline (2026-09)" in result
    groceries = next(ln for ln in result.splitlines() if "Groceries" in ln)
    assert "✗ -150.00 EUR" in groceries
    assert "unbudgeted: Pets 40.00 EUR" in result
    assert "against Home loan: 180,000.00 EUR outstanding" in result
    assert "Net cash flow" in result
    assert "Month in progress" not in result


def test_budget_summary_warns_month_in_progress():
    from datetime import date

    with (
        patch("pfm_mcp.client._get", return_value=reply(_budget_payload())),
        patch("pfm_mcp.client._today", return_value=date(2026, 9, 15)),
    ):
        result = spending.budget_summary()
    assert "Month in progress (day 15 of 30)" in result


def test_budget_summary_suppresses_verdicts_when_nothing_imported():
    # A month with no imported statements reads as zero actuals, i.e. heroically
    # under budget -- the tool must say so and not hand out a verdict.
    from datetime import date

    with (
        patch("pfm_mcp.client._get", return_value=reply(_budget_payload(active=False))),
        patch("pfm_mcp.client._today", return_value=date(2026, 9, 30)),
    ):
        result = spending.budget_summary()
    assert "No imported activity for 2026-09" in result
    assert "✗" not in result and "✓" not in result


def test_budget_summary_reports_no_active_budget():
    with patch("pfm_mcp.client._get", return_value=reply(None)):
        assert spending.budget_summary() == "No active budget."


def test_budget_summary_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = spending.budget_summary()
    assert "Error fetching budget summary:" in result


_SPENDING = {
    "spent_eur": 1200.0,
    "income_eur": 3000.0,
    "transferred_eur": 500.0,
    "by_category_eur": {"Groceries": 400.0, "Housing": 700.0, "uncategorized": 100.0},
}


_TREND = [
    {"month": "2026-08", "spent_eur": 2000.0, "income_eur": 3100.0, "net_eur": 1100.0},
    {"month": "2026-09", "spent_eur": 1200.0, "income_eur": 3000.0, "net_eur": 1800.0},
]


def test_spending_summary_reports_totals_categories_and_trend():
    with patch("pfm_mcp.client._get", side_effect=[reply(_SPENDING), reply(_TREND)]):
        result = spending.spending_summary(days=30, trend_months=2)
    assert "SPENDING — last 30 days" in result
    assert "1,200.00 EUR" in result
    assert result.index("Housing") < result.index("Groceries")
    assert "MONTHLY TREND" in result and "2026-08" in result


def test_spending_summary_survives_trend_failure():
    with patch(
        "pfm_mcp.client._get",
        side_effect=[reply(_SPENDING), urllib.error.URLError("boom")],
    ):
        result = spending.spending_summary()
    assert "1,200.00 EUR" in result
    assert "MONTHLY TREND" not in result


def test_spending_summary_skips_trend_when_not_requested():
    with patch("pfm_mcp.client._get", side_effect=[reply(_SPENDING)]):
        result = spending.spending_summary(trend_months=0)
    assert "MONTHLY TREND" not in result


def test_spending_summary_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = spending.spending_summary()
    assert "Error fetching spending summary:" in result


def _sp_row(i, amount=-10.0, category="Groceries", is_transfer=False, description=None):
    return {
        "id": i,
        "date": f"2026-01-{(i % 28) + 1:02d}",
        "portfolio_id": 1,
        "portfolio_name": "Example Bank",
        "amount": amount,
        "currency": "EUR",
        "category": category,
        "is_transfer": is_transfer,
        "merchant": None,
        "description": description or f"EXAMPLE SHOP {i}",
        "balance": None,
    }


def test_spending_transactions_lists_rows_and_totals():
    rows = [
        _sp_row(1, -40.0, "Income Tax", description="EXAMPLE TAX OFFICE"),
        _sp_row(2, -60.5, "Income Tax"),
        _sp_row(3, 25.0, "Income"),
    ]
    with patch(
        "pfm_mcp.client._get", return_value=reply({"items": rows, "total": 3})
    ) as urlopen:
        result = spending.spending_transactions(
            query="income tax", start_date="2026-01-01"
        )
    url = requested_url(urlopen)
    assert "q=income%20tax" in url and "start_date=2026-01-01" in url
    assert "3 matching" in result
    assert "Money out:" in result and "-100.50 EUR" in result
    assert "Money in:" in result and "25.00 EUR" in result
    line = next(ln for ln in result.splitlines() if "EXAMPLE TAX OFFICE" in ln)
    assert "2026-01-02" in line and "-40.00 EUR" in line and "Income Tax" in line


def test_spending_transactions_totals_cover_every_page_not_just_shown_rows():
    page1 = [_sp_row(i) for i in range(200)]
    page2 = [_sp_row(i) for i in range(200, 250)]
    with patch(
        "pfm_mcp.client._get",
        side_effect=[
            reply({"items": page1, "total": 250}),
            reply({"items": page2, "total": 250}),
        ],
    ) as urlopen:
        result = spending.spending_transactions(limit=5)
    assert urlopen.call_count == 2
    assert "offset=200" in requested_url(urlopen)
    assert "250 matching" in result
    assert "-2,500.00 EUR" in result
    assert "Showing the newest 5 of 250" in result
    assert sum(1 for ln in result.splitlines() if "EXAMPLE SHOP" in ln) == 5


def test_spending_transactions_keeps_transfers_out_of_totals():
    rows = [_sp_row(1, -40.0), _sp_row(2, -1000.0, "Transfer", is_transfer=True)]
    with patch("pfm_mcp.client._get", return_value=reply({"items": rows, "total": 2})):
        result = spending.spending_transactions()
    assert "-40.00 EUR" in next(ln for ln in result.splitlines() if "Money out:" in ln)
    assert "1 transfer" in result
    assert "[transfer]" in next(
        ln for ln in result.splitlines() if "-1,000.00 EUR" in ln
    )


def test_spending_transactions_reports_no_matches():
    with patch("pfm_mcp.client._get", return_value=reply({"items": [], "total": 0})):
        result = spending.spending_transactions(query="nothing")
    assert result == "No spending transactions match."


def test_spending_transactions_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = spending.spending_transactions()
    assert "Error fetching spending transactions:" in result


# ── cost control: pace, funds, comparison, anomalies, forecast, review ─────


def test_budget_summary_prints_available_pace_fund_and_data_through():
    from datetime import date

    payload = _budget_payload()
    spending_section = next(s for s in payload["sections"] if s["key"] == "spending")
    spending_section["available_total"] = 800.0
    spending_section["pace"] = {
        "status": "at_risk",
        "actual_to_date_eur": 300.0,
        "projected_month_end_eur": 900.0,
        "limit_eur": 800.0,
        "data_through": "2026-09-10",
    }
    spending_section["lines"][0].update(
        period="yearly",
        set_aside_eur=100.0,
        available_total=500.0,
        pace=None,
        fund={
            "yearly_amount_eur": 1200.0,
            "window_start": "2025-10",
            "window_end": "2026-09",
            "spent_eur": 700.0,
            "remaining_eur": 500.0,
        },
    )
    payload["data_through"] = "2026-09-10"
    payload["warnings"] = ["Example warning."]
    with (
        patch("pfm_mcp.client._get", return_value=reply(payload)),
        patch("pfm_mcp.client._today", return_value=date(2026, 9, 30)),
    ):
        result = spending.budget_summary()
    assert "available 800.00 EUR" in result
    assert "at risk; spent 300.00 EUR to 2026-09-10" in result
    assert "projected month end 900.00 EUR (estimate)" in result
    assert "yearly 1,200.00 EUR" in result and "left 500.00 EUR" in result
    assert "last 12 months (2025-10 to 2026-09) spent 700.00 EUR" in result
    assert "Data through: 2026-09-10." in result
    assert "⚠️ Example warning." in result


def test_budget_summary_says_too_early_instead_of_a_projection():
    from datetime import date

    payload = _budget_payload()
    spending_section = next(s for s in payload["sections"] if s["key"] == "spending")
    spending_section["pace"] = {
        "status": "too_early",
        "actual_to_date_eur": 600.0,
        "projected_month_end_eur": None,
        "limit_eur": 400.0,
        "data_through": "2026-09-02",
    }
    with (
        patch("pfm_mcp.client._get", return_value=reply(payload)),
        patch("pfm_mcp.client._today", return_value=date(2026, 9, 3)),
    ):
        result = spending.budget_summary()
    assert "too early to project" in result
    assert "projected month end" not in result
    assert "spent 600.00 EUR to 2026-09-02 against 400.00 EUR" in result


_COMPARE = {
    "month": "2026-06",
    "status": "complete",
    "data_through": "2026-07-05",
    "baseline_months": ["2026-04", "2026-05"],
    "group_by": "category",
    "totals": {
        "actual_eur": 1700.0,
        "baseline_eur": 1200.0,
        "last_year_eur": None,
        "delta_eur": 500.0,
        "delta_pct": 41.7,
        "one_off_eur": 0.0,
        "savings_eur": 0.0,
    },
    "items": [
        {
            "name": "Groceries",
            "actual_eur": 700.0,
            "baseline_eur": 200.0,
            "last_year_eur": None,
            "delta_eur": 500.0,
            "delta_pct": 250.0,
            "tier": "flexible",
        }
    ],
    "warnings": ["Only 2 of the 6 months before 2026-06 have imports."],
}


def test_spending_compare_prints_deltas_and_never_zero_for_missing():
    with patch("pfm_mcp.client._get", return_value=reply(_COMPARE)) as get:
        result = spending.spending_compare(month="2026-06", group_by="category")
    url = requested_url(get)
    assert url.startswith("/api/v1/spending/compare?")
    assert "month=2026-06" in url and "group_by=category" in url
    assert "data through 2026-07-05" in result
    assert "Last year: n/a" in result
    assert "+500.00 EUR (+41.7%)" in result
    assert "[flexible]" in result
    assert "⚠️ Only 2 of the 6 months" in result


def test_spending_compare_without_deltas_prints_na():
    payload = dict(_COMPARE, status="in_progress")
    payload["totals"] = dict(_COMPARE["totals"], delta_eur=None, delta_pct=None)
    payload["items"] = [dict(_COMPARE["items"][0], delta_eur=None, delta_pct=None)]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = spending.spending_compare()
    assert "Change:    n/a EUR" in result
    assert "change        n/a" in result


def test_spending_compare_rejects_a_bad_month_and_reports_api_errors():
    assert "month must look like" in spending.spending_compare(month="June")
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        assert "Error fetching spending comparison:" in spending.spending_compare()


def test_spending_anomalies_lists_flags_without_descriptions():
    payload = {
        "days": 30,
        "data_through": "2026-07-05",
        "items": [
            {
                "id": "anomaly:large:7",
                "kind": "large_charge",
                "date": "2026-07-02",
                "merchant": "Example Market",
                "category": "Groceries",
                "amount_eur": 500.0,
                "usual_eur": 100.0,
                "detail": "500.00 EUR at Example Market; the usual charge is 100.00 EUR.",
            }
        ],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = spending.spending_anomalies(days=30)
    assert requested_url(get) == "/api/v1/spending/anomalies?days=30"
    assert "data through 2026-07-05): 1 flagged" in result
    assert "large_charge" in result and "Example Market" in result
    assert "not verdicts" in result
    with patch("pfm_mcp.client._get", return_value=reply({"items": []})):
        assert "Nothing unusual." in spending.spending_anomalies()


_FORECAST = {
    "as_of": "2026-07-15",
    "data_through": "2026-07-10",
    "accounts": [
        {
            "name": "Example Bank",
            "data_through": "2026-07-10",
            "stale": True,
            "balance_eur": None,
        }
    ],
    "this_month": {
        "month": "2026-07",
        "status": "ok",
        "spent_to_date_eur": 500.0,
        "recurring_remaining_eur": 100.0,
        "variable_remaining_eur": None,
        "projected_spending_eur": None,
        "projected_income_eur": 3000.0,
        "projected_net_eur": None,
        "range": {"spending_low_eur": None, "spending_high_eur": None},
        "projected_balance_end_eur": None,
    },
    "next_months": [
        {
            "month": "2026-08",
            "income_eur": 3000.0,
            "recurring_eur": 1000.0,
            "variable_eur": 800.0,
            "net_eur": 1200.0,
            "projected_balance_eur": -50.0,
            "balance_below_zero": True,
        }
    ],
    "known_bills": [
        {"date": "2026-07-20", "merchant": "Example Gym", "amount_eur": 30.0}
    ],
    "first_below_zero": "2026-08",
    "assumptions": ["Income is the average income of the complete months below."],
    "warnings": ["Example Bank: the latest import is old."],
}


def test_cashflow_forecast_prints_data_through_estimates_and_na():
    with patch("pfm_mcp.client._get", return_value=reply(_FORECAST)) as get:
        result = spending.cashflow_forecast(months=3)
    assert requested_url(get) == "/api/v1/spending/forecast?months=3"
    assert "data through 2026-07-10 (all figures are estimates)" in result
    assert "Projected spending:  n/a" in result
    assert "Balance at month end: n/a" in result
    assert "balance, n/a" not in result and "balance n/a" in result
    assert "STALE" in result and "BELOW ZERO" in result
    assert "first goes below zero in 2026-08" in result
    assert "2026-07-20" in result and "Example Gym" in result
    assert "Assumptions:" in result and "⚠️ Example Bank" in result


def test_spending_review_prints_parts_and_degrades_per_part():
    payload = {
        "month": "2026-06",
        "status": "complete",
        "data_through": "2026-07-05",
        "totals": {"totals": _COMPARE["totals"]},
        "movers": {"categories": _COMPARE["items"], "merchants": []},
        "anomalies": {"count": 0, "items": []},
        "uncategorized": {
            "count": 4,
            "total_eur": 250.0,
            "share_pct": 7.5,
            "highlight": "4 uncategorised outflow(s) are 8% of 2026-06's spending.",
        },
        "tiers": {
            "month": {
                "fixed_eur": 1000.0,
                "flexible_eur": 700.0,
                "optional_eur": 0.0,
                "unclassified_eur": None,
                "savings_eur": 0.0,
            }
        },
        "recurring": {
            "new": [
                {
                    "merchant": "Example Streaming",
                    "typical_eur": 10.0,
                    "cadence": "monthly",
                }
            ],
            "ended": [],
            "missed": [],
            "price_change": [
                {
                    "merchant": "Example Gym",
                    "typical_eur": 30.0,
                    "cadence": "monthly",
                    "price_change_pct": 33.3,
                    "last_eur": 40.0,
                }
            ],
        },
        "budget": None,
        "forecast": None,
        "freshness": [
            {"name": "Example Bank", "data_through": "2026-07-05", "stale": False}
        ],
        "warnings": ["Forecast could not be built: boom"],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)) as get:
        result = spending.spending_review(month="2026-06")
    assert requested_url(get) == "/api/v1/spending/review?month=2026-06"
    assert "data through 2026-07-05" in result
    assert "change +500.00 EUR (+41.7%)" in result
    assert "unclassified n/a" in result
    assert "Example Streaming" in result and "+33.3% (now 40.00 EUR)" in result
    assert "Uncategorised: 4 outflow(s)" in result and "7.5% of spending" in result
    assert "Budget: no active budget." in result
    assert "Forecast: n/a" in result
    assert "Data freshness: Example Bank through 2026-07-05" in result
    assert "⚠️ Forecast could not be built: boom" in result


# ── cost control round 2: planned items, what-if, year view, queue ──────────

_PLANNED_FORECAST = {
    **_FORECAST,
    "this_month": {
        **_FORECAST["this_month"],
        "planned_spending_eur": 200.0,
        "planned_savings_eur": 0.0,
        "planned_income_eur": 0.0,
        "tax_eur": 0.0,
    },
    "next_months": [
        {
            **_FORECAST["next_months"][0],
            "planned_spending_eur": 1000.0,
            "planned_savings_eur": 0.0,
            "planned_income_eur": 500.0,
            "tax_eur": 900.0,
        }
    ],
    "planned_items": [
        {
            "id": 7,
            "date": "2026-08-10",
            "description": "Example Holiday",
            "amount_eur": -1000.0,
            "kind": "spending",
            "status": "projected",
        },
        {
            "id": 8,
            "date": "2026-06-01",
            "description": "Example Old Repair",
            "amount_eur": -300.0,
            "kind": "spending",
            "status": "overdue",
        },
    ],
    "tax_payments": [
        {
            "quarter": "2026-Q2",
            "due": "2026-07-20",
            "amount_eur": 900.0,
            "basis": "average",
        }
    ],
    "what_if": {
        "amount_eur": -500.0,
        "date": "2026-09-15",
        "lowest_balance_eur": -550.0,
        "lowest_balance_month": "2026-09",
        "lowest_balance_without_eur": -50.0,
        "goes_below_zero": True,
    },
}


def test_cashflow_forecast_sends_what_if_and_prints_planned_tax_and_result():
    with patch("pfm_mcp.client._get", return_value=reply(_PLANNED_FORECAST)) as get:
        result = spending.cashflow_forecast(
            months=3, extra_amount=500, extra_month="2026-09", extra_kind="spending"
        )
    url = requested_url(get)
    assert "extra_amount=500" in url and "extra_date=2026-09" in url
    assert "extra_kind=spending" in url
    assert "Planned, this month: spending" in result
    assert "Example Holiday" in result and "overdue" in result
    assert "Estimated quarterly tax payments (an estimate)" in result
    assert "2026-Q2" in result and "basis average" in result
    assert "WHAT IF" in result and "(not saved)" in result
    assert "lowest month-end balance" in result and "550" in result
    assert "in 2026-09, against" in result
    assert "The balance goes below zero." in result


def test_cashflow_forecast_without_what_if_has_no_extra_params_or_block():
    with patch("pfm_mcp.client._get", return_value=reply(_FORECAST)) as get:
        result = spending.cashflow_forecast(months=3)
    assert "extra_" not in requested_url(get)
    assert "WHAT IF" not in result and "Estimated quarterly tax" not in result


def test_cashflow_forecast_refuses_a_half_or_bad_what_if_without_a_request():
    with patch("pfm_mcp.client._get") as get:
        for kwargs in (
            {"extra_amount": 100},
            {"extra_month": "2026-09"},
            {"extra_amount": 100, "extra_month": "soon"},
            {"extra_amount": 100, "extra_month": "2026-09", "extra_kind": "x"},
            {"extra_amount": -1, "extra_month": "2026-09"},
        ):
            assert spending.cashflow_forecast(**kwargs).startswith("Error:")
        get.assert_not_called()


def test_planned_items_prints_status_and_na_when_the_forecast_fails():
    items = [
        {
            "id": 7,
            "date": "2026-08-10",
            "description": "Example Holiday",
            "amount": -1000.0,
            "currency": "EUR",
            "kind": "spending",
            "done": False,
            "notes": None,
        },
        {
            "id": 9,
            "date": "2026-05-01",
            "description": "Example Gift",
            "amount": -50.0,
            "currency": "EUR",
            "kind": "spending",
            "done": True,
            "notes": "paid",
        },
    ]

    def fake(path, params=None):
        if path.endswith("/planned"):
            return items
        return _PLANNED_FORECAST

    with patch("pfm_mcp.client._get", side_effect=fake):
        result = spending.planned_items(include_done=True)
    assert "2 listed (data through 2026-07-10)" in result
    assert "Example Holiday" in result and "[spending, projected]" in result
    assert "[spending, done]" in result

    def broken(path, params=None):
        if path.endswith("/planned"):
            return items[:1]
        raise urllib.error.URLError("down")

    with patch("pfm_mcp.client._get", side_effect=broken):
        result = spending.planned_items()
    assert "data through n/a" in result and "[spending, n/a]" in result
    assert "no statuses" in result


def test_planned_items_reports_an_api_error():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("down")):
        assert spending.planned_items().startswith("Error fetching planned items")


_YEAR = {
    "year": 2026,
    "previous_year": 2025,
    "complete": False,
    "compared_through": "2026-06",
    "compared_months": ["2026-05", "2026-06"],
    "data_through": "2026-07-05",
    "totals": {
        "this": {
            "income_eur": 6000.0,
            "spending_eur": 4000.0,
            "savings_eur": 500.0,
            "net_eur": 2000.0,
            "savings_rate_pct": 33.3,
            "one_off_eur": 300.0,
        },
        "previous": {
            "income_eur": 5000.0,
            "spending_eur": 3500.0,
            "savings_eur": 0.0,
            "net_eur": 1500.0,
            "savings_rate_pct": None,
            "one_off_eur": 0.0,
        },
        "previous_full": None,
    },
    "months": {
        "this": [
            {
                "month": "2026-05",
                "income_eur": 3000.0,
                "spending_eur": 2000.0,
                "savings_eur": 250.0,
                "has_imports": True,
                "complete": True,
            },
            {
                "month": "2026-03",
                "income_eur": 0.0,
                "spending_eur": 0.0,
                "savings_eur": 0.0,
                "has_imports": False,
                "complete": False,
            },
        ],
        "previous": [],
    },
    "categories": [
        {
            "category": "Groceries",
            "this_eur": 1200.0,
            "previous_eur": 1000.0,
            "delta_eur": 200.0,
            "delta_pct": 20.0,
            "share_pct": 30.0,
            "children": [
                {
                    "category": "Market",
                    "this_eur": 700.0,
                    "previous_eur": 500.0,
                    "delta_eur": 200.0,
                    "delta_pct": 40.0,
                    "share_pct": 17.5,
                }
            ],
        }
    ],
    "movers": {
        "increases": [
            {
                "category": "Groceries",
                "this_eur": 1200.0,
                "previous_eur": 1000.0,
                "delta_eur": 200.0,
                "delta_pct": 20.0,
            }
        ],
        "decreases": [],
    },
    "merchants": [
        {"merchant": "Example Market", "this_eur": 900.0, "previous_eur": 700.0}
    ],
    "one_offs": [
        {
            "id": 3,
            "date": "2026-05-04",
            "description": "Example Laptop",
            "amount_eur": -300.0,
            "category": "Shopping",
            "account": "Example Bank",
        }
    ],
    "warnings": ["No imports for 2026-03: unknown, not zero."],
}


def test_spending_year_prints_like_for_like_and_unknown_months():
    with patch("pfm_mcp.client._get", return_value=reply(_YEAR)) as get:
        result = spending.spending_year(year=2026, depth=2)
    url = requested_url(get)
    assert "year=2026" in url and "depth=2" in url
    assert "against 2025" in result and "compared through 2026-06" in result
    assert "in progress" in result
    assert "savings rate 33.3%" in result and "savings rate n/a" in result
    assert "all imported months): n/a" in result
    assert "2026-03  no imports (unknown)" in result
    assert "Groceries" in result and "Market" in result and "share 17.5%" in result
    assert "Example Laptop" in result and "Example Market" in result
    assert "⚠️ No imports for 2026-03" in result


def test_spending_year_defaults_and_reports_errors():
    with patch("pfm_mcp.client._get", return_value=reply(_YEAR)) as get:
        spending.spending_year()
    assert requested_url(get) == "/api/v1/spending/year?depth=1"
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("down")):
        assert spending.spending_year().startswith("Error fetching the year view")


_QUEUE = {
    "window_start": "2025-12-01",
    "total_count": 14,
    "total_eur": 1500.0,
    "share_of_spending_pct": 6.5,
    "last_complete_month": {
        "month": "2026-06",
        "count": 3,
        "total_eur": 210.0,
        "share_pct": None,
    },
    "groups": [
        {
            "key": "out:example kiosk",
            "merchant": "Example Kiosk",
            "direction": "out",
            "count": 5,
            "total_eur": 400.0,
            "first_date": "2026-01-02",
            "last_date": "2026-06-20",
            "sample_descriptions": ["EXAMPLE KIOSK 12", "EXAMPLE KIOSK 14"],
            "transaction_ids": [1, 2, 3, 4, 5],
            "proposed_pattern": None,
            "account_names": ["Example Bank"],
        }
    ],
}


def test_uncategorized_queue_prints_groups_share_and_na():
    with patch("pfm_mcp.client._get", return_value=reply(_QUEUE)) as get:
        result = spending.uncategorized_queue(limit=5, months=6)
    url = requested_url(get)
    assert "limit=5" in url and "months=6" in url
    assert "14 rows" in result and "share of spending 6.5%" in result
    assert (
        "Last complete month 2026-06: 3 rows" in result and "n/a of spending" in result
    )
    assert "Example Kiosk" in result and "pattern: none" in result
    assert "EXAMPLE KIOSK 12" in result
    assert "nothing is changed here" in result
    empty = {
        **_QUEUE,
        "groups": [],
        "last_complete_month": None,
        "share_of_spending_pct": None,
    }
    with patch("pfm_mcp.client._get", return_value=reply(empty)):
        result = spending.uncategorized_queue()
    assert "Nothing to classify." in result
    assert "Last complete month: n/a" in result and "share of spending n/a" in result
