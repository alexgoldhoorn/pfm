# Portfolio Manager — Project Status

> **Note:** `CLAUDE.md` is the authoritative, up-to-date project reference. This
> file is a periodic snapshot and its lower sections (Test Status, Pending Work,
> Data Import table) may lag the code — verify against `CLAUDE.md` and the
> codebase before relying on them.

Last updated: 2026-06-24

**Recent (v2.5.1):** **Housekeeping** — Portfolio column added to `list-transactions` CLI output (all three DB query paths now `LEFT JOIN portfolios`); `GeminiLLMClient` migrated from deprecated `google-generativeai` SDK to `google-genai` (`self._client = genai_sdk.Client(...)` at init, reused across all methods); test mocks updated to the new SDK pattern. 677 tests passing.

**Recent (v2.5):** **AI Chat: agentic tool calling** — `ToolCapableLLMClient` protocol + 15 in-process portfolio tools (`portf_server/chat_tools.py`: `get_holdings`, `get_performance`, `get_risk`, `get_diversification`, `get_kpis`, `get_health`, `get_brokers`, `get_quote`, `get_price`, `get_research`, `get_transactions`, `get_tax_estimate`, `asset_details`, `asset_news`, `financial_news`). All 4 LLM providers implement the protocol; `EnhancedChatEngine` branches on `isinstance(llm, ToolCapableLLMClient)` and runs a 2-pass agentic loop (compact context summary + live tool data) instead of the static snapshot path. Ollama gets native `/api/chat` tools + JSON-in-prompt fallback. 677 tests passing.

**Recent (v2.4):** **AI Chat: persistent named threads** — schema v24 (`chat_sessions` table with messages JSON column), DB-backed sessions replacing kv_cache. Four new endpoints: `GET|POST /api/v1/llm/chat/sessions`, `DELETE /api/v1/llm/chat/sessions/{id}`, `GET /api/v1/llm/chat/sessions/{id}/messages`. Two-column chat layout (sessions sidebar + message area); `openChatWithContext()` in `pfm_core.js` allows Research workbench and Portfolio Health panel to pre-load threads with on-screen data. 635 tests passing.

**Recent (v2.3.3):** **Bug fix — Monthly Cash Flow 500 error**: `monthly_cashflow` table was missing on the production DB (WAL checkpoint interrupted at first migration); added schema v23 recovery migration (`CREATE TABLE IF NOT EXISTS monthly_cashflow`) that re-creates the table on any DB where it is absent. 624 tests passing.

**Recent (v2.3.2):** **Goals edit** — `PUT /api/v1/goals/{id}` + `db.update_goal()`; pencil button on each goal card opens a Bootstrap modal pre-filled with the goal's current values. **Holdings & Assets broker filter** — both pages now have a Broker/Portfolio dropdown; Holdings re-fetches with `?portfolio_id=`; Assets cross-references holdings symbols for the selected portfolio. **Net-worth chart hover tooltip** — mousemove crosshair on the SVG chart shows a dark tooltip with date, portfolio value, and invested cost. 624 tests passing.

**Recent (v2.3.1):** **Bug fix — LLM import portfolio resolution**: when `portfolio_id` is explicitly supplied in the save request, the LLM-extracted `broker` field on transactions/bookings/deposits no longer calls `get_or_create_portfolio`, preventing duplicate portfolios with slightly different names (e.g. "MY INVESTOR" vs "MyInvestor"). Regression test added. 623 tests passing.

**Recent (v2.3):** **Asset Correlation Matrix** — `GET /api/v1/analytics/correlation?days=90` computes Pearson correlation from daily log-returns across held assets; `chartjs-chart-matrix` heatmap (red→white→green) added to the Risk & Diversification analytics tab. **Portfolio Comparison** — `GET /api/v1/analytics/portfolio-comparison` returns invested/value/return/IRR per broker; new Portfolios tab in Analytics with horizontal bar chart and per-broker detail cards. **PWA Push Notifications** — schema v22 (`push_subscriptions` table), VAPID keys auto-generated at startup in `app_settings`, new `/api/v1/notifications` router (`vapid-key` public; subscribe/unsubscribe authed), service worker push handler, Settings modal toggle; price-alerts cron (`alerts/check`) dispatches pushes to all registered browsers via `pywebpush`. **Diversification data quality** — `_resolve_sector_country()` helper in `portfolio_advisor.py` now uses `asset["ticker"]` (v18 column) for the yfinance lookup when `symbol` is an ISIN; crypto assets short-circuited to "Cryptocurrency"/"Global"; fund/index/bond asset-type defaults applied when yfinance returns nothing; cache key tied to the resolved yfinance symbol so ISIN-keyed and ticker-keyed lookups don't collide. 608 tests passing.

**Recent (v2.2):** **Portfolio Health Analysis** — LLM-powered scored health report on the Research page: 6 parallel data-gather threads (performance, risk, diversification, fees/dividends, tax, per-holding fundamentals), single structured prompt → 5 category scores (1–10) + prioritised recommendations + summary. `portf_manager/services/portfolio_advisor.py` (6 gather helpers, prompt builder, response parser). Three new endpoints: `GET /api/v1/research/portfolio-analysis`, `GET|PUT /api/v1/research/portfolio-analysis/settings` (cache TTL in `app_settings`). Results cached in `kv_cache` with user-configurable TTL (6h/24h/7d via Settings → Portfolio Advisor). `setupPortfolioHealth()` in `pfm_features.js`: idle/loading/result/error states, colour-coded score cards (Bootstrap success/warning/danger), animated status text, refresh button. 608 tests passing.

**Recent (v2.1):** **Advanced analytics metrics**: CAGR, Inception Date, Annualized Gain added to performance tab; Sortino, Calmar, Beta, Alpha added to risk tab (`?benchmark=` param); CAGR sub-line on dashboard Return card; 3Y/5Y period windows; 7 new `METRIC_HELP` tooltip entries. **search-grounded research** (Gemini `google_search` + Anthropic `web_search` tool; `SearchCapableLLMClient` protocol; graceful fallback to yfinance headlines when neither search provider is configured); **monthly cash flow tracker** (salary/income/mortgage/loan/rest entries on Net Worth page, net monthly figure, db v20); **Platform Export: Yahoo Finance + Simply Wall St CSV download** (transactions or positions, ticker-resolved, skip warning for ISIN-only assets); AI chat reads the real portfolio; research workbench (position panel, sell calculator, cost chart, downloadable report); analytics split into lazy tabs with a gain/loss leaderboard, dividend forward-income/calendar, and a per-lot tax report + CSV; dashboard alerts banner; `index` asset type; yfinance caching (`kv_cache`, schema v14); per-user settings (default currency/broker, holdings sort, hide-tiny, change password); grouped/collapsible sidebar with Help/About/Resources pages; stress-test endpoint + UI; **Data Quality tab on Diagnostics page** (cash reconciliation, fuzzy duplicate detection, suspicious pattern checks — inline delete/dismiss); **parser fixes** (Coinbase staking income → `interest` tx, MyInvestor `@QTY` positive = dividend not sell, Mintos keyword fixes, new `myinvestor_paste_parser.py`); **comprehensive help text** (`help_text.js` `PAGE_HELP`/`METRIC_HELP` covering all 14 pages + card-level ⓘ tooltips). Tests: 580 passing.

## Architecture Overview

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────┐
│  Web Client  │───▶│  FastAPI Server   │───▶│   SQLite /   │
│ (Bootstrap5) │    │  (portf_server/)  │    │  PostgreSQL  │
└─────────────┘    └──────────────────┘    └─────────────┘
                          ▲                       ▲
                          │                       │
                   ┌──────┴───────┐               │
                   │  CLI Client   │───────────────┘
                   │(portf_manager)│    (direct DB in local mode)
                   └──────────────┘
                          │
                   ┌──────┴───────┐
                   │  LLM Client   │
                   │ Gemini/Ollama │
                   └──────────────┘
```

## Components

### CLI (`portf_manager/`) — ✅ Working
- 25+ commands: asset/transaction/portfolio/entity CRUD, import, export, tax, chat, stock-report
- Local mode (direct SQLite) and server mode (`--server` + `--api-key`)
- Interactive REPL with tab completion (`portf` wrapper script)

### Backend (`portf_server/`) — ✅ Working
- FastAPI REST API with 25+ endpoints
- API key authentication
- Routers: auth, assets, transactions, portfolios, entities, sectors, LLM, tax
- Docker support with docker-compose

### Frontend (`web_client/`) — ✅ Working, Actively Maintained
- Bootstrap 5 + Chart.js, Vanilla JS, no build step
- 14+ pages: dashboard, holdings, transactions, analytics (tabbed), research, net worth, goals, forecast, import/export, watchlist, chat, diagnostics, and more
- API key + password login, dark/light theme, sortable/filterable tables, privacy blur
- PDT / Google Sheets sync, platform export (Yahoo Finance, Simply Wall St)
- Actively maintained; tested with Node.js built-in test runner (`make test-js`)

### Database — ✅ Working
- SQLite (default) + PostgreSQL support via database factory
- Schema v21 with automatic migrations on startup
- Tables: assets, transactions, portfolios, prices, bookings, dividends, watchlist, goals, research_notes, price_targets, networth snapshots, fixed_deposits, monthly_cashflow, app_settings, kv_cache, and more

### LLM Integration — ✅ Working
- Provider-agnostic abstraction (`llm_client.py`)
- **Default: auto-detect** — tries Ollama locally first (zero config), falls back to Gemini, then OpenRouter, then Anthropic
- Default models: `llama3.2` (Ollama), `gemini-2.5-flash` (Gemini), `claude-sonnet-4-6` (Anthropic)
- Config via `PORTF_LLM_PROVIDER` (`auto`/`ollama`/`gemini`/`openrouter`/`anthropic`) + `PORTF_LLM_MODEL` + `ANTHROPIC_API_KEY`
- Search-grounded research: Gemini/Anthropic implement `generate_with_search()`; research endpoint uses it when available, falls back to yfinance headlines
- Three use cases: transaction extraction, stock reports, chat/advisor

## Test Status

**580 passed, 0 failed, 6 skipped** (unit tests, excluding integration/e2e)

All tests passing as of 2026-06-18.

## Recent Changes (main)

See `git log --oneline` for full history. Key v2.1 additions:

1. **Monthly cash flow tracker** (db v20) — salary/income/mortgage/loan entries; net monthly figure on Net Worth page
2. **Platform export** — Yahoo Finance + Simply Wall St CSV (transactions or positions)
3. **Data Quality tab** on Diagnostics page — cash reconciliation, fuzzy duplicate detection, suspicious pattern checks
4. **Fixed deposits** (db v19) — fixed-term deposit tracking with maturity and interest-posting
5. **Bootstrap tabs** — Analytics and Import/Export pages migrated to Bootstrap nav-tabs
6. **Net Worth page** — manual assets (cash/property/pension/mortgage), fixed deposits, monthly cashflow combined with brokerage value
7. **Parser improvements** — Coinbase staking → interest tx, MyInvestor paste parser, Mintos keyword fixes

## Pending Work

### High Priority
- [ ] **Import real data** — DB is empty; need to import from actual broker accounts
- [ ] **MyInvestor structured parser** — Extract inline parsing from `import_csv()` into standalone `myinvestor_csv_parser.py` module (like IndexaCapital has)
- [ ] **Mintos parser** — CSV account statement parser for P2P loans, fractional bonds, and ETFs. Format: `Date`, `Details`, `Transaction ID`, `Turnover`. Mintos interest = same Spanish tax category as stock dividends (rendimientos del capital mobiliario, Box 27)

### Medium Priority
- [ ] **Price fetching** — `update-prices` command exists but prices table is empty; no scheduled updates
- [x] **Deprecated google.generativeai** — Migrated `GeminiLLMClient` to `google-genai` SDK; `self._client` created at init
- [x] **Untracked file** — Removed broken duplicate `calculator.py` from project root
- [x] **Portfolio column in list-transactions** — Portfolio column added to CLI table output

### Low Priority
- [ ] **Web client refresh** — Frontend not tested recently, may need updates for new endpoints
- [ ] **Generic CSV import template** — Support more broker formats without dedicated parsers
- [ ] **Scheduled price updates** — Cron/background job for automatic price fetching
- [ ] **Multi-currency support** — Tax reports assume single currency; need EUR/USD conversion

## Data Import Support

| Broker | Format | Parser | Status |
|---|---|---|---|
| IndexaCapital | CSV (semicolon, ISIN, EUR) | `indexacapital_csv_parser.py` | ✅ Working |
| MyInvestor | XLS via Inversis (semicolon, Spanish) | Inline in `import_csv()` | 🟡 Works but not modular |
| Coinbase | CSV | `coinbase_csv_parser.py` | ✅ Working |
| Mintos | CSV account statement | `mintos_csv_parser.py` | ✅ Working |
| Any broker | Free text (LLM) | `gemini_client.py` via `paste-transaction` | ✅ Working (needs API key or Ollama) |

## Tax Reporting

- FIFO cost basis calculation (`tax_calculator.py`)
- Per-portfolio filtering for per-broker tax filing (`--portfolio` flag)
- CSV export with long-term/short-term classification
- Relevant for Spanish IRPF: stocks, ETFs, and P2P lending interest all go in "rendimientos del capital mobiliario" (Box 27)

## Environment Variables

| Variable | Purpose | Required |
|---|---|---|
| `PORTF_LLM_PROVIDER` | LLM backend: `auto` (default), `ollama`, or `gemini` | No |
| `PORTF_LLM_MODEL` | Model name (e.g. `gemini-2.5-flash`, `llama3`) | No |
| `GEMINI_API_KEY` | Google Gemini API key | Only if provider=gemini |
| `OLLAMA_HOST` / `OLLAMA_PORT` | Ollama server address | No (defaults localhost:11434) |
| `PORTF_SERVER_URL` | Server URL for CLI server mode | No |
| `PORTF_API_KEY` | API key for server mode | No |
| `DATABASE_URL` | PostgreSQL connection string | No (defaults to SQLite) |
