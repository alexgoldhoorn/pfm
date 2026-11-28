# Core patterns and gotchas

> Moved out of `CLAUDE.md` to keep it short. CLAUDE.md points here.

## Performance / event loop
- Endpoints doing blocking yfinance I/O are plain `def` (not `async`) so FastAPI runs them in a threadpool. Keep new blocking-IO endpoints sync.
- The `.venv` is root-owned (Docker-created); run tooling with `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …`.

## Auth
API key auth (`X-API-Key` header). `SERVER_API_KEY` env var is **auto-seeded** at startup (`app.py` lifespan) — no manual DB insert needed after container restart.

Startup fails fast in production if `PORTF_SECRET_KEY` is still the default
placeholder or shorter than 32 characters.

`APIKeyBearer` caches a successful validation on `request.state.api_key_info` for
the lifetime of that request. This matters because data routers are protected at
include-time **and** some endpoints still keep a local `_auth` dependency; with
the cache, one request does one DB validation/`last_used` update instead of two.

## Portfolio Resolver
`db.get_or_create_portfolio(name, base_currency="EUR")` — centralized helper; use instead of inline get/create pattern.

## Transactions
`database.create_transaction()` requires `portfolio_id`. `asset_id` required — use `db.get_asset_by_symbol()` + `db.create_asset()`. Always pass `currency=` (the transaction's own currency) to preserve per-row currency.

## Positions & corporate actions (`portf_manager/positions.py`)
`compute_positions(transactions, key=...)` — single source of truth. Processes **chronologically**. Stock splits: `split` tx stores ratio in `quantity` (2-for-1=2.0; reverse=0.1), scales held quantity, cost unchanged. All holdings/analytics endpoints delegate to it.

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
