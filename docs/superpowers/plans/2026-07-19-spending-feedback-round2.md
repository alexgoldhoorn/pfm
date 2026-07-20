# Spending Tracking — Feedback Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address user feedback after trying the Bank Spending Tracking feature: (1) a delete endpoint + bulk select/recategorize/delete on the Spending page, (2) direct bank-account creation on the Brokers page (not just via CSV import), (3) multi-account selection for the existing investment-transaction CSV export, (4) a "Bank Statement" import entry point on the existing Import/Export page, reusing the Spending page's import logic.

**Architecture:** No new DB tables or migration this round — item 1 adds one DB method (`delete_spending_transaction`) and one router endpoint (hard delete, no soft-delete concept on this table, consistent with `bookings`/`spending_rules`). Item 2 extends the existing `PortfolioCreate` schema with an `account_type` field already supported end-to-end by `Database.create_portfolio` since the original feature (this is wiring, not new plumbing). Item 3 is router+UI only (loops the existing single-portfolio DB query, no new DB method). Item 4 refactors `_wireSpendingImportModal`/`_renderSpImportPreview` (`pfm_features.js`) to accept a config object of element ids instead of hard-coded ones, so the same functions drive both the existing Spending-page modal and a new card on the Import/Export page — avoids duplicating the parse/suggest/save flow.

**Tech Stack:** Python 3.13 / SQLite / FastAPI / Pydantic / Vanilla JS / Bootstrap 5 / pytest / `uv run` / Node built-in test runner

## Global Constraints

- Black formatting (line length 88): `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black <file>`
- Type hints on all function signatures; Google-style docstrings; comments on the line before the code, not inline
- All Python commands: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run ...` (the `.venv` is root-owned)
- flake8 clean: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 <file> --max-line-length=88 --extend-ignore=E203,W503,E501`
- No new DB table/column/migration needed this round — everything reuses schema already in place (v25)
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`), each ending with `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Web changes only go live after: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`
- Python changes only go live after: `docker exec portf_backend_dev kill -HUP 1`
- No real personal/financial data anywhere (tests, fixtures) — fictional data only
- Public repo: no home-directory paths (`/home/agoldhoorn/` → `~/`) in anything committed

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `portf_manager/database.py` | Modify | `delete_spending_transaction` DB helper |
| `portf_server/routers/spending.py` | Modify | `DELETE /api/v1/spending/{id}` endpoint |
| `tests/unit/test_spending_db.py` | Modify | DB-layer delete tests |
| `tests/unit/test_spending_api.py` | Modify | API-layer delete tests |
| `web_client/js/pfm_core.js` | Modify | `deleteSpendingTransaction` API client method |
| `web_client/index.html` | Modify | `portfolioAccountType` selector in the Add/Edit Broker modal; checkbox column + bulk-actions bar on the Spending transactions table; new "Import Bank Statement" card on the Import/Export page; multi-select account list on the Export tab |
| `web_client/js/pfm_features.js` | Modify | Account-type wiring in `setupPortfoliosPage`/`editPortfolio`; bulk-select logic on `_renderSpendingTable`; parameterized `_wireSpendingImportModal`/`_renderSpImportPreview`; multi-account CSV export wiring |
| `portf_server/routers/portfolios.py` | Modify | `account_type` on `PortfolioCreate`, passed through to `database.create_portfolio` |
| `portf_server/routers/exports.py` | Modify | `GET /api/v1/export/csv` accepts repeated `portfolio_id` query params |
| `tests/unit/test_api_routers.py` or a new `tests/unit/test_exports.py` | Modify/Create | Multi-portfolio export test (check which file already covers `/export/csv` before deciding) |
| `PROJECT_STATUS.md` | Modify | Bump summary line |
| `CLAUDE.md` | Modify | Extend the existing `### Spending Tracking` section and portfolios docs |

---

## Task 1: DB delete helper + `DELETE /api/v1/spending/{id}` endpoint

**Files:**
- Modify: `portf_manager/database.py`
- Modify: `portf_server/routers/spending.py`
- Modify: `tests/unit/test_spending_db.py`
- Modify: `tests/unit/test_spending_api.py`

This is foundational for Task 3 (bulk delete) — land it first.

- [ ] **Step 1: Write failing DB-layer tests**

In `tests/unit/test_spending_db.py`, append at the end of the file:

```python
def test_delete_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    assert db.delete_spending_transaction(tx_id) is True
    assert db.get_spending_transaction(tx_id) is None


def test_delete_spending_transaction_missing_returns_false(db):
    assert db.delete_spending_transaction(999999) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -v 2>&1 | tail -20
```

Expected: `AttributeError: 'Database' object has no attribute 'delete_spending_transaction'`.

- [ ] **Step 3: Add the DB helper**

In `portf_manager/database.py`, find `list_unlinked_spending_transactions` (ends right before the `# CRUD Operations for Spending Rules` comment):

```python
    def list_unlinked_spending_transactions(self) -> List[Dict]:
        """List spending rows not yet linked as a transfer (is_transfer = 0)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_transactions WHERE is_transfer = 0"
            )
            return [dict(row) for row in cursor.fetchall()]

    # CRUD Operations for Spending Rules (description → category matching)
```

Insert `delete_spending_transaction` between them (mirrors `delete_booking`'s exact pattern):

```python
    def list_unlinked_spending_transactions(self) -> List[Dict]:
        """List spending rows not yet linked as a transfer (is_transfer = 0)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_transactions WHERE is_transfer = 0"
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_spending_transaction(self, spending_id: int) -> bool:
        """Delete a spending transaction by ID (hard delete — this table has
        no soft-delete concept, consistent with bookings/spending_rules)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM spending_transactions WHERE id = ?", (spending_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    # CRUD Operations for Spending Rules (description → category matching)
```

- [ ] **Step 4: Run to confirm pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -v 2>&1 | tail -20
```

Expected: all pass (14 total in this file).

- [ ] **Step 5: Add the router endpoint**

In `portf_server/routers/spending.py`, find `update_spending_category` and the `rescan_transfers` endpoint right after it:

```python
@router.put("/{spending_id}", response_model=dict)
async def update_spending_category(
    spending_id: int,
    body: CategoryUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit a spending row's category (inline edit from the UI table)."""
    if not db.get_spending_transaction(spending_id):
        raise HTTPException(status_code=404, detail="Spending transaction not found")
    db.update_spending_transaction(spending_id, category=body.category)
    return {"id": spending_id, "category": body.category}


@router.post("/rescan-transfers", response_model=dict)
```

Insert a new `DELETE` endpoint between them:

```python
@router.put("/{spending_id}", response_model=dict)
async def update_spending_category(
    spending_id: int,
    body: CategoryUpdateBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Edit a spending row's category (inline edit from the UI table)."""
    if not db.get_spending_transaction(spending_id):
        raise HTTPException(status_code=404, detail="Spending transaction not found")
    db.update_spending_transaction(spending_id, category=body.category)
    return {"id": spending_id, "category": body.category}


@router.delete("/{spending_id}", response_model=dict)
async def delete_spending(
    spending_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a spending transaction (hard delete)."""
    if not db.delete_spending_transaction(spending_id):
        raise HTTPException(status_code=404, detail="Spending transaction not found")
    return {"deleted": True, "id": spending_id}


@router.post("/rescan-transfers", response_model=dict)
```

- [ ] **Step 6: Add failing-then-passing API tests**

In `tests/unit/test_spending_api.py`, append at the end of the file:

```python
def test_delete_spending_transaction(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.delete(f"/api/v1/spending/{tx_id}", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "id": tx_id}
    assert client.get("/api/v1/spending/", headers=HEADERS).json() == []


def test_delete_spending_transaction_missing(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.delete("/api/v1/spending/999999", headers=HEADERS)
    assert r.status_code == 404
```

- [ ] **Step 7: Run tests, format, lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py tests/unit/test_spending_api.py -v 2>&1 | tail -30
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/database.py portf_server/routers/spending.py tests/unit/test_spending_db.py tests/unit/test_spending_api.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/database.py portf_server/routers/spending.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: all pass, no lint errors.

- [ ] **Step 8: Add the JS API client method**

In `web_client/js/pfm_core.js`, find the existing `getSpendingSummary` method (the last of the spending-related methods added previously, ending with a `},`). Insert `deleteSpendingTransaction` immediately after it:

```javascript
        async deleteSpendingTransaction(id) {
            const response = await fetch(this.baseURL + '/api/v1/spending/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to delete transaction');
            return response.json();
        },
```

- [ ] **Step 9: Commit**

```bash
git add portf_manager/database.py portf_server/routers/spending.py tests/unit/test_spending_db.py tests/unit/test_spending_api.py web_client/js/pfm_core.js
git commit -m "feat: add DELETE /api/v1/spending/{id} endpoint and delete_spending_transaction helper

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Bank account creation on the Brokers page

**Files:**
- Modify: `portf_server/routers/portfolios.py`
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_features.js`

Currently a bank-type portfolio (`account_type='bank'`) can only be created indirectly via the Spending page's import-modal "New account name" field. This adds a direct "Account type" choice to the existing Add/Edit Broker modal. Editing an existing portfolio's account_type is out of scope (the field is disabled in edit mode) — only creation.

- [ ] **Step 1: Add `account_type` to `PortfolioCreate` and wire it through**

In `portf_server/routers/portfolios.py`, the import block at the top:

```python
import time
from typing import Optional
```

Replace with:

```python
import time
from typing import Literal, Optional
```

Find `PortfolioCreate`:

```python
class PortfolioCreate(BaseModel):
    """Schema for creating a portfolio."""

    name: str = Field(..., description="Portfolio name")
    base_currency: str = Field("EUR", description="Base currency")
    entity_id: Optional[int] = Field(None, description="Linked entity/broker ID")
    description: Optional[str] = Field(None, description="Portfolio description")
    website: Optional[str] = Field(None, description="Broker website URL")
```

Replace with (adds `account_type`):

```python
class PortfolioCreate(BaseModel):
    """Schema for creating a portfolio."""

    name: str = Field(..., description="Portfolio name")
    base_currency: str = Field("EUR", description="Base currency")
    entity_id: Optional[int] = Field(None, description="Linked entity/broker ID")
    description: Optional[str] = Field(None, description="Portfolio description")
    website: Optional[str] = Field(None, description="Broker website URL")
    account_type: Literal["brokerage", "bank"] = Field(
        "brokerage",
        description="'brokerage' for investment accounts, 'bank' for checking/savings accounts",
    )
```

Find `create_portfolio`:

```python
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    portfolio: PortfolioCreate,
    database: Database = Depends(get_database),
):
    """Create a new portfolio."""
    try:
        portfolio_id = database.create_portfolio(
            name=portfolio.name,
            base_currency=portfolio.base_currency,
            entity_id=portfolio.entity_id,
            description=portfolio.description,
        )
        return {
            "id": portfolio_id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "entity_id": portfolio.entity_id,
            "description": portfolio.description,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create portfolio: {str(e)}",
        )
```

Replace with (passes `account_type` through, includes it in the response):

```python
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    portfolio: PortfolioCreate,
    database: Database = Depends(get_database),
):
    """Create a new portfolio."""
    try:
        portfolio_id = database.create_portfolio(
            name=portfolio.name,
            base_currency=portfolio.base_currency,
            entity_id=portfolio.entity_id,
            description=portfolio.description,
            account_type=portfolio.account_type,
        )
        return {
            "id": portfolio_id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "entity_id": portfolio.entity_id,
            "description": portfolio.description,
            "account_type": portfolio.account_type,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create portfolio: {str(e)}",
        )
```

- [ ] **Step 2: Add a test**

Find `tests/unit/test_api_routers.py` (or wherever existing portfolio-creation API tests live — grep `def test_create_portfolio` first to find the right file) and add, following that file's existing `_make_client`-style fixture pattern:

```python
def test_create_bank_account_portfolio(tmp_path):
    client, db = _make_client(tmp_path)
    r = client.post(
        "/api/v1/portfolios/",
        json={"name": "Example Bank Checking", "account_type": "bank"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["account_type"] == "bank"
    listed = client.get("/api/v1/portfolios/", headers=HEADERS).json()
    created = next(p for p in listed if p["name"] == "Example Bank Checking")
    assert created["account_type"] == "bank"


def test_create_portfolio_defaults_to_brokerage(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/portfolios/", json={"name": "Example Broker"}, headers=HEADERS
    )
    assert r.status_code == 201
    assert r.json()["account_type"] == "brokerage"
```

(If the existing test file's client-fixture helper or header constant has different names than `_make_client`/`HEADERS`, use whatever that file already uses — match its established pattern exactly rather than the names shown here.)

- [ ] **Step 3: Add the account-type selector to the Add/Edit Broker modal**

In `web_client/index.html`, find the `portfolioModal` form's Name field:

```html
                        <div class="mb-3">
                            <label class="form-label" for="portfolioName">Name *</label>
                            <input type="text" class="form-control" id="portfolioName" placeholder="e.g. IndexaCapital" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label" for="portfolioCurrency">Currency</label>
                            <input type="text" class="form-control" id="portfolioCurrency" value="EUR" maxlength="3">
                        </div>
```

Insert an account-type selector between them:

```html
                        <div class="mb-3">
                            <label class="form-label" for="portfolioName">Name *</label>
                            <input type="text" class="form-control" id="portfolioName" placeholder="e.g. IndexaCapital" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label" for="portfolioAccountType">Account type</label>
                            <select class="form-select" id="portfolioAccountType">
                                <option value="brokerage">Brokerage / investment account</option>
                                <option value="bank">Bank account (checking/savings — for Spending tracking)</option>
                            </select>
                            <div class="form-text" id="portfolioAccountTypeHint"></div>
                        </div>
                        <div class="mb-3">
                            <label class="form-label" for="portfolioCurrency">Currency</label>
                            <input type="text" class="form-control" id="portfolioCurrency" value="EUR" maxlength="3">
                        </div>
```

- [ ] **Step 4: Wire the selector in `pfm_features.js`**

In `web_client/js/pfm_features.js`, find the `addBtn` click handler in `setupPortfoliosPage`:

```javascript
    addBtn.addEventListener('click', () => {
        document.getElementById('portfolioModalTitle').textContent = 'Add Broker';
        document.getElementById('portfolioEditId').value = '';
        document.getElementById('portfolioName').value = '';
        document.getElementById('portfolioCurrency').value = 'EUR';
        document.getElementById('portfolioDescription').value = '';
        document.getElementById('portfolioWebsite').value = '';
        bsModal.show();
    });
```

Replace with (resets and enables the account-type selector for new portfolios):

```javascript
    addBtn.addEventListener('click', () => {
        document.getElementById('portfolioModalTitle').textContent = 'Add Broker';
        document.getElementById('portfolioEditId').value = '';
        document.getElementById('portfolioName').value = '';
        document.getElementById('portfolioCurrency').value = 'EUR';
        document.getElementById('portfolioDescription').value = '';
        document.getElementById('portfolioWebsite').value = '';
        const typeSel = document.getElementById('portfolioAccountType');
        if (typeSel) { typeSel.value = 'brokerage'; typeSel.disabled = false; }
        const typeHint = document.getElementById('portfolioAccountTypeHint');
        if (typeHint) typeHint.textContent = '';
        bsModal.show();
    });
```

Find the form submit handler:

```javascript
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id   = document.getElementById('portfolioEditId').value;
        const data = {
            name: document.getElementById('portfolioName').value.trim(),
            base_currency: document.getElementById('portfolioCurrency').value.trim() || 'EUR',
            description: document.getElementById('portfolioDescription').value.trim() || null,
            website: document.getElementById('portfolioWebsite').value.trim() || null,
        };
        try {
            if (id) {
                await window.apiClient.updatePortfolio(parseInt(id), data);
            } else {
                await window.apiClient.createPortfolio(data);
            }
```

Replace with (only sends `account_type` on create, since it can't be changed after the fact):

```javascript
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id   = document.getElementById('portfolioEditId').value;
        const data = {
            name: document.getElementById('portfolioName').value.trim(),
            base_currency: document.getElementById('portfolioCurrency').value.trim() || 'EUR',
            description: document.getElementById('portfolioDescription').value.trim() || null,
            website: document.getElementById('portfolioWebsite').value.trim() || null,
        };
        if (!id) {
            const typeSel = document.getElementById('portfolioAccountType');
            data.account_type = typeSel ? typeSel.value : 'brokerage';
        }
        try {
            if (id) {
                await window.apiClient.updatePortfolio(parseInt(id), data);
            } else {
                await window.apiClient.createPortfolio(data);
            }
```

Find `window.editPortfolio`:

```javascript
window.editPortfolio = function(id, name, currency, description, website) {
    document.getElementById('portfolioModalTitle').textContent = 'Edit Broker';
    document.getElementById('portfolioEditId').value           = id;
    document.getElementById('portfolioName').value             = name;
    document.getElementById('portfolioCurrency').value         = currency;
    document.getElementById('portfolioDescription').value      = description === 'null' ? '' : (description || '');
    document.getElementById('portfolioWebsite').value          = website === 'null' ? '' : (website || '');
    bootstrap.Modal.getOrCreateInstance(document.getElementById('portfolioModal')).show();
};
```

Replace with (disables the account-type selector when editing, since changing it isn't supported):

```javascript
window.editPortfolio = function(id, name, currency, description, website) {
    document.getElementById('portfolioModalTitle').textContent = 'Edit Broker';
    document.getElementById('portfolioEditId').value           = id;
    document.getElementById('portfolioName').value             = name;
    document.getElementById('portfolioCurrency').value         = currency;
    document.getElementById('portfolioDescription').value      = description === 'null' ? '' : (description || '');
    document.getElementById('portfolioWebsite').value          = website === 'null' ? '' : (website || '');
    const typeSel = document.getElementById('portfolioAccountType');
    if (typeSel) typeSel.disabled = true;
    const typeHint = document.getElementById('portfolioAccountTypeHint');
    if (typeHint) typeHint.textContent = "Account type can't be changed after creation.";
    bootstrap.Modal.getOrCreateInstance(document.getElementById('portfolioModal')).show();
};
```

- [ ] **Step 5: Run tests, format, lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/portfolios.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/portfolios.py --max-line-length=88 --extend-ignore=E203,W503,E501
node --check web_client/js/pfm_features.js
```

Expected: all tests pass (count higher than before), no lint errors, valid JS syntax.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/portfolios.py web_client/index.html web_client/js/pfm_features.js tests/unit/test_api_routers.py
git commit -m "feat: add bank-account creation to the Brokers page Add/Edit modal

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

(Adjust the `git add` test-file path to whichever file Step 2 actually landed the new tests in.)

---

## Task 3: Bulk select, recategorize, and delete on the Spending page

**Files:**
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_features.js`

**Depends on Task 1** (`deleteSpendingTransaction` API client method + backend endpoint).

- [ ] **Step 1: Add a checkbox column to the transactions table**

In `web_client/index.html`, find the Spending page's Transactions table:

```html
                    <div class="card mb-3">
                        <div class="card-header fw-semibold">Transactions</div>
                        <div class="table-responsive">
                            <table class="table table-hover mb-0">
                                <thead><tr>
                                    <th class="ps-3" data-key="date" data-type="date">Date</th>
                                    <th data-key="portfolio_name" data-type="text">Account</th>
                                    <th data-key="description" data-type="text">Description</th>
                                    <th data-key="category" data-type="text">Category</th>
                                    <th class="text-end" data-key="amount" data-type="num">Amount</th>
                                    <th class="pe-3"></th>
                                </tr></thead>
                                <tbody id="spTxBody"><tr><td colspan="6" class="text-center text-muted py-3">No transactions yet. Import a bank statement to get started.</td></tr></tbody>
                            </table>
                        </div>
                    </div>
```

Replace with (adds a checkbox column + header select-all, and a bulk-actions bar shown above the table when rows are selected):

```html
                    <div class="card mb-3">
                        <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                            <span>Transactions</span>
                        </div>
                        <div id="spBulkBar" class="card-body py-2 border-bottom bg-light-subtle" style="display:none;">
                            <div class="d-flex flex-wrap align-items-center gap-2">
                                <span class="small text-muted"><span id="spSelectedCount">0</span> selected</span>
                                <select class="form-select form-select-sm w-auto" id="spBulkCategorySelect"></select>
                                <button class="btn btn-sm btn-outline-primary" id="spBulkRecategorizeBtn">Set category</button>
                                <button class="btn btn-sm btn-outline-danger ms-auto" id="spBulkDeleteBtn"><i class="bi bi-trash me-1"></i>Delete selected</button>
                            </div>
                        </div>
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
```

Note the `colspan` on the empty-state row changed from 6 to 7 (one more column now).

- [ ] **Step 2: Add the checkbox column to `_renderSpendingTable` and wire bulk actions**

In `web_client/js/pfm_features.js`, find `_renderSpendingTable`:

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
            { key: 'date', type: 'date' }, { key: 'portfolio_name', type: 'text' },
            { key: 'description', type: 'text' }, { key: 'category', type: 'text' },
            { key: 'amount', type: 'num' }, { key: null },
        ],
        getRows: () => window._spFilteredRows || [],
        renderRows: (sorted, tbody) => {
            const categories = [...new Set(['uncategorized', 'Transfer', ...rows.map(r => r.category)])];
            tbody.innerHTML = sorted.length ? sorted.map(r => `
                <tr>
                    <td class="ps-3">${Fmt.date(r.date)}</td>
                    <td>${esc(r.portfolio_name || '')}</td>
                    <td>${esc(r.description)}</td>
                    <td>
                        <select class="form-select form-select-sm d-inline-block" style="width:auto;" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
                            ${categories.map(c => `<option value="${esc(c)}" ${c === r.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
                        </select>
                        ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                    </td>
                    <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
                    <td class="pe-3"></td>
                </tr>`).join('') : '<tr><td colspan="6" class="text-center text-muted py-3">No transactions match the current filters.</td></tr>';
        },
        prefsKey: 'spending',
    });
    window._spFilteredRows = filtered;
    window._spTable.refresh();
}
```

Replace with (adds a checkbox cell per row, a select-all checkbox handler, and re-wires the bulk bar after every render since row identity changes on refresh):

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
            const categories = [...new Set(['uncategorized', 'Transfer', ...rows.map(r => r.category)])];
            tbody.innerHTML = sorted.length ? sorted.map(r => `
                <tr>
                    <td class="ps-3"><input type="checkbox" class="form-check-input sp-row-check" data-id="${r.id}"></td>
                    <td>${Fmt.date(r.date)}</td>
                    <td>${esc(r.portfolio_name || '')}</td>
                    <td>${esc(r.description)}</td>
                    <td>
                        <select class="form-select form-select-sm d-inline-block" style="width:auto;" onchange="window.updateSpendingRowCategory(${r.id}, this.value)">
                            ${categories.map(c => `<option value="${esc(c)}" ${c === r.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}
                        </select>
                        ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                    </td>
                    <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
                    <td class="pe-3"></td>
                </tr>`).join('') : '<tr><td colspan="7" class="text-center text-muted py-3">No transactions match the current filters.</td></tr>';
            _populateSpBulkCategorySelect(categories);
            _updateSpBulkBar();
        },
        prefsKey: 'spending',
    });
    window._spFilteredRows = filtered;
    window._spTable.refresh();
    _wireSpBulkActions();
}

function _populateSpBulkCategorySelect(categories) {
    const sel = document.getElementById('spBulkCategorySelect');
    if (!sel) return;
    sel.innerHTML = categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
}

function _selectedSpendingIds() {
    return Array.from(document.querySelectorAll('#spTxBody .sp-row-check:checked'))
        .map(cb => parseInt(cb.dataset.id, 10));
}

function _updateSpBulkBar() {
    const ids = _selectedSpendingIds();
    const bar = document.getElementById('spBulkBar');
    const count = document.getElementById('spSelectedCount');
    if (count) count.textContent = String(ids.length);
    if (bar) bar.style.display = ids.length > 0 ? '' : 'none';
    const selectAll = document.getElementById('spSelectAll');
    const rowChecks = document.querySelectorAll('#spTxBody .sp-row-check');
    if (selectAll) selectAll.checked = rowChecks.length > 0 && ids.length === rowChecks.length;
}

function _wireSpBulkActions() {
    document.querySelectorAll('#spTxBody .sp-row-check').forEach(cb => {
        cb.addEventListener('change', _updateSpBulkBar);
    });
    const selectAll = document.getElementById('spSelectAll');
    if (selectAll && !selectAll.dataset.wired) {
        selectAll.dataset.wired = '1';
        selectAll.addEventListener('change', () => {
            document.querySelectorAll('#spTxBody .sp-row-check').forEach(cb => { cb.checked = selectAll.checked; });
            _updateSpBulkBar();
        });
    }
    const recatBtn = document.getElementById('spBulkRecategorizeBtn');
    if (recatBtn && !recatBtn.dataset.wired) {
        recatBtn.dataset.wired = '1';
        recatBtn.addEventListener('click', async () => {
            const ids = _selectedSpendingIds();
            const category = document.getElementById('spBulkCategorySelect')?.value;
            if (!ids.length || !category) return;
            recatBtn.disabled = true;
            try {
                for (const id of ids) {
                    await window.apiClient.updateSpendingCategory(id, category);
                }
                await _refreshSpendingData();
            } catch (err) { alert('Error: ' + err.message); }
            recatBtn.disabled = false;
        });
    }
    const delBtn = document.getElementById('spBulkDeleteBtn');
    if (delBtn && !delBtn.dataset.wired) {
        delBtn.dataset.wired = '1';
        delBtn.addEventListener('click', async () => {
            const ids = _selectedSpendingIds();
            if (!ids.length) return;
            if (!confirm(`Delete ${ids.length} transaction(s)? This cannot be undone.`)) return;
            delBtn.disabled = true;
            try {
                for (const id of ids) {
                    await window.apiClient.deleteSpendingTransaction(id);
                }
                await _refreshSpendingData();
            } catch (err) { alert('Error: ' + err.message); }
            delBtn.disabled = false;
        });
    }
}
```

`_wireSpBulkActions` is called on every `_renderSpendingTable()` refresh (row checkboxes are recreated on every render, so they need re-binding each time — unlike the other one-time-wired controls in this file); the `dataset.wired` guards on `selectAll`/`recatBtn`/`delBtn` still prevent those three from double-binding across repeated calls, since those specific elements persist across re-renders.

- [ ] **Step 3: Add a unit test for the pure selection-counting logic (optional but recommended)**

Since `_selectedSpendingIds`/`_updateSpBulkBar` are DOM-coupled (not pure), no new pure-function test is required here — skip adding one rather than forcing a DOM-dependent test into the Node test runner, which this project doesn't set up jsdom for. Confirm this by checking `web_client/js/tests/web_client.test.mjs`'s existing `loadAppIntoContext()` helper doesn't already provide a DOM (if it does, a lightweight test wiring a few checkboxes and calling `_selectedSpendingIds` would be worth adding — check before skipping).

- [ ] **Step 4: Run JS tests, syntax check**

```bash
node --test web_client/js/tests/ 2>&1 | tail -20
node --check web_client/js/pfm_features.js
```

Expected: existing tests still pass (no new ones added unless Step 3 found a DOM harness), valid syntax.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: add bulk select, recategorize, and delete to the Spending transactions table

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Multi-account selection for investment-transaction CSV export

**Files:**
- Modify: `portf_server/routers/exports.py`
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_features.js`
- Modify/Create: test file for `/api/v1/export/csv` (check `tests/unit/` for an existing exports test file first; if none exists, create `tests/unit/test_exports.py`)

This is about the existing investment-transaction export (`GET /api/v1/export/csv`), not spending-transaction export — the user explicitly did not ask for the latter this round.

- [ ] **Step 1: Accept multiple `portfolio_id` query params**

In `portf_server/routers/exports.py`, the import line:

```python
from typing import Optional
```

Replace with:

```python
from typing import List, Optional
```

Find `export_transactions_csv`:

```python
@router.get("/csv")
async def export_transactions_csv(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Download all transactions as a CSV file."""
    if portfolio_id is not None:
        transactions = db.get_transactions_by_portfolio(portfolio_id)
    else:
        transactions = db.get_all_transactions(limit=100_000)
```

Replace with (FastAPI binds repeated `?portfolio_id=1&portfolio_id=2` query params to a `List[int]`):

```python
@router.get("/csv")
async def export_transactions_csv(
    portfolio_id: Optional[List[int]] = Query(
        default=None, description="Filter by one or more portfolio IDs"
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Download all transactions as a CSV file."""
    if portfolio_id:
        transactions = []
        for pid in portfolio_id:
            transactions.extend(db.get_transactions_by_portfolio(pid))
    else:
        transactions = db.get_all_transactions(limit=100_000)
```

- [ ] **Step 2: Add a test**

Check whether `tests/unit/` already has a test file covering `/api/v1/export/csv` (grep `def test.*export.*csv` across `tests/unit/`). If one exists, append to it using its existing fixture pattern; otherwise create `tests/unit/test_exports.py`:

```python
"""Tests for the export router's multi-portfolio CSV export."""

import pytest
from fastapi.testclient import TestClient
from portf_manager.database import Database

_TEST_API_KEY = "test-key-exports-abc123"
HEADERS = {"X-API-Key": _TEST_API_KEY}


def _make_client(tmp_path):
    from portf_server.app import app
    from portf_server.dependencies import get_database, get_api_key_manager
    from portf_server.auth_middleware import APIKeyManager

    db_instance = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager(db_instance)
    km.create_api_key(key_name="test", description="test key", raw_key=_TEST_API_KEY)
    from portf_server.app import app as _app

    _app.dependency_overrides[get_database] = lambda: db_instance
    _app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(_app), db_instance


def test_export_csv_multiple_portfolios(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Broker A")
    pid_b = db.create_portfolio("Broker B")
    asset_id = db.create_asset("EXAMPLE", "Example Corp", "stock", currency="EUR")
    db.create_transaction(
        portfolio_id=pid_a, asset_id=asset_id, transaction_type="buy",
        transaction_date="2026-01-05", quantity=1, price=10.0, currency="EUR",
    )
    db.create_transaction(
        portfolio_id=pid_b, asset_id=asset_id, transaction_type="buy",
        transaction_date="2026-01-06", quantity=2, price=20.0, currency="EUR",
    )
    r = client.get(
        f"/api/v1/export/csv?portfolio_id={pid_a}&portfolio_id={pid_b}", headers=HEADERS
    )
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert body.count("EXAMPLE") == 2


def test_export_csv_single_portfolio_still_works(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Broker A")
    pid_b = db.create_portfolio("Broker B")
    asset_id = db.create_asset("EXAMPLE", "Example Corp", "stock", currency="EUR")
    db.create_transaction(
        portfolio_id=pid_a, asset_id=asset_id, transaction_type="buy",
        transaction_date="2026-01-05", quantity=1, price=10.0, currency="EUR",
    )
    db.create_transaction(
        portfolio_id=pid_b, asset_id=asset_id, transaction_type="buy",
        transaction_date="2026-01-06", quantity=2, price=20.0, currency="EUR",
    )
    r = client.get(f"/api/v1/export/csv?portfolio_id={pid_a}", headers=HEADERS)
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert body.count("EXAMPLE") == 1
```

Check `db.create_transaction`'s actual required kwargs against `portf_manager/database.py` before finalizing this test — the exact parameter names/order used above are illustrative of the shape, verify against the real signature (CLAUDE.md notes `create_transaction()` requires `portfolio_id` and `asset_id`, resolved via `get_asset_by_symbol()`/`create_asset()`, and to always pass `currency=`).

- [ ] **Step 3: Add multi-select UI to the Export tab**

In `web_client/index.html`, find the Export card's button row:

```html
                                <div class="card-body">
                                    <p class="text-muted small mb-3">Download all transactions. CSV is compatible with spreadsheets; PDT (XLSX) is the Portfolio Dividend Tracker format (includes bookings).</p>
                                    <div class="d-flex gap-3 flex-wrap mb-3">
                                        <button class="btn btn-sm btn-outline-primary" id="ioExportCsvBtn">
                                            <i class="bi bi-filetype-csv me-2"></i>Export CSV
                                        </button>
```

Replace with (adds a multi-select account list above the buttons):

```html
                                <div class="card-body">
                                    <p class="text-muted small mb-3">Download all transactions. CSV is compatible with spreadsheets; PDT (XLSX) is the Portfolio Dividend Tracker format (includes bookings).</p>
                                    <div class="mb-3" style="max-width:320px;">
                                        <label class="form-label small mb-1" for="ioExportCsvPortfolios">Accounts (CSV export — leave empty for all)</label>
                                        <select class="form-select form-select-sm" id="ioExportCsvPortfolios" multiple size="4"></select>
                                    </div>
                                    <div class="d-flex gap-3 flex-wrap mb-3">
                                        <button class="btn btn-sm btn-outline-primary" id="ioExportCsvBtn">
                                            <i class="bi bi-filetype-csv me-2"></i>Export CSV
                                        </button>
```

- [ ] **Step 4: Wire the multi-select and build the query string**

In `web_client/js/pfm_features.js`, find:

```javascript
    // --- Export section ---
    const ioCsvBtn = document.getElementById('ioExportCsvBtn');
    const ioPdtBtn = document.getElementById('ioExportPdtBtn');
    if (ioCsvBtn) ioCsvBtn.addEventListener('click', async () => {
        try {
            await window.apiClient.downloadBlob(window.apiClient.baseURL + '/api/v1/export/csv', 'transactions.csv');
        } catch (err) { alert('Export error: ' + err.message); }
    });
```

Replace with (populates the multi-select from `getPortfolios()` and appends repeated `portfolio_id` params for whatever's selected):

```javascript
    // --- Export section ---
    const ioCsvBtn = document.getElementById('ioExportCsvBtn');
    const ioPdtBtn = document.getElementById('ioExportPdtBtn');
    const ioCsvPortfolios = document.getElementById('ioExportCsvPortfolios');
    if (ioCsvPortfolios && ioCsvPortfolios.options.length === 0) {
        (async () => {
            try {
                const portfolios = await window.apiClient.getPortfolios();
                portfolios.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id; opt.textContent = p.name;
                    ioCsvPortfolios.appendChild(opt);
                });
            } catch (e) { /* silent */ }
        })();
    }
    if (ioCsvBtn) ioCsvBtn.addEventListener('click', async () => {
        try {
            const selectedIds = ioCsvPortfolios
                ? Array.from(ioCsvPortfolios.selectedOptions).map(o => o.value)
                : [];
            const qs = selectedIds.map(id => `portfolio_id=${encodeURIComponent(id)}`).join('&');
            const url = window.apiClient.baseURL + '/api/v1/export/csv' + (qs ? '?' + qs : '');
            await window.apiClient.downloadBlob(url, 'transactions.csv');
        } catch (err) { alert('Export error: ' + err.message); }
    });
```

- [ ] **Step 5: Run tests, format, lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/exports.py tests/unit/test_exports.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/exports.py --max-line-length=88 --extend-ignore=E203,W503,E501
node --check web_client/js/pfm_features.js
```

(Adjust the `black`/test paths if Step 2 appended to an existing file instead of creating `test_exports.py`.)

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/exports.py web_client/index.html web_client/js/pfm_features.js tests/unit/test_exports.py
git commit -m "feat: support multi-account selection for investment-transaction CSV export

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

(Adjust the test file path in `git add` to match Step 2's actual location.)

---

## Task 5: Add "Import Bank Statement" to the Import/Export page

**Files:**
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_features.js`

Reuses the existing Spending-page import logic (`uploadBankStatement`/`suggestSpendingCategories`/`saveSpendingTransactions`, all already in `pfm_core.js`) rather than the investment-import machinery in this same page (`ioFileBroker`/`fileParseBtn`/etc.), which is shaped entirely around asset/quantity/price rows and isn't compatible. `_wireSpendingImportModal`/`_renderSpImportPreview` are refactored to take a config of element ids instead of hard-coded ones, so the exact same functions drive both the Spending-page modal (unchanged behavior) and this new card.

- [ ] **Step 1: Add a new card to the Import/Export page's Import tab**

In `web_client/index.html`, find the end of the "Import from Text (AI Extraction)" card, right before the closing of the `row g-4` inside `#ioTabImport`:

```html
                        </div>
                            </div>
                        </div><!-- /#ioTabImport -->
```

Replace with (adds a third card):

```html
                        </div>

                        <!-- Import Bank Statement (Spending) -->
                        <div class="col-12 col-lg-6">
                            <div class="card h-100">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-wallet2 me-2"></i>Import Bank Statement (Spending)
                                </div>
                                <div class="card-body d-flex flex-column">
                                    <p class="text-muted small mb-2">Categorized day-to-day spending, tracked separately from investment transactions. See the <strong>Spending</strong> page for the full view.</p>
                                    <div class="row g-2 mb-2">
                                        <div class="col-12">
                                            <select class="form-select form-select-sm" id="ioSpImportAccountSelect"><option value="">— New account —</option></select>
                                        </div>
                                        <div class="col-12">
                                            <input class="form-control form-control-sm" id="ioSpImportAccountName" placeholder="New account name (if not selected above)">
                                        </div>
                                    </div>
                                    <div class="mb-2">
                                        <input type="file" class="form-control form-control-sm" id="ioSpImportFile" accept=".csv">
                                        <div class="form-text">Columns: date, description, amount (optional: balance, currency). <a href="#" id="ioSpDownloadTemplate">Download template</a></div>
                                    </div>
                                    <div id="ioSpImportPreview" class="flex-grow-1"></div>
                                    <div class="d-flex gap-2 mt-auto pt-3 flex-wrap">
                                        <button class="btn btn-sm btn-outline-secondary" id="ioSpParseBtn">Parse</button>
                                        <button class="btn btn-sm btn-outline-primary" id="ioSpSuggestBtn" style="display:none;">Suggest categories (AI)</button>
                                        <button class="btn btn-sm btn-primary" id="ioSpSaveBtn" style="display:none;">Save</button>
                                        <div class="small text-muted w-100" id="ioSpImportStatus"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                            </div>
                        </div><!-- /#ioTabImport -->
```

- [ ] **Step 2: Parameterize `_wireSpendingImportModal` / `_renderSpImportPreview` / `_spDupControl` / `_spDupAction`**

In `web_client/js/pfm_features.js`, find the full block from `_wireSpendingImportModal` through `_renderSpImportPreview` (already read in full during planning — this is the exact current content):

```javascript
function _wireSpendingImportModal() {
    const parseBtn = document.getElementById('spParseBtn');
    const suggestBtn = document.getElementById('spSuggestBtn');
    const saveBtn = document.getElementById('spSaveBtn');
    const preview = document.getElementById('spImportPreview');
    const status = document.getElementById('spImportStatus');
    const templateLink = document.getElementById('spDownloadTemplate');
    if (templateLink && !templateLink.dataset.wired) {
        templateLink.dataset.wired = '1';
        templateLink.addEventListener('click', (e) => { e.preventDefault(); downloadGenericBankTemplate(); });
    }
    if (parseBtn && !parseBtn.dataset.wired) {
        parseBtn.dataset.wired = '1';
        parseBtn.addEventListener('click', async () => {
            const fileInput = document.getElementById('spImportFile');
            const file = fileInput.files[0];
            if (!file) { status.textContent = 'Choose a file first.'; return; }
            const accountId = document.getElementById('spImportAccountSelect').value || null;
            const accountName = document.getElementById('spImportAccountName').value.trim() || null;
            status.textContent = 'Parsing…';
            try {
                const result = await window.apiClient.uploadBankStatement(file, accountId, accountName);
                window._spImportPreview = result;
                _renderSpImportPreview(result);
                status.textContent = `${result.rows.length} row(s) parsed, ${result.duplicate_count} duplicate(s), ${result.skipped_count} skipped.`;
                suggestBtn.style.display = result.rows.some(r => r.category === 'uncategorized') ? 'inline-block' : 'none';
                saveBtn.style.display = 'inline-block';
            } catch (err) { status.textContent = err.message; }
        });
    }
    if (suggestBtn && !suggestBtn.dataset.wired) {
        suggestBtn.dataset.wired = '1';
        suggestBtn.addEventListener('click', async () => {
            const preview_ = window._spImportPreview;
            if (!preview_) return;
            const uncategorized = preview_.rows.filter(r => r.category === 'uncategorized');
            status.textContent = 'Asking AI for category suggestions…';
            try {
                const { suggestions } = await window.apiClient.suggestSpendingCategories(uncategorized);
                const byDesc = new Map(suggestions.map(s => [s.description, s]));
                preview_.rows.forEach(r => {
                    const s = byDesc.get(r.description);
                    if (s && r.category === 'uncategorized') {
                        r.category = s.category;
                        r._suggestedPattern = s.suggested_pattern;
                        r._aiSuggested = true;
                    }
                });
                _renderSpImportPreview(preview_);
                status.textContent = 'Suggestions applied — review and edit before saving.';
            } catch (err) { status.textContent = err.message; }
        });
    }
    if (saveBtn && !saveBtn.dataset.wired) {
        saveBtn.dataset.wired = '1';
        saveBtn.addEventListener('click', async () => {
            const preview_ = window._spImportPreview;
            if (!preview_) return;
            status.textContent = 'Saving…';
            try {
                // AI-accepted suggestions become permanent rules so future imports auto-match.
                const newRules = preview_.rows.filter(r => r._aiSuggested && r._suggestedPattern);
                for (const r of newRules) {
                    await window.apiClient.createSpendingRule(r._suggestedPattern, r.category);
                }
                const result = await window.apiClient.saveSpendingTransactions(
                    preview_.account_portfolio_id, preview_.rows, _spDupAction()
                );
                status.textContent = `Saved ${result.saved}, ${result.duplicates_skipped} duplicate(s) skipped, ${result.transfers_linked} transfer(s) linked.`;
                preview.innerHTML = '';
                saveBtn.style.display = 'none';
                suggestBtn.style.display = 'none';
                await _refreshSpendingData();
            } catch (err) { status.textContent = err.message; }
        });
    }
}
```

Replace the function signature and every hard-coded element-id lookup with an `ids` config parameter (default value reproduces the exact original Spending-page ids, so its own call site needs no change):

```javascript
function _wireSpendingImportModal(ids) {
    ids = ids || {
        parseBtn: 'spParseBtn', suggestBtn: 'spSuggestBtn', saveBtn: 'spSaveBtn',
        preview: 'spImportPreview', status: 'spImportStatus', templateLink: 'spDownloadTemplate',
        accountSelect: 'spImportAccountSelect', accountName: 'spImportAccountName',
        dupSelectId: 'spDuplicateAction', onSaved: () => _refreshSpendingData(),
    };
    const parseBtn = document.getElementById(ids.parseBtn);
    const suggestBtn = document.getElementById(ids.suggestBtn);
    const saveBtn = document.getElementById(ids.saveBtn);
    const preview = document.getElementById(ids.preview);
    const status = document.getElementById(ids.status);
    const templateLink = document.getElementById(ids.templateLink);
    if (templateLink && !templateLink.dataset.wired) {
        templateLink.dataset.wired = '1';
        templateLink.addEventListener('click', (e) => { e.preventDefault(); downloadGenericBankTemplate(); });
    }
    if (parseBtn && !parseBtn.dataset.wired) {
        parseBtn.dataset.wired = '1';
        parseBtn.addEventListener('click', async () => {
            const fileInput = document.getElementById(ids.fileInput || 'spImportFile');
            const file = fileInput.files[0];
            if (!file) { status.textContent = 'Choose a file first.'; return; }
            const accountId = document.getElementById(ids.accountSelect).value || null;
            const accountName = document.getElementById(ids.accountName).value.trim() || null;
            status.textContent = 'Parsing…';
            try {
                const result = await window.apiClient.uploadBankStatement(file, accountId, accountName);
                window._spImportPreview = result;
                _renderSpImportPreview(result, ids);
                status.textContent = `${result.rows.length} row(s) parsed, ${result.duplicate_count} duplicate(s), ${result.skipped_count} skipped.`;
                suggestBtn.style.display = result.rows.some(r => r.category === 'uncategorized') ? 'inline-block' : 'none';
                saveBtn.style.display = 'inline-block';
            } catch (err) { status.textContent = err.message; }
        });
    }
    if (suggestBtn && !suggestBtn.dataset.wired) {
        suggestBtn.dataset.wired = '1';
        suggestBtn.addEventListener('click', async () => {
            const preview_ = window._spImportPreview;
            if (!preview_) return;
            const uncategorized = preview_.rows.filter(r => r.category === 'uncategorized');
            status.textContent = 'Asking AI for category suggestions…';
            try {
                const { suggestions } = await window.apiClient.suggestSpendingCategories(uncategorized);
                const byDesc = new Map(suggestions.map(s => [s.description, s]));
                preview_.rows.forEach(r => {
                    const s = byDesc.get(r.description);
                    if (s && r.category === 'uncategorized') {
                        r.category = s.category;
                        r._suggestedPattern = s.suggested_pattern;
                        r._aiSuggested = true;
                    }
                });
                _renderSpImportPreview(preview_, ids);
                status.textContent = 'Suggestions applied — review and edit before saving.';
            } catch (err) { status.textContent = err.message; }
        });
    }
    if (saveBtn && !saveBtn.dataset.wired) {
        saveBtn.dataset.wired = '1';
        saveBtn.addEventListener('click', async () => {
            const preview_ = window._spImportPreview;
            if (!preview_) return;
            status.textContent = 'Saving…';
            try {
                // AI-accepted suggestions become permanent rules so future imports auto-match.
                const newRules = preview_.rows.filter(r => r._aiSuggested && r._suggestedPattern);
                for (const r of newRules) {
                    await window.apiClient.createSpendingRule(r._suggestedPattern, r.category);
                }
                const result = await window.apiClient.saveSpendingTransactions(
                    preview_.account_portfolio_id, preview_.rows, _spDupAction(ids.dupSelectId)
                );
                status.textContent = `Saved ${result.saved}, ${result.duplicates_skipped} duplicate(s) skipped, ${result.transfers_linked} transfer(s) linked.`;
                preview.innerHTML = '';
                saveBtn.style.display = 'none';
                suggestBtn.style.display = 'none';
                await ids.onSaved();
            } catch (err) { status.textContent = err.message; }
        });
    }
}
```

Find `_spDupControl`, `_spDupAction`, and `_renderSpImportPreview`:

```javascript
function _spDupControl(rows) {
    const dupCount = (rows || []).filter(r => r.is_duplicate).length;
    if (dupCount === 0) return '';
    return `
        <div class="alert alert-warning py-2 small d-flex flex-wrap align-items-center gap-2 mb-2">
            <span><i class="bi bi-exclamation-triangle me-1"></i><strong>${dupCount}</strong> row(s) already exist (marked <span class="badge bg-warning text-dark">dup</span> below).</span>
            <label class="ms-auto mb-0 d-flex align-items-center">On duplicates:
                <select id="spDuplicateAction" class="form-select form-select-sm d-inline-block w-auto ms-1">
                    <option value="skip">Skip duplicates</option>
                    <option value="add">Add anyway</option>
                    <option value="overwrite">Overwrite existing</option>
                </select>
            </label>
        </div>`;
}

function _spDupAction() {
    const el = document.getElementById('spDuplicateAction');
    return el ? el.value : 'skip';
}

function _renderSpImportPreview(result) {
    const preview = document.getElementById('spImportPreview');
    if (!preview) return;
    preview.innerHTML = `
        ${_spDupControl(result.rows)}
        <div class="table-responsive" style="max-height:300px;">
            <table class="table table-sm">
                <thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Category</th></tr></thead>
                <tbody>
                    ${result.rows.map(r => `
                        <tr class="${r.is_duplicate ? 'table-warning' : ''}">
                            <td>${esc(r.date)}</td>
                            <td>${esc(r.description)}</td>
                            <td class="text-end">${Fmt.num(r.amount, 2, 2)} ${esc(r.currency)}</td>
                            <td>${esc(r.category)}${r.is_duplicate ? ' <span class="badge bg-warning text-dark">dup</span>' : ''}</td>
                        </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
}
```

Replace with (each takes an id to render/read, defaulting to the original Spending-page id):

```javascript
function _spDupControl(rows, dupSelectId) {
    dupSelectId = dupSelectId || 'spDuplicateAction';
    const dupCount = (rows || []).filter(r => r.is_duplicate).length;
    if (dupCount === 0) return '';
    return `
        <div class="alert alert-warning py-2 small d-flex flex-wrap align-items-center gap-2 mb-2">
            <span><i class="bi bi-exclamation-triangle me-1"></i><strong>${dupCount}</strong> row(s) already exist (marked <span class="badge bg-warning text-dark">dup</span> below).</span>
            <label class="ms-auto mb-0 d-flex align-items-center">On duplicates:
                <select id="${dupSelectId}" class="form-select form-select-sm d-inline-block w-auto ms-1">
                    <option value="skip">Skip duplicates</option>
                    <option value="add">Add anyway</option>
                    <option value="overwrite">Overwrite existing</option>
                </select>
            </label>
        </div>`;
}

function _spDupAction(dupSelectId) {
    const el = document.getElementById(dupSelectId || 'spDuplicateAction');
    return el ? el.value : 'skip';
}

function _renderSpImportPreview(result, ids) {
    ids = ids || { preview: 'spImportPreview', dupSelectId: 'spDuplicateAction' };
    const preview = document.getElementById(ids.preview);
    if (!preview) return;
    preview.innerHTML = `
        ${_spDupControl(result.rows, ids.dupSelectId)}
        <div class="table-responsive" style="max-height:300px;">
            <table class="table table-sm">
                <thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Category</th></tr></thead>
                <tbody>
                    ${result.rows.map(r => `
                        <tr class="${r.is_duplicate ? 'table-warning' : ''}">
                            <td>${esc(r.date)}</td>
                            <td>${esc(r.description)}</td>
                            <td class="text-end">${Fmt.num(r.amount, 2, 2)} ${esc(r.currency)}</td>
                            <td>${esc(r.category)}${r.is_duplicate ? ' <span class="badge bg-warning text-dark">dup</span>' : ''}</td>
                        </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
}
```

- [ ] **Step 3: Wire the new card in `setupImportExportPage`**

In `web_client/js/pfm_features.js`, find `setupImportExportPage`'s opening (right after the `if (!fileBroker) return;` guard is fine as an anchor, or anywhere inside the function body before its closing) — add a call to populate the new card's account dropdown and wire it with its own id set. Find:

```javascript
    // --- Export section ---
    const ioCsvBtn = document.getElementById('ioExportCsvBtn');
```

Insert immediately before it:

```javascript
    // --- Bank Statement import (reuses Spending page logic with different element ids) ---
    const ioSpAccountSelect = document.getElementById('ioSpImportAccountSelect');
    if (ioSpAccountSelect && ioSpAccountSelect.options.length <= 1) {
        (async () => {
            try {
                const portfolios = await window.apiClient.getPortfolios();
                portfolios.filter(p => p.account_type === 'bank').forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.id; opt.textContent = p.name;
                    ioSpAccountSelect.appendChild(opt);
                });
            } catch (e) { /* silent */ }
        })();
    }
    _wireSpendingImportModal({
        parseBtn: 'ioSpParseBtn', suggestBtn: 'ioSpSuggestBtn', saveBtn: 'ioSpSaveBtn',
        preview: 'ioSpImportPreview', status: 'ioSpImportStatus', templateLink: 'ioSpDownloadTemplate',
        accountSelect: 'ioSpImportAccountSelect', accountName: 'ioSpImportAccountName',
        fileInput: 'ioSpImportFile', dupSelectId: 'ioSpDuplicateAction',
        onSaved: async () => { if (window.loadSpendingPage) await _refreshSpendingDataIfLoaded(); },
    });

    // --- Export section ---
    const ioCsvBtn = document.getElementById('ioExportCsvBtn');
```

Add the small helper this references, right after `setupImportExportPage`'s closing `}` (find it by searching for the next top-level `function` after `setupImportExportPage`, and insert before that):

```javascript
// The Import/Export page's Bank Statement card can save spending rows before
// the Spending page has ever been visited (window._spendingAllRows unset) —
// only refresh Spending's own cached data if it's actually been loaded once,
// to avoid touching DOM elements that don't exist yet.
async function _refreshSpendingDataIfLoaded() {
    if (typeof window._spendingAllRows !== 'undefined') {
        await _refreshSpendingData();
    }
}
```

- [ ] **Step 4: Confirm `loadSpendingPage`'s own call site still uses the defaults**

Find `loadSpendingPage`'s existing `_wireSpendingImportModal();` call (no arguments) — confirm it's unchanged; the default-parameter fallback in Step 2 reproduces the exact original ids, so this call site needs no edit. Just verify by reading it, don't change it.

- [ ] **Step 5: Run JS tests, syntax check**

```bash
node --test web_client/js/tests/ 2>&1 | tail -20
node --check web_client/js/pfm_features.js
```

Expected: all existing tests pass unchanged (this task only adds parameters with defaults reproducing prior behavior — no test should need updating), valid syntax.

- [ ] **Step 6: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: add Bank Statement import card to the Import/Export page, reusing Spending page logic

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 6: Rebuild, verify, docs

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rebuild and restart both services**

```bash
docker exec portf_backend_dev kill -HUP 1
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

- [ ] **Step 2: Run the full test suite one final time**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -15
node --test web_client/js/tests/ 2>&1 | tail -15
```

Expected: all pass, 0 failures.

- [ ] **Step 3: Smoke-test via curl against the real running API** (mirrors the discipline used for the original feature — real evidence, not assumed)

- Create a bank-type portfolio via `POST /api/v1/portfolios/` with `account_type: "bank"`, confirm it appears with that type on `GET /api/v1/portfolios/`.
- Create one fictional spending transaction (via `POST /api/v1/spending/save` with a single row, or reuse an existing account), then `DELETE /api/v1/spending/{id}` and confirm it's gone from `GET /api/v1/spending/`.
- `GET /api/v1/export/csv?portfolio_id=<a>&portfolio_id=<b>` against two real portfolio ids and confirm the returned CSV contains rows from both.
- Clean up any fictional smoke-test data created here the same way Task 13 of the original plan did (soft-delete or hard-delete via the API, whichever is appropriate for the row type) — do not leave test artifacts in the live database.

- [ ] **Step 4: Update `PROJECT_STATUS.md`**

Bump `Last updated` to the date this task actually runs, and prepend a new `**Recent (v2.5.20):**` line before the existing v2.5.19 line summarizing: delete endpoint + bulk select/recategorize/delete on the Spending page; bank-account creation on the Brokers page; multi-account CSV export; Bank Statement import added to the Import/Export page.

- [ ] **Step 5: Extend `CLAUDE.md`'s `### Spending Tracking` section**

Add to the existing bullet list (don't duplicate the section header — it already exists from the original feature): a line documenting `DELETE /api/v1/spending/{id}` (hard delete) and the bulk-select UI; a line noting bank accounts can now be created directly via `POST /api/v1/portfolios/` with `account_type: "bank"` from the Brokers page UI, not just via Spending import; a line noting `GET /api/v1/export/csv` now accepts repeated `portfolio_id` params; a line noting the Import/Export page's "Import Bank Statement" card reuses `_wireSpendingImportModal`/`_renderSpImportPreview` (now parameterized by element-id config) rather than duplicating the import flow.

- [ ] **Step 6: Final commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: document spending feedback round 2 (delete, bulk actions, bank accounts, multi-export, import page)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Self-Review

**Spec coverage:** Delete endpoint + bulk UI (Task 1, 3), bank-account creation on Brokers page (Task 2), multi-account export (Task 4), Import/Export page bank-statement entry point (Task 5), docs (Task 6) — all four user-decided items covered. Interest-rate tracking explicitly needs no new work per the user's own choice, and is not a task here.

**Placeholder scan:** No TBD/TODO markers. Two steps (Task 2 Step 2, Task 4 Step 2) explicitly instruct the implementer to verify an existing test file/fixture pattern before finalizing exact code, since the plan author could not enumerate every existing test file in this research pass — this is a deliberate "verify against real repo state" instruction, not a placeholder for logic; the actual test bodies given are complete and correct against the schemas/endpoints as researched.

**Type consistency:** `deleteSpendingTransaction`/`updateSpendingCategory` names match `pfm_core.js`'s existing method names exactly. `_wireSpendingImportModal`/`_renderSpImportPreview`/`_spDupControl`/`_spDupAction`'s parameter additions are backward-compatible (default values reproduce prior hard-coded ids), so the existing Spending-page call site needs no change — verified by re-reading that call site in Task 5 Step 4 rather than assuming.
