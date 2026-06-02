"""
Analytics Router — dividends, performance, net-worth history, tax estimate.
"""

import logging
import math
import statistics
from datetime import date, datetime
from typing import Optional

import yfinance as yf
from fastapi import APIRouter, Depends, Query, Request

from portf_manager.services.analytics_service import (
    dividend_income,
    irpf_savings_tax,
    money_weighted_irr,
    period_return,
    period_start_date,
    simple_return,
)
from portf_manager.tax_calculator import TaxCalculator

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

# Reuse the portfolios router's resilient FX helper: it pre-seeds typical
# rates with fresh timestamps (so the first request never blocks on yfinance)
# and falls back to the cached/default rate on failure (so a yfinance outage
# can't make us re-hit the network per position — the cause of the tax-estimate
# 504 gateway timeouts).
from .portfolios import _get_fx_rate as _fx

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _compute_positions(db):
    """Return {asset_id: {quantity, cost}} for open positions, plus realised P&L."""
    positions = {}
    realised = 0.0
    for tx in db.get_all_transactions():
        aid = tx["asset_id"]
        t = tx["transaction_type"].lower()
        qty = float(tx["quantity"])
        total = float(tx["total_amount"])
        if aid not in positions:
            positions[aid] = {"quantity": 0.0, "cost": 0.0}
        if t == "buy":
            positions[aid]["quantity"] += qty
            positions[aid]["cost"] += total
        elif t == "sell":
            pos = positions[aid]
            if pos["quantity"] > 0:
                avg = pos["cost"] / pos["quantity"]
                realised += total - avg * qty
                pos["cost"] *= (pos["quantity"] - qty) / pos["quantity"]
            pos["quantity"] -= qty
    return positions, realised


# ── Dividends ────────────────────────────────────────────────────────────────


@router.get("/dividends")
async def get_dividends(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Dividend income by year, month, symbol + projected forward annual income."""
    txns = db.get_all_transactions()
    income = dividend_income(txns)

    # Yield-on-cost: trailing-12m dividends per symbol / current cost basis
    positions, _ = _compute_positions(db)
    cost_by_symbol = {}
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if asset:
            cost_by_symbol[asset["symbol"]] = (
                cost_by_symbol.get(asset["symbol"], 0) + pos["cost"]
            )

    # Trailing 12 months income per symbol
    cutoff = date.today().replace(year=date.today().year - 1)
    ttm_by_symbol = {}
    for tx in txns:
        if tx.get("transaction_type", "").lower() != "dividend":
            continue
        d = tx.get("transaction_date", "")
        try:
            dd = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if dd >= cutoff:
            sym = tx.get("symbol", "?")
            ttm_by_symbol[sym] = ttm_by_symbol.get(sym, 0) + float(
                tx.get("total_amount") or 0
            )

    yield_on_cost = {}
    for sym, ttm in ttm_by_symbol.items():
        cost = cost_by_symbol.get(sym, 0)
        if cost > 0:
            yield_on_cost[sym] = round(ttm / cost * 100, 2)

    projected_annual = round(sum(ttm_by_symbol.values()), 2)

    return {
        **income,
        "ttm": round(sum(ttm_by_symbol.values()), 2),
        "projected_annual": projected_annual,
        "yield_on_cost": yield_on_cost,
    }


# ── Performance ───────────────────────────────────────────────────────────────


@router.get("/performance")
async def get_performance(
    benchmark: str = Query("^GSPC", description="Benchmark ticker for comparison"),
    period: str = Query("all", description="Return window: ytd | 1m | 1y | all"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Total return, money-weighted IRR, benchmark comparison, and period return.

    ``total_return_pct`` / ``money_weighted_irr_pct`` are lifetime figures.
    ``period_return_pct`` is the change over the selected window, derived from
    daily snapshots (null when history is shorter than the window).
    """
    positions, realised = _compute_positions(db)

    invested = 0.0
    current_value = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        invested += pos["cost"] * _fx(cur)
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        current_value += pos["quantity"] * price * _fx(cur)

    # Build cash flows for IRR: buys negative, sells positive (EUR)
    cash_flows = []
    for tx in db.get_all_transactions():
        d = tx.get("transaction_date", "")
        try:
            dd = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        asset = db.get_asset(tx["asset_id"])
        cur = asset.get("currency", "EUR") if asset else "EUR"
        amount_eur = float(tx["total_amount"] or 0) * _fx(cur)
        t = tx["transaction_type"].lower()
        if t == "buy":
            cash_flows.append((dd, -amount_eur))
        elif t == "sell":
            cash_flows.append((dd, amount_eur))
        elif t == "dividend":
            cash_flows.append((dd, amount_eur))

    irr = money_weighted_irr(cash_flows, current_value)
    total_ret = simple_return(invested, current_value, realised)

    # Period return.
    # "All-time" is a lifetime figure, so it must equal the cost-basis total
    # return — deriving it from snapshots[0] instead understates it whenever
    # the daily-snapshot history started after the portfolio's inception
    # (which is why All-time previously showed ~0% next to a +12% Total Return).
    # Named windows (ytd/1m/1y) use the snapshot-based time-weighted return.
    if (period or "all").lower() == "all":
        period_ret = total_ret
    else:
        period_ret = period_return(db.get_snapshots(), current_value, period)
    bench_start = period_start_date(period)

    # Benchmark: total return over the selected window (or full history for 'all')
    benchmark_ret = None
    try:
        if bench_start is not None:
            start = bench_start
        elif cash_flows:
            start = min(c[0] for c in cash_flows)
        else:
            start = None
        if start is not None:
            hist = yf.download(
                benchmark, start=start.isoformat(), progress=False, auto_adjust=True
            )
            if not hist.empty:
                closes = hist["Close"]
                # yfinance may return multi-index columns -> take the first column
                if hasattr(closes, "columns"):
                    closes = closes.iloc[:, 0]
                closes = closes.dropna()
                if len(closes) > 1:
                    first = float(closes.iloc[0])
                    last = float(closes.iloc[-1])
                    benchmark_ret = round((last - first) / first * 100, 2)
    except Exception as e:
        logger.warning(f"Benchmark fetch failed: {e}")

    return {
        "invested_eur": round(invested, 2),
        "current_value_eur": round(current_value, 2),
        "realised_pnl_eur": round(realised, 2),
        "total_return_pct": total_ret,
        "money_weighted_irr_pct": irr,
        "period": period,
        "period_return_pct": period_ret,
        "benchmark": benchmark,
        "benchmark_return_pct": benchmark_ret,
    }


# ── Net-worth history ─────────────────────────────────────────────────────────


@router.get("/networth-history")
async def get_networth_history(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Daily portfolio value vs invested-cost snapshots."""
    return {"snapshots": db.get_snapshots()}


@router.post("/snapshot")
async def take_snapshot(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Record today's portfolio value/cost snapshot (called by the price cron)."""
    positions, _ = _compute_positions(db)
    value = cost = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        cost += pos["cost"] * _fx(cur)
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value += pos["quantity"] * price * _fx(cur)
    db.record_snapshot(date.today().isoformat(), round(value, 2), round(cost, 2))
    return {
        "date": date.today().isoformat(),
        "total_value_eur": round(value, 2),
        "total_cost_eur": round(cost, 2),
    }


# ── Tax estimate ──────────────────────────────────────────────────────────────


@router.get("/tax-estimate")
async def get_tax_estimate(
    year: Optional[int] = Query(None, description="Tax year (default current)"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Spanish IRPF savings-base estimate: realised gains + dividends YTD, unrealised, harvest candidates."""
    yr = year or date.today().year
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    # Realised capital gains via FIFO (all portfolios)
    calc = TaxCalculator(db)
    realised_gain = 0.0
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for txns in report.values():
            for t in txns:
                realised_gain += float(getattr(t, "gain_loss", 0) or 0)
    except Exception as e:
        logger.warning(f"Tax report calc failed: {e}")

    # Dividend income this year
    div = dividend_income(db.get_all_transactions())
    div_this_year = div["by_year"].get(str(yr), 0.0)

    savings_base = realised_gain + div_this_year
    estimated_tax = irpf_savings_tax(savings_base)

    # Unrealised gains + tax-loss harvesting candidates
    positions, _ = _compute_positions(db)
    unrealised = 0.0
    harvest_candidates = []
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value = pos["quantity"] * price
        gain = (value - pos["cost"]) * _fx(cur)
        unrealised += gain
        if gain < -1:  # currently at a loss → harvest candidate
            harvest_candidates.append(
                {
                    "symbol": asset["symbol"],
                    "unrealised_loss_eur": round(gain, 2),
                }
            )

    harvest_candidates.sort(key=lambda x: x["unrealised_loss_eur"])

    return {
        "year": yr,
        "realised_gain_eur": round(realised_gain, 2),
        "dividend_income_eur": round(div_this_year, 2),
        "savings_base_eur": round(savings_base, 2),
        "estimated_tax_eur": estimated_tax,
        "unrealised_gain_eur": round(unrealised, 2),
        "harvest_candidates": harvest_candidates,
        "note": "Spanish IRPF base del ahorro estimate. Realised gains use FIFO. Not tax advice.",
    }


# ── Diversification & Risk ─────────────────────────────────────────────────────


@router.get("/diversification")
async def get_diversification(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Sector / country / currency / asset-type concentration + Herfindahl index."""
    positions, _ = _compute_positions(db)

    by_type: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_country: dict[str, float] = {}
    total = 0.0

    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        price_data = db.get_latest_price(aid)
        price = float(price_data["price"]) if price_data else 0.0
        value = pos["quantity"] * price * _fx(cur)
        if value <= 0:
            continue
        total += value
        by_type[asset.get("asset_type", "other")] = (
            by_type.get(asset.get("asset_type", "other"), 0) + value
        )
        by_currency[cur] = by_currency.get(cur, 0) + value
        # sector/country from yfinance (cached info)
        sector = country = None
        try:
            info = yf.Ticker(asset["symbol"]).info
            sector = info.get("sector")
            country = info.get("country")
        except Exception:
            pass
        by_sector[sector or "Unknown"] = by_sector.get(sector or "Unknown", 0) + value
        by_country[country or "Unknown"] = (
            by_country.get(country or "Unknown", 0) + value
        )

    def pct_map(d):
        return (
            {
                k: round(v / total * 100, 1)
                for k, v in sorted(d.items(), key=lambda x: -x[1])
            }
            if total
            else {}
        )

    def herfindahl(d):
        if not total:
            return 0.0
        return round(sum((v / total) ** 2 for v in d.values()) * 10000, 0)

    return {
        "total_value_eur": round(total, 2),
        "by_asset_type": pct_map(by_type),
        "by_currency": pct_map(by_currency),
        "by_sector": pct_map(by_sector),
        "by_country": pct_map(by_country),
        "concentration_hhi": herfindahl(by_type),
        "largest_position_pct": max(pct_map(by_type).values()) if by_type else 0,
    }


@router.get("/risk")
async def get_risk(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Max drawdown, annualised volatility, and Sharpe ratio from snapshot history."""
    snapshots = db.get_snapshots()
    if len(snapshots) < 3:
        return {
            "max_drawdown_pct": None,
            "volatility_pct": None,
            "sharpe_ratio": None,
            "note": "Need at least 3 daily snapshots — collected automatically each day.",
        }

    values = [s["total_value_eur"] for s in snapshots]

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            max_dd = min(max_dd, dd)

    # Daily returns
    returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    vol = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else None
    mean_daily = statistics.mean(returns) if returns else 0
    # Sharpe (rf=0), annualised
    sharpe = None
    if vol and vol > 0:
        sharpe = round((mean_daily * 252) / vol, 2)

    return {
        "max_drawdown_pct": round(max_dd * 100, 2),
        "volatility_pct": round(vol * 100, 2) if vol else None,
        "sharpe_ratio": sharpe,
        "snapshots_used": len(snapshots),
    }


# ── Fees & Costs ───────────────────────────────────────────────────────────────


@router.get("/fees")
async def get_fees(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Total fees + tax by broker/portfolio, fee drag as % of invested."""
    by_portfolio: dict[str, dict] = {}
    total_fees = total_tax = total_invested = 0.0

    portfolios = {p["id"]: p["name"] for p in db.get_all_portfolios()}

    for tx in db.get_all_transactions():
        asset = db.get_asset(tx["asset_id"])
        cur = asset.get("currency", "EUR") if asset else "EUR"
        fx = _fx(cur)
        fees = float(tx.get("fees") or 0) * fx
        tax = float(tx.get("tax") or 0) * fx
        pid = tx.get("portfolio_id")
        pname = portfolios.get(pid, "Unassigned")
        if pname not in by_portfolio:
            by_portfolio[pname] = {
                "fees_eur": 0.0,
                "tax_eur": 0.0,
                "invested_eur": 0.0,
                "tx_count": 0,
            }
        by_portfolio[pname]["fees_eur"] += fees
        by_portfolio[pname]["tax_eur"] += tax
        by_portfolio[pname]["tx_count"] += 1
        total_fees += fees
        total_tax += tax
        if tx["transaction_type"].lower() == "buy":
            invested = float(tx["total_amount"] or 0) * fx
            by_portfolio[pname]["invested_eur"] += invested
            total_invested += invested

    for p in by_portfolio.values():
        p["fees_eur"] = round(p["fees_eur"], 2)
        p["tax_eur"] = round(p["tax_eur"], 2)
        p["invested_eur"] = round(p["invested_eur"], 2)
        p["fee_drag_pct"] = (
            round(p["fees_eur"] / p["invested_eur"] * 100, 3)
            if p["invested_eur"] > 0
            else 0
        )

    return {
        "total_fees_eur": round(total_fees, 2),
        "total_tax_eur": round(total_tax, 2),
        "total_invested_eur": round(total_invested, 2),
        "fee_drag_pct": (
            round(total_fees / total_invested * 100, 3) if total_invested > 0 else 0
        ),
        "by_broker": dict(
            sorted(by_portfolio.items(), key=lambda x: -x[1]["fees_eur"])
        ),
    }


# ── Tax report: per-lot realised gains + withholding ──────────────────────────


@router.get("/tax-report")
async def get_tax_report(
    year: Optional[int] = Query(None, description="Tax year (default current)"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Per-lot realised gains (FIFO) + dividend withholding summary for a year.

    Reuses the FIFO engine in TaxCalculator. Amounts are in each transaction's
    own currency as stored; withholding sums the per-transaction ``tax`` field.
    """
    yr = year or date.today().year
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    calc = TaxCalculator(db)
    lots = []
    total_gain = 0.0
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for symbol, txns in report.items():
            for t in txns:
                gain = float(getattr(t, "gain_loss", 0) or 0)
                total_gain += gain
                lots.append(
                    {
                        "symbol": symbol,
                        "sell_date": str(getattr(t, "sell_date", "")),
                        "quantity": float(getattr(t, "quantity", 0) or 0),
                        "proceeds": round(float(getattr(t, "proceeds", 0) or 0), 2),
                        "cost_basis": round(float(getattr(t, "cost_basis", 0) or 0), 2),
                        "gain_loss": round(gain, 2),
                        "holding_days": getattr(t, "holding_period_days", None),
                    }
                )
    except Exception as e:
        logger.warning(f"Tax report failed: {e}")

    lots.sort(key=lambda x: x["sell_date"])

    # Dividend withholding tax this year (per-transaction `tax` on dividends)
    withholding = 0.0
    dividends_gross = 0.0
    for tx in db.get_all_transactions():
        if tx.get("transaction_type", "").lower() != "dividend":
            continue
        d = tx.get("transaction_date", "")
        try:
            dd = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= dd <= end:
            withholding += float(tx.get("tax") or 0)
            dividends_gross += float(tx.get("total_amount") or 0)

    return {
        "year": yr,
        "realised_lots": lots,
        "realised_gain_total": round(total_gain, 2),
        "lot_count": len(lots),
        "dividends_gross_eur": round(dividends_gross, 2),
        "dividend_withholding_eur": round(withholding, 2),
        "note": "FIFO realised gains. Withholding is the tax already paid at source on dividends.",
    }
