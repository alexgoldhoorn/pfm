# pfm — Portfolio Manager

[![Python](https://img.shields.io/badge/Python-3.13%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Style](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![Testing](https://img.shields.io/badge/Tests-394%20passing-brightgreen.svg)](https://pytest.org)

Python CLI + FastAPI server + web client for tracking stocks, ETFs, funds, bonds, crypto and commodities across multiple brokers. Features LLM-powered import, full Portfolio Dividend Tracker (PDT) v2 compatibility, Google Sheets sync, and Spanish IRPF tax reporting.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Installation](#installation)
4. [CLI Usage](#cli-usage)
5. [Server Mode](#server-mode)
6. [Import & Broker Support](#import--broker-support)
7. [PDT Format & Google Sheets Sync](#pdt-format--google-sheets-sync)
8. [LLM Integration](#llm-integration)
9. [Tax Reporting (Spanish IRPF)](#tax-reporting-spanish-irpf)
10. [Docker](#docker)
11. [Testing](#testing)
12. [Project Structure](#project-structure)
13. [Configuration](#configuration)
14. [Contributing](#contributing)

---

## Features

- **Multi-broker tracking**: IndexaCapital, Coinbase, PDT XLSX — parsers with European number/date format support
- **Full PDT v2 compatibility**: import/export Transactions, Dividends, and Bookings (deposits/withdrawals) with correct per-transaction currencies
- **Google Sheets sync**: pull from / push to a PDT-format Google Spreadsheet via service account
- **LLM-powered import**: paste any transaction text; Ollama, Gemini, or OpenRouter parses it automatically (extracts fees, dedupes duplicates, assigns to a portfolio)
- **Daily prices in EUR**: yfinance prices updated daily via cron, GBX→GBP normalized, all values FX-converted to EUR
- **Analytics**: dividend income (yield-on-cost, projected annual), performance (total return, money-weighted IRR, benchmark comparison, YTD/1M/1Y/all), net-worth-over-time chart, diversification + concentration (HHI), risk (drawdown/volatility/Sharpe), fee drag
- **Rebalancing calculator**: set target % per asset type → buy/sell actions to rebalance
- **Research & valuation agent**: yfinance fundamentals + LLM → fair value, BUY/HOLD/SELL, with per-position price-target alerts to Telegram
- **Watchlist**: track not-yet-owned tickers with buy-zone Telegram alerts
- **Goals / FIRE tracker**: target net worth + date with on-track projection
- **Wealth Simulator**: GBM wealth projection (cash/stocks/bonds + mortgage) with confidence bands
- **Tax reporting**: Spanish IRPF FIFO cost-basis export, savings-base tax estimate, per-lot realised-gains report, withholding summary, tax-loss-harvesting candidates
- **Telegram reports**: daily portfolio section + monthly summary + price/watchlist alerts
- **Public view**: optional shareable %-only page (no amounts), like PDT's public view
- **Authentication**: username/password login (web) + API-key (machine); external HTTPS via nginx-proxy-manager (`portfolio.example.com`)
- **Web client**: Bootstrap 5 responsive sidebar UI with per-page help + metric tooltips (static, no build step)
- **FastAPI backend**: REST API with API-key auth, 60+ endpoints
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
       └─────────────┘              └───────────────┘
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
python -m portf_manager sync-pdt-pull --sheet-id 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
python -m portf_manager sync-pdt-pull   # uses GOOGLE_SPREADSHEET_ID env var

# Push DB data to the Google Spreadsheet (overwrites Transactions/Dividends/Bookings sheets)
python -m portf_manager sync-pdt-push --sheet-id 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
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
| **Import file** | Upload IndexaCapital CSV, Coinbase CSV, or PDT XLSX. Parsed server-side, previewed with checkboxes. PDT imports also show bookings summary. |
| **Import text** | Paste any broker statement text. LLM extracts transactions, previewed before saving. |
| **Chat → Extract & Import** | Paste text in the Chat page, click "Extract & Import". |

Import/Export page also has direct **Pull/Push** buttons for Google Sheets sync.

### Supported broker formats

| Broker | Format | Module | CLI | Web |
|--------|--------|--------|-----|-----|
| IndexaCapital | CSV (`;` sep, ISIN, EUR) | `indexacapital_csv_parser.py` | ✅ | ✅ |
| MyInvestor | CSV (`;` sep, Spanish) | inline in `import_csv()` | ✅ | via text/LLM |
| Coinbase | CSV (Advanced Trade) | `coinbase_csv_parser.py` | ✅ | ✅ |
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

## LLM Integration

| Provider | Env var | Default model |
|---|---|---|
| Ollama | `OLLAMA_HOST` / `OLLAMA_PORT` | `llama3.2` |
| Gemini | `GEMINI_API_KEY` | `gemini-2.5-flash` |
| OpenRouter | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` |

Auto mode (default) tries: Ollama → Gemini → OpenRouter.

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

**Current status**: 394 passed, 6 skipped.

Key test files:
- `tests/test_pdt_xlsx_parser.py` — PDT XLSX parse/export roundtrip (42 tests)
- `tests/test_pdt_sheets_sync.py` — Google Sheets sync, all mocked (40 tests)
- `tests/unit/test_imports_exports.py` — server import/export/sync API (30 tests)
- `tests/test_database.py` — database CRUD + migrations

---

## Project Structure

```
pfm/
├── portf_manager/              # Core CLI package
│   ├── cli.py                  # 30+ commands (argparse)
│   ├── database.py             # SQLite layer + auto-migrations (v6)
│   ├── database_factory.py     # SQLite/PostgreSQL auto-detection
│   ├── models.py               # Data models
│   ├── llm_client.py           # Provider-agnostic LLM client
│   ├── pdt_xlsx_parser.py      # PDT XLSX import + export (all 3 sheets)
│   ├── pdt_sheets_sync.py      # PDT Google Sheets pull/push
│   ├── coinbase_csv_parser.py
│   ├── indexacapital_csv_parser.py
│   ├── google_sheets_export.py # Legacy custom-format Sheets export
│   ├── tax_calculator.py       # FIFO cost basis, IRPF export
│   ├── tax_export.py
│   ├── csv_export.py
│   └── auth.py
├── portf_server/               # FastAPI REST API
│   ├── app.py                  # Router registration
│   ├── routers/
│   │   ├── assets.py
│   │   ├── transactions.py
│   │   ├── portfolios.py
│   │   ├── bookings.py         # GET /bookings/, DELETE /bookings/{id}
│   │   ├── imports.py          # upload + save (transactions + bookings)
│   │   ├── exports.py          # CSV + PDT XLSX
│   │   ├── sync.py             # PDT Google Sheets pull/push
│   │   ├── llm.py
│   │   ├── tax.py
│   │   └── auth.py
│   ├── schemas/
│   └── auth_middleware.py
├── web_client/                 # Bootstrap 5 + Chart.js frontend (static)
│   ├── index.html
│   ├── js/portfolio_debug.js
│   └── css/
├── tests/
│   ├── test_pdt_xlsx_parser.py
│   ├── test_pdt_sheets_sync.py
│   ├── test_database.py
│   ├── unit/
│   │   ├── test_imports_exports.py
│   │   └── test_api_routers.py
│   ├── integration/
│   └── e2e/
├── service-account.json        # Google service account (gitignored in prod)
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
| `PORTF_LLM_PROVIDER` | `auto` | `auto` / `ollama` / `gemini` / `openrouter` |
| `PORTF_LLM_MODEL` | provider default | Model name override |
| `GEMINI_API_KEY` | — | Required for Gemini |
| `OPENROUTER_API_KEY` | — | Required for OpenRouter |
| `OLLAMA_HOST` | `localhost` | Ollama server host |
| `DATABASE_URL` | `sqlite:///portfolio.db` | PostgreSQL connection string |
| `PORTF_SERVER_URL` | `http://localhost:8000` | CLI server mode URL |
| `SERVER_API_KEY` | — | API key for all endpoints |
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
