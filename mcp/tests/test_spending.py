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
