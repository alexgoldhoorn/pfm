# Spending: rescan categories + AI suggestions on already-imported rows

**Date:** 2026-07-21
**Status:** Approved

## Problem

Spending Tracking's rule-based and AI-assisted categorization only ever run
at import time: `_apply_rules()` runs once per row during `POST
/api/v1/spending/upload`'s preview, and `POST /api/v1/spending/suggest-categories`
is only wired into the import-preview modal. Once a row is saved as
`uncategorized`, nothing re-categorizes it later — adding a new rule after
the fact, or wanting an AI suggestion for rows already sitting in the
database, requires deleting and re-importing the statement. This is the
only Spending workflow without a "catch up existing data" path; transfer
matching already has one (`POST /api/v1/spending/rescan-transfers`).

## Scope

Two independent, composable additions to the existing Spending page — no
new pages, no schema changes:

1. **Rescan categories** — a manual button that re-applies the *current*
   set of `spending_rules` against every `spending_transactions` row still
   at `category = 'uncategorized'`, across all accounts. Mirrors
   `rescan-transfers` exactly: on-demand, not automatic on rule creation.
   Only touches `uncategorized` rows — never overwrites a category that
   was already set (by a prior rule match, AI suggestion, or manual edit).
2. **AI suggestions for already-saved rows, with review before applying** —
   extends the existing bulk-select mechanism on the Spending transaction
   table with a "Suggest categories (AI)" action that calls the existing
   `/suggest-categories` endpoint against the selected uncategorized rows
   (deduplicated by description), shows the results in a review panel
   (editable per-suggestion, with include/exclude checkboxes), and only
   writes anything to the database when the user clicks "Apply". Accepted
   suggestions also create a new `spending_rules` row each, identical to
   the import flow's existing "accepting a suggestion creates a rule"
   behavior — so Rescan categories (or the next import) picks up the same
   merchant automatically without a further AI call.

**Not doing**: automatic re-categorization triggered by rule creation
(explicitly rejected — manual button, per user decision during
brainstorming). Re-evaluating already-categorized rows against newer rules
(explicitly rejected — only `uncategorized` rows are ever touched, to
avoid silently overwriting a category the user set on purpose). A
dedicated review *page* — the review step is a lightweight panel/modal,
not a new nav destination. Deduplicating `spending_rules` on identical
`pattern` — out of scope; the existing import-flow "accept creates a rule"
path already has this same characteristic and isn't being fixed here.

## Design

### Backend: `POST /api/v1/spending/rescan-categories`

New endpoint in `portf_server/routers/spending.py`, placed next to
`rescan-transfers`:

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

Reuses `_apply_rules()` (already defined, first-match-wins substring
match) and `db.list_spending_transactions(category=...)` (existing filter
param) and `db.update_spending_transaction()` (existing, used by `PUT
/api/v1/spending/{id}`) — no new database methods needed.

### Backend: no changes needed for AI suggestions on saved rows

`POST /api/v1/spending/suggest-categories` already accepts
`SuggestCategoriesRequest.rows: List[PreviewSpendingRow]`, and
`PreviewSpendingRow` only requires `date`, `description`, `amount`
(`currency`, `category`, `is_duplicate`, `balance` all have defaults) — a
saved `spending_transactions` row already carries every required field, so
the frontend can call this endpoint directly with saved-row data cast into
that shape. The endpoint's internal logic only reads `.description` off
each row, so no backend change is required at all for this half of the
feature.

### Frontend: Rescan categories button

`web_client/index.html`: add a "Rescan categories" button next to the
existing "Rescan transfers" button (`#spRescanTransfers`) on the Spending
page — same row, same styling (`btn-outline-secondary btn-sm` or matching
whatever the existing button uses).

`web_client/js/pfm_features.js`, `loadSpendingPage()`: wire the new button
following the exact same pattern as the existing `rescanBtn` handler
(disable while running, call the endpoint, refresh data via
`_refreshSpendingData()`, show a status message using the count returned).

### Frontend: AI suggestions on selected saved rows

`web_client/index.html`: add a "Suggest categories (AI)" button to the
bulk action bar (`#spBulkBar`, alongside the existing bulk recategorize/
delete controls) and a small review panel container (hidden until
populated) below the bulk bar — e.g. `#spSuggestReviewPanel`.

`web_client/js/pfm_features.js`:

- New `_wireSpBulkSuggestAction()` (called from `_wireSpBulkActions()`,
  same place the recategorize/delete bulk handlers are wired): on click,
  read `_selectedSpendingIds()`, resolve them against
  `window._spendingAllRows` to get full row objects, filter to
  `category === 'uncategorized'` (silently skip already-categorized rows
  in the selection — matches the import flow's precedent of only ever
  suggesting for uncategorized rows).
- Deduplicate the filtered rows by `description` into a `Map<description,
  {rows: [...], date, amount, currency, category}>` — one representative
  row per unique description is sent to `/suggest-categories` (keeps the
  LLM call small even when hundreds of rows are selected; a real account
  can have the same merchant description repeated dozens of times).
- Call `window.apiClient.suggestSpendingCategories(representativeRows)`
  (existing client method, already used by the import flow — no new API
  client method needed beyond it already accepting an array of
  `{date, description, amount, currency, category, is_duplicate}`-shaped
  objects).
- Render `#spSuggestReviewPanel`: one row per unique description, showing
  the description, an editable `<select>` pre-filled with the suggested
  category (same category list already built for the per-row inline
  dropdown), a checkbox (checked by default) to include/exclude, and how
  many saved rows share that description (e.g. "×12"). An "Apply N" /
  "Discard" button pair at the bottom.
- On "Apply": for each checked suggestion, (a) call
  `window.apiClient.createSpendingRule(suggested_pattern, chosenCategory)`
  — same "accept creates a rule" behavior as the import flow — and (b) for
  every *originally selected* row matching that description, call
  `window.apiClient.updateSpendingCategory(id, chosenCategory)`. Tally
  succeeded/failed per row the same way `_wireSpBulkActions`'s existing
  recategorize handler already does. Refresh via `_refreshSpendingData()`
  and clear/hide the review panel.
- "Discard": just hides/clears the panel, no writes.

No new `apiClient` methods are needed for this half either —
`suggestSpendingCategories`, `createSpendingRule`, and
`updateSpendingCategory` already exist and are already used by the import
flow; this feature is a new caller of the same three, plus the one new
`rescanCategories` client method for Feature A.

### Frontend: new API client method

`web_client/js/pfm_core.js`, alongside the existing `rescanTransfers()`
method:

```javascript
async rescanCategories() {
    const resp = await fetch(this.baseURL + '/api/v1/spending/rescan-categories', {
        method: 'POST', headers: { 'X-API-Key': this.apiKey }
    });
    if (!resp.ok) throw new Error('Failed to rescan categories');
    return resp.json();
},
```

### Error handling

- `rescan-categories` with zero uncategorized rows: returns
  `{"recategorized": 0}`, frontend shows "No new matches found." (mirrors
  the existing `rescan-transfers` "No new transfers found." message).
- Clicking "Suggest categories (AI)" with no uncategorized rows in the
  current selection: show a status message ("No uncategorized rows
  selected.") and do not call the LLM endpoint.
- LLM call failure: same as the import flow — show the error message,
  review panel stays empty/unopened, no partial state.
- Per-row `updateSpendingCategory` failure during Apply: tallied as
  failed, same pattern as the existing bulk recategorize handler; does not
  abort the remaining rows.

### Testing

- `tests/unit/test_spending_api.py`: new tests for
  `POST /api/v1/spending/rescan-categories` — (1) a rule added after an
  uncategorized row was saved gets applied on rescan; (2) a row that
  already has a non-"uncategorized" category is left untouched even if a
  rule would now match it differently; (3) zero-uncategorized-rows returns
  `{"recategorized": 0}`.
- No new backend tests needed for the AI-suggestion half — it calls the
  existing, already-tested `/suggest-categories` endpoint unchanged; the
  new behavior is entirely client-side (selection, dedup, review-panel
  render, apply). Frontend logic worth unit-testing per the existing
  `web_client/js/tests/` pattern: a pure dedup-by-description helper
  function (rows in → `Map`/array of unique-description groups out), since
  that's the one piece of new logic with real edge cases (empty selection,
  all-same-description, mixed categorized/uncategorized).
