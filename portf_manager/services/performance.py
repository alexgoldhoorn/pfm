"""Lifetime performance figures and the EUR conversions they rest on.

Currency rule: what was **paid** is converted at the rate on the day it was
paid (each transaction at its own date's FX), and what a holding is **worth** is
converted at the rate on the valuation day. The gap between the two is the
currency gain or loss, which for a euro investor is part of the return. Converting
cost at today's rate instead (the old behaviour) cancelled most of that gain out.

Return measures (CFA Institute, GIPS 2020 — Global Investment Performance
Standards):

* **Money-weighted return (IRR)** — the annual rate that discounts every
  contribution, withdrawal and the current value to zero. It reflects *your*
  timing: the return you actually earned on the money you put in.
* **Time-weighted return (TWR)** — chains daily returns with deposits and
  withdrawals taken out, so it measures the investments, not your timing. Use it
  to compare with a benchmark.
* **Total return** — total gain (unrealised + realised + dividends + interest)
  as a share of all the money spent on purchases. Simple, not annualised.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any, Callable, Optional

from portf_manager.positions import compute_positions

logger = logging.getLogger(__name__)

FxOn = Callable[[str, Any], float]

# Benchmarks offered in the UI. ``total_return`` is True when dividends are
# reinvested in the price (an accumulating ETF, or a performance index such as
# the DAX). A price index leaves out dividends, about 1.5-2%/yr for equities,
# so comparing a portfolio's total return against one flatters the portfolio.
PERFORMANCE_BENCHMARKS: dict[str, dict[str, Any]] = {
    "VWCE.DE": {
        "label": "FTSE All-World (EUR, dividends reinvested)",
        "currency": "EUR",
        "total_return": True,
    },
    "IWDA.AS": {
        "label": "MSCI World (EUR, dividends reinvested)",
        "currency": "EUR",
        "total_return": True,
    },
    "^GSPC": {
        "label": "S&P 500 (price only)",
        "currency": "USD",
        "total_return": False,
    },
    "^IXIC": {"label": "NASDAQ (price only)", "currency": "USD", "total_return": False},
    "URTH": {"label": "MSCI World (USD ETF)", "currency": "USD", "total_return": False},
    "^STOXX50E": {
        "label": "Euro Stoxx 50 (price only)",
        "currency": "EUR",
        "total_return": False,
    },
    "^AEX": {"label": "AEX (price only)", "currency": "EUR", "total_return": False},
    "^IBEX": {
        "label": "IBEX 35 (price only)",
        "currency": "EUR",
        "total_return": False,
    },
    "^FCHI": {"label": "CAC 40 (price only)", "currency": "EUR", "total_return": False},
    "^FTSE": {
        "label": "FTSE 100 (price only)",
        "currency": "GBP",
        "total_return": False,
    },
    "^GDAXI": {
        "label": "DAX (dividends reinvested)",
        "currency": "EUR",
        "total_return": True,
    },
}
DEFAULT_BENCHMARK = "VWCE.DE"


def benchmark_info(ticker: str, currency: Optional[str] = None) -> dict[str, Any]:
    """Label, currency and total-return flag for a benchmark ticker.

    Args:
        ticker: Yahoo ticker.
        currency: quote currency for a ticker not in the list, if known.
    """
    known = PERFORMANCE_BENCHMARKS.get(ticker)
    if known:
        return {"ticker": ticker, **known}
    return {
        "ticker": ticker,
        "label": ticker,
        "currency": (currency or "EUR").upper(),
        "total_return": None,
    }


def _d10(value: Any) -> str:
    return str(value or "")[:10]


def to_eur_transactions(transactions: list[dict], fx_on: FxOn) -> list[dict]:
    """Copies of *transactions* with ``total_amount`` in EUR at each one's date.

    Running :func:`compute_positions` over the copies gives a cost basis and
    realised gains in the euros actually paid and received.

    Args:
        transactions: rows with ``total_amount``, ``currency``, ``transaction_date``.
        fx_on: ``(currency, date)`` → EUR rate on that date.
    """
    return [
        {
            **tx,
            "total_amount": float(tx.get("total_amount") or 0)
            * fx_on(tx.get("currency") or "EUR", _d10(tx.get("transaction_date"))),
            "currency": "EUR",
        }
        for tx in transactions
    ]


def portfolio_totals(
    transactions: list[dict],
    eur_transactions: list[dict],
    price_of: Callable[[int], Optional[float]],
    currency_of: Callable[[int], str],
    fx_value: Callable[[str], float],
) -> tuple[float, float]:
    """``(value_eur, cost_eur)`` of the open positions.

    Value is quantity × price × the valuation-day rate; cost is the EUR cost
    basis at transaction-date rates. A position with no price is valued at its
    cost (neutral) rather than at zero.

    Args:
        transactions: the transactions in their own currencies (for quantities).
        eur_transactions: the same rows from :func:`to_eur_transactions`.
        price_of: asset id → price in the asset's currency, or None.
        currency_of: asset id → the asset's (price) currency.
        fx_value: currency → EUR rate on the valuation day.
    """
    positions, _ = compute_positions(transactions)
    eur_positions, _ = compute_positions(eur_transactions)
    value = cost = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        cost_eur = eur_positions.get(aid, {}).get("cost", 0.0)
        price = price_of(aid)
        if price is None:
            value += cost_eur
        else:
            value += pos["quantity"] * price * fx_value(currency_of(aid))
        cost += cost_eur
    return value, cost


def money_weighted_irr(
    cash_flows: list[tuple[date, float]],
    final_value: float,
    today: Optional[date] = None,
) -> Optional[float]:
    """Annualised money-weighted return (IRR), in %.

    Solves ``Σ CFᵢ / (1 + r)^tᵢ + V / (1 + r)^T = 0`` by bisection, with t in
    years (actual days / 365.25).

    Args:
        cash_flows: ``(date, amount)``; money put in is NEGATIVE, money taken
            out (sale proceeds, dividends, interest) POSITIVE.
        final_value: current value, treated as a final inflow today.
        today: valuation date (defaults to today).
    """
    if not cash_flows:
        return None
    flows = sorted(cash_flows, key=lambda x: x[0])
    t0 = flows[0][0]
    series = flows + [(today or date.today(), final_value)]

    def npv(rate: float) -> float:
        return sum(amt / ((1 + rate) ** ((d - t0).days / 365.25)) for d, amt in series)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return round(mid * 100, 2)
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return round((lo + hi) / 2 * 100, 2)


def lifetime_twr(
    snapshots: list[dict],
    realised: dict[str, float],
    inception: Optional[date],
    today: Optional[date] = None,
) -> tuple[Optional[float], Optional[float]]:
    """Time-weighted return since inception, in %: ``(total, annualised)``.

    Both are None when the snapshot history starts more than 10 days after the
    first trade, so it can't stand for the whole life of the portfolio (rebuild
    the history to fill it). Annualised only with ~a year or more of history:
    annualising a few months turns noise into a headline.

    Args:
        snapshots: daily ``{snapshot_date, total_value_eur, total_cost_eur}``.
        realised: realised EUR gain per day.
        inception: date of the first transaction.
        today: reference date (defaults to today).
    """
    from portf_manager.services.risk_metrics import (
        MIN_ANNUALISE_DAYS,
        flow_adjusted_returns,
    )

    if inception is None or len(snapshots) < 2:
        return None, None
    ordered = sorted(snapshots, key=lambda s: _d10(s["snapshot_date"]))
    first = date.fromisoformat(_d10(ordered[0]["snapshot_date"]))
    if (first - inception).days > 10:
        return None, None
    growth = math.prod(1 + r for _, r in flow_adjusted_returns(ordered, realised))
    total = round((growth - 1) * 100, 2)
    last = date.fromisoformat(_d10(ordered[-1]["snapshot_date"]))
    span = (last - first).days
    if span < MIN_ANNUALISE_DAYS or growth <= 0:
        return total, None
    return total, round((growth ** (365.25 / span) - 1) * 100, 2)


def compute_performance(
    db,
    fx_now: Callable[[str], float],
    fx_on: FxOn,
    portfolio_id: Optional[int] = None,
) -> dict[str, Any]:
    """Lifetime performance in EUR: value, cost, gains, total return and IRR.

    Args:
        db: Database handle.
        fx_now: currency → today's EUR rate (for current value).
        fx_on: ``(currency, date)`` → EUR rate on that date (for flows/cost).
        portfolio_id: limit to one portfolio (None = all).

    Returns:
        ``invested_eur`` (cost of open positions), ``capital_invested_eur``
        (all purchases), ``current_value_eur``, ``realised_pnl_eur``,
        ``income_eur`` (dividends + interest), ``total_gain_eur``,
        ``total_return_pct``, ``money_weighted_irr_pct``, ``inception_date``,
        plus ``cash_flows`` (for callers that need them; not JSON-ready).
    """
    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    dated = [t for t in txns if _d10(t.get("transaction_date"))]
    assets = {a["id"]: a for a in db.get_all_assets(active_only=False)}
    eur_txns = to_eur_transactions(dated, fx_on)

    def price_of(aid: int) -> Optional[float]:
        row = db.get_latest_price(aid)
        return float(row["price"]) if row else 0.0

    def currency_of(aid: int) -> str:
        return (assets.get(aid) or {}).get("currency") or "EUR"

    value, cost = portfolio_totals(dated, eur_txns, price_of, currency_of, fx_now)
    _, realised = compute_positions(eur_txns)

    cash_flows: list[tuple[date, float]] = []
    income = bought = 0.0
    for tx in eur_txns:
        try:
            d = datetime.strptime(_d10(tx["transaction_date"]), "%Y-%m-%d").date()
        except ValueError:
            continue
        amount = float(tx["total_amount"] or 0)
        t = (tx.get("transaction_type") or "").lower()
        if t == "buy":
            cash_flows.append((d, -amount))
            bought += amount
        elif t == "sell":
            cash_flows.append((d, amount))
        elif t in ("dividend", "interest"):
            cash_flows.append((d, amount))
            income += amount

    gain = value - cost + realised + income
    inception = min((d for d, _ in cash_flows), default=None)
    return {
        "invested_eur": round(cost, 2),
        "capital_invested_eur": round(bought, 2),
        "current_value_eur": round(value, 2),
        "realised_pnl_eur": round(realised, 2),
        "unrealised_pnl_eur": round(value - cost, 2),
        "income_eur": round(income, 2),
        "total_gain_eur": round(gain, 2),
        "total_return_pct": round(gain / bought * 100, 2) if bought > 0 else None,
        "money_weighted_irr_pct": money_weighted_irr(cash_flows, value),
        "inception_date": inception.isoformat() if inception else None,
        "cash_flows": cash_flows,
    }


def to_eur_closes(
    closes: list[tuple[str, float]], currency: str, fx_on: FxOn
) -> list[tuple[str, float]]:
    """Convert ``(date, close)`` pairs to EUR at each day's rate.

    A USD index measured in USD leaves out the currency move a euro investor
    actually lives through, so every benchmark is compared in EUR.
    """
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return list(closes)
    return [(d, p * fx_on(cur, d)) for d, p in closes]
