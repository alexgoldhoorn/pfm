# Monthly Cash Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a monthly cash flow tracker (salary / other income / mortgage / loan / rest) to the Net Worth page, with a summary card row, entries table, and add/delete form.

**Architecture:** New `monthly_cashflow` DB table (v20) with three CRUD methods; three endpoints inline in `networth.py`; summary cards + table + form added to `#networthPage` in `index.html`; rendering logic added to `pfm_analytics.js`.

**Tech Stack:** Python 3.13 / SQLite / FastAPI / Pydantic / Vanilla JS / Bootstrap 5 / pytest / `uv run`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `portf_manager/database.py` | Modify | `monthly_cashflow` table + CRUD + migration v20 |
| `tests/test_database.py` | Modify | Bump version assertions + cashflow CRUD tests |
| `portf_server/routers/networth.py` | Modify | GET / POST / DELETE `/cashflow` endpoints |
| `tests/unit/test_networth.py` | Create | API + DB layer integration tests |
| `web_client/js/pfm_core.js` | Modify | `getCashflow`, `createCashflowEntry`, `deleteCashflowEntry` API methods |
| `web_client/index.html` | Modify | Monthly Cash Flow section inside `#networthPage` |
| `web_client/js/pfm_analytics.js` | Modify | `_loadCashflow`, `_wireCashflowForm`, `confirmDeleteCashflow` |
| `web_client/js/help_text.js` | Modify | Update networth PAGE_HELP entry |
| `PROJECT_STATUS.md` | Modify | Bump summary line |
| `CLAUDE.md` | Modify | Add cashflow API docs, bump DB version to 20 |

---

## Task 1: DB layer — `monthly_cashflow` table + CRUD

**Files:**
- Modify: `portf_manager/database.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_cashflow_db.py` (permanent DB-layer tests, mirrors the pattern of `test_deposits.py`):

```python
import os
import tempfile
import pytest
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_create_and_list_cashflow(db):
    entry_id = db.create_monthly_cashflow("Salary", "salary", 3500.0, "EUR")
    assert entry_id > 0
    items = db.get_monthly_cashflow()
    assert len(items) == 1
    assert items[0]["label"] == "Salary"
    assert items[0]["category"] == "salary"
    assert items[0]["amount"] == 3500.0
    assert items[0]["currency"] == "EUR"


def test_cashflow_multiple_entries(db):
    db.create_monthly_cashflow("Salary", "salary", 3500.0, "EUR")
    db.create_monthly_cashflow("Mortgage", "mortgage", 1200.0, "EUR")
    items = db.get_monthly_cashflow()
    assert len(items) == 2


def test_delete_cashflow(db):
    entry_id = db.create_monthly_cashflow("Loan", "loan", 300.0, "EUR")
    assert db.delete_monthly_cashflow(entry_id) is True
    assert db.get_monthly_cashflow() == []


def test_delete_cashflow_missing_returns_false(db):
    assert db.delete_monthly_cashflow(999) is False


def test_cashflow_notes_optional(db):
    entry_id = db.create_monthly_cashflow("Rest", "rest", 800.0, "EUR", notes="Groceries etc")
    items = db.get_monthly_cashflow()
    assert items[0]["notes"] == "Groceries etc"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_cashflow_db.py -v 2>&1 | tail -20
```

Expected: `AttributeError: 'Database' object has no attribute 'create_monthly_cashflow'`

- [ ] **Step 3: Bump DATABASE_VERSION to 20**

In `portf_manager/database.py`, line 16:

```python
DATABASE_VERSION = 20
```

- [ ] **Step 4: Add `monthly_cashflow` table to `_create_all_tables()`**

In `portf_manager/database.py`, find the block that creates `fixed_deposits` (around line 504). Immediately after the `fixed_deposits` `conn.execute(...)` block (before the comment `# Price-update run history`), add:

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_cashflow (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                category    TEXT NOT NULL CHECK(category IN
                                ('salary','other_income','mortgage','loan','rest')),
                amount      REAL NOT NULL DEFAULT 0,
                currency    TEXT NOT NULL DEFAULT 'EUR',
                notes       TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
```

- [ ] **Step 5: Add `_migrate_to_v20()` method**

In `portf_manager/database.py`, directly after the `_migrate_to_v19` method (around line 1234), add:

```python
    def _migrate_to_v20(self, conn: sqlite3.Connection):
        """Migrate from v19 to v20 — add monthly_cashflow table."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_cashflow (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                category    TEXT NOT NULL CHECK(category IN
                                ('salary','other_income','mortgage','loan','rest')),
                amount      REAL NOT NULL DEFAULT 0,
                currency    TEXT NOT NULL DEFAULT 'EUR',
                notes       TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
```

- [ ] **Step 6: Wire migration into `_run_migrations()`**

In `portf_manager/database.py`, find (around line 600):
```python
        if current_version < 19:
            self._migrate_to_v19(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

Replace with:
```python
        if current_version < 19:
            self._migrate_to_v19(conn)
        if current_version < 20:
            self._migrate_to_v20(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

- [ ] **Step 7: Add CRUD methods**

At the end of `portf_manager/database.py`, before the `cache_get` method (or after the `delete_fixed_deposit` method), add a new section:

```python
    # ── Monthly Cash Flow ──────────────────────────────────────────────────

    def get_monthly_cashflow(self) -> List[Dict]:
        """List all monthly cash flow entries ordered by id."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM monthly_cashflow ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def create_monthly_cashflow(
        self,
        label: str,
        category: str,
        amount: float,
        currency: str = "EUR",
        notes: str = None,
    ) -> int:
        """Create a monthly cash flow entry, return new id."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO monthly_cashflow (label, category, amount, currency, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (label, category, amount, currency.upper(), notes),
            )
            conn.commit()
            return cur.lastrowid

    def delete_monthly_cashflow(self, entry_id: int) -> bool:
        """Delete a monthly cash flow entry. Returns True if found."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM monthly_cashflow WHERE id = ?", (entry_id,)
            )
            conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 8: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_cashflow_db.py -v 2>&1 | tail -15
```

Expected: `5 passed`

- [ ] **Step 9: Commit**

```bash
git add portf_manager/database.py tests/unit/test_cashflow_db.py
git commit -m "feat: add monthly_cashflow table and CRUD (db v20)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Bump version assertions in `test_database.py`

**Files:**
- Modify: `tests/test_database.py`

- [ ] **Step 1: Replace all four `== 19` version assertions with `== 20`**

In `tests/test_database.py`, there are four occurrences of the version assertion. Replace all four:

```python
# Line ~53 — inside test for fresh db check
assert result[0] == 19  # Current schema version
```
→
```python
assert result[0] == 20  # Current schema version
```

```python
# Line ~1001 — inside test_migration_and_list_portfolios
assert version == 19
```
→
```python
assert version == 20
```

```python
# Line ~1031 — inside test_fresh_database_creation
assert version == 19
```
→
```python
assert version == 20
```

```python
# Line ~1100 — inside test_migration_from_older_version
assert version == 19
```
→
```python
assert version == 20
```

Use `replace_all=True` on the Edit tool for the `assert version == 19` pattern (there are 3 occurrences at lines ~1001, ~1031, ~1100), and handle the first one at line ~53 separately since it has an inline comment.

- [ ] **Step 2: Add `monthly_cashflow` table check to `test_fresh_database_creation`**

In `tests/test_database.py`, find `test_fresh_database_creation` (around line 1021). The test already checks for `"users"`, `"entities"`, `"portfolios"` in `tables`. Add after those assertions:

```python
            assert "monthly_cashflow" in tables
```

- [ ] **Step 3: Run the full DB test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py -v 2>&1 | tail -20
```

Expected: all tests pass (no `== 19` failures).

- [ ] **Step 4: Commit**

```bash
git add tests/test_database.py
git commit -m "test: bump DB version assertions to 20, add monthly_cashflow table check

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: API endpoints in `networth.py`

**Files:**
- Modify: `portf_server/routers/networth.py`

- [ ] **Step 1: Add constants and Pydantic model**

In `portf_server/routers/networth.py`, after the existing `ManualAssetUpdate` class definition, add:

```python
_CF_INCOME_CATS = {"salary", "other_income"}
_CF_ALL_CATS = {"salary", "other_income", "mortgage", "loan", "rest"}


class CashflowBody(BaseModel):
    label: str
    category: str
    amount: float = 0.0
    currency: str = "EUR"
    notes: Optional[str] = None
```

- [ ] **Step 2: Add GET `/cashflow` endpoint**

At the end of `portf_server/routers/networth.py`, add:

```python
@router.get("/cashflow")
def get_cashflow(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List monthly cash flow entries with income/expense summary."""
    items = db.get_monthly_cashflow()
    out = []
    income_eur = 0.0
    expenses_eur = 0.0
    by_category = {cat: 0.0 for cat in _CF_ALL_CATS}
    for it in items:
        amt_eur = float(it["amount"] or 0) * _fx(it.get("currency", "EUR"))
        out.append({**it, "amount_eur": round(amt_eur, 2)})
        if it["category"] in _CF_INCOME_CATS:
            income_eur += amt_eur
        else:
            expenses_eur += amt_eur
        if it["category"] in by_category:
            by_category[it["category"]] += amt_eur
    return {
        "items": out,
        "income_eur": round(income_eur, 2),
        "expenses_eur": round(expenses_eur, 2),
        "net_monthly_eur": round(income_eur - expenses_eur, 2),
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
    }


@router.post("/cashflow")
def create_cashflow(
    body: CashflowBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Create a monthly cash flow entry."""
    if body.category not in _CF_ALL_CATS:
        raise HTTPException(
            status_code=422, detail=f"Invalid category '{body.category}'"
        )
    new_id = db.create_monthly_cashflow(
        label=body.label,
        category=body.category,
        amount=body.amount,
        currency=(body.currency or "EUR").upper(),
        notes=body.notes,
    )
    return {"id": new_id}


@router.delete("/cashflow/{entry_id}")
def delete_cashflow(
    entry_id: int,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Delete a monthly cash flow entry."""
    if not db.delete_monthly_cashflow(entry_id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}
```

- [ ] **Step 3: Run linter to catch any issues**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black portf_server/routers/networth.py && UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run flake8 portf_server/routers/networth.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add portf_server/routers/networth.py
git commit -m "feat: add GET/POST/DELETE /api/v1/networth/cashflow endpoints

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: API tests for cashflow endpoints

**Files:**
- Create: `tests/unit/test_networth.py`

- [ ] **Step 1: Write tests (they will pass immediately since Task 3 is done — run them to verify)**

Create `tests/unit/test_networth.py`:

```python
"""Tests for the networth cashflow API endpoints."""

import pytest
from fastapi.testclient import TestClient
from portf_manager.database import Database

_TEST_API_KEY = "test-key-networth-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.dependencies import get_database, get_api_key_manager
    from portf_server.auth_middleware import APIKeyManager

    db_instance = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db_instance)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    app.dependency_overrides[get_database] = lambda: db_instance
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app)


def test_cashflow_empty(tmp_path):
    client = _make_client(tmp_path)
    r = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["items"] == []
    assert d["net_monthly_eur"] == 0.0
    assert d["income_eur"] == 0.0
    assert d["expenses_eur"] == 0.0
    assert set(d["by_category"]) == {"salary", "other_income", "mortgage", "loan", "rest"}


def test_cashflow_net_monthly_eur(tmp_path):
    client = _make_client(tmp_path)
    client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Salary", "category": "salary", "amount": 3500.0, "currency": "EUR"},
        headers=HEADERS,
    )
    client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Mortgage", "category": "mortgage", "amount": 1200.0, "currency": "EUR"},
        headers=HEADERS,
    )
    client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Living", "category": "rest", "amount": 800.0, "currency": "EUR"},
        headers=HEADERS,
    )
    r = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["income_eur"] == 3500.0
    assert d["expenses_eur"] == 2000.0
    assert d["net_monthly_eur"] == 1500.0
    assert d["by_category"]["salary"] == 3500.0
    assert d["by_category"]["mortgage"] == 1200.0
    assert d["by_category"]["rest"] == 800.0
    assert d["by_category"]["loan"] == 0.0
    assert len(d["items"]) == 3
    assert "amount_eur" in d["items"][0]


def test_cashflow_create_invalid_category(tmp_path):
    client = _make_client(tmp_path)
    r = client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Foo", "category": "bad_category", "amount": 100.0},
        headers=HEADERS,
    )
    assert r.status_code == 422


def test_cashflow_delete(tmp_path):
    client = _make_client(tmp_path)
    r = client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Loan", "category": "loan", "amount": 300.0},
        headers=HEADERS,
    )
    assert r.status_code == 200
    entry_id = r.json()["id"]

    r2 = client.delete(f"/api/v1/networth/cashflow/{entry_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["deleted"] is True

    r3 = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    assert r3.json()["items"] == []


def test_cashflow_delete_missing(tmp_path):
    client = _make_client(tmp_path)
    r = client.delete("/api/v1/networth/cashflow/999", headers=HEADERS)
    assert r.status_code == 404


def test_cashflow_other_income_is_income(tmp_path):
    client = _make_client(tmp_path)
    client.post(
        "/api/v1/networth/cashflow",
        json={"label": "Rental", "category": "other_income", "amount": 500.0},
        headers=HEADERS,
    )
    r = client.get("/api/v1/networth/cashflow", headers=HEADERS)
    d = r.json()
    assert d["income_eur"] == 500.0
    assert d["expenses_eur"] == 0.0
    assert d["net_monthly_eur"] == 500.0
```

- [ ] **Step 2: Run tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_networth.py -v 2>&1 | tail -20
```

Expected: `6 passed`

- [ ] **Step 3: Run all unit tests to check nothing is broken**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: all tests pass (count will be higher than the pre-feature baseline, no failures).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_networth.py
git commit -m "test: add cashflow API endpoint tests

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: API client methods in `pfm_core.js`

**Files:**
- Modify: `web_client/js/pfm_core.js`

- [ ] **Step 1: Add three API client methods after `deleteDeposit`**

In `web_client/js/pfm_core.js`, find the `matureDeposit` method (around line 1327). Insert the three new methods directly before it:

```javascript
        async getCashflow() {
            const resp = await fetch(this.baseURL + '/api/v1/networth/cashflow', { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to load cash flow');
            return resp.json();
        },
        async createCashflowEntry(payload) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/cashflow', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to add entry');
            return resp.json();
        },
        async deleteCashflowEntry(id) {
            const resp = await fetch(this.baseURL + '/api/v1/networth/cashflow/' + id, { method: 'DELETE', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to delete entry');
            return resp.json().catch(() => ({}));
        },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add getCashflow/createCashflowEntry/deleteCashflowEntry API client methods

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 6: HTML section in `index.html`

**Files:**
- Modify: `web_client/index.html`

- [ ] **Step 1: Add Monthly Cash Flow section**

In `web_client/index.html`, find the closing `</div>` of the Fixed Deposits section (line ~2403, the one that closes `<div class="col-12 mt-2">` wrapping the deposits card). Insert the new block immediately **after** that `</div>` and **before** the `</div>` that closes the `<div class="row g-3">` at line ~2404:

```html
                        <!-- Monthly Cash Flow section -->
                        <div class="col-12 mt-2">
                            <div class="card">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span><i class="bi bi-arrow-left-right me-2"></i>Monthly Cash Flow
                                        <span class="ms-1 text-muted" style="cursor:help" title="Rough monthly income and recurring expenses. Update when your situation changes. Used by Goals and Forecast projections."><i class="bi bi-info-circle"></i></span>
                                    </span>
                                </div>
                                <!-- Summary cards -->
                                <div class="card-body pb-0">
                                    <div class="row g-2 mb-3">
                                        <div class="col-6 col-md-2">
                                            <div class="card h-100 border-success">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Monthly income</div>
                                                    <div class="fs-6 fw-bold text-success" id="cfIncome">—</div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-6 col-md-2">
                                            <div class="card h-100">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Mortgage</div>
                                                    <div class="fs-6 fw-bold text-danger" id="cfMortgage">—</div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-6 col-md-2">
                                            <div class="card h-100">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Loan</div>
                                                    <div class="fs-6 fw-bold text-danger" id="cfLoan">—</div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-6 col-md-2">
                                            <div class="card h-100">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Rest</div>
                                                    <div class="fs-6 fw-bold text-danger" id="cfRest">—</div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-12 col-md-4">
                                            <div class="card h-100 text-white" id="cfNetCard" style="background:#0d6efd;">
                                                <div class="card-body py-2">
                                                    <div class="small opacity-75 mb-1">Net / month</div>
                                                    <div class="fs-6 fw-bold" id="cfNet">—</div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <!-- Entries table -->
                                <div class="table-responsive">
                                    <table class="table table-hover mb-0">
                                        <thead><tr>
                                            <th class="ps-3">Label</th>
                                            <th>Category</th>
                                            <th class="text-end">Amount / month</th>
                                            <th class="text-end">EUR</th>
                                            <th class="pe-3"></th>
                                        </tr></thead>
                                        <tbody id="cfItemsBody"><tr><td colspan="5" class="text-center text-muted py-3">No entries yet.</td></tr></tbody>
                                    </table>
                                </div>
                                <!-- Add form -->
                                <div class="card-body border-top">
                                    <p class="small text-muted mb-2">Add a recurring monthly income or expense:</p>
                                    <form id="cfAddForm" class="row g-2 align-items-end">
                                        <div class="col-12 col-sm-3">
                                            <label class="form-label small mb-1">Label *</label>
                                            <input class="form-control form-control-sm" id="cfLabel" placeholder="e.g. Salary, Mortgage payment" required>
                                        </div>
                                        <div class="col-6 col-sm-2">
                                            <label class="form-label small mb-1">Category</label>
                                            <select class="form-select form-select-sm" id="cfCategory">
                                                <optgroup label="Income">
                                                    <option value="salary">Salary</option>
                                                    <option value="other_income">Other income</option>
                                                </optgroup>
                                                <optgroup label="Expenses">
                                                    <option value="mortgage">Mortgage</option>
                                                    <option value="loan">Loan</option>
                                                    <option value="rest">Rest</option>
                                                </optgroup>
                                            </select>
                                        </div>
                                        <div class="col-6 col-sm-2">
                                            <label class="form-label small mb-1">Amount / month *</label>
                                            <input type="number" step="any" class="form-control form-control-sm" id="cfAmount" placeholder="0.00" required>
                                        </div>
                                        <div class="col-4 col-sm-1">
                                            <label class="form-label small mb-1">Currency</label>
                                            <input class="form-control form-control-sm" id="cfCurrency" value="EUR" maxlength="3">
                                        </div>
                                        <div class="col-8 col-sm-2">
                                            <label class="form-label small mb-1">Notes</label>
                                            <input class="form-control form-control-sm" id="cfNotes" placeholder="Optional">
                                        </div>
                                        <div class="col-12 col-sm-2">
                                            <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                        </div>
                                        <div class="col-12"><div class="small" id="cfAddStatus"></div></div>
                                    </form>
                                </div>
                            </div>
                        </div>
```

- [ ] **Step 2: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Monthly Cash Flow HTML section to Net Worth page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 7: JS rendering in `pfm_analytics.js`

**Files:**
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Add constants before `loadNetworthPage`**

In `web_client/js/pfm_analytics.js`, find the `NW_CATEGORY_LABELS` constant (line ~8). After the `NW_LIABILITY_CATS` set declaration, add:

```javascript
const CF_INCOME_CATS = new Set(['salary', 'other_income']);
const CF_CATEGORY_LABELS = {
    salary: 'Salary', other_income: 'Other income',
    mortgage: 'Mortgage', loan: 'Loan', rest: 'Rest',
};
```

- [ ] **Step 2: Add `_loadCashflow` function**

After the closing `}` of `_wireDepositForm` (around line 233), add:

```javascript
async function _loadCashflow() {
    const body = document.getElementById('cfItemsBody');
    if (!body) return;
    body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</td></tr>';
    const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
    try {
        const d = await window.apiClient.getCashflow();
        const el = id => document.getElementById(id);
        if (el('cfIncome')) el('cfIncome').innerHTML = eur(d.income_eur);
        if (el('cfMortgage')) el('cfMortgage').innerHTML = eur(d.by_category.mortgage);
        if (el('cfLoan')) el('cfLoan').innerHTML = eur(d.by_category.loan);
        if (el('cfRest')) el('cfRest').innerHTML = eur(d.by_category.rest);
        if (el('cfNet')) el('cfNet').innerHTML = eur(d.net_monthly_eur);
        const card = el('cfNetCard');
        if (card) card.style.background = d.net_monthly_eur >= 0 ? '#0d6efd' : '#dc3545';
        if (!d.items.length) {
            body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No entries yet. Add income and expenses below.</td></tr>';
            return;
        }
        body.innerHTML = d.items.map(it => `
            <tr>
                <td class="ps-3"><strong>${escapeForAttr(it.label)}</strong>${it.notes ? `<br><small class="text-muted">${escapeForAttr(it.notes)}</small>` : ''}</td>
                <td><span class="badge ${CF_INCOME_CATS.has(it.category) ? 'bg-success' : 'bg-danger'}">${CF_CATEGORY_LABELS[it.category] || it.category}</span></td>
                <td class="text-end">${Fmt.num(it.amount, 2, 2)} ${it.currency || ''}</td>
                <td class="text-end ${CF_INCOME_CATS.has(it.category) ? 'text-success' : 'text-danger'}">${CF_INCOME_CATS.has(it.category) ? '' : '−'}${Fmt.amt('€' + Fmt.num(it.amount_eur, 0, 0))}</td>
                <td class="pe-3 text-end"><button class="btn btn-sm btn-outline-danger" onclick="confirmDeleteCashflow(${it.id})"><i class="bi bi-trash"></i></button></td>
            </tr>`).join('');
    } catch (err) {
        body.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-3">${err.message}</td></tr>`;
    }
}

function _wireCashflowForm() {
    const form = document.getElementById('cfAddForm');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('cfAddStatus');
            const payload = {
                label: document.getElementById('cfLabel').value.trim(),
                category: document.getElementById('cfCategory').value,
                amount: parseFloat(document.getElementById('cfAmount').value) || 0,
                currency: (document.getElementById('cfCurrency').value || 'EUR').toUpperCase(),
                notes: document.getElementById('cfNotes').value.trim() || null,
            };
            if (!payload.label) return;
            status.className = 'small text-muted'; status.textContent = 'Adding…';
            try {
                await window.apiClient.createCashflowEntry(payload);
                form.reset();
                document.getElementById('cfCurrency').value = 'EUR';
                status.textContent = '';
                _loadCashflow();
            } catch (err) { status.className = 'small text-danger'; status.textContent = err.message; }
        });
    }
}

window.confirmDeleteCashflow = async function (id) {
    if (!confirm('Delete this entry?')) return;
    try { await window.apiClient.deleteCashflowEntry(id); _loadCashflow(); }
    catch (err) { alert('Error: ' + err.message); }
};
```

- [ ] **Step 3: Wire into `loadNetworthPage`**

In `loadNetworthPage()` (around line 20), find this block near the top of the function:

```javascript
    _wireNetworthForm();
    _wireDepositForm();
```

Add the cashflow wiring call:

```javascript
    _wireNetworthForm();
    _wireDepositForm();
    _wireCashflowForm();
```

Then at the end of the `try` block in `loadNetworthPage`, find this line:

```javascript
        _renderDeposits(d.deposits || []);
```

Add after it:

```javascript
        _loadCashflow();
```

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: add monthly cash flow rendering and form logic to Net Worth page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 8: Update help text

**Files:**
- Modify: `web_client/js/help_text.js`

- [ ] **Step 1: Extend the `networth` PAGE_HELP entry**

In `web_client/js/help_text.js`, find:

```javascript
  networth: {
    title: "Net Worth",
    body: `
      <p>Your complete financial picture: brokerage investments plus off-brokerage assets and liabilities, all converted to EUR.</p>
      <ul class="mb-2">
        <li><strong>Investments</strong> are auto-calculated from your portfolio positions. <strong>Fixed Deposits</strong> tracks active term deposits; maturing one posts an interest transaction automatically.</li>
        <li>Add <strong>manual assets</strong> (cash, property, pension) and <strong>liabilities</strong> (mortgage, loans) to complete the picture.</li>
        <li>FIRE goals project from total net worth, not just the brokerage value.</li>
      </ul>
      <p class="text-muted small mb-0">All amounts converted to EUR at live FX rates.</p>`
  },
```

Replace with:

```javascript
  networth: {
    title: "Net Worth",
    body: `
      <p>Your complete financial picture: brokerage investments plus off-brokerage assets and liabilities, all converted to EUR.</p>
      <ul class="mb-2">
        <li><strong>Investments</strong> are auto-calculated from your portfolio positions. <strong>Fixed Deposits</strong> tracks active term deposits; maturing one posts an interest transaction automatically.</li>
        <li>Add <strong>manual assets</strong> (cash, property, pension) and <strong>liabilities</strong> (mortgage, loans) to complete the picture.</li>
        <li><strong>Monthly Cash Flow</strong> tracks rough recurring income (salary, other) vs expenses (mortgage payment, loan, rest). The net figure feeds Goals and Forecast projections.</li>
        <li>FIRE goals project from total net worth, not just the brokerage value.</li>
      </ul>
      <p class="text-muted small mb-0">All amounts converted to EUR at live FX rates.</p>`
  },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/help_text.js
git commit -m "docs: update Net Worth help text to mention Monthly Cash Flow

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 9: Rebuild web, smoke-test, update docs

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rebuild and restart the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Expected: container restarts cleanly.

- [ ] **Step 2: Open the Net Worth page and verify**

Open the app in a browser and navigate to Net Worth. Verify:
- Monthly Cash Flow section appears below Fixed Deposits
- Summary cards show `—` (empty state)
- Entries table shows "No entries yet."
- Add form is present with Label / Category / Amount / Currency / Notes fields
- Add a salary entry (e.g. "Salary", salary, 3000, EUR) → card updates, row appears
- Add a mortgage entry (e.g. "Mortgage", mortgage, 1000, EUR) → Net/month shows €2,000 in blue
- Delete an entry → row disappears, cards update

- [ ] **Step 3: Update `PROJECT_STATUS.md`**

Update the `Last updated` line and prepend to the Recent summary:

Change:
```
Last updated: 2026-06-16
```
to:
```
Last updated: 2026-06-16
```
(same date, it's already today)

Prepend to the **Recent** summary line:
```
**monthly cash flow tracker** (salary/income/mortgage/loan/rest entries on Net Worth page, net monthly figure, db v20);
```

- [ ] **Step 4: Update `CLAUDE.md`**

Make two changes:

**a)** Find `**Current schema version: 19.**` and change to `**Current schema version: 20.**`

**b)** Find the v19 entry:
```
- v19: `fixed_deposits` table — fixed-term deposit tracking ...
```

Add after it:
```
- v20: `monthly_cashflow` table (`id, label, category CHECK('salary'|'other_income'|'mortgage'|'loan'|'rest'), amount, currency, notes, created_at`). Category implies income/expense (salary/other_income = income; mortgage/loan/rest = expense). CRUD: `db.get/create/delete_monthly_cashflow`. Router at `GET|POST|DELETE /api/v1/networth/cashflow`; GET returns `items`, `income_eur`, `expenses_eur`, `net_monthly_eur`, `by_category`. Web: Monthly Cash Flow section on Net Worth page (summary cards + table + add form). No update endpoint — delete and re-add.
```

- [ ] **Step 5: Run full test suite one final time**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: all tests pass, 0 failures.

- [ ] **Step 6: Final commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: update CLAUDE.md and PROJECT_STATUS.md for monthly cash flow (db v20)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
