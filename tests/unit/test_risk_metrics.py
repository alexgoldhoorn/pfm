"""Tests for flow-adjusted risk metrics (portf_manager/services/risk_metrics.py)."""

from datetime import date, timedelta

import pytest

from portf_manager.services.risk_metrics import (
    compute_risk_metrics,
    daily_realised_gains,
    flow_adjusted_returns,
)


def _snaps(start: date, values: list[float], costs: list[float] = None) -> list[dict]:
    costs = costs or [0.0] * len(values)
    return [
        {
            "snapshot_date": (start + timedelta(days=i)).isoformat(),
            "total_value_eur": v,
            "total_cost_eur": c,
        }
        for i, (v, c) in enumerate(zip(values, costs))
    ]


def _tx(tx_type: str, day: str, qty: float, total: float, cur: str = "EUR") -> dict:
    return {
        "asset_id": 1,
        "transaction_type": tx_type,
        "transaction_date": day,
        "quantity": qty,
        "total_amount": total,
        "currency": cur,
    }


class TestDailyRealisedGains:
    def test_sell_books_gain_on_its_day(self):
        txs = [
            _tx("buy", "2025-01-01", 10, 1000),
            _tx("sell", "2025-01-05 10:00:00", 5, 600),
            _tx("dividend", "2025-01-06", 0, 50),
        ]
        assert daily_realised_gains(txs, lambda c: 1.0) == {
            "2025-01-05": pytest.approx(100.0)
        }

    def test_converts_to_eur(self):
        txs = [
            _tx("buy", "2025-01-01", 1, 100, "USD"),
            _tx("sell", "2025-01-02", 1, 200, "USD"),
        ]
        gains = daily_realised_gains(txs, lambda c: 0.5 if c == "USD" else 1.0)
        assert gains == {"2025-01-02": pytest.approx(50.0)}


class TestFlowAdjustedReturns:
    def test_buy_is_not_a_return(self):
        # Value doubles only because 1000 was bought in on day 2.
        snaps = _snaps(date(2025, 1, 1), [1000, 2000, 2020], [1000, 2000, 2000])
        rets = flow_adjusted_returns(snaps, {})
        assert [d for d, _ in rets] == ["2025-01-02", "2025-01-03"]
        assert rets[0][1] == pytest.approx(0.0)
        assert rets[1][1] == pytest.approx(0.01)

    def test_sell_is_not_a_loss(self):
        # Half sold at market (basis 400, proceeds 500); the rest is flat.
        snaps = _snaps(date(2025, 1, 1), [1000, 500], [800, 400])
        rets = flow_adjusted_returns(snaps, {"2025-01-02": 100.0})
        assert rets[0][1] == pytest.approx(0.0)

    def test_late_imported_history_is_not_a_return(self):
        # Trades dated in the past, imported today: the snapshot jumps in value
        # and cost together, with no flow on today's date.
        snaps = _snaps(date(2025, 1, 1), [1000, 1000, 11_000], [900, 900, 10_900])
        rets = flow_adjusted_returns(snaps, {})
        assert rets[1][1] == pytest.approx(0.0)

    def test_late_import_gain_built_up_before_import_still_counts(self):
        # Known limit: the imported lots' unrealised gain (here 900) lands on
        # the import day, because the snapshots before it never saw them.
        snaps = _snaps(date(2025, 1, 1), [1000, 11_000], [1000, 10_100])
        rets = flow_adjusted_returns(snaps, {})
        assert rets[0][1] == pytest.approx(900 / 10_100)

    def test_first_buy_from_empty_is_skipped(self):
        snaps = _snaps(date(2025, 1, 1), [0, 1000, 1100], [0, 1000, 1000])
        rets = flow_adjusted_returns(snaps, {})
        assert rets[0][1] == pytest.approx(0.0)
        assert rets[1][1] == pytest.approx(0.10)


class TestComputeRiskMetrics:
    def test_insufficient_history(self):
        m = compute_risk_metrics(_snaps(date(2025, 1, 1), [100, 101]), {})
        assert m["sharpe_ratio"] is None
        assert m["note"]

    def test_deposits_do_not_inflate_metrics(self):
        # Flat market; a big deposit mid-way must not show up as volatility,
        # return or drawdown. A small real wiggle keeps stdev non-zero.
        start = date(2024, 1, 1)
        values, costs = [], []
        v, c = 1000.0, 1000.0
        for i in range(400):
            if i == 200:
                v += 5000
                c += 5000
            v *= 1.001 if i % 2 else 0.999
            values.append(v)
            costs.append(c)
        m = compute_risk_metrics(
            _snaps(start, values, costs), {}, today=date(2025, 2, 4)
        )
        assert m["volatility_pct"] < 5
        assert m["max_drawdown_pct"] > -1
        assert abs(m["annualised_return_pct"]) < 1

    def test_annualises_by_observed_frequency(self):
        # Calendar-daily snapshots: 365 obs/yr, not 252.
        start = date(2024, 1, 1)
        values = [1000.0]
        for i in range(1, 731):
            values.append(values[-1] * (1.01 if i % 2 else 1 / 1.01))
        m = compute_risk_metrics(_snaps(start, values), {}, today=date(2026, 1, 1))
        # Daily stdev ≈ 1% → annualised ≈ 1% × √365 ≈ 19.1%
        assert m["volatility_pct"] == pytest.approx(19.1, abs=0.3)
        assert m["periods_per_year"] == pytest.approx(365, abs=1)

    def test_drawdowns(self):
        start = date(2025, 1, 1)
        m = compute_risk_metrics(
            _snaps(start, [100, 120, 90, 110, 100]), {}, today=date(2025, 1, 5)
        )
        assert m["max_drawdown_pct"] == pytest.approx(-25.0)
        assert m["current_drawdown_pct"] == pytest.approx(-16.67, abs=0.01)

    def test_one_year_window_and_confidence(self):
        start = date(2024, 1, 1)
        values = [1000 * (1.0005**i) * (1.002 if i % 2 else 0.998) for i in range(700)]
        today = start + timedelta(days=699)
        full = compute_risk_metrics(_snaps(start, values), {}, today=today)
        yr = compute_risk_metrics(_snaps(start, values), {}, window="1y", today=today)
        assert full["history_days"] == 699
        assert 360 <= yr["history_days"] <= 366
        assert yr["low_confidence"] is False
        assert yr["window"] == "1y"

        short = compute_risk_metrics(_snaps(start, values[:100]), {}, today=today)
        assert short["low_confidence"] is True
        # No annualised return / Calmar from less than a year.
        assert short["annualised_return_pct"] is None
        assert short["calmar_ratio"] is None

    def test_current_drawdown_is_within_window(self):
        start = date(2024, 1, 1)
        # Peak early (outside the year), then a slide, then a partial recovery.
        values = [2000.0] + [1000.0 - i for i in range(500)] + [600.0] * 100
        values[-1] = 700.0
        today = start + timedelta(days=len(values) - 1)
        yr = compute_risk_metrics(_snaps(start, values), {}, window="1y", today=today)
        # The index starts at the snapshot just before the first windowed return.
        high = max(values[-366:])
        assert yr["current_drawdown_pct"] == pytest.approx(
            (700.0 / high - 1) * 100, abs=0.01
        )
        assert yr["current_drawdown_pct"] >= yr["max_drawdown_pct"]
