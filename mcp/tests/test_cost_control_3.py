"""Output tests for the grouped action items, net-worth history, month close
and subscriptions tools; the API is patched at pfm_mcp.client._get. Payloads
follow the router responses (invented names and round amounts)."""

from datetime import date
from unittest.mock import patch

from pfm_mcp.tools import networth, spending

from helpers import reply, requested_url


def _child(n):
    return {
        "id": f"recurring:missed:{n}",
        "severity": "medium",
        "category": "spending",
        "title": f"Example Gym {n} did not arrive",
        "detail": "Expected on the 3rd",
        "link_page": "spending",
        "status": "open",
        "ack": None,
        "priority": 215,
    }


def _grouped():
    kids = [_child(1), _child(2), _child(3)]
    return {
        "items": [
            {
                "id": "group:recurring_missed",
                "kind": "group",
                "family": "recurring_missed",
                "count": 3,
                "category": "spending",
                "severity": "medium",
                "title": "3 recurring charges did not arrive",
                "detail": "Example Gym 1, Example Gym 2 and 1 more",
                "link_page": "spending",
                "status": "open",
                "ack": None,
                "children": kids,
                "priority": 215,
            },
            {
                "id": "dq:suspicious",
                "severity": "high",
                "category": "data_quality",
                "title": "1 suspicious transaction",
                "detail": "Zero price",
                "link_page": "diagnostics",
                "status": "open",
                "ack": None,
                "priority": 340,
            },
        ],
        "summary": {"open": 4, "open_entries": 2},
        "acks": {},
        "top": [],
    }


def test_action_items_asks_for_the_grouped_form_and_prints_one_line_per_group():
    with patch("pfm_mcp.client._get", return_value=reply(_grouped())) as get:
        out = networth.action_items()
    assert "grouped=true" in requested_url(get)
    assert "3 x 3 recurring charges did not arrive  [family: recurring_missed]" in out
    assert "Example Gym 1 did not arrive" not in out
    assert "[HIGH] 1 suspicious transaction  (data_quality)" in out


def test_action_items_family_lists_the_children():
    with patch("pfm_mcp.client._get", return_value=reply(_grouped())):
        out = networth.action_items(family="recurring_missed")
    assert "ACTION ITEMS in recurring_missed (3):" in out
    assert "Example Gym 3 did not arrive" in out
    assert "suspicious" not in out


def test_action_items_unknown_family_and_category_filter():
    with patch("pfm_mcp.client._get", return_value=reply(_grouped())):
        assert "No open action items in family 'nope'" in networth.action_items(
            family="nope"
        )
        out = networth.action_items(category="data_quality")
    assert "suspicious" in out and "recurring charges" not in out


def test_action_items_family_filters_ungrouped_singles():
    def single(n):
        return {
            "id": f"research:EX{n}",
            "family": "price_targets",
            "severity": "medium",
            "category": "watchlist",
            "title": f"Example Corp {n} crossed its target",
            "status": "open",
        }

    payload = {
        "items": [
            single(1),
            single(2),
            {"id": "dq:x", "family": None, "severity": "high", "title": "Other"},
        ],
        "summary": {},
        "acks": {},
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        out = networth.action_items(family="price_targets")
    assert "ACTION ITEMS in price_targets (2):" in out
    assert "Example Corp 1 crossed" in out and "Example Corp 2 crossed" in out
    assert "Other" not in out


def _history():
    snap = {
        "snapshot_date": "2026-11-18",
        "net_worth_eur": 150000.0,
        "investments_eur": 100000.0,
        "cash_eur": 20000.0,
        "property_eur": 80000.0,
        "other_assets_eur": 0.0,
        "liabilities_eur": 50000.0,
    }
    return {
        "snapshots": [dict(snap, snapshot_date="2026-10-20"), snap],
        "first_date": "2026-10-20",
        "last_date": "2026-11-18",
        "count": 2,
        "warnings": [],
    }


def _change():
    return {
        "start": "2026-10-20",
        "end": "2026-11-18",
        "net_worth_change_eur": 2000.0,
        "net_invested_eur": 500.0,
        "parts": {
            "saved_eur": 1000.0,
            "market_eur": 500.0,
            "debt_paid_eur": 400.0,
            "property_change_eur": 0.0,
            "other_change_eur": 100.0,
        },
        "by_component": {},
        "warnings": [],
    }


def test_networth_history_prints_snapshots_and_the_split():
    def fake(path, params=None):
        return _history() if path.endswith("/history") else _change()

    with (
        patch("pfm_mcp.client._get", side_effect=fake),
        patch("pfm_mcp.client._today", return_value=date(2026, 11, 18)),
    ):
        out = networth.networth_history("1m")
    assert "2 snapshots, 2026-10-20 to 2026-11-18" in out
    assert "history starts on the first snapshot date" in out
    assert "Saved (cash added plus net buys): 1,000.00 EUR" in out
    assert "2,000.00 EUR" in out


def test_networth_history_null_parts_are_not_available():
    empty = {
        "start": None,
        "end": None,
        "net_worth_change_eur": None,
        "net_invested_eur": None,
        "parts": {k: None for k in _change()["parts"]},
        "by_component": None,
        "warnings": ["no snapshots stored yet"],
    }
    hist = {
        "snapshots": [],
        "first_date": None,
        "last_date": None,
        "count": 0,
        "warnings": ["no snapshots stored yet"],
    }

    def fake(path, params=None):
        return hist if path.endswith("/history") else empty

    with patch("pfm_mcp.client._get", side_effect=fake):
        out = networth.networth_history("all")
    assert "No snapshots stored yet." in out
    assert "not available" in out
    assert out.count("Warning: no snapshots stored yet") == 1


def test_networth_history_all_starts_the_split_on_the_first_snapshot():
    calls = []

    def fake(path, params=None):
        calls.append((path, params))
        return _history() if path.endswith("/history") else _change()

    with patch("pfm_mcp.client._get", side_effect=fake):
        networth.networth_history("all")
    assert calls[1][0].endswith("/history/change")
    assert calls[1][1] == {"start": "2026-10-20"}


def test_networth_history_rejects_bad_period_and_reports_errors():
    assert networth.networth_history("soon").startswith("Error:")
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("down")):
        assert "Error fetching net worth history" in networth.networth_history("3m")


def _close():
    return {
        "month": "2026-10",
        "closed": {
            "closed_at": "2026-11-02T09:00:00",
            "notes": "all fine",
            "report_id": 4,
            "stale": True,
        },
        "ready": True,
        "checklist": [
            {
                "key": "imports",
                "label": "Bank imports",
                "status": "ok",
                "count": 0,
                "detail": "All accounts imported through the 31st",
                "link_page": "spending",
            },
            {
                "key": "uncategorized",
                "label": "Uncategorized rows",
                "status": "warn",
                "count": 3,
                "detail": "3 rows without a category",
                "link_page": "spending",
            },
            {
                "key": "anomalies",
                "label": "Anomalies",
                "status": "unknown",
                "count": None,
                "detail": "could not be built",
                "link_page": "spending",
            },
        ],
        "figures": {
            "income_eur": 3000.0,
            "spending_eur": 2000.0,
            "savings_eur": 500.0,
            "net_eur": 1000.0,
            "savings_rate_pct": 33.3,
            "budget": {
                "name": "Main",
                "sections": [
                    {
                        "key": "spending",
                        "label": "Spending",
                        "planned_total": 2100.0,
                        "actual_total": 2000.0,
                        "variance_eur": 100.0,
                    }
                ],
            },
            "top_categories": [
                {
                    "name": "Groceries",
                    "actual_eur": 600.0,
                    "baseline_eur": 550.0,
                    "delta_eur": 50.0,
                }
            ],
            "versus_normal": {
                "status": "ok",
                "baseline_months": 3,
                "actual_eur": 2000.0,
                "baseline_eur": 1900.0,
                "delta_eur": 100.0,
                "delta_pct": 5.3,
            },
        },
        "warnings": ["The budget could not be built: x"],
    }


def test_month_close_prints_checklist_figures_and_state():
    with patch("pfm_mcp.client._get", return_value=reply(_close())) as get:
        out = spending.month_close("2026-10")
    assert "month=2026-10" in requested_url(get)
    assert "Closed 2026-11-02 - changed since (stale)" in out
    assert "[LOOK] Uncategorized rows: 3 rows without a category" in out
    assert "[unknown] Anomalies" in out
    assert "Income: 3,000.00 EUR" in out
    assert "Net as share of income: 33.3%" in out
    assert "Groceries: 600.00 EUR" in out
    assert "Warning: The budget could not be built" in out


def test_month_close_defaults_and_null_figures():
    data = _close()
    data["closed"] = None
    data["figures"] = {k: None for k in data["figures"]}
    with patch("pfm_mcp.client._get", return_value=reply(data)) as get:
        out = spending.month_close()
    assert requested_url(get) == "/api/v1/spending/month-close"
    assert "Not closed." in out
    assert "Income: n/a" in out and "Net as share of income: n/a" in out


def test_month_close_reports_errors():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("400")):
        assert "Error fetching month close" in spending.month_close("2999-01")


def _item(merchant, annual, status, **extra):
    base = {
        "merchant": merchant,
        "portfolio_id": 1,
        "account_name": "Main account",
        "currency": "EUR",
        "category": "Subscriptions",
        "cadence": "monthly",
        "occurrences": 6,
        "first_date": "2026-01-03",
        "last_date": "2026-10-03",
        "last_amount": annual / 12,
        "typical_amount": annual / 12,
        "annual_amount": annual,
        "annual_amount_eur": annual,
        "next_expected": "2026-11-03",
        "status": status,
        "price_change_pct": None,
        "transaction_ids": [1, 2],
        "resolution": None,
        "resolution_stale": False,
    }
    base.update(extra)
    return base


def test_subscriptions_sorted_by_yearly_cost_with_statuses_and_totals():
    data = {
        "items": [
            _item("Example Video", 240.0, "active"),
            _item(
                "Example Gym",
                120.0,
                "cancelled",
                resolution={
                    "status": "cancelled",
                    "since": "2026-09-01",
                    "note": "left",
                },
                resolution_stale=True,
            ),
            _item("Example Phone", 60.0, "paid_elsewhere"),
        ],
        "monthly_total_eur": 25.0,
        "annual_total_eur": 300.0,
        "paid_elsewhere_annual_eur": 60.0,
        "cancelled_annual_eur": 120.0,
    }
    with patch("pfm_mcp.client._get", return_value=reply(data)) as get:
        out = spending.subscriptions()
    assert "sort=yearly_cost" in requested_url(get)
    assert "Example Video: 240.00 EUR a year, monthly, active" in out
    assert (
        "Example Gym: 120.00 EUR a year, monthly, cancelled - a charge came back" in out
    )
    assert "Note: left" in out
    assert "paid elsewhere" in out
    assert "Yearly total: 300.00 EUR" in out
    assert "Saved a year by cancellations: 120.00 EUR" in out


def test_subscriptions_empty_and_error():
    empty = {
        "items": [],
        "monthly_total_eur": 0.0,
        "annual_total_eur": 0.0,
        "paid_elsewhere_annual_eur": 0.0,
        "cancelled_annual_eur": 0.0,
    }
    with patch("pfm_mcp.client._get", return_value=reply(empty)):
        assert spending.subscriptions() == "No recurring charges detected."
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("x")):
        assert "Error fetching subscriptions" in spending.subscriptions()
