"""Shared market-data service: cached Yahoo quotes, FX rates and fundamentals.

Freshness is decided at READ time: values are stored in the kv_cache with a
long expiry (7 days) plus a ``fetched_at`` epoch inside the value. Callers
pass ``max_age`` (seconds) and get the cached value when fresh enough, a live
yfinance fetch otherwise, and a ``stale: true`` value when the live fetch
fails. Spec: docs/superpowers/specs/2026-06-12-market-data-api-design.md.
"""

import logging
import time
from datetime import date, datetime, timedelta
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
        prev = fi.get("previous_close")
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

    ``get_asset_by_symbol`` also resolves the v18 ticker alias, so 'NVDA'
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
    return {"symbol": sym, "price": None, "error": "no data available", "stale": True}


def get_quotes(db, symbols, max_age: float = 86400) -> list:
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


def get_fx_eur(db, currency: str, max_age: float = 3600) -> tuple:
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
        if rate:
            _cache_set(db, key, {"rate": float(rate), "fetched_at": now})
            return float(rate), False
    except Exception as e:
        logger.warning(f"FX {cur}->EUR live fetch failed: {e}")
    if cached:
        return float(cached["rate"]), True
    return _FX_FALLBACK.get(cur, 1.0), True


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
