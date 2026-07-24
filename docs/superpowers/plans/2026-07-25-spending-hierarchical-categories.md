# Spending Hierarchical Categories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the flat spending-category namespace into a tree rooted at two fixed nodes, "Income" and "Spend" — with migration of existing categories, sign-validated category assignment, reparenting, merge-preserves-children, a rolled-up chart, and a tree view on the Categories tab.

**Architecture:** `spending_categories` (already exists from phase 1) gains `parent_id`/`is_root` columns. `spending_transactions.category`/`spending_rules.category` are untouched — still bare, globally-unique leaf-name strings; the tree lives entirely in `spending_categories`, resolved on demand by walking `parent_id`. Backend gains tree-query/reparent endpoints and sign validation woven into the three existing category-write paths. Frontend gains a tree-view Categories tab and full-path display in the three datalist-backed category inputs (values still submit as bare names).

**Tech Stack:** Python/FastAPI + SQLite backend (`portf_manager/database.py`, `portf_server/routers/spending.py`), vanilla JS frontend (`web_client/js/pfm_core.js`, `web_client/js/pfm_features.js`, `web_client/index.html`), pytest (backend) + Node's built-in `node --test` (frontend).

## Global Constraints

- Category names stay globally unique — a bare name unambiguously identifies one tree node no matter its position (spec: Scope A).
- `spending_transactions.category`/`spending_rules.category` are never modified by this plan — no migration of their contents, no schema change to either table.
- `uncategorized` and `Transfer` are never added to `spending_categories` — they stay outside the tree entirely (spec: Scope B).
- Sign validation: negative amount → category must resolve to root "Spend"; non-negative → root "Income"; a category outside the tree (root `None`) is exempt. Direct user edits (`PUT /api/v1/spending/{id}`) reject a mismatch with 400. Automated/bulk rule application (`_apply_rules` and its callers, `/save`) silently falls back to `uncategorized` on a mismatch instead of erroring (spec: Scope C).
- `DATABASE_VERSION` becomes `28`; the migration must be idempotent against a category already named "Income"/"Spend" (promote it to root rather than crashing on the UNIQUE constraint).
- `create_spending_category`'s new `parent_id` parameter is *optional* (`Optional[int] = None`) — the DB method itself must not require it, since 7 existing test call sites pass only a name. "A parent is required" is enforced one layer up, in the `POST /categories` endpoint only.
- No new npm/JS dependency.
- Design reference: `docs/superpowers/specs/2026-07-25-spending-hierarchical-categories-design.md`.

---

### Task 1: DB schema + migration (v28)

**Files:**
- Modify: `portf_manager/database.py` (`_create_all_tables` ~line 642, `DATABASE_VERSION` ~line 17, `_run_migrations` ~line 725, new `_migrate_to_v28`)
- Modify: `tests/test_database.py` (bump 4 `== 27` occurrences to `28`; new migration tests)

**Interfaces:**
- Produces: `spending_categories` table with `parent_id INTEGER REFERENCES spending_categories(id)` and `is_root INTEGER NOT NULL DEFAULT 0` columns, on both fresh installs and migrated ones. Two seed rows: `"Income"` and `"Spend"`, both `parent_id = NULL`, `is_root = 1`.
- Consumes: nothing new — this is the foundational task everything else builds on.

- [ ] **Step 1: Update `_create_all_tables`'s `spending_categories` definition**

In `portf_manager/database.py`, locate (~line 642):

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spending_categories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
```

Replace with:

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spending_categories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                parent_id  INTEGER REFERENCES spending_categories(id),
                is_root    INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO spending_categories (name, parent_id, is_root) VALUES ('Income', NULL, 1), ('Spend', NULL, 1)"
        )
```

- [ ] **Step 2: Bump `DATABASE_VERSION` and register the migration**

In `portf_manager/database.py`, change (~line 17):

```python
DATABASE_VERSION = 27
```

to:

```python
DATABASE_VERSION = 28
```

In `_run_migrations` (~line 725), locate:

```python
        if current_version < 27:
            self._migrate_to_v27(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

Replace with:

```python
        if current_version < 27:
            self._migrate_to_v27(conn)
        if current_version < 28:
            self._migrate_to_v28(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

- [ ] **Step 3: Write `_migrate_to_v28`**

In `portf_manager/database.py`, immediately after `_migrate_to_v27` (~line 1512, right after its closing `conn.commit()`), add:

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
            # A pre-existing category already named "Income"/"Spend" (created
            # via phase 1's free-text Add Category before this migration ever
            # ran) would violate the UNIQUE(name) constraint on a plain
            # INSERT -- promote it to a root in place instead.
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

- [ ] **Step 4: Bump version assertions in `tests/test_database.py`**

Run: `grep -n "== 27" tests/test_database.py`

For each of the 4 occurrences, change `27` to `28` (these assert the current schema version after a fresh DB init or a migration run — same two-step bump pattern used for every prior version increment in this file).

- [ ] **Step 5: Write the new migration tests**

Append to `tests/test_database.py`'s `TestSpendingCategories` class (~after the last existing test, `test_rename_spending_category_to_same_name_is_a_noop`):

```python
    def test_migration_seeds_income_and_spend_roots(self):
        # setup_method already created self.db via a fresh init, which runs
        # _create_all_tables (not the migration path) -- roots must exist
        # either way.
        cats = self.db.list_spending_categories_tree()
        income = next(c for c in cats if c["name"] == "Income")
        spend = next(c for c in cats if c["name"] == "Spend")
        assert income["is_root"] == 1
        assert income["parent_id"] is None
        assert spend["is_root"] == 1
        assert spend["parent_id"] is None

    def _build_v27_database(self, db_path, extra_sql=()):
        """Hand-build a v27-shaped database on disk: the pre-parent_id/is_root
        spending tables, stamped at schema version 27 (so constructing a real
        Database() against this file triggers _run_migrations automatically,
        exercising the actual upgrade path rather than invoking a migration
        method directly)."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE database_version (
                version INTEGER PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO database_version (version) VALUES (27)")
        conn.execute(
            """
            CREATE TABLE portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, account_type TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE spending_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio_id INTEGER,
                date TEXT, description TEXT, amount REAL, currency TEXT DEFAULT 'EUR',
                category TEXT DEFAULT 'uncategorized', is_transfer INTEGER DEFAULT 0,
                transfer_link_type TEXT, transfer_link_id INTEGER, source TEXT, balance REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE spending_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT, category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE spending_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for sql in extra_sql:
            conn.execute(sql)
        conn.commit()
        conn.close()

    def test_migrate_to_v28_direct(self):
        db_path = tempfile.mktemp(suffix=".db")
        try:
            self._build_v27_database(
                db_path,
                extra_sql=[
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-05', 'D', -10.0, 'Groceries')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-06', 'D', -5.0, 'Groceries')",
                    "INSERT INTO spending_rules (pattern, category) VALUES ('NETFLIX', 'Subscriptions')",
                    "INSERT INTO spending_transactions (portfolio_id, date, description, amount, category) VALUES (1, '2026-01-07', 'D', 500.0, 'Salary')",
                ],
            )

            # Constructing Database() on an existing version-27 file triggers
            # _run_migrations automatically (27 < DATABASE_VERSION), which is
            # what actually runs _migrate_to_v28 -- this exercises the real
            # upgrade path an existing user's database goes through.
            db = Database(db_path)

            assert db.get_spending_category_root("Groceries") == "Spend"
            assert db.get_spending_category_root("Subscriptions") == "Spend"  # rule-only, no transactions -> defaults to Spend
            assert db.get_spending_category_root("Salary") == "Income"
            assert db.get_spending_category_root("uncategorized") is None
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_migrate_to_v28_promotes_preexisting_income_named_category(self):
        db_path = tempfile.mktemp(suffix=".db")
        try:
            # A user already created a category literally named "Income"
            # before this migration ever ran.
            self._build_v27_database(
                db_path,
                extra_sql=["INSERT INTO spending_categories (name) VALUES ('Income')"],
            )

            db = Database(db_path)

            tree = db.list_spending_categories_tree()
            income_rows = [c for c in tree if c["name"] == "Income"]
            assert len(income_rows) == 1  # promoted in place, not duplicated
            assert income_rows[0]["is_root"] == 1
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
```

`setup_method`'s fresh `Database()` never exercises the migration path
itself — a version-0 DB always takes `_create_all_tables`, not
`_run_migrations` — so `test_migration_seeds_income_and_spend_roots`
above only covers fresh-install root seeding. The two
`test_migrate_to_v28_*` tests are the real migration coverage: they
hand-build a v27-shaped database on disk, stamp it at schema version 27,
then construct a real `Database(db_path)` against it — the constructor's
own `_initialize_database` sees `27 < DATABASE_VERSION` and runs
`_run_migrations` automatically, which is what actually invokes
`_migrate_to_v28`. This exercises the same upgrade path a real existing
installation goes through, rather than calling the migration method
directly. `list_spending_categories_tree` and `get_spending_category_root`
are defined in Task 2 — these two tests reference them, so Task 2 must
run before they can pass; write them now (they'll fail with
`AttributeError` until Task 2 lands) and confirm them green as part of
Task 2's own verification step instead of blocking on them here.

- [ ] **Step 6: Run the new tests to confirm the schema/migration mechanics that don't depend on Task 2**

Run: `python -m pytest tests/test_database.py -k "v27 or v28 or TestSpendingCategories" -v 2>&1 | tail -40`
Expected: the version-bump assertions and `test_migration_seeds_income_and_spend_roots` PASS; `test_migrate_to_v28_direct` and `test_migrate_to_v28_promotes_preexisting_income_named_category` FAIL with `AttributeError: 'Database' object has no attribute 'get_spending_category_root'` (or `list_spending_categories_tree`) — expected at this point, Task 2 adds those methods.

- [ ] **Step 7: Commit**

```bash
git add portf_manager/database.py tests/test_database.py
git commit -m "feat: add category-tree schema (parent_id/is_root) and v28 migration"
```

---

### Task 2: DB layer — tree query, root lookup, reparent, merge-reparents-children

**Files:**
- Modify: `portf_manager/database.py` (`create_spending_category` ~line 3042, `rename_spending_category` ~line 3060, new `get_spending_category_root`, `list_spending_categories_tree`, `reparent_spending_category`)
- Modify: `tests/test_database.py` (new tests)

**Interfaces:**
- Consumes: the `parent_id`/`is_root` columns from Task 1.
- Produces: `get_spending_category_root(name: str) -> Optional[str]`; `list_spending_categories_tree() -> List[Dict]` (each `{id, name, parent_id, parent_name, is_root}`); `reparent_spending_category(name: str, new_parent_name: str) -> None` (raises `ValueError` on any failure); `create_spending_category(name: str, parent_id: Optional[int] = None) -> int` (signature change — `parent_id` added, optional); `rename_spending_category`'s existing merge branch now also reparents children.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_database.py`'s `TestSpendingCategories` class:

```python
    def test_get_spending_category_root_resolves_nested_category(self):
        # "Income" already exists as a seeded root (Task 1) -- use its real
        # id rather than creating a second row of the same name, which
        # would violate the UNIQUE(name) constraint.
        income_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Income"
        )
        with self.db.get_connection() as conn:
            job_id = conn.execute(
                "INSERT INTO spending_categories (name, parent_id) VALUES ('Job', ?)",
                (income_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO spending_categories (name, parent_id) VALUES ('Salary', ?)",
                (job_id,),
            )
            conn.commit()
        assert self.db.get_spending_category_root("Salary") == "Income"
        assert self.db.get_spending_category_root("Job") == "Income"

    def test_get_spending_category_root_returns_none_for_unknown_name(self):
        assert self.db.get_spending_category_root("uncategorized") is None
        assert self.db.get_spending_category_root("Nonexistent") is None

    def test_list_spending_categories_tree_resolves_parent_name(self):
        spend_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)

        tree = self.db.list_spending_categories_tree()
        insurance = next(c for c in tree if c["name"] == "Insurance")
        assert insurance["parent_name"] == "Spend"
        spend = next(c for c in tree if c["name"] == "Spend")
        assert spend["parent_name"] is None

    def test_reparent_spending_category_moves_node(self):
        spend_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Spend"
        )
        self.db.create_spending_category("Car Insurance", parent_id=spend_id)
        self.db.create_spending_category("Insurance", parent_id=spend_id)

        self.db.reparent_spending_category("Car Insurance", "Insurance")

        tree = self.db.list_spending_categories_tree()
        car = next(c for c in tree if c["name"] == "Car Insurance")
        assert car["parent_name"] == "Insurance"

    def test_reparent_spending_category_rejects_root(self):
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Spend", "Income")

    def test_reparent_spending_category_rejects_unknown_names(self):
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Nonexistent", "Spend")
        self.db.create_spending_category("Vacation")
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Vacation", "AlsoNonexistent")

    def test_reparent_spending_category_rejects_cycle(self):
        spend_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)
        insurance_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Insurance"
        )
        self.db.create_spending_category("Car Insurance", parent_id=insurance_id)

        # Direct cycle: Insurance -> Car Insurance's parent, now try the reverse.
        with pytest.raises(ValueError):
            self.db.reparent_spending_category("Insurance", "Car Insurance")

    def test_rename_spending_category_merge_reparents_children(self):
        spend_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Spend"
        )
        self.db.create_spending_category("Insurance", parent_id=spend_id)
        self.db.create_spending_category("Cover", parent_id=spend_id)  # merge target
        insurance_id = next(
            c["id"] for c in self.db.list_spending_categories_tree() if c["name"] == "Insurance"
        )
        self.db.create_spending_category("Car Insurance", parent_id=insurance_id)

        self.db.rename_spending_category("Insurance", "Cover")  # merge case

        tree = self.db.list_spending_categories_tree()
        car = next(c for c in tree if c["name"] == "Car Insurance")
        assert car["parent_name"] == "Cover"

    def test_create_spending_category_without_parent_still_works(self):
        # Backward compatibility: existing callers that pass only a name
        # must keep working unchanged (parent_id defaults to None/NULL).
        cat_id = self.db.create_spending_category("Vacation")
        assert isinstance(cat_id, int)
        tree = self.db.list_spending_categories_tree()
        vacation = next(c for c in tree if c["name"] == "Vacation")
        assert vacation["parent_id"] is None
```

Add `import pytest` at the top of `tests/test_database.py` if not already present (check with `grep -n "^import pytest" tests/test_database.py` first).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_database.py -k TestSpendingCategories -v 2>&1 | tail -60`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'get_spending_category_root'` (and similarly for `list_spending_categories_tree`, `reparent_spending_category`); `test_create_spending_category_without_parent_still_works` and the merge/children test fail differently (missing `parent_id` column usage) until Step 3 lands.

- [ ] **Step 3: Implement the DB methods**

In `portf_manager/database.py`, change `create_spending_category` (~line 3042) from:

```python
    def create_spending_category(self, name: str) -> int:
        """Register a new, initially-unused spending category."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO spending_categories (name) VALUES (?)", (name,)
            )
            conn.commit()
            return cursor.lastrowid
```

to:

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

Immediately after `create_spending_category`, before `find_spending_category_by_name` (~line 3051), add:

```python
    def get_spending_category_root(self, name: str) -> Optional[str]:
        """Walk parent_id up to the root and return 'Income'/'Spend', or
        None if name isn't in the tree (uncategorized/Transfer/unknown)."""
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
            cursor_id = parent["id"]
            while cursor_id is not None:
                if cursor_id == row["id"]:
                    raise ValueError("That would make a category its own ancestor")
                next_row = conn.execute(
                    "SELECT parent_id FROM spending_categories WHERE id = ?", (cursor_id,)
                ).fetchone()
                cursor_id = next_row["parent_id"] if next_row else None
            conn.execute(
                "UPDATE spending_categories SET parent_id = ? WHERE id = ?",
                (parent["id"], row["id"]),
            )
            conn.commit()
```

In `rename_spending_category` (~line 3060), locate the merge branch:

```python
            if self.find_spending_category_by_name(new_name):
                conn.execute(
                    "DELETE FROM spending_categories WHERE name = ?", (old_name,)
                )
```

Replace with:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_database.py -k TestSpendingCategories -v 2>&1 | tail -60`
Expected: PASS — all `TestSpendingCategories` tests, including the ones added in this task and Task 1's `test_migrate_to_v28_direct`/`test_migrate_to_v28_promotes_preexisting_income_named_category` (which depend on these methods).

- [ ] **Step 5: Run the full backend test suite**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -20`
Expected: PASS, no regressions (the `create_spending_category` signature change is additive/optional, so existing callers passing just a name are unaffected).

- [ ] **Step 6: Commit**

```bash
git add portf_manager/database.py tests/test_database.py
git commit -m "feat: add category tree query, reparent, and merge-reparents-children"
```

---

### Task 3: Backend — sign validation

**Files:**
- Modify: `portf_server/routers/spending.py` (`_apply_rules` ~line 145, its two call sites, `update_spending_category` ~line 427, `save_spending_transactions` ~line 289)
- Test: `tests/unit/test_spending_api.py` (new tests)

**Interfaces:**
- Consumes: `db.get_spending_category_root(name)` (Task 2).
- Produces: `_sign_matches_root(root: Optional[str], amount: float) -> bool`; `_apply_rules(description: str, rules: List[dict], amount: float, db) -> str` (signature change — gains `amount`, `db`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_spending_api.py`:

```python
def test_update_category_rejects_sign_mismatch_income_category_on_debit(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="uncategorized"
    )
    income_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Income"
    )
    db.create_spending_category("Freelance", parent_id=income_id)

    r = client.put(
        f"/api/v1/spending/{tx_id}",
        json={"category": "Freelance"},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_update_category_rejects_sign_mismatch_spend_category_on_credit(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "Desc", 100.0, category="uncategorized"
    )
    spend_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Spend"
    )
    db.create_spending_category("Groceries", parent_id=spend_id)

    r = client.put(
        f"/api/v1/spending/{tx_id}",
        json={"category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_update_category_accepts_matching_sign(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="uncategorized"
    )
    spend_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Spend"
    )
    db.create_spending_category("Groceries", parent_id=spend_id)

    r = client.put(
        f"/api/v1/spending/{tx_id}",
        json={"category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 200


def test_update_category_exempt_for_uncategorized_and_transfer(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="Groceries"
    )
    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "Transfer"}, headers=HEADERS
    )
    assert r.status_code == 200

    tx_id_2 = db.create_spending_transaction(
        pid, "2026-01-06", "Desc", -10.0, category="Groceries"
    )
    r2 = client.put(
        f"/api/v1/spending/{tx_id_2}", json={"category": "uncategorized"}, headers=HEADERS
    )
    assert r2.status_code == 200


def test_rescan_categories_skips_sign_mismatched_rule(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    income_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Income"
    )
    db.create_spending_category("Freelance", parent_id=income_id)
    db.create_spending_rule(pattern="INVOICE123", category="Freelance")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "INVOICE123 payment", -10.0, category="uncategorized"
    )

    r = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["recategorized"] == 0
    assert db.get_spending_transaction(tx_id)["category"] == "uncategorized"


def test_save_falls_back_to_uncategorized_on_sign_mismatch(tmp_path):
    client, db = _make_client(tmp_path)
    income_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Income"
    )
    db.create_spending_category("Freelance", parent_id=income_id)

    r = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": db.create_portfolio(
                "Example Bank", account_type="bank"
            ),
            "duplicate_action": "add",
            "rows": [
                {
                    "date": "2026-01-05",
                    "description": "Desc",
                    "amount": -10.0,
                    "currency": "EUR",
                    "category": "Freelance",
                    "is_duplicate": False,
                }
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["saved"] == 1
    rows = client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
    assert rows[0]["category"] == "uncategorized"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_spending_api.py -k "sign_mismatch or exempt or rescan_categories_skips or save_falls_back" -v 2>&1 | tail -40`
Expected: FAIL — the sign-mismatch tests currently get 200 (no validation exists yet); the rescan/save fallback tests currently see the mismatched category applied as-is instead of falling back.

- [ ] **Step 3: Implement `_sign_matches_root` and update `_apply_rules`**

In `portf_server/routers/spending.py`, locate (~line 145):

```python
def _apply_rules(description: str, rules: List[dict]) -> str:
    """First-match-wins, case-insensitive substring match.

    Rules are already ordered by id (oldest = highest priority) by
    db.list_spending_rules(). A blank pattern is skipped rather than
    treated as a match-everything wildcard — "" is a substring of every
    string in Python, so an unguarded empty pattern would silently
    recategorize an entire backlog to one category.
    """
    desc_lower = description.lower()
    for rule in rules:
        pattern = rule["pattern"].strip()
        if pattern and pattern.lower() in desc_lower:
            return rule["category"]
    return "uncategorized"
```

Replace with:

```python
def _sign_matches_root(root: Optional[str], amount: float) -> bool:
    """True if a category's tree root is consistent with a transaction's
    amount sign. A category outside the tree (root is None) is exempt."""
    if root is None:
        return True
    return (root == "Spend") == (amount < 0)


def _apply_rules(description: str, rules: List[dict], amount: float, db) -> str:
    """First-match-wins, case-insensitive substring match.

    Rules are already ordered by id (oldest = highest priority) by
    db.list_spending_rules(). A blank pattern is skipped rather than
    treated as a match-everything wildcard — "" is a substring of every
    string in Python, so an unguarded empty pattern would silently
    recategorize an entire backlog to one category. A rule matching a
    category whose tree root doesn't match the transaction's amount sign
    is treated as a non-match (falls back to uncategorized) rather than
    applied incorrectly or raising -- this runs unattended over many rows.
    """
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

- [ ] **Step 4: Update `_apply_rules`'s two call sites**

In `upload_bank_statement` (~line 211), change:

```python
        category = _apply_rules(r.description, rules)
```

to:

```python
        category = _apply_rules(r.description, rules, r.amount, db)
```

In `rescan_categories` (~line 516), change:

```python
        category = _apply_rules(row["description"], rules)
```

to:

```python
        category = _apply_rules(row["description"], rules, row["amount"], db)
```

- [ ] **Step 5: Add the hard-reject check to `update_spending_category`**

In `portf_server/routers/spending.py` (~line 427), locate:

```python
    category = body.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")

    update_kwargs = {"category": category}
```

Replace with:

```python
    category = body.category.strip()
    if not category:
        raise HTTPException(status_code=400, detail="Category cannot be empty")

    root = db.get_spending_category_root(category)
    if not _sign_matches_root(root, existing["amount"]):
        raise HTTPException(
            status_code=400,
            detail=f"'{category}' is an {root} category; this transaction is {'a debit' if existing['amount'] < 0 else 'a credit'}",
        )

    update_kwargs = {"category": category}
```

- [ ] **Step 6: Add the silent-fallback to `save_spending_transactions`**

In `portf_server/routers/spending.py` (~line 289), locate:

```python
async def save_spending_transactions(
    body: SpendingSaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save previewed spending rows, honoring duplicate_action, then auto-link transfers."""
    saved = 0
```

Replace with:

```python
async def save_spending_transactions(
    body: SpendingSaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save previewed spending rows, honoring duplicate_action, then auto-link transfers."""

    def _resolve_row_category(row) -> str:
        root = db.get_spending_category_root(row.category)
        return row.category if _sign_matches_root(root, row.amount) else "uncategorized"

    saved = 0
```

Then in the same function, locate:

```python
                if body.duplicate_action == "overwrite":
                    db.update_spending_transaction(
                        existing["id"], category=row.category
                    )
                    overwritten += 1
                    saved_ids.append(existing["id"])
                    continue
                # "add": fall through and insert a second copy

            new_id = db.create_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                description=row.description,
                amount=row.amount,
                currency=row.currency,
                category=row.category,
                source="generic",
                balance=row.balance,
            )
```

Replace with:

```python
                if body.duplicate_action == "overwrite":
                    db.update_spending_transaction(
                        existing["id"], category=_resolve_row_category(row)
                    )
                    overwritten += 1
                    saved_ids.append(existing["id"])
                    continue
                # "add": fall through and insert a second copy

            new_id = db.create_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                description=row.description,
                amount=row.amount,
                currency=row.currency,
                category=_resolve_row_category(row),
                source="generic",
                balance=row.balance,
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_spending_api.py -k "sign_mismatch or exempt or rescan_categories_skips or save_falls_back" -v 2>&1 | tail -40`
Expected: PASS — all 6 new tests.

- [ ] **Step 8: Run the full backend test suite**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -20`
Expected: PASS, no regressions. If any pre-existing test calling `_apply_rules` directly (not through the HTTP endpoints) now fails with a missing-argument error, update that call site to pass `amount`/`db` too — search with `grep -rn "_apply_rules(" tests/ portf_server/`.

- [ ] **Step 9: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: validate category sign against Income/Spend tree root"
```

---

### Task 4: Backend — tree endpoint, reparent endpoint, create requires parent

**Files:**
- Modify: `portf_server/routers/spending.py` (new `SpendingCategoryReparentBody`, new `list_categories_tree`/`reparent_category` endpoints, `SpendingCategoryBody` gains `parent_name`, `create_category` validates it)
- Modify: `tests/unit/test_spending_api.py` (fix 3 existing tests that call `POST /categories` without `parent_name`; new tests)

**Interfaces:**
- Consumes: `db.list_spending_categories_tree()`, `db.reparent_spending_category()`, `db.create_spending_category(name, parent_id=...)`, `db.find_spending_category_by_name()` (all Task 2).
- Produces: `GET /api/v1/spending/categories/tree`, `PUT /api/v1/spending/categories/{name}/parent`.

- [ ] **Step 1: Fix the 3 existing tests broken by `parent_name` becoming required**

In `tests/unit/test_spending_api.py`, `test_create_category` (~line 961):

```python
def test_create_category(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Vacation"

    listed = client.get("/api/v1/spending/categories", headers=HEADERS).json()
    assert "Vacation" in listed
```

Replace with:

```python
def test_create_category(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories",
        json={"name": "Vacation", "parent_name": "Spend"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Vacation"

    listed = client.get("/api/v1/spending/categories", headers=HEADERS).json()
    assert "Vacation" in listed
```

`test_create_category_rejects_blank_name` (~line 973):

```python
def test_create_category_rejects_blank_name(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories", json={"name": "   "}, headers=HEADERS
    )
    assert r.status_code == 400
```

Replace with:

```python
def test_create_category_rejects_blank_name(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories",
        json={"name": "   ", "parent_name": "Spend"},
        headers=HEADERS,
    )
    assert r.status_code == 400
```

`test_create_category_rejects_exact_duplicate` (~line 981):

```python
def test_create_category_rejects_exact_duplicate(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    r = client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    assert r.status_code == 409
```

Replace with:

```python
def test_create_category_rejects_exact_duplicate(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/categories",
        json={"name": "Vacation", "parent_name": "Spend"},
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/categories",
        json={"name": "Vacation", "parent_name": "Spend"},
        headers=HEADERS,
    )
    assert r.status_code == 409
```

- [ ] **Step 2: Write the new failing tests**

Append to `tests/unit/test_spending_api.py`:

```python
def test_create_category_rejects_unknown_parent(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories",
        json={"name": "Vacation", "parent_name": "Nonexistent"},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_list_categories_tree_shape(tmp_path):
    client, db = _make_client(tmp_path)
    r = client.get("/api/v1/spending/categories/tree", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    names = {c["name"] for c in body}
    assert "Income" in names
    assert "Spend" in names
    income = next(c for c in body if c["name"] == "Income")
    assert income["parent_name"] is None
    assert income["is_root"] == 1


def test_reparent_category_moves_node(tmp_path):
    # Uses single-word names to avoid space-encoding ambiguity in the raw
    # URL path string built by TestClient -- URL-encoding a category name
    # containing a space is the frontend's job (encodeURIComponent, see
    # apiClient.reparentSpendingCategory), not what this test is checking.
    client, db = _make_client(tmp_path)
    client.post(
        "/api/v1/spending/categories",
        json={"name": "Insurance", "parent_name": "Spend"},
        headers=HEADERS,
    )
    client.post(
        "/api/v1/spending/categories",
        json={"name": "CarInsurance", "parent_name": "Spend"},
        headers=HEADERS,
    )
    r = client.put(
        "/api/v1/spending/categories/CarInsurance/parent",
        json={"new_parent_name": "Insurance"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    tree = client.get("/api/v1/spending/categories/tree", headers=HEADERS).json()
    car = next(c for c in tree if c["name"] == "CarInsurance")
    assert car["parent_name"] == "Insurance"


def test_reparent_category_rejects_root(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.put(
        "/api/v1/spending/categories/Spend/parent",
        json={"new_parent_name": "Income"},
        headers=HEADERS,
    )
    assert r.status_code == 400
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `python -m pytest tests/unit/test_spending_api.py -k "create_category_rejects_unknown_parent or list_categories_tree_shape or reparent_category" -v 2>&1 | tail -40`
Expected: FAIL — 404 (endpoint doesn't exist) for the tree/reparent tests; `create_category_rejects_unknown_parent` fails because `parent_name` isn't validated yet.

- [ ] **Step 4: Implement**

In `portf_server/routers/spending.py`, change `SpendingCategoryBody` (~line 130):

```python
class SpendingCategoryBody(BaseModel):
    name: str
```

to:

```python
class SpendingCategoryBody(BaseModel):
    name: str
    parent_name: str
```

Add near it:

```python
class SpendingCategoryReparentBody(BaseModel):
    new_parent_name: str
```

Change `create_category` (~line 596):

```python
@router.post("/categories", response_model=dict, status_code=201)
async def create_category(
    body: SpendingCategoryBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Register a new, initially-unused spending category."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if db.find_spending_category_by_name(name):
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    category_id = db.create_spending_category(name)
    return {"id": category_id, "name": name}
```

to:

```python
@router.post("/categories", response_model=dict, status_code=201)
async def create_category(
    body: SpendingCategoryBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Register a new, initially-unused spending category."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if db.find_spending_category_by_name(name):
        raise HTTPException(status_code=409, detail=f"Category '{name}' already exists")
    parent = db.find_spending_category_by_name(body.parent_name.strip())
    if not parent:
        raise HTTPException(
            status_code=400, detail=f"Parent category '{body.parent_name}' not found"
        )
    category_id = db.create_spending_category(name, parent_id=parent["id"])
    return {"id": category_id, "name": name}
```

Add two new endpoints after `rename_category` (~line 629, right after its closing `return`):

```python
@router.get("/categories/tree", response_model=List[dict])
async def list_categories_tree(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Every category with its tree position, for building paths/indented views."""
    return db.list_spending_categories_tree()


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

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_spending_api.py -k "create_category or list_categories_tree_shape or reparent_category" -v 2>&1 | tail -60`
Expected: PASS — all `create_category`/`list_categories_tree`/`reparent_category` tests.

- [ ] **Step 6: Run the full backend test suite**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -20`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add category tree/reparent endpoints, require parent on create"
```

---

### Task 5: Backend — chart rollup

**Files:**
- Modify: `portf_server/routers/spending.py` (`get_spending_summary` ~line 631)
- Test: `tests/unit/test_spending_api.py` (new test)

**Interfaces:**
- Consumes: `db.list_spending_categories_tree()` (Task 2).
- Produces: no new function — `by_category_eur` in `SpendingSummaryResponse` now keys by top-level Spend group instead of leaf category.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_spending_api.py`:

```python
def test_summary_rolls_up_category_chart_to_top_level_spend_group(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    spend_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Spend"
    )
    insurance_id = db.create_spending_category("Insurance", parent_id=spend_id)
    db.create_spending_category("Car Insurance", parent_id=insurance_id)
    db.create_spending_category("Home Insurance", parent_id=insurance_id)
    today = date.today().isoformat()
    db.create_spending_transaction(
        pid, today, "Desc", -30.0, category="Car Insurance"
    )
    db.create_spending_transaction(
        pid, today, "Desc", -20.0, category="Home Insurance"
    )

    r = client.get("/api/v1/spending/summary", params={"days": 30}, headers=HEADERS)
    assert r.status_code == 200
    by_cat = r.json()["by_category_eur"]
    assert by_cat.get("Insurance") == 50.0
    assert "Car Insurance" not in by_cat
    assert "Home Insurance" not in by_cat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_spending_api.py -k test_summary_rolls_up_category_chart_to_top_level_spend_group -v 2>&1 | tail -30`
Expected: FAIL — `by_category_eur` currently has separate `"Car Insurance"` and `"Home Insurance"` keys, not a combined `"Insurance"` key.

- [ ] **Step 3: Implement the rollup**

In `portf_server/routers/spending.py`, `get_spending_summary` (~line 631), locate:

```python
    spent_eur = 0.0
    income_eur = 0.0
    transferred_eur = 0.0
    by_category_eur: dict = {}

    for r in rows:
        amt_eur = float(r["amount"]) * _fx(r.get("currency", "EUR"))
        if r["is_transfer"]:
            transferred_eur += abs(amt_eur)
            continue
        if amt_eur < 0:
            spent_eur += abs(amt_eur)
            by_category_eur[r["category"]] = by_category_eur.get(
                r["category"], 0.0
            ) + abs(amt_eur)
        else:
            income_eur += amt_eur
```

Replace with:

```python
    spent_eur = 0.0
    income_eur = 0.0
    transferred_eur = 0.0
    by_category_eur: dict = {}

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

    for r in rows:
        amt_eur = float(r["amount"]) * _fx(r.get("currency", "EUR"))
        if r["is_transfer"]:
            transferred_eur += abs(amt_eur)
            continue
        if amt_eur < 0:
            spent_eur += abs(amt_eur)
            key = _rollup_key(r["category"])
            by_category_eur[key] = by_category_eur.get(key, 0.0) + abs(amt_eur)
        else:
            income_eur += amt_eur
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_spending_api.py -k test_summary_rolls_up_category_chart_to_top_level_spend_group -v 2>&1 | tail -30`
Expected: PASS.

- [ ] **Step 5: Run the full backend test suite**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -20`
Expected: PASS. If any pre-existing summary test asserted `by_category_eur` keyed by a leaf name that has no parent chain to "Spend" (e.g. a category created directly as a child of Spend, which rolls up to itself unchanged), it should still pass — the rollup is a no-op for direct-child-of-Spend categories. If a pre-existing test used a category that, post-migration, now sits *under* another category, update its assertion to expect the rolled-up key instead.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: roll up spending chart to top-level Spend categories"
```

---

### Task 6: Frontend — API client methods

**Files:**
- Modify: `web_client/js/pfm_core.js` (`createSpendingCategory` ~line 1537, new `getSpendingCategoryTree`/`reparentSpendingCategory`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `apiClient.getSpendingCategoryTree() -> Promise<Array>`; `apiClient.reparentSpendingCategory(name, newParentName) -> Promise<Object>`; `apiClient.createSpendingCategory(name, parentName)` (signature change — gains `parentName`).

- [ ] **Step 1: Update `createSpendingCategory` and add the two new methods**

In `web_client/js/pfm_core.js`, locate (~line 1537):

```javascript
        async createSpendingCategory(name) {
            const response = await fetch(this.baseURL + '/api/v1/spending/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name })
            });
            if (!response.ok) {
                let detail = 'Failed to create category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
```

Replace with:

```javascript
        async createSpendingCategory(name, parentName) {
            const response = await fetch(this.baseURL + '/api/v1/spending/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ name, parent_name: parentName })
            });
            if (!response.ok) {
                let detail = 'Failed to create category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
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
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
```

- [ ] **Step 2: Run the frontend test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs 2>&1 | tail -15`
Expected: PASS, no regressions (this file has no existing tests directly exercising these three methods' internals — the load-smoke test confirms the file still parses).

- [ ] **Step 3: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add category tree/reparent API client methods"
```

---

### Task 7: Frontend — Categories tab tree view

**Files:**
- Modify: `web_client/index.html` (Add Category form gains a parent `<select>`)
- Modify: `web_client/js/pfm_features.js` (`_refreshSpendingData` ~line 4595, `_renderCategoriesList` rewrite ~line 5344, new `_isDescendant`, `window.editSpendingCategory` rewrite ~line 5404, `_wireSpCategoryAddForm` ~line 5529)
- Test: `web_client/js/tests/web_client.test.mjs` (new tests for `_isDescendant`)

**Interfaces:**
- Consumes: `apiClient.getSpendingCategoryTree()`, `apiClient.reparentSpendingCategory()` (Task 6); `_warnIfSimilarCategory` (phase 1, unchanged).
- Produces: `window._spendingCategoryTree` (array of tree records, refreshed each `_refreshSpendingData()`); `_isDescendant(tree, ancestorId, candidateId) -> boolean`, exported as `window._isDescendant`.

- [ ] **Step 1: Write the failing test for `_isDescendant`**

Append to `web_client/js/tests/web_client.test.mjs`:

```javascript
test('_isDescendant: detects a direct child', () => {
    const ctx = loadAppIntoContext();
    const tree = [
        { id: 1, name: 'Spend', parent_id: null },
        { id: 2, name: 'Insurance', parent_id: 1 },
    ];
    assert.equal(ctx._isDescendant(tree, 1, 2), true);
});

test('_isDescendant: detects a multi-level descendant', () => {
    const ctx = loadAppIntoContext();
    const tree = [
        { id: 1, name: 'Spend', parent_id: null },
        { id: 2, name: 'Insurance', parent_id: 1 },
        { id: 3, name: 'Car Insurance', parent_id: 2 },
    ];
    assert.equal(ctx._isDescendant(tree, 1, 3), true);
});

test('_isDescendant: returns false for an unrelated node', () => {
    const ctx = loadAppIntoContext();
    const tree = [
        { id: 1, name: 'Spend', parent_id: null },
        { id: 2, name: 'Insurance', parent_id: 1 },
        { id: 3, name: 'Income', parent_id: null },
    ];
    assert.equal(ctx._isDescendant(tree, 1, 3), false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test web_client/js/tests/web_client.test.mjs 2>&1 | tail -20`
Expected: FAIL — `TypeError: ctx._isDescendant is not a function`.

- [ ] **Step 3: Add the parent `<select>` to the Add Category form**

In `web_client/index.html`, locate (~line 2745):

```html
                                    <form id="spCategoryAddForm" class="row g-2 align-items-end">
                                        <div class="col-8 col-sm-9">
                                            <label class="form-label small mb-1">New category name</label>
                                            <input class="form-control form-control-sm" id="spCategoryNameInput" placeholder="e.g. Vacation" required>
                                        </div>
                                        <div class="col-4 col-sm-3">
                                            <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                        </div>
                                    </form>
```

Replace with:

```html
                                    <form id="spCategoryAddForm" class="row g-2 align-items-end">
                                        <div class="col-5 col-sm-5">
                                            <label class="form-label small mb-1">New category name</label>
                                            <input class="form-control form-control-sm" id="spCategoryNameInput" placeholder="e.g. Vacation" required>
                                        </div>
                                        <div class="col-4 col-sm-4">
                                            <label class="form-label small mb-1">Parent</label>
                                            <select class="form-select form-select-sm" id="spCategoryParentInput"></select>
                                        </div>
                                        <div class="col-3 col-sm-3">
                                            <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                        </div>
                                    </form>
```

- [ ] **Step 4: Fetch and store the category tree in `_refreshSpendingData`**

In `web_client/js/pfm_features.js`, locate (~line 4595):

```javascript
async function _refreshSpendingData() {
    try {
        const [summary, portfolios, categories, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(getSpendingPeriodDays()),
            window.apiClient.getPortfolios(),
            window.apiClient.getSpendingCategories(),
            window.apiClient.getSpendingRules(),
        ]);
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        const el = id => document.getElementById(id);
        if (el('spSpent')) el('spSpent').innerHTML = eur(summary.spent_eur);
        if (el('spIncome')) el('spIncome').innerHTML = eur(summary.income_eur);
        if (el('spTransferred')) el('spTransferred').innerHTML = eur(summary.transferred_eur);

        window._spendingAllCategories = categories;
        const bankAccounts = (portfolios || []).filter(p => p.account_type === 'bank');
        _populateSpendingAccountFilters(bankAccounts);
        _renderSpendingCategoryChart(summary.by_category_eur || {});
        _renderSpendingRules(rules);
        _renderCategoriesList(categories);
        _renderPossibleDuplicates(categories);
        await _fetchAndRenderSpendingTable();
    } catch (err) {
        const body = document.getElementById('spTxBody');
        if (body) body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${esc(err.message)}</td></tr>`;
    }
}
```

Replace with:

```javascript
async function _refreshSpendingData() {
    try {
        const [summary, portfolios, categories, categoryTree, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(getSpendingPeriodDays()),
            window.apiClient.getPortfolios(),
            window.apiClient.getSpendingCategories(),
            window.apiClient.getSpendingCategoryTree(),
            window.apiClient.getSpendingRules(),
        ]);
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        const el = id => document.getElementById(id);
        if (el('spSpent')) el('spSpent').innerHTML = eur(summary.spent_eur);
        if (el('spIncome')) el('spIncome').innerHTML = eur(summary.income_eur);
        if (el('spTransferred')) el('spTransferred').innerHTML = eur(summary.transferred_eur);

        window._spendingAllCategories = categories;
        window._spendingCategoryTree = categoryTree;
        const bankAccounts = (portfolios || []).filter(p => p.account_type === 'bank');
        _populateSpendingAccountFilters(bankAccounts);
        _renderSpendingCategoryChart(summary.by_category_eur || {});
        _renderSpendingRules(rules);
        _renderCategoriesList(categoryTree);
        _renderPossibleDuplicates(categories);
        _populateSpCategoryParentSelect(categoryTree);
        await _fetchAndRenderSpendingTable();
    } catch (err) {
        const body = document.getElementById('spTxBody');
        if (body) body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${esc(err.message)}</td></tr>`;
    }
}

function _populateSpCategoryParentSelect(tree) {
    const sel = document.getElementById('spCategoryParentInput');
    if (!sel) return;
    sel.innerHTML = tree
        .map(c => `<option value="${escapeForAttr(c.name)}" ${c.name === 'Spend' ? 'selected' : ''}>${esc(c.name)}</option>`)
        .join('');
}
```

- [ ] **Step 5: Rewrite `_renderCategoriesList` and add `_isDescendant`**

In `web_client/js/pfm_features.js`, locate (~line 5344):

```javascript
function _renderCategoriesList(categories) {
    window._spCategoriesListData = categories;
    const dir = window._spCategoriesSortDir;
    const sorted = [...categories].sort((a, b) => {
        const cmp = a.toLowerCase().localeCompare(b.toLowerCase());
        return dir === 'asc' ? cmp : -cmp;
    });
    const wrap = document.getElementById('spCategoriesList');
    if (!wrap) return;
    wrap.innerHTML = sorted.length ? sorted.map((cat, i) => `
        <div class="list-group-item d-flex align-items-center justify-content-between">
            <span id="spCategoryNameCell${i}" data-value="${escapeForAttr(cat)}">${esc(cat)}</span>
            <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingCategory(${i})" title="Edit"><i class="bi bi-pencil"></i></button>
        </div>`).join('') : '<div class="list-group-item text-center text-muted py-2">No categories yet.</div>';
}
```

Replace with:

```javascript
function _isDescendant(tree, ancestorId, candidateId) {
    let node = tree.find(c => c.id === candidateId);
    while (node && node.parent_id != null) {
        if (node.parent_id === ancestorId) return true;
        node = tree.find(c => c.id === node.parent_id);
    }
    return false;
}
window._isDescendant = _isDescendant;

function _renderCategoriesList(tree) {
    window._spCategoriesListData = tree;
    if (!tree.length) {
        const wrap = document.getElementById('spCategoriesList');
        if (wrap) wrap.innerHTML = '<div class="list-group-item text-center text-muted py-2">No categories yet.</div>';
        return;
    }
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
    wrap.innerHTML = renderChildren(null, 0);
}
```

- [ ] **Step 6: Rewrite `window.editSpendingCategory`**

In `web_client/js/pfm_features.js`, locate (~line 5404):

```javascript
window.editSpendingCategory = function (idx) {
    const cell = document.getElementById(`spCategoryNameCell${idx}`);
    if (!cell || cell.dataset.editing) return;
    cell.dataset.editing = '1';
    const originalName = cell.dataset.value;
    cell.outerHTML = `<input class="form-control form-control-sm" style="max-width:220px;" id="spCategoryNameCell${idx}" value="${escapeForAttr(originalName)}">`;
    const input = document.getElementById(`spCategoryNameCell${idx}`);
    input.focus();
    input.select();

    let done = false;
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
};
```

Replace with:

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
        if (!commit) {
            await _refreshSpendingData();
            return;
        }
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

- [ ] **Step 7: Wire the Add Category form's parent field into the submit handler**

In `web_client/js/pfm_features.js`, locate `_wireSpCategoryAddForm` (~line 5529):

```javascript
            const name = document.getElementById('spCategoryNameInput').value.trim();
            if (!name) return;
            if (!_warnIfSimilarCategory(name)) return;
            const status = document.getElementById('spCategoryAddStatus');
            try {
                await window.apiClient.createSpendingCategory(name);
```

Replace with:

```javascript
            const name = document.getElementById('spCategoryNameInput').value.trim();
            const parentName = document.getElementById('spCategoryParentInput').value;
            if (!name) return;
            if (!_warnIfSimilarCategory(name)) return;
            const status = document.getElementById('spCategoryAddStatus');
            try {
                await window.apiClient.createSpendingCategory(name, parentName);
```

- [ ] **Step 8: Run the frontend test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs 2>&1 | tail -20`
Expected: PASS — the 3 new `_isDescendant` tests, plus no regressions elsewhere (nothing else in this file calls `_renderCategoriesList`/`editSpendingCategory` from a test — this is DOM-wiring code, verified manually in Step 9).

- [ ] **Step 9: Manual verification**

Run the app locally (check `Makefile`/`README.md` for how this project runs the web client + API if unfamiliar). On the Categories tab: confirm "Income" and "Spend" render at the top with no edit pencil; add a new category via the form with a chosen parent, confirm it renders indented under that parent; click its pencil, change its parent via the new select, confirm it re-renders under the new parent after commit; create a category with children, then attempt to set that category's own parent to one of its children via the dropdown — confirm that option simply isn't offered (not merely rejected after the fact).

- [ ] **Step 10: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: tree view + reparent controls on the Categories tab"
```

---

### Task 8: Frontend — full-path datalist

**Files:**
- Modify: `web_client/index.html` (`spRuleCategory` gains `list="spCategoryList"`)
- Modify: `web_client/js/pfm_features.js` (`_allSpendingCategories` rewrite ~line 4969, new `_categoryFullPath`/`_resolveCategoryInput`, 3 call-site updates)
- Test: `web_client/js/tests/web_client.test.mjs` (new tests for `_categoryFullPath`/`_resolveCategoryInput`)

**Interfaces:**
- Consumes: `window._spendingCategoryTree` (Task 7).
- Produces: `_categoryFullPath(tree, name) -> string`, exported as `window._categoryFullPath`; `_resolveCategoryInput(value) -> string`, exported as `window._resolveCategoryInput`; `_allSpendingCategories(extra)` (signature unchanged, behavior changed — returns full paths for tree members).

- [ ] **Step 1: Write the failing tests**

Append to `web_client/js/tests/web_client.test.mjs`:

```javascript
test('_categoryFullPath: builds a nested path from parent pointers', () => {
    const ctx = loadAppIntoContext();
    const tree = [
        { name: 'Spend', parent_name: null },
        { name: 'Insurance', parent_name: 'Spend' },
        { name: 'Car Insurance', parent_name: 'Insurance' },
    ];
    assert.equal(ctx._categoryFullPath(tree, 'Car Insurance'), 'Spend > Insurance > Car Insurance');
});

test('_categoryFullPath: a root-level category returns just its own name', () => {
    const ctx = loadAppIntoContext();
    const tree = [{ name: 'Spend', parent_name: null }];
    assert.equal(ctx._categoryFullPath(tree, 'Spend'), 'Spend');
});

test('_resolveCategoryInput: extracts the leaf from a full path', () => {
    const ctx = loadAppIntoContext();
    assert.equal(ctx._resolveCategoryInput('Spend > Insurance > Car Insurance'), 'Car Insurance');
});

test('_resolveCategoryInput: a bare name with no path separator is unchanged', () => {
    const ctx = loadAppIntoContext();
    assert.equal(ctx._resolveCategoryInput('Groceries'), 'Groceries');
});

test('_resolveCategoryInput: trims surrounding whitespace', () => {
    const ctx = loadAppIntoContext();
    assert.equal(ctx._resolveCategoryInput('  Groceries  '), 'Groceries');
});

test('_allSpendingCategories: renders a tree-known category as its full path', () => {
    const ctx = loadAppIntoContext();
    ctx.window._spendingAllCategories = ['Car Insurance'];
    ctx.window._spendingCategoryTree = [
        { name: 'Spend', parent_name: null },
        { name: 'Insurance', parent_name: 'Spend' },
        { name: 'Car Insurance', parent_name: 'Insurance' },
    ];
    const result = ctx._allSpendingCategories();
    assert.ok([...result].includes('Spend > Insurance > Car Insurance'));
});

test('_allSpendingCategories: a category not in the tree falls back to its bare name', () => {
    const ctx = loadAppIntoContext();
    ctx.window._spendingAllCategories = ['Groceries'];
    ctx.window._spendingCategoryTree = [];
    const result = ctx._allSpendingCategories();
    assert.ok([...result].includes('Groceries'));
});
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `node --test web_client/js/tests/web_client.test.mjs 2>&1 | tail -30`
Expected: FAIL — `ctx._categoryFullPath is not a function` / `ctx._resolveCategoryInput is not a function`; the two `_allSpendingCategories` tests fail because it doesn't yet consult `window._spendingCategoryTree`. Confirm the 3 pre-existing `_allSpendingCategories` tests (`includes uncategorized...`, `merges in extra categories`, `handles no configured categories`) still PASS unmodified — they never set `window._spendingCategoryTree`, so an empty-tree fallback must produce identical output to today.

- [ ] **Step 3: Implement**

In `web_client/js/pfm_features.js`, locate (~line 4969):

```javascript
function _allSpendingCategories(extra) {
    return [...new Set(['uncategorized', 'Transfer',
        ...(window._spendingAllCategories || []), ...(extra || [])])].sort();
}
```

Replace with:

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

function _resolveCategoryInput(value) {
    const trimmed = value.trim();
    return trimmed.includes(' > ') ? trimmed.split(' > ').pop() : trimmed;
}
window._resolveCategoryInput = _resolveCategoryInput;

function _allSpendingCategories(extra) {
    const tree = window._spendingCategoryTree || [];
    const names = [...new Set(['uncategorized', 'Transfer',
        ...(window._spendingAllCategories || []), ...(extra || [])])];
    return names
        .map(n => tree.some(c => c.name === n) ? _categoryFullPath(tree, n) : n)
        .sort();
}
```

- [ ] **Step 4: Add the datalist to the Add Rule form's category field**

In `web_client/index.html`, locate (~line 2775):

```html
                                            <input class="form-control form-control-sm" id="spRuleCategory" placeholder="e.g. Groceries" required>
```

Replace with:

```html
                                            <input class="form-control form-control-sm" list="spCategoryList" id="spRuleCategory" placeholder="e.g. Groceries" required>
```

- [ ] **Step 5: Wrap the 3 datalist-backed call sites with `_resolveCategoryInput`**

In `web_client/js/pfm_features.js`, `_wireSpBulkActions`'s recat handler (~line 5068), locate:

```javascript
            const category = document.getElementById('spBulkCategorySelect')?.value.trim();
```

Replace with:

```javascript
            const category = _resolveCategoryInput(document.getElementById('spBulkCategorySelect')?.value || '');
```

`_wireSpendingRuleForm` (~line 5502), locate:

```javascript
            const category = document.getElementById('spRuleCategory').value.trim();
```

Replace with:

```javascript
            const category = _resolveCategoryInput(document.getElementById('spRuleCategory').value);
```

`_renderSpSuggestReviewPanel`'s `.sp-suggest-category` listener (~line 5219), locate:

```javascript
    panel.querySelectorAll('.sp-suggest-category').forEach(inp => {
        inp.addEventListener('input', () => {
            window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedCategory = inp.value;
        });
    });
```

Replace with:

```javascript
    panel.querySelectorAll('.sp-suggest-category').forEach(inp => {
        inp.addEventListener('input', () => {
            window._spSuggestGroups[parseInt(inp.dataset.idx, 10)].suggestedCategory = _resolveCategoryInput(inp.value);
        });
    });
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `node --test web_client/js/tests/web_client.test.mjs 2>&1 | tail -30`
Expected: PASS — all new tests, plus the 3 pre-existing `_allSpendingCategories` tests unmodified.

- [ ] **Step 7: Manual verification**

With a category nested at least 2 levels deep: open the bulk-recategorize field, the Add Rule form's category field, and the AI-suggest panel's category field — confirm each shows the full path as a typing suggestion. Select a full-path suggestion in each, submit, and confirm (via the Categories tab or the transaction's stored value) that only the bare leaf name was actually persisted. Type a brand-new bare name (no path) into each and confirm it still works exactly as before (phase 1 behavior unchanged for genuinely new categories).

- [ ] **Step 8: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: show full category paths in datalist suggestions, resolve to leaf on submit"
```

---

### Task 9: Documentation and rebuild

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section — document the category tree, sign validation, and the new endpoints)
- Modify: `PROJECT_STATUS.md` (changelog entry)

**Interfaces:**
- Consumes: nothing — this task only touches documentation.
- Produces: nothing consumed by later tasks (this is the last task).

- [ ] **Step 1: Update `CLAUDE.md`**

Find the Spending Tracking section (`grep -n "Spending Tracking" CLAUDE.md`) and append a paragraph documenting: categories now form a tree rooted at fixed "Income"/"Spend" nodes (`spending_categories.parent_id`/`is_root`); `spending_transactions.category`/`spending_rules.category` still store bare leaf names (globally unique, unchanged); a category's root must match its transactions' amount sign — direct edits reject a mismatch (400), automated rule application silently falls back to `uncategorized`; new endpoints `GET /api/v1/spending/categories/tree` and `PUT /api/v1/spending/categories/{name}/parent`; `POST /api/v1/spending/categories` now requires `parent_name`; the category chart rolls up to top-level Spend groups.

- [ ] **Step 2: Update `PROJECT_STATUS.md`**

Add a new changelog entry above the most recent one (check the current top entry's version number with `head -30 PROJECT_STATUS.md` and increment appropriately), describing: hierarchical spending categories (Income/Spend tree), sign-validated category assignment, category reparenting, chart rollup, and the tree view on the Categories tab.

- [ ] **Step 3: Run the full test suite one more time**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -20 && node --test web_client/js/tests/web_client.test.mjs 2>&1 | tail -20`
Expected: PASS, both suites, no regressions.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document hierarchical spending categories"
```
