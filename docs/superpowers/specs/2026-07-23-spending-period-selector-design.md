# Spending time-frame selector + Dashboard spending summary

**Date:** 2026-07-23
**Status:** Approved

## Problem

The Spending page's Spent/Income/Transferred summary cards (and the
Categories tab's chart, which shares the same underlying data) are hardcoded
to a 30-day window — there's no way to see a shorter or longer period. The
Dashboard also has no visibility into these figures at all; the existing
"Top Spending Categories" card only shows the category breakdown, not the
overall Spent/Income/Transferred totals.

## Scope

- Add a period selector (7/30/90/365 days) to the Spending page, replacing
  the hardcoded 30-day window for both the three summary cards and the
  Categories tab's chart (they already share one underlying API call).
- Merge a compact Spent/Income/Transferred stat row into the Dashboard's
  existing "Top Spending Categories" card, renamed **"Spending"**, with its
  own inline period selector kept in sync with the Spending page's choice.
- **Not doing**: no backend changes (`GET /api/v1/spending/summary?days=N`
  already accepts the `days` param). The Net Worth page's "Actual (last 30
  days)" comparison widget (`pfm_analytics.js:535`) is untouched — separate
  feature, stays fixed at 30 days.

## Data flow

No new endpoints. One new piece of shared client-side state:

- `localStorage['pfmSpendingSummaryDays']` — a plain integer string
  (`'7'|'30'|'90'|'365'`), default `'30'` if unset. Same lightweight
  single-value pattern already used for `pfmDiagTab`/`pfmForecastConfig`.
- `getSpendingPeriodDays()` / `setSpendingPeriodDays(days)` — module-scope
  helpers in `pfm_features.js`, read/write that key. Both the Spending page
  and the Dashboard card call these, so a change on either page is reflected
  on the other the next time it loads.

## Spending page

- New `<select id="spSummaryPeriod">` (options: 7/30/90/365 → "Last 7/30/90/365
  days") placed next to the existing Spent/Income/Transferred cards.
- On change: `setSpendingPeriodDays(value)`, then re-run `_refreshSpendingData()`.
- `_refreshSpendingData()`'s call `window.apiClient.getSpendingSummary(30)`
  becomes `window.apiClient.getSpendingSummary(getSpendingPeriodDays())`. Since
  that one summary object already feeds both the three stat cards
  (`spSpent`/`spIncome`/`spTransferred`) and the Categories tab's chart
  (`_renderSpendingCategoryChart(summary.by_category_eur)`), both update
  together automatically from one fetch — no separate wiring needed for the
  chart.
- Card labels (`"Spent (30d)"`, `"Income (30d)"`, `"Moved to other accounts
  (30d)"`) become dynamic, driven by the selected days value (e.g. `"Spent
  (90d)"`, `"Spent (365d)"` — no special-casing to "1y", kept as a plain day
  count for consistency and simplicity).
- On page load, the select's initial value is set from `getSpendingPeriodDays()`
  before the first `_refreshSpendingData()` call, so a returning user sees
  their last-picked period immediately, not a flash of the 30-day default.

## Dashboard

- The existing "Top Spending Categories" card (`#dashTopCategoriesArea`,
  shipped in the prior Dashboard-widgets feature) is renamed **"Spending"**
  and gains:
  - A compact 3-stat row (Spent / Income / Transferred) above the existing
    category bars, using the same `Fmt.amt()`/`esc()` conventions as the rest
    of the Dashboard.
  - A small inline period `<select>` in the card header, visually matching
    the existing `dashReturnPeriod` inline-select already used on the
    Dashboard's Return KPI card (compact, borderless, `form-select-sm`).
- `loadDashboardTopCategories()` is renamed `loadDashboardSpending()` and
  becomes the single function driving both the new stat row and the existing
  category bars, from one `getSpendingSummary(getSpendingPeriodDays())` call
  — replacing the two separate hardcoded-30 call sites (`pfm_features.js`
  lines ~4546 and ~4696 as they stood before this change).
- Changing the Dashboard's inline selector calls `setSpendingPeriodDays(...)`
  too, then re-runs `loadDashboardSpending()` — so switching it there also
  updates what the Spending page shows next time it's opened.
- This card keeps its existing independent, non-blocking load pattern (own
  try/catch, fire-and-forget from `loadDashboardPage()`) — unchanged from
  the prior feature.

## Edge cases

- A corrupted or missing `pfmSpendingSummaryDays` value falls back to `'30'`
  (matching today's hardcoded default) — mirrors the existing
  `loadForecastConfig()`/`saveForecastConfig()` try/catch-and-fall-back-to-default
  pattern already used for the Wealth Simulator's persisted config.
- The Dashboard's period selector and the Spending page's period selector are
  two independent DOM elements reading/writing the same localStorage key —
  they are not required to be open simultaneously and don't need any
  cross-tab live-sync mechanism (e.g. a `storage` event listener); each just
  reads the shared value at its own load time, consistent with how
  `pfmForecastConfig` already works between the Dashboard's Wealth Simulator
  preview and the full Forecast page.
- Empty-category / no-spending-imported-yet states for the Dashboard card
  are unaffected by this change — the existing empty-state message in
  `renderDashboardTopCategories`/`renderDashboardSpending` still applies when
  `by_category_eur` is empty for the selected period, independent of whether
  the new stat row above it has zero values too.

## Testing

`getSpendingPeriodDays()`/`setSpendingPeriodDays()` are pure (no DOM), so unit
tests are added via the existing `loadAppIntoContext()` VM-harness pattern in
`web_client/js/tests/web_client.test.mjs` (same approach used for
`saveForecastConfig`/`loadForecastConfig` — though note those did NOT get unit
tests in the prior feature; this feature's helpers should, since they're
simpler and equally cheap to cover): default-when-unset, round-trip
save/load, and fallback-to-default on corrupted JSON/localStorage failure.
No backend tests needed (no API change). Manually verify in-browser: switching
period on the Spending page updates all three stat cards and the Categories
chart together; switching period on the Dashboard's Spending card updates its
own stat row and category bars; a period picked on one page is reflected as
the pre-selected value the next time the other page loads.

## Documentation

Update `PROJECT_STATUS.md` (new version entry) and `CLAUDE.md`'s Web Client /
Spending Tracking sections (note the new period selector, the shared
`pfmSpendingSummaryDays` persistence, and the Dashboard "Spending" card's
merged stat row + selector, renaming from "Top Spending Categories").
