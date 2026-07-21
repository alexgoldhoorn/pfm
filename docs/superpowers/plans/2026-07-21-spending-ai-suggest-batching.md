# Spending AI-Suggest Batching + Select-All-Uncategorized Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a real production 502 (the "Suggest categories (AI)" bulk action sent 1,334 unique descriptions to the LLM in one request, exceeding nginx's 200s proxy timeout) by capping each AI-suggest call to 30 unique descriptions with a clear "X of Y" status message, and add a one-click "Select all uncategorized" button so working through a large backlog batch-by-batch doesn't require manually re-filtering each round.

**Architecture:** Two small, independent, frontend-only changes in the same two files already touched by the prior categorization-follow-up feature — no backend/schema changes, no new pure functions, no new automated tests (both changes are either a `.slice()` on an existing array or DOM wiring, matching this codebase's existing no-test precedent for that kind of change).

**Tech Stack:** Vanilla JS / Bootstrap 5 (frontend, no build step).

## Global Constraints

- Comments go on the line before the code they describe, not inline.
- Never commit real personal/financial data.
- `node --test web_client/js/tests/` (or `make test-js`) must show all 49 existing tests still passing after this change (no new tests added, per the approved spec's testing section).
- Web client changes require rebuild + redeploy to take effect: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`.
- Both `PROJECT_STATUS.md` and `CLAUDE.md` must be updated (mandatory project convention).

---

## Task 1: Batch cap + "Select all uncategorized" button

**Files:**
- Modify: `web_client/index.html:2582-2585` (Transactions card header — add button) and `web_client/index.html:2591` (context only, no change needed there)
- Modify: `web_client/js/pfm_features.js` (the `suggestBtn` handler inside `_wireSpBulkActions()`, and `loadSpendingPage()` for the new button's wiring)

**Interfaces:**
- Consumes: `dedupSpendingRowsByDescription` (existing, unchanged), `window.apiClient.suggestSpendingCategories` (existing, unchanged), `_renderSpendingTable()` (existing, unchanged), `_updateSpBulkBar()` (existing, unchanged).
- Produces: nothing consumed by anything else — self-contained UI change.

- [ ] **Step 1: Read the current live code**

Read `web_client/index.html` around the "Transactions" card header (search
for `<span>Transactions</span>`) and `web_client/js/pfm_features.js` around
the `suggestBtn` handler inside `_wireSpBulkActions()` (search for
`spBulkSuggestBtn`) and the end of `loadSpendingPage()`'s filter-wiring
block (search for `spRescanCategories`, the most recently added sibling
button). Confirm the code shown in the steps below still matches — if
line numbers or exact surrounding text have drifted, match on the code
shape described, not literal line numbers.

- [ ] **Step 2: Add the "Select all uncategorized" button to index.html**

Find (around line 2582-2585):

```html
                    <div class="card mb-3">
                        <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                            <span>Transactions</span>
                        </div>
```

Replace with:

```html
                    <div class="card mb-3">
                        <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                            <span>Transactions</span>
                            <button class="btn btn-sm btn-outline-secondary" id="spSelectAllUncategorized" title="Filter to uncategorized rows and select them all"><i class="bi bi-check2-square me-1"></i>Select all uncategorized</button>
                        </div>
```

- [ ] **Step 3: Add the batch-size constant and cap the AI-suggest handler**

In `web_client/js/pfm_features.js`, find the `dedupSpendingRowsByDescription`
function definition (it ends with `window.dedupSpendingRowsByDescription = dedupSpendingRowsByDescription;`).
Immediately after that line, add:

```javascript
// Cap on unique descriptions sent to the LLM per "Suggest categories (AI)"
// click — a real account's uncategorized backlog can have 1000+ unique
// descriptions; sending them all in one request risks exceeding
// portf_web's nginx proxy_read_timeout (200s) with no useful error. One
// batch per click; the user re-selects and continues manually (see
// #spSelectAllUncategorized) — no auto-chaining needed since applying a
// batch's suggestions removes those rows from the uncategorized filter.
const SP_AI_SUGGEST_BATCH_SIZE = 30;
```

Then find the `suggestBtn` handler inside `_wireSpBulkActions()`:

```javascript
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
```

Replace with:

```javascript
            try {
                const allGroups = dedupSpendingRowsByDescription(selectedRows);
                const groups = allGroups.slice(0, SP_AI_SUGGEST_BATCH_SIZE);
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
                if (status) {
                    status.textContent = allGroups.length > groups.length
                        ? `Sent ${groups.length} of ${allGroups.length} unique descriptions in this selection. Apply this batch, then use "Select all uncategorized" again to continue with the rest.`
                        : `${window._spSuggestGroups.length} suggestion(s) ready for review below.`;
                }
            } catch (err) {
                if (status) { status.className = 'small text-danger px-3 pt-2'; status.textContent = err.message; }
            }
```

(Only the two lines building `groups`/`allGroups` and the status-message
block change; everything else in the handler — the disabled-state toggling,
the empty-selection guard above this block — is untouched.)

- [ ] **Step 4: Wire the "Select all uncategorized" button**

In `web_client/js/pfm_features.js`, inside `loadSpendingPage()`,
immediately after the `rescanCatBtn` wiring block added by the prior
feature (it ends with its closing `});` and `}`, right before the
`['spAccountFilter', 'spCategoryFilter', 'spFromDate', 'spToDate'].forEach(...)`
line), add:

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

- [ ] **Step 5: Verify**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing (syntax OK).

Run: `make test-js`
Expected: all 49 tests pass (no new tests added, per spec — this change
adds no new pure functions).

Rebuild and load the page to confirm visually:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On the Spending page: click "Select all uncategorized" and confirm the
Category filter switches to "uncategorized" and every visible row's
checkbox is checked. With more than 30 unique descriptions selected, click
"Suggest categories (AI)" and confirm the status message reads "Sent 30 of
N unique descriptions..."; confirm the review panel shows at most 30
rows; Apply a few, then click "Select all uncategorized" again and
confirm the remaining count shrunk by the number of rows whose category
changed.

- [ ] **Step 6: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "fix: cap AI-suggest batch size + add select-all-uncategorized

The bulk 'Suggest categories (AI)' action sent every unique
description in the selection to the LLM in one request. On a real
backlog (2,384 uncategorized rows, 1,334 unique descriptions
observed in production) this exceeded portf_web's nginx
proxy_read_timeout (200s), surfacing as an opaque 502. Caps each
call to 30 unique descriptions with an explicit 'X of Y' status
message. New 'Select all uncategorized' button makes working
through a large backlog batch-by-batch a one-click action per
round instead of a manual filter-then-select-all each time.

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Documentation updates

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section)
- Modify: `PROJECT_STATUS.md` (header date + new "Recent" line)

- [ ] **Step 1: Update CLAUDE.md**

Find the bullet added by the prior feature that starts with `- **AI
category suggestions on already-imported rows**:` in the Spending
Tracking section. Append one sentence to the end of that same bullet
(before its final period), or add a short trailing note — whichever reads
more naturally given the exact current text — covering: the call is
capped to 30 unique descriptions per click (`SP_AI_SUGGEST_BATCH_SIZE` in
`pfm_features.js`) to stay under `portf_web`'s nginx `proxy_read_timeout`
on large backlogs, and a new "Select all uncategorized" button
(`#spSelectAllUncategorized`) sets the Category filter to `uncategorized`
and selects every now-filtered row in one click, so working through a
large backlog is: select-all-uncategorized → suggest (batch of 30) →
review → Apply → repeat.

- [ ] **Step 2: Update PROJECT_STATUS.md**

Bump "Last updated" to today's actual date (check with `date +%F`) and add
a new "Recent" line (next sequential version number after whatever is
currently the top entry) summarizing: capped the AI-suggest batch to 30
unique descriptions per call (fixes a real 502 from nginx's proxy timeout
on a large uncategorized backlog) and added a "Select all uncategorized"
button for working through a backlog batch-by-batch.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document AI-suggest batching + select-all-uncategorized

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## After this plan ships

`web_client/` changes need `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web` to take effect. No backend restart needed (no Python changes in this plan).
