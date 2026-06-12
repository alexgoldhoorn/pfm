# Shared Market-Data Service & API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One shared, freshness-parameterised Yahoo Finance cache in pfm (`/api/v1/market`), consumed by the web/server internals, the pfm MCP, and the cron monitors — no independent Yahoo fetching anywhere on this machine.

**Architecture:** New `portf_manager/market.py` service implements read-time freshness over the existing `kv_cache` (values stored with a 7-day expiry + `fetched_at` epoch inside; callers pass `max_age`). A new key-auth router `portf_server/routers/market.py` exposes batch quotes, FX and fundamentals. Existing ad-hoc fetchers (FX in 3 routers, research fallback, watchlist) delegate to the service; `stock-monitor.py` and a new MCP `quote` tool consume the HTTP API.

**Tech Stack:** Python 3.13, FastAPI, yfinance, SQLite kv_cache (schema v14+, no migration needed), pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-market-data-api-design.md`

**Project rules that apply everywhere:**
- Run tooling as `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run …` (the `.venv` is root-owned).
- black formatting (line 88); comments on the line **before** the code; type hints; Google docstrings.
- Blocking yfinance endpoints are plain `def` (never `async def`).
- Commit messages: conventional commits + `Co-Authored-By: Oz <oz-agent@warp.dev>`.
- Pre-commit runs black/flake8/autoflake automatically.

---

### Task 1: `portf_manager/market.py` — quote core (cache → live → stale)

**Files:**
- Create: `portf_manager/market.py`
- Create: `tests/unit/test_market_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_market_service.py`:

```python
"""Tests for portf_manager.market — shared market-data service."""

import time

import pytest

from portf_manager import market
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def _patch_ticker(monkeypatch, fast_info=None, raise_exc=False):
    """Replace market.yf.Ticker with a fake returning *fast_info* (a dict)."""

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def fast_info(self):
            if raise_exc:
                raise RuntimeError("yahoo down")
            return fast_info

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)


class TestGetQuote:
    def test_live_fetch_stores_and_returns(self, db, monkeypatch):
        _patch_ticker(
            monkeypatch,
            {"last_price": 100.0, "previous_close": 98.0, "currency": "USD"},
        )
        q = market.get_quote(db, "NVDA", max_age=60)
        assert q["price"] == 100.0
        assert q["prev_close"] == 98.0
        assert q["change_pct"] == pytest.approx(2.04, abs=0.01)
        assert q["currency"] == "USD"
        assert q["source"] == "live"
        assert q["stale"] is False
        # Second call within max_age must not hit yfinance at all.
        _patch_ticker(monkeypatch, raise_exc=True)
        q2 = market.get_quote(db, "NVDA", max_age=3600)
        assert q2["price"] == 100.0
        assert q2["source"] == "cache"
        assert q2["stale"] is False

    def test_expired_cache_refetches(self, db, monkeypatch):
        db.cache_set(
            "mkt:quote:NVDA",
            {
                "symbol": "NVDA",
                "price": 90.0,
                "prev_close": 89.0,
                "change_pct": 1.12,
                "currency": "USD",
                "name": None,
                "fetched_at": time.time() - 90000,
            },
            7 * 24 * 3600,
        )
        _patch_ticker(
            monkeypatch,
            {"last_price": 100.0, "previous_close": 98.0, "currency": "USD"},
        )
        q = market.get_quote(db, "NVDA", max_age=86400)
        assert q["price"] == 100.0
        assert q["source"] == "live"

    def test_stale_served_on_fetch_failure(self, db, monkeypatch):
        db.cache_set(
            "mkt:quote:NVDA",
            {
                "symbol": "NVDA",
                "price": 90.0,
                "prev_close": 89.0,
                "change_pct": 1.12,
                "currency": "USD",
                "name": None,
                "fetched_at": time.time() - 90000,
            },
            7 * 24 * 3600,
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "NVDA", max_age=86400)
        assert q["price"] == 90.0
        assert q["stale"] is True
        assert q["source"] == "cache"

    def test_error_when_nothing_available(self, db, monkeypatch):
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "NOPE", max_age=60)
        assert q.get("error")
        assert q["price"] is None

    def test_gbx_normalised_to_gbp(self, db, monkeypatch):
        _patch_ticker(
            monkeypatch,
            {"last_price": 4500.0, "previous_close": 4400.0, "currency": "GBp"},
        )
        q = market.get_quote(db, "GAW.L", max_age=60)
        assert q["price"] == 45.0
        assert q["prev_close"] == 44.0
        assert q["currency"] == "GBP"

    def test_none_db_degrades_to_live(self, monkeypatch):
        _patch_ticker(
            monkeypatch,
            {"last_price": 10.0, "previous_close": 10.0, "currency": "EUR"},
        )
        q = market.get_quote(None, "BTC-EUR", max_age=60)
        assert q["price"] == 10.0
        assert q["source"] == "live"


class TestGetQuotes:
    def test_batch_partial_failure(self, db, monkeypatch):
        db.cache_set(
            "mkt:quote:GOOD",
            {
                "symbol": "GOOD",
                "price": 5.0,
                "prev_close": 5.0,
                "change_pct": 0.0,
                "currency": "EUR",
                "name": None,
                "fetched_at": time.time(),
            },
            7 * 24 * 3600,
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        out = market.get_quotes(db, ["GOOD", "BAD"], max_age=3600)
        assert out[0]["price"] == 5.0
        assert out[1].get("error")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError: cannot import name 'market'`.

- [ ] **Step 3: Implement `portf_manager/market.py`**

```python
"""Shared market-data service: cached Yahoo quotes, FX rates and fundamentals.

Freshness is decided at READ time: values are stored in the kv_cache with a
long expiry (7 days) plus a ``fetched_at`` epoch inside the value. Callers
pass ``max_age`` (seconds) and get the cached value when fresh enough, a live
yfinance fetch otherwise, and a ``stale: true`` value when the live fetch
fails. Spec: docs/superpowers/specs/2026-06-12-market-data-api-design.md.
"""

import logging
import time
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
        fallback = cached if cached["fetched_at"] >= db_quote["fetched_at"] else db_quote
    else:
        fallback = cached or db_quote
    if fallback:
        source = "cache" if fallback is cached else "db"
        return {**fallback, "source": source, "stale": True}
    return {"symbol": sym, "price": None, "error": "no data available", "stale": True}


def get_quotes(db, symbols, max_age: float = 86400) -> list:
    """Quotes for many symbols; per-symbol errors, never raises for a batch."""
    return [get_quote(db, s, max_age=max_age) for s in symbols]


def _quote_from_db(db, symbol: str) -> Optional[dict]:
    """Implemented in Task 2 — returns None until then."""
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py -v`
Expected: all `TestGetQuote`/`TestGetQuotes` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/market.py tests/unit/test_market_service.py
git commit -m "feat: shared market-data service — read-time-freshness quote cache

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: DB-source path for held assets

Held assets get a daily close from the price cron — a default 1-day quote
request for them must never hit Yahoo.

**Files:**
- Modify: `portf_manager/market.py` (replace the `_quote_from_db` stub)
- Modify: `tests/unit/test_market_service.py` (append class)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_market_service.py`:

```python
from datetime import date, datetime, timedelta


class TestQuoteFromDb:
    def _seed_asset_with_prices(self, db):
        db.create_asset(
            symbol="ASML.AS", name="ASML Holding", asset_type="stock",
            currency="EUR",
        )
        db.insert_price_record(
            symbol="ASML.AS", price=600.0, fetched_ts=datetime.now(),
            price_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        db.insert_price_record(
            symbol="ASML.AS", price=610.0, fetched_ts=datetime.now(),
            price_date=date.today().isoformat(),
        )

    def test_held_asset_served_from_db(self, db, monkeypatch):
        self._seed_asset_with_prices(db)
        # Yahoo unreachable: the DB path must satisfy a 2-day max_age alone.
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "ASML.AS", max_age=2 * 86400)
        assert q["price"] == 610.0
        assert q["prev_close"] == 600.0
        assert q["change_pct"] == pytest.approx(1.67, abs=0.01)
        assert q["source"] == "db"
        assert q["stale"] is False
        assert q["name"] == "ASML Holding"

    def test_stale_db_price_flagged_when_live_fails(self, db, monkeypatch):
        db.create_asset(
            symbol="OLD.AS", name="Old Co", asset_type="stock", currency="EUR"
        )
        db.insert_price_record(
            symbol="OLD.AS", price=10.0, fetched_ts=datetime.now(),
            price_date=(date.today() - timedelta(days=5)).isoformat(),
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        q = market.get_quote(db, "OLD.AS", max_age=86400)
        assert q["price"] == 10.0
        assert q["stale"] is True
        assert q["source"] == "db"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py::TestQuoteFromDb -v`
Expected: FAIL — `_quote_from_db` stub returns None, so quotes come back as errors.

- [ ] **Step 3: Replace the `_quote_from_db` stub**

In `portf_manager/market.py`, replace the stub with (and add
`from datetime import date, datetime, timedelta` to the imports):

```python
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
            round((price - prev_price) / prev_price * 100, 2)
            if prev_price
            else None
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
```

- [ ] **Step 4: Run the whole service test file**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py -v`
Expected: all PASS (Task 1 tests must still pass).

- [ ] **Step 5: Commit**

```bash
git add portf_manager/market.py tests/unit/test_market_service.py
git commit -m "feat: serve held-asset quotes from stored daily prices

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: FX rates — `get_fx_eur`

**Files:**
- Modify: `portf_manager/market.py` (append)
- Modify: `tests/unit/test_market_service.py` (append class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_market_service.py`:

```python
class TestGetFxEur:
    def test_eur_short_circuit(self, db):
        assert market.get_fx_eur(db, "EUR") == (1.0, False)

    def test_live_fetch_and_cache(self, db, monkeypatch):
        _patch_ticker(monkeypatch, {"last_price": 0.93})
        rate, stale = market.get_fx_eur(db, "USD", max_age=60)
        assert rate == 0.93
        assert stale is False
        # Fresh cache: no fetch on the second call.
        _patch_ticker(monkeypatch, raise_exc=True)
        rate2, stale2 = market.get_fx_eur(db, "USD", max_age=3600)
        assert rate2 == 0.93
        assert stale2 is False

    def test_stale_cache_on_failure(self, db, monkeypatch):
        db.cache_set(
            "mkt:fx:USD",
            {"rate": 0.91, "fetched_at": time.time() - 90000},
            7 * 24 * 3600,
        )
        _patch_ticker(monkeypatch, raise_exc=True)
        rate, stale = market.get_fx_eur(db, "USD", max_age=3600)
        assert rate == 0.91
        assert stale is True

    def test_fallback_default_when_nothing(self, db, monkeypatch):
        _patch_ticker(monkeypatch, raise_exc=True)
        rate, stale = market.get_fx_eur(db, "USD", max_age=3600)
        assert rate == pytest.approx(0.92)
        assert stale is True
```

Note: the FX implementation below reads `fi["last_price"]` (subscript,
consistent with the quote path), so the existing `_patch_ticker` dict fake
works unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py::TestGetFxEur -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_fx_eur'`.

- [ ] **Step 3: Implement `get_fx_eur`**

Append to `portf_manager/market.py`:

```python
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
```

- [ ] **Step 4: Run the whole service test file**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/market.py tests/unit/test_market_service.py
git commit -m "feat: shared EUR FX rates with read-time freshness + fallbacks

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Fundamentals — extract raw fetch + `get_fundamentals`

**Files:**
- Modify: `portf_manager/services/research.py:67-93` (extract `_fetch_fundamentals_live`)
- Modify: `portf_manager/market.py` (append `get_fundamentals`)
- Modify: `tests/unit/test_market_service.py` (append class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_market_service.py`:

```python
class TestGetFundamentals:
    def test_live_then_cached(self, db, monkeypatch):
        calls = []

        def fake_live(symbol):
            calls.append(symbol)
            return {"symbol": symbol, "trailingPE": 30.0, "marketCap": 1e12}

        monkeypatch.setattr(
            "portf_manager.services.research._fetch_fundamentals_live", fake_live
        )
        f = market.get_fundamentals(db, "NVDA", max_age=3600)
        assert f["trailingPE"] == 30.0
        assert f["source"] == "live"
        f2 = market.get_fundamentals(db, "NVDA", max_age=3600)
        assert f2["source"] == "cache"
        assert calls == ["NVDA"]

    def test_miss_not_cached_then_error(self, db, monkeypatch):
        monkeypatch.setattr(
            "portf_manager.services.research._fetch_fundamentals_live",
            lambda s: {"symbol": s},
        )
        f = market.get_fundamentals(db, "NOPE", max_age=3600)
        assert f.get("error")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py::TestGetFundamentals -v`
Expected: FAIL — `_fetch_fundamentals_live` / `get_fundamentals` don't exist yet.

- [ ] **Step 3: Extract the raw fetch in `services/research.py`**

In `portf_manager/services/research.py`, split `fetch_fundamentals` (currently
lines 67-93) so the yfinance call lives in its own function. The existing
write-TTL caching for the research workbench stays exactly as it was:

```python
def _fetch_fundamentals_live(symbol: str) -> dict[str, Any]:
    """Uncached yfinance fundamentals fetch (shared with portf_manager.market)."""
    try:
        info = yf.Ticker(symbol).info
        data = {k: info.get(k) for k in _FUNDAMENTAL_FIELDS if info.get(k) is not None}
        data["symbol"] = symbol
        return data
    except Exception as e:
        logger.warning(f"Could not fetch fundamentals for {symbol}: {e}")
        return {"symbol": symbol}


def fetch_fundamentals(symbol: str, db=None) -> dict[str, Any]:
    """Pull key fundamentals from yfinance for *symbol*.

    When *db* is supplied the result is cached for ~6h (fundamentals change
    roughly quarterly), so repeated research lookups don't re-hit yfinance.
    """
    if db is not None:
        try:
            hit = db.cache_get(f"yf:fund:{symbol}")
            if hit is not None:
                return hit
        except Exception as e:
            logger.warning(f"fundamentals cache_get failed for {symbol}: {e}")
    data = _fetch_fundamentals_live(symbol)
    # Only cache a genuine hit (more than just the symbol key).
    if db is not None and len(data) > 1:
        try:
            db.cache_set(f"yf:fund:{symbol}", data, 6 * 3600)
        except Exception as e:
            logger.warning(f"fundamentals cache_set failed for {symbol}: {e}")
    return data
```

- [ ] **Step 4: Implement `get_fundamentals` in `market.py`**

Append to `portf_manager/market.py`:

```python
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
```

- [ ] **Step 5: Run service tests + the research tests that cover fetch_fundamentals**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py tests/unit/test_research_valuation.py tests/unit/test_rebalance_research.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add portf_manager/market.py portf_manager/services/research.py tests/unit/test_market_service.py
git commit -m "feat: market.get_fundamentals with read-time freshness; extract raw fetch

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: `/api/v1/market` router + registration

**Files:**
- Create: `portf_server/routers/market.py`
- Modify: `portf_server/app.py` (`from .routers import (...)` block at line 27, and the `include_router` block — add after the `networth` include, ~line 356)
- Create: `tests/unit/test_market_router.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_market_router.py`. It uses the existing
`async_test_client` / `auth_headers` fixtures (same pattern as
`tests/unit/test_watchlist_goals_risk.py`) and monkeypatches the service so
no live Yahoo calls happen:

```python
"""Tests for the /api/v1/market router."""

import pytest
from httpx import AsyncClient
from fastapi import status

from portf_manager import market


def _fake_quote(symbol, price=100.0):
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": 99.0,
        "change_pct": 1.01,
        "currency": "USD",
        "name": None,
        "fetched_at": 0,
        "source": "cache",
        "stale": False,
    }


class TestMarketQuotes:
    @pytest.mark.asyncio
    async def test_batch_quotes(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market,
            "get_quotes",
            lambda db, syms, max_age: [_fake_quote(s) for s in syms],
        )
        resp = await async_test_client.get(
            "/api/v1/market/quotes?symbols=NVDA,ASML.AS&max_age=900",
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        quotes = resp.json()["quotes"]
        assert [q["symbol"] for q in quotes] == ["NVDA", "ASML.AS"]

    @pytest.mark.asyncio
    async def test_empty_symbols_rejected(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/market/quotes?symbols=,,", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_too_many_symbols_rejected(
        self, async_test_client: AsyncClient, auth_headers
    ):
        syms = ",".join(f"S{i}" for i in range(51))
        resp = await async_test_client.get(
            f"/api/v1/market/quotes?symbols={syms}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_single_quote(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market, "get_quote", lambda db, s, max_age: _fake_quote(s)
        )
        resp = await async_test_client.get(
            "/api/v1/market/quote/NVDA", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["symbol"] == "NVDA"

    @pytest.mark.asyncio
    async def test_requires_auth(self, async_test_client: AsyncClient):
        resp = await async_test_client.get("/api/v1/market/quotes?symbols=NVDA")
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class TestMarketFx:
    @pytest.mark.asyncio
    async def test_fx_rates(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market, "get_fx_eur", lambda db, cur, max_age: (0.93, False)
        )
        resp = await async_test_client.get(
            "/api/v1/market/fx?currencies=USD,GBP", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        rates = resp.json()["rates"]
        assert rates["USD"] == {"rate": 0.93, "stale": False}
        assert rates["GBP"] == {"rate": 0.93, "stale": False}


class TestMarketFundamentals:
    @pytest.mark.asyncio
    async def test_fundamentals(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market,
            "get_fundamentals",
            lambda db, s, max_age: {"symbol": s, "trailingPE": 30.0,
                                    "source": "cache", "stale": False},
        )
        resp = await async_test_client.get(
            "/api/v1/market/fundamentals/NVDA", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["trailingPE"] == 30.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_router.py -v`
Expected: FAIL — 404s (router not registered).

- [ ] **Step 3: Implement `portf_server/routers/market.py`**

```python
"""
Market Data Router — shared, cached Yahoo Finance data (quotes, FX,
fundamentals). Contains NO portfolio data; key-auth like the rest of the API.

All handlers are plain ``def`` (not ``async``): the underlying service may do
blocking yfinance I/O, which must run in FastAPI's threadpool.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from portf_manager import market

from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)

# Floor for max_age so a misconfigured client can't hammer Yahoo.
_MIN_MAX_AGE = 60
_MAX_BATCH = 50


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


@router.get("/quotes")
def batch_quotes(
    symbols: str = Query(..., description="Comma-separated Yahoo-format symbols"),
    max_age: int = Query(86400, ge=0, description="Max data age in seconds"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Batch quotes; per-symbol errors inside the response, never a batch 500."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="No symbols supplied")
    if len(syms) > _MAX_BATCH:
        raise HTTPException(
            status_code=400, detail=f"Too many symbols (max {_MAX_BATCH})"
        )
    return {"quotes": market.get_quotes(db, syms, max_age=max(max_age, _MIN_MAX_AGE))}


@router.get("/quote/{symbol}")
def single_quote(
    symbol: str,
    max_age: int = Query(86400, ge=0),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Quote for one Yahoo-format symbol (e.g. NVDA, ASML.AS, BTC-EUR)."""
    return market.get_quote(db, symbol, max_age=max(max_age, _MIN_MAX_AGE))


@router.get("/fx")
def fx_rates(
    currencies: str = Query(..., description="Comma-separated ISO currency codes"),
    max_age: int = Query(3600, ge=0),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """EUR conversion rates for the given currencies."""
    curs = [c.strip().upper() for c in currencies.split(",") if c.strip()]
    if not curs:
        raise HTTPException(status_code=400, detail="No currencies supplied")
    rates = {}
    for cur in curs:
        rate, stale = market.get_fx_eur(db, cur, max_age=max(max_age, _MIN_MAX_AGE))
        rates[cur] = {"rate": rate, "stale": stale}
    return {"rates": rates}


@router.get("/fundamentals/{symbol}")
def fundamentals(
    symbol: str,
    max_age: int = Query(21600, ge=0),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Key yfinance fundamentals (PE, market cap, dividend yield, 52w range…)."""
    return market.get_fundamentals(db, symbol, max_age=max(max_age, _MIN_MAX_AGE))
```

- [ ] **Step 4: Register the router in `portf_server/app.py`**

Add `market` to the `from .routers import (...)` list (it is alphabetical-ish;
place it near `llm`/`networth`), then after the `networth` include block add:

```python
app.include_router(
    market.router,
    prefix="/api/v1/market",
    tags=["Market Data"],
    dependencies=_PROTECTED,
)
```

(Copy the exact `dependencies=` value used by the `watchlist` include in the
same file — `_PROTECTED` is the existing module-level constant there.)

- [ ] **Step 5: Run router tests + auth-gating tests**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_router.py tests/unit/test_auth_gating.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/market.py portf_server/app.py tests/unit/test_market_router.py
git commit -m "feat: /api/v1/market endpoints — batch quotes, FX, fundamentals

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 6: Migrate the three router FX implementations

**Files:**
- Modify: `portf_server/routers/portfolios.py:52-88` (`_get_fx_rate` body)
- Modify: `portf_server/routers/public.py:20-37` (`_FX`/`_FX_TS`/`_fx`)
- Modify: `portf_server/routers/rebalance.py:91-103` (`to_eur` inner fetch)
- Modify: `tests/unit/test_market_service.py` (append delegation test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_market_service.py`:

```python
class TestFxDelegation:
    def test_portfolios_get_fx_rate_uses_market(self, db, monkeypatch):
        from portf_server.routers import portfolios

        # Point the router's shared DB at our test DB and clear its L1 cache.
        monkeypatch.setattr(portfolios, "_SHARED_DB", db)
        portfolios._FX_CACHE.clear()
        portfolios._FX_CACHE_TS.clear()
        monkeypatch.setattr(
            market, "get_fx_eur", lambda d, cur, max_age=3600: (0.5, False)
        )
        assert portfolios._get_fx_rate("USD") == 0.5
```

- [ ] **Step 2: Run it to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py::TestFxDelegation -v`
Expected: FAIL — `_get_fx_rate` still does its own kv_cache/yfinance dance, returns a live/seeded rate, not 0.5.

- [ ] **Step 3: Rewrite `portfolios._get_fx_rate`**

Replace the body of `_get_fx_rate` in `portf_server/routers/portfolios.py`
(keep `_FX_CACHE`, `_FX_CACHE_TS`, `_FX_TTL`, `_SHARED_DB`, `set_shared_db`
as they are; the L1 in-process layer stays for the hot per-holding loops).
Add `from portf_manager import market` to the imports; the module's
`import yfinance as yf` can be removed if nothing else in the file uses it
(flake8 will tell you):

```python
def _get_fx_rate(currency: str) -> float:
    """Return EUR/currency rate, cached two ways.

    L1 is a per-worker in-process dict (fast, for the per-holding loops).
    L2 + live fetching is delegated to portf_manager.market.get_fx_eur, the
    shared read-time-freshness cache used by every consumer.
    """
    if currency == "EUR":
        return 1.0
    now = time.time()
    if currency in _FX_CACHE and now - _FX_CACHE_TS.get(currency, 0) < _FX_TTL:
        return _FX_CACHE[currency]
    rate, _stale = market.get_fx_eur(_SHARED_DB, currency, max_age=_FX_TTL)
    _FX_CACHE[currency] = rate
    _FX_CACHE_TS[currency] = now
    return rate
```

- [ ] **Step 4: Rewrite `public._fx`**

In `portf_server/routers/public.py`, delete the module-level `_FX`, `_FX_TS`
dicts and the old `_fx` body; replace with (and update the one call site
`fx = _fx(cur)` inside `public_summary` to `fx = _fx(db, cur)`; remove the
now-unused `import time` / `import yfinance as yf` if flake8 flags them):

```python
def _fx(db, currency: str) -> float:
    """EUR rate via the shared market-data cache (30-min freshness)."""
    rate, _stale = market.get_fx_eur(db, currency, max_age=1800)
    return rate
```

Add `from portf_manager import market` to the imports.

- [ ] **Step 5: Rewrite the `rebalance.py` inner fetch**

In `portf_server/routers/rebalance.py`, inside the analysis endpoint, replace
the `to_eur` helper's live-fetch branch (lines ~96-102) with the shared
service (add `from portf_manager import market` to imports; remove
`import yfinance as yf` if now unused):

```python
    def to_eur(amount: float, currency: str) -> float:
        if currency == "EUR" or amount == 0:
            return amount
        if currency not in _fx:
            _fx[currency] = market.get_fx_eur(db, currency, max_age=1800)[0]
        return amount * _fx[currency]
```

- [ ] **Step 6: Run the affected suites**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py tests/unit/test_api_routers.py tests/unit/test_rebalance_research.py tests/unit/test_routers_coverage.py tests/unit/test_shared_state.py -v`
Expected: all PASS (no existing FX test may regress).

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/portfolios.py portf_server/routers/public.py portf_server/routers/rebalance.py tests/unit/test_market_service.py
git commit -m "refactor: route all router FX lookups through market.get_fx_eur

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 7: Migrate research `_current_price` + watchlist price fetch

**Files:**
- Modify: `portf_server/routers/research.py:148-158`
- Modify: `portf_server/routers/watchlist.py:22-37`
- Modify: `tests/unit/test_market_service.py` (append test)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_market_service.py`:

```python
class TestCurrentPriceDelegation:
    def test_unheld_symbol_uses_market_quote(self, db, monkeypatch):
        from portf_server.routers import research as research_router

        monkeypatch.setattr(
            market,
            "get_quote",
            lambda d, s, max_age=900: {
                "symbol": s, "price": 42.0, "currency": "USD", "stale": False,
            },
        )
        price, cur = research_router._current_price(db, None, "NVDA")
        assert price == 42.0
        assert cur == "USD"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py::TestCurrentPriceDelegation -v`
Expected: FAIL — `_current_price` still calls `yf.Ticker(...).fast_info`
directly (real network call or error, not 42.0).

- [ ] **Step 3: Rewrite `research._current_price`**

In `portf_server/routers/research.py` add `from portf_manager import market`
and replace `_current_price`:

```python
def _current_price(db, asset: Optional[dict], symbol: str) -> tuple[float, str]:
    """Latest price + currency: stored price for held assets, else the shared
    market-data cache (15-min freshness)."""
    if asset:
        pd_ = db.get_latest_price(asset["id"])
        if pd_:
            return float(pd_["price"]), asset.get("currency", "EUR")
    q = market.get_quote(db, symbol, max_age=900)
    if q.get("price"):
        return float(q["price"]), q.get("currency") or "EUR"
    return 0.0, (asset.get("currency", "EUR") if asset else "EUR")
```

Remove the file's `import yfinance as yf` if nothing else uses it.

- [ ] **Step 4: Rewrite `watchlist._fetch_price_cached`**

In `portf_server/routers/watchlist.py` add `from portf_manager import market`
and replace `_fetch_price_cached` (keep `_PRICE_CACHE_TTL = 600`; the `.info`
lookup in `add_watchlist` stays — it fetches a *name*, not a price):

```python
def _fetch_price_cached(db, symbol: str) -> Tuple[Optional[float], Optional[str]]:
    """Return (price, fetched_at_iso) via the shared market-data cache."""
    q = market.get_quote(db, symbol, max_age=_PRICE_CACHE_TTL)
    if not q.get("price"):
        return None, None
    fetched_at = datetime.fromtimestamp(
        q["fetched_at"], tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return float(q["price"]), fetched_at
```

- [ ] **Step 5: Run the affected suites**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py tests/unit/test_rebalance_research.py tests/unit/test_research_valuation.py tests/unit/test_watchlist_goals_risk.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/research.py portf_server/routers/watchlist.py tests/unit/test_market_service.py
git commit -m "refactor: research + watchlist price lookups via shared market cache

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 8: pfm MCP `quote` tool

**Files:**
- Modify: `~/repos/pfm/mcp/server.py` (append a tool before the `__main__` block)

The MCP server is a plain HTTP client of the pfm API (no test harness — it is
verified by calling the function directly).

- [ ] **Step 1: Add the tool**

Append to `mcp/server.py` (before `if __name__ == "__main__":`):

```python
@mcp.tool()
def quote(symbols: str, max_age: int = 86400) -> str:
    """
    Market quotes (price, daily change %, currency) for one or more
    Yahoo-format tickers, served from pfm's shared market-data cache.

    Args:
        symbols: Comma-separated Yahoo tickers, e.g. 'NVDA,ASML.AS,BTC-EUR'.
        max_age: Maximum acceptable data age in seconds (default 1 day).
            Lower it (e.g. 900) when intraday freshness matters.
    """
    try:
        data = _get(
            "/api/v1/market/quotes", {"symbols": symbols, "max_age": max_age}
        )
    except Exception as e:
        return f"Error fetching quotes: {e}"

    lines = ["QUOTES:"]
    for q in data.get("quotes", []):
        if q.get("error"):
            lines.append(f"  {q.get('symbol', '?'):12s}  unavailable ({q['error']})")
            continue
        chg = (
            f"{q['change_pct']:+.2f}%"
            if q.get("change_pct") is not None
            else "n/a"
        )
        stale = "  [stale]" if q.get("stale") else ""
        lines.append(
            f"  {q['symbol']:12s} {q['price']:>12.4f} {q.get('currency') or '':3s}"
            f"  {chg}{stale}"
        )
    return "\n".join(lines)
```

- [ ] **Step 2: Smoke-test the tool function directly**

(The server must be running with the new router deployed — if Task 10's
deploy hasn't happened yet, do the backend HUP from that task first.)

Run: `python3 -c "import sys; sys.path.insert(0, '/home/agoldhoorn/repos/pfm/mcp'); import server; print(server.quote.fn('BTC-EUR', max_age=86400) if hasattr(server.quote, 'fn') else server.quote('BTC-EUR'))"`
Expected: a `QUOTES:` block with a BTC-EUR price (FastMCP wraps tools; `.fn`
is the raw function on current versions — whichever attribute exists).

- [ ] **Step 3: Commit (mcp has its own git repo)**

```bash
cd ~/repos/pfm/mcp && git add server.py && git commit -m "feat: quote tool — shared market-data quotes via pfm API

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

Note: `~/repos/pfm/mcp` contains a `.git` of its own (it is the `~/mcp` repo
content symlinked); check `git -C ~/repos/pfm/mcp status` first — if it turns
out to be part of the main pfm repo instead, commit it there with the same
message.

---

### Task 9: Migrate `~/scripts/stock-monitor.py`

**Files:**
- Modify: `~/scripts/stock-monitor.py:92-116` (`fetch_quotes`) + the `.L` entry in `EXCHANGE_CURRENCY`

Not in a git repo / test suite — verified by a manual run (step 3).

- [ ] **Step 1: Replace `fetch_quotes` and add key loading**

Add after the existing credentials block (after `TG_CHAT = ...`):

```python
# ── pfm shared market-data API ───────────────────────────────────────────────
PFM_ENV = os.path.expanduser("~/repos/pfm/.env.local")
PFM_URL = "http://localhost:8000"


def _pfm_api_key() -> str:
    with open(PFM_ENV) as f:
        for line in f:
            line = line.strip()
            if line.startswith("SERVER_API_KEY="):
                return line.partition("=")[2].strip()
    raise RuntimeError("SERVER_API_KEY not found in ~/repos/pfm/.env.local")
```

Replace the whole `fetch_quotes` function with:

```python
def fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch quotes from pfm's shared market-data cache (one batch call).

    max_age=1500 (25 min) sits under the 30-min cron cadence, so each run
    refreshes at most once while sharing data with all other pfm consumers.
    """
    url = (
        f"{PFM_URL}/api/v1/market/quotes"
        f"?symbols={','.join(symbols)}&max_age=1500"
    )
    req = urllib.request.Request(url, headers={"X-API-Key": _pfm_api_key()})
    # Generous timeout: a cold cache means ~30 sequential Yahoo fetches.
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    result = {}
    for q in data.get("quotes", []):
        if q.get("error") or q.get("price") is None or q.get("change_pct") is None:
            print(f"Warning: no usable quote for {q.get('symbol')}")
            continue
        result[q["symbol"]] = {
            "price":      q["price"],
            "change_pct": q["change_pct"],
            "name":       q.get("name") or q["symbol"],
        }
    return result
```

- [ ] **Step 2: Fix the `.L` currency display**

The shared service normalises GBX→GBP (÷100), so update the
`EXCHANGE_CURRENCY` map entry:

```python
    ".L":  ("GBP", "£"),
```

- [ ] **Step 3: Manual smoke run**

(Requires the backend deployed — Task 10 step 1. Run during crypto-always
hours; stocks only load during NYSE hours.)

Run: `python3 ~/scripts/stock-monitor.py`
Expected: exits 0; prints only `Warning:` lines for genuinely unresolvable
symbols (if any); sends Telegram only if a ±3/5/10% threshold is actually
crossed. Then verify the cache was shared:
`sqlite3` is not installed in the host — instead check via the API:
`curl -s -H "X-API-Key: $(grep '^SERVER_API_KEY=' ~/repos/pfm/.env.local | cut -d= -f2)" "http://localhost:8000/api/v1/market/quote/BTC-EUR?max_age=86400" | python3 -m json.tool`
Expected: `"source": "cache"` (seeded by the monitor's batch call).

No commit — `~/scripts` is not a git repo. (If `git -C ~/scripts status`
shows it actually is one, commit there: `chore: stock-monitor uses pfm shared market-data API`.)

---

### Task 10: Deploy, full verification, docs

**Files:**
- Modify: `CLAUDE.md` (Key Patterns section)

- [ ] **Step 1: Deploy the backend**

The backend container has no reload; HUP gunicorn (per deploy notes):

```bash
docker exec portf_backend_dev kill -HUP 1
```

- [ ] **Step 2: Live verification of all three endpoints**

```bash
KEY=$(grep '^SERVER_API_KEY=' ~/repos/pfm/.env.local | cut -d= -f2)
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/v1/market/quotes?symbols=NVDA,ASML.AS,BTC-EUR&max_age=600" | python3 -m json.tool
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/v1/market/fx?currencies=USD,GBP" | python3 -m json.tool
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/v1/market/fundamentals/NVDA" | python3 -m json.tool
```

Expected: quotes with `source` `live`/`cache`/`db` and no top-level error;
repeat the first curl — `source` should flip to `"cache"`.

- [ ] **Step 3: Run the full unit suite**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: 0 failures (429 passing baseline + the new market tests).

- [ ] **Step 4: Update CLAUDE.md**

Add to the *Key Patterns* section of `CLAUDE.md` (after the "Price Updates"
subsection):

```markdown
### Market Data API (`portf_server/routers/market.py` + `portf_manager/market.py`)
Shared, key-auth Yahoo Finance cache — the single market-data source for web,
MCP, and cron scripts (no consumer fetches Yahoo directly except the price
cron, which *writes* the `prices` table):
- `GET /api/v1/market/quotes?symbols=A,B,C&max_age=` (batch, ≤50), `/market/quote/{symbol}`, `/market/fx?currencies=`, `/market/fundamentals/{symbol}`
- **Read-time freshness**: kv_cache values (`mkt:quote:*`, `mkt:fx:*`, `mkt:fund:*`) store a 7-day-expiry payload with `fetched_at` inside; callers pass `max_age` seconds (floor 60). Stale-on-failure: Yahoo down → last value with `stale: true`.
- Quote resolution: fresh cache → held asset's stored daily price (prev close from prior row) → live fast_info (GBX÷100) → stale fallback → error entry.
- Consumers: routers (`_get_fx_rate`, `public._fx`, rebalance, research `_current_price`, watchlist) all delegate to `portf_manager.market`; `~/scripts/stock-monitor.py` and the MCP `quote` tool call the HTTP API.
```

- [ ] **Step 5: Final commit + push**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-06-12-market-data-api.md
git commit -m "docs: market-data API section in CLAUDE.md + implementation plan

Co-Authored-By: Oz <oz-agent@warp.dev>"
GIT_SSH_COMMAND="ssh -o IdentitiesOnly=no" git push origin main
```

(The pre-push hook re-runs the unit suite; it must pass.)
