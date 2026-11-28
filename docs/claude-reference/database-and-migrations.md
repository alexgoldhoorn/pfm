# Database, schema versions and migrations

> Moved out of `CLAUDE.md` to keep it short. CLAUDE.md points here.

SQLite by default (`portfolio.db`), PostgreSQL via `DATABASE_URL` env var. Use `portf_manager/database.py` for SQLite, `database_factory.py` for auto-detection.

**Current schema version: 47.** Migrations run automatically on startup.

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
- v25: `portfolios.account_type` (`'brokerage'`|`'bank'`, default brokerage — a bank account is a portfolio too); `spending_transactions` (id, portfolio_id, date, description, amount [signed: −out/+in], currency, category, is_transfer, transfer_link_type [`'spending'`|`'booking'`], transfer_link_id, source, created_at); `spending_rules` (id, pattern, category, created_at — global, case-insensitive substring match, first-match-by-id wins — superseded v32, see below). See `docs/features/spending.md`.
- v26: `spending_transactions.balance` (nullable REAL) — populated from the optional `balance` column in imported bank statements. See `docs/features/networth-portfolios.md`.
- v27: `spending_categories` (id, name UNIQUE, created_at) — lightweight category name registry, decoupled from `spending_transactions`/`spending_rules` (which keep storing `category` as a free string, no FK) so a category can exist with zero usages. See `docs/features/spending.md`.
- v28: `spending_categories.parent_id`, `is_root` — hierarchical category tree rooted at fixed "Income" and "Spend" nodes. See `docs/features/spending.md`.
- v29: `budgets` (id, name UNIQUE, description, is_active, created_at, updated_at), `budget_lines` (id, budget_id FK CASCADE, line_type [`income`|`spending`|`debt`|`investment`], ref_key, monthly_amount, overrides [JSON], link_id, notes, UNIQUE(budget_id, line_type, ref_key)) — named open-ended monthly budgets with budget-vs-actual variance. See `docs/features/budgeting.md`.
- v30: `fund_profiles` (asset_id PK REFERENCES assets(id) ON DELETE CASCADE, benchmark_key, source [`benchmark`|`llm`|`manual`], asset_class/regions/sectors [JSON weight maps], currency_hedged, hedge_currency, as_of, notes, updated_at) — one row per fund-like asset (`etf`/`mutual_fund`/`index`), holding the weight maps that let a fund be "seen through" into asset class, region and sector instead of counted as one opaque line. Nothing is backfilled on migration — an asset with no row is reported as unclassified rather than guessed at. See `docs/features/analytics.md`.
- v31: `app_logs` (id, created_at [UTC ISO], level, source [logger name], event, message, details [JSON]) — the persistent application log. See `docs/features/llm.md`.
- v32: `spending_transactions.merchant` (`normalize_merchant`, backfilled); `spending_rules.portfolio_id`/`amount_sign`/`min_amount`/`max_amount`/`priority` — rules evaluate by priority then id. See `docs/features/spending.md`.
- v33: re-runs v32's guarded steps; heals a DB stamped 32 without the columns.
- v34: `fund_profiles.ongoing_charges_pct` (REAL, nullable; the fund's TER in % of NAV a year) — feeds the `ongoing_charges` block of `GET /analytics/fees`. See `docs/features/analytics.md`.
- v35: `manual_assets.as_of` (TEXT, backfilled from `updated_at`); `loans` (principal, annual_rate_pct, `rate_as_of` + `balance_as_of` anchor, start_date, term_months, extra_payment_eur), `account_valuations` (portfolio_id PK, provider, account_ref, value incl. cash, cash, as_of, fetched_at, last_error), `retirement_incomes` (owner `self`|`partner`, monthly_eur in today's euros, from_age, to_age, indexed) — feed the balance sheet and retirement readiness. See `docs/features/networth-portfolios.md`.
- v36: `goals.start_networth_eur` (REAL, nullable — the net worth when the goal was set, written by `POST /goals/`; NULL for older goals, nothing is backfilled because there is no net-worth history) and `goals.notes` (TEXT) — feed the "since set" gain and the actual-vs-needed monthly pace. See `docs/features/networth-portfolios.md`.
- v37: `wealth_scenarios` (name UNIQUE, is_primary [at most one], params [JSON of the simulator inputs], baseline_date, baseline_net_worth_eur [nullable]) — saved Wealth Simulator scenarios tracked against the balance-sheet net worth. See `docs/features/wealth-simulator.md`.
- v38: `portfolios.is_managed` (robo-advisor account: holdings tracked, never a price signal or research nag) and `decisions` (decided_on, symbol/asset_id, action CHECK, title, rationale, expectation, review_on, price_at_decision, outcome, outcome_rating CHECK, reviewed_at, source) — the decision journal. See `docs/features/decisions.md`.
- v39: `research_history` (append-only copy of every research report: asset_id, symbol, generated_at, source [`llm`|`bulk`|`backfill`], recommendation, confidence, fair_value, buy_below, sell_above, currency, price_at_report, horizon_months default 12) — `upsert_research_report` appends a row on every call, the migration backfills one row per existing `research_reports` row. Feeds the research scorecard. See `docs/features/research-rebalance.md`.
- v40: `position_caps` (scope `symbol`|`sector`|`asset_type`, key, max_pct, note; UNIQUE(scope, key)) — user caps checked against `compute_exposure` (`services/caps.py`), enforced by the allocator for symbols and sectors, raised as Action Items when breached. See `docs/features/research-rebalance.md`.
- v41: `spending_transactions.transfer_source` (`match`|`pattern`|`manual`|`rejected`, nullable) — how a row became a transfer; backfills flagged rows (`match` if linked, else `manual`) and sets rows with category `Transfer` but no flag back to `uncategorized`. See `docs/features/spending.md`.
- v42: `spending_categories.essentiality` (`fixed`|`flexible`|`optional`, nullable, nothing backfilled) — drives the what-if scenarios' cut order; `reports` (id, title, content_md, source_session_id, created_at, updated_at) — Markdown reports saved from the AI chat. See `docs/features/scenarios.md` and `llm.md`.
- v43: `spending_transactions.one_off` (INTEGER NOT NULL DEFAULT 0 — counts in every total, left out of every average/baseline), `budget_lines.period` (`monthly`|`yearly`, default monthly; on a yearly line `monthly_amount` stores the YEARLY figure) and `budget_lines.rollover` (0/1) — nothing backfilled. See `docs/features/spending.md`, `budgeting.md`, `cashflow-forecast.md`.
- v44: `budget_lines.link_type` (`manual`|`loan`, default manual — what a debt line's `link_id` points at: a `manual_assets` liability or a `loans` row; a dangling link returns `link_broken: true`, never an error). See `docs/features/budgeting.md`.
- v45: `loan_rate_periods` (loan_id FK CASCADE, from_date, annual_rate_pct, balance_eur nullable, note; UNIQUE(loan_id, from_date)) — the rates a loan had before its anchor, rebuilding history, paid-to-date and pre-anchor reconciliation; nothing backfilled. See `docs/features/mortgage.md`.
- v46: `planned_items` (date, description, amount [signed], currency, kind [`spending`|`savings`|`income`], portfolio_id, done, notes) — hand-entered future one-offs placed once in the cash-flow forecast; never part of an average. See `docs/features/cashflow-forecast.md`.
- v47: `networth_snapshots` (snapshot_date PK, net_worth_eur, investments/cash/property/other_assets/liabilities_eur, detail, warnings JSON) — one stored balance sheet a day, no backfill; `recurring_resolutions` (portfolio_id, merchant UNIQUE, status `cancelled`|`paid_elsewhere`, since, note); `month_closes` (month PK, closed_at, figures, checklist, report_id, notes). See `docs/features/networth-portfolios.md`, `spending.md`.

⚠️ **New tables must appear in BOTH `_create_all_tables` (fresh DBs) AND `_migrate_to_vN` (existing DBs)** — migration-only adds break fresh installs/tests with "no such table".
⚠️ **CHECK constraint rebuilds** require `PRAGMA legacy_alter_table=ON` around the `RENAME` — see `_migrate_to_v13`.

All transaction SELECT queries use `COALESCE(t.currency, a.currency) AS currency` with an explicit column list (NOT `t.*`) — `sqlite3.Row` uses the first column when names collide.

`bookings` table: `id, portfolio_id, date, action (Deposit|Withdrawal), amount, currency`
