# Portfolio Manager — Project Status

> **Note:** `CLAUDE.md` is the authoritative, up-to-date project reference. This
> file is a periodic snapshot and its lower sections (Test Status, Pending Work,
> Data Import table) may lag the code — verify against `CLAUDE.md` and the
> codebase before relying on them.

Last updated: 2026-07-21

**Recent (v2.5.25):** **Fixed a real 502 in AI-suggest categorization on large backlogs.** The Spending page's "Suggest categories (AI)" bulk action sent every unique description in the selection to the LLM in one request — on a production account with 2,384 uncategorized rows (1,334 unique descriptions), this exceeded `portf_web`'s nginx `proxy_read_timeout` (200s) and surfaced as an opaque 502 with nothing logged server-side. Now capped to 30 unique descriptions per click, with an explicit "Sent X of Y..." status message when the cap applies. New "Select all uncategorized" button sets the Category filter and selects every now-filtered row in one click, so clearing a large backlog is: select-all-uncategorized → suggest (batch of 30) → review → Apply → repeat, with no auto-chaining needed (applying a batch shrinks the next round automatically).

**Recent (v2.5.24):** **Categorize already-imported Spending rows.** New `POST /api/v1/spending/rescan-categories` re-applies current rules to every row still `uncategorized` (manual button, mirrors the existing Rescan Transfers pattern) — safe to run after adding/editing rules since it never overwrites an already-set category. The Spending page's existing bulk-select checkboxes gained a "Suggest categories (AI)" action: calls the existing (unchanged) `/suggest-categories` endpoint against selected uncategorized rows, deduplicated by description, and opens an editable review panel — nothing saves until you click Apply, at which point accepted suggestions both categorize the matching rows and create a new rule each, so Rescan (or the next import) picks up the same merchant automatically afterward.

**Recent (v2.5.23):** **AEB43/N43 follow-ups.** The Spending/Import-Export bank-statement file pickers (`#spImportFile`, `#ioSpImportFile`) restricted the OS file dialog to `.csv` via the `accept` attribute, hiding `.q43`/`.n43` AEB43 exports even though the backend already auto-detected them — widened to `.csv,.q43,.n43,.txt`. The Brokers page's "Value (EUR)" column showed "—" for every bank account (its value comes from `GET /api/v1/portfolios/values`, which sources only `transactions`, never populated by bank accounts) — `loadPortfoliosPage()` now also fetches `GET /api/v1/networth/` and shows each bank account's derived balance + "As of" date there, same source as the Net Worth page's "Bank Accounts" card.

**Recent (v2.5.22):** **AEB43/N43 fixed-width bank statement import.** Spending Tracking's upload endpoint now auto-detects AEB43/Norma 43 ("Cuaderno 43") exports — the Spanish national fixed-width bank-statement standard offered by Caixa Enginyers and Abanca as an alternative to CSV — via a new `aeb43_parser.py`, with zero new UI/API surface (content-sniffed, falls back to the existing generic CSV parser for everything else). Unlike CSV imports, AEB43 exports carry a genuine per-row running balance computed from the file's own opening-balance record. Field layout was reverse-engineered and validated against two independent real bank exports (debit/credit counts, sums, and computed running balance all matched each file's own trailer record to the cent). Upload decoding also gained a Latin-1 fallback for non-UTF-8 statement files, fixing a latent bug that would have rejected both real AEB43 exports outright.

**Recent (v2.5.21):** **Bank-account balances derived automatically into Net Worth.** `spending_transactions.balance` (nullable, db v26) is now persisted from the optional `balance` column in imported bank statements. `net_worth_eur(db)` and `GET /api/v1/networth/` sum each bank-type portfolio's most recent balance-bearing row (via `Database.get_latest_bank_balance`) into the total — mirroring how brokerage positions are already automatic rather than manually re-entered. An account with no balance-bearing import yet is excluded from the total (not zero) and flagged by the setup checklist, which also warns if a manual cash/bank asset and an imported bank balance both exist (possible double-counting). New "Bank Accounts" card on the Net Worth page shows each account's derived balance and as-of date.

**Recent (v2.5.20):** **Spending tracking round-2 feedback.** `DELETE /api/v1/spending/{id}` (hard delete) plus a bulk select/recategorize/delete UI on the Spending transactions table. Bank accounts can now be created directly from the Brokers page via `POST /api/v1/portfolios/` with `account_type: "bank"`, without needing a Spending import first. `GET /api/v1/export/csv` accepts repeated `portfolio_id` query params for a combined multi-account CSV export. Import/Export page gains an "Import Bank Statement" card that reuses the existing `_wireSpendingImportModal`/`_renderSpImportPreview` flow (now parameterized by element-id config) instead of duplicating it.

**Recent (v2.5.19):** **Bank spending tracking + inter-account transfer detection.** New `spending_transactions`/`spending_rules` tables (db v25) plus `portfolios.account_type` (brokerage/bank). Bank statements import via a new generic CSV parser (date/description/amount, EU/US auto-detect) into a new `spending.py` router; rows auto-categorize against saved description-match rules, with an LLM "Suggest categories" fallback for anything unmatched (accepting a suggestion creates a new rule). A pure `transfer_matcher.py` auto-links an outflow in one account to a same-amount inflow in another within ±3 days — bank-to-bank or bank-to-brokerage (matched against existing `bookings` Deposits) — so transfers are excluded from spending totals. New "Spending" nav page (import, category breakdown, sortable transaction table, rules management); Net Worth page gets a read-only 30-day "Actual" comparison next to the manual Monthly Cash Flow entries. Dedicated parsers for specific banks (Abanca, Caixa Enginyers, Revolut, MyInvestor cash) deferred pending real sample export files — the generic parser covers them in the meantime. **Whole-branch review fixes:** `_run_transfer_matching` now excludes brokerage Deposit bookings already claimed as a transfer counterpart in a prior `/save`/`/rescan-transfers` call, preventing a later unrelated same-amount outflow from wrongly reusing them (regression test in `test_spending_api.py`); the import modal's Save button now reads a `duplicate_action` choice (Skip/Add anyway/Overwrite existing) from a new `#spDuplicateAction` select instead of always hard-coding `'skip'`.
**Recent (v2.5.19):** **Seven small fixes from user-reported issues.** (1) An asset's `asset_type` is now corrected on re-import when a later broker parser carries a more accurate value (e.g. an ISHARES fund first misclassified `stock` by the MyInvestor/PDT name heuristic, later correctly tagged `etf` by an IndexaCapital import) — previously only set on first creation, so filtering the Assets page to "stock" could show ETFs forever; the `pdt_xlsx_parser` heuristic also now recognises bare `ISHARES` naming. (2) `POST /api/v1/import/save` now rejects transactions with a blank date instead of silently saving one with no date. (3) The Gemini empty-response guard (`_gemini_search`/`generate`) now also catches whitespace-only responses (previously only `None`/`""`), closing the last gap that could still reach `json.loads("")` in `generate_valuation_report`. (4) The Research modal title now shows "Name (SYMBOL)" instead of just the symbol. (5) Action Items now show "Name (SYMBOL)" instead of a bare symbol/ISIN for price-update failures, stale-research, and watchlist/price-target alerts. (6) Assets/Dashboard/Brokers P/L column headers gained hover tooltips explaining the metric. (7) New "Add Cash" button on the Transactions page opens a manual deposit/withdrawal modal (previously only reachable from the Import/Export page's Bookings tab).

**Recent (v2.5.18):** **Merged Assets + Holdings into one page** — the separate "Holdings" (positions with cost basis, live price, P&L) and "Assets" (instrument catalogue with manual price override and OpenFIGI ticker resolution) nav pages are now a single "Assets" page. Defaults to owned positions only (same portfolio-scoped summary cards, hide-tiny-position threshold, and rebalancing card as the old Holdings page); a new "Show assets with no holding" checkbox appends catalogue assets held in zero portfolios ("held anywhere" computed from an unfiltered `getHoldings()` call, independent of the selected portfolio filter). No backend changes — purely a frontend merge of `getAssets()` + `getHoldings(portfolio_id)`.

**Recent (v2.5.17):** **IndexaCapital import: fixed two bugs losing/misjudging real transactions.** (1) `IndexaCapitalCSVParser.IMPORTABLE_TYPES` required an exact `"SUSCRIPCIÓN"`/`"REEMBOLSO"` match, so internal fund-switch rows (`SUSCRIPCIÓN POR TRASPASO`, `REEMBOLSO POR TRASPASO`) and the CSV header row were silently dropped into the "skipped" count with no way to review or import them — now matched by prefix (`startswith`), and the header row is explicitly skipped instead of mis-parsed. (2) `PreviewTransaction`s built from IndexaCapital files never set `broker`, so `POST /api/v1/import/upload`'s duplicate check ran with `portfolio_id=None` — since real imported rows always have a real portfolio_id, the SQL `portfolio_id IS ? OR portfolio_id = ?` filter never matched and **every** re-imported row looked new, regardless of how many duplicates actually existed (confirmed against production data: 113 previously-imported rows out of 141 went from 0 detected to all 113 correctly flagged). Fixed by tagging `broker="Indexa Capital"`, the same pattern Coinbase already used. 5 new tests (3 parser-level, 2 router-level including a real seeded-duplicate regression test).

**Recent (v2.5.16):** **Action Items page** — new `GET /api/v1/action-items/` endpoint aggregates six maintenance checks server-side (stale broker imports 60+ days, data-quality summary reusing the existing dq/* checks, price-update-run failures, held positions not re-valued in 90+ days, off-track goals, watchlist/price-target alerts) via `portf_manager/services/action_items.py`. New "Action Items" nav page merges that response with the existing (unchanged, still client-only) Net Worth setup checklist and renders one severity-sorted, dismissible list (`localStorage["pfmDismissedActionItems"]`, same pattern as the Data Quality tab's dismissals). `compute_price_target_alerts()` extracted from the `/research/alerts/check` endpoint so it can be reused without re-triggering push notifications. 18 new backend tests, 5 new JS tests.

**Recent (v2.5.15):** **Research: fixed cryptic Gemini valuation error** — `GeminiLLMClient._gemini_search()` (search-grounded valuation calls) could silently return an empty `text` when `response.text` was `None` (thinking-token budget exhausted before a final answer, or a safety block), which then made `generate_valuation_report()` crash on `json.loads("")` with a confusing `Expecting value: line 1 column 1 (char 0)` instead of a readable error. `_gemini_search` now raises `RuntimeError("Empty response from Gemini API...")` on empty text, matching the guard `generate()` already had — the existing outer try/except surfaces a clear message in the UI instead. 1 new regression test.

**Recent (v2.5.14):** **Net Worth: staleness hint + split expense checklist** — bank/cash manual-asset rows (`savings_account`/`current_account`/`cash`) now show "Updated N days ago" under the name, in amber past 30 days (`NW_STALE_DAYS`), reusing the `updated_at` column that was already bumped on create/edit but never surfaced. The checklist/wizard's single "Monthly expenses" item is now three conditional items — **Mortgage payment** (shown only if you have a mortgage), **Loan payment** (shown only if you have a non-mortgage liability), **Other recurring expenses** (always shown, for utilities/insurance/subscriptions/etc.) — so entering just the mortgage payment no longer marks the whole category "done". 2 new unit tests, 2 existing tests updated for the new checklist keys.

**Recent (v2.5.13):** **Net Worth: click-to-edit balances** — manual-asset amounts (bank/cash balances, updated frequently) can now be edited in place by clicking the € figure in the items table, instead of delete-and-re-add. New `apiClient.updateManualAsset(id, payload)` calls the `PUT /api/v1/networth/{id}` endpoint that already existed server-side but was never wired into the UI. Also clarified in help text that a home's manual-asset value should be a current market estimate, not the original purchase price.

**Recent (v2.5.12):** **Cashflow ↔ Goals/Forecast wiring + Goals transparency** — three related, client-side-only additions (no new backend endpoints/schema): (1) adding a new goal now pre-fills "Monthly €" from the existing Monthly Cash Flow net figure (`_autofillGoalMonthlyFromCashflow`, only while the field is still empty); (2) each goal card gets an expandable breakdown (brokerage + fixed deposits + manual assets − liabilities = current net worth), reusing `GET /api/v1/networth/`; (3) the Wealth Simulator gained a "Monthly contribution" input (`fcStocksContribution`, feeds an ordinary-annuity term in `projectAccount`'s stocks bucket), auto-fillable from Cash Flow via the existing "Load from Net Worth" button (which now also pulls the mortgage monthly payment), plus a Goals card to overlay one or more goal targets on the projection chart — `computeGoalOverlays()` draws an in-chart dashed line/marker for goals within the projection's natural value range, or an off-chart annotation chip for goals far outside it (e.g. a €1M target against a much smaller projection) so the chart never gets flattened. 8 new unit tests (`web_client/js/tests/`).

**Recent (v2.5.11):** **Net Worth setup checklist + wizard** — new client-side-only "Setup checklist" card on the Net Worth page (`computeNetWorthChecklist()` in `pfm_analytics.js`, no new backend endpoints/schema — derived from data the page already fetches). Flags: home value missing when a mortgage exists, no bank/cash account entered, no monthly income/expense rows, and any fixed deposit past its maturity date but still `active`. Each gap links to a 4-step "Run setup wizard" modal that reuses the existing add-manual-asset/add-cashflow endpoints (skips steps already satisfied). 5 new unit tests (`web_client/js/tests/`).

**Recent (v2.5.10):** **Goals net-worth bug fix** — Goals' current-net-worth calc had drifted from the Net Worth page: it summed brokerage + manual assets/liabilities but silently omitted active fixed deposits, so `current_networth_eur`/`progress_pct`/projections on the Goals page could disagree with the Net Worth total. Extracted a shared `net_worth_eur(db)` in `portf_server/routers/networth.py` (brokerage + manual assets − liabilities + active deposits) and had `goals.py` call it instead of its own copy, so the two can't drift again. Also corrected Net Worth help text that claimed `monthly_cashflow` feeds Goals/Forecast projections — it doesn't (tracked as a follow-up feature).

**Recent (v2.5.9):** **FIFO tax engine correctness fixes** — (1) sells outside the report window now still consume FIFO lots (previously a prior-year sell left its lots intact, so later-year reports matched already-sold lots and misstated cost basis — affected tax-report, tax-estimate, tax-optimizer, `/tax/report`, chat `tax` tool, CLI); (2) fees now follow IRPF: `purchase_amount` includes purchase fees, `sell_amount` is net of sale fees, both allocated per share (gross `purchase_price`/`sell_price` unchanged; per-share fees scale through stock splits); (3) `calculate_tax_report` no longer filters by `user_id` — live transaction rows store `user_id` NULL (users table unused since API-key auth), so the `user_id=1` filter every caller passed returned an **empty report**: realised gains were silently 0 in tax-report/estimate/optimizer on live data; (4) legacy Google Sheets exporter's tax sheet fixed — it called `calculate_tax_report()` with no arguments and the swallowed TypeError made the tax sheet silently empty; (5) withholding at source on **interest** rows (Mintos P2P) now reported — tax-report gains `interest_withholding_eur` (helper `_year_withholding_eur`), the UI "Withholding paid" tile sums dividend + interest withholding, CSV export gains an interest row. Stale `portf:advisor:*` cache entries (computed while realised gains were empty) were cleared. 7 new tests. Unit suite now 728 passing. CHANGELOG.md refreshed (was stuck at v2.0.0) and now defers detailed history to this file.

**Recent (v2.5.8):** **Transaction-date FX for all tax figures** — new `market.get_fx_eur_on(db, currency, date)` (per-currency-year yfinance history, kv-cached as `mkt:fxhist:{CUR}:{year}`); tax-report lots now convert proceeds at sell-date FX and cost basis at purchase-date FX (`gain_loss_eur` includes the FX gain/loss, as IRPF requires) and each lot carries `purchase_date`; tax-estimate and tax-optimizer convert dividend + interest income per transaction at its own date/currency (previously raw-summed across currencies) and realised gains per lot at historical FX (tax-optimizer previously applied no FX at all). Also: native `<input type="date">` fields (text-import preview, chat-extracted transactions, deposits, etc.) now follow the user's configured date-format preference via a `lang`-attribute trick, instead of always rendering in the browser/OS locale's order. "What's New" page refreshed with a "Late June – July 2026" entry (agentic chat tool calling, generic CSV import, multi-currency transaction-date-FX tax report, on-demand price refresh) — it had been stuck at June 22 content.

**Recent (v2.5.7):** **Pre-public code review fixes** — 10 issues addressed: `_CRYPTO_YF_OVERRIDES` consolidated to single source in `price_updater.py`; `get_tax_report` changed to `def` (was illegally `async`); tax-estimate now applies `_fx()` per symbol for realised gains; `started_at` race in trigger-price-update fixed; CLI `update_prices` delegates to shared service; `LLMTransaction.asset_type` field added; generic CSV parser passes `asset_type` through to DB; `"amount"` removed from quantity synonyms; date-style detection added to generic CSV parser; `innerHTML` safety comments added. Integration test for tax-report FIFO shape added. `_parse_number` now accepts `decimal_style` param resolved file-wide via `_detect_decimal_style`.

**Recent (v2.5.6):** **MCP server: 4 new tools** — `portfolio_health` (AI-scored 5-category health from cache), `tax_estimate` (IRPF savings-base estimate), `goals` (progress + on-track status), `bookings` (cash deposits/withdrawals with totals). MCP server now has 18 tools total.

**Recent (v2.5.5):** **Import preview: editable symbol field with autocomplete** — symbol cell in the file/LLM import preview is now an editable input; typing triggers `AssetSearch` autocomplete against all known assets (fuzzy match on symbol/name/alias); corrected symbol is picked up on save. Works in both the Import/Export page and the Transactions file-import modal.

**Recent (v2.5.4):** **Multi-currency tax report** — `GET /api/v1/analytics/tax-report` now converts all amounts to EUR via `_fx()` at current rates; fixes field-name mismatches in `TaxTransaction` (`sell_quantity`/`sell_amount`/`purchase_amount` — previously returned zeros for proceeds and cost basis); per-transaction currency applied to dividend withholding sums; each realised lot carries `currency`, `proceeds_eur`, `cost_basis_eur`, `gain_loss_eur`; frontend table shows native + EUR amounts for non-EUR assets with a CCY badge; CSV download updated with EUR columns.

**Recent (v2.5.3):** **Generic CSV import** — `portf_manager/parsers/generic_csv_parser.py` accepts any broker's CSV with canonical columns (date, symbol, name, type, quantity, price, currency, fees, asset_type, notes); column headers are case-insensitive with multilingual synonyms; delimiter and decimal style auto-detected; type synonyms for buy/sell/dividend/interest in English and Spanish. `generic` broker added to import UI with format hint and downloadable template. 22 new unit tests.

**Recent (v2.5.2):** **On-demand price update** — `portf_manager/services/price_updater.py` extracts the update-prices logic from the CLI into a shared service; two new endpoints `POST /api/v1/analytics/trigger-price-update` (starts a background thread, returns 409 if already running) and `GET /api/v1/analytics/price-update-status`; "Refresh prices" button added to the dashboard header (next to the freshness chip) with a spinner while the background update runs, auto-refreshes the chip and dashboard when done.

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
- Schema v24 with automatic migrations on startup
- Tables: assets, transactions, portfolios, prices, bookings, dividends, watchlist, goals, research_notes, price_targets, networth snapshots, fixed_deposits, monthly_cashflow, app_settings, kv_cache, push_subscriptions, chat_sessions, price_update_runs, and more

### LLM Integration — ✅ Working
- Provider-agnostic abstraction (`llm_client.py`)
- **Default: auto-detect** — tries Ollama locally first (zero config), falls back to Gemini, then OpenRouter, then Anthropic
- Default models: `llama3.2` (Ollama), `gemini-2.5-flash` (Gemini), `claude-sonnet-4-6` (Anthropic)
- Config via `PORTF_LLM_PROVIDER` (`auto`/`ollama`/`gemini`/`openrouter`/`anthropic`) + `PORTF_LLM_MODEL` + `ANTHROPIC_API_KEY`
- Search-grounded research: Gemini/Anthropic implement `generate_with_search()`; research endpoint uses it when available, falls back to yfinance headlines
- Three use cases: transaction extraction, stock reports, chat/advisor

## Test Status

**721 passed, 0 failed, 6 skipped** (unit tests, excluding integration/e2e)

All tests passing as of 2026-07-04.

## Recent Changes (main)

See `git log --oneline` for full history. Notable milestones: agentic chat (v2.5), MCP server (v2.5+), generic CSV import (v2.5.3), price-update service (v2.5.2), multi-currency tax report (v2.5.4), pre-public code-review fixes (v2.5.7).

## Pending Work

### Low Priority
- [ ] **Web client smoke test** — Frontend verified working via API smoke tests (generic CSV import, tax-report, price-update-status, assets); full browser test not done

## Data Import Support

| Broker | Format | Parser | Status |
|---|---|---|---|
| IndexaCapital | CSV (semicolon, ISIN, EUR) | `indexacapital_csv_parser.py` | ✅ Working |
| MyInvestor | CSV (semicolon, Spanish) | `myinvestor_csv_parser.py` | ✅ Working |
| Coinbase | CSV | `coinbase_csv_parser.py` | ✅ Working |
| Mintos | CSV account statement | `mintos_csv_parser.py` | ✅ Working |
| Any broker | Generic CSV (canonical columns) | `generic_csv_parser.py` | ✅ Working |
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
