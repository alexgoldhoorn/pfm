# Merge Assets + Holdings into one "Assets" page

**Date:** 2026-07-17
**Status:** Approved

## Problem

The web client has two separate nav pages that both list instruments:
"Holdings" (portfolio-scoped positions with quantity/cost/P&L) and "Assets"
(the full instrument catalog with management actions like manual price
override and ticker resolution). The split is unclear to Alex day-to-day —
it's not obvious which page to check for a given ticker, and an asset that's
never been bought only shows up on Assets, silently disappearing from the
Holdings view. Combine them into one page.

## Scope

- Merge `#holdingsPage` and `#assetsPage` into a single "Assets" page and nav
  entry in `web_client/`.
- Default view = owned rows only (today's Holdings behavior: portfolio-scoped,
  quantity/avg price/current price/value/P&L, summary cards, hide-tiny
  threshold). A new checkbox toggle appends catalog assets with **zero
  holdings in any portfolio** ("held anywhere" check, not just the selected
  one).
- Carry over Assets-page-only features onto every row: Exchange column,
  manual price override (pencil icon), bulk "Resolve Tickers" (OpenFIGI)
  button.
- **Not doing**: no backend/API changes — this is a frontend-only merge.
  `getAssets()`, `getHoldings(portfolio_id)` stay as-is.

## Data flow

Client-side merge, no new endpoints:

- `getAssets()` — full catalog (symbol, name, type, exchange, currency, price)
- `getHoldings(selectedPortfolioId)` — owned rows for the selected portfolio
  (or aggregate, if "all portfolios" is selected), unchanged from today
- `getHoldings()` with no portfolio filter, fetched once per page load and
  cached — used only to build the "held anywhere" symbol set. This determines
  which catalog assets are eligible to append when the toggle is on. An asset
  held in a *different*, non-selected portfolio is excluded either way (not
  shown as a position for the selected portfolio, and not eligible as "zero
  holding" since it's held somewhere) — this matches today's Holdings
  portfolio-filter behavior; nothing new to reconcile.

## UI

- **Nav**: single "Assets" sidebar entry (`data-page="assets"`) replaces both
  "Holdings" and "Assets" entries. `#holdingsPage` markup (summary cards,
  portfolio filter, hide-tiny threshold input, table) becomes the base
  container; Assets-page-only controls (Resolve Tickers button, type filter)
  are folded into the same filter bar. `#assetsPage` markup is removed.
- **Columns**: symbol, name, type, exchange, currency, quantity, avg price,
  current price (pencil-icon manual override), value, P&L €, P&L %, links,
  actions (research button). Rows with no holding show `—` for
  quantity/avg price/value/P&L €/P&L %.
- **Toggle**: "Show assets with no holding" checkbox in the filter bar,
  **off** by default.
- **Summary cards** (total value/cost/P&L): computed from owned rows for the
  selected portfolio only — unaffected by the toggle.
- **Hide-tiny-position threshold** (`PREFS.hideBelowEur`): applies only to
  owned rows. Unowned rows are exempt — they're catalog entries, not
  positions, so a €0 filter shouldn't hide them.
- **Filters**: one type filter, one portfolio filter, one search box
  (symbol/name/exchange substring match, replacing the two pages' separate
  filter bars).
- **Resolve Tickers** and manual price edit apply to every row regardless of
  ownership, same as today's Assets page.

## Edge cases

- All-portfolios view + toggle on: the "held anywhere" set is the same data
  already fetched for that view — no extra API call needed.
- Sort/filter prefs: reuse the existing `PREFS.tableState.holdings` key so
  Alex's saved sort/filter state from the old Holdings page carries over
  unchanged. The old `PREFS.tableState.assets` key becomes dead but is left
  alone (harmless orphan, consistent with how other deprecated pref keys are
  handled in this codebase).
- Dashboard, Research "Chat about this", Rebalance, and other callers of
  `getHoldings()`/`getAssets()` are untouched — only this page's own
  aggregation logic changes.

## Testing

No existing JS unit tests cover page-render functions directly (they're
DOM-heavy, consistent with the rest of `pfm_pages.js`). No backend tests
needed since there's no API change. Manually verify in-browser: toggle
on/off, portfolio switch, hide-tiny threshold, manual price edit, and Resolve
Tickers all still work on the merged page.

## Documentation

Update `PROJECT_STATUS.md` (new version entry) and `CLAUDE.md`'s Web Client
section (remove the separate Holdings/Assets page references, note the
merged page and toggle behavior).
