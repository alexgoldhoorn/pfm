# Spending: free-text category entry everywhere + blank-category rejection

**Date:** 2026-07-22
**Status:** Approved

## Problem

Three places on the Spending page let a user set a transaction's category,
and all three are `<select>` elements populated only from categories that
already exist somewhere in the data (plus, for the AI-suggest panel, the
category the LLM proposed):

1. The per-transaction category cell in the main transactions table.
2. The bulk-recategorize `<select id="spBulkCategorySelect">`.
3. The category `<select class="sp-suggest-category">` in the "Review AI
   suggestions" panel (`_renderSpSuggestReviewPanel`).

There is no way to type a brand-new category name at the point of review —
a user who wants to accept an AI suggestion under a category that doesn't
exist yet has no way to do so in that panel (same limitation on the other
two selects). A new category can currently only be introduced via the
separate "Add Rule" form's free-text Category field, which is a different
part of the page and requires creating a pattern/rule at the same time.

Separately, `PUT /api/v1/spending/{spending_id}` (`update_spending_category`)
accepts any string, including empty, for `body.category` — nothing stops a
blank category from being written today, whether from a bug or a UI slip.

## Scope

Two changes:

**A) Free-text category entry in all three locations**, via a shared
`<datalist>` — the browser still suggests existing categories as you type,
but any typed value is accepted, matching the free-text pattern field
already added to the AI-suggest panel in the prior feature
(`docs/superpowers/specs/2026-07-21-spending-rule-editing-and-pattern-quality-design.md`).

**B) Server-side rejection of a blank category** on
`PUT /api/v1/spending/{spending_id}`, mirroring the validation
`PUT /api/v1/spending/rules/{id}` already has for `pattern`/`category`. This
single endpoint backs all three UI entry points (direct row edit, the bulk
loop, and AI-suggest Apply), so one backend change covers all of them.

**Not doing:** a dedicated category management page (add/rename/delete
categories as first-class entities, independent of rules/transactions) —
confirmed out of scope; this is about being able to type a new category at
the point of use, not about managing the category set as its own concept.
No change to `createSpendingRule`'s existing blank-category validation
(`POST /api/v1/spending/rules` already rejects it, per the prior feature).

## Design

### A) Shared datalist + free-text inputs

`web_client/index.html`: add `<datalist id="spCategoryList"></datalist>`
once, anywhere on the Spending page markup (a datalist renders nothing
itself).

`web_client/js/pfm_features.js`:

- New shared helper, replacing the three separate inline
  `[...new Set([...])]` category-list constructions (at the category
  filter population, the per-row table's `renderRows`, and
  `_renderSpSuggestReviewPanel`):

  ```javascript
  function _allSpendingCategories(rows, extra) {
      return [...new Set(['uncategorized', 'Transfer',
          ...rows.map(r => r.category), ...(extra || [])])].sort();
  }
  ```

- New `_populateSpCategoryDatalist(categories)`: sets `#spCategoryList`'s
  `innerHTML` to `categories.map(c => `<option value="${esc(c)}">`).join('')`.
  Called once per render pass, everywhere `_allSpendingCategories` is
  called (the per-row table render, and `_renderSpSuggestReviewPanel` —
  the latter passes `groups.map(g => g.suggestedCategory)` as `extra`, so
  an AI-suggested new category still appears as a suggestion).

- Per-row category cell: replace

  ```javascript
  <select class="form-select form-select-sm d-inline-block" style="width:auto;" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
      ${categories.map(c => `<option value="${esc(c)}" ${c === r.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
  </select>
  ```

  with

  ```javascript
  <input type="text" list="spCategoryList" class="form-control form-control-sm d-inline-block" style="width:auto;" value="${esc(r.category)}" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
  ```

  `window.updateSpendingRowCategory` gains a guard: trim the incoming
  value; if empty or unchanged from the row's current category, return
  without calling the API (today's `<select>` can never submit blank or a
  no-op change, so this guard is new behavior, not a regression fix).

- Bulk-recategorize field (`web_client/index.html`,
  `<select class="form-select form-select-sm w-auto" id="spBulkCategorySelect"></select>`):
  change to
  `<input type="text" list="spCategoryList" class="form-control form-control-sm w-auto" id="spBulkCategorySelect" placeholder="Category">`.
  Remove `_populateSpBulkCategorySelect` (no longer needed — the datalist
  is shared and already populated) and its call site in the table's
  `renderRows`. The existing click handler's
  `if (!ids.length || !category) return;` gets `category` read via
  `.value.trim()` instead of `.value`.

- AI-suggest panel category field: replace

  ```javascript
  <select class="form-select form-select-sm w-auto sp-suggest-category" data-idx="${i}">
      ${categories.map(c => `<option value="${esc(c)}" ${c === g.suggestedCategory ? 'selected' : ''}>${esc(c)}</option>`).join('')}
  </select>
  ```

  with

  ```javascript
  <input type="text" list="spCategoryList" class="form-control form-control-sm w-auto sp-suggest-category" data-idx="${i}" value="${esc(g.suggestedCategory)}">
  ```

  and change its listener from `change` to `input` (mirrors the pattern
  field's own listener added in the prior feature — live-updates
  `window._spSuggestGroups[idx].suggestedCategory` as the user types, not
  just on blur/selection):

  ```javascript
  panel.querySelectorAll('.sp-suggest-category').forEach(inp => {
      inp.addEventListener('input', () => {
          window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedCategory = inp.value;
      });
  });
  ```

### B) Server-side blank-category rejection

`portf_server/routers/spending.py`, `update_spending_category`: after the
existing 404 check, trim `body.category` and raise
`HTTPException(status_code=400, detail="Category cannot be empty")` if
empty, before building `update_kwargs`. Same shape as the validation
`update_rule` already has. Use the trimmed value (not the raw
`body.category`) in `update_kwargs["category"]` and the response, so
leading/trailing whitespace typed into a free-text field doesn't get
persisted.

### Error handling

- A: blank/unchanged value in the per-row input → client-side guard, no
  API call (see above). Blank value in the bulk field → existing
  `if (!ids.length || !category) return;` guard (now trimmed) prevents the
  loop from starting. Blank value in the AI-suggest panel's category field
  at Apply time → not specially guarded client-side; falls into the
  existing per-row try/catch in `_applySpSuggestions`, which now receives
  a 400 from B and counts it as `failed`, surfaced via the existing
  "Applied to X row(s), Y failed" status message — consistent with how
  other per-row failures in that loop are already reported, no new UI
  needed.
- B: 400 with `{"detail": "Category cannot be empty"}`, matching the
  existing validation error shape used by `update_rule`.

### Testing

- Backend: `tests/unit/test_spending_api.py` — new test
  `test_update_category_blank_rejected` asserting 400 on
  `PUT /api/v1/spending/{id}` with `{"category": "   "}`; extend or add
  near the existing category-update test(s) for this endpoint.
- Frontend: no new pure functions requiring `web_client/js/tests/`
  coverage — `_allSpendingCategories` is a small pure function and could
  reasonably get a unit test (existing precedent: pure helpers in this
  file are tested where they exist); the three DOM-wiring changes follow
  the established no-test precedent for this router
  (`editSpendingRule`, the pattern-field wiring, etc. have none). Verify
  manually: type a brand-new category into the per-row field, the bulk
  field, and the AI-suggest panel and confirm each persists as typed;
  confirm the datalist offers existing categories as suggestions while
  typing; confirm clearing a per-row category and blurring leaves it
  unchanged; confirm a blank category typed into the AI-suggest panel
  shows up in the "failed" count after Apply.
