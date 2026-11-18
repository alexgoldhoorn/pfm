# Cost control, round 3: action-item triage, net-worth history, broker import-file, recurring resolutions, lumpy pace, month close

Date: 2026-11-18. Branch `feat/cost-control-3`. Builds on
`2026-11-15-cost-control-design.md` and `2026-11-17-cost-control-2-design.md`;
every shared rule and anti-goal there still holds (no LLM in a calculation, a
failing source is a warning and `None`, never a zero or a 5xx, no flattering
number for a month without imports, one-off rows and transfers stay out of
every average, "data through" is the earliest last import over the accounts in
the window).

## Why

Use of rounds 1 and 2 on real data showed: the Action Items list is long and
most of it is one family repeated (price-target crossings, pace lines), so the
few items that matter are buried; there is no history of the full balance
sheet, so "are we getting richer and why" has no answer; broker imports go
stale because only bank statements have a one-call import; a recurring charge
that was cancelled or moved to a card can only be snoozed; the pace warns on
categories that are spent in a few large months; and month end has all the
parts but no sequence and no "done" mark.

## Goals

1. Action Items triage: families folded into one group item, a priority order, a top 5.
2. Net-worth history: stored snapshots of the balance sheet and a "what changed" split.
3. A one-call broker import a host job can use (the drop folder is phase 2).
4. Recurring resolutions: mark a series cancelled or paid elsewhere; a subscriptions view.
5. Pace stays quiet for lumpy categories.
6. Month close: a checklist, a stored close with its figures, a saved report.

## Non-goals

- No card-statement import. No new bank or broker format. No LLM parser in the one-call import.
- No backfill of net-worth history: there is no past data, and a reconstructed figure would be a guess.
- Chat gets no new write tool.
- No cron, Telegram or gateway change in this branch (phase 2, after the merge, in `~/mcp`).

## Approach

One branch, one schema version (v47) carrying all three new tables, sequential
batches: (1) schema + pace; (2) triage + recurring, with the broker import in a
side worktree at the same time; (3) net-worth history + month close; (4) web
client; (5) chat, MCP, docs. Considered and dropped: one schema version per
feature (three live migrations for nothing), and storing resolutions and
closes in `app_settings` like the acks (they need queries by month and by
merchant, and a close holds a figures snapshot).

`PROJECT_STATUS.md` and `CLAUDE.md` are edited only in batch 5.

## Schema v47

All three in `_create_all_tables` and `_migrate_to_v47`. Nothing is backfilled.

`networth_snapshots`

| column | notes |
|---|---|
| `snapshot_date` | TEXT `YYYY-MM-DD`, PRIMARY KEY (one per day; a second write the same day replaces it) |
| `net_worth_eur` | REAL NOT NULL |
| `investments_eur`, `cash_eur`, `property_eur`, `other_assets_eur`, `liabilities_eur` | REAL NOT NULL; `liabilities_eur` positive; the five reconcile to `net_worth_eur` to the cent |
| `detail` | TEXT JSON: the balance sheet's group totals as returned, for later use |
| `warnings` | TEXT JSON list: the balance sheet's warnings at that time (stale lines etc.) |
| `created_at` | TEXT |

`recurring_resolutions`

| column | notes |
|---|---|
| `id` | PK |
| `portfolio_id` | INTEGER NOT NULL (the bank account of the series) |
| `merchant` | TEXT NOT NULL; UNIQUE(`portfolio_id`, `merchant`) |
| `status` | `cancelled` \| `paid_elsewhere`, CHECK |
| `since` | TEXT `YYYY-MM-DD`: the date of the series' last charge when it was resolved |
| `note` | TEXT |
| `created_at`, `updated_at` | TEXT |

`month_closes`

| column | notes |
|---|---|
| `month` | TEXT `YYYY-MM`, PRIMARY KEY |
| `closed_at` | TEXT |
| `figures` | TEXT JSON: the figures at close (see §6) |
| `checklist` | TEXT JSON: the checklist as it stood at close |
| `report_id` | INTEGER nullable, the saved report (`reports.id`); not a hard FK |
| `notes` | TEXT |

DB methods follow the existing naming (`create_/get_/list_/update_/delete_`).

## 1. Action Items triage

Service only (`services/action_items.py` plus a new `services/action_item_groups.py`
for the pure parts). The checks themselves do not change, and neither do item ids.

- **Families.** A family is a set of items of one kind: `price_targets`
  (ids `research:*` and `watchlist:*`), `budget_pace` (`budget:pace:*`),
  `budget_overrun` (`budget:line:*`), `recurring_missed` (`recurring:missed:*`),
  `fund_exposure` (the consolidation items), `stale_imports`, `cash_freshness`
  (the balance-sheet freshness items). The mapping is one table in the new
  module, by id prefix; an item in no family stays alone.
- **Folding.** When a family has 3 or more items with status `open`, they are
  replaced by one group item: `id` `group:<family>`, `kind: "group"`,
  `family`, `count`, `children` (the original items, unchanged, in priority
  order), severity = the highest child severity, `title` naming the count
  ("22 price targets crossed"), `detail` a one-line summary (for price targets:
  how many BUY and how many SELL, how many are new in the last 7 days; for pace:
  the three largest projected overshoots by label), `link_page` of the children.
  Fewer than 3: the items stay as they are. Checked and snoozed items are never
  folded and never counted.
- **Priority.** Every item and group gets `priority` (int, higher first) from
  one pure function: severity (high 300, medium 200, low 100) + a category
  weight from one table (tax and data_quality and retirement and allocate above
  budget and spending, watchlist lowest) + urgency (a `context.days_left` or due
  date within 45 days adds up to 50). Ties keep the existing order. The year-end
  tax item must put `days_left` in its context if it does not already.
- **API.** `GET /action-items/` gains `grouped: bool = false`. With it the
  response's `items` is the folded, priority-sorted list and a new `top` holds
  the first 5 open entries of that list. `summary` is unchanged (it counts
  single items) and gains `open_entries` (the folded count). Without `grouped`
  the response is exactly what it is today plus `priority` on each item.
- **Acks.** `POST` of an ack with id `group:<family>` applies the same ack to
  every currently open child and stores nothing under the group id; the
  response lists the child ids it touched. A child that appears later is open.
- The `get_action_items` chat tool and the MCP `action_items` tool read the
  grouped form: one line per group with the count and the summary, children
  only on request (`family=<name>` argument).

## 2. Net-worth history

- `services/networth_history.py`. `take_snapshot(db, today=None)` builds the
  balance sheet once and writes the row; the five components come from the
  balance sheet's own groups (investments = positions incl. account valuations;
  cash = bank accounts + broker cash + fixed deposits; property; other assets;
  liabilities incl. loans). If the balance sheet cannot be built it raises;
  nothing partial is stored. The mapping from balance-sheet groups to the five
  columns lives in one place and a test pins that they sum to
  `totals.net_worth_eur`.
- **When.** At the end of every successful price-update run (wrapped: a
  snapshot failure is logged and never fails the run), and by
  `POST /api/v1/networth/history/snapshot`. One row per day.
- `GET /api/v1/networth/history?months=24` → `{snapshots: [...], first_date,
  last_date, count, warnings}`; at most one point per day, oldest first.
- `GET /api/v1/networth/history/change?start=&end=` (defaults: the snapshot
  nearest to 30 days before the latest, and the latest) →
  `{start, end, net_worth_change_eur, parts: {saved_eur, market_eur,
  debt_paid_eur, property_change_eur, other_change_eur}, by_component: {...},
  warnings}` where
  - `net_invested_eur` = buys − sells of non-cash assets between the two dates,
    at transaction-date FX (use the existing attribution/performance flow
    helpers; do not write a new FX path),
  - `market_eur` = Δinvestments − net_invested,
  - `saved_eur` = Δcash + net_invested,
  - `debt_paid_eur` = −Δliabilities,
  - the five parts sum to `net_worth_change_eur` to the cent.
  With fewer than 2 snapshots: 200, parts `null`, a warning "history starts
  <date>". If the flows cannot be read: `saved_eur` and `market_eur` are `null`
  with a warning; the other parts still show.
- Both routes are registered before any `/{id}` route in the router.
- Goals are not changed in this round.

## 3. Broker import-file

`POST /api/v1/imports/import-file` (multipart: `portfolio_id`, `file`, optional
`format`), the broker counterpart of `/spending/import-file`.

- The portfolio must be `account_type='brokerage'`; 400 otherwise.
- Format: `format` if given, else auto-detected from the portfolio's broker
  name and the file's header, among the deterministic parsers only
  (Indexa, MyInvestor CSV, Coinbase, Mintos, generic CSV, PDT XLSX). No match:
  400 naming what was looked for. The LLM text path is never used.
- It runs the same parse and duplicate flagging as `/imports/upload` and the
  same save path as `/imports/save` (shared functions; never a forked copy),
  with the web default for duplicates (skip). Bookings follow their own
  duplicate rule as today.
- All or nothing: if the save fails part-way, what this call wrote is removed
  and the call returns 500 with the reason. If the existing save path cannot be
  made transactional without a rewrite, say so in the batch report and stop;
  do not ship a partial-write version.
- Response: `{portfolio_id, portfolio_name, format, parsed, imported,
  duplicates_skipped, possible_duplicates, bookings_imported, new_assets,
  first_date, last_date, warnings}`. `possible_duplicates` counts rows saved
  although the checker called them "possible": the caller prints it as "to
  review". No amounts, no descriptions in the response.
- Every booking-creating path calls `settle_after_bookings`, as today.
- 400 for bad input, 500 for a save failure, same as the spending endpoint. A
  module lock serialises calls.

## 4. Recurring resolutions and subscriptions

- A resolution applies to a series (`portfolio_id`, `merchant`). `load_recurring`
  (not `detect_recurring`, which stays pure) attaches it: a series whose last
  charge is on or before `since` gets `status` `cancelled` or `paid_elsewhere`
  and `resolution: {status, since, note}`. A series with a charge after `since`
  keeps its detected status and gets `resolution_stale: true` (the charge came
  back).
- Effects of a live resolution: no `recurring:missed` Action Item; the series is
  not placed in the forecast's known bills, not in the budget pace's upcoming
  charges, and not in "new/ended" of the monthly review. `cancelled` leaves the
  annual totals; `paid_elsewhere` stays in them, labelled, at its last amount.
- A stale resolution raises one low Action Item
  (`recurring:returned:<portfolio>:<merchant>:<date>`): "charge from X arrived
  although it was marked cancelled".
- API: `PUT /api/v1/spending/recurring/resolution` `{portfolio_id, merchant,
  status, note}` (`since` is set by the server from the series; 404 when no such
  series), `DELETE` with the same key clears it. `GET /spending/recurring`
  gains the fields above, `include_resolved: bool = true`, and totals
  `annual_total_eur` (active + paid elsewhere), `paid_elsewhere_annual_eur`,
  `cancelled_annual_eur` (what the cancellations save a year).
- Subscriptions view: the same endpoint sorted by yearly cost; no second
  endpoint.

## 5. Lumpy pace

A cost line is **lumpy** when the baseline is reliable and its categories had
everyday spending (the `everyday_outflows` rule) in at most a third of the
baseline months, with a minimum of 6 baseline months. Use the same activity
count the seed proposals use for the yearly suggestion; one definition.

For a lumpy line `project()` returns `status: "irregular"`, `method:
"irregular"`, `projected_month_end_eur` = actual to date + recurring still due
(no everyday average added), `meaningful_overshoot` false unless the actual
already exceeds the limit (then it is `over`, as today), and
`months_with_activity`. No pace Action Item for `irregular`. The web badge reads
"irregular: spent in N of M months". The section-level pace is unchanged.

## 6. Month close

- `services/month_close.py` assembles existing services only.
  `GET /api/v1/spending/month-close?month=YYYY-MM` (default: the last complete
  calendar month) → `{month, closed: {closed_at, notes, report_id, stale} | null,
  ready, checklist: [...], figures, warnings}`.
- Checklist steps, each `{key, label, status: ok|todo|warn|unknown, count,
  detail, link_page}`; a step whose source fails is `unknown` with a warning:
  1. `imports`: every bank account with rows in the 12 months before has rows
     reaching the month's last day or later (names the lagging accounts). `todo` blocks.
  2. `uncategorized`: uncategorised outflows in the month (count and share). `warn` above 0.
  3. `transfers`: unlinked transfers and unmatched pattern transfers in the month. `warn`.
  4. `anomalies`: open (not checked) anomaly items dated in the month. `warn`.
  5. `recurring`: missed series due in the month without a resolution. `warn`.
  6. `one_offs`: the month's largest outflows not marked one-off above the anomaly rule's threshold, as a reminder count. `warn`, never blocks.
  `ready` is true when step 1 is `ok`.
- `figures`: income, spending, savings moved (`savings_eur`), net, savings rate,
  budget planned vs actual for the active budget, top 5 categories, and the
  month against its normal (the comparison service). All from existing
  services; a part that fails is `null` + warning.
- `POST /api/v1/spending/month-close` `{month, notes, force: false}`: 409 when
  not `ready` and not `force`; 400 for a month in progress or in the future.
  Stores the row, and writes a Markdown report to `reports` (title "Month close
  YYYY-MM", built by a pure function from `figures` and `checklist`, no LLM,
  amounts rounded to whole euros). Closing again replaces the row and updates
  the same report.
- `DELETE /api/v1/spending/month-close?month=` reopens (the report stays).
- `closed.stale` is true when the figures recomputed now differ from the stored
  ones by more than 1 EUR on income, spending or net (rows were imported or
  recategorised after the close). Nothing is locked: a close is a mark, not a
  freeze.
- One Action Item, low: `spending:month-close:<YYYY-MM>` "October is ready to
  close", only when the last complete month is `ready`, not closed, and today
  is the 5th or later. None for older months.

## Web client (batch 4)

- Action Items page and the dashboard card read `grouped=true`. A group is one
  row with a count badge that expands to its children; Check/Snooze on the
  group acts on all children. Dashboard: a "This week" card with `top`.
- Net Worth page: a "History" card (line chart of net worth, stacked components
  on toggle) and a "What changed" row for 1 month / 3 months / since the first
  snapshot. With one snapshot: "History starts <date>".
- Spending › Recurring: a resolution menu per row (Cancelled / Paid another way
  / Clear), badges for both states and for "came back", the three totals, sort
  by yearly cost.
- Budget: the `irregular` pace badge.
- Budget › a new "Month close" tab (or section on the Review tab): the
  checklist with links, the figures, Close / Reopen, the stale banner, a link
  to the saved report.
- Help text for each new card; 390px check; no native dialogs; `Fmt.money` only.

## Chat, MCP, docs (batch 5)

- Chat tools: `get_action_items` reads the grouped form (optional `family`);
  new read-only `get_networth_history` (history + change), `get_month_close`;
  `get_recurring` (or the existing recurring tool) gains the resolution fields.
  Update the count pin.
- MCP (`mcp/pfm_mcp`): `action_items` grouped with `family`; new
  `networth_history`, `month_close`, `subscriptions`; README table and count pin.
- Docs: `action-items.md`, `networth-portfolios.md`, `imports-exports.md`,
  `spending.md`, `budgeting.md`, `cashflow-forecast.md`; `PROJECT_STATUS.md`
  release entry; `CLAUDE.md` schema line v47 and one-line gotchas only.

## Left to the builder

Names of private helpers, the exact wording of titles and details, the priority
weights (keep them in one table with a comment), chart styling within the
`--viz-*` tokens.

## Tests

Every rule above has a unit test with invented names and round amounts. Never
reuse a real statement row, merchant, ISIN or amount, even renamed. DB version
assertions move to 47.
