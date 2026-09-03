# Budgeting — design

**Date:** 2026-09-03
**Status:** implemented

## Problem

pfm already knows every input a budget needs: categorized bank spending
(`spending_transactions` plus the Income/Spend category tree), investment cash
movements (`bookings`), liabilities (`manual_assets`) and goals. What it has no
concept of is **intent** — what you *meant* to spend or contribute.

The only "planned" figures today are the five coarse `monthly_cashflow` rows
(`salary`/`other_income`/`mortgage`/`loan`/`rest`) on the Net Worth page, and
nothing computes a variance against them: the page's "Actual (last 30 days)"
widget puts planned and actual side by side and leaves the subtraction to the
reader.

## Scope

A budget is a named, open-ended set of planned monthly amounts. Several can
coexist as scenarios (base / best case / worst case); exactly one is active.

**In scope:** spending-category lines, income-category lines, per-broker
investment contributions, debt/mortgage payments; per-month overrides;
budget-vs-actual variance with unbudgeted actuals surfaced; a top-level Budget
page, a Dashboard card, an Action Items check, seed-from-actuals, and a
planned-vs-actual trend chart.

**Explicitly not in scope:** any change to `monthly_cashflow`, Goals or the
Wealth Simulator — the budget does not become their data source. No
Telegram/cron alerting. No per-portfolio budget scoping (budgets are global,
like goals).

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Validity window | Open-ended | Amounts apply to any month you look at, edited in place as life changes. Year scoping would force a copy every January and stop variance crossing a year boundary. |
| Granularity | Default €/month + per-month overrides | One number to enter for the common case; lumpy costs (insurance, holidays) get a real month rather than being smeared across twelve. |
| Scenarios | Independent named budgets, one active | Simplest mental model. Base + deltas avoids duplication but makes a scenario's full picture hard to read and edit. |
| Unbudgeted actuals | Own rows, counted in actual totals | Otherwise "under budget" can be an artifact of leaving half your spending out of the plan. |
| Overrides storage | JSON column on the line | A whole budget is always loaded at once (tens of lines), and an edit stays a single `UPDATE`. Same precedent as `chat_sessions.messages`. |

## Schema (database v29)

```sql
CREATE TABLE budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE budget_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_id INTEGER NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    line_type TEXT NOT NULL CHECK (line_type IN
        ('income', 'spending', 'debt', 'investment')),
    ref_key TEXT NOT NULL,
    monthly_amount REAL NOT NULL DEFAULT 0,
    overrides TEXT,
    link_id INTEGER,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (budget_id, line_type, ref_key)
);
```

`line_type` + `ref_key` mirrors the `transfer_link_type`/`transfer_link_id`
discriminator idiom already in `spending_transactions`. `ref_key` holds a bare
spending-category name for `income`/`spending`/`debt` (no FK, matching how
`spending_transactions.category` stores one) and a `portfolio_id` as text for
`investment`. `overrides` is a JSON object, `{"2026-03": 450.0}`. `link_id` is
an optional `manual_assets.id` on a debt line, for display only — it never
affects variance. `is_active` exclusivity is enforced by
`Database.set_active_budget`, which clears the others in the same transaction,
not by a constraint.

## Variance semantics

The rules that make the numbers trustworthy:

- **Calendar months**, keyed `YYYY-MM` off `date[:7]` — the same bucketing as
  `/api/v1/spending/trend`.
- **Today's FX rate** via `portfolios._get_fx_rate`, consistent with every
  other spending endpoint. Not historical-date FX.
- **Transfers excluded** (`is_transfer = 0`), so a bank→broker transfer is
  never counted as both spending and an investment contribution — the
  investment side reads `bookings` instead.
- **Actual per line type:** spending/debt sum negative amounts over the
  category's whole subtree; income sums positive amounts the same way;
  investment takes net `bookings` (Deposits − Withdrawals) for its portfolio.
- **Positive variance is always favourable** — `planned − actual` for
  spending/debt, `actual − planned` for income and investment. Costs are good
  under plan; earning and contributing are good over plan. Each line also
  carries an explicit `favourable` flag so no caller re-derives the direction.
- **No two category lines on the same branch.** A parent's actual already sums
  its children's subtree, so budgeting both `Housing` and `Housing > Rent`
  would count the same euros twice. Rejected with 400 on create and on bulk
  save (where the check must consider the rest of the batch, not just what is
  already stored), and mirrored client-side.
- **Root/type invariant:** an income line's category must sit under `Income`,
  spending/debt under `Spend`, via `db.get_spending_category_root`. 400 on
  mismatch, mirroring what `PUT /api/v1/spending/{id}` already enforces.
- **Unbudgeted actuals are attributed by sign, not by tree position.** This is
  the subtle one, and getting it wrong is what broke reconciliation during
  implementation: filtering on the category's tree root silently dropped three
  real cases — a refund in a Spend category (positive but Spend-rooted), a
  charge in an Income category, and anything unfiled, including
  `uncategorized`, which exists in a real database as a parentless non-root
  node with no root at all. Attributing by sign is what `/spending/summary`
  and `/spending/trend` do, so all three surfaces agree.
- **Unbudgeted rollup stops below anything budgeted.** An uncovered charge
  climbs to its highest ancestor that is neither budgeted nor an ancestor of
  something budgeted. With no lines, everything rolls to the direct children
  of Income/Spend; with a line on `Housing > Rent`, a stray
  `Housing > Utilities` charge reports as `Utilities` — naming the line worth
  adding — rather than as a confusing second `Housing` row.

## Structure

- `portf_manager/services/budget.py` — pure helpers (`parse_overrides`,
  `month_range`, `planned_for_months`, `subtree_names`, `is_ancestor`,
  `coverage_conflict`, `budgeted_or_above`, `uncovered_rollup_key`,
  `variance`) plus `compute_budget_variance` and `propose_budget_lines`, both
  of which take their FX converter as an argument so tests need no network.
  `subtree_names` is shared with `/spending/categories/breakdown`, which
  previously carried its own copy of the walk.
- `portf_server/routers/budgets.py` — CRUD, bulk upsert, `/variance`,
  `/seed-proposals`, `/summary`. `/summary` **must** be registered before
  `/{budget_id}`: FastAPI matches first, so a single-segment route declared
  after it is swallowed as a budget id. `/variance`, `/summary` and
  `/seed-proposals` are plain `def` so the blocking FX lookups run in the
  threadpool.
- `check_budget_overruns` in `portf_manager/services/action_items.py` — flags
  costs over plan and contributions short of plan, each by both >10% and >€50,
  plus one low-severity item when more than 15% of the month's spending is
  unbudgeted. Silent with no active budget.
- Frontend: a `budgetPage` under the Planning nav section with Overview / Edit
  / Scenarios tabs; `loadDashboardBudget()` for the Dashboard card. Pure
  helpers (`budgetRowStatus`, `budgetMonthRange`, `expandBudgetLineMonths`,
  `budgetCoverageConflict`) are module-scope and `window.`-exported for the
  DOM-free test runner.

## Seeding

`propose_budget_lines(db, months, fx)` averages the last N **complete** months
(the current partial month is excluded, so a mid-month run doesn't halve every
average) per direct child of Spend and Income, plus one investment line per
portfolio with net deposits. It writes nothing; the UI shows a reviewable
checklist and applies via bulk upsert — the same "nothing is written until
Apply" shape as the Spending page's AI category suggestions.

## Follow-up: money you kept vs money you spent

Shipped alongside, after the first real budget read a monthly "spending" figure
when a large share of it was money the user still had. On the account in question
roughly a third of that total was a mortgage repayment, unmatched transfers, a
renovation and investment contributions — none of it consumption.

Three changes, all confined to the budget layer:

1. **An investment line can be keyed by a spending category**, not just a
   broker. `is_broker_ref(ref_key)` (`str(ref_key).isdigit()`) tells the two
   apart: all digits → broker, actual from `bookings`; otherwise a Spend-rooted
   category, actual from that category's bank outflows. The bank side is the
   only way to budget a destination pfm doesn't track — a pension plan — and
   the only one that can be reclassified. A category-keyed investment line
   joins the same Spend coverage set (`line_uses_category`), so its euros can't
   also appear as unbudgeted spending.

2. **`line_type` is editable in place.** Reclassifying is the lever, so it's a
   dropdown per row in the Edit tab and per proposal in the seed panel, rather
   than a delete-and-recreate. `ref_key` stays immutable, so coverage can't be
   sidestepped. Seeding deliberately does **not** guess types — whether an
   outflow is debt, a contribution, or consumption is a judgement about intent,
   not a pattern in the data — so it defaults to `spending` and makes the
   choice one click at review time.

3. **Root-dominant seeding.** Walking only a root's children missed income
   filed against the bare `Income` root: nearly all of it, proposing an amount an order of
   magnitude below the real one. Seeding now compares a root's own direct activity to the
   sum of its children's and proposes whichever holds more (they can't coexist
   — a root line covers the whole subtree). A root proposal's amount is the
   whole subtree total, since that's what the line measures.

And one invariant that fell out of testing it: **a section reports one
measurement basis, never the sum of two.** With a bank-side investment line
present, `bank_basis` empties the Investments section's broker-side
`unbudgeted` list and sets `unbudgeted_suppressed`. Real data made the reason
obvious: bank-side contributions sat alongside broker deposits an order of
magnitude larger describing the same money, the broker figure inflated further
by transfers between the user's own accounts.

Result on the account in question: the spending figure fell by about a quarter,
the income figure rose by an order of magnitude, Debt and Investments were
populated, and planned net went from heavily negative to roughly break-even.

## Missing-data caveat

Actuals only exist for what has been imported on the Spending page, so a month
nobody has imported yet reads as zero spend — gloriously under budget. This
bites every time you open the page mid-month before importing.
`budgetMonthsWithoutActivity(variance)` names those months; the Overview tab
shows them as a warning banner, and the Dashboard card replaces its net line
with an explanation and suppresses the per-section variance figure rather than
show a flattering green number.

The Overview trend chart plots spending + debt only. Investment contributions
are an order of magnitude larger and lumpier (and include moves between the
user's own broker accounts), so including them buried the comparison — the
first build did include them and the chart was unreadable. This also matches
the KPI tiles, which are spending-only.

## Verification

Reconciled against real data: the budget's spending + debt actuals and income
actuals match `GET /api/v1/spending/trend?months=6` to the cent for all six
months. That equality is the invariant worth re-checking after any change to
either surface.
