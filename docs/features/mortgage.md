# Loans & Mortgage page

Page (sidebar: Planning, "Loans & Mortgage", page id `loans`) for the loans entered on Net Worth. It works with zero loans (empty state linking to Net Worth), one, or several (a loan picker). Loans are still created and edited on Net Worth; the only data this page changes is the rate history (below).

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

## Rate history (schema v45)

- Table `loan_rate_periods` (id, loan_id FK ON DELETE CASCADE, from_date, annual_rate_pct, balance_eur nullable, note, created_at, updated_at; UNIQUE(loan_id, from_date)). Nothing is backfilled.
- Periods describe the loan's past and need an anchored loan (`rate_as_of` + `balance_as_of`); a period must start between `start_date` and the anchor date (inclusive). Without usable periods everything behaves exactly as before, to the cent, and the yearly rows carry no `history` key.
- `loans.history_rows`: runs from `start_date` with the original principal over the original term. A period's rate applies to every payment whose previous payment date is on or after its `from_date`; the earliest period's rate also covers any time before it. The payment is recalculated at each period start over the remaining term, and `balance_eur` re-anchors the balance there. `extra_payment_eur` is not applied to history. Payments stop at the anchor date.
- The forward projection from the anchor is untouched (balance today, end date, remaining interest). History only adds: `history: true` yearly rows before the anchor year (the anchor year keeps its forward figures and gains `history_*` fields), `summary.paid_to_date` and `summary.has_history`, and `rate_periods` on the overview.
- `paid_to_date`: interest, principal, total, `payments_count`, `from_date`, `complete` (true for an unanchored loan, or an anchored one with history; false for anchored without periods, where sums would start at the anchor), `anchor_gap_eur` (history balance at the anchor less the recorded balance: a big gap means the periods are off).
- Reconciliation: with history, `before_anchor` entries carry `scheduled_eur`, `difference_eur`, `status`, `complete`, `compared` (months with no history payment have `compared: false`). Without periods they keep the old `{month, actual_eur}` shape. Forward `rows`, the hint and the action item are unchanged.
- Endpoints: `GET/POST /{id}/rate-periods`, `PUT/DELETE /{id}/rate-periods/{period_id}`. 422 for an unanchored loan, a date before the start or after the anchor, a rate outside 0-50 or a balance above the principal; 409 for a duplicate date (checked before INSERT: `get_connection()` turns an `IntegrityError` into `DatabaseError`); 404 for a period of another loan. PUT is partial; null clears `balance_eur`/`note`.

## Rate-change what-if

`GET /{id}/what-if` also takes `new_rate_pct` or `rate_change_pts` (exactly one) and `rate_from` (default today). The balance is taken after the last scheduled payment on or before `rate_from`, the payment is recalculated over the remaining payments (the fixed extra payment stays on top). The reply is the usual shape plus `rate_change` (`previous_rate_pct`, `new_rate_pct`, `balance_eur`, `payment_before_eur`, `payment_after_eur`, `payment_diff_eur`, `interest_diff_eur`); `interest_saved_eur` is negative when the rate rises. A rate change is **rejected with 422** when combined with a lump sum, extra monthly, `mode=payment` or an investment return (their dates would interact): run them separately. Rate outside 0-100 % or `rate_from` with no rate: 422. The web panel disables the other fields while a rate is typed.

## Detect a loan from bank rows

`GET /networth/loans/detect?category=&description=&principal=&term_months=` (registered before the `{loan_id}` routes; read-only, creates nothing). Debits come from `category`, else the category `Mortgage`, else one named like an existing loan; `description` narrows by substring. Needs 3+ months with a debit covering 60 % of the span. Monthly sums are grouped into stretches of an unchanged amount (within a euro) lasting 2+ months; one-off months are dropped, equal neighbours merged. Reply: `found`, `reason`, `monthly_payment_eur` (latest stretch), `first_payment_date`, `stretches` (each with `annual_rate_pct` and `balance_at_start_eur` when solvable), `rate_periods` (candidates for every stretch but the last, dated the payment before the change), `form` (start date one month before the first payment, plus rate, `rate_as_of`/`balance_as_of` when the amount changed), `notes`. Rates need a principal and term (given, or a principal from an incoming row at least 20 times the payment under a description filter); they assume the first debit found is the loan's first payment and an annuity over the remaining term. Nothing found gives `found: false` and a reason, never a 5xx. The Net Worth loan form has a "Detect from bank" button that fills the fields; saving stays manual.

## Agent tools

MCP `loans` (per loan: balance, rate, payment, end, interest, paid to date, rate periods, equity/LTV, latest complete bank month) and `loan_what_if` wrap the overview and what-if endpoints. Chat `get_loans` (via `mortgage.digest`) and `loan_what_if` call the services directly. All read-only.
