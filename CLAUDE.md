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
- `web_client/` — Vanilla JS + Bootstrap 5 frontend (static files, no build step). Entry: `pfm_core.js` → `pfm_pages.js` → `pfm_analytics.js` → `pfm_features.js` (split from the former `portfolio_debug.js`).
- `tests/` — pytest test suite. `tests/unit/`, `tests/integration/`, `tests/e2e/`.

## Key Patterns

### Database
SQLite by default (`portfolio.db`), PostgreSQL via `DATABASE_URL` env var. Use `portf_manager/database.py` for SQLite, `database_factory.py` for auto-detection.

**Current schema version: 24.** Migrations run automatically on startup.

Migration history (condensed — see `_migrate_to_vN` for full schema detail):
- v5: `bookings` table (deposits/withdrawals); `tax` on `transactions`
- v6: per-tx `currency` | v7: `allocation_targets`, `price_targets`, `research_reports`
- v8: `portfolio_snapshots` | v9: `watchlist`, `goals` | v10: `portfolios.website` | v11: `assets.auto_price`
- v12: `research_notes` | v13: `'index'` asset_type — **add new `asset_type` values to BOTH Pydantic (`portf_server/schemas/assets.py`) AND `models.py` enum or `/assets` 500s**
- v14: `kv_cache` (TTL cache via `portf_manager/cache.py` `cached(db, key, ttl, producer)`)
- v15: `manual_assets` | v16: `'interest'` tx_type — update `models.py TransactionType` + SQLAlchemy CHECK + rebuild `transactions` table (all three)
- v17: `price_update_runs` | v18: `assets.ticker` | v19: `fixed_deposits` | v20: `monthly_cashflow`
- v21: `app_settings` (`key TEXT PK, value TEXT`; `db.get/set_setting`) | v22: `push_subscriptions` (PWA push) | v23: recovery migration
- v24: `chat_sessions` (id TEXT PK, name, created_at, last_message_at, message_count, messages JSON) — persistent named chat threads; `db.create/get/list/update/delete_chat_session`; web: col-md-3 sidebar + col-md-9 message area

⚠️ **New tables must appear in BOTH `_create_all_tables` (fresh DBs) AND `_migrate_to_vN` (existing DBs)** — migration-only adds break fresh installs/tests with "no such table".
⚠️ **CHECK constraint rebuilds** require `PRAGMA legacy_alter_table=ON` around the `RENAME` — see `_migrate_to_v13`.

All transaction SELECT queries use `COALESCE(t.currency, a.currency) AS currency` with an explicit column list (NOT `t.*`) — `sqlite3.Row` uses the first column when names collide.

`bookings` table: `id, portfolio_id, date, action (Deposit|Withdrawal), amount, currency`

### Performance / event loop
- Endpoints doing blocking yfinance I/O are plain `def` (not `async`) so FastAPI runs them in a threadpool. Keep new blocking-IO endpoints sync.
- The `.venv` is root-owned (Docker-created); run tooling with `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …`.

### LLM
Provider-agnostic via `portf_manager/llm_client.py`. Factory `get_llm_client()` auto-detects in priority order:
1. Ollama (`OLLAMA_HOST:OLLAMA_PORT`) | 2. Gemini (`GEMINI_API_KEY`, default `gemini-2.5-flash`) | 3. OpenRouter (`OPENROUTER_API_KEY`) | 4. Anthropic (`ANTHROPIC_API_KEY`, default `claude-sonnet-4-6`)

Override: `PORTF_LLM_PROVIDER=auto|ollama|gemini|openrouter|anthropic`, `PORTF_LLM_MODEL=<model>`.

**Search grounding**: `GeminiLLMClient` and `AnthropicLLMClient` implement `generate_with_search(prompt, symbol) -> str`. Research `generate_valuation_report()` detects via `hasattr(llm, "generate_with_search")`. Returns `{"text": "<llm json>", "sources": [...]}`. Ollama/OpenRouter do NOT support search grounding.

**Tool calling (chat agentic loop)**: All 4 providers implement `ToolCapableLLMClient` (protocol in `portf_manager/llm_client.py`):
- `generate_with_tools(messages, tools) -> ToolResponse` — first pass; returns either `ToolResponse(text=...)` or `ToolResponse(tool_call=ToolCallRequest(name, arguments, call_id))`
- `complete_with_tool_result(messages, tool_call, tool_result, tools=None) -> str` — second pass; `tools` required by Anthropic for the follow-up call, ignored by others
- Ollama: primary path via `/api/chat` `tools` field (llama3.1+); falls back to JSON-in-prompt via `/api/generate` when the model rejects it
- 15 in-process tools live in `portf_server/chat_tools.py`; `execute_tool(name, args, db) -> str` is the dispatcher. Tools never make HTTP round-trips — they call DB/service functions directly.
- `EnhancedChatEngine._generate_with_tool_loop()` branches on `isinstance(self.llm, ToolCapableLLMClient)`; wraps all blocking calls (provider methods + `execute_tool`) in `asyncio.to_thread()`

`docker-compose.yml` sets `PORTF_LLM_PROVIDER=gemini` — these `environment:` entries override `.env*`; change the provider there, not only in `.env.local`.

All LLM calls: `LLMClient.generate(prompt) -> str`. `GeminiClient` is a legacy wrapper that delegates to `get_llm_client()`.

### CSV / File Parsers
Each broker has a standalone parser returning `LLMTransaction` objects via `ParseResult`. Valid `tx_type`: `buy`, `sell`, `dividend`, `interest`.
- `indexacapital_csv_parser.py` — semicolon CSV, European numbers, ISINs. Also auto-detects "Movimientos" cash statement → SEPA → deposit/withdrawal bookings; fund subscriptions skipped.
- `myinvestor_csv_parser.py` — "Movimientos Mi Cuenta": INVEST=deposit; `NAME @ QTY` with Importe<0=buy, >0=dividend; positive no-`@`=dividend lump-sum. No ISIN/fees → buys flagged "review".
- `myinvestor_paste_parser.py` — MyInvestor statements pasted as text.
- `mintos_csv_parser.py` — P2P statement. Aggregates interest/withholding **per month** into `interest` transactions vs synthetic `MINTOS` asset. Deposits/withdrawals kept individual → bookings. Large files → nginx `client_max_body_size 25m`.
- `coinbase_csv_parser.py` — Fiat Deposit/Withdrawal rows → bookings; staking income → `interest` tx (qty=1, price=EUR total). Returns `(previews, bookings, skipped)`.
- `generic_csv_parser.py` — Universal broker-agnostic CSV. Required cols: `date`, `symbol`, `type`, `quantity`, `price`, `currency`. Optional: `name`, `fees`, `asset_type`, `notes`. Case-insensitive multilingual headers (EN/ES/NL); type synonyms for buy/sell/dividend/interest; auto-detects delimiter and European/US decimal style. Template at `web_client/generic_import_template.csv`.
- `pdt_xlsx_parser.py` / `pdt_sheets_sync.py` — PDT XLSX and Google Sheets import/export.

### PDT Format (`pdt_xlsx_parser.py` and `pdt_sheets_sync.py`)
5 sheets with 3-row header block (machine keys / group labels / display labels), data from row 4: Transactions, Dividends, Bookings, Expenses, Settings.

`PDTXLSXParser.parse(path) → PDTParseResult` | `PDTXLSXExporter.export(db, path)` — writes all 5 sheets.
`PDTSheetsSync(sheet_id).pull() → PDTParseResult` | `PDTSheetsSync(sheet_id).push(db)` — reads/writes Google Sheet.

Google Sheets API: `UNFORMATTED_VALUE + SERIAL_NUMBER` for reading (dates = floats → `_serial_to_date`); `USER_ENTERED` for writing. Auth: `GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json`.

**PDT compatibility rules** (verified against live PDT API):
- Effect types: exactly `"Stock market"`, `"Crypto"`, `"Commodity"` (case-sensitive)
- Crypto/commodity assets: **must have empty exchange** — use `_pdt_exchange(exchange, asset_type)` helper
- Bookings display label (row 2 col 0): `" "` (single space, not `"Broker"`)
- Canonical exchange names: `"XETRA Exchange"`, `"Nasdaq"`, `"MyInvestor"`, `"Indexa Capital"`
- Dividend action always `"Cash"` (Cash/Stock/Staking subtype not stored separately)
- Only parse 3 sheets on import (Transactions/Dividends/Bookings); write all 5 on export/push.

### Import API (`portf_server/routers/imports.py`)
- `POST /api/v1/import/upload` — multipart file + `broker` field → parse preview (no DB write)
- `POST /api/v1/import/save` — `SaveRequest(transactions, bookings, portfolio_id, duplicate_action)` → write to DB
- `POST /api/v1/import/check-duplicates` — flag duplicates (no write); powers text/LLM preview
- Brokers: `indexacapital`, `myinvestor`, `mintos`, `coinbase`, `pdt`, `bookings`, `generic`
- `duplicate_action`: `skip` (default) | `add` | `overwrite`. `force=True` is legacy alias for `add`.
- `find_duplicate_transaction` is **time-aware**: matches date + time-of-day only when both rows carry one. `find_duplicate_booking` matches date+action+amount+currency+portfolio.

### Export API (`portf_server/routers/exports.py`)
- `GET /api/v1/export/csv` — all transactions as UTF-8 CSV (Excel BOM); `?portfolio_id=`
- `GET /api/v1/export/pdt` — PDT XLSX (5 sheets)
- `GET /api/v1/export/yahoo-finance?portfolio_id=&mode=transactions|positions` — Yahoo CSV (MM/DD/YYYY); assets without `ticker` skipped (X-Skipped-Count / X-Skipped-Symbols headers)
- `GET /api/v1/export/simply-wall-st?portfolio_id=&mode=transactions|positions` — SWS CSV (YYYY-MM-DD)
- Platform logic in `portf_manager/platform_export.py`.

### Bookings API
`GET|POST /api/v1/bookings/`, `DELETE /api/v1/bookings/{id}`. Importable via PDT (sheet/XLSX), generic `bookings` CSV, LLM extraction (`POST /api/v1/llm/extract-bookings`), or manual form.

### Rebalance API
`GET /api/v1/rebalance/targets`, `PUT /api/v1/rebalance/targets` (`[{asset_type, target_pct}]`), `GET /api/v1/rebalance/analysis`.

### Research API (`portf_server/routers/research.py` + `services/research.py`)

**Route order is critical** (FastAPI first-match): `portfolio-analysis/*` routes → `compare` → `alerts/check` → `/{symbol}/*`.

#### Portfolio Health (`GET /api/v1/research/portfolio-analysis`)
Plain `def`; gathers 6 data bundles via `ThreadPoolExecutor` → LLM prompt → 5 scored categories (`diversification`, `risk_adjusted_return`, `income`, `fees`, `tax_efficiency`, each 1–10 with reason) + `recommendations` + `summary`. Cached in `kv_cache` (`portf:advisor:all` or `portf:advisor:{portfolio_id}`). `cache_ttl_hours` via `GET|PUT /api/v1/research/portfolio-analysis/settings`.

`_fx(currency)` in `portfolio_advisor.py` lazy-imports `_get_fx_rate` from `portf_server.routers.portfolios` at call-time to avoid circular imports.

#### Workbench & Compare
- `GET /api/v1/research/{symbol}/lookup` — snapshot (no LLM); works for unheld tickers
- `POST /api/v1/research/{symbol}/generate` — web-augmented LLM → fair value, BUY/HOLD/SELL, confidence, risks, catalysts
- `compute_targets(fundamentals, method, assumptions)` — deterministic valuation (`pe`/`dividend_yield`); mirrored client-side for live recompute
- `POST /api/v1/research/{symbol}/save` — versioned `research_notes` row + pushes to `price_targets`
- `GET /api/v1/research/compare` — registered before `/{symbol}`
- `GET /api/v1/research/alerts/check` — price targets crossed vs latest prices (no Telegram send); Telegram sent by `~/scripts/portf-price-alerts.sh` at 20:05 via cron

### Analytics API (`portf_server/routers/analytics.py` + `services/analytics_service.py`)
- `GET /api/v1/analytics/performance?benchmark=^GSPC` — IRR, benchmark, `inception_date`, `cagr_pct`, `annualized_gain_eur`
- `GET /api/v1/analytics/networth-history` | `POST /api/v1/analytics/snapshot` | `POST /api/v1/analytics/backfill-snapshots[?force=]`
- `period_return` is a **time-weighted return** (chains daily returns, removes contributions via cost-basis delta)
- `GET /api/v1/analytics/tax-estimate?year=` — IRPF savings base (realised gains + dividends + interest); `irpf_savings_tax()` progressive brackets (19/21/23/27/28%)
- `GET /api/v1/analytics/diversification` — sector/country/currency/type + Herfindahl HHI (slow, fetches yfinance)
- `GET /api/v1/analytics/risk?benchmark=^GSPC` — max drawdown, volatility, Sharpe, `sortino_ratio`, `calmar_ratio`, `beta`, `alpha_pct`. Plain `def` (threadpool).
- `GET /api/v1/analytics/fees` | `GET /api/v1/analytics/tax-report?year=` (per-lot FIFO + withholding; all amounts converted to EUR via `_fx()`; each lot carries `currency`, `proceeds_eur`, `cost_basis_eur`, `gain_loss_eur`; **`TaxTransaction` uses `sell_quantity`/`sell_amount`/`purchase_amount` — not `quantity`/`proceeds`/`cost_basis`**)
- `GET /api/v1/analytics/data-freshness?stale_days=4` — price freshness + stale asset list; powers dashboard chip + alerts banner
- **Data Quality**: `GET /api/v1/analytics/dq/reconciliation|duplicates|suspicious` — pure DB checks (plain `def`). Powers Diagnostics page Data Quality tab. Dismissals in `localStorage["pfmDismissedIssues"]`.
- `services/tax_rates.py` — IRPF brackets; `GET /api/v1/public/summary` off unless `PORTF_PUBLIC_VIEW=true`
- Auth: `POST /api/v1/auth/login-key`
- Cron: `portf-price-alerts.sh` (20:05), `portf-monthly-report.sh` (1st of month 09:00)

### Watchlist / Goals / Sync APIs
- Watchlist: `GET|POST /api/v1/watchlist/`, `DELETE /api/v1/watchlist/{symbol}`, `GET /api/v1/watchlist/alerts/check`
- Goals: `GET|POST /api/v1/goals/`, `DELETE /api/v1/goals/{id}`; GET computes progress %, projected value, on-track flag, required monthly contribution
- Sync: `GET|PUT /api/v1/sync/pdt-config`, `POST pdt-pull`, `POST pdt-push`, `POST pdt-backup`. Resolution order for `spreadsheet_id`: query param → DB `app_settings` → `GOOGLE_SPREADSHEET_ID` env var.

### LLM API (`portf_server/routers/llm.py`)
- `POST /api/v1/llm/extract-transactions` | `POST /api/v1/llm/chat`
- `EnhancedChatEngine` uses **lazy initialization** — do NOT instantiate at module level (crashes without API key)
- Chat sessions: `GET|POST /api/v1/llm/chat/sessions`, `DELETE /api/v1/llm/chat/sessions/{id}`, `GET sessions/{id}/messages`
- `POST /api/v1/llm/chat` auto-creates `"New Chat"` session when `session_id` absent/unknown
- History stored in `chat_sessions.messages` column (not kv_cache)

### Auth
API key auth (`X-API-Key` header). `SERVER_API_KEY` env var is **auto-seeded** at startup (`app.py` lifespan) — no manual DB insert needed after container restart.

### Portfolio Resolver
`db.get_or_create_portfolio(name, base_currency="EUR")` — centralized helper; use instead of inline get/create pattern.

### Portfolios = Brokers (`portf_server/routers/portfolios.py`)
A portfolio doubles as a broker/account. `GET /api/v1/portfolios/` returns `website`, `description` (from `KNOWN_BROKERS` if not stored), `first/last_transaction_date`, `first/last_booking_date`. Portfolio queries alias `e.website AS entity_website` to avoid colliding with `p.website`. `DELETE /api/v1/portfolios/{id}/transactions?include_bookings=true`.

### Price Updates
Daily cron at **20:00 UTC** via `~/scripts/portf-update-prices.sh`. Manual CLI: `docker exec -e PORTF_API_KEY=... portf_backend_dev python3 -m portf_manager.cli update-prices`

On-demand via API (powers the dashboard "Refresh prices" button):
- `POST /api/v1/analytics/trigger-price-update` — starts a background thread; returns `{"status":"started"}` or 409 if already running
- `GET /api/v1/analytics/price-update-status` — returns `{"running": bool, "started_at": "..."}`
- Core logic in `portf_manager/services/price_updater.py::run_price_update(db)` — shared by CLI and API. Records each run in `price_update_runs`.

- **GBX**: yfinance returns UK stocks in GBX (pence). `fetch_latest_prices` auto-converts when `fast_info.currency == "GBp"` (÷100). Never store raw yfinance prices for UK ISINs without this check.
- **Crypto**: `{SYM}-EUR` format. Some tokens need `_CRYPTO_YF_OVERRIDES` (e.g. `SUI → ("SUI20947-USD", "USD")`); USD results converted to EUR before storing.
- Holdings EUR conversion via `XYZEUR=X` FX tickers.

### Market Data API (`portf_server/routers/market.py` + `portf_manager/market.py`)
Single market-data source for web, MCP, cron:
- `GET /api/v1/market/quotes?symbols=A,B,C&max_age=` (batch, ≤50), `/market/quote/{symbol}`, `/market/fx?currencies=`, `/market/fundamentals/{symbol}`
- kv_cache keys: `mkt:quote:*`, `mkt:fx:*`, `mkt:fund:*`. Stale-on-failure: last value with `stale: true`.
- **Read `previous_close` via subscript, never `fast_info.get()` with snake_case — it silently returns None.**
- All routers delegate to `portf_manager.market`; external scripts call the HTTP API.

### Transactions
`database.create_transaction()` requires `portfolio_id`. `asset_id` required — use `db.get_asset_by_symbol()` + `db.create_asset()`. Always pass `currency=` (the transaction's own currency) to preserve per-row currency.

### Positions & corporate actions (`portf_manager/positions.py`)
`compute_positions(transactions, key=...)` — single source of truth. Processes **chronologically**. Stock splits: `split` tx stores ratio in `quantity` (2-for-1=2.0; reverse=0.1), scales held quantity, cost unchanged. All holdings/analytics endpoints delegate to it.

## Web Client (`web_client/`)
Single `index.html` + four JS files (no build step), **must load in order**: `help_text.js` → `pfm_core.js` → `pfm_pages.js` → `pfm_analytics.js` → `pfm_features.js`. They share one global scope.

- `pfm_core.js`: prefs, `Fmt`, `esc`, `AssetSearch`, API + modal managers, `openChatWithContext()`
- `pfm_pages.js`: page/nav/auth, dashboard, transactions, assets, holdings, help/resources
- `pfm_analytics.js`: net-worth/dividend/analytics/diversification charts
- `pfm_features.js`: watchlist, goals, chat, portfolios, import/export, forecast, rebalance, research, settings + `DOMContentLoaded` bootstrap

`openChatWithContext(threadName, openingMessage)` — sets `window._chatPendingContext`, navigates to chat; used by Research ("Chat about this") and Portfolio Health ("Discuss with AI").

`window.METRIC_HELP` / `window.PAGE_HELP` in `help_text.js` — tooltip definitions and per-page help modal content. Add entries when adding new pages or non-obvious cards.

`makeSortableTable(config)` / `applyTableState(rows, columns, state)` in `pfm_core.js` — shared sortable/filterable tables; per-table state persists in `PREFS.tableState[<page>]`.

`window.PREFS` → `localStorage['pfmPrefs']`. Format numbers via `Fmt.num()`, dates via `Fmt.date()`. Theme = `data-bs-theme` on `<html>` (Bootstrap 5.3).

**Deploy after editing web files** (`portf_web` bakes files at build time — NOT live-mounted):
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

`saveImportedTransactions(transactions, bookings = [], portfolioId = null)` — always pass bookings array (even if empty) so PDT bookings are saved alongside transactions.

## Testing
- Unit tests: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` (635 passing, 6 skipped)
- JS tests: `make test-js` or `node --test web_client/js/tests/`
- Pre-push hook runs full unit suite automatically.
- F541 fixer: `uv run python scripts/fix_f541.py`
- Reset LLM singleton: `from portf_manager.llm_client import reset_llm_client`
- DB tests: `tests/test_database.py` — version assertion is `== 24` (bump with `DATABASE_VERSION`)

## Documentation (Default Behaviour)
When adding or changing any feature, always update **both**:
1. `PROJECT_STATUS.md` — bump the "Last updated" date and add the feature to the Recent summary line.
2. Relevant inline sections of `CLAUDE.md` — endpoint signatures, schema notes, key patterns, gotchas non-obvious from reading the code.

This is mandatory. A feature is not done until the docs reflect it.

## Privacy and Demo Data
Public repo — never commit real personal or financial data.

**Forbidden:** real API keys/tokens/passwords; real Spreadsheet IDs (use `YOUR_SPREADSHEET_ID`); real ISINs for held assets (use `US0000000001`/`LU0000000001`/`ES0000000001` family); real portfolio amounts/prices; home directory paths (`/home/agoldhoorn/` → use `~/`).

**OK:** well-known tickers (AAPL, BTC-EUR) as format examples; Apple's `US0378331005` in prompt templates; personal website/GitHub links in About page; fictional prices in test fixtures.

When writing tests, invent asset names (e.g. "Example Corp", "Global Bond Fund"). Same rule for plan docs under `docs/superpowers/`.

## Git
- Public repo `github.com:alexgoldhoorn/pfm` — develop on `main`. Push: `GIT_SSH_COMMAND="ssh -o IdentitiesOnly=no" git push origin main`.
- Use `git -P` or `GIT_PAGER=cat` (some git versions don't support `--no-pager` as a flag).
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`. Co-author: `Co-Authored-By: Oz <oz-agent@warp.dev>`.
- **Pre-commit**: black + flake8 + autoflake via `.pre-commit-config.yaml`. **Pre-push**: full unit suite.

## Important Gotchas
- **`_TX_COLS` uses f-strings**: queries in `database.py` are `f"""SELECT {self._TX_COLS}..."""`. The `f` prefix is load-bearing. Never run F541-fixers that touch triple-quoted strings on this file. Autoflake is safe; custom regex strippers are not.
- **Black + regex**: `f""` matches the first two chars of `f"""..."""`. Limit F541-fixers to `[^\n]` single-line patterns.
- **App always uses SQLite**: `app.py` falls back to `portfolio.db`. Container data at `/app/portfolio.db` inside `portf_backend_dev`.
- **Linting**: `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`. ~11 known warnings in `cli.py`/`portfolio_aware_agent.py`.
- **`sqlite3.Row` name collision**: never rely on `SELECT t.*, ..., COALESCE(t.col, other) AS col` — use explicit column list. First occurrence wins in dict.
- **Spanish tax**: FIFO cost basis; stocks/ETFs/bonds/funds = IRPF Box 27 ("rendimientos del capital mobiliario").
- **PDT XLSX**: openpyxl writes to a file path, not BytesIO. Always clean up with `os.unlink()`.
- **Env var prefixes**: `portf_server/settings.py` uses `PORTF_` prefix. Google vars (`GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SPREADSHEET_ID`) are NOT prefixed — read via `os.getenv()` in sync router and `pdt_sheets_sync.py`.
- **GBX normalization**: `portf_manager/currency_utils.normalize_gbx_amounts()` ÷100 on import (`imports.py` save + `sync.py` pull). Live price fetch normalizes separately in `api_client.py`. Missing this → cost basis 100× too high.
- **DB version bump**: update `assert version == N` in `tests/test_database.py`.
- **`asset_type` enum**: Pydantic (`portf_server/schemas/assets.py`) AND `models.py` — add new types to both or `/assets` 500s.
- **`transaction_type`**: `models.py TransactionType` + SQLAlchemy CHECK + `transactions` table rebuild — update all three when adding new types.
- **MyInvestor CSV**: semicolon-delimited, European numbers (comma=decimal, dot=thousands). No standalone parser — CLI or LLM text import.

## After Every Task — What Needs Restarting

| Change type | Action required |
|---|---|
| `web_client/` JS/HTML/CSS edited | `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web` |
| `web_client/nginx.conf` edited | Same as above (nginx config is baked into the image) |
| `portf_server/` or `portf_manager/` Python edited | `docker exec portf_backend_dev kill -HUP 1` |
| `DATABASE_VERSION` bumped / new migration added | `docker compose restart portf_backend_dev` (or HUP) |
| DB schema patched manually | No restart — note what was done |
| `docker-compose.yml` or `Dockerfile` edited | Full rebuild of affected service |
| No code changes (docs/tests only) | Nothing — say so explicitly |

Never leave the user guessing. If a change is already live, say that too.

## Status
See `PROJECT_STATUS.md` for full component status, pending work, and known issues.

## Issue Tracker
Tickets managed in **Todoist** → project **#Dev Projects / #pfm**.
