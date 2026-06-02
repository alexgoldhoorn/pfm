"""Tests for the research valuation calculator (compute_targets)."""

from portf_manager.services.research import compute_targets


def test_pe_valuation_with_margin_and_premium():
    r = compute_targets(
        {"trailingEps": 6.0},
        "pe",
        {"target_pe": 25, "margin_of_safety": 20, "premium": 25},
    )
    assert r["fair_value"] == 150.0
    assert r["buy_below"] == 120.0  # 150 * (1-0.20)
    assert r["sell_above"] == 187.5  # 150 * (1+0.25)


def test_pe_uses_explicit_eps_override():
    r = compute_targets({"trailingEps": 1.0}, "pe", {"eps": 10, "target_pe": 20})
    assert r["fair_value"] == 200.0


def test_dividend_yield_valuation():
    # €2 dividend at a 4% target yield → fair value €50.
    r = compute_targets(
        {"dividendRate": 2.0},
        "dividend_yield",
        {"target_yield": 4, "margin_of_safety": 10},
    )
    assert r["fair_value"] == 50.0
    assert r["buy_below"] == 45.0


def test_missing_inputs_return_none():
    r = compute_targets({}, "pe", {"target_pe": 25})  # no EPS
    assert r["fair_value"] is None
    assert r["buy_below"] is None


def test_unknown_method_is_safe():
    r = compute_targets({"trailingEps": 5}, "dcf", {})
    assert r["fair_value"] is None
