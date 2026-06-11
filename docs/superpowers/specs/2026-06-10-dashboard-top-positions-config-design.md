# Dashboard "Top Positions" — configurable filter & sort

**Date:** 2026-06-10
**Status:** Approved design (pending spec review)
**Scope:** The Top Positions card on the dashboard only. Broader "configurable
dashboard / reorderable widgets" is explicitly out of scope and deferred.

## Context & goal

The dashboard's **Top Positions** card is currently hardcoded to the **top 5
positions sorted by EUR value** (`loadDashboardPage` in
`web_client/js/pfm_pages.js`, the `#dashTopPositionsTable` block). Users want to
control what it shows:

- **N**: how many positions (Top 5 / 10 / 15 / 20 / All)
- **Asset type** filter (All / stock / etf / index / crypto / bond / p2p)
- **Broker** filter (All / a specific portfolio)
- **Sort** by: Value, Gain %, Loss %, Gain total, Loss total

The chosen config persists so it sticks across reloads.

## Key constraint that shapes the design

`GET /api/v1/portfolios/holdings` returns each asset **aggregated across all
brokers** — positions come from `compute_positions(get_all_transactions())` in
`portf_server/routers/portfolios.py`, keyed by `asset_id` with no portfolio
dimension. So:

- **N, asset-type, and all five sort modes** can be done **client-side** on the
  holdings array the dashboard already fetches — no backend change.
- **Broker filter** is the only piece that needs the server, because the
  per-broker slice of a multi-broker asset isn't in the aggregated response.

## Persistence decision

The app has **no server-side per-user settings store** — `window.PREFS`
(localStorage key `pfmPrefs`) is the user-config mechanism, and the web logs in
with the shared API key (0 users in the DB). So "user config" here means
**`PREFS`**, consistent with theme / holdings-sort / etc. If true server-side
per-user config is wanted later, that's a separate, larger change.

New PREFS key:
```js
dashTopPositions: { n: 5, type: 'all', broker: 'all', sort: 'value' }
```
Added to `PREFS_DEFAULTS` in `pfm_core.js`.

## Approach (recommended)

**Client-side controls on the existing holdings data + one optional server
param for the broker filter.** Rejected alternatives: computing per-broker
positions in JS (duplicates `positions.py`, the server's single source of truth
— divergence risk); a new `/holdings/by-broker` endpoint (more payload/surface
than "filter to one broker" needs).

## UI

A compact, collapsible control row in the Top Positions card header, toggled by
a gear icon (keeps the default view tidy). Bootstrap `<select>` controls,
matching existing dashboard styling:

```
Top Positions                                   [⚙]
──────────────────────────────────────────────────
Show:[Top 10 ▾]  Type:[All ▾]  Broker:[All ▾]  Sort:[Gain % ▾]
──────────────────────────────────────────────────
 NVDA   STOCK    12,400 EUR   +38.2%
 BTC    CRYPTO    9,100 EUR   +22.0%
 ...
```

- **Show**: `5 | 10 | 15 | 20 | all`
- **Type**: `all` + the asset types present in the current holdings (built
  dynamically so empty types don't appear).
- **Broker**: `all` + one option per portfolio from `getPortfolios()`
  (value = `portfolio_id`, label = name).
- **Sort**: `value | gain_pct | loss_pct | gain_total | loss_total`.
- The active sort is reflected in the table's right-hand column header (e.g.
  "Gain %" vs "Gain €") so the number's meaning is unambiguous.

## Data flow

1. `loadDashboardPage()` fetches holdings once (as today) and stores the array
   for the card (e.g. on a closure / `pageManager` field).
2. A new `renderTopPositions()` reads `PREFS.dashTopPositions`, then
   **filters** (type) → **sorts** → **slices** (N) the in-memory holdings and
   re-renders only `#dashTopPositionsTable tbody` + the sort-column header.
3. Control `change` handlers write the new value into
   `PREFS.dashTopPositions`, call `savePrefs()`, then:
   - **Broker changed** → refetch holdings via
     `getHoldings(portfolioId)` (or `getHoldings()` for `all`), update the
     stored array, then `renderTopPositions()`.
   - **Any other control** → `renderTopPositions()` directly (no fetch).
4. The broker `<select>` is populated once per dashboard load from
   `getPortfolios()` (already used elsewhere); failure → just the "All" option.

### Sort semantics (on the filtered list)

| Mode | Key | Order |
|---|---|---|
| Value | `total_value_eur` (fallback `total_value`) | desc |
| Gain % | `pnl_pct` | desc |
| Loss % | `pnl_pct` | asc |
| Gain total | `pnl_amount` | desc |
| Loss total | `pnl_amount` | asc |

"Loss" modes always render N rows even if nothing is negative (least-positive
first); the column header still reads "Loss %/€" so intent is clear.

## Server change

`GET /api/v1/portfolios/holdings` gains an **optional** `portfolio_id` query
param (default `None` = current all-brokers behaviour). When present, the
handler filters transactions to that portfolio **before** `compute_positions`,
so a multi-broker asset shows only that broker's quantity/cost/value.

- `Database.get_all_transactions` already accepts `portfolio_id=`
  (`portf_manager/database.py:1906`), so the handler just forwards the param
  into the existing fetch before `compute_positions` — no new DB code.
- The handler stays a plain `def` (it already is) — blocking FX lookups run in
  the threadpool.

### API client

`getHoldings(portfolioId = null)` in `pfm_core.js` appends
`?portfolio_id=<id>` when provided; signature stays backward-compatible
(existing call sites pass nothing).

## Testing

**Backend (pytest, `tests/unit/`):**
- `holdings?portfolio_id=` returns only that broker's positions; a known
  multi-broker asset shows the per-broker quantity/value, not the aggregate.
- Omitting the param preserves current aggregated output (regression).

**Web (`node --test web_client/js/tests/`):**
- A pure `sortHoldings(list, mode)` / `filterHoldings` helper (extracted so it's
  unit-testable in the existing vm harness) returns the correct order for each
  of the five sort modes and the type filter, including the "no losses" edge
  case. Keep the helper free of DOM access so it loads in the test context.

**Manual smoke:** rebuild web container, exercise each control, confirm the
config survives a reload (PREFS), and that broker filtering changes the rows.

## Out of scope (deferred)

- Reorderable / show-hide dashboard widgets and any whole-dashboard layout
  config.
- Server-side per-user settings.
- Multi-select type/broker (single-select only for now — YAGNI).
