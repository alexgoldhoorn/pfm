# Shared Market-Data Service & API — Design

**Date:** 2026-06-12
**Status:** Approved

## Goal

pfm becomes the single source of financial data on this machine. Private data
(portfolio) stays behind the existing key auth; cached market data (ticker
quotes, FX rates, fundamentals) is exposed via new key-auth endpoints with a
caller-controlled freshness parameter, so every consumer — web client, pfm MCP
(Claude), hermes scripts, cron monitors — shares one Yahoo Finance cache
instead of fetching independently.

## Background / problem

Audit findings (2026-06-12):

- `~/scripts/stock-monitor.py` hits `query2.finance.yahoo.com` directly every
  30 min for ~30 tickers, many overlapping pfm holdings — duplicate fetches,
  no shared cache.
- `research.py:_current_price` falls back to a **live, uncached**
  `yf.Ticker(...).fast_info` call for non-held tickers — every
  `finance_monitor.py` / Research-page lookup of an unheld ticker hits Yahoo.
- Four independent FX-rate implementations: `portfolios._get_fx_rate`
  (per-worker in-memory dict), `public._fx` (own in-memory dict), an inline
  fetch in `rebalance.py`, and `api_client.get_fx_rate` (CLI side).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Auth | Existing `X-API-Key` — "public" means *contains no portfolio data*, not unauthenticated |
| Data scope | Price quotes (batchable), FX EUR rates, fundamentals. **No** price history (YAGNI) |
| Freshness | `?max_age=<seconds>` per request; freshness decided at **read** time. Defaults: quotes 86400, FX 3600, fundamentals 21600 |
| Stale + fetch failure | Serve stale value with `stale: true` + `fetched_at`; `error` field only if nothing cached |
| Consumers migrated now | `stock-monitor.py`, research lookup internals, new pfm MCP `quote` tool, all internal ad-hoc fetchers |
| Topology | One server (pfm) on this PC; no new servers in `~/mcp` |

## Architecture

### 1. Core service: `portf_manager/market.py` (new module)

Used by both server routers and CLI-side code.

```python
get_quote(db, symbol, max_age=86400) -> dict
get_quotes(db, symbols, max_age=86400) -> list[dict]   # loops get_quote
get_fx_eur(db, currency, max_age=3600) -> tuple[float, bool]  # (rate, stale)
get_fundamentals(db, symbol, max_age=21600) -> dict  # read-time pattern over the raw fetch
```

Quote dict: `{symbol, price, prev_close, change_pct, currency, name,
fetched_at, source, stale}` (+ `error` when unresolvable).

**Read-time freshness:** values stored in the existing `kv_cache` (v14) under
`mkt:quote:{SYM}` / `mkt:fx:{CUR}` with a long expiry (7 days) and a
`fetched_at` epoch *inside* the value. The existing `cached()` helper bakes
TTL in at write time, so `market.py` reads/writes `db.cache_get/cache_set`
directly and compares `fetched_at` against the caller's `max_age`.

Quote resolution order:

1. `kv_cache` entry with `fetched_at` within `max_age` → serve, `source: "cache"`.
2. Held asset whose latest `prices` row is within `max_age` → serve from DB;
   `prev_close` from the prior stored row; `source: "db"`. Default 1-day
   requests for held assets never hit Yahoo (daily price cron keeps them fresh).
3. Live Yahoo `fast_info` fetch, with GBX ÷100 → GBP normalization → store in
   kv_cache → serve, `source: "live"`.
4. Live fetch fails → stale cache/DB value with `stale: true`, else `error`.

FX uses the same pattern with `{CUR}EUR=X` tickers; `EUR` short-circuits to 1.0.

Fundamentals use the same read-time pattern under `mkt:fund:{SYM}` (calling the
raw yfinance fetch that `services/research.fetch_fundamentals` uses), so a
caller's smaller `max_age` is honored. The existing write-TTL caching inside
`fetch_fundamentals` stays as-is for the research workbench; the two share the
underlying fetch function, not the cache entry.

### 2. New router: `portf_server/routers/market.py` → `/api/v1/market`

Key-auth like the rest of the API. **Plain `def` handlers** (blocking yfinance
I/O must run in the threadpool — project rule).

- `GET /market/quotes?symbols=NVDA,ASML.AS,BTC-EUR&max_age=1500` — batch;
  per-symbol errors inside the response (never a whole-batch 500)
- `GET /market/quote/{symbol}?max_age=` — single convenience
- `GET /market/fx?currencies=USD,GBP,DKK&max_age=3600` — EUR rates
- `GET /market/fundamentals/{symbol}?max_age=21600`

Symbols are Yahoo format (crypto as `BTC-EUR`). `max_age` is clamped to a
sane floor (e.g. 60 s) to prevent accidental hammering.

### 3. Internal migrations

| Site | Change |
|---|---|
| `portfolios._get_fx_rate` | Keep signature (4 routers import it); delegate to `market.get_fx_eur`. Per-worker dict removed — FX shared across gunicorn workers via kv_cache |
| `public._fx` | Delegate to `market.get_fx_eur` |
| `rebalance.py` inline FX fetch | Delegate to `market.get_fx_eur` |
| `research._current_price` fallback | `market.get_quote` — closes the uncached-price gap for finance_monitor / Research page |
| `watchlist` price checks | `market.get_quote` with `max_age≈900` |

**Not migrated (with reasons):** `api_client.fetch_latest_prices` (daily-cron
*writer* of the `prices` table — the source, not a consumer); benchmark
history / sector-country / fundamentals-news internals (already kv_cached with
sensible TTLs); `stock_report.py` (needs history series, out of quote scope);
the old `services/market_data.py` `EnhancedMarketDataService`
(screener/technical-analysis service, unrelated — untouched).

### 4. External consumers (one server on this PC)

- **`~/scripts/stock-monitor.py`** — `fetch_quotes()` becomes one
  `GET /market/quotes` call with `max_age=1500` (under its 30-min cadence);
  reads `SERVER_API_KEY` from `~/repos/pfm/.env.local` like the other portf
  scripts. Telegram/threshold logic unchanged.
- **pfm MCP** (`~/repos/pfm/mcp/server.py`, existing server) — new
  `quote(symbols, max_age=86400)` tool hitting `/market/quotes`; serves Claude
  sessions and anything else registered against the pfm MCP.
- **hermes scripts** (`finance_monitor.py`, daily report) — no change needed;
  they already go through the pfm API and benefit via the research-lookup
  migration.

### 5. Error handling

- Batch endpoint: per-symbol `error` entries, HTTP 200 for the batch.
- Yahoo outage: stale data with `stale: true` keeps alert crons functional.
- kv_cache read/write failures degrade to live fetch (same philosophy as
  `cache.py`).
- Invalid `max_age` (non-numeric, negative) → 422 via FastAPI validation.

### 6. Testing

Unit tests (`tests/unit/`), mocked yfinance + in-memory SQLite:

- fresh-cache hit → no fetch called
- expired cache → refetch + re-store
- fetch failure → stale value with `stale: true`
- nothing cached + fetch failure → `error` entry
- held-asset DB path (source `"db"`, prev_close from prior row)
- GBX normalization (GBp → GBP ÷100)
- batch with one bad symbol → others unaffected
- FX delegation: `_get_fx_rate` returns kv_cache-backed rate; EUR → 1.0
- router param validation (max_age clamp/422)

No DB schema change (kv_cache exists since v14). Web client untouched except
that nothing breaks — no `?v=` bump needed unless web files change.

## Out of scope

- Price history / OHLC endpoints
- Unauthenticated access or a separate market-data key
- Migrating `stock_report.py` or the screener service
- Intraday writes to the `prices` table (cron remains the only writer)
