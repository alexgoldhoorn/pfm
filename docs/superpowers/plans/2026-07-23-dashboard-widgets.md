# Dashboard Widgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Dashboard's CTA-only "Wealth Simulator" card with a live compact projection chart, and add two new Dashboard cards — Bank Accounts and Top Spending Categories — all reading data/math that already exists elsewhere in the app.

**Architecture:** Classic-script frontend, no build step (`web_client/js/*.js` concatenated in `index.html` load order: `pfm_core.js`, `pfm_pages.js`, `pfm_analytics.js`, `pfm_features.js`). No new backend endpoints. `projectAccount()`/`runProjection()` get hoisted from a private closure inside `setupForecastPage()` (`pfm_features.js`) to module scope so the Dashboard can call the same math the full Wealth Simulator page uses. A new `pfmForecastConfig` localStorage key persists the simulator's non-stocks inputs across visits. Two new Dashboard-only loader/render function pairs (`loadDashboardBankAccounts`/`renderDashboardBankAccounts` in `pfm_analytics.js`, `loadDashboardTopCategories`/`renderDashboardTopCategories` in `pfm_features.js`) read data already fetched by other pages (`getNetworth()`, `getSpendingSummary()`).

**Tech Stack:** Vanilla JS (no framework), Bootstrap 5 (cards, progress bars), hand-rolled SVG (no Chart.js for the new widgets — consistent with the Dashboard's existing donut chart), Node's built-in test runner (`node --test`) via the `vm`-context harness in `web_client/js/tests/web_client.test.mjs`.

## Global Constraints

- No new backend endpoints or schema changes — frontend-only work.
- `stocksAmount` is never persisted to `pfmForecastConfig` — it always comes from live holdings (`getHoldings().summary.total_value`), both on the Forecast page and the Dashboard preview.
- New dashboard widgets must load independently/non-blocking — a failure in one (Bank Accounts, Top Spending Categories, Wealth Simulator preview) must not blank out the rest of the Dashboard. Follow the existing `loadDashboardAlerts()`/`loadDataFreshness()` pattern (each wrapped in its own try/catch, called fire-and-forget from `loadDashboardPage()`).
- No Chart.js for the two new cards' visuals — use plain Bootstrap markup (table, progress bars) or hand-rolled SVG, matching the Dashboard's existing allocation donut rather than the heavier chart widget used on the Spending page.
- Reuse existing formatting helpers: `Fmt.num()`, `Fmt.amt()` (wraps money in `<span class="pfm-amt">` for the privacy-blur toggle — every money figure in the new cards must go through this), `esc()` for any interpolated user/API-sourced text.
- Spec: `docs/superpowers/specs/2026-07-23-dashboard-widgets-design.md`

---

### Task 1: Hoist `projectAccount`/`runProjection` to module scope + unit tests

**Files:**
- Modify: `web_client/js/pfm_features.js:2150-2346` (insert hoisted functions after `computeGoalOverlays`'s window export, remove the two function definitions from inside `setupForecastPage()`)
- Test: `web_client/js/tests/web_client.test.mjs` (insert after the existing `computeGoalOverlays` tests, ~line 465)

**Interfaces:**
- Produces: `window.projectAccount(startAmount, annualRatePct, volatility, years, sigma, monthlyContribution)` → `Array<{year, mean, high, low}>` (length `years+1`)
- Produces: `window.runProjection(cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate, mortgagePrincipal, mortgageRate, monthlyPayment, years, sigma, stocksVol, stocksMonthlyContribution)` → `{ data: Array<{year, assets, assetsHigh, assetsLow, mortgage, netWorth, netWorthHigh, netWorthLow}>, mortgagePaidOffYear: number|null, totalInterestPaid: number }`
- Consumed by: Task 2 (`saveForecastConfig`/`loadForecastConfig`, unchanged call sites inside `setupForecastPage()`) and Task 3 (`loadDashboardForecastPreview` calls `runProjection` directly).

- [ ] **Step 1: Write the failing tests**

Open `web_client/js/tests/web_client.test.mjs` and insert the following block right after the existing test `"computeGoalOverlays: target year beyond the slider's years is not marked onChartYear"` (which currently ends right before the `mergeActionItems` tests):

```javascript
test("projectAccount/runProjection are exposed at module scope (not trapped in setupForecastPage's closure)", () => {
    const w = loadAppIntoContext();
    assert.equal(typeof w.projectAccount, "function");
    assert.equal(typeof w.runProjection, "function");
});

test("projectAccount: zero rate and zero volatility leaves the mean flat with no band spread", () => {
    const { projectAccount } = loadAppIntoContext();
    const p = projectAccount(1000, 0, 0, 3, 1.96, 0)[3];
    assert.equal(p.year, 3);
    assert.equal(p.mean, 1000);
    assert.equal(p.high, 1000);
    assert.equal(p.low, 1000);
});

test("projectAccount: monthly contribution compounds as an ordinary annuity at zero rate", () => {
    const { projectAccount } = loadAppIntoContext();
    const points = projectAccount(0, 0, 0, 2, 1.96, 100);
    assert.equal(points[1].mean, 1200);
    assert.equal(points[2].mean, 2400);
});

test("runProjection: mortgage paid off in exactly the expected year at 0% interest", () => {
    const { runProjection } = loadAppIntoContext();
    const proj = runProjection(0, 0, 0, 0, 0, 0, 12000, 0, 1000, 5, 1.96, 0, 0);
    assert.equal(proj.mortgagePaidOffYear, 1);
    assert.equal(proj.totalInterestPaid, 0);
});

test("runProjection: mortgage not paid off within the window keeps a running balance", () => {
    const { runProjection } = loadAppIntoContext();
    const proj = runProjection(0, 0, 0, 0, 0, 0, 100000, 0, 100, 5, 1.96, 0, 0);
    assert.equal(proj.mortgagePaidOffYear, null);
    assert.equal(proj.data[5].mortgage, 94000);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test-js` (or directly: `node --test web_client/js/tests/`)
Expected: FAIL — `w.projectAccount` and `w.runProjection` are `undefined` (still trapped inside `setupForecastPage()`'s closure), so `typeof` checks fail and the other three tests throw `TypeError: projectAccount is not a function` / `runProjection is not a function`.

- [ ] **Step 3: Hoist the two functions to module scope**

In `web_client/js/pfm_features.js`, find this exact block (currently inside `setupForecastPage()`, right before the `// Full projection...` comment and `renderChart` function):

```javascript
    // Per-asset GBM projection, with an optional monthly contribution added
    // as an ordinary annuity (annual compounding, deposits at each year-end
    // — the same order of approximation as the rest of this model).
    // Returns array[0..years] of { year, mean, high, low }
    function projectAccount(startAmount, annualRatePct, volatility, years, sigma, monthlyContribution) {
        const r = annualRatePct / 100;
        const contribution = monthlyContribution || 0;
        const points = [];
        for (let i = 0; i <= years; i++) {
            const grown = startAmount * Math.pow(1 + r, i);
            const contributed = contribution <= 0 ? 0
                : r > 0 ? contribution * 12 * ((Math.pow(1 + r, i) - 1) / r)
                : contribution * 12 * i;
            const mean = grown + contributed;
            const totalVol = volatility * Math.sqrt(i || 0.5);
            points.push({
                year: i,
                mean: Math.max(0, mean),
                high: Math.max(0, mean * Math.exp(sigma * totalVol)),
                low:  Math.max(0, mean * Math.exp(-sigma * totalVol))
            });
        }
        return points;
    }

    // Full projection: assets + mortgage amortization + net worth.
    // Returns { data[], mortgagePaidOffYear, totalInterestPaid }
    function runProjection(cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate,
                           mortgagePrincipal, mortgageRate, monthlyPayment, years, sigma, stocksVol,
                           stocksMonthlyContribution) {
        const VOLATILITY = { cash: 0.01, bonds: 0.06, stocks: 0.16 };

        const cashProj   = projectAccount(cashAmt,   cashRate,   VOLATILITY.cash,   years, sigma);
        const stocksProj = projectAccount(stocksAmt, stocksRate, (stocksVol != null ? stocksVol : VOLATILITY.stocks), years, sigma, stocksMonthlyContribution);
        const bondsProj  = projectAccount(bondsAmt,  bondsRate,  VOLATILITY.bonds,  years, sigma);

        let currentMortgage     = mortgagePrincipal;
        const mRate             = mortgageRate / 100 / 12;
        let mortgagePaidOffYear = null;
        let totalInterestPaid   = 0;

        const data = [];

        for (let i = 0; i <= years; i++) {
            // Month-by-month amortization for year i
            if (i > 0 && currentMortgage > 0) {
                for (let m = 0; m < 12; m++) {
                    if (currentMortgage <= 0) break;
                    const interest = currentMortgage * mRate;
                    totalInterestPaid += interest;
                    const principal = monthlyPayment - interest;
                    currentMortgage -= principal;
                }
                if (currentMortgage < 0) currentMortgage = 0;
                if (currentMortgage === 0 && mortgagePaidOffYear === null) {
                    mortgagePaidOffYear = i;
                }
            }

            const assetMean = cashProj[i].mean + stocksProj[i].mean + bondsProj[i].mean;
            const assetHigh = cashProj[i].high + stocksProj[i].high + bondsProj[i].high;
            const assetLow  = cashProj[i].low  + stocksProj[i].low  + bondsProj[i].low;

            data.push({
                year:          i,
                assets:        assetMean,
                assetsHigh:    assetHigh,
                assetsLow:     assetLow,
                mortgage:      currentMortgage,
                netWorth:      assetMean - currentMortgage,
                netWorthHigh:  assetHigh - currentMortgage,
                netWorthLow:   assetLow  - currentMortgage
            });
        }

        return { data, mortgagePaidOffYear, totalInterestPaid };
    }

    // SVG chart rendering
    function renderChart(projResult, totalStarting, years, goals) {
```

Replace it with (function bodies unchanged, just removed from this scope — `renderChart` stays here since it's still DOM-bound to `#fcChartContainer`, `#fcGoalChips`):

```javascript
    // SVG chart rendering
    function renderChart(projResult, totalStarting, years, goals) {
```

Then find this exact block near the top of the file (right after `computeGoalOverlays` and its window export, right before `function setupForecastPage() {`):

```javascript
window.computeGoalOverlays = computeGoalOverlays;

function setupForecastPage() {
```

Replace it with:

```javascript
window.computeGoalOverlays = computeGoalOverlays;

// Per-asset GBM projection, with an optional monthly contribution added
// as an ordinary annuity (annual compounding, deposits at each year-end
// — the same order of approximation as the rest of this model).
// Returns array[0..years] of { year, mean, high, low }
// Module-scope (not trapped in setupForecastPage's closure) so the
// Dashboard's Wealth Simulator preview card can reuse it without duplicating
// the math — see loadDashboardForecastPreview().
function projectAccount(startAmount, annualRatePct, volatility, years, sigma, monthlyContribution) {
    const r = annualRatePct / 100;
    const contribution = monthlyContribution || 0;
    const points = [];
    for (let i = 0; i <= years; i++) {
        const grown = startAmount * Math.pow(1 + r, i);
        const contributed = contribution <= 0 ? 0
            : r > 0 ? contribution * 12 * ((Math.pow(1 + r, i) - 1) / r)
            : contribution * 12 * i;
        const mean = grown + contributed;
        const totalVol = volatility * Math.sqrt(i || 0.5);
        points.push({
            year: i,
            mean: Math.max(0, mean),
            high: Math.max(0, mean * Math.exp(sigma * totalVol)),
            low:  Math.max(0, mean * Math.exp(-sigma * totalVol))
        });
    }
    return points;
}
window.projectAccount = projectAccount;

// Full projection: assets + mortgage amortization + net worth.
// Returns { data[], mortgagePaidOffYear, totalInterestPaid }
function runProjection(cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate,
                       mortgagePrincipal, mortgageRate, monthlyPayment, years, sigma, stocksVol,
                       stocksMonthlyContribution) {
    const VOLATILITY = { cash: 0.01, bonds: 0.06, stocks: 0.16 };

    const cashProj   = projectAccount(cashAmt,   cashRate,   VOLATILITY.cash,   years, sigma);
    const stocksProj = projectAccount(stocksAmt, stocksRate, (stocksVol != null ? stocksVol : VOLATILITY.stocks), years, sigma, stocksMonthlyContribution);
    const bondsProj  = projectAccount(bondsAmt,  bondsRate,  VOLATILITY.bonds,  years, sigma);

    let currentMortgage     = mortgagePrincipal;
    const mRate             = mortgageRate / 100 / 12;
    let mortgagePaidOffYear = null;
    let totalInterestPaid   = 0;

    const data = [];

    for (let i = 0; i <= years; i++) {
        // Month-by-month amortization for year i
        if (i > 0 && currentMortgage > 0) {
            for (let m = 0; m < 12; m++) {
                if (currentMortgage <= 0) break;
                const interest = currentMortgage * mRate;
                totalInterestPaid += interest;
                const principal = monthlyPayment - interest;
                currentMortgage -= principal;
            }
            if (currentMortgage < 0) currentMortgage = 0;
            if (currentMortgage === 0 && mortgagePaidOffYear === null) {
                mortgagePaidOffYear = i;
            }
        }

        const assetMean = cashProj[i].mean + stocksProj[i].mean + bondsProj[i].mean;
        const assetHigh = cashProj[i].high + stocksProj[i].high + bondsProj[i].high;
        const assetLow  = cashProj[i].low  + stocksProj[i].low  + bondsProj[i].low;

        data.push({
            year:          i,
            assets:        assetMean,
            assetsHigh:    assetHigh,
            assetsLow:     assetLow,
            mortgage:      currentMortgage,
            netWorth:      assetMean - currentMortgage,
            netWorthHigh:  assetHigh - currentMortgage,
            netWorthLow:   assetLow  - currentMortgage
        });
    }

    return { data, mortgagePaidOffYear, totalInterestPaid };
}
window.runProjection = runProjection;

function setupForecastPage() {
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test-js`
Expected: PASS — all 5 new tests, plus every pre-existing test in the file (the hoist doesn't change either function's behavior, so `computeGoalOverlays`/`historyToForecast`/etc. tests are unaffected). Also manually confirm no regression: open the app, go to Wealth Simulator, click "Run Forecast" — it should render exactly as before (this exercises `runForecast()`'s existing calls to the now-hoisted functions, unchanged call sites).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "refactor: hoist projectAccount/runProjection to module scope for reuse"
```

---

### Task 2: Persist Wealth Simulator inputs (`pfmForecastConfig`) + prefill on load

**Files:**
- Modify: `web_client/js/pfm_features.js` (add `saveForecastConfig`/`loadForecastConfig`, prefill block in `setupForecastPage()`, save call in `runForecast()`)

**Interfaces:**
- Produces: `window.saveForecastConfig(cfg)` (writes `localStorage['pfmForecastConfig']` as JSON; swallows quota/private-mode errors)
- Produces: `window.loadForecastConfig()` → parsed config object or `null` if nothing saved / JSON is corrupt
- Consumed by: Task 3 (`loadDashboardForecastPreview` calls `loadForecastConfig()`)

- [ ] **Step 1: Add the persistence helpers**

In `web_client/js/pfm_features.js`, right after the `window.runProjection = runProjection;` line added in Task 1 (and before `function setupForecastPage() {`), insert:

```javascript
const FORECAST_CONFIG_KEY = 'pfmForecastConfig';

// Persists everything the Wealth Simulator's Run button used *except* the
// stocks amount, which always tracks live holdings (loadStartValue()) rather
// than a stale saved figure — read back both by the Forecast page itself
// (prefill on load) and by the Dashboard's live preview card.
function saveForecastConfig(cfg) {
    try {
        localStorage.setItem(FORECAST_CONFIG_KEY, JSON.stringify(cfg));
    } catch (e) { /* localStorage unavailable (private mode / quota) — skip persistence */ }
}
function loadForecastConfig() {
    try {
        const raw = localStorage.getItem(FORECAST_CONFIG_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch (e) {
        return null;
    }
}
window.saveForecastConfig = saveForecastConfig;
window.loadForecastConfig = loadForecastConfig;
```

- [ ] **Step 2: Prefill inputs from saved config on page setup**

In `web_client/js/pfm_features.js`, inside `setupForecastPage()`, find:

```javascript
    // Chart + summary
    const chartSvg         = document.getElementById('fcChartSvg');
    const chartPlaceholder = document.getElementById('fcChartPlaceholder');
    const summaryRow       = document.getElementById('fcSummaryRow');
    const rangeRow         = document.getElementById('fcRangeRow');
    const totalLiquidBadge = document.getElementById('fcTotalLiquidBadge');

    if (!runBtn) return;
```

Replace it with:

```javascript
    // Chart + summary
    const chartSvg         = document.getElementById('fcChartSvg');
    const chartPlaceholder = document.getElementById('fcChartPlaceholder');
    const summaryRow       = document.getElementById('fcSummaryRow');
    const rangeRow         = document.getElementById('fcRangeRow');
    const totalLiquidBadge = document.getElementById('fcTotalLiquidBadge');

    if (!runBtn) return;

    // Prefill from last-used settings (localStorage) — everything except the
    // stocks amount, which always tracks live holdings via loadStartValue().
    // A first-ever visit (nothing saved yet) leaves the HTML's hardcoded
    // default values untouched.
    const savedForecastConfig = loadForecastConfig();
    if (savedForecastConfig) {
        if (savedForecastConfig.cashAmount != null) cashAmountInput.value = savedForecastConfig.cashAmount;
        if (savedForecastConfig.cashRate != null) cashRateInput.value = savedForecastConfig.cashRate;
        if (savedForecastConfig.stocksRate != null) stocksRateInput.value = savedForecastConfig.stocksRate;
        if (savedForecastConfig.stocksVol != null && stocksVolInput) stocksVolInput.value = savedForecastConfig.stocksVol;
        if (savedForecastConfig.stocksContribution != null && stocksContributionInput) stocksContributionInput.value = savedForecastConfig.stocksContribution;
        if (savedForecastConfig.bondsAmount != null) bondsAmountInput.value = savedForecastConfig.bondsAmount;
        if (savedForecastConfig.bondsRate != null) bondsRateInput.value = savedForecastConfig.bondsRate;
        if (savedForecastConfig.mortgagePrincipal != null) mortgagePrincipalInput.value = savedForecastConfig.mortgagePrincipal;
        if (savedForecastConfig.mortgageRate != null) mortgageRateInput.value = savedForecastConfig.mortgageRate;
        if (savedForecastConfig.monthlyPayment != null) monthlyPaymentInput.value = savedForecastConfig.monthlyPayment;
        if (savedForecastConfig.years != null) yearsSlider.value = savedForecastConfig.years;
        if (savedForecastConfig.confidence != null) confSelect.value = savedForecastConfig.confidence;
        if (yearsDisplay) yearsDisplay.textContent = yearsSlider.value;
        updateTotalLiquidBadge();
        updateMortgageNote();
    }
```

(`updateTotalLiquidBadge` and `updateMortgageNote` are `function` declarations later in the same `setupForecastPage()` body — hoisted within the function scope, so calling them here is valid.)

- [ ] **Step 3: Save config on every "Run Forecast" click**

In `web_client/js/pfm_features.js`, inside `runForecast()`, find:

```javascript
        const stocksVol     = stocksVolInput ? (parseFloat(stocksVolInput.value) || 16) / 100 : null;
        const stocksContribution = stocksContributionInput ? (parseFloat(stocksContributionInput.value) || 0) : 0;

        const proj = runProjection(
```

Replace it with:

```javascript
        const stocksVol     = stocksVolInput ? (parseFloat(stocksVolInput.value) || 16) / 100 : null;
        const stocksContribution = stocksContributionInput ? (parseFloat(stocksContributionInput.value) || 0) : 0;

        saveForecastConfig({
            cashAmount: cashAmountInput.value,
            cashRate: cashRateInput.value,
            stocksRate: stocksRateInput.value,
            stocksVol: stocksVolInput ? stocksVolInput.value : '16',
            stocksContribution: stocksContributionInput ? stocksContributionInput.value : '0',
            bondsAmount: bondsAmountInput.value,
            bondsRate: bondsRateInput.value,
            mortgagePrincipal: mortgagePrincipalInput.value,
            mortgageRate: mortgageRateInput.value,
            monthlyPayment: monthlyPaymentInput.value,
            years: yearsSlider.value,
            confidence: confSelect.value,
        });

        const proj = runProjection(
```

- [ ] **Step 4: Manually verify persistence in the browser**

Run: `make dev` (serves the app at `http://localhost:8000`)

1. Open the app, go to Wealth Simulator.
2. Set Cash amount = `5000`, Cash rate = `2.0`, Years slider = `20`, click "Run Forecast".
3. Open DevTools → Application/Storage → Local Storage → confirm `pfmForecastConfig` holds `{"cashAmount":"5000","cashRate":"2.0",...,"years":"20",...}`.
4. Navigate to Dashboard, then back to Wealth Simulator (or hard-reload the page).
5. Confirm Cash amount still shows `5000`, Cash rate `2.0`, Years `20` — but Stocks amount still shows the live holdings total (unchanged, not persisted).

Expected: all of the above hold. No test framework covers this (DOM-heavy, input-value manipulation) — this manual pass is the verification for this task.

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: persist Wealth Simulator inputs across visits (pfmForecastConfig)"
```

---

### Task 3: Dashboard Wealth Simulator live preview card

**Files:**
- Modify: `web_client/index.html:465-519` (replace CTA card body; add `mb-4` to Row 3 now that Row 4 follows it)
- Modify: `web_client/js/pfm_features.js` (add `renderDashboardForecastChart`, `DASH_FORECAST_DEFAULTS`, `loadDashboardForecastPreview`, right after `setupForecastPage()` closes)
- Modify: `web_client/js/pfm_pages.js:401-406` (wire the new loader into `loadDashboardPage()`)

**Interfaces:**
- Consumes: `window.runProjection(...)` and `window.loadForecastConfig()` from Tasks 1–2.
- Produces: `window.loadDashboardForecastPreview(stocksTotalValue)` — called from `loadDashboardPage()`.

- [ ] **Step 1: Update the Dashboard HTML**

In `web_client/index.html`, find:

```html
                    <!-- Row 3: Simulator CTA + Recent Transactions -->
                    <div class="row g-3">
                        <!-- Simulate Future Wealth CTA -->
                        <div class="col-12 col-md-4">
                            <div class="card border-primary h-100">
                                <div class="card-body d-flex flex-column justify-content-between">
                                    <div>
                                        <h5 class="card-title text-primary">
                                            <i class="bi bi-graph-up-arrow me-2"></i>Wealth Simulator
                                        </h5>
                                        <p class="card-text text-muted small">
                                            Project your portfolio growth over time using Geometric Brownian Motion.
                                            Your current portfolio value is pre-filled automatically.
                                        </p>
                                    </div>
                                    <button class="btn btn-primary mt-3" onclick="window.navigationManager.showPage('forecast')">
                                        Simulate Future Wealth <i class="bi bi-arrow-right ms-1"></i>
                                    </button>
                                </div>
                            </div>
                        </div>
```

Replace it with:

```html
                    <!-- Row 3: Wealth Simulator preview + Recent Transactions -->
                    <div class="row g-3 mb-4">
                        <!-- Wealth Simulator live preview -->
                        <div class="col-12 col-md-4">
                            <div class="card border-primary h-100">
                                <div class="card-body d-flex flex-column">
                                    <h5 class="card-title text-primary mb-2">
                                        <i class="bi bi-graph-up-arrow me-2"></i>Wealth Simulator
                                    </h5>
                                    <div id="dashForecastArea" class="flex-grow-1 d-flex flex-column justify-content-center">
                                        <div class="text-muted small text-center">
                                            <div class="spinner-border spinner-border-sm mb-1" role="status"></div><br>Loading…
                                        </div>
                                    </div>
                                    <button class="btn btn-outline-primary btn-sm mt-2" onclick="window.navigationManager.showPage('forecast')">
                                        Customize <i class="bi bi-arrow-right ms-1"></i>
                                    </button>
                                </div>
                            </div>
                        </div>
```

(The closing `</div>` of Row 3 and the "Recent Transactions" column are unchanged — only the `<!-- Row 3 ... -->` comment, the opening `row g-3` → `row g-3 mb-4`, and the CTA card's inner content changed.)

- [ ] **Step 2: Add the compact chart renderer + preview loader**

In `web_client/js/pfm_features.js`, find the end of `setupForecastPage()`:

```javascript
    // Expose loadStartValue/loadGoals so navigationManager can call them on page show
    window._fcLoadStartValue = loadStartValue;
}

// ---------------------------------------------------------------------------
// Rebalancing (Holdings page)
// ---------------------------------------------------------------------------
```

Replace it with:

```javascript
    // Expose loadStartValue/loadGoals so navigationManager can call them on page show
    window._fcLoadStartValue = loadStartValue;
}

// ---------------------------------------------------------------------------
// Dashboard: Wealth Simulator live preview card
// ---------------------------------------------------------------------------

// Compact SVG projection chart for the Dashboard card — mean net-worth line
// + confidence band only, no goal overlays or mortgage-payoff annotations
// (those stay exclusive to the full Wealth Simulator page).
function renderDashboardForecastChart(container, data, years) {
    const W = container.clientWidth || 240;
    const H = 130;
    const PAD = { top: 22, right: 14, bottom: 18, left: 14 };
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;

    const allVals = data.flatMap(p => [p.netWorthHigh, p.netWorthLow]);
    const maxVal = Math.max(...allVals, 0);
    const minVal = Math.min(...allVals, 0);
    const range = (maxVal - minVal) || 1;

    const xScale = t => PAD.left + (t / years) * innerW;
    const yScale = v => PAD.top + innerH - ((v - minVal) / range) * innerH;
    const pathD = key => data.map((p, i) =>
        (i === 0 ? 'M' : 'L') + xScale(p.year).toFixed(1) + ',' + yScale(p[key]).toFixed(1)
    ).join(' ');

    const bandPath = pathD('netWorthHigh') + ' '
        + data.slice().reverse().map(p =>
            'L' + xScale(p.year).toFixed(1) + ',' + yScale(p.netWorthLow).toFixed(1)
        ).join(' ') + ' Z';

    const startVal = data[0].netWorth;
    const endVal = data[years].netWorth;
    const fmtCompact = v => {
        const n = Math.round(v);
        if (Math.abs(n) >= 1000000) return '€' + (n / 1000000).toFixed(1) + 'M';
        if (Math.abs(n) >= 1000) return '€' + (n / 1000).toFixed(0) + 'k';
        return '€' + n;
    };

    container.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;display:block;">
            <defs>
                <linearGradient id="dashFcBandGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#93c5fd" stop-opacity="0.35"/>
                    <stop offset="100%" stop-color="#93c5fd" stop-opacity="0.05"/>
                </linearGradient>
            </defs>
            <path d="${bandPath}" fill="url(#dashFcBandGrad)" stroke="none"/>
            <path d="${pathD('netWorth')}" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="${xScale(0).toFixed(1)}" cy="${yScale(startVal).toFixed(1)}" r="3.5" fill="#64748b"/>
            <circle cx="${xScale(years).toFixed(1)}" cy="${yScale(endVal).toFixed(1)}" r="4" fill="#2563eb" stroke="white" stroke-width="1.5"/>
            <text x="${xScale(0).toFixed(1)}" y="${(PAD.top - 8).toFixed(1)}" font-size="10" fill="#64748b">Now: ${fmtCompact(startVal)}</text>
            <text x="${xScale(years).toFixed(1)}" y="${(PAD.top - 8).toFixed(1)}" text-anchor="end" font-size="10" fill="#2563eb" font-weight="bold">${fmtCompact(endVal)} in ${years}y</text>
        </svg>
    `;
}

// Defaults mirror the Forecast page's hardcoded HTML input values
// (index.html #fcCashRate etc.) so a user who has never opened that page
// sees the same assumptions here as they would there.
const DASH_FORECAST_DEFAULTS = {
    cashAmount: '0', cashRate: '1.5', stocksRate: '8.0', stocksVol: '16',
    stocksContribution: '0', bondsAmount: '0', bondsRate: '4.0',
    mortgagePrincipal: '0', mortgageRate: '3.5', monthlyPayment: '0',
    years: '30', confidence: '1.96',
};

// stocksTotalValue: current holdings total (already fetched for the KPI
// cards) — always live, never read from pfmForecastConfig.
function loadDashboardForecastPreview(stocksTotalValue) {
    const area = document.getElementById('dashForecastArea');
    if (!area) return;
    const cfg = Object.assign({}, DASH_FORECAST_DEFAULTS, loadForecastConfig() || {});

    const cashAmt    = parseFloat(cfg.cashAmount)    || 0;
    const cashRate   = parseFloat(cfg.cashRate)      || 0;
    const stocksAmt  = parseFloat(stocksTotalValue)  || 0;
    const stocksRate = parseFloat(cfg.stocksRate)    || 0;
    const stocksVol  = (parseFloat(cfg.stocksVol) || 16) / 100;
    const stocksContribution = parseFloat(cfg.stocksContribution) || 0;
    const bondsAmt   = parseFloat(cfg.bondsAmount)   || 0;
    const bondsRate  = parseFloat(cfg.bondsRate)     || 0;
    const mortPrincipal = parseFloat(cfg.mortgagePrincipal) || 0;
    const mortRate      = parseFloat(cfg.mortgageRate)      || 0;
    const mortPayment   = parseFloat(cfg.monthlyPayment)    || 0;
    const years  = parseInt(cfg.years, 10) || 30;
    const sigma  = parseFloat(cfg.confidence) || 1.96;

    const totalStarting = cashAmt + stocksAmt + bondsAmt;
    if (totalStarting <= 0) {
        area.innerHTML = `
            <p class="card-text text-muted small mb-0">
                Project your portfolio growth over time using Geometric Brownian Motion.
                Your current portfolio value is pre-filled automatically.
            </p>`;
        return;
    }

    const proj = runProjection(
        cashAmt, cashRate, stocksAmt, stocksRate, bondsAmt, bondsRate,
        mortPrincipal, mortRate, mortPayment, years, sigma, stocksVol, stocksContribution
    );
    renderDashboardForecastChart(area, proj.data, years);
}
window.loadDashboardForecastPreview = loadDashboardForecastPreview;

// ---------------------------------------------------------------------------
// Rebalancing (Holdings page)
// ---------------------------------------------------------------------------
```

- [ ] **Step 3: Wire the preview into `loadDashboardPage()`**

In `web_client/js/pfm_pages.js`, find:

```javascript
            // --- KPI cards ---
            const totalValue = parseFloat(summary.total_value || 0);
            const totalCost  = parseFloat(summary.total_cost  || 0);
            const totalPnl   = parseFloat(summary.total_pnl   || 0);
            const totalPnlPct = parseFloat(summary.total_pnl_pct || 0);
            const openPositions = holdings.filter(h => parseFloat(h.quantity || 0) > 0).length;
```

Replace it with:

```javascript
            // --- KPI cards ---
            const totalValue = parseFloat(summary.total_value || 0);
            const totalCost  = parseFloat(summary.total_cost  || 0);
            const totalPnl   = parseFloat(summary.total_pnl   || 0);
            const totalPnlPct = parseFloat(summary.total_pnl_pct || 0);
            const openPositions = holdings.filter(h => parseFloat(h.quantity || 0) > 0).length;

            // Wealth Simulator live preview — independent, non-blocking (a
            // failure here must not blank the rest of the dashboard).
            if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue);
```

- [ ] **Step 4: Manually verify in the browser**

Run: `make dev`

1. Open the Dashboard. Confirm the Wealth Simulator card shows a compact chart (blue line + shaded band) with "Now: €X" and "€Y in 30y" labels, instead of the old CTA text.
2. Compare the numbers against the full Wealth Simulator page: open it, note the current auto-populated Stocks amount, click "Run Forecast" with default cash/bonds/mortgage (0), confirm the full page's final-year net worth (`#fcSumNetWorth`) matches the Dashboard card's end-value label for the same inputs.
3. Change an input on the full Wealth Simulator page (e.g. Cash amount = 3000), click "Run Forecast", go back to Dashboard — confirm the card's chart reflects the new Cash amount (band/line shifted).
4. Test the empty state: as a portfolio with zero holdings and no saved config, confirm the card falls back to descriptive text (no chart) — this can be checked by clearing `localStorage.removeItem('pfmForecastConfig')` in DevTools console on an account with zero holdings, or by reasoning through the code (`totalStarting <= 0` branch).

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js web_client/js/pfm_pages.js
git commit -m "feat: embed a live Wealth Simulator preview chart on the Dashboard"
```

---

### Task 4: Dashboard Bank Accounts card

**Files:**
- Modify: `web_client/index.html:519` (insert new Row 4 with both card shells — Bank Accounts fully wired, Top Spending Categories shell only, wired in Task 5)
- Modify: `web_client/js/pfm_analytics.js` (add `renderDashboardBankAccounts`, `loadDashboardBankAccounts`, after `_renderBankAccounts`)
- Modify: `web_client/js/pfm_pages.js` (wire the new loader into `loadDashboardPage()`)

**Interfaces:**
- Consumes: `window.apiClient.getNetworth()` → `{ bank_accounts: Array<{portfolio_id, name, balance, currency, balance_eur, as_of}> }` (existing endpoint, unchanged).
- Produces: `window.loadDashboardBankAccounts()` — called from `loadDashboardPage()`.

Note: after this task, the Top Spending Categories card (added in the same HTML edit) will show its "Loading…" spinner indefinitely — that's expected until Task 5 wires it up. Don't treat it as a bug when verifying this task.

- [ ] **Step 1: Add Row 4 HTML (both card shells)**

In `web_client/index.html`, find the closing of Row 3 (this is the end of the Dashboard's `row g-3 mb-4` div from Task 3, immediately followed by the Dashboard page's own closing div and the Assets page comment):

```html
                        </div>
                    </div>
                </div>

                <!-- Assets Page -->
```

Replace it with:

```html
                        </div>
                    </div>

                    <!-- Row 4: Bank Accounts + Top Spending Categories -->
                    <div class="row g-3">
                        <!-- Bank Accounts -->
                        <div class="col-12 col-md-6">
                            <div class="card h-100">
                                <div class="card-header">
                                    <i class="bi bi-bank me-2"></i>Bank Accounts
                                </div>
                                <div class="card-body p-0">
                                    <div class="table-responsive">
                                        <table class="table table-hover table-sm mb-0" id="dashBankAccountsTable">
                                            <thead>
                                                <tr>
                                                    <th class="ps-3">Account</th>
                                                    <th class="text-end">Balance</th>
                                                    <th class="text-end pe-3">EUR</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                <tr>
                                                    <td colspan="3" class="text-center py-3">
                                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>
                                                        Loading…
                                                    </td>
                                                </tr>
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Top Spending Categories -->
                        <div class="col-12 col-md-6">
                            <div class="card h-100">
                                <div class="card-header d-flex justify-content-between align-items-center">
                                    <span><i class="bi bi-tags me-2"></i>Top Spending Categories</span>
                                    <span class="small text-muted">Last 30 days</span>
                                </div>
                                <div class="card-body" id="dashTopCategoriesArea">
                                    <div class="text-muted small text-center py-3">
                                        <div class="spinner-border spinner-border-sm mb-1" role="status"></div><br>Loading…
                                    </div>
                                </div>
                                <div class="card-footer bg-transparent text-end">
                                    <a href="#" class="small" onclick="window.navigationManager.showPage('spending'); const t = document.getElementById('spTabBtnCategories'); if (t) t.click(); return false;">View all <i class="bi bi-arrow-right"></i></a>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Assets Page -->
```

- [ ] **Step 2: Add the Bank Accounts renderer + loader**

In `web_client/js/pfm_analytics.js`, find the end of `_renderBankAccounts`:

```javascript
function _renderBankAccounts(accounts) {
    const wrap = document.getElementById('nwBankAccountsWrap');
    const body = document.getElementById('nwBankAccountsBody');
    if (!wrap || !body) return;
    if (!accounts.length) { wrap.style.display = 'none'; return; }
    wrap.style.display = '';
    body.innerHTML = accounts.map(a => {
        if (a.balance === null || a.balance === undefined) {
            return `
                <tr>
                    <td class="ps-3">${esc(a.name)}</td>
                    <td class="text-end text-muted" colspan="3">No balance imported yet</td>
                </tr>`;
        }
        return `
            <tr>
                <td class="ps-3">${esc(a.name)}</td>
                <td class="text-end">${Fmt.num(a.balance, 2, 2)} ${esc(a.currency || '')}</td>
                <td class="text-end">${Fmt.amt('€' + Fmt.num(a.balance_eur, 0, 0))}</td>
                <td class="text-muted small">${Fmt.date(a.as_of)}</td>
            </tr>`;
    }).join('');
}
```

Insert immediately after it:

```javascript

// Dashboard-only: same bank_accounts source as the Net Worth page's card
// above, rendered into the Dashboard's own compact table instead.
function renderDashboardBankAccounts(accounts) {
    const tbody = document.querySelector('#dashBankAccountsTable tbody');
    if (!tbody) return;
    if (!accounts.length) {
        tbody.innerHTML = `<tr><td colspan="3" class="text-center text-muted py-3">
            No bank accounts yet — add one on the
            <a href="#" onclick="window.navigationManager.showPage('networth'); return false;">Net Worth</a> page.
        </td></tr>`;
        return;
    }
    const total = accounts.reduce((s, a) => s + (parseFloat(a.balance_eur) || 0), 0);
    const rows = accounts.map(a => {
        if (a.balance === null || a.balance === undefined) {
            return `<tr><td class="ps-3">${esc(a.name)}</td><td class="text-end text-muted" colspan="2">No balance imported yet</td></tr>`;
        }
        return `
            <tr>
                <td class="ps-3">${esc(a.name)}</td>
                <td class="text-end">${Fmt.num(a.balance, 2, 2)} ${esc(a.currency || '')}</td>
                <td class="text-end pe-3">${Fmt.amt('€' + Fmt.num(a.balance_eur, 0, 0))}</td>
            </tr>`;
    }).join('');
    tbody.innerHTML = rows + `
        <tr class="table-light fw-semibold">
            <td class="ps-3">Total</td>
            <td></td>
            <td class="text-end pe-3">${Fmt.amt('€' + Fmt.num(total, 0, 0))}</td>
        </tr>`;
}

async function loadDashboardBankAccounts() {
    const tbody = document.querySelector('#dashBankAccountsTable tbody');
    if (!tbody) return;
    try {
        const nw = await window.apiClient.getNetworth();
        renderDashboardBankAccounts(nw.bank_accounts || []);
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-danger py-3">Could not load bank accounts.</td></tr>';
    }
}
window.loadDashboardBankAccounts = loadDashboardBankAccounts;
```

- [ ] **Step 3: Wire the loader into `loadDashboardPage()`**

In `web_client/js/pfm_pages.js`, find (edited in Task 3, Step 3):

```javascript
            // Wealth Simulator live preview — independent, non-blocking (a
            // failure here must not blank the rest of the dashboard).
            if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue);
```

Replace it with:

```javascript
            // Wealth Simulator live preview + Bank Accounts — independent,
            // non-blocking (a failure in either must not blank the rest of
            // the dashboard).
            if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue);
            if (window.loadDashboardBankAccounts) window.loadDashboardBankAccounts();
```

- [ ] **Step 4: Manually verify in the browser**

Run: `make dev`

1. Open the Dashboard. Confirm the new "Bank Accounts" card (bottom-left, Row 4) lists each bank account with native-currency balance and EUR equivalent, plus a "Total" row summing `balance_eur`.
2. Compare against the Net Worth page's existing "Bank Accounts" card for the same account — figures should match exactly (same `getNetworth()` source).
3. If the account has no bank-type portfolios, confirm the empty state renders: "No bank accounts yet — add one on the Net Worth page" with a working link.
4. Confirm the Top Spending Categories card next to it still shows its loading spinner (expected — wired in Task 5).

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_analytics.js web_client/js/pfm_pages.js
git commit -m "feat: add Bank Accounts card to the Dashboard"
```

---

### Task 5: Dashboard Top Spending Categories card

**Files:**
- Modify: `web_client/js/pfm_features.js` (add `renderDashboardTopCategories`, `loadDashboardTopCategories`, after `_renderSpendingCategoryChart`)
- Modify: `web_client/js/pfm_pages.js` (wire the new loader into `loadDashboardPage()`)

**Interfaces:**
- Consumes: `window.apiClient.getSpendingSummary(30)` → `{ by_category_eur: { [category: string]: number }, ... }` (existing endpoint, unchanged).
- Produces: `window.loadDashboardTopCategories()` — called from `loadDashboardPage()`.

- [ ] **Step 1: Add the renderer + loader**

In `web_client/js/pfm_features.js`, find the end of `_renderSpendingCategoryChart` (it ends with the `bar` chart-type branch's closing brace):

```javascript
        _spCategoryChartInstance = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Spent (30d, EUR)',
                    data: values,
                    backgroundColor: 'rgba(220,53,69,0.7)',
                    borderColor: 'rgba(220,53,69,1)',
                    borderWidth: 1,
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label(item) { return ` €${Fmt.num(item.raw, 0, 0)}`; }
                        }
                    }
                },
                scales: {
                    x: {
                        title: { display: true, text: 'EUR (30d)' },
                        ticks: { callback: v => '€' + v }
                    }
                }
            }
        });
    }
}
```

Insert immediately after this closing `}` (the one that ends `_renderSpendingCategoryChart`):

```javascript

// Dashboard-only: top-5 categories as a compact bar list (no Chart.js, kept
// consistent with the Dashboard's hand-rolled SVG donut rather than pulling
// in the heavier chart widget used on the Spending page).
function renderDashboardTopCategories(byCategoryEur) {
    const area = document.getElementById('dashTopCategoriesArea');
    if (!area) return;
    const entries = Object.entries(byCategoryEur || {}).sort((a, b) => b[1] - a[1]).slice(0, 5);
    if (!entries.length) {
        area.innerHTML = '<p class="text-muted small mb-0 text-center py-3">No spending imported yet.</p>';
        return;
    }
    const maxVal = entries[0][1];
    area.innerHTML = entries.map(([cat, amt]) => {
        const pct = maxVal > 0 ? Math.round((amt / maxVal) * 100) : 0;
        return `
            <div class="mb-2">
                <div class="d-flex justify-content-between small mb-1">
                    <span>${esc(cat)}</span>
                    <span class="text-muted">${Fmt.amt('€' + Fmt.num(amt, 0, 0))}</span>
                </div>
                <div class="progress" style="height:6px;">
                    <div class="progress-bar bg-danger" role="progressbar" style="width:${pct}%"></div>
                </div>
            </div>`;
    }).join('');
}

async function loadDashboardTopCategories() {
    const area = document.getElementById('dashTopCategoriesArea');
    if (!area) return;
    try {
        const summary = await window.apiClient.getSpendingSummary(30);
        renderDashboardTopCategories(summary.by_category_eur || {});
    } catch (e) {
        area.innerHTML = '<p class="text-danger small mb-0 text-center py-3">Could not load spending data.</p>';
    }
}
window.loadDashboardTopCategories = loadDashboardTopCategories;
```

- [ ] **Step 2: Wire the loader into `loadDashboardPage()`**

In `web_client/js/pfm_pages.js`, find (edited in Task 4, Step 3):

```javascript
            // Wealth Simulator live preview + Bank Accounts — independent,
            // non-blocking (a failure in either must not blank the rest of
            // the dashboard).
            if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue);
            if (window.loadDashboardBankAccounts) window.loadDashboardBankAccounts();
```

Replace it with:

```javascript
            // Wealth Simulator live preview + Bank Accounts + Top Spending
            // Categories — independent, non-blocking (a failure in any one
            // must not blank the rest of the dashboard).
            if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue);
            if (window.loadDashboardBankAccounts) window.loadDashboardBankAccounts();
            if (window.loadDashboardTopCategories) window.loadDashboardTopCategories();
```

- [ ] **Step 3: Manually verify in the browser**

Run: `make dev`

1. Open the Dashboard. Confirm the "Top Spending Categories" card (bottom-right, Row 4) now shows up to 5 categories as horizontal bars, sorted by 30-day spend descending, each with its EUR amount.
2. Compare against the Spending page's Categories tab chart for the same period — the top categories and amounts should match (same `getSpendingSummary(30).by_category_eur` source).
3. Click "View all →" — confirm it navigates to the Spending page and lands on the Categories tab specifically (not the default Transactions tab).
4. On an account with no spending imports, confirm the empty state renders: "No spending imported yet."

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/pfm_pages.js
git commit -m "feat: add Top Spending Categories card to the Dashboard"
```

---

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (Wealth Simulator paragraph, ~line 264; add a Dashboard cards note after it)
- Modify: `PROJECT_STATUS.md` (new version entry at the top of the "Recent" list)

- [ ] **Step 1: Update CLAUDE.md's Wealth Simulator paragraph**

In `CLAUDE.md`, find:

```
**Wealth Simulator** (`pfm_features.js`, `setupForecastPage`): entirely client-side, no dedicated backend router — `projectAccount(startAmount, annualRatePct, volatility, years, sigma, monthlyContribution)` models each asset class as GBM plus an optional ordinary-annuity monthly contribution (stocks bucket only, via `fcStocksContribution`); `runProjection(...)` adds deterministic mortgage amortization; `renderChart(projResult, totalStarting, years, goals)` draws the SVG. `computeGoalOverlays(goals, naturalMin, naturalMax, years)` (pure, unit-tested) decides per selected goal whether its target renders as an in-chart dashed line/marker or — when far outside the projection's natural range — an off-chart chip in `#fcGoalChips`, so a large goal (e.g. €1M) can't flatten a much smaller projection.
```

Replace it with:

```
**Wealth Simulator** (`pfm_features.js`, `setupForecastPage`): entirely client-side, no dedicated backend router — `projectAccount(startAmount, annualRatePct, volatility, years, sigma, monthlyContribution)` models each asset class as GBM plus an optional ordinary-annuity monthly contribution (stocks bucket only, via `fcStocksContribution`); `runProjection(...)` adds deterministic mortgage amortization; `renderChart(projResult, totalStarting, years, goals)` draws the SVG. `computeGoalOverlays(goals, naturalMin, naturalMax, years)` (pure, unit-tested) decides per selected goal whether its target renders as an in-chart dashed line/marker or — when far outside the projection's natural range — an off-chart chip in `#fcGoalChips`, so a large goal (e.g. €1M) can't flatten a much smaller projection. `projectAccount`/`runProjection` are module-scope (hoisted out of `setupForecastPage`, same treatment as `computeGoalOverlays`) so the Dashboard's Wealth Simulator preview card can reuse the same math without duplicating it. Every input except the stocks amount (always live from holdings) persists to `localStorage['pfmForecastConfig']` on each "Run Forecast" click and prefills on the Forecast page's next load; the Dashboard preview reads the same key (falling back to the page's hardcoded defaults if nothing is saved yet) to render a compact SVG chart (`renderDashboardForecastChart`) in `#dashForecastArea` — no goal overlays or mortgage-payoff annotations there, those stay exclusive to the full page.

**Dashboard** (`pfm_pages.js`, `loadDashboardPage`) also renders two independent, non-blocking cards alongside the KPI/positions/donut/simulator content: **Bank Accounts** (`renderDashboardBankAccounts` in `pfm_analytics.js`, same `getNetworth().bank_accounts` source as the Net Worth page's card) and **Top Spending Categories** (`renderDashboardTopCategories` in `pfm_features.js`, top 5 from `getSpendingSummary(30).by_category_eur`, plain Bootstrap progress bars rather than Chart.js). Both degrade to an empty-state message independently of each other and of the simulator preview.
```

- [ ] **Step 2: Add a PROJECT_STATUS.md entry**

In `PROJECT_STATUS.md`, find:

```
Last updated: 2026-07-23

**Recent (v2.5.32):**
```

Replace it with:

```
Last updated: 2026-07-23

**Recent (v2.5.33):** **Dashboard: live Wealth Simulator preview + Bank Accounts + Top Spending Categories cards.** The Dashboard's "Wealth Simulator" card was a CTA-only stub (description text + a button to the full page); it now embeds a compact live projection chart (mean net-worth line + confidence band), using either your last-run Wealth Simulator settings (`localStorage['pfmForecastConfig']`, new) or the page's defaults, with your current holdings total always read live. `projectAccount`/`runProjection` were hoisted from a private closure inside `setupForecastPage()` to module scope so this card can reuse the exact same projection math. Two new cards read data that already existed elsewhere: **Bank Accounts** (same source as the Net Worth page's card) and **Top Spending Categories** (top 5 by 30-day spend, same source as the Spending page's Categories chart, rendered as plain Bootstrap progress bars rather than pulling in Chart.js).

**Recent (v2.5.32):**
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document the Dashboard's Wealth Simulator preview, Bank Accounts, and Top Spending Categories cards"
```
