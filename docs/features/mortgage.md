# Loans & Mortgage page

Read-only page (sidebar: Planning, "Loans & Mortgage", page id `loans`) for the loans entered on Net Worth. It works with zero loans (empty state linking to Net Worth), one, or several (a loan picker). Loans are still created and edited on Net Worth; this page changes nothing.

## Maths

- `portf_manager/services/loans.py` is pure maths on a loan dict. Besides the existing schedule it has `payment_rows` (date, payment, interest, principal, balance after), `payment_split(loan, "YYYY-MM")`, `anchor_info` and `what_if`. `schedule()` rows also carry `principal_paid_eur` and `payments_count`.
- `portf_manager/services/mortgage.py` adds what needs the database: `summary`, `equity`, `reconciliation`, `overview`. Routers only wrap them.
- A payment falls on each monthly anniversary of the anchor, so the first payment is one month after it and the start year has 11 payments.

## Endpoints (`/api/v1/networth/loans/...`, plain `def`)

- `GET /{id}/overview?category=` returns `summary`, `equity`, `schedule` (one row per year) and `reconciliation`.
  - `summary`: balance today, rate and `rate_as_of`, monthly payment, end date, `months_left`, `interest_remaining_eur`, principal/interest paid so far this calendar year and scheduled for the whole year, and `anchor_note` when the schedule runs from a recorded balance (history before it is unknown).
  - `equity`: manual assets of category `property` (not liabilities) summed, less the loan balance, with `ltv_pct`. No property line gives `equity_eur: null` and a `reason`. With several loans the balance of all loans is used and `note` says so.
  - `reconciliation`: see below.
- `GET /{id}/what-if` (read-only). Params `lump_sum`, `lump_date` (default today), `extra_monthly`, `mode` (`term` keeps the payment and finishes sooner; `payment` keeps the end date and lowers the payment), `invest_return_pct`. Returns `baseline`, `scenario`, `interest_saved_eur`, `months_saved` and `invest`. 422 for negatives, a lump sum above the balance on that date, an unknown mode, `extra_monthly` with `mode=payment`, or no payments left after the date. The lump sum is applied to the balance as of the last scheduled payment on or before `lump_date`; later payments keep their dates.
- `PUT /{id}` accepts a partial body: a missing field stays, an explicit null clears `lender`, `rate_as_of`, `balance_as_of` or `notes`, and null on any other field is a 422.

## Payments vs bank

- Actual = debits (amount < 0) of non-transfer `spending_transactions` in one category, summed per calendar month. Category: `?category=`, else the category named like the loan (case-insensitive), else `Mortgage`; none found gives `rows: null` and a `reason`. Only an exact category name is used, not its sub-categories.
- One row per scheduled payment up to today, from the anchor (or loan start). Status `ok` (within one euro), `differs`, `missing` or `pending`.
- `missing` needs a statement that reaches past the month end (latest import over the accounts holding that category). Until then a month without a debit is `pending`, so a month never looks missed because nothing was imported yet.
- Debits in months before the anchor are returned apart as `before_anchor` (amounts only).
- `hint`: when the last two complete months both differ by the same amount (within 0.5), "payment changed: the rate may have been revised".

## Investment comparison

With `invest_return_pct`, the same money (the lump sum, plus the extra monthly amount for the months the scenario still runs) is compounded monthly to the baseline end date. The result is before tax, costs and risk, and labelled not advice. The payment freed once the loan ends sooner is not invested.

## Action item

`check_mortgage_payment` (`networth` category, `medium`, id `mortgage:payment-differs:{loan_id}`, link page `loans`) when the latest complete month with a debit is `differs`. Each loan is checked on its own and a failing loan is logged and skipped. See `action-items.md`.

## Web client

`loadLoansPage` and the pure helpers (`loanCards`, `loanChartModel`, `loanBalanceSvg`, `loanPaidSvg`, `loanReconHtml`, `loanWhatIfQuery`, `loanWhatIfHtml`, `loanRecentRows`) are in `pfm_analytics.js`, tested in `web_client/js/tests/loans_page.test.mjs`. The what-if call is debounced (350 ms) and stale answers are dropped. The yearly table is collapsed below 768 px. `apiClient.getLoanOverview` replaced the unused `getLoanSchedule`; `GET /{id}/schedule` stays.

## Budget link

A budget debt line can point at a loan: see `budgeting.md` (schema v44, `budget_lines.link_type`).
