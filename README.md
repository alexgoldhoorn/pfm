# pfm — Portfolio Manager

[![Python](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![Testing](https://img.shields.io/badge/Tests-705%20passing-brightgreen.svg)](https://pytest.org)

Python CLI + FastAPI server + web client for tracking stocks, ETFs, funds, bonds, crypto and commodities across multiple brokers. Features LLM-powered import, an agentic AI chat with live portfolio tools, an MCP server for AI assistants, full Portfolio Dividend Tracker (PDT) v2 compatibility, Google Sheets sync, and Spanish IRPF tax reporting.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Installation](#installation)
4. [CLI Usage](#cli-usage)
5. [Server Mode](#server-mode)
6. [Import & Broker Support](#import--broker-support)
7. [PDT Format & Google Sheets Sync](#pdt-format--google-sheets-sync)
8. [LLM Integration & AI Chat](#llm-integration--ai-chat)
9. [MCP Server](#mcp-server)
10. [Tax Reporting (Spanish IRPF)](#tax-reporting-spanish-irpf)
11. [Docker](#docker)
12. [Testing](#testing)
13. [Project Structure](#project-structure)
14. [Configuration](#configuration)
15. [Contributing](#contributing)

---

## Features

- **Multi-broker tracking**: IndexaCapital (trades + cash "Movimientos"), MyInvestor, Mintos (P2P), Coinbase, PDT XLSX, and a **generic CSV** for any broker — parsers with European number/date format auto-detection. Asset types: stocks, ETFs, a distinct **index fund** type, crypto, bonds, commodities, cash — plus first-class **interest** income (P2P/savings) that feeds the tax savings base
- **Full PDT v2 compatibility**: import/export Transactions, Dividends, and Bookings (deposits/withdrawals) with correct per-transaction currencies
- **Google Sheets sync**: pull from / push to a PDT-format Google Spreadsheet via service account
- **LLM-powered import**: paste any transaction text; Ollama, Gemini, OpenRouter, or Anthropic parses it automatically (extracts fees, dedupes duplicates, assigns to a portfolio)
- **Agentic AI chat**: ask about your portfolio in natural language using an agentic loop with 15 live in-process tools (holdings, performance, risk, diversification, tax, quotes, research, news). Persistent named chat sessions. All 4 LLM providers supported. Dark-mode aware
- **MCP server**: 18 tools exposing holdings, transactions, performance, dividends, risk, tax, research, watchlist, goals and more — connect any MCP-compatible AI assistant (Claude, etc.) to your live portfolio data
- **Daily prices in EUR**: yfinance prices updated daily via cron or on-demand from the dashboard "Refresh prices" button. GBX→GBP normalized, all values FX-converted to EUR. yfinance lookups (sector/country, fundamentals, news, benchmarks) are cached for speed
- **Analytics** (tabbed, lazy-loaded): performance (total return, money-weighted IRR, benchmark vs S&P/AEX/IBEX/CAC/FTSE/DAX, YTD/1M/1Y/all) + net-worth chart; dividend income with **forward income & calendar**; **gain/loss leaderboard** (top winners/losers by € and %); tax estimate + **per-lot tax report with CSV export**; diversification + concentration (HHI over holdings); risk (drawdown/volatility/Sharpe); fee drag
- **Research workbench**: ticker autocomplete, your position (cost basis, P/L, **sell calculator**, average-cost chart), Yahoo fundamentals with sources, LLM valuation (fair value, BUY/HOLD/SELL), and a **downloadable Markdown research report** per ticker; price-target alerts to Telegram
- **Rebalancing calculator**: set target % per asset type → buy/sell actions to rebalance
- **Alerts**: in-app dashboard banner + Telegram for price targets crossed and watchlist buy zones
- **Watchlist**: track not-yet-owned tickers with buy-zone alerts
- **Goals / FIRE tracker**: target net worth + date with on-track projection
- **Wealth Simulator**: GBM wealth projection (cash/stocks/bonds + mortgage) with confidence bands
- **Tax reporting**: Spanish IRPF FIFO cost-basis export, savings-base tax estimate, per-lot realised-gains report, withholding summary, tax-loss-harvesting candidates
- **Telegram reports**: daily portfolio section + monthly summary + price/watchlist alerts
- **Public view**: optional shareable %-only page (no amounts), like PDT's public view
- **Per-user settings** (browser-local): theme/dark mode, number & date format, default currency, default broker, holdings sort, hide-tiny-positions, benchmark, landing page; plus in-app **change password**
- **Authentication**: username/password login (web) + API-key (machine); external HTTPS via nginx-proxy-manager (`portfolio.example.com`)
- **Web client**: Bootstrap 5 responsive UI with a grouped, collapsible sidebar, per-page Help + glossary, About and Resources pages, and metric tooltips (static, no build step)
- **FastAPI backend**: REST API with API-key auth, 70+ endpoints
- **Dual-mode CLI**: direct SQLite access (local mode) or REST API (server mode)
- **PostgreSQL support**: drop-in via `DATABASE_URL` env var

---

## Architecture

```
┌─────────────┐    ┌──────────────────┐    ┌───────────────┐
│  Web Client  │───▶│  FastAPI Server   │───▶│  SQLite /     │
│ (Bootstrap5) │    │  (portf_server/)  │    │  PostgreSQL   │
└─────────────┘    └──────────────────┘    └───────────────┘
                          ▲                        ▲
                          │                        │
                   ┌──────┴───────┐                │
                   │  CLI Client   │────────────────┘
                   │(portf_manager)│  (direct DB in local mode)
                   └──────────────┘
                       │        │
              ┌────────┘        └────────────┐
       ┌──────┴──────┐              ┌────────┴──────┐
       │  LLM Client  │              │ Google Sheets  │
       │Ollama/Gemini │              │  PDT Sync      │
       │Anthropic/OR  │              └───────────────┘
       └─────────────┘
                          ▲
                          │ HTTP (MCP)
                   ┌──────┴───────┐
                   │  MCP Server   │
                   │  18 tools     │
                   │(mcp/server.py)│
                   └──────────────┘
```

---

## Installation

### Prerequisites

- Python 3.13
- [`uv`](https://github.com/astral-sh/uv) for virtual environment management

### Setup

```bash
git clone https://github.com/alexgoldhoorn/pfm.git
cd pfm
uv sync
```

### Environment Variables

Copy `.env.example` to `.env` and set relevant values:

```bash
# LLM provider (auto = tries Ollama → Gemini → OpenRouter)
PORTF_LLM_PROVIDER=auto
PORTF_LLM_MODEL=llama3.2

# Provider API keys
GEMINI_API_KEY=your_key_here
OPENROUTER_API_KEY=sk-or-...

# Optional: PostgreSQL
DATABASE_URL=postgresql://user:pass@localhost:5432/portf_db

# CLI server mode
PORTF_SERVER_URL=http://localhost:8000
SERVER_API_KEY=your_api_key

# Google Sheets PDT sync
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SPREADSHEET_ID=your_sheet_id_here   # optional default

# Database backup (~/scripts/portf-backup.sh, daily cron at 03:00)
PFM_BACKUP_DIR=/home/youruser/backups/pfm
PFM_BACKUP_KEEP=30   # days of retention
```

---

## CLI Usage

The CLI runs as `python -m portf_manager` or via the `portf` wrapper script (see [CLI Setup](docs/CLI_SETUP_README.md)).

### Portfolio & Asset Management

```bash
python -m portf_manager list-portfolios
python -m portf_manager add-portfolio "My Broker" --description "Example broker"

python -m portf_manager list-assets
python -m portf_manager add-asset US0378331005 "Apple Inc." stock --exchange "NASDAQ" --currency USD
```

### Transactions

```bash
# List (with optional filters)
python -m portf_manager list-transactions
python -m portf_manager list-transactions --portfolio "MyInvestor" --type dividend
python -m portf_manager list-transactions --start-date 2025-01-01 --end-date 2025-12-31
```

### Bookings (deposits & withdrawals)

```bash
python -m portf_manager list-bookings
python -m portf_manager list-bookings --portfolio "MyInvestor"
```

### PDT XLSX import / export

```bash
# Import all data from a PDT v2 XLSX file
python -m portf_manager import-pdt "Portfolio Dividend Tracker v2.xlsx"
python -m portf_manager import-pdt "Portfolio Dividend Tracker v2.xlsx" --import-bookings
python -m portf_manager import-pdt "Portfolio Dividend Tracker v2.xlsx" --no-dividends --portfolio "MyInvestor"

# Export all data back to PDT XLSX format
python -m portf_manager export-pdt
python -m portf_manager export-pdt --output my_portfolio.xlsx --portfolio "MyInvestor"
```

### Google Sheets PDT Sync

```bash
# Pull from a PDT-format Google Spreadsheet into DB
python -m portf_manager sync-pdt-pull --sheet-id YOUR_SPREADSHEET_ID
python -m portf_manager sync-pdt-pull   # uses GOOGLE_SPREADSHEET_ID env var

# Push DB data to the Google Spreadsheet (overwrites Transactions/Dividends/Bookings sheets)
python -m portf_manager sync-pdt-push --sheet-id YOUR_SPREADSHEET_ID
python -m portf_manager sync-pdt-push --portfolio "MyInvestor"
```

See [PDT Format & Google Sheets Sync](#pdt-format--google-sheets-sync) for setup instructions.

### Other Import

```bash
# CSV / paste import
python -m portf_manager import-csv "Movimientos Mi Cuenta MyInvestor.csv"
python -m portf_manager import-csv "IndexaCapital.csv" --portfolio "IndexaCapital"
python -m portf_manager paste-transaction   # LLM free-text (interactive)
python -m portf_manager paste indexacapital
python -m portf_manager paste coinbase
```

### Export

```bash
python -m portf_manager export-transactions          # CSV
python -m portf_manager extract-tax-report --start-date 2024-01-01 --end-date 2024-12-31
```

### Prices & Analysis

```bash
python -m portf_manager update-prices
python -m portf_manager chat
python -m portf_manager chat --once "What is my portfolio worth in EUR?"
```

---

## Server Mode

```bash
# Development (auto-reload)
python start_server.py --reload

# Production
python start_server.py --host 0.0.0.0 --workers 4
```

API docs: **Swagger UI** at http://localhost:8000/docs · **ReDoc** at http://localhost:8000/redoc

All endpoints require `X-API-Key` header. Create a key:
```bash
python create_api_key.py --username admin
```

---

## Import & Broker Support

### Web Import

Three ways to import from the **Transactions** page:

| Button | How it works |
|---|---|
| **Import file** | Upload IndexaCapital, MyInvestor, Mintos, Coinbase CSV or PDT XLSX. Parsed server-side, previewed with checkboxes. Imports show a bookings (cash deposits/withdrawals) summary too. |
| **Import text** | Paste any broker statement text. LLM extracts transactions, previewed before saving. |
| **Chat → Extract & Import** | Paste text in the Chat page, click "Extract & Import". |

Import/Export page also has direct **Pull/Push** buttons for Google Sheets sync.

### Supported broker formats

| Broker | Format | Module | CLI | Web |
|--------|--------|--------|-----|-----|
| IndexaCapital | CSV — ISIN trades export, **and** the "Movimientos" cash statement (SEPA → bookings; auto-detected) | `indexacapital_csv_parser.py` | ✅ | ✅ |
| MyInvestor | CSV "Movimientos Mi Cuenta" — deposits, dividends, buy/sell (flagged for review: no ISIN/fees) | `myinvestor_csv_parser.py` | ✅ | ✅ |
| Mintos (P2P) | CSV account statement — interest aggregated **per month** into `interest` income + withholding; loan/principal churn ignored | `mintos_csv_parser.py` | — | ✅ |
| Coinbase | CSV (Advanced Trade) | `coinbase_csv_parser.py` | ✅ | ✅ |
| **Any broker** | **Generic CSV** — canonical columns `date, symbol, type, quantity, price, currency` + optional `name, fees, asset_type, notes`. Case-insensitive multilingual headers (EN/ES/NL); delimiter, date style (EU/US), and decimal style (EU/US) auto-detected. Downloadable template in the web UI | `generic_csv_parser.py` | — | ✅ |
| PDT (Portfolio Dividend Tracker) | XLSX | `pdt_xlsx_parser.py` | ✅ | ✅ |
| PDT Google Sheets | Sheets API | `pdt_sheets_sync.py` | ✅ | ✅ |
| Any broker | Free-text (LLM) | `llm_client.py` | ✅ | ✅ |
| Cash deposits/withdrawals | Generic CSV (`date, action, amount, currency, broker`) | `bookings_csv_parser.py` | — | ✅ |

**Duplicate handling**: every import path (file, CSV, PDT, text/LLM) flags rows that already
exist and lets you choose **skip** (default), **import anyway**, or **overwrite**. Matching is
deterministic (asset + type + quantity + price + broker + date, time-aware when a statement
provides a time) — no LLM involved. Cash bookings are deduplicated too.

**Bookings** (cash transfers to/from a broker) can be added via PDT, the generic cash CSV,
free-text/LLM extraction, or the manual form on the Import/Export page.

---

## PDT Format & Google Sheets Sync

The app is fully compatible with the [Portfolio Dividend Tracker v2](https://portfoliodividendtracker.com/) format — the same Google Sheets / XLSX template used by the PDT website.

### What's tracked

| PDT sheet | Content | On import | On export |
|---|---|---|---|
| Transactions | Buy/sell orders | Saved to `transactions` table | Written with full costs/tax/currency |
| Dividends | Cash/stock/staking payouts | Saved to `transactions` table | Written with correct per-transaction currency |
| Bookings | Deposits and withdrawals | Saved to `bookings` table | Written with portfolio as broker |
| Expenses | Broker costs | Skipped (no Expenses feature) | Written as empty sheet (correct headers) |
| Settings | PDT API config | Skipped | Written with version 2.0 + API URL |

All values, currencies (per-transaction, not just per-asset), fees, and taxes are preserved. Compatibility verified against PDT's live API — broker names, exchange names, and effect types all match PDT's canonical values exactly.

### Google Sheets setup (one-time)

> **Google Sheets sync is optional.** It needs **two** things: a Google **service-account
> JSON key** (a secret credential you create once) *and* the **spreadsheet ID**. File
> import/export (XLSX/CSV) and everything else work without any of this.

**1. Create a service account and download its JSON key** (in the [Google Cloud Console](https://console.cloud.google.com/)):

   1. Create or select a project.
   2. Enable the **Google Sheets API** (APIs & Services → Library → "Google Sheets API" → Enable).
   3. APIs & Services → **Credentials** → *Create credentials* → **Service account**. Give it a name; no roles are required.
   4. Open the new service account → **Keys** → *Add key* → *Create new key* → **JSON**. A `.json` file downloads — **this is the secret**.

**2. Put the key where the app can find it:**

   ```bash
   # Save the downloaded file in the project root as service-account.json
   # (it is gitignored — never commit it). Then point the app at it:
   GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json   # in .env / .env.local
   ```

**3. Share your Google Sheet with the service account.** Open the JSON and copy the
   `client_email` value (looks like `name@project-id.iam.gserviceaccount.com`). In your
   PDT Google Sheet click **Share**, add that email, and grant **Editor** access.

   > Tip: once the key is configured, `GET /api/v1/sync/pdt-config` returns the exact
   > `service_account_email` to share with — and the web **Import/Export → Google Sheets**
   > card shows it for you, along with a green/red "configured" badge.

**4. Set the spreadsheet ID** (the long ID in the sheet URL, `/spreadsheets/d/`**`ID`**`/`):

   ```bash
   GOOGLE_SPREADSHEET_ID=your_id_here   # optional default; or pass --sheet-id (CLI) / enter it in the web UI
   ```

Once the JSON key is in place and the sheet is shared, use the Pull/Push buttons on the
**Import/Export** page, the CLI commands below, or the sync API endpoints.

### Sync endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/sync/pdt-config` | Service account status + default sheet ID |
| `POST` | `/api/v1/sync/pdt-pull?spreadsheet_id=` | Sheet → DB |
| `POST` | `/api/v1/sync/pdt-push?spreadsheet_id=` | DB → Sheet |

---

## Export

### CLI

```bash
python -m portf_manager export-pdt portfolio.xlsx     # Full PDT XLSX (all 3 sheets)
python -m portf_manager sync-pdt-push                 # Push to Google Sheet
python -m portf_manager export-transactions           # CSV
python -m portf_manager extract-tax-report ...        # IRPF tax CSV
```

### Web API / Browser

| Endpoint | Result |
|---|---|
| `GET /api/v1/export/csv` | All transactions as UTF-8 CSV (BOM for Excel) |
| `GET /api/v1/export/pdt` | Full PDT XLSX (Transactions + Dividends + Bookings) |
| `GET /api/v1/export/backup` | Download a consistent SQLite snapshot (`.db` file) |
| `POST /api/v1/system/restore` | Upload a `.db` or `.db.gz` backup to replace the live DB (validates schema version; auto-saves a pre-restore snapshot to `PFM_BACKUP_DIR` if set) |
| `DELETE /api/v1/portfolios/{id}/transactions` | Delete all transactions for a broker (bulk clear; use before re-importing to fix date inconsistencies) |
| `GET /api/v1/bookings/` | List all bookings (JSON) |
| `POST /api/v1/sync/pdt-push` | Write to Google Sheet |

---

## Analytics & Investing Tools

Web pages: **Dashboard**, **Analytics**, **Holdings**, **Watchlist**, **Goals**,
**Wealth Simulator**, plus **Portfolios** (per-broker values + totals). Each page
has an `(i)` help button and metric tooltips.

| Endpoint | Result |
|---|---|
| `GET /api/v1/portfolios/values` | Per-portfolio EUR value, cost, P&L + grand total |
| `GET /api/v1/analytics/dividends` | Income by year/month/symbol, TTM, yield-on-cost |
| `GET /api/v1/analytics/performance?period=ytd\|1m\|1y\|all&benchmark=^GSPC` | Total return, money-weighted IRR, period return, benchmark comparison |
| `GET /api/v1/analytics/networth-history` | Daily value/cost snapshots for the chart |
| `GET /api/v1/analytics/diversification` | Sector/country/currency/type % + Herfindahl HHI |
| `GET /api/v1/analytics/risk` | Max drawdown, volatility, Sharpe (from snapshots) |
| `GET /api/v1/analytics/fees` | Fees + tax per broker, fee drag % |
| `GET /api/v1/analytics/tax-estimate?year=` | IRPF savings-base estimate + harvest candidates |
| `GET /api/v1/analytics/tax-report?year=` | Per-lot FIFO realised gains + dividend withholding |
| `GET/PUT /api/v1/rebalance/targets`, `GET /api/v1/rebalance/analysis` | Allocation targets + buy/sell actions to rebalance |
| `GET /api/v1/research/{symbol}`, `POST .../generate` | LLM fair value, BUY/HOLD/SELL, risks; `.../targets` for buy/sell alerts |
| `GET/POST/DELETE /api/v1/watchlist/` | Watched tickers with buy-zone alerts |
| `GET/POST/DELETE /api/v1/goals/` | FIRE/savings goals with on-track projection |
| `GET /api/v1/public/summary` | %-only shareable summary (off unless `PORTF_PUBLIC_VIEW=true`) |

### Scheduled jobs (cron, see `~/scripts/`)
- **20:00** daily — update prices (yfinance) + record a net-worth snapshot
- **20:05** daily — price-target + watchlist buy-zone Telegram alerts
- **1st of month 09:00** — monthly portfolio summary to Telegram

### Authentication & external access
- Web login: username/password (`POST /api/v1/auth/login-key`) or API key.
- The web client calls the API **same-origin** (`/api` proxied by nginx), so it
  works on the LAN and behind HTTPS. To expose externally as
  `portfolio.example.com`, see [`docs/EXTERNAL_ACCESS.md`](docs/EXTERNAL_ACCESS.md).

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the phased feature history.

---

## LLM Integration & AI Chat

### Providers

| Provider | Env var | Default model |
|---|---|---|
| Ollama (local) | `OLLAMA_HOST` / `OLLAMA_PORT` | `llama3.2` |
| Gemini | `GEMINI_API_KEY` | `gemini-2.5-flash` |
| OpenRouter | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |

Auto mode (default) tries in order: **Ollama → Gemini → OpenRouter → Anthropic**. Override with `PORTF_LLM_PROVIDER=ollama|gemini|openrouter|anthropic` and `PORTF_LLM_MODEL=<model>`.

### Agentic chat (tool-calling loop)

The AI chat page uses an agentic two-pass loop: all 4 providers implement a `ToolCapableLLMClient` protocol with 15 in-process portfolio tools:

| Tool | What it returns |
|---|---|
| `get_holdings` | All positions with cost basis, current value, P/L |
| `get_performance` | IRR, period return, benchmark comparison |
| `get_risk` | Drawdown, volatility, Sharpe, Sortino, Beta, Alpha |
| `get_diversification` | Sector/country/currency breakdown + HHI |
| `get_kpis` | Dashboard KPIs (total value, invested, unrealised gain) |
| `get_health` | AI-scored portfolio health (5 categories) |
| `get_brokers` | Per-broker values and returns |
| `get_quote` | Live market quote for a symbol |
| `get_price` | Latest stored price for a held asset |
| `get_research` | Saved research notes and valuation |
| `get_transactions` | Recent transactions (filterable) |
| `get_tax_estimate` | IRPF savings-base estimate for a year |
| `asset_details` | Full asset metadata + position |
| `asset_news` | Recent news headlines for a symbol |
| `financial_news` | General market/financial news |

Tools never make HTTP round-trips — they call DB/service functions directly. Chat history is persisted as named sessions (DB v24).

### Search-grounded research

Gemini and Anthropic providers implement `generate_with_search()` — the Research Workbench uses web search to ground LLM valuations. Ollama/OpenRouter fall back to yfinance headlines.

---

## MCP Server

The `mcp/server.py` file exposes your portfolio data as **18 MCP tools** so any MCP-compatible AI assistant (Claude Desktop, Claude Code, etc.) can query your live portfolio directly.

### Available tools

| Tool | Description |
|---|---|
| `portfolio_holdings` | Full position list with cost, value, P/L per broker |
| `list_portfolios` | All brokers/portfolios with totals |
| `list_assets` | Assets (filterable by type) |
| `list_transactions` | Transactions (filterable by portfolio/type/date) |
| `quote` | Live market quote(s) for any symbols |
| `performance` | IRR, period return, benchmark |
| `dividends` | Income history by year/month/symbol |
| `diversification` | Sector/country/currency breakdown + HHI |
| `risk` | Drawdown, volatility, Sharpe, Sortino, Beta, Alpha |
| `fundamentals` | P/E, EPS, market cap, dividend yield |
| `research_lookup` | Snapshot + saved research notes for a symbol |
| `research_compare` | Side-by-side comparison of all researched assets |
| `watchlist` | Watched tickers with buy-zone alerts |
| `portfolio_health` | AI-scored 5-category health report (from cache) |
| `tax_estimate` | IRPF savings-base estimate + harvest candidates |
| `tax_report` | Per-lot FIFO realised gains + dividend withholding |
| `goals` | FIRE/savings goals with on-track progress |
| `bookings` | Cash deposits and withdrawals |

### Setup

The server reads credentials from `~/repos/pfm/.env.local` at startup. Register it in your MCP client config:

```json
{
  "mcpServers": {
    "pfm": {
      "command": "uv",
      "args": ["run", "python", "~/repos/pfm/mcp/server.py"]
    }
  }
}
```

The `mcp/` directory is also symlinked from `~/mcp/pfm/` so existing Claude registrations that point at `~/mcp` keep working unchanged.

---

## Tax Reporting (Spanish IRPF)

FIFO cost-basis, compatible with Spanish IRPF Box 27 (_rendimientos del capital mobiliario_):

```bash
python -m portf_manager extract-tax-report \
  --start-date 2024-01-01 --end-date 2024-12-31

python -m portf_manager extract-tax-report \
  --portfolio "IndexaCapital" --output tax_indexa_2024.csv
```

---

## Docker

```bash
docker-compose up -d        # server + PostgreSQL
docker compose --profile dev up --build   # dev with auto-reload
```

---

## Testing

```bash
# Unit tests (fast, no network)
uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e

# With coverage
uv run pytest tests/ --cov=portf_manager --cov-report=term-missing
```

**Current status**: 705 passed, 6 skipped.

Key test files:
- `tests/test_pdt_xlsx_parser.py` — PDT XLSX parse/export roundtrip
- `tests/test_pdt_sheets_sync.py` — Google Sheets sync, all mocked
- `tests/unit/test_generic_csv_parser.py` — generic CSV parser (date/decimal style, asset_type pass-through)
- `tests/unit/test_imports_exports.py` — server import/export/sync API
- `tests/unit/test_api_routers.py` — all routers incl. tax-report FIFO shape assertion
- `tests/test_database.py` — database CRUD + migrations (schema v24)
- `tests/test_cli_update_prices.py` — CLI update-prices via shared service

---

## Project Structure

```
pfm/
├── portf_manager/              # Core CLI package
│   ├── cli.py                  # 30+ commands (argparse)
│   ├── database.py             # SQLite layer + auto-migrations (schema v24)
│   ├── database_factory.py     # SQLite/PostgreSQL auto-detection
│   ├── models.py               # Enums: AssetType, TransactionType
│   ├── llm_client.py           # Provider-agnostic LLM client (Ollama/Gemini/OpenRouter/Anthropic)
│   ├── llm_types.py            # LLMTransaction dataclass used by all parsers
│   ├── tax_calculator.py       # FIFO cost basis, IRPF export
│   ├── positions.py            # compute_positions() — single source of truth for holdings
│   ├── market.py               # yfinance wrapper (quotes, FX, fundamentals, kv_cache)
│   ├── parsers/
│   │   ├── generic_csv_parser.py       # Universal broker CSV (auto-detects delimiter/date/decimal style)
│   │   ├── indexacapital_csv_parser.py
│   │   ├── myinvestor_csv_parser.py
│   │   ├── mintos_csv_parser.py
│   │   ├── coinbase_csv_parser.py
│   │   ├── pdt_xlsx_parser.py          # PDT XLSX import + export (5 sheets)
│   │   └── pdt_sheets_sync.py          # PDT Google Sheets pull/push
│   └── services/
│       ├── price_updater.py            # run_price_update() — shared by CLI and API
│       ├── analytics_service.py
│       ├── portfolio_advisor.py        # AI-scored portfolio health
│       └── research.py
├── portf_server/               # FastAPI REST API (70+ endpoints)
│   ├── app.py                  # Lifespan, router registration, API key seed
│   ├── routers/
│   │   ├── analytics.py        # Performance, risk, tax, diversification, snapshots
│   │   ├── assets.py
│   │   ├── transactions.py
│   │   ├── portfolios.py
│   │   ├── bookings.py
│   │   ├── imports.py          # File upload + save (transactions + bookings)
│   │   ├── exports.py          # CSV, PDT XLSX, Yahoo Finance, Simply Wall St
│   │   ├── sync.py             # PDT Google Sheets pull/push
│   │   ├── llm.py              # Chat (agentic loop), transaction extraction, sessions
│   │   ├── research.py         # Workbench, portfolio health, compare, alerts
│   │   ├── market.py           # Quotes, FX rates, fundamentals (cached)
│   │   └── auth.py
│   ├── chat_tools.py           # 15 in-process portfolio tools for the agentic chat loop
│   ├── schemas/
│   └── auth_middleware.py
├── web_client/                 # Bootstrap 5 + Chart.js frontend (no build step)
│   ├── index.html
│   ├── js/
│   │   ├── help_text.js        # PAGE_HELP / METRIC_HELP — all 14 pages documented
│   │   ├── pfm_core.js         # Prefs, Fmt, AssetSearch, API/modal managers
│   │   ├── pfm_pages.js        # Nav, dashboard, transactions, assets, holdings
│   │   ├── pfm_analytics.js    # Net-worth/dividend/analytics/diversification charts
│   │   └── pfm_features.js     # Watchlist, goals, chat, portfolios, import/export, research
│   └── css/
├── mcp/
│   └── server.py               # 18 MCP tools backed by the FastAPI server
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── start_server.py
├── portf                       # Shell wrapper for CLI
├── Makefile
├── docker-compose.yml
└── pyproject.toml
```

---

## Configuration

Key env vars:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORTF_LLM_PROVIDER` | `auto` | `auto` / `ollama` / `gemini` / `openrouter` / `anthropic` |
| `PORTF_LLM_MODEL` | provider default | Model name override |
| `GEMINI_API_KEY` | — | Required for Gemini |
| `OPENROUTER_API_KEY` | — | Required for OpenRouter |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic |
| `OLLAMA_HOST` | `localhost` | Ollama server host |
| `OLLAMA_PORT` | `11434` | Ollama server port |
| `DATABASE_URL` | `sqlite:///portfolio.db` | PostgreSQL connection string |
| `PORTF_SERVER_URL` | `http://localhost:8000` | CLI server mode URL |
| `SERVER_API_KEY` | auto-seeded | API key for all endpoints (auto-generated on first start) |
| `PORTF_PUBLIC_VIEW` | `false` | Enable %-only shareable public summary page |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | `service-account.json` | Google service account JSON path |
| `GOOGLE_SPREADSHEET_ID` | — | Default PDT sync spreadsheet ID |

---

## Contributing

1. Fork and create a feature branch off `develop`
2. Code style: `black` (line 88), Google-style docstrings, type hints everywhere
3. Run tests: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
4. Commit: conventional format (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`)
5. Open a pull request against `develop`

---

## License

MIT — see [LICENSE](LICENSE).
