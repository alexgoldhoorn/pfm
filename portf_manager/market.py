"""Shared market-data service: cached Yahoo quotes, FX rates and fundamentals.

Freshness is decided at READ time: values are stored in the kv_cache with a
long expiry (7 days) plus a ``fetched_at`` epoch inside the value. Callers
pass ``max_age`` (seconds) and get the cached value when fresh enough, a live
yfinance fetch otherwise, and a ``stale: true`` value when the live fetch
fails. Spec: docs/superpowers/specs/2026-06-12-market-data-api-design.md.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# Stale entries are kept long enough to serve through a Yahoo outage.
_STORE_TTL = 7 * 24 * 3600


def _cache_get(db, key: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        return db.cache_get(key)
    except Exception as e:
        logger.warning(f"market cache_get failed for {key}: {e}")
        return None


def _cache_set(db, key: str, value: Any) -> None:
    if db is None:
        return
    try:
        db.cache_set(key, value, _STORE_TTL)
    except Exception as e:
        logger.warning(f"market cache_set failed for {key}: {e}")


def _fetch_quote_live(symbol: str) -> Optional[dict]:
    """One yfinance fast_info fetch → quote value dict (GBX-normalised)."""
    try:
        fi = yf.Ticker(symbol).fast_info
        price = float(fi["last_price"])
        currency = fi.get("currency") or "EUR"
        # FastInfo.get() only resolves camelCase keys; subscript handles snake_case.
        try:
            prev = fi["previous_close"]
        except Exception:
            prev = None
        prev = float(prev) if prev else None
        # Yahoo quotes UK listings in pence (GBX); normalise to GBP.
        if currency == "GBp":
            price /= 100
            prev = prev / 100 if prev else None
            currency = "GBP"
        change = round((price - prev) / prev * 100, 2) if prev else None
        return {
            "symbol": symbol,
            "price": round(price, 6),
            "prev_close": round(prev, 6) if prev is not None else None,
            "change_pct": change,
            "currency": currency,
            "name": None,
            "fetched_at": time.time(),
        }
    except Exception as e:
        logger.warning(f"live quote fetch failed for {symbol}: {e}")
        return None


def _quote_from_db(db, symbol: str) -> Optional[dict]:
    """Quote built from a held asset's stored daily prices (the price cron).

    ``get_asset_by_symbol`` also resolves the ticker alias column, so 'NVDA'
    finds an asset stored under its ISIN. Stored closes carry a date, not a
    time — ``fetched_at`` is that date's midnight, which slightly overstates
    age; acceptable for day-granularity freshness.
    """
    if db is None:
        return None
    try:
        asset = db.get_asset_by_symbol(symbol)
        if not asset:
            return None
        since = (date.today() - timedelta(days=14)).isoformat()
        rows = db.get_price_history(asset["id"], start_date=since)
        if not rows:
            return None
        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else None
        price = float(latest["price"])
        prev_price = float(prev["price"]) if prev else None
        fetched_at = datetime.strptime(
            str(latest["price_date"])[:10], "%Y-%m-%d"
        ).timestamp()
        change = (
            round((price - prev_price) / prev_price * 100, 2) if prev_price else None
        )
        return {
            "symbol": symbol,
            "price": price,
            "prev_close": prev_price,
            "change_pct": change,
            "currency": asset.get("currency", "EUR"),
            "name": asset.get("name"),
            "fetched_at": fetched_at,
        }
    except Exception as e:
        logger.warning(f"db quote failed for {symbol}: {e}")
        return None


def get_quote(db, symbol: str, max_age: float = 86400) -> dict:
    """Quote for one Yahoo-format symbol, no older than *max_age* seconds.

    Resolution order: fresh kv_cache entry → fresh stored daily price for a
    held asset → live yfinance fetch → stale cache/DB value (``stale: true``)
    → ``error`` entry when nothing is available.
    """
    sym = symbol.strip().upper()
    now = time.time()
    key = f"mkt:quote:{sym}"

    cached = _cache_get(db, key)
    if cached and now - cached.get("fetched_at", 0) <= max_age:
        return {**cached, "source": "cache", "stale": False}

    db_quote = _quote_from_db(db, sym)
    if db_quote and now - db_quote["fetched_at"] <= max_age:
        return {**db_quote, "source": "db", "stale": False}

    live = _fetch_quote_live(sym)
    if live:
        # Keep a known display name from the held asset or the old cache entry.
        live["name"] = (db_quote or {}).get("name") or (cached or {}).get("name")
        _cache_set(db, key, live)
        return {**live, "source": "live", "stale": False}

    # Live fetch failed — serve the freshest stale value we have.
    if cached and db_quote:
        fallback = (
            cached if cached["fetched_at"] >= db_quote["fetched_at"] else db_quote
        )
    else:
        fallback = cached or db_quote
    if fallback:
        source = "cache" if fallback is cached else "db"
        return {**fallback, "source": source, "stale": True}
    return {
        "symbol": sym,
        "price": None,
        "prev_close": None,
        "change_pct": None,
        "currency": None,
        "name": None,
        "fetched_at": None,
        "source": "error",
        "error": "no data available",
        "stale": True,
    }


def get_quotes(db, symbols: list[str], max_age: float = 86400) -> list[dict]:
    """Quotes for many symbols; per-symbol errors, never raises for a batch."""
    return [get_quote(db, s, max_age=max_age) for s in symbols]


# Last-resort FX rates, used only when there is no cache and no live data.
_FX_FALLBACK = {
    "USD": 0.92,
    "GBP": 1.17,
    "SEK": 0.092,
    "DKK": 0.134,
    "CHF": 1.062,
    "NOK": 0.086,
    "JPY": 0.0059,
}


def get_fx_eur(db, currency: str, max_age: float = 3600) -> tuple[float, bool]:
    """EUR rate for *currency* via the ``{CUR}EUR=X`` ticker → (rate, stale)."""
    cur = currency.strip().upper()
    if cur == "EUR":
        return 1.0, False
    now = time.time()
    key = f"mkt:fx:{cur}"
    cached = _cache_get(db, key)
    if cached and now - cached.get("fetched_at", 0) <= max_age:
        return float(cached["rate"]), False
    try:
        fi = yf.Ticker(f"{cur}EUR=X").fast_info
        rate = fi["last_price"]
        # A zero or None rate is meaningless and treated as a failed fetch.
        if rate:
            _cache_set(db, key, {"rate": float(rate), "fetched_at": now})
            return float(rate), False
    except Exception as e:
        logger.warning(f"FX {cur}->EUR live fetch failed: {e}")
    if cached:
        return float(cached["rate"]), True
    return _FX_FALLBACK.get(cur, 1.0), True


def _rate_at_or_before(
    rates: dict, on_date: date, max_lookback: int = 7
) -> Optional[float]:
    """Close for *on_date* or the nearest prior day within *max_lookback* days."""
    for i in range(max_lookback + 1):
        value = rates.get(str(on_date - timedelta(days=i)))
        if value:
            return float(value)
    return None


def get_fx_eur_on(db, currency: str, on_date: Optional[date]) -> tuple[float, bool]:
    """EUR rate for *currency* on *on_date* (nearest prior trading day).

    Daily closes for the ``{CUR}EUR=X`` ticker are fetched one calendar year
    at a time (single yfinance call) and cached under
    ``mkt:fxhist:{CUR}:{year}``. Historical closes never change, so a cache
    hit is always fresh. Falls back to the current rate with ``stale=True``
    when history is unavailable; ``on_date`` of None/today/future delegates
    to :func:`get_fx_eur` directly.

    Args:
        db: Database handle for the kv_cache (may be None).
        currency: ISO currency code, e.g. "USD".
        on_date: The historical date to price, or None for "now".

    Returns:
        (rate, stale) — stale=True means the rate is NOT transaction-date
        accurate (current-rate fallback was used).
    """
    cur = currency.strip().upper()
    if cur == "EUR":
        return 1.0, False
    if on_date is None or on_date >= date.today():
        return get_fx_eur(db, cur)
    key = f"mkt:fxhist:{cur}:{on_date.year}"
    cached = _cache_get(db, key)
    rates = (cached or {}).get("rates", {})
    rate = _rate_at_or_before(rates, on_date)
    if rate is not None:
        return rate, False
    try:
        # Start in late December of the prior year so early-January dates can
        # look back to the last trading days of the previous year.
        hist = yf.Ticker(f"{cur}EUR=X").history(
            start=f"{on_date.year - 1}-12-20", end=f"{on_date.year + 1}-01-01"
        )
        fetched = {
            str(idx.date()): round(float(close), 6)
            for idx, close in hist["Close"].items()
            if close and close > 0
        }
        if fetched:
            rates.update(fetched)
            _cache_set(db, key, {"rates": rates, "fetched_at": time.time()})
            rate = _rate_at_or_before(rates, on_date)
            if rate is not None:
                return rate, False
    except Exception as e:
        logger.warning(f"FX history {cur}->EUR fetch failed for {on_date}: {e}")
    return get_fx_eur(db, cur)[0], True


def get_fundamentals(db, symbol: str, max_age: float = 21600) -> dict:
    """Key fundamentals for *symbol*, no older than *max_age* seconds.

    Same read-time pattern as quotes, under ``mkt:fund:{SYM}``. Shares the
    raw yfinance fetch with the research workbench (which keeps its own
    write-TTL cache entry) — see the design spec.
    """
    # Imported here to avoid importing the research service (and its
    # transitive deps) for callers that only need quotes/FX.
    from .services.research import _fetch_fundamentals_live

    sym = symbol.strip().upper()
    now = time.time()
    key = f"mkt:fund:{sym}"
    cached = _cache_get(db, key)
    if cached and now - cached.get("fetched_at", 0) <= max_age:
        return {**cached, "source": "cache", "stale": False}
    data = _fetch_fundamentals_live(sym)
    # A genuine hit has more than just the symbol echo.
    if len(data) > 1:
        data["fetched_at"] = now
        _cache_set(db, key, data)
        return {**data, "source": "live", "stale": False}
    if cached:
        return {**cached, "source": "cache", "stale": True}
    return {"symbol": sym, "error": "no data available", "stale": True}


# What a Yahoo Finance quote page shows, grouped the way the page is. Each
# entry maps our key to the yfinance ``info`` field it is read from.
_PROFILE_GROUPS: dict[str, dict[str, str]] = {
    "profile": {
        "sector": "sector",
        "industry": "industry",
        "country": "country",
        "website": "website",
        "employees": "fullTimeEmployees",
        "summary": "longBusinessSummary",
    },
    "price": {
        "price": "currentPrice",
        "regular_market_price": "regularMarketPrice",
        "previous_close": "previousClose",
        "open": "open",
        "day_low": "dayLow",
        "day_high": "dayHigh",
        "week52_low": "fiftyTwoWeekLow",
        "week52_high": "fiftyTwoWeekHigh",
        "week52_change": "52WeekChange",
        "avg_50d": "fiftyDayAverage",
        "avg_200d": "twoHundredDayAverage",
        "volume": "volume",
        "avg_volume": "averageVolume",
        "market_cap": "marketCap",
        "beta": "beta",
    },
    "valuation": {
        "trailing_pe": "trailingPE",
        "forward_pe": "forwardPE",
        "peg_ratio": "trailingPegRatio",
        "price_to_book": "priceToBook",
        "price_to_sales": "priceToSalesTrailing12Months",
        "enterprise_value": "enterpriseValue",
        "ev_to_revenue": "enterpriseToRevenue",
        "ev_to_ebitda": "enterpriseToEbitda",
    },
    "financials": {
        "total_revenue": "totalRevenue",
        "revenue_growth": "revenueGrowth",
        "earnings_growth": "earningsGrowth",
        "gross_margin": "grossMargins",
        "operating_margin": "operatingMargins",
        "profit_margin": "profitMargins",
        "ebitda": "ebitda",
        "trailing_eps": "trailingEps",
        "forward_eps": "forwardEps",
        "return_on_equity": "returnOnEquity",
        "return_on_assets": "returnOnAssets",
        "total_cash": "totalCash",
        "total_debt": "totalDebt",
        "debt_to_equity": "debtToEquity",
        "current_ratio": "currentRatio",
        "free_cashflow": "freeCashflow",
        "operating_cashflow": "operatingCashflow",
        "shares_outstanding": "sharesOutstanding",
        "next_earnings_date": "earningsTimestamp",
    },
    "dividends": {
        "dividend_rate": "dividendRate",
        "dividend_yield": "dividendYield",
        "five_year_avg_yield": "fiveYearAvgDividendYield",
        "payout_ratio": "payoutRatio",
        "ex_dividend_date": "exDividendDate",
    },
    "analysts": {
        "recommendation": "recommendationKey",
        "recommendation_mean": "recommendationMean",
        "analyst_count": "numberOfAnalystOpinions",
        "target_mean": "targetMeanPrice",
        "target_median": "targetMedianPrice",
        "target_low": "targetLowPrice",
        "target_high": "targetHighPrice",
    },
    "fund": {
        "family": "fundFamily",
        "category": "category",
        "legal_type": "legalType",
        "total_assets": "totalAssets",
        "nav_price": "navPrice",
        "expense_ratio": "netExpenseRatio",
        "yield": "yield",
        "ytd_return": "ytdReturn",
        "three_year_avg_return": "threeYearAverageReturn",
        "five_year_avg_return": "fiveYearAverageReturn",
        "beta_3y": "beta3Year",
    },
}

# Per-share figures Yahoo reports in pence for a GBp listing (see
# research._GBP_PENCE_FIELDS for why totals such as market cap are left alone).
_PROFILE_PENCE_KEYS = {
    "price",
    "regular_market_price",
    "previous_close",
    "open",
    "day_low",
    "day_high",
    "week52_low",
    "week52_high",
    "avg_50d",
    "avg_200d",
    "target_mean",
    "target_median",
    "target_low",
    "target_high",
    "nav_price",
}
_PROFILE_DATE_KEYS = {"next_earnings_date", "ex_dividend_date"}
_PROFILE_SUMMARY_CHARS = 800


def _fetch_info_live(symbol: str) -> dict:
    """Live yfinance ``info`` dict for *symbol*. Patched in tests."""
    return yf.Ticker(symbol).info or {}


def _profile_from_info(symbol: str, info: dict) -> dict:
    """Shape a raw yfinance ``info`` dict into the grouped market profile."""
    pence = (info.get("currency") or "") == "GBp"
    out: dict[str, Any] = {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName"),
        "quote_type": info.get("quoteType"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "currency": "GBP" if pence else info.get("currency"),
    }
    for group, fields in _PROFILE_GROUPS.items():
        block: dict[str, Any] = {}
        for key, source in fields.items():
            value = info.get(source)
            if value is None or value == "":
                continue
            is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if pence and key in _PROFILE_PENCE_KEYS and is_number:
                value = value / 100.0
            elif key in _PROFILE_DATE_KEYS and is_number:
                value = (
                    datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
                )
            elif key == "summary":
                text = str(value)
                value = (
                    text
                    if len(text) <= _PROFILE_SUMMARY_CHARS
                    else text[:_PROFILE_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
                )
            block[key] = value
        if block:
            out[group] = block
    if pence:
        out["note"] = (
            "Yahoo quotes this listing in pence; prices and targets are converted "
            "to GBP. dividend_rate is as Yahoo reports it and may be in pence."
        )
    return out


def get_market_profile(db, symbol: str, max_age: float = 21600) -> dict:
    """Everything a Yahoo Finance quote page shows for *symbol*, grouped.

    Company or fund profile, price and trading range, valuation, financials,
    dividends, analyst targets and fund facts — only the fields Yahoo has for
    that instrument. Same read-time freshness pattern as quotes, under
    ``mkt:profile:{SYM}``: a value no older than *max_age* comes from the
    cache, otherwise one live fetch, and a failed fetch serves the cached
    value flagged ``stale`` or an ``error`` entry — never an empty profile.
    """
    sym = symbol.strip().upper()
    now = time.time()
    key = f"mkt:profile:{sym}"
    cached = _cache_get(db, key)
    if cached and now - cached.get("fetched_at", 0) <= max_age:
        return {**cached, "source": "cache", "stale": False}
    error = "no data available"
    try:
        data = _profile_from_info(sym, _fetch_info_live(sym))
    except Exception as e:
        logger.warning(f"market profile fetch failed for {sym}: {e}")
        data, error = {}, str(e)
    # A genuine hit has at least one data group, not just the identity keys.
    if any(group in data for group in _PROFILE_GROUPS):
        data["fetched_at"] = now
        _cache_set(db, key, data)
        return {**data, "source": "live", "stale": False}
    if cached:
        return {**cached, "source": "cache", "stale": True}
    return {"symbol": sym, "error": error, "source": "error", "stale": True}


# yfinance reports fund sector weights under lowercase compact keys; direct
# stocks report Title Case names. Both feed one breakdown, so they must agree.
_YF_SECTOR_NAMES: dict[str, str] = {
    "basic_materials": "Basic Materials",
    "communication_services": "Communication Services",
    "consumer_cyclical": "Consumer Cyclical",
    "consumer_defensive": "Consumer Defensive",
    "energy": "Energy",
    "financial_services": "Financial Services",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "realestate": "Real Estate",
    "technology": "Technology",
    "utilities": "Utilities",
}

_YF_ASSET_CLASSES: dict[str, str] = {
    "stockPosition": "equity",
    "bondPosition": "bond",
    "cashPosition": "cash",
    "preferredPosition": "other",
    "convertiblePosition": "other",
    "otherPosition": "other",
}


def _fetch_funds_data(symbol: str):
    """Live yfinance ``funds_data`` accessor. Patched in tests."""
    return yf.Ticker(symbol).funds_data


def get_fund_composition(db, symbol: str, max_age: float = 7 * 86400) -> dict:
    """Sector weights and the equity/bond/cash split for a fund.

    yfinance has no country or region data for funds, so geography does not
    come from here — it comes from the benchmark index the fund tracks. A
    failed fetch returns empty maps and ``stale``, never zeros, so the caller
    can report the gap instead of presenting it as a real composition.
    """
    sym = symbol.strip().upper()
    key = f"mkt:fundcomp:{sym}"
    try:
        hit = db.cache_get(key)
    except Exception:
        hit = None
    if hit:
        return {**hit, "source": "cache", "stale": False}

    try:
        fd = _fetch_funds_data(sym)
        raw_sectors = fd.sector_weightings or {}
        raw_classes = fd.asset_classes or {}
    except Exception as e:
        logger.warning(f"Could not fetch fund composition for {sym}: {e}")
        return {
            "symbol": sym,
            "sectors": {},
            "asset_class": {},
            "error": str(e),
            "stale": True,
        }

    sectors = {
        _YF_SECTOR_NAMES.get(k, k.replace("_", " ").title()): round(float(v), 4)
        for k, v in raw_sectors.items()
        if v
    }
    asset_class: dict[str, float] = {}
    for raw_key, value in raw_classes.items():
        if not value:
            continue
        mapped = _YF_ASSET_CLASSES.get(raw_key, "other")
        asset_class[mapped] = round(asset_class.get(mapped, 0.0) + float(value), 4)

    data = {"symbol": sym, "sectors": sectors, "asset_class": asset_class}
    if sectors or asset_class:
        try:
            db.cache_set(key, data, max_age)
        except Exception as e:
            logger.warning(f"fund composition cache_set failed for {sym}: {e}")
    return {**data, "source": "live", "stale": False}
