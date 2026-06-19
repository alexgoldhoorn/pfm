"""
Portfolio Analytics Service

Computes dividend income, performance metrics (TWR / money-weighted IRR),
and a Spanish IRPF tax estimate. Pure-Python + numpy; no external API calls
except optional yfinance dividend dates / benchmark prices.
"""

from __future__ import annotations

import logging
import math
import statistics as _stats
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def irpf_savings_tax(base: float, jurisdiction: str = "ES") -> float:
    """Progressive savings-base tax on *base* euros of gains/income.

    Delegates to ``services.tax_rates`` so bracket tables live in one place and
    other jurisdictions can be added there.
    """
    from portf_manager.services.tax_rates import progressive_tax

    return progressive_tax(base, jurisdiction)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


# ── Dividends ────────────────────────────────────────────────────────────────


def dividend_income(transactions: list[dict]) -> dict[str, Any]:
    """Aggregate dividend transactions into per-year, per-month, per-symbol income."""
    by_year: dict[int, float] = defaultdict(float)
    by_month: dict[str, float] = defaultdict(float)
    by_symbol: dict[str, float] = defaultdict(float)
    total = 0.0

    for tx in transactions:
        if tx.get("transaction_type", "").lower() != "dividend":
            continue
        d = _parse_date(tx.get("transaction_date"))
        if not d:
            continue
        amount = float(tx.get("total_amount") or 0)
        total += amount
        by_year[d.year] += amount
        by_month[f"{d.year}-{d.month:02d}"] += amount
        by_symbol[tx.get("symbol", "?")] += amount

    return {
        "total": round(total, 2),
        "by_year": {str(k): round(v, 2) for k, v in sorted(by_year.items())},
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "by_symbol": {
            k: round(v, 2) for k, v in sorted(by_symbol.items(), key=lambda x: -x[1])
        },
    }


# ── Performance: cash flows, TWR, IRR ──────────────────────────────────────────


def money_weighted_irr(
    cash_flows: list[tuple[date, float]], final_value: float
) -> Optional[float]:
    """
    Compute annualised money-weighted return (IRR) via Newton's method.

    cash_flows: list of (date, amount) where deposits/buys are NEGATIVE
                (money in) and withdrawals/sells are POSITIVE (money out).
    final_value: current portfolio value (treated as a final positive flow).
    """
    if not cash_flows:
        return None
    flows = sorted(cash_flows, key=lambda x: x[0])
    t0 = flows[0][0]
    # Append the current value as a terminal inflow at today
    today = date.today()
    series = [(d, amt) for d, amt in flows] + [(today, final_value)]

    def npv(rate: float) -> float:
        total = 0.0
        for d, amt in series:
            years = (d - t0).days / 365.25
            total += amt / ((1 + rate) ** years)
        return total

    # Bisection between -0.99 and 10.0
    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None  # no sign change → IRR not bracketed
    for _ in range(100):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return round(mid * 100, 2)
        if f_lo * f_mid < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return round((lo + hi) / 2 * 100, 2)


def simple_return(
    invested: float, current_value: float, realised: float = 0.0
) -> Optional[float]:
    """Total return % = (current + realised - invested) / invested."""
    if invested <= 0:
        return None
    return round((current_value + realised - invested) / invested * 100, 2)


def period_start_date(period: str, today: Optional[date] = None) -> Optional[date]:
    """Return the start date for a named period, or None for 'all'.

    Args:
        period: One of 'ytd', '1m', '1y', 'all'.
        today: Reference date (defaults to today).
    """
    today = today or date.today()
    p = (period or "all").lower()
    if p == "ytd":
        return date(today.year, 1, 1)
    if p == "1m":
        # ~30 days back, month-aware
        month = today.month - 1 or 12
        year = today.year - 1 if today.month == 1 else today.year
        day = min(today.day, 28)
        return date(year, month, day)
    if p == "1y":
        return date(today.year - 1, today.month, min(today.day, 28))
    if p == "3y":
        return date(today.year - 3, today.month, min(today.day, 28))
    if p == "5y":
        return date(today.year - 5, today.month, min(today.day, 28))
    return None  # 'all'


def period_return(
    snapshots: list[dict],
    current_value: float,
    period: str,
    current_cost: Optional[float] = None,
) -> Optional[float]:
    """Time-weighted return (TWR) over a period from daily snapshots.

    Chains each step's market return while removing the day's net contribution
    (≈ change in cost basis), so deposits/withdrawals don't inflate the figure —
    the correct way to measure return when money is being added (a naive
    (end−start)/start can read absurdly high, e.g. when the starting balance was
    tiny and most growth came from contributions). Falls back to a chained value
    return if snapshots carry no cost basis. Returns None if history is too short
    or doesn't cover the period start.

    Args:
        snapshots: [{snapshot_date, total_value_eur, total_cost_eur}].
        current_value: latest value (kept for signature compatibility).
        period: 'ytd' | '1m' | '1y' | 'all'.
        current_cost: latest cost basis (kept for signature compatibility).
    """
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda s: str(s.get("snapshot_date", "")))
    start = period_start_date(period)
    if start is None:
        window = ordered
    else:
        window = [
            s
            for s in ordered
            if _parse_date(s["snapshot_date"])
            and _parse_date(s["snapshot_date"]) >= start
        ]
        if not window:
            return None
        # History coverage guard: if the earliest snapshot inside the window is
        # far after the period start, our daily history doesn't actually cover
        # the period — report "no data" rather than a mislabeled shorter return.
        opening_date = _parse_date(window[0]["snapshot_date"])
        if opening_date and (opening_date - start).days > 10:
            return None
    if len(window) < 2:
        return None

    # Time-weighted return: chain each step's market return, removing the day's
    # net contribution (≈ change in cost basis) so deposits/withdrawals don't
    # inflate the figure. This is the correct way to measure return over a
    # period when money is being added — unlike a naive (end-start)/start.
    factor = 1.0
    has_cost = all(s.get("total_cost_eur") is not None for s in window)
    for prev, cur in zip(window, window[1:]):
        v0 = prev.get("total_value_eur") or 0
        v1 = cur.get("total_value_eur") or 0
        if v0 <= 0:
            continue
        flow = (cur["total_cost_eur"] - prev["total_cost_eur"]) if has_cost else 0.0
        factor *= 1 + (v1 - v0 - flow) / v0
    return round((factor - 1) * 100, 2)


# ── New performance metrics ───────────────────────────────────────────────────


def compute_cagr(
    invested: float,
    current_value: float,
    realised: float,
    inception_date: Optional[date],
    today: Optional[date] = None,
) -> Optional[float]:
    """Compound Annual Growth Rate since inception.

    Returns None when invested is zero, inception is unknown, or history
    is shorter than one year (CAGR is misleading over shorter spans).

    Args:
        invested: Total cost basis.
        current_value: Current market value.
        realised: Realised gain/loss to date.
        inception_date: Date of first investment.
        today: Reference date (defaults to today).
    """
    if invested <= 0 or inception_date is None:
        return None
    today = today or date.today()
    years = (today - inception_date).days / 365.25
    if years < 1:
        return None
    ratio = (current_value + realised) / invested
    if ratio <= 0:
        return None
    return round((ratio ** (1.0 / years) - 1) * 100, 2)


def sortino_ratio(returns: list[float]) -> Optional[float]:
    """Annualised Sortino ratio (rf=0) from a list of raw daily returns.

    Penalises only downside volatility (negative-return days).
    Returns None when fewer than 2 downside observations are available.
    """
    if not returns:
        return None
    downside = [r for r in returns if r < 0]
    if len(downside) < 2:
        return None
    downside_std = _stats.stdev(downside) * math.sqrt(252)
    if downside_std == 0:
        return None
    return round((_stats.mean(returns) * 252) / downside_std, 2)


def calmar_ratio(
    cagr_pct: Optional[float], max_drawdown_pct: Optional[float]
) -> Optional[float]:
    """Calmar ratio: CAGR ÷ |max drawdown|.

    Both arguments are in % (not fractions). Returns None when no drawdown
    has been recorded (max_drawdown_pct >= 0) or either input is None.
    A negative CAGR paired with any drawdown yields a negative ratio — calmar is
    signed and negative values indicate the portfolio is losing money.
    """
    if cagr_pct is None or max_drawdown_pct is None or max_drawdown_pct >= 0:
        return None
    return round(-cagr_pct / max_drawdown_pct, 2)


def compute_beta_alpha(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    snapshot_cagr: Optional[float],
    benchmark_cagr: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Beta and alpha (annualised, rf=0, CAPM) from aligned daily return series.

    Args:
        portfolio_returns: daily raw returns (not %) aligned to benchmark.
        benchmark_returns: daily raw returns (not %) for the same dates.
        snapshot_cagr: portfolio annualised return as a fraction (e.g. 0.10).
        benchmark_cagr: benchmark annualised return as a fraction.

    Returns:
        (beta, alpha_pct) — alpha_pct is in %, rounded to 2 dp.
        Either may be None when insufficient data.
    """
    if len(portfolio_returns) < 10 or len(portfolio_returns) != len(benchmark_returns):
        return None, None
    var_b = _stats.variance(benchmark_returns)
    # When benchmark has zero variance (all-identical returns), beta is degenerate.
    # Treat perfectly correlated identical series as beta=1.0; otherwise undefined.
    if var_b == 0:
        var_p = _stats.variance(portfolio_returns)
        if var_p == 0:
            beta = 1.0
        else:
            return None, None
    else:
        beta = round(_stats.covariance(portfolio_returns, benchmark_returns) / var_b, 3)
    if snapshot_cagr is None or benchmark_cagr is None:
        return beta, None
    alpha_pct = round((snapshot_cagr - beta * benchmark_cagr) * 100, 2)
    return beta, alpha_pct
