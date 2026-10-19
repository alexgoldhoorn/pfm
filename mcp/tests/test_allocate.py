"""Output tests for the allocate tools; the API is patched at pfm_mcp.client._get."""

from unittest.mock import patch

from pfm_mcp.tools import allocate


def test_next_euro_prints_summary():
    payload = {"status": "ok", "chat_summary": "NEXT EURO\n  Deployable: 1,000"}
    with patch("pfm_mcp.client._get", return_value=payload) as get:
        out = allocate.next_euro(500.0)
    assert out == "NEXT EURO\n  Deployable: 1,000"
    assert get.call_args.args[0] == "/api/v1/allocate/next-euro"
    assert get.call_args.args[1] == {"amount": 500.0}


def test_next_euro_non_ok_prints_the_breakdown_when_there_is_one():
    payload = {
        "status": "targets_incomplete",
        "reasons": ["no target for etf"],
        "chat_summary": "Allocation targets are incomplete:\n- no target for etf",
    }
    with patch("pfm_mcp.client._get", return_value=payload):
        out = allocate.next_euro()
    assert out == payload["chat_summary"]


def test_next_euro_non_ok_without_summary_is_one_line():
    payload = {"status": "targets_incomplete", "reasons": ["no target for etf", "x"]}
    with patch("pfm_mcp.client._get", return_value=payload):
        out = allocate.next_euro()
    assert out == "Next euro: targets_incomplete — no target for etf; x"
    with patch("pfm_mcp.client._get", return_value={"status": "nothing_to_deploy"}):
        assert allocate.next_euro().endswith("see the Assets page")
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        assert allocate.next_euro() == "Error fetching next euro: boom"


def test_next_euro_missing_summary_is_na():
    with patch("pfm_mcp.client._get", return_value={"status": "ok"}) as get:
        assert allocate.next_euro() == "n/a"
    assert get.call_args.args[1] is None


def test_deployable_cash_formats_the_breakdown():
    payload = {
        "status": "ok",
        "as_of": "2026-10-19",
        "liquid_eur": 20000.0,
        "liquid_lines": [{"label": "Example Bank", "amount_eur": 20000.0}],
        "tax_reserve": {
            "reserve_eur": 3000.0,
            "quarters": [
                {
                    "quarter": "2026-Q3",
                    "gross_eur": 10000.0,
                    "iva_eur": 1735.54,
                    "irpf_eur": 1652.89,
                    "paid_eur": 0.0,
                    "remaining_eur": 3388.43,
                    "due": "2026-10-20",
                }
            ],
            "merchants_counted": ["Alpha Client"],
        },
        "buffer": {
            "months": 5.5,
            "min_months": 4,
            "target_months": 6,
            "max_months": 8,
            "status": "topping_up",
            "monthly_spend_eur": 2000.0,
            "target_eur": 12000.0,
            "idle_eur": 0.0,
        },
        "deployable_eur": 5000.0,
        "coming_in": [{"label": "Deposit", "amount_eur": 1000.0, "date": "2026-11-01"}],
        "warnings": ["left out: Old"],
    }
    with patch("pfm_mcp.client._get", return_value=payload) as get:
        out = allocate.deployable_cash()
    assert get.call_args.args[0] == "/api/v1/allocate/deployable"
    assert "Liquid: 20,000.00 EUR" in out
    assert "2026-Q3: gross 10,000.00 EUR" in out and "due 2026-10-20" in out
    assert "Merchants counted: Alpha Client" in out
    assert "Buffer: 5.5 months (min 4, target 6, max 8), status topping_up" in out
    assert "Deployable: 5,000.00 EUR" in out
    assert "Coming in: Deposit 1,000.00 EUR on 2026-11-01" in out
    assert "Warning: left out: Old" in out


def test_deployable_cash_missing_fields_are_na():
    with patch("pfm_mcp.client._get", return_value={"status": "insufficient_data"}):
        out = allocate.deployable_cash()
    assert "Liquid: n/a" in out and "Deployable: n/a" in out
    assert "Buffer: n/a months" in out
    assert "0.00" not in out
    assert "Status: insufficient_data - n/a" in out


def test_deployable_cash_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        assert allocate.deployable_cash() == "Error fetching deployable cash: boom"


def test_allocation_targets_formats_drift_and_untargeted():
    payload = {
        "total_value_eur": 10000.0,
        "targets_sum_pct": 100.0,
        "allocations": [
            {
                "asset_type": "etf",
                "current_value_eur": 5000.0,
                "current_pct": 50.0,
                "target_pct": 60.0,
                "drift_pct": -10.0,
                "drift_eur": 1000.0,
            },
            {
                "asset_type": "crypto",
                "current_value_eur": 500.0,
                "current_pct": 5.0,
                "target_pct": 0.0,
                "drift_pct": 5.0,
                "drift_eur": -500.0,
            },
        ],
    }
    with patch("pfm_mcp.client._get", return_value=payload) as get:
        out = allocate.allocation_targets()
    assert get.call_args.args[0] == "/api/v1/rebalance/analysis"
    assert "total 10,000.00 EUR" in out
    assert "drift -10.0pp (+1,000.00 EUR)" in out
    assert "Held with no target: crypto" in out


def test_allocation_targets_missing_fields_are_na():
    payload = {"allocations": [{"asset_type": "etf"}]}
    with patch("pfm_mcp.client._get", return_value=payload):
        out = allocate.allocation_targets()
    assert "total n/a" in out and "now n/a" in out and "drift n/a" in out
    assert "Held with no target: none" in out


def test_allocation_targets_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        assert (
            allocate.allocation_targets() == "Error fetching allocation targets: boom"
        )


_CANDIDATES = {
    "bucket": "stock",
    "total_value_eur": 10000.0,
    "target_pct": 40.0,
    "current_pct": 35.0,
    "candidates": [
        {
            "symbol": "STKA",
            "name": "Example Corp",
            "portfolio_name": "Example Broker",
            "value_eur": 200.0,
            "weight_pct": 2.0,
            "is_fund": False,
            "ter_pct": None,
            "buy_below": 25.0,
            "price_native": 20.0,
            "currency": "USD",
            "fair_value": 30.0,
            "recommendation": "BUY",
            "conviction": "high",
            "blocked": None,
            "managed": False,
            "is_watchlist": False,
            "cap_room_eur": 300.0,
            "rank_reason": "#1: latest research says BUY",
        },
        {
            "symbol": "STKB",
            "name": "Other Corp",
            "portfolio_name": "Example Broker",
            "value_eur": 50.0,
            "weight_pct": 0.5,
            "is_fund": False,
            "blocked": {"safe_from": "2026-12-01", "reason": "sold at a loss"},
            "cap_room_eur": 450.0,
            "rank_reason": "sold at a loss",
        },
    ],
}


def test_allocation_candidates_formats_lines_blocked_and_cap_room():
    with patch("pfm_mcp.client._get", return_value=_CANDIDATES) as get:
        out = allocate.allocation_candidates("stock")
    assert get.call_args.args[0] == "/api/v1/allocate/candidates"
    assert get.call_args.args[1] == {"bucket": "stock"}
    assert "bucket stock (target 40.0%, now 35.0%)" in out
    assert "STKA (Example Corp) [stock] in Example Broker: 200.00 EUR, 2.0%" in out
    assert "buy-below 25.00 vs price 20.00 USD" in out
    assert "cap room 300.00 EUR" in out
    assert "BLOCKED until 2026-12-01: sold at a loss" in out
    assert "rank: #1: latest research says BUY" in out


def test_allocation_candidates_missing_fields_are_na():
    payload = {"candidates": [{"symbol": "FUNDX", "is_fund": True}]}
    with patch("pfm_mcp.client._get", return_value=payload) as get:
        out = allocate.allocation_candidates()
    assert get.call_args.args[1] is None
    assert "bucket all" in out and "portfolio n/a" in out
    assert "FUNDX (n/a) [fund] in n/a: n/a, n/a of portfolio" in out
    assert "TER n/a" in out and "rank: n/a" in out
    assert "cap room" not in out


def test_allocation_candidates_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("404")):
        assert (
            allocate.allocation_candidates("nope")
            == "Error fetching allocation candidates: 404"
        )


def test_allocator_settings_marks_ticked_merchants():
    payload = {
        "buffer_months_min": 4,
        "buffer_months_target": 6,
        "buffer_months_max": 8,
        "iva_pct": 21.0,
        "irpf_pct": 20.0,
        "irpf_retention_pct": 0.0,
        "income_category": "Income",
        "work_income_merchants": ["Alpha Client"],
        "tax_paid_categories": ["Income Tax"],
        "managed_portfolio_ids": [],
        "min_line_eur": 100,
        "max_lines_per_bucket": 2,
        "max_single_stock_pct": 5.0,
        "traspaso_threshold_pp": 5.0,
        "income_merchants": ["Alpha Client", "Beta Client"],
    }
    with patch("pfm_mcp.client._get", return_value=payload) as get:
        out = allocate.allocator_settings()
    assert get.call_args.args[0] == "/api/v1/allocate/settings"
    assert "Buffer months: min 4, target 6, max 8" in out
    assert "Managed portfolio ids: n/a" in out
    assert "[x] Alpha Client" in out and "[ ] Beta Client" in out


def test_allocator_settings_missing_fields_are_na():
    with patch("pfm_mcp.client._get", return_value={}):
        out = allocate.allocator_settings()
    assert "Buffer months: min n/a, target n/a, max n/a" in out
    assert "Minimum line: n/a" in out
    assert "none (all income counted)" in out
    assert "Income merchants seen (last 365 days): n/a" in out


def test_allocator_settings_error():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        assert (
            allocate.allocator_settings() == "Error fetching allocator settings: boom"
        )
