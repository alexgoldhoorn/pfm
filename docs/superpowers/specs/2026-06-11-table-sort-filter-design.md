# Coherent table sort/filter across all tables

**Date:** 2026-06-11
**Status:** Approved design (pending spec review)
**Scope:** A shared, consistent sort + filter mechanism for the four main data
tables — Holdings, Transactions, Assets, Brokers — driven by clickable column
headers and a filter row, with per-table state persisted in PREFS.

## Context & goal

Today each table behaves differently: Holdings sorts by a single global
`PREFS.holdingsSort` (4 modes, no column clicking); Transactions has a server
broker filter + fixed date-DESC order; Assets and Brokers have no sort/filter at
all. The goal is one coherent interaction everywhere — **click a column header to
sort (asc/desc toggle + arrow indicator), and a compact filter row of dropdowns
for categorical columns** — backed by a single reusable helper so the behavior
and look are identical across pages.

All four tables already load their full data client-side (Holdings/Assets/Brokers
are complete lists; Transactions loads up to 500), so sort/filter is client-side.

## Architecture

One shared helper in `web_client/js/pfm_core.js`, split into a **pure core** and a
**thin DOM wrapper** — generalizing the `topPositions()` pattern.

### Pure core (DOM-free, unit-tested)
```
applyTableState(rows, columns, state) -> filtered+sorted rows
```
- `columns`: array of `{ key, label, type, sortable, filter, align }` where
  `type ∈ {'text','num','date'}`, `filter ∈ {'select', null}`.
- `state`: `{ sort: {key, dir: 'asc'|'desc'}, filters: { <key>: <value|'all'> } }`.
- Filtering: for each active `filters[key]` not `'all'`, keep rows whose
  `String(row[key])` equals the selected value.
- Sorting: by `state.sort.key`'s column `type` — `num` via numeric compare,
  `date` via ISO/string compare, `text` via `localeCompare` (case-insensitive);
  reversed for `dir==='desc'`. Missing/blank values sort last.
- Returns a new array (no mutation).

### DOM wrapper
```
makeSortableTable({ table, columns, getRows, renderRow, prefsKey, onState }) 
```
- `table`: the `<table>` element (has `<thead>`/`<tbody>`).
- `getRows()`: returns the current data array (so the page can refetch and
  re-render).
- `renderRow(row)`: returns the `<tr>...</tr>` HTML for one row (existing
  per-page row markup, reused verbatim — untrusted fields already use `esc()`).
- `prefsKey`: namespace under `PREFS.tableState` (e.g. `'holdings'`).
- Behavior: builds clickable `<thead>` headers (sortable ones wrapped in a
  button showing ▲/▼ on the active column), a filter `<select>` row above the
  body (options = distinct present values of each `filter:'select'` column +
  "All"), wires `click`/`change` handlers (idempotent via a `_bound` flag),
  reads/writes `PREFS.tableState[prefsKey]`, and on any change calls
  `applyTableState(getRows(), columns, state)` then fills `<tbody>` via
  `renderRow`. Re-applies persisted state on each page load.

State shape in PREFS:
```js
PREFS.tableState = {
  holdings:     { sort: {key, dir}, filters: {asset_type: 'all'} },
  transactions: { sort: {key, dir}, filters: {asset_type:'all', transaction_type:'all'} },
  assets:       { sort: {key, dir}, filters: {asset_type:'all'} },
  portfolios:   { sort: {key, dir}, filters: {} },
}
```
Added to `PREFS_DEFAULTS` in `pfm_core.js`.

## Per-table configuration

| Table | Sortable columns | Filters |
|---|---|---|
| **Holdings** (`loadHoldingsPage`) | symbol, name, asset_type, quantity, avg_price, current_price, total_value_eur, pnl_amount, pnl_pct | asset_type |
| **Transactions** (`loadTransactionsPage`) | date, symbol, transaction_type, quantity, price, total, fees | asset_type, transaction_type |
| **Assets** (`loadAssetsPage`) | symbol, name, asset_type, exchange, current_price, currency | asset_type |
| **Brokers** (`loadPortfoliosPage`) | name, value_eur, cost_eur, pnl_eur, first/last dates | — |

Non-data columns (Actions, Links, Research) are not sortable and have no header
button.

**Transactions broker filter stays server-side:** the existing `txPortfolioFilter`
dropdown keeps refetching via `getTransactions(500, portfolioId)` (it can exceed
the loaded window); its result feeds `getRows()` for the helper. The new
client-side asset-type/tx-type filters and column sort operate on that loaded set.
This is the one intentional asymmetry, documented so it isn't "fixed" later.

## Migration of existing behavior
- Holdings: the global `PREFS.holdingsSort` Settings control becomes redundant
  (header-click sort supersedes it). Keep the Settings control as the **initial
  default** seed for `tableState.holdings.sort` on first use, but the table is
  now interactively sortable. (Don't remove the Settings control in this change —
  out of scope; note it.)
- Transactions default sort stays date-DESC (seed `tableState.transactions`).

## Testing

**Web (`node --test web_client/js/tests/`):** unit-test `applyTableState`:
- text sort asc/desc (case-insensitive), number sort, date sort.
- a `'select'` filter keeps only matching rows; `'all'` passes everything.
- combined filter + sort; blanks/missing values sort last.
- (DOM wrapper stays thin; covered by the existing load/smoke test which
  asserts the new globals exist.)

**Manual smoke:** rebuild web; on each page click headers (arrow flips, order
changes), use each filter dropdown, reload and confirm sort/filter persisted.

## Out of scope (deferred)
- Refactoring the dashboard **Top Positions** to use this helper (it has a
  specialized 5-mode P/L sort; leave it to avoid regression — future
  consolidation).
- Watchlist / rebalance / other tables.
- Removing the holdings-sort Settings control.
- Multi-column sort, free-text search, server-side sort/pagination.
