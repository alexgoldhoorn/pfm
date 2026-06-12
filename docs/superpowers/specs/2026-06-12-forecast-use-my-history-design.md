# Forecast — "Use my history" return/volatility prefill

**Date:** 2026-06-12
**Status:** Approved design (pending spec review)
**Scope:** Forecast (wealth simulator) page. Client-side; both endpoints already
exist.

## Context & goal

The forecast projects the Stocks bucket with a **manual expected return**
(`fcStocksRate`, default 8%) and a **hardcoded 16% volatility** (`VOLATILITY.stocks`,
shown as a static "Volatility: 16%" label). The app already computes the user's
real figures: `GET /analytics/performance` → `money_weighted_irr_pct`
(annualised return) and `GET /analytics/risk` → `volatility_pct` (+ `snapshots_used`).
Goal: let the user prefill the stock return and volatility from their own history.

## Changes

### 1. Make stock volatility editable — `web_client/index.html`
Replace the static label `<span>Volatility: 16% — high risk</span>` with a small
number input:
```html
<label class="form-label small mb-1">Volatility %</label>
<input type="number" class="form-control form-control-sm" id="fcStocksVol" value="16" min="0" max="100" step="1">
```
Also widen the existing return input's max so a high historical IRR fits:
`#fcStocksRate` `max="20"` → `max="40"`.

### 2. Use the input in the projection — `web_client/js/pfm_features.js`
`runProjection(...)` currently does
`projectAccount(stocksAmt, stocksRate, VOLATILITY.stocks, years, sigma)`. Add a
`stocksVol` parameter (a fraction) and pass it instead of `VOLATILITY.stocks`.
`runForecast()` reads it: `const stocksVol = (parseFloat(stocksVolInput.value) || 16) / 100;`
and passes it through. Cash/bonds keep their fixed `VOLATILITY` assumptions.

### 3. Pure helper — `historyToForecast(perf, risk)` (DOM-free, unit-tested)
```js
function historyToForecast(perf, risk) {
    const snaps = (risk && risk.snapshots_used) || 0;
    if (snaps < 3) return { ok: false, reason: 'Not enough history yet — need a few more daily snapshots.' };
    const rate = perf && typeof perf.money_weighted_irr_pct === 'number' ? perf.money_weighted_irr_pct : null;
    const vol = risk && typeof risk.volatility_pct === 'number' ? risk.volatility_pct : null;
    if (rate == null || vol == null) return { ok: false, reason: 'Return/volatility unavailable.' };
    return { ok: true, rate, vol, snapshots: snaps };
}
window.historyToForecast = historyToForecast;
```

### 4. "Use my history" button + wiring
- Button `#fcUseHistory` ("Use my history") next to the stock return/volatility
  inputs, plus a note `#fcHistoryNote`.
- On click: `Promise.all([getPerformance(null,'all'), getRisk()])` →
  `historyToForecast(perf, risk)`. If `ok`: set `fcStocksRate = rate.toFixed(1)`,
  `fcStocksVol = Math.round(vol)`, then re-run the existing volatility label /
  badge updates as needed; note: *"Set from your history: return X%/yr
  (money-weighted IRR), volatility Y% — based on N daily snapshots. Note: this is
  your whole-portfolio figure (incl. crypto), a proxy for the stocks bucket."* If
  not `ok`: show `reason`, change nothing.
- Opt-in: only runs on click; values stay editable.

## Data flow
Reuses `getPerformance()` / `getRisk()` (already in `pfm_core.js`). No backend or
persistence change.

## Testing
- **Web (`node --test`):** unit-test `historyToForecast` — `ok` with rate/vol from
  the fields; `< 3` snapshots → `{ok:false}` with the history reason; missing
  fields → `{ok:false}`.
- Bump `pfm_*.js` `?v=`; rebuild web; curly-quote guard + load test stay green.
- Manual smoke: open Forecast → click **Use my history** → return ≈ 24.7%,
  volatility ≈ 50%, note shows snapshot count; run the projection and confirm the
  cone widens vs the 16% default.

## Out of scope
- Equity-only (vs whole-portfolio) volatility/return.
- Per-bucket history (only the Stocks bucket is prefilled).
- Changing the default model when history is absent (defaults stay 8% / 16%).
- Backend changes.
