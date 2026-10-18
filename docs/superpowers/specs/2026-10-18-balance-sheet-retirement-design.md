# Balance sheet and retirement readiness — design

**Date:** 2026-10-18
**Status:** draft for review
**Area:** net worth, goals, action items, MCP tools
**Supersedes nothing.** Builds on `docs/features/networth-portfolios.md`
and the price-guard / ongoing-charges work of 2026-10-18.

## 1. Intent

The owner wants one screen that answers three questions:

1. What do I own, in total and by line, and how fresh is each number?
2. What do I owe?
3. If my family keeps living at today's financial level, will the money
   last from the day I stop working at 65 for the rest of our lives?

Today pfm has the parts but not the answer. `GET /networth/` adds up
brokerage positions, bank balances, manual lines and deposits, but a manual
line typed a year ago looks exactly like one typed yesterday, broker cash is
not counted at all, the mortgage is a number someone has to remember to
update, and the Indexa account is valued at lagging fund NAVs. The Goals page
holds "Retire at 65" with a placeholder target and reads a progress
percentage that means nothing. The Wealth Simulator can project a pot, but
only from numbers the user types in each time, and it never compares the
result with what the household actually spends.

This design adds three things: a **balance sheet** in which every line knows
where it came from and when; a few **sourced lines** that replace typed
figures (loan schedule, live account valuation, broker cash); and a
**retirement readiness** projection that turns the sheet, the last twelve
months of spending and the expected pensions into a verdict in today's euros.

## 2. Goals, non-goals, anti-goals

### Goals

- One page, the existing **Net Worth** page, shows assets, liabilities, net
  worth and the retirement verdict together.
- Every balance-sheet line carries a `source`, an `as_of` date and a
  `stale` flag with a per-source allowed age. Stale lines are visible on the
  sheet and raised as Action Items.
- The retirement verdict is deterministic, reproducible and explained: the
  user can see the spending target, the pensions assumed, the return
  assumption and the year the money runs out (or does not).
- The same verdict is shown for retirement at the goal age (65) and at the
  Spanish statutory age (67), because the state pension only starts at 67
  unless taken early with a reduction.
- Everything is in **today's euros** (real terms). Nominal numbers appear
  nowhere on this screen.
- Nothing is guessed. A missing input (no pension entered, no birth date,
  fewer than 6 months of bank data) produces an explicit "not enough data"
  verdict, never a silently optimistic one.

### Non-goals

- Computing the Spanish state pension from contribution history. The user
  looks it up in the Seguridad Social simulator and types the monthly
  figure.
- Dutch AOW: treated as zero unless the user adds an income line.
- Selling or downsizing the home. The owner stays in the house; the home is
  a net-worth line and never funds retirement.
- Monte Carlo paths, inflation scenarios or asset-allocation glide paths.
  The Wealth Simulator keeps those what-ifs; readiness gives one base case
  and one pessimistic case.
- Replacing the Wealth Simulator, the Goals page or `GET /networth/`.
- Tax modelling of withdrawals beyond one flat haircut setting (see §6.4).
- Feeding the Finance Weekly Review or the Telegram digests. That is a
  follow-up in `~/mcp` once the API exists.

### Anti-goals (what would make this a failure)

- A verdict that reads "on track" because a stale or missing input was
  counted as zero spending, zero debt or a fresh valuation.
- A second total that disagrees with `net_worth_eur(db)`. The balance sheet
  **is** the net worth; the helper is rewritten on top of it, not beside it.
- Real amounts in the public repository. Tests and docs use invented round
  figures.

## 3. Constraints

- SQLite schema version becomes **35**. New tables go in both
  `_create_all_tables` and `_migrate_to_v35`.
- Blocking I/O endpoints stay plain `def` (FX lookups, Indexa fetch).
- The Indexa fetch is network I/O and must sit behind the autouse network
  guard in tests: mock `portf_manager.services.indexa._get`.
- `web_client/` is vanilla JS, no build step; new helpers are
  `window.`-exported for the DOM-free tests; the page must work at 390 px.
- MCP: a new tool is registered in the gateway (`~/mcp/remote_gateway/
  server.py` incl. `TOOL_SPECIALIST`), the finance agent's tools line
  (generated from `~/agents/finance/adapter.yaml`) and the persona.
- Public repo: no real amounts, ISINs, account numbers or home paths in any
  committed file. The Indexa account number lives in a DB row, never in
  code or docs.

## 4. The balance sheet

### 4.1 Endpoint

`GET /api/v1/networth/balance-sheet` → 

```json
{
  "as_of": "2026-10-18",
  "totals": {
    "assets_eur": 0, "liabilities_eur": 0, "net_worth_eur": 0,
    "financial_assets_eur": 0, "liquid_eur": 0, "stale_lines": 0
  },
  "groups": [
    {"key": "cash", "label": "Cash", "total_eur": 0, "lines": [...]},
    {"key": "deposits", "label": "Fixed deposits", ...},
    {"key": "investments", "label": "Investments", ...},
    {"key": "property", "label": "Property", ...},
    {"key": "other_assets", "label": "Other assets", ...},
    {"key": "liabilities", "label": "Liabilities", ...}
  ]
}
```

Each line:

```json
{
  "id": "portfolio:3", "label": "Indexa Capital", "group": "investments",
  "amount_eur": 0, "amount": 0, "currency": "EUR",
  "source": "account_valuation",
  "as_of": "2026-10-17", "age_days": 1, "max_age_days": 7, "stale": false,
  "note": null,
  "edit": {"kind": "portfolio", "id": 3}
}
```

- `financial_assets_eur` = the `cash`, `deposits` and `investments` groups.
  This is the pot the retirement projection draws on. `property` and
  `other_assets` are in net worth and never in the pot.
- Manual-line categories map to groups as follows: `cash`,
  `current_account`, `savings_account` → `cash`; `property` → `property`;
  `other` and any unknown category → `other_assets`; every line with
  `is_liability = 1` → `liabilities`, whatever its category. There is no
  pension or vehicle category today; a pension plan, if one is ever added,
  is a new category that goes in `investments` and is withdrawable only
  from the plan's retirement age (not in this design).
- `liquid_eur` = cash + deposits maturing within 12 months. Used by the
  emergency-fund metric (replacing `_cash_eur` in `analytics.py`, which
  currently reads only bank balances and cash-type manual lines).
- `net_worth_eur(db)` in `networth.py` is reimplemented as
  `build_balance_sheet(db, today)["totals"]["net_worth_eur"]` so Goals,
  Action Items and the balance sheet can never disagree. `GET /networth/`
  keeps its response shape for the existing page and MCP tool, computed
  from the same builder.

### 4.2 Lines and their sources

| Line | Source | `as_of` | `max_age_days` | Notes |
|---|---|---|---|---|
| Brokerage positions, one line per brokerage portfolio | `compute_positions` × latest stored price × live FX (today's `_brokerage_value_eur`, split by portfolio) | oldest `price_date` among the portfolio's held auto-priced assets | 4 | Same rule as `/analytics/data-freshness`. An asset with no price makes the line stale with note "N assets unpriced". |
| Broker cash, one line per brokerage portfolio | ledger cash: deposits − withdrawals − buys + sells + dividends + interest − fees, at transaction-date FX (the existing `cash_eur` of `GET /portfolios/values`, moved into a shared service) | date of the portfolio's newest booking or transaction | 60 | New: today net worth ignores broker cash. Shown only when non-zero. |
| Account valuation (Indexa) | `account_valuations` row for the portfolio (§5.2) | row `as_of` | 7 | When a fresh valuation exists it **replaces** that portfolio's positions line and cash line, so the account is counted once. When it is stale or absent the positions line is used and gets note "valued at fund NAVs, account valuation N days old / never fetched". |
| Bank account, one per `account_type='bank'` portfolio | latest `spending_transactions.balance` (existing) | that row's date | 45 | A monthly statement import keeps it fresh. An account with no balance row stays excluded and is a stale line with note "no statement with a balance". |
| Fixed deposit | `fixed_deposits` principal (existing) | `start_date` | none | Never stale; maturity is known. Matured deposits with status `active` past `maturity_date` get note "past maturity, mark matured". |
| Loan (mortgage), one per `loans` row (§5.1) | amortisation schedule evaluated at `today` | today | none | Note shows the monthly payment and the end date. A variable-rate loan whose `rate_as_of` is older than 400 days gets note "rate last confirmed <date>". |
| Manual line | `manual_assets` amount (existing) | new `as_of` column | per category: `property` 365, `cash`/`savings_account`/`current_account` 90, everything else 180 | Existing rows get `as_of = updated_at` on migration. |

Possible double counting is flagged, never fixed: a manual line whose name
matches a portfolio name (case-insensitive) gets note "a portfolio with this
name exists; is this line still needed?" and raises a low Action Item. The
owner's own cleanup of today's manual lines (a savings account also tracked
as broker cash, a typed mortgage balance once the loan row exists) is done by
hand in the UI after deployment and is not part of the build.

### 4.3 Freshness in Action Items

A new check `check_balance_sheet_freshness(db)` in
`services/action_items.py` emits one item per stale line, id
`balance_sheet_stale:{line_id}`, severity `medium` for cash, bank, valuation
and position lines, `low` for property and other manual lines. The
client-side `computeNetWorthChecklist` keeps its setup gaps (account with no
balance, deposit without a portfolio); the server check covers freshness
only, so the two never overlap.

## 5. New sourced lines

### 5.1 Loans

Table `loans` (v35):

```
id INTEGER PK, name TEXT NOT NULL, lender TEXT,
principal REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'EUR',
annual_rate_pct REAL NOT NULL, rate_as_of TEXT,
start_date TEXT NOT NULL, term_months INTEGER NOT NULL,
extra_payment_eur REAL NOT NULL DEFAULT 0,
notes TEXT, created_at, updated_at
```

`services/loans.py` provides `balance_on(loan, day)`, `monthly_payment(loan)`
and `schedule(loan)` for a standard annuity (French amortisation):
payment = P·r / (1 − (1+r)^−n), balance after k payments =
P·(1+r)^k − payment·((1+r)^k − 1)/r, with r = annual_rate_pct/1200 and k =
whole months elapsed since `start_date` (a payment counted on each monthly
anniversary). `extra_payment_eur` is a fixed monthly overpayment that
shortens the term. A rate change is recorded by editing `annual_rate_pct`
and `rate_as_of`; the schedule from then on uses the new rate on the
current balance over the remaining term (the loan is not re-amortised from
origination). Balances are rounded to cents; the final payment clears any
residual.

Endpoints: `GET|POST /api/v1/networth/loans`, `PUT|DELETE
/api/v1/networth/loans/{id}`, `GET /api/v1/networth/loans/{id}/schedule`
(year-by-year balance and interest paid). The retirement projection reads
`schedule()` to know when payments stop.

The Wealth Simulator's "Load from Net Worth" prefill reads the first loan's
balance, rate and payment instead of the `mortgage` cashflow category when a
loan exists (builder's call on the exact wiring; the simulator's maths does
not change).

### 5.2 Account valuations (Indexa)

Table `account_valuations` (v35), one row per portfolio, upserted:

```
portfolio_id INTEGER PK REFERENCES portfolios(id) ON DELETE CASCADE,
provider TEXT NOT NULL, account_ref TEXT NOT NULL,
value REAL NOT NULL, cash REAL NOT NULL DEFAULT 0,
currency TEXT NOT NULL DEFAULT 'EUR',
as_of TEXT NOT NULL, fetched_at TEXT NOT NULL,
last_error TEXT
```

`services/indexa.py` wraps the Indexa Capital REST API
(`https://api.indexacapital.com`, header `X-AUTH-TOKEN`), reading the token
from a new setting `PORTF_INDEXA_TOKEN` (`.env.local`; never the DB). One
public function `refresh_account_valuations(db) -> list[dict]` iterates the
`account_valuations` rows whose provider is `indexa`, calls
`/accounts/{ref}/portfolio` and reads the `portfolio` object: its
`total_amount` becomes `value`, its `cash_amount` becomes `cash`, and the
response's valuation date becomes `as_of` (today's date if the API gives
none). **`value` includes `cash`**: the account's investments are
`value − cash`, and that split is what the projection uses (investments at
the investment return, cash at the cash return). The balance-sheet line
shows `value`. A failure writes
`last_error` and leaves the previous value and `as_of` untouched, so the
line simply goes stale after 7 days. The daily price cron
(`price_updater.update_all_prices`) calls it after the price pass when the
token is set; its result is reported in the run summary like held prices
are. `POST /api/v1/networth/valuations/refresh` triggers it by hand.

Linking: `POST /api/v1/networth/valuations {portfolio_id, provider,
account_ref}` creates the row with `value = 0` and `as_of = 1970-01-01` and
immediately attempts a refresh. The MyInvestor and Coinbase portfolios keep
their positions-based lines; Indexa is the only provider in this design,
and the table is shaped so a second one (a broker's CSV upload, a bank
API) is a new `provider` value rather than a new table.

The `~/mcp/indexa` MCP server already speaks this API from Hermes and the
remote gateway; pfm gets its own thin client because the API container has
no access to `~/mcp`.

### 5.3 Broker cash

The `cash_eur` computation in `routers/portfolios.py` (`GET
/portfolios/values`) moves to `services/cash_ledger.py` as
`cash_by_portfolio(db) -> dict[int, dict]` with `{cash_eur, as_of}`, and the
router calls it. The balance sheet uses the same function. No behaviour
change for the portfolios endpoint.

A brokerage portfolio can also carry a synthetic cash asset (today's
`MYINVESTOR-CASH`-style row, `asset_type='cash'`, manual price). Such an
asset is already inside the positions line; the builder must make sure the
ledger cash line **excludes** transactions on `asset_type='cash'` assets so
the same euros are not counted twice. The feature doc explains which of the
two ways to keep, and the owner picks one per portfolio by hand.

### 5.4 Retirement incomes

Table `retirement_incomes` (v35):

```
id INTEGER PK, name TEXT NOT NULL,
owner TEXT NOT NULL CHECK (owner IN ('self','partner')),
monthly_eur REAL NOT NULL,          -- in today's euros
from_age INTEGER NOT NULL,          -- age of the owner when it starts
to_age INTEGER,                     -- NULL = for life
indexed INTEGER NOT NULL DEFAULT 1, -- 1 = keeps pace with inflation
source TEXT, as_of TEXT, notes TEXT, created_at, updated_at
```

Examples the UI suggests on an empty table: "Spanish state pension (self),
from 67" with a link to the Seguridad Social simulator; "Partner state
pension, from 67"; "Dutch AOW" prefilled at 0. Amounts are entered
**gross**, which is what the Seguridad Social simulator reports; the whole
screen is before tax (§6.4). The projection takes an
income's value in the years between `from_age` and `to_age` inclusive of
the first, exclusive of the last. `indexed = 0` means the amount loses
2 %/year in real terms (the same `ASSUMED_INFLATION_PCT` the simulator
uses). Endpoints: `GET|POST /api/v1/retirement/incomes`, `PUT|DELETE
/api/v1/retirement/incomes/{id}`.

## 6. Retirement readiness

### 6.1 Plan settings

One JSON `app_settings` key `retirement_plan`, read and written through
`GET|PUT /api/v1/retirement/plan`:

```json
{
  "birth_date": "1975-03-01",
  "partner_birth_date": null,
  "retirement_age": 65,
  "statutory_age": 67,
  "horizon_age": 95,
  "real_return_pct": 3.0,
  "pessimistic_real_return_pct": 1.0,
  "cash_real_return_pct": -2.0,
  "withdrawal_tax_pct": 0.0,
  "spending_months": 12,
  "spending_includes_loan_payments": true,
  "excluded_categories": ["Income Tax", "Social Security", "Invest", "House purchase"]
}
```

Defaults are those values with `birth_date = null`. Without a birth date
the readiness endpoint returns `status: "needs_setup"` and the UI shows the
settings form first. `horizon_age` is the owner's age; the household is
projected as one unit to that age, with constant spending, whoever is
alive (the owner and partner are the same age here, and a constant
household spend is the conservative reading). `excluded_categories` names
spending categories, by name, matched against each row's category and its
ancestors in the category tree. `real_return_pct` is the simulator's convention: an
expected nominal 5 % on investments minus 2 % inflation. Cash and deposits
earn their stated rate minus inflation; a deposit's rate is known, a cash
line uses `cash_real_return_pct`.

### 6.2 Inputs

- **Pot:** `financial_assets_eur` from the balance sheet, split into
  `investments` (positions and account valuations) and `cash` (cash group,
  deposits at their own rate until maturity, then cash).
- **Debt:** each loan's schedule (balance and monthly payment per year).
  Net worth on the chart is pot minus loan balance; the pot itself is never
  reduced by the loan balance, because the loan is paid from spending.
- **Spending target:** the average monthly outflow over the last
  `spending_months` complete months that have bank imports (the window
  rule of `savings_and_buffer`: the month in progress and months with
  nothing imported never count), over non-transfer spend rows **minus the
  rows in `excluded_categories`**. The defaults drop what stops or is not
  spending at retirement: income tax and the self-employed social-security
  contribution (the screen is before tax), investment contributions that
  were categorised as spending rather than transfers, and the one-off
  house purchase. Municipal taxes, insurance and the mortgage stay in.
  `savings_and_buffer` gains an `exclude` parameter so Progress and
  readiness share one function; Progress keeps calling it without
  exclusions. The response carries the target's category breakdown so the
  user can see what is in it and adjust the list in the plan settings.
- **Loan payments:** in the owner's data the mortgage debit is a spending
  row (category `Mortgage`), so `spending_includes_loan_payments` defaults
  to `true`: the target already contains the payment and the projection
  subtracts each loan's scheduled monthly payment from the need in the
  years after that loan ends. With `false` the projection adds the
  scheduled payments instead while a loan runs. Either way payments run to
  the schedule's end date, `extra_payment_eur` continues after retirement,
  and there is no lump-sum payoff from the pot.
- **Savings now:** `income − spent` over the same months, where `spent`
  excludes only `Invest` and `House purchase` (taxes are real outflows
  today, so they reduce what is saved), as a yearly contribution in today's
  euros, continued until retirement age. Floor at zero. Income is what the
  bank statements show as income rows; for a freelancer that is gross
  receipts, and the taxes paid out of them are in `spent`, so the
  difference is net saving. `Invest` rows count as saving: they are money
  that reached the brokerage, which the pot already holds.
- **Pensions:** `retirement_incomes` as in §5.4, partner ages computed from
  `partner_birth_date` (an income with `owner='partner'` and no partner
  birth date is ignored and flagged in `warnings`).

Fewer than 6 complete imported months → `status: "insufficient_data"` with
the reason; the UI shows what is missing, no verdict.

### 6.3 Projection

Year by year from today's age to `horizon_age`, in real terms, for each
retirement age in `[retirement_age, statutory_age]` and for each return
case in `[base, pessimistic]`:

```
pot_investments *= (1 + real_return)
pot_cash        *= (1 + cash_real_return)      (deposits: own rate − inflation until maturity)
if age < retirement_age:   pot_investments += savings_now
else:
    need = spending_target*12 − pensions_this_year
    if spending_includes_loan_payments:
        need −= scheduled payments this year of loans that have already ended
    else:
        need += scheduled payments this year of loans still running
    need = max(need, 0)
    withdrawal = need / (1 − withdrawal_tax_pct/100)
    draw from pot_cash first (down to 6 months of spending), then pot_investments
    if pot < withdrawal: pot = 0, record depletion_age
```

The first year is pro-rated by months remaining in the calendar year;
`retirement_age` is reached on the birthday. Deposits roll into cash at
maturity.

### 6.4 Outputs

`GET /api/v1/retirement/readiness` →

```json
{
  "status": "ok",
  "as_of": "2026-10-18",
  "plan": {...},
  "inputs": {
    "age": 51, "financial_assets_eur": 0, "investments_eur": 0, "cash_eur": 0,
    "spending_target_monthly_eur": 0, "spending_months_used": 12,
    "spending_includes_loan_payments": true,
    "savings_now_yearly_eur": 0, "loans": [...], "incomes": [...],
    "stale_lines": 0
  },
  "scenarios": [
    {
      "retirement_age": 65, "case": "base",
      "verdict": "on_track" | "short" | "at_risk",
      "lasts_until_age": 95 | 83,
      "pot_at_retirement_eur": 0,
      "pot_at_horizon_eur": 0,
      "sustainable_monthly_eur": 0,
      "gap_monthly_eur": 0,
      "extra_saving_needed_monthly_eur": 0,
      "path": [{"age": 51, "year": 2026, "investments_eur": 0, "cash_eur": 0,
                "loans_eur": 0, "pensions_eur": 0, "withdrawal_eur": 0}, ...]
    }, ...
  ],
  "warnings": ["No state pension entered: the projection assumes none.", ...]
}
```

- `verdict`: `on_track` when the base case lasts to `horizon_age` with a
  pot at horizon ≥ 0; `short` when it lasts past retirement + 10 years but
  not to the horizon; `at_risk` otherwise.
- `sustainable_monthly_eur`: the constant real monthly spend, pensions included, the pot and pensions can fund from retirement to the horizon, found by
  bisection on the same simulation. `gap_monthly_eur` =
  `spending_target − sustainable` (negative = surplus).
- `extra_saving_needed_monthly_eur`: the extra monthly contribution from
  today until retirement that makes the base case last to the horizon,
  also by bisection; 0 when on track.
- `warnings` always lists: no pension entered, stale lines counted at their
  stale value, partner income ignored, loan payments assumption, the tax
  haircut being 0 ("before tax on withdrawals").

The whole computation lives in `services/retirement.py` as pure functions
over plain dicts (`project(plan, pot, loans, incomes, spending, savings,
today) -> scenario`), unit-tested with invented round figures: a pot that
exactly covers a known need, a pension that covers everything, a loan that
ends mid-retirement, a depletion case, the bisection results.

### 6.5 Goals

The `goals` table is untouched. The Goals page shows, under any goal whose
name contains "retire" (case-insensitive), a line "Readiness: <verdict>,
money lasts until <age>" linking to the Net Worth page. The progress bar
for such goals is hidden, since net worth over a placeholder target is not
progress. The existing `check_goals_off_track` keeps working on the
`_project` maths for non-retirement goals; a new
`check_retirement_readiness(db)` raises one `medium` item when the base
verdict is `short`, `high` when `at_risk`, `low` when `needs_setup`, id
`retirement_readiness`.

## 7. The page

The **Net Worth** page is reorganised; no new sidebar entry.

1. **Balance sheet** (top): a table grouped as in §4.1, one row per line
   with amount, source badge, "as of" and a stale marker; group subtotals;
   totals row with assets, liabilities, net worth, and a second line
   "financial assets (excluding property)". A stale row is amber with its
   note on hover and the line's edit control (manual line form, loan form,
   valuation refresh button, or a link to the import page for a bank
   account). The old summary cards (brokerage, bank, manual, deposits, net
   worth) are removed; their numbers are all in the sheet.
2. **Retirement** (middle): a card with the verdict for 65 and 67 side by
   side (base case, pessimistic in small text), the spending target and
   where it came from, the pensions assumed, "money lasts until age N" and
   either "surplus of X/month" or "short by X/month; save Y/month more". One
   SVG line chart (shared chart helpers) of the base path: investments +
   cash stacked, loan balance as a dashed line, a vertical marker at each
   retirement age. A gear icon opens the plan settings form; a second
   section lists retirement incomes with add/edit/delete. `needs_setup` and
   `insufficient_data` render the form and the missing-data reason in place
   of the verdict.
3. **Manage** (bottom): the existing manual lines, deposits and cashflow
   editors, plus loans and account valuations. Manual lines gain an "as of"
   date field defaulting to today.

Help text: new entries in `METRIC_HELP` for source badges, stale, financial
assets, sustainable spend, extra saving needed. Check at 390 px: the sheet
collapses the source and as-of columns into a second line under the label.

## 8. MCP and digests

- `networth` tool: adds the balance-sheet totals and a count of stale lines
  to its output, keeps its existing fields.
- New read-only tool `retirement_readiness` returning the verdicts, lasts-
  until ages, sustainable spend, gap and warnings for both retirement ages;
  registered in the gateway, the finance adapter and the persona.
- Follow-up outside this design: the Finance Weekly Review prints the
  verdict line; `financial-situation.md` records how the readiness screen
  is sourced.

## 9. Migration and rollout

- v35: `manual_assets.as_of` (TEXT, backfilled from `updated_at`), tables
  `loans`, `account_valuations`, `retirement_incomes`. Guarded `ALTER` and
  `CREATE TABLE IF NOT EXISTS`, same pattern as v34.
- Back up `portfolio.db` before the live backend reloads (it migrates on
  reload).
- After deployment the owner: adds the mortgage as a loan and deletes the
  typed liability line; links the Indexa portfolio to its account
  reference and sets `PORTF_INDEXA_TOKEN`; decides per brokerage portfolio
  between ledger cash and a synthetic cash asset; enters birth date,
  the Seguridad Social estimate and, if wanted, partner details; dates the
  home line. None of this is in the repository.
- Docs: `docs/features/networth-portfolios.md` gains sections for the
  balance sheet, loans, valuations and retirement; `CLAUDE.md` gets the v35
  line and two gotchas (the valuation replaces the positions line; ledger
  cash excludes cash-type assets); `PROJECT_STATUS.md` bumped.

## 10. Decided by the owner

- Target: same financial level as now, from age 65; show 67 too.
- State income: the Spanish pension from his contribution years, as a
  figure he enters; Dutch AOW nothing or minimal, zero by default.
- The home is kept, never sold or downsized; it counts in net worth and not
  in the retirement pot.
- Crypto on the hardware wallet was bought on Coinbase and sent over, so
  the Coinbase portfolio already holds it; no extra line.

## 11. Calls made in this design (not the owner's)

- Loans as a schedule rather than a typed balance; rate changes applied
  going forward from the current balance.
- Indexa valued from its API, replacing the positions line when fresh.
- Broker cash from pfm's ledger; synthetic cash assets excluded from it.
- Freshness limits per source (4/7/45/60/90/180/365 days).
- Real-terms projection with 3 % base and 1 % pessimistic real return,
  cash at −2 %, horizon age 95, cash drawn first, withdrawal tax 0 and
  labelled "before tax".
- Savings continue at the current measured rate until retirement.
- The spending target leaves out income tax, the self-employed
  social-security contribution, investment contributions and the house
  purchase; municipal taxes and the mortgage stay in. The mortgage payment
  drops out of the need once the loan ends.
- Pensions are entered gross; every figure on the screen is before tax.
- Partner modelled only as optional income lines plus a birth date; the
  household is one unit with constant spending to the owner's horizon age.
- Manual-line categories map to groups as in §4.1; `other` lines are in
  net worth but not in the retirement pot.
- The page is the existing Net Worth page, not a new one; Goals links to it
  instead of duplicating the verdict.
- Verdict thresholds: `short` = lasts past retirement + 10 years.

## 12. Left to the builder

- Exact table layout and badge styling, within the Bootstrap conventions of
  the page.
- How the positions line is split per portfolio (`compute_positions` is
  keyed by asset; portfolio attribution follows the transactions).
- Bisection tolerances and iteration caps for the sustainable spend and
  extra-saving numbers.
- Whether the Wealth Simulator prefill reads loans directly or through the
  balance sheet.
- Order of the Action Item checks and their copy.
