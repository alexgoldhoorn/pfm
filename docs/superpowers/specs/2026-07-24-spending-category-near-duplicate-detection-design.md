# Spending: near-duplicate category detection, merge, and stricter entry

**Date:** 2026-07-24
**Status:** Approved

## Problem

Spending categories are free strings with exact-match uniqueness only
(`docs/superpowers/specs/2026-07-22-spending-page-tabs-and-category-management-design.md`).
Nothing stops "Subscription" and "Subscriptions" (or any near-duplicate
pair — a typo, a trailing period, a pluralization) from existing side by
side as two separate categories, splitting spend that should be reported
together. A category can be merged into another today (renaming into an
existing name cascades to every transaction/rule using the old name and
upserts the registry — see the design doc above, section F), but:

1. There's no signal that a near-duplicate is about to be created — typing
   "Subscriptions" into the Add Category form, the Add Rule form, the bulk
   recategorize field, or the AI-suggest review panel when "Subscription"
   already exists silently creates a second, separate category.
2. There's no way to discover near-duplicates that already exist in the
   data today, short of scrolling the full category list by eye.

This is scoped to the existing flat category namespace. Hierarchical
categories (`spend > insurance > car`, etc.) are an explicitly separate,
larger piece of work — not addressed here (see Not doing).

## Scope

Three additions, all client-side, no new backend endpoints:

**A) A shared similarity helper** — Levenshtein-distance-based, normalized
0–1 score, comparing trimmed/lowercased strings.

**B) Write-time warnings** at every point a category name can be typed:
if the typed value is a close-but-not-exact match to an existing category,
`confirm()` before proceeding. An exact match is never warned (that's the
existing, intentional merge-by-rename path).

**C) A "Possible duplicates" panel** on the Categories tab: a pairwise scan
of the full existing category list, surfacing pairs that score above
threshold, each with a one-click "Merge into ‹X›" / "Merge into ‹Y›"
action that reuses the existing rename-to-merge endpoint.

**Not doing:** hierarchical/nested categories — a separate, future spec;
this feature works entirely within today's flat namespace and doesn't
preclude a later hierarchy redesign. A new backend endpoint for
similarity/duplicate detection — the full category list is already fetched
client-side once per page load (`window._spendingAllCategories`, populated
by `_refreshSpendingData()`), and the pairwise scan is cheap at realistic
list sizes (dozens of categories, not thousands), so there's no need to
move this server-side. Blocking (rather than warning-and-allowing) category
creation on a near-duplicate — some near-duplicates are legitimately
distinct (e.g. "Car insurance" vs "Home insurance" could score high on a
loose threshold); a hard block would need a bypass anyway, so a `confirm()`
is simpler and matches this codebase's existing pattern for consequential-
but-not-catastrophic actions (see `confirm()` calls already in
`pfm_features.js` for delete actions). A configurable similarity threshold
(UI setting) — one hardcoded constant, tunable in code if it proves wrong
in practice.

## Design

### A) Shared similarity helper

`web_client/js/pfm_features.js`, added near the existing
`_allSpendingCategories` (~line 4968):

```javascript
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

window._categorySimilarity = _categorySimilarity;
window._findSimilarCategories = _findSimilarCategories;
```

`excludeExact` defaults to `true` for the write-time-warning use (B) — an
exact match should never warn. The Possible Duplicates scan (C) doesn't
call `_findSimilarCategories` at all — it works over the already-unique
category list (a `Set`, per `_allSpendingCategories`), so exact matches
can't occur in a pair; it uses the lower-level `_categorySimilarity`
directly against `SP_CATEGORY_SIMILARITY_THRESHOLD` (see Design C). The
`excludeExact` parameter exists purely for B's use, not shared with C.

### B) Write-time warnings

A single shared guard, added alongside the helper:

```javascript
function _warnIfSimilarCategory(candidate, excludeName) {
    const existing = (window._spendingAllCategories || []).filter(c => c !== excludeName);
    const matches = _findSimilarCategories(candidate, existing);
    if (!matches.length) return true;
    return confirm(`"${candidate}" is similar to existing categor${matches.length > 1 ? 'ies' : 'y'} ${matches.map(m => `"${m}"`).join(', ')}. Create it as a new, separate category anyway?`);
}
```

Returns `true` to proceed, `false` to abort — called as an early-return
guard, same shape as the existing `if (!x) return;` guards throughout this
file. Four call sites:

1. **Add category form** (`_wireSpCategoryAddForm`, ~line 5430): after
   `const name = ...trim(); if (!name) return;`, add
   `if (!_warnIfSimilarCategory(name)) return;` before
   `createSpendingCategory(name)`.
2. **Rename-in-place** (`window.editSpendingCategory`'s `finish`, ~line
   5309): after the existing
   `if (!commit || !newName || newName === originalName) { ...; return; }`
   guard, add `if (!_warnIfSimilarCategory(newName, originalName)) { await _refreshSpendingData(); return; }`
   before `renameSpendingCategory(originalName, newName)`. Passing
   `originalName` as `excludeName` means renaming "Subscription" to
   "Subscriptions" (a real near-duplicate collision, not a merge) still
   warns — the field being edited doesn't exclude itself from the check
   just because it's the source of the rename.
3. **Add Rule form** (`_wireSpendingRuleForm`, ~line 5396): after
   `if (!pattern || !category) return;`, add
   `if (!_warnIfSimilarCategory(category)) return;`.
4. **Bulk recategorize field** (`_wireSpBulkActions`'s `recatBtn`
   handler, ~line 5013): after `if (!ids.length || !category) return;`,
   add `if (!_warnIfSimilarCategory(category)) return;`.

**AI-suggest review panel** (`_applySpSuggestions`, ~line 5178) gets one
consolidated check instead of a per-field warning, since Apply can submit
several distinct typed categories at once: before the existing
`if (!accepted.length) { ...; return; }` early-return, compute

```javascript
const flagged = accepted
    .map(g => ({ typed: g.suggestedCategory, matches: _findSimilarCategories(g.suggestedCategory, window._spendingAllCategories || []) }))
    .filter(f => f.matches.length);
if (flagged.length && !confirm(
    `${flagged.length} suggested categor${flagged.length > 1 ? 'ies are' : 'y is'} similar to an existing one:\n` +
    flagged.map(f => `"${f.typed}" ↔ "${f.matches[0]}"`).join('\n') +
    '\n\nApply anyway?'
)) return;
```

placed after the `accepted` list is built and the empty-selection
early-return, before the `status.textContent = 'Applying…'` line.

### C) Possible Duplicates panel

`web_client/index.html`: new card in `#spPaneCategories`, between the
existing "Spending by category" chart card and the "All categories" card
(~line 2733):

```html
<div class="card mb-3" id="spDuplicatesCard" style="display:none;">
    <div class="card-header fw-semibold">Possible duplicate categories</div>
    <div id="spDuplicatesList" class="list-group list-group-flush"></div>
</div>
```

`web_client/js/pfm_features.js`: new `_renderPossibleDuplicates(categories)`,
called from `_refreshSpendingData()` (~line 4614) right after the existing
`_renderCategoriesList(categories);` call:

```javascript
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
window._findDuplicatePairs = _findDuplicatePairs;

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
```

Passing `(pairIndex, keepIdx)` rather than the category name strings
themselves follows the same index-into-stored-array pattern already used
one function up (`editSpendingCategory(${i})`, looked up against
`window._spCategoriesListData`) — avoids embedding arbitrary category text
inside an inline `onclick="..."` JS-string literal, where a name
containing a quote character would otherwise need escaping (a narrower
precedent for that exists elsewhere, e.g. `deleteGoalRow`'s
`.replace(/'/g, "\\'")`, but the index-lookup pattern used immediately
above this insertion point is the more direct match here and needs no
escaping at all).

`renameSpendingCategory(loser, winner)` reusing the existing merge-by-rename
endpoint means no new backend code at all for this feature — B and C are
both pure client-side additions on top of the existing category API.

### Error handling

- B: `confirm()` returning `false` aborts before any API call — no error
  state, matches this file's existing delete-confirmation pattern.
- C: `mergeSpendingCategories` failure surfaces via the existing
  `alert('Error: ' + err.message)` pattern used elsewhere in this file
  (e.g. `editSpendingCategory`'s `finish`).
- Both B and C degrade to "no warning shown" if `window._spendingAllCategories`
  is empty/unset (e.g. very first page load before data arrives) — no crash,
  just no duplicate detection until the list is populated, same as every
  other feature that reads that variable today.

### Testing

- `web_client/js/tests/web_client.test.mjs`: new tests for
  `_levenshteinDistance` (known distance pairs), `_categorySimilarity`
  (identical strings → 1, "Subscription"/"Subscriptions" → above
  threshold, unrelated short strings → below threshold, empty string
  handling), `_findSimilarCategories` (excludes exact match by default,
  includes it when `excludeExact = false`), and `_findDuplicatePairs`
  (returns pairs above threshold, empty list on no matches, doesn't
  duplicate a pair in both orders). These are the four pure functions
  introduced — matches this file's existing precedent of testing pure
  helpers (`dedupSpendingRowsByDescription`, etc.) while leaving DOM
  wiring manually verified.
- Manual verification: type "Subscriptions" into the Add Category form
  when "Subscription" already exists → confirm dialog appears; Cancel
  aborts (category not created); OK creates it. Same check in the Add
  Rule form's category field, the bulk recategorize field, and
  rename-in-place. Select several uncategorized rows with descriptions
  that would get AI-suggested into a near-duplicate of an existing
  category, confirm the consolidated Apply-time dialog lists all of them.
  With two near-duplicate categories present, open the Categories tab and
  confirm the "Possible duplicate categories" card shows the pair with
  both merge directions; click one, confirm the merge dialog, confirm
  afterward that only the winning name remains and its transactions/rules
  count reflects both merged sets. With no near-duplicates present,
  confirm the card is hidden entirely.
