# Spending Time-Frame Selector + Dashboard Spending Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Spending page's hardcoded 30-day Spent/Income/Transferred window with a selectable period (7/30/90/365 days), and merge that same summary into the Dashboard's existing "Top Spending Categories" card (renamed "Spending"), keeping both pages' period choice in sync via one shared `localStorage` key.

**Architecture:** Classic-script frontend, no build step (`web_client/js/*.js` concatenated in `index.html` load order: `pfm_core.js`, `pfm_pages.js`, `pfm_analytics.js`, `pfm_features.js`). No new backend endpoints — `GET /api/v1/spending/summary?days=N` already accepts `days` (default 30). A new `pfmSpendingSummaryDays` localStorage key (read/written via two new pure module-scope helpers) is the single source of truth for "which period is currently selected," read by both the Spending page and the Dashboard's Spending card.

**Tech Stack:** Vanilla JS (no framework), Bootstrap 5 (form-select, card-header), Node's built-in test runner (`node --test`) via the `vm`-context harness in `web_client/js/tests/web_client.test.mjs`.

## Global Constraints

- No new backend endpoints or schema changes — frontend-only work. `GET /api/v1/spending/summary?days=N` is unchanged.
- The Net Worth page's "Actual (last 30 days)" comparison widget (`pfm_analytics.js:535`, `getSpendingSummary(30)`) is untouched — stays fixed at 30 days, out of scope.
- Allowed period values are exactly `7`, `30`, `90`, `365` (days) — no other presets, no free-form input.
- `localStorage['pfmSpendingSummaryDays']` is a plain string of one of those four values; default `'30'` when unset or unparseable — matches today's hardcoded default so a first-ever visit looks identical to before this change.
- Every money figure must go through `Fmt.amt('€' + Fmt.num(...))`; every interpolated category/account name through `esc()` — same convention as the rest of this codebase.
- Spec: `docs/superpowers/specs/2026-07-23-spending-period-selector-design.md`

---

### Task 1: Shared period persistence helpers + unit tests

**Files:**
- Modify: `web_client/js/pfm_features.js` (insert `getSpendingPeriodDays`/`setSpendingPeriodDays` before `async function loadSpendingPage()`)
- Test: `web_client/js/tests/web_client.test.mjs` (append after the last existing test)

**Interfaces:**
- Produces: `window.getSpendingPeriodDays()` → `number` (one of `7`, `30`, `90`, `365`; falls back to `30` on missing/corrupt/invalid storage)
- Produces: `window.setSpendingPeriodDays(days)` → `void` (persists `days` as a string; swallows localStorage errors)
- Consumed by: Task 2 (Spending page) and Task 3 (Dashboard's Spending card), both via `window.getSpendingPeriodDays()`/`window.setSpendingPeriodDays()`.

- [ ] **Step 1: Write the failing tests**

Open `web_client/js/tests/web_client.test.mjs` and append the following block at the end of the file (after the last existing test, which currently ends with the `_ruleDedupKey treats a different pattern with the same category as distinct` test):

```javascript
test("getSpendingPeriodDays: defaults to 30 when nothing is saved", () => {
    const { getSpendingPeriodDays } = loadAppIntoContext();
    assert.equal(getSpendingPeriodDays(), 30);
});

test("getSpendingPeriodDays/setSpendingPeriodDays: round-trips a valid value", () => {
    const w = loadAppIntoContext();
    w.setSpendingPeriodDays(90);
    assert.equal(w.getSpendingPeriodDays(), 90);
});

test("getSpendingPeriodDays: falls back to 30 for a corrupted/invalid stored value", () => {
    const w = loadAppIntoContext();
    w.localStorage.setItem('pfmSpendingSummaryDays', 'not-a-number');
    assert.equal(w.getSpendingPeriodDays(), 30);
    w.localStorage.setItem('pfmSpendingSummaryDays', '999');
    assert.equal(w.getSpendingPeriodDays(), 30);
});

test("setSpendingPeriodDays: persists across a fresh getSpendingPeriodDays call for every allowed value", () => {
    const w = loadAppIntoContext();
    [7, 30, 90, 365].forEach(days => {
        w.setSpendingPeriodDays(days);
        assert.equal(w.getSpendingPeriodDays(), days);
    });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test-js` (or directly: `node --test web_client/js/tests/`)
Expected: FAIL — `getSpendingPeriodDays`/`setSpendingPeriodDays` are `undefined` on the loaded context, so every new test throws `TypeError: ... is not a function`.

- [ ] **Step 3: Implement the helpers**

In `web_client/js/pfm_features.js`, find this exact block (a constant right before `loadSpendingPage`):

```javascript
// Cap on unique descriptions sent to the LLM per "Suggest categories (AI)"
// click — a real account's uncategorized backlog can have 1000+ unique
// descriptions; sending them all in one request risks exceeding
// portf_web's nginx proxy_read_timeout (200s) with no useful error. One
// batch per click; the user re-selects and continues manually (see
// #spSelectAllUncategorized) — no auto-chaining needed since applying a
// batch's suggestions removes those rows from the uncategorized filter.
const SP_AI_SUGGEST_BATCH_SIZE = 30;

async function loadSpendingPage() {
```

Replace it with:

```javascript
// Cap on unique descriptions sent to the LLM per "Suggest categories (AI)"
// click — a real account's uncategorized backlog can have 1000+ unique
// descriptions; sending them all in one request risks exceeding
// portf_web's nginx proxy_read_timeout (200s) with no useful error. One
// batch per click; the user re-selects and continues manually (see
// #spSelectAllUncategorized) — no auto-chaining needed since applying a
// batch's suggestions removes those rows from the uncategorized filter.
const SP_AI_SUGGEST_BATCH_SIZE = 30;

const SPENDING_PERIOD_KEY = 'pfmSpendingSummaryDays';
const SPENDING_PERIOD_ALLOWED = [7, 30, 90, 365];
const SPENDING_PERIOD_DEFAULT = 30;

// Single source of truth for "which period is the Spent/Income/Transferred
// summary showing" — read by both the Spending page's own selector and the
// Dashboard's merged Spending card, so picking a period on either page is
// reflected on the other the next time it loads (see loadDashboardSpending()
// in the Dashboard section below).
function getSpendingPeriodDays() {
    try {
        const raw = parseInt(localStorage.getItem(SPENDING_PERIOD_KEY), 10);
        return SPENDING_PERIOD_ALLOWED.includes(raw) ? raw : SPENDING_PERIOD_DEFAULT;
    } catch (e) {
        return SPENDING_PERIOD_DEFAULT;
    }
}
function setSpendingPeriodDays(days) {
    try {
        localStorage.setItem(SPENDING_PERIOD_KEY, String(days));
    } catch (e) { /* localStorage unavailable (private mode / quota) — skip persistence */ }
}
window.getSpendingPeriodDays = getSpendingPeriodDays;
window.setSpendingPeriodDays = setSpendingPeriodDays;

async function loadSpendingPage() {
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test-js`
Expected: PASS — all 4 new tests, plus every pre-existing test in the file unaffected (no existing code path calls these two new functions yet).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: add shared spending-period persistence helpers"
```

---

### Task 2: Spending page period selector

**Files:**
- Modify: `web_client/index.html` (add `#spSummaryPeriod` select; add ids to the three summary-card labels)
- Modify: `web_client/js/pfm_features.js` (wire the select in `loadSpendingPage()`; add a label-update helper; make `_refreshSpendingData()` use the selected period)

**Interfaces:**
- Consumes: `window.getSpendingPeriodDays()`/`window.setSpendingPeriodDays()` from Task 1.
- Produces: `_updateSpSummaryLabels(days)` (private helper, not exported) — updates the three summary-card label texts to reflect the given day count.

- [ ] **Step 1: Add the period selector + label ids to the Spending page HTML**

In `web_client/index.html`, find this exact block (the Spending page's header action buttons):

```html
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanTransfers" title="Re-scan for transfers"><i class="bi bi-arrow-repeat"></i> Re-scan transfers</button>
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanCategories" title="Re-apply category rules to uncategorized rows"><i class="bi bi-tags"></i> Rescan categories</button>
                            <button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#spImportModal"><i class="bi bi-upload me-1"></i>Import statement</button>
                        </div>
```

Replace it with:

```html
                        <div class="d-flex gap-2 align-items-center">
                            <div class="d-flex align-items-center gap-1">
                                <label class="small text-muted mb-0" for="spSummaryPeriod">Period</label>
                                <select class="form-select form-select-sm" id="spSummaryPeriod" style="width:auto;">
                                    <option value="7">Last 7 days</option>
                                    <option value="30" selected>Last 30 days</option>
                                    <option value="90">Last 90 days</option>
                                    <option value="365">Last 365 days</option>
                                </select>
                            </div>
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanTransfers" title="Re-scan for transfers"><i class="bi bi-arrow-repeat"></i> Re-scan transfers</button>
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanCategories" title="Re-apply category rules to uncategorized rows"><i class="bi bi-tags"></i> Rescan categories</button>
                            <button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#spImportModal"><i class="bi bi-upload me-1"></i>Import statement</button>
                        </div>
```

Next, in the same file, find this exact block (the three summary cards):

```html
                    <div class="row g-2 mb-3">
                        <div class="col-6 col-md-3">
                            <div class="card h-100 border-danger">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1">Spent (30d)</div>
                                    <div class="fs-6 fw-bold text-danger" id="spSpent">—</div>
                                </div>
                            </div>
                        </div>
                        <div class="col-6 col-md-3">
                            <div class="card h-100 border-success">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1">Income (30d)</div>
                                    <div class="fs-6 fw-bold text-success" id="spIncome">—</div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-6">
                            <div class="card h-100">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1">Moved to other accounts (30d)</div>
                                    <div class="fs-6 fw-bold" id="spTransferred">—</div>
                                </div>
                            </div>
                        </div>
                    </div>
```

Replace it with (adds an `id` to each label div so JS can update the day count in place; text content is otherwise unchanged):

```html
                    <div class="row g-2 mb-3">
                        <div class="col-6 col-md-3">
                            <div class="card h-100 border-danger">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1" id="spSpentLabel">Spent (30d)</div>
                                    <div class="fs-6 fw-bold text-danger" id="spSpent">—</div>
                                </div>
                            </div>
                        </div>
                        <div class="col-6 col-md-3">
                            <div class="card h-100 border-success">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1" id="spIncomeLabel">Income (30d)</div>
                                    <div class="fs-6 fw-bold text-success" id="spIncome">—</div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-6">
                            <div class="card h-100">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1" id="spTransferredLabel">Moved to other accounts (30d)</div>
                                    <div class="fs-6 fw-bold" id="spTransferred">—</div>
                                </div>
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 2: Add the label-update helper and wire the selector**

In `web_client/js/pfm_features.js`, find this exact block inside `loadSpendingPage()` (its first three statements):

```javascript
async function loadSpendingPage() {
    _wireSpendingRuleForm();
    _wireSpCategoryAddForm();
    _wireSpendingImportModal();
```

Replace it with:

```javascript
// Updates the three summary cards' label text to reflect the selected
// period (e.g. "Spent (90d)") — kept as a plain day count for every
// option, no special-casing 365 to "1y", for consistency and simplicity.
function _updateSpSummaryLabels(days) {
    const el = id => document.getElementById(id);
    if (el('spSpentLabel')) el('spSpentLabel').textContent = `Spent (${days}d)`;
    if (el('spIncomeLabel')) el('spIncomeLabel').textContent = `Income (${days}d)`;
    if (el('spTransferredLabel')) el('spTransferredLabel').textContent = `Moved to other accounts (${days}d)`;
}

async function loadSpendingPage() {
    _wireSpendingRuleForm();
    _wireSpCategoryAddForm();
    _wireSpendingImportModal();
    const periodSel = document.getElementById('spSummaryPeriod');
    if (periodSel) {
        // Reflect the persisted choice immediately, before the first
        // _refreshSpendingData() call below, so a returning user sees their
        // last-picked period rather than a flash of the 30-day default.
        periodSel.value = String(getSpendingPeriodDays());
        _updateSpSummaryLabels(getSpendingPeriodDays());
        if (!periodSel.dataset.wired) {
            periodSel.dataset.wired = '1';
            periodSel.addEventListener('change', () => {
                const days = parseInt(periodSel.value, 10);
                setSpendingPeriodDays(days);
                _updateSpSummaryLabels(days);
                _refreshSpendingData();
            });
        }
    }
```

- [ ] **Step 3: Make `_refreshSpendingData()` use the selected period**

In `web_client/js/pfm_features.js`, find:

```javascript
async function _refreshSpendingData() {
    try {
        const [summary, portfolios, categories, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(30),
```

Replace it with:

```javascript
async function _refreshSpendingData() {
    try {
        const [summary, portfolios, categories, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(getSpendingPeriodDays()),
```

- [ ] **Step 4: Manually verify in the browser**

Run: `make dev` (serves the app at `http://localhost:8000`)

1. Open the Spending page. Confirm the new "Period" selector appears in the header, defaulting to "Last 30 days", and the three summary cards read "Spent (30d)", "Income (30d)", "Moved to other accounts (30d)".
2. Switch to "Last 7 days". Confirm all three card labels update to "(7d)" and their figures change to match a shorter window (should generally be smaller in magnitude than the 30-day figures, for an account with steady spending).
3. Switch to the Categories tab. Confirm the category-breakdown chart also reflects the 7-day window (same data source as the summary cards) — compare a category total against what it showed before switching, in a different browser tab if needed for comparison, or note the total before/after.
4. Switch back to "Last 90 days" or "Last 365 days", reload the page (F5). Confirm the selector and all three labels/figures come back showing your last pick (90d/365d), not reset to 30d.
5. Open DevTools → Local Storage → confirm `pfmSpendingSummaryDays` holds the value you last picked.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: add a selectable time frame to the Spending page's summary"
```

---

### Task 3: Dashboard "Spending" card (merge summary stats + rename)

**Files:**
- Modify: `web_client/index.html` (rename card, add inline period select + stat row div)
- Modify: `web_client/js/pfm_features.js` (add `renderDashboardSpendingStats`; rename `loadDashboardTopCategories` → `loadDashboardSpending`, merging both renders + wiring the new select)
- Modify: `web_client/js/pfm_pages.js` (update the wiring call site to the renamed function)

**Interfaces:**
- Consumes: `window.getSpendingPeriodDays()`/`window.setSpendingPeriodDays()` (Task 1), the existing `window.apiClient.getSpendingSummary(days)`, the existing `renderDashboardTopCategories(byCategoryEur)` (unchanged, still renders into `#dashTopCategoriesArea`).
- Produces: `window.loadDashboardSpending()` — replaces `window.loadDashboardTopCategories` as the function `loadDashboardPage()` calls.

- [ ] **Step 1: Update the Dashboard card's HTML**

In `web_client/index.html`, find this exact block:

```html
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
```

Replace it with:

```html
                        <!-- Spending: Spent/Income/Transferred summary + top categories -->
                        <div class="col-12 col-md-6">
                            <div class="card h-100">
                                <div class="card-header d-flex justify-content-between align-items-center">
                                    <span><i class="bi bi-wallet2 me-2"></i>Spending</span>
                                    <select class="form-select form-select-sm" id="dashSpendingPeriod" style="width:auto;" title="Time frame">
                                        <option value="7">Last 7 days</option>
                                        <option value="30" selected>Last 30 days</option>
                                        <option value="90">Last 90 days</option>
                                        <option value="365">Last 365 days</option>
                                    </select>
                                </div>
                                <div class="card-body">
                                    <div class="d-flex justify-content-around text-center mb-3 pb-2 border-bottom" id="dashSpendingStatsArea">
                                        <div class="text-muted small">Loading…</div>
                                    </div>
                                    <div id="dashTopCategoriesArea">
                                        <div class="text-muted small text-center py-3">
                                            <div class="spinner-border spinner-border-sm mb-1" role="status"></div><br>Loading…
                                        </div>
                                    </div>
                                </div>
                                <div class="card-footer bg-transparent text-end">
                                    <a href="#" class="small" onclick="window.navigationManager.showPage('spending'); const t = document.getElementById('spTabBtnCategories'); if (t) t.click(); return false;">View all <i class="bi bi-arrow-right"></i></a>
                                </div>
                            </div>
                        </div>
```

(`#dashTopCategoriesArea`'s id is unchanged — the existing `renderDashboardTopCategories()` from the prior feature still targets it correctly, no changes needed to that function.)

- [ ] **Step 2: Add the stats renderer and merge into a renamed loader**

In `web_client/js/pfm_features.js`, find this exact block:

```javascript
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

Replace it with:

```javascript
// Compact Spent/Income/Transferred row above the category bars — same
// summary object as the bars below, so one fetch drives both.
function renderDashboardSpendingStats(summary) {
    const area = document.getElementById('dashSpendingStatsArea');
    if (!area) return;
    const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
    area.innerHTML = `
        <div>
            <div class="small text-muted">Spent</div>
            <div class="fw-bold text-danger">${eur(summary.spent_eur)}</div>
        </div>
        <div>
            <div class="small text-muted">Income</div>
            <div class="fw-bold text-success">${eur(summary.income_eur)}</div>
        </div>
        <div>
            <div class="small text-muted">Transferred</div>
            <div class="fw-bold">${eur(summary.transferred_eur)}</div>
        </div>
    `;
}

async function loadDashboardSpending() {
    const statsArea = document.getElementById('dashSpendingStatsArea');
    const catArea = document.getElementById('dashTopCategoriesArea');
    if (!statsArea && !catArea) return;

    const periodSel = document.getElementById('dashSpendingPeriod');
    if (periodSel) {
        periodSel.value = String(getSpendingPeriodDays());
        if (!periodSel.dataset.wired) {
            periodSel.dataset.wired = '1';
            periodSel.addEventListener('change', () => {
                setSpendingPeriodDays(parseInt(periodSel.value, 10));
                loadDashboardSpending();
            });
        }
    }

    try {
        const summary = await window.apiClient.getSpendingSummary(getSpendingPeriodDays());
        renderDashboardSpendingStats(summary);
        renderDashboardTopCategories(summary.by_category_eur || {});
    } catch (e) {
        if (statsArea) statsArea.innerHTML = '<p class="text-danger small mb-0">Could not load spending data.</p>';
        if (catArea) catArea.innerHTML = '';
    }
}
window.loadDashboardSpending = loadDashboardSpending;
```

- [ ] **Step 3: Update the Dashboard's wiring call site**

In `web_client/js/pfm_pages.js`, find:

```javascript
            // Wealth Simulator live preview + Bank Accounts + Top Spending
            // Categories — independent, non-blocking (a failure in any one
            // must not blank the rest of the dashboard).
            try { if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue); }
            catch (e) { console.error('Forecast preview failed:', e); }
            if (window.loadDashboardBankAccounts) window.loadDashboardBankAccounts();
            if (window.loadDashboardTopCategories) window.loadDashboardTopCategories();
```

Replace it with:

```javascript
            // Wealth Simulator live preview + Bank Accounts + Spending
            // summary — independent, non-blocking (a failure in any one
            // must not blank the rest of the dashboard).
            try { if (window.loadDashboardForecastPreview) window.loadDashboardForecastPreview(totalValue); }
            catch (e) { console.error('Forecast preview failed:', e); }
            if (window.loadDashboardBankAccounts) window.loadDashboardBankAccounts();
            if (window.loadDashboardSpending) window.loadDashboardSpending();
```

- [ ] **Step 4: Manually verify in the browser**

Run: `make dev`

1. Open the Dashboard. Confirm the card previously titled "Top Spending Categories" now reads "Spending", shows a period `<select>` in its header (defaulting to "Last 30 days"), a Spent/Income/Transferred stat row, and the category bars below it (unchanged from before).
2. Compare the stat row's figures against the Spending page's own summary cards for the same period — should match exactly (same `getSpendingSummary(days)` source).
3. Switch the Dashboard card's period selector to "Last 90 days". Confirm both the stat row and the category bars update together.
4. Navigate to the Spending page. Confirm its own period selector now shows "Last 90 days" too (picked up from the same persisted `pfmSpendingSummaryDays` key) and its cards/chart match the Dashboard's 90-day figures.
5. Reverse the test: change the period on the Spending page, return to the Dashboard, confirm the Dashboard's Spending card picks up the new value on its next load.
6. Confirm the "View all →" footer link still navigates to Spending → Categories tab correctly.
7. Test the empty-category state (an account with no spending in the selected period): confirm the category-bars area still falls back to "No spending imported yet." as before, independent of the stat row above it.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js web_client/js/pfm_pages.js
git commit -m "feat: merge Spent/Income/Transferred into the Dashboard's Spending card"
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md` (Dashboard paragraph; Spending Tracking section if it references the old card name)
- Modify: `PROJECT_STATUS.md` (new version entry)

- [ ] **Step 1: Update CLAUDE.md's Dashboard paragraph**

In `CLAUDE.md`, find:

```
**Dashboard** (`pfm_pages.js`, `loadDashboardPage`) also renders two independent, non-blocking cards alongside the KPI/positions/donut/simulator content: **Bank Accounts** (`renderDashboardBankAccounts` in `pfm_analytics.js`, same `getNetworth().bank_accounts` source as the Net Worth page's card) and **Top Spending Categories** (`renderDashboardTopCategories` in `pfm_features.js`, top 5 from `getSpendingSummary(30).by_category_eur`, plain Bootstrap progress bars rather than Chart.js). Both degrade to an empty-state message independently of each other and of the simulator preview.
```

Replace it with:

```
**Dashboard** (`pfm_pages.js`, `loadDashboardPage`) also renders two independent, non-blocking cards alongside the KPI/positions/donut/simulator content: **Bank Accounts** (`renderDashboardBankAccounts` in `pfm_analytics.js`, same `getNetworth().bank_accounts` source as the Net Worth page's card) and **Spending** (`loadDashboardSpending` in `pfm_features.js` — a Spent/Income/Transferred stat row via `renderDashboardSpendingStats` plus the top-5-categories bars via `renderDashboardTopCategories`, both from one `getSpendingSummary(days)` call, plain Bootstrap progress bars rather than Chart.js). Both cards degrade to an empty-state message independently of each other and of the simulator preview. The Spending card's time frame (7/30/90/365 days) is shared with the Spending page's own period selector via `localStorage['pfmSpendingSummaryDays']` (read/written through `getSpendingPeriodDays()`/`setSpendingPeriodDays()`) — picking a period on either page is reflected on the other the next time it loads. The Net Worth page's own "Actual (last 30 days)" comparison widget is separate and stays fixed at 30 days.
```

- [ ] **Step 2: Add a PROJECT_STATUS.md entry**

In `PROJECT_STATUS.md`, find:

```
Last updated: 2026-07-23

**Recent (v2.5.33):**
```

Replace it with:

```
Last updated: 2026-07-23

**Recent (v2.5.34):** **Spending: selectable time frame + Dashboard spending summary.** The Spending page's Spent/Income/Transferred cards (and the Categories tab's chart, which shares the same underlying data) were hardcoded to a 30-day window — a new "Period" selector (7/30/90/365 days) now drives both, persisted via `localStorage['pfmSpendingSummaryDays']`. The Dashboard's "Top Spending Categories" card is renamed **Spending**, gains its own inline period selector kept in sync with the Spending page's choice via the same key, and now shows a compact Spent/Income/Transferred stat row above the existing category bars — both from one shared `getSpendingSummary(days)` call.

**Recent (v2.5.33):**
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document the spending time-frame selector and Dashboard Spending card"
```
