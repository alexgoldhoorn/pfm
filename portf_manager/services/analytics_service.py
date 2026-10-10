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

# Re-exported: the IRR lives with the rest of the lifetime performance figures.
from portf_manager.services.performance import money_weighted_irr  # noqa: F401,E402

logger = logging.getLogger(__name__)


def irpf_savings_tax(base: float, jurisdiction: str = "ES") -> float:
    """Progressive savings-base tax on *base* euros of gains/income.

    Delegates to ``services.tax_rates`` so bracket tables live in one place and
    other jurisdictions can be added there.
    """
    from portf_manager.services.tax_rates import progressive_tax

    return progressive_tax(base, jurisdiction)


def current_year_savings_components(db, year: Optional[int] = None) -> dict[str, Any]:
    """The Spanish IRPF savings base for *year*, plus the legs it is made of.

    One pass over the FIFO tax report and the transaction list produces every
    figure ``GET /analytics/tax-estimate`` reports about the savings base, so
    the endpoint's ``realised_gain_eur``/``dividend_income_eur``/
    ``interest_income_eur`` and its ``savings_base_eur`` can never come from
    two separate runs of the same logic and disagree.
    :func:`current_year_savings_base` is the single-value entry point for
    callers that only need the total (e.g. the tax-aware rebalance planner).

    Lazily imports the analytics router's private EUR-conversion helpers
    (``_lot_eur_amounts``, ``_savings_income_eur``) to avoid a circular
    import — ``portf_server/routers/analytics.py`` imports this module at
    load time, so importing it back at module scope here would fail. Same
    lazy-import pattern as ``portfolio_advisor.py``'s ``_fx()``.

    Args:
        db: Database instance.
        year: Tax year; defaults to the current year.

    Returns:
        ``{"year", "realised_gain_eur", "dividend_income_eur",
        "interest_income_eur", "savings_base_eur", "realised_by_symbol"}``.
        The three income/gain legs and the base are unrounded EUR floats and
        ``savings_base_eur`` is exactly their sum; ``realised_by_symbol`` is
        the per-symbol realised-gain breakdown
        (``{"symbol", "name", "realised_eur"}``, rounded for display, sorted
        worst-first) that the endpoint surfaces.
        A failure in the realised-gains calculation is logged and treated as
        zero realised gain, matching the endpoint's existing behaviour.
    """
    from portf_manager.tax_calculator import TaxCalculator
    from portf_server.routers.analytics import _lot_eur_amounts, _savings_income_eur

    yr = year or date.today().year
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    calc = TaxCalculator(db)
    realised_gain = 0.0
    realised_by_symbol: list[dict[str, Any]] = []
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for sym, txns in report.items():
            a = db.get_asset_by_symbol(sym)
            currency = ((a or {}).get("currency") or "EUR").upper()
            sym_total_eur = 0.0
            for t in txns:
                proceeds_eur, cost_eur = _lot_eur_amounts(db, currency, t)
                sym_total_eur += proceeds_eur - cost_eur
            realised_gain += sym_total_eur
            realised_by_symbol.append(
                {
                    "symbol": sym,
                    "name": (a or {}).get("name", sym),
                    "realised_eur": round(sym_total_eur, 2),
                }
            )
        realised_by_symbol.sort(key=lambda x: x["realised_eur"])
    except Exception as e:
        logger.warning(f"Tax report calc failed: {e}")

    all_txns = db.get_all_transactions()
    div_this_year, interest_this_year = _savings_income_eur(db, all_txns, yr)

    return {
        "year": yr,
        "realised_gain_eur": realised_gain,
        "dividend_income_eur": div_this_year,
        "interest_income_eur": interest_this_year,
        "savings_base_eur": realised_gain + div_this_year + interest_this_year,
        "realised_by_symbol": realised_by_symbol,
    }


def current_year_savings_base(db, year: Optional[int] = None) -> float:
    """Spanish IRPF taxable savings base for *year*: realised gains (FIFO,
    via ``TaxCalculator``) plus dividend/interest income, all in EUR.

    Thin single-value wrapper over :func:`current_year_savings_components` —
    the public entry point for callers that need only the total. A caller
    that also wants the legs (as ``GET /analytics/tax-estimate`` does) must
    call the components function once rather than both, so the parts and the
    total always come from the same pass.

    Args:
        db: Database instance.
        year: Tax year; defaults to the current year.

    Returns:
        Unrounded savings base in EUR (realised gain + dividends + interest).
    """
    return float(current_year_savings_components(db, year=year)["savings_base_eur"])


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


def dividend_ttm_enrichment(
    transactions: list[dict],
    cost_by_symbol: dict[str, float],
) -> dict:
    """Trailing-12-month dividend income per symbol, projected annual, and yield-on-cost.

    Args:
        transactions: All transactions (non-dividend rows are ignored).
        cost_by_symbol: Current cost basis per symbol (for yield-on-cost calc).

    Returns:
        dict with keys: ttm, ttm_by_symbol, projected_annual, yield_on_cost.
        All amounts are rounded to 2 decimal places.
    """
    cutoff = date.today().replace(year=date.today().year - 1)
    ttm_by_symbol: dict[str, float] = {}
    for tx in transactions:
        if tx.get("transaction_type", "").lower() != "dividend":
            continue
        d = _parse_date(tx.get("transaction_date"))
        if d is None or d < cutoff:
            continue
        sym = tx.get("symbol", "?")
        ttm_by_symbol[sym] = ttm_by_symbol.get(sym, 0) + float(
            tx.get("total_amount") or 0
        )

    yield_on_cost = {}
    for sym, ttm in ttm_by_symbol.items():
        cost = cost_by_symbol.get(sym, 0)
        if cost > 0:
            yield_on_cost[sym] = round(ttm / cost * 100, 2)

    total_ttm = sum(ttm_by_symbol.values())
    return {
        "ttm": round(total_ttm, 2),
        "ttm_by_symbol": {s: round(v, 2) for s, v in ttm_by_symbol.items()},
        "projected_annual": round(total_ttm, 2),
        "yield_on_cost": yield_on_cost,
    }


# ── Performance: period TWR ──────────────────────────────────────────────────


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
    realised: Optional[dict[str, float]] = None,
) -> Optional[float]:
    """Time-weighted return (TWR) over a period from daily snapshots.

    Chains the same flow-adjusted daily returns the risk metrics use
    (``risk_metrics.flow_adjusted_returns``), so buys, sells and late imports
    don't count as gains or losses and the dashboard's Return card agrees with
    its risk row. Snapshots without a cost basis are treated as having no flows.
    Returns None if history is too short or doesn't cover the period start.

    Args:
        snapshots: [{snapshot_date, total_value_eur, total_cost_eur}].
        current_value: latest value (kept for signature compatibility).
        period: 'ytd' | '1m' | '1y' | 'all'.
        current_cost: latest cost basis (kept for signature compatibility).
        realised: realised EUR gain per day from sells
            (``risk_metrics.daily_realised_gains``). Without it a sell's
            realised gain reads as a loss on the day of the sale.
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

    # Imported here: risk_metrics imports this module.
    from portf_manager.services.risk_metrics import flow_adjusted_returns

    factor = 1.0
    for _, r in flow_adjusted_returns(window, realised or {}):
        factor *= 1 + r
    return round((factor - 1) * 100, 2)


# ── Risk-adjusted ratios ──────────────────────────────────────────────────────


def sortino_ratio(
    returns: list[float], periods_per_year: float = 252, risk_free: float = 0.0
) -> Optional[float]:
    """Annualised Sortino ratio from raw periodic returns.

    Follows Sortino & Price (1994), "Performance Measurement in a Downside Risk
    Framework", Journal of Investing: the denominator is the *downside
    deviation* — the root mean square of shortfalls below the target, taken
    over **all** periods (a period above the target contributes zero). The
    target is the risk-free rate, and so is what the numerator subtracts.

    Returns None when fewer than 2 periods fall below the target.

    Args:
        returns: raw (not %) returns, one per period.
        periods_per_year: observations per year used to annualise.
        risk_free: annual risk-free rate as a fraction (0.02 = 2%).
    """
    if not returns:
        return None
    target = risk_free / periods_per_year
    shortfalls = [min(r - target, 0.0) for r in returns]
    if sum(1 for s in shortfalls if s < 0) < 2:
        return None
    downside_dev = math.sqrt(sum(s * s for s in shortfalls) / len(returns))
    downside_dev *= math.sqrt(periods_per_year)
    if downside_dev == 0:
        return None
    excess = _stats.mean(returns) * periods_per_year - risk_free
    return round(excess / downside_dev, 2)


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
    portfolio_annual: Optional[float],
    benchmark_annual: Optional[float],
    risk_free: float = 0.0,
) -> tuple[Optional[float], Optional[float]]:
    """Beta and Jensen's alpha from aligned return series.

    Beta is ``cov(Rp, Rb) / var(Rb)``. Alpha is Jensen's (1968) CAPM alpha on
    annualised returns: ``(Rp − rf) − β·(Rb − rf)``.

    Args:
        portfolio_returns: raw (not %) periodic returns aligned to the benchmark.
        benchmark_returns: raw periodic returns for the same periods.
        portfolio_annual: portfolio annualised return as a fraction (0.10).
        benchmark_annual: benchmark annualised return as a fraction.
        risk_free: annual risk-free rate as a fraction.

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
    if portfolio_annual is None or benchmark_annual is None:
        return beta, None
    alpha = (portfolio_annual - risk_free) - beta * (benchmark_annual - risk_free)
    return beta, round(alpha * 100, 2)


def weekly_from_daily(daily: list[tuple[str, float]]) -> dict[str, float]:
    """Compound daily ``(date, return)`` pairs into ISO-week returns.

    Keyed ``"YYYY-Www"``. A week with fewer than 5 daily observations (the
    partial first or last week of a series) is left out.
    """
    weeks: dict[str, list[float]] = defaultdict(list)
    for d, r in daily:
        iso = date.fromisoformat(d[:10]).isocalendar()
        weeks[f"{iso.year}-W{iso.week:02d}"].append(r)
    return {
        k: math.prod(1 + r for r in rs) - 1 for k, rs in weeks.items() if len(rs) >= 5
    }


def weekly_from_closes(closes: list[tuple[str, float]]) -> dict[str, float]:
    """ISO-week returns from ``(date, close)`` pairs, last close to last close.

    The first week has no prior close, so it has no return.
    """
    last: dict[str, float] = {}
    for d, p in sorted(closes):
        iso = date.fromisoformat(d[:10]).isocalendar()
        last[f"{iso.year}-W{iso.week:02d}"] = p
    keys = sorted(last)
    return {
        k: last[k] / last[prev] - 1 for prev, k in zip(keys, keys[1:]) if last[prev] > 0
    }
