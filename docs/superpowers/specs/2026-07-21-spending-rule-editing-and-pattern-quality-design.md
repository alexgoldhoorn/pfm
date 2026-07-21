# Spending: editable AI-suggested patterns + rule editing + better prompt

**Date:** 2026-07-21
**Status:** Approved

## Problem

`spending_rules` matching is already substring-based (`_apply_rules()`:
`rule["pattern"].lower() in description.lower()`), not exact-match — the
matching engine is fine. The actual gap is upstream of matching, visible
against real production data (Abanca card-purchase descriptions):

```
767002813179FORN AIXELA          \TIANA\ES0000000020
767002813178FORN AIXELA TIANA 363447467
767002813178MERCADONA MERCAT DE\MONTGAT\ES0000000019
```

Every row carries a leading card/transaction-reference number and a
trailing `\CITY\ESyymmddNNNN`-style location+date+reference, so almost
every transaction's *full description* is unique even for repeat visits to
the same merchant. Two consequences:

1. The AI-suggest review panel (added in the prior feature) lets the user
   edit only the **category** before Apply — the **pattern** that actually
   gets written into a new `spending_rules` row is whatever the LLM
   returned, with no way to shorten/fix it if it's too specific (e.g. if
   the LLM ever echoes noise instead of extracting just the merchant name).
2. Once a rule exists, there is no way to fix it afterward either — the
   existing Rules card (`GET/POST /api/v1/spending/rules`,
   `DELETE /api/v1/spending/rules/{id}`) supports add and delete, but not
   edit.

A rule with too-specific a pattern behaves like an exact match in
practice, defeating the entire point of "accept a suggestion → it becomes
a reusable rule."

## Scope

Three independent, composable changes:

**A) Editable pattern in the AI-suggest review panel.** Each suggestion
row gains a text input (pre-filled with the LLM's `suggested_pattern`,
editable) alongside the existing editable category dropdown. Apply already
reads `g.suggestedPattern` from the in-memory group when calling
`createSpendingRule` — no change needed there, only to what populates and
can edit that field before Apply.

**B) Edit-in-place on the existing Rules list.** The Spending page's Rules
card (pattern, category, delete) gains an edit affordance per row — click
a pencil icon, both cells become inputs, Enter/blur saves via a new
`PUT /api/v1/spending/rules/{id}`, Escape cancels. Mirrors this codebase's
existing click-to-edit pattern (`editManualAssetAmount` on the Net Worth
page) rather than inventing a new interaction style.

**C) Better LLM prompt.** `_build_suggest_prompt()` gains an explicit
instruction to ignore leading numeric card/transaction-reference prefixes
and trailing `\CITY\ESyymmddNNNN`-style location/date/reference suffixes
when extracting the merchant name for `suggested_pattern` — reduces how
often A is actually needed, using the real noise shape observed in
production data as the concrete example in the prompt.

**Not doing:** changing `_apply_rules()`'s substring-match semantics
(already correct). Changing `dedupSpendingRowsByDescription`'s exact-string
grouping to something noise-tolerant (e.g. stripping the reference
suffix before grouping) — real value, but a materially harder, riskier
problem (reliably identifying "noise" across arbitrary bank formats) than
what was asked for here; once a single instance of a merchant gets a rule
via A, "Rescan categories" already sweeps up every other instance of that
merchant on the next click, which covers the practical need without this
extra complexity. A dedicated "Rules" sub-page — the existing Rules card
on the Spending page already has room for this per the design below; no
navigation change needed unless review reveals it doesn't fit.

## Design

### A) Editable pattern field

`web_client/js/pfm_features.js`, `_renderSpSuggestReviewPanel()`: add a
text `<input class="form-control form-control-sm sp-suggest-pattern">`
per group row, value initialized from `g.suggestedPattern`, next to the
existing category `<select>`. An `input` event listener updates
`window._spSuggestGroups[idx].suggestedPattern` in place — same pattern
already used for the category `<select>`'s `change` listener. No backend
change: `_applySpSuggestions` already calls
`createSpendingRule(g.suggestedPattern, g.suggestedCategory)` reading from
this same in-memory state.

### B) Rule edit-in-place

Backend (`portf_server/routers/spending.py`): new
`PUT /api/v1/spending/rules/{rule_id}` accepting a body with optional
`pattern`/`category` fields (at least one required, matching the pattern
`PUT /api/v1/spending/{spending_id}` already uses for category-only
updates). New `Database.update_spending_rule(rule_id, **kwargs)` in
`portf_manager/database.py`, mirroring `update_spending_transaction`'s
whitelist-and-dynamic-SET-clause shape (valid fields: `pattern`,
`category`).

Frontend (`web_client/js/pfm_features.js`, `_renderSpendingRules()`): each
row's pattern/category cells get a pencil-icon click target
(`window.editSpendingRule(id)`, mirroring `editManualAssetAmount`'s
click-cell-to-swap-in-inputs interaction): click swaps both cells for text
inputs pre-filled with current values; Enter or blur on either input
commits via a new `apiClient.updateSpendingRule(id, {pattern, category})`
and re-renders the rules list; Escape cancels and re-renders unchanged. A
blank pattern or category on commit is rejected client-side (same
non-empty validation the existing add-rule form already applies) —
show an inline error rather than saving an empty value.

### C) Prompt improvement

`_build_suggest_prompt()` in `spending.py`: add a paragraph (with the real
noise shapes as examples, using fictional data per this repo's privacy
rules — e.g. a made-up card-reference-number-and-city-code example, not
the real observed strings) instructing the LLM to strip leading
transaction-reference-number prefixes and trailing
`\CITY\ESyymmddNNNN`-style suffixes before extracting `suggested_pattern`,
so the returned pattern is the clean merchant name only.

### Error handling

- B's `PUT` with neither field set, or both blank after trimming: 400,
  same shape as other validation errors in this router.
- B's client-side: empty pattern/category on commit → inline error text,
  input stays open, no API call (mirrors the existing add-rule form's
  `if (!pattern || !category) return;` guard, just surfaced as a message
  instead of a silent no-op, since here the user already tried to save).
- A: an empty pattern at Apply time — `_apply_rules()` now explicitly
  skips blank patterns (a blank pattern is a substring of every string in
  Python, so leaving it unguarded would make it match everything, not
  nothing, as originally assumed here). The AI-suggest review panel skips
  rule creation entirely for a group whose pattern was cleared to blank
  (the category is still applied to the matching rows); `POST
  /api/v1/spending/rules` also now rejects a blank pattern/category with
  400, matching the validation `PUT /api/v1/spending/rules/{id}` already
  has.

### Testing

- Backend: `tests/unit/test_spending_api.py` — new tests for
  `PUT /api/v1/spending/rules/{id}`: updates pattern only, updates category
  only, updates both, 404 on unknown id, 400 on empty body.
- Frontend: no new pure functions introduced (A and B are DOM wiring, same
  precedent as prior features in this codebase — untested at that layer,
  only pure functions get `web_client/js/tests/` coverage). Verify
  manually: edit a rule's pattern/category and confirm it persists after
  reload; edit a suggested pattern in the review panel before Apply and
  confirm the created rule uses the edited value, not the original
  suggestion.
