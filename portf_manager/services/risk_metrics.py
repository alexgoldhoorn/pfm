"""Flow-adjusted portfolio risk metrics from daily snapshots.

Snapshots store the market value and cost basis of open positions, so a raw
day-over-day change mixes market moves with money moving in and out (buys add
value, sells remove it). Every metric here is computed from **time-weighted**
daily returns instead: the day's net flow is taken out before measuring the move.

The flow comes from the snapshot's own cost basis rather than from transaction
dates. Trades imported after a snapshot was recorded, with earlier dates, are
missing from that snapshot; the first snapshot that does include them jumps in
value *and* cost together, which this treats as a flow rather than a gain. A
sell lowers cost by the sold lots' basis, not by the proceeds, so the realised
gain booked that day is subtracted too. One limit remains: the unrealised gain
late-imported lots built up before the import lands on the import day.

Snapshots are calendar-daily (weekends included), so annualisation uses the
observed number of return observations per year, not the 252-trading-day
convention.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any, Callable, Optional

from portf_manager.positions import compute_positions
from portf_manager.services.analytics_service import calmar_ratio, sortino_ratio

# Annualised return, Calmar and a confident rating all need close to a year.
MIN_ANNUALISE_DAYS = 350
WINDOW_DAYS = {"1y": 365}


def daily_realised_gains(
    transactions: list[dict], fx: Callable[[str], float]
) -> dict[str, float]:
    """Realised gain booked by sells per day, in EUR.

    Args:
        transactions: transaction rows (all types; only sells book a gain).
        fx: currency → EUR rate.
    """
    gains: dict[str, float] = {}

    def _record(tx: dict, gain: float) -> None:
        day = str(tx.get("transaction_date") or "")[:10]
        eur = gain * fx(tx.get("currency") or "EUR")
        gains[day] = gains.get(day, 0.0) + eur

    compute_positions(
        [t for t in transactions if t.get("transaction_date")], on_sell=_record
    )
    return gains


def flow_adjusted_returns(
    snapshots: list[dict], realised: dict[str, float]
) -> list[tuple[str, float]]:
    """Daily time-weighted returns as ``(date, return)`` pairs.

    ``F = ΔCost − realised gain`` since the previous snapshot (a sell
    removes basis + gain as proceeds), and
    ``r = (V_t − V_{t−1} − F) / (V_{t−1} + max(F, 0))``. Inflows are treated as
    arriving at the start of the day, outflows at the end, so a large first
    purchase doesn't divide by a near-zero starting value.
    """
    snaps = sorted(snapshots, key=lambda s: str(s["snapshot_date"])[:10])
    out: list[tuple[str, float]] = []
    for prev, cur in zip(snaps, snaps[1:]):
        d0 = str(prev["snapshot_date"])[:10]
        d1 = str(cur["snapshot_date"])[:10]
        gain = sum(g for d, g in realised.items() if d0 < d <= d1)
        flow = float(cur.get("total_cost_eur") or 0) - float(
            prev.get("total_cost_eur") or 0
        )
        flow -= gain
        v0 = float(prev["total_value_eur"] or 0)
        v1 = float(cur["total_value_eur"] or 0)
        denom = v0 + max(flow, 0.0)
        if denom <= 0:
            continue
        out.append((d1, (v1 - v0 - flow) / denom))
    return out


def _drawdowns(returns: list[float]) -> tuple[float, float]:
    """Max and current drawdown (fractions, ≤ 0) of a time-weighted index."""
    index = peak = 1.0
    max_dd = 0.0
    for r in returns:
        index *= 1 + r
        peak = max(peak, index)
        max_dd = min(max_dd, index / peak - 1)
    return max_dd, index / peak - 1


def compute_risk_metrics(
    snapshots: list[dict],
    realised: dict[str, float],
    window: str = "all",
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Volatility, Sharpe, Sortino, Calmar and drawdowns (rf = 0).

    Args:
        snapshots: daily ``{snapshot_date, total_value_eur, total_cost_eur}``.
        realised: realised EUR gain per day (see :func:`daily_realised_gains`).
        window: ``"all"`` or ``"1y"`` (trailing 365 days).
        today: reference date for the window (defaults to today).

    Returns:
        Metrics dict. Both drawdowns are measured within the window, so
        ``current_drawdown_pct`` is the distance below the window's high.
        ``low_confidence`` is True under ~a year of history. ``returns`` holds
        the ``(date, return)`` pairs used, so callers can align them with a
        benchmark.
    """
    all_returns = flow_adjusted_returns(snapshots, realised)
    base: dict[str, Any] = {
        "window": window,
        "max_drawdown_pct": None,
        "current_drawdown_pct": None,
        "volatility_pct": None,
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "annualised_return_pct": None,
        "period_return_pct": None,
        "start_date": None,
        "end_date": None,
        "history_days": 0,
        "periods_per_year": None,
        "low_confidence": True,
        "returns": [],
    }
    if len(all_returns) < 2:
        base["note"] = (
            "Need at least 3 daily snapshots — collected automatically each day."
        )
        return base

    returns = all_returns
    if window in WINDOW_DAYS:
        cutoff = (
            (today or date.today()) - timedelta(days=WINDOW_DAYS[window])
        ).isoformat()
        returns = [(d, r) for d, r in all_returns if d > cutoff]
        if len(returns) < 2:
            base["note"] = "Not enough snapshots in this window."
            return base

    # Span runs from the snapshot before the first return to the last one.
    first_idx = all_returns.index(returns[0])
    start = (
        date.fromisoformat(all_returns[first_idx - 1][0])
        if first_idx > 0
        else date.fromisoformat(returns[0][0]) - timedelta(days=1)
    )
    span_days = (date.fromisoformat(returns[-1][0]) - start).days
    rs = [r for _, r in returns]
    ppy = len(rs) / (span_days / 365.25) if span_days > 0 else 365.25

    vol = statistics.stdev(rs) * math.sqrt(ppy)
    mean = statistics.mean(rs)
    max_dd, current_dd = _drawdowns(rs)

    growth = math.prod(1 + r for r in rs)
    ann_pct = None
    if span_days >= MIN_ANNUALISE_DAYS and growth > 0:
        ann_pct = round((growth ** (365.25 / span_days) - 1) * 100, 2)
    max_dd_pct = round(max_dd * 100, 2)

    base.update(
        max_drawdown_pct=max_dd_pct,
        current_drawdown_pct=round(current_dd * 100, 2),
        volatility_pct=round(vol * 100, 2) if vol else None,
        sharpe_ratio=round(mean * ppy / vol, 2) if vol else None,
        sortino_ratio=sortino_ratio(rs, periods_per_year=ppy),
        calmar_ratio=calmar_ratio(ann_pct, max_dd_pct),
        annualised_return_pct=ann_pct,
        period_return_pct=round((growth - 1) * 100, 2),
        start_date=start.isoformat(),
        end_date=returns[-1][0],
        history_days=span_days,
        periods_per_year=round(ppy, 1),
        low_confidence=span_days < MIN_ANNUALISE_DAYS,
        returns=returns,
    )
    return base


def compute_portfolio_risk(
    db, fx: Callable[[str], float], window: str = "all"
) -> dict[str, Any]:
    """:func:`compute_risk_metrics` over the database's snapshots and trades."""
    snapshots = db.get_snapshots(limit=100_000)
    realised = daily_realised_gains(db.get_all_transactions(), fx)
    return compute_risk_metrics(snapshots, realised, window=window)
