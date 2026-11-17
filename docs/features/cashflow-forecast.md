# Cash-flow forecast

> New 2026-11-15 (`feat/cost-control`). Code: `portf_manager/services/cashflow_forecast.py`; route `GET /api/v1/spending/forecast?months=12&portfolio_id=` in `routers/spending.py` (plain `def`: FX lookups). Also chat `get_cashflow_forecast`, MCP `cashflow_forecast`, the Budget page's Forecast tab (`web-client.md` → Cost control) and a slice of the monthly review (`spending.md`).

Where this month ends and how the next N months (default 12, 1 to 36) look:
income, recurring bills on the dates they fall due, everyday spending, net and
the projected bank balance. Deterministic, no LLM, and **every figure is an
estimate built from past months**.

## Method

- **Baseline.** The last up to 6 complete calendar months that have imported
  rows and end on or before the data-through date (`baseline`; the effective
  date, see Freshness). The rule that picks the months and the everyday
  spending per category (`baseline_month_list`, `everyday_outflows`) is shared
  with the budget pace. Per month it
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
  from the **data-through date** of the data (the earliest last-import date
  over the accounts with rows in the baseline window), not from today: a
  statement that stops on the 10th is projected from the 10th. When the latest
  import is in the previous month, bills dated before the 1st still move the
  balance (`prior_month_bills_eur`) but only bills dated on or after the 1st
  count in this month's projected spending and `recurring_remaining_eur`. Income
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

## Planned items, tax payments and the what-if (2026-11-17, schema v46)

Code: `services/planned_items.py` (rules), the `planned_items` table, CRUD under
`/api/v1/spending/planned` (`GET ?include_done=`, `POST`, partial `PUT /{id}`,
`DELETE /{id}`; a rule violation is a 400 with the reason), and
`cashflow_forecast.estimated_tax_payments`.

- **Planned items** are future one-off expenses, savings or income entered by
  hand. `amount` is signed like a bank row (negative out, positive in, never 0);
  `kind` is `spending` | `savings` | `income` (`income` needs a positive amount,
  the others a negative one). An open item dated on or after the data-through
  date is placed once, in its month, and adds to that month's
  `planned_spending_eur`, `planned_savings_eur` or `planned_income_eur` (also on
  `this_month`); net, balance change, projected balance, the low/high range and
  `first_below_zero` include them. **They never enter a baseline, the budget
  pace or a comparison.**
- `planned_items` in the response lists each open item with a `status`:
  `projected`, `overdue` (dated before the data-through date: not projected, one
  warning "mark them done or move their date"), `beyond_horizon`, `unconverted`
  (no FX rate) or `what_if`. `amount_eur` is signed. An item tied to another
  bank account than the `portfolio_id` filter is left out. Marking an item
  `done` (the real row is now in a statement) stops it being projected.
- **What-if without saving.** `GET /spending/forecast?extra_amount=&extra_date=&extra_kind=spending`
  adds one unsaved item (`id: null`, status `what_if`) and returns
  `what_if: {amount_eur (signed), date, lowest_balance_eur, lowest_balance_month,
  lowest_balance_without_eur, goes_below_zero}`. `extra_amount` is a positive
  magnitude, `extra_kind` gives the sign, `extra_date` is `YYYY-MM` (taken as
  the 15th) or a full date; amount and date must come together (400). A
  what-if dated before the data-through date or beyond the horizon is not
  counted and says so in `warnings`. Chat: `get_cashflow_forecast` takes the
  same arguments; MCP: `extra_amount`, `extra_month`, `extra_kind`.
- **Estimated quarterly tax.** The reserve maths is the allocator's
  (`services/deployable.py`: `quarter_reserve`, `due_date`, the settings loader),
  not a copy. One entry per quarter whose due date lies between the data-through
  date and the horizon: `{quarter, due, amount_eur, basis}`. The previous
  quarter contributes what is still unpaid (`accrued`); the current quarter the
  larger of its accrual so far and the average of the last up to 4 complete
  quarters (`accrued` or `average`); later quarters that average. Each month
  gains `tax_eur` (already inside `spending_eur`), the response `tax_payments`
  and an assumption line naming the IVA and IRPF rates. **Only when the
  allocator settings name work-income merchants**; with none ticked there is no
  tax estimate and a warning says where to tick them (as does a history with no
  complete quarter of work income). `include_tax=false` switches it off.
- **Past-due is judged at the data-through date.** `quarter_reserve` zeroes an
  unpaid quarter more than 30 days after its due date; the forecast passes the
  data-through date as that reference, because the balance starts there: a
  quarter due on or after it that is missing from the statements is still
  projected, even when `today` is far later and an account lags. The allocator's
  own use of `quarter_reserve` is unchanged.
- **No double counting.** When the estimate is in use, recurring series with a
  **quarterly** cadence in one of the allocator's tax-paid categories (the
  pagos fraccionados the estimate replaces) are dropped from the known bills; a
  yearly or other-cadence series in those categories stays a known bill. And
  rows in those categories are left out of the everyday baseline **of the
  forecast only** (`everyday_outflows` as the budget pace uses it is unchanged:
  the new parameter defaults off). When the estimate is not in use, behaviour
  is exactly as before.

## Freshness and degradation

- `accounts` lists each bank account with `data_through`, `days_behind`,
  `stale` (no import for more than 7 days, or none at all), `balance_eur` and
  `balance_date`; a stale or empty account adds a warning. `data_through` of
  the forecast is the **earliest** of them over the accounts that have rows in
  the baseline window (an account with none there is ignored); a warning names
  the account that lags behind the others.
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
- A one-time annual tax settlement (the Renta result, say) is not estimated;
  enter it as a planned item.
- A planned item is not a bill: it never changes an average, and an item left open after its date stops counting (overdue) until it is marked done or moved.
- Tax payments are an estimate with a `basis`; label them as such, and never add them to the recurring bills yourself.
- Changing what counts as income or recurring changes the review and the chat
  figures at once, since they all call `load_forecast`.
