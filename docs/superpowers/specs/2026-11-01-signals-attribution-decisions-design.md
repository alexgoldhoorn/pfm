# Managed portfolios, return attribution and a decision journal — design

Date: 2026-11-01. Three self-contained improvements chosen from the live
backlog: the Action Items list on 2026-11-01 carried 20 "crossed a price
target" rows, most of them on funds inside the Indexa account that the owner
cannot trade (finance memory 2026-10-01: "pfm should exclude Indexa portfolio
3 from price signals"); the agreed roadmap (2026-09-16, item D) still listed
return attribution and a decision journal; and the owner works money decisions
through with Claude, so each feature ships a read-only MCP tool.

## 1. Managed portfolios: quieter price signals

**Intent.** A robo-advisor account (Indexa) is tracked in pfm for net worth and
exposure, but its holdings are not the owner's to buy or sell. Price-target
crossings and "not re-valued in 90 days" nags on those holdings are noise that
hides the signals that are actionable.

**Decided.**
- Schema v38 adds `portfolios.is_managed INTEGER NOT NULL DEFAULT 0`
  (someone else makes the trades). Editable on create and update
  (`PortfolioCreate`/`PortfolioUpdate`), shown in list responses and the
  web Portfolios form ("Managed by the broker" checkbox).
- `compute_price_target_alerts` skips an asset whose entire held quantity sits
  in managed portfolios. The same function feeds `/research/alerts/check`, the
  20:05 Telegram price-alert cron and the Action Items check, so all three go
  quiet together. An asset also held in a self-managed portfolio still alerts.
- `check_stale_research` ignores holdings that exist only in managed
  portfolios.
- `check_price_alerts` emits one item per symbol: a symbol that is both on
  the watchlist (buy zone) and crossed a research target gets the watchlist
  item with the target noted in its detail, instead of two rows.
- `list_portfolios` (MCP) prints `managed` on such accounts.

**Non-goals.** No change to the allocator or exposure checks (fund overlap
across Indexa and MyInvestor is a real, actionable finding). No automatic
detection of managed accounts: the flag is set by hand.

## 2. Return attribution

**Intent.** Performance shows the portfolio's total return; nothing shows what
drove it. Attribution answers "which holdings, asset types and brokers made or
lost the money over this period" with flow-adjusted gains in EUR.

**Decided.**
- `portf_manager/services/attribution.py::compute_attribution(db, start, end,
  fx_now, fx_on, portfolio_id=None)`. Per asset: value at `start` and at `end`
  (held quantity from transactions up to and including that day × the last
  stored price on or before it × that day's FX), plus the flows in
  `(start, end]` converted at transaction-date FX: buys, sells, dividends and
  interest (net of their `tax`). `gain = end − start − buys + sells + income`.
  A holding with no stored price on or before a boundary is valued at its EUR
  cost (neutral) and flagged `priced: false`.
- Groupings: by asset (sorted by gain, with `weight_start_pct` and
  `contribution_pct` = gain / total start value), by `asset_type`, by
  portfolio. Totals include the whole-period gain and a
  `return_pct` over the start value plus net invested.
- `GET /api/v1/analytics/attribution?period=1m|3m|ytd|1y|all&start=&end=&portfolio_id=`.
  Explicit `start`/`end` win over `period`; `end` defaults to today;
  `all` starts at the first transaction.
- MCP tool `return_attribution(period, portfolio_id)`; chat tool
  `get_return_attribution`; a "What drove the return" card on the Analytics →
  Performance tab (period buttons, top contributors and detractors, by type).

**Non-goals.** No benchmark-relative (Brinson) attribution; no intraperiod
time weighting of flows beyond the start/end valuation.

## 3. Decision journal

**Intent.** Decisions ("hold AMD below the sell gate", "keep monthly DCA, no
lump sum", "sell Fastned") are made in chats and digests and then lost. A
journal records the decision, why, what was expected, and when to review it,
and later the outcome. The price at decision time makes the review honest.

**Decided.**
- Schema v38 adds `decisions` (id, decided_on, symbol, asset_id, action in
  buy/sell/hold/dca/rebalance/traspaso/other, title, rationale, expectation,
  review_on, price_at_decision, currency, outcome, outcome_rating in
  good/bad/neutral/unclear, reviewed_at, source, created_at, updated_at).
- Router `/api/v1/decisions/`: list (`status=open|reviewed|all`, `symbol`),
  create (resolves `symbol` to an asset and its latest stored price when
  `price_at_decision` is omitted), get, put, `POST /{id}/review` (outcome +
  rating stamps `reviewed_at`; rating alone allowed), delete. Reads add
  `price_now` and `price_change_pct` when the asset has a stored price.
- Action item `decision:review:{id}` (medium) when `review_on` has passed and
  the decision is not reviewed.
- Chat tools `get_decisions` and `record_decision` (the second write-capable
  chat tool, after `set_action_item_status`; it writes the journal only).
- MCP tool `decisions(status, symbol)` (read-only).
- Web page "Decisions" in the sidebar: add form, list with filter, inline
  review, delete; dashboard untouched.

**Non-goals.** No link from a decision to the transaction that executed it; no
LLM-written outcomes.

## Shared constraints

- Production runs the live checkout; this work is done in a worktree and the
  merge (which migrates the live DB to v38) needs the owner's go-ahead and a
  DB backup first.
- The remote gateway imports the live `pfm.server`, so the gateway, finance
  adapter and persona registrations of the two new MCP tools happen after the
  merge, not in this branch.
