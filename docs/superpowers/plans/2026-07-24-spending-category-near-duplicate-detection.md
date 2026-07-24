# Spending Category Near-Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warn when a typed category name is a near-duplicate of an existing one (e.g. "Subscription" vs "Subscriptions"), and surface a "Possible duplicates" panel on the Categories tab with one-click merge.

**Architecture:** Four pure-JS additions to `web_client/js/pfm_features.js`, all client-side, reusing the existing `renameSpendingCategory` merge-by-rename endpoint (no backend changes, no new dependency). A shared Levenshtein-based similarity primitive backs both a write-time `confirm()` guard (wired into 5 existing category-entry points) and a pairwise duplicate scan rendered as a new card on the Categories tab.

**Tech Stack:** Vanilla JS (no framework), Bootstrap 5 (existing `card`/`list-group` classes), Node's built-in `node --test` + `vm` module for the existing test harness.

## Global Constraints

- No new npm/JS dependency — the Levenshtein distance function is hand-written (spec: "Not doing" — configurable/library approach rejected).
- No new backend endpoint or schema change — everything reads `window._spendingAllCategories` (already fetched by `_refreshSpendingData()`) and writes via the existing `apiClient.createSpendingCategory` / `renameSpendingCategory`.
- Similarity threshold is the single constant `SP_CATEGORY_SIMILARITY_THRESHOLD = 0.75` (spec Design A) — not user-configurable.
- An **exact** match (case/whitespace-insensitive) is never warned — that's the existing, intentional merge-by-rename path.
- Tests run via `node --test web_client/js/tests/` from the repo root. The harness loads `pfm_core.js`, `pfm_pages.js`, `pfm_analytics.js`, `pfm_features.js` concatenated into one `vm` sandbox where `sandbox.window === sandbox` — so a top-level function `foo` is reachable in tests as both `ctx.foo` and `ctx.window.foo`.
- Design reference: `docs/superpowers/specs/2026-07-24-spending-category-near-duplicate-detection-design.md`.

---

### Task 1: Similarity primitives (`_levenshteinDistance`, `_categorySimilarity`, `_findSimilarCategories`, `_findDuplicatePairs`)

**Files:**
- Modify: `web_client/js/pfm_features.js:4968` (insert new functions between the existing `_allSpendingCategories` and `_populateSpCategoryDatalist`)
- Test: `web_client/js/tests/web_client.test.mjs` (append new tests at end of file, after line 642)

**Interfaces:**
- Produces: `_levenshteinDistance(a: string, b: string) -> number`; `_categorySimilarity(a: string, b: string) -> number` (0–1, 1 = identical after trim/lowercase); `SP_CATEGORY_SIMILARITY_THRESHOLD = 0.75`; `_findSimilarCategories(candidate: string, existing: string[], excludeExact = true) -> string[]`; `_findDuplicatePairs(categories: string[]) -> [string, string][]`. All four assigned onto `window` at definition (`window._categorySimilarity = _categorySimilarity;` etc., matching this file's existing export style — see `window.dedupSpendingRowsByDescription = dedupSpendingRowsByDescription;`).
- Consumes: nothing new — pure functions over their own arguments.

- [ ] **Step 1: Write the failing tests**

Append to `web_client/js/tests/web_client.test.mjs`:

```javascript
test('_categorySimilarity: identical strings (case/whitespace-insensitive) score 1', () => {
    const ctx = loadAppIntoContext();
    assert.equal(ctx._categorySimilarity('Groceries', 'Groceries'), 1);
    assert.equal(ctx._categorySimilarity('Groceries', '  groceries  '), 1);
});

test('_categorySimilarity: near-duplicate pair scores above threshold, distinct pair below', () => {
    const ctx = loadAppIntoContext();
    assert.ok(ctx._categorySimilarity('Subscription', 'Subscriptions') >= ctx.SP_CATEGORY_SIMILARITY_THRESHOLD);
    assert.ok(ctx._categorySimilarity('Groceries', 'Insurance') < ctx.SP_CATEGORY_SIMILARITY_THRESHOLD);
});

test('_categorySimilarity: empty string never scores 1 against a non-empty string', () => {
    const ctx = loadAppIntoContext();
    assert.equal(ctx._categorySimilarity('', 'Groceries'), 0);
    assert.equal(ctx._categorySimilarity('Groceries', ''), 0);
});

test('_findSimilarCategories: excludes an exact match by default, includes it when excludeExact is false', () => {
    const ctx = loadAppIntoContext();
    const existing = ['Subscription', 'Groceries', 'Insurance'];
    assert.deepEqual([...ctx._findSimilarCategories('Subscription', existing)], []);
    assert.deepEqual([...ctx._findSimilarCategories('Subscriptions', existing)], ['Subscription']);
    assert.deepEqual([...ctx._findSimilarCategories('Subscription', existing, false)], ['Subscription']);
});

test('_findSimilarCategories: no matches when nothing is close', () => {
    const ctx = loadAppIntoContext();
    assert.deepEqual([...ctx._findSimilarCategories('Vacation', ['Groceries', 'Insurance'])], []);
});

test('_findDuplicatePairs: returns pairs scoring above threshold, each pair once', () => {
    const ctx = loadAppIntoContext();
    const pairs = ctx._findDuplicatePairs(['Subscription', 'Subscriptions', 'Groceries']);
    assert.equal(pairs.length, 1);
    assert.deepEqual([...pairs[0]], ['Subscription', 'Subscriptions']);
});

test('_findDuplicatePairs: empty list when no category is close to another', () => {
    const ctx = loadAppIntoContext();
    assert.deepEqual([...ctx._findDuplicatePairs(['Groceries', 'Insurance', 'Vacation'])], []);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: FAIL — `TypeError: ctx._categorySimilarity is not a function` (and similarly for the other three new tests).

- [ ] **Step 3: Implement the four functions**

In `web_client/js/pfm_features.js`, locate the existing block:

```javascript
function _allSpendingCategories(extra) {
    return [...new Set(['uncategorized', 'Transfer',
        ...(window._spendingAllCategories || []), ...(extra || [])])].sort();
}

function _populateSpCategoryDatalist(categories) {
```

Insert the new block between the two functions, so it reads:

```javascript
function _allSpendingCategories(extra) {
    return [...new Set(['uncategorized', 'Transfer',
        ...(window._spendingAllCategories || []), ...(extra || [])])].sort();
}

function _levenshteinDistance(a, b) {
    const m = a.length, n = b.length;
    const dp = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
    for (let j = 0; j <= n; j++) dp[0][j] = j;
    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            dp[i][j] = a[i - 1] === b[j - 1]
                ? dp[i - 1][j - 1]
                : 1 + Math.min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]);
        }
    }
    return dp[m][n];
}

function _categorySimilarity(a, b) {
    const la = a.trim().toLowerCase(), lb = b.trim().toLowerCase();
    if (la === lb) return 1;
    if (!la.length || !lb.length) return 0;
    return 1 - _levenshteinDistance(la, lb) / Math.max(la.length, lb.length);
}

const SP_CATEGORY_SIMILARITY_THRESHOLD = 0.75;

function _findSimilarCategories(candidate, existing, excludeExact = true) {
    return existing.filter(c => {
        const score = _categorySimilarity(candidate, c);
        return excludeExact ? (score >= SP_CATEGORY_SIMILARITY_THRESHOLD && score < 1) : score >= SP_CATEGORY_SIMILARITY_THRESHOLD;
    });
}

function _findDuplicatePairs(categories) {
    const pairs = [];
    for (let i = 0; i < categories.length; i++) {
        for (let j = i + 1; j < categories.length; j++) {
            const score = _categorySimilarity(categories[i], categories[j]);
            if (score >= SP_CATEGORY_SIMILARITY_THRESHOLD) pairs.push([categories[i], categories[j]]);
        }
    }
    return pairs;
}

window._categorySimilarity = _categorySimilarity;
window._findSimilarCategories = _findSimilarCategories;
window._findDuplicatePairs = _findDuplicatePairs;
window.SP_CATEGORY_SIMILARITY_THRESHOLD = SP_CATEGORY_SIMILARITY_THRESHOLD;

function _populateSpCategoryDatalist(categories) {
```

(`window.SP_CATEGORY_SIMILARITY_THRESHOLD` is exported so Step 1's test can reference the real constant instead of hardcoding `0.75` a second time.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: PASS — all 7 new tests plus every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: add category-name similarity primitives"
```

---

### Task 2: Write-time warning guard + 4 simple call sites

**Files:**
- Modify: `web_client/js/pfm_features.js` (add `_warnIfSimilarCategory`; wire into the bulk recategorize handler, the Add Rule form, the Add Category form, and rename-in-place)

**Interfaces:**
- Consumes: `_findSimilarCategories` (Task 1), `window._spendingAllCategories` (existing global, populated by `_refreshSpendingData()`).
- Produces: `_warnIfSimilarCategory(candidate: string, excludeName?: string) -> boolean` (`true` = proceed, `false` = abort), exported as `window._warnIfSimilarCategory` for manual/console verification.

- [ ] **Step 1: Add the guard function**

In `web_client/js/pfm_features.js`, immediately after the `window.SP_CATEGORY_SIMILARITY_THRESHOLD = SP_CATEGORY_SIMILARITY_THRESHOLD;` line added in Task 1, insert:

```javascript
function _warnIfSimilarCategory(candidate, excludeName) {
    const existing = (window._spendingAllCategories || []).filter(c => c !== excludeName);
    const matches = _findSimilarCategories(candidate, existing);
    if (!matches.length) return true;
    return confirm(`"${candidate}" is similar to existing categor${matches.length > 1 ? 'ies' : 'y'} ${matches.map(m => `"${m}"`).join(', ')}. Create it as a new, separate category anyway?`);
}
window._warnIfSimilarCategory = _warnIfSimilarCategory;
```

- [ ] **Step 2: Wire the bulk recategorize field**

Locate in `_wireSpBulkActions()`:

```javascript
        recatBtn.addEventListener('click', async () => {
            const ids = _selectedSpendingIds();
            const category = document.getElementById('spBulkCategorySelect')?.value.trim();
            if (!ids.length || !category) return;
            recatBtn.disabled = true;
```

Replace with:

```javascript
        recatBtn.addEventListener('click', async () => {
            const ids = _selectedSpendingIds();
            const category = document.getElementById('spBulkCategorySelect')?.value.trim();
            if (!ids.length || !category) return;
            if (!_warnIfSimilarCategory(category)) return;
            recatBtn.disabled = true;
```

- [ ] **Step 3: Wire the Add Rule form**

Locate in `_wireSpendingRuleForm()`:

```javascript
            const pattern = document.getElementById('spRulePattern').value.trim();
            const category = document.getElementById('spRuleCategory').value.trim();
            if (!pattern || !category) return;
            const status = document.getElementById('spRuleStatus');
```

Replace with:

```javascript
            const pattern = document.getElementById('spRulePattern').value.trim();
            const category = document.getElementById('spRuleCategory').value.trim();
            if (!pattern || !category) return;
            if (!_warnIfSimilarCategory(category)) return;
            const status = document.getElementById('spRuleStatus');
```

- [ ] **Step 4: Wire the Add Category form**

Locate in `_wireSpCategoryAddForm()`:

```javascript
            const name = document.getElementById('spCategoryNameInput').value.trim();
            if (!name) return;
            const status = document.getElementById('spCategoryAddStatus');
```

Replace with:

```javascript
            const name = document.getElementById('spCategoryNameInput').value.trim();
            if (!name) return;
            if (!_warnIfSimilarCategory(name)) return;
            const status = document.getElementById('spCategoryAddStatus');
```

- [ ] **Step 5: Wire rename-in-place**

Locate in `window.editSpendingCategory`'s `finish`:

```javascript
    const finish = async (commit) => {
        if (done) return;
        done = true;
        const newName = input.value.trim();
        if (!commit || !newName || newName === originalName) {
            await _refreshSpendingData();
            return;
        }
        try {
            await window.apiClient.renameSpendingCategory(originalName, newName);
```

Replace with:

```javascript
    const finish = async (commit) => {
        if (done) return;
        done = true;
        const newName = input.value.trim();
        if (!commit || !newName || newName === originalName) {
            await _refreshSpendingData();
            return;
        }
        if (!_warnIfSimilarCategory(newName, originalName)) {
            await _refreshSpendingData();
            return;
        }
        try {
            await window.apiClient.renameSpendingCategory(originalName, newName);
```

- [ ] **Step 6: Run the full test suite to confirm no regression**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: PASS — the load-smoke test (`"split loads in one scope and defines functions from every file"`) confirms the file still parses and loads cleanly; no new automated tests are added in this task (DOM wiring follows this file's existing no-automated-test precedent — verified manually in Step 7).

- [ ] **Step 7: Manual verification**

Start the app locally (however this project normally runs the web client + API — check `Makefile`/`README.md` if unfamiliar) with at least one existing spending category, e.g. "Subscription". Then:
- Categories tab → Add Category → type "Subscriptions" → submit. Expect a confirm dialog naming "Subscription". Cancel it → confirm no new category was created (list unchanged after refresh). Repeat and click OK → confirm "Subscriptions" now appears in the category list.
- Categories tab → click the pencil next to an existing category → rename it to something similar to a *different* existing category → confirm the warning appears; Escape/blur-cancel leaves the name unchanged.
- Rules tab → Add Rule form → type a category similar to an existing one → confirm the warning appears before the rule is created.
- Transactions tab → select rows → bulk "Set category" → type a category similar to an existing one → confirm the warning appears before the bulk update runs.
- Type an **exact** existing category name in each of the four places above → confirm no warning appears (exact match is the merge path, not warned).

- [ ] **Step 8: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: warn on near-duplicate category names at entry points"
```

---

### Task 3: AI-suggest panel consolidated warning

**Files:**
- Modify: `web_client/js/pfm_features.js` (`_applySpSuggestions`)

**Interfaces:**
- Consumes: `_findSimilarCategories` (Task 1), `window._spendingAllCategories`.
- Produces: no new exported function — inline logic within the existing `_applySpSuggestions`.

- [ ] **Step 1: Add the consolidated check**

Locate in `_applySpSuggestions()`:

```javascript
    const status = document.getElementById('spBulkStatus');
    if (!accepted.length) {
        window._spSuggestGroups = [];
        panel.style.display = 'none'; panel.innerHTML = '';
        return;
    }
    if (status) { status.className = 'small text-muted px-3 pt-2'; status.textContent = 'Applying…'; }
```

Replace with:

```javascript
    const status = document.getElementById('spBulkStatus');
    if (!accepted.length) {
        window._spSuggestGroups = [];
        panel.style.display = 'none'; panel.innerHTML = '';
        return;
    }
    const flagged = accepted
        .map(g => ({ typed: g.suggestedCategory, matches: _findSimilarCategories(g.suggestedCategory, window._spendingAllCategories || []) }))
        .filter(f => f.matches.length);
    if (flagged.length && !confirm(
        `${flagged.length} suggested categor${flagged.length > 1 ? 'ies are' : 'y is'} similar to an existing one:\n` +
        flagged.map(f => `"${f.typed}" ↔ "${f.matches[0]}"`).join('\n') +
        '\n\nApply anyway?'
    )) return;
    if (status) { status.className = 'small text-muted px-3 pt-2'; status.textContent = 'Applying…'; }
```

- [ ] **Step 2: Run the full test suite to confirm no regression**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: PASS — same load-smoke rationale as Task 2 Step 6; `_applySpSuggestions` has no existing automated tests to update (not a pure function — DOM + `apiClient` calls).

- [ ] **Step 3: Manual verification**

With an existing category "Subscription" and at least one uncategorized transaction whose description would plausibly get AI-suggested as "Subscriptions" (or manually edit a suggestion's category field in the review panel to "Subscriptions" before clicking Apply): select the row(s), click "Suggest categories (AI)", edit the suggested category in the review panel to a near-duplicate of an existing category, click Apply. Expect a single confirm dialog listing the flagged suggestion(s). Cancel → confirm nothing was applied (status/panel unchanged, no API calls). Redo and click OK → confirm the category is applied as typed.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: warn on near-duplicate categories in AI-suggest apply"
```

---

### Task 4: Possible Duplicates panel

**Files:**
- Modify: `web_client/index.html:2733-2734` (new card in `#spPaneCategories`)
- Modify: `web_client/js/pfm_features.js` (`_renderPossibleDuplicates`, `window.mergeSpendingCategories`; wire into `_refreshSpendingData`)
- Test: `web_client/js/tests/web_client.test.mjs` (no new pure function beyond `_findDuplicatePairs`, already tested in Task 1 — this task is DOM wiring, verified manually per Step 4)

**Interfaces:**
- Consumes: `_findDuplicatePairs` (Task 1), `apiClient.renameSpendingCategory` (existing).
- Produces: `window.mergeSpendingCategories(pairIndex: number, keepIdx: 0|1) -> Promise<void>`, `window._spDuplicatePairs` (the last-rendered pairs array, read by `mergeSpendingCategories`).

- [ ] **Step 1: Add the new card to the Categories tab**

In `web_client/index.html`, locate:

```html
                                </div>
                            </div>
                        </div>
                        <div class="tab-pane fade" id="spPaneCategories">
                            <div class="card mb-3">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span>Spending by category</span>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-sm btn-outline-secondary" id="spCategoryChartTypeToggle">Pie chart</button>
                                        <button class="btn btn-sm btn-outline-secondary" id="spCategoryChartShowAll">Show all</button>
                                    </div>
                                </div>
                                <div class="card-body">
                                    <div style="position: relative; height: 320px;">
                                        <canvas id="spCategoryChartCanvas"></canvas>
                                    </div>
                                </div>
                            </div>
                            <div class="card">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span>All categories</span>
```

Replace the line `                            </div>\n                            <div class="card">` (the closing `</div>` of the chart card, immediately followed by the opening of the "All categories" card) with:

```html
                            </div>
                            <div class="card mb-3" id="spDuplicatesCard" style="display:none;">
                                <div class="card-header fw-semibold">Possible duplicate categories</div>
                                <div id="spDuplicatesList" class="list-group list-group-flush"></div>
                            </div>
                            <div class="card">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span>All categories</span>
```

(i.e. the new card is inserted between the two existing cards; nothing else in this region changes.)

- [ ] **Step 2: Add the render + merge functions**

In `web_client/js/pfm_features.js`, locate the end of `_renderCategoriesList` (immediately before `function _wireCategoriesSortToggle()`):

```javascript
        <div class="list-group-item d-flex align-items-center justify-content-between">
            <span id="spCategoryNameCell${i}" data-value="${escapeForAttr(cat)}">${esc(cat)}</span>
            <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingCategory(${i})" title="Edit"><i class="bi bi-pencil"></i></button>
        </div>`).join('') : '<div class="list-group-item text-center text-muted py-2">No categories yet.</div>';
}

function _wireCategoriesSortToggle() {
```

Insert new functions between them:

```javascript
        <div class="list-group-item d-flex align-items-center justify-content-between">
            <span id="spCategoryNameCell${i}" data-value="${escapeForAttr(cat)}">${esc(cat)}</span>
            <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingCategory(${i})" title="Edit"><i class="bi bi-pencil"></i></button>
        </div>`).join('') : '<div class="list-group-item text-center text-muted py-2">No categories yet.</div>';
}

function _renderPossibleDuplicates(categories) {
    const card = document.getElementById('spDuplicatesCard');
    const list = document.getElementById('spDuplicatesList');
    if (!card || !list) return;
    const pairs = _findDuplicatePairs(categories);
    window._spDuplicatePairs = pairs;
    card.style.display = pairs.length ? '' : 'none';
    list.innerHTML = pairs.map(([a, b], i) => `
        <div class="list-group-item d-flex align-items-center justify-content-between">
            <span class="small">"${esc(a)}" &harr; "${esc(b)}"</span>
            <div class="d-flex gap-2">
                <button class="btn btn-sm btn-outline-secondary" onclick="window.mergeSpendingCategories(${i}, 0)">Merge into "${esc(a)}"</button>
                <button class="btn btn-sm btn-outline-secondary" onclick="window.mergeSpendingCategories(${i}, 1)">Merge into "${esc(b)}"</button>
            </div>
        </div>`).join('');
}

window.mergeSpendingCategories = async function (pairIndex, keepIdx) {
    const pair = (window._spDuplicatePairs || [])[pairIndex];
    if (!pair) return;
    const winner = pair[keepIdx];
    const loser = pair[1 - keepIdx];
    if (!confirm(`Merge "${loser}" into "${winner}"? This moves every transaction and rule using "${loser}" to "${winner}".`)) return;
    try {
        await window.apiClient.renameSpendingCategory(loser, winner);
    } catch (err) {
        alert('Error: ' + err.message);
    }
    await _refreshSpendingData();
};

function _wireCategoriesSortToggle() {
```

- [ ] **Step 3: Wire the render into `_refreshSpendingData`**

Locate in `_refreshSpendingData()`:

```javascript
        _renderSpendingRules(rules);
        _renderCategoriesList(categories);
        await _fetchAndRenderSpendingTable();
```

Replace with:

```javascript
        _renderSpendingRules(rules);
        _renderCategoriesList(categories);
        _renderPossibleDuplicates(categories);
        await _fetchAndRenderSpendingTable();
```

- [ ] **Step 4: Run the full test suite, then manually verify**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: PASS — load-smoke test confirms `pfm_features.js` still parses; `_findDuplicatePairs` itself is already covered by Task 1's tests.

Manual verification: with two near-duplicate categories present (e.g. "Subscription" and "Subscriptions", created via Task 2's Add Category flow — confirm the warning and click OK anyway to get both to exist), open the Categories tab. Confirm the "Possible duplicate categories" card is visible and shows the pair with two buttons, "Merge into ..." each direction. Click one; confirm the merge confirmation dialog; click OK; confirm afterward that only the winning name remains in the "All categories" list, the duplicates card is now hidden (assuming no other near-duplicates remain), and any transactions/rules that used the losing name now show the winning name. With zero near-duplicate categories present, confirm the card is not shown at all (not even an empty card).

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: add Possible Duplicates panel with one-click category merge"
```
