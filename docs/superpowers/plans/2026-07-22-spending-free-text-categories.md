# Spending Free-Text Categories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user type a brand-new category anywhere a category is set on the Spending page (per-row edit, bulk recategorize, AI-suggest review panel) instead of being limited to a fixed dropdown of categories that already exist, and reject a blank category server-side.

**Architecture:** One shared `<datalist>` in `index.html`, populated from one new pure helper in `pfm_features.js`, backs three `<input type="text" list="spCategoryList">` fields that replace the existing `<select>` elements. One backend validation addition to the existing `PUT /api/v1/spending/{spending_id}` endpoint, which already backs all three UI entry points.

**Tech Stack:** Python 3.13 / FastAPI (backend), vanilla JS / Bootstrap 5 (frontend, no build step), pytest, Node's built-in `node --test` for JS.

## Global Constraints

- Code style: black (line length 88); comments on the line before the code they describe; type hints on all function signatures; Google-style docstrings.
- `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` must pass after every backend task.
- `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` must report 0 warnings.
- `node --test web_client/js/tests/` (or `make test-js`) must pass after every frontend task.
- Both `PROJECT_STATUS.md` and `CLAUDE.md` must be updated (mandatory project convention).
- Web client changes require rebuild + redeploy: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`. Backend changes: `docker exec portf_backend_dev kill -HUP 1`.

---

## Task 1: Backend — reject a blank category on `PUT /api/v1/spending/{spending_id}`

**Files:**
- Modify: `portf_server/routers/spending.py` (`update_spending_category`, currently lines 360-391)
- Test: `tests/unit/test_spending_api.py` (new test immediately after `test_update_category_missing_row`, which currently ends at line 158 with `assert r.status_code == 404`)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `PUT /api/v1/spending/{spending_id}` now returns 400 with `{"detail": "Category cannot be empty"}` when `body.category` is empty or whitespace-only; on success the persisted/returned category is now trimmed. Consumed by Task 4 (frontend row edit), Task 5 (bulk field), and Task 6 (AI-suggest panel) only insofar as they rely on this endpoint already existing — no other task calls new code from this one.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_spending_api.py`, add immediately after `test_update_category_missing_row` (ends at line 158 with `assert r.status_code == 404`):

```python
def test_update_category_blank_rejected(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "   "}, headers=HEADERS
    )
    assert r.status_code == 400
    unchanged = db.get_spending_transaction(tx_id)
    assert unchanged["category"] != ""


def test_update_category_trims_whitespace(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}",
        json={"category": "  Groceries  "},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["category"] == "Groceries"
    assert db.get_spending_transaction(tx_id)["category"] == "Groceries"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k "blank_rejected or trims_whitespace" -v`
Expected: `test_update_category_blank_rejected` FAILS (gets 200, not 400); `test_update_category_trims_whitespace` FAILS (category stored as `"  Groceries  "`, not trimmed).

- [ ] **Step 3: Add the validation**

In `portf_server/routers/spending.py`, find `update_spending_category`:

```python
    existing = db.get_spending_transaction(spending_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Spending transaction not found")

    update_kwargs = {"category": body.category}
    if body.category != "Transfer" and existing.get("is_transfer"):
        update_kwargs["is_transfer"] = False
        update_kwargs["transfer_link_type"] = None
        update_kwargs["transfer_link_id"] = None

    db.update_spending_transaction(spending_id, **update_kwargs)
    return {"id": spending_id, "category": body.category}
```

Replace with:

```python
    existing = db.get_spending_transaction(spending_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Spending transaction not found")

    category = body.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")

    update_kwargs = {"category": category}
    if category != "Transfer" and existing.get("is_transfer"):
        update_kwargs["is_transfer"] = False
        update_kwargs["transfer_link_type"] = None
        update_kwargs["transfer_link_id"] = None

    db.update_spending_transaction(spending_id, **update_kwargs)
    return {"id": spending_id, "category": category}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k "blank_rejected or trims_whitespace" -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full spending test file and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v`
Expected: all pass, no regressions (in particular `test_update_category`, `test_update_category_clears_transfer_flag`, `test_update_category_non_transfer_row_unaffected` still pass — none of them submit a category needing trimming or rejection).

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings (run `uv run black portf_server/routers/spending.py` first if needed).

- [ ] **Step 6: Run the full unit suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "fix: reject a blank category on PUT /api/v1/spending/{id}

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Frontend — shared category datalist helper

**Files:**
- Modify: `web_client/index.html` (add the `<datalist>` element)
- Modify: `web_client/js/pfm_features.js` (new `_allSpendingCategories` and `_populateSpCategoryDatalist` helpers)
- Test: `web_client/js/tests/web_client.test.mjs` (new tests for `_allSpendingCategories`)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `_allSpendingCategories(rows: Array<{category: string}>, extra?: string[]) -> string[]` (sorted, deduplicated, always includes `'uncategorized'` and `'Transfer'`), `_populateSpCategoryDatalist(categories: string[]) -> void` (fills `#spCategoryList` with `<option>` elements). Both consumed by Tasks 3-6.

- [ ] **Step 1: Add the datalist element**

In `web_client/index.html`, find (around line 2596-2597):

```html
                        <div id="spBulkStatus" class="small text-muted px-3 pt-2"></div>
                        <div id="spSuggestReviewPanel" class="px-3 pb-2" style="display:none;"></div>
```

Replace with:

```html
                        <div id="spBulkStatus" class="small text-muted px-3 pt-2"></div>
                        <div id="spSuggestReviewPanel" class="px-3 pb-2" style="display:none;"></div>
                        <datalist id="spCategoryList"></datalist>
```

- [ ] **Step 2: Write the failing test for `_allSpendingCategories`**

This test file loads all four `web_client/js/*.js` files concatenated into
one `vm` context (see `loadAppIntoContext()` near the top of
`web_client.test.mjs`); every top-level `function` declaration in any of
those files — including underscore-prefixed ones like `_allSpendingCategories`
— becomes a property on the object `loadAppIntoContext()` returns, with no
separate export step needed (this is how `esc`, `topPositions`, etc. are
already tested). Add, in `web_client/js/tests/web_client.test.mjs`, after
the last existing test in the file:

```javascript
test('_allSpendingCategories includes uncategorized, Transfer, and row categories, deduplicated and sorted', () => {
    const { _allSpendingCategories } = loadAppIntoContext();
    const rows = [{ category: 'Groceries' }, { category: 'Transport' }, { category: 'Groceries' }];
    const result = _allSpendingCategories(rows);
    assert.deepStrictEqual(result, ['Groceries', 'Transfer', 'Transport', 'uncategorized']);
});

test('_allSpendingCategories merges in extra categories', () => {
    const { _allSpendingCategories } = loadAppIntoContext();
    const rows = [{ category: 'Groceries' }];
    const result = _allSpendingCategories(rows, ['Kids', 'Groceries']);
    assert.deepStrictEqual(result, ['Groceries', 'Kids', 'Transfer', 'uncategorized']);
});

test('_allSpendingCategories handles empty rows and no extra', () => {
    const { _allSpendingCategories } = loadAppIntoContext();
    const result = _allSpendingCategories([]);
    assert.deepStrictEqual(result, ['Transfer', 'uncategorized']);
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: the three new tests FAIL (`_allSpendingCategories is not defined` or similar — function doesn't exist yet).

- [ ] **Step 4: Add the helpers**

In `web_client/js/pfm_features.js`, find `_populateSpBulkCategorySelect` (currently lines 4401-4405):

```javascript
function _populateSpBulkCategorySelect(categories) {
    const sel = document.getElementById('spBulkCategorySelect');
    if (!sel) return;
    sel.innerHTML = categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
}
```

Add the two new helpers immediately before it, leaving `_populateSpBulkCategorySelect` itself in place for now:

```javascript
function _allSpendingCategories(rows, extra) {
    return [...new Set(['uncategorized', 'Transfer',
        ...rows.map(r => r.category), ...(extra || [])])].sort();
}

function _populateSpCategoryDatalist(categories) {
    const list = document.getElementById('spCategoryList');
    if (!list) return;
    list.innerHTML = categories.map(c => `<option value="${esc(c)}">`).join('');
}

function _populateSpBulkCategorySelect(categories) {
    const sel = document.getElementById('spBulkCategorySelect');
    if (!sel) return;
    sel.innerHTML = categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
}
```

(`_populateSpBulkCategorySelect` still has a call site in `renderRows` until Task 3 rewrites it — removing the function here, before its caller is updated, would leave a broken intermediate commit. Task 3 removes both the call site and this function's definition together. `_allSpendingCategories` and `_populateSpCategoryDatalist` are wired into the render paths in Tasks 3 and 5. No separate export step needed, per Step 2's note.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: the three new tests PASS, and all pre-existing tests still pass (no test in this file references `_populateSpBulkCategorySelect`, so removing it doesn't break anything here).

- [ ] **Step 6: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 7: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: add shared category datalist helper for the Spending page

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Frontend — per-row category cell becomes free text

**Files:**
- Modify: `web_client/js/pfm_features.js` (`renderRows` inside the spending table's `makeSortableTable` call, and `window.updateSpendingRowCategory`)

**Interfaces:**
- Consumes: `_allSpendingCategories`, `_populateSpCategoryDatalist` from Task 2.
- Produces: nothing new consumed by other tasks — `window.updateSpendingRowCategory`'s signature (`id: number, category: string`) is unchanged, only its internal guard changes.

- [ ] **Step 1: Update the per-row category cell and datalist population**

In `web_client/js/pfm_features.js`, find the `renderRows` function passed to `makeSortableTable` (currently around lines 4374-4392):

```javascript
        renderRows: (sorted, tbody) => {
            const categories = [...new Set(['uncategorized', 'Transfer', ...rows.map(r => r.category)])];
            tbody.innerHTML = sorted.length ? sorted.map(r => `
                <tr>
                    <td class="ps-3"><input type="checkbox" class="form-check-input sp-row-check" data-id="${r.id}"></td>
                    <td>${Fmt.date(r.date)}</td>
                    <td>${esc(r.portfolio_name || '')}</td>
                    <td>${esc(r.description)}</td>
                    <td>
                        <select class="form-select form-select-sm d-inline-block" style="width:auto;" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
                            ${categories.map(c => `<option value="${esc(c)}" ${c === r.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
                        </select>
                        ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                    </td>
                    <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
                    <td class="pe-3"></td>
                </tr>`).join('') : '<tr><td colspan="7" class="text-center text-muted py-3">No transactions match the current filters.</td></tr>';
            _populateSpBulkCategorySelect(categories);
            _updateSpBulkBar();
        },
```

Replace with:

```javascript
        renderRows: (sorted, tbody) => {
            const categories = _allSpendingCategories(rows);
            _populateSpCategoryDatalist(categories);
            tbody.innerHTML = sorted.length ? sorted.map(r => `
                <tr>
                    <td class="ps-3"><input type="checkbox" class="form-check-input sp-row-check" data-id="${r.id}"></td>
                    <td>${Fmt.date(r.date)}</td>
                    <td>${esc(r.portfolio_name || '')}</td>
                    <td>${esc(r.description)}</td>
                    <td>
                        <input type="text" list="spCategoryList" class="form-control form-control-sm d-inline-block" style="width:auto;" value="${esc(r.category)}" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
                        ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                    </td>
                    <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
                    <td class="pe-3"></td>
                </tr>`).join('') : '<tr><td colspan="7" class="text-center text-muted py-3">No transactions match the current filters.</td></tr>';
            _updateSpBulkBar();
        },
```

- [ ] **Step 2: Remove the now-unused `_populateSpBulkCategorySelect`**

Its only call site was removed in Step 1. In `web_client/js/pfm_features.js`, find (added in Task 2, immediately after `_populateSpCategoryDatalist`):

```javascript
function _populateSpBulkCategorySelect(categories) {
    const sel = document.getElementById('spBulkCategorySelect');
    if (!sel) return;
    sel.innerHTML = categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
}
```

Delete it entirely.

- [ ] **Step 3: Add the blank/unchanged guard to `updateSpendingRowCategory`**

In `web_client/js/pfm_features.js`, find:

```javascript
window.updateSpendingRowCategory = async function (id, category) {
    try {
        await window.apiClient.updateSpendingCategory(id, category);
        const row = (window._spendingAllRows || []).find(r => r.id === id);
        if (row) row.category = category;
    } catch (err) { alert('Error: ' + err.message); }
};
```

Replace with:

```javascript
window.updateSpendingRowCategory = async function (id, category) {
    const trimmed = category.trim();
    const row = (window._spendingAllRows || []).find(r => r.id === id);
    if (!trimmed || (row && trimmed === row.category)) {
        await _refreshSpendingData();
        return;
    }
    try {
        await window.apiClient.updateSpendingCategory(id, trimmed);
        if (row) row.category = trimmed;
    } catch (err) {
        alert('Error: ' + err.message);
        await _refreshSpendingData();
    }
};
```

(A blank or unchanged value re-renders from the already-fetched data rather than calling the API — this both restores the visible text to the stored value if the user cleared the field, and avoids a no-op write. On a genuine API error, re-rendering likewise restores the field to the last-known-good value instead of leaving the user's edit displayed but not saved.)

- [ ] **Step 4: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 5: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass, including Task 2's three new tests.

- [ ] **Step 6: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: allow typing a new category directly on a spending row

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Frontend — bulk-recategorize field becomes free text

**Files:**
- Modify: `web_client/index.html` (`#spBulkCategorySelect`)
- Modify: `web_client/js/pfm_features.js` (`_wireSpBulkActions`'s recategorize click handler)

**Interfaces:**
- Consumes: `spCategoryList` datalist populated by Task 3's `renderRows` (already runs before the bulk bar is used, since the table renders on every data refresh).
- Produces: nothing new consumed by other tasks.

- [ ] **Step 1: Change the bulk category field from select to text input**

In `web_client/index.html`, find (around line 2590):

```html
                                <select class="form-select form-select-sm w-auto" id="spBulkCategorySelect"></select>
```

Replace with:

```html
                                <input type="text" list="spCategoryList" class="form-control form-control-sm w-auto" id="spBulkCategorySelect" placeholder="Category">
```

- [ ] **Step 2: Trim the value read from the field**

In `web_client/js/pfm_features.js`, find, inside `_wireSpBulkActions`'s `recatBtn` click handler (currently around line 4440):

```javascript
            const category = document.getElementById('spBulkCategorySelect')?.value;
            if (!ids.length || !category) return;
```

Replace with:

```javascript
            const category = document.getElementById('spBulkCategorySelect')?.value.trim();
            if (!ids.length || !category) return;
```

- [ ] **Step 3: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 4: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: allow typing a new category in the bulk-recategorize field

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Frontend — AI-suggest review panel category field becomes free text

**Files:**
- Modify: `web_client/js/pfm_features.js` (`_renderSpSuggestReviewPanel`)

**Interfaces:**
- Consumes: `_allSpendingCategories`, `_populateSpCategoryDatalist` from Task 2.
- Produces: nothing new consumed by other tasks — `window._spSuggestGroups[i].suggestedCategory` keeps the same shape, only how it's edited changes.

- [ ] **Step 1: Update the category field and its listener**

In `web_client/js/pfm_features.js`, find `_renderSpSuggestReviewPanel` (currently lines 4532-4569):

```javascript
function _renderSpSuggestReviewPanel() {
    const panel = document.getElementById('spSuggestReviewPanel');
    if (!panel) return;
    const groups = window._spSuggestGroups || [];
    if (!groups.length) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
    const categories = [...new Set(['uncategorized', 'Transfer',
        ...groups.map(g => g.suggestedCategory),
        ...(window._spendingAllRows || []).map(r => r.category)])];
    panel.style.display = '';
    panel.innerHTML = `
        <div class="card">
            <div class="card-header small fw-semibold">Review AI suggestions</div>
            <div class="card-body py-2">
                ${groups.map((g, i) => `
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <input type="checkbox" class="form-check-input sp-suggest-check" data-idx="${i}" checked>
                        <span class="small flex-grow-1">${esc(g.description)} <span class="text-muted">(&times;${g.ids.length})</span></span>
                        <input type="text" class="form-control form-control-sm sp-suggest-pattern" style="max-width:160px;" data-idx="${i}" value="${escapeForAttr(g.suggestedPattern)}" title="Rule pattern (matches as a substring)">
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
    panel.querySelectorAll('.sp-suggest-pattern').forEach(inp => {
        inp.addEventListener('input', () => {
            window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedPattern = inp.value;
        });
    });
```

Replace with:

```javascript
function _renderSpSuggestReviewPanel() {
    const panel = document.getElementById('spSuggestReviewPanel');
    if (!panel) return;
    const groups = window._spSuggestGroups || [];
    if (!groups.length) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
    const categories = _allSpendingCategories(
        window._spendingAllRows || [], groups.map(g => g.suggestedCategory));
    _populateSpCategoryDatalist(categories);
    panel.style.display = '';
    panel.innerHTML = `
        <div class="card">
            <div class="card-header small fw-semibold">Review AI suggestions</div>
            <div class="card-body py-2">
                ${groups.map((g, i) => `
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <input type="checkbox" class="form-check-input sp-suggest-check" data-idx="${i}" checked>
                        <span class="small flex-grow-1">${esc(g.description)} <span class="text-muted">(&times;${g.ids.length})</span></span>
                        <input type="text" class="form-control form-control-sm sp-suggest-pattern" style="max-width:160px;" data-idx="${i}" value="${escapeForAttr(g.suggestedPattern)}" title="Rule pattern (matches as a substring)">
                        <input type="text" list="spCategoryList" class="form-control form-control-sm w-auto sp-suggest-category" data-idx="${i}" value="${escapeForAttr(g.suggestedCategory)}">
                    </div>`).join('')}
                <div class="d-flex gap-2 mt-2">
                    <button class="btn btn-sm btn-primary" id="spSuggestApplyBtn">Apply</button>
                    <button class="btn btn-sm btn-outline-secondary" id="spSuggestDiscardBtn">Discard</button>
                </div>
            </div>
        </div>`;
    panel.querySelectorAll('.sp-suggest-pattern').forEach(inp => {
        inp.addEventListener('input', () => {
            window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedPattern = inp.value;
        });
    });
```

- [ ] **Step 2: Update the category listener**

Immediately after the block from Step 1, find:

```javascript
    panel.querySelectorAll('.sp-suggest-category').forEach(sel => {
        sel.addEventListener('change', () => {
            window._spSuggestGroups[parseInt(sel.dataset.idx, 10)].suggestedCategory = sel.value;
        });
    });
```

Replace with:

```javascript
    panel.querySelectorAll('.sp-suggest-category').forEach(inp => {
        inp.addEventListener('input', () => {
            window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedCategory = inp.value;
        });
    });
```

- [ ] **Step 3: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 4: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: allow typing a new category in the AI-suggest review panel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Documentation, rebuild, and manual verification

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section)
- Modify: `PROJECT_STATUS.md` (new "Recent" line)

- [ ] **Step 1: Update CLAUDE.md**

Find the bullet in the Spending Tracking section that mentions the AI-suggest review panel's editable pattern field (added by the prior feature, `docs/superpowers/specs/2026-07-21-spending-rule-editing-and-pattern-quality-design.md`). Append a sentence covering: category fields throughout the Spending page (per-row edit, bulk recategorize, AI-suggest review panel) are now free-text inputs backed by a shared autocomplete datalist of existing categories, so a brand-new category can be typed anywhere rather than only via the Add Rule form; the backend now also rejects a blank category on `PUT /api/v1/spending/{id}`.

- [ ] **Step 2: Update PROJECT_STATUS.md**

Bump "Last updated" to today's actual date (check with `date +%F`) and add a new "Recent" line (next sequential version number after whatever is currently the top entry) summarizing: category entry on the Spending page (row edit, bulk recategorize, AI-suggest panel) is now free text with autocomplete instead of a fixed dropdown, so new categories no longer require going through the separate Add Rule form first; blank categories are now rejected server-side.

- [ ] **Step 3: Verify only docs changed**

Run: `git diff --stat CLAUDE.md PROJECT_STATUS.md`
Expected: both files show changes; `git status --short` shows no other file modified.

- [ ] **Step 4: Commit docs**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document free-text spending categories

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Rebuild and redeploy**

Run:
```bash
docker exec portf_backend_dev kill -HUP 1
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
Expected: both commands complete without error; `docker ps --filter name=portf_web` shows a recent `CreatedAt`.

- [ ] **Step 6: Verify manually**

On the Spending page:
1. In the main transactions table, click into a row's category field, clear it and type a brand-new category name (e.g. "Kids"), press Tab/click away — confirm it saves and persists after reload, and that the browser's autocomplete dropdown showed existing categories while typing.
2. Select several rows, type a new category into the bulk field, click "Set category" — confirm all selected rows update to the new category.
3. Select some uncategorized rows, click "Suggest categories (AI)", then in the review panel type a brand-new category (not one the AI suggested and not one that existed before) into a suggestion's category field, click Apply — confirm the row(s) end up with that new category.
4. In the main table, clear a row's category field entirely and click away — confirm the field reverts to the original category (not saved as blank) and no error is shown.
5. Confirm the category filter dropdown at the top of the page picks up any newly-typed category after the next refresh.
