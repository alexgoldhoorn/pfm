# CLAUDE.md — Project Rules for AI Assistants

## Project Overview
Portfolio Manager: a Python CLI + FastAPI server + web client for tracking stocks, ETFs, funds, bonds, crypto and commodities across multiple brokers, with LLM-powered import, file import/export, web chat, Spanish IRPF tax reporting, and Google Sheets PDT sync.

## Code Style
- Use **black** code formatting (line length 88). Run: `uv run black <file>`.
- Comments go on the **line before** the code they describe, not inline.
- Use type hints on all function signatures.
- Docstrings: Google style.

## Python
- Target Python **3.13**. Virtual env managed with **uv** (`uv sync`).
- Dependencies in `pyproject.toml`. Dev deps under `[tool.uv] dev-dependencies`. Lockfile: `uv.lock`.

## Architecture
- `portf_manager/` — Core package: CLI, models, database, parsers, LLM client, tax calculator.
- `portf_server/` — FastAPI REST API server with routers, schemas, auth middleware.
- `web_client/` — Vanilla JS + Bootstrap 5 frontend (static files, no build step). Entry: `portfolio_debug.js`.
- `tests/` — pytest test suite. `tests/unit/`, `tests/integration/`, `tests/e2e/`.

## Key Patterns

### Database
SQLite by default (`portfolio.db`), PostgreSQL via `DATABASE_URL` env var. Use `portf_manager/database.py` for SQLite, `database_factory.py` for auto-detection.

**Current schema version: 10.** Migrations run automatically on startup.

Key fields added in recent migrations:
- v5: `tax REAL DEFAULT 0` on `transactions`; new `bookings` table (deposits/withdrawals)
- v6: `currency TEXT` on `transactions` — per-transaction currency overrides asset currency
- v7: `allocation_targets` (rebalancing), `price_targets` (buy/sell thresholds + fair value), `research_reports` (LLM valuation cache)
- v8: `portfolio_snapshots` (daily value/cost for the net-worth chart, recorded by the price cron)
- v9: `watchlist` (tracked tickers + buy_below), `goals` (FIRE targets + projection)
- v10: `website TEXT` on `portfolios` (broker website; `description` already existed)

All transaction SELECT queries use `COALESCE(t.currency, a.currency) AS currency` with an explicit column list (NOT `t.*`) because `sqlite3.Row` dict uses the first column when names collide.

`bookings` table: `id, portfolio_id, date, action (Deposit|Withdrawal), amount, currency`

### LLM
Provider-agnostic via `portf_manager/llm_client.py`. Factory `get_llm_client()` auto-detects in priority order:
1. Ollama (`OLLAMA_HOST:OLLAMA_PORT`) — local, no API key
2. Gemini (`GEMINI_API_KEY`) — default model `gemini-2.5-flash`
3. OpenRouter (`OPENROUTER_API_KEY`) — default model `openai/gpt-4o-mini`

Override with env vars:
- `PORTF_LLM_PROVIDER=auto|ollama|gemini|openrouter`
- `PORTF_LLM_MODEL=<model-name>`

`docker-compose.yml` sets `PORTF_LLM_PROVIDER=gemini` for the backend (the container has `GEMINI_API_KEY`). These `environment:` entries override `.env*`, so change the provider there, not only in `.env.local`. `GeminiClient.extract_transactions` keeps a statement's time (`...THH:MM:SS`) when present; `extract_bookings` pulls cash deposits/withdrawals.

All LLM calls go through `LLMClient.generate(prompt) -> str`. `GeminiClient` is a legacy wrapper kept for backward compatibility; it delegates to `get_llm_client()`.

### CSV / File Parsers
Each broker has a standalone parser module returning `LLMTransaction` objects via a `ParseResult` dataclass:
- `indexacapital_csv_parser.py` — semicolon CSV, European number format, ISINs, EUR
- `coinbase_csv_parser.py` — Coinbase Advanced Trade CSV export
- `pdt_xlsx_parser.py` — Portfolio Dividend Tracker XLSX (import + export, all 3 sheets)
- `pdt_sheets_sync.py` — Portfolio Dividend Tracker Google Sheets sync (pull/push)

### PDT Format (`pdt_xlsx_parser.py` and `pdt_sheets_sync.py`)
The PDT v2 format has **5 sheets** with a 3-row header block (machine keys / group labels / display labels), data from row 4:
- **Transactions** — buy/sell with costs, tax, exchange rate
- **Dividends** — cash/stock/staking payouts
- **Bookings** — deposits and withdrawals (no asset)
- **Expenses** — broker costs (we export empty sheet with correct headers)
- **Settings** — version 2.0 + PDT API URL (required for Google Sheets integration)

`PDTXLSXParser.parse(path) → PDTParseResult` — reads from file (Transactions/Dividends/Bookings only).
`PDTXLSXExporter.export(db, path)` — writes all 5 sheets from DB.
`PDTSheetsSync(sheet_id).pull() → PDTParseResult` — reads Transactions/Dividends/Bookings from Google Sheet.
`PDTSheetsSync(sheet_id).push(db)` — writes all 5 sheets to Google Sheet.

Google Sheets API uses `UNFORMATTED_VALUE + SERIAL_NUMBER` for reading (dates come back as floats; convert via `_serial_to_date`). Writing uses `USER_ENTERED` so ISO date strings are interpreted as dates.

Auth: `GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json` (already configured).

**PDT compatibility rules** (verified against live PDT API endpoints):
- Effect types: exactly `"Stock market"`, `"Crypto"`, `"Commodity"` (case-sensitive, these are PDT's live values)
- Crypto and commodity assets **must have empty exchange** — use `_pdt_exchange(exchange, asset_type)` helper which returns `""` for crypto/commodity
- Bookings display label (row 2 col 0) is `" "` (single space), not `"Broker"`
- `"XETRA Exchange"`, `"Nasdaq"`, `"MyInvestor"`, `"Indexa Capital"` all match PDT's canonical names exactly
- Dividend action is always `"Cash"` (we don't yet store Cash/Stock/Staking subtype separately)

### Import API (`portf_server/routers/imports.py`)
- `POST /api/v1/import/upload` — multipart file + `broker` field → parse preview (no DB write)
- `POST /api/v1/import/save` — `SaveRequest(transactions, bookings, portfolio_id, duplicate_action)` → write to DB
- `POST /api/v1/import/check-duplicates` — flag which supplied rows already exist (no write); powers the text/LLM preview
- Supported broker values: `indexacapital`, `coinbase`, `pdt`, `bookings` (generic cash-CSV → `parsers/bookings_csv_parser.py`)
- `PreviewTransaction` has: `symbol, name, asset_type, tx_type, date, quantity, price, currency, fees, tax, exchange, notes, broker, is_duplicate`
- `PreviewBooking` has: `broker, date, action, amount, currency, is_duplicate`
- `SaveResponse` has: `saved, saved_bookings, duplicates_skipped, overwritten, errors`
- **Duplicate handling**: `duplicate_action` = `skip` (default) | `add` (insert copy) | `overwrite` (update existing). `force=True` is the legacy alias for `add`. `find_duplicate_transaction` matches asset+type+qty(±0.0001)+price(±0.001%)+portfolio and is **time-aware**: matches on the calendar date, plus the time-of-day only when both rows carry one. `find_duplicate_booking` matches date+action+amount+currency+portfolio. Upload/preview set `is_duplicate`; the dedup is pure SQL (no LLM).

### Export API (`portf_server/routers/exports.py`)
- `GET /api/v1/export/csv` — all transactions as UTF-8 CSV (Excel BOM)
- `GET /api/v1/export/pdt` — full PDT XLSX (5 sheets: Transactions, Dividends, Bookings, Expenses, Settings)
- Both accept optional `?portfolio_id=` query param

### Bookings API (`portf_server/routers/bookings.py`)
- `GET /api/v1/bookings/` — list bookings, optional `?portfolio_id=`
- `POST /api/v1/bookings/` — create a manual deposit/withdrawal (the Import/Export page's add-booking form)
- `DELETE /api/v1/bookings/{id}`
- Bookings are importable 4 ways: PDT (sheet/XLSX), generic `bookings` CSV, LLM text extraction (`POST /api/v1/llm/extract-bookings`), and the manual form.

### Rebalance API (`portf_server/routers/rebalance.py`)
- `GET /api/v1/rebalance/targets` — list allocation targets per asset type
- `PUT /api/v1/rebalance/targets` — bulk replace targets (`[{asset_type, target_pct}]`)
- `GET /api/v1/rebalance/analysis` — current vs target % drift + buy/sell actions to rebalance (EUR-converted)

### Research API (`portf_server/routers/research.py` + `services/research.py`)
- `GET /api/v1/research/{symbol}` — cached valuation report (404 if none)
- `POST /api/v1/research/{symbol}/generate` — fetch yfinance fundamentals + LLM → fair value, BUY/HOLD/SELL, confidence, risks, catalysts (~10s)
- `GET|PUT /api/v1/research/{symbol}/targets` — per-asset buy_below / sell_above / fair_value
- `GET /api/v1/research/alerts/check` — price targets crossed vs latest prices (no Telegram send)
- Telegram alerts: `~/scripts/portf-price-alerts.sh` runs at 20:05 daily (after price update) via cron, calls the check endpoint and pings Telegram on crossed thresholds.

### Analytics API (`portf_server/routers/analytics.py` + `services/analytics_service.py`)
- `GET /api/v1/analytics/dividends` — income by year/month/symbol, TTM, projected annual, yield-on-cost
- `GET /api/v1/analytics/performance?benchmark=^GSPC` — invested, current value, total return, money-weighted IRR, benchmark comparison
- `GET /api/v1/analytics/networth-history` — daily value/cost snapshots for the chart
- `POST /api/v1/analytics/snapshot` — record today's value/cost (called by the price cron)
- `POST /api/v1/analytics/backfill-snapshots[?force=]` + `GET /analytics/backfill-status` — reconstruct daily snapshots from transactions + historical yfinance prices (background thread; only fills missing dates unless `force`). Unpriced assets (ISIN/P2P) are valued at cost for history. `period_return` is a **time-weighted return** (chains daily returns, removes contributions via the cost-basis delta) — a naive (end−start)/start reads absurdly high when contributions dominate.
- `GET /api/v1/analytics/tax-estimate?year=` — Spanish IRPF savings-base estimate (realised gains + dividends), unrealised gain, tax-loss harvesting candidates
- `irpf_savings_tax()` uses the progressive base-del-ahorro brackets (19/21/23/27/28%)
- `GET /api/v1/analytics/diversification` — sector/country/currency/type concentration + Herfindahl HHI (fetches yfinance, slow)
- `GET /api/v1/analytics/risk` — max drawdown, volatility, Sharpe from snapshots (needs ≥3)
- `GET /api/v1/analytics/fees` — total fees/tax per broker, fee drag % of invested
- `GET /api/v1/analytics/tax-report?year=` — per-lot FIFO realised gains + dividend withholding summary
- `services/tax_rates.py` — progressive savings-base brackets per jurisdiction (Spain default); `irpf_savings_tax` delegates here
- `GET /api/v1/public/summary` (router `public.py`) — %-only allocation + return, NO amounts; off unless `PORTF_PUBLIC_VIEW=true`. Standalone page: `web_client/public.html`
- Web client is **same-origin**: calls `/api/...` proxied by the web container's nginx to `portf_backend_dev:8000` (see `web_client/nginx.conf`). External access via nginx-proxy-manager → `docs/EXTERNAL_ACCESS.md`
- Auth: `POST /api/v1/auth/login-key` (username/password → returns SERVER_API_KEY for the browser); web login modal has Password / Create account / API key tabs
- Cron jobs: `portf-price-alerts.sh` (20:05, also checks watchlist buy zones), `portf-monthly-report.sh` (1st of month 09:00); daily price script also POSTs a snapshot

### Watchlist API (`portf_server/routers/watchlist.py`)
- `GET/POST /api/v1/watchlist/`, `DELETE /api/v1/watchlist/{symbol}`, `GET /api/v1/watchlist/alerts/check`
- Tracks not-yet-owned tickers with a `buy_below` target; the price-alerts cron pings Telegram when a watched ticker enters its buy zone

### Goals API (`portf_server/routers/goals.py`)
- `GET/POST /api/v1/goals/`, `DELETE /api/v1/goals/{id}`
- FIRE/savings targets; GET computes progress %, projected value (monthly compounding from latest snapshot), on-track flag, and required monthly contribution

### Sync API (`portf_server/routers/sync.py`)
- `GET /api/v1/sync/pdt-config` — service account status + default spreadsheet ID
- `POST /api/v1/sync/pdt-pull?spreadsheet_id=` — Google Sheet → DB (reads Transactions, Dividends, Bookings)
- `POST /api/v1/sync/pdt-push?spreadsheet_id=` — DB → Google Sheet (writes all 5 PDT sheets)
- Falls back to `GOOGLE_SPREADSHEET_ID` env var if no `spreadsheet_id` query param.

### LLM API (`portf_server/routers/llm.py`)
- `POST /api/v1/llm/extract-transactions` — text body → LLM extracts buy/sell transactions
- `POST /api/v1/llm/chat` — conversational endpoint with session history
- `EnhancedChatEngine` uses **lazy initialization** — do NOT instantiate at module level (crashes without API key)

### Auth
API key auth for all server endpoints (`X-API-Key` header). User auth with password hashing in local CLI mode.
`SERVER_API_KEY` env var is **auto-seeded** into the `api_keys` table at startup (`app.py` lifespan). No manual DB insert needed after a container restart — just set the var in `.env.local`.

### Portfolio Resolver
`db.get_or_create_portfolio(name, base_currency="EUR")` — centralized helper in `database.py`. Use this instead of the inline get/create pattern in every router.

### Portfolios = Brokers (`portf_server/routers/portfolios.py`)
A portfolio doubles as a broker/account (the web page is titled "Portfolios / Brokers"; no rename). `GET /api/v1/portfolios/` returns `website` + `description` (stored value, else a built-in default from `KNOWN_BROKERS` for names like MyInvestor/Indexa/Degiro/Coinbase/Mintos…) plus `first/last_transaction_date` and `first/last_booking_date` (from `db.get_portfolio_date_ranges()`). `website` is editable via `PUT /api/v1/portfolios/{id}` (v10 column). Portfolio queries alias `e.website AS entity_website` to avoid colliding with the new `p.website`.

### Price Updates
Daily cron at **20:00 UTC** via `~/scripts/portf-update-prices.sh` (reads API key from `.env.local`).
Manual: `docker exec -e PORTF_API_KEY=... portf_backend_dev python3 -m portf_manager.cli update-prices`

- yfinance returns UK-listed securities in **GBX (pence)**, not GBP. `fetch_latest_prices` auto-converts when `fast_info.currency == "GBp"` (÷100). Never store raw yfinance prices for UK ISINs without this check.
- Crypto uses `{SYM}-EUR` format (e.g. `BTC-EUR`). Some tickers need a Yahoo-specific suffix (e.g. `UNI` → `UNI1-EUR`); a few assets have no Yahoo price data at all and are skipped on price refresh.
- `GET /api/v1/portfolios/holdings` summary is EUR-converted via `XYZEUR=X` FX tickers. Per-holding `total_value_eur` is available; `total_value` is in the asset's native currency.

### Portfolio Value History
`~/.hermes/data/portf_history.jsonl` — daily report appends `{"date": "...", "value": ...}` each run. The 1d/1w/1m/1y comparisons only appear once enough history has accumulated.

### Transactions
All financial transactions go through `database.create_transaction()` with `portfolio_id` for per-broker tracking. `asset_id` is required; use `db.get_asset_by_symbol()` + `db.create_asset()` for auto-create pattern. Always pass `currency=` (the transaction's own currency, e.g. `tx.price_currency`) to preserve per-row currency correctly.

### Positions & corporate actions (`portf_manager/positions.py`)
`compute_positions(transactions, key=...)` is the **single source of truth** for turning transactions into `{key: {quantity, cost}}` + realised P&L. It processes **chronologically** and supports **stock splits**: a `split` transaction stores the ratio in its `quantity` (2-for-1 → 2.0; 1-for-10 reverse → 0.1), scaling held quantity and leaving cost unchanged. The holdings/values endpoints and `analytics._compute_positions` all delegate to it; `tax_calculator` applies splits to FIFO lots. **Note:** this fixed a latent cost-basis bug — the old per-loop accumulation ran on `get_all_transactions()` (date DESC), so a partial sell processed before its buys left sold shares in cost basis, *overstating invested / understating return* for any asset with sells.

## Web Client (`web_client/`)
Single HTML file (`index.html`) + one JS file (`portfolio_debug.js`). All pages are divs toggled by `navigationManager.showPage()`. Pages: `dashboard`, `assets`, `transactions`, `holdings`, `chat`, `importexport`, `portfolios`, **`forecast`**.

**User preferences (browser-local)**: `window.PREFS` (persisted to `localStorage` key `pfmPrefs`) holds number locale, decimals, date format, theme, privacy-blur, default benchmark, landing page, rows-per-page. The Settings modal (`setupSettings()`, gear in the sidebar) edits them. Format all numbers via `Fmt.num()` / money helpers (which wrap in `<span class="pfm-amt">` for privacy blur) and dates via `Fmt.date()`. Theme = `data-bs-theme` on `<html>` (Bootstrap 5.3). There is no server-side per-user settings store — the web logs in with the shared `SERVER_API_KEY`, so prefs are per-browser.

**Deploy after editing web files**: `portf_web` is an nginx container with files **baked into the image** at build time — they are NOT live-mounted. After any change to `web_client/`:
```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

`forecast` page — wealth projection using live portfolio value + GBM simulation (`setupForecastPage()` in `portfolio_debug.js`).

### Import / Export page (`importexport`)
Inline (no modals). Sections:
- **File import** — broker select + file upload → parse preview (shows bookings summary for PDT) → save
- **Text import (LLM)** — paste broker statement → extract → save
- **Export** — CSV and PDT XLSX download buttons
- **Bookings table** — live table of all bookings with refresh button
- **Google Sheets Sync** — enter spreadsheet ID, Pull / Push buttons with live status

### Transaction import/export buttons (Transactions page)
- **Import text** → `#llmImportModal` → `POST /api/v1/llm/extract-transactions` → `POST /api/v1/import/save`
- **Import file** → `#fileImportModal` → `POST /api/v1/import/upload` → `POST /api/v1/import/save`
- **Export CSV** / **Export PDT** → fetch + blob download via `setupExportButtons()`

### Chat page
`setupChatPage()` in `portfolio_debug.js`:
- **Send** (or Ctrl+Enter) → `POST /api/v1/llm/chat` → conversation thread
- **Extract & Import** → `POST /api/v1/llm/extract-transactions` → inline transaction card with import button

### API client (`saveImportedTransactions`)
Signature: `saveImportedTransactions(transactions, bookings = [], portfolioId = null)` — always pass bookings array (even if empty) so PDT bookings are saved alongside transactions.

## Testing
- Run tests: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
- **342 passing**, 0 failures, 6 skipped (as of last session).
- Pre-push hook runs the full unit test suite automatically (`git push` will fail if tests fail).
- Safe F541 fixer: `uv run python scripts/fix_f541.py` — strips `f` from f-strings without `{}` using a regex that correctly excludes triple-quoted strings.
- Reset LLM singleton between tests: `from portf_manager.llm_client import reset_llm_client`.
- PDT parser tests: `tests/test_pdt_xlsx_parser.py` (42 tests)
- PDT Sheets sync tests: `tests/test_pdt_sheets_sync.py` (40 tests — all mocked, no real API calls)
- Import/export + sync API tests: `tests/unit/test_imports_exports.py` (30 tests)
- DB tests: `tests/test_database.py` — version assertion is `== 10` (bump it with `DATABASE_VERSION`)

## Git
- Main development branch: `develop` (ahead of `main`). Current feature branch: `pdt-format`.
- Use `--no-pager` with all git commands (note: some git versions don't support `--no-pager` as a flag; use `git -P` or `GIT_PAGER=cat` instead).
- Commit messages: conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`).
- Co-author line: `Co-Authored-By: Oz <oz-agent@warp.dev>`.
- **Pre-commit**: black + flake8 + autoflake run on every `git commit` via `.pre-commit-config.yaml`.
- **Pre-push**: full unit test suite runs before every `git push` (`.git/hooks/pre-push`).

## Important Gotchas
- **`_TX_COLS` uses f-strings**: queries in `database.py` are `f"""SELECT {self._TX_COLS}..."""`. The `f` prefix is load-bearing — SQLite rejects literal `{` with "unrecognized token". Never run regex F541-fixers that touch triple-quoted strings on this file. Autoflake is safe; custom regex strippers are not.
- **Black + regex interaction**: a regex like `re.sub(r'\bf("...')` strips `f` from single-line strings, but `f""` matches the first two chars of `f"""..."""` opening a multiline string. Limit single-line patterns with `[^\n]` and never match across newlines when looking for `F541`.
- **App always uses SQLite**: `app.py` falls back to `portfolio.db` (SQLite) for non-`sqlite://` URLs. Container data lives at `/app/portfolio.db` inside `portf_backend_dev`.
- **Linting**: `uv add --dev flake8` then `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`. ~11 known structural warnings remain in `cli.py` / `portfolio_aware_agent.py`.
- MyInvestor CSV is semicolon-delimited with European number formatting (comma = decimal, dot = thousands). No standalone parser module — handled inline in CLI or via LLM text import.
- Spanish tax: stocks, ETFs, bonds and funds all = "rendimientos del capital mobiliario" (IRPF Box 27). FIFO method for cost basis.
- PDT XLSX export uses tempfile (openpyxl writes to a file path, not BytesIO). Always clean up with `os.unlink()`.
- `portf_server/settings.py` uses `PORTF_` prefix for all env vars. Google vars (`GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SPREADSHEET_ID`) are NOT prefixed — read directly via `os.getenv()` in the sync router and `pdt_sheets_sync.py`.
- `sqlite3.Row` dict (from `dict(row)`) uses the **first** occurrence when column names collide. Never rely on `SELECT t.*, ..., COALESCE(t.col, other) AS col` — use an explicit column list instead.
- PDT has 5 sheets; we only parse the 3 data sheets (Transactions/Dividends/Bookings) on import. Expenses and Settings are written on export/push.
- PDT dividend action subtypes (Cash / Stock / Staking) are not stored as a separate field — all stored as `transaction_type='dividend'` and exported as `"Cash"`. Only Cash dividends are in the current dataset.
- Crypto/commodity assets must have empty exchange in PDT format — use `_pdt_exchange(exchange, asset_type)` which enforces this.
- **GBX (pence) normalization**: Yahoo quotes some UK-listed stocks (GB-prefixed ISINs) in GBX. `portf_manager/currency_utils.normalize_gbx_amounts()` divides imported price/amount/fees ÷100 and sets currency GBP — called in `imports.py` save + `sync.py` pull. The live price fetch normalizes separately in `api_client.py`. Without this, cost basis is 100× too high.
- When bumping `DATABASE_VERSION`, update the `assert version == N` assertions in `tests/test_database.py`.

## Status
See `PROJECT_STATUS.md` for full component status, pending work, and known issues.
