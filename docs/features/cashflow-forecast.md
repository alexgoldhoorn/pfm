# Cash-flow forecast

> New 2026-11-15 (`feat/cost-control`). Code: `portf_manager/services/cashflow_forecast.py`; route `GET /api/v1/spending/forecast?months=12&portfolio_id=` in `routers/spending.py` (plain `def`: FX lookups). Also chat `get_cashflow_forecast`, MCP `cashflow_forecast`, the Budget page's Forecast tab (`web-client.md` → Cost control) and a slice of the monthly review (`spending.md`).

Where this month ends and how the next N months (default 12, 1 to 36) look:
income, recurring bills on the dates they fall due, everyday spending, net and
the projected bank balance. Deterministic, no LLM, and **every figure is an
estimate built from past months**.

## Method

- **Baseline.** The last up to 6 complete calendar months that have imported
  rows and end on or before the data-through date (`baseline`). Per month it
  sums income, everyday spending and savings; the forecast uses the average,
  and the smallest and largest single month give the low/high range. One-off
  rows and transfers are left out. Fewer than 3 months gives a "rough baseline"
  warning; none gives no income or spending projection at all.
- **Income** is the average of ALL incoming non-transfer, non-one-off money
  over those months (owner-confirmed choice): salary, interest, anything
  positive. Positive rows filed under Spend are refunds, not income.
- **Everyday spending** = non-transfer outflows minus the rows that belong to a
  recurring series, outside the savings categories. **Recurring and variable
  never overlap:** the recurring charges are added back separately on the dates
  the detection expects them (`due_dates`, quarterly and yearly included, one
  charge per due date; a charge that was due but never imported is not
  counted). A charge due later this month is counted once, as a recurring
  charge.
- **Savings.** Money moved to `Invest` / `House purchase` is `savings_eur`
  (baseline average plus recurring savings bills), subtracted from the balance
  but not counted as spending.
- **This month.** Spent and income so far, the recurring charges still to
  arrive, a variable estimate for the days left and a projected month end
  (spending, income, net, balance, with a range). The days left are measured
  from the **data-through date** of the data (latest imported row), not from
  today: a statement that stops on the 10th is projected from the 10th. Income
  still to arrive = the baseline average minus the regular income already seen,
  never negative.
- **Next months.** Per month: `income_eur`, `recurring_eur` (bills due that
  month), `variable_eur`, `savings_eur`, `spending_eur`, `net_eur`,
  `balance_change_eur`, `projected_balance_eur`, a `range` and
  `known_bill_count`. `known_bills` lists every expected charge (date, merchant,
  amount, kind spending or savings, account). `first_below_zero` is the first
  month whose balance goes under zero; each such month carries
  `balance_below_zero`.
- **Starting balance** is the sum of the latest stored balance of each bank
  account that has one (`get_latest_bank_balance`). An account without a stored
  balance is left out and named in a warning, never counted as zero.

## Assumptions the response states (`assumptions`)

Income and everyday spending are past averages; recurring charges sit on their
expected dates; savings moves are not spending; amounts are EUR at today's
rate; the rest of the month is measured from the data-through date. The
baseline months are listed too.

## What is excluded

- **The projected balance is bank-only.** Transfers to your own accounts (a
  broker, say) are not projected, so it is not a net-worth projection.
- One-off rows and transfers (left out of the baseline), and anything not yet
  visible in an imported statement. Irregular income that is not marked one-off
  is averaged in like any other income.
- A **yearly bill larger than the fund built so far** is not smoothed here: it
  lands on its due month in `recurring_eur` (the budget's fund view is
  `budgeting.md`).

## Freshness and degradation

- `accounts` lists each bank account with `data_through`, `days_behind`,
  `stale` (no import for more than 7 days, or none at all), `balance_eur` and
  `balance_date`; a stale or empty account adds a warning. `data_through` of
  the forecast is the newest of them.
- Each source (bank rows, category tree, recurring series, accounts) is loaded
  on its own. One that fails becomes a warning and null in the figures that need
  it, never a zero and never a 5xx: without the tree or the series there is no
  baseline; without accounts no balance; `this_month.status` is `ok`,
  `no_baseline` or `no_imports` (nothing imported for the month yet: projected
  from the baseline and recurring charges alone, and the spent/income to date
  are null).
- Nothing is invented for an empty database: 200 with `data_through: null`, null
  projections and a "No bank statement is imported" warning.

## Gotchas

- A `null` is "not known", not zero: clients print "not available".
- `months` outside 1..36 is a 422.
- Changing what counts as income or recurring changes the review and the chat
  figures at once, since they all call `load_forecast`.
