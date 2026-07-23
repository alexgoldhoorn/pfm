# Dashboard: live Wealth Simulator preview + Bank Accounts + Top Spending Categories

**Date:** 2026-07-23
**Status:** Approved

## Problem

The Dashboard's "Wealth Simulator" card is a CTA-only stub (description text +
a button to the full Forecast page) — it doesn't show a projection until you
click through. The Dashboard also has no visibility into bank account
balances or where money is going each month, even though both already exist
elsewhere in the app (Net Worth page, Spending page).

## Scope

- Wealth Simulator card on the Dashboard becomes a live compact projection
  chart instead of a CTA-only stub.
- New "Bank Accounts" card on the Dashboard.
- New "Top Spending Categories" card on the Dashboard.
- **Not doing**: no new backend endpoints. All three cards read data that
  already exists (`getHoldings()`, `getNetworth()`, `getSpendingSummary()`)
  or math that already exists (the Forecast page's projection functions,
  hoisted to be shared rather than duplicated).

## Data flow

No new API endpoints. Three independent, non-blocking loads (same pattern as
the existing `loadDashboardAlerts()` / `loadDataFreshness()` — a failure in
one must not blank out the rest of the dashboard):

- **Wealth Simulator preview**: reuses the holdings total already fetched for
  the KPI cards (no extra call) for the live stocks amount, plus a new
  `pfmForecastConfig` localStorage entry for everything else (see below).
- **Bank Accounts**: `getNetworth().bank_accounts` — same field the Net Worth
  page and Portfolios page already consume.
- **Top Spending Categories**: `getSpendingSummary(30).by_category_eur` — same
  field the Spending page's Categories chart already consumes.

## Wealth Simulator: refactor + persistence

`projectAccount()` and `runProjection()` currently live as private closures
inside `setupForecastPage()` in `pfm_features.js`, so only the Forecast page
can call them. Hoist both to module scope (same treatment already given to
`computeGoalOverlays`, defined just above `setupForecastPage()` and exposed
via `window.computeGoalOverlays`) and expose them the same way. Pure
functions, no DOM access — hoisting changes nothing about their behavior.

New localStorage key `pfmForecastConfig`, written inside `runForecast()`
every time "Run Forecast" is clicked:

```
{ cashAmount, cashRate, stocksRate, stocksVol, stocksContribution,
  bondsAmount, bondsRate, mortgagePrincipal, mortgageRate, monthlyPayment,
  years, confidence }
```

`stocksAmount` is deliberately excluded — it always comes from live holdings,
both on the Forecast page (existing `loadStartValue()` behavior, unchanged)
and on the Dashboard preview.

On Forecast-page load, these fields prefill from `pfmForecastConfig` if
present; otherwise fall back to today's hardcoded HTML defaults (cash=0 /
1.5%, stocks 8.0% / 16% vol, bonds=0 / 4.0%, mortgage=0 / 3.5%, 30 years, 95%
CI) — a first-ever visit looks exactly like it does today.

## Dashboard cards

### Wealth Simulator (replaces existing CTA card, same position: col-md-4)

On dashboard load: read `pfmForecastConfig` (or the defaults above), take the
stocks amount from the already-fetched holdings total, run
`runProjection(...)`, and render a new compact SVG chart — mean net-worth
line + shaded confidence band, minimal axes (start/end value labels only).
No goal overlays, no mortgage-payoff line/badge — those stay exclusive to the
full Forecast page to keep the card glanceable. A "Customize →" link under
the chart opens the full Wealth Simulator (`showPage('forecast')`, same as
today's button).

Empty state (both holdings and saved config are effectively zero — projected
value is 0 throughout): fall back to today's existing CTA text + button
instead of rendering an empty chart.

### Bank Accounts (new card, col-md-6, Row 4)

List each account from `getNetworth().bank_accounts`: name, balance (native
currency), EUR equivalent, as-of date — same row shape as
`_renderBankAccounts()` in `pfm_analytics.js`. Add a total row (sum of
`balance_eur`). Empty state: "No bank accounts yet — add one on the Net Worth
page" (matches the tone of the Dashboard's other empty states, e.g. "No
holdings data yet").

### Top Spending Categories (new card, col-md-6, Row 4)

Top 5 categories from `by_category_eur`, sorted descending, as a compact
horizontal bar list: category name, EUR amount, proportional mini-bar, % of
the top-5 total. No Chart.js — consistent with the Dashboard's existing
hand-rolled SVG donut rather than pulling in the heavier bar/pie widget used
on the Spending page. "View all →" link opens Spending → Categories tab.
Empty state: "No spending imported yet."

## Layout

Row 3 keeps its current split — Wealth Simulator (col-md-4) + Recent
Transactions (col-md-8), unchanged widths. New Row 4: Bank Accounts
(col-md-6) + Top Spending Categories (col-md-6).

## Edge cases

- Dashboard's stocks-amount source (holdings total) and the Forecast page's
  own `loadStartValue()` must stay in agreement — both read
  `getHoldings().summary.total_value`, so no drift.
- A user who has never opened the Forecast page has no `pfmForecastConfig`
  yet — the Dashboard preview uses the same defaults the Forecast page would
  show fresh, so first impressions match.
- Bank Accounts card empty state and Top Spending Categories empty state are
  independent — one being empty must not suppress the other card or the
  Wealth Simulator preview.
- The `dashAlerts` banner (price staleness / watchlist / target alerts)
  is unaffected by this work — stays at the top of the page, above Row 1.

## Testing

`projectAccount()`/`runProjection()` become testable via the existing
`loadAppIntoContext()` VM-harness pattern in
`web_client/js/tests/web_client.test.mjs` (same approach already used for
`computeGoalOverlays`) — add unit tests for both now that they're module-scope.
No backend tests needed (no API change). Manually verify in-browser: Wealth
Simulator card renders a chart matching the full page's numbers for the same
inputs, config persists across a page reload, Bank Accounts and Top Spending
Categories cards render correctly and degrade to their empty states when the
underlying data is empty.

## Documentation

Update `PROJECT_STATUS.md` (new version entry) and `CLAUDE.md`'s Web Client
section (Dashboard bullet gains the three new/changed cards; note the
`pfmForecastConfig` persistence and that `projectAccount`/`runProjection` are
now shared module-scope functions).
