# CLAUDE.md — Project Rules for AI Assistants

## Project Overview
Portfolio Manager: a Python CLI + FastAPI server + web client for tracking stocks, ETFs, funds, bonds, crypto and commodities across multiple brokers, with LLM-powered import, file import/export, web chat, Spanish IRPF tax reporting, and Google Sheets PDT sync.

## Exposed via the internet-facing MCP gateway
`mcp/server.py` is symlinked from `~/mcp/pfm/server.py` and imported live by
`~/mcp/remote_gateway/server.py`, the always-on, internet-facing
`mcp-remote-gateway.service` (`https://mcp.goldhoorn.net`), which re-exports most
of its tools. A change here affects that surface too — see `~/mcp/CLAUDE.md`,
"Exception: remote_gateway/server.py".

- The tools are read-only wrappers over the HTTP API. Tests:
  `cd ~/mcp/pfm && python3 -m pytest -q .`.
- When an API response changes shape, update the matching tool. Tools read fields
  with `.get(..., 0)`, so a renamed field silently becomes zero, and a test that
  pins the old field names stays green.
- `budget_summary` mirrors `months_without_activity`: no verdicts for a month with
  nothing imported, and it flags a month still in progress.
- `wash_sale_check` applies art. 33.5 LIRPF: a 2-month window, or 1 year for
  unlisted fund units, matched against FIFO lots from `/analytics/tax-report` for
  this year and last. "Bought recently" lists only positions still held.
  ⚠️ **Don't trust `asset_type` to spot a fund** — no live asset is typed
  `mutual_fund` (heuristic imports store index funds as `stock`; Indexa's funds
  are `etf` on exchange `"Funds"`). `_wash_window_months` counts an asset as a
  fund if it is typed `mutual_fund`, OR its exchange is `"Funds"`, OR it isn't
  `etf` and its name contains fund/idx/fondo/fonds — erring towards the year.
  Crypto, cash and the synthetic `MINTOS` asset are excluded.
- A new tool must be registered in three places or it stays local-only: the
  gateway (`~/mcp/remote_gateway/server.py`, including `TOOL_SPECIALIST`), the
  finance agent's tools line (`~/.claude/agents/finance.md`), and the persona
  (`~/agents/finance/persona.md`).

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
- `web_client/` — Vanilla JS + Bootstrap 5 frontend (static files, no build step). See the Web Client section for load order.
- `tests/` — pytest test suite. `tests/unit/`, `tests/integration/`, `tests/e2e/`.
- `docs/features/` — full per-feature reference (see the Feature reference table). `docs/superpowers/` holds specs and plans.

## Key Patterns

### Database
SQLite by default (`portfolio.db`), PostgreSQL via `DATABASE_URL` env var. Use `portf_manager/database.py` for SQLite, `database_factory.py` for auto-detection.

**Current schema version: 31.** Migrations run automatically on startup.

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
- v25: `portfolios.account_type` (`'brokerage'`|`'bank'`, default brokerage — a bank account is a portfolio too); `spending_transactions` (id, portfolio_id, date, description, amount [signed: −out/+in], currency, category, is_transfer, transfer_link_type [`'spending'`|`'booking'`], transfer_link_id, source, created_at); `spending_rules` (id, pattern, category, created_at — global, case-insensitive substring match, first-match-by-id wins). See `docs/features/spending.md`.
- v26: `spending_transactions.balance` (nullable REAL) — populated from the optional `balance` column in imported bank statements. See `docs/features/networth-portfolios.md`.
- v27: `spending_categories` (id, name UNIQUE, created_at) — lightweight category name registry, decoupled from `spending_transactions`/`spending_rules` (which keep storing `category` as a free string, no FK) so a category can exist with zero usages. See `docs/features/spending.md`.
- v28: `spending_categories.parent_id`, `is_root` — hierarchical category tree rooted at fixed "Income" and "Spend" nodes. See `docs/features/spending.md`.
- v29: `budgets` (id, name UNIQUE, description, is_active, created_at, updated_at), `budget_lines` (id, budget_id FK CASCADE, line_type [`income`|`spending`|`debt`|`investment`], ref_key, monthly_amount, overrides [JSON], link_id, notes, UNIQUE(budget_id, line_type, ref_key)) — named open-ended monthly budgets with budget-vs-actual variance. See `docs/features/budgeting.md`.
- v30: `fund_profiles` (asset_id PK REFERENCES assets(id) ON DELETE CASCADE, benchmark_key, source [`benchmark`|`llm`|`manual`], asset_class/regions/sectors [JSON weight maps], currency_hedged, hedge_currency, as_of, notes, updated_at) — one row per fund-like asset (`etf`/`mutual_fund`/`index`), holding the weight maps that let a fund be "seen through" into asset class, region and sector instead of counted as one opaque line. Nothing is backfilled on migration — an asset with no row is reported as unclassified rather than guessed at. See `docs/features/analytics.md`.
- v31: `app_logs` (id, created_at [UTC ISO], level, source [logger name], event, message, details [JSON]) — the persistent application log. See `docs/features/llm.md`.

⚠️ **New tables must appear in BOTH `_create_all_tables` (fresh DBs) AND `_migrate_to_vN` (existing DBs)** — migration-only adds break fresh installs/tests with "no such table".
⚠️ **CHECK constraint rebuilds** require `PRAGMA legacy_alter_table=ON` around the `RENAME` — see `_migrate_to_v13`.

All transaction SELECT queries use `COALESCE(t.currency, a.currency) AS currency` with an explicit column list (NOT `t.*`) — `sqlite3.Row` uses the first column when names collide.

`bookings` table: `id, portfolio_id, date, action (Deposit|Withdrawal), amount, currency`

### Performance / event loop
- Endpoints doing blocking yfinance I/O are plain `def` (not `async`) so FastAPI runs them in a threadpool. Keep new blocking-IO endpoints sync.
- The `.venv` is root-owned (Docker-created); run tooling with `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …`.

### LLM
Provider-agnostic via `portf_manager/llm_client.py`. `get_llm_client()` auto-detects
Ollama → Gemini → OpenRouter → Anthropic; override with
`PORTF_LLM_PROVIDER`/`PORTF_LLM_MODEL`. ⚠️ `docker-compose.yml` sets
`PORTF_LLM_PROVIDER=gemini` under `environment:`, which overrides `.env*` — change
the provider there, not only in `.env.local`.

- `EnhancedChatEngine` is lazily initialized — never instantiate it at module level
  (crashes without an API key).
- Search grounding (`generate_with_search`) exists only on Gemini and Anthropic;
  callers detect it with `hasattr`. All four providers implement
  `ToolCapableLLMClient`; the 15 chat tools in `portf_server/chat_tools.py` call
  DB/service functions directly, never HTTP.
- Every provider method is wrapped by `_instrument`: transient errors are retried
  (`PORTF_LLM_MAX_ATTEMPTS`, `PORTF_LLM_RETRY_DELAY`), config/request errors fail
  at once, a final failure raises `LLMError`, and each call is logged as an
  `llm.call` event in `app_logs`.
- **LLM failures are errors, never an empty result.** Extraction raises
  `LLMError`/`LLMResponseError` rather than returning `[]`; routers return 502
  with the reason in `detail`; chat writes nothing to the session on failure;
  research returns `recommendation: None`, never a placeholder `HOLD`. Don't add a
  fallback that hides a failure as "nothing found".
- `configure_logging()` (`portf_manager/event_log.py`) is the only place that
  configures logging — library modules must never call `logging.basicConfig`.

Full detail (tool loop, extract-bookings date handling, chat sessions, the
`app_logs` handler, `GET /api/v1/system/logs`): `docs/features/llm.md`.

### Auth
API key auth (`X-API-Key` header). `SERVER_API_KEY` env var is **auto-seeded** at startup (`app.py` lifespan) — no manual DB insert needed after container restart.

Startup fails fast in production if `PORTF_SECRET_KEY` is still the default
placeholder or shorter than 32 characters.

`APIKeyBearer` caches a successful validation on `request.state.api_key_info` for
the lifetime of that request. This matters because data routers are protected at
include-time **and** some endpoints still keep a local `_auth` dependency; with
the cache, one request does one DB validation/`last_used` update instead of two.

### Portfolio Resolver
`db.get_or_create_portfolio(name, base_currency="EUR")` — centralized helper; use instead of inline get/create pattern.

### Transactions
`database.create_transaction()` requires `portfolio_id`. `asset_id` required — use `db.get_asset_by_symbol()` + `db.create_asset()`. Always pass `currency=` (the transaction's own currency) to preserve per-row currency.

### Positions & corporate actions (`portf_manager/positions.py`)
`compute_positions(transactions, key=...)` — single source of truth. Processes **chronologically**. Stock splits: `split` tx stores ratio in `quantity` (2-for-1=2.0; reverse=0.1), scales held quantity, cost unchanged. All holdings/analytics endpoints delegate to it.

### Feature reference
Full per-feature docs (endpoints, schema, UI wiring, design rationale) live in
`docs/features/`. **Read the doc for the area you're changing before you edit
it.** The column on the right lists only what's easy to break.

| Area | Doc | Must-know gotchas |
|---|---|---|
| Broker parsers, PDT, import/export, bookings | `imports-exports.md` | A parser must set `broker=` on every row, or preview duplicate detection silently matches nothing. Match Indexa `Tipo` values by prefix. PDT: crypto/commodity exchange empty, `mutual_fund` exchange the literal `"Funds"`, bond qty/price rescaled via `_pdt_bond_quantity_price`. In `find_duplicate_transaction` the whole price ratio goes inside `ABS()`. Every import path (web, PDT pull, CLI) dedups against rows stored *before* the import — never within it — and `duplicate_action` doesn't apply to bookings (only per-row `force`). |
| Research, Portfolio Health, rebalance planner | `research-rebalance.md` | Single-segment GET routes (`portfolio-analysis`, `compare`, `bulk-refresh-status`) must be registered before `/{symbol}`. `upsert_price_target` treats `None` as "leave unchanged"; only `clear=` nulls a field. `/rebalance/analysis` and `/plan` both use `compute_positions`. |
| Analytics, fund look-through, PDF reports | `analytics.md` | `/tax-report` converts at transaction-date FX (`_fx_on`); `/fees` at current FX. `TaxTransaction` internal field names differ from the response keys. Fund region weights come from hand-maintained `benchmarks.json`, never yfinance. `by_currency` ≠ `by_currency_exposure`. `compute_exposure()` backs both `/diversification` and Portfolio Health — don't fork it. The Portfolio Health PDF section reads the cache only, never runs the LLM. |
| Net worth, brokers/portfolios, goals, sync | `networth-portfolios.md` | `net_worth_eur(db)` is the one total (Goals uses it). Bank balances come from the latest `spending_transactions.balance`; an account with none is excluded, not zero. `account_type` is create-only. `cash_eur`/`cost_eur` use transaction-date FX, `value_eur` the live rate; a ~1–2% gap vs the broker's own EUR figure is expected FX noise. |
| Spending (bank accounts) | `spending.md` | Bank accounts are `portfolios` rows with `account_type='bank'` and never touch `transactions`/`bookings`. `GET /spending/` returns `{items, total}`. A category's tree root must match the amount's sign. Un-transferring a row resets its reciprocal counterpart (`reset_transfer_counterpart`). Exclude already-linked bookings before transfer matching. |
| Budgeting | `budgeting.md` | `GET /summary` must stay before `/{budget_id}`. Positive variance always means favourable. Attribute unbudgeted actuals by SIGN, not tree root. Budget and `/spending/trend` must reconcile to the cent. An all-digits investment `ref_key` is a broker — use `line_uses_category()`. `net` is cash flow, not "better off". Never show a flattering variance for a month with nothing imported. |
| Action Items | `action-items.md` | Each check is wrapped independently. Net Worth checklist gaps are merged client-side (`computeNetWorthChecklist`), never duplicated server-side. Item ids are deterministic per entity. Only `consolidation_candidate` fund overlaps raise an item. |
| Price updates, market data | `prices-market.md` | yfinance returns UK prices in GBX — ÷100 when `fast_info.currency == "GBp"`. `_CRYPTO_YF_OVERRIDES` lives only in `price_updater.py`. Read `previous_close` by subscript, never `fast_info.get()` (silently None). Currency self-healing skips crypto. |
| Web client | `web-client.md` | See the Web Client section below. |
| Metrics, error tracking, traces | `observability.md` | All off unless configured (`PORTF_METRICS_ENABLED`, `PORTF_SENTRY_DSN`, `PORTF_OTEL_ENDPOINT`). Telemetry must never stop the API: a feature that fails to start is logged and skipped. LLM metrics/spans hook `_call_with_retry`/`_log_llm_call`, so every `_instrument`-wrapped call is covered. Prompt text goes on spans only with `PORTF_OTEL_CAPTURE_LLM_CONTENT`. `/metrics` is `include_in_schema=False` and outside the API-key dependency, so protect it with `PORTF_METRICS_TOKEN`. |

## Web Client (`web_client/`)
Vanilla JS + Bootstrap 5.3, no build step. `index.html` loads five files **in this
order**, sharing one global scope: `help_text.js` → `pfm_core.js` (prefs, `Fmt`,
`esc`, API/modal managers, shared chart helpers, `notify`/`confirmDialog`) →
`pfm_pages.js` (nav, dashboard, transactions, assets) → `pfm_analytics.js`
(charts) → `pfm_features.js` (all other pages + `DOMContentLoaded` bootstrap).
Full reference (dashboard layout, chart helpers, page-by-page wiring):
`docs/features/web-client.md`.

- Escape dynamic text before `innerHTML` (`esc(err.message)`) — imported data and
  backend errors are untrusted.
- Messages via `notify(msg, level?)`; questions via
  `await confirmDialog({title, message, confirmLabel, danger})`. No native
  `alert()`/`confirm()` (the one `alert()` left, before the DB-restore reload, is
  deliberate).
- Money via `Fmt.money(v, currency)` only — no `'€' + Fmt.num(…)` strings. Numbers
  `Fmt.num()`, dates `Fmt.date()`.
- ⚠️ Formatters built on `Fmt.amt(...)` emit a `<span>` — never interpolate them
  into an attribute; use `esc(fmtEurCents(v))` there.
- ⚠️ An element with Bootstrap `d-flex`/`d-block` can't be hidden with
  `style.display = 'none'` (`!important`) — swap classes instead.
- Colours: `--viz-*` CSS tokens; asset types map to a fixed colour, never by rank.
  SVG axes use `SVG_GRID`/`SVG_AXIS`/`SVG_LABEL`, never hard-coded hex.
- New pages or non-obvious cards get entries in `METRIC_HELP`/`PAGE_HELP`
  (`help_text.js`); call `initTooltips()` after rendering tooltip-bearing markup.
- Pure helpers are `window.`-exported so the DOM-free JS tests
  (`web_client/js/tests/`) can import them.
- Check new pages at 390px width for horizontal overflow.
- Web files are baked into the image, not live-mounted — redeploy after every
  edit (see the restart table).

## Testing
- Unit: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
  (~1.5k tests, ~50s; also passes as root). JS: `make test-js`.
- Integration + e2e: `uv run pytest tests/integration tests/e2e` (~30s). Each
  starts the real app in a **spawned** (not forked — a fork inherits
  already-imported settings) process on a temp DB with `SERVER_API_KEY` seeded.
  The e2e smoke test mounts `web_client/`, stubs the CDN libraries, and fails on
  any uncaught JS error or 5xx across every sidebar page. Needs Chromium; not in CI.
- **Tests can't reach the network.** An autouse guard in `tests/conftest.py` fails
  any real HTTP call, even one the app swallows. Mock at the seam:
  `portf_manager.market._fetch_quote_live`, `currency_utils.is_gbx`,
  `portf_server.routers.llm.get_llm_client`. `PORTF_LLM_RETRY_DELAY=0` is set
  autouse. Genuine live tests are `@pytest.mark.network`, run only with
  `PFM_LIVE_NETWORK_TESTS=1`. Loopback is allowed only in integration/e2e.
- Every test gets its own empty `HOME` (the CLI writes `~/.portf_session`). Write
  test files under `tmp_path`, never the working directory.
- `pytest.ini` must start with `[pytest]` — `[tool:pytest]` is silently ignored.
  Unknown-key warnings are hidden by `filterwarnings`; check with `-W default`.
  `timeout = 300` needs the `pytest-timeout` dev dep.
- Markers come from the directory (`unit`/`integration`/`e2e`) plus explicit
  decorators — never from test names.
- Assert the exact outcome. No "any of 200/403/500" or
  `if "id" in data: … else: pass` tests — they pass against missing endpoints.
- `make test-js` names `web_client/js/tests/*.test.mjs` explicitly; on Node 24
  `node --test <dir>` fails with a misleading `MODULE_NOT_FOUND`.
- Pre-push hook runs the full unit suite. F541 fixer:
  `uv run python scripts/fix_f541.py`. Reset the LLM singleton with
  `portf_manager.llm_client.reset_llm_client()`.
- DB version bump: update every `== 31` assertion in `tests/test_database.py`.

## Documentation (Default Behaviour)
When adding or changing a feature, always update:
1. `PROJECT_STATUS.md` — bump "Last updated" and add the feature to the Recent
   summary line.
2. `docs/features/<area>.md` — endpoints, schema notes, UI wiring, design
   rationale. A new area gets a new file plus a row in the Feature reference table.
3. `CLAUDE.md` — **only** what someone must know before touching that area: a
   one-line gotcha in the Feature reference table or Important Gotchas, or a new
   command/convention. No endpoint lists, element IDs or incident stories here;
   the story of how a bug was found goes in the commit message.

This is mandatory. A feature is not done until the docs reflect it.

## Privacy and Demo Data
Public repo — never commit real personal or financial data.

**Forbidden:** real API keys/tokens/passwords; real Spreadsheet IDs (use `YOUR_SPREADSHEET_ID`); real ISINs for held assets (use `US0000000001`/`LU0000000001`/`ES0000000001` family); real portfolio amounts/prices; home directory paths (`/home/agoldhoorn/` → use `~/`). <!-- allow-financial: this line must quote the forbidden pattern -->

**Enforced by a pre-commit hook** (`scripts/check_no_real_financials.py`, first
in `.pre-commit-config.yaml`), because this rule lived only in prose and real
figures reached `main` and GitHub anyway — scrubbed from all 554 commits on
2026-09-04 with `git filter-repo`. The hook favours precision over recall (a
noisy hook gets disabled): it blocks ISINs outside an allowlist, IBANs and
home-directory paths anywhere, plus money-with-cents and six-figure amounts in
**Markdown only** — invented examples are round, and a fixture price of
`155.00` in a test carries no meaning. Put `allow-financial` in a comment on
the line for a genuine exception, so exceptions show up in review.

⚠️ **A history rewrite does not reach commit messages.** `--replace-text` only
rewrites blob contents; the same strings survived in commit messages and needed
a second `--replace-message` pass. Verify both (`git log -p --all` covers
contents *and* messages) before believing a scrub is complete.

**OK:** well-known tickers (AAPL, BTC-EUR) as format examples; Apple's `US0378331005` in prompt templates; personal website/GitHub links in About page; fictional prices in test fixtures.

When writing tests, invent asset names (e.g. "Example Corp", "Global Bond Fund"). Same rule for plan docs under `docs/superpowers/`.

## Git
- Public repo `github.com:alexgoldhoorn/pfm` — develop on `main`. Push: `GIT_SSH_COMMAND="ssh -o IdentitiesOnly=no" git push origin main`.
- Use `git -P` or `GIT_PAGER=cat` (some git versions don't support `--no-pager` as a flag).
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`. Co-author: `Co-Authored-By: Oz <oz-agent@warp.dev>`.
- **Pre-commit**: black + flake8 + autoflake via `.pre-commit-config.yaml`. **Pre-push**: full unit suite.

## Important Gotchas
- **`_TX_COLS` uses f-strings**: queries in `database.py` are
  `f"""SELECT {self._TX_COLS}..."""`; the `f` prefix is load-bearing. Never run
  F541-fixers that touch triple-quoted strings on this file. Autoflake is safe;
  custom regex strippers are not.
- **Black + regex**: `f""` matches the first two chars of `f"""..."""`. Limit
  F541-fixers to `[^\n]` single-line patterns.
- **DB adapter comes from the URL, no silent fallback**:
  `database_factory.get_database_adapter(settings.database_url)` maps
  `sqlite:///...` to SQLite and `postgres://`/`postgresql://` to Postgres;
  anything else raises.
- **Linting**: `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`.
  It reports 0 warnings — keep it that way.
- **`sqlite3.Row` name collision**: never `SELECT t.*, ..., COALESCE(t.col, other) AS col`
  — use an explicit column list. The first occurrence wins.
- **Spanish tax**: stocks/ETFs/bonds/funds are IRPF Box 27. FIFO over the **full** history — out-of-window sells still
  consume lots; only in-window sells are reported. `purchase_amount` includes
  purchase fees, `sell_amount` is net of sale fees (`purchase_price`/`sell_price`
  stay gross). Transaction rows store `user_id` NULL, so `calculate_tax_report`
  ignores its `user_id` param, and any `user_id` filter (e.g. the CLI's
  `transaction_filter.py`) must keep NULL rows.
- **PDT XLSX**: openpyxl writes to a file path, not BytesIO. Always clean up with
  `os.unlink()`.
- **Env var prefixes**: `portf_server/settings.py` uses the `PORTF_` prefix.
  `GOOGLE_SERVICE_ACCOUNT_FILE`/`GOOGLE_SPREADSHEET_ID` are not prefixed — read
  via `os.getenv()`.
- **Compose interpolates the whole file before service selection**: a required
  var in one service can break `docker compose build web`. Keep local-dev
  fallbacks for cross-service vars; enforce strict secrets in deployment envs.
- **GBX**: `currency_utils.normalize_gbx_amounts()` ÷100 on import (`imports.py`
  save + `sync.py` pull); missing it makes cost basis 100× too high. The live
  price fetch ÷100 only when `fast_info.currency == "GBp"`, so an odd Yahoo
  response can store one run's UK prices ~100× too high, and nothing guards
  against it. A one-day spike-and-revert on a net-worth chart → check `prices`
  for GBP assets on that date. `is_gbx` caches successful lookups only.
- **ISIN→ticker resolution** (`ticker_resolver.py`): OpenFIGI returns no currency,
  so `_pick_best_ticker` infers it from the exchange code and `_verify_yf` checks
  the quote's currency. The resolver returns `None` rather than a wrong-currency
  ticker; those assets need a manual `ticker` via `PUT /api/v1/assets/{id}`.
  Known gap: with several right-currency venues, the first one wins.
- **`auto_price`**: a manual price (`POST /assets/{id}/prices` with
  `source="manual"`, or a deposit import) sets `auto_price=0`, and the daily cron
  skips the asset from then on. Re-enable with
  `PUT /api/v1/assets/{id} {"auto_price": true}`.
- **`asset_type` enum**: add new values to BOTH Pydantic
  (`portf_server/schemas/assets.py`) and `models.py`, or `/assets` 500s.
- **`transaction_type`**: `models.py TransactionType` + SQLAlchemy CHECK +
  `transactions` table rebuild — update all three.
- **Thousands with no decimals**: `parsers/utils.py`'s `parse_european_number`
  and `parse_unsigned_amount` read a lone separator followed by groups of three
  as thousands (`"10.000"` = 10000, `"1.000.000"` = 1000000). Keep it that way.
- **MyInvestor CSV**: semicolon-delimited, European numbers (comma decimal, dot
  thousands). `myinvestor_csv_parser.py` covers trades, dividends, deposits, fees
  and cash interest; LLM text import covers the rest.

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
