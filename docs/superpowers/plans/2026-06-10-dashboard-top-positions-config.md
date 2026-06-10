# Dashboard Top Positions Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add filter/sort controls (N, asset type, broker, and five sort modes) to the dashboard's Top Positions card, persisted in browser `PREFS`.

**Architecture:** N / type / sort run client-side on the holdings the dashboard already fetches; the broker filter adds an optional `?portfolio_id=` to `GET /portfolios/holdings` (reusing `get_all_transactions(portfolio_id=)`). A pure, DOM-free `topPositions()` helper does the filter+sort so it's unit-testable in the Node vm harness.

**Tech Stack:** FastAPI (Python 3.13, pytest), vanilla JS classic scripts (Node built-in `node --test`), Bootstrap 5.

Spec: `docs/superpowers/specs/2026-06-10-dashboard-top-positions-config-design.md`

---

### Task 1: Backend — optional `portfolio_id` on `/portfolios/holdings`

**Files:**
- Modify: `portf_server/routers/portfolios.py` (imports line 9; `get_holdings` at 342–345)
- Test: `tests/unit/test_routers_coverage.py` (append to `class TestPortfolioHoldings`, after line 317)

- [ ] **Step 1: Write the failing test**

Append this method inside `class TestPortfolioHoldings` in `tests/unit/test_routers_coverage.py`:

```python
    @pytest.mark.asyncio
    async def test_holdings_filtered_by_portfolio_id(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        """holdings?portfolio_id= returns only that broker's positions."""
        # Two brokers.
        pa = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "BrokerA", "base_currency": "EUR"},
            headers=auth_headers,
        )
        pb = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "BrokerB", "base_currency": "EUR"},
            headers=auth_headers,
        )
        pa_id, pb_id = pa.json()["id"], pb.json()["id"]

        # One asset, bought in BOTH brokers.
        asset = await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        asset_id = asset.json()["id"]
        for pid, qty in ((pa_id, 5.0), (pb_id, 3.0)):
            await async_test_client.post(
                "/api/v1/transactions",
                json={
                    "asset_id": asset_id,
                    "portfolio_id": pid,
                    "transaction_type": "buy",
                    "quantity": qty,
                    "price": 100.0,
                    "total_amount": qty * 100.0,
                    "transaction_date": "2024-06-01",
                },
                headers=auth_headers,
            )

        # Aggregated (no filter) = 5 + 3 = 8 units.
        all_resp = await async_test_client.get(
            "/api/v1/portfolios/holdings", headers=auth_headers
        )
        all_qty = next(
            h["quantity"]
            for h in all_resp.json()["holdings"]
            if h["asset_id"] == asset_id
        )
        assert all_qty == 8.0

        # Filtered to BrokerA = only 5 units.
        a_resp = await async_test_client.get(
            f"/api/v1/portfolios/holdings?portfolio_id={pa_id}",
            headers=auth_headers,
        )
        a_qty = next(
            h["quantity"]
            for h in a_resp.json()["holdings"]
            if h["asset_id"] == asset_id
        )
        assert a_qty == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_routers_coverage.py::TestPortfolioHoldings::test_holdings_filtered_by_portfolio_id -v`
Expected: FAIL — aggregated query ignores the param, so BrokerA returns 8.0, not 5.0 (assertion error on `a_qty == 5.0`).

- [ ] **Step 3: Add the `Query` import**

In `portf_server/routers/portfolios.py` line 9, add `Query`:

```python
from fastapi import APIRouter, HTTPException, status, Depends, Request, Query
```

- [ ] **Step 4: Add the optional param and forward it**

Replace the `get_holdings` signature + first data line (currently lines 342–351):

```python
@router.get("/holdings")
def get_holdings(
    portfolio_id: Optional[int] = Query(
        None, description="Filter positions to a single broker/portfolio"
    ),
    database: Database = Depends(get_database),
):
    """Get current holdings (positions with total value) computed from transactions.

    Sync (plain ``def``) so the blocking FX lookups in ``_get_fx_rate`` run in
    the threadpool rather than stalling the event loop on a cache miss.

    When ``portfolio_id`` is given, positions are computed from only that
    broker's transactions (a multi-broker asset shows just that broker's slice).
    """
    transactions = database.get_all_transactions(portfolio_id=portfolio_id)
```

(`Optional` is already imported at the top of the file via `from typing import Optional`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_routers_coverage.py::TestPortfolioHoldings -v`
Expected: PASS (3 tests in the class).

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/portfolios.py tests/unit/test_routers_coverage.py
git commit -m "feat(api): optional portfolio_id filter on /portfolios/holdings"
```

---

### Task 2: JS — pure `topPositions()` helper + tests

**Files:**
- Modify: `web_client/js/pfm_core.js` (add helper right after the `esc()` block, ~line 69)
- Test: `web_client/js/tests/web_client.test.mjs` (append tests, reuse `loadAppIntoContext`)

- [ ] **Step 1: Write the failing tests**

Append to `web_client/js/tests/web_client.test.mjs`:

```javascript
const SAMPLE_HOLDINGS = [
    { symbol: "A", asset_type: "stock", quantity: 1, total_value_eur: 100, pnl_pct: 5, pnl_amount: 10 },
    { symbol: "B", asset_type: "crypto", quantity: 2, total_value_eur: 300, pnl_pct: -8, pnl_amount: -40 },
    { symbol: "C", asset_type: "stock", quantity: 3, total_value_eur: 200, pnl_pct: 20, pnl_amount: 15 },
    { symbol: "D", asset_type: "etf", quantity: 0, total_value_eur: 999, pnl_pct: 99, pnl_amount: 99 },
];

test("topPositions: drops zero-qty, sorts by value desc, slices N", () => {
    const { topPositions } = loadAppIntoContext();
    const r = topPositions(SAMPLE_HOLDINGS, { n: 2, type: "all", sort: "value" });
    assert.deepEqual(r.map((h) => h.symbol), ["B", "C"]); // D dropped (qty 0)
});

test("topPositions: each sort mode orders correctly", () => {
    const { topPositions } = loadAppIntoContext();
    const syms = (sort) =>
        topPositions(SAMPLE_HOLDINGS, { n: "all", type: "all", sort }).map((h) => h.symbol);
    assert.deepEqual(syms("value"), ["B", "C", "A"]);        // 300,200,100
    assert.deepEqual(syms("gain_pct"), ["C", "A", "B"]);     // 20,5,-8
    assert.deepEqual(syms("loss_pct"), ["B", "A", "C"]);     // -8,5,20
    assert.deepEqual(syms("gain_total"), ["C", "A", "B"]);   // 15,10,-40
    assert.deepEqual(syms("loss_total"), ["B", "A", "C"]);   // -40,10,15
});

test("topPositions: type filter + N='all'", () => {
    const { topPositions } = loadAppIntoContext();
    const r = topPositions(SAMPLE_HOLDINGS, { n: "all", type: "stock", sort: "value" });
    assert.deepEqual(r.map((h) => h.symbol), ["C", "A"]);
});

test("topPositions: loss sort still returns rows when nothing is negative", () => {
    const { topPositions } = loadAppIntoContext();
    const winners = [
        { symbol: "X", quantity: 1, total_value_eur: 1, pnl_pct: 5, pnl_amount: 5 },
        { symbol: "Y", quantity: 1, total_value_eur: 1, pnl_pct: 2, pnl_amount: 2 },
    ];
    const r = topPositions(winners, { n: 5, type: "all", sort: "loss_pct" });
    assert.deepEqual(r.map((h) => h.symbol), ["Y", "X"]); // least-positive first
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test web_client/js/tests/`
Expected: the 4 new tests FAIL with `topPositions is not a function` (helper not defined yet).

- [ ] **Step 3: Implement the helper**

In `web_client/js/pfm_core.js`, immediately after the `window.esc = esc;` line (~line 69), add:

```javascript
// Pure, DOM-free filter+sort for the dashboard Top Positions card (unit-tested
// in web_client/js/tests/). Drops zero/negative-quantity positions, filters by
// asset type, sorts by the chosen mode, then takes the top N.
function _topVal(h) { return parseFloat(h.total_value_eur || h.total_value || 0); }
function _topPct(h) { return parseFloat(h.pnl_pct || 0); }
function _topAmt(h) { return parseFloat(h.pnl_amount || 0); }
const TOP_POSITION_SORTS = {
    value:      (a, b) => _topVal(b) - _topVal(a),
    gain_pct:   (a, b) => _topPct(b) - _topPct(a),
    loss_pct:   (a, b) => _topPct(a) - _topPct(b),
    gain_total: (a, b) => _topAmt(b) - _topAmt(a),
    loss_total: (a, b) => _topAmt(a) - _topAmt(b),
};
function topPositions(holdings, opts) {
    const { n = 5, type = 'all', sort = 'value' } = opts || {};
    let list = (holdings || []).filter(h => parseFloat(h.quantity || 0) > 0);
    if (type && type !== 'all') {
        list = list.filter(h => (h.asset_type || 'other') === type);
    }
    list = list.slice().sort(TOP_POSITION_SORTS[sort] || TOP_POSITION_SORTS.value);
    if (n === 'all' || n == null) return list;
    return list.slice(0, Number(n));
}
window.topPositions = topPositions;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test web_client/js/tests/`
Expected: PASS (9 total: 5 existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_core.js web_client/js/tests/web_client.test.mjs
git commit -m "feat(web): pure topPositions() filter+sort helper with tests"
```

---

### Task 3: JS — PREFS default + `getHoldings(portfolioId)`

**Files:**
- Modify: `web_client/js/pfm_core.js` (`PREFS_DEFAULTS` ~line 13–26; `getHoldings` ~line 712)

- [ ] **Step 1: Add the PREFS default**

In `PREFS_DEFAULTS` (pfm_core.js), add a key before the closing `}` (after `hideBelowEur`):

```javascript
    hideBelowEur: 0,          // hide holdings below this EUR value (0 = show all)
    dashTopPositions: { n: 5, type: 'all', broker: 'all', sort: 'value' },
};
```

- [ ] **Step 2: Make `getHoldings` accept a portfolio id**

Replace the `getHoldings` method body (pfm_core.js ~712–723):

```javascript
        async getHoldings(portfolioId = null) {
            try {
                const q = (portfolioId != null && portfolioId !== 'all')
                    ? `?portfolio_id=${encodeURIComponent(portfolioId)}` : '';
                const response = await fetch(this.baseURL + '/api/v1/portfolios/holdings' + q, {
                    headers: { 'X-API-Key': this.apiKey }
                });
                const data = await response.json();
                return data;
            } catch (error) {
                console.error('Error loading holdings:', error);
                return { holdings: [], summary: {} };
            }
        },
```

- [ ] **Step 3: Verify the app still loads (smoke test)**

Run: `node --test web_client/js/tests/`
Expected: PASS (9 tests) — the load/smoke test confirms `pfm_core.js` still parses and `PREFS.dashTopPositions` exists (it's covered indirectly; the load test asserts `PREFS.defaultCurrency`).

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat(web): dashTopPositions PREFS default + getHoldings(portfolioId)"
```

---

### Task 4: HTML — control row in the Top Positions card

**Files:**
- Modify: `web_client/index.html` (Top Positions card header ~349–353; P&L header cell ~366)

- [ ] **Step 1: Add the gear toggle + collapsible control row**

Replace the card-header block (the `<div class="card-header ...">` containing "Top Positions" and the refresh button, index.html ~349–354) with:

```html
                                <div class="card-header">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <span><i class="bi bi-bar-chart-steps me-2"></i>Top Positions</span>
                                        <div class="btn-group btn-group-sm">
                                            <button class="btn btn-outline-secondary" id="dashTopConfigBtn" title="Configure">
                                                <i class="bi bi-sliders"></i>
                                            </button>
                                            <button class="btn btn-outline-secondary" id="refreshTopPositions" title="Refresh top positions">
                                                <i class="bi bi-arrow-clockwise"></i>
                                            </button>
                                        </div>
                                    </div>
                                    <div id="dashTopControls" class="row g-2 mt-2 small" style="display:none;">
                                        <div class="col-6 col-sm-3">
                                            <label class="form-label mb-0 text-muted">Show</label>
                                            <select id="dashTopN" class="form-select form-select-sm">
                                                <option value="5">Top 5</option>
                                                <option value="10">Top 10</option>
                                                <option value="15">Top 15</option>
                                                <option value="20">Top 20</option>
                                                <option value="all">All</option>
                                            </select>
                                        </div>
                                        <div class="col-6 col-sm-3">
                                            <label class="form-label mb-0 text-muted">Type</label>
                                            <select id="dashTopType" class="form-select form-select-sm">
                                                <option value="all">All</option>
                                            </select>
                                        </div>
                                        <div class="col-6 col-sm-3">
                                            <label class="form-label mb-0 text-muted">Broker</label>
                                            <select id="dashTopBroker" class="form-select form-select-sm">
                                                <option value="all">All</option>
                                            </select>
                                        </div>
                                        <div class="col-6 col-sm-3">
                                            <label class="form-label mb-0 text-muted">Sort</label>
                                            <select id="dashTopSort" class="form-select form-select-sm">
                                                <option value="value">Value</option>
                                                <option value="gain_pct">Gain %</option>
                                                <option value="loss_pct">Loss %</option>
                                                <option value="gain_total">Gain total</option>
                                                <option value="loss_total">Loss total</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>
```

- [ ] **Step 2: Give the P&L column header an id**

In the same card's `<thead>`, replace the P&L header cell (index.html ~366):

```html
                                                    <th class="text-end pe-3" id="dashTopPnlHeader">P&amp;L %</th>
```

- [ ] **Step 3: Commit**

```bash
git add web_client/index.html
git commit -m "feat(web): Top Positions config controls markup"
```

---

### Task 5: JS — wire controls + `renderDashTopPositions` in pfm_pages.js

**Files:**
- Modify: `web_client/js/pfm_pages.js` (`loadDashboardPage` top-positions block ~166–194; refresh-button wiring ~287–291)
- Modify: `web_client/index.html` (bump cache-busters)

- [ ] **Step 1: Add module-level state + render/wire functions**

At the **top of `pfm_pages.js`** (after the header comment block, before `function createPageManager`), add:

```javascript
// Dashboard Top Positions state + rendering. _dashHoldingsAll backs the KPIs,
// donut and the table when broker=all; _dashTopHoldings backs the table when a
// broker filter is active (separate per-broker fetch).
let _dashHoldingsAll = [];
let _dashTopHoldings = [];

function _dashTypeBadge(t) {
    return ({ stock: 'bg-primary', etf: 'bg-info', index: 'bg-success', crypto: 'bg-warning text-dark', bond: 'bg-secondary', p2p: 'bg-dark' }[t] || 'bg-secondary');
}

function renderDashTopPositions() {
    const cfg = window.PREFS.dashTopPositions || { n: 5, type: 'all', broker: 'all', sort: 'value' };
    const body = document.querySelector('#dashTopPositionsTable tbody');
    if (!body) return;

    // Right column shows € P&L for the "total" sorts, otherwise % P&L.
    const isTotal = cfg.sort === 'gain_total' || cfg.sort === 'loss_total';
    const hdr = document.getElementById('dashTopPnlHeader');
    if (hdr) hdr.textContent = isTotal ? 'P&L (EUR)' : 'P&L %';

    const rows = topPositions(_dashTopHoldings, { n: cfg.n, type: cfg.type, sort: cfg.sort });
    if (rows.length === 0) {
        body.innerHTML = '<tr><td colspan="4" class="text-center text-muted ps-3 py-3">No positions match this filter.</td></tr>';
        return;
    }
    body.innerHTML = rows.map(h => {
        const valEur = parseFloat(h.total_value_eur || h.total_value || 0);
        const pnlPct = parseFloat(h.pnl_pct || 0);
        const pnlAmt = parseFloat(h.pnl_amount || 0);
        const metric = isTotal ? pnlAmt : pnlPct;
        const cls = metric >= 0 ? 'text-success' : 'text-danger';
        const txt = isTotal
            ? (metric >= 0 ? '+' : '') + metric.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : (metric >= 0 ? '+' : '') + metric.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%';
        const name = h.name || h.symbol || '';
        return `
        <tr>
            <td class="ps-3" style="max-width:220px;">
                <div class="fw-semibold text-truncate" title="${esc(name)}">${esc(name)}</div>
                <div class="small text-muted">${esc(h.symbol || '')} ${assetLinks(h.symbol)}</div>
            </td>
            <td><span class="badge ${_dashTypeBadge(h.asset_type)}">${esc((h.asset_type || '').toUpperCase())}</span></td>
            <td class="text-end">${valEur.toLocaleString(Fmt.loc(), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
            <td class="text-end pe-3 ${cls} fw-semibold">${txt}</td>
        </tr>`;
    }).join('');
}

// Re-fetch the table's backing data if a broker filter is active, else reuse
// the all-broker holdings; then render.
async function refreshDashTopHoldings() {
    const cfg = window.PREFS.dashTopPositions || {};
    if (cfg.broker && cfg.broker !== 'all') {
        const data = await window.apiClient.getHoldings(cfg.broker);
        _dashTopHoldings = (data && data.holdings) || [];
    } else {
        _dashTopHoldings = _dashHoldingsAll;
    }
    renderDashTopPositions();
}

// Populate the Type (from current holdings) and Broker (from portfolios) selects
// and apply saved values. Wire change handlers once.
async function setupDashTopControls() {
    const cfg = window.PREFS.dashTopPositions || { n: 5, type: 'all', broker: 'all', sort: 'value' };
    const elN = document.getElementById('dashTopN');
    const elType = document.getElementById('dashTopType');
    const elBroker = document.getElementById('dashTopBroker');
    const elSort = document.getElementById('dashTopSort');
    if (!elN || !elType || !elBroker || !elSort) return;

    // Type options from the asset types actually present.
    const types = [...new Set(_dashHoldingsAll
        .filter(h => parseFloat(h.quantity || 0) > 0)
        .map(h => h.asset_type || 'other'))].sort();
    elType.innerHTML = '<option value="all">All</option>' +
        types.map(t => `<option value="${esc(t)}">${esc(t.toUpperCase())}</option>`).join('');

    // Broker options from portfolios.
    try {
        const ps = await window.apiClient.getPortfolios();
        const list = Array.isArray(ps) ? ps : (ps.portfolios || []);
        elBroker.innerHTML = '<option value="all">All</option>' +
            list.map(p => `<option value="${esc(String(p.id))}">${esc(p.name || '')}</option>`).join('');
    } catch (e) { /* keep just "All" */ }

    // Apply saved values (fall back to defaults if an option no longer exists).
    elN.value = String(cfg.n);
    elType.value = [...elType.options].some(o => o.value === cfg.type) ? cfg.type : 'all';
    elBroker.value = [...elBroker.options].some(o => o.value === String(cfg.broker)) ? String(cfg.broker) : 'all';
    elSort.value = cfg.sort;

    // Wire once.
    const save = (patch, refetch) => {
        window.PREFS.dashTopPositions = Object.assign({}, window.PREFS.dashTopPositions, patch);
        savePrefs();
        if (refetch) refreshDashTopHoldings(); else renderDashTopPositions();
    };
    if (!elN._bound) { elN._bound = true; elN.addEventListener('change', () => save({ n: elN.value }, false)); }
    if (!elType._bound) { elType._bound = true; elType.addEventListener('change', () => save({ type: elType.value }, false)); }
    if (!elSort._bound) { elSort._bound = true; elSort.addEventListener('change', () => save({ sort: elSort.value }, false)); }
    if (!elBroker._bound) { elBroker._bound = true; elBroker.addEventListener('change', () => save({ broker: elBroker.value }, true)); }

    const gear = document.getElementById('dashTopConfigBtn');
    const panel = document.getElementById('dashTopControls');
    if (gear && panel && !gear._bound) {
        gear._bound = true;
        gear.addEventListener('click', () => {
            panel.style.display = panel.style.display === 'none' ? '' : 'none';
        });
    }
}
```

- [ ] **Step 2: Replace the old top-5 block in `loadDashboardPage`**

In `pfm_pages.js`, replace the entire `// --- Top 5 positions table ---` block (currently lines 165–194, from the comment through the closing `}` of `if (topBody)`) with:

```javascript
            // --- Top positions (configurable) ---
            _dashHoldingsAll = holdings;
            await setupDashTopControls();
            await refreshDashTopHoldings();
```

- [ ] **Step 3: Keep the refresh button working**

The existing refresh wiring (`refreshTopPositions` → `this.loadDashboardPage()`) at ~287–291 still works unchanged — leave it. No edit needed.

- [ ] **Step 4: Bump the cache-busters**

In `web_client/index.html`, change the four `?v=1780000041` on the `pfm_*.js` tags to `?v=1780000042`.

Run: `grep -c "pfm_.*?v=1780000042" web_client/index.html`
Expected: `4`

- [ ] **Step 5: Run JS tests (regression — app still loads)**

Run: `node --test web_client/js/tests/`
Expected: PASS (9 tests). The load/smoke test exercises `pfm_pages.js` parsing with the new top-level functions.

- [ ] **Step 6: Commit**

```bash
git add web_client/js/pfm_pages.js web_client/index.html
git commit -m "feat(web): wire Top Positions config controls to render/refetch"
```

---

### Task 6: Verify end-to-end + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
Expected: all pass (430 passed: 429 prior + the 1 new holdings test), 6 skipped.

- [ ] **Step 2: JS tests + lint**

Run: `node --test web_client/js/tests/ && UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black --check . && UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run ruff check portf_manager portf_server`
Expected: 9 JS tests pass; black + ruff clean.

- [ ] **Step 3: Rebuild + redeploy web, manual smoke**

Run:
```bash
WEB_PORT=8080 docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
Then in the browser (dashboard): open the gear, change **Show** (5→20), **Type**, **Sort** (each of the 5 modes — confirm the P&L column header flips to "P&L (EUR)" for the two "total" modes), and **Broker** (rows change; KPI cards stay whole-portfolio). Reload the page and confirm the controls keep their values (PREFS persistence).

- [ ] **Step 4: Final commit (if any smoke fixes were needed)**

```bash
git add -A
git commit -m "fix(web): Top Positions config smoke-test adjustments"
```
(Skip if no changes.)

---

## Notes for the implementer

- Run all Python tooling with the `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv` prefix (the `.venv` is root-owned).
- The web container bakes files at build time — JS/HTML changes need the rebuild in Task 6 Step 3 to be visible in the browser.
- `getPortfolios()` returns an array (see `pfm_core.js`); the broker-populate code handles both array and `{portfolios:[...]}` shapes defensively.
- Keep the holdings endpoint a plain `def` — do not make it `async`.
