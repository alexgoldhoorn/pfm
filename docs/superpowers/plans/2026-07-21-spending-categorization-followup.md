# Spending Categorization Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let already-imported Spending transactions be categorized after the fact — a manual "Rescan categories" button that re-applies current rules to rows still sitting at `uncategorized`, and an AI-suggestion flow (with a review step before anything is saved) reachable from the existing bulk-select checkboxes on the Spending transaction table.

**Architecture:** One new backend endpoint (`POST /api/v1/spending/rescan-categories`) reusing existing helpers (`_apply_rules`, `db.list_spending_transactions`, `db.update_spending_transaction`) exactly like the existing `rescan-transfers` endpoint. The AI-suggestion half needs **no backend changes** — it's a new frontend caller of the already-existing, already-generic `/suggest-categories` endpoint, plus a client-side dedup-by-description step and a review panel that only writes to the DB when the user clicks Apply.

**Tech Stack:** Python 3.13 / FastAPI (backend), vanilla JS / Bootstrap 5 (frontend, no build step), pytest (backend tests), Node's built-in test runner (frontend pure-function tests).

## Global Constraints

- Code style: **black** (line length 88); comments on the line before the code they describe; type hints on all function signatures; Google-style docstrings (backend only — the frontend files don't use docstrings, follow their existing comment style).
- Never commit real personal/financial data — use fictional names (e.g. "Example Bank", "MERCADONA COMPRA" — already used elsewhere in this codebase's fixtures as a generic merchant example) in all test fixtures.
- `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` must pass after every backend task.
- `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` must report 0 warnings.
- `node --test web_client/js/tests/` (or `make test-js`) must pass after every frontend task.
- Pre-commit runs black + flake8 + autoflake automatically on `git commit`; pre-push runs the full unit suite.
- **Only `uncategorized` rows are ever touched** by rescan-categories — never overwrite a category that was already set by a prior rule match, AI suggestion, or manual edit. This is a hard requirement from the approved spec, not a style preference.
- **Rescan is manual only** — no automatic re-categorization triggered by rule creation. Also from the approved spec.
- Both `PROJECT_STATUS.md` (bump "Last updated" date + a new "Recent" line) and `CLAUDE.md` (Spending Tracking section) must be updated — mandatory project convention.
- Web client changes require a rebuild + redeploy to take effect: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`. Backend changes require `docker exec portf_backend_dev kill -HUP 1` (though the dev container also auto-reloads on file change via watchfiles — HUP is the documented/reliable path, do it anyway).

---

## Task 1: Backend `POST /api/v1/spending/rescan-categories`

**Files:**
- Modify: `portf_server/routers/spending.py` (add new endpoint immediately after the existing `rescan_transfers` endpoint, which currently ends around line 406, right before `@router.get("/rules", ...)`)
- Test: `tests/unit/test_spending_api.py` (add new tests immediately after the existing `test_rescan_transfers` test, which currently ends around line 271)

**Interfaces:**
- Consumes: `_apply_rules(description: str, rules: List[dict]) -> str` (already defined in `spending.py`, first-match-wins substring match), `db.list_spending_rules() -> List[Dict]`, `db.list_spending_transactions(category: str = None, ...) -> List[Dict]` (existing `category` filter param), `db.update_spending_transaction(spending_id: int, **kwargs) -> bool` (existing, accepts `category=` kwarg).
- Produces: `POST /api/v1/spending/rescan-categories` → `{"recategorized": <int>}`. Consumed by Task 2's frontend button.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_spending_api.py`, add immediately after `test_rescan_transfers` (which ends with `assert r.json()["transfers_linked"] == 1`):

```python
def test_rescan_categories_applies_new_rule_to_uncategorized_row(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "MERCADONA COMPRA", -24.50)

    before = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert before[0]["category"] == "uncategorized"

    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["recategorized"] == 1

    after = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert after[0]["category"] == "Groceries"


def test_rescan_categories_does_not_touch_already_categorized_row(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "MERCADONA COMPRA", -24.50, category="Dining"
    )
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.json()["recategorized"] == 0

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()
    row = next(r for r in rows if r["id"] == tx_id)
    assert row["category"] == "Dining"


def test_rescan_categories_zero_matches(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "SOME SHOP", -10.0)

    resp = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert resp.json()["recategorized"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k rescan_categories -v`
Expected: all 3 FAIL with 404 Not Found (the route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `portf_server/routers/spending.py`, immediately after the `rescan_transfers` function (it ends with `return {"transfers_linked": linked}`) and before `@router.get("/rules", ...)`, add:

```python
@router.post("/rescan-categories", response_model=dict)
async def rescan_categories(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Re-apply current spending_rules to every still-uncategorized row.

    Never touches a row that already has a non-"uncategorized" category —
    covers the case where rules are added/edited after rows were imported.
    """
    rules = db.list_spending_rules()
    uncategorized = db.list_spending_transactions(category="uncategorized")
    updated = 0
    for row in uncategorized:
        category = _apply_rules(row["description"], rules)
        if category != "uncategorized":
            db.update_spending_transaction(row["id"], category=category)
            updated += 1
    return {"recategorized": updated}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k rescan_categories -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full spending test file and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v`
Expected: all tests pass (existing tests unaffected).

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings. If `spending.py` isn't black-formatted, run `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/spending.py` first.

- [ ] **Step 6: Run the full unit suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add POST /api/v1/spending/rescan-categories

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Frontend "Rescan categories" button

**Files:**
- Modify: `web_client/index.html:2518` (add a new button next to `#spRescanTransfers`)
- Modify: `web_client/js/pfm_core.js` (add `rescanCategories()` API client method immediately after the existing `rescanTransfers()` method)
- Modify: `web_client/js/pfm_features.js` (wire the new button inside `loadSpendingPage()`, immediately after the existing `rescanBtn` wiring block)

**Interfaces:**
- Consumes: `POST /api/v1/spending/rescan-categories` from Task 1 (already deployed to the dev backend by the time this task runs, since Task 1 is sequenced first).
- Produces: nothing consumed by a later task — this task is self-contained.

This task has no automated test coverage by design, matching this codebase's existing precedent: the sibling `#spRescanTransfers` button (identical wiring pattern) has no unit test either — DOM click-handler wiring in this file isn't unit-tested anywhere in the project; only pure functions extracted to module scope (like `filterSpendingRows`) get `web_client/js/tests/` coverage. Verify manually per Step 4 below instead.

- [ ] **Step 1: Add the button to index.html**

In `web_client/index.html`, find this line (around line 2518):

```html
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanTransfers" title="Re-scan for transfers"><i class="bi bi-arrow-repeat"></i> Re-scan transfers</button>
```

Add a new button immediately after it (same line style/classes, new id/label/icon):

```html
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanTransfers" title="Re-scan for transfers"><i class="bi bi-arrow-repeat"></i> Re-scan transfers</button>
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanCategories" title="Re-apply category rules to uncategorized rows"><i class="bi bi-tags"></i> Rescan categories</button>
```

- [ ] **Step 2: Add the API client method**

In `web_client/js/pfm_core.js`, immediately after the existing `async rescanTransfers() { ... }` method (it ends with `return response.json();` followed by `},`), add:

```javascript
        async rescanCategories() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rescan-categories', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to rescan categories';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
```

- [ ] **Step 3: Wire the button**

In `web_client/js/pfm_features.js`, inside `loadSpendingPage()`, immediately after the existing block that wires `rescanBtn` (it ends with the closing `});` and `}` right before `['spAccountFilter', 'spCategoryFilter', ...`), add:

```javascript
    const rescanCatBtn = document.getElementById('spRescanCategories');
    if (rescanCatBtn && !rescanCatBtn.dataset.wired) {
        rescanCatBtn.dataset.wired = '1';
        rescanCatBtn.addEventListener('click', async () => {
            rescanCatBtn.disabled = true;
            const status = document.getElementById('spRescanStatus');
            if (status) { status.className = 'small text-muted mb-2'; status.textContent = 'Rescanning…'; }
            try {
                const result = await window.apiClient.rescanCategories();
                const n = (result && result.recategorized) || 0;
                await _refreshSpendingData();
                if (status) {
                    status.className = n > 0 ? 'small text-success mb-2' : 'small text-muted mb-2';
                    status.textContent = n > 0
                        ? `Recategorized ${n} row${n === 1 ? '' : 's'}.`
                        : 'No new matches found.';
                }
            } catch (err) {
                if (status) { status.className = 'small text-danger mb-2'; status.textContent = 'Error: ' + err.message; }
                else alert('Error: ' + err.message);
            }
            rescanCatBtn.disabled = false;
        });
    }
```

This reuses the existing `#spRescanStatus` element (shared with the transfers-rescan button) rather than adding a new status line, since the two buttons are never clicked simultaneously.

- [ ] **Step 4: Verify manually**

Run: `node --check web_client/js/pfm_core.js && node --check web_client/js/pfm_features.js`
Expected: both print nothing (syntax OK).

Run: `make test-js`
Expected: all existing tests still pass (this task adds no new ones — confirms the edit didn't break anything else in these two files).

Rebuild and load the page to confirm visually:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
Then open the Spending page in a browser, confirm the "Rescan categories" button appears next to "Re-scan transfers", and clicking it (with at least one uncategorized row and a matching rule present) shows a "Recategorized N row(s)." message and the row's category updates in the table.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_core.js web_client/js/pfm_features.js
git commit -m "feat: add Rescan categories button to the Spending page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: AI suggestions for already-saved rows, with review before applying

**Files:**
- Modify: `web_client/index.html` (add a "Suggest categories (AI)" button to `#spBulkBar`, and a new `#spSuggestReviewPanel` container right after `#spBulkStatus`)
- Modify: `web_client/js/pfm_features.js` (new `dedupSpendingRowsByDescription` pure function exposed on `window`, plus wiring inside `_wireSpBulkActions()` and two new render/apply functions)
- Test: `web_client/js/tests/web_client.test.mjs` (new tests for `dedupSpendingRowsByDescription`)

**Interfaces:**
- Consumes: `window.apiClient.suggestSpendingCategories(rows)` (existing, `web_client/js/pfm_core.js`, already used by the import flow — POSTs `{rows}` to `/api/v1/spending/suggest-categories`, returns `{suggestions: [{description, category, suggested_pattern}]}`), `window.apiClient.createSpendingRule(pattern, category)` (existing), `window.apiClient.updateSpendingCategory(id, category)` (existing), `_selectedSpendingIds() -> number[]` (existing, in `pfm_features.js`), `window._spendingAllRows` (existing global array of saved transaction objects: `{id, portfolio_id, portfolio_name, date, description, amount, currency, category, is_transfer, ...}`), `_refreshSpendingData()` (existing), `esc(s)` (existing global HTML-escaping helper, `pfm_core.js`).
- Produces: `dedupSpendingRowsByDescription(rows: Array<{id, description, date, amount, currency, category}>) -> Array<{description, date, amount, currency, category, ids: number[]}>` — exposed as `window.dedupSpendingRowsByDescription`, tested directly. Not consumed by any other task.

- [ ] **Step 1: Write the failing tests for the dedup helper**

In `web_client/js/tests/web_client.test.mjs`, add near the existing `filterSpendingRows` tests (after the `test("filterSpendingRows: does not mutate input", ...)` block):

```javascript
test("dedupSpendingRowsByDescription: groups rows sharing a description", () => {
    const { dedupSpendingRowsByDescription } = loadAppIntoContext();
    const rows = [
        { id: 1, description: "MERCADONA", date: "2026-01-05", amount: -10, currency: "EUR", category: "uncategorized" },
        { id: 2, description: "MERCADONA", date: "2026-01-06", amount: -12, currency: "EUR", category: "uncategorized" },
        { id: 3, description: "OTHER SHOP", date: "2026-01-07", amount: -5, currency: "EUR", category: "uncategorized" },
    ];
    const groups = dedupSpendingRowsByDescription(rows);
    assert.equal(groups.length, 2);
    const mercadona = groups.find(g => g.description === "MERCADONA");
    assert.deepEqual(mercadona.ids, [1, 2]);
    const other = groups.find(g => g.description === "OTHER SHOP");
    assert.deepEqual(other.ids, [3]);
});

test("dedupSpendingRowsByDescription: empty or missing input returns empty array", () => {
    const { dedupSpendingRowsByDescription } = loadAppIntoContext();
    assert.deepEqual(dedupSpendingRowsByDescription([]), []);
    assert.deepEqual(dedupSpendingRowsByDescription(undefined), []);
});

test("dedupSpendingRowsByDescription: single-row group keeps that row's own fields", () => {
    const { dedupSpendingRowsByDescription } = loadAppIntoContext();
    const rows = [{ id: 5, description: "X", date: "2026-02-01", amount: 100, currency: "USD", category: "uncategorized" }];
    const groups = dedupSpendingRowsByDescription(rows);
    assert.equal(groups.length, 1);
    assert.equal(groups[0].currency, "USD");
    assert.deepEqual(groups[0].ids, [5]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test web_client/js/tests/ 2>&1 | grep -A3 dedupSpendingRowsByDescription`
Expected: failures — `loadAppIntoContext()` destructures `dedupSpendingRowsByDescription` as `undefined` (not yet defined on `window`), so calling it throws `TypeError: dedupSpendingRowsByDescription is not a function`.

- [ ] **Step 3: Implement the dedup helper**

In `web_client/js/pfm_features.js`, immediately after `window.filterSpendingRows = filterSpendingRows;` (right before `async function loadSpendingPage() {`), add:

```javascript
// Groups selected spending rows by description for AI suggestion review —
// one representative per unique description, keeping every matching row's
// id so an accepted suggestion can be applied to all of them at once.
// Cuts LLM cost/latency: a real account can have the same merchant
// description repeated dozens of times.
function dedupSpendingRowsByDescription(rows) {
    const groups = new Map();
    (rows || []).forEach(r => {
        if (!groups.has(r.description)) {
            groups.set(r.description, {
                description: r.description,
                date: r.date,
                amount: r.amount,
                currency: r.currency,
                category: r.category,
                ids: [],
            });
        }
        groups.get(r.description).ids.push(r.id);
    });
    return Array.from(groups.values());
}
window.dedupSpendingRowsByDescription = dedupSpendingRowsByDescription;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test web_client/js/tests/ 2>&1 | tail -15`
Expected: all tests pass (46 existing + 3 new = 49).

- [ ] **Step 5: Commit the dedup helper**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: add dedupSpendingRowsByDescription helper

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

- [ ] **Step 6: Add the button and review panel container to index.html**

In `web_client/index.html`, find the `#spBulkBar` block (around line 2585):

```html
                        <div id="spBulkBar" class="card-body py-2 border-bottom bg-light-subtle" style="display:none;">
                            <div class="d-flex flex-wrap align-items-center gap-2">
                                <span class="small text-muted"><span id="spSelectedCount">0</span> selected</span>
                                <select class="form-select form-select-sm w-auto" id="spBulkCategorySelect"></select>
                                <button class="btn btn-sm btn-outline-primary" id="spBulkRecategorizeBtn">Set category</button>
                                <button class="btn btn-sm btn-outline-danger ms-auto" id="spBulkDeleteBtn"><i class="bi bi-trash me-1"></i>Delete selected</button>
                            </div>
                        </div>
                        <div id="spBulkStatus" class="small text-muted px-3 pt-2"></div>
```

Replace with:

```html
                        <div id="spBulkBar" class="card-body py-2 border-bottom bg-light-subtle" style="display:none;">
                            <div class="d-flex flex-wrap align-items-center gap-2">
                                <span class="small text-muted"><span id="spSelectedCount">0</span> selected</span>
                                <select class="form-select form-select-sm w-auto" id="spBulkCategorySelect"></select>
                                <button class="btn btn-sm btn-outline-primary" id="spBulkRecategorizeBtn">Set category</button>
                                <button class="btn btn-sm btn-outline-info" id="spBulkSuggestBtn"><i class="bi bi-magic me-1"></i>Suggest categories (AI)</button>
                                <button class="btn btn-sm btn-outline-danger ms-auto" id="spBulkDeleteBtn"><i class="bi bi-trash me-1"></i>Delete selected</button>
                            </div>
                        </div>
                        <div id="spBulkStatus" class="small text-muted px-3 pt-2"></div>
                        <div id="spSuggestReviewPanel" class="px-3 pb-2" style="display:none;"></div>
```

- [ ] **Step 7: Wire the button, review panel, and apply/discard logic**

In `web_client/js/pfm_features.js`, inside `_wireSpBulkActions()`, immediately after the existing `delBtn` wiring block (it ends with the closing `});` and `}` right before the function's final closing `}`), add:

```javascript
    const suggestBtn = document.getElementById('spBulkSuggestBtn');
    if (suggestBtn && !suggestBtn.dataset.wired) {
        suggestBtn.dataset.wired = '1';
        suggestBtn.addEventListener('click', async () => {
            const ids = _selectedSpendingIds();
            const allRows = window._spendingAllRows || [];
            const selectedRows = allRows.filter(r => ids.includes(r.id) && r.category === 'uncategorized');
            const status = document.getElementById('spBulkStatus');
            if (!selectedRows.length) {
                if (status) { status.className = 'small text-muted px-3 pt-2'; status.textContent = 'No uncategorized rows selected.'; }
                return;
            }
            suggestBtn.disabled = true;
            if (status) { status.className = 'small text-muted px-3 pt-2'; status.textContent = 'Asking AI for category suggestions…'; }
            try {
                const groups = dedupSpendingRowsByDescription(selectedRows);
                const { suggestions } = await window.apiClient.suggestSpendingCategories(
                    groups.map(g => ({
                        date: g.date, description: g.description, amount: g.amount,
                        currency: g.currency, category: g.category,
                    }))
                );
                const byDesc = new Map(suggestions.map(s => [s.description, s]));
                window._spSuggestGroups = groups
                    .filter(g => byDesc.has(g.description))
                    .map(g => ({
                        ...g,
                        suggestedCategory: byDesc.get(g.description).category,
                        suggestedPattern: byDesc.get(g.description).suggested_pattern,
                    }));
                _renderSpSuggestReviewPanel();
                if (status) { status.textContent = `${window._spSuggestGroups.length} suggestion(s) ready for review below.`; }
            } catch (err) {
                if (status) { status.className = 'small text-danger px-3 pt-2'; status.textContent = err.message; }
            }
            suggestBtn.disabled = false;
        });
    }
```

Then, after the closing `}` of `_wireSpBulkActions()`, add two new top-level functions:

```javascript
function _renderSpSuggestReviewPanel() {
    const panel = document.getElementById('spSuggestReviewPanel');
    if (!panel) return;
    const groups = window._spSuggestGroups || [];
    if (!groups.length) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
    const categories = [...new Set(['uncategorized', 'Transfer', ...(window._spendingAllRows || []).map(r => r.category)])];
    panel.style.display = '';
    panel.innerHTML = `
        <div class="card">
            <div class="card-header small fw-semibold">Review AI suggestions</div>
            <div class="card-body py-2">
                ${groups.map((g, i) => `
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <input type="checkbox" class="form-check-input sp-suggest-check" data-idx="${i}" checked>
                        <span class="small flex-grow-1">${esc(g.description)} <span class="text-muted">(&times;${g.ids.length})</span></span>
                        <select class="form-select form-select-sm w-auto sp-suggest-category" data-idx="${i}">
                            ${categories.map(c => `<option value="${esc(c)}" ${c === g.suggestedCategory ? 'selected' : ''}>${esc(c)}</option>`).join('')}
                        </select>
                    </div>`).join('')}
                <div class="d-flex gap-2 mt-2">
                    <button class="btn btn-sm btn-primary" id="spSuggestApplyBtn">Apply</button>
                    <button class="btn btn-sm btn-outline-secondary" id="spSuggestDiscardBtn">Discard</button>
                </div>
            </div>
        </div>`;
    panel.querySelectorAll('.sp-suggest-category').forEach(sel => {
        sel.addEventListener('change', () => {
            window._spSuggestGroups[parseInt(sel.dataset.idx, 10)].suggestedCategory = sel.value;
        });
    });
    document.getElementById('spSuggestApplyBtn').addEventListener('click', _applySpSuggestions);
    document.getElementById('spSuggestDiscardBtn').addEventListener('click', () => {
        window._spSuggestGroups = [];
        panel.style.display = 'none'; panel.innerHTML = '';
    });
}

async function _applySpSuggestions() {
    const panel = document.getElementById('spSuggestReviewPanel');
    const checks = panel.querySelectorAll('.sp-suggest-check');
    const accepted = Array.from(checks)
        .filter(c => c.checked)
        .map(c => window._spSuggestGroups[parseInt(c.dataset.idx, 10)]);
    const status = document.getElementById('spBulkStatus');
    if (!accepted.length) {
        window._spSuggestGroups = [];
        panel.style.display = 'none'; panel.innerHTML = '';
        return;
    }
    if (status) { status.className = 'small text-muted px-3 pt-2'; status.textContent = 'Applying…'; }
    let succeeded = 0, failed = 0;
    for (const g of accepted) {
        try { await window.apiClient.createSpendingRule(g.suggestedPattern, g.suggestedCategory); }
        catch (e) { /* rule creation failing shouldn't block applying the category itself */ }
        for (const id of g.ids) {
            try { await window.apiClient.updateSpendingCategory(id, g.suggestedCategory); succeeded++; }
            catch (e) { failed++; }
        }
    }
    window._spSuggestGroups = [];
    panel.style.display = 'none'; panel.innerHTML = '';
    await _refreshSpendingData();
    if (status) {
        status.className = failed > 0 ? 'small text-danger px-3 pt-2' : 'small text-success px-3 pt-2';
        status.textContent = failed > 0
            ? `Applied to ${succeeded} row(s), ${failed} failed.`
            : `Applied to ${succeeded} row(s).`;
    }
}
```

- [ ] **Step 8: Verify manually**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing (syntax OK).

Run: `make test-js`
Expected: all 49 tests pass (no regressions from Step 7's wiring changes, since it adds no new pure functions — only DOM wiring, consistent with Task 2's precedent).

Rebuild and load the page to confirm visually:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On the Spending page: select one or more uncategorized rows via the checkboxes, click "Suggest categories (AI)", confirm the review panel appears below the bulk bar with one row per unique description and an editable category dropdown, edit one, click Apply, and confirm (a) the table refreshes with the new categories on the originally-selected rows, and (b) a new rule appears in the Rules card for each accepted suggestion.

- [ ] **Step 9: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: AI category suggestions for already-imported Spending rows

Reuses the existing /suggest-categories endpoint against selected
uncategorized rows (deduplicated by description), with a review
panel — nothing is saved until Apply is clicked. Accepted
suggestions create a new rule each, same as the import flow.

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Documentation updates

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section)
- Modify: `PROJECT_STATUS.md` (header date + new "Recent" line)

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing — final task.

- [ ] **Step 1: Update CLAUDE.md**

In `/home/agoldhoorn/repos/pfm/CLAUDE.md`, find the Spending Tracking section's bullet that starts with `- `GET /api/v1/spending/` (filters: `portfolio_id`, `category`, ...)` — the one listing `GET/POST /api/v1/spending/rules`, `DELETE /api/v1/spending/rules/{id}`, `GET /api/v1/spending/summary?days=30`, `POST /api/v1/spending/rescan-transfers`. Add a new bullet immediately after it:

```markdown
- `POST /api/v1/spending/rescan-categories` — re-applies current `spending_rules` to every row still at `category='uncategorized'` (all accounts), same on-demand pattern as `/rescan-transfers`; never touches a row that already has a non-`uncategorized` category, so it's safe to run after adding/editing rules without risk of overwriting a manually-set or previously-matched category. Web: "Rescan categories" button next to "Re-scan transfers" on the Spending page.
- **AI category suggestions on already-imported rows**: the Spending page's bulk-select checkboxes (used for bulk recategorize/delete) also drive a "Suggest categories (AI)" action — calls the existing `/suggest-categories` endpoint (unchanged; already generic, not import-specific) against the selected `uncategorized` rows, deduplicated client-side by description (`dedupSpendingRowsByDescription` in `pfm_features.js`) to keep the LLM call small when many rows share a merchant. Results open in a review panel (`#spSuggestReviewPanel`, editable per-suggestion category dropdown + include/exclude checkbox) — nothing is written until "Apply", at which point each accepted suggestion both updates the matching row(s)' category and creates a new `spending_rules` row (same "accept creates a rule" behavior as the import flow), so a later Rescan (or the next import) picks up the same merchant without a further AI call.
```

- [ ] **Step 2: Update PROJECT_STATUS.md**

In `/home/agoldhoorn/repos/pfm/PROJECT_STATUS.md`, bump the "Last updated" line to today's date (check the current date rather than assuming — use whatever `date +%F` reports), and insert a new "Recent" line immediately after it, before the current top entry:

```markdown
**Recent (v2.5.24):** **Categorize already-imported Spending rows.** New `POST /api/v1/spending/rescan-categories` re-applies current rules to every row still `uncategorized` (manual button, mirrors the existing Rescan Transfers pattern) — safe to run after adding/editing rules since it never overwrites an already-set category. The Spending page's existing bulk-select checkboxes gained a "Suggest categories (AI)" action: calls the existing (unchanged) `/suggest-categories` endpoint against selected uncategorized rows, deduplicated by description, and opens an editable review panel — nothing saves until you click Apply, at which point accepted suggestions both categorize the matching rows and create a new rule each, so Rescan (or the next import) picks up the same merchant automatically afterward.
```

Use the actual version number following whatever is currently the top entry in the file at implementation time (this plan was written assuming v2.5.23 is current top; if a different feature landed in between, use the next number after that).

- [ ] **Step 3: Verify only docs changed**

Run: `git diff --stat CLAUDE.md PROJECT_STATUS.md`
Expected: both files show changes; confirm via `git status --short` that no other file is modified.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document rescan-categories + AI suggestions for saved rows

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## After this plan ships

Per the project's restart table: `portf_server` changes (Task 1) need `docker exec portf_backend_dev kill -HUP 1`; `web_client/` changes (Tasks 2-3) need `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`. Not part of the plan's tasks themselves — call out to the user separately once implementation is verified.
