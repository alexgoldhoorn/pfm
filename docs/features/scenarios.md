# What-if scenarios

Four questions answered from the household's own numbers (bank statements,
balance sheet, held portfolio), for the AI chat, the API and the MCP server.
The maths lives in `portf_manager/services/life_scenarios.py`; everything else
is a thin wrapper, so chat, API and MCP always give the same figures.

| Scenario | Service function | Chat tool | API (GET, query params) | MCP tool |
|---|---|---|---|---|
| Income from work stops | `run_job_loss` | `run_job_loss_scenario` | `/api/v1/scenarios/job-loss` | `scenario_job_loss` |
| Income changes | `run_income_change` | `run_income_change_scenario` | `/api/v1/scenarios/income-change` | `scenario_income_change` |
| Buying a home | `run_house_purchase` | `run_house_purchase_scenario` | `/api/v1/scenarios/house-purchase` | `scenario_house_purchase` |
| Market crash and recovery | `run_market_crash` | `run_market_crash_scenario` | `/api/v1/scenarios/market-crash` | `scenario_market_crash` |

`GET /api/v1/scenarios/baseline` returns the inputs every scenario starts from.
The chat also has `get_spending_cut_candidates` (the tier split and cut order
without a scenario) and `get_spending_matrix` (exact spending per month by
category or merchant). The endpoints are GETs because they only read; a bad
input is a 422.

## The baseline

`household_baseline(db, today, fx, months=12)`:

- **Income and spending** are monthly averages over the last complete calendar
  months that have imported rows (the month in progress never counts),
  transfers excluded. Money moved into savings (categories `Invest` and
  `House purchase` and their children) is not spending; it is reported as
  `savings_transfers_monthly_eur`.
- **Work income** is the income rows from the merchants ticked in the allocator
  settings (`work_income_merchants`). With none ticked every income row counts
  as work income, and a warning says so.
- **Money**: `liquid_eur` and the investments group from the balance sheet, and
  the unrealised gain from `compute_positions` at the latest stored prices.
- **Loans**: balance and payment per loan. Loan payments are already inside the
  spending figures (they are bank rows), so they are never subtracted twice.

- **Irregular spending**: a category with spending in at most a quarter of the
  months used (given six or more months) is flagged `irregular` and named in a
  warning, because a monthly average spreads a one-off (a renovation) thin.
  Every scenario that uses spending takes `exclude_categories` (comma-separated
  over HTTP and MCP; sub-categories included, case-insensitive) to leave such
  a category out; what was left out is reported back and in a warning. The
  same option covers a cost that would stop with the income (tax on it).

Each source degrades on its own: a failing one leaves its figure `None` and adds
a warning, and the scenario still answers.

## Spending tiers (schema v42)

`spending_categories.essentiality` is `fixed`, `flexible`, `optional` or NULL:

- **fixed**: contractual, hard to change within months (mortgage, insurance,
  school);
- **flexible**: needed, but the amount can be reduced (groceries, energy);
- **optional**: can stop (restaurants, travel, subscriptions).

A category without a mark inherits its nearest marked ancestor's
(`tier_of`); with none it is `unclassified`. Unclassified spending is **never
cut** and raises a warning: guessing would make the runway look longer than it
is. Nothing is filled in by the migration. `PUT
/spending/categories/{name}/essentiality` sets or clears a mark (roots cannot
carry one); `POST /spending/categories/suggest-essentiality` asks the fast LLM
tier for suggestions for unmarked Spend categories, sends only category names,
and saves nothing.

## Job loss

Three levels: `as_today` (all spending), `no_optional` (optional categories
dropped) and `lean` (flexible categories also trimmed by `flexible_cut_pct`,
default 20). For each: the monthly gap, `months_on_cash` (bank cash plus liquid
deposits), `months_with_investments` (the portfolio sold too, after tax on the
gain; `None` = lasts beyond 50 years) and the money left month by month for
five years (a ready-made chart series). A month counts when it can be paid in
full. Inputs: the income lost (default all work income), a benefit for N
months, severance, and a simultaneous market drop. `cut_first` lists optional
categories in full, then flexible ones at the trim, largest first.

## Income change

Give `change_pct` or `new_income_monthly_eur` (exactly one). Returns the net
and savings rate before and after, whether the open goals' planned
contributions still fit, and either the cuts that would keep saving the same or
(when spending now exceeds income) the job-loss levels for the gap. When a
primary Wealth Simulator scenario exists, `plan_impact` re-runs it with the
monthly contribution lowered by the income lost (never below zero).

## House purchase

Cash needed = down payment + purchase costs − sale proceeds. Cash is used down
to a buffer (`buffer_months` of the *new* spending), then investments are sold;
`gross_sale_for` grosses the sale up for tax assuming every euro sold carries
the portfolio's average gain. A shortfall is reported, not hidden. Also: the
mortgage payment and total interest, the month before and after, loan payments
as a share of income against `DEBT_RATIO_LIMIT_PCT` (35), the highest price that
limit allows, and what is left. Net worth falls only by the costs and the tax:
the down payment becomes home equity.

Assumptions the caller can change, and that an answer must state:
`purchase_costs_pct` (default 12: transfer tax differs by region and by new
build or resale) and `rate_pct` (default 3). Property value changes and tax
deductions are not modelled.

## Market crash

The loss comes from `services.stress.run_stress` (fund look-through), for a
`drop_pct` on all equities or a preset (`ai_bubble` was added for this: tech,
semiconductors and communication services −60%, other equities −30%).
`crash_recovery` then gives the months until the portfolio shows today's value
again at a steady annual return, for `expected_return_pct` ± 2 points, with
and without monthly contributions. The contribution defaults to the primary
Wealth Simulator scenario's, else the average monthly transfer into
investments. "Recovered" means the figure is back, new money included; it is
not "the market recovered". `HISTORICAL_CRASHES` adds approximate index price
levels (USD, peak to trough and back) as a reference; they are hand-maintained
constants, not fetched.

## Tests

`tests/unit/test_life_scenarios.py` (pure maths on a fixed baseline, the
database wiring, the endpoints and the chat tools), `mcp/tests/test_scenarios.py`.
