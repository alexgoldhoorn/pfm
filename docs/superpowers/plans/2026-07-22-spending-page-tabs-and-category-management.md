# Spending Page Tabs, Pagination, and Category Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Spending page into three tabs (Transactions / Categories / Rules), make the Transactions tab server-paginated and server-sorted instead of fetching+rendering the full history at once, remove the per-row inline category editor (bulk-select + AI-suggest remain the only transaction-level recategorization paths), and add a category-management surface on the Categories tab (rename, cascading everywhere; add a bare unused category) backed by a new lightweight `spending_categories` name registry.

**Architecture:** One new db table (`spending_categories`, v27) plus 4 new `Database` methods. Three new/changed backend endpoints (`GET/POST /categories`, `PUT /categories/{old_name}`) and one changed endpoint (`GET /` gains pagination/sort, response shape changes to `{items, total}`). Frontend: one HTML restructure (Bootstrap tabs, same pattern as the existing Import/Export page), a dedicated (non-shared) pagination/sort implementation for the Transactions table (the shared `makeSortableTable` component used by other pages is not touched), a Chart.js chart replacing the hand-rolled category bars, and an edit-in-place category list mirroring the existing Rules list's pattern.

**Tech Stack:** Python 3.13 / FastAPI (backend), vanilla JS / Bootstrap 5 + Chart.js (frontend, no build step), pytest, Node's built-in `node --test` for JS.

## Global Constraints

- Code style: black (line length 88); comments on the line before the code they describe; type hints on all function signatures; Google-style docstrings.
- Never string-interpolate a client-supplied value into raw SQL — `sort_by`/`sort_dir` must go through a whitelist dict mapping to literal column/direction strings, never the request value directly.
- `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` must pass after every backend task.
- `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501` must report 0 warnings.
- `node --test web_client/js/tests/` (or `make test-js`) must pass after every frontend task.
- The shared `makeSortableTable`/`applyTableState` component (`web_client/js/pfm_core.js`) is used by other pages (Assets, Transactions, etc.) and must not be modified by this plan — the Transactions tab's pagination is a dedicated, Spending-only implementation.
- Both `PROJECT_STATUS.md` and `CLAUDE.md` must be updated (mandatory project convention).
- Web client changes require rebuild + redeploy: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`. Backend changes: `docker exec portf_backend_dev kill -HUP 1`.

---

## Task 1: DB layer — `spending_categories` table + CRUD/rename methods

**Files:**
- Modify: `portf_manager/database.py` (bump `DATABASE_VERSION`, add `_migrate_to_v27`, register it, add 4 new methods)
- Test: `tests/test_database.py` (bump 4 version assertions, add new tests for the 4 methods)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `Database.list_spending_categories() -> List[str]`,
  `Database.create_spending_category(name: str) -> int`,
  `Database.find_spending_category_by_name(name: str) -> Optional[Dict]`,
  `Database.rename_spending_category(old_name: str, new_name: str) -> Dict[str, int]`
  (keys `transactions_updated`, `rules_updated`). Consumed by Task 2's endpoints.

- [ ] **Step 1: Write the failing tests**

In `tests/test_database.py`, bump all 4 existing version assertions from
`26` to `27`:
- Line ~53: `assert result[0] == 26  # Current schema version` → `assert result[0] == 27  # Current schema version`
- Line ~1001, ~1031, ~1102 (three occurrences of the identical pattern): `assert version == 26` → `assert version == 27`

Then add, near the end of the file:

```python
class TestSpendingCategories:
    """v27 — spending_categories registry + CRUD/rename."""

    def setup_method(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.db = Database(self.db_path)

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_list_spending_categories_unions_transactions_rules_and_registry(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        self.db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0, category="Groceries")
        self.db.create_spending_rule(pattern="NETFLIX", category="Subscriptions")
        self.db.create_spending_category("Vacation")

        cats = self.db.list_spending_categories()
        assert "Groceries" in cats
        assert "Subscriptions" in cats
        assert "Vacation" in cats
        assert len(cats) == len(set(cats))  # deduplicated

    def test_create_spending_category_returns_new_id(self):
        cat_id = self.db.create_spending_category("Vacation")
        assert isinstance(cat_id, int)
        assert cat_id > 0

    def test_find_spending_category_by_name(self):
        self.db.create_spending_category("Vacation")
        found = self.db.find_spending_category_by_name("Vacation")
        assert found is not None
        assert found["name"] == "Vacation"
        assert self.db.find_spending_category_by_name("Nonexistent") is None

    def test_rename_spending_category_updates_transactions_and_rules(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        tx_id = self.db.create_spending_transaction(
            pid, "2026-01-05", "Desc", -10.0, category="Groceries"
        )
        rule_id = self.db.create_spending_rule(pattern="MERCADONA", category="Groceries")

        result = self.db.rename_spending_category("Groceries", "Food")
        assert result == {"transactions_updated": 1, "rules_updated": 1}

        assert self.db.get_spending_transaction(tx_id)["category"] == "Food"
        assert self.db.get_spending_rule(rule_id)["category"] == "Food"

    def test_rename_spending_category_registers_previously_unregistered_name(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        self.db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0, category="Groceries")

        self.db.rename_spending_category("Groceries", "Food")

        assert self.db.find_spending_category_by_name("Food") is not None
        assert self.db.find_spending_category_by_name("Groceries") is None

    def test_rename_spending_category_renames_existing_registry_row(self):
        self.db.create_spending_category("Groceries")

        self.db.rename_spending_category("Groceries", "Food")

        assert self.db.find_spending_category_by_name("Food") is not None
        assert self.db.find_spending_category_by_name("Groceries") is None

    def test_rename_spending_category_merges_into_existing_name_without_error(self):
        pid = self.db.create_portfolio("Bank", account_type="bank")
        tx_id = self.db.create_spending_transaction(
            pid, "2026-01-05", "Desc", -10.0, category="Groceries"
        )
        self.db.create_spending_category("Food")  # target already registered

        result = self.db.rename_spending_category("Groceries", "Food")
        assert result == {"transactions_updated": 1, "rules_updated": 0}

        assert self.db.get_spending_transaction(tx_id)["category"] == "Food"
        # Merge case: old_name's registry row (none here) is a no-op delete;
        # new_name's existing registry row is untouched, not duplicated.
        cats = self.db.list_spending_categories()
        assert cats.count("Food") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/test_database.py -k "version or SpendingCategories" -v`
Expected: the 4 version-bump tests FAIL (schema is still v26); all `TestSpendingCategories` tests FAIL with `AttributeError` (methods don't exist yet).

- [ ] **Step 3: Bump `DATABASE_VERSION` and add the migration**

In `portf_manager/database.py`, find:

```python
DATABASE_VERSION = 26
```

Replace with:

```python
DATABASE_VERSION = 27
```

Find (the migration-chain block, ends with the `_migrate_to_v26` call):

```python
        if current_version < 26:
            self._migrate_to_v26(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

Replace with:

```python
        if current_version < 26:
            self._migrate_to_v26(conn)
        if current_version < 27:
            self._migrate_to_v27(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

Find the end of `_migrate_to_v26` (immediately after its `conn.commit()`,
before the `# ── App settings` section comment):

```python
        _add_column_if_missing(conn, "spending_transactions", "balance", "REAL")
        conn.commit()

    # ── App settings (persistent key/value) ────────────────────────────────
```

Replace with:

```python
        _add_column_if_missing(conn, "spending_transactions", "balance", "REAL")
        conn.commit()

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

    # ── App settings (persistent key/value) ────────────────────────────────
```

- [ ] **Step 4: Add the 4 new `Database` methods**

Immediately after `create_spending_rule` (it ends with `return
cursor.lastrowid`) and before `find_duplicate_spending_rule`, add:

```python
    def list_spending_categories(self) -> List[str]:
        """List every known spending category — used on a transaction or
        rule, or explicitly registered — deduplicated and sorted."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT category AS name FROM spending_transactions
                UNION
                SELECT category AS name FROM spending_rules
                UNION
                SELECT name FROM spending_categories
                ORDER BY name
                """
            )
            return [row["name"] for row in cursor.fetchall()]

    def create_spending_category(self, name: str) -> int:
        """Register a new, initially-unused spending category."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO spending_categories (name) VALUES (?)", (name,)
            )
            conn.commit()
            return cursor.lastrowid

    def find_spending_category_by_name(self, name: str) -> Optional[Dict]:
        """Find a registered category by exact name match."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_categories WHERE name = ?", (name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def rename_spending_category(self, old_name: str, new_name: str) -> Dict[str, int]:
        """Rename a category everywhere it's used (transactions, rules, registry).

        Three mutually exclusive registry-upsert cases, checked in order:
        (1) new_name is already registered (merge case — consolidating a
        near-duplicate) — delete old_name's registry row if present, leave
        new_name's row as-is; (2) old_name is registered — rename its row;
        (3) neither is registered (purely usage-derived) — insert new_name.
        """
        with self.get_connection() as conn:
            c1 = conn.execute(
                "UPDATE spending_transactions SET category = ? WHERE category = ?",
                (new_name, old_name),
            )
            c2 = conn.execute(
                "UPDATE spending_rules SET category = ? WHERE category = ?",
                (new_name, old_name),
            )
            if self.find_spending_category_by_name(new_name):
                conn.execute(
                    "DELETE FROM spending_categories WHERE name = ?", (old_name,)
                )
            else:
                updated = conn.execute(
                    "UPDATE spending_categories SET name = ? WHERE name = ?",
                    (new_name, old_name),
                )
                if updated.rowcount == 0:
                    conn.execute(
                        "INSERT INTO spending_categories (name) VALUES (?)",
                        (new_name,),
                    )
            conn.commit()
            return {
                "transactions_updated": c1.rowcount,
                "rules_updated": c2.rowcount,
            }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/test_database.py -k "version or SpendingCategories" -v`
Expected: all pass.

- [ ] **Step 6: Run the full test suite and lint**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions (in particular every other test that asserted `database_version` — grep confirmed only the 4 bumped in Step 1 reference it).

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings (run `uv run black portf_manager/database.py` first if needed).

- [ ] **Step 7: Commit**

```bash
git add portf_manager/database.py tests/test_database.py
git commit -m "feat: add spending_categories registry table + CRUD/rename methods

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Backend — `/categories` endpoints

**Files:**
- Modify: `portf_server/routers/spending.py` (2 new models, 3 new endpoints)
- Test: `tests/unit/test_spending_api.py` (new tests)

**Interfaces:**
- Consumes: `Database.list_spending_categories`, `create_spending_category`,
  `find_spending_category_by_name`, `rename_spending_category` from Task 1.
- Produces: `GET /api/v1/spending/categories` → `List[str]`;
  `POST /api/v1/spending/categories` `{"name": str}` → `{"id": int, "name": str}`
  (201; 400 blank; 409 exact duplicate);
  `PUT /api/v1/spending/categories/{old_name}` `{"new_name": str}` →
  `{"old_name": str, "new_name": str, "transactions_updated": int, "rules_updated": int}`
  (400 blank or same-as-current). Consumed by Task 4's frontend API client methods.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_spending_api.py`, add near the end of the file:

```python
def test_list_categories_includes_used_and_registered(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0, category="Groceries")
    db.create_spending_category("Vacation")

    r = client.get("/api/v1/spending/categories", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "Groceries" in body
    assert "Vacation" in body


def test_create_category(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS
    )
    assert r.status_code == 201
    assert r.json()["name"] == "Vacation"

    listed = client.get("/api/v1/spending/categories", headers=HEADERS).json()
    assert "Vacation" in listed


def test_create_category_rejects_blank_name(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post("/api/v1/spending/categories", json={"name": "   "}, headers=HEADERS)
    assert r.status_code == 400


def test_create_category_rejects_exact_duplicate(tmp_path):
    client, _ = _make_client(tmp_path)
    client.post("/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS)
    r = client.post("/api/v1/spending/categories", json={"name": "Vacation"}, headers=HEADERS)
    assert r.status_code == 409


def test_rename_category_cascades_to_transactions_and_rules(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "Desc", -10.0, category="Groceries"
    )
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["transactions_updated"] == 1
    assert body["rules_updated"] == 1

    assert db.get_spending_transaction(tx_id)["category"] == "Food"


def test_rename_category_rejects_blank_new_name(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_spending_category("Groceries")
    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "   "},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_rename_category_rejects_same_name(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_spending_category("Groceries")
    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_rename_category_merges_into_existing_name(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0, category="Groceries")
    db.create_spending_category("Food")

    r = client.put(
        "/api/v1/spending/categories/Groceries",
        json={"new_name": "Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k category -v`
Expected: all FAIL with 404 (routes don't exist yet).

- [ ] **Step 3: Add the request models**

In `portf_server/routers/spending.py`, find `SpendingRuleUpdateBody` (it
ends with `category: Optional[str] = None`, immediately before
`class SpendingSummaryResponse`):

```python
class SpendingRuleUpdateBody(BaseModel):
    pattern: Optional[str] = None
    category: Optional[str] = None


class SpendingSummaryResponse(BaseModel):
```

Replace with:

```python
class SpendingRuleUpdateBody(BaseModel):
    pattern: Optional[str] = None
    category: Optional[str] = None


class SpendingCategoryBody(BaseModel):
    name: str


class SpendingCategoryRenameBody(BaseModel):
    new_name: str


class SpendingSummaryResponse(BaseModel):
```

- [ ] **Step 4: Add the endpoints**

In `portf_server/routers/spending.py`, find `delete_rule` (it ends with
`return {"deleted": True, "id": rule_id}`) followed by
`@router.get("/summary", ...)`:

```python
    return {"deleted": True, "id": rule_id}


@router.get("/summary", response_model=SpendingSummaryResponse)
```

Replace with:

```python
    return {"deleted": True, "id": rule_id}


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


@router.get("/summary", response_model=SpendingSummaryResponse)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k category -v`
Expected: all pass.

- [ ] **Step 6: Run the full spending test file, lint, and full suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v`
Expected: all pass.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add GET/POST/PUT /api/v1/spending/categories endpoints

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Backend — pagination/sort for `GET /api/v1/spending/`

**Files:**
- Modify: `portf_manager/database.py` (`list_spending_transactions` gains kwargs, new `count_spending_transactions`)
- Modify: `portf_server/routers/spending.py` (new response model, endpoint gains query params)
- Test: `tests/unit/test_spending_api.py` (new tests)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Database.list_spending_transactions(..., limit=None, offset=None, sort_by=None, sort_dir=None)`
  (unchanged default behavior when these are omitted — the 3 other
  existing callers in `spending.py` are unaffected);
  `Database.count_spending_transactions(portfolio_id=None, category=None, start_date=None, end_date=None, is_transfer=None) -> int`;
  `GET /api/v1/spending/?limit=&offset=&sort_by=&sort_dir=&...` →
  `{"items": [...], "total": int}` (was a bare array — this is the
  endpoint's only consumer, updated in Task 6). Consumed by Task 6's
  frontend pagination rewrite.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_spending_api.py`, add near the end of the file:

```python
def test_list_spending_pagination_shape_and_total(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    for i in range(5):
        db.create_spending_transaction(pid, f"2026-01-{i + 1:02d}", f"Desc {i}", -10.0)

    r = client.get("/api/v1/spending/?limit=2&offset=0", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_list_spending_pagination_offset_advances(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    for i in range(5):
        db.create_spending_transaction(pid, f"2026-01-{i + 1:02d}", f"Desc {i}", -10.0)

    page1 = client.get("/api/v1/spending/?limit=2&offset=0&sort_by=date&sort_dir=asc", headers=HEADERS).json()
    page2 = client.get("/api/v1/spending/?limit=2&offset=2&sort_by=date&sort_dir=asc", headers=HEADERS).json()
    ids1 = {r["id"] for r in page1["items"]}
    ids2 = {r["id"] for r in page2["items"]}
    assert ids1.isdisjoint(ids2)


def test_list_spending_sort_by_amount_asc(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-01", "Big", -100.0)
    db.create_spending_transaction(pid, "2026-01-02", "Small", -5.0)

    r = client.get(
        "/api/v1/spending/?limit=10&offset=0&sort_by=amount&sort_dir=asc", headers=HEADERS
    )
    items = r.json()["items"]
    assert [i["description"] for i in items] == ["Big", "Small"]


def test_list_spending_total_respects_filters(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-01", "A", -10.0, category="Groceries")
    db.create_spending_transaction(pid, "2026-01-02", "B", -10.0, category="Dining")

    r = client.get(
        "/api/v1/spending/?limit=10&offset=0&category=Groceries", headers=HEADERS
    )
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


def test_list_spending_invalid_sort_by_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/v1/spending/?sort_by=not_a_column", headers=HEADERS)
    assert r.status_code == 400


def test_list_spending_invalid_limit_rejected(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/v1/spending/?limit=0", headers=HEADERS)
    assert r.status_code == 422  # FastAPI Query validation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k "pagination or sort_by or list_spending_total" -v`
Expected: shape-based tests fail with `KeyError: 'total'` (response is still a bare list); sort/filter tests fail similarly.

- [ ] **Step 3: Update `list_spending_transactions` and add `count_spending_transactions`**

In `portf_manager/database.py`, find `list_spending_transactions` in
full (starts `def list_spending_transactions(`, ends with `return [dict(row)
for row in cursor.fetchall()]` immediately before `def
get_spending_transaction`):

```python
    def list_spending_transactions(
        self,
        portfolio_id: int = None,
        category: str = None,
        start_date: str = None,
        end_date: str = None,
        is_transfer: bool = None,
    ) -> List[Dict]:
        """List spending transactions with optional filters, newest first."""
        with self.get_connection() as conn:
            query = """
                SELECT s.*, p.name AS portfolio_name
                FROM spending_transactions s
                LEFT JOIN portfolios p ON s.portfolio_id = p.id
            """
            conditions = []
            params: List = []
            if portfolio_id is not None:
                conditions.append("s.portfolio_id = ?")
                params.append(portfolio_id)
            if category is not None:
                conditions.append("s.category = ?")
                params.append(category)
            if start_date is not None:
                conditions.append("s.date >= ?")
                params.append(start_date)
            if end_date is not None:
                conditions.append("s.date <= ?")
                params.append(end_date)
            if is_transfer is not None:
                conditions.append("s.is_transfer = ?")
                params.append(1 if is_transfer else 0)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY s.date DESC, s.id DESC"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
```

Replace with:

```python
    _SPENDING_SORT_COLUMNS = {
        "date": "s.date",
        "portfolio_name": "p.name",
        "description": "s.description",
        "category": "s.category",
        "amount": "s.amount",
    }
    _SPENDING_SORT_DIRS = {"asc": "ASC", "desc": "DESC"}

    def _spending_where_clause(
        self,
        portfolio_id: int = None,
        category: str = None,
        start_date: str = None,
        end_date: str = None,
        is_transfer: bool = None,
    ):
        """Shared WHERE-clause builder for list/count spending transactions."""
        conditions = []
        params: List = []
        if portfolio_id is not None:
            conditions.append("s.portfolio_id = ?")
            params.append(portfolio_id)
        if category is not None:
            conditions.append("s.category = ?")
            params.append(category)
        if start_date is not None:
            conditions.append("s.date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("s.date <= ?")
            params.append(end_date)
        if is_transfer is not None:
            conditions.append("s.is_transfer = ?")
            params.append(1 if is_transfer else 0)
        clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        return clause, params

    def list_spending_transactions(
        self,
        portfolio_id: int = None,
        category: str = None,
        start_date: str = None,
        end_date: str = None,
        is_transfer: bool = None,
        limit: int = None,
        offset: int = None,
        sort_by: str = None,
        sort_dir: str = None,
    ) -> List[Dict]:
        """List spending transactions with optional filters.

        Newest-first (`ORDER BY s.date DESC, s.id DESC`) unless `sort_by`
        is given, in which case that fixed ordering is replaced by
        `sort_by`/`sort_dir` (both mapped through a whitelist to real SQL
        column/direction tokens — never interpolated from the caller
        directly). `limit`/`offset` are appended as bound params when
        `limit` is given; omitted entirely (unbounded, today's behavior)
        when it isn't.
        """
        with self.get_connection() as conn:
            where_clause, params = self._spending_where_clause(
                portfolio_id, category, start_date, end_date, is_transfer
            )
            query = (
                "SELECT s.*, p.name AS portfolio_name FROM spending_transactions s "
                "LEFT JOIN portfolios p ON s.portfolio_id = p.id" + where_clause
            )
            if sort_by is not None:
                column = self._SPENDING_SORT_COLUMNS[sort_by]
                direction = self._SPENDING_SORT_DIRS[sort_dir or "desc"]
                query += f" ORDER BY {column} {direction}"
            else:
                query += " ORDER BY s.date DESC, s.id DESC"
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params = params + [limit, offset or 0]
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_spending_transactions(
        self,
        portfolio_id: int = None,
        category: str = None,
        start_date: str = None,
        end_date: str = None,
        is_transfer: bool = None,
    ) -> int:
        """Count spending transactions matching the same filters as
        list_spending_transactions (no JOIN needed — sort/portfolio_name
        are irrelevant to a count)."""
        with self.get_connection() as conn:
            where_clause, params = self._spending_where_clause(
                portfolio_id, category, start_date, end_date, is_transfer
            )
            query = "SELECT COUNT(*) FROM spending_transactions s" + where_clause
            cursor = conn.execute(query, params)
            return cursor.fetchone()[0]
```

- [ ] **Step 4: Update the endpoint**

In `portf_server/routers/spending.py`, find `SpendingTransactionResponse`
(ends with `balance: Optional[float] = None`, immediately before
`class CategoryUpdateBody`):

```python
class SpendingTransactionResponse(BaseModel):
    id: int
    portfolio_id: int
    portfolio_name: Optional[str] = None
    date: str
    description: str
    amount: float
    currency: str
    category: str
    is_transfer: bool
    transfer_link_type: Optional[str] = None
    transfer_link_id: Optional[int] = None
    source: Optional[str] = None
    balance: Optional[float] = None


class CategoryUpdateBody(BaseModel):
```

Replace with:

```python
class SpendingTransactionResponse(BaseModel):
    id: int
    portfolio_id: int
    portfolio_name: Optional[str] = None
    date: str
    description: str
    amount: float
    currency: str
    category: str
    is_transfer: bool
    transfer_link_type: Optional[str] = None
    transfer_link_id: Optional[int] = None
    source: Optional[str] = None
    balance: Optional[float] = None


class SpendingTransactionListResponse(BaseModel):
    items: List[SpendingTransactionResponse]
    total: int


class CategoryUpdateBody(BaseModel):
```

Find `list_spending` in full (starts `@router.get("/",
response_model=List[SpendingTransactionResponse])`, ends with the
closing `]` of its return statement, immediately before `@router.put("/{spending_id}"...)`):

```python
@router.get("/", response_model=List[SpendingTransactionResponse])
async def list_spending(
    portfolio_id: Optional[int] = None,
    category: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_transfer: Optional[bool] = None,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """List spending transactions with optional filters."""
    rows = db.list_spending_transactions(
        portfolio_id=portfolio_id,
        category=category,
        start_date=start_date,
        end_date=end_date,
        is_transfer=is_transfer,
    )
    return [
        SpendingTransactionResponse(**{**r, "is_transfer": bool(r["is_transfer"])})
        for r in rows
    ]
```

Replace with:

```python
_SPENDING_SORT_BY_VALUES = {"date", "portfolio_name", "description", "category", "amount"}
_SPENDING_SORT_DIR_VALUES = {"asc", "desc"}


@router.get("/", response_model=SpendingTransactionListResponse)
async def list_spending(
    portfolio_id: Optional[int] = None,
    category: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_transfer: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = "date",
    sort_dir: str = "desc",
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """List spending transactions with optional filters, paginated and sorted."""
    if sort_by not in _SPENDING_SORT_BY_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"sort_by must be one of {sorted(_SPENDING_SORT_BY_VALUES)}",
        )
    if sort_dir not in _SPENDING_SORT_DIR_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"sort_dir must be one of {sorted(_SPENDING_SORT_DIR_VALUES)}",
        )
    filters = dict(
        portfolio_id=portfolio_id,
        category=category,
        start_date=start_date,
        end_date=end_date,
        is_transfer=is_transfer,
    )
    rows = db.list_spending_transactions(
        limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir, **filters
    )
    total = db.count_spending_transactions(**filters)
    return SpendingTransactionListResponse(
        items=[
            SpendingTransactionResponse(**{**r, "is_transfer": bool(r["is_transfer"])})
            for r in rows
        ],
        total=total,
    )
```

Then check the top of the file for the `fastapi` import block (it
currently imports `APIRouter, Depends, File, Form, HTTPException,
Request, UploadFile, status` — no `Query`). Find:

```python
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
```

Replace with:

```python
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k "pagination or sort_by or list_spending_total or list_spending_invalid" -v`
Expected: all pass.

- [ ] **Step 6: Update existing tests for the new response shape**

`GET /api/v1/spending/` now returns `{"items": [...], "total": N}`
instead of a bare array (Step 4) — every pre-existing test in this file
that calls it and indexes/asserts on the result as a bare list now
breaks. This is expected breakage from the intentional response-shape
change in this task, not a regression to avoid.

In `tests/unit/test_spending_api.py`, replace every occurrence of the
exact literal substring:

```python
client.get("/api/v1/spending/", headers=HEADERS).json()
```

with:

```python
client.get("/api/v1/spending/", headers=HEADERS).json()["items"]
```

This exact substring occurs 14 times in the file (confirmed via `grep
-c 'client\.get("/api/v1/spending/", headers=HEADERS)\.json()'
tests/unit/test_spending_api.py` — verify the count still matches
before proceeding, since other tasks don't touch this file but confirm
regardless), each embedded in a larger expression (e.g.
`client.get(...).json()[0]["category"]`,
`len(client.get(...).json())`, `{r["id"]: r for r in
client.get(...).json()}`) — a plain substring replace is correct in
every case since `["items"]` composes correctly with whatever indexing/
iteration follows it in each expression. Use a global find-and-replace
across the file for this one substring rather than 14 separate edits.

- [ ] **Step 7: Run the full spending test file, lint, and full suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v`
Expected: all pass, including every test touched by Step 6.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings.

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add portf_manager/database.py portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add pagination and sort to GET /api/v1/spending/

Response shape changes from a bare array to {items, total} -- this
endpoint's only consumer is the Spending page's own JS (updated in a
later task), so the shape change is safe.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Frontend — new API client methods

**Files:**
- Modify: `web_client/js/pfm_core.js` (3 new methods)

**Interfaces:**
- Consumes: Task 2's `/categories` endpoints.
- Produces: `apiClient.getSpendingCategories() -> Promise<string[]>`,
  `apiClient.createSpendingCategory(name: string) -> Promise<object>`,
  `apiClient.renameSpendingCategory(oldName: string, newName: string) -> Promise<object>`.
  Consumed by Tasks 8 and 10.
  (`getSpendingTransactions(params)` needs no change — it already
  accepts an arbitrary `params` object and returns `response.json()`
  as-is, so the new `limit`/`offset`/`sort_by`/`sort_dir` params and the
  `{items, total}` response shape both flow through it unchanged.)

- [ ] **Step 1: Add the 3 methods**

In `web_client/js/pfm_core.js`, find `getSpendingRules` (it ends with
`return response.json();` followed by `},`, immediately before
`async createSpendingRule`):

```javascript
        async getSpendingRules() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to load rules';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async createSpendingRule(pattern, category) {
```

Replace with:

```javascript
        async getSpendingRules() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) {
                let detail = 'Failed to load rules';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async getSpendingCategories() {
            const response = await fetch(this.baseURL + '/api/v1/spending/categories', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load categories');
            return response.json();
        },
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
        async renameSpendingCategory(oldName, newName) {
            const response = await fetch(
                this.baseURL + '/api/v1/spending/categories/' + encodeURIComponent(oldName),
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                    body: JSON.stringify({ new_name: newName })
                }
            );
            if (!response.ok) {
                let detail = 'Failed to rename category';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        async createSpendingRule(pattern, category) {
```

- [ ] **Step 2: Verify syntax**

Run: `node --check web_client/js/pfm_core.js`
Expected: prints nothing.

- [ ] **Step 3: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass (no new tests needed — matches this file's
existing precedent of not unit-testing plain `fetch` wrapper methods).

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add category CRUD API client methods

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Frontend — HTML: tab restructure + new markup

**Files:**
- Modify: `web_client/index.html`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: new DOM element ids consumed by Tasks 6-10:
  `#spTabs`, `#spTabBtnTransactions`/`#spTabBtnCategories`/`#spTabBtnRules`,
  `#spPaneTransactions`/`#spPaneCategories`/`#spPaneRules`,
  `#spTxPagination`, `#spTxPrevPage`, `#spTxNextPage`, `#spTxPageInfo`,
  `#spTxPageSize`, `#spCategoryChartCanvas`, `#spCategoryChartShowAll`,
  `#spCategoriesList`, `#spCategoryAddForm`, `#spCategoryNameInput`,
  `#spCategoryAddStatus`. All existing ids (`spTxBody`, `spRulesBody`,
  `spCategoryChart` container div — repurposed, see below — etc.) are
  preserved so later tasks' JS keeps working against stable hooks.

- [ ] **Step 1: Add the Chart.js script tag**

In `web_client/index.html`, find (the existing Chart.js include, used
by the Analytics page):

```html
    <!-- Chart.js (UMD build required for non-module scripts) -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

No change needed here — it's already loaded globally, available to the
Spending page's chart too. (This step is a no-op verification, not an
edit — confirms the dependency is already present before Task 9 uses
`new Chart(...)`.)

- [ ] **Step 2: Restructure the Spending page body into tabs**

Find the full block from the "Spending by category" card through the
end of the "Category rules" card (currently
`web_client/index.html` lines ~2575-2639 — verify against current file
content, since Tasks 1-4 didn't touch this file):

```html
                    <div class="card mb-3">
                        <div class="card-header fw-semibold">Spending by category</div>
                        <div class="card-body">
                            <div id="spCategoryChart"><div class="text-muted small">Loading…</div></div>
                        </div>
                    </div>

                    <div class="card mb-3">
                        <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                            <span>Transactions</span>
                            <button class="btn btn-sm btn-outline-secondary" id="spSelectAllUncategorized" title="Filter to uncategorized rows and select them all"><i class="bi bi-check2-square me-1"></i>Select all uncategorized</button>
                        </div>
                        <div id="spBulkBar" class="card-body py-2 border-bottom bg-light-subtle" style="display:none;">
                            <div class="d-flex flex-wrap align-items-center gap-2">
                                <span class="small text-muted"><span id="spSelectedCount">0</span> selected</span>
                                <input type="text" list="spCategoryList" class="form-control form-control-sm w-auto" id="spBulkCategorySelect" placeholder="Category">
                                <button class="btn btn-sm btn-outline-primary" id="spBulkRecategorizeBtn">Set category</button>
                                <button class="btn btn-sm btn-outline-info" id="spBulkSuggestBtn"><i class="bi bi-magic me-1"></i>Suggest categories (AI)</button>
                                <button class="btn btn-sm btn-outline-secondary" id="spBulkApplyRulesBtn" title="Re-apply category rules to selected rows that are still uncategorized"><i class="bi bi-tags me-1"></i>Apply rules to selected</button>
                                <button class="btn btn-sm btn-outline-danger ms-auto" id="spBulkDeleteBtn"><i class="bi bi-trash me-1"></i>Delete selected</button>
                            </div>
                        </div>
                        <div id="spBulkStatus" class="small text-muted px-3 pt-2"></div>
                        <div id="spSuggestReviewPanel" class="px-3 pb-2" style="display:none;"></div>
                        <datalist id="spCategoryList"></datalist>
                        <div class="table-responsive">
                            <table class="table table-hover mb-0">
                                <thead><tr>
                                    <th class="ps-3" style="width:2.5rem;"><input type="checkbox" class="form-check-input" id="spSelectAll"></th>
                                    <th data-key="date" data-type="date">Date</th>
                                    <th data-key="portfolio_name" data-type="text">Account</th>
                                    <th data-key="description" data-type="text">Description</th>
                                    <th data-key="category" data-type="text">Category</th>
                                    <th class="text-end" data-key="amount" data-type="num">Amount</th>
                                    <th class="pe-3"></th>
                                </tr></thead>
                                <tbody id="spTxBody"><tr><td colspan="7" class="text-center text-muted py-3">No transactions yet. Import a bank statement to get started.</td></tr></tbody>
                            </table>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-header fw-semibold">Category rules</div>
                        <div class="table-responsive">
                            <table class="table table-sm mb-0">
                                <thead><tr><th class="ps-3">Pattern</th><th>Category</th><th class="pe-3"></th></tr></thead>
                                <tbody id="spRulesBody"><tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr></tbody>
                            </table>
                        </div>
                        <div class="card-body border-top">
                            <form id="spRuleAddForm" class="row g-2 align-items-end">
                                <div class="col-6 col-sm-5">
                                    <label class="form-label small mb-1">Pattern (matches description)</label>
                                    <input class="form-control form-control-sm" id="spRulePattern" placeholder="e.g. MERCADONA" required>
                                </div>
                                <div class="col-6 col-sm-5">
                                    <label class="form-label small mb-1">Category</label>
                                    <input class="form-control form-control-sm" id="spRuleCategory" placeholder="e.g. Groceries" required>
                                </div>
                                <div class="col-12 col-sm-2">
                                    <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                </div>
                            </form>
                            <div id="spRuleStatus" class="small text-muted mt-2"></div>
                        </div>
                    </div>
                </div>
```

Replace with:

```html
                    <ul class="nav nav-tabs mb-3" id="spTabs">
                        <li class="nav-item">
                            <button type="button" class="nav-link active" data-bs-toggle="tab" data-bs-target="#spPaneTransactions" id="spTabBtnTransactions"><i class="bi bi-list-ul me-1"></i>Transactions</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#spPaneCategories" id="spTabBtnCategories"><i class="bi bi-tags me-1"></i>Categories</button>
                        </li>
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#spPaneRules" id="spTabBtnRules"><i class="bi bi-signpost-split me-1"></i>Rules</button>
                        </li>
                    </ul>
                    <div class="tab-content">
                        <div class="tab-pane fade show active" id="spPaneTransactions">
                            <div class="card">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span>Transactions</span>
                                    <button class="btn btn-sm btn-outline-secondary" id="spSelectAllUncategorized" title="Filter to uncategorized rows and select them all"><i class="bi bi-check2-square me-1"></i>Select all uncategorized</button>
                                </div>
                                <div id="spBulkBar" class="card-body py-2 border-bottom bg-light-subtle" style="display:none;">
                                    <div class="d-flex flex-wrap align-items-center gap-2">
                                        <span class="small text-muted"><span id="spSelectedCount">0</span> selected</span>
                                        <input type="text" list="spCategoryList" class="form-control form-control-sm w-auto" id="spBulkCategorySelect" placeholder="Category">
                                        <button class="btn btn-sm btn-outline-primary" id="spBulkRecategorizeBtn">Set category</button>
                                        <button class="btn btn-sm btn-outline-info" id="spBulkSuggestBtn"><i class="bi bi-magic me-1"></i>Suggest categories (AI)</button>
                                        <button class="btn btn-sm btn-outline-secondary" id="spBulkApplyRulesBtn" title="Re-apply category rules to selected rows that are still uncategorized"><i class="bi bi-tags me-1"></i>Apply rules to selected</button>
                                        <button class="btn btn-sm btn-outline-danger ms-auto" id="spBulkDeleteBtn"><i class="bi bi-trash me-1"></i>Delete selected</button>
                                    </div>
                                </div>
                                <div id="spBulkStatus" class="small text-muted px-3 pt-2"></div>
                                <div id="spSuggestReviewPanel" class="px-3 pb-2" style="display:none;"></div>
                                <datalist id="spCategoryList"></datalist>
                                <div class="table-responsive">
                                    <table class="table table-hover mb-0">
                                        <thead><tr>
                                            <th class="ps-3" style="width:2.5rem;"><input type="checkbox" class="form-check-input" id="spSelectAll"></th>
                                            <th data-key="date">Date</th>
                                            <th data-key="portfolio_name">Account</th>
                                            <th data-key="description">Description</th>
                                            <th data-key="category">Category</th>
                                            <th class="text-end" data-key="amount">Amount</th>
                                            <th class="pe-3"></th>
                                        </tr></thead>
                                        <tbody id="spTxBody"><tr><td colspan="7" class="text-center text-muted py-3">No transactions yet. Import a bank statement to get started.</td></tr></tbody>
                                    </table>
                                </div>
                                <div id="spTxPagination" class="card-body py-2 border-top d-flex align-items-center gap-2">
                                    <button class="btn btn-sm btn-outline-secondary" id="spTxPrevPage">Previous</button>
                                    <span class="small text-muted" id="spTxPageInfo">Page 1</span>
                                    <button class="btn btn-sm btn-outline-secondary" id="spTxNextPage">Next</button>
                                    <select class="form-select form-select-sm w-auto ms-auto" id="spTxPageSize">
                                        <option value="25">25 / page</option>
                                        <option value="50" selected>50 / page</option>
                                        <option value="100">100 / page</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                        <div class="tab-pane fade" id="spPaneCategories">
                            <div class="card mb-3">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span>Spending by category</span>
                                    <button class="btn btn-sm btn-outline-secondary" id="spCategoryChartShowAll">Show all</button>
                                </div>
                                <div class="card-body">
                                    <canvas id="spCategoryChartCanvas" height="80"></canvas>
                                </div>
                            </div>
                            <div class="card">
                                <div class="card-header fw-semibold">All categories</div>
                                <div id="spCategoriesList" class="list-group list-group-flush"></div>
                                <div class="card-body border-top">
                                    <form id="spCategoryAddForm" class="row g-2 align-items-end">
                                        <div class="col-8 col-sm-9">
                                            <label class="form-label small mb-1">New category name</label>
                                            <input class="form-control form-control-sm" id="spCategoryNameInput" placeholder="e.g. Vacation" required>
                                        </div>
                                        <div class="col-4 col-sm-3">
                                            <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                        </div>
                                    </form>
                                    <div id="spCategoryAddStatus" class="small text-muted mt-2"></div>
                                </div>
                            </div>
                        </div>
                        <div class="tab-pane fade" id="spPaneRules">
                            <div class="card">
                                <div class="card-header fw-semibold">Category rules</div>
                                <div class="table-responsive">
                                    <table class="table table-sm mb-0">
                                        <thead><tr><th class="ps-3">Pattern</th><th>Category</th><th class="pe-3"></th></tr></thead>
                                        <tbody id="spRulesBody"><tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr></tbody>
                                    </table>
                                </div>
                                <div class="card-body border-top">
                                    <form id="spRuleAddForm" class="row g-2 align-items-end">
                                        <div class="col-6 col-sm-5">
                                            <label class="form-label small mb-1">Pattern (matches description)</label>
                                            <input class="form-control form-control-sm" id="spRulePattern" placeholder="e.g. MERCADONA" required>
                                        </div>
                                        <div class="col-6 col-sm-5">
                                            <label class="form-label small mb-1">Category</label>
                                            <input class="form-control form-control-sm" id="spRuleCategory" placeholder="e.g. Groceries" required>
                                        </div>
                                        <div class="col-12 col-sm-2">
                                            <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                        </div>
                                    </form>
                                    <div id="spRuleStatus" class="small text-muted mt-2"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
```

(Note: the `data-type="date"`/`data-type="text"`/`data-type="num"`
attributes on the transactions table's `<th>` elements are dropped —
they were consumed only by the now-removed `makeSortableTable`/
`applyTableState` client-side sort machinery for this specific table;
Task 6 reads `data-key` directly for its own dedicated sort wiring.
`data-key` itself is kept since Task 6 still uses it to identify which
column a header click sorts by.)

- [ ] **Step 3: Verify**

Run: `grep -c '<div class="tab-pane' web_client/index.html` — expect
the count to have grown by exactly 3 versus before this step (the file
already has tab-panes on other pages; this confirms 3 new ones were
added, not that a specific total). Open `web_client/index.html` in a
text editor or `python3 -c "from html.parser import HTMLParser"`-based
sanity check is unnecessary — visually confirm via Task 5's Step 4
manual check instead.

- [ ] **Step 4: Manual check (no backend/frontend logic exists yet for the new elements — this only confirms the markup itself is well-formed)**

Rebuild and reload (`docker compose build web && docker stop portf_web
&& WEB_PORT=8080 docker compose up -d web`), open the Spending page,
confirm: three tabs render and switching between them works (native
Bootstrap, no JS needed yet); the Transactions tab looks the same as
before plus new (currently inert) pagination controls at the bottom;
the Categories tab shows an empty canvas and an empty categories
list/add form; the Rules tab shows the existing rules list/add form
unchanged. Existing Transactions-tab functionality (bulk actions, AI
suggest) should still work exactly as before — only the *pagination*
controls and *Categories tab management UI* are inert placeholders
until Tasks 6-10 wire them up.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html
git commit -m "feat: restructure Spending page into Transactions/Categories/Rules tabs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Frontend — Transactions tab pagination/sort

**Files:**
- Modify: `web_client/js/pfm_features.js` (`_renderSpendingTable` replaced
  by `_fetchAndRenderSpendingTable`, new pagination/sort logic,
  `filterSpendingRows` and `window.updateSpendingRowCategory` deleted)
- Modify: `web_client/js/tests/web_client.test.mjs` (delete
  `filterSpendingRows`'s 4 tests)

**Interfaces:**
- Consumes: `apiClient.getSpendingTransactions` (unchanged signature,
  Task 3's backend changes make it paginated), Task 5's
  `#spTxPrevPage`/`#spTxNextPage`/`#spTxPageInfo`/`#spTxPageSize`
  elements.
- Produces: `window._spTxState` (page/pageSize/sortBy/sortDir),
  `_fetchAndRenderSpendingTable()`. Consumed by `loadSpendingPage`'s
  filter wiring (updated in this task) and Task 7 (`_allSpendingCategories`
  call site inside this function's `renderRows`-equivalent).

This task also makes the transactions table's category column
read-only (Scope C of the spec) in the same pass, since it's the exact
row template this task already rewrites for pagination — doing it as a
separate task would touch the same lines twice for no benefit.
`window.updateSpendingRowCategory` (whose only caller is the input this
task removes) is deleted here too.

- [ ] **Step 1: Delete `filterSpendingRows` and its tests**

In `web_client/js/pfm_features.js`, find:

```javascript
function filterSpendingRows(rows, filters) {
    const { accountId, category, fromDate, toDate } = filters || {};
    return (rows || []).filter(r =>
        (!accountId || String(r.portfolio_id) === String(accountId)) &&
        (!category || r.category === category) &&
        (!fromDate || r.date >= fromDate) &&
        (!toDate || r.date <= toDate)
    );
}
window.filterSpendingRows = filterSpendingRows;

// Groups selected spending rows by description for AI suggestion review —
// one representative per unique description, keeping every matching row's
// id so an accepted suggestion can be applied to all of them at once.
// Cuts LLM cost/latency: a real account can have the same merchant
// description repeated dozens of times.
function dedupSpendingRowsByDescription(rows) {
```

Replace with (deletes `filterSpendingRows` and its `window` export
entirely — filtering moves server-side in this task, this function
becomes dead code):

```javascript
// Groups selected spending rows by description for AI suggestion review —
// one representative per unique description, keeping every matching row's
// id so an accepted suggestion can be applied to all of them at once.
// Cuts LLM cost/latency: a real account can have the same merchant
// description repeated dozens of times.
function dedupSpendingRowsByDescription(rows) {
```

- [ ] **Step 2: Delete `filterSpendingRows`'s 4 tests**

In `web_client/js/tests/web_client.test.mjs`, find (they test a
function that no longer exists after Step 1):

```javascript
test("filterSpendingRows: no filters returns all rows", () => {
    const { filterSpendingRows } = loadAppIntoContext();
    const rows = [
        { portfolio_id: 1, category: "Groceries", date: "2026-01-05" },
        { portfolio_id: 2, category: "Dining", date: "2026-01-06" },
    ];
    assert.equal(filterSpendingRows(rows, {}).length, 2);
});

test("filterSpendingRows: filters by account and category", () => {
    const { filterSpendingRows } = loadAppIntoContext();
    const rows = [
        { portfolio_id: 1, category: "Groceries", date: "2026-01-05" },
        { portfolio_id: 2, category: "Dining", date: "2026-01-06" },
    ];
    const result = filterSpendingRows(rows, { accountId: "1" });
    assert.equal(result.length, 1);
    assert.equal(result[0].category, "Groceries");

    const result2 = filterSpendingRows(rows, { category: "Dining" });
    assert.equal(result2.length, 1);
    assert.equal(result2[0].portfolio_id, 2);
});

test("filterSpendingRows: filters by date range", () => {
    const { filterSpendingRows } = loadAppIntoContext();
    const rows = [
        { portfolio_id: 1, category: "Groceries", date: "2026-01-05" },
        { portfolio_id: 1, category: "Groceries", date: "2026-02-05" },
    ];
    const result = filterSpendingRows(rows, { fromDate: "2026-02-01" });
    assert.equal(result.length, 1);
    assert.equal(result[0].date, "2026-02-05");

    const result2 = filterSpendingRows(rows, { toDate: "2026-01-31" });
    assert.equal(result2.length, 1);
    assert.equal(result2[0].date, "2026-01-05");
});

test("filterSpendingRows: does not mutate input", () => {
    const { filterSpendingRows } = loadAppIntoContext();
    const rows = [{ portfolio_id: 1, category: "Groceries", date: "2026-01-05" }];
    const copy = JSON.parse(JSON.stringify(rows));
    filterSpendingRows(rows, { accountId: "1" });
    assert.deepEqual(rows, copy);
});

test("dedupSpendingRowsByDescription: groups rows sharing a description", () => {
```

Replace with (deletes all 4 tests, leaves the following
`dedupSpendingRowsByDescription` test — a different, unrelated function
— untouched):

```javascript
test("dedupSpendingRowsByDescription: groups rows sharing a description", () => {
```

- [ ] **Step 3: Verify tests fail correctly (RED for the wrong reason is fine here)**

Run: `node --test web_client/js/tests/web_client.test.mjs 2>&1 | grep -i "filterSpendingRows\|fail"`
Expected: no `filterSpendingRows` references remain in the output — the
4 deleted tests no longer run at all (not "failing", just gone). If any
other test fails at this point, stop and investigate before continuing
(this step should be a clean deletion with no side effects yet, since
`_renderSpendingTable` — the only caller — hasn't been rewritten yet).

- [ ] **Step 4: Rewrite the transactions table render/fetch logic**

In `web_client/js/pfm_features.js`, find `_renderSpendingTable` in full
(starts `function _renderSpendingTable() {`, ends with the closing `}`
immediately before `function _allSpendingCategories`):

```javascript
function _renderSpendingTable() {
    const rows = window._spendingAllRows || [];
    const filtered = filterSpendingRows(rows, {
        accountId: document.getElementById('spAccountFilter')?.value,
        category: document.getElementById('spCategoryFilter')?.value,
        fromDate: document.getElementById('spFromDate')?.value,
        toDate: document.getElementById('spToDate')?.value,
    });

    const catSel = document.getElementById('spCategoryFilter');
    if (catSel && !catSel.dataset.populated) {
        catSel.dataset.populated = '1';
        const cats = [...new Set(rows.map(r => r.category))].sort();
        catSel.innerHTML = '<option value="">All categories</option>' +
            cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    }

    window._spTable = window._spTable || makeSortableTable({
        table: document.querySelector('#spendingPage table'),
        columns: [
            { key: null }, { key: 'date', type: 'date' }, { key: 'portfolio_name', type: 'text' },
            { key: 'description', type: 'text' }, { key: 'category', type: 'text' },
            { key: 'amount', type: 'num' }, { key: null },
        ],
        getRows: () => window._spFilteredRows || [],
        renderRows: (sorted, tbody) => {
            const categories = _allSpendingCategories(rows);
            _populateSpCategoryDatalist(categories);
            tbody.innerHTML = sorted.length ? sorted.map(r => `
                <tr>
                    <td class="ps-3"><input type="checkbox" class="form-check-input sp-row-check" data-id="${r.id}"></td>
                    <td>${Fmt.date(r.date)}</td>
                    <td>${esc(r.portfolio_name || '')}</td>
                    <td>${esc(r.description)}</td>
                    <td>
                        <input type="text" list="spCategoryList" class="form-control form-control-sm d-inline-block" style="width:auto;" value="${esc(r.category)}" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
                        ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                    </td>
                    <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
                    <td class="pe-3"></td>
                </tr>`).join('') : '<tr><td colspan="7" class="text-center text-muted py-3">No transactions match the current filters.</td></tr>';
            _updateSpBulkBar();
        },
        prefsKey: 'spending',
    });
    window._spFilteredRows = filtered;
    window._spTable.refresh();
    _wireSpBulkActions();
}
```

Replace with:

```javascript
window._spTxState = window._spTxState || { page: 0, pageSize: 50, sortBy: 'date', sortDir: 'desc' };

async function _fetchAndRenderSpendingTable() {
    const st = window._spTxState;
    const params = {
        limit: st.pageSize,
        offset: st.page * st.pageSize,
        sort_by: st.sortBy,
        sort_dir: st.sortDir,
    };
    const accountId = document.getElementById('spAccountFilter')?.value;
    const category = document.getElementById('spCategoryFilter')?.value;
    const fromDate = document.getElementById('spFromDate')?.value;
    const toDate = document.getElementById('spToDate')?.value;
    if (accountId) params.portfolio_id = accountId;
    if (category) params.category = category;
    if (fromDate) params.start_date = fromDate;
    if (toDate) params.end_date = toDate;

    const tbody = document.getElementById('spTxBody');
    let result;
    try {
        result = await window.apiClient.getSpendingTransactions(params);
    } catch (err) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3">${esc(err.message)}</td></tr>`;
        return;
    }
    const rows = result.items || [];
    window._spendingAllRows = rows;

    const catSel = document.getElementById('spCategoryFilter');
    if (catSel && !catSel.dataset.populated) {
        catSel.dataset.populated = '1';
        const cats = _allSpendingCategories();
        catSel.innerHTML = '<option value="">All categories</option>' +
            cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    }
    _populateSpCategoryDatalist(_allSpendingCategories());

    if (tbody) {
        tbody.innerHTML = rows.length ? rows.map(r => `
            <tr>
                <td class="ps-3"><input type="checkbox" class="form-check-input sp-row-check" data-id="${r.id}"></td>
                <td>${Fmt.date(r.date)}</td>
                <td>${esc(r.portfolio_name || '')}</td>
                <td>${esc(r.description)}</td>
                <td>
                    ${esc(r.category)}
                    ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                </td>
                <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
                <td class="pe-3"></td>
            </tr>`).join('') : '<tr><td colspan="7" class="text-center text-muted py-3">No transactions match the current filters.</td></tr>';
    }
    _updateSpBulkBar();

    const total = result.total || 0;
    const pageCount = Math.max(1, Math.ceil(total / st.pageSize));
    const pageInfo = document.getElementById('spTxPageInfo');
    if (pageInfo) pageInfo.textContent = `Page ${st.page + 1} of ${pageCount} (${total} total)`;
    const prevBtn = document.getElementById('spTxPrevPage');
    const nextBtn = document.getElementById('spTxNextPage');
    if (prevBtn) prevBtn.disabled = st.page <= 0;
    if (nextBtn) nextBtn.disabled = st.page >= pageCount - 1;

    document.querySelectorAll('#spendingPage th[data-key]').forEach(th => {
        const arrow = th.querySelector('.pfm-sort-arrow') || (() => {
            const s = document.createElement('span');
            s.className = 'pfm-sort-arrow ms-1';
            th.appendChild(s);
            return s;
        })();
        arrow.textContent = th.dataset.key === st.sortBy ? (st.sortDir === 'asc' ? '▲' : '▼') : '';
    });

    _wireSpBulkActions();
}

function _wireSpendingTablePagination() {
    const table = document.querySelector('#spPaneTransactions table');
    if (table && !table.dataset.sortWired) {
        table.dataset.sortWired = '1';
        table.querySelectorAll('th[data-key]').forEach(th => {
            th.style.cursor = 'pointer';
            th.addEventListener('click', () => {
                const key = th.dataset.key;
                const st = window._spTxState;
                if (st.sortBy === key) {
                    st.sortDir = st.sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    st.sortBy = key;
                    st.sortDir = key === 'description' || key === 'category' || key === 'portfolio_name' ? 'asc' : 'desc';
                }
                st.page = 0;
                _fetchAndRenderSpendingTable();
            });
        });
    }
    const prevBtn = document.getElementById('spTxPrevPage');
    if (prevBtn && !prevBtn.dataset.wired) {
        prevBtn.dataset.wired = '1';
        prevBtn.addEventListener('click', () => {
            if (window._spTxState.page > 0) {
                window._spTxState.page -= 1;
                _fetchAndRenderSpendingTable();
            }
        });
    }
    const nextBtn = document.getElementById('spTxNextPage');
    if (nextBtn && !nextBtn.dataset.wired) {
        nextBtn.dataset.wired = '1';
        nextBtn.addEventListener('click', () => {
            window._spTxState.page += 1;
            _fetchAndRenderSpendingTable();
        });
    }
    const pageSizeSel = document.getElementById('spTxPageSize');
    if (pageSizeSel && !pageSizeSel.dataset.wired) {
        pageSizeSel.dataset.wired = '1';
        pageSizeSel.addEventListener('change', () => {
            window._spTxState.pageSize = parseInt(pageSizeSel.value, 10);
            window._spTxState.page = 0;
            _fetchAndRenderSpendingTable();
        });
    }
}
```

(`_wireSpendingTablePagination` is called once from `loadSpendingPage`
in Step 5 below, guarded by `dataset.sortWired`/`dataset.wired` the same
way every other one-time listener in this file is guarded, so repeat
page visits don't stack duplicate listeners.)

- [ ] **Step 5: Update `loadSpendingPage`'s filter wiring and delete `window.updateSpendingRowCategory`**

In `web_client/js/pfm_features.js`, find (inside `loadSpendingPage`):

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
    ['spAccountFilter', 'spCategoryFilter', 'spFromDate', 'spToDate'].forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.dataset.wired) {
            el.dataset.wired = '1';
            el.addEventListener('change', () => _renderSpendingTable());
        }
    });
    await _refreshSpendingData();
}
window.loadSpendingPage = loadSpendingPage;
```

Replace with:

```javascript
    const selAllUncatBtn = document.getElementById('spSelectAllUncategorized');
    if (selAllUncatBtn && !selAllUncatBtn.dataset.wired) {
        selAllUncatBtn.dataset.wired = '1';
        selAllUncatBtn.addEventListener('click', async () => {
            const catFilter = document.getElementById('spCategoryFilter');
            if (catFilter) catFilter.value = 'uncategorized';
            window._spTxState.page = 0;
            await _fetchAndRenderSpendingTable();
            document.querySelectorAll('#spTxBody .sp-row-check').forEach(cb => { cb.checked = true; });
            _updateSpBulkBar();
        });
    }
    ['spAccountFilter', 'spCategoryFilter', 'spFromDate', 'spToDate'].forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.dataset.wired) {
            el.dataset.wired = '1';
            el.addEventListener('change', () => {
                window._spTxState.page = 0;
                _fetchAndRenderSpendingTable();
            });
        }
    });
    _wireSpendingTablePagination();
    await _refreshSpendingData();
    await _fetchAndRenderSpendingTable();
}
window.loadSpendingPage = loadSpendingPage;
```

Then find and delete `window.updateSpendingRowCategory` in full (its
only caller was the removed `onchange` attribute):

```javascript
window.updateSpendingRowCategory = async function (id, category) {
    const trimmed = category.trim();
    const row = (window._spendingAllRows || []).find(r => r.id === id);
    if (!trimmed || (row && trimmed === row.category)) {
        await _refreshSpendingData();
        return;
    }
    try {
        await window.apiClient.updateSpendingCategory(id, trimmed);
        if (row) row.category = trimmed;
    } catch (err) {
        alert('Error: ' + err.message);
        await _refreshSpendingData();
    }
};

```

Replace with nothing (delete these lines entirely, including the
trailing blank line, so the file goes directly from the end of
`loadSpendingPage`'s closing `window.loadSpendingPage = loadSpendingPage;`
to `function _renderSpendingRules(rules) {`).

- [ ] **Step 6: Rework `_refreshSpendingData` and `_allSpendingCategories`**

This step must land together with Steps 4-5: `_fetchAndRenderSpendingTable`
(Step 4) already calls `_allSpendingCategories()` with no arguments
(the new, post-pagination signature — `_allSpendingCategories` can no
longer derive categories by scanning `window._spendingAllRows`, since
after Step 4 that only ever holds one page). Doing this step in a
*later* task would leave an intermediate commit where
`_fetchAndRenderSpendingTable` calls a signature `_allSpendingCategories`
doesn't have yet, breaking category-filter/datalist population — so it
belongs here, not deferred.

In `web_client/js/pfm_features.js`, find `_refreshSpendingData` in full
(starts `async function _refreshSpendingData() {`, ends with its
closing `}` immediately before `function _populateSpendingAccountFilters`):

```javascript
async function _refreshSpendingData() {
    try {
        const [summary, portfolios, txs, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(30),
            window.apiClient.getPortfolios(),
            window.apiClient.getSpendingTransactions(),
            window.apiClient.getSpendingRules(),
        ]);
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        const el = id => document.getElementById(id);
        if (el('spSpent')) el('spSpent').innerHTML = eur(summary.spent_eur);
        if (el('spIncome')) el('spIncome').innerHTML = eur(summary.income_eur);
        if (el('spTransferred')) el('spTransferred').innerHTML = eur(summary.transferred_eur);

        window._spendingAllRows = txs;
        const bankAccounts = (portfolios || []).filter(p => p.account_type === 'bank');
        _populateSpendingAccountFilters(bankAccounts);
        _renderSpendingCategoryChart(summary.by_category_eur || {});
        _renderSpendingTable();
        _renderSpendingRules(rules);
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
        const [summary, portfolios, categories, rules] = await Promise.all([
            window.apiClient.getSpendingSummary(30),
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
    } catch (err) {
        const body = document.getElementById('spTxBody');
        if (body) body.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">${esc(err.message)}</td></tr>`;
    }
}
```

Then find `_allSpendingCategories` in full:

```javascript
function _allSpendingCategories(rows, extra) {
    return [...new Set(['uncategorized', 'Transfer',
        ...rows.map(r => r.category), ...(extra || [])])].sort();
}
```

Replace with:

```javascript
function _allSpendingCategories(extra) {
    return [...new Set(['uncategorized', 'Transfer',
        ...(window._spendingAllCategories || []), ...(extra || [])])].sort();
}
```

Then find the AI-suggest review panel's call site (the only remaining
caller that passes an argument — `_fetchAndRenderSpendingTable`'s two
calls from Step 4 already use the new no-first-argument form):

```javascript
    const categories = _allSpendingCategories(
        window._spendingAllRows || [], groups.map(g => g.suggestedCategory));
```

Replace with:

```javascript
    const categories = _allSpendingCategories(groups.map(g => g.suggestedCategory));
```

- [ ] **Step 7: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 8: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass (55 minus the 4 deleted `filterSpendingRows`
tests = 51 remaining, all passing).

- [ ] **Step 9: Rebuild, redeploy, and verify manually**

Run:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On the Spending page's Transactions tab: confirm the table loads with
50 rows by default; click "Next"/"Previous" and confirm different rows
appear each time and the "Page X of Y (N total)" text updates; change
the page-size dropdown and confirm the row count changes and page
resets to 1; click a column header (e.g. "Amount") and confirm the
whole table re-sorts (not just the visible page — check by comparing
page 1's values against a manual expectation, e.g. sorting by amount
ascending should show the most negative outflow first across the
*entire* history, not just today's page); change the Account/Category/
Date filters and confirm the table re-fetches and the total count
changes accordingly; confirm the category column is now plain text (no
input box, nothing happens on click); confirm bulk-select, "Set
category", "Suggest categories (AI)", and "Apply rules to selected"
all still work exactly as before (operating on the current page's
selected rows).

- [ ] **Step 10: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: server-side pagination/sort for the Transactions tab; read-only category column

The category column drops its per-row inline editor -- bulk-select +
'Set category' and AI-suggest Apply remain the only ways to
recategorize a transaction from this tab.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Frontend — Categories tab: Chart.js chart

**Files:**
- Modify: `web_client/js/pfm_features.js` (`_renderSpendingCategoryChart` rewritten, new `shown.bs.tab` wiring)

**Interfaces:**
- Consumes: Task 5's `#spCategoryChartCanvas`/`#spCategoryChartShowAll`
  elements, `summary.by_category_eur` (unchanged shape, already fetched
  by `_refreshSpendingData`).
- Produces: nothing new consumed by other tasks — this is a self-contained
  visual rework of an existing render function, same call site
  (`_renderSpendingCategoryChart(summary.by_category_eur || {})` inside
  `_refreshSpendingData`, untouched by this task) and same function name.

- [ ] **Step 1: Rewrite the chart function**

In `web_client/js/pfm_features.js`, find `_renderSpendingCategoryChart`
in full (starts `function _renderSpendingCategoryChart(byCategoryEur) {`,
ends with its closing `}` immediately before
`window._spTxState = window._spTxState || ...` from Task 6):

```javascript
function _renderSpendingCategoryChart(byCategoryEur) {
    const wrap = document.getElementById('spCategoryChart');
    if (!wrap) return;
    const entries = Object.entries(byCategoryEur).sort((a, b) => b[1] - a[1]);
    if (!entries.length) {
        wrap.innerHTML = '<div class="text-muted small">No categorized spending yet.</div>';
        return;
    }
    const max = Math.max(...entries.map(e => e[1]));
    wrap.innerHTML = entries.map(([cat, amt]) => `
        <div class="d-flex align-items-center mb-1">
            <div class="small text-muted" style="width:140px;">${esc(cat)}</div>
            <div class="flex-grow-1 bg-light rounded" style="height:18px;">
                <div class="bg-danger rounded" style="height:18px;width:${Math.max(2, amt / max * 100)}%;"></div>
            </div>
            <div class="small ms-2" style="width:80px;text-align:right;">€${Fmt.num(amt, 0, 0)}</div>
        </div>`).join('');
}
```

Replace with:

```javascript
let _spCategoryChartInstance = null;

function _renderSpendingCategoryChart(byCategoryEur) {
    window._spCategoryChartData = byCategoryEur || {};
    const canvas = document.getElementById('spCategoryChartCanvas');
    if (!canvas) return;
    const showAll = !!window._spCategoryChartShowAll;
    let entries = Object.entries(window._spCategoryChartData).sort((a, b) => b[1] - a[1]);
    if (!showAll) entries = entries.slice(0, 8);

    if (_spCategoryChartInstance) {
        _spCategoryChartInstance.destroy();
        _spCategoryChartInstance = null;
    }
    if (!entries.length) return;

    const labels = entries.map(([cat]) => cat);
    const values = entries.map(([, amt]) => amt);
    _spCategoryChartInstance = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Spent (30d, EUR)',
                data: values,
                backgroundColor: 'rgba(220,53,69,0.7)',
                borderColor: 'rgba(220,53,69,1)',
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label(item) { return ` €${Fmt.num(item.raw, 0, 0)}`; }
                    }
                }
            },
            scales: {
                x: {
                    title: { display: true, text: 'EUR (30d)' },
                    ticks: { callback: v => '€' + v }
                }
            }
        }
    });
}
```

- [ ] **Step 2: Wire the "Show all" toggle and the `shown.bs.tab` re-render**

In `web_client/js/pfm_features.js`, find (inside `loadSpendingPage`,
the `_wireSpendingTablePagination();` call added in Task 6 Step 5):

```javascript
    _wireSpendingTablePagination();
    await _refreshSpendingData();
    await _fetchAndRenderSpendingTable();
}
window.loadSpendingPage = loadSpendingPage;
```

Replace with:

```javascript
    _wireSpendingTablePagination();
    const showAllBtn = document.getElementById('spCategoryChartShowAll');
    if (showAllBtn && !showAllBtn.dataset.wired) {
        showAllBtn.dataset.wired = '1';
        showAllBtn.addEventListener('click', () => {
            window._spCategoryChartShowAll = !window._spCategoryChartShowAll;
            showAllBtn.textContent = window._spCategoryChartShowAll ? 'Show top 8' : 'Show all';
            _renderSpendingCategoryChart(window._spCategoryChartData || {});
        });
    }
    const categoriesTabBtn = document.getElementById('spTabBtnCategories');
    if (categoriesTabBtn && !categoriesTabBtn.dataset.wired) {
        categoriesTabBtn.dataset.wired = '1';
        // A Chart.js chart built while its canvas sits inside a
        // display:none tab-pane renders at zero size — re-render (no
        // re-fetch, the data's already in memory) once the pane is
        // actually visible and the canvas has real dimensions.
        categoriesTabBtn.addEventListener('shown.bs.tab', () => {
            _renderSpendingCategoryChart(window._spCategoryChartData || {});
        });
    }
    await _refreshSpendingData();
    await _fetchAndRenderSpendingTable();
}
window.loadSpendingPage = loadSpendingPage;
```

- [ ] **Step 3: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 4: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass.

- [ ] **Step 5: Rebuild, redeploy, and verify manually**

Run:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On the Spending page, switch to the Categories tab: confirm a
horizontal bar chart renders (not zero-height/blank — this is the
specific failure mode the `shown.bs.tab` wiring in Step 2 prevents),
showing at most 8 categories sorted by amount descending. Click "Show
all" and confirm every category with 30-day spending appears, and the
button label flips to "Show top 8"; click it again and confirm it
reverts. Switch to another tab and back to Categories and confirm the
chart still renders correctly (not a repeat of the zero-size bug).

- [ ] **Step 6: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: replace the category breakdown bars with a Chart.js chart

Top 8 categories by 30-day amount, sorted descending, with a 'Show
all' toggle. Re-renders on shown.bs.tab since Chart.js can't size a
canvas inside a hidden tab-pane.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Frontend — Categories tab: list + edit-in-place rename + add form

**Files:**
- Modify: `web_client/js/pfm_features.js` (new `_renderCategoriesList`,
  `window.editSpendingCategory`, `_wireSpCategoryAddForm`; wired into
  `_refreshSpendingData`/`loadSpendingPage`)

**Interfaces:**
- Consumes: `apiClient.getSpendingCategories`/`createSpendingCategory`/
  `renameSpendingCategory` (Task 4), Task 5's `#spCategoriesList`/
  `#spCategoryAddForm`/`#spCategoryNameInput`/`#spCategoryAddStatus`
  elements, `window._spendingAllCategories` (Task 6 Step 6).
- Produces: nothing new consumed by other tasks — this is the final
  piece of the Categories tab.

- [ ] **Step 1: Add `_renderCategoriesList` and `window.editSpendingCategory`**

In `web_client/js/pfm_features.js`, find `_renderSpendingRules` in full
(starts `function _renderSpendingRules(rules) {`, ends with its closing
`}` immediately before `window.deleteSpendingRule = async function`):

```javascript
function _renderSpendingRules(rules) {
    const body = document.getElementById('spRulesBody');
    if (!body) return;
    body.innerHTML = rules.length ? rules.map(r => `
        <tr>
            <td class="ps-3" id="spRulePatternCell${r.id}" data-value="${escapeForAttr(r.pattern)}">${esc(r.pattern)}</td>
            <td id="spRuleCategoryCell${r.id}" data-value="${escapeForAttr(r.category)}">${esc(r.category)}</td>
            <td class="pe-3 text-end">
                <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingRule(${r.id})" title="Edit"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger" onclick="window.deleteSpendingRule(${r.id})" title="Delete"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`).join('') : '<tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr>';
}
```

Replace with (adds `_renderCategoriesList`/`window.editSpendingCategory`
immediately after, unchanged `_renderSpendingRules`):

```javascript
function _renderSpendingRules(rules) {
    const body = document.getElementById('spRulesBody');
    if (!body) return;
    body.innerHTML = rules.length ? rules.map(r => `
        <tr>
            <td class="ps-3" id="spRulePatternCell${r.id}" data-value="${escapeForAttr(r.pattern)}">${esc(r.pattern)}</td>
            <td id="spRuleCategoryCell${r.id}" data-value="${escapeForAttr(r.category)}">${esc(r.category)}</td>
            <td class="pe-3 text-end">
                <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingRule(${r.id})" title="Edit"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger" onclick="window.deleteSpendingRule(${r.id})" title="Delete"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`).join('') : '<tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr>';
}

function _renderCategoriesList(categories) {
    const wrap = document.getElementById('spCategoriesList');
    if (!wrap) return;
    wrap.innerHTML = categories.length ? categories.map((cat, i) => `
        <div class="list-group-item d-flex align-items-center justify-content-between">
            <span id="spCategoryNameCell${i}" data-value="${escapeForAttr(cat)}">${esc(cat)}</span>
            <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingCategory(${i})" title="Edit"><i class="bi bi-pencil"></i></button>
        </div>`).join('') : '<div class="list-group-item text-center text-muted py-2">No categories yet.</div>';
    window._spCategoriesListData = categories;
}

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

- [ ] **Step 2: Add `_wireSpCategoryAddForm`**

In `web_client/js/pfm_features.js`, find the end of `_wireSpendingRuleForm`
(its closing `}` — the function itself is unchanged, only the insertion
point right after it matters here; do not touch the comment that
follows it, `// \`ids\` lets the same import-modal logic...`, which
stays exactly where it is, immediately after the newly-inserted
function):

```javascript
            } catch (err) {
                if (status) { status.className = 'small text-danger mt-2'; status.textContent = 'Error: ' + err.message; }
                else alert('Error: ' + err.message);
            }
        });
    }
}
```

Replace with:

```javascript
            } catch (err) {
                if (status) { status.className = 'small text-danger mt-2'; status.textContent = 'Error: ' + err.message; }
                else alert('Error: ' + err.message);
            }
        });
    }
}

function _wireSpCategoryAddForm() {
    const form = document.getElementById('spCategoryAddForm');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const name = document.getElementById('spCategoryNameInput').value.trim();
            if (!name) return;
            const status = document.getElementById('spCategoryAddStatus');
            try {
                await window.apiClient.createSpendingCategory(name);
                form.reset();
                await _refreshSpendingData();
                if (status) { status.className = 'small text-success mt-2'; status.textContent = 'Category added.'; }
            } catch (err) {
                if (status) { status.className = 'small text-danger mt-2'; status.textContent = 'Error: ' + err.message; }
                else alert('Error: ' + err.message);
            }
        });
    }
}
```

Note this old_string block (`_wireSpendingRuleForm`'s closing lines)
is *not* unique in the file by itself — `editSpendingRule`'s `finish`
handler has a similarly-shaped `catch (err) { ... alert ... }` block.
Use enough surrounding context (the full `_wireSpendingRuleForm`
function body, or search for the specific `form.dataset.wired = '1';`
paired with `spRulePattern`/`spRuleCategory` lookups a few lines above
this closing block) to target the correct occurrence — the one whose
`form` variable came from `document.getElementById('spRuleAddForm')`.

- [ ] **Step 3: Wire both into `_refreshSpendingData` and `loadSpendingPage`**

In `web_client/js/pfm_features.js`, find (inside `_refreshSpendingData`,
from Task 6 Step 6's rework):

```javascript
        window._spendingAllCategories = categories;
        const bankAccounts = (portfolios || []).filter(p => p.account_type === 'bank');
        _populateSpendingAccountFilters(bankAccounts);
        _renderSpendingCategoryChart(summary.by_category_eur || {});
        _renderSpendingRules(rules);
```

Replace with:

```javascript
        window._spendingAllCategories = categories;
        const bankAccounts = (portfolios || []).filter(p => p.account_type === 'bank');
        _populateSpendingAccountFilters(bankAccounts);
        _renderSpendingCategoryChart(summary.by_category_eur || {});
        _renderSpendingRules(rules);
        _renderCategoriesList(categories);
```

Then find (inside `loadSpendingPage`, from Task 1's original code —
still present, untouched by earlier tasks):

```javascript
async function loadSpendingPage() {
    _wireSpendingRuleForm();
    _wireSpendingImportModal();
```

Replace with:

```javascript
async function loadSpendingPage() {
    _wireSpendingRuleForm();
    _wireSpCategoryAddForm();
    _wireSpendingImportModal();
```

- [ ] **Step 4: Verify syntax**

Run: `node --check web_client/js/pfm_features.js`
Expected: prints nothing.

- [ ] **Step 5: Run the JS test suite**

Run: `node --test web_client/js/tests/web_client.test.mjs`
Expected: all tests pass.

- [ ] **Step 6: Rebuild, redeploy, and verify manually**

Run:
```bash
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
On the Spending page's Categories tab: confirm every known category
(from transactions, rules, and any explicitly-added ones) appears in
the list below the chart. Click the pencil on one, type a new name,
press Enter — confirm it saves, and check the Transactions tab and
Rules tab to confirm every transaction/rule that used the old name now
shows the new name. Click pencil again on another category, press
Escape — confirm nothing changed. Type a brand-new name into the "Add
category" form and submit — confirm it appears in the categories list
immediately, and also appears in the Transactions tab's category filter
dropdown and free-text datalist without needing a page reload. Try
renaming a category to another category's existing name — confirm it
succeeds (merge case) rather than erroring, and both categories'
transactions end up under the target name.

- [ ] **Step 7: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: add category rename and add-category UI to the Categories tab

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Documentation, rebuild, and manual verification

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section)
- Modify: `PROJECT_STATUS.md` (new "Recent" line)

**Interfaces:**
- Consumes: the finished state of Tasks 1-8 (this task documents and
  verifies the whole feature, no new code).

- [ ] **Step 1: Update CLAUDE.md**

In `CLAUDE.md`'s "Spending Tracking" section, find the endpoints bullet
(currently starts `` `GET /api/v1/spending/` (filters: `portfolio_id`,
`category`, `start_date`, `end_date`, `is_transfer`) ``, ends with
`` `POST /api/v1/spending/rescan-transfers`. ``) and update the
`GET /api/v1/spending/` portion to note it's now paginated/sorted
(`limit`/`offset`/`sort_by`/`sort_dir` query params, response shape
`{items, total}` instead of a bare array) and add a new clause for the
three `/categories` endpoints (`GET` lists the union of used +
registered categories; `POST` creates a bare unused one, 409 on exact
duplicate; `PUT /categories/{old_name}` renames everywhere — every
transaction, every rule, and the registry — merging into an existing
name without erroring if the target name is already registered).

Then find the sentence in the "AI category suggestions" bullet that
currently ends `` `PUT /api/v1/spending/{id}` also now rejects a blank
category with a 400. `` and append: the per-row category cell in the
main transactions table is no longer editable inline (removed —
bulk-select + "Set category" and this AI-suggest panel remain the only
transaction-level recategorization paths); the whole page is now split
into Transactions/Categories/Rules tabs (`#spTabs`), with the
Categories tab holding the category-breakdown chart (now Chart.js, top
8 + "Show all") plus the rename/add-category UI just described.

- [ ] **Step 2: Update PROJECT_STATUS.md**

Bump "Last updated" (currently `2026-07-22` — confirm with `date +%F`,
don't assume it's still today by the time this task runs) and add a
new top entry `**Recent (v2.5.30):**` (current top is v2.5.29) directly
above it, summarizing: the Spending page is now split into
Transactions/Categories/Rules tabs; the Transactions tab is
server-paginated and server-sorted (`GET /api/v1/spending/` gained
`limit`/`offset`/`sort_by`/`sort_dir`, response shape changed to
`{items, total}`) instead of fetching/rendering the full history at
once; the per-row category cell is no longer inline-editable (bulk
actions + AI-suggest remain); new `spending_categories` registry table
(db v27) plus `GET/POST /api/v1/spending/categories` and
`PUT /api/v1/spending/categories/{old_name}` let you add a bare unused
category and rename a category everywhere (transactions, rules,
registry) it's used, merging into an existing name without erroring
if you rename onto one that already exists. Match the prose
style/density of the existing entries (technical, specific, no
marketing language).

- [ ] **Step 3: Verify only docs changed**

Run: `git diff --stat CLAUDE.md PROJECT_STATUS.md`
Expected: both files show changes; `git status --short` shows no other
file modified.

- [ ] **Step 4: Commit docs**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document spending page tabs, pagination, and category management

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Rebuild and redeploy**

Run:
```bash
docker exec portf_backend_dev kill -HUP 1
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```
Expected: both commands complete without error; `docker ps --filter
name=portf_web` shows a recent `CreatedAt`.

- [ ] **Step 6: End-to-end verification**

Run the full backend suite once more to confirm nothing regressed
across all 9 tasks together:

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e
```
Expected: all pass.

```bash
node --test web_client/js/tests/web_client.test.mjs
```
Expected: all pass.

On the live Spending page, walk through the full feature once more as
a single connected flow (each piece was already verified in isolation
in its own task — this checks they compose correctly): open the
Transactions tab, page through a few pages and sort by a column;
switch to the Categories tab, confirm the chart renders immediately
(not blank) and toggle "Show all"; rename a category and confirm the
Transactions tab (after switching back) shows the new name on affected
rows; add a brand-new category and confirm it's selectable via the
free-text datalist on a bulk-recategorize action; switch to the Rules
tab and confirm it's unchanged from before this feature (add/edit/
delete a rule still works); trigger an AI-suggest Apply and confirm it
still creates a rule, categorizes the row, and sweeps other matching
uncategorized rows (behavior from earlier this session, unaffected by
this plan).

- [ ] **Step 7: Report final state**

Summarize in your final report: confirmation that both test suites
pass, confirmation that the manual walkthrough in Step 6 succeeded (or
a precise description of what didn't, if anything), and the final
commit range for this plan (first commit's short SHA from Task 1
through this task's docs commit).

---