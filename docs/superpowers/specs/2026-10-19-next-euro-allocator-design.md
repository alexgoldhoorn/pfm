# Next-euro allocator — design

**Date:** 2026-10-19
**Status:** draft for review
**Area:** analytics/rebalance, net worth, action items, dashboard, MCP tools
**Builds on:** A (fund look-through, `docs/superpowers/specs/2026-09-16-fund-lookthrough-design.md`)
and B (balance sheet + retirement readiness,
`docs/superpowers/specs/2026-10-18-balance-sheet-retirement-design.md`).

## 1. Intent

When new money arrives the owner goes through the same chain every time:
look at pfm's rebalance analysis and action items, open a chat about the
best options given the holdings, paste a Simply Wall St report for any
single stock in the running, decide by hand how much is actually free to
invest, decide the split across index funds, ETFs and stocks, place the
orders.

Two links in that chain are weak. The amount is a mental estimate ("what is
free") that mixes the household's needs, the autónomo tax that is not yet
paid and an emergency margin. And the chat only knows what gets pasted, so
its advice is only as good as the paste.

C makes pfm answer both with rules it can show its work for: **"this much is
deployable this month, and why"** and **"put it here, and here is the reason
for each line"**. The chat then starts from that proposal through the MCP
instead of from scratch, and the owner's judgement and research still make
the final call.

## 2. Goals, non-goals, anti-goals

### Goals

- One deterministic figure, **deployable cash**, derived from the balance
  sheet (B), the measured household spending (B) and a tax-reserve rule, with
  its three parts always shown: liquid cash, tax reserve, buffer.
- A buffer rule with a target of **6 months** of household spending, a floor
  of **4** and a ceiling of **8** (the owner's "4 to 8"): nothing is
  deployable under 4 months, new money tops the buffer up to 6 first,
  anything above 8 is flagged as idle cash to invest.
- A **proposal**: concrete lines (fund, ETF, stock, coin) with an amount and
  a plain-language reason each, filling the most underweight buckets of the
  owner's allocation targets, never selling, respecting the antiaplicación
  window, avoiding overlapping funds (A), preferring a **traspaso** between
  funds over a sale when a fund bucket is overweight.
- Every rule is visible on the page and in the MCP output; nothing is an
  opaque score.
- Reachable from the chat: an MCP tool returns the deployable figure and the
  proposal with reasons, so the "deeper conversation" starts informed.
- No LLM anywhere in the computation.

### Non-goals

- Placing or simulating orders, broker integration.
- Region or sector targets (A reports exposure by region; C uses it only to
  explain and to avoid overlap, not to target).
- Invoice-level IVA accounting: pfm does not see invoices. The tax reserve is
  a rule over imported income rows, good enough for a monthly decision, not
  for the Modelo 303 itself (Xolo does that).
- Tax-loss harvesting, bond ladders, modelling the interest of the Plus
  account.
- Replacing the Rebalance page's sell planner (it stays for the "I want to
  sell something" case; C only buys and traspasos).
- Feeding the Finance Weekly Review or Telegram (follow-up in `~/mcp`).

### Anti-goals

- A deployable figure that is optimistic because a reserve was skipped: a
  missing input makes the figure "not computable" with the reason, never 0
  reserve.
- A proposal that names a fund or stock without a reason the owner can
  disagree with.
- A proposal that would breach the antiaplicación window or double an
  overlapping fund.
- Real amounts in the public repository.

## 3. Constraints

- No schema change: settings live in one `app_settings` JSON key
  (`allocator_settings`, §5.7); nothing else is persisted. The schema stays
  at 35.
- Blocking endpoints stay plain `def`. Tests cannot reach the network.
- The allocator reads existing services only: `balance_sheet.build_balance_sheet`,
  `retirement.spending_inputs` (the household spending target and its
  exclusions), `rebalance_planner.compute_before_state`/`merge_targets`,
  `exposure.compute_exposure`/`find_fund_overlaps`, `ongoing_charges`,
  the research tables, the watchlist, `price_targets`.
- The antiaplicación rule currently lives in `mcp/server.py`
  (`_wash_window_months`, `_wash_sale_analysis`). It moves to
  `portf_manager/services/wash_sale.py` and the MCP tool calls the service,
  so one rule serves both.
- A new MCP tool is registered in three places (gateway incl.
  `TOOL_SPECIALIST`, finance adapter, persona) after the merge.
- Web client: vanilla JS, pure helpers exported and tested, 390 px check.

## 4. Deployable cash

### 4.1 Definition

```
liquid_eur        = counted lines of the balance sheet's `cash` group
                    (bank balances, counted broker cash, manual cash lines
                    such as the Plus account)
tax_reserve_eur   = §4.2
buffer_target_eur = buffer_months_target × spending_target_monthly_eur
deployable_eur    = max(0, liquid_eur − tax_reserve_eur − buffer_target_eur)
buffer_months     = (liquid_eur − tax_reserve_eur) / spending_target_monthly_eur
```

`spending_target_monthly_eur` is B's measured household spend over the same
months, with one difference: the retirement plan leaves the autónomo
social-security contribution out (it stops at retirement), the buffer puts
it back in because it is a fixed monthly outflow today. So C calls
`retirement.spending_inputs` with the plan's exclusions minus
"Social Security", and the response names the figure "household spending
incl. autónomo quota" next to the retirement figure so the difference is
visible. Fewer than 6 complete imported months → `status: "insufficient_data"`.

Buffer status from `buffer_months`: `rebuild` below `buffer_months_min` (4),
`topping_up` from 4 to the target (6), `ok` from the target to
`buffer_months_max` (8), `idle` above 8. In `rebuild` and `topping_up`
`deployable_eur` is 0 by the formula; in `idle` the response also carries
`idle_eur = liquid − reserve − max × spend`, the amount sitting above the
ceiling.

Deposits are not liquid (they are the deposits group), but a deposit
maturing within 30 days is listed under `coming_in` so the owner sees it.

### 4.2 Tax reserve (autónomo)

Settings: `iva_pct` (21), `irpf_pct` (20), `irpf_retention_pct` (0: the
share of each invoice the client already withholds; a professional whose
clients withhold 15 % sets 15 and the Modelo 130 part shrinks accordingly),
`income_category` ("Income"), `work_income_merchants` (list of merchant
strings; empty = every merchant), `tax_paid_categories` (["Income Tax"]).

**Which rows are work income.** The "Income" category in the owner's bank
imports also holds the partner's transfers, a tax refund and bank interest,
so the reserve must not read the whole category. Work income = non-transfer,
positive rows in `income_category` whose `merchant` is in
`work_income_merchants`. The settings form lists every merchant seen in that
category over the last 12 months with a checkbox, and the deployable
response lists the merchants it counted, so a wrong tick is visible at once.
With the list empty the response carries the warning "all income counted as
work income: tick the work merchants in the settings".

For the current quarter (Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec):

```
gross    = Σ work-income rows dated in the quarter
iva      = gross × iva_pct / (100 + iva_pct)                      # Modelo 303
net      = gross − iva
irpf     = net × irpf_pct / 100 − net × irpf_retention_pct / 100  # Modelo 130
accrued  = max(0, iva + irpf)
```

**Which payments settle a quarter.** A quarter's 303/130 are paid in the
next quarter, so a row in `tax_paid_categories` dated within 30 days after a
quarter end counts as that previous quarter's payment; any other tax row
(the annual IRPF settlement in June, a complementary payment) is ignored by
the reserve. The previous quarter stays reserved (`accrued` minus such a
payment, floored at 0) until a payment is seen or its due date (the 20th of
the month after the quarter; 30 January for Q4) is 30 days past. The reserve
is `current_accrued + previous_remaining`. Modelo 130 is cumulative over the
year in law; the flat per-quarter estimate is the conservative side when
income is steady and is accepted as an estimate. Social-security
contributions are ordinary monthly spending and are not reserved.

The response shows `gross`, `iva`, `irpf`, `paid`, the merchants counted and
which quarters are in the reserve, so the figure can be checked against
Xolo in a minute.

### 4.3 Endpoint

`GET /api/v1/allocate/deployable` →

```json
{
  "status": "ok" | "insufficient_data",
  "as_of": "2026-10-19",
  "liquid_eur": 0, "liquid_lines": [{"id": "bank:11", "label": "...", "amount_eur": 0}],
  "tax_reserve": {"reserve_eur": 0, "quarters": [{"quarter": "2026-Q4", "gross_eur": 0,
                   "iva_eur": 0, "irpf_eur": 0, "paid_eur": 0, "accrued_eur": 0, "due": "2027-01-20"}]},
  "buffer": {"monthly_spend_eur": 0, "months_used": 12, "target_months": 6, "min_months": 4,
             "max_months": 8, "target_eur": 0, "months": 0.0, "status": "ok", "idle_eur": 0},
  "deployable_eur": 0,
  "coming_in": [{"label": "deposit …", "amount_eur": 0, "date": "…"}],
  "stale_lines": 0,
  "warnings": ["…"]
}
```

Warnings always list: stale cash lines counted at their stale value; a
negative ledger line left out; no income rows this quarter (reserve 0 from
rows, not from knowledge); the buffer rule in words.

## 5. The proposal

### 5.1 Inputs

- `amount_eur`: the deployable figure by default; the owner can pass another
  amount (query `amount`) to see what pfm would do with it.
- Targets: the existing `allocation_targets` by raw `asset_type`
  (`merge_targets`). The allocator refuses with `status: "targets_incomplete"`
  and a reason when a held asset type with more than 1 % of the portfolio has
  no target row or the targets do not sum to 100 (the planner's
  `validate_target_sum` error becomes that status, never a 500), and raises a
  `low` Action Item saying which. `cash` and `bond` may be set to 0
  explicitly (the owner's cash is his fixed-income sleeve). Buckets are the
  raw `asset_type` because that is what the targets are keyed by; the fund
  heuristic of §5.4 (type `mutual_fund`, exchange "Funds", or a fund word in
  the name of a non-ETF) is used only for the traspaso rule and for choosing
  fund-style versus stock-style ranking inside a bucket.
- The `cash` bucket, and any bucket with no investable candidate, is left
  out of the split and its target weight is renormalised over the investable
  buckets; `total_value_eur` is the positions value from
  `compute_before_state` (live FX), never B's net worth (bank cash and the
  home are not part of the mix).
- Current values per asset type: `compute_before_state` (the rebalance
  planner's own numbers, live FX).
- Managed accounts: a settings list `managed_portfolio_ids` (default: the
  portfolio named like "Indexa"). The holdings of a managed portfolio are
  one candidate, "contribution to <portfolio>", in the bucket of its funds,
  with the account's value-weighted TER, because its mix cannot be bought
  line by line. Every line carries `portfolio_id`/`portfolio_name`, the
  account where the order is placed (a held asset's portfolio; a watchlist
  name defaults to the broker that holds most of that bucket).
- Candidates: every held asset with a price, plus watchlist entries that
  resolve to an asset; each tagged with its bucket (`asset_type`), current
  weight, TER (`fund_profiles.ongoing_charges_pct`), overlap group (A's
  `find_fund_overlaps`), latest research note (`conviction`, `buy_below`,
  `fair_value`), price target (`price_targets.buy_below`), research report
  recommendation, and the antiaplicación state from §5.4.

### 5.2 Bucket split

```
total_after = total_value_eur + amount_eur
for each bucket with target_pct > 0:
    gap_eur = target_pct/100 × total_after − current_value_eur
underweights = buckets with gap_eur > 0
fill = water-filling: distribute amount_eur across underweights in
       proportion to gap_eur, capped at each gap (never over-fill),
       then any remainder in proportion to target_pct
drop lines under min_line_eur (100); their amount goes back into the pool
```

A bucket that is overweight gets nothing. If every bucket is at or above
target the whole amount goes in proportion to `target_pct` (keeps the mix).
The response shows the before/after weight per bucket.

Order of operations: the traspasos of §5.5 are computed first on the
current state (they move value between fund buckets without new money),
and the water-filling of new money runs on the post-traspaso state, so the
two cannot fill the same gap twice. `before_pct` is the current state,
`after_pct` includes both.

`amount` above `deployable_eur` is allowed (the owner may know about money
pfm does not see) and produces the warning "amount exceeds deployable cash
by X"; `status: "nothing_to_deploy"` applies only when no amount was given
and deployable is 0.

### 5.3 Picking the lines in a bucket

Each bucket's amount is split over at most `max_lines_per_bucket` (2)
candidates, ranked by the rules below; each line carries the list of reasons
that ranked it and, for the runner-up, why it lost. Ties keep the larger
existing position (adding to a holding beats opening one).

- **Funds and ETFs** (`etf`, `mutual_fund`, `index`): exclude any fund in a
  `consolidation_candidate` overlap group other than the group's largest
  member (reason: "overlaps with X, which is the bigger holding"); rank by
  lowest TER (a fund with no TER ranks after those with one, reason: "TER
  unknown, enter it on the Fees tab"); then by the region it adds the most
  of where the portfolio is lightest (from `by_region`, informational, not
  a target); unhedged before hedged when both exist for the same index.
- **Stocks**: exclude anything blocked by antiaplicación (§5.4) and any
  stock whose weight after the buy would exceed `max_single_stock_pct` (5)
  of the portfolio (reason given); rank first the names whose current price
  is at or below their `buy_below` (price target, then research note), then
  by research recommendation (`BUY` from the latest report, note
  `conviction` descending), then by lowest current weight. A stock with no
  research at all is listed under `needs_research` rather than proposed,
  with the hint that the chat can research it; the bucket's amount then
  goes to the researched names, or stays as `unallocated_eur` with the
  reason "no researched candidate under the price target".
- **Crypto**: split across held coins in proportion to current weights
  (DCA); no research rules.
- **Bonds**: only when a `bond` target exists; rank by nearest maturity.

### 5.4 Antiaplicación

`services/wash_sale.py` exposes `blocked_assets(db, today) -> dict[asset_id,
{"sold_on", "loss_eur", "safe_from"}]`: assets sold at a loss inside their
window (2 months listed, 1 year unlisted fund units, crypto excluded; the
heuristics now in `mcp/server.py`). A blocked candidate is listed under
`blocked` with the date it becomes safe and is never proposed. The MCP
`wash_sale_check` tool is rewired to the service with no change in output.

### 5.5 Traspasos

When a `mutual_fund` bucket is overweight by more than `traspaso_threshold_pp`
(5 percentage points) and another fund bucket is underweight, the response
adds a `traspasos` entry: move `min(overweight_eur, underweight_eur)` from the
fund with the highest TER (or the smaller member of an overlap group) to the
best-ranked fund of the underweight bucket, with the reason "traspaso keeps
the gain untaxed; selling this amount would realise N € of gain" (FIFO lots
for the moved amount only, via `rebalance_planner.fifo_gain_eur`). A fund is
traspasable when it passes the fund heuristic of §5.4 and is not an ETF;
ETFs are not traspasable in Spain and never appear here. A managed
portfolio (§5.1) is never the source of a traspaso.

### 5.6 Endpoint

`GET /api/v1/allocate/next-euro?amount=` →

```json
{
  "status": "ok" | "nothing_to_deploy" | "targets_incomplete" | "insufficient_data",
  "deployable": { …the §4.3 object… },
  "amount_eur": 0,
  "buckets": [{"asset_type": "etf", "target_pct": 30, "before_pct": 0, "after_pct": 0,
               "gap_eur": 0, "allocated_eur": 0}],
  "lines": [{"asset_type": "etf", "asset_id": 1, "symbol": "…", "name": "…",
             "amount_eur": 0, "reasons": ["…"], "runner_up": {"symbol": "…", "why_not": "…"}}],
  "traspasos": [{"from_symbol": "…", "to_symbol": "…", "amount_eur": 0, "reasons": ["…"]}],
  "blocked": [{"symbol": "…", "safe_from": "…", "loss_eur": 0}],
  "needs_research": [{"symbol": "…", "weight_pct": 0}],
  "unallocated_eur": 0,
  "chat_summary": "…",
  "warnings": ["…"]
}
```

`chat_summary` is a plain-text rendering of the whole answer (the same text
the MCP tool prints), so the page has a "Copy for chat" button.

### 5.7 Settings

`GET|PUT /api/v1/allocate/settings`, one `app_settings` JSON key
`allocator_settings`, validated by a typed model:

```json
{"buffer_months_target": 6, "buffer_months_min": 4, "buffer_months_max": 8,
 "iva_pct": 21.0, "irpf_pct": 20.0, "irpf_retention_pct": 0.0,
 "income_category": "Income", "work_income_merchants": [],
 "tax_paid_categories": ["Income Tax"],
 "managed_portfolio_ids": [],
 "min_line_eur": 100, "max_lines_per_bucket": 2, "max_single_stock_pct": 5.0,
 "traspaso_threshold_pp": 5.0}
```

Validation: `0 < min ≤ target ≤ max ≤ 24`, percentages 0–100, amounts ≥ 0,
portfolio ids must exist. On first read with an empty
`managed_portfolio_ids`, the loader fills it with the id of a portfolio
whose name contains "indexa" (case-insensitive) if there is one, and
reports that default in the response.

## 6. Surfaces

- **Dashboard**: a "Next euro" card in the attention strip: the deployable
  figure with its three parts on one line, the buffer status badge, and the
  top three proposed lines; a link to the full view. Hidden when
  `insufficient_data`.
- **Rebalance page**: a new "Next euro" tab with the deployable breakdown
  (liquid lines, tax quarters, buffer gauge against the 4–8 band), an amount
  input, the bucket table (before/after), the lines with reasons, traspasos,
  blocked, needs-research, and the "Copy for chat" button; settings form.
- **Action Items**: `idle_cash` (`low`, buffer above the ceiling, amount),
  `buffer_low` (`medium`, below the floor), `allocation_targets_incomplete`
  (`low`, naming the asset types). Category `allocate`.
- **MCP**: `next_euro(amount: float | None = None) -> str` printing the
  deployable breakdown and the proposal with reasons; `wash_sale_check`
  rewired to the shared service. The finance persona reads `next_euro`
  before advising on new money.
- Help text for the card, the buffer band, the tax reserve and the reasons.

## 7. Decided by the owner

- The buffer is 4 to 8 months of household spending.
- pfm suggests concrete lines with reasoning; the deeper conversation
  happens in the chat afterwards.
- Account roles: income into Caixa Enginyers; Abanca carries the household
  and the autónomo taxes; MyInvestor is the stock/ETF broker with the Plus
  account; Indexa is pure index. (Recorded for context; the computation
  reads the balance sheet, not account names.)
- Cash is the owner's fixed-income sleeve (earlier decision).

## 8. Calls made in this design

- Buffer target 6 with the owner's 4/8 as floor and ceiling; deployable uses
  the target, so the figure is 0 until the buffer is back at 6.
- Tax reserve as 21/121 of gross work income plus 20 % of the net, per
  quarter, less tax rows paid; the previous quarter stays reserved until
  its due date.
- Buckets are the existing `allocation_targets` by asset type; no new
  target dimension.
- Water-filling of underweights, never selling, min line 100, at most two
  lines per bucket, single-stock cap 5 %.
- Fund ranking by overlap then TER; stock ranking by price target, then
  research, then weight; unresearched stocks are listed, not proposed.
- Traspaso only between mutual funds, threshold 5 pp.
- The chat summary text is produced server-side so the page and the MCP
  tool print the same words.
- The buffer counts the autónomo social-security contribution as household
  spending (the retirement plan does not).
- Work income is the subset of the Income category the owner ticks by
  merchant; the partner's transfers, refunds and interest are not reserved
  against. Client IRPF retention defaults to 0 and is a setting.
- A tax row within 30 days after a quarter end settles that quarter; other
  tax rows are ignored by the reserve; Modelo 130 is a flat per-quarter
  estimate.
- Buckets are raw asset types; the fund heuristic decides traspaso
  eligibility and ranking style only. Cash and candidate-less buckets are
  renormalised away; the denominator is the positions value.
- Indexa (any managed portfolio) is one contribution line, never split by
  fund, never a traspaso source; every line names the account to place the
  order in.
- Traspasos are computed before the new-money split so they cannot
  double-fill a gap.
- No schema change; targets that do not sum to 100 and amounts above
  deployable are handled as described, not as errors.

## 9. Left to the builder

- Exact copy of the reason strings and the help entries.
- Where the amount input and settings form sit on the tab.
- Test fixtures (invented names and round figures).
- Whether `blocked_assets` reuses the tax-report FIFO lots directly or a
  thinner lot scan.
