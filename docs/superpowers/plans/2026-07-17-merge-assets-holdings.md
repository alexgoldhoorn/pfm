# Merge Assets + Holdings Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the web client's separate "Holdings" and "Assets" nav pages into a single "Assets" page: owned positions (quantity/cost/P&L) by default, with a checkbox to also list catalog assets held nowhere.

**Architecture:** Frontend-only change in `web_client/`. No backend/API changes — `GET /api/v1/assets/` and `GET /api/v1/portfolios/holdings` are reused exactly as they are today. The merged page fetches both, joins rows client-side by `symbol`, and renders one table.

**Tech Stack:** Vanilla JS (no build step), Bootstrap 5, `web_client/js/pfm_pages.js` / `pfm_features.js` / `pfm_core.js` / `help_text.js`, `web_client/index.html`.

## Global Constraints

- No backend changes. `apiClient.getAssets()` and `apiClient.getHoldings(portfolioId)` keep their existing signatures (`portf_manager` / `portf_server` are out of scope for this plan).
- The surviving nav/page key is `assets` (`data-page="assets"`, container `#assetsPage`). The `holdings` key, `#holdingsPage` container, and `loadHoldingsPage()` are removed entirely.
- "Held anywhere" (for the zero-holding toggle) means quantity > 0 in *any* portfolio, independent of the currently selected portfolio filter — computed from `getHoldings()` called with no portfolio filter.
- The hide-tiny-position threshold (`PREFS.hideBelowEur`) applies only to owned rows; catalog-only (unowned) rows are never hidden by it.
- Reuse the existing `PREFS.tableState.holdings` key for the merged table's sort/filter state (not `.assets`), so an existing user's saved Holdings sort carries over unchanged.
- Manual price override (pencil icon) and "Resolve Tickers" (OpenFIGI) apply to every row, owned or not — this is existing Assets-page behavior, unchanged.
- Deploy step after `web_client/` edits: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web` (per `CLAUDE.md`).

---

### Task 1: Merge page markup + nav links

**Files:**
- Modify: `web_client/index.html:147` and `:212` (sidebar nav — remove the "Holdings" link, both copies)
- Modify: `web_client/index.html:517-590` (`#assetsPage` block — replaced with the merged markup)
- Modify: `web_client/index.html:696-855` (`#holdingsPage` block — deleted entirely)

**Interfaces:**
- Produces: `#assetsPage` container with element ids `assetTypeFilter`, `assetPortfolioFilter`, `assetSearchInput`, `assetSuggest`, `assetShowZeroHoldings` (new), `refreshAssets`, `resolveTickersBtn`, `assetsTable`, `holdingsTotalValue`, `holdingsTotalCost`, `holdingsTotalPnl`, `holdingsPnlCard`, `rebalanceTargetsForm`, `rebalanceTargetsRows`, `rebalanceTargetsTotal`, `rebalanceSaveBtn`, `rebalanceAnalysisTable`, `rebalanceActions`. These exact ids are consumed by Task 2 (`pfm_pages.js`) and by existing, unmodified rebalance-wiring code in `pfm_features.js` (`setupRebalanceForm()`).
- Consumes: nothing from other tasks.

- [ ] **Step 1: Remove the duplicated "Holdings" nav link**

Read `web_client/index.html` first (required before any Edit). Then remove both copies of the Holdings sidebar link in one call:

```
old_string (with replace_all: true):
"
                    <a class=\"sidebar-nav-link\" href=\"#\" data-page=\"holdings\"><i class=\"bi bi-wallet2 me-2\"></i>Holdings</a>"
new_string: ""
```

This deletes the line (and its leading newline) at both line 147 (offcanvas sidebar) and line 212 (desktop sidebar) — the two blocks are byte-identical, so `replace_all: true` removes both in one call. The "Assets" link two lines below each (`data-page="assets"`) is untouched.

- [ ] **Step 2: Replace the `#assetsPage` block with the merged markup**

Find and replace the entire existing `#assetsPage` div (starts `<div id="assetsPage" class="page-content" style="display: none;">`, ends with its matching `</div>` right before the `<!-- Transactions Page -->` comment) with:

```html
                <div id="assetsPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-coin me-2 text-primary"></i>Assets<button class="btn btn-sm btn-link p-0 ms-2 align-baseline" onclick="showPageHelp('assets')" title="What is this page?"><i class="bi bi-question-circle"></i></button></h4>
                            <p class="text-muted small mb-0">Current positions with cost basis, live price and P&amp;L, plus the full catalogue of tracked securities and funds. Prices update daily from Yahoo Finance; values in EUR.</p>
                        </div>
                        <button class="btn btn-primary btn-sm" data-bs-toggle="modal" data-bs-target="#addAssetModal">
                            <i class="bi bi-plus-lg me-1"></i>Add Asset
                        </button>
                    </div>

                    <!-- Summary Cards -->
                    <div class="row g-3 mb-4">
                        <div class="col-12 col-md-4">
                            <div class="card bg-primary text-white">
                                <div class="card-body py-3">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <div>
                                            <div class="small opacity-75 mb-1">Total Value</div>
                                            <div class="fs-4 fw-bold" id="holdingsTotalValue">—</div>
                                        </div>
                                        <i class="bi bi-cash-stack fs-2 opacity-50"></i>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-4">
                            <div class="card bg-secondary text-white">
                                <div class="card-body py-3">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <div>
                                            <div class="small opacity-75 mb-1">Total Cost</div>
                                            <div class="fs-4 fw-bold" id="holdingsTotalCost">—</div>
                                        </div>
                                        <i class="bi bi-wallet2 fs-2 opacity-50"></i>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-4">
                            <div class="card text-white" id="holdingsPnlCard">
                                <div class="card-body py-3">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <div>
                                            <div class="small opacity-75 mb-1">Total P/L</div>
                                            <div class="fs-4 fw-bold" id="holdingsTotalPnl">—</div>
                                        </div>
                                        <i class="bi bi-graph-up-arrow fs-2 opacity-50"></i>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Filters -->
                    <div class="row g-2 mb-3 align-items-center">
                        <div class="col-6 col-sm-2">
                            <select class="form-select form-select-sm" id="assetTypeFilter">
                                <option value="">All Asset Types</option>
                                <option value="stock">Stock</option>
                                <option value="bond">Bond</option>
                                <option value="etf">ETF</option>
                                <option value="index">Index fund</option>
                                <option value="crypto">Cryptocurrency</option>
                                <option value="commodity">Commodity</option>
                            </select>
                        </div>
                        <div class="col-6 col-sm-2">
                            <select class="form-select form-select-sm" id="assetPortfolioFilter">
                                <option value="">All Brokers</option>
                            </select>
                        </div>
                        <div class="col-12 col-sm-3 position-relative">
                            <input type="text" class="form-control form-control-sm" id="assetSearchInput" autocomplete="off" placeholder="Search by name, ticker or alias…">
                            <div id="assetSuggest" class="list-group position-absolute w-100 shadow-sm" style="z-index:1050; display:none; max-height:260px; overflow-y:auto;"></div>
                        </div>
                        <div class="col-8 col-sm-3">
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="assetShowZeroHoldings">
                                <label class="form-check-label small" for="assetShowZeroHoldings">Show assets with no holding</label>
                            </div>
                        </div>
                        <div class="col-2 col-sm-1">
                            <button class="btn btn-sm btn-outline-secondary w-100" id="refreshAssets" title="Refresh assets and holdings">
                                <i class="bi bi-arrow-clockwise"></i>
                            </button>
                        </div>
                        <div class="col-2 col-sm-1">
                            <button class="btn btn-sm btn-outline-info w-100" id="resolveTickersBtn" title="Auto-fill missing Yahoo Finance tickers for ISIN-keyed assets via OpenFIGI">
                                <i class="bi bi-magic"></i>
                            </button>
                        </div>
                    </div>

                    <!-- Assets Table -->
                    <div class="card">
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-hover mb-0" id="assetsTable">
                                    <thead>
                                        <tr>
                                            <th class="ps-3" data-key="symbol" data-type="text">Symbol</th>
                                            <th data-key="name" data-type="text">Name</th>
                                            <th data-key="asset_type" data-type="text">Type</th>
                                            <th data-key="exchange" data-type="text">Exchange</th>
                                            <th data-key="currency" data-type="text">Currency</th>
                                            <th class="text-end" data-key="quantity" data-type="num">Quantity</th>
                                            <th class="text-end" data-key="avg_price" data-type="num">Avg Price</th>
                                            <th class="text-end" data-key="current_price" data-type="num">Current Price</th>
                                            <th class="text-end" data-key="total_value_eur" data-type="num">Total Value</th>
                                            <th class="text-end" data-key="pnl_amount" data-type="num">P/L</th>
                                            <th class="text-end" data-key="pnl_pct" data-type="num">P/L %</th>
                                            <th class="text-center">Links</th>
                                            <th class="text-end pe-3">Research</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <tr>
                                            <td colspan="13" class="text-center py-4">
                                                <div class="spinner-border spinner-border-sm me-2" role="status"></div>
                                                Loading assets...
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- Rebalancing Card -->
                    <div class="card mt-4">
                        <div class="card-header fw-semibold" style="cursor:pointer;" data-bs-toggle="collapse" data-bs-target="#rebalanceBody" aria-expanded="false">
                            <i class="bi bi-sliders me-2 text-primary"></i>Rebalancing
                            <i class="bi bi-chevron-down float-end mt-1 small"></i>
                        </div>
                        <div class="collapse" id="rebalanceBody">
                            <div class="card-body">
                                <div class="row g-4">
                                    <!-- Target allocation form -->
                                    <div class="col-12 col-lg-5">
                                        <h6 class="fw-semibold mb-2"><i class="bi bi-bullseye me-2"></i>Target Allocation</h6>
                                        <p class="text-muted small mb-2">Set a target percentage for each asset type. Targets should sum to 100%.</p>
                                        <form id="rebalanceTargetsForm">
                                            <div id="rebalanceTargetsRows">
                                                <p class="text-muted small mb-0">Loading…</p>
                                            </div>
                                            <div class="d-flex justify-content-between align-items-center mt-2">
                                                <span class="small">Total: <strong id="rebalanceTargetsTotal">0%</strong></span>
                                                <button type="submit" class="btn btn-sm btn-primary" id="rebalanceSaveBtn">
                                                    <i class="bi bi-check-lg me-1"></i>Save Targets
                                                </button>
                                            </div>
                                        </form>
                                    </div>

                                    <!-- Analysis -->
                                    <div class="col-12 col-lg-7">
                                        <h6 class="fw-semibold mb-2"><i class="bi bi-graph-up me-2"></i>Analysis</h6>
                                        <div class="table-responsive">
                                            <table class="table table-sm table-hover mb-3" id="rebalanceAnalysisTable">
                                                <thead>
                                                    <tr>
                                                        <th>Type</th>
                                                        <th class="text-end">Current %</th>
                                                        <th class="text-end">Target %</th>
                                                        <th class="text-end">Drift %</th>
                                                        <th class="text-end">Action</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    <tr><td colspan="5" class="text-center text-muted small">Save targets to see analysis.</td></tr>
                                                </tbody>
                                            </table>
                                        </div>
                                        <div id="rebalanceActions"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
```

- [ ] **Step 3: Delete the `#holdingsPage` block entirely**

Find the block starting `<!-- Holdings Page -->` (immediately followed by `<div id="holdingsPage" class="page-content" style="display: none;">`) and delete it, INCLUDING the blank line immediately before the comment, but NOT the blank line that follows its closing `</div>` (that blank line stays, as the single separator before the following `<!-- Analytics Page -->` comment). The block to delete is exactly:

```html

                <!-- Holdings Page -->
                <div id="holdingsPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-bar-chart-line me-2 text-primary"></i>Holdings<button class="btn btn-sm btn-link p-0 ms-2 align-baseline" onclick="showPageHelp('holdings')" title="What is this page?"><i class="bi bi-question-circle"></i></button></h4>
                            <p class="text-muted small mb-0">Current open positions with cost basis, live price, and P&amp;L. All values in EUR.</p>
                        </div>
                        <button class="btn btn-sm btn-outline-secondary" id="refreshHoldings" title="Refresh holdings data">
                            <i class="bi bi-arrow-clockwise me-1"></i>Refresh
                        </button>
                    </div>

                    <!-- Holdings Summary Cards -->
                    <div class="row g-3 mb-4">
                        <div class="col-12 col-md-4">
                            <div class="card bg-primary text-white">
                                <div class="card-body py-3">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <div>
                                            <div class="small opacity-75 mb-1">Total Value</div>
                                            <div class="fs-4 fw-bold" id="holdingsTotalValue">—</div>
                                        </div>
                                        <i class="bi bi-cash-stack fs-2 opacity-50"></i>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-4">
                            <div class="card bg-secondary text-white">
                                <div class="card-body py-3">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <div>
                                            <div class="small opacity-75 mb-1">Total Cost</div>
                                            <div class="fs-4 fw-bold" id="holdingsTotalCost">—</div>
                                        </div>
                                        <i class="bi bi-wallet2 fs-2 opacity-50"></i>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-4">
                            <div class="card text-white" id="holdingsPnlCard">
                                <div class="card-body py-3">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <div>
                                            <div class="small opacity-75 mb-1">Total P/L</div>
                                            <div class="fs-4 fw-bold" id="holdingsTotalPnl">—</div>
                                        </div>
                                        <i class="bi bi-graph-up-arrow fs-2 opacity-50"></i>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Holdings Filter -->
                    <div class="row g-2 mb-3">
                        <div class="col-12 col-sm-3">
                            <select class="form-select form-select-sm" id="holdingsTypeFilter">
                                <option value="all">All Asset Types</option>
                            </select>
                        </div>
                        <div class="col-12 col-sm-3">
                            <select class="form-select form-select-sm" id="holdingsPortfolioFilter">
                                <option value="">All Brokers</option>
                            </select>
                        </div>
                        <div class="col-12 col-sm-5">
                            <input type="text" class="form-control form-control-sm" id="holdingsSearchInput" autocomplete="off" placeholder="Search by symbol, ticker or name…">
                        </div>
                    </div>

                    <!-- Holdings Table -->
                    <div class="card">
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-hover mb-0" id="holdingsTable">
                                    <thead>
                                        <tr>
                                            <th class="ps-3" data-key="symbol" data-type="text">Symbol</th>
                                            <th data-key="name" data-type="text">Name</th>
                                            <th data-key="asset_type" data-type="text">Type</th>
                                            <th data-key="currency" data-type="text">Currency</th>
                                            <th class="text-end" data-key="quantity" data-type="num">Quantity</th>
                                            <th class="text-end" data-key="avg_price" data-type="num">Avg Price</th>
                                            <th class="text-end" data-key="current_price" data-type="num">Current Price</th>
                                            <th class="text-end" data-key="total_value_eur" data-type="num">Total Value</th>
                                            <th class="text-end" data-key="pnl_amount" data-type="num">P/L</th>
                                            <th class="text-end" data-key="pnl_pct" data-type="num">P/L %</th>
                                            <th class="text-center">Links</th>
                                            <th class="text-end pe-3">Research</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <tr>
                                            <td colspan="12" class="text-center py-4">
                                                <div class="spinner-border spinner-border-sm me-2" role="status"></div>
                                                Loading holdings...
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- Rebalancing Card -->
                    <div class="card mt-4">
                        <div class="card-header fw-semibold" style="cursor:pointer;" data-bs-toggle="collapse" data-bs-target="#rebalanceBody" aria-expanded="false">
                            <i class="bi bi-sliders me-2 text-primary"></i>Rebalancing
                            <i class="bi bi-chevron-down float-end mt-1 small"></i>
                        </div>
                        <div class="collapse" id="rebalanceBody">
                            <div class="card-body">
                                <div class="row g-4">
                                    <!-- Target allocation form -->
                                    <div class="col-12 col-lg-5">
                                        <h6 class="fw-semibold mb-2"><i class="bi bi-bullseye me-2"></i>Target Allocation</h6>
                                        <p class="text-muted small mb-2">Set a target percentage for each asset type. Targets should sum to 100%.</p>
                                        <form id="rebalanceTargetsForm">
                                            <div id="rebalanceTargetsRows">
                                                <p class="text-muted small mb-0">Loading…</p>
                                            </div>
                                            <div class="d-flex justify-content-between align-items-center mt-2">
                                                <span class="small">Total: <strong id="rebalanceTargetsTotal">0%</strong></span>
                                                <button type="submit" class="btn btn-sm btn-primary" id="rebalanceSaveBtn">
                                                    <i class="bi bi-check-lg me-1"></i>Save Targets
                                                </button>
                                            </div>
                                        </form>
                                    </div>

                                    <!-- Analysis -->
                                    <div class="col-12 col-lg-7">
                                        <h6 class="fw-semibold mb-2"><i class="bi bi-graph-up me-2"></i>Analysis</h6>
                                        <div class="table-responsive">
                                            <table class="table table-sm table-hover mb-3" id="rebalanceAnalysisTable">
                                                <thead>
                                                    <tr>
                                                        <th>Type</th>
                                                        <th class="text-end">Current %</th>
                                                        <th class="text-end">Target %</th>
                                                        <th class="text-end">Drift %</th>
                                                        <th class="text-end">Action</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    <tr><td colspan="5" class="text-center text-muted small">Save targets to see analysis.</td></tr>
                                                </tbody>
                                            </table>
                                        </div>
                                        <div id="rebalanceActions"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
```

Replace that whole block (including its leading blank line) with an empty string.

- [ ] **Step 4: Verify structurally**

```bash
grep -c 'id="assetsPage"' web_client/index.html   # expect: 1
grep -c 'id="holdingsPage"' web_client/index.html # expect: 0
grep -c 'data-page="holdings"' web_client/index.html  # expect: 0
grep -c 'id="holdingsTable"' web_client/index.html    # expect: 0
grep -c 'id="assetsTable"' web_client/index.html      # expect: 1
python3 -c "import re; s=open('web_client/index.html').read(); print(s.count('<div'), s.count('</div>'))"
```
Expected: first four counts match the comments above; the last line's two numbers must be equal (balanced divs) — compare it against the same command run on the pre-edit file (via `git show HEAD:web_client/index.html`) to confirm the count only dropped by the number of `<div>` tags actually removed and no tag was orphaned.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html
git commit -m "feat: merge Assets + Holdings page markup into a single Assets page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: Rewrite page logic in `pfm_pages.js`

**Files:**
- Modify: `web_client/js/pfm_pages.js:132-240` (`loadAssetsPage` + `_renderFilteredAssets` — rewritten)
- Modify: `web_client/js/pfm_pages.js:677-826` (`loadHoldingsPage` — deleted)
- Test: manual verification via `node --check` + `make test-js` (see Step 4) — no dedicated unit tests, per the approved spec's Testing section (page-render functions are DOM-heavy and untested elsewhere in this file).

**Interfaces:**
- Consumes: `#assetsPage` markup and element ids from Task 1 (`assetTypeFilter`, `assetPortfolioFilter`, `assetSearchInput`, `assetShowZeroHoldings`, `refreshAssets`, `resolveTickersBtn`); `window.apiClient.getAssets()`, `window.apiClient.getHoldings(portfolioId)` (`pfm_core.js:1213`, `:1316`); `AssetSearch.enrich/match/buildAutocomplete`, `makeSortableTable`, `assetLinks`, `esc`, `Fmt` (`pfm_core.js`); `setupRebalanceTargets` (existing, called with the current holdings array — unchanged call signature); `openResearchModal`, `setAssetPrice` (existing globals, unchanged).
- Produces: `window.pageManager.loadAssetsPage()` — the only page-load entry point Task 3's navigation manager calls for `data-page="assets"`. `loadHoldingsPage` no longer exists — Task 3 must not reference it.

- [ ] **Step 1: Read the file, then replace `loadAssetsPage` + `_renderFilteredAssets`**

Read `web_client/js/pfm_pages.js` first. Replace the two methods currently spanning (approximately — re-read to get the exact current line numbers, since Task 1 does not touch this file) lines 132–240, i.e. from `loadAssetsPage: async function() {` through the end of `_renderFilteredAssets`'s closing `},` (the `_resolveTickersClick` method that follows is unchanged), with:

```javascript
        loadAssetsPage: async function() {
            const tableBody = document.querySelector('#assetsPage tbody');
            if (tableBody) tableBody.innerHTML = '<tr><td colspan="13" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>Loading…</td></tr>';
            try {
                // Populate broker dropdown once
                const aPortFilter = document.getElementById('assetPortfolioFilter');
                if (aPortFilter && aPortFilter.options.length <= 1) {
                    try {
                        const portfolios = await window.apiClient.getPortfolios();
                        portfolios.forEach(p => {
                            const opt = document.createElement('option');
                            opt.value = p.id;
                            opt.textContent = p.name;
                            aPortFilter.appendChild(opt);
                        });
                        aPortFilter.onchange = () => this._loadHoldingsAndRender();
                    } catch (e) { /* non-fatal */ }
                }

                const assets = await window.apiClient.getAssets();
                this._assetsData = assets;
                this._assetSuggestions = assets
                    .filter(a => a.symbol)
                    .map(a => AssetSearch.enrich(a.symbol, a.name));

                // Wire filters once per page lifecycle
                const page = document.getElementById('assetsPage');
                if (page && !page.dataset.filtersWired) {
                    page.dataset.filtersWired = '1';
                    document.getElementById('assetTypeFilter')
                        ?.addEventListener('change', () => this._renderFilteredAssets());
                    document.getElementById('assetShowZeroHoldings')
                        ?.addEventListener('change', () => this._renderFilteredAssets());
                    document.getElementById('refreshAssets')
                        ?.addEventListener('click', () => this.loadAssetsPage());
                    document.getElementById('resolveTickersBtn')
                        ?.addEventListener('click', () => this._resolveTickersClick());
                    this._setupAssetAutocomplete();
                }

                await this._loadHoldingsAndRender();
                this.hideLoadingSpinners();
            } catch (error) {
                console.error('Error loading assets page:', error);
                if (tableBody) tableBody.innerHTML = '<tr><td colspan="13" class="text-center text-danger">Error loading assets.</td></tr>';
                this.hideLoadingSpinners();
            }
        },

        // Fetches holdings for the selected portfolio (or the all-portfolios
        // aggregate when none is selected), updates the summary cards and the
        // rebalancing target rows, and — the first time it's needed —
        // computes the "held anywhere" symbol set the zero-holding toggle
        // filters against. Delegates to _renderFilteredAssets() for the
        // actual table render.
        _loadHoldingsAndRender: async function() {
            const tableBody = document.querySelector('#assetsPage tbody');
            const aPortFilter = document.getElementById('assetPortfolioFilter');
            const selectedPortfolioId = aPortFilter?.value || null;

            try {
                const data = await window.apiClient.getHoldings(selectedPortfolioId);
                const { holdings = [], summary = {} } = data;
                this._ownedHoldings = holdings;

                if (selectedPortfolioId === null) {
                    // The unfiltered call already is "held anywhere".
                    this._heldSymbolsGlobal = new Set(holdings.map(h => h.symbol));
                } else if (!this._heldSymbolsGlobal) {
                    const all = await window.apiClient.getHoldings(null);
                    this._heldSymbolsGlobal = new Set((all.holdings || []).map(h => h.symbol));
                }

                const fmt = (n) => n !== undefined ? parseFloat(n).toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
                const el = id => document.getElementById(id);
                if (el('holdingsTotalValue')) el('holdingsTotalValue').textContent = fmt(summary.total_value);
                if (el('holdingsTotalCost'))  el('holdingsTotalCost').textContent  = fmt(summary.total_cost);

                const pnl = summary.total_pnl || 0;
                const pnlPct = summary.total_pnl_pct || 0;
                const pnlCard = el('holdingsPnlCard');
                const pnlEl = el('holdingsTotalPnl');
                if (pnlEl) pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${fmt(pnl)} (${pnlPct >= 0 ? '+' : ''}${fmt(pnlPct)}%)`;
                if (pnlCard) {
                    pnlCard.className = `card text-white ${pnl >= 0 ? 'bg-success' : 'bg-danger'}`;
                }

                setupRebalanceTargets(holdings);

                await this._renderFilteredAssets();
            } catch (error) {
                console.error('Error loading holdings:', error);
                if (tableBody) tableBody.innerHTML = '<tr><td colspan="13" class="text-center text-danger">Error loading holdings.</td></tr>';
            }
        },

        _renderFilteredAssets: async function() {
            const tableBody = document.querySelector('#assetsPage tbody');
            if (!tableBody || !this._assetsData || !this._ownedHoldings) return;

            // One-time migration of the pre-tableState "holdingsSort" pref,
            // must run before the first makeSortableTable() call below seeds
            // a default.
            if (!window.PREFS.tableState || !window.PREFS.tableState.holdings) {
                const legacy = { value: { key: 'total_value_eur', dir: 'desc' }, pnl: { key: 'pnl_amount', dir: 'desc' }, pnlpct: { key: 'pnl_pct', dir: 'desc' }, name: { key: 'name', dir: 'asc' } }[window.PREFS.holdingsSort || 'value'];
                if (legacy) { if (!window.PREFS.tableState) window.PREFS.tableState = {}; window.PREFS.tableState.holdings = { sort: legacy, filters: {} }; }
            }

            const typeVal   = document.getElementById('assetTypeFilter')?.value || '';
            const searchVal = (document.getElementById('assetSearchInput')?.value || '').trim();
            const showZero  = document.getElementById('assetShowZeroHoldings')?.checked || false;

            const assetBySymbol = new Map(this._assetsData.map(a => [a.symbol, a]));
            const hideBelow = parseFloat(window.PREFS.hideBelowEur) || 0;
            const hVal = h => parseFloat(h.total_value_eur ?? h.total_value ?? 0) || 0;

            let ownedRows = this._ownedHoldings
                .filter(h => hideBelow <= 0 || hVal(h) >= hideBelow)
                .map(h => {
                    const a = assetBySymbol.get(h.symbol) || {};
                    return { ...h, exchange: a.exchange || '', auto_price: a.auto_price, id: a.id, owned: true };
                });

            let unownedRows = [];
            if (showZero) {
                const heldAnywhere = this._heldSymbolsGlobal || new Set();
                unownedRows = this._assetsData
                    .filter(a => !heldAnywhere.has(a.symbol))
                    .map(a => ({
                        ...a, quantity: null, avg_price: null, total_value: null,
                        total_value_eur: null, pnl_amount: null, pnl_pct: null, owned: false,
                    }));
            }

            let combined = ownedRows.concat(unownedRows);
            if (typeVal) combined = combined.filter(r => r.asset_type === typeVal);
            if (searchVal) {
                const matched = new Set(
                    AssetSearch.match(searchVal, this._assetSuggestions || [], this._assetsData.length)
                        .map(s => s.symbol)
                );
                const sq = searchVal.toLowerCase();
                combined = combined.filter(r =>
                    matched.has(r.symbol) || (r.exchange || '').toLowerCase().includes(sq)
                );
            }

            this._assetsRows = combined;
            const emptyMsg = '<tr><td colspan="13" class="text-center text-muted py-4">No assets match the current filters.</td></tr>';
            const fmt = (n) => (n !== undefined && n !== null) ? parseFloat(n).toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
            const renderRow = (row) => {
                const pnlClass = (row.pnl_amount || 0) >= 0 ? 'text-success' : 'text-danger';
                const typeBadge = { stock: 'bg-primary', etf: 'bg-info', index: 'bg-success', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[row.asset_type] || 'bg-secondary';
                const symEsc = (row.symbol || '').replace(/'/g, "\\'");
                const dash = '<span class="text-muted">—</span>';
                return `
                    <tr>
                        <td><strong>${esc(row.symbol || 'N/A')}</strong></td>
                        <td>${esc(row.name || 'N/A')}</td>
                        <td><span class="badge ${typeBadge}">${esc((row.asset_type || '').toUpperCase())}</span></td>
                        <td>${row.exchange || 'N/A'}</td>
                        <td>${row.currency || ''}</td>
                        <td class="text-end">${row.owned ? parseFloat(row.quantity).toLocaleString(Fmt.loc(), { maximumFractionDigits: 4 }) : dash}</td>
                        <td class="text-end">${row.owned ? fmt(row.avg_price) : dash}</td>
                        <td class="text-end">
                            ${row.current_price > 0 ? fmt(row.current_price) : dash}
                            ${row.auto_price === false ? '<span class="badge bg-secondary ms-1" title="Manual price — the daily cron will not overwrite it">manual</span>' : ''}
                            <button class="btn btn-sm btn-link p-0 ms-1 align-baseline" title="Set a manual price" onclick="setAssetPrice(${row.id}, '${symEsc}', '${row.currency || ''}')"><i class="bi bi-pencil-square"></i></button>
                        </td>
                        <td class="text-end fw-bold">${row.owned ? fmt(row.total_value) : dash}</td>
                        <td class="text-end ${row.owned ? pnlClass : ''}">${row.owned ? (row.pnl_amount >= 0 ? '+' : '') + fmt(row.pnl_amount) : dash}</td>
                        <td class="text-end ${row.owned ? pnlClass : ''}">${row.owned ? (row.pnl_pct >= 0 ? '+' : '') + fmt(row.pnl_pct) + '%' : dash}</td>
                        <td class="text-center text-nowrap">${assetLinks(row.symbol)}</td>
                        <td class="text-end pe-3">
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-primary" title="Research / Valuation" onclick="openResearchModal('${symEsc}')"><i class="bi bi-graph-up"></i></button>
                            </div>
                        </td>
                    </tr>`;
            };
            this._assetsST = this._assetsST || makeSortableTable({
                table: document.querySelector('#assetsPage table'),
                columns: [
                    { key: 'symbol', type: 'text' }, { key: 'name', type: 'text' },
                    { key: 'asset_type', type: 'text' }, { key: 'exchange', type: 'text' },
                    { key: 'currency', type: 'text' },
                    { key: 'quantity', type: 'num' }, { key: 'avg_price', type: 'num' },
                    { key: 'current_price', type: 'num' }, { key: 'total_value_eur', type: 'num' },
                    { key: 'pnl_amount', type: 'num' }, { key: 'pnl_pct', type: 'num' },
                    { key: null }, { key: null },
                ],
                getRows: () => this._assetsRows,
                renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderRow).join('') : emptyMsg; },
                prefsKey: 'holdings',
            });
            this._assetsST.refresh();
        },
```

Note `_resolveTickersClick` (unchanged) already calls `this.loadAssetsPage()` on success — no change needed there, it still refreshes the merged page correctly.

- [ ] **Step 2: Verify syntax**

```bash
node --check web_client/js/pfm_pages.js
```
Expected: no output (exit 0).

- [ ] **Step 3: Delete `loadHoldingsPage`**

Re-read the file (line numbers shifted after Step 1). Find the method starting `loadHoldingsPage: async function() {` and delete it, including the blank line immediately before it, through its closing `},` — but keep the blank line that follows (the one before `loadPortfoliosPage: async function() {`), so exactly one blank line separates the two remaining methods.

- [ ] **Step 4: Verify syntax + regression tests**

```bash
node --check web_client/js/pfm_pages.js
grep -c "loadHoldingsPage" web_client/js/pfm_pages.js   # expect: 0
make test-js
```
Expected: `node --check` silent; grep returns `0`; `make test-js` passes with the same pass count as before this task (no test in `web_client/js/tests/web_client.test.mjs` references these functions, so the count should be unchanged).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_pages.js
git commit -m "feat: merge Holdings + Assets page logic into one loadAssetsPage

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: Update navigation manager + bootstrap wiring in `pfm_features.js`

**Files:**
- Modify: `web_client/js/pfm_features.js:407` (`pages` array in `showPage`)
- Modify: `web_client/js/pfm_features.js:432-441` (`PAGE_TITLES` map)
- Modify: `web_client/js/pfm_features.js:448-469` (`loadPageData` switch)
- Modify: `web_client/js/pfm_features.js:3916-3919` (`refreshHoldings` button wiring — deleted, since Task 2's `loadAssetsPage` now wires `refreshAssets` itself)

**Interfaces:**
- Consumes: `window.pageManager.loadAssetsPage` (Task 2). Must NOT reference `loadHoldingsPage` (deleted in Task 2) or `#holdingsPage`/`refreshHoldings` (deleted in Task 1) anywhere.
- Produces: `data-page="assets"` is the only remaining route to the merged page; `data-page="holdings"` no longer resolves to anything (its nav link no longer exists after Task 1, so this is dead-code removal, not a behavior change).

- [ ] **Step 1: Remove `'holdingsPage'` from the `pages` array**

Read `web_client/js/pfm_features.js` first. In `showPage`:

```
old_string:
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'holdingsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage', 'helpPage', 'versionPage', 'aboutPage', 'resourcesPage', 'networthPage', 'diagnosticsPage', 'actionitemsPage'];
new_string:
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage', 'helpPage', 'versionPage', 'aboutPage', 'resourcesPage', 'networthPage', 'diagnosticsPage', 'actionitemsPage'];
```

- [ ] **Step 2: Remove the `holdings` entry from `PAGE_TITLES`**

```
old_string:
            const PAGE_TITLES = {
                dashboard: 'Dashboard', assets: 'Assets', transactions: 'Transactions',
                holdings: 'Holdings', analytics: 'Analytics', watchlist: 'Watchlist',
                goals: 'Goals', research: 'Research', chat: 'AI Chat',
new_string:
            const PAGE_TITLES = {
                dashboard: 'Dashboard', assets: 'Assets', transactions: 'Transactions',
                analytics: 'Analytics', watchlist: 'Watchlist',
                goals: 'Goals', research: 'Research', chat: 'AI Chat',
```

- [ ] **Step 3: Remove the `holdings` case from `loadPageData`**

```
old_string:
                case 'assets':       window.pageManager.loadAssetsPage(); break;
                case 'transactions': window.pageManager.loadTransactionsPage(); break;
                case 'holdings':     window.pageManager.loadHoldingsPage(); break;
                case 'analytics':    window.pageManager.loadAnalyticsPage(); break;
new_string:
                case 'assets':       window.pageManager.loadAssetsPage(); break;
                case 'transactions': window.pageManager.loadTransactionsPage(); break;
                case 'analytics':    window.pageManager.loadAnalyticsPage(); break;
```

- [ ] **Step 4: Remove the `refreshHoldings` bootstrap wiring**

```
old_string:

    const refreshHoldings = document.getElementById('refreshHoldings');
    if (refreshHoldings) {
        refreshHoldings.addEventListener('click', () => window.pageManager.loadHoldingsPage());
    }

new_string:

```

(This leaves the surrounding `setupEditGoalModal();` line and the `refreshTxPage` block adjacent with a single blank line between them — re-read the file to confirm exact spacing before finalizing the edit.)

- [ ] **Step 5: Verify**

```bash
node --check web_client/js/pfm_features.js
grep -n "holdingsPage\|loadHoldingsPage\|refreshHoldings" web_client/js/pfm_features.js
make test-js
```
Expected: `node --check` silent; the `grep` prints nothing (no matches, exit code 1 is fine — it means zero hits); `make test-js` passes.

- [ ] **Step 6: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: remove holdings route from navigation, merged into assets

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Merge help text in `help_text.js`

**Files:**
- Modify: `web_client/js/help_text.js:83-94` (`holdings` entry — deleted)
- Modify: `web_client/js/help_text.js:153-162` (`assets` entry — body replaced with merged content)

**Interfaces:**
- Consumes: nothing from other tasks (the merged page's `showPageHelp('assets')` call in Task 1's markup already targets the `assets` key, which already exists — no new wiring needed).
- Produces: `window.PAGE_HELP.assets` — the only surviving key; `window.PAGE_HELP.holdings` removed.

- [ ] **Step 1: Delete the `holdings` entry**

Read `web_client/js/help_text.js` first.

```
old_string:
  holdings: {
    title: "Holdings",
    body: `
      <p>Your current open positions with cost basis, live price and profit/loss, all in EUR.</p>
      <ul class="mb-2">
        <li><strong>Avg Price</strong> is your FIFO cost basis; <strong>Current Price</strong> is the latest Yahoo Finance quote.</li>
        <li><strong>P/L</strong> is unrealised gain/loss on positions you still hold.</li>
        <li><strong>Research</strong> opens an LLM-generated fair-value analysis from fundamentals — informational, not advice.</li>
        <li><strong>Rebalancing</strong> compares your current allocation against your target percentages and suggests buys/sells to close the drift.</li>
      </ul>
      <p class="text-muted small mb-0">Prices from Yahoo Finance, refreshed daily at 20:00 UTC; converted to EUR at live FX rates.</p>`
  },
  watchlist: {
new_string:
  watchlist: {
```

- [ ] **Step 2: Replace the `assets` entry body**

```
old_string:
  assets: {
    title: "Assets",
    body: `
      <p>The catalogue of securities and funds you track.</p>
      <ul class="mb-2">
        <li>Each asset has a symbol, type, exchange and currency.</li>
        <li><strong>Current Price</strong> comes from Yahoo Finance, refreshed daily at 20:00 UTC.</li>
      </ul>
      <p class="text-muted small mb-0">Assets are created automatically when you import transactions, or added manually here.</p>`
  },
new_string:
  assets: {
    title: "Assets",
    body: `
      <p>Your current open positions with cost basis, live price and profit/loss, plus the full catalogue of securities and funds you track — all in EUR.</p>
      <ul class="mb-2">
        <li>Each asset has a symbol, type, exchange and currency. Assets are created automatically when you import transactions, or added manually here.</li>
        <li><strong>Avg Price</strong> is your FIFO cost basis; <strong>Current Price</strong> is the latest Yahoo Finance quote, refreshed daily at 20:00 UTC.</li>
        <li><strong>P/L</strong> is unrealised gain/loss on positions you still hold. Quantity, cost and P/L are blank for assets you don't currently hold — tick <strong>"Show assets with no holding"</strong> to include them in the table.</li>
        <li><strong>Research</strong> opens an LLM-generated fair-value analysis from fundamentals — informational, not advice.</li>
        <li><strong>Rebalancing</strong> compares your current allocation against your target percentages and suggests buys/sells to close the drift.</li>
      </ul>
      <p class="text-muted small mb-0">Prices from Yahoo Finance, refreshed daily at 20:00 UTC; converted to EUR at live FX rates.</p>`
  },
```

- [ ] **Step 3: Verify**

```bash
node --check web_client/js/help_text.js
grep -n "holdings:" web_client/js/help_text.js   # expect: no "title: \"Holdings\"" match
make test-js
```
Expected: `node --check` silent; `make test-js` passes.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/help_text.js
git commit -m "docs: merge Holdings help text into the Assets page help entry

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: Deploy, verify in browser, and update docs

**Files:**
- Modify: `PROJECT_STATUS.md` (new "Recent (vX.Y.Z)" entry + "Last updated" date)
- Modify: `CLAUDE.md` (Web Client section — short note on the merged page)

**Interfaces:**
- Consumes: the fully merged page from Tasks 1–4.
- Produces: nothing consumed by other tasks (terminal task).

- [ ] **Step 1: Determine the next version number**

```bash
grep -m1 "Recent (v" PROJECT_STATUS.md
```

Read the version in that line and increment the patch number by one (e.g. if it reads `v2.5.16`, the new entry is `v2.5.17`). Use that version in Steps 2 and 4 below.

- [ ] **Step 2: Add the `PROJECT_STATUS.md` entry**

Read `PROJECT_STATUS.md` first. Insert a new line directly after the `Last updated:` line and its blank line, before the existing top `**Recent (v...)**` line, following the exact format of the existing entries (one paragraph, starting `**Recent (vX.Y.Z):** **<short title>** — <description>.`). Also update the `Last updated:` line to today's date. Content for the new paragraph:

```
**Recent (vX.Y.Z):** **Merged Assets + Holdings into one page** — the separate "Holdings" (positions with cost basis, live price, P&L) and "Assets" (instrument catalogue with manual price override and OpenFIGI ticker resolution) nav pages are now a single "Assets" page. Defaults to owned positions only (same portfolio-scoped summary cards, hide-tiny-position threshold, and rebalancing card as the old Holdings page); a new "Show assets with no holding" checkbox appends catalogue assets held in zero portfolios ("held anywhere" computed from an unfiltered `getHoldings()` call, independent of the selected portfolio filter). No backend changes — purely a frontend merge of `getAssets()` + `getHoldings(portfolio_id)`.
```

(Replace `vX.Y.Z` with the version determined in Step 1.)

- [ ] **Step 3: Add the `CLAUDE.md` note**

Read `CLAUDE.md` first. In the `## Web Client (`web_client/`)` section, after the line documenting `pfm_pages.js`'s contents (`- \`pfm_pages.js\`: page/nav/auth, dashboard, transactions, assets, holdings, help/resources`), update it to remove the now-separate "holdings" mention and add a short note:

```
old_string:
- `pfm_pages.js`: page/nav/auth, dashboard, transactions, assets, holdings, help/resources
new_string:
- `pfm_pages.js`: page/nav/auth, dashboard, transactions, assets (positions + catalogue merged, see below), help/resources
```

Then, immediately after the `makeSortableTable`/`applyTableState` paragraph in the same section, add:

```
**Assets page** (`loadAssetsPage`/`_loadHoldingsAndRender`/`_renderFilteredAssets` in `pfm_pages.js`): merges the old separate Holdings and Assets pages. Defaults to owned positions for the selected portfolio (`GET /api/v1/portfolios/holdings`, unchanged); the "Show assets with no holding" checkbox appends `GET /api/v1/assets/` catalogue entries whose symbol has zero quantity in *any* portfolio — computed via one unfiltered `getHoldings()` call, cached in `this._heldSymbolsGlobal`, independent of the portfolio filter. An asset held in a different, non-selected portfolio is excluded from both halves (not a position for the selected portfolio, not "held nowhere" either) — same as the old Holdings portfolio-filter behavior, nothing new to reconcile. `PREFS.tableState.holdings` is the sort/filter-state key (kept from the old Holdings page, not renamed).
```

- [ ] **Step 4: Commit the docs**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: document the merged Assets + Holdings page (vX.Y.Z)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

(Replace `vX.Y.Z` with the version from Step 1.)

- [ ] **Step 5: Rebuild and redeploy the web container**

```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

Expected: build succeeds, container restarts. This briefly interrupts access to the live app — fine to run without asking, since it's the project's own documented standard workflow for `web_client/` changes (`CLAUDE.md`'s "After Every Task" table), not a destructive or externally-visible action beyond Alex's own homelab instance.

- [ ] **Step 6: Verify in browser**

Use the `run` skill (or open `http://localhost:8080` directly) to log in and check, on the merged "Assets" page:
1. Only one "Assets" entry appears in the sidebar (no separate "Holdings").
2. With the zero-holding checkbox unchecked, the table matches what the old Holdings page showed (same summary cards, same owned rows).
3. Checking "Show assets with no holding" appends catalogue assets with quantity/avg price/value/P&L shown as `—`.
4. Switching the portfolio filter updates the summary cards and owned rows; the zero-holding set does not change based on portfolio selection.
5. The manual-price pencil icon and "Fill tickers" button both still work.
6. The Rebalancing card at the bottom still loads and saves targets.

If a browser isn't reachable in this environment, say so explicitly rather than claiming the UI was verified, and ask Alex to confirm visually before considering this task done (per `CLAUDE.md`: "if you can't test the UI, say so explicitly rather than claiming success").
