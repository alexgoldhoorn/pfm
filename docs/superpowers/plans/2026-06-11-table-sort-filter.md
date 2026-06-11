# Coherent Table Sort/Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Holdings, Transactions, Assets, and Brokers tables a coherent clickable-header sort + filter-row, backed by one shared helper, with per-table state persisted in PREFS.

**Architecture:** A pure `applyTableState(rows, columns, state)` (DOM-free, unit-tested) does filter+sort; a thin `makeSortableTable(config)` DOM wrapper enhances an existing `<table>` — it reads `data-key`/`data-type`/`data-filter` attributes added to the `<th>`s, wires header-click sorting + a filter row, persists state to `PREFS.tableState[prefsKey]`, and re-renders `<tbody>` via the page's existing row markup.

**Tech Stack:** Vanilla JS classic scripts (no build step), Node built-in test runner (`node --test`), Bootstrap 5. Web container bakes files at build → rebuild needed for browser smoke (`WEB_PORT=8080 docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`).

Spec: `docs/superpowers/specs/2026-06-11-table-sort-filter-design.md`

---

### Task 1: Pure `applyTableState` core + PREFS default + tests

**Files:**
- Modify: `web_client/js/pfm_core.js` (add helper after `topPositions`/`window.topPositions`; add PREFS default)
- Test: `web_client/js/tests/web_client.test.mjs` (append)

- [ ] **Step 1: Write the failing tests.** Append to `web_client/js/tests/web_client.test.mjs`:

```javascript
const TS_COLUMNS = [
    { key: "symbol", type: "text" },
    { key: "value", type: "num" },
    { key: "date", type: "date" },
    { key: "asset_type", type: "text", filter: "select" },
];
const TS_ROWS = [
    { symbol: "bbb", value: 10, date: "2025-01-02", asset_type: "stock" },
    { symbol: "AAA", value: 30, date: "2025-03-01", asset_type: "crypto" },
    { symbol: "ccc", value: 20, date: "2025-02-01", asset_type: "stock" },
    { symbol: "ddd", value: null, date: "", asset_type: "stock" },
];

test("applyTableState: numeric sort desc, blanks last", () => {
    const { applyTableState } = loadAppIntoContext();
    const r = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "value", dir: "desc" }, filters: {} });
    assert.deepEqual(r.map((x) => x.symbol), ["AAA", "ccc", "bbb", "ddd"]);
});

test("applyTableState: text sort asc is case-insensitive", () => {
    const { applyTableState } = loadAppIntoContext();
    const r = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "symbol", dir: "asc" }, filters: {} });
    assert.deepEqual(r.map((x) => x.symbol), ["AAA", "bbb", "ccc", "ddd"]);
});

test("applyTableState: date sort asc", () => {
    const { applyTableState } = loadAppIntoContext();
    const r = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "date", dir: "asc" }, filters: {} });
    // ddd has blank date -> last
    assert.deepEqual(r.map((x) => x.symbol), ["bbb", "ccc", "AAA", "ddd"]);
});

test("applyTableState: select filter keeps matches; 'all' passes through", () => {
    const { applyTableState } = loadAppIntoContext();
    const f = applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "symbol", dir: "asc" }, filters: { asset_type: "stock" } });
    assert.deepEqual(f.map((x) => x.symbol), ["bbb", "ccc", "ddd"]);
    const all = applyTableState(TS_ROWS, TS_COLUMNS, { sort: null, filters: { asset_type: "all" } });
    assert.equal(all.length, 4);
});

test("applyTableState: does not mutate input", () => {
    const { applyTableState } = loadAppIntoContext();
    const before = TS_ROWS.map((x) => x.symbol);
    applyTableState(TS_ROWS, TS_COLUMNS, { sort: { key: "value", dir: "asc" }, filters: {} });
    assert.deepEqual(TS_ROWS.map((x) => x.symbol), before);
});
```

- [ ] **Step 2: Run to verify FAIL:** `node --test web_client/js/tests/` → the 5 new tests fail (`applyTableState is not a function`).

- [ ] **Step 3: Implement the pure core + PREFS default.** In `web_client/js/pfm_core.js`, add `tableState` to `PREFS_DEFAULTS` (after the `dashTopPositions` line added earlier):
```javascript
    dashTopPositions: { n: 5, type: 'all', broker: 'all', sort: 'value' },
    tableState: {},   // per-table sort/filter, keyed by table (holdings, transactions, assets, portfolios)
};
```
Then, immediately after the `window.topPositions = topPositions;` line, add:
```javascript
// Pure filter+sort for the shared sortable tables (unit-tested). `columns` is
// [{key, type:'text'|'num'|'date', filter?}]; `state` is {sort:{key,dir}, filters:{key:value}}.
// Blanks/missing always sort last; returns a new array (no mutation).
function applyTableState(rows, columns, state) {
    const byKey = Object.fromEntries((columns || []).map(c => [c.key, c]));
    let out = (rows || []).slice();
    const filters = (state && state.filters) || {};
    for (const [k, v] of Object.entries(filters)) {
        if (v && v !== 'all') out = out.filter(r => String(r[k] == null ? '' : r[k]) === String(v));
    }
    const s = state && state.sort;
    if (s && s.key && byKey[s.key]) {
        const type = byKey[s.key].type || 'text';
        const dir = s.dir === 'asc' ? 1 : -1;
        out.sort((a, b) => {
            const av = a[s.key], bv = b[s.key];
            const ab = av == null || av === '';
            const bb = bv == null || bv === '';
            if (ab && bb) return 0;
            if (ab) return 1;   // blanks last regardless of direction
            if (bb) return -1;
            let cmp;
            if (type === 'num') cmp = (parseFloat(av) || 0) - (parseFloat(bv) || 0);
            else if (type === 'date') cmp = String(av).localeCompare(String(bv));
            else cmp = String(av).toLowerCase().localeCompare(String(bv).toLowerCase());
            return cmp * dir;
        });
    }
    return out;
}
window.applyTableState = applyTableState;
```

- [ ] **Step 4: Run to verify PASS:** `node --test web_client/js/tests/` → all pass (9 prior + 5 new = 14).

- [ ] **Step 5: Commit:**
```bash
git add web_client/js/pfm_core.js web_client/js/tests/web_client.test.mjs
git commit -m "feat(web): pure applyTableState filter+sort core + tests"
```

---

### Task 2: `makeSortableTable` DOM wrapper

**Files:**
- Modify: `web_client/js/pfm_core.js` (add after `applyTableState`)
- Modify: `web_client/css/styles.css` (small style for the sort arrow/cursor)

- [ ] **Step 1: Implement the wrapper.** In `web_client/js/pfm_core.js`, immediately after `window.applyTableState = applyTableState;`, add:

```javascript
// Per-table state accessor (seeds a default sort the first time).
function _tableState(prefsKey, columns) {
    if (!window.PREFS.tableState) window.PREFS.tableState = {};
    if (!window.PREFS.tableState[prefsKey]) {
        const first = (columns || []).find(c => c.sortable !== false && c.key);
        window.PREFS.tableState[prefsKey] = {
            sort: first ? { key: first.key, dir: (first.type === 'text' ? 'asc' : 'desc') } : null,
            filters: {},
        };
    }
    const st = window.PREFS.tableState[prefsKey];
    if (!st.filters) st.filters = {};
    return st;
}

// Enhance an existing <table> with clickable-header sort + a filter row.
// config: { table, columns, getRows, renderRows, prefsKey }
//  - columns: [{key, type, sortable?, filter?}] matched to <th data-key=...>
//  - getRows(): current data array  - renderRows(rows, tbody): fills tbody
// Returns { refresh() } — call after (re)loading data.
function makeSortableTable(config) {
    const { table, columns, getRows, renderRows, prefsKey } = config;
    if (!table) return { refresh() {} };
    const thead = table.querySelector('thead');
    const tbody = table.querySelector('tbody');
    const state = _tableState(prefsKey, columns);

    function updateIndicators() {
        thead.querySelectorAll('th[data-key]').forEach(th => {
            let arrow = th.querySelector('.pfm-sort-arrow');
            if (!arrow) {
                arrow = document.createElement('span');
                arrow.className = 'pfm-sort-arrow ms-1';
                th.appendChild(arrow);
            }
            const active = state.sort && state.sort.key === th.dataset.key;
            arrow.textContent = active ? (state.sort.dir === 'asc' ? '▲' : '▼') : '';
        });
    }

    function render() {
        renderRows(applyTableState(getRows(), columns, state), tbody);
        updateIndicators();
    }

    function populateFilters() {
        const rows = getRows() || [];
        thead.querySelectorAll('select[data-filter-key]').forEach(sel => {
            const key = sel.dataset.filterKey;
            const vals = [...new Set(rows.map(r => String(r[key] == null ? '' : r[key])).filter(Boolean))].sort();
            const cur = state.filters[key] || 'all';
            sel.innerHTML = '<option value="all">All</option>' +
                vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
            sel.value = [...sel.options].some(o => o.value === cur) ? cur : 'all';
        });
    }

    if (!table.dataset.sortWired) {
        table.dataset.sortWired = '1';
        // Header click → toggle/set sort.
        thead.querySelectorAll('th[data-key]').forEach(th => {
            th.style.cursor = 'pointer';
            th.classList.add('pfm-sortable-th');
            th.addEventListener('click', () => {
                const key = th.dataset.key;
                if (state.sort && state.sort.key === key) {
                    state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    state.sort = { key, dir: (th.dataset.type === 'text' ? 'asc' : 'desc') };
                }
                savePrefs();
                render();
            });
        });
        // Build a second thead row of filter <select>s (one cell per column).
        const filterCols = (columns || []).filter(c => c.filter === 'select');
        if (filterCols.length) {
            const fr = document.createElement('tr');
            fr.className = 'pfm-filter-row';
            // Match the table's column count from the first header row.
            const headerCells = thead.querySelector('tr').children.length;
            for (let i = 0; i < headerCells; i++) {
                const cell = document.createElement('th');
                cell.className = 'py-1 fw-normal';
                const col = columns[i];
                if (col && col.filter === 'select') {
                    const sel = document.createElement('select');
                    sel.className = 'form-select form-select-sm';
                    sel.dataset.filterKey = col.key;
                    sel.addEventListener('change', () => {
                        state.filters[col.key] = sel.value;
                        savePrefs();
                        render();
                    });
                    cell.appendChild(sel);
                }
                fr.appendChild(cell);
            }
            thead.appendChild(fr);
        }
    }

    return {
        refresh() { populateFilters(); render(); },
    };
}
window.makeSortableTable = makeSortableTable;
```

NOTE: the filter row maps `columns[i]` to header cell `i`, so **`columns` must be in the same left-to-right order as the table's `<th>` cells, including non-sortable ones** (Actions/Links/Research). Non-data columns use `{ key: null }` placeholders so indexes line up.

- [ ] **Step 2: Add minimal CSS.** In `web_client/css/styles.css`, append:
```css
/* Shared sortable tables */
.pfm-sortable-th { user-select: none; white-space: nowrap; }
.pfm-sortable-th:hover { text-decoration: underline; }
.pfm-sort-arrow { font-size: 0.75em; }
.pfm-filter-row select { min-width: 6rem; }
```

- [ ] **Step 3: Run the JS tests (regression — app still loads):** `node --test web_client/js/tests/` → 14 pass. (The load/smoke test exercises `pfm_core.js` parsing; `makeSortableTable` is a function, no top-level execution.)

- [ ] **Step 4: Commit:**
```bash
git add web_client/js/pfm_core.js web_client/css/styles.css
git commit -m "feat(web): makeSortableTable DOM wrapper (clickable headers + filter row)"
```

---

### Task 3: Wire the Holdings table

**Files:**
- Modify: `web_client/index.html` (holdings `<thead>` ~line 719–731 — add data attrs)
- Modify: `web_client/js/pfm_pages.js` (`loadHoldingsPage` ~567)

- [ ] **Step 1: Add `data-*` attributes to the holdings header cells.** In `web_client/index.html`, the holdings table `<thead>` row currently is:
```html
                                            <th class="ps-3">Symbol</th>
                                            <th>Name</th>
                                            <th>Type</th>
                                            <th>Currency</th>
                                            <th class="text-end">Quantity</th>
                                            <th class="text-end">Avg Price</th>
                                            <th class="text-end">Current Price</th>
                                            <th class="text-end">Total Value</th>
                                            <th class="text-end">P/L</th>
                                            <th class="text-end">P/L %</th>
                                            <th class="text-center">Links</th>
                                            <th class="text-end pe-3">Research</th>
```
Replace with (adds `data-key`/`data-type`, and `data-filter` on Type):
```html
                                            <th class="ps-3" data-key="symbol" data-type="text">Symbol</th>
                                            <th data-key="name" data-type="text">Name</th>
                                            <th data-key="asset_type" data-type="text" data-filter="select">Type</th>
                                            <th data-key="currency" data-type="text">Currency</th>
                                            <th class="text-end" data-key="quantity" data-type="num">Quantity</th>
                                            <th class="text-end" data-key="avg_price" data-type="num">Avg Price</th>
                                            <th class="text-end" data-key="current_price" data-type="num">Current Price</th>
                                            <th class="text-end" data-key="total_value_eur" data-type="num">Total Value</th>
                                            <th class="text-end" data-key="pnl_amount" data-type="num">P/L</th>
                                            <th class="text-end" data-key="pnl_pct" data-type="num">P/L %</th>
                                            <th class="text-center">Links</th>
                                            <th class="text-end pe-3">Research</th>
```

- [ ] **Step 2: Refactor `loadHoldingsPage` to use the helper.** In `web_client/js/pfm_pages.js`, in `loadHoldingsPage`, keep the data fetch, summary cards, and the **hide-tiny** filter that produces `view`. Then **replace** the sort block + the `if (view.length === 0) { ... } else { tableBody.innerHTML = view.map(...) }` rendering block with helper wiring. Concretely:

Delete these lines (the `PREFS.holdingsSort` sort and the manual tbody render):
```javascript
                const sortKey = window.PREFS.holdingsSort || 'value';
                view.sort((a, b) => {
                    if (sortKey === 'name') return String(a.name || a.symbol).localeCompare(String(b.name || b.symbol));
                    if (sortKey === 'pnl') return (parseFloat(b.pnl_amount) || 0) - (parseFloat(a.pnl_amount) || 0);
                    if (sortKey === 'pnlpct') return (parseFloat(b.pnl_pct) || 0) - (parseFloat(a.pnl_pct) || 0);
                    return hVal(b) - hVal(a); // value (default)
                });

                if (view.length === 0) {
                    tableBody.innerHTML = `<tr><td colspan="12" class="text-center text-muted">${holdings.length ? 'All positions are below your “hide tiny positions” threshold.' : 'No holdings found. Add buy transactions to see your positions here.'}</td></tr>`;
                } else {
                    tableBody.innerHTML = view.map(h => {
```
…through the end of that `.map(...).join('')` and its closing `}`. Replace the whole removed region with:
```javascript
                // Store the hide-tiny-filtered set; the shared table does sort+filter.
                this._holdingsRows = view;
                const emptyMsg = `<tr><td colspan="12" class="text-center text-muted">${holdings.length ? 'No holdings match the current filter.' : 'No holdings found. Add buy transactions to see your positions here.'}</td></tr>`;
                const renderHoldingRow = (h) => {
                    const pnlClass = h.pnl_amount >= 0 ? 'text-success' : 'text-danger';
                    const typeBadge = { stock: 'bg-primary', etf: 'bg-info', index: 'bg-success', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[h.asset_type] || 'bg-secondary';
                    const symEsc = (h.symbol || '').replace(/'/g, "\\'");
                    return `
                        <tr>
                            <td><strong>${esc(h.symbol)}</strong></td>
                            <td>${esc(h.name)}</td>
                            <td><span class="badge ${typeBadge}">${esc((h.asset_type || '').toUpperCase())}</span></td>
                            <td>${esc(h.currency || '')}</td>
                            <td class="text-end">${parseFloat(h.quantity).toLocaleString(Fmt.loc(), { maximumFractionDigits: 4 })}</td>
                            <td class="text-end">${fmt(h.avg_price)}</td>
                            <td class="text-end">${h.current_price > 0 ? fmt(h.current_price) : '<span class="text-muted">—</span>'}</td>
                            <td class="text-end fw-bold">${fmt(h.total_value)}</td>
                            <td class="text-end ${pnlClass}">${h.pnl_amount >= 0 ? '+' : ''}${fmt(h.pnl_amount)}</td>
                            <td class="text-end ${pnlClass}">${h.pnl_pct >= 0 ? '+' : ''}${fmt(h.pnl_pct)}%</td>
                            <td class="text-center text-nowrap">${assetLinks(h.symbol)}</td>
                            <td class="text-end pe-3"><button class="btn btn-sm btn-outline-primary" title="Research / Valuation" onclick="openResearchModal('${symEsc}')"><i class="bi bi-graph-up"></i></button></td>
                        </tr>`;
                };
                this._holdingsST = this._holdingsST || makeSortableTable({
                    table: document.getElementById('holdingsTable'),
                    columns: [
                        { key: 'symbol', type: 'text' }, { key: 'name', type: 'text' },
                        { key: 'asset_type', type: 'text', filter: 'select' }, { key: 'currency', type: 'text' },
                        { key: 'quantity', type: 'num' }, { key: 'avg_price', type: 'num' },
                        { key: 'current_price', type: 'num' }, { key: 'total_value_eur', type: 'num' },
                        { key: 'pnl_amount', type: 'num' }, { key: 'pnl_pct', type: 'num' },
                        { key: null }, { key: null },
                    ],
                    getRows: () => this._holdingsRows,
                    renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderHoldingRow).join('') : emptyMsg; },
                    prefsKey: 'holdings',
                });
                this._holdingsST.refresh();
```
(`fmt`, `hVal`, `holdings`, `view` are already defined above in the function. The `#holdingsTable` id already exists on the table.)

- [ ] **Step 2b: Seed the default holdings sort from the legacy pref (once).** Right before the `this._holdingsST = ...` line, add a one-time seed so existing users keep their `PREFS.holdingsSort` as the initial column:
```javascript
                if (!window.PREFS.tableState || !window.PREFS.tableState.holdings) {
                    const legacy = { value: { key: 'total_value_eur', dir: 'desc' }, pnl: { key: 'pnl_amount', dir: 'desc' }, pnlpct: { key: 'pnl_pct', dir: 'desc' }, name: { key: 'name', dir: 'asc' } }[window.PREFS.holdingsSort || 'value'];
                    if (legacy) { if (!window.PREFS.tableState) window.PREFS.tableState = {}; window.PREFS.tableState.holdings = { sort: legacy, filters: {} }; }
                }
```

- [ ] **Step 3: Bump cache-busters.** In `web_client/index.html` change the four `pfm_*.js` `?v=` values to `?v=1780000043`.
Run: `grep -c "pfm_.*?v=1780000043" web_client/index.html` → expected `4`.

- [ ] **Step 4: JS tests still pass:** `node --test web_client/js/tests/` → 14 pass.

- [ ] **Step 5: Commit:**
```bash
git add web_client/index.html web_client/js/pfm_pages.js
git commit -m "feat(web): sortable/filterable Holdings table"
```

---

### Task 4: Wire the Assets table

**Files:**
- Modify: `web_client/index.html` (assets `<thead>` ~538–544)
- Modify: `web_client/js/pfm_pages.js` (`_renderFilteredAssets` ~163)

- [ ] **Step 1: Add `data-*` attributes to the assets header.** Replace the assets `<thead>` cells:
```html
                                            <th class="ps-3">Symbol</th>
                                            <th>Name</th>
                                            <th>Type</th>
                                            <th>Exchange</th>
                                            <th class="text-end">Current Price</th>
                                            <th>Currency</th>
                                            <th class="pe-3">Actions</th>
```
with:
```html
                                            <th class="ps-3" data-key="symbol" data-type="text">Symbol</th>
                                            <th data-key="name" data-type="text">Name</th>
                                            <th data-key="asset_type" data-type="text">Type</th>
                                            <th data-key="exchange" data-type="text">Exchange</th>
                                            <th class="text-end" data-key="current_price" data-type="num">Current Price</th>
                                            <th data-key="currency" data-type="text">Currency</th>
                                            <th class="pe-3">Actions</th>
```
(NOTE: the Assets page already has a dedicated **type filter** dropdown (`assetTypeFilter`) and a search box above the table; do NOT add a `data-filter` here — keep the existing controls and just add header sorting.)

- [ ] **Step 2: Route the assets render through the helper.** In `_renderFilteredAssets`, the function computes `filtered`. Keep that. Then **replace** the final `if (filtered.length === 0) { ... } else { tableBody.innerHTML = filtered.map(asset => \`...\`).join('') }` block: store `filtered` and render via the helper. Specifically, set `this._assetsRows = filtered;` and replace the render with:
```javascript
            this._assetsRows = filtered;
            const emptyMsg = '<tr><td colspan="7" class="text-center text-muted py-4">No assets match the current filters.</td></tr>';
            this._assetsST = this._assetsST || makeSortableTable({
                table: document.querySelector('#assetsPage table'),
                columns: [
                    { key: 'symbol', type: 'text' }, { key: 'name', type: 'text' },
                    { key: 'asset_type', type: 'text' }, { key: 'exchange', type: 'text' },
                    { key: 'current_price', type: 'num' }, { key: 'currency', type: 'text' },
                    { key: null },
                ],
                getRows: () => this._assetsRows,
                renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderAssetRow).join('') : emptyMsg; },
                prefsKey: 'assets',
            });
            this._assetsST.refresh();
```
Extract the existing per-asset template into a `const renderAssetRow = (asset) => \`...\`;` defined just above this block, using the **exact existing row HTML** from the current `filtered.map(asset => \`...\`)` (do not rewrite it; move it verbatim, ensuring untrusted fields keep their `esc()` wrappers). The assets table has no `id`; `#assetsPage table` selects it.

- [ ] **Step 3: JS tests pass + commit:**
```bash
node --test web_client/js/tests/
git add web_client/index.html web_client/js/pfm_pages.js
git commit -m "feat(web): sortable Assets table"
```

---

### Task 5: Wire the Transactions table

**Files:**
- Modify: `web_client/index.html` (transactions `<thead>` ~632–641)
- Modify: `web_client/js/pfm_pages.js` (`loadTransactionsPage` ~395)

- [ ] **Step 1: Add `data-*` attributes to the transactions header.** Replace the transactions `<thead>` cells:
```html
                                            <th class="ps-3">Date</th>
                                            <th>Portfolio</th>
                                            <th>Asset</th>
                                            <th>Type</th>
                                            <th class="text-end">Quantity</th>
                                            <th class="text-end">Price</th>
                                            <th>Currency</th>
                                            <th class="text-end">Total</th>
                                            <th class="text-end">Fees</th>
                                            <th class="pe-3">Actions</th>
```
with:
```html
                                            <th class="ps-3" data-key="transaction_date" data-type="date">Date</th>
                                            <th data-key="portfolio_name" data-type="text">Portfolio</th>
                                            <th data-key="symbol" data-type="text">Asset</th>
                                            <th data-key="transaction_type" data-type="text" data-filter="select">Type</th>
                                            <th class="text-end" data-key="quantity" data-type="num">Quantity</th>
                                            <th class="text-end" data-key="price" data-type="num">Price</th>
                                            <th data-key="currency" data-type="text">Currency</th>
                                            <th class="text-end" data-key="total_amount" data-type="num">Total</th>
                                            <th class="text-end" data-key="fees" data-type="num">Fees</th>
                                            <th class="pe-3">Actions</th>
```

- [ ] **Step 2: Route the transactions render through the helper, keeping the server broker filter.** In `loadTransactionsPage`, the existing code fetches `getTransactions(500, selectedPortfolioId)` (broker filter — KEEP), then sorts by date desc and renders. Replace **only the date-sort line and the tbody render** with helper wiring: store the fetched rows in `this._txRows` (the broker filter already applied server-side), define `renderTxRow` from the existing row template (verbatim, keep `esc()`), and wire:
```javascript
                this._txRows = transactions;  // already broker-filtered server-side
                const txEmpty = '<tr><td colspan="10" class="text-center text-muted">No transactions found.</td></tr>';
                this._txST = this._txST || makeSortableTable({
                    table: document.querySelector('#transactionsPage table'),
                    columns: [
                        { key: 'transaction_date', type: 'date' }, { key: 'portfolio_name', type: 'text' },
                        { key: 'symbol', type: 'text' }, { key: 'transaction_type', type: 'text', filter: 'select' },
                        { key: 'quantity', type: 'num' }, { key: 'price', type: 'num' },
                        { key: 'currency', type: 'text' }, { key: 'total_amount', type: 'num' },
                        { key: 'fees', type: 'num' }, { key: null },
                    ],
                    getRows: () => this._txRows,
                    renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderTxRow).join('') : txEmpty; },
                    prefsKey: 'transactions',
                });
                this._txST.refresh();
```
Remove the old `rows.sort((a, b) => String(b.date)...)` line and the old manual tbody assignment. Define `const renderTxRow = (tx) => \`...\`;` from the existing transaction row template (move verbatim). The default sort seeded by the helper for `prefsKey:'transactions'` will be the first column (`transaction_date`, `desc`) — matching today's date-DESC behaviour.

**Filter scope note:** the spec listed `asset_type` + `transaction_type` filters for transactions, but the transactions table has no asset-type column (its "Type" column is `transaction_type`). In the column-anchored filter-row model there's nowhere to attach an `asset_type` filter, so this task adds only the **`transaction_type`** filter (on the "Type" column). This is an intentional refinement of the spec — do not add a free-floating asset_type dropdown.

- [ ] **Step 3: JS tests pass + commit:**
```bash
node --test web_client/js/tests/
git add web_client/index.html web_client/js/pfm_pages.js
git commit -m "feat(web): sortable/filterable Transactions table (broker filter stays server-side)"
```

---

### Task 6: Wire the Brokers table

**Files:**
- Modify: `web_client/index.html` (portfolios `<thead>` ~1175–1181)
- Modify: `web_client/js/pfm_pages.js` (`loadPortfoliosPage` ~647)

The Brokers table is sort-only (no filter). Note: its numeric values (`value_eur`, `pnl_eur`) come from a **separate** `valByName[p.name]` join, not the portfolio row object, so the rows must be merged before sorting.

- [ ] **Step 1: Add `data-key`/`data-type` to the brokers header.** In `web_client/index.html`, the `#portfoliosTable` `<thead>` row is:
```html
                                            <th class="ps-3">Broker</th>
                                            <th>Currency</th>
                                            <th class="text-end">Value (EUR)</th>
                                            <th class="text-end">P&amp;L</th>
                                            <th>Activity <span class="text-muted fw-normal small">(first → last)</span></th>
                                            <th>Description</th>
                                            <th class="pe-3">Actions</th>
```
Replace with:
```html
                                            <th class="ps-3" data-key="name" data-type="text">Broker</th>
                                            <th data-key="base_currency" data-type="text">Currency</th>
                                            <th class="text-end" data-key="value_eur" data-type="num">Value (EUR)</th>
                                            <th class="text-end" data-key="pnl_eur" data-type="num">P&amp;L</th>
                                            <th data-key="last_transaction_date" data-type="date">Activity <span class="text-muted fw-normal small">(first → last)</span></th>
                                            <th data-key="description" data-type="text">Description</th>
                                            <th class="pe-3">Actions</th>
```

- [ ] **Step 2: Route the render through the helper.** In `loadPortfoliosPage`, keep the data fetch, `valByName`, and the helper closures (`eur`, `pnlCell`, the local `esc`, `range`). Replace the `} else {` body — currently `tableBody.innerHTML = portfolios.map(p => { ... }).join(''); if (footer && ...) { footer.innerHTML = ...; }` — with the version below. It defines `renderBrokerRow` from the **exact existing row template** (verbatim — it closes over `valByName`/`eur`/`pnlCell`/`range`/the local `esc`), merges the value fields onto the rows for sorting, wires the helper, and keeps the footer block unchanged:
```javascript
                } else {
                    const renderBrokerRow = (p) => {
                        const v = valByName[p.name];
                        const site = p.website
                            ? ` <a href="${p.website}" target="_blank" rel="noopener" title="${p.website}${p.website_is_default ? ' (suggested)' : ''}" class="text-decoration-none"><i class="bi bi-box-arrow-up-right small ${p.website_is_default ? 'text-muted' : ''}"></i></a>`
                            : '';
                        const activity = `
                            <div class="small"><i class="bi bi-graph-up me-1 text-muted" title="Transactions"></i>${range(p.first_transaction_date, p.last_transaction_date)}</div>
                            <div class="small"><i class="bi bi-cash-stack me-1 text-muted" title="Cash deposits/withdrawals"></i>${range(p.first_booking_date, p.last_booking_date)}</div>`;
                        return `
                        <tr>
                            <td class="ps-3"><strong>${esc(p.name)}</strong>${site}</td>
                            <td>${p.base_currency || ''}</td>
                            <td class="text-end">${v ? eur(v.value_eur) : '<span class="text-muted">—</span>'}${v && Math.abs(v.cash_eur || 0) >= 1 ? `<div class="small text-muted" title="Cash balance (deposits − withdrawals + sells − buys + dividends)"><i class="bi bi-cash-coin me-1"></i>${eur(v.cash_eur)} cash</div>` : ''}</td>
                            ${pnlCell(v)}
                            <td>${activity}</td>
                            <td><small class="text-muted">${esc(p.description || '')}</small></td>
                            <td class="pe-3">
                                <button class="btn btn-sm btn-outline-primary me-1" title="Edit" onclick="editPortfolio(${p.id}, '${esc(p.name)}', '${p.base_currency || 'EUR'}', '${esc(p.description)}', '${esc(p.website)}')">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" title="Delete" onclick="deletePortfolio(${p.id}, '${esc(p.name)}')">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </td>
                        </tr>`;
                    };
                    // Merge per-broker EUR values onto the rows so the shared table can
                    // sort by value_eur / pnl_eur (they otherwise live on valByName).
                    this._brokerRows = portfolios.map(p => {
                        const v = valByName[p.name] || {};
                        return Object.assign({}, p, {
                            value_eur: v.value_eur == null ? null : v.value_eur,
                            pnl_eur: v.pnl_eur == null ? null : v.pnl_eur,
                        });
                    });
                    this._brokersST = this._brokersST || makeSortableTable({
                        table: document.getElementById('portfoliosTable'),
                        columns: [
                            { key: 'name', type: 'text' }, { key: 'base_currency', type: 'text' },
                            { key: 'value_eur', type: 'num' }, { key: 'pnl_eur', type: 'num' },
                            { key: 'last_transaction_date', type: 'date' }, { key: 'description', type: 'text' },
                            { key: null },
                        ],
                        getRows: () => this._brokerRows,
                        renderRows: (rows, tbody) => { tbody.innerHTML = rows.length ? rows.map(renderBrokerRow).join('') : '<tr><td colspan="7" class="text-center text-muted">No brokers.</td></tr>'; },
                        prefsKey: 'portfolios',
                    });
                    this._brokersST.refresh();

                    if (footer && (values.total_value_eur || 0) > 0) {
                        const tp = values.total_pnl_eur;
                        const tcls = tp >= 0 ? 'text-success' : 'text-danger';
                        const tsign = tp >= 0 ? '+' : '';
                        const cash = values.total_cash_eur || 0;
                        const nw = values.total_networth_eur != null ? values.total_networth_eur : (values.total_value_eur + cash);
                        footer.innerHTML = `<tr class="fw-bold border-top">
                            <td class="ps-3">Total holdings</td><td></td>
                            <td class="text-end">${eur(values.total_value_eur)}</td>
                            <td class="text-end ${tcls}">${tsign}${eur(tp)}</td>
                            <td colspan="3"></td>
                        </tr>
                        <tr class="text-muted">
                            <td class="ps-3">+ Cash</td><td></td>
                            <td class="text-end">${eur(cash)}</td>
                            <td colspan="4"></td>
                        </tr>
                        <tr class="fw-bold">
                            <td class="ps-3">Net worth</td><td></td>
                            <td class="text-end">${eur(nw)}</td>
                            <td colspan="4"></td>
                        </tr>`;
                    }
                }
```
(`renderBrokerRow` deliberately reuses the existing local `esc` JS-string escaper for parity with the current row — do not change its escaping in this task.)

- [ ] **Step 3: JS tests pass + commit:**
```bash
node --test web_client/js/tests/
git add web_client/index.html web_client/js/pfm_pages.js
git commit -m "feat(web): sortable Brokers table"
```

---

### Task 7: Verify end-to-end + docs

**Files:**
- Modify: `CLAUDE.md` (Web Client section)

- [ ] **Step 1: JS tests + full backend suite (regression):**
```bash
node --test web_client/js/tests/
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```
Expected: 14 JS pass; backend 435 passed, 6 skipped (unchanged — no backend edits this feature).

- [ ] **Step 2: Rebuild + redeploy web, manual smoke:**
```bash
WEB_PORT=8080 docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On each of Holdings / Transactions / Assets / Brokers: click several column headers (arrow flips ▲/▼, order changes for text/number/date), use the Type / Tx-type filter dropdowns, and reload to confirm the sort + filter persisted (PREFS). Confirm the Transactions broker dropdown still refetches and the table re-sorts.

- [ ] **Step 3: Update CLAUDE.md.** In the `## Web Client (web_client/)` section, add a sentence after the PREFS paragraph:
```
**Sortable/filterable tables**: Holdings, Transactions, Assets and Brokers use the shared `makeSortableTable(config)` helper (pure `applyTableState(rows, columns, state)` core, both in `pfm_core.js`): clickable `<th data-key data-type [data-filter=select]>` headers toggle sort (▲/▼), a filter row provides categorical dropdowns, and per-table state persists in `PREFS.tableState[<page>]`. The Transactions broker filter stays a server refetch (`getTransactions(500, portfolioId)`); the dashboard Top Positions keeps its own specialised control bar.
```

- [ ] **Step 4: Commit:**
```bash
git add CLAUDE.md
git commit -m "docs: note shared sortable/filterable tables in CLAUDE.md"
```

---

## Notes for the implementer
- **Column order matters**: each table's `columns` array must list one entry per `<th>` in left-to-right order (use `{ key: null }` for Actions/Links/Research) so the filter row's cells line up under the right headers.
- Sort uses `data-type` on the `<th>` and the matching `type` in the `columns` config — keep them consistent.
- Reuse each table's existing row HTML verbatim as the `renderRows`/`renderXRow` body; do not rewrite it (untrusted fields already use `esc()`).
- `makeSortableTable` is created once per page (guarded by `this._<x>ST ||=` pattern) and `.refresh()`ed on each load; the `data-sort-wired` guard prevents duplicate header wiring.
- Bump the `pfm_*.js` `?v=` cache-buster once (Task 3) — later tasks in the same branch don't need to bump again, but re-bump before the final web rebuild if browsers cached.
