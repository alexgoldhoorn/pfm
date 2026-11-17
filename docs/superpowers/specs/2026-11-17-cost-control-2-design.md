# Cost control, round 2: planned items, tax in the forecast, budget suggestions, statement inbox, uncategorised queue, year view

Date: 2026-11-17. Branch `feat/cost-control-2`. Builds on
`2026-11-15-cost-control-design.md`; every shared rule and anti-goal there still
holds (no LLM in a calculation, a failing source is a warning and `None`, never
a zero or a 5xx, no flattering number for a month without imports, one-off rows
and transfers stay out of every average, "data through" is the earliest last
import over the accounts in the window).

## Why

Round 1 shipped the forecast, pace, yearly lines and the monthly review. Use on
real data showed the gaps: the forecast cannot hold a known future expense, it
ignores the quarterly self-employed tax payments the allocator already
estimates, no budget line uses the yearly or carry-over type, one bank account
that lags holds every figure back, uncategorised rows distort the review, and
there is no year-level view.

## Goals

1. Planned items: future one-off expenses and income entered by hand, in the forecast.
2. Estimated quarterly tax payments in the forecast, from the allocator's reserve maths.
3. Budget suggestions that propose yearly and carry-over lines.
4. A one-call statement import a host job can use (the drop folder itself is phase 2).
5. An uncategorised queue ordered by money, with a rule proposal per group.
6. A year view: this year against last year.

## Non-goals

- No card-statement import. No new bank format.
- Chat gets no new write tool (planned items are written in the web client and the API).
- No cron, Telegram or gateway change in this branch (phase 2, after the merge, in `~/mcp`).

## Approach

One branch, four sequential batches (backend forecast; backend budget, queue,
year, import; web client; chat, MCP and docs), each a commit or two with its
tests. Considered and dropped: a branch per feature (six merges and six live
migrations for features that share the forecast and the spending router), and
putting planned items into `budget_lines.overrides` (an override is a planned
*amount for a category in a month*, has no date, and is absent without a
budget).

## 1. Planned items

Schema v46, table `planned_items` (in `_create_all_tables` and `_migrate_to_v46`):

| column | notes |
|---|---|
| `id` | PK |
| `date` | TEXT `YYYY-MM-DD`, the day the money is expected to move |
| `description` | TEXT NOT NULL |
| `amount` | REAL NOT NULL, signed like `spending_transactions.amount`: negative out, positive in; never 0 |
| `currency` | TEXT NOT NULL DEFAULT 'EUR' |
| `kind` | `spending` \| `savings` \| `income`, CHECK; `income` needs amount > 0, the other two amount < 0 |
| `portfolio_id` | nullable FK to a bank account (display only) |
| `done` | INTEGER NOT NULL DEFAULT 0: it happened (the real row is in a statement), stop projecting it |
| `notes` | TEXT |
| `created_at`, `updated_at` | TEXT |

API under `/api/v1/spending/planned`: `GET` (query `include_done=false`),
`POST`, `PUT /{id}` (partial), `DELETE /{id}`. Validation errors are 400 with
the reason.

Forecast (`cashflow_forecast.compute_forecast` gets `planned: Optional[List[dict]]`):

- An open item dated on or after the data-through date is projected in its
  month: `kind` decides whether it adds to `spending_eur`, `savings_eur` or
  `income_eur`. Each month (and `this_month`) gains `planned_spending_eur`,
  `planned_savings_eur`, `planned_income_eur` (0.0 when none); net, balance
  change, projected balance, the low/high range and `first_below_zero` include
  them (a planned amount shifts low and high equally).
- An open item dated before the data-through date is **overdue**: not
  projected, listed with `status: "overdue"` and one warning ("mark it done or
  move its date").
- An item beyond the horizon is listed with `status: "beyond_horizon"`.
- Response gains `planned_items: [{id, date, description, amount_eur, kind, status}]`.
- **What-if without saving:** `GET /spending/forecast?extra_amount=&extra_date=&extra_kind=spending`
  adds one unsaved item (`id: null`, `status: "what_if"`); the response then also
  carries `what_if: {amount_eur, date, lowest_balance_eur, lowest_balance_month,
  lowest_balance_without_eur, goes_below_zero}`. `extra_amount` is a positive
  magnitude; `extra_kind` gives the sign. `extra_date` accepts `YYYY-MM` (taken
  as the 15th) or a full date.
- Planned items never enter a baseline, the budget pace or a comparison.

## 2. Tax payments in the forecast

Reuse `services/deployable.py` (`quarter_reserve`, `due_date`,
`work_income_rows`, the settings loader and whatever it uses to pick the
tax-paid rows); do not copy its maths.

- New `estimated_tax_payments(rows, tax_rows, settings, today, horizon_end)` in
  `cashflow_forecast.py` (or a small helper module) returns one entry per
  quarter whose due date lies between the data-through date and the horizon:
  `{quarter, due, amount_eur, basis}`.
  - previous quarter, unpaid: `remaining_eur`, basis `accrued`;
  - current quarter: the larger of the tax accrued so far and the average
    accrued of the last up to 4 complete quarters, basis `accrued` or `average`;
  - later quarters: that average, basis `average`. No complete quarter with
    work income: no entry and a warning.
- **Only when the allocator settings name work-income merchants.** With none
  ticked the forecast adds no tax estimate and one warning that says where to
  tick them. `GET /spending/forecast?include_tax=false` switches it off.
- **No double counting.** When the estimate is in use: recurring series whose
  category is one of the allocator's tax-paid categories **and whose cadence is
  quarterly** (the pagos fraccionados the estimate replaces) are dropped from
  the known bills (a yearly or other-cadence series stays a known bill), and rows in those categories are left out of the everyday
  baseline of the *forecast only* (`everyday_outflows` as used by the budget
  pace is unchanged: add a parameter, default off). When the estimate is not
  in use, behaviour is exactly as today.
- Each month gains `tax_eur` (inside `spending_eur`); response gains
  `tax_payments: [...]` and an assumption line ("quarterly tax is an estimate
  from the allocator settings: IVA x%, IRPF y%"). A one-time annual tax
  settlement is not estimated. The past-due rule of `quarter_reserve` is judged
  at the data-through date, not today (the allocator keeps its own behaviour).

## 3. Budget suggestions

`budget_service.propose_budget_lines` keeps its current fields and adds, for
spending lines, over the last 12 complete months with imports (one-off rows
and transfers out):

- `months_with_activity`, `suggested_period`, `suggested_amount`,
  `suggested_rollover`, `reason`.
- `yearly` when the category had spending in at most 4 of those months, or its
  two largest months hold at least 70% of the total; `suggested_amount` is the
  12-month total rounded up to the next 10. Otherwise `monthly`, amount as today.
- `suggested_rollover: true` for a monthly suggestion with activity in at least
  6 months and a coefficient of variation above 0.5, unless the category's
  essentiality (own or inherited) is `fixed`.
- Fewer than 6 complete months with imports: every suggestion stays `monthly`,
  `reason` says the history is too short.
- For a category that already has a line in the budget, the proposal carries
  `existing_line_id` and `change` (`"to_yearly"`, `"add_rollover"` or `null`);
  nothing is written. Applying uses the existing bulk and line `PUT` endpoints
  (extend them only if they cannot set `period`/`rollover`).

## 4. Statement import in one call

`POST /api/v1/spending/import-file` (multipart: `file`, `portfolio_id`): parse
with the existing auto-detection, drop rows already stored (the same duplicate
rule as the web preview, against rows stored before the import), apply rules
and transfer matching exactly like upload + save, and return
`{parsed, imported, duplicates, uncategorized, date_from, date_to, data_through, balance_check, transfers_settled, warnings}`.
The portfolio must be a bank account (400 otherwise); a file that cannot be
parsed or parses to nothing is a 400 with the parser's reason; all or nothing:
a row that fails to save removes what the call saved (500). Calls are
serialised. `balance_check` is `ok`, `mismatch` (warning, not a refusal) or
`unknown`; if transfer settling fails after the save, the rows stay and
`transfers_settled` is false.
It must share code with `/upload` and `/save`, not copy it.

Phase 2 (not in this branch): `~/mcp/scripts/pfm_statement_inbox.py` watches
`~/pfm-inbox/<bank account name>/`, posts each file, moves it to `done/` or
`failed/`, and sends one Telegram line with counts only.

## 5. Uncategorised queue

`GET /api/v1/spending/uncategorized-queue?limit=20&months=12`:

- Non-transfer rows with category `uncategorized` in the window, grouped by
  merchant (rows without one: by normalised description), ordered by total
  absolute EUR.
- Group: `{key, merchant, direction (out|in), count, total_eur, first_date,
  last_date, sample_descriptions (max 3), transaction_ids, proposed_pattern,
  account_names}`. `proposed_pattern` is the merchant (or the longest common
  description prefix of at least 5 characters, else `null`).
- Top level: `{groups, total_count, total_eur, share_of_spending_pct,
  last_complete_month: {month, count, total_eur, share_pct}}`.
- Classifying a group from the web client uses what exists: `PUT` per row or a
  new rule plus `rescan-categories`. Add
  `POST /api/v1/spending/uncategorized-queue/classify` `{transaction_ids, category,
  create_rule: bool, pattern}` only as a thin wrapper over those paths (sign
  and root checks included), so a group is one request.
- `GET /spending/review` gains `uncategorized: {count, total_eur, share_pct}`
  for the reviewed month, with a highlight line when the share is above 5%.
- Action Item `spending:uncategorized:{YYYY-MM}` (the month it judges, so a
  checked item does not hide a later month) when the last complete month's
  uncategorised share is above 5% or its total above EUR 500 (check first
  whether an uncategorised item already exists and extend it instead).

## 6. Year view

`GET /api/v1/spending/year?year=YYYY` (default: the current year),
`services/spending_insights.py` or a new `spending_year.py`:

- `year`, `previous_year`, `complete` (every month complete by the data-through
  rule), `compared_through` (`YYYY-MM`): for a year in progress, the previous
  year is cut to the same months, through the last complete month, and the
  response says so; both the cut and the full previous-year totals are given.
- `totals` for each year: `income_eur`, `spending_eur`, `savings_eur`,
  `net_eur`, `savings_rate_pct` (null without income), `one_off_eur`
  (one-off rows count in the totals and are listed separately).
- `months`: 12 entries `{month, income_eur, spending_eur, savings_eur,
  has_imports, complete}` for the year and the same for the previous year.
- `categories`: top-level Spend categories `{category, this_eur, previous_eur,
  delta_eur, delta_pct, share_pct}` ordered by this year's amount, children
  available with `?depth=2`.
- `movers`: the 5 largest increases and decreases; `merchants`: the 10 largest
  by spend with last year's figure; `one_offs`: the one-off rows of the year.
- A month without imports is `has_imports: false`, counted as unknown, and
  named in a warning; deltas are null when either side has no comparable months.

## Clients

- **Web.** Budget › Forecast: a "Planned" card (list, add, edit, done, delete),
  a what-if row (amount, month, result line), planned and tax bars in the
  chart and columns in the table. Budget seed dialog: period badge, reason,
  toggle, "convert" actions for existing lines. Spending: a "To classify" card
  (queue, category picker, "also add a rule"), and a Year card or tab under
  Analytics (totals, month grid, category table). `METRIC_HELP`/`PAGE_HELP`
  entries, 390px check, JS tests for the pure helpers, e2e smoke stays green.
- **Chat** (read-only): `get_planned_items`, `get_spending_year`,
  `get_uncategorized_queue`; `get_cashflow_forecast` gains the what-if
  arguments and prints planned and tax lines.
- **MCP**: `planned_items`, `spending_year`, `uncategorized_queue`;
  `cashflow_forecast` gains `extra_amount`, `extra_month`, `extra_kind`.
  README table and the pinned counts updated.
- **Docs**: `cashflow-forecast.md`, `budgeting.md`, `spending.md`,
  `web-client.md`, `llm.md`, `action-items.md`, `PROJECT_STATUS.md`,
  `CLAUDE.md` (schema 46, one-line gotchas only).

## Left to the builder

File layout inside the services, response field order, UI wording, test
structure. Anything that would change a rule above comes back as a question.
