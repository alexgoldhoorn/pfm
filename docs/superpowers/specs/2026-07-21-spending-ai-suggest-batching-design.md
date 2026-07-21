# Spending: batch the AI-suggest bulk action + "select all uncategorized"

**Date:** 2026-07-21
**Status:** Approved

## Problem

The "Suggest categories (AI)" bulk action (shipped in the prior
categorization-follow-up feature) deduplicates the selected uncategorized
rows by description and sends the full unique list to `/suggest-categories`
in one request. On a real account with a large uncategorized backlog —
observed in production: 2,384 uncategorized rows across two accounts,
1,334 unique descriptions — selecting "all" produces a single LLM call
large enough that `portf_web`'s nginx `proxy_read_timeout 200s` fires
before Gemini responds, surfacing as a 502 with no useful error message and
nothing logged on the backend (the request never completed). Even without
the timeout, a review panel listing 1,334 rows to scan before Apply would
not be usable.

Separately, there is no direct way to select "just the uncategorized rows"
in one action — today it requires manually setting the Category filter to
`uncategorized` and then clicking the table's select-all checkbox.

## Scope

Two small, independent frontend-only changes (no backend/schema changes):

1. **Cap the AI-suggest batch.** The existing bulk "Suggest categories
   (AI)" handler dedupes the selection by description (unchanged); it now
   also caps that deduplicated list to the first `SP_AI_SUGGEST_BATCH_SIZE`
   (30) unique descriptions, in current table order, before calling
   `/suggest-categories`. The status message explicitly states how many of
   the total unique descriptions were sent (e.g. "Sent 30 of 187 unique
   descriptions in this selection.") so the cap is never a silent
   surprise.
2. **"Select all uncategorized" button.** A new button, next to the
   Transactions table, that sets the Category filter to `uncategorized`
   (leaving the Account filter untouched, so it respects whichever account
   is currently selected) and checks every now-filtered row.

**Explicitly not doing** (per user decision during brainstorming):
- No automatic batch-chaining. One click of "Suggest categories (AI)"
  processes one batch of up to 30 and stops. The user reviews, clicks
  Apply, then manually clicks "Select all uncategorized" again to pull in
  the next batch — this requires no new state or progress tracking,
  because applying a batch's suggestions removes those rows from the
  `uncategorized` filter, so the next "Select all uncategorized" click
  naturally surfaces a smaller remaining set.
- No pagination or virtualization of the review panel — it's now bounded
  to at most 30 rows per batch by construction, which is already a
  comfortably scannable size.
- No change to the review panel's own UI (editable category dropdown,
  include/exclude checkbox, Apply/Discard) — unchanged from the prior
  feature.
- No change to `/suggest-categories` itself, or to `createSpendingRule`/
  `updateSpendingCategory` — this is purely about what the frontend sends
  and how rows get selected, not the API contract.

## Design

### Batch cap

In `web_client/js/pfm_features.js`, the `suggestBtn` click handler
(`_wireSpBulkActions()`) currently does:

```javascript
const groups = dedupSpendingRowsByDescription(selectedRows);
const { suggestions } = await window.apiClient.suggestSpendingCategories(
    groups.map(g => ({ ... }))
);
```

Add a module-level constant `const SP_AI_SUGGEST_BATCH_SIZE = 30;` next to
the function, and change to:

```javascript
const allGroups = dedupSpendingRowsByDescription(selectedRows);
const groups = allGroups.slice(0, SP_AI_SUGGEST_BATCH_SIZE);
```

then use `allGroups.length` in the status message once results come back:

```javascript
if (status) {
    status.textContent = allGroups.length > groups.length
        ? `Sent ${groups.length} of ${allGroups.length} unique descriptions in this selection. Apply this batch, then use "Select all uncategorized" again to continue with the rest.`
        : `${window._spSuggestGroups.length} suggestion(s) ready for review below.`;
}
```

`selectedRows` itself is unaffected (still every uncategorized row in the
current selection) — only the *deduplicated, unique-description* list sent
to the LLM is capped. Applying a batch's suggestions still updates every
row in the *original* selection matching an accepted description, exactly
as before (a batch's `g.ids` array is unaffected by the cap — it holds
every row id sharing that description across the whole selection, not
just the first `SP_AI_SUGGEST_BATCH_SIZE`).

### "Select all uncategorized" button

`web_client/index.html`: add a button in the "Transactions" card header
(next to the existing "Transactions" label), e.g. `#spSelectAllUncategorized`.

`web_client/js/pfm_features.js`, `loadSpendingPage()`: wire it —

```javascript
const selAllUncatBtn = document.getElementById('spSelectAllUncategorized');
if (selAllUncatBtn && !selAllUncatBtn.dataset.wired) {
    selAllUncatBtn.dataset.wired = '1';
    selAllUncatBtn.addEventListener('click', () => {
        const catFilter = document.getElementById('spCategoryFilter');
        if (catFilter) catFilter.value = 'uncategorized';
        _renderSpendingTable();
        document.querySelectorAll('#spTxBody .sp-row-check').forEach(cb => { cb.checked = true; });
        _updateSpBulkBar();
    });
}
```

`_renderSpendingTable()` already reads `#spCategoryFilter`'s current value
to filter rows (`filterSpendingRows(rows, { category: ... })`) and already
re-wires fresh checkboxes on every render (`_wireSpBulkActions()` is called
at the end of every `_renderSpendingTable()` call) — so setting the filter
value programmatically and re-rendering is exactly equivalent to a user
picking "uncategorized" from the dropdown by hand, just combined with the
select-all step into one click. If there happen to be zero uncategorized
rows, the click is a harmless no-op (empty selection).

### Error handling

- If the selection (after filtering to `uncategorized` and deduping)
  is empty, the existing "No uncategorized rows selected." message is
  unchanged.
- If `allGroups.length <= SP_AI_SUGGEST_BATCH_SIZE`, the status message is
  unchanged from today (no mention of a cap, since there wasn't one).
- The LLM-call-failure path (`catch` block) is unchanged.

### Testing

No new pure-function logic is introduced (the cap is a `.slice()` on an
existing array, the button handler is DOM wiring only) — no new automated
tests, consistent with this codebase's existing precedent that DOM
click-handler wiring isn't unit-tested (only pure functions extracted to
module scope get `web_client/js/tests/` coverage, and this change adds
none). Verify manually: with a selection whose unique-description count
exceeds 30, confirm the status message states the "X of Y" cap and that
applying still updates every row sharing an accepted description (not
just the first occurrence).
