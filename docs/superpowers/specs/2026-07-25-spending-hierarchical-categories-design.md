# Spending: hierarchical categories (Income/Spend tree)

**Date:** 2026-07-25
**Status:** Approved

## Problem

Spending categories are a flat namespace (globally-unique free strings, as
of `docs/superpowers/specs/2026-07-24-spending-category-near-duplicate-detection-design.md`).
There is no way to group related categories — e.g. "Car Insurance" and
"Home Insurance" under an "Insurance" umbrella, or "Freelance"/"Invest
sale"/"Job" under "Income" — so the category-breakdown chart is a flat
list of every leaf category ever used, with no rollup view, and there is
no structural distinction between an income category and a spend
category beyond the sign of whatever transactions happen to use it.

This is phase 2 of a two-phase plan (phase 1, already shipped, added
near-duplicate detection/merge on top of the flat namespace). Phase 2
adds real hierarchy on top of that same namespace.

## Scope

**A) `spending_categories` becomes a tree.** Two new columns: `parent_id`
(self-referencing, nullable) and `is_root` (marks the two fixed roots,
"Income" and "Spend"). `spending_transactions.category` and
`spending_rules.category` **do not change** — they keep storing the bare
leaf name as a plain string, unchanged from phase 1. Category names stay
globally unique (confirmed decision — not unique-per-parent), so a bare
name still unambiguously identifies one tree node; phase 1's
near-duplicate detection, rename, and rule matching all keep working
without modification.

**B) Migration (db v28).** Seeds the two roots. For every category
already known today (used on a transaction, used on a rule, or
explicitly registered), auto-files it as a direct child of Income or
Spend based on the majority sign of its own past transactions (no
transactions → defaults to Spend). No deeper nesting is guessed — that's
manual, later, via the Categories tab. `uncategorized` and `Transfer`
stay outside the tree entirely, exactly as today.

**C) Sign validation.** A category's root ancestor must match a
transaction's amount sign (negative → under Spend, non-negative → under
Income); `uncategorized`/`Transfer` are exempt. Direct user edits
(`PUT /api/v1/spending/{id}`) reject a mismatch with 400. Automated/bulk
rule application (`_apply_rules`, used by upload-preview, `/save`, and
`/rescan-categories`) silently falls back to `uncategorized` on a
mismatch instead of erroring — these paths run unattended over many rows
at once.

**D) Tree management.** New reparent endpoint with cycle prevention.
Merging a category into another (phase 1's rename-to-merge, used by both
the manual rename-in-place flow and the "Possible duplicates" panel) now
also reparents the merged-away category's children onto the surviving
category, so a merge never orphans a subtree. Creating a category now
requires a parent.

**E) Chart rollup.** The Spending page's category chart (and the
Dashboard's "Spending" card, which reads the same data) sums each spend
transaction up to its nearest ancestor that is a direct child of Spend,
instead of one bar per leaf.

**F) Categories tab becomes a tree view**, with an indented list, a
"parent" control alongside the existing rename-in-place pencil, and a
required parent field on the Add Category form.

**G) Full-path display everywhere an existing category is picked.** The
shared datalist (`#spCategoryList`, phase 1's shared
`_allSpendingCategories`) today backs exactly two inputs — bulk
recategorize and the AI-suggest panel's per-suggestion field; the Add
Rule form's category field and the Add Category/rename-in-place name
fields were never wired to it (those are for typing a category's own new
name, not picking an existing one). All three "pick an existing category"
inputs (the two existing ones, plus the Add Rule form's field, which gains
the datalist for the first time here — the same one-line `list=` addition
phase 1 used elsewhere, and a natural fit since choosing an existing
category for a rule is the same kind of pick as bulk recategorize) show
each suggestion as a full breadcrumb path ("Spend > Insurance > Car")
instead of a bare name. The value actually submitted/stored stays the
bare leaf name (still globally unique, still unambiguous) — no
path-parsing needed server-side.

**Not doing:** unique-per-parent names (confirmed — names stay globally
unique); a dedicated tree-picker widget (confirmed — free text + a
path-aware datalist, reusing phase 1's inputs, was chosen over a new
widget); drag-and-drop reparenting (confirmed — a parent `<select>` next
to the existing rename control was chosen instead); leaf-only transaction
assignment (confirmed — a transaction can be filed on any node, leaf or
not, same as today's flat behavior); retroactively re-validating
already-categorized transactions when a category is reparented across
the Income/Spend divide (a category can move sides freely; sign
validation only ever runs at the moment a transaction's category is
written, not retroactively — same non-retroactive philosophy already
used for `uncategorized` rows never being force-touched by anything but
an explicit rescan).

**Known accepted limitation:** a positive-amount refund/reversal of a
purchase can't be filed under that purchase's own Spend-side category
(e.g. a "Groceries" refund) — sign validation would reject it, since
"Groceries" lives under Spend and the refund's amount is non-negative. It
would need an Income-side category instead, or stay `uncategorized`.
This is a direct, accepted consequence of the confirmed "real tree roots
+ sign validation" choice (see the Income/Spend roots decision above),
not an oversight — flagged here so it isn't a surprise later.

## Design

### A) Schema

`portf_manager/database.py`, both `_create_all_tables` (fresh installs,
~line 642) and the new `_migrate_to_v28` (existing installs) end up with
the same shape:

```sql
CREATE TABLE IF NOT EXISTS spending_categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    parent_id  INTEGER REFERENCES spending_categories(id),
    is_root    INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

For `_create_all_tables`, add `parent_id`/`is_root` directly to the
existing `CREATE TABLE` statement, then (still inside `_create_all_tables`,
after that statement) seed the two roots:

```python
conn.execute(
    "INSERT INTO spending_categories (name, parent_id, is_root) VALUES ('Income', NULL, 1), ('Spend', NULL, 1)"
)
```

A fresh install has no existing transactions to auto-file, so that's the
entire fresh-install path — no further migration logic needed there.

### B) Migration: `_migrate_to_v28`

`portf_manager/database.py`: bump `DATABASE_VERSION = 28`, register
`if current_version < 28: self._migrate_to_v28(conn)` in `_run_migrations`
(same chain shape as `_migrate_to_v27`, ~line 725).

```python
def _migrate_to_v28(self, conn: sqlite3.Connection) -> None:
    """Migrate from v27 to v28 — category tree (Income/Spend roots + parent_id).

    Every category known today (used on a transaction, used on a rule, or
    explicitly registered) is auto-filed as a direct child of Income or
    Spend, based on the majority sign of its own past transactions (no
    transactions -> defaults to Spend). No deeper nesting is guessed.
    "uncategorized" and "Transfer" are never added to the tree.
    """
    conn.execute(
        "ALTER TABLE spending_categories ADD COLUMN parent_id INTEGER REFERENCES spending_categories(id)"
    )
    conn.execute(
        "ALTER TABLE spending_categories ADD COLUMN is_root INTEGER NOT NULL DEFAULT 0"
    )

    def _get_or_create_root(name: str) -> int:
        # A pre-existing category already named "Income"/"Spend" (created via
        # phase 1's free-text Add Category before this migration ever ran)
        # would violate the UNIQUE(name) constraint on a plain INSERT —
        # promote it to a root in place instead of inserting a duplicate.
        row = conn.execute(
            "SELECT id FROM spending_categories WHERE name = ?", (name,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE spending_categories SET parent_id = NULL, is_root = 1 WHERE id = ?",
                (row[0],),
            )
            return row[0]
        return conn.execute(
            "INSERT INTO spending_categories (name, parent_id, is_root) VALUES (?, NULL, 1)",
            (name,),
        ).lastrowid

    income_id = _get_or_create_root("Income")
    spend_id = _get_or_create_root("Spend")

    names = [
        row[0]
        for row in conn.execute(
            """
            SELECT category FROM spending_transactions
            UNION SELECT category FROM spending_rules
            UNION SELECT name FROM spending_categories WHERE is_root = 0
            """
        ).fetchall()
        if row[0] not in ("uncategorized", "Transfer")
    ]
    for name in names:
        row = conn.execute(
            "SELECT SUM(CASE WHEN amount < 0 THEN 1 ELSE 0 END) AS neg, COUNT(*) AS total "
            "FROM spending_transactions WHERE category = ?",
            (name,),
        ).fetchone()
        is_spend = row[1] == 0 or row[0] >= (row[1] - row[0])
        parent_id = spend_id if is_spend else income_id
        existing = conn.execute(
            "SELECT id FROM spending_categories WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE spending_categories SET parent_id = ? WHERE id = ?",
                (parent_id, existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO spending_categories (name, parent_id) VALUES (?, ?)",
                (name, parent_id),
            )
    conn.commit()
```

(`row[1] == 0` — zero transactions, e.g. a rule-only or never-used
category — defaults to Spend per the confirmed tie-break; `row[0] >=
(row[1] - row[0])` — negative-amount count >= non-negative count — is the
majority-sign check.)

### C) Sign validation

`portf_manager/database.py`, new method:

```python
def get_spending_category_root(self, name: str) -> Optional[str]:
    """Walk parent_id up to the root and return 'Income'/'Spend', or None
    if name isn't in the tree (uncategorized/Transfer/unknown)."""
    with self.get_connection() as conn:
        row = conn.execute(
            "SELECT id, parent_id, is_root FROM spending_categories WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            return None
        while not row["is_root"]:
            if row["parent_id"] is None:
                return None
            row = conn.execute(
                "SELECT id, parent_id, is_root FROM spending_categories WHERE id = ?",
                (row["parent_id"],),
            ).fetchone()
        return conn.execute(
            "SELECT name FROM spending_categories WHERE id = ?", (row["id"],)
        ).fetchone()["name"]
```

`portf_server/routers/spending.py`, new helper near `_apply_rules`:

```python
def _sign_matches_root(root: Optional[str], amount: float) -> bool:
    """True if a category's tree root is consistent with a transaction's
    amount sign. A category outside the tree (root is None) is exempt."""
    if root is None:
        return True
    return (root == "Spend") == (amount < 0)
```

`_apply_rules` gains `amount` and `db` parameters; after a rule matches,
validate before returning:

```python
def _apply_rules(description: str, rules: List[dict], amount: float, db) -> str:
    desc_lower = description.lower()
    for rule in rules:
        pattern = rule["pattern"].strip()
        if pattern and pattern.lower() in desc_lower:
            category = rule["category"]
            if _sign_matches_root(db.get_spending_category_root(category), amount):
                return category
            return "uncategorized"
    return "uncategorized"
```

Both existing call sites pass the new arguments: `upload_bank_statement`
(~line 211, `_apply_rules(r.description, rules, r.amount, db)`) and
`rescan_categories` (~line 516,
`_apply_rules(row["description"], rules, row["amount"], db)`).

`update_spending_category` (~line 427) gains a hard-reject check, inserted
after the existing blank-category check:

```python
    root = db.get_spending_category_root(category)
    if not _sign_matches_root(root, existing["amount"]):
        raise HTTPException(
            status_code=400,
            detail=f"'{category}' is an {root} category; this transaction is {'a debit' if existing['amount'] < 0 else 'a credit'}",
        )
```

`save_spending_transactions` (~line 289) gains the same silent-fallback
treatment as `_apply_rules`'s callers — right before each row is
persisted (both the `create_spending_transaction` branch and the
`overwrite` branch), the row's `category` is passed through:

```python
    def _resolve_row_category(row) -> str:
        root = db.get_spending_category_root(row.category)
        return row.category if _sign_matches_root(root, row.amount) else "uncategorized"
```

defined once near the top of `save_spending_transactions`, called as
`category=_resolve_row_category(row)` at both the `create_spending_transaction`
call and the `overwrite` branch's `update_spending_transaction` call —
catches both rule-derived categories from `/upload` and any category the
user hand-edited in the preview modal before saving, uniformly.

### D) Tree management

**Reparent.** `portf_manager/database.py`, new method:

```python
def reparent_spending_category(self, name: str, new_parent_name: str) -> None:
    with self.get_connection() as conn:
        row = conn.execute(
            "SELECT id, is_root FROM spending_categories WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            raise ValueError(f"Category '{name}' not found")
        if row["is_root"]:
            raise ValueError(f"'{name}' is a root category and cannot be reparented")
        parent = conn.execute(
            "SELECT id FROM spending_categories WHERE name = ?", (new_parent_name,)
        ).fetchone()
        if not parent:
            raise ValueError(f"Category '{new_parent_name}' not found")
        # Cycle check: walk new_parent's ancestors, reject if `row` appears.
        cursor_id = parent["id"]
        while cursor_id is not None:
            if cursor_id == row["id"]:
                raise ValueError("That would make a category its own ancestor")
            cursor_id = conn.execute(
                "SELECT parent_id FROM spending_categories WHERE id = ?", (cursor_id,)
            ).fetchone()["parent_id"]
        conn.execute(
            "UPDATE spending_categories SET parent_id = ? WHERE id = ?",
            (parent["id"], row["id"]),
        )
        conn.commit()
```

`portf_server/routers/spending.py`, new endpoint:

```python
class SpendingCategoryReparentBody(BaseModel):
    new_parent_name: str


@router.put("/categories/{name}/parent", response_model=dict)
async def reparent_category(
    name: str,
    body: SpendingCategoryReparentBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Move a category under a different parent."""
    try:
        db.reparent_spending_category(name, body.new_parent_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": name, "new_parent_name": body.new_parent_name}
```

**Merge reparents children.** `rename_spending_category` (~line 3060),
merge branch (`new_name` already registered): before the existing
`DELETE FROM spending_categories WHERE name = ?` for `old_name`, move any
children of `old_name` onto `new_name`:

```python
            if self.find_spending_category_by_name(new_name):
                old_row = conn.execute(
                    "SELECT id FROM spending_categories WHERE name = ?", (old_name,)
                ).fetchone()
                new_row = conn.execute(
                    "SELECT id FROM spending_categories WHERE name = ?", (new_name,)
                ).fetchone()
                if old_row:
                    conn.execute(
                        "UPDATE spending_categories SET parent_id = ? WHERE parent_id = ?",
                        (new_row["id"], old_row["id"]),
                    )
                conn.execute(
                    "DELETE FROM spending_categories WHERE name = ?", (old_name,)
                )
```

**Create requires a parent.** `SpendingCategoryBody` gains
`parent_name: str`; `create_category` (~line 596) resolves it to an id
and 400s if not found, inserting with that `parent_id`:

```python
class SpendingCategoryBody(BaseModel):
    name: str
    parent_name: str
```

```python
    parent = db.find_spending_category_by_name(body.parent_name.strip())
    if not parent:
        raise HTTPException(status_code=400, detail=f"Parent category '{body.parent_name}' not found")
    category_id = db.create_spending_category(name, parent_id=parent["id"])
```

`create_spending_category` (~line 3042) gains an *optional*
`parent_id: Optional[int] = None` parameter — optional specifically so
the 7 existing call sites across `tests/test_database.py` and
`tests/unit/test_spending_api.py` that call it with just a name keep
working unchanged; the "a parent is required" rule is enforced one layer
up, by `create_category`'s 400 check above, not by the DB method itself:

```python
def create_spending_category(self, name: str, parent_id: Optional[int] = None) -> int:
    """Register a new, initially-unused spending category."""
    with self.get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO spending_categories (name, parent_id) VALUES (?, ?)",
            (name, parent_id),
        )
        conn.commit()
        return cursor.lastrowid
```

### E) Tree query endpoint + chart rollup

`portf_manager/database.py`, new method (the existing
`list_spending_categories() -> List[str]` stays exactly as-is — this is
additive):

```python
def list_spending_categories_tree(self) -> List[Dict]:
    """Every category with its tree position: id, name, parent_id,
    parent_name (None for roots), is_root."""
    with self.get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT c.id, c.name, c.parent_id, p.name AS parent_name, c.is_root
            FROM spending_categories c
            LEFT JOIN spending_categories p ON c.parent_id = p.id
            ORDER BY c.name
            """
        )
        return [dict(row) for row in cursor.fetchall()]
```

`portf_server/routers/spending.py`, new endpoint next to the existing
`/categories` ones:

```python
@router.get("/categories/tree", response_model=List[dict])
async def list_categories_tree(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every category with its tree position, for building paths/indented views."""
    return db.list_spending_categories_tree()
```

**Chart rollup.** `get_spending_summary` (~line 631) builds
`by_category_eur` today with `by_category_eur[r["category"]] = ...`
(~line 660). Change to resolve up to the nearest direct-child-of-Spend
ancestor first:

```python
    tree = {row["name"]: row for row in db.list_spending_categories_tree()}

    def _rollup_key(category: str) -> str:
        node = tree.get(category)
        if node is None:
            return category
        while node["parent_name"] is not None and node["parent_name"] != "Spend":
            node = tree.get(node["parent_name"])
            if node is None:
                return category
        return node["name"]
```

called as `by_category_eur[_rollup_key(r["category"])] = ...` instead of
the current `by_category_eur[r["category"]]`. `tree` is built once per
call (one extra query per summary request — this endpoint is already
called once per page load/filter change, not hot-looped). Both the
Spending page's chart and the Dashboard's "Spending" card read this same
`by_category_eur` field, so both roll up with this one change.

### F) Categories tab: tree view

`web_client/js/pfm_core.js`, new client methods alongside the existing
category ones:

```javascript
async getSpendingCategoryTree() {
    const response = await fetch(this.baseURL + '/api/v1/spending/categories/tree', {
        headers: { 'X-API-Key': this.apiKey }
    });
    if (!response.ok) throw new Error('Failed to load category tree');
    return response.json();
},
async reparentSpendingCategory(name, newParentName) {
    const response = await fetch(
        this.baseURL + '/api/v1/spending/categories/' + encodeURIComponent(name) + '/parent',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
            body: JSON.stringify({ new_parent_name: newParentName })
        }
    );
    if (!response.ok) {
        let detail = 'Failed to move category';
        try { detail = (await response.json()).detail || detail; } catch (e) { /* not JSON */ }
        throw new Error(detail);
    }
    return response.json();
},
```

`createSpendingCategory(name)` becomes `createSpendingCategory(name, parentName)`,
sending `{ name, parent_name: parentName }` in the body.

`web_client/js/pfm_features.js`, `_refreshSpendingData` (~line 4595)
fetches the tree alongside everything else and stores it:

```javascript
        const [summary, portfolios, categories, categoryTree, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(getSpendingPeriodDays()),
            window.apiClient.getPortfolios(),
            window.apiClient.getSpendingCategories(),
            window.apiClient.getSpendingCategoryTree(),
            window.apiClient.getSpendingRules(),
        ]);
```

with `window._spendingCategoryTree = categoryTree;` added alongside the
existing `window._spendingAllCategories = categories;` line, and
`_renderCategoriesList(categoryTree)` replacing the current
`_renderCategoriesList(categories)` call (the flat-name-list call to
`_renderPossibleDuplicates(categories)` stays unchanged — dedup detection
is still purely about names, unaffected by tree position).

`_renderCategoriesList` is rewritten to build an indented tree from
parent pointers instead of a flat sorted list:

```javascript
function _renderCategoriesList(tree) {
    window._spCategoriesListData = tree;
    const byParent = new Map();
    tree.forEach(c => {
        const key = c.parent_name || null;
        if (!byParent.has(key)) byParent.set(key, []);
        byParent.get(key).push(c);
    });
    const dir = window._spCategoriesSortDir;
    const sortSiblings = list => [...list].sort((a, b) => {
        const cmp = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
        return dir === 'asc' ? cmp : -cmp;
    });
    const rowHtml = (c, depth) => {
        const indent = 'ps-' + Math.min(depth * 3, 5);
        const editControl = c.is_root
            ? ''
            : `<button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingCategory(${c.id})" title="Edit"><i class="bi bi-pencil"></i></button>`;
        return `
        <div class="list-group-item d-flex align-items-center justify-content-between ${indent}" data-cat-id="${c.id}">
            <span id="spCategoryNameCell${c.id}" data-value="${escapeForAttr(c.name)}" data-parent="${escapeForAttr(c.parent_name || '')}">${esc(c.name)}</span>
            ${editControl}
        </div>`;
    };
    const renderChildren = (parentName, depth) =>
        sortSiblings(byParent.get(parentName) || []).map(c => rowHtml(c, depth) + renderChildren(c.name, depth + 1)).join('');
    const wrap = document.getElementById('spCategoriesList');
    if (!wrap) return;
    wrap.innerHTML = renderChildren(null, 0) || '<div class="list-group-item text-center text-muted py-2">No categories yet.</div>';
}

function _isDescendant(tree, ancestorId, candidateId) {
    let node = tree.find(c => c.id === candidateId);
    while (node && node.parent_id != null) {
        if (node.parent_id === ancestorId) return true;
        node = tree.find(c => c.id === node.parent_id);
    }
    return false;
}
window._isDescendant = _isDescendant;
```

(`rowHtml`'s trailing `+ (byParent.get...).map(...).join('')` line is
dead — superseded by `renderChildren`'s own recursion; the actual
recursive structure is `renderChildren`, which calls `rowHtml` for the
row itself and recurses into `renderChildren` for that row's children at
depth+1, concatenating both.) The two roots (`is_root`) render without an
edit control — nothing to rename/reparent/delete on them.

`window.editSpendingCategory` (currently index-based, ~line 5404) becomes
id-based (matching the `data-cat-id`/`spCategoryNameCell${c.id}` scheme
above) and gains a parent `<select>` alongside the existing name input:

```javascript
window.editSpendingCategory = function (id) {
    const cell = document.getElementById(`spCategoryNameCell${id}`);
    if (!cell || cell.dataset.editing) return;
    cell.dataset.editing = '1';
    const originalName = cell.dataset.value;
    const originalParent = cell.dataset.parent;
    const tree = window._spCategoriesListData || [];
    const cat = tree.find(c => c.name === originalName);
    const parentOptions = tree
        .filter(c => c.id !== cat.id && !_isDescendant(tree, cat.id, c.id))
        .map(c => `<option value="${escapeForAttr(c.name)}" ${c.name === originalParent ? 'selected' : ''}>${esc(c.name)}</option>`)
        .join('');
    cell.outerHTML = `
        <span class="d-flex gap-1">
            <input class="form-control form-control-sm" style="max-width:180px;" id="spCategoryNameCell${id}" value="${escapeForAttr(originalName)}">
            <select class="form-select form-select-sm" style="max-width:160px;" id="spCategoryParentCell${id}">${parentOptions}</select>
        </span>`;
    const input = document.getElementById(`spCategoryNameCell${id}`);
    const parentSelect = document.getElementById(`spCategoryParentCell${id}`);
    input.focus();
    input.select();

    let done = false;
    const finish = async (commit) => {
        if (done) return;
        done = true;
        const newName = input.value.trim();
        const newParent = parentSelect.value;
        if (!commit) { await _refreshSpendingData(); return; }
        try {
            if (newName && newName !== originalName) {
                if (!_warnIfSimilarCategory(newName, originalName)) { await _refreshSpendingData(); return; }
                await window.apiClient.renameSpendingCategory(originalName, newName);
            }
            if (newParent && newParent !== originalParent) {
                await window.apiClient.reparentSpendingCategory(newName || originalName, newParent);
            }
        } catch (err) {
            alert('Error: ' + err.message);
        }
        await _refreshSpendingData();
    };
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') finish(true);
        if (e.key === 'Escape') finish(false);
    });
    input.addEventListener('blur', () => finish(true));
    parentSelect.addEventListener('change', () => finish(true));
};
```

(Renaming and reparenting are independent, sequential calls when both
changed — a rename first, so the reparent call, if any, targets the
post-rename name.) `_wireCategoriesSortToggle` (~line 5391) is unaffected
— sort direction still applies per-sibling-group inside
`_renderCategoriesList`'s `sortSiblings`.

`_wireSpCategoryAddForm` (~line 5529) gains a parent `<select>`:
`web_client/index.html`'s `#spCategoryAddForm` gets a second field,
`<select id="spCategoryParentInput">`, populated from
`window._spendingCategoryTree` on each render (defaulting to "Spend"
selected), and the submit handler passes it through:

```javascript
            const name = document.getElementById('spCategoryNameInput').value.trim();
            const parentName = document.getElementById('spCategoryParentInput').value;
            if (!name) return;
            if (!_warnIfSimilarCategory(name)) return;
            const status = document.getElementById('spCategoryAddStatus');
            try {
                await window.apiClient.createSpendingCategory(name, parentName);
```

### G) Full-path datalist

`_allSpendingCategories(extra)` (~line 4969) currently returns bare
names from `window._spendingAllCategories`. It now builds full paths from
`window._spendingCategoryTree` instead:

```javascript
function _categoryFullPath(tree, name) {
    const byName = new Map(tree.map(c => [c.name, c]));
    const parts = [name];
    let node = byName.get(name);
    while (node && node.parent_name) {
        parts.unshift(node.parent_name);
        node = byName.get(node.parent_name);
    }
    return parts.join(' > ');
}
window._categoryFullPath = _categoryFullPath;

function _allSpendingCategories(extra) {
    const tree = window._spendingCategoryTree || [];
    const names = [...new Set(['uncategorized', 'Transfer',
        ...(window._spendingAllCategories || []), ...(extra || [])])];
    return names
        .map(n => tree.some(c => c.name === n) ? _categoryFullPath(tree, n) : n)
        .sort();
}
```

(`uncategorized`/`Transfer` aren't in the tree, so they fall through to
their bare name unchanged, same as any not-yet-migrated edge case.)

`web_client/index.html`: the Add Rule form's category field
(`<input class="form-control form-control-sm" id="spRuleCategory" placeholder="e.g. Groceries" required>`)
gains `list="spCategoryList"`, joining the two inputs that already have
it (`#spBulkCategorySelect`, `.sp-suggest-category`). The Add
Category/rename-in-place name fields are untouched — they're for typing
a category's own new name, never for picking an existing one, so they
were never wired to the datalist and don't need to be now.

The datalist's `<option value="...">` now carries a full path for every
option; the three inputs that read from it are unaffected code-wise
(still plain text inputs) — but the *committed* value on submit needs to
resolve back to a bare leaf name before being sent to the API, since
that's what every backend endpoint expects. Add one shared resolver:

```javascript
function _resolveCategoryInput(value) {
    const trimmed = value.trim();
    const leaf = trimmed.includes(' > ') ? trimmed.split(' > ').pop() : trimmed;
    return leaf;
}
window._resolveCategoryInput = _resolveCategoryInput;
```

and wrap every read of a category `<input>`'s `.value` at the three
datalist-backed call sites (`_wireSpBulkActions`'s recat handler,
`_wireSpendingRuleForm`, `_applySpSuggestions`'s per-suggestion
`suggestedCategory`) with `_resolveCategoryInput(...)` before use —
e.g. `_wireSpBulkActions`'s
`document.getElementById('spBulkCategorySelect')?.value.trim()` becomes
`_resolveCategoryInput(document.getElementById('spBulkCategorySelect')?.value || '')`.
Since names are globally unique, taking the last segment after `" > "`
is unambiguous even if a user types a bare name with no path at all (the
`split('>').pop()` on a string with no `" > "` just returns the whole
trimmed string unchanged).

### Error handling

- Reparent: root categories rejected (400), unknown category/parent name
  rejected (400/404 — `reparent_spending_category` raises `ValueError`,
  caught and turned into 400 by the endpoint), cycle rejected (400). The
  client-side parent `<select>` in `editSpendingCategory` never *offers*
  an invalid option (self or descendant excluded from the list), so a
  cycle rejection should only ever occur from a stale tree snapshot —
  still handled server-side as the authority.
- Sign mismatch: hard 400 on `PUT /api/v1/spending/{id}` (surfaces via
  the existing per-row try/catch in bulk-recategorize and AI-suggest
  Apply — tallied as "failed", same pattern already established); silent
  fallback to `uncategorized` in `_apply_rules`'s callers and `/save` (no
  error surfaced — matches `rescan-categories`'s existing "never
  overwrites, silently skips what it can't confidently do" philosophy).
- Create category: missing/unknown `parent_name` → 400 (mirrors the
  existing blank-name 400).
- Merge: unchanged from phase 1's existing error handling
  (`renameSpendingCategory`'s existing `alert('Error: ' + err.message)`
  in `mergeSpendingCategories`) — the added child-reparenting is an
  internal detail of the same DB call, no new failure mode exposed to
  the caller.

### Testing

- `tests/test_database.py`: bump all four `== 27` occurrences to `28`
  (same two-step pattern as the v26→v27 bump). New tests for
  `_migrate_to_v28` (roots created, existing categories filed under the
  correct root by transaction-sign majority, a rule-only/never-used
  category defaults to Spend, `uncategorized`/`Transfer` never appear in
  `spending_categories`), `get_spending_category_root` (leaf under
  nested parents resolves to the correct root, unknown name returns
  `None`), `reparent_spending_category` (successful move, root rejected,
  unknown names rejected, cycle rejected — including a multi-level cycle,
  not just an immediate self-parent), `list_spending_categories_tree`
  (correct `parent_name` resolution including `None` for roots),
  `rename_spending_category`'s merge branch now also reparenting children
  (a category with children merged into an existing category — assert
  the children's `parent_id` now points at the survivor).
- `tests/unit/test_spending_api.py`: new tests for
  `PUT /api/v1/spending/{id}` sign-mismatch rejection (400, both
  directions — a Spend-side category on a positive amount and vice
  versa), `/save` silently falling back to `uncategorized` on a
  sign-mismatched row instead of erroring the whole batch,
  `/rescan-categories` never applying a sign-mismatched rule,
  `GET/POST/PUT /categories/tree`-and-`/parent` endpoints (tree shape,
  create requires valid `parent_name`, reparent success/400s).
- Frontend: `web_client/js/tests/web_client.test.mjs` — new tests for
  `_categoryFullPath` (nested path built correctly, root-level category
  returns just its own name), `_isDescendant` (direct child, multi-level
  descendant, unrelated node returns false), `_resolveCategoryInput`
  (full path → leaf, bare name → unchanged, exercises the `" > "` split
  edge case of a category name that itself happens to contain `>`
  — not expected in practice but worth asserting the split only ever
  pops the last segment). `_allSpendingCategories`'s existing tests
  (phase 1) need updating for the new tree-based path-building — a
  category present in `window._spendingCategoryTree` should now render
  as its full path, not its bare name. DOM-wiring changes
  (`_renderCategoriesList`'s recursion, `editSpendingCategory`'s two-field
  edit, the Add Category parent select) follow this file's established
  no-automated-test precedent for wiring code — verified manually
  instead: create a new category nested two levels deep via the Add
  Category form's parent select; confirm it renders correctly indented;
  reparent an existing category via the edit control and confirm its
  children move with it (visually, and via the "Merge" test above at the
  DB layer); attempt to reparent a category onto its own descendant and
  confirm the option simply isn't offered in the dropdown; confirm the
  Spending page's chart now shows one bar per top-level Spend child, with
  amounts correctly summed across nested children; confirm the Dashboard
  "Spending" card shows the same rolled-up breakdown; confirm all three
  datalist-backed entry points (bulk recategorize, Add Rule form,
  AI-suggest panel) now show full paths in suggestions but still
  submit/persist correctly as a bare name.
