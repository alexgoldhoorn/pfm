# Portfolio Manager — Project Status

> **Note:** `CLAUDE.md` is the authoritative, up-to-date project reference. This
> file is a periodic snapshot and its lower sections (Test Status, Pending Work,
> Data Import table) may lag the code — verify against `CLAUDE.md` and the
> codebase before relying on them.

Last updated: 2026-06-16

**Recent (v2.1):** AI chat reads the real portfolio; research workbench (position panel, sell calculator, cost chart, downloadable report); analytics split into lazy tabs with a gain/loss leaderboard, dividend forward-income/calendar, and a per-lot tax report + CSV; dashboard alerts banner; `index` asset type; yfinance caching (`kv_cache`, schema v14); per-user settings (default currency/broker, holdings sort, hide-tiny, change password); grouped/collapsible sidebar with Help/About/Resources pages; stress-test endpoint + UI; **Data Quality tab on Diagnostics page** (cash reconciliation, fuzzy duplicate detection, suspicious pattern checks — inline delete/dismiss); **parser fixes** (Coinbase staking income → `interest` tx, MyInvestor `@QTY` positive = dividend not sell, Mintos keyword fixes, new `myinvestor_paste_parser.py`); **comprehensive help text** (`help_text.js` `PAGE_HELP`/`METRIC_HELP` covering all 14 pages + card-level ⓘ tooltips). Tests: 524 passing.

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

### Frontend (`web_client/`) — ⚠️ Exists, Not Actively Maintained
- Bootstrap 5 + Chart.js dashboard
- Vanilla JS, no build step
- API key login, asset/transaction views
- Needs running server to function
- Not tested recently

### Database — ✅ Working, Empty
- SQLite (default) + PostgreSQL support via database factory
- Schema v4 with automatic migrations
- Tables: users, assets, transactions, portfolios, entities, prices, portfolio_config
- **Currently empty** — no real data imported yet

### LLM Integration — ✅ Working
- Provider-agnostic abstraction (`llm_client.py`)
- **Default: auto-detect** — tries Ollama locally first (zero config), falls back to Gemini
- Default model: `llama3.2` (Ollama) or `gemini-2.5-flash` (Gemini)
- Config via `PORTF_LLM_PROVIDER` (`auto`/`ollama`/`gemini`) + `PORTF_LLM_MODEL` env vars
- Three use cases: transaction extraction, stock reports, chat/advisor

## Test Status

**429 passed, 0 failed, 0 errors, 6 skipped** (unit tests, excluding integration/e2e)

All tests passing as of 2026-06-10.

## Recent Changes (develop branch)

1. **LLM abstraction** (commit `59c59f7`) — Provider-agnostic `LLMClient` protocol with Gemini + Ollama support
2. **Portfolio-level reporting** (commit `732f0e9`) — `--portfolio` filter on `portfolio-value`, `list-transactions`, `extract-tax-report`

## Pending Work

### High Priority
- [ ] **Import real data** — DB is empty; need to import from actual broker accounts
- [ ] **MyInvestor structured parser** — Extract inline parsing from `import_csv()` into standalone `myinvestor_csv_parser.py` module (like IndexaCapital has)
- [ ] **Mintos parser** — CSV account statement parser for P2P loans, fractional bonds, and ETFs. Format: `Date`, `Details`, `Transaction ID`, `Turnover`. Mintos interest = same Spanish tax category as stock dividends (rendimientos del capital mobiliario, Box 27)
- [x] **Fix pre-existing test failures** — Fixed Google Sheets mock, LLM router tests, chat engine init (224/224 passing)
- [ ] **Merge develop → main** — All new work is on `develop` branch

### Medium Priority
- [ ] **Price fetching** — `update-prices` command exists but prices table is empty; no scheduled updates
- [ ] **Deprecated google.generativeai** — `stock_report.py` still imports old SDK directly (should use `llm_client.py`)
- [x] **Untracked file** — Removed broken duplicate `calculator.py` from project root
- [ ] **Portfolio column in list-transactions** — Show which portfolio each transaction belongs to in the output table

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
