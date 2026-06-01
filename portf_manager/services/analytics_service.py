"""
Portfolio Analytics Service

Computes dividend income, performance metrics (TWR / money-weighted IRR),
and a Spanish IRPF tax estimate. Pure-Python + numpy; no external API calls
except optional yfinance dividend dates / benchmark prices.
"""

from __future__ import annotations

import logging
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
    return None  # 'all'


def period_return(
    snapshots: list[dict], current_value: float, period: str
) -> Optional[float]:
    """Time-weighted-ish return over a period using snapshot boundary values.

    Uses the earliest snapshot on/after the period start as the opening value
    and *current_value* as the closing value. Returns None when there is no
    snapshot within the period (history too short).

    Args:
        snapshots: list of {snapshot_date, total_value_eur} ascending by date.
        current_value: latest portfolio value in EUR.
        period: 'ytd' | '1m' | '1y' | 'all'.
    """
    if not snapshots:
        return None
    start = period_start_date(period)
    if start is None:
        opening = snapshots[0]["total_value_eur"]
    else:
        in_period = [
            s
            for s in snapshots
            if _parse_date(s["snapshot_date"])
            and _parse_date(s["snapshot_date"]) >= start
        ]
        if not in_period:
            return None
        opening = in_period[0]["total_value_eur"]
    if not opening:
        return None
    return round((current_value - opening) / opening * 100, 2)
