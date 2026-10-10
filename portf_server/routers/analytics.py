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
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from portf_manager.services.analytics_service import (
    compute_beta_alpha,
    current_year_savings_components,
    dividend_income,
    dividend_ttm_enrichment,
    irpf_savings_tax,
    period_return,
    period_start_date,
    weekly_from_closes,
    weekly_from_daily,
)
from portf_manager.services.performance import (
    DEFAULT_BENCHMARK,
    PERFORMANCE_BENCHMARKS,
    benchmark_info,
    compute_performance,
    lifetime_twr,
    portfolio_totals,
    to_eur_closes,
    to_eur_transactions,
)
from portf_manager.services.risk_metrics import (
    compute_portfolio_risk,
    daily_realised_gains,
)
from portf_manager.tax_calculator import TaxCalculator
from portf_manager.positions import compute_positions
from portf_manager.cache import cached
from portf_manager.services.price_updater import _CRYPTO_YF_OVERRIDES
from portf_manager import market

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

# Reuse the portfolios router's resilient FX helper: it pre-seeds typical
# rates with fresh timestamps (so the first request never blocks on yfinance)
# and falls back to the cached/default rate on failure (so a yfinance outage
# can't make us re-hit the network per position — the cause of the tax-estimate
# 504 gateway timeouts).
from .portfolios import _get_fx_rate as _fx

_KNOWN_BENCHMARKS = set(PERFORMANCE_BENCHMARKS)

# Historical rates are immutable — memoise per (currency, date) for the
# lifetime of the worker so per-lot loops don't re-hit the kv_cache.
_FX_HIST_MEMO: dict[tuple[str, str], float] = {}


def _fx_on(db, currency: str, on_date) -> float:
    """EUR rate at *on_date* (transaction-date FX); current rate as fallback.

    Accepts a date, a 'YYYY-MM-DD...' string, or None. Only genuinely
    historical (non-stale) rates are memoised.
    """
    cur = (currency or "EUR").strip().upper()
    if cur == "EUR":
        return 1.0
    if isinstance(on_date, str):
        try:
            on_date = datetime.strptime(on_date[:10], "%Y-%m-%d").date()
        except ValueError:
            on_date = None
    if on_date is None:
        return _fx(cur)
    memo_key = (cur, on_date.isoformat())
    if memo_key in _FX_HIST_MEMO:
        return _FX_HIST_MEMO[memo_key]
    rate, stale = market.get_fx_eur_on(db, cur, on_date)
    if not stale:
        _FX_HIST_MEMO[memo_key] = rate
    return rate


def _savings_income_eur(db, transactions: list, yr: int) -> tuple[float, float]:
    """Dividend and interest income for *yr* in EUR at transaction-date FX.

    Returns:
        (dividends_eur, interest_eur) — the two savings-base income legs.
    """
    dividends = 0.0
    interest = 0.0
    for tx in transactions:
        tx_type = (tx.get("transaction_type") or "").lower()
        if tx_type not in ("dividend", "interest"):
            continue
        d = str(tx.get("transaction_date", ""))[:10]
        if d[:4] != str(yr):
            continue
        cur = (tx.get("currency") or "EUR").upper()
        amount_eur = float(tx.get("total_amount") or 0) * _fx_on(db, cur, d)
        if tx_type == "dividend":
            dividends += amount_eur
        else:
            interest += amount_eur
    return dividends, interest


def _year_withholding_eur(
    db, transactions: list, start, end
) -> tuple[float, float, float]:
    """Gross dividends + withholding at source for the period, in EUR.

    Withholding (the per-transaction ``tax`` field) is counted on both
    dividend and interest rows — P2P/savings interest (e.g. Mintos) is
    withheld at source too and is equally creditable under IRPF.

    Returns:
        (dividends_gross_eur, dividend_withholding_eur, interest_withholding_eur)
    """
    dividends_gross = 0.0
    dividend_wh = 0.0
    interest_wh = 0.0
    for tx in transactions:
        tx_type = (tx.get("transaction_type") or "").lower()
        if tx_type not in ("dividend", "interest"):
            continue
        try:
            dd = datetime.strptime(
                str(tx.get("transaction_date", ""))[:10], "%Y-%m-%d"
            ).date()
        except ValueError:
            continue
        if not (start <= dd <= end):
            continue
        cur = (tx.get("currency") or "EUR").upper()
        fx = _fx_on(db, cur, dd)
        withheld = float(tx.get("tax") or 0) * fx
        if tx_type == "dividend":
            dividends_gross += float(tx.get("total_amount") or 0) * fx
            dividend_wh += withheld
        else:
            interest_wh += withheld
    return dividends_gross, dividend_wh, interest_wh


def _lot_eur_amounts(db, currency: str, t) -> tuple[float, float]:
    """(proceeds_eur, cost_basis_eur) for one TaxTransaction lot.

    IRPF rule: proceeds convert at sell-date FX, cost basis at purchase-date
    FX — the FX gain/loss is itself part of the taxable result.
    """
    proceeds = float(getattr(t, "sell_amount", 0) or 0)
    cost = float(getattr(t, "purchase_amount", 0) or 0)
    fx_sell = _fx_on(db, currency, getattr(t, "sell_date", None))
    fx_buy = _fx_on(db, currency, getattr(t, "purchase_date", None))
    return proceeds * fx_sell, cost * fx_buy


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
    """Dividend income by year, month, symbol + projected forward annual income.

    Every amount is in EUR, each payment converted at its own date's rate, and
    yield-on-cost divides by the EUR cost basis at purchase-date rates — so a
    USD dividend and a EUR one add up, and neither ratio moves with today's FX.
    """
    txns = to_eur_transactions(db.get_all_transactions(), _fx_on_db(db))
    income = dividend_income(txns)

    # Build cost_by_symbol from current open positions (for yield-on-cost)
    positions, _ = compute_positions(txns)
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
        "currency": "EUR",
        "names": names,
    }


# ── Performance ───────────────────────────────────────────────────────────────


def _fx_on_db(db):
    """``(currency, date)`` → EUR rate on that date, bound to *db*."""
    return lambda cur, d: _fx_on(db, cur, d)


def _benchmark_currency(ticker: str) -> Optional[str]:
    """Quote currency of a ticker outside the known benchmark list."""
    try:
        return str(yf.Ticker(ticker).fast_info["currency"] or "").upper() or None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Benchmark currency lookup failed for {ticker}: {e}")
        return None


def _benchmark_closes_eur(db, ticker: str, start: date) -> tuple[list, dict]:
    """Daily benchmark closes from *start*, in EUR, plus the benchmark's info.

    Raw closes are cached 12h (history is immutable except for the latest
    day); the EUR conversion uses each day's rate.
    """
    info = benchmark_info(ticker)
    if ticker not in _KNOWN_BENCHMARKS:
        cur = cached(
            db,
            f"yf:bench-cur:{ticker}",
            30 * 86400,
            lambda: _benchmark_currency(ticker),
        )
        info = benchmark_info(ticker, cur)

    def _fetch():
        hist = yf.download(
            ticker, start=start.isoformat(), progress=False, auto_adjust=True
        )
        if hist.empty:
            return []
        closes = hist["Close"]
        # yfinance may return multi-index columns -> take the first column
        if hasattr(closes, "columns"):
            closes = closes.iloc[:, 0]
        closes = closes.dropna()
        return [
            (str(dt.date()), float(p)) for dt, p in zip(closes.index, closes.tolist())
        ]

    raw = cached(
        db,
        f"yf:bench-daily:{ticker}:{start.isoformat()}:{date.today().isoformat()}",
        12 * 3600,
        _fetch,
    )
    return to_eur_closes(raw or [], info["currency"], _fx_on_db(db)), info


@router.get("/performance")
def get_performance(
    benchmark: str = Query(
        DEFAULT_BENCHMARK, description="Benchmark ticker for comparison"
    ),
    period: str = Query("all", description="Return window: ytd | 1m | 1y | all"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Total return, money-weighted IRR, and time-weighted return vs a benchmark.

    ``total_return_pct`` / ``money_weighted_irr_pct`` are lifetime figures.
    ``period_return_pct`` is the time-weighted return over the window, from
    daily snapshots — for ``all`` it runs from the first trade, and is null
    when the snapshot history doesn't reach back that far. The benchmark is
    measured in EUR over the same days. See
    ``portf_manager/services/performance.py``.
    """
    fx_on = _fx_on_db(db)
    perf = compute_performance(db, _fx, fx_on)
    cash_flows = perf.pop("cash_flows")
    inception = date.fromisoformat(perf["inception_date"]) if cash_flows else None

    snapshots = db.get_snapshots(limit=100_000)
    realised_by_day = daily_realised_gains(db.get_all_transactions(), fx_on)
    twr_total, twr_annual = lifetime_twr(snapshots, realised_by_day, inception)
    if (period or "all").lower() == "all":
        period_ret = twr_total
        start = inception
    else:
        period_ret = period_return(
            snapshots, perf["current_value_eur"], period, realised=realised_by_day
        )
        start = period_start_date(period)

    benchmark_ret = None
    info = benchmark_info(benchmark)
    if start is not None:
        try:
            closes, info = _benchmark_closes_eur(db, benchmark, start)
            if len(closes) > 1 and closes[0][1] > 0:
                benchmark_ret = round((closes[-1][1] / closes[0][1] - 1) * 100, 2)
        except Exception as e:
            logger.warning(f"Benchmark fetch failed: {e}")

    return {
        **perf,
        "period": period,
        "period_return_pct": period_ret,
        "twr_since_inception_pct": twr_total,
        "annualised_twr_pct": twr_annual,
        "benchmark": benchmark,
        "benchmark_label": info["label"],
        "benchmark_total_return": info["total_return"],
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
def take_snapshot(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Record today's portfolio value/cost snapshot (called by the price cron).

    Cost is the EUR cost basis at transaction-date rates, so it only changes
    when a trade lands — a currency move shows up as a change in value (a
    gain or loss), never as money moving in or out.
    """
    txs = [t for t in db.get_all_transactions() if t.get("transaction_date")]
    assets = {a["id"]: a for a in db.get_all_assets(active_only=False)}

    def price_of(aid):
        row = db.get_latest_price(aid)
        return float(row["price"]) if row else 0.0

    value, cost = portfolio_totals(
        txs,
        to_eur_transactions(txs, _fx_on_db(db)),
        price_of,
        lambda aid: (assets.get(aid) or {}).get("currency") or "EUR",
        _fx,
    )
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


def _yf_symbol(asset: dict) -> str:
    sym = asset.get("symbol", "")
    if asset.get("asset_type") == "crypto":
        yf_ticker, _ = _CRYPTO_YF_OVERRIDES.get(sym, (f"{sym}-EUR", "EUR"))
        return yf_ticker
    # Assets stored under an ISIN carry their Yahoo ticker separately.
    return asset.get("ticker") or sym


def _run_backfill(db, force: bool = False) -> None:
    """Rebuild daily snapshots from transactions, stored and Yahoo prices.

    Prices: the app's own stored price for the day wins (it is what the daily
    cron recorded), then the Yahoo close, then the last earlier price of
    either kind; with none at all the position is valued at cost. Value uses
    the day's FX rate; cost uses each trade's own date (see
    ``performance.portfolio_totals``), the same as the live snapshot.
    """
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
        fx_on = _fx_on_db(db)
        eur_txs = to_eur_transactions(txs, fx_on)

        _BACKFILL.update(message="Fetching historical prices…", total=len(aids))
        hist: dict = {}
        for i, aid in enumerate(aids):
            a = assets[aid] or {}
            merged: dict[str, float] = {}
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
                    merged[idx.date().isoformat()] = float(row["Close"]) / (
                        100.0 if gbx else 1.0
                    )
            except Exception:
                pass
            for row in db.get_price_history(aid, start_date=start_d.isoformat()):
                if row.get("price") is not None:
                    merged[d10(row["price_date"])] = float(row["price"])
            hist[aid] = sorted(merged.items())
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
                value, cost = portfolio_totals(
                    [t for t in txs if d10(t["transaction_date"]) <= dstr],
                    [t for t in eur_txs if d10(t["transaction_date"]) <= dstr],
                    lambda aid: price_asof(aid, dstr),
                    lambda aid: (assets.get(aid) or {}).get("currency") or "EUR",
                    lambda cur: fx_on(cur, dstr),
                )
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

    # Realised capital gains (FIFO, all portfolios, broken down per symbol) +
    # dividend/interest income at transaction-date FX. ONE pass: the legs and
    # the savings base they sum to come from the same computation, so they
    # can't drift apart. See services/analytics_service.py.
    components = current_year_savings_components(db, year=yr)
    realised_gain = components["realised_gain_eur"]
    realised_by_symbol = components["realised_by_symbol"]
    div_this_year = components["dividend_income_eur"]
    interest_this_year = components["interest_income_eur"]
    savings_base = components["savings_base_eur"]
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
        for sym, txns in report.items():
            a = db.get_asset_by_symbol(sym)
            currency = ((a or {}).get("currency") or "EUR").upper()
            for t in txns:
                proceeds_eur, cost_eur = _lot_eur_amounts(db, currency, t)
                realised_gain += proceeds_eur - cost_eur
    except Exception as e:
        logger.warning(f"Tax optimizer realised calc failed: {e}")

    # Income this year (dividends + interest) at transaction-date FX
    all_txns = db.get_all_transactions()
    div_this_year, interest_this_year = _savings_income_eur(db, all_txns, yr)
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
    """Exposure breakdown with funds looked through, plus concentration.

    Defined as a sync handler (not async) so FastAPI runs it in a threadpool:
    the per-holding yfinance lookups for direct holdings are blocking and would
    freeze the event loop if awaited inline.

    The computation lives in ``portf_manager.services.exposure`` so this and
    Portfolio Health cannot drift apart.
    """
    from portf_manager.services.exposure import compute_exposure

    result = compute_exposure(db, fx=_fx)
    # The per-fund list is an input to fund-overlap, not part of this view.
    result.pop("funds", None)
    return result


@router.get("/fund-overlap")
def get_fund_overlap(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Held funds that hold the same thing — same index family, or nested."""
    from portf_manager.services.exposure import compute_exposure, find_fund_overlaps

    result = compute_exposure(db, fx=_fx)
    total = result["total_value_eur"]
    return {
        "groups": find_fund_overlaps(result["funds"], total),
        "total_value_eur": total,
    }


@router.get("/risk")
def get_risk(
    benchmark: str = Query(
        DEFAULT_BENCHMARK, description="Benchmark ticker for beta/alpha"
    ),
    window: str = Query(
        "all", pattern="^(all|1y)$", description="all history, or trailing 1y"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Drawdowns, volatility, Sharpe, Sortino, Calmar, Beta, Alpha from snapshots.

    Computed from flow-adjusted (time-weighted) daily returns, so buys, sells and
    late imports don't count as gains or losses. Sharpe, Sortino and alpha are
    in excess of the average €STR over the window. The benchmark is converted
    to EUR, and beta/alpha come from **weekly** returns: daily returns of a
    calendar-daily portfolio and a trading-day index that closes hours later
    don't line up, which biases a daily beta towards zero. See
    ``portf_manager/services/risk_metrics.py``.
    """
    metrics = compute_portfolio_risk(db, _fx_on_db(db), window=window)
    returns = metrics.pop("returns")
    info = benchmark_info(benchmark)
    metrics.update(
        beta=None,
        alpha_pct=None,
        benchmark=benchmark,
        benchmark_label=info["label"],
        benchmark_total_return=info["total_return"],
        beta_observations=0,
    )
    metrics["benchmark_return_pct"] = None
    metrics["benchmark_annualised_return_pct"] = None
    metrics["snapshots_used"] = len(returns) + 1 if returns else 0
    if not returns:
        return metrics

    try:
        start_d = date.fromisoformat(metrics["start_date"])
        bench_data, info = _benchmark_closes_eur(db, benchmark, start_d)
        metrics.update(
            benchmark_label=info["label"], benchmark_total_return=info["total_return"]
        )
        bench_data = [(d, p) for d, p in bench_data if d <= metrics["end_date"]]

        if len(bench_data) >= 2 and bench_data[0][1] > 0:
            growth = bench_data[-1][1] / bench_data[0][1]
            metrics["benchmark_return_pct"] = round((growth - 1) * 100, 2)
            bench_annual = None
            if metrics["annualised_return_pct"] is not None:
                bench_annual = growth ** (365.25 / metrics["history_days"]) - 1
                metrics["benchmark_annualised_return_pct"] = round(
                    bench_annual * 100, 2
                )

            port_w = weekly_from_daily(returns)
            bench_w = weekly_from_closes(bench_data)
            weeks = sorted(set(port_w) & set(bench_w))
            metrics["beta_observations"] = len(weeks)
            ann = metrics["annualised_return_pct"]
            metrics["beta"], metrics["alpha_pct"] = compute_beta_alpha(
                [port_w[w] for w in weeks],
                [bench_w[w] for w in weeks],
                ann / 100 if ann is not None else None,
                bench_annual,
                risk_free=(metrics.get("risk_free_rate_pct") or 0.0) / 100,
            )
    except Exception as e:
        logger.warning(f"Beta/alpha computation failed: {e}")

    return metrics


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


def _build_tax_report_data(db, yr: int) -> dict:
    """Per-lot realised gains (FIFO) + dividend withholding summary for a year.

    Pure data builder shared by the JSON endpoint and the PDF export below —
    see the "One implementation backs both" convention used elsewhere in this
    module (e.g. ``compute_exposure``) so the two representations can't drift.
    Reuses the FIFO engine in TaxCalculator. Amounts are in each transaction's
    own currency as stored; withholding sums the per-transaction ``tax`` field.
    """
    start = date(yr, 1, 1)
    end = date(yr, 12, 31)

    calc = TaxCalculator(db)
    lots = []
    total_gain_eur = 0.0
    # Build symbol → name and currency lookups once.
    all_assets = db.get_all_assets()
    asset_names: dict[str, str] = {
        a["symbol"]: a.get("name", "") or "" for a in all_assets if a.get("symbol")
    }
    asset_currencies: dict[str, str] = {
        a["symbol"]: (a.get("currency") or "EUR").upper()
        for a in all_assets
        if a.get("symbol")
    }
    try:
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for symbol, txns in report.items():
            currency = asset_currencies.get(symbol, "EUR")
            for t in txns:
                # TaxTransaction uses sell_quantity / sell_amount / purchase_amount
                qty = float(getattr(t, "sell_quantity", 0) or 0)
                proceeds = float(getattr(t, "sell_amount", 0) or 0)
                cost_basis = float(getattr(t, "purchase_amount", 0) or 0)
                gain = float(getattr(t, "gain_loss", 0) or 0)
                # IRPF: proceeds at sell-date FX, cost at purchase-date FX; the
                # FX gain/loss is part of the taxable result.
                proceeds_eur, cost_basis_eur = _lot_eur_amounts(db, currency, t)
                gain_eur = proceeds_eur - cost_basis_eur
                total_gain_eur += gain_eur
                lots.append(
                    {
                        "symbol": symbol,
                        "name": asset_names.get(symbol, ""),
                        "sell_date": str(getattr(t, "sell_date", "")),
                        "purchase_date": str(getattr(t, "purchase_date", "")),
                        "quantity": qty,
                        "currency": currency,
                        "proceeds": round(proceeds, 2),
                        "cost_basis": round(cost_basis, 2),
                        "gain_loss": round(gain, 2),
                        "proceeds_eur": round(proceeds_eur, 2),
                        "cost_basis_eur": round(cost_basis_eur, 2),
                        "gain_loss_eur": round(gain_eur, 2),
                        "holding_days": getattr(t, "holding_period_days", None),
                    }
                )
    except Exception as e:
        logger.warning(f"Tax report failed: {e}")

    lots.sort(key=lambda x: x["sell_date"])

    # Dividend/interest withholding converted to EUR via per-transaction currency.
    dividends_gross_eur, dividend_wh_eur, interest_wh_eur = _year_withholding_eur(
        db, db.get_all_transactions(), start, end
    )

    return {
        "year": yr,
        "realised_lots": lots,
        "realised_gain_total": round(total_gain_eur, 2),
        "lot_count": len(lots),
        "dividends_gross_eur": round(dividends_gross_eur, 2),
        "dividend_withholding_eur": round(dividend_wh_eur, 2),
        "interest_withholding_eur": round(interest_wh_eur, 2),
        "note": (
            "FIFO realised gains converted to EUR at transaction-date FX "
            "(proceeds at sell-date, cost at purchase-date). Withholding is "
            "the tax already paid at source on dividends and interest."
        ),
    }


@router.get("/tax-report")
def get_tax_report(
    year: Optional[int] = Query(None, description="Tax year (default current)"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Per-lot realised gains (FIFO) + dividend withholding summary for a year."""
    yr = year or date.today().year
    return _build_tax_report_data(db, yr)


@router.get("/tax-report/pdf")
def get_tax_report_pdf(
    year: Optional[int] = Query(None, description="Tax year (default current)"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Filing-ready Spanish IRPF tax report as a PDF.

    Same data as ``GET /tax-report`` (so the two can't disagree), plus the
    savings-base estimated tax from ``current_year_savings_components`` /
    ``irpf_savings_tax`` — the same numbers the Tax Estimate tab shows.
    """
    from portf_manager.services.pdf_reports import build_tax_report_pdf

    yr = year or date.today().year
    data = _build_tax_report_data(db, yr)

    try:
        components = current_year_savings_components(db, year=yr)
        estimated_tax = {
            "savings_base_eur": round(components["savings_base_eur"], 2),
            "estimated_tax_eur": irpf_savings_tax(components["savings_base_eur"]),
        }
    except Exception as e:
        logger.warning(f"Tax report PDF: savings-base estimate failed: {e}")
        estimated_tax = None

    pdf_bytes = build_tax_report_pdf(data, estimated_tax)
    filename = f"irpf_tax_report_{yr}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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


# ── On-demand Price Update ───────────────────────────────────────────────────

_price_update_lock = threading.Lock()
_price_update_state: dict = {"running": False, "started_at": None}


@router.post("/trigger-price-update")
def trigger_price_update(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Start a background price update for all auto-priced assets.

    Returns 409 if an update is already in progress.
    """
    from portf_manager.services.price_updater import run_price_update

    with _price_update_lock:
        if _price_update_state["running"]:
            raise HTTPException(
                status_code=409, detail="Price update already in progress"
            )
        _price_update_state["running"] = True
        started_at = datetime.now().isoformat()
        _price_update_state["started_at"] = started_at

    def _run() -> None:
        try:
            run_price_update(db)
        finally:
            with _price_update_lock:
                _price_update_state["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"status": "started", "started_at": started_at}


@router.get("/price-update-status")
def price_update_status(
    api_key_info: dict = Depends(_auth),
):
    """Check whether a background price update is currently running."""
    with _price_update_lock:
        return {
            "running": _price_update_state["running"],
            "started_at": _price_update_state["started_at"],
        }


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

    Returns invested, current value, total gain and return (incl. dividends
    and interest), IRR, and asset count per portfolio, sorted by current
    value descending. Same definitions as ``/performance``.
    """
    fx_on = _fx_on_db(db)
    results = []
    for portfolio in db.get_all_portfolios():
        pid = portfolio["id"]
        txs = db.get_all_transactions(portfolio_id=pid)
        if not txs:
            continue
        perf = compute_performance(db, _fx, fx_on, portfolio_id=pid)
        positions, _ = compute_positions(txs)
        asset_count = sum(1 for pos in positions.values() if pos["quantity"] > 0.001)
        results.append(
            {
                "portfolio_id": pid,
                "name": portfolio["name"],
                "invested_eur": perf["invested_eur"],
                "current_value_eur": perf["current_value_eur"],
                "gain_loss_eur": perf["total_gain_eur"],
                "total_return_pct": perf["total_return_pct"],
                "irr_pct": perf["money_weighted_irr_pct"],
                "asset_count": asset_count,
                "transaction_count": len(txs),
            }
        )

    results.sort(key=lambda x: -x["current_value_eur"])
    return results
