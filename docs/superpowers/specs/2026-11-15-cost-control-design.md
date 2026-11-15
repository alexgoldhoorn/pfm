# Cost control, spending deep dive, budget pace and cash-flow forecast

Date: 2026-11-15. Branch `feat/cost-control`, worktree `~/repos/pfm-cost-control`.

## Why

pfm records and categorises bank spending and compares it to a monthly budget,
but it only looks backward. It cannot say where this month will land, which
bills are coming, what changed compared to normal, or which spending could be
cut. This work adds those answers on top of the data that is already there.

## Goals

1. A short-term cash-flow forecast: this month's likely end and the next 12 months.
2. A budget that shows pace mid-month, supports yearly (sinking-fund) lines and carry-over.
3. Comparisons (month vs normal, vs last year) and flags for unusual charges.
4. The fixed / flexible / optional split visible on the Spending and Budget pages.
5. One-off spending that stops distorting averages.
6. Every forecast and pace figure states the date the data runs to.
7. One bundled monthly spending review for the chat, the MCP server and later a cron.

## Non-goals

- No new bank import path, no card-statement import.
- No LLM in any calculation; all of it is deterministic.
- The budget does not become the data source of Goals or the Wealth Simulator.
- No cron job and no gateway registration in this branch (they need the merge first).

## Anti-goals

- A flattering number for a period with nothing imported. Keep every existing
  suppression rule (`months_without_activity`, the Dashboard card).
- Breaking the invariant that budget variance and `/spending/trend` reconcile to the cent.
- A second copy of maths that exists: reuse `services/recurring.py`,
  `services/budget.py` tree helpers and the essentiality resolver in
  `services/life_scenarios.py`.
- Guessing: an unmarked category stays `unclassified`; a failing source becomes
  a warning and a `None`, never a zero and never a 5xx.

## Shared rules

- Complete calendar months with imported rows are the only baseline months.
  The month in progress never counts in an average.
- "Data through" for a bank account is the date of its latest imported row.
  The date the data reaches is the **earliest** of these over the accounts that
  have rows in the relevant window (an account with none there is ignored).
  Completeness of a month, the baseline months and the elapsed share of a
  month are all measured to that date, not to today and not to the freshest
  account.
- Transfers are excluded everywhere, as today. Money moved to `Invest` /
  `House purchase` is not spending (same as `household_baseline`).
- EUR at today's rate, same as `/spending/trend`.
- Thresholds are named module constants.
- Tests use invented merchants and round amounts only. Never copy a row from
  the live database or call the live API from a test.

## Schema v43 (one migration, written by batch A for every batch)

- `spending_transactions.one_off` INTEGER NOT NULL DEFAULT 0.
- `budget_lines.period` TEXT NOT NULL DEFAULT 'monthly' (`monthly` | `yearly`).
- `budget_lines.rollover` INTEGER NOT NULL DEFAULT 0.

Nothing is backfilled. Both `_create_all_tables` and `_migrate_to_v43`, and the
SQLAlchemy/Postgres side if those tables are declared there.

## Batch A: one-offs, comparison, anomalies, tiers (backend)

New `portf_manager/services/spending_insights.py`; endpoints in
`portf_server/routers/spending.py`.

- **One-off flag.** `PUT /spending/{id}` accepts `one_off`; the list endpoint
  returns it and can filter on it; a bulk set if a bulk path already exists.
  A one-off row still counts in every total (summary, trend, budget actuals).
  It is left out only of averages and baselines: budget seeding, the
  comparison baseline, the anomaly baseline, the forecast run-rate and
  `household_baseline` (which reports what it left out). `/spending/trend`
  gains `one_off_eur` per month.
- **Comparison.** `GET /spending/compare?month=&baseline_months=6&group_by=category|merchant&portfolio_id=`.
  Per group: actual, baseline average, same month last year (null without
  data), delta in EUR and %. Totals too. Flags a month in progress and a month
  with no imports.
- **Anomalies.** `GET /spending/anomalies?days=60`. Three deterministic kinds:
  a charge far above that merchant's usual amount or a large charge from a new
  merchant; a category whose complete-month total is well above its baseline;
  a possible duplicate (same account, merchant and amount within a few days).
  Each item has a deterministic id.
- **Tiers.** `GET /spending/tiers?months=12`: per month the fixed / flexible /
  optional / unclassified totals and the top categories per tier, using the
  existing inherited-essentiality resolver.
- **Action Items.** `check_spending_anomalies`, low severity, recent items only,
  using the existing server-side ack mechanism.

## Batch B: cash-flow forecast and budget (backend)

New `portf_manager/services/cashflow_forecast.py`;
`GET /api/v1/spending/forecast?months=12&portfolio_id=`.

- **Freshness.** `data_through` per bank account and a warning when stale.
- **This month.** Actual to date, recurring charges still expected this month
  (from `load_recurring`, not yet arrived), a variable-spend estimate for the
  days left, projected month-end spending / income / net and a low/high range
  taken from the spread of past months.
- **Next N months.** Per month: expected income, recurring charges placed on
  their due months (quarterly and yearly included), variable baseline, net and
  projected bank balance starting from the latest known balances. A
  `known_bills` list (date, merchant, amount) and a flag on any month where
  the balance goes below zero.
- Recurring and variable must not overlap: variable baseline = non-transfer
  outflows minus the outflows that belong to a recurring series, one-offs excluded.
- The response lists its assumptions in plain words.

Budget (`services/budget.py`, `routers/budgets.py`):

- **Pace.** For the month in progress each cost line gains
  `pace: {elapsed_share, projected_month_end_eur, expected_recurring_remaining_eur, status, ...}`
  with status `on_track` | `at_risk` | `over` | `too_early`. Projected month
  end = actual to date + recurring charges still due this month + the line's
  baseline everyday average (its categories' average monthly spend over the
  baseline months, one-offs and recurring-series rows left out, the forecast's
  rule) x the share of the month still to come, so a lump sum is not
  extrapolated. With fewer than 3 baseline months the month-to-date run rate
  is used once 25 % of the month has elapsed; before that the pace is
  `too_early` with a null projection and no Action Item. No pace for a month
  without imports.
- **Yearly lines** (`period='yearly'`). The amount is a yearly figure; each
  month sets aside a twelfth (the cash to put aside, not a limit). The limit is
  a **rolling 12 months**: in month m the line may still spend the yearly
  amount less what it spent in the 11 months before m, and it is over only
  when the 12 months ending at m exceed the yearly amount. There is no January
  reset. `fund` is `{yearly_amount_eur, window_start, window_end, spent_eur,
  remaining_eur}`. Months of that span without imports are named in a warning.
- **Rollover** (`rollover=1`). Under- or overspend from earlier months of the
  same calendar year (months with imports only) carries into the month:
  `carried_eur`, `available_eur`, variance against available. The per-month carry
  resets in January; the variance of a window is carry-in + sum(planned) -
  sum(actual), so the reset never hides an overspend.
- Spending lines and the spending section carry the essentiality tier and tier totals.
- **Action Item.** "On pace to exceed" for the month in progress, same
  thresholds as the overrun check, one deterministic id per line and month,
  never together with an overrun item for the same line.
- Actual totals do not change: the reconcile-to-the-cent invariant holds.

## Batch C: web client

- Spending › Analytics: comparison table (month picker, category / merchant
  toggle), anomalies card (mark one-off, dismiss), tier chart per month.
- Spending › Transactions: one-off toggle per row and a filter.
- Budget › Overview: pace column and projected month end, yearly-line fund,
  carried amounts, tier totals. Budget › Edit: period and rollover controls.
- Budget page gets a **Forecast** tab: this-month projection, 12-month table
  with the balance line, upcoming-bills list, the data-through dates and the
  assumptions.
- Dashboard budget card: one pace line (hidden under the existing suppression).
- `METRIC_HELP` / `PAGE_HELP` entries, pure helpers `window.`-exported with JS
  tests, checked at 390px, all existing web-client rules in `CLAUDE.md`.

## Batch D: review, chat, MCP, docs

- `GET /spending/review?month=` (default: last complete month): totals vs
  baseline, biggest movers, anomalies, tiers, recurring changes, budget
  result, next month's forecast and data freshness, in one payload from the
  services above.
- Chat tools (read-only) for comparison, anomalies, tiers, forecast and review;
  update the pinned count.
- MCP tools `spending_compare`, `spending_anomalies`, `cashflow_forecast`,
  `spending_review`; `budget_summary` prints pace. README table and count pins.
- Docs: `docs/features/spending.md`, `budgeting.md`, new `cashflow-forecast.md`,
  `action-items.md`, `llm.md`, `PROJECT_STATUS.md`, and only the must-know
  gotchas in `CLAUDE.md` (schema v43 line, Feature reference rows).

## Approaches considered

- **Four sequential batches in one worktree (chosen).** The batches share
  `database.py`, `spending.py`, `action_items.py` and `pfm_features.js`; one
  schema bump and no merge conflicts.
- Parallel worktrees per feature: faster on the clock, but every branch bumps
  the schema and edits the same files and pinned counts.
- Forecast in the browser, like the Wealth Simulator: rejected, because chat
  and MCP need the same figures and the simulator's two copies are a known cost.

## Left to the builder

Exact thresholds, response field names beyond those given, storage detail of
the yearly amount, and layout, within the rules above.

## After the branch

Merge migrates the live database (Alex approves first). Then: web redeploy,
gateway + finance adapter + persona registration of the new MCP tools, and
optionally the monthly review in the Sunday finance job.
