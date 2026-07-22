# Spending page: tabs, server-side pagination, and category management

**Date:** 2026-07-22
**Status:** Approved

## Problem

The Spending page has grown into one long scroll: a summary/filter header,
a hand-rolled category-breakdown bar chart, a transactions table that
fetches and renders **every** transaction with no pagination (a real
account already has 2,500+ rows), and a full rules-management card
(add/edit/delete), all stacked vertically on one view. Three concrete
issues:

1. No pagination — `GET /api/v1/spending/` returns the full history on
   every page load/filter change/refresh, and the table renders all of
   it at once.
2. The category chart is a fixed-height list of every category as a
   horizontal div-bar, uncapped — with many categories it's tall and
   pushes the transaction table far down the page.
3. The rules card (list + add form) sits permanently below the
   transactions table, adding significant page length even though it's
   used far less often than the table itself.

Separately, category assignment currently has four different entry
points (per-row inline edit on the transactions table, bulk-select +
"Set category", the AI-suggest review panel, and the Add Rule form) —
one more than needed. And there is no way to rename a category (fixing
a typo, consolidating two near-duplicate names) or to create a category
that doesn't yet have any transaction/rule using it — categories today
exist purely as a byproduct of being used somewhere.

## Scope

**A) Tabbed layout.** Split the page into three Bootstrap tabs
(`Transactions` / `Categories` / `Rules`), matching the existing pattern
used by the Import/Export and Analytics pages
(`web_client/index.html`'s `#ioTabs`/`#analyticsTabs`). The page header
(title, Re-scan transfers/categories buttons, Import statement button,
the three summary cards, and the Account/Category/From/To filter row)
stays above the tabs, shared context for all three.

**B) Server-side pagination + sort for the Transactions tab.**
`GET /api/v1/spending/` gains `limit`, `offset`, `sort_by`, `sort_dir`
query params, combined with the existing `portfolio_id`/`category`/
`start_date`/`end_date`/`is_transfer` filters. Response shape changes
from a bare array to `{"items": [...], "total": N}`. This is the
endpoint's only consumer (`pfm_core.js`'s own `getSpendingTransactions`),
so the shape change is safe. Sorting and filtering both trigger a fresh
server request (correct across full history), not client-side
re-ordering of an in-memory page.

**C) Category column becomes read-only on the Transactions tab.** The
per-row free-text `<input>` added in the prior feature
(`docs/superpowers/specs/2026-07-22-spending-free-text-categories-design.md`)
is removed; the cell goes back to plain text. Recategorizing a
transaction from this tab is now only possible via bulk-select + "Set
category" (existing) or accepting an AI suggestion (existing).
`window.updateSpendingRowCategory` and its wiring are deleted as dead
code.

**D) Categories tab: chart + category management.** A Chart.js
horizontal bar chart (same style as the Analytics page's portfolio
comparison chart, `pfm_analytics.js`'s `_portfolioCompChart`) replaces
the hand-rolled `_renderSpendingCategoryChart` div-bars — sorted by
30-day amount, defaulting to the top 8 categories with a "Show all"
toggle. Below it, a list of every known category (name only) with an
edit-in-place pencil (mirrors the existing Rules list's
`editSpendingRule` pattern) to rename it, and a small "Add category"
form to create a bare, unused category.

**E) New `spending_categories` table** (db v27) — a lightweight name
registry, decoupled from `spending_transactions`/`spending_rules` (both
keep storing `category` as a free string, unchanged; no foreign key, no
migration of existing rows). Lets a category persist even with zero
transactions/rules currently using it. The category list surfaced
anywhere in the app (filters, the free-text datalist, the Categories
tab) becomes the union of: distinct categories already on
`spending_transactions`, distinct categories already on
`spending_rules`, and names in `spending_categories`.

**F) Renaming a category cascades everywhere.** `PUT
/api/v1/spending/categories/{old_name}` updates every
`spending_transactions` row and every `spending_rules` row currently
using `old_name`, and upserts the registry entry (renames it if
present, inserts `new_name` if the category was purely derived from
usage and never explicitly registered) — so nothing is left split
across the old and new name.

**Not doing:** deleting a category (not requested — a category with
transactions/rules still using it would need a defined behavior for
those rows, which wasn't asked for); case-insensitive category
name matching (nothing else in this codebase treats category names
case-insensitively; new/renamed categories use plain exact-string
uniqueness, same as `spending_rules.pattern`+`category` duplicate
detection added in the prior feature); applying the Account/Category/
Date filter row to the Categories tab's chart (it keeps showing the
same fixed 30-day breakdown it shows today, just relocated — not
asked for, and conflating "filtered view" with "management view" adds
ambiguity to what "Edit"/"Add" mean); lazy-fetching tab data (unlike
Analytics' per-tab API calls, all of Spending's non-transaction data —
summary, rules, categories — is cheap and already fetched together in
one `_refreshSpendingData()` call regardless of which tab is active;
only the Transactions tab's own paginated fetch is decoupled from that.
The Categories tab's chart still needs its *render* — not its data —
deferred until the tab is shown, purely because Chart.js can't size a
canvas that's inside a hidden `display:none` pane; see Design A.).

## Design

### A) Tab markup

`web_client/index.html`: below the existing shared header block (title,
Re-scan/Import buttons, summary cards, filter row — all unchanged, all
stay outside the tabs), add a `<ul class="nav nav-tabs mb-3" id="spTabs">`
with three `<button data-bs-toggle="tab" data-bs-target="#spPane...">`
triggers (`Transactions`/`Categories`/`Rules`, `Transactions` active by
default), then a `<div class="tab-content">` with three
`<div class="tab-pane fade ...">` panes — same structure as `#ioTabs`/
`#ioTabImport` etc. (`web_client/index.html` lines ~1903-1918). The
existing "Transactions" card (bulk bar + table) moves into
`#spPaneTransactions` unchanged except for B's pagination controls; the
existing "Spending by category" card moves into `#spPaneCategories` and
gains the categories-list + add-form markup from D; the existing
"Category rules" card moves into `#spPaneRules` unchanged.

No new JS is needed for the tab-*switching* itself (Bootstrap's
`data-bs-toggle="tab"` handles that natively, same as Import/Export).
One JS wiring IS needed: a Chart.js chart built while its canvas is
inside a `display:none` tab-pane renders at zero size (the canvas has
no measurable dimensions until its pane becomes visible) — the same
reason the Analytics page defers its own chart-bearing tabs to render
on `shown.bs.tab` rather than eagerly. `loadSpendingPage()` wires a
`shown.bs.tab` listener on the Categories tab button that re-invokes
`_renderSpendingCategoryChart` using whatever `by_category_eur` data is
already in memory (from the same `_refreshSpendingData()` fetch every
other tab already shares — no extra network call, just a re-render now
that the canvas has real dimensions).

### B) Backend pagination/sort

`portf_server/routers/spending.py`, `GET /api/v1/spending/` (`list_spending`,
currently ~line 336): add query params `limit: int = 50` (validated
`1 <= limit <= 200`), `offset: int = 0` (`>= 0`), `sort_by: str = "date"`
(one of `date`, `portfolio_name`, `description`, `category`, `amount`;
400 on any other value), `sort_dir: str = "desc"` (`asc`/`desc`; 400
otherwise). Response model becomes
`SpendingTransactionListResponse { items: List[SpendingTransactionResponse], total: int }`.

`portf_manager/database.py`, `list_spending_transactions`: gains
`limit`, `offset`, `sort_by`, `sort_dir` kwargs (all optional, default
`None`/unbounded). Its three other existing callers in
`portf_server/routers/spending.py` — transfer matching's
`list_spending_transactions(is_transfer=True)` (line 247),
`rescan_categories`'s `list_spending_transactions(category="uncategorized")`
(line 442), and the summary endpoint's
`list_spending_transactions(start_date=start_date)` (line 537) — pass
none of the new kwargs and keep today's unbounded/unsorted behavior
unchanged. (Investment-transaction CSV export is a separate table
entirely, `transactions`, not `spending_transactions` — unaffected by
this change.)

The method's query joins `portfolios` for `p.name AS portfolio_name`
(`SELECT s.*, p.name AS portfolio_name FROM spending_transactions s LEFT
JOIN portfolios p ON s.portfolio_id = p.id`) and currently has a fixed
`ORDER BY s.date DESC, s.id DESC`. When `sort_by`/`sort_dir` are given,
that fixed `ORDER BY` is *replaced* (not appended to) by one built from
a whitelist dict mapping the 5 allowed `sort_by` values to their real
SQL column references — `{"date": "s.date", "portfolio_name": "p.name",
"description": "s.description", "category": "s.category", "amount":
"s.amount"}` — never the client-supplied string interpolated directly
(SQL-injection risk, and `portfolio_name` specifically isn't a real
column on `spending_transactions`, only the joined alias). `sort_dir`
is similarly mapped through `{"asc": "ASC", "desc": "DESC"}`, not
interpolated raw. `LIMIT ? OFFSET ?` (bound params) is appended after.
A companion `count_spending_transactions(**same filters)` runs the
identical `WHERE` clause (no `JOIN` needed — the count doesn't touch
`portfolio_name`) wrapped in `SELECT COUNT(*)` for `total`.

### B) Frontend — Transactions tab pagination/sort

`web_client/js/pfm_features.js`: `_renderSpendingTable` is rewritten to
no longer use the shared `makeSortableTable` (client-side sort/filter
over an in-memory array — kept exactly as-is for every other page that
uses it; not touched by this change). New Spending-specific state:
`window._spTxState = { page: 0, pageSize: 50, sortBy: 'date', sortDir: 'desc' }`.
A new `_fetchAndRenderSpendingTable()` reads the Account/Category/Date
filter inputs + `_spTxState`, calls `apiClient.getSpendingTransactions({...})`
with `limit`/`offset`/`sort_by`/`sort_dir`/existing filters, and renders
the returned `items` (no client-side `filterSpendingRows` step — the
server already filtered). `filterSpendingRows` and its `window` export
are deleted (dead code once filtering is server-side); its 4 tests in
`web_client/js/tests/web_client.test.mjs` are removed with it. Column
`<th>` clicks set `sortBy`/toggle `sortDir` (mirroring the existing
click-to-sort UX) and re-call `_fetchAndRenderSpendingTable()`; changing
any filter input resets `page` to 0 and re-fetches. New pagination
controls below the table: Previous/Next buttons (disabled at the
first/last page) + "Page X of Y (N total)" text + a page-size
`<select>` (25/50/100, default 50) that resets to page 0 on change.

Bulk-select (`_selectedSpendingIds`, the bulk bar, "Apply rules to
selected", AI-suggest) all continue to operate on `window._spendingAllRows`-equivalent
— but since only one page is in memory now, bulk actions only ever see
the current page's checked rows, which is the correct/expected scope
(you can't bulk-select a row you can't see). This is not a behavior
change from what pagination inherently implies; no separate handling
needed.

### C) Read-only category column

`web_client/js/pfm_features.js`: in the (rewritten) transactions
`renderRows`, the category `<td>` goes from
`<input type="text" list="spCategoryList" ... onchange="window.updateSpendingRowCategory(...)">`
back to plain `<td>${esc(r.category)}</td>` (matching how `description`
already renders). `window.updateSpendingRowCategory` function is
deleted entirely — its only caller was this removed `onchange`.

### D) Categories tab

`web_client/index.html`: new tab pane containing a `<canvas>` for the
chart, a "Show all" toggle button, a `<div id="spCategoriesList">` for
the name+edit rows, and a small `<form id="spCategoryAddForm">` (single
text input + "Add" button, mirroring `#spRuleAddForm`'s shape).

`web_client/js/pfm_features.js`:
- `_renderSpendingCategoryChart` is rewritten to build a Chart.js
  `type: 'bar', indexAxis: 'y'` chart into the new canvas (module-level
  `_spCategoryChartInstance`, destroyed/recreated each render — same
  lifecycle as `_portfolioCompChart`), sorted desc by amount, sliced to
  the top 8 unless "Show all" is toggled (a `window._spCategoryChartShowAll`
  boolean flips on click and re-renders from the already-fetched
  `by_category_eur`, no re-fetch needed).
- New `_renderCategoriesList(categories)`: one row per category name
  with a pencil icon; click swaps the name `<span>` for a text `<input>`
  pre-filled with the current name (same click-to-edit shape as
  `editSpendingRule`) — Enter/blur commits via a new
  `apiClient.renameSpendingCategory(oldName, newName)`, Escape cancels,
  a blank/unchanged value on commit is a no-op (same guard shape as
  `editSpendingRule`'s pattern/category guard).
- New `_wireSpCategoryAddForm()`: submit calls a new
  `apiClient.createSpendingCategory(name)`; blank name is a client-side
  no-op (same `if (!x) return;` shape as `#spRuleAddForm`).
- Both the rename and add handlers call `_refreshSpendingData()`
  afterward (categories affect the filter dropdown and datalist
  everywhere, so a full refresh — already cheap, already the pattern
  used after every other spending mutation — keeps everything in sync).

`web_client/js/pfm_core.js`: new API client methods
`getSpendingCategories()` (`GET /api/v1/spending/categories`),
`createSpendingCategory(name)` (`POST /api/v1/spending/categories`),
`renameSpendingCategory(oldName, newName)`
(`PUT /api/v1/spending/categories/{encodeURIComponent(oldName)}`).

`_allSpendingCategories` (the shared helper from the prior feature)
changes signature from `(rows, extra)` to `(extra)` — it no longer scans
transaction rows for distinct categories (post-pagination,
`window._spendingAllRows` only ever holds one page, so scanning it would
silently drop every category not present on the current page).
`_refreshSpendingData` fetches `apiClient.getSpendingCategories()`
alongside summary/portfolios/rules in the same `Promise.all` and stores
the result as `window._spendingAllCategories`; `_allSpendingCategories`
becomes `[...new Set(['uncategorized', 'Transfer', ...window._spendingAllCategories, ...(extra || [])])].sort()`
— the hardcoded `'uncategorized'`/`'Transfer'` entries stay (a fresh
account with zero transfer-categorized rows yet should still offer
"Transfer" as a pattern target, matching today's behavior; the backend
union doesn't guarantee either value is present). All three call sites
(the Transactions tab's datalist population, the Categories tab, the
AI-suggest panel's `extra` argument for a not-yet-existing suggested
category) update their call to drop the now-removed first argument.

### E) `spending_categories` table (db v27)

`portf_manager/database.py`: bump `DATABASE_VERSION = 27`. New
`_migrate_to_v27`, registered alongside the existing `if current_version
< N` chain (same shape as `_migrate_to_v26`):

```python
def _migrate_to_v27(self, conn: sqlite3.Connection) -> None:
    """Migrate from v26 to v27 — spending category registry.

    A lightweight name registry for spending categories, decoupled from
    spending_transactions/spending_rules (which keep storing category
    as a free string, unchanged) — lets a category exist (freshly
    created, or renamed away from) even with zero transactions/rules
    currently using it.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spending_categories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
```

New `Database` methods: `list_spending_categories() -> List[str]` (the
three-way union described in Scope E, `SELECT DISTINCT category FROM
spending_transactions UNION SELECT DISTINCT category FROM spending_rules
UNION SELECT name FROM spending_categories ORDER BY 1`);
`create_spending_category(name) -> int` (plain insert);
`find_spending_category_by_name(name) -> Optional[Dict]` (registry-table
lookup, for duplicate detection on add — exact match, no case-folding);
`rename_spending_category(old_name, new_name) -> Dict` — in one
transaction:

1. `c1 = UPDATE spending_transactions SET category = ? WHERE category = ?`
   (`new_name`, `old_name`); `c2` = the same for `spending_rules`.
2. Registry upsert, three mutually exclusive cases (check
   `new_name`'s registry row first, since a naive
   `UPDATE spending_categories SET name = new_name WHERE name = old_name`
   would raise a `UNIQUE` violation whenever `new_name` is already
   registered under the merge case below):
   - `new_name` already has a `spending_categories` row (merge case —
     renaming into an existing category to consolidate a near-duplicate):
     `DELETE FROM spending_categories WHERE name = old_name` (a no-op
     if `old_name` was never explicitly registered); `new_name`'s
     existing row is left as-is.
   - Otherwise, `old_name` has a `spending_categories` row: plain
     `UPDATE spending_categories SET name = new_name WHERE name = old_name`.
   - Otherwise (neither registered — the category was purely derived
     from transaction/rule usage): `INSERT INTO spending_categories
     (name) VALUES (new_name)` — renaming it is also the first time
     it's explicitly recorded.

Returns `{"transactions_updated": c1, "rules_updated": c2}` for the
endpoint to report.

### F) Category endpoints

`portf_server/routers/spending.py`, new models/endpoints (placed near
the existing rules endpoints, same file):

```python
class SpendingCategoryBody(BaseModel):
    name: str


class SpendingCategoryRenameBody(BaseModel):
    new_name: str


@router.get("/categories", response_model=List[str])
async def list_categories(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List every known spending category (used + explicitly registered, deduplicated)."""
    return db.list_spending_categories()


@router.post("/categories", response_model=dict, status_code=201)
async def create_category(
    body: SpendingCategoryBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Register a new, initially-unused spending category."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if db.find_spending_category_by_name(name):
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category_id = db.create_spending_category(name)
    return {"id": category_id, "name": name}


@router.put("/categories/{old_name}", response_model=dict)
async def rename_category(
    old_name: str,
    body: SpendingCategoryRenameBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rename a category everywhere it's used (transactions, rules, registry)."""
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if new_name == old_name:
        raise HTTPException(status_code=400, detail="New name is the same as the current name")
    result = db.rename_spending_category(old_name, new_name)
    return {"old_name": old_name, "new_name": new_name, **result}
```

`old_name` arrives URL-decoded by FastAPI's path-parameter handling, so
`encodeURIComponent` on the frontend side round-trips correctly for
category names containing spaces/special characters.

### Error handling

- Pagination: `limit`/`offset` outside their valid ranges → 400 (FastAPI
  `Query(..., ge=1, le=200)` / `Query(..., ge=0)` — validation, not
  hand-written checks). Invalid `sort_by`/`sort_dir` → 400 with an
  explicit message naming the allowed values.
- Category add: blank name → 400; exact duplicate → 409 (same shape as
  the rule-duplicate check added in the prior feature).
- Category rename: blank new name → 400; renaming to the same name →
  400 (a no-op that would otherwise silently "succeed" while doing
  nothing — better to say so); renaming to a name that already exists
  elsewhere is *not* rejected — two categories merging into one via
  rename is a legitimate consolidation use case (the whole point of
  "fixing a near-duplicate name"), handled by the three-branch registry
  upsert in `rename_spending_category` above (merge case first, to
  avoid a `UNIQUE`-constraint error).
- Category rename/create client-side: same blank-value guards as the
  existing Rules edit-in-place / Add Rule form (`if (!x) return`).

### Testing

- Backend: `tests/unit/test_database.py` — bump all four
  `assert ... == 26` occurrences to 27 (same two-step pattern as prior
  version bumps in this codebase); new tests for
  `list_spending_categories` (union of the three sources, deduplicated),
  `create_spending_category`, `find_spending_category_by_name`,
  `rename_spending_category` (transactions updated, rules updated,
  registry upserted both when old_name was/wasn't previously
  registered, and the merge-into-existing-name case).
- Backend: `tests/unit/test_spending_api.py` — new tests for
  `GET /api/v1/spending/` pagination (`limit`/`offset` slicing, `total`
  count correct with filters applied, invalid `sort_by`/`limit` → 400)
  and the three new `/categories` endpoints (create, 409 duplicate,
  rename cascades to transactions+rules, rename merges into an existing
  name without erroring, blank-name 400s).
- Frontend: `web_client/js/tests/web_client.test.mjs` — remove
  `filterSpendingRows`'s 4 tests (function deleted). No new pure
  functions introduced by A/C/D beyond what's already covered
  elsewhere; DOM-wiring (pagination controls, tab switching, category
  edit-in-place, add-category form) follows this file's existing
  no-automated-test precedent for wiring code — verified manually
  instead: switch between all three tabs; page through transactions
  and confirm sort-by-amount/date orders correctly across pages (not
  just within one page); confirm a category column cell is no longer
  editable; rename a category and confirm both an existing transaction
  and an existing rule using it now show the new name; add a bare
  category and confirm it appears in the filter dropdown and the
  free-text datalist immediately.
