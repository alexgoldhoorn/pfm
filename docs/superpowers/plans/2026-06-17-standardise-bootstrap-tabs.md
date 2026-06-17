# Standardise Bootstrap Nav-Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom btn-group tab pattern on Analytics and Import/Export with Bootstrap's native `nav-tabs` style (already used on Diagnostics and Help), and document the standard pattern.

**Architecture:** Each page's tab bar becomes a `<ul class="nav nav-tabs">` whose `<button data-bs-toggle="tab">` elements activate `<div class="tab-pane">` containers. Lazy loading uses `shown.bs.tab` events instead of custom show/hide JS. The custom `showAnalyticsTab`/`setupAnalyticsTabs` and `showImportExportTab`/`setupImportExportTabs` functions are replaced with a slimmer Bootstrap-event-driven approach that follows the Diagnostics page pattern exactly.

**Tech Stack:** Vanilla JS, Bootstrap 5.3 (`window.bootstrap.Tab`), no build step.

---

### Task 1: Write the web UI patterns design doc

**Files:**
- Create: `docs/web-ui-patterns.md`

- [ ] **Step 1: Create the design doc**

Create `docs/web-ui-patterns.md` with the content below. This is the canonical reference for all future tab implementations in the app.

```markdown
# Web UI Patterns

Design reference for the web client (`web_client/`). Follow these patterns for all new pages and components.

## Tab Navigation

Use Bootstrap 5 `nav-tabs` for all tabbed pages. This matches the Diagnostics and Help pages.

### Standard markup

```html
<!-- Tab bar -->
<ul class="nav nav-tabs mb-3" id="fooTabs">
    <li class="nav-item">
        <button type="button" class="nav-link active"
                data-bs-toggle="tab" data-bs-target="#fooTabFirst"
                id="fooTabBtnFirst">
            <i class="bi bi-icon me-1"></i>First
        </button>
    </li>
    <li class="nav-item">
        <button type="button" class="nav-link"
                data-bs-toggle="tab" data-bs-target="#fooTabSecond"
                id="fooTabBtnSecond">
            <i class="bi bi-icon me-1"></i>Second
        </button>
    </li>
</ul>

<!-- Tab panes -->
<div class="tab-content">
    <div class="tab-pane fade show active" id="fooTabFirst">
        <!-- content -->
    </div>
    <div class="tab-pane fade" id="fooTabSecond">
        <!-- content -->
    </div>
</div>
```

Bootstrap handles show/hide natively — no custom CSS or JS needed for the toggle itself.

### ID naming convention

| Element | Pattern | Example |
|---------|---------|---------|
| Tab bar `ul` | `{page}Tabs` | `#analyticsTabs` |
| Tab button | `{page}TabBtn{Name}` | `#anTabBtnPerformance` |
| Tab pane | `{page}Tab{Name}` | `#anTabPerformance` |

### Lazy loading

When a tab's content is expensive to fetch, load it on first activation using the `shown.bs.tab` event:

```javascript
function setupFooTabs() {
    const secondBtn = document.getElementById('fooTabBtnSecond');
    if (secondBtn && !secondBtn._fooWired) {
        secondBtn._fooWired = true;
        // shown.bs.tab fires after Bootstrap shows the pane (after fade)
        secondBtn.addEventListener('shown.bs.tab', () => {
            if (!_fooSecondLoaded) { _fooSecondLoaded = true; loadFooSecond(); }
        });
        // Also hook click with a short delay — guards against Bootstrap
        // animation timing issues (same pattern as Diagnostics page)
        secondBtn.addEventListener('click', () =>
            setTimeout(() => {
                if (!_fooSecondLoaded) { _fooSecondLoaded = true; loadFooSecond(); }
            }, 50));
    }
    _fooSecondLoaded = false;
    // Reset to default tab on each page visit
    const firstBtn = document.getElementById('fooTabBtnFirst');
    if (firstBtn && window.bootstrap) {
        const pane = document.getElementById('fooTabFirst');
        if (!pane || !pane.classList.contains('active')) {
            new window.bootstrap.Tab(firstBtn).show();
        }
    }
}
```

### Per-visit state reset

Pages that need fresh data each time they are opened should expose a reset function:

```javascript
// At the end of setupFooPage():
window.loadFooPage = () => setupFooTabs();
```

Wire it in the navigation switch in `pfm_features.js`:

```javascript
case 'foo': if (window.loadFooPage) window.loadFooPage(); break;
```

### Programmatic tab activation

To activate a tab via JS (e.g., restore last-used tab from `localStorage`):

```javascript
const pane = document.getElementById('fooTabSecond');
if (pane && pane.classList.contains('active')) {
    // Pane already visible — shown.bs.tab won't fire, call loader directly
    loadFooSecond();
} else {
    new window.bootstrap.Tab(document.getElementById('fooTabBtnSecond')).show();
    // shown.bs.tab fires → loader runs via listener
}
```

### Don't use

- `<div class="d-flex flex-wrap gap-1">` with `btn btn-sm btn-outline-secondary` buttons — this is the old pattern, replaced by `nav-tabs`.
- Custom `data-*-tab` / `data-*-section` attributes with manual `style.display` toggling.
- `data-bs-toggle="pill"` — reserved for the login modal only.
```

- [ ] **Step 2: Commit**

```bash
git add docs/web-ui-patterns.md
git commit -m "docs: add web UI patterns doc — standard Bootstrap nav-tabs pattern"
```

---

### Task 2: Migrate Analytics page to Bootstrap nav-tabs

**Files:**
- Modify: `web_client/index.html:826–1017` (Analytics page section)
- Modify: `web_client/js/pfm_analytics.js:972–1017` (tab globals + functions)

#### HTML changes

The Analytics section currently has a flat list of cards with `data-an-section` attributes shown/hidden by custom JS. The new structure wraps each group in a `<div class="tab-pane">` and Bootstrap drives visibility.

- [ ] **Step 1: Replace the tab bar**

In `index.html`, find and replace the entire tab bar block (≈ lines 827–835):

**Find:**
```html
                    <!-- Sub-navigation: each tab lazy-loads only its own sections -->
                    <div class="d-flex flex-wrap gap-1 mb-3" id="analyticsTabs">
                        <button type="button" class="btn btn-sm btn-outline-secondary active" data-an-tab="performance"><i class="bi bi-speedometer2 me-1"></i>Performance &amp; Net Worth</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="dividends"><i class="bi bi-cash-coin me-1"></i>Dividends</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="gainloss"><i class="bi bi-trophy me-1"></i>Gain / Loss</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="tax"><i class="bi bi-receipt me-1"></i>Tax</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="risk"><i class="bi bi-shield-check me-1"></i>Risk &amp; Diversification</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="fees"><i class="bi bi-cash-stack me-1"></i>Fees &amp; Costs</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="stress"><i class="bi bi-lightning-charge me-1"></i>Stress Test</button>
                    </div>
```

**Replace with:**
```html
                    <!-- Sub-navigation: each tab lazy-loads only its own sections -->
                    <ul class="nav nav-tabs mb-3" id="analyticsTabs">
                        <li class="nav-item">
                            <button type="button" class="nav-link active" data-bs-toggle="tab" data-bs-target="#anPanePerformance" id="anTabPerformance"><i class="bi bi-speedometer2 me-1"></i>Performance &amp; Net Worth</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#anPaneDividends" id="anTabDividends"><i class="bi bi-cash-coin me-1"></i>Dividends</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#anPaneGainLoss" id="anTabGainLoss"><i class="bi bi-trophy me-1"></i>Gain / Loss</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#anPaneTax" id="anTabTax"><i class="bi bi-receipt me-1"></i>Tax</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#anPaneRisk" id="anTabRisk"><i class="bi bi-shield-check me-1"></i>Risk &amp; Diversification</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#anPaneFees" id="anTabFees"><i class="bi bi-cash-stack me-1"></i>Fees &amp; Costs</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#anPaneStress" id="anTabStress"><i class="bi bi-lightning-charge me-1"></i>Stress Test</button>
                        </li>
                    </ul>
```

- [ ] **Step 2: Wrap all cards in tab-content + tab-panes**

Immediately after the new `</ul>`, add `<div class="tab-content">`. Then wrap each section group in a tab-pane. The cards themselves do NOT change (keep all inner HTML exactly as-is); only their outer wrappers change.

**Find** the block from the first `<!-- a) Performance -->` comment through the closing `</div>` of `#analyticsPage` (≈ lines 837–1017):

```html
                    <!-- a) Performance -->
                    <div class="card mb-4" data-an-section="performance">
```
...all the way to...
```html
                    </div>
                </div>
```
(where the inner `</div>` closes `#analyticsPage` and the outer closes `#mainContent`)

**Replace the entire block** with:

```html
                    <div class="tab-content">

                        <div class="tab-pane fade show active" id="anPanePerformance">
                            <!-- a) Performance -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center flex-wrap gap-2">
                                    <span><i class="bi bi-speedometer2 me-2 text-primary"></i>Performance</span>
                                    <div class="d-flex align-items-center gap-2 flex-wrap">
                                        <div class="btn-group btn-group-sm" role="group" aria-label="Return period" id="anPeriodGroup">
                                            <input type="radio" class="btn-check" name="anPeriod" id="anPeriodAll" value="all" autocomplete="off" checked>
                                            <label class="btn btn-outline-secondary" for="anPeriodAll">All</label>
                                            <input type="radio" class="btn-check" name="anPeriod" id="anPeriodYtd" value="ytd" autocomplete="off">
                                            <label class="btn btn-outline-secondary" for="anPeriodYtd">YTD</label>
                                            <input type="radio" class="btn-check" name="anPeriod" id="anPeriod1y" value="1y" autocomplete="off">
                                            <label class="btn btn-outline-secondary" for="anPeriod1y">1Y</label>
                                            <input type="radio" class="btn-check" name="anPeriod" id="anPeriod1m" value="1m" autocomplete="off">
                                            <label class="btn btn-outline-secondary" for="anPeriod1m">1M</label>
                                        </div>
                                        <label class="small text-muted mb-0" for="anBenchmark">Benchmark</label>
                                        <select class="form-select form-select-sm" id="anBenchmark" style="width:auto;">
                                            <option value="^GSPC">S&amp;P 500 (^GSPC)</option>
                                            <option value="^IXIC">NASDAQ (^IXIC)</option>
                                            <option value="URTH">MSCI World (URTH)</option>
                                            <option value="^STOXX50E">Euro Stoxx 50 (^STOXX50E)</option>
                                            <option value="^AEX">AEX — Amsterdam (^AEX)</option>
                                            <option value="^IBEX">IBEX 35 — Madrid (^IBEX)</option>
                                            <option value="^FCHI">CAC 40 — Paris (^FCHI)</option>
                                            <option value="^FTSE">FTSE 100 — London (^FTSE)</option>
                                            <option value="^GDAXI">DAX — Frankfurt (^GDAXI)</option>
                                        </select>
                                    </div>
                                </div>
                                <div class="card-body" id="anPerformanceBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>
                                        Loading performance (fetching benchmark, may take a few seconds)…
                                    </div>
                                </div>
                            </div>

                            <!-- b) Net worth over time -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center flex-wrap gap-2">
                                    <span><i class="bi bi-graph-up me-2 text-primary"></i>Net Worth Over Time</span>
                                    <div class="d-flex gap-3 align-items-center" style="font-size:0.8rem;">
                                        <span><svg width="18" height="3"><line x1="0" y1="1.5" x2="18" y2="1.5" stroke="#2563eb" stroke-width="2.5"/></svg> Value</span>
                                        <span><svg width="18" height="3"><line x1="0" y1="1.5" x2="18" y2="1.5" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4 3"/></svg> Invested</span>
                                        <button class="btn btn-sm btn-outline-secondary" id="anBackfillBtn" title="Reconstruct daily history from your transactions + historical prices (back to your first transaction)">
                                            <i class="bi bi-clock-history me-1"></i>Backfill history
                                        </button>
                                    </div>
                                </div>
                                <div class="card-body">
                                    <div id="anNetworthContainer" style="position:relative; width:100%; min-height:300px;">
                                        <div class="align-items-center justify-content-center text-muted" id="anNetworthPlaceholder" style="min-height:300px; display:flex;">
                                            <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                        </div>
                                        <svg id="anNetworthSvg" width="100%" style="display:none; overflow:visible;"></svg>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="anPaneDividends">
                            <!-- c) Dividend income -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-cash-coin me-2 text-success"></i>Dividend Income
                                </div>
                                <div class="card-body" id="anDividendsBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="anPaneGainLoss">
                            <!-- d) Gain / Loss leaderboard -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-trophy me-2 text-warning"></i>Gain / Loss leaders
                                </div>
                                <div class="card-body" id="anGainLossBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="anPaneTax">
                            <div class="card mb-4">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center flex-wrap gap-2">
                                    <span><i class="bi bi-receipt me-2 text-danger"></i>Tax Estimate (Spanish IRPF)
                                        <i class="bi bi-info-circle text-muted ms-1" style="cursor:help;" data-bs-toggle="tooltip" title="Sums FIFO realised gains + dividends + interest received this year (base del ahorro) and applies the progressive savings-base brackets: 19% up to €6k, 21% €6k–€50k, 23% €50k–€200k, 27% €200k–€300k, 28% above €300k. An estimate — consult a tax adviser."></i>
                                    </span>
                                    <div class="d-flex align-items-center gap-2">
                                        <label class="small text-muted mb-0" for="anTaxYear">Year</label>
                                        <select class="form-select form-select-sm" id="anTaxYear" style="width:auto;"></select>
                                    </div>
                                </div>
                                <div class="card-body" id="anTaxBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>

                            <!-- Detailed tax report (per-lot FIFO + dividend withholding) -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center flex-wrap gap-2">
                                    <span><i class="bi bi-file-earmark-spreadsheet me-2 text-secondary"></i>Detailed tax report
                                        <i class="bi bi-info-circle text-muted" style="cursor:help;" data-bs-toggle="tooltip" title="Per-lot realised gains (FIFO) for the selected year plus dividend gross income and withholding tax already paid at source — for your IRPF filing."></i>
                                    </span>
                                    <button class="btn btn-sm btn-outline-secondary" id="anTaxReportCsvBtn"><i class="bi bi-download me-1"></i>Download CSV</button>
                                </div>
                                <div class="card-body" id="anTaxReportBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>

                            <!-- Year-end tax optimizer -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-scissors me-2 text-success"></i>Year-end tax optimizer
                                    <i class="bi bi-info-circle text-muted" style="cursor:help;" data-bs-toggle="tooltip" title="Estimates the tax you could save by harvesting unrealised losses before year-end to offset realised gains and income. Flags positions you bought in the last 60 days (Spain's 2-month rule). Informational, not advice."></i>
                                </div>
                                <div class="card-body" id="anTaxOptimizerBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="anPaneRisk">
                            <!-- e) Diversification & concentration -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center flex-wrap gap-2">
                                    <span><i class="bi bi-pie-chart-fill me-2 text-primary"></i>Diversification &amp; Concentration</span>
                                    <button class="btn btn-sm btn-outline-primary" id="anDiversificationBtn" title="Fetches sector/country data from Yahoo Finance — takes ~20-30s">
                                        <i class="bi bi-arrow-clockwise me-1"></i>Load
                                    </button>
                                </div>
                                <div class="card-body" id="anDiversificationBody">
                                    <div class="text-center text-muted py-4">
                                        Sector / country breakdown is fetched live from Yahoo Finance and takes ~20-30s.
                                        <div class="mt-2"><button class="btn btn-sm btn-primary" id="anDiversificationLoadInline"><i class="bi bi-pie-chart me-1"></i>Load diversification</button></div>
                                    </div>
                                </div>
                            </div>

                            <!-- f) Risk metrics -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-shield-exclamation me-2 text-danger"></i>Risk
                                </div>
                                <div class="card-body" id="anRiskBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="anPaneFees">
                            <!-- g) Fees & costs -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-cash-stack me-2 text-warning"></i>Fees &amp; Costs
                                </div>
                                <div class="card-body" id="anFeesBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading…
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="tab-pane fade" id="anPaneStress">
                            <!-- h) Stress Test -->
                            <div class="card mb-4">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-lightning-charge me-2 text-danger"></i>Stress Testing
                                </div>
                                <div class="card-body" id="anStressBody">
                                    <div class="text-center text-muted py-4">
                                        <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading&hellip;
                                    </div>
                                </div>
                            </div>
                        </div>

                    </div><!-- /.tab-content -->
                </div><!-- /#analyticsPage -->
```

- [ ] **Step 3: Run JS smoke test**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv make test-js
```

Expected: 20/20 pass. This catches malformed HTML that breaks the JS load.

- [ ] **Step 4: Commit HTML**

```bash
git add web_client/index.html
git commit -m "feat: migrate Analytics page to Bootstrap nav-tabs"
```

#### JS changes

- [ ] **Step 5: Replace the custom tab globals and functions in `pfm_analytics.js`**

Find and replace the block from `// Analytics sub-navigation` through the end of `setupAnalyticsTabs()` (≈ lines 972–1017):

**Find:**
```javascript
// Analytics sub-navigation: show one tab's sections at a time and lazy-load
// each tab's data on first activation. Loaders per tab reuse the existing
// section render functions.
const _ANALYTICS_LOADERS = {
    performance: () => { loadAnalyticsPerformance(); loadAnalyticsNetworth(); },
    dividends: () => { loadAnalyticsDividends(); },
    gainloss: () => { loadAnalyticsGainLoss(); },
    tax: () => { loadAnalyticsTax(); loadAnalyticsTaxReport(); loadTaxOptimizer(); },
    risk: () => { loadAnalyticsRisk(); _wireDiversificationButtons(); },
    fees: () => { loadAnalyticsFees(); },
    stress: () => { loadAnalyticsStress('2008'); },
};
let _analyticsLoaded = {};
let _analyticsActiveTab = 'performance';

function showAnalyticsTab(tab, forceReload) {
    _analyticsActiveTab = tab;
    // Toggle nav pill active state
    document.querySelectorAll('#analyticsTabs [data-an-tab]').forEach(b =>
        b.classList.toggle('active', b.dataset.anTab === tab));
    // Show only this tab's section cards
    document.querySelectorAll('#analyticsPage [data-an-section]').forEach(card => {
        card.style.display = card.dataset.anSection === tab ? '' : 'none';
    });
    // Lazy-load (once) unless a refresh is forced
    if (forceReload || !_analyticsLoaded[tab]) {
        _analyticsLoaded[tab] = true;
        (_ANALYTICS_LOADERS[tab] || (() => {}))();
    }
}

function setupAnalyticsTabs() {
    const tabs = document.getElementById('analyticsTabs');
    if (tabs && !tabs.dataset.wired) {
        tabs.dataset.wired = '1';
        tabs.querySelectorAll('[data-an-tab]').forEach(btn => {
            btn.addEventListener('click', () => showAnalyticsTab(btn.dataset.anTab));
        });
        // Note: the #refreshAnalytics button calls loadAnalyticsPage() (which
        // re-enters here), so refresh is handled by the reset below — no extra
        // listener needed.
    }
    // Reset load state each time the page opens so data stays fresh per visit
    _analyticsLoaded = {};
    showAnalyticsTab(_analyticsActiveTab || 'performance');
}
```

**Replace with:**
```javascript
// Analytics tab map: button id → { key for state tracking, loader function }
const _AN_TAB_MAP = {
    anTabPerformance: { key: 'performance', loader: () => { loadAnalyticsPerformance(); loadAnalyticsNetworth(); } },
    anTabDividends:   { key: 'dividends',   loader: () => { loadAnalyticsDividends(); } },
    anTabGainLoss:    { key: 'gainloss',    loader: () => { loadAnalyticsGainLoss(); } },
    anTabTax:         { key: 'tax',         loader: () => { loadAnalyticsTax(); loadAnalyticsTaxReport(); loadTaxOptimizer(); } },
    anTabRisk:        { key: 'risk',        loader: () => { loadAnalyticsRisk(); _wireDiversificationButtons(); } },
    anTabFees:        { key: 'fees',        loader: () => { loadAnalyticsFees(); } },
    anTabStress:      { key: 'stress',      loader: () => { loadAnalyticsStress('2008'); } },
};
let _analyticsLoaded = {};
let _analyticsActiveTab = 'performance';

function setupAnalyticsTabs() {
    const tabs = document.getElementById('analyticsTabs');
    if (tabs && !tabs.dataset.wired) {
        tabs.dataset.wired = '1';
        // Wire shown.bs.tab on each button; Bootstrap fires this after the pane fades in.
        Object.entries(_AN_TAB_MAP).forEach(([btnId, { key, loader }]) => {
            const btn = document.getElementById(btnId);
            if (!btn) return;
            btn.addEventListener('shown.bs.tab', () => {
                _analyticsActiveTab = key;
                if (!_analyticsLoaded[key]) {
                    _analyticsLoaded[key] = true;
                    loader();
                }
            });
        });
    }
    // Reset per-visit so each page open gets fresh data
    _analyticsLoaded = {};
    // Restore last active tab (or performance)
    const targetBtnId = (Object.entries(_AN_TAB_MAP)
        .find(([, { key }]) => key === _analyticsActiveTab) || ['anTabPerformance'])[0];
    const targetBtn = document.getElementById(targetBtnId);
    if (targetBtn && window.bootstrap) {
        const paneId = targetBtn.dataset.bsTarget;
        const pane = paneId && document.querySelector(paneId);
        if (pane && pane.classList.contains('active')) {
            // Pane already shown — shown.bs.tab won't fire; trigger loader directly
            const { key, loader } = _AN_TAB_MAP[targetBtnId];
            _analyticsLoaded[key] = true;
            loader();
        } else {
            new window.bootstrap.Tab(targetBtn).show();
            // shown.bs.tab fires → loader runs via the listener above
        }
    }
}
```

- [ ] **Step 6: Run JS smoke test**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv make test-js
```

Expected: 20/20 pass.

- [ ] **Step 7: Commit JS**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: replace Analytics custom tab JS with Bootstrap shown.bs.tab events"
```

---

### Task 3: Migrate Import/Export page to Bootstrap nav-tabs

**Files:**
- Modify: `web_client/index.html:1752–1997` (Import/Export page section)
- Modify: `web_client/js/pfm_features.js` (inside `setupImportExportPage`)

#### HTML changes

The current structure has `<div class="row g-4">` containing all six card wrappers tagged with `data-io-section`. The new structure uses three `tab-pane` divs, each containing its own `<div class="row g-4">` with the relevant cards.

- [ ] **Step 1: Replace tab bar and restructure cards into panes**

In `index.html`, find the entire block from the tab bar through the end of the row (≈ lines 1752–1997):

**Find:**
```html
                    <div class="d-flex flex-wrap gap-1 mb-3" id="ioTabs">
                        <button type="button" class="btn btn-sm btn-outline-secondary active" data-io-tab="import"><i class="bi bi-file-earmark-arrow-up me-1"></i>Import</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-io-tab="export"><i class="bi bi-file-earmark-arrow-down me-1"></i>Export</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" data-io-tab="data"><i class="bi bi-bank me-1"></i>Data</button>
                    </div>
                    <div class="row g-4">
                        <!-- File Import -->
                        <div class="col-12 col-lg-6" data-io-section="import">
```

(this continues through all six cards to the closing `</div>` of the row at ≈ line 1997)

**Replace** the tab bar + entire row with the tab-content structure below. The card bodies are unchanged — only the surrounding structure changes.

```html
                    <ul class="nav nav-tabs mb-3" id="ioTabs">
                        <li class="nav-item">
                            <button type="button" class="nav-link active" data-bs-toggle="tab" data-bs-target="#ioTabImport" id="ioTabBtnImport"><i class="bi bi-file-earmark-arrow-up me-1"></i>Import</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#ioTabExport" id="ioTabBtnExport"><i class="bi bi-file-earmark-arrow-down me-1"></i>Export</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#ioTabData" id="ioTabBtnData"><i class="bi bi-bank me-1"></i>Data</button>
                        </li>
                    </ul>
                    <div class="tab-content">

                        <div class="tab-pane fade show active" id="ioTabImport">
                            <div class="row g-4">
                                <!-- File Import -->
                                <div class="col-12 col-lg-6">
                                    <div class="card h-100">
                                        <div class="card-header fw-semibold">
                                            <i class="bi bi-file-earmark-arrow-up me-2"></i>Import from File
                                        </div>
                                        <div class="card-body d-flex flex-column">
                                            <div id="ioFileStep1">
                                                <div class="mb-3">
                                                    <label class="form-label" for="ioFileBroker">Broker / Format</label>
                                                    <select class="form-select" id="ioFileBroker">
                                                        <option value="">Select broker…</option>
                                                        <option value="indexacapital">IndexaCapital (CSV)</option>
                                                        <option value="myinvestor">MyInvestor (CSV)</option>
                                                        <option value="myinvestor_paste">MyInvestor (Paste)</option>
                                                        <option value="mintos">Mintos P2P (CSV)</option>
                                                        <option value="coinbase">Coinbase (CSV)</option>
                                                        <option value="pdt">Portfolio Dividend Tracker (XLSX)</option>
                                                        <option value="bookings">Cash deposits/withdrawals (CSV)</option>
                                                        <option value="deposits">Fixed Deposits (CSV)</option>
                                                    </select>
                                                </div>
                                                <div class="mb-2">
                                                    <select class="form-select form-select-sm" id="ioFilePortfolio">
                                                        <option value="">Portfolio — None / auto-detect by broker name</option>
                                                    </select>
                                                </div>
                                                <div id="ioFileHint" class="alert alert-info py-2 small" style="display:none;"></div>
                                                <div class="mb-3" id="ioFileInputWrap">
                                                    <label class="form-label" for="ioFileInput">File</label>
                                                    <input type="file" class="form-control" id="ioFileInput" accept=".csv,.xlsx,.xls">
                                                </div>
                                                <div class="mb-3" id="ioFilePasteWrap" style="display:none;">
                                                    <label class="form-label" for="ioFilePasteArea">Paste statement text</label>
                                                    <textarea class="form-control font-monospace" id="ioFilePasteArea" rows="8" placeholder="Paste your MyInvestor transaction history here…"></textarea>
                                                </div>
                                            </div>
                                            <div id="ioFileStep2" style="display:none;">
                                                <div id="ioFilePreview"></div>
                                            </div>
                                            <div class="d-flex gap-2 mt-auto pt-3">
                                                <button class="btn btn-sm btn-outline-secondary" id="ioFileBackBtn" style="display:none;">
                                                    <i class="bi bi-arrow-left me-1"></i>Back
                                                </button>
                                                <button class="btn btn-sm btn-primary" id="ioFileParseBtn">
                                                    <i class="bi bi-search me-1"></i>Parse File
                                                </button>
                                                <button class="btn btn-sm btn-success" id="ioFileSaveBtn" style="display:none;">
                                                    <i class="bi bi-check-lg me-1"></i>Save Selected
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- LLM Text Import -->
                                <div class="col-12 col-lg-6">
                                    <div class="card h-100">
                                        <div class="card-header fw-semibold">
                                            <i class="bi bi-magic me-2"></i>Import from Text (AI Extraction)
                                        </div>
                                        <div class="card-body d-flex flex-column">
                                            <p class="text-muted small mb-2">Paste a broker statement. Works with any broker not directly supported. Detects buy/sell trades <em>and</em> cash deposits/withdrawals.</p>
                                            <div id="ioTextStep1" class="flex-grow-1">
                                                <div class="mb-2">
                                                    <select class="form-select form-select-sm" id="ioTextPortfolio">
                                                        <option value="">Portfolio — None / auto-detect</option>
                                                    </select>
                                                </div>
                                                <textarea class="form-control font-monospace" id="ioTextarea" rows="8"
                                                    placeholder="Paste broker statement text here…"></textarea>
                                            </div>
                                            <div id="ioTextStep2" style="display:none;">
                                                <div id="ioTextPreview"></div>
                                            </div>
                                            <div class="d-flex gap-2 mt-auto pt-3">
                                                <button class="btn btn-sm btn-outline-secondary" id="ioTextBackBtn" style="display:none;">
                                                    <i class="bi bi-arrow-left me-1"></i>Back
                                                </button>
                                                <button class="btn btn-sm btn-primary" id="ioTextExtractBtn">
                                                    <i class="bi bi-magic me-1"></i>Extract
                                                </button>
                                                <button class="btn btn-sm btn-success" id="ioTextSaveBtn" style="display:none;">
                                                    <i class="bi bi-check-lg me-1"></i>Save Selected
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div><!-- /#ioTabImport -->

                        <div class="tab-pane fade" id="ioTabExport">
                            <div class="row g-4">
                                <!-- Export -->
                                <div class="col-12">
                                    <div class="card">
                                        <div class="card-header fw-semibold">
                                            <i class="bi bi-file-earmark-arrow-down me-2"></i>Export
                                        </div>
                                        <div class="card-body">
                                            <p class="text-muted small mb-3">Download all transactions. CSV is compatible with spreadsheets; PDT (XLSX) is the Portfolio Dividend Tracker format (includes bookings).</p>
                                            <div class="d-flex gap-3 flex-wrap">
                                                <button class="btn btn-sm btn-outline-primary" id="ioExportCsvBtn">
                                                    <i class="bi bi-filetype-csv me-2"></i>Export CSV
                                                </button>
                                                <button class="btn btn-sm btn-outline-primary" id="ioExportPdtBtn">
                                                    <i class="bi bi-file-earmark-spreadsheet me-2"></i>Export PDT (XLSX)
                                                </button>
                                                <button class="btn btn-sm btn-outline-secondary" id="ioExportBackupBtn" title="Download a consistent snapshot of the whole database (.db)">
                                                    <i class="bi bi-database-down me-2"></i>Download DB backup
                                                </button>
                                                <button class="btn btn-sm btn-outline-danger" id="ioRestoreBackupBtn" title="Upload a .db or .db.gz backup to replace the current database">
                                                    <i class="bi bi-database-up me-2"></i>Restore DB backup
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Platform Export -->
                                <div class="col-12">
                                    <div class="card">
                                        <div class="card-header fw-semibold">
                                            <i class="bi bi-cloud-upload me-2"></i>Platform Export
                                        </div>
                                        <div class="card-body">
                                            <p class="text-muted small mb-3">Export your portfolio for Yahoo Finance or Simply Wall St. Download the file and upload it to that platform&rsquo;s portfolio tracker.</p>
                                            <div class="row g-3 align-items-end">
                                                <div class="col-auto">
                                                    <label class="form-label small mb-1" for="platformExportSelect">Platform</label>
                                                    <select class="form-select form-select-sm" id="platformExportSelect">
                                                        <option value="yahoo-finance">Yahoo Finance</option>
                                                        <option value="simply-wall-st">Simply Wall St</option>
                                                    </select>
                                                </div>
                                                <div class="col-auto">
                                                    <label class="form-label small mb-1">Data</label>
                                                    <div class="d-flex gap-3">
                                                        <div class="form-check mb-0">
                                                            <input class="form-check-input" type="radio" name="platformExportMode" id="platformExportModeTransactions" value="transactions" checked>
                                                            <label class="form-check-label small" for="platformExportModeTransactions">Full transaction history</label>
                                                        </div>
                                                        <div class="form-check mb-0">
                                                            <input class="form-check-input" type="radio" name="platformExportMode" id="platformExportModePositions" value="positions">
                                                            <label class="form-check-label small" for="platformExportModePositions">Current positions only</label>
                                                        </div>
                                                    </div>
                                                </div>
                                                <div class="col-auto">
                                                    <label class="form-label small mb-1" for="platformExportPortfolio">Portfolio</label>
                                                    <select class="form-select form-select-sm" id="platformExportPortfolio">
                                                        <option value="">All portfolios</option>
                                                    </select>
                                                </div>
                                                <div class="col-auto">
                                                    <button class="btn btn-sm btn-outline-primary" id="platformExportBtn">
                                                        <i class="bi bi-download me-2"></i>Download
                                                    </button>
                                                </div>
                                            </div>
                                            <div id="platformExportWarning" class="alert alert-warning mt-3 mb-0 py-2 small d-none" role="alert"></div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div><!-- /#ioTabExport -->

                        <div class="tab-pane fade" id="ioTabData">
                            <div class="row g-4">
                                <!-- Bookings (cash transactions: deposits / withdrawals) -->
                                <div class="col-12">
                                    <div class="card">
                                        <div class="card-header fw-semibold d-flex justify-content-between align-items-center">
                                            <span><i class="bi bi-bank me-2"></i>Cash Transactions (Deposits &amp; Withdrawals)</span>
                                            <button class="btn btn-sm btn-outline-secondary" id="ioRefreshBookingsBtn" title="Refresh">
                                                <i class="bi bi-arrow-clockwise"></i>
                                            </button>
                                        </div>
                                        <div class="card-body">
                                            <!-- Add booking form -->
                                            <form id="addBookingForm" class="row g-2 align-items-end mb-3">
                                                <div class="col-6 col-sm-3 col-md-2">
                                                    <label class="form-label small mb-1">Date</label>
                                                    <input type="date" class="form-control form-control-sm" id="addBookingDate" required>
                                                </div>
                                                <div class="col-6 col-sm-3 col-md-2">
                                                    <label class="form-label small mb-1">Type</label>
                                                    <select class="form-select form-select-sm" id="addBookingAction">
                                                        <option value="Deposit">Deposit</option>
                                                        <option value="Withdrawal">Withdrawal</option>
                                                    </select>
                                                </div>
                                                <div class="col-6 col-sm-3 col-md-2">
                                                    <label class="form-label small mb-1">Amount</label>
                                                    <input type="number" class="form-control form-control-sm" id="addBookingAmount" min="0.01" step="0.01" placeholder="0.00" required>
                                                </div>
                                                <div class="col-4 col-sm-2 col-md-1">
                                                    <label class="form-label small mb-1">Currency</label>
                                                    <input type="text" class="form-control form-control-sm" id="addBookingCurrency" value="EUR" maxlength="3">
                                                </div>
                                                <div class="col-8 col-sm-4 col-md-3">
                                                    <label class="form-label small mb-1">Portfolio</label>
                                                    <select class="form-select form-select-sm" id="addBookingPortfolio">
                                                        <option value="">— None —</option>
                                                    </select>
                                                </div>
                                                <div class="col-6 col-sm-2 col-md-2">
                                                    <button type="submit" class="btn btn-sm btn-primary w-100">
                                                        <i class="bi bi-plus-lg me-1"></i>Add
                                                    </button>
                                                </div>
                                            </form>
                                            <!-- Bookings table -->
                                            <div id="ioBookingsTable" class="table-responsive" style="max-height:300px;">
                                                <p class="text-muted small mb-0">Loading…</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Google Sheets PDT Sync -->
                                <div class="col-12">
                                    <div class="card">
                                        <div class="card-header fw-semibold">
                                            <i class="bi bi-cloud-arrow-up-fill me-2 text-success"></i>Google Sheets — PDT Sync
                                        </div>
                                        <div class="card-body">
                                            <p class="text-muted small mb-3">
                                                Sync directly with a Google Spreadsheet in Portfolio Dividend Tracker format.
                                                The sheet must be shared with the service account email shown below.
                                            </p>
                                            <div id="syncConfigInfo" class="mb-3"></div>
                                            <div class="mb-3">
                                                <label class="form-label" for="syncSheetId">Spreadsheet ID
                                                    <span class="text-muted small">(the long ID from the URL: /spreadsheets/d/<strong>ID</strong>/)</span>
                                                </label>
                                                <input type="text" class="form-control font-monospace" id="syncSheetId"
                                                    placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms">
                                            </div>
                                            <div class="d-flex gap-3 flex-wrap">
                                                <button class="btn btn-sm btn-outline-success" id="syncPullBtn">
                                                    <i class="bi bi-cloud-arrow-down me-2"></i>Pull from Sheet
                                                </button>
                                                <button class="btn btn-sm btn-outline-primary" id="syncPushBtn">
                                                    <i class="bi bi-cloud-arrow-up me-2"></i>Push to Sheet
                                                </button>
                                            </div>
                                            <div id="syncStatus" class="mt-3"></div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div><!-- /#ioTabData -->

                    </div><!-- /.tab-content -->
```

- [ ] **Step 2: Run JS smoke test**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv make test-js
```

Expected: 20/20 pass.

- [ ] **Step 3: Commit HTML**

```bash
git add web_client/index.html
git commit -m "feat: migrate Import/Export page to Bootstrap nav-tabs"
```

#### JS changes (`pfm_features.js`)

The current `showImportExportTab` and `setupImportExportTabs` use manual `style.display` toggling on `[data-io-section]` elements. Replace them with a Bootstrap-event-driven approach identical in shape to `loadDiagnosticsPage`.

- [ ] **Step 4: Replace `showImportExportTab` + `setupImportExportTabs` inside `setupImportExportPage`**

Find the two functions and their call at the end of `setupImportExportPage` (≈ lines 1416–1443):

**Find:**
```javascript
    let _ioDataTabLoaded = false;

    function showImportExportTab(tab) {
        document.querySelectorAll('#ioTabs [data-io-tab]').forEach(b =>
            b.classList.toggle('active', b.dataset.ioTab === tab));
        document.querySelectorAll('#importexportPage [data-io-section]').forEach(card => {
            card.style.display = card.dataset.ioSection === tab ? '' : 'none';
        });
        if (tab === 'data' && !_ioDataTabLoaded) {
            _ioDataTabLoaded = true;
            loadBookings();
        }
    }

    function setupImportExportTabs() {
        const tabs = document.getElementById('ioTabs');
        if (tabs && !tabs.dataset.wired) {
            tabs.dataset.wired = '1';
            tabs.querySelectorAll('[data-io-tab]').forEach(btn => {
                btn.addEventListener('click', () => showImportExportTab(btn.dataset.ioTab));
            });
        }
        _ioDataTabLoaded = false;
        showImportExportTab('import');
    }

    setupImportExportTabs();
    window.loadImportExportPage = () => setupImportExportTabs();
```

**Replace with:**
```javascript
    let _ioDataTabLoaded = false;

    function _triggerDataTabLoad() {
        if (!_ioDataTabLoaded) { _ioDataTabLoaded = true; loadBookings(); }
    }

    function setupImportExportTabs() {
        const dataBtn = document.getElementById('ioTabBtnData');
        if (dataBtn && !dataBtn._ioWired) {
            dataBtn._ioWired = true;
            dataBtn.addEventListener('shown.bs.tab', _triggerDataTabLoad);
            // Also hook click with a short delay — guards against Bootstrap
            // animation timing (same pattern as Diagnostics page)
            dataBtn.addEventListener('click', () => setTimeout(_triggerDataTabLoad, 50));
        }
        _ioDataTabLoaded = false;
        // Reset to Import tab on each page visit
        const importBtn = document.getElementById('ioTabBtnImport');
        if (importBtn && window.bootstrap) {
            const pane = document.getElementById('ioTabImport');
            if (!pane || !pane.classList.contains('active')) {
                new window.bootstrap.Tab(importBtn).show();
            }
        }
    }

    setupImportExportTabs();
    window.loadImportExportPage = () => setupImportExportTabs();
```

- [ ] **Step 5: Run JS smoke test**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv make test-js
```

Expected: 20/20 pass.

- [ ] **Step 6: Commit JS**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: replace Import/Export custom tab JS with Bootstrap shown.bs.tab events"
```

---

### Task 4: Bump cache busters, redeploy, verify

**Files:**
- Modify: `web_client/index.html` (two `?v=` bumps)

- [ ] **Step 1: Bump version strings for both changed JS files**

In `index.html` (near the bottom, ≈ lines 3218–3219):

**Find:**
```html
    <script src="js/pfm_analytics.js?v=1780000054"></script>
    <script src="js/pfm_features.js?v=1780000058"></script>
```

**Replace with:**
```html
    <script src="js/pfm_analytics.js?v=1780000055"></script>
    <script src="js/pfm_features.js?v=1780000059"></script>
```

- [ ] **Step 2: Rebuild and redeploy the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Wait for the container to come up, then confirm:

```bash
docker ps | grep portf_web
```

Expected: container status `Up` and `(healthy)`.

- [ ] **Step 3: Manual verification checklist**

Open the app and verify:

**Analytics page:**
1. The tab bar shows seven underlined tabs (Bootstrap nav-tabs style, not pill buttons).
2. Default tab is Performance & Net Worth — both cards load (performance metrics + net worth chart).
3. Click **Dividends** — dividend table loads.
4. Click **Tax** — all three tax cards load (estimate, detailed report, optimizer).
5. Click **Risk & Diversification** — risk card loads, Load button appears for diversification.
6. Click **Fees & Costs** — fees card loads.
7. Click **Stress Test** — stress card loads.
8. Click **Refresh** — refreshes the current tab's data.
9. Navigate away (e.g. to Holdings) and back — lands on last active tab, data reloads.

**Import/Export page:**
1. Tab bar shows three underlined tabs (Import / Export / Data).
2. Default tab is Import — File Import and AI Text Import cards visible side by side.
3. Click **Export** — Export and Platform Export cards visible full-width.
4. Click **Data** — Bookings table loads, Google Sheets Sync card visible.
5. Refresh button inside Data tab still works.
6. Navigate away and back — resets to Import tab.

- [ ] **Step 4: Commit version bumps**

```bash
git add web_client/index.html
git commit -m "feat: bump cache busters for Analytics + Import/Export Bootstrap tabs migration"
```
