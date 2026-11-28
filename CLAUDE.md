# CLAUDE.md — Project Rules for AI Assistants

## Project Overview
Portfolio Manager: a Python CLI + FastAPI server + web client for tracking stocks, ETFs, funds, bonds, crypto and commodities across multiple brokers, with LLM-powered import, file import/export, web chat, Spanish IRPF tax reporting, and Google Sheets PDT sync.

## Hard rules
- ⚠️ **`~/repos/pfm` is the live checkout.** `portf_backend_dev` runs `uvicorn --reload` on it (bind-mounted at `/app`), half-written code and migrations included. Develop in a separate git worktree, never in `~/repos/pfm`.
- **Public repo: never commit real personal or financial data.** Forbidden: real API keys/tokens/passwords; real Spreadsheet IDs (use `YOUR_SPREADSHEET_ID`); real ISINs for held assets (use `US0000000001`/`LU0000000001`/`ES0000000001` family); real portfolio amounts/prices; home directory paths (`/home/agoldhoorn/` → use `~/`). <!-- allow-financial: this line must quote the forbidden pattern --> Invent asset names in tests and plan docs ("Example Corp"). Enforced by the `scripts/check_no_real_financials.py` pre-commit hook; mark a genuine exception with an `allow-financial` comment on the line. Details, what is OK, and how to scrub history: `docs/claude-reference/privacy.md`.
- **Migrations:** a new table goes in BOTH `_create_all_tables` (fresh DBs) AND `_migrate_to_vN` (existing DBs). CHECK-constraint rebuilds need `PRAGMA legacy_alter_table=ON` around the `RENAME` (see `_migrate_to_v13`). The next reload migrates the **live** DB.
- **LLM failures are errors, never an empty result** (raise `LLMError`, routers return 502; never a fallback that looks like "nothing found").
- **The MCP server (`mcp/pfm_mcp/`) is re-exported by the internet-facing gateway.** Before changing it or an API response shape it reads, read `docs/claude-reference/mcp-server.md`.

## Code Style
- **black** (line length 88): `uv run black <file>`.
- Comments go on the **line before** the code they describe, not inline.
- Type hints on all function signatures. Google-style docstrings.

## Python
- Python **3.13**, **uv** (`uv sync`). Deps in `pyproject.toml`, dev deps under `[tool.uv] dev-dependencies`, lockfile `uv.lock`.
- The `.venv` is root-owned (Docker-created); run tooling with `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …`.
- Lint: `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` — 0 warnings, keep it that way.

## Architecture
- `portf_manager/` — core package: CLI, models, database, parsers, LLM client, tax calculator.
- `portf_server/` — FastAPI REST API (routers, schemas, auth middleware).
- `web_client/` — vanilla JS + Bootstrap 5 frontend, no build step.
- `mcp/` — read-only MCP tools over the HTTP API (own tests: `cd mcp && python3 -m pytest -q`).
- `tests/` — `unit/`, `integration/`, `e2e/`.
- `docs/features/` — per-feature reference. `docs/claude-reference/` — detail moved out of this file. `docs/superpowers/` — specs and plans. `docs/api-specification/` is the original 2025 design spec (JWT, React SPA — never built), kept for history; only `portfolio-management-api.md` there is maintained.

## Key Patterns
- **Database:** SQLite by default (`portfolio.db`), PostgreSQL via `DATABASE_URL`; `database_factory.get_database_adapter(url)` raises on an unknown scheme (no silent fallback). **Current schema version: 47**; migrations run on startup. Before adding a migration or touching the schema, read `docs/claude-reference/database-and-migrations.md` (per-version history).
- **Transaction SELECTs** use an explicit column list with `COALESCE(t.currency, a.currency) AS currency`, never `t.*` (`sqlite3.Row` takes the first of two same-named columns). `_TX_COLS` queries are f-strings — the `f` is load-bearing; never run F541-fixers on triple-quoted strings in `database.py` (`uv run python scripts/fix_f541.py` is the safe one).
- **New `asset_type` value:** add to BOTH Pydantic (`portf_server/schemas/assets.py`) AND the `models.py` enum, or `/assets` 500s. **New `transaction_type`:** `models.py TransactionType` + SQLAlchemy CHECK + `transactions` table rebuild.
- **Transactions:** `create_transaction()` needs `portfolio_id` and `asset_id` (`get_asset_by_symbol` / `create_asset`), and always `currency=`. Use `db.get_or_create_portfolio(name, base_currency="EUR")`.
- **Positions:** `portf_manager/positions.py` `compute_positions()` is the single source of truth (chronological; splits scale quantity, cost unchanged).
- **Blocking I/O endpoints** (yfinance) are plain `def`, not `async`.
- **Auth:** `X-API-Key`; `SERVER_API_KEY` is auto-seeded at startup.
- **LLM:** `get_llm_client(tier="fast"|"main"|"heavy")`, never a model slug; no `temperature` on Claude 5. `docker-compose.yml` sets `PORTF_LLM_PROVIDER` under `environment:`, overriding `.env*`. Never instantiate `EnhancedChatEngine` at module level. Only `configure_logging()` configures logging. Model Markdown is untrusted (`safeMarkdownHtml`). Before touching LLM/chat code, read `docs/features/llm.md` (its "Rules summary" section).
- More patterns and gotchas (auth caching, GBX ÷100, ISIN→ticker, `auto_price`, Spanish tax FIFO, PDT XLSX, env prefixes, compose interpolation, European number parsing, MyInvestor CSV): read `docs/claude-reference/core-patterns-and-gotchas.md` before touching imports, prices, tax or settings.

## Feature reference
**Before editing an area, read its doc in `docs/features/`**, especially its closing "Must-know gotchas before editing" section.

| Area | Doc |
|---|---|
| Broker parsers, PDT, import/export, bookings | `imports-exports.md` |
| Research, Portfolio Health, rebalance planner, caps | `research-rebalance.md` |
| Analytics, fund look-through, risk, tax report, PDF reports | `analytics.md` |
| Net worth, balance sheet, loans, retirement, portfolios, goals, sync | `networth-portfolios.md` |
| Loans & Mortgage page, reconciliation, what-if | `mortgage.md` |
| Wealth Simulator (projection exists in JS and Python — change both) | `wealth-simulator.md` |
| Spending (bank accounts) | `spending.md` |
| Budgeting | `budgeting.md` |
| Cash-flow forecast | `cashflow-forecast.md` |
| Action Items, Diagnostics follow-up | `action-items.md` |
| Decision journal | `decisions.md` |
| What-if scenarios (job loss, income, house, crash) | `scenarios.md` |
| Next-euro allocator | `allocator.md` |
| Price updates, market data | `prices-market.md` |
| LLM, chat, app logs | `llm.md` |
| Web client | `web-client.md` |
| Metrics, error tracking, traces | `observability.md` |

Cross-cutting rule: route ordering — single-segment GETs (`/summary`, `/prefill`, `/detect`, `/scorecard`, …) must be registered before `/{id}` routes.

## Web Client (`web_client/`)
`index.html` loads, in order, sharing one global scope: `help_text.js` → `pfm_core.js` → `pfm_pages.js` → `pfm_analytics.js` → `pfm_features.js`. Core rules:
- Escape dynamic text before `innerHTML` (`esc(...)`); server-written sentences go through `serverTextHtml(text)`.
- `notify()` / `await confirmDialog({...})`, no native `alert()`/`confirm()`.
- Money via `Fmt.money(v, currency)` only; numbers `Fmt.num()`, dates `Fmt.date()`. `Fmt.amt` formatters emit a `<span>` — never put them in an attribute.
- Web files are baked into the image — redeploy after every edit.

Full rules (Bootstrap `d-flex` hiding, colours, help text, dashboard blocks, PWA share target, 390px check): `docs/features/web-client.md`, section "Rules summary".

## Testing
- Unit: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` (~1.9k tests, 1–2 min). JS: `make test-js`. Integration + e2e: `uv run pytest tests/integration tests/e2e` (~30 s, needs Chromium).
- Tests can't reach the network (autouse guard); mock at the seam. Write files under `tmp_path`. Assert the exact outcome, never "any of 200/403/500".
- DB version bump: update every `== 47` assertion in `tests/test_database.py` and check `tests/unit/test_schema_v*.py`.
- More (seams to mock, live-network marker, pytest.ini traps, Node version for JS tests): `docs/TESTING.md`, section "Rules summary".

## Documentation (Default Behaviour)
When adding or changing a feature, always update:
1. `PROJECT_STATUS.md` — bump "Last updated" and add the feature to the Recent summary line.
2. `docs/features/<area>.md` — endpoints, schema notes, UI wiring, design rationale, and its "Must-know gotchas" section. A new area gets a new file plus a row in the Feature reference table.
3. `CLAUDE.md` — **only** a new command, convention or hard rule. Gotchas go in the feature doc; incident stories go in the commit message.

This is mandatory. A feature is not done until the docs reflect it.

## Git
- Public repo `github.com:alexgoldhoorn/pfm`, branch `main`. Push: `GIT_SSH_COMMAND="ssh -o IdentitiesOnly=no" git push origin main`.
- Use `git -P` or `GIT_PAGER=cat`.
- Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`). Co-author line names the agent that wrote the commit (e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
- **Pre-commit:** privacy check + black + flake8 + autoflake (`.pre-commit-config.yaml`). **Pre-push:** full unit suite.

## After Every Task — What Needs Restarting

| Change type | Action required |
|---|---|
| `web_client/` JS/HTML/CSS or `nginx.conf` edited | `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web` |
| `portf_server/` or `portf_manager/` Python edited | Nothing — live at once via `uvicorn --reload` once it reaches `~/repos/pfm` |
| `DATABASE_VERSION` bumped / new migration | Nothing — the next reload migrates the **live** DB |
| DB schema patched manually | No restart — note what was done |
| `docker-compose.yml` or `Dockerfile` edited | Full rebuild of the affected service |
| Docs/tests only | Nothing — say so explicitly |

Never leave the user guessing. If a change is already live, say that too.

## Status and issues
`PROJECT_STATUS.md` has component status, pending work and known issues. Tickets: **Todoist** → **#Dev Projects / #pfm**.
