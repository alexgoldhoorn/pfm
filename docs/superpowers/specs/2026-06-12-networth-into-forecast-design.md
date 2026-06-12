# Load Net Worth into the wealth simulator

**Date:** 2026-06-12
**Status:** Approved design (pending spec review)
**Scope:** The Forecast (wealth simulator) page only. Client-side; no backend
change.

## Context & goal

The forecast page (`setupForecastPage` in `web_client/js/pfm_features.js`) lets
the user project wealth: it auto-fills **Stocks** from tracked holdings, and the
user types **Cash**, **Bonds**, and a **Mortgage** by hand. The Net Worth page
already holds those off-brokerage figures. Goal: an opt-in **"Load from Net
Worth"** button that pre-fills Cash / Bonds / Mortgage from `getNetworth()`,
mapping only the categories the simulator models and noting the rest.

## Mapping (decided)

From `getNetworth()` `items` (each has `category`, `is_liability`, `amount_eur`):

| Forecast field (input id) | Net Worth categories summed |
|---|---|
| **Cash** (`fcCashAmount`) | `savings_account` + `current_account` + `cash` |
| **Bonds** (`fcBondsAmount`) | `investment_external` |
| **Mortgage principal** (`fcMortgagePrincipal`) | liability `mortgage` (summed) |

- **Stocks** (`fcStocksAmount`) is **left untouched** — it auto-loads from
  tracked holdings; manual assets are separate.
- **Skipped (noted, not loaded):** non-mortgage assets (`property`, `vehicle`,
  `pension`, `other`) and non-mortgage liabilities (`personal_loan`, `car_loan`,
  `credit_card`, `other_debt`, legacy `loan`/`credit`). The simulator has no
  field for them and they have no market volatility.
- The growth-rate and volatility inputs are **not** touched (the model uses a
  manual expected return + fixed volatility assumption; out of scope here).

## Architecture

### Pure mapper — `pfm_features.js` (DOM-free, unit-tested)
```js
function mapNetworthToForecast(items) {
    const CASH = new Set(['savings_account', 'current_account', 'cash']);
    const BONDS = new Set(['investment_external']);
    let cash = 0, bonds = 0, mortgage = 0;
    const skipped = [];
    for (const it of (items || [])) {
        const eur = parseFloat(it.amount_eur != null ? it.amount_eur : it.amount) || 0;
        if (it.is_liability) {
            if (it.category === 'mortgage') mortgage += eur;
            else skipped.push(it.name || it.category);
        } else if (CASH.has(it.category)) cash += eur;
        else if (BONDS.has(it.category)) bonds += eur;
        else skipped.push(it.name || it.category);
    }
    return { cash, bonds, mortgage, skipped };
}
window.mapNetworthToForecast = mapNetworthToForecast;
```

### Button + wiring (thin DOM)
- New button `#fcLoadNetworth` ("Load from Net Worth") near the starting-value
  inputs (next to the existing Stocks refresh control), plus a status line
  `#fcNetworthNote`.
- On click: `const d = await window.apiClient.getNetworth();`
  `const m = mapNetworthToForecast(d.items || []);`
  set `fcCashAmount = Math.round(m.cash)`, `fcBondsAmount = Math.round(m.bonds)`,
  `fcMortgagePrincipal = Math.round(m.mortgage)`; then call the existing
  `updateTotalLiquidBadge()` and `updateMortgageNote()` so badges/notes refresh.
- Status note: `Loaded Cash €X · Bonds €Y · Mortgage €Z from Net Worth.` plus,
  if `m.skipped.length`, ` Skipped (not modeled): a, b, c.` On error, show the
  message and change nothing.
- Opt-in: nothing happens unless the user clicks; the button never runs on page
  load, and the user can edit the fields afterward.

## Data flow / compatibility
- Reads the existing `GET /networth` (no change). Stocks remains driven by
  `getHoldings()`. No persistence needed (forecast inputs aren't persisted).

## Testing
- **Web (`node --test`):** unit-test `mapNetworthToForecast` — cash/bonds sums,
  mortgage from the `mortgage` liability only, non-mortgage assets+liabilities
  go to `skipped`, empty/undefined input → zeros + empty skipped, `amount_eur`
  preferred over `amount`.
- Bump the `pfm_*.js` `?v=` cache-buster; rebuild web.
- Manual smoke: add a savings account + a mortgage on Net Worth, open Forecast,
  click **Load from Net Worth** → Cash and Mortgage fields fill, the total/
  mortgage notes update, and the skipped note lists property/pension if present.

## Out of scope
- Auto-loading on page open (button is the explicit choice).
- Modeling property/vehicle/pension or non-mortgage debts.
- Using historical return/volatility from snapshots (possible later follow-up).
- Backend changes.
