# Research scorecard, managed-aware rebalancing, review feed — design

Date: 2026-11-04. Follows the 2026-11-01 spec (managed portfolios, return
attribution, decision journal; schema v38). Three small features, one branch.

## Intent

1. **Managed-aware rebalancing.** Since v38 a portfolio can be `is_managed`
   (robo-advisor: Indexa Capital). Its holdings stopped raising price signals,
   but the rebalance analysis and plan still propose trades inside it, which
   the owner cannot execute. Managed holdings must keep their weight in the
   allocation and never appear in proposed trades.
2. **Research history and scorecard.** `research_reports` holds one row per
   asset and is overwritten on every refresh, so nothing says how good the
   LLM targets have been. Keep every report (append-only history) and score
   each one against what the price did, two ways: at the report's horizon and
   at the next report for the same symbol. The owner's own manual research
   notes already carry history (`research_notes.price_at_save`), so they are
   scored too and labelled `manual`.
3. **Sunday finance review feed.** `~/mcp/scripts/finance_review.py` gets the
   week's return attribution and the open/due decisions, so the Telegram
   review says what drove the week and which calls are due for review.

## Non-goals

- No LLM-written verdicts on the scorecard; it is arithmetic.
- No new write tools on the MCP gateway.
- No change to how reports are generated, except an optional `horizon_months`
  key in the LLM JSON (default 12 when absent).
- No scoring of `buy_below`/`sell_above` bands; only `fair_value` and the
  recommendation are scored. The bands are stored in history for later.

## 1. Managed-aware rebalancing

`portf_manager/services/rebalance_planner.py`:

- New helper `managed_only_symbols(db, transactions) -> tuple[set[str], list[str]]`:
  symbols whose open quantity sits only in managed portfolios (use
  `portf_manager.positions.self_managed_held_asset_ids` and
  `portf_server.routers.research.managed_portfolio_ids`, or move
  `managed_portfolio_ids` into the positions module if importing the router
  from a service is awkward; a service must not import a router). Returns
  the upper-cased symbols plus the managed portfolio names.
- `build_plan`: before building candidates, union those symbols into the
  effective `excluded_symbols` (they count in the allocation but are never
  sold or bought). Record them in the returned `inputs` as
  `managed_symbols` (sorted list) and add one warning when the set is not
  empty: `"{n} holdings in managed portfolios ({names}) are kept at their
  current weight and never traded: {symbols}"`. When `portfolio_id` names a
  managed portfolio, the plan has no trades and a warning saying the
  portfolio is managed.
- A symbol held both in a managed and in a self-managed portfolio stays
  tradable (the self-managed lot can be sold); the plan's quantity cap for
  it is the self-managed quantity, not the total. If the planner cannot
  cap per portfolio cheaply, keep the total and add the symbol to a
  `warnings` line saying part of the position is managed. Choose the
  cheaper implementation and document which.

`portf_server/routers/rebalance.py` `GET /analysis`: add a top-level
`managed` block: `{"value_eur", "pct", "portfolios": [names]}` of the
holdings in managed portfolios (0 / empty when none). Allocation and
actions are unchanged (type-level).

`mcp/pfm_mcp/tools/*` `rebalance_analysis`: print one line
`Managed (not tradable): €X (Y%) in Indexa Capital` when `managed.value_eur > 0`.
The chat tool for rebalance returns the dict, nothing to change.

Tests: `tests/unit/test_managed_rebalance.py` — managed-only symbol never
appears in sell or buy trades, still counts in `before` allocation; mixed
symbol stays tradable; managed `portfolio_id` yields no trades; `/analysis`
managed block; MCP renderer line.

## 2. Research history and scorecard (schema v39)

### Table `research_history` (in BOTH `_create_all_tables` and `_migrate_to_v39`)

| column | note |
|---|---|
| id | PK |
| asset_id | REFERENCES assets(id) ON DELETE CASCADE, NOT NULL |
| symbol | NOT NULL |
| generated_at | TEXT ISO, NOT NULL |
| source | `llm` (single research), `bulk` (bulk refresh), `manual` is NOT stored here (manual notes live in `research_notes`) |
| recommendation | BUY/HOLD/SELL or NULL |
| confidence | high/medium/low or NULL |
| fair_value, buy_below, sell_above | REAL nullable, in `currency` |
| currency | asset currency at the time |
| price_at_report | latest stored price on or before `generated_at`, nullable |
| horizon_months | INTEGER NOT NULL DEFAULT 12 |

Index `idx_research_history_symbol (asset_id, generated_at)`.

`db.upsert_research_report(...)` gains keyword args `source="llm"`,
`buy_below=None`, `sell_above=None`, `horizon_months=12` and appends one
history row on every call (price_at_report from `get_latest_price` at call
time, currency from the asset). Callers in `routers/research.py` (single and
bulk) pass what they have. `services/research.py` prompt JSON gets an
optional `"horizon_months": <int, 6-36>`; parse it when present, else 12.

**Migration v39 backfill:** insert one history row per existing
`research_reports` row (`source='backfill'`, `generated_at` from the report,
`price_at_report` = last stored price on or before that date, bands from
`report_json` when present). Guard: skip the backfill when the table already
has rows. `DATABASE_VERSION = 39`; update every `== 38` pin
(`tests/test_database.py`), `tests/unit/test_schema_v38.py` compares with
`DATABASE_VERSION` already; add `tests/unit/test_schema_v39.py` (fresh DB,
migration from 38 with a seeded report → one backfilled history row,
idempotent).

### Scoring (`portf_manager/services/research_scorecard.py`)

`compute_scorecard(db, symbol=None, today=None) -> dict`.

Entries = `research_history` rows (source as stored) + `research_notes`
rows with a non-null `fair_value` (source `manual`, `price_at_report` =
`price_at_save`, recommendation derived: BUY if fair_value > price × 1.10,
SELL if < price × 0.90, else HOLD; horizon 12). Sorted per symbol by date.

Per entry:

- `implied_return_pct = (fair_value / price_at_report - 1) × 100` (None
  when either is missing).
- Two comparison points, each `{date, price, realised_return_pct,
  target_error_pct, direction_hit, reached_target}` or None when not yet
  scorable:
  - `at_horizon`: `generated_at + horizon_months`; scorable when that date
    ≤ today; price = last stored price on or before it.
  - `at_next`: the next entry's `generated_at` for the same symbol (any
    source); price = that entry's `price_at_report`, else last stored price
    on or before it.
  - `target_error_pct = (price - fair_value) / fair_value × 100`.
  - `direction_hit`: BUY → realised > 0; SELL → realised < 0; HOLD →
    |realised| ≤ 10. None when the recommendation is missing.
  - `reached_target`: BUY → max stored price between the report date and
    the point ≥ fair_value; SELL → min ≤ fair_value; HOLD → None. Use
    `db.get_price_history(asset_id, start_date, end_date)` once per entry.
- `now`: `{date, price, realised_return_pct}` from the latest stored price,
  for pending entries (so the page shows "so far").

Aggregates, each computed separately for `at_horizon` and `at_next` over the
scorable entries: `n`, `direction_hit_rate_pct`, `median_abs_target_error_pct`,
`mean_implied_return_pct`, `mean_realised_return_pct`, `reached_target_rate_pct`.
Breakdowns `by_recommendation`, `by_confidence`, `by_source`, `by_asset_type`
(same fields, `n ≥ 1`). `best` / `worst` five entries by realised return at
the horizon when scorable, else at next. `pending` count and `total`.
Return `{"as_of", "totals": {"at_horizon": …, "at_next": …}, "by_recommendation",
"by_confidence", "by_source", "by_asset_type", "best", "worst", "entries",
"total", "pending", "method": "<one sentence>"}`.

### Surfaces

- `GET /api/v1/research/scorecard?symbol=` in `routers/research.py`,
  **registered before `/{symbol}`** (single-segment rule). Sync `def`.
- `GET /api/v1/research/history/{symbol}`: the raw entries for one symbol,
  newest first (history + manual notes merged), for the workbench.
- Chat tool `get_research_scorecard(symbol=None)` in `chat_tools.py`
  (count pin in `tests/unit/test_chat_tools.py` → 28).
- MCP tool `research_scorecard(symbol=None)` in `mcp/pfm_mcp/tools/research.py`,
  README row, `EXPECTED_TOOLS` → 35, output test in `mcp/tests/`.
- Web: a **Scorecard** tab in `#researchTabs` on the Research page: totals
  for the two comparison points, breakdown by recommendation and confidence,
  best/worst, and a table of entries (symbol, date, source, rec, target vs
  price then, horizon/next/now returns, hit marks). `METRIC_HELP` entry.
  Pure helpers (`scorecardHitBadge`, `scorecardRowHtml`) exported on
  `window` and tested in `web_client/js/tests/scorecard.test.mjs`.
- Docs: `docs/features/research-rebalance.md` gains "Research history and
  scorecard"; `CLAUDE.md` schema 39 + one-line gotchas; `PROJECT_STATUS.md`.

## 3. Sunday finance review feed (`~/mcp/scripts/finance_review.py`)

- Fetch `GET /api/v1/analytics/attribution?start=<today-7d>&end=<today>` and
  `GET /api/v1/decisions/?status=open` through the existing `_pfm` helper
  (both may be None on failure; degrade like the other sections).
- Prompt: a "WEEK ATTRIBUTION" block (gain €, return %, top 3 contributors
  and detractors with € and %, by broker) and a "DECISIONS" block (due ones
  first: title, action, symbol, decided_on, price then → now %, review_on;
  then the other open ones, max 8). Instruct the model to name what drove
  the week from the attribution rather than from value changes, and to
  mention due decisions as items to revisit, never to decide them.
- Telegram message: one line "Week: +€X (+Y%) — up: A, B; down: C" and
  "Decisions due: N" when N > 0. Trilium note: the two tables.
- € figures: this script already prints € in its own output (not the
  prompt); keep the existing rule — check how the script handles amounts in
  the prompt today and follow it.
- Tests in `~/mcp/scripts/tests/test_finance_review.py`: the two fetches
  are requested, the prompt contains both blocks from fixture payloads, the
  Telegram line renders, a None payload degrades without raising.

## Shared constraints

- Same rules as the 2026-11-01 spec: develop in the worktree
  `~/repos/pfm-scorecard`; never edit `~/repos/pfm`; black, flake8, Google
  docstrings, comments on the line before; invented fixtures only; unit
  suite via `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest`.
- Merging into main is the deploy and migrates the live DB to v39: back up
  first. After the merge: register `research_scorecard` on the remote
  gateway, the finance adapter and persona; rebuild the web image with
  `WEB_PORT=8080`.
