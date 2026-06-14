# Fixed Deposits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `fixed_deposits` table, CRUD API, maturity-to-interest-transaction flow, LLM text extraction, and a UI section on the Net Worth page.

**Architecture:** A new `fixed_deposits` DB table (v19 migration) with its own router at `/api/v1/deposits`. The maturity action creates an `interest` transaction against a synthetic `DEPOSITS` asset (same pattern as Mintos), so income flows into existing analytics with no analytics-code changes. LLM extraction follows the `extract-bookings` pattern: dedicated `GeminiClient.extract_deposits` method + `POST /api/v1/llm/extract-deposits` endpoint.

**Tech Stack:** Python 3.13, FastAPI, SQLite, Pydantic v2, Vanilla JS + Bootstrap 5

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `portf_manager/database.py` | Bump `DATABASE_VERSION` 18→19, add `fixed_deposits` table to `_create_all_tables`, add `_migrate_to_v19`, add 5 CRUD methods |
| Create | `portf_server/routers/deposits.py` | CRUD + `POST /{id}/mature` |
| Modify | `portf_server/app.py` | Import and register `deposits` router |
| Modify | `portf_server/routers/networth.py` | Add `deposits_eur` + `deposits` list to `/api/v1/networth` response |
| Modify | `portf_manager/gemini_client.py` | Add `extract_deposits(text)` method |
| Modify | `portf_server/routers/llm.py` | Add `POST /extract-deposits` endpoint + `DepositExtractionResponse` schema |
| Modify | `web_client/js/pfm_core.js` | Add 6 API client methods for deposits |
| Modify | `web_client/index.html` | Add deposits HTML section + mature modal to `#networthPage` |
| Modify | `web_client/js/pfm_analytics.js` | Extend `loadNetworthPage` + wire deposits form/mature/LLM logic |
| Create | `tests/unit/test_deposits.py` | Unit tests for DB CRUD + router endpoints |
| Modify | `tests/test_database.py` | Bump version assertion 18→19 |

---

## Task 1: DB migration — add `fixed_deposits` table

**Files:**
- Modify: `portf_manager/database.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write failing test for DB version and table existence**

Add to `tests/test_database.py` (near the top of the existing version-check tests):

```python
def test_fixed_deposits_table_exists(tmp_path):
    from portf_manager.database import Database
    db = Database(str(tmp_path / "test.db"))
    with db.get_connection() as conn:
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "fixed_deposits" in tables
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py::test_fixed_deposits_table_exists -v
```

Expected: FAIL — "AssertionError: assert 'fixed_deposits' in ..."

- [ ] **Step 3: Bump DATABASE_VERSION and add table**

In `portf_manager/database.py`:

Change line 1:
```python
DATABASE_VERSION = 18
```
to:
```python
DATABASE_VERSION = 19
```

In `_create_all_tables`, add directly before the `price_update_runs` table block (after the `manual_assets` block):

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fixed_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                portfolio_id INTEGER REFERENCES portfolios(id),
                principal REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                interest_rate REAL NOT NULL,
                start_date TEXT NOT NULL,
                maturity_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'matured', 'closed')),
                interest_paid REAL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
```

Add migration method after `_migrate_to_v18`:

```python
    def _migrate_to_v19(self, conn: sqlite3.Connection):
        """Migrate from v18 to v19 — add fixed_deposits table."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fixed_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                portfolio_id INTEGER REFERENCES portfolios(id),
                principal REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                interest_rate REAL NOT NULL,
                start_date TEXT NOT NULL,
                maturity_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'matured', 'closed')),
                interest_paid REAL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
```

Add to the migration ladder (after `if current_version < 18: self._migrate_to_v18(conn)`):

```python
        if current_version < 19:
            self._migrate_to_v19(conn)
```

- [ ] **Step 4: Update DB version assertions in tests**

In `tests/test_database.py`, replace all occurrences of `assert version == 18` with `assert version == 19`.

Run:
```bash
grep -n "assert version == 18" tests/test_database.py
```

Then replace each one (there are 3).

- [ ] **Step 5: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py::test_fixed_deposits_table_exists -v
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_database.py -v --tb=short 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add portf_manager/database.py tests/test_database.py
git commit -m "feat: add fixed_deposits table (DB v19)"
```

---

## Task 2: DB CRUD methods

**Files:**
- Modify: `portf_manager/database.py`
- Create: `tests/unit/test_deposits.py`

- [ ] **Step 1: Write failing tests for CRUD**

Create `tests/unit/test_deposits.py`:

```python
import pytest
from datetime import date, timedelta
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def _make_deposit(db, **overrides):
    defaults = dict(
        name="Superdepósito 1 mes",
        portfolio_id=None,
        principal=5000.0,
        currency="EUR",
        interest_rate=4.0,
        start_date="2026-06-12",
        maturity_date="2026-07-12",
        notes=None,
    )
    defaults.update(overrides)
    return db.create_fixed_deposit(**defaults)


def test_create_and_get_deposit(db):
    dep_id = _make_deposit(db)
    dep = db.get_fixed_deposit(dep_id)
    assert dep["name"] == "Superdepósito 1 mes"
    assert dep["principal"] == 5000.0
    assert dep["interest_rate"] == 4.0
    assert dep["status"] == "active"
    assert dep["interest_paid"] is None


def test_list_deposits(db):
    _make_deposit(db, name="Dep A")
    _make_deposit(db, name="Dep B")
    deps = db.get_fixed_deposits()
    assert len(deps) == 2


def test_update_deposit(db):
    dep_id = _make_deposit(db)
    ok = db.update_fixed_deposit(dep_id, status="matured", interest_paid=8.35)
    assert ok is True
    dep = db.get_fixed_deposit(dep_id)
    assert dep["status"] == "matured"
    assert dep["interest_paid"] == 8.35


def test_delete_deposit(db):
    dep_id = _make_deposit(db)
    assert db.delete_fixed_deposit(dep_id) is True
    assert db.get_fixed_deposit(dep_id) is None


def test_get_active_deposits(db):
    _make_deposit(db, name="Active")
    dep_id2 = _make_deposit(db, name="Matured")
    db.update_fixed_deposit(dep_id2, status="matured")
    active = db.get_fixed_deposits(status="active")
    assert len(active) == 1
    assert active[0]["name"] == "Active"
```

- [ ] **Step 2: Run to confirm fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py -v 2>&1 | head -30
```

Expected: FAIL — "AttributeError: 'Database' object has no attribute 'create_fixed_deposit'"

- [ ] **Step 3: Add CRUD methods to database.py**

Add the following block after `delete_manual_asset` (around line 1296):

```python
    # ── Fixed Deposits ──────────────────────────────────────────────────────

    def create_fixed_deposit(
        self,
        name: str,
        principal: float,
        currency: str = "EUR",
        interest_rate: float = 0.0,
        start_date: str = "",
        maturity_date: str = "",
        portfolio_id: int = None,
        notes: str = None,
    ) -> int:
        """Create a fixed deposit record."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO fixed_deposits
                    (name, portfolio_id, principal, currency, interest_rate,
                     start_date, maturity_date, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, portfolio_id, principal, currency.upper(),
                 interest_rate, start_date, maturity_date, notes),
            )
            conn.commit()
            return cur.lastrowid

    def get_fixed_deposit(self, deposit_id: int) -> Optional[Dict]:
        """Get a single fixed deposit by id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM fixed_deposits WHERE id = ?", (deposit_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_fixed_deposits(self, status: str = None) -> List[Dict]:
        """List fixed deposits, optionally filtered by status."""
        with self.get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM fixed_deposits WHERE status = ? ORDER BY maturity_date",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM fixed_deposits ORDER BY maturity_date"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_fixed_deposit(self, deposit_id: int, **fields) -> bool:
        """Update fixed deposit fields."""
        valid = {
            "name", "portfolio_id", "principal", "currency", "interest_rate",
            "start_date", "maturity_date", "status", "interest_paid", "notes",
        }
        update = {k: v for k, v in fields.items() if k in valid}
        if not update:
            return False
        with self.get_connection() as conn:
            cols = ", ".join(f"{k} = ?" for k in update)
            vals = list(update.values()) + [deposit_id]
            cur = conn.execute(
                f"UPDATE fixed_deposits SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                vals,
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_fixed_deposit(self, deposit_id: int) -> bool:
        """Delete a fixed deposit."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM fixed_deposits WHERE id = ?", (deposit_id,)
            )
            conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/database.py tests/unit/test_deposits.py
git commit -m "feat: fixed_deposits DB CRUD methods"
```

---

## Task 3: API router — CRUD + mature endpoint

**Files:**
- Create: `portf_server/routers/deposits.py`
- Modify: `portf_server/app.py`
- Modify: `tests/unit/test_deposits.py`

- [ ] **Step 1: Add API tests**

Append to `tests/unit/test_deposits.py`:

```python
from fastapi.testclient import TestClient
from portf_server.app import create_app
from portf_server.dependencies import get_database, get_api_key_manager
from portf_manager.auth import APIKeyManager


@pytest.fixture
def client(tmp_path):
    db_instance = Database(str(tmp_path / "api_test.db"))
    km = APIKeyManager.__new__(APIKeyManager)
    km.api_keys = {"test-key": {"name": "test"}}

    app = create_app()
    app.dependency_overrides[get_database] = lambda: db_instance
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app)


HEADERS = {"X-API-Key": "test-key"}


def test_api_create_list_deposit(client):
    payload = {
        "name": "Dep 1",
        "principal": 5000.0,
        "currency": "EUR",
        "interest_rate": 4.0,
        "start_date": "2026-06-12",
        "maturity_date": "2026-07-12",
    }
    r = client.post("/api/v1/deposits/", json=payload, headers=HEADERS)
    assert r.status_code == 200
    dep_id = r.json()["id"]

    r2 = client.get("/api/v1/deposits/", headers=HEADERS)
    assert r2.status_code == 200
    deps = r2.json()
    assert len(deps) == 1
    assert deps[0]["id"] == dep_id
    assert "projected_interest" in deps[0]


def test_api_delete_deposit(client):
    r = client.post("/api/v1/deposits/", json={
        "name": "X", "principal": 1000.0, "interest_rate": 2.0,
        "start_date": "2026-01-01", "maturity_date": "2026-07-01"
    }, headers=HEADERS)
    dep_id = r.json()["id"]
    r2 = client.delete(f"/api/v1/deposits/{dep_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["deleted"] is True


def test_api_mature_deposit(client):
    r = client.post("/api/v1/deposits/", json={
        "name": "Dep Mature", "principal": 5000.0, "interest_rate": 4.0,
        "start_date": "2026-06-12", "maturity_date": "2026-07-12"
    }, headers=HEADERS)
    dep_id = r.json()["id"]

    r2 = client.post(f"/api/v1/deposits/{dep_id}/mature",
                     json={"interest_paid": 8.35, "date": "2026-07-12"},
                     headers=HEADERS)
    assert r2.status_code == 200
    body = r2.json()
    assert "transaction_id" in body
    assert body["deposit_id"] == dep_id
```

- [ ] **Step 2: Run to confirm fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py::test_api_create_list_deposit -v 2>&1 | head -20
```

Expected: FAIL — ImportError or 404.

- [ ] **Step 3: Create the deposits router**

Create `portf_server/routers/deposits.py`:

```python
"""Fixed deposits CRUD + maturity action."""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from portf_manager.database import Database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager, get_database

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _projected_interest(principal: float, rate: float, start: str, maturity: str) -> float:
    d1 = date.fromisoformat(start)
    d2 = date.fromisoformat(maturity)
    days = max((d2 - d1).days, 0)
    return round(principal * (rate / 100) * (days / 365), 2)


def _enrich(dep: dict) -> dict:
    dep["projected_interest"] = _projected_interest(
        dep["principal"], dep["interest_rate"], dep["start_date"], dep["maturity_date"]
    )
    dep["days_remaining"] = max((date.fromisoformat(dep["maturity_date"]) - date.today()).days, 0)
    return dep


class DepositBody(BaseModel):
    name: str
    principal: float
    currency: str = "EUR"
    interest_rate: float
    start_date: str
    maturity_date: str
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None


class DepositUpdate(BaseModel):
    name: Optional[str] = None
    principal: Optional[float] = None
    currency: Optional[str] = None
    interest_rate: Optional[float] = None
    start_date: Optional[str] = None
    maturity_date: Optional[str] = None
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class MatureBody(BaseModel):
    interest_paid: float
    date: str


@router.get("/")
def list_deposits(db: Database = Depends(get_database), api_key_info: dict = Depends(_auth)):
    return [_enrich(d) for d in db.get_fixed_deposits()]


@router.post("/")
def create_deposit(
    body: DepositBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    dep_id = db.create_fixed_deposit(
        name=body.name,
        principal=body.principal,
        currency=(body.currency or "EUR").upper(),
        interest_rate=body.interest_rate,
        start_date=body.start_date,
        maturity_date=body.maturity_date,
        portfolio_id=body.portfolio_id,
        notes=body.notes,
    )
    return {"id": dep_id}


@router.put("/{deposit_id}")
def update_deposit(
    deposit_id: int,
    body: DepositUpdate,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    fields = body.model_dump(exclude_none=True)
    if "currency" in fields:
        fields["currency"] = fields["currency"].upper()
    if not db.update_fixed_deposit(deposit_id, **fields):
        raise HTTPException(status_code=404, detail="Not found or nothing to update")
    return {"updated": True}


@router.delete("/{deposit_id}")
def delete_deposit(
    deposit_id: int,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    if not db.delete_fixed_deposit(deposit_id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


@router.post("/{deposit_id}/mature")
def mature_deposit(
    deposit_id: int,
    body: MatureBody,
    db: Database = Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    dep = db.get_fixed_deposit(deposit_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deposit not found")
    if dep["status"] != "active":
        raise HTTPException(status_code=400, detail="Deposit is not active")

    asset = db.get_asset_by_symbol("DEPOSITS")
    if asset:
        asset_id = asset["id"]
    else:
        asset_id = db.create_asset(
            symbol="DEPOSITS",
            name="Fixed Deposits Interest",
            asset_type="cash",
            currency=dep["currency"],
        )
        db.update_asset(asset_id, auto_price=0)

    tx_id = db.create_transaction(
        asset_id=asset_id,
        transaction_type="interest",
        quantity=1.0,
        price=body.interest_paid,
        total_amount=body.interest_paid,
        transaction_date=body.date,
        portfolio_id=dep["portfolio_id"],
        currency=dep["currency"],
        description=f"Interest from {dep['name']}",
    )
    db.update_fixed_deposit(deposit_id, status="matured", interest_paid=body.interest_paid)
    return {"transaction_id": tx_id, "deposit_id": deposit_id}
```

- [ ] **Step 4: Register router in app.py**

In `portf_server/app.py`, add `deposits` to the imports:

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
    deposits,   # ← add this line
)
```

Then add the include_router call (after the `networth` include):

```python
app.include_router(
    deposits.router,
    prefix="/api/v1/deposits",
    tags=["deposits"],
)
```

- [ ] **Step 5: Run API tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/deposits.py portf_server/app.py tests/unit/test_deposits.py
git commit -m "feat: fixed deposits router (CRUD + mature endpoint)"
```

---

## Task 4: Net worth integration

**Files:**
- Modify: `portf_server/routers/networth.py`
- Modify: `tests/unit/test_deposits.py`

- [ ] **Step 1: Add net worth test**

Append to `tests/unit/test_deposits.py`:

```python
def test_networth_includes_deposits(client):
    client.post("/api/v1/deposits/", json={
        "name": "Active dep", "principal": 5000.0, "interest_rate": 4.0,
        "start_date": "2026-06-12", "maturity_date": "2026-07-12"
    }, headers=HEADERS)
    r = client.get("/api/v1/networth/", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "deposits_eur" in body
    assert body["deposits_eur"] == 5000.0
```

- [ ] **Step 2: Run to confirm fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py::test_networth_includes_deposits -v 2>&1 | head -20
```

Expected: FAIL — `'deposits_eur' not in body`.

- [ ] **Step 3: Modify networth.py**

In `portf_server/routers/networth.py`, update `get_networth` to:

```python
@router.get("/")
def get_networth(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """Brokerage value + manual assets/liabilities + deposits + total net worth (EUR)."""
    items = db.get_manual_assets()
    assets_eur = 0.0
    liabilities_eur = 0.0
    out = []
    for it in items:
        amt_eur = float(it["amount"] or 0) * _fx(it.get("currency", "EUR"))
        if it["is_liability"]:
            liabilities_eur += amt_eur
        else:
            assets_eur += amt_eur
        out.append({**it, "amount_eur": round(amt_eur, 2)})

    raw_deposits = db.get_fixed_deposits(status="active")
    deposits_eur = sum(
        float(d["principal"]) * _fx(d.get("currency", "EUR")) for d in raw_deposits
    )

    brokerage = round(_brokerage_value_eur(db), 2)
    net_worth = round(brokerage + assets_eur - liabilities_eur + deposits_eur, 2)
    return {
        "brokerage_eur": brokerage,
        "manual_assets_eur": round(assets_eur, 2),
        "manual_liabilities_eur": round(liabilities_eur, 2),
        "deposits_eur": round(deposits_eur, 2),
        "deposits": raw_deposits,
        "net_worth_eur": net_worth,
        "items": out,
    }
```

- [ ] **Step 4: Run tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/networth.py tests/unit/test_deposits.py
git commit -m "feat: include active fixed deposits in net worth total"
```

---

## Task 5: LLM extraction — GeminiClient method + endpoint

**Files:**
- Modify: `portf_manager/gemini_client.py`
- Modify: `portf_server/routers/llm.py`
- Modify: `tests/unit/test_deposits.py`

- [ ] **Step 1: Add LLM extraction test (mocked)**

Append to `tests/unit/test_deposits.py`:

```python
from unittest.mock import patch, MagicMock


def test_extract_deposits_llm_endpoint(client):
    extracted = [{
        "name": "Superdepósito PREMIUM 1 mes",
        "principal": 5000.0,
        "currency": "EUR",
        "interest_rate": 4.0,
        "start_date": "2026-06-12",
        "maturity_date": "2026-07-12",
    }]
    with patch(
        "portf_server.routers.llm.GeminiClient.extract_deposits",
        return_value=extracted,
    ):
        r = client.post(
            "/api/v1/llm/extract-deposits",
            json={"text": "Superdepósito PREMIUM 1 mes 5000€ TAE 4% vence 12/07/2026"},
            headers=HEADERS,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["deposits"][0]["interest_rate"] == 4.0
```

- [ ] **Step 2: Run to confirm fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py::test_extract_deposits_llm_endpoint -v 2>&1 | head -20
```

Expected: FAIL — 404 or AttributeError.

- [ ] **Step 3: Add `extract_deposits` to GeminiClient**

In `portf_manager/gemini_client.py`, add this method after `extract_bookings`:

```python
    def extract_deposits(self, text: str) -> list:
        """Extract fixed-deposit records from a bank statement or overview text.

        Returns a list of dicts with keys: name, principal, currency,
        interest_rate (annual %), start_date, maturity_date.
        Returns [] on any failure.
        """
        prompt = f"""
You extract FIXED-TERM DEPOSITS (depósitos a plazo fijo) from a bank statement
or product overview. Each deposit is a fixed-term savings product with a
principal amount, an annual interest rate (TAE/APR), a start date and a
maturity date.

Return ONLY a JSON array. Each object has these exact fields:
- name: product name as a string (e.g. "Superdepósito PREMIUM 1 mes")
- principal: deposit amount as a positive float
- currency: ISO currency code (default "EUR")
- interest_rate: annual rate as a percentage float (e.g. 4.0 for 4%)
- start_date: ISO date YYYY-MM-DD (if not stated, use today)
- maturity_date: ISO date YYYY-MM-DD

RULES:
- Return ONLY the JSON array, no prose.
- If no fixed deposits are described, return [].
- "TAE" / "TIN" / "APR" / "rendimiento anual" → interest_rate
- "vence" / "fecha vencimiento" / "maturity" / "plazo" → maturity_date
- European number format: "5.000,00" → 5000.0
- "€" → "EUR", "$" → "USD"

EXAMPLE
Input: "Superdepósito 3 meses 10.000€ TAE 3,5% apertura 01/01/2026 vence 01/04/2026"
Output: [{{"name": "Superdepósito 3 meses", "principal": 10000.0, "currency": "EUR",
           "interest_rate": 3.5, "start_date": "2026-01-01", "maturity_date": "2026-04-01"}}]

Now extract deposits from this text:

{text}
"""
        try:
            response_text = self.llm.generate(prompt).strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(
                    ln for ln in lines if not ln.strip().startswith("```")
                )
            data = json.loads(response_text)
            deposits = []
            for item in data if isinstance(data, list) else []:
                try:
                    deposits.append({
                        "name": str(item.get("name", "")).strip(),
                        "principal": float(item.get("principal", 0)),
                        "currency": str(item.get("currency", "EUR")).upper(),
                        "interest_rate": float(item.get("interest_rate", 0)),
                        "start_date": str(item.get("start_date", "")),
                        "maturity_date": str(item.get("maturity_date", "")),
                    })
                except (TypeError, ValueError):
                    continue
            return deposits
        except Exception:
            logger.exception("extract_deposits failed")
            return []
```

- [ ] **Step 4: Add endpoint to llm.py**

In `portf_server/routers/llm.py`, add the response schema after `BookingExtractionResponse`:

```python
class DepositExtractionResponse(BaseModel):
    """Schema for fixed deposit extraction response."""

    deposits: List[dict] = Field(..., description="Extracted fixed deposits")
    count: int = Field(..., description="Number of deposits extracted")
```

Add the endpoint after `extract_bookings_from_text`:

```python
@router.post("/extract-deposits", response_model=DepositExtractionResponse)
async def extract_deposits_from_text(
    request: TransactionExtractionRequest,
    api_key_info: dict = Depends(get_api_key_auth_for_llm),
):
    """Extract fixed-term deposit records from statement text via LLM."""
    try:
        gemini_client = GeminiClient(llm=get_llm_client())
        deposits = gemini_client.extract_deposits(request.text)
        return DepositExtractionResponse(deposits=deposits, count=len(deposits))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract deposits: {str(e)}",
        )
```

- [ ] **Step 5: Run all deposit tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_deposits.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: 0 failures.

- [ ] **Step 7: Commit**

```bash
git add portf_manager/gemini_client.py portf_server/routers/llm.py tests/unit/test_deposits.py
git commit -m "feat: LLM extract-deposits endpoint + GeminiClient.extract_deposits"
```

---

## Task 6: Web API client methods

**Files:**
- Modify: `web_client/js/pfm_core.js`

- [ ] **Step 1: Add deposit API methods**

In `web_client/js/pfm_core.js`, locate the `deleteManualAsset` method (around line 996) and add the following after it:

```javascript
        async getDeposits() {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/', { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to load deposits');
            return resp.json();
        },
        async createDeposit(payload) {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to create deposit');
            return resp.json();
        },
        async deleteDeposit(id) {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/' + id, { method: 'DELETE', headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error('Failed to delete deposit');
            return resp.json().catch(() => ({}));
        },
        async matureDeposit(id, payload) {
            const resp = await fetch(this.baseURL + '/api/v1/deposits/' + id + '/mature', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'Failed to mature deposit');
            return resp.json();
        },
        async extractDepositsLLM(text) {
            const resp = await fetch(this.baseURL + '/api/v1/llm/extract-deposits', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ text })
            });
            if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || 'LLM extraction failed');
            return resp.json();
        },
```

- [ ] **Step 2: Verify no syntax errors**

```bash
node --check /home/agoldhoorn/repos/pfm/web_client/js/pfm_core.js && echo "OK"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add deposit API client methods to pfm_core.js"
```

---

## Task 7: HTML — deposits section + mature modal

**Files:**
- Modify: `web_client/index.html`

- [ ] **Step 1: Add deposits section and summary tile**

In `web_client/index.html`, locate the summary tiles row in `#networthPage` (the `<div class="row g-3 mb-4">` block at ~line 2108). Add a "Fixed Deposits" tile after the "Other assets" tile:

```html
                        <div class="col-6 col-md-3"><div class="card h-100"><div class="card-body py-3"><div class="small text-muted mb-1">Other assets</div><div class="fs-5 fw-bold text-success" id="nwAssets">—</div></div></div></div>
                        <div class="col-6 col-md-3"><div class="card h-100"><div class="card-body py-3"><div class="small text-muted mb-1">Fixed deposits</div><div class="fs-5 fw-bold text-info" id="nwDeposits">—</div></div></div></div>
```

(Replace the existing "Other assets" line with the two lines above so the tile is inserted between "Other assets" and "Liabilities".)

- [ ] **Step 2: Add the Fixed Deposits card below the existing row**

Locate the closing `</div>` of the `<div class="row g-3">` layout block (the one containing the "Add asset or liability" form and the items table, ends around line 2180). Insert the Fixed Deposits card **before** the closing `</div>` of that row:

```html
                    <!-- Fixed Deposits section -->
                    <div class="col-12 mt-2">
                        <div class="card">
                            <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                <span><i class="bi bi-bank me-2"></i>Fixed Deposits</span>
                            </div>
                            <div class="table-responsive">
                                <table class="table table-hover mb-0">
                                    <thead><tr>
                                        <th class="ps-3">Name</th>
                                        <th>Broker</th>
                                        <th class="text-end">Principal</th>
                                        <th class="text-end">Rate</th>
                                        <th>Maturity</th>
                                        <th class="text-end">Projected Interest</th>
                                        <th>Status</th>
                                        <th class="pe-3"></th>
                                    </tr></thead>
                                    <tbody id="nwDepositsBody"><tr><td colspan="8" class="text-center text-muted py-3">No fixed deposits yet.</td></tr></tbody>
                                </table>
                            </div>
                            <!-- Add deposit form -->
                            <div class="card-body border-top">
                                <p class="small text-muted mb-2">Add a fixed-term deposit manually:</p>
                                <form id="nwDepositForm" class="row g-2 align-items-end">
                                    <div class="col-12 col-sm-4">
                                        <label class="form-label small mb-1">Name *</label>
                                        <input class="form-control form-control-sm" id="depName" placeholder="e.g. Superdepósito 1 mes" required>
                                    </div>
                                    <div class="col-6 col-sm-2">
                                        <label class="form-label small mb-1">Principal *</label>
                                        <input type="number" step="any" class="form-control form-control-sm" id="depPrincipal" placeholder="5000" required>
                                    </div>
                                    <div class="col-6 col-sm-1">
                                        <label class="form-label small mb-1">Currency</label>
                                        <input class="form-control form-control-sm" id="depCurrency" value="EUR" maxlength="3">
                                    </div>
                                    <div class="col-6 col-sm-1">
                                        <label class="form-label small mb-1">Rate % *</label>
                                        <input type="number" step="any" class="form-control form-control-sm" id="depRate" placeholder="4.0" required>
                                    </div>
                                    <div class="col-6 col-sm-2">
                                        <label class="form-label small mb-1">Start date *</label>
                                        <input type="date" class="form-control form-control-sm" id="depStart" required>
                                    </div>
                                    <div class="col-6 col-sm-2">
                                        <label class="form-label small mb-1">Maturity date *</label>
                                        <input type="date" class="form-control form-control-sm" id="depMaturity" required>
                                    </div>
                                    <div class="col-12 col-sm-2">
                                        <button type="submit" class="btn btn-sm btn-primary w-100"><i class="bi bi-plus-lg me-1"></i>Add deposit</button>
                                    </div>
                                    <div class="col-12"><div class="small" id="depAddStatus"></div></div>
                                </form>
                                <!-- LLM paste panel -->
                                <div class="mt-3">
                                    <label class="form-label small mb-1"><i class="bi bi-magic me-1"></i>Or paste a statement and let AI extract deposits:</label>
                                    <textarea class="form-control form-control-sm mb-2" id="depLlmText" rows="3" placeholder="Paste bank statement or deposit overview here…"></textarea>
                                    <div class="d-flex gap-2 align-items-center">
                                        <button class="btn btn-sm btn-outline-secondary" id="depExtractBtn"><i class="bi bi-cpu me-1"></i>Extract deposits</button>
                                        <span class="small text-muted" id="depExtractStatus"></span>
                                    </div>
                                    <div id="depExtractPreview" class="mt-2"></div>
                                </div>
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 3: Add the mature modal**

Insert this modal just before the closing `</body>` tag (or alongside the other modals, e.g. after the last modal in the file):

```html
    <!-- Mature Deposit Modal -->
    <div class="modal fade" id="depositMatureModal" tabindex="-1">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title"><i class="bi bi-check-circle me-2"></i>Mark Deposit as Matured</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="matureDepositId">
                    <div class="mb-3">
                        <label class="form-label">Actual interest paid</label>
                        <div class="input-group">
                            <span class="input-group-text">€</span>
                            <input type="number" step="any" class="form-control" id="matureInterestPaid" placeholder="8.35">
                        </div>
                        <div class="form-text">This will be recorded as an interest transaction.</div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Payout date</label>
                        <input type="date" class="form-control" id="maturePayoutDate">
                    </div>
                    <div class="small text-danger" id="matureDepositError"></div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                    <button type="button" class="btn btn-success" id="matureDepositConfirm"><i class="bi bi-check-lg me-1"></i>Confirm maturity</button>
                </div>
            </div>
        </div>
    </div>
```

- [ ] **Step 4: Verify HTML is well-formed**

```bash
node --input-type=module <<'EOF'
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
const html = readFileSync('/home/agoldhoorn/repos/pfm/web_client/index.html', 'utf8');
const dom = new JSDOM(html);
const errs = dom.window.document.querySelectorAll('parsererror');
console.log(errs.length === 0 ? 'HTML OK' : 'ERRORS: ' + errs.length);
EOF
```

If `jsdom` is not available, visually inspect the added HTML blocks for unclosed tags.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html
git commit -m "feat: deposits HTML section + mature modal on Net Worth page"
```

---

## Task 8: JS — deposits logic in pfm_analytics.js

**Files:**
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Update `loadNetworthPage` to render deposits**

In `pfm_analytics.js`, locate the `loadNetworthPage` function (line 20). Replace the `try` block content to also load deposits:

```javascript
async function loadNetworthPage() {
    const $ = id => document.getElementById(id);
    _wireNetworthForm();
    _wireDepositForm();
    const body = $('nwItemsBody');
    if (body) body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</td></tr>';
    try {
        const d = await window.apiClient.getNetworth();
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        $('nwBrokerage').innerHTML = eur(d.brokerage_eur);
        $('nwAssets').innerHTML = eur(d.manual_assets_eur);
        if ($('nwDeposits')) $('nwDeposits').innerHTML = eur(d.deposits_eur || 0);
        $('nwLiabilities').innerHTML = eur(d.manual_liabilities_eur);
        $('nwTotal').innerHTML = eur(d.net_worth_eur);
        const card = $('nwTotalCard');
        if (card) card.style.background = d.net_worth_eur >= 0 ? '#0d6efd' : '#dc3545';
        const items = (d.items || []).slice()
            .sort((a, b) => (a.is_liability ? 1 : 0) - (b.is_liability ? 1 : 0));
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No off-brokerage items yet. Add cash, property, a mortgage… on the left.</td></tr>';
        } else {
            body.innerHTML = items.map(it => `
                <tr>
                    <td class="ps-3"><strong>${escapeForAttr(it.name)}</strong>${it.notes ? `<br><small class="text-muted">${escapeForAttr(it.notes)}</small>` : ''}</td>
                    <td><span class="badge ${it.is_liability ? 'bg-danger' : 'bg-secondary'}">${NW_CATEGORY_LABELS[it.category] || it.category}</span></td>
                    <td class="text-end">${Fmt.num(it.amount, 2, 2)} ${it.currency || ''}</td>
                    <td class="text-end ${it.is_liability ? 'text-danger' : ''}">${it.is_liability ? '−' : ''}${Fmt.amt('€' + Fmt.num(it.amount_eur, 0, 0))}</td>
                    <td class="pe-3 text-end"><button class="btn btn-sm btn-outline-danger" onclick="confirmDeleteManualAsset(${it.id})"><i class="bi bi-trash"></i></button></td>
                </tr>`).join('');
        }
        _renderDeposits(d.deposits || []);
    } catch (err) {
        if (body) body.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-3">${err.message}</td></tr>`;
    }
}
```

- [ ] **Step 2: Add deposit rendering and wiring functions**

After the `window.confirmDeleteManualAsset` definition, add:

```javascript
function _renderDeposits(deposits) {
    const tbody = document.getElementById('nwDepositsBody');
    if (!tbody) return;
    if (!deposits.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">No fixed deposits yet.</td></tr>';
        return;
    }
    const today = new Date().toISOString().slice(0, 10);
    tbody.innerHTML = deposits.map(d => {
        const statusBadge = d.status === 'active'
            ? '<span class="badge bg-success">Active</span>'
            : '<span class="badge bg-secondary">' + d.status.charAt(0).toUpperCase() + d.status.slice(1) + '</span>';
        const matureBtn = d.status === 'active'
            ? `<button class="btn btn-sm btn-outline-success me-1" onclick="openMatureDepositModal(${d.id}, ${d.projected_interest}, '${d.maturity_date}')"><i class="bi bi-check-circle"></i></button>`
            : '';
        return `<tr>
            <td class="ps-3"><strong>${escapeForAttr(d.name)}</strong>${d.notes ? `<br><small class="text-muted">${escapeForAttr(d.notes)}</small>` : ''}</td>
            <td>${d.portfolio_id ? escapeForAttr(d.portfolio_id) : '<span class="text-muted">—</span>'}</td>
            <td class="text-end">${Fmt.num(d.principal, 2, 2)} ${d.currency}</td>
            <td class="text-end">${Fmt.num(d.interest_rate, 2, 2)}%</td>
            <td>${d.maturity_date}</td>
            <td class="text-end">${Fmt.num(d.projected_interest, 2, 2)} ${d.currency}</td>
            <td>${statusBadge}</td>
            <td class="pe-3 text-end">${matureBtn}<button class="btn btn-sm btn-outline-danger" onclick="confirmDeleteDeposit(${d.id})"><i class="bi bi-trash"></i></button></td>
        </tr>`;
    }).join('');
}

function _wireDepositForm() {
    const form = document.getElementById('nwDepositForm');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = document.getElementById('depAddStatus');
            const payload = {
                name: document.getElementById('depName').value.trim(),
                principal: parseFloat(document.getElementById('depPrincipal').value) || 0,
                currency: (document.getElementById('depCurrency').value || 'EUR').toUpperCase(),
                interest_rate: parseFloat(document.getElementById('depRate').value) || 0,
                start_date: document.getElementById('depStart').value,
                maturity_date: document.getElementById('depMaturity').value,
            };
            if (!payload.name || !payload.start_date || !payload.maturity_date) return;
            status.className = 'small text-muted'; status.textContent = 'Adding…';
            try {
                await window.apiClient.createDeposit(payload);
                form.reset();
                document.getElementById('depCurrency').value = 'EUR';
                status.textContent = '';
                loadNetworthPage();
            } catch (err) { status.className = 'small text-danger'; status.textContent = err.message; }
        });
    }

    const extractBtn = document.getElementById('depExtractBtn');
    if (extractBtn && !extractBtn.dataset.wired) {
        extractBtn.dataset.wired = '1';
        extractBtn.addEventListener('click', async () => {
            const text = document.getElementById('depLlmText').value.trim();
            const statusEl = document.getElementById('depExtractStatus');
            const preview = document.getElementById('depExtractPreview');
            if (!text) return;
            statusEl.textContent = 'Extracting…';
            preview.innerHTML = '';
            try {
                const result = await window.apiClient.extractDepositsLLM(text);
                statusEl.textContent = '';
                if (!result.deposits.length) {
                    preview.innerHTML = '<p class="small text-muted">No deposits found in the text.</p>';
                    return;
                }
                preview.innerHTML = `
                    <table class="table table-sm table-bordered mt-2 mb-2">
                        <thead><tr><th>Name</th><th>Principal</th><th>Rate</th><th>Start</th><th>Maturity</th></tr></thead>
                        <tbody>${result.deposits.map((d, i) => `
                            <tr>
                                <td>${escapeForAttr(d.name)}</td>
                                <td>${Fmt.num(d.principal, 2, 2)} ${d.currency}</td>
                                <td>${d.interest_rate}%</td>
                                <td>${d.start_date}</td>
                                <td>${d.maturity_date}</td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
                    <button class="btn btn-sm btn-primary" id="depSaveExtracted">
                        <i class="bi bi-cloud-upload me-1"></i>Save all (${result.deposits.length})
                    </button>
                    <span class="small text-muted ms-2" id="depSaveStatus"></span>`;
                document.getElementById('depSaveExtracted').addEventListener('click', async () => {
                    const saveStatus = document.getElementById('depSaveStatus');
                    saveStatus.textContent = 'Saving…';
                    try {
                        for (const d of result.deposits) {
                            await window.apiClient.createDeposit(d);
                        }
                        preview.innerHTML = '';
                        document.getElementById('depLlmText').value = '';
                        statusEl.textContent = `${result.deposits.length} deposit(s) saved.`;
                        loadNetworthPage();
                    } catch (err) { saveStatus.textContent = 'Error: ' + err.message; }
                });
            } catch (err) { statusEl.textContent = 'Error: ' + err.message; }
        });
    }
}

window.confirmDeleteDeposit = async function (id) {
    if (!confirm('Delete this deposit?')) return;
    try { await window.apiClient.deleteDeposit(id); loadNetworthPage(); }
    catch (err) { alert('Error: ' + err.message); }
};

window.openMatureDepositModal = function (id, projectedInterest, maturityDate) {
    document.getElementById('matureDepositId').value = id;
    document.getElementById('matureInterestPaid').value = projectedInterest;
    document.getElementById('maturePayoutDate').value = maturityDate;
    document.getElementById('matureDepositError').textContent = '';

    const confirmBtn = document.getElementById('matureDepositConfirm');
    const newBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);
    newBtn.addEventListener('click', async () => {
        const depId = parseInt(document.getElementById('matureDepositId').value);
        const interest = parseFloat(document.getElementById('matureInterestPaid').value);
        const dt = document.getElementById('maturePayoutDate').value;
        const errEl = document.getElementById('matureDepositError');
        if (!dt || isNaN(interest)) { errEl.textContent = 'Please fill in all fields.'; return; }
        newBtn.disabled = true;
        try {
            await window.apiClient.matureDeposit(depId, { interest_paid: interest, date: dt });
            bootstrap.Modal.getInstance(document.getElementById('depositMatureModal')).hide();
            window.showToast(`Interest of ${Fmt.num(interest, 2, 2)} recorded.`, 'success');
            loadNetworthPage();
        } catch (err) { errEl.textContent = err.message; newBtn.disabled = false; }
    });

    new bootstrap.Modal(document.getElementById('depositMatureModal')).show();
};
```

- [ ] **Step 3: Check JS for syntax errors**

```bash
node --check /home/agoldhoorn/repos/pfm/web_client/js/pfm_analytics.js && echo "OK"
```

Expected: `OK`

- [ ] **Step 4: Run JS test suite**

```bash
make test-js 2>&1 | tail -10
```

Expected: all pass (the existing load/smoke test will catch broken syntax).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: deposits UI on Net Worth page (table, add form, mature modal, LLM extract)"
```

---

## Task 9: Deploy + smoke test

**Files:** none changed — runtime rebuild only.

- [ ] **Step 1: Bump web cache-buster versions**

In `web_client/index.html`, find the `?v=` version strings on the `<script>` tags that load `pfm_analytics.js` and `pfm_core.js` and increment the version by 1 (e.g. `?v=12` → `?v=13`).

- [ ] **Step 2: Restart backend**

```bash
docker compose restart portf_backend_dev
```

Wait ~5 seconds, then verify the DB migration ran:

```bash
docker exec portf_backend_dev python3 -c "
from portf_manager.database import Database
db = Database('/app/portfolio.db')
with db.get_connection() as c:
    v = c.execute('PRAGMA user_version').fetchone()[0]
    tables = [r['name'] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]
print('version:', v)
print('fixed_deposits in tables:', 'fixed_deposits' in tables)
"
```

Expected output:
```
version: 19
fixed_deposits in tables: True
```

- [ ] **Step 3: Rebuild and redeploy web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

- [ ] **Step 4: Smoke test in browser**

1. Navigate to **Planning → Net Worth**
2. Verify the "Fixed Deposits" summary tile shows `€0`
3. Add a deposit via the form: name "Test Dep", principal 1000, rate 4, start today, maturity in 30 days
4. Verify the deposit appears in the table with a projected interest value
5. Click **Mature**, enter an interest amount, confirm → verify toast "Interest of X recorded" appears
6. Navigate to **Analytics → Dividends** → verify the interest entry appears in income
7. Verify the Net Worth tile reflects the `+€1,000` increase while the deposit is active

- [ ] **Step 5: Run full test suite one last time**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -5
```

Expected: 0 failures.

- [ ] **Step 6: Final commit**

```bash
git add web_client/index.html
git commit -m "chore: bump cache-buster versions for pfm_core, pfm_analytics after fixed-deposits feature"
```
