# Net Worth — grouped types + clarity

**Date:** 2026-06-11
**Status:** Approved design (pending spec review)
**Scope:** The Net Worth page's manual asset/liability form + items table.
Client-side only (no backend/migration).

## Context & goal

The Net Worth page lets the user add off-brokerage assets/liabilities. Today the
form has a flat Type dropdown (`nwCategory`) plus a separate "It's a debt"
checkbox (`nwIsLiability`) — redundant and error-prone. The `category` column is
free-text (`manual_assets.category TEXT DEFAULT 'other'`) and `is_liability` is
sent by the client, so this is a pure front-end change.

Goal: clearer, grouped type choices that imply the asset/liability split, and
remove the redundant checkbox.

## Changes

### 1. Form markup — `web_client/index.html` (`#nwAddForm`)

Replace the flat `<select id="nwCategory">` with two `<optgroup>`s:

- **Assets**: `savings_account` (Savings account), `current_account`
  (Current / running account), `cash` (Cash), `property` (Property),
  `vehicle` (Vehicle), `pension` (Pension), `investment_external`
  (Investment (external)), `other` (Other asset)
- **Liabilities**: `mortgage` (Mortgage), `personal_loan` (Personal loan),
  `car_loan` (Car loan), `credit_card` (Credit card), `other_debt` (Other debt)

Remove the "It's a debt" checkbox block (`#nwIsLiability`). The Type field
column widens (e.g. `col-6` → `col-12`) to use the freed row space.

### 2. Form JS — `web_client/js/pfm_analytics.js` (`_wireNetworthForm`)

- Define `liabilityCats = new Set(['mortgage','personal_loan','car_loan','credit_card','other_debt'])`.
- On submit, set `is_liability: liabilityCats.has(category)` (derived from the
  selected type) instead of reading the removed checkbox.
- Remove the `nwCategory` change-listener that auto-ticked the checkbox, and all
  `nwIsLiability` references.

### 3. Labels — `NW_CATEGORY_LABELS` (`pfm_analytics.js`)

Extend the map with the new values AND keep the legacy ones so existing rows
still render:
```js
const NW_CATEGORY_LABELS = {
    savings_account: 'Savings account', current_account: 'Current account',
    cash: 'Cash', property: 'Property', vehicle: 'Vehicle', pension: 'Pension',
    investment_external: 'Investment (external)', other: 'Other asset',
    mortgage: 'Mortgage', personal_loan: 'Personal loan', car_loan: 'Car loan',
    credit_card: 'Credit card', other_debt: 'Other debt',
    // legacy values from earlier entries:
    loan: 'Loan', credit: 'Credit / debt',
};
```
The render already falls back to `it.category` for any unmapped value.

### 4. Clarity touch — items table ordering (`loadNetworthPage`)

Sort the items so **assets come first, then liabilities** (stable within each):
`items.slice().sort((a, b) => (a.is_liability ? 1 : 0) - (b.is_liability ? 1 : 0))`.
This groups the list visually; the summary tiles and the red liability badge are
unchanged.

## Data / compatibility
- No backend change. New category strings are stored as-is.
- Existing manual assets keep their old `category` (`cash`/`loan`/`credit`) and
  render via the legacy label entries; their `is_liability` flag is untouched.

## Testing
- Bump the `pfm_*.js` `?v=` cache-buster; rebuild the web container.
- JS load/smoke + curly-quote guard tests must stay green (`node --test`).
- Manual smoke: add a savings account (asset, positive), add a mortgage
  (auto-counts as liability/red, subtracts from net worth), confirm the items
  table lists assets before liabilities, and an existing old-category item still
  shows a sensible label.

## Out of scope
- Server-side category validation / enum.
- Editing existing items' type (only add/delete exist today).
- Per-type icons, multi-currency rework, or net-worth history changes.
