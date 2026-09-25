# Budgeting

> Moved out of `CLAUDE.md` on 2026-09-25 so it isn't loaded into every session. CLAUDE.md keeps the must-know gotchas and links here; this file is the full reference. "See the X section" means the matching file in `docs/features/`, or CLAUDE.md.

### Budgeting (`portf_server/routers/budgets.py` + `portf_manager/services/budget.py`)

Named, **open-ended** monthly plans with budget-vs-actual variance. A budget's
amounts apply to any month you look at (no year scoping, no valid-from/to);
several budgets coexist as scenarios (base / best case / worst case) and
exactly one carries `is_active`. Nothing here changes `monthly_cashflow`,
Goals or the Wealth Simulator — the budget is deliberately not their data
source. Design: `docs/superpowers/specs/2026-09-03-budgeting-design.md`.

- **Line addressing**: `budget_lines.line_type` + `ref_key` is the same
  discriminator idiom as `spending_transactions`' `transfer_link_type` +
  `transfer_link_id`. `ref_key` holds a **bare spending-category name** (no FK,
  same convention as `spending_transactions.category`) for `income`,
  `spending`, `debt` — and for an `investment` line too, unless it's
  **broker-keyed**, in which case it's a **`portfolio_id` as text**.
- ⚠️ **An `investment` line has two possible keyings, told apart by shape**:
  `budget_service.is_broker_ref(ref_key)` is `str(ref_key).isdigit()`. All
  digits → broker, actual = net Deposit `bookings`. Otherwise → a Spend-rooted
  spending category, actual = that category's **bank outflows**, exactly like a
  spending line. The bank side is the only way to budget a destination pfm
  doesn't track (a pension plan), and the only one that can be reclassified.
  The router rejects an all-digits *category* name on an investment line so the
  two can never collide. `line_uses_category(line_type, ref_key)` is the
  predicate for "resolves against the category tree" — use it, not a
  `line_type != "investment"` check, anywhere coverage is computed
  (`_category_ref_keys`, `covered_by_type`), or a category-keyed investment
  line's category will also surface as unbudgeted *spending*, doubling it. `overrides` is a JSON object of per-month exceptions
  (`{"2026-03": 450.0}`), parsed by the service (`parse_overrides`, tolerant
  of `NULL`/malformed) — never by the DB layer, which passes the text through.
  `link_id` is an optional `manual_assets.id` on a debt line, display only.
- `is_active` exclusivity lives in `db.set_active_budget()` (clears every other
  row in the same transaction), **not** in a constraint.
- **Endpoints** — `GET|POST /api/v1/budgets/`, `GET /summary`,
  `GET|PUT|DELETE /{budget_id}`, `POST /{budget_id}/activate`,
  `GET|POST /{budget_id}/lines`, `POST /{budget_id}/lines/bulk`,
  `PUT|DELETE /{budget_id}/lines/{line_id}`,
  `GET /{budget_id}/variance?months=&end_month=`,
  `GET /{budget_id}/seed-proposals?months=`.
- ⚠️ **`GET /summary` must stay registered before `GET /{budget_id}`** —
  FastAPI matches first, so a single-segment route declared after it is
  swallowed as a budget id (same hazard documented for `research.py`'s
  `compare`/`portfolio-analysis`). `/summary` returns the active budget's
  current-month variance, or `null` when there is no active budget; it's the
  one call behind both the Dashboard card and the Action Items check.
- `/variance`, `/summary` and `/seed-proposals` are plain `def` (blocking
  `_fx`, which is the same lazy-import shim of `portfolios._get_fx_rate` that
  `spending.py` uses).
- **`line_type` IS editable on `PUT`; `ref_key` is not.** Reclassifying is the
  lever that keeps money you merely moved out of the spending total — a
  mortgage charge is `debt`, a transfer to a broker or pension is
  `investment`, and only `spending` swells the spending figure. The category is
  unchanged so the coverage space is identical; only the reporting section
  moves. Validation re-runs (an Income category can't become a spending line),
  excluding the line's own `ref_key` so it can't conflict with itself.
  Retargeting a line at a *different* category is still a delete plus a
  create, so the coverage check can't be sidestepped.

**Variance conventions** (all of these are load-bearing):
- Calendar months keyed `YYYY-MM` off `date[:7]`, EUR at **today's** rate,
  transfers excluded — identical to `GET /api/v1/spending/trend`, and the two
  **reconcile to the cent**. That equality is the invariant to re-check after
  touching either surface.
- Investment actuals read net `bookings` (Deposits − Withdrawals) for the
  portfolio, never `spending_transactions` — a bank→broker transfer is already
  `is_transfer=1` on the spending side, so the same euros can't be counted twice.
- **`variance_eur` is signed so positive always means favourable**:
  `planned − actual` for spending/debt (costs are good under plan),
  `actual − planned` for income and investment (earning and contributing are
  good over plan). Every line/section also carries an explicit `favourable`
  bool so no client re-derives the direction. `variance_pct` is `None` when
  nothing was planned.
- **No two category lines on the same branch** in one budget (`Housing` and
  `Housing > Rent` would double-count, since a parent's actual sums its
  children's subtree) — 400 via `coverage_conflict`. On `POST /lines/bulk` the
  check must consider **the rest of the batch**, not just stored lines, or two
  overlapping lines arriving together both pass; the handler builds the
  post-save key set first, then validates each candidate against
  `final_keys - {candidate}`. Bulk also validates the whole batch before
  writing anything.
- An income line's category must sit under the `Income` root, spending/debt
  under `Spend` (`db.get_spending_category_root`) — 400 on mismatch, mirroring
  `PUT /api/v1/spending/{id}`.
- ⚠️ **Unbudgeted actuals are attributed by SIGN, not by tree root.** Filtering
  on the category's root silently drops three real cases: a refund in a Spend
  category (positive but Spend-rooted), a charge in an Income category, and
  anything unfiled — including **`uncategorized`, which exists in a real
  database as a parentless `is_root=0` node with no root at all**. Sign
  attribution is what `/spending/summary` and `/spending/trend` do; this cost a
  real reconciliation bug during implementation and is regression-tested in
  `tests/unit/test_budget_service.py`.
- Uncovered spend rolls up to its **highest ancestor that is neither budgeted
  nor an ancestor of something budgeted** (`budgeted_or_above` +
  `uncovered_rollup_key`). With no lines, everything rolls to the direct
  children of Income/Spend; with a line on `Housing > Rent`, a stray
  `Housing > Utilities` charge reports as `Utilities` (naming the line worth
  adding) rather than as a second, confusing `Housing` row. Unbudgeted totals
  count toward each section's `actual_total` but never its `planned_total`, so
  "under budget" can't be an artifact of leaving spending out of the plan.
- ⚠️ **`net` is a CASH-FLOW figure**: `income − spending − debt − investment`,
  i.e. everything that left the accounts. Reading it as "am I better off?" is
  wrong and is the natural mistake — income minus spending alone can look
  healthy while net is negative, because debt repayments and contributions are
  outflows too. The Overview therefore renders the arithmetic explicitly
  (`#bgNetBreakdown`, from the pure `budgetNetBreakdown(variance)`) rather than
  showing the result alone, and names the part that is still yours
  (`kept` = investment contributions). **`kept` deliberately excludes debt**:
  mortgage principal is money kept too, but a bank statement can't say how much
  of a payment was principal and how much was interest, so it isn't guessed at.
- **A debt line can link to the `manual_assets` liability it repays**, via
  `budget_lines.link_id`; the variance response resolves it into `link_label` +
  `link_amount_eur` so the Overview can print "against X outstanding" under the
  line, and the Edit tab renders a liability picker on debt rows only. This is
  **display only — the balance never enters any total**, for the same
  principal/interest reason. A `link_id` pointing at a deleted liability
  degrades to nulls, never a 500. On `PUT`, `link_id: 0` means "clear the
  link" (an omitted field means "unchanged") and is normalized to NULL.
- Debt lines share the Spend tree with spending lines, so the **Debt section's
  `unbudgeted` list is always empty by design** — uncovered charges are
  reported once, in the Spending section.
- ⚠️ **A section reports ONE measurement basis, never the sum of two.** As soon
  as a budget holds any category-keyed (bank-side) investment line,
  `compute_budget_variance` sets `bank_basis` and the Investments section's
  broker-side `unbudgeted` list is emptied, flagged by
  `unbudgeted_suppressed: true` on the section (the Overview explains it in a
  table footer row). A bank outflow to a broker and that broker's Deposit are
  the same euros; summing them doubles the total, and broker deposits are
  inflated further by moves between the user's own accounts. Confirmed on real
  data, where the broker-side figure was an order of magnitude larger than the
  bank-side one describing the same money.
- `subtree_names`/`build_children_index` are shared with
  `/spending/categories/breakdown`, which previously carried its own copy of
  the same tree walk. Don't reintroduce a local copy.

**Seeding**: `propose_budget_lines(db, months, fx)` averages the last N
**complete** months (the current partial month is excluded, so a mid-month run
doesn't halve every average) per direct child of Spend/Income, plus one
investment line per portfolio with net deposits. Writes nothing — the UI
reviews and applies via bulk upsert, the same "nothing is written until Apply"
shape as the Spending page's AI category suggestions. Every proposal carries a
`line_type` the review panel lets you change before applying, because
seeding **never guesses** which outflows are really debt or contributions —
that's a judgement about intent, not a pattern in the data.

⚠️ **Transactions can be filed against a tree root directly**, not against one
of its children, and walking only the children misses that money entirely — a
real account had nearly all its income booked straight against `Income`, and
seeding proposed an amount an order of magnitude below the real one. So per root, seeding compares the
root's own direct activity against the sum of its children's and proposes
whichever side holds more; a root line and its child lines can't coexist
(the root covers the whole subtree, so `coverage_conflict` rejects the pair).
A proposed root line's amount is the **whole subtree** total, not the
root-direct rows alone, since that's what the line will actually measure.

**Action Items**: `check_budget_overruns` (`services/action_items.py`) returns
early when `months_without_activity(summary)` is non-empty — with nothing
imported for the month yet every line reads as zero actual, so costs look
heroically under plan and contributions look behind, and flagging that at every
month-start would train the user to ignore the whole category. Otherwise it
emits
`category: "budget"` items — one per line missing plan by both
`BUDGET_OVERRUN_PCT` (10%) and `BUDGET_OVERRUN_EUR` (€50), reading "over
budget" for costs and "behind plan" for contributions (income is skipped;
that's what `check_goals_off_track` is for), plus one low-severity
`budget:unbudgeted` when more than `BUDGET_UNBUDGETED_SHARE` (15%) of the
month's spending falls outside the budget. Silent with no active budget.

**Web**: new top-level "Budget" page under the **Planning** nav section, tabs
Overview / Edit / Scenarios (`#bgTabs`), plus a Dashboard card
(`#dashBudgetCard`, `loadDashboardBudget()`) hidden unless an active budget has
lines. Pure helpers are module-scope and `window.`-exported for the DOM-free
test runner: `budgetRowStatus` (planned-vs-actual classification, direction
aware), `budgetMonthRange`, `expandBudgetLineMonths`, `budgetCoverageConflict`
(client mirror of the server rule, reusing `_isDescendant`),
`budgetMonthsWithoutActivity`, `budgetNetBreakdown`, `_bgRootOf`. The Edit tab's category input is
backed by a breadcrumb `<datalist>` filtered to the root the selected line type
can budget, via the existing `_categoryFullPath`/`_resolveCategoryInput`
helpers.

The Edit tab renders a **type selector per line** (`.bg-line-type` →
`setBudgetLineType`) and the seed-proposal panel one per proposal
(`.bg-seed-type`), because reclassifying is the main thing a user does to a
seeded budget. Both are omitted for a broker-keyed investment line, which can't
become anything else — its actual comes from `bookings`, which no other line
type reads. The investment picker in the add-line form offers brokers and
Spend-rooted categories in two `<optgroup>`s, labelled by how each is measured.

⚠️ **A month with no imported statement reads as zero spend, i.e. gloriously
under budget** — actuals only exist for what's been imported on the Spending
page, so this bites every time you look at the current month before importing
it. `budgetMonthsWithoutActivity(variance)` names those months (no activity in
any section, budgeted or not); the Overview tab renders them as a warning
banner above the tables, and the Dashboard card replaces its net line with an
explanation and **suppresses the per-section variance figure entirely** rather
than showing a flattering green number. Keep that suppression if you touch the
card — a €0-actual month showing a large "under budget" figure is the misleading case
this exists to prevent.

The Overview trend chart plots **spending + debt only**, deliberately
excluding investment contributions: broker deposits are an order of magnitude
larger and lumpier than day-to-day spending (and include moves between your
own accounts), so folding them in buries the comparison the chart exists to
make. This matches the KPI tiles, which are also spending-only; investment
lines still appear in their own section of the variance table.
