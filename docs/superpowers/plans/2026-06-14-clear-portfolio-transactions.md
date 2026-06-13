# Clear Portfolio Transactions + Backup/Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Clear all transactions" per portfolio and "Restore DB backup" upload, both with safety-first confirmation flows.

**Architecture:** One new router (`system.py`) for restore; one new endpoint on the existing portfolios router for clear. The DB method `delete_transactions_by_portfolio` already exists. Two new modals in the web client wired via `pfm_pages.js` (clear) and `pfm_features.js` / `index.html` (restore). Backup env vars documented in `.env.local` only — no new settings code.

**Tech Stack:** FastAPI, SQLite (`sqlite3` backup API), Bootstrap 5 modals, vanilla JS.

---

## File Map

| File | Change |
|---|---|
| `portf_server/routers/system.py` | CREATE — `POST /api/v1/system/restore` |
| `portf_server/routers/portfolios.py` | MODIFY — add `DELETE /{portfolio_id}/transactions` |
| `portf_server/app.py` | MODIFY — import + register system router |
| `.env.local` | MODIFY — document `PFM_BACKUP_DIR` / `PFM_BACKUP_KEEP` |
| `web_client/index.html` | MODIFY — restore modal + clear-transactions modal |
| `web_client/js/pfm_pages.js` | MODIFY — "Clear" button in portfolio rows |
| `web_client/js/pfm_features.js` | MODIFY — wire clear modal + restore upload |
| `tests/unit/test_api_routers.py` | MODIFY — tests for both new endpoints |

---

## Task 1: `DELETE /api/v1/portfolios/{id}/transactions` endpoint

**Files:**
- Modify: `portf_server/routers/portfolios.py` (after the existing `delete_portfolio` handler at line ~470)
- Test: `tests/unit/test_api_routers.py`

The DB method `delete_transactions_by_portfolio(portfolio_id) -> int` already exists in `portf_manager/database.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_api_routers.py` inside a new `class TestPortfolioTransactionsClear`:

```python
class TestPortfolioTransactionsClear:
    """Tests for DELETE /api/v1/portfolios/{id}/transactions."""

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_clear_transactions_returns_deleted_count(
        self, async_test_client: AsyncClient, auth_headers
    ):
        # Create a portfolio
        port_resp = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "ClearTest", "base_currency": "EUR"},
            headers=auth_headers,
        )
        assert port_resp.status_code == 201
        port_id = port_resp.json()["id"]

        # Create an asset + transaction so there is something to delete
        asset_resp = await async_test_client.post(
            "/api/v1/assets",
            json={"symbol": "CLRT", "name": "ClearTest Asset", "asset_type": "stock", "currency": "EUR"},
            headers=auth_headers,
        )
        assert asset_resp.status_code == 201
        asset_id = asset_resp.json()["id"]

        tx_resp = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "portfolio_id": port_id,
                "transaction_type": "buy",
                "quantity": 1.0,
                "price": 10.0,
                "transaction_date": "2024-01-01",
                "currency": "EUR",
            },
            headers=auth_headers,
        )
        assert tx_resp.status_code == 201

        # Clear transactions
        resp = await async_test_client.delete(
            f"/api/v1/portfolios/{port_id}/transactions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 1

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_clear_transactions_returns_zero_when_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        port_resp = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "EmptyClear", "base_currency": "EUR"},
            headers=auth_headers,
        )
        assert port_resp.status_code == 201
        port_id = port_resp.json()["id"]

        resp = await async_test_client.delete(
            f"/api/v1/portfolios/{port_id}/transactions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_clear_transactions_404_for_unknown_portfolio(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.delete(
            "/api/v1/portfolios/999999/transactions",
            headers=auth_headers,
        )
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_api_routers.py::TestPortfolioTransactionsClear -v 2>&1 | tail -20
```

Expected: `FAILED` — endpoint does not exist yet.

- [ ] **Step 3: Add the endpoint to `portf_server/routers/portfolios.py`**

Append after the `delete_portfolio` handler (around line 470):

```python
@router.delete("/{portfolio_id}/transactions")
async def clear_portfolio_transactions(
    portfolio_id: int,
    database: Database = Depends(get_database),
    api_key_info: dict = Depends(_get_api_key_auth),
):
    """Delete all transactions for a portfolio. Returns count of deleted rows."""
    portfolio = database.get_portfolio(portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    deleted = database.delete_transactions_by_portfolio(portfolio_id)
    return {"deleted": deleted}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_api_routers.py::TestPortfolioTransactionsClear -v 2>&1 | tail -20
```

Expected: 3 passed.

- [ ] **Step 5: Run full unit suite to check for regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: all passing, none failing.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/portfolios.py tests/unit/test_api_routers.py
git commit -m "feat: DELETE /api/v1/portfolios/{id}/transactions — clear broker transactions

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: `POST /api/v1/system/restore` endpoint

**Files:**
- Create: `portf_server/routers/system.py`
- Modify: `portf_server/app.py`
- Test: `tests/unit/test_api_routers.py`

The restore endpoint:
1. Accepts a multipart `.db` or `.db.gz` upload
2. Validates it is a SQLite DB with `PRAGMA integrity_check`
3. Checks `PRAGMA user_version` equals `DATABASE_VERSION` (rejects mismatches)
4. Auto-saves current DB to `PFM_BACKUP_DIR` before replacing (if configured)
5. Replaces live DB via SQLite backup API

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_api_routers.py`:

```python
import sqlite3
import tempfile
import os
import gzip as gzip_mod


class TestSystemRestore:
    """Tests for POST /api/v1/system/restore."""

    def _make_valid_db(self, version: int = 18) -> bytes:
        """Return bytes of a minimal SQLite DB with the given user_version."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.close()
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_valid_db(
        self, async_test_client: AsyncClient, auth_headers, tmp_path
    ):
        db_bytes = self._make_valid_db(18)
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("backup.db", db_bytes, "application/octet-stream")},
        )
        assert resp.status_code == 200
        assert resp.json()["restored"] is True

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_version_mismatch(
        self, async_test_client: AsyncClient, auth_headers
    ):
        db_bytes = self._make_valid_db(version=5)
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("old.db", db_bytes, "application/octet-stream")},
        )
        assert resp.status_code == 422
        assert "version" in resp.json()["detail"].lower()

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_invalid_file(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("bad.db", b"not a sqlite file", "application/octet-stream")},
        )
        assert resp.status_code == 422
        assert "valid sqlite" in resp.json()["detail"].lower()

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_restore_gzip_db(
        self, async_test_client: AsyncClient, auth_headers
    ):
        db_bytes = self._make_valid_db(18)
        gz_bytes = gzip_mod.compress(db_bytes)
        resp = await async_test_client.post(
            "/api/v1/system/restore",
            headers=auth_headers,
            files={"file": ("backup.db.gz", gz_bytes, "application/gzip")},
        )
        assert resp.status_code == 200
        assert resp.json()["restored"] is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_api_routers.py::TestSystemRestore -v 2>&1 | tail -20
```

Expected: `FAILED` — router not registered yet.

- [ ] **Step 3: Create `portf_server/routers/system.py`**

```python
"""System administration endpoints: DB restore."""

import gzip
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from portf_manager.database import DATABASE_VERSION, Database
from ..dependencies import get_database
from ..auth_middleware import APIKeyManager
from ..dependencies import get_api_key_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _get_api_key_auth(request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)):
    from fastapi import status
    from fastapi.security import APIKeyHeader
    from starlette.requests import Request as StarletteRequest

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    info = api_key_manager.validate_api_key(api_key)
    if not info:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return info


def _auto_backup(db: Database, backup_dir: str) -> str | None:
    """Save a pre-restore snapshot to backup_dir using the SQLite backup API."""
    src_path = getattr(db, "db_path", None)
    if not src_path or not os.path.exists(src_path):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(backup_dir, f"portfolio-prerestore-{ts}.db")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(out)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return out


@router.post("/restore")
async def restore_db(
    file: UploadFile,
    request: Request,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_get_api_key_auth),
):
    """Replace the live SQLite database with an uploaded backup.

    Accepts .db or .db.gz files. Validates integrity and schema version before
    replacing. Saves a pre-restore snapshot to PFM_BACKUP_DIR if configured.
    """
    src_path = getattr(db, "db_path", None)
    if not src_path:
        raise HTTPException(
            status_code=503,
            detail="Restore is only available for the SQLite backend.",
        )

    raw = await file.read()
    fname = file.filename or ""

    # Decompress gzip if needed
    tmp_db = None
    try:
        fd, tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        if fname.endswith(".gz"):
            try:
                raw = gzip.decompress(raw)
            except Exception:
                raise HTTPException(status_code=422, detail="Could not decompress .gz file.")

        with open(tmp_db, "wb") as f:
            f.write(raw)

        # Validate: must be a readable SQLite DB
        try:
            conn = sqlite3.connect(tmp_db)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                raise ValueError("integrity_check failed")
            uploaded_version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.close()
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Not a valid SQLite database.",
            )

        # Validate schema version
        if uploaded_version != DATABASE_VERSION:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Schema version mismatch: backup is v{uploaded_version}, "
                    f"current app requires v{DATABASE_VERSION}."
                ),
            )

        # Auto-backup current DB before replacing
        backup_dir = os.environ.get("PFM_BACKUP_DIR", "")
        pre_restore_backup = None
        if backup_dir:
            try:
                pre_restore_backup = _auto_backup(db, backup_dir)
            except Exception as e:
                logger.warning("Pre-restore backup failed: %s", e)

        # Replace live DB via SQLite backup API
        upload_conn = sqlite3.connect(tmp_db)
        live_conn = sqlite3.connect(src_path)
        with live_conn:
            upload_conn.backup(live_conn)
        live_conn.close()
        upload_conn.close()

        logger.info("Database restored from %s; pre-restore backup: %s", fname, pre_restore_backup)
        return {"restored": True, "pre_restore_backup": pre_restore_backup}

    finally:
        if tmp_db and os.path.exists(tmp_db):
            try:
                os.unlink(tmp_db)
            except OSError:
                pass
```

- [ ] **Step 4: Register system router in `portf_server/app.py`**

Add `system` to the imports block (around line 27–48):

```python
from .routers import (
    assets,
    transactions,
    portfolios,
    entities,
    sectors,
    auth,
    llm,
    tax,
    imports,
    exports,
    bookings,
    sync,
    rebalance,
    research,
    analytics,
    watchlist,
    goals,
    public,
    networth,
    market,
    system,
)
```

Then add the router registration (find the block where other routers are included, e.g. `app.include_router(market.router)`, and add after it):

```python
app.include_router(system.router)
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_api_routers.py::TestSystemRestore -v 2>&1 | tail -20
```

Expected: 4 passed.

- [ ] **Step 6: Run full unit suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/system.py portf_server/app.py tests/unit/test_api_routers.py
git commit -m "feat: POST /api/v1/system/restore — upload a .db/.db.gz backup to replace live DB

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: Config — document backup env vars in `.env.local`

**Files:**
- Modify: `.env.local`

- [ ] **Step 1: Add backup vars to `.env.local`**

Open `/home/agoldhoorn/repos/pfm/.env.local` and add this block (near the bottom, before any trailing comments):

```bash
# ---------------------------------------------------------------------------
# Database backup
# ~/scripts/portf-backup.sh runs daily at 03:00 via cron.
# Backups are gzip-compressed SQLite snapshots. PFM_BACKUP_DIR is also read
# by POST /api/v1/system/restore to save a pre-restore snapshot automatically.
# ---------------------------------------------------------------------------
PFM_BACKUP_DIR=/home/agoldhoorn/backups/pfm
PFM_BACKUP_KEEP=30
```

- [ ] **Step 2: Commit**

```bash
git add .env.local
git commit -m "chore: document PFM_BACKUP_DIR and PFM_BACKUP_KEEP in .env.local

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Frontend — "Clear transactions" modal + button

**Files:**
- Modify: `web_client/index.html` — add modal HTML
- Modify: `web_client/js/pfm_pages.js` — add "Clear" button to portfolio row
- Modify: `web_client/js/pfm_features.js` — wire up modal

### 4a: Add modal HTML to `index.html`

Find the existing `portfolioModal` (search for `id="portfolioModal"`) and add the clear-transactions modal **immediately after it**:

```html
<!-- Clear portfolio transactions modal -->
<div class="modal fade" id="clearTransactionsModal" tabindex="-1" aria-labelledby="clearTransactionsModalLabel" aria-hidden="true">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header border-danger">
        <h5 class="modal-title text-danger" id="clearTransactionsModalLabel">
          <i class="bi bi-exclamation-triangle-fill me-2"></i>Clear all transactions
        </h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p>This will permanently delete <strong>all transactions</strong> for <strong id="clearPortfolioName"></strong>. This cannot be undone.</p>
        <div class="mb-3">
          <button class="btn btn-sm btn-outline-secondary" id="clearModalBackupBtn">
            <i class="bi bi-database-down me-1"></i>Download DB backup first
          </button>
        </div>
        <div class="form-check">
          <input class="form-check-input" type="checkbox" id="clearConfirmCheck">
          <label class="form-check-label" for="clearConfirmCheck">
            I have downloaded a backup or accept the risk
          </label>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-danger" id="clearTransactionsConfirmBtn" disabled>
          Clear transactions
        </button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 1: Add the modal HTML after `portfolioModal` in `index.html`**

Search for the closing `</div>` of `id="portfolioModal"` — it ends with something like `</div><!-- /portfolioModal -->`. Insert the HTML block above immediately after that closing div.

### 4b: Add "Clear" button to portfolio rows in `pfm_pages.js`

In `pfm_pages.js`, find the `renderBrokerRow` function (around line 784). The last `<td>` currently has Edit and Delete buttons:

```javascript
<td class="pe-3">
    <button class="btn btn-sm btn-outline-primary me-1" title="Edit" onclick="editPortfolio(...)">
        <i class="bi bi-pencil"></i>
    </button>
    <button class="btn btn-sm btn-outline-danger" title="Delete" onclick="deletePortfolio(${p.id}, '${esc(p.name)}')">
        <i class="bi bi-trash"></i>
    </button>
</td>
```

Replace that `<td>` with:

```javascript
<td class="pe-3">
    <button class="btn btn-sm btn-outline-primary me-1" title="Edit" onclick="editPortfolio(${p.id}, '${esc(p.name)}', '${p.base_currency || 'EUR'}', '${esc(p.description)}', '${esc(p.website)}')">
        <i class="bi bi-pencil"></i>
    </button>
    <button class="btn btn-sm btn-outline-warning me-1" title="Clear all transactions" onclick="clearPortfolioTransactions(${p.id}, '${esc(p.name)}')">
        <i class="bi bi-eraser"></i>
    </button>
    <button class="btn btn-sm btn-outline-danger" title="Delete portfolio" onclick="deletePortfolio(${p.id}, '${esc(p.name)}')">
        <i class="bi bi-trash"></i>
    </button>
</td>
```

- [ ] **Step 2: Update `renderBrokerRow` in `pfm_pages.js`**

### 4c: Wire up the modal in `pfm_features.js`

In `pfm_features.js`, find `window.deletePortfolio` (around line 720) and add the `clearPortfolioTransactions` global function immediately after it:

```javascript
window.clearPortfolioTransactions = async function(id, name) {
    const modal = document.getElementById('clearTransactionsModal');
    const bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    const nameEl = document.getElementById('clearPortfolioName');
    const checkbox = document.getElementById('clearConfirmCheck');
    const confirmBtn = document.getElementById('clearTransactionsConfirmBtn');
    const backupBtn = document.getElementById('clearModalBackupBtn');

    // Reset state
    nameEl.textContent = name;
    checkbox.checked = false;
    confirmBtn.disabled = true;

    checkbox.onchange = () => { confirmBtn.disabled = !checkbox.checked; };

    backupBtn.onclick = async () => {
        try {
            await window.apiClient.downloadBlob(
                window.apiClient.baseURL + '/api/v1/export/backup',
                'pfm-backup.db'
            );
        } catch (err) { alert('Backup error: ' + err.message); }
    };

    confirmBtn.onclick = async () => {
        confirmBtn.disabled = true;
        try {
            const resp = await fetch(
                window.apiClient.baseURL + `/api/v1/portfolios/${id}/transactions`,
                { method: 'DELETE', headers: { 'X-API-Key': window.apiClient.apiKey } }
            );
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();
            bsModal.hide();
            checkbox.checked = false;
            window.showToast(`Deleted ${data.deleted} transaction${data.deleted !== 1 ? 's' : ''} from ${name}.`, 'success');
            window.pageManager.loadPortfoliosPage();
        } catch (err) {
            alert('Error clearing transactions: ' + err.message);
            confirmBtn.disabled = false;
        }
    };

    bsModal.show();
};
```

- [ ] **Step 3: Add `clearPortfolioTransactions` to `pfm_features.js` after `deletePortfolio`**

- [ ] **Step 4: Rebuild and test the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Open the Portfolios page. Verify:
- Each portfolio row now has three buttons: Edit (blue), Clear (yellow eraser), Delete (red)
- Clicking "Clear transactions" opens the modal with the portfolio name
- The "Clear transactions" button is disabled until the checkbox is checked
- "Download DB backup first" triggers a file download
- After confirming, a toast shows the deleted count and the page refreshes

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_pages.js web_client/js/pfm_features.js
git commit -m "feat: clear-transactions modal on Portfolios page with backup-first prompt

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: Frontend — "Restore DB backup" modal + button

**Files:**
- Modify: `web_client/index.html` — add restore modal HTML
- Modify: `web_client/js/pfm_features.js` — wire up restore upload in `setupImportExportPage`

### 5a: Add restore modal to `index.html`

Find the existing `clearTransactionsModal` you added in Task 4, and add the restore modal immediately after it:

```html
<!-- Restore DB backup modal -->
<div class="modal fade" id="restoreBackupModal" tabindex="-1" aria-labelledby="restoreBackupModalLabel" aria-hidden="true">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header border-danger">
        <h5 class="modal-title text-danger" id="restoreBackupModalLabel">
          <i class="bi bi-exclamation-triangle-fill me-2"></i>Restore database backup
        </h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p>This will <strong>replace all current data</strong> with the uploaded backup. A pre-restore snapshot will be saved automatically to the backup directory (if configured).</p>
        <div class="mb-3">
          <label for="restoreFileInput" class="form-label">Select backup file (.db or .db.gz)</label>
          <input type="file" class="form-control" id="restoreFileInput" accept=".db,.gz">
        </div>
        <div id="restoreStatusMsg" class="text-muted small"></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-danger" id="restoreConfirmBtn" disabled>
          Restore
        </button>
      </div>
    </div>
  </div>
</div>
```

Also add a "Restore DB backup" button in the Export section of the Import/Export page (find `id="ioExportBackupBtn"` and add immediately after its closing `</button>`):

```html
<button class="btn btn-sm btn-outline-danger" id="ioRestoreBackupBtn" title="Upload a .db or .db.gz backup to replace the current database">
    <i class="bi bi-database-up me-2"></i>Restore DB backup
</button>
```

- [ ] **Step 1: Add restore modal HTML + restore button to `index.html`**

### 5b: Wire up the restore modal in `pfm_features.js`

Inside `setupImportExportPage()`, after the existing `ioBackupBtn` handler (around line 971), add:

```javascript
const ioRestoreBtn = document.getElementById('ioRestoreBackupBtn');
if (ioRestoreBtn) {
    const restoreModal = document.getElementById('restoreBackupModal');
    const bsRestoreModal = bootstrap.Modal.getOrCreateInstance(restoreModal);
    const restoreFileInput = document.getElementById('restoreFileInput');
    const restoreConfirmBtn = document.getElementById('restoreConfirmBtn');
    const restoreStatusMsg = document.getElementById('restoreStatusMsg');

    ioRestoreBtn.addEventListener('click', () => {
        restoreFileInput.value = '';
        restoreConfirmBtn.disabled = true;
        restoreStatusMsg.textContent = '';
        bsRestoreModal.show();
    });

    restoreFileInput.addEventListener('change', () => {
        restoreConfirmBtn.disabled = !restoreFileInput.files.length;
        restoreStatusMsg.textContent = '';
    });

    restoreConfirmBtn.addEventListener('click', async () => {
        const file = restoreFileInput.files[0];
        if (!file) return;
        restoreConfirmBtn.disabled = true;
        restoreStatusMsg.textContent = 'Restoring…';
        try {
            const formData = new FormData();
            formData.append('file', file);
            const resp = await fetch(
                window.apiClient.baseURL + '/api/v1/system/restore',
                { method: 'POST', headers: { 'X-API-Key': window.apiClient.apiKey }, body: formData }
            );
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || resp.statusText);
            bsRestoreModal.hide();
            const backupNote = data.pre_restore_backup
                ? ` Pre-restore snapshot saved to ${data.pre_restore_backup}.`
                : '';
            alert(`Database restored successfully.${backupNote}\n\nThe page will reload.`);
            window.location.reload();
        } catch (err) {
            restoreStatusMsg.textContent = 'Error: ' + err.message;
            restoreConfirmBtn.disabled = false;
        }
    });
}
```

- [ ] **Step 2: Add restore wiring inside `setupImportExportPage` in `pfm_features.js`**

- [ ] **Step 3: Rebuild web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

Open the Import/Export page. Verify:
- "Restore DB backup" button is visible alongside "Download DB backup"
- Clicking it opens the modal
- The Restore button is disabled until a file is selected
- Uploading a valid `.db` file shows "Database restored…" alert and reloads the page
- Uploading a non-SQLite file or wrong-version backup shows the error message from the server

- [ ] **Step 4: Commit**

```bash
git add web_client/index.html web_client/js/pfm_features.js
git commit -m "feat: restore DB backup modal on Import/Export page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 6: Bump `index.html` cache-buster and final check

**Files:**
- Modify: `web_client/index.html` — bump `?v=` query strings on changed JS files

- [ ] **Step 1: Bump cache-buster version in `index.html`**

Find lines like:
```html
<script src="js/pfm_pages.js?v=NNN"></script>
<script src="js/pfm_features.js?v=NNN"></script>
```

Increment each `v=` value by 1.

- [ ] **Step 2: Final rebuild**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

- [ ] **Step 3: Run full test suite one final time**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add web_client/index.html
git commit -m "chore: bump cache-buster versions after clear/restore UI additions

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
