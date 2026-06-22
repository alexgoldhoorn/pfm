"""
Analytics Router — dividends, performance, net-worth history, tax estimate.
"""

import logging
import math
import statistics
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from portf_manager.services.analytics_service import (
    calmar_ratio,
    compute_beta_alpha,
    compute_cagr,
    dividend_income,
    dividend_ttm_enrichment,
    irpf_savings_tax,
    money_weighted_irr,
    period_return,
    period_start_date,
    simple_return,
    sortino_ratio,
)
from portf_manager.tax_calculator import TaxCalculator
from portf_manager.positions import compute_positions
from portf_manager.cache import cached

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

# Reuse the portfolios router's resilient FX helper: it pre-seeds typical
# rates with fresh timestamps (so the first request never blocks on yfinance)
# and falls back to the cached/default rate on failure (so a yfinance outage
# can't make us re-hit the network per position — the cause of the tax-estimate
# 504 gateway timeouts).
from .portfolios import _get_fx_rate as _fx

_STRESS_SCENARIOS: dict[str, dict] = {
    "2008": {
        "label": "2008 Financial Crisis",
        "from_date": "2007-10-01",
        "to_date": "2009-03-09",
    },
    "2020": {
        "label": "2020 COVID Crash",
        "from_date": "2020-02-19",
        "to_date": "2020-03-23",
    },
    "2022": {
        "label": "2022 Rate Hike Selloff",
        "from_date": "2021-12-31",
        "to_date": "2022-10-12",
    },
    "dotcom": {
        "label": "Dot-com Bust",
        "from_date": "2000-03-24",
        "to_date": "2002-10-09",
    },
}

_STRESS_FALLBACKS: dict[str, dict[str, float]] = {
    "2008": {
        "stock": -50.0,
        "etf": -50.0,
        "index": -50.0,
        "mutual_fund": -40.0,
        "bond": -5.0,
        "crypto": 0.0,
        "commodity": -30.0,
        "cash": 0.0,
    },
    "2020": {
        "stock": -32.0,
        "etf": -32.0,
        "index": -32.0,
        "mutual_fund": -25.0,
        "bond": 5.0,
        "crypto": -50.0,
        "commodity": -20.0,
        "cash": 0.0,
    },
    "2022": {
        "stock": -22.0,
        "etf": -22.0,
        "index": -22.0,
        "mutual_fund": -18.0,
        "bond": -15.0,
        "crypto": -65.0,
        "commodity": 20.0,
        "cash": 0.0,
    },
    "dotcom": {
        "stock": -60.0,
        "etf": -60.0,
        "index": -60.0,
        "mutual_fund": -45.0,
        "bond": 5.0,
        "crypto": 0.0,
        "commodity": -15.0,
        "cash": 0.0,
    },
}


def _get_ticker_return(sym: str, from_date: str, to_date: str) -> float | None:
    """Return total return % for sym between from_date and to_date via yfinance.

    Returns None when data is unavailable (asset too new, bad ticker, network error).
    Extends the end date by 5 days so the last trading day before to_date is included.
    """
    try:
        end = (datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=5)).strftime(
            "%Y-%m-%d"
        )
        hist = yf.Ticker(sym).history(start=from_date, end=end, auto_adjust=True)
        if hist.empty:
            return None
        closes = hist["Close"].dropna()
        closes = closes[closes.index.date <= pd.Timestamp(to_date).date()]
        if len(closes) < 2:
            return None
        price_from = float(closes.iloc[0])
        price_to = float(closes.iloc[-1])
        if price_from == 0:
            return None
        return round((price_to - price_from) / price_from * 100, 2)
    except Exception:
        return None


router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _compute_positions(db):
    """Return {asset_id: {quantity, cost}} for open positions, plus realised P&L.

    Delegates to the shared chronological helper (handles buy/sell/splits).
    """
    return compute_positions(db.get_all_transactions())


# ── Dividends ────────────────────────────────────────────────────────────────


@router.get("/dividends")
async def get_dividends(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Dividend income by year, month, symbol + projected forward annual income."""
    txns = db.get_all_transactions()
    income = dividend_income(txns)

    # Build cost_by_symbol from current open positions (for yield-on-cost)
    positions, _ = _compute_positions(db)
    cost_by_symbol: dict = {}
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if asset:
            sym = asset["symbol"]
            cost_by_symbol[sym] = cost_by_symbol.get(sym, 0) + pos["cost"]

    ttm_data = dividend_ttm_enrichment(txns, cost_by_symbol)

    names = {a["symbol"]: a.get("name", a["symbol"]) for a in db.get_all_assets()}

    return {
        **income,
        **ttm_data,
        "names": names,
    }


# ── Performance ───────────────────────────────────────────────────────────────


@router.get("/performance")
def get_performance(
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

    # Prefetch all assets once (avoids a get_asset() query per position AND per
    # transaction — the latter was ~one query per trade in the IRR loop).
    assets_by_id = {a["id"]: a for a in db.get_all_assets(active_only=False)}

    invested = 0.0
    current_value = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = assets_by_id.get(aid)
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
        asset = assets_by_id.get(tx["asset_id"])
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

    inception_date = min((d for d, _ in cash_flows), default=None)
    inception_date_str = inception_date.isoformat() if inception_date else None
    cagr_pct = (
        compute_cagr(invested, current_value, realised, inception_date)
        if inception_date
        else None
    )
    annualized_gain_eur = (
        round(invested * cagr_pct / 100, 2) if cagr_pct is not None else None
    )

    # Period return.
    # "All-time" is a lifetime figure, so it must equal the cost-basis total
    # return — deriving it from snapshots[0] instead understates it whenever
    # the daily-snapshot history started after the portfolio's inception
    # (which is why All-time previously showed ~0% next to a +12% Total Return).
    # Named windows (ytd/1m/1y) use the snapshot-based time-weighted return.
    if (period or "all").lower() == "all":
        period_ret = total_ret
    else:
        period_ret = period_return(
            db.get_snapshots(), current_value, period, current_cost=invested
        )
    bench_start = period_start_date(period)

    # Benchmark: total return over the selected window (or full history for
    # 'all'). Cached ~12h keyed by ticker+start — the historical close series
    # is immutable except for the most recent day.
    benchmark_ret = None
    try:
        if bench_start is not None:
            start = bench_start
        elif cash_flows:
            start = min(c[0] for c in cash_flows)
        else:
            start = None
        if start is not None:

            def _fetch_benchmark_ret(b=benchmark, s=start):
                hist = yf.download(
                    b, start=s.isoformat(), progress=False, auto_adjust=True
                )
                if hist.empty:
                    return None
                closes = hist["Close"]
                # yfinance may return multi-index columns -> take the first column
                if hasattr(closes, "columns"):
                    closes = closes.iloc[:, 0]
                closes = closes.dropna()
                if len(closes) <= 1:
                    return None
                first = float(closes.iloc[0])
                last = float(closes.iloc[-1])
                return round((last - first) / first * 100, 2)

            benchmark_ret = cached(
                db,
                f"yf:bench:{benchmark}:{start.isoformat()}",
                12 * 3600,
                _fetch_benchmark_ret,
            )
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
        "inception_date": inception_date_str,
        "cagr_pct": cagr_pct,
        "annualized_gain_eur": annualized_gain_eur,
    }


# ── Net-worth history ─────────────────────────────────────────────────────────


@router.get("/networth-history")
async def get_networth_history(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Daily portfolio value vs invested-cost snapshots."""
    return {"snapshots": db.get_snapshots()}


@router.post("/snapshot")
def take_snapshot(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
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


# ── Historical net-worth backfill ──────────────────────────────────────────────
# Reconstructs daily snapshots from transactions + historical prices so the
# net-worth chart, period returns and risk metrics work from inception (not just
# from when the daily cron started). Runs in a background thread (it fetches
# per-asset price history) and only fills dates that don't already have a
# snapshot, so it never overwrites the accurate forward cron snapshots.
_BACKFILL: dict = {
    "running": False,
    "done": 0,
    "total": 0,
    "added": 0,
    "message": "",
    "error": None,
}
_CRYPTO_YF_OVERRIDES = {"UNI": "UNI1"}


def _yf_symbol(asset: dict) -> str:
    sym = asset.get("symbol", "")
    if asset.get("asset_type") == "crypto":
        return f"{_CRYPTO_YF_OVERRIDES.get(sym, sym)}-EUR"
    return sym


def _run_backfill(db, force: bool = False) -> None:
    try:
        _BACKFILL.update(running=True, error=None, done=0, added=0)
        txs = [t for t in db.get_all_transactions() if t.get("transaction_date")]
        if not txs:
            _BACKFILL.update(running=False, message="No transactions to backfill.")
            return

        def d10(s):
            return str(s)[:10]

        start_d = datetime.strptime(
            min(d10(t["transaction_date"]) for t in txs), "%Y-%m-%d"
        ).date()
        today = date.today()
        aids = {t["asset_id"] for t in txs}
        assets = {aid: db.get_asset(aid) for aid in aids}

        # Per-asset historical close series (GBX-normalised); flat fallback to the
        # latest stored price for assets yfinance can't resolve (unlisted/P2P).
        _BACKFILL.update(message="Fetching historical prices…", total=len(aids))
        hist: dict = {}
        for i, aid in enumerate(aids):
            a = assets[aid] or {}
            series = []
            try:
                yfsym = _yf_symbol(a)
                ticker = yf.Ticker(yfsym)
                h = ticker.history(start=start_d, end=today + timedelta(days=1))
                gbx = False
                try:
                    gbx = ticker.fast_info.currency == "GBp"
                except Exception:
                    pass
                for idx, row in h.iterrows():
                    series.append(
                        (
                            idx.date().isoformat(),
                            float(row["Close"]) / (100.0 if gbx else 1.0),
                        )
                    )
            except Exception:
                pass
            hist[aid] = sorted(series)
            _BACKFILL.update(done=i + 1)

        def price_asof(aid, dstr):
            # Return the last close on/before the date, or None if we have no
            # real price there (caller values the position at cost instead).
            price = None
            for ds, close in hist.get(aid, []):
                if ds <= dstr:
                    price = close
                else:
                    break
            return price

        existing = (
            set() if force else {d10(s["snapshot_date"]) for s in db.get_snapshots()}
        )
        _BACKFILL.update(message="Computing daily snapshots…")
        added = 0
        d = start_d
        while d <= today:
            dstr = d.isoformat()
            if dstr not in existing:
                day_txs = [t for t in txs if d10(t["transaction_date"]) <= dstr]
                pos, _ = compute_positions(day_txs)
                value = cost = 0.0
                for aid, p in pos.items():
                    if p["quantity"] <= 0:
                        continue
                    cur = (assets.get(aid) or {}).get("currency", "EUR")
                    fx = _fx(cur)
                    px = price_asof(aid, dstr)
                    # No real price for this asset/date → value it at cost
                    # (neutral) instead of inventing a mark-to-market figure.
                    value += (p["quantity"] * px if px is not None else p["cost"]) * fx
                    cost += p["cost"] * fx
                db.record_snapshot(dstr, round(value, 2), round(cost, 2))
                added += 1
                _BACKFILL.update(added=added)
            d += timedelta(days=1)
        _BACKFILL.update(
            running=False, message=f"Backfilled {added} day(s) of history."
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Snapshot backfill failed")
        _BACKFILL.update(running=False, error=str(e))


@router.post("/backfill-snapshots")
async def backfill_snapshots(
    force: bool = Query(False, description="Recompute/overwrite existing snapshots"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Start a background reconstruction of historical net-worth snapshots."""
    if _BACKFILL["running"]:
        return {"status": "running", **_BACKFILL}
    threading.Thread(target=_run_backfill, args=(db, force), daemon=True).start()
    return {"status": "started"}


@router.get("/backfill-status")
async def backfill_status(api_key_info: dict = Depends(_auth)):
    """Progress of the historical backfill (poll while running)."""
    return _BACKFILL


# ── Tax estimate ──────────────────────────────────────────────────────────────


@router.get("/tax-estimate")
def get_tax_estimate(
    year: Optional[int] = Query(None, description="Tax year (default current)"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Spanish IRPF savings-base estimate: realised gains + dividends YTD, unrealised, harvest candidates."""
    yr = year or date.today().year
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    # Realised capital gains via FIFO (all portfolios), broken down per symbol
    calc = TaxCalculator(db)
    realised_gain = 0.0
    realised_by_symbol: list = []
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for sym, txns in report.items():
            sym_total = sum(float(getattr(t, "gain_loss", 0) or 0) for t in txns)
            realised_gain += sym_total
            a = db.get_asset_by_symbol(sym)
            realised_by_symbol.append(
                {
                    "symbol": sym,
                    "name": (a or {}).get("name", sym),
                    "realised_eur": round(sym_total, 2),
                }
            )
        realised_by_symbol.sort(key=lambda x: x["realised_eur"])
    except Exception as e:
        logger.warning(f"Tax report calc failed: {e}")

    # Dividend income this year
    all_txns = db.get_all_transactions()
    div = dividend_income(all_txns)
    div_this_year = div["by_year"].get(str(yr), 0.0)

    # Interest income this year (P2P / savings — taxed in the savings base too)
    interest_this_year = 0.0
    for tx in all_txns:
        if (tx.get("transaction_type") or "").lower() != "interest":
            continue
        d = str(tx.get("transaction_date", ""))[:10]
        if d[:4] == str(yr):
            interest_this_year += float(tx.get("total_amount") or 0)

    savings_base = realised_gain + div_this_year + interest_this_year
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
                    "name": asset.get("name", asset["symbol"]),
                    "quantity": round(pos["quantity"], 4),
                    "unrealised_loss_eur": round(gain, 2),
                }
            )

    harvest_candidates.sort(key=lambda x: x["unrealised_loss_eur"])

    return {
        "year": yr,
        "realised_gain_eur": round(realised_gain, 2),
        "realised_by_symbol": realised_by_symbol,
        "dividend_income_eur": round(div_this_year, 2),
        "interest_income_eur": round(interest_this_year, 2),
        "savings_base_eur": round(savings_base, 2),
        "estimated_tax_eur": estimated_tax,
        "unrealised_gain_eur": round(unrealised, 2),
        "harvest_candidates": harvest_candidates,
        "note": "Spanish IRPF base del ahorro estimate. Realised gains use FIFO. Not tax advice.",
    }


@router.get("/tax-optimizer")
def get_tax_optimizer(
    year: Optional[int] = Query(None),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Year-end tax optimisation: realised gains + income this year, the losses
    you could still harvest (flagging recent buys that trip Spain's 2-month
    rule), and the estimated tax saved by harvesting them. Informational only."""
    yr = year or date.today().year
    today = date.today()
    start, end = date(yr, 1, 1), date(yr, 12, 31)

    # Realised gains (FIFO) this year
    realised_gain = 0.0
    calc = TaxCalculator(db)
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for _sym, txns in report.items():
            realised_gain += sum(float(getattr(t, "gain_loss", 0) or 0) for t in txns)
    except Exception as e:
        logger.warning(f"Tax optimizer realised calc failed: {e}")

    # Income this year (dividends + interest) — both in the savings base
    all_txns = db.get_all_transactions()
    div_this_year = dividend_income(all_txns)["by_year"].get(str(yr), 0.0)
    interest_this_year = sum(
        float(t.get("total_amount") or 0)
        for t in all_txns
        if (t.get("transaction_type") or "").lower() == "interest"
        and str(t.get("transaction_date", ""))[:4] == str(yr)
    )
    income = div_this_year + interest_this_year

    # Harvest candidates: held positions at an unrealised loss, with the most
    # recent buy date so we can flag Spain's 2-month anti-application rule.
    positions, _ = _compute_positions(db)
    candidates = []
    harvestable = 0.0  # sum of clean (non-wash) losses, negative
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        cur = asset.get("currency", "EUR")
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        gain = (pos["quantity"] * price - pos["cost"]) * _fx(cur)
        if gain >= -1:
            continue
        last_buy = None
        for tx in db.get_transactions_by_asset(aid):
            if (tx.get("transaction_type") or "").lower() == "buy":
                d = str(tx.get("transaction_date", ""))[:10]
                if d and (last_buy is None or d > last_buy):
                    last_buy = d
        wash = False
        if last_buy:
            try:
                wash = (
                    today - datetime.strptime(last_buy, "%Y-%m-%d").date()
                ).days < 60
            except ValueError:
                wash = False
        if not wash:
            harvestable += gain
        candidates.append(
            {
                "symbol": asset["symbol"],
                "name": asset.get("name", asset["symbol"]),
                "quantity": round(pos["quantity"], 4),
                "unrealised_loss_eur": round(gain, 2),
                "last_buy": last_buy,
                "wash_sale_risk": wash,
            }
        )
    candidates.sort(key=lambda x: x["unrealised_loss_eur"])

    # Current vs after-harvest tax. Spanish savings base: capital gains/losses
    # net together; a net capital LOSS offsets up to 25% of dividend/interest
    # income, the rest carries forward (modelled simply here).
    def savings_tax(capital_result, inc):
        if capital_result >= 0:
            return irpf_savings_tax(capital_result + inc)
        offset = min(inc * 0.25, -capital_result)
        return irpf_savings_tax(max(inc - offset, 0))

    tax_current = savings_tax(realised_gain, income)
    tax_after = savings_tax(realised_gain + harvestable, income)

    return {
        "year": yr,
        "realised_gain_eur": round(realised_gain, 2),
        "dividend_income_eur": round(div_this_year, 2),
        "interest_income_eur": round(interest_this_year, 2),
        "income_eur": round(income, 2),
        "harvestable_loss_eur": round(harvestable, 2),
        "estimated_tax_now_eur": tax_current,
        "estimated_tax_after_harvest_eur": tax_after,
        "estimated_tax_saved_eur": round(tax_current - tax_after, 2),
        "candidates": candidates,
        "note": (
            "Estimate, not tax advice. Spain disallows a loss if you hold/rebuy "
            "the same security within 2 months — candidates bought in the last "
            "60 days are flagged; avoid rebuying harvested positions for 2 months."
        ),
    }


# ── Diversification & Risk ─────────────────────────────────────────────────────


@router.get("/diversification")
def get_diversification(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Sector / country / currency / asset-type concentration + Herfindahl index.

    Defined as a sync handler (not async) so FastAPI runs it in a threadpool:
    the per-holding yfinance ``.info`` lookups are blocking and would freeze
    the event loop — stalling every other request — if awaited inline.
    """
    positions, _ = _compute_positions(db)

    by_type: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_country: dict[str, float] = {}
    # Per-holding values for the textbook concentration measure (HHI over
    # individual positions, not asset-type buckets).
    by_position: dict[str, float] = {}
    position_names: dict[str, str] = {}
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
        sym = asset["symbol"]
        by_position[sym] = by_position.get(sym, 0) + value
        position_names[sym] = asset.get("name", sym)
        by_type[asset.get("asset_type", "other")] = (
            by_type.get(asset.get("asset_type", "other"), 0) + value
        )
        by_currency[cur] = by_currency.get(cur, 0) + value

        # sector/country: use ticker when available so ISIN-keyed assets resolve
        # correctly; crypto/bond short-circuited to hardcoded defaults.
        from portf_manager.services.portfolio_advisor import _resolve_sector_country

        sector, country = _resolve_sector_country(db, asset)
        by_sector[sector] = by_sector.get(sector, 0) + value
        by_country[country] = by_country.get(country, 0) + value

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

    # Largest single holding (more meaningful than the biggest asset-type bucket)
    largest_symbol = None
    if by_position:
        largest_symbol = max(by_position, key=by_position.get)
    largest_pct = (
        round(by_position[largest_symbol] / total * 100, 1)
        if total and largest_symbol
        else 0
    )

    return {
        "total_value_eur": round(total, 2),
        "by_asset_type": pct_map(by_type),
        "by_currency": pct_map(by_currency),
        "by_sector": pct_map(by_sector),
        "by_country": pct_map(by_country),
        # HHI over individual holdings — the standard portfolio concentration index
        "concentration_hhi": herfindahl(by_position),
        "largest_position_pct": largest_pct,
        "largest_position_symbol": largest_symbol,
        "largest_position_name": position_names.get(largest_symbol, largest_symbol),
    }


@router.get("/risk")
def get_risk(
    benchmark: str = Query("^GSPC", description="Benchmark ticker for beta/alpha"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Max drawdown, volatility, Sharpe, Sortino, Calmar, Beta, Alpha from snapshots."""
    snapshots = db.get_snapshots()
    if len(snapshots) < 3:
        return {
            "max_drawdown_pct": None,
            "volatility_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "calmar_ratio": None,
            "beta": None,
            "alpha_pct": None,
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
    sharpe = None
    if vol and vol > 0:
        sharpe = round((mean_daily * 252) / vol, 2)

    # Sortino
    sortino = sortino_ratio(returns)

    # Snapshot-based CAGR (for Calmar)
    snap_dates = [s["snapshot_date"][:10] for s in snapshots]
    snap_days = (
        date.fromisoformat(snap_dates[-1]) - date.fromisoformat(snap_dates[0])
    ).days
    snap_cagr_pct = None
    if snap_days >= 365 and values[0] > 0 and values[-1] > 0:
        snap_cagr_pct = round(
            ((values[-1] / values[0]) ** (365.25 / snap_days) - 1) * 100, 2
        )

    max_dd_pct = round(max_dd * 100, 2)
    calmar = calmar_ratio(snap_cagr_pct, max_dd_pct)

    # Beta / Alpha — fetches benchmark daily closes (cached 12h)
    beta_val: Optional[float] = None
    alpha_val: Optional[float] = None
    try:
        cache_key = (
            f"yf:bench-daily:{benchmark}:{snap_dates[0]}:{date.today().isoformat()}"
        )

        def _fetch_bench(b=benchmark, s=snap_dates[0]):
            hist = yf.download(b, start=s, progress=False, auto_adjust=True)
            if hist.empty:
                return []
            closes = hist["Close"]
            if hasattr(closes, "columns"):
                closes = closes.iloc[:, 0]
            closes = closes.dropna()
            return [
                (str(dt.date()), float(p))
                for dt, p in zip(closes.index, closes.tolist())
            ]

        bench_data: list[tuple[str, float]] = cached(
            db, cache_key, 12 * 3600, _fetch_bench
        )

        if bench_data and len(bench_data) >= 2:
            bench_by_date: dict[str, float] = {}
            for i in range(1, len(bench_data)):
                d_str, prev_p, curr_p = (
                    bench_data[i][0],
                    bench_data[i - 1][1],
                    bench_data[i][1],
                )
                if prev_p > 0:
                    bench_by_date[d_str] = (curr_p - prev_p) / prev_p

            return_dates = snap_dates[1:]
            aligned_p: list[float] = []
            aligned_b: list[float] = []
            for i, d_str in enumerate(return_dates):
                if d_str in bench_by_date and i < len(returns):
                    aligned_p.append(returns[i])
                    aligned_b.append(bench_by_date[d_str])

            bench_cagr = None
            if snap_days >= 365 and bench_data[0][1] > 0 and bench_data[-1][1] > 0:
                bench_cagr = (
                    (bench_data[-1][1] / bench_data[0][1]) ** (365.25 / snap_days)
                ) - 1

            snap_cagr_frac = snap_cagr_pct / 100 if snap_cagr_pct is not None else None
            beta_val, alpha_val = compute_beta_alpha(
                aligned_p, aligned_b, snap_cagr_frac, bench_cagr
            )
    except Exception as e:
        logger.warning(f"Beta/alpha computation failed: {e}")

    return {
        "max_drawdown_pct": max_dd_pct,
        "volatility_pct": round(vol * 100, 2) if vol else None,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "beta": beta_val,
        "alpha_pct": alpha_val,
        "snapshots_used": len(snapshots),
    }


# ── Fees & Costs ───────────────────────────────────────────────────────────────


@router.get("/fees")
def get_fees(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
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
    # Build symbol → name lookup once so lot rows can include a friendly name.
    asset_names: dict[str, str] = {
        a["symbol"]: a.get("name", "") or ""
        for a in db.get_all_assets()
        if a.get("symbol")
    }
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for symbol, txns in report.items():
            for t in txns:
                gain = float(getattr(t, "gain_loss", 0) or 0)
                total_gain += gain
                lots.append(
                    {
                        "symbol": symbol,
                        "name": asset_names.get(symbol, ""),
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


@router.get("/data-freshness")
async def get_data_freshness(
    stale_days: int = Query(
        4,
        description=(
            "Flag held auto-priced assets whose latest price is older than this "
            "many calendar days (4 covers a normal weekend)."
        ),
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Freshness of the external price data behind value / gain-loss figures.

    Reports when prices were last fetched, the market date they are 'as of',
    and any currently-held auto-priced assets whose price has gone stale (or was
    never available). Manual-price assets (auto_price=0) are excluded from the
    stale check since their prices are intentionally hand-maintained.
    """
    positions, _ = _compute_positions(db)
    held_ids = [aid for aid, d in positions.items() if d["quantity"] > 0]

    today = date.today()
    # Most recent fetch time (prices.created_at) and market date (price_date).
    last_refresh = None
    prices_as_of = None
    stale = []
    checked = 0

    for aid in held_ids:
        asset = db.get_asset(aid)
        if not asset:
            continue
        auto = bool(asset.get("auto_price", 1))
        price_data = db.get_latest_price(aid)

        if not price_data:
            # Held but never priced (e.g. an ISIN/P2P asset with no Yahoo data).
            if auto:
                stale.append(
                    {
                        "symbol": asset.get("symbol", ""),
                        "name": asset.get("name", ""),
                        "price_date": None,
                        "age_days": None,
                        "reason": "no price data",
                    }
                )
            continue

        checked += 1
        created = price_data.get("created_at")
        if created and (last_refresh is None or str(created) > str(last_refresh)):
            last_refresh = created
        pdate = price_data.get("price_date")
        if pdate and (prices_as_of is None or str(pdate) > str(prices_as_of)):
            prices_as_of = pdate

        if not auto:
            continue
        try:
            d = datetime.strptime(str(pdate)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        age = (today - d).days
        if age > stale_days:
            stale.append(
                {
                    "symbol": asset.get("symbol", ""),
                    "name": asset.get("name", ""),
                    "price_date": str(pdate)[:10],
                    "age_days": age,
                    "reason": "stale price",
                }
            )

    # SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS".
    refresh_age_hours = None
    if last_refresh:
        try:
            dt = datetime.strptime(str(last_refresh)[:19], "%Y-%m-%d %H:%M:%S")
            refresh_age_hours = round(
                (datetime.utcnow() - dt).total_seconds() / 3600.0, 1
            )
        except ValueError:
            pass

    # Worst offenders first; "no price" rows (age_days None) sort to the end.
    stale.sort(key=lambda x: (x["age_days"] is None, -(x["age_days"] or 0)))

    return {
        "last_refresh": str(last_refresh) if last_refresh else None,
        "refresh_age_hours": refresh_age_hours,
        "prices_as_of": str(prices_as_of)[:10] if prices_as_of else None,
        "stale_days_threshold": stale_days,
        "checked": checked,
        "stale_count": len(stale),
        "stale": stale,
    }


@router.get("/update-runs")
async def get_update_runs(
    limit: int = Query(20, ge=1, le=100, description="Max runs to return."),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Recent price-update runs (timings, success/skip/error counts, symbols).

    Powers the Diagnostics page's update-history table so the daily cron's
    outcome — which assets were skipped and why prices may be stale — is
    visible in the app instead of being lost to the cron's stdout.
    """
    return {"runs": db.get_price_update_runs(limit=limit)}


class UpdateRunIn(BaseModel):
    """Outcome of one price-update run, posted by the CLI in server mode."""

    started_at: str
    duration_seconds: float = 0.0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    skipped_symbols: list[str] = []
    error_symbols: list[str] = []
    api_errors: list[str] = []
    source: str = "cron"


@router.post("/update-runs")
async def record_update_run(
    run: UpdateRunIn,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Persist a price-update run (server-mode path for the CLI cron)."""
    run_id = db.record_price_update_run(
        started_at=run.started_at,
        duration_seconds=run.duration_seconds,
        updated_count=run.updated_count,
        skipped_count=run.skipped_count,
        error_count=run.error_count,
        skipped_symbols=run.skipped_symbols,
        error_symbols=run.error_symbols,
        api_errors=run.api_errors,
        source=run.source,
    )
    return {"id": run_id}


# ── Stress Testing ───────────────────────────────────────────────────────────


@router.get("/stress-test")
def stress_test(
    scenario: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Stress test portfolio against a historical crash scenario or custom date range.

    Pass ``scenario`` (one of: 2008, 2020, 2022, dotcom) OR both ``from`` and
    ``to`` (YYYY-MM-DD) for a custom period. Results for preset scenarios are
    cached 7 days; custom queries run live.
    """
    if scenario is not None and scenario not in _STRESS_SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{scenario}'. Valid: {list(_STRESS_SCENARIOS)}",
        )

    if scenario and scenario in _STRESS_SCENARIOS:
        meta = _STRESS_SCENARIOS[scenario]
        from_str = meta["from_date"]
        to_str = meta["to_date"]
        label = meta["label"]
        scenario_key = scenario
    elif from_date and to_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d").date()
            td = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD.")
        if td <= fd:
            raise HTTPException(
                status_code=400, detail="End date must be after start date."
            )
        from_str = from_date
        to_str = to_date
        label = f"{from_date} to {to_date}"
        scenario_key = "custom"
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'scenario' or both 'from' and 'to' query parameters.",
        )

    preset_fallbacks = _STRESS_FALLBACKS.get(scenario_key, _STRESS_FALLBACKS["2008"])

    def _compute() -> dict:
        positions, _ = _compute_positions(db)
        assets_by_id = {a["id"]: a for a in db.get_all_assets(active_only=False)}

        if scenario_key == "custom":
            sp500_ret = _get_ticker_return("^GSPC", from_str, to_str)
            equity_fb = sp500_ret if sp500_ret is not None else -30.0
            active_fallbacks: dict[str, float] = {
                "stock": equity_fb,
                "etf": equity_fb,
                "index": equity_fb,
                "mutual_fund": round(equity_fb * 0.8, 2),
                "bond": 0.0,
                "crypto": round(equity_fb * 1.5, 2),
                "commodity": 0.0,
                "cash": 0.0,
            }
        else:
            active_fallbacks = preset_fallbacks

        assets_out = []
        total_current = 0.0
        total_stressed = 0.0

        for aid, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            asset = assets_by_id.get(aid)
            if not asset:
                continue
            cur = asset.get("currency", "EUR")
            price_data = db.get_latest_price(aid)
            price = float(price_data["price"]) if price_data else 0.0
            current_value_eur = pos["quantity"] * price * _fx(cur)
            if current_value_eur <= 0:
                continue

            sym = _yf_symbol(asset)
            hist_ret: float | None = None
            data_source = "fallback"
            if sym:
                hist_ret = _get_ticker_return(sym, from_str, to_str)
                if hist_ret is not None:
                    data_source = "yfinance"

            if hist_ret is None:
                asset_type = asset.get("asset_type", "stock")
                hist_ret = active_fallbacks.get(
                    asset_type, active_fallbacks.get("stock", -30.0)
                )

            stressed_value_eur = current_value_eur * (1 + hist_ret / 100)
            loss_eur = stressed_value_eur - current_value_eur
            total_current += current_value_eur
            total_stressed += stressed_value_eur

            assets_out.append(
                {
                    "symbol": asset.get("symbol", ""),
                    "name": asset.get("name", ""),
                    "asset_type": asset.get("asset_type", ""),
                    "current_value_eur": round(current_value_eur, 2),
                    "historical_return_pct": round(hist_ret, 2),
                    "stressed_value_eur": round(stressed_value_eur, 2),
                    "loss_eur": round(loss_eur, 2),
                    "data_source": data_source,
                }
            )

        assets_out.sort(key=lambda a: a["loss_eur"])
        total_loss = total_stressed - total_current
        total_loss_pct = (
            (total_loss / total_current * 100) if total_current > 0 else 0.0
        )

        return {
            "scenario": scenario_key,
            "label": label,
            "from_date": from_str,
            "to_date": to_str,
            "portfolio_current_value_eur": round(total_current, 2),
            "portfolio_stressed_value_eur": round(total_stressed, 2),
            "total_loss_eur": round(total_loss, 2),
            "total_loss_pct": round(total_loss_pct, 2),
            "assets": assets_out,
        }

    if scenario_key != "custom":
        return cached(db, f"stress:{scenario_key}", 7 * 24 * 3600, _compute)
    return _compute()


# ── Data Quality ──────────────────────────────────────────────────────────────


@router.get("/dq/reconciliation")
def dq_reconciliation(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Per-portfolio cash reconciliation.

    Computes the 'implied cash' each portfolio should hold at the broker
    (deposits − withdrawals − buy costs + sell proceeds + dividends + interest)
    and the current invested value from stored prices. Both figures are EUR.
    The caller compares implied_cash against the broker's cash balance.

    Plain ``def`` because _fx() may make a blocking yfinance call for
    non-EUR assets.
    """
    portfolios = db.get_all_portfolios()
    result = []

    for p in portfolios:
        pid = p["id"]
        txns = db.get_all_transactions(portfolio_id=pid)
        bookings = db.get_all_bookings(portfolio_id=pid)

        deposits = sum(
            float(b["amount"] or 0) for b in bookings if b["action"] == "Deposit"
        )
        withdrawals = sum(
            float(b["amount"] or 0) for b in bookings if b["action"] == "Withdrawal"
        )
        net_bookings = deposits - withdrawals

        buy_costs = 0.0
        sell_proceeds = 0.0
        dividend_income_total = 0.0
        interest_income_total = 0.0
        for tx in txns:
            amt = float(tx["total_amount"] or 0)
            tx_type = tx["transaction_type"]
            if tx_type == "buy":
                buy_costs += amt
            elif tx_type == "sell":
                sell_proceeds += amt
            elif tx_type == "dividend":
                dividend_income_total += amt
            elif tx_type == "interest":
                interest_income_total += amt

        implied_cash = (
            net_bookings
            - buy_costs
            + sell_proceeds
            + dividend_income_total
            + interest_income_total
        )

        # Invested value: held quantity × latest stored price (EUR-converted).
        # Falls back to cost basis when no price is stored.
        positions, _ = compute_positions(txns)
        invested_value = 0.0
        for asset_id_key, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            price_data = db.get_latest_price(asset_id_key)
            asset = db.get_asset(asset_id_key)
            currency = (asset.get("currency") or "EUR") if asset else "EUR"
            if price_data and price_data.get("price"):
                price = float(price_data["price"])
                invested_value += pos["quantity"] * price * _fx(currency)
            else:
                invested_value += pos["cost"] * _fx(currency)

        result.append(
            {
                "portfolio_id": pid,
                "portfolio_name": p["name"],
                "net_bookings": round(net_bookings, 2),
                "buy_costs": round(buy_costs, 2),
                "sell_proceeds": round(sell_proceeds, 2),
                "dividend_income": round(dividend_income_total, 2),
                "interest_income": round(interest_income_total, 2),
                "implied_cash": round(implied_cash, 2),
                "invested_value": round(invested_value, 2),
                "total_accounted": round(implied_cash + invested_value, 2),
            }
        )

    return {"portfolios": result}


def _within_pct(a: float, b: float, pct: float) -> bool:
    """Return True when a and b are within pct (0–1) of each other."""
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(abs(a), abs(b)) <= pct


@router.get("/dq/duplicates")
def dq_duplicates(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Scan all transactions for fuzzy near-duplicates.

    Groups by (portfolio_id, asset_id, transaction_type). Within each group,
    flags pairs where date is within ±3 days AND quantity within ±5% AND
    price within ±5%. Labels 'likely' when same day + qty/price within ±1%.
    """
    txns = db.get_all_transactions()
    groups: dict = defaultdict(list)
    for tx in txns:
        key = (
            tx.get("portfolio_id"),
            tx.get("asset_id"),
            tx.get("transaction_type"),
        )
        groups[key].append(tx)

    def _summary(tx: dict) -> dict:
        return {
            "id": tx["id"],
            "date": str(tx.get("transaction_date") or "")[:10],
            "asset": tx.get("symbol") or "",
            "asset_name": tx.get("name") or "",
            "type": tx.get("transaction_type") or "",
            "quantity": float(tx.get("quantity") or 0),
            "price": float(tx.get("price") or 0),
            "portfolio": tx.get("portfolio_name") or "",
        }

    duplicates = []
    seen_pairs: set = set()

    for group_txns in groups.values():
        group_txns.sort(key=lambda t: str(t.get("transaction_date") or ""))
        n = len(group_txns)
        for i in range(n):
            tx_a = group_txns[i]
            date_a_str = str(tx_a.get("transaction_date") or "")[:10]
            try:
                d_a = datetime.strptime(date_a_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            for j in range(i + 1, n):
                tx_b = group_txns[j]
                date_b_str = str(tx_b.get("transaction_date") or "")[:10]
                try:
                    d_b = datetime.strptime(date_b_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                day_diff = abs((d_b - d_a).days)
                if day_diff > 3:
                    # list is sorted by date; no closer matches ahead
                    break

                qty_a = float(tx_a.get("quantity") or 0)
                qty_b = float(tx_b.get("quantity") or 0)
                price_a = float(tx_a.get("price") or 0)
                price_b = float(tx_b.get("price") or 0)

                if not (
                    _within_pct(qty_a, qty_b, 0.05)
                    and _within_pct(price_a, price_b, 0.05)
                ):
                    continue

                id_a, id_b = tx_a["id"], tx_b["id"]
                pair_key = f"dup:{min(id_a, id_b)}:{max(id_a, id_b)}"
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                label = (
                    "likely"
                    if day_diff == 0
                    and _within_pct(qty_a, qty_b, 0.01)
                    and _within_pct(price_a, price_b, 0.01)
                    else "possible"
                )

                duplicates.append(
                    {
                        "label": label,
                        "key": pair_key,
                        "tx_a": _summary(tx_a),
                        "tx_b": _summary(tx_b),
                    }
                )

    return {"duplicates": duplicates}


@router.get("/dq/suspicious")
def dq_suspicious(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Scan transactions for data anomalies.

    Checks (per transaction, chronologically):
    - zero_price: buy or sell with price = 0 (splits and dividends excluded)
    - zero_qty: non-split, non-dividend transaction with quantity = 0
    - negative_position: sell that pushes the running quantity below zero
    - dividend_before_buy: dividend recorded before the first buy for that asset
    - price_outlier: price > 5× or < 0.2× the median for that asset
      (requires ≥3 price data points to compute median)
    """
    txns = db.get_all_transactions()
    txns_sorted = sorted(
        txns,
        key=lambda t: (str(t.get("transaction_date") or ""), t.get("id", 0) or 0),
    )

    # Pre-compute per-asset price median (buy/sell only, price > 0)
    asset_prices: dict = {}
    for tx in txns_sorted:
        if tx.get("transaction_type") in ("buy", "sell"):
            p = float(tx.get("price") or 0)
            if p > 0:
                asset_prices.setdefault(tx.get("asset_id"), []).append(p)

    asset_median: dict = {}
    for aid, prices in asset_prices.items():
        if len(prices) >= 3:
            asset_median[aid] = statistics.median(prices)

    running_qty: dict = {}
    first_buy: dict = {}
    issues = []

    for tx in txns_sorted:
        aid = tx.get("asset_id")
        tx_type = tx.get("transaction_type") or ""
        qty = float(tx.get("quantity") or 0)
        price = float(tx.get("price") or 0)
        tx_id = tx["id"]
        tx_date = str(tx.get("transaction_date") or "")[:10]
        asset_sym = tx.get("symbol") or ""
        asset_nm = tx.get("name") or ""

        def _flag(severity: str, check: str, description: str) -> None:
            issues.append(
                {
                    "severity": severity,
                    "key": f"susp:{tx_id}:{check}",
                    "check": check,
                    "transaction_id": tx_id,
                    "asset": asset_sym,
                    "asset_name": asset_nm,
                    "date": tx_date,
                    "type": tx_type,
                    "description": description,
                }
            )

        # zero_price: buy/sell only (splits and dividends legitimately have price 0)
        if tx_type in ("buy", "sell") and price == 0:
            _flag(
                "warning",
                "zero_price",
                f"{tx_type.capitalize()} transaction has price = 0",
            )

        # zero_qty: buy/sell only (interest, transfer_in/out etc. legitimately omit qty)
        if tx_type in ("buy", "sell") and qty == 0:
            _flag("warning", "zero_qty", "Transaction has quantity = 0")

        # dividend_before_buy
        if tx_type == "dividend" and aid not in first_buy:
            _flag(
                "info",
                "dividend_before_buy",
                "Dividend recorded before any buy for this asset",
            )

        # price_outlier (buy/sell, price > 0, median established)
        if tx_type in ("buy", "sell") and price > 0 and aid in asset_median:
            med = asset_median[aid]
            if med > 0 and (price > 5.0 * med or price < 0.2 * med):
                _flag(
                    "warning",
                    "price_outlier",
                    f"Price {price:.4f} is far from median {med:.4f} (possible unit error)",
                )

        # Update running state
        if tx_type == "buy":
            running_qty[aid] = running_qty.get(aid, 0.0) + qty
            first_buy.setdefault(aid, tx_date)
        elif tx_type == "sell":
            prev = running_qty.get(aid, 0.0)
            new_qty = prev - qty
            if new_qty < -0.001:
                _flag(
                    "warning",
                    "negative_position",
                    f"Sell results in negative quantity ({new_qty:.4f}); missing buy transaction?",
                )
            running_qty[aid] = new_qty
        elif tx_type == "transfer_in":
            running_qty[aid] = running_qty.get(aid, 0.0) + qty
        elif tx_type == "transfer_out":
            running_qty[aid] = running_qty.get(aid, 0.0) - qty
        elif tx_type == "split":
            running_qty[aid] = running_qty.get(aid, 0.0) * qty

    return {"issues": issues}


# ── Asset Correlation Matrix ──────────────────────────────────────────────────


@router.get("/correlation")
def get_correlation(
    portfolio_id: Optional[int] = Query(None),
    days: int = Query(90, ge=30, le=365),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Pearson correlation matrix of daily log-returns for held assets.

    Only assets with at least 10 price points in the requested window are
    included. The matrix is computed over the intersection of dates where
    every included asset has a recorded price.
    """
    txs = db.get_all_transactions(portfolio_id=portfolio_id)
    positions, _ = compute_positions(txs)

    # Only assets with a meaningful open position.
    held_ids = [aid for aid, pos in positions.items() if pos["quantity"] > 0.01]

    start_date = date.today() - timedelta(days=days)
    start_date_str = start_date.isoformat()

    assets_by_id = {a["id"]: a for a in db.get_all_assets(active_only=False)}

    # price_series maps asset_id → {date_str: price}
    price_series: dict[int, dict[str, float]] = {}
    assets_skipped: list[str] = []

    for aid in held_ids:
        history = db.get_price_history(aid, start_date=start_date_str)
        if not history or len(history) < 10:
            asset = assets_by_id.get(aid)
            symbol = asset.get("symbol", str(aid)) if asset else str(aid)
            assets_skipped.append(symbol)
            continue
        price_series[aid] = {
            str(row["price_date"])[:10]: float(row["price"]) for row in history
        }

    if len(price_series) < 2:
        return {
            "symbols": [],
            "names": [],
            "matrix": [],
            "days_used": 0,
            "assets_skipped": assets_skipped,
            "note": "Not enough overlapping price history.",
        }

    # Intersect dates across all included assets.
    date_sets = [set(dates.keys()) for dates in price_series.values()]
    common_dates = sorted(date_sets[0].intersection(*date_sets[1:]))

    if len(common_dates) < 5:
        return {
            "symbols": [],
            "names": [],
            "matrix": [],
            "days_used": 0,
            "assets_skipped": assets_skipped,
            "note": "Not enough overlapping price history.",
        }

    # Build ordered list of asset ids that passed the filters.
    included_ids = list(price_series.keys())

    def _log_returns(aid: int) -> list[float]:
        """Compute daily log-returns from the common-date price series."""
        prices = [price_series[aid][d] for d in common_dates]
        return [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
            if prices[i - 1] > 0 and prices[i] > 0
        ]

    returns_map: dict[int, list[float]] = {
        aid: _log_returns(aid) for aid in included_ids
    }

    n = len(included_ids)

    def _pearson(a: list[float], b: list[float]) -> float:
        """Compute Pearson correlation between two equal-length return series."""
        if len(a) < 2 or len(a) != len(b):
            return 0.0
        mean_a = statistics.mean(a)
        mean_b = statistics.mean(b)
        cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / len(a)
        std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a) / len(a))
        std_b = math.sqrt(sum((y - mean_b) ** 2 for y in b) / len(b))
        if std_a == 0 or std_b == 0:
            return 0.0
        return round(cov / (std_a * std_b), 3)

    # Build NxN symmetric matrix; diagonal is always 1.0.
    matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            r = _pearson(returns_map[included_ids[i]], returns_map[included_ids[j]])
            matrix[i][j] = r
            matrix[j][i] = r

    symbols = []
    names = []
    for aid in included_ids:
        asset = assets_by_id.get(aid)
        symbols.append(asset.get("symbol", str(aid)) if asset else str(aid))
        names.append(asset.get("name", "") if asset else "")

    return {
        "symbols": symbols,
        "names": names,
        "matrix": matrix,
        "days_used": len(common_dates) - 1,
        "assets_skipped": assets_skipped,
        "date_range": {
            "from": common_dates[0],
            "to": common_dates[-1],
        },
    }


# ── Portfolio Comparison ──────────────────────────────────────────────────────


@router.get("/portfolio-comparison")
def get_portfolio_comparison(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Performance comparison across all portfolios.

    Returns invested, current value, return %, IRR, and asset count per
    portfolio, sorted by current value descending.
    """
    portfolios = db.get_all_portfolios()
    assets_by_id = {a["id"]: a for a in db.get_all_assets(active_only=False)}

    results = []
    for portfolio in portfolios:
        pid = portfolio["id"]
        txs = db.get_all_transactions(portfolio_id=pid)
        if not txs:
            continue

        positions, _ = compute_positions(txs)

        invested_eur = 0.0
        current_value_eur = 0.0
        for aid, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            asset = assets_by_id.get(aid)
            if not asset:
                continue
            cur = asset.get("currency", "EUR")
            invested_eur += pos["cost"] * _fx(cur)
            price_data = db.get_latest_price(aid)
            price = float(price_data["price"]) if price_data else 0.0
            current_value_eur += pos["quantity"] * price * _fx(cur)

        # Cash flows for IRR: buys negative, sells and dividends positive (EUR).
        cash_flows: list[tuple[date, float]] = []
        for tx in txs:
            d = tx.get("transaction_date", "")
            try:
                dd = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            asset = assets_by_id.get(tx["asset_id"])
            cur = asset.get("currency", "EUR") if asset else "EUR"
            amount_eur = float(tx["total_amount"] or 0) * _fx(cur)
            t = tx["transaction_type"].lower()
            if t == "buy":
                cash_flows.append((dd, -amount_eur))
            elif t == "sell":
                cash_flows.append((dd, amount_eur))
            elif t == "dividend":
                cash_flows.append((dd, amount_eur))

        irr = money_weighted_irr(cash_flows, current_value_eur)
        total_return_pct = (
            round((current_value_eur - invested_eur) / invested_eur * 100, 2)
            if invested_eur > 0
            else 0.0
        )
        asset_count = sum(1 for pos in positions.values() if pos["quantity"] > 0.001)

        results.append(
            {
                "portfolio_id": pid,
                "name": portfolio["name"],
                "invested_eur": round(invested_eur, 2),
                "current_value_eur": round(current_value_eur, 2),
                "gain_loss_eur": round(current_value_eur - invested_eur, 2),
                "total_return_pct": total_return_pct,
                "irr_pct": round(irr * 100, 2) if irr is not None else None,
                "asset_count": asset_count,
                "transaction_count": len(txs),
            }
        )

    results.sort(key=lambda x: -x["current_value_eur"])
    return results
