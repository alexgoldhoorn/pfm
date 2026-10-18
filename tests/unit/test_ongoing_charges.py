"""Ongoing charges (TER): weighting, uncovered funds, cost projection."""

import json
from datetime import datetime

import pytest

from portf_manager.services.ongoing_charges import compute_ongoing_charges


def _fund(db, symbol, value, ter=None, profiled=True):
    """A held ETF worth *value* EUR (quantity 1) with an optional TER."""
    pid = db.get_or_create_portfolio("Example Broker", base_currency="EUR")
    aid = db.create_asset(symbol, f"{symbol} Fund", "etf", currency="EUR")
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=1.0,
        price=value,
        total_amount=value,
        transaction_date="2026-01-01",
        portfolio_id=pid,
        currency="EUR",
    )
    db.insert_price_record(symbol, value, datetime(2026, 10, 1, 9, 0))
    if profiled:
        db.upsert_fund_profile(
            asset_id=aid,
            benchmark_key=None,
            source="manual",
            asset_class=json.dumps({"equity": 1.0}),
            regions=json.dumps({"north_america": 1.0}),
            sectors="{}",
            currency_hedged=0,
            hedge_currency=None,
            as_of="2026-09-01",
            ongoing_charges_pct=ter,
        )
    return aid


def _compute(db):
    return compute_ongoing_charges(db, fx=lambda c: 1.0)


def test_single_fund_projection_on_round_numbers(test_database):
    _fund(test_database, "AAA", 10000.0, ter=0.2)

    out = _compute(test_database)

    assert out["weighted_avg_pct"] == 0.2
    assert out["annual_cost_eur"] == 20.0
    assert out["covered_value_eur"] == out["fund_value_eur"] == 10000.0
    assert out["uncovered"] == []
    ten = next(p for p in out["projection"] if p["years"] == 10)
    assert ten["cost_eur"] == round(10000 * (1.05**10 - 1.048**10), 2)
    # The reduction in yield of a single fund is its TER, to rounding.
    assert ten["riy_pct"] == pytest.approx(0.2, abs=0.001)
    assert [p["years"] for p in out["projection"]] == [10, 20, 30]


def test_weighted_average_is_by_value(test_database):
    _fund(test_database, "AAA", 30000.0, ter=0.1)
    _fund(test_database, "BBB", 10000.0, ter=0.5)

    out = _compute(test_database)

    # (30000 * 0.1 + 10000 * 0.5) / 40000
    assert out["weighted_avg_pct"] == 0.2
    assert out["annual_cost_eur"] == 80.0


def test_funds_without_a_ter_are_listed_not_counted_as_free(test_database):
    _fund(test_database, "AAA", 10000.0, ter=0.2)
    _fund(test_database, "NOTER", 4000.0, ter=None)
    _fund(test_database, "NOPROF", 6000.0, profiled=False)

    out = _compute(test_database)

    assert out["weighted_avg_pct"] == 0.2
    assert out["covered_value_eur"] == 10000.0
    assert out["fund_value_eur"] == 20000.0
    assert [u["symbol"] for u in out["uncovered"]] == ["NOPROF", "NOTER"]


def test_no_covered_fund_gives_no_average_or_projection(test_database):
    _fund(test_database, "AAA", 10000.0, ter=None)

    out = _compute(test_database)

    assert out["weighted_avg_pct"] is None
    assert out["projection"] == []
    assert out["annual_cost_eur"] == 0.0


def test_no_holdings(test_database):
    out = _compute(test_database)
    assert out["fund_value_eur"] == 0.0 and out["uncovered"] == []
