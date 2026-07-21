# Bank Account Balance in Net Worth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bank accounts tracked via the Spending feature should contribute their balance to Net Worth automatically, derived from the latest imported statement row that has one — mirroring how brokerage positions are already automatic rather than manually re-entered. Accounts with no balance-bearing import stay excluded (not silently zero) and are flagged by the existing setup checklist; manual assets remain the fallback for anything with no account integration.

**Architecture:** The CSV parser already parses an optional `balance` column into `SpendingRow.balance` but nothing persists it. Add a nullable `balance REAL` column to `spending_transactions` (migration v26), thread it through upload/save, and add a DB helper that returns a bank portfolio's most recent balance-bearing row. `net_worth_eur(db)` (the single shared helper Goals also imports) and `GET /api/v1/networth/` derive and sum bank-account balances the same way they already derive brokerage value. The pure `computeNetWorthChecklist` gains two additive fields (missing-balance accounts, a manual/imported double-counting nudge) without changing its existing return shape, so the Action Items merge that already consumes `.checklist`/`.attention` is unaffected. A new "Bank Accounts" card on the Net Worth page displays the derived balances.

**Tech Stack:** Python 3.13 / SQLite / FastAPI / Pydantic / Vanilla JS / Bootstrap 5 / pytest / `uv run` / Node built-in test runner

## Global Constraints

- Black formatting (line length 88): `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black <file>`
- Type hints on all function signatures; Google-style docstrings; comments on the line before the code, not inline
- All Python commands: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run ...` (the `.venv` is root-owned)
- flake8 clean: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 <file> --max-line-length=88 --extend-ignore=E203,W503,E501`
- New DB column must land in **both** `_create_all_tables` (fresh DBs) and `_migrate_to_v26` (existing DBs) — migration-only adds break fresh installs/tests
- `DATABASE_VERSION` is currently `25` — bump to `26`
- Conventional commits (`feat:`, `test:`, `docs:`), each ending with `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Web changes only go live after: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`
- Python changes only go live after: `docker exec portf_backend_dev kill -HUP 1`
- No real personal/financial data anywhere (tests, fixtures) — fictional data only
- Public repo: no home-directory paths (`/home/agoldhoorn/` → `~/`) in anything committed
- Tests that touch `_get_fx_rate`/`_fx` should use `currency="EUR"` throughout — it short-circuits to `1.0` with no network call (`portf_server/routers/portfolios.py:58-59`); no FX mocking needed

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `portf_manager/database.py` | Modify | `balance` column on `spending_transactions` (migration v26), `create_spending_transaction` accepts it, new `get_latest_bank_balance` helper |
| `tests/unit/test_spending_db.py` | Modify | DB-layer tests for balance storage + `get_latest_bank_balance` |
| `tests/test_database.py` | Modify | Bump version assertions to 26 |
| `portf_server/routers/spending.py` | Modify | `PreviewSpendingRow`/`SpendingTransactionResponse` gain `balance`; upload/save thread it through |
| `tests/unit/test_spending_api.py` | Modify | API-layer tests for balance round-trip |
| `portf_server/routers/networth.py` | Modify | `net_worth_eur(db)` + `GET /api/v1/networth/` derive and include bank-account balances |
| `tests/unit/test_networth.py` | Modify | API tests for bank-account balance in net worth |
| `web_client/js/pfm_analytics.js` | Modify | `computeNetWorthChecklist` gains `missingBalanceAccounts`/`duplicateWarning`; `_renderChecklist` renders them; new `_renderBankAccounts`; `loadNetworthPage` wiring; new Bank Accounts card |
| `web_client/js/tests/web_client.test.mjs` | Modify | Unit tests for the two new `computeNetWorthChecklist` fields |
| `web_client/index.html` | Modify | New "Bank Accounts" card on the Net Worth page |
| `PROJECT_STATUS.md` | Modify | Bump summary line |
| `CLAUDE.md` | Modify | Extend `### Spending Tracking` and `### Net Worth API` sections, bump schema version, add v26 migration-history line |

---

## Task 1: DB layer — `balance` column + `get_latest_bank_balance`

**Files:**
- Modify: `portf_manager/database.py`
- Modify: `tests/unit/test_spending_db.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_spending_db.py`:

```python
def test_create_spending_transaction_with_balance(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        pid, "2026-01-05", "MERCADONA COMPRA", -24.50, balance=475.50
    )
    row = db.get_spending_transaction(tx_id)
    assert row["balance"] == 475.50


def test_create_spending_transaction_balance_defaults_none(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    row = db.get_spending_transaction(tx_id)
    assert row["balance"] is None


def test_get_latest_bank_balance_picks_most_recent_date(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "A", -10.0, balance=100.0)
    db.create_spending_transaction(pid, "2026-01-10", "B", -20.0, balance=80.0)
    db.create_spending_transaction(pid, "2026-01-07", "C", -5.0, balance=95.0)
    latest = db.get_latest_bank_balance(pid)
    assert latest["date"] == "2026-01-10"
    assert latest["balance"] == 80.0


def test_get_latest_bank_balance_ties_broken_by_id(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-10", "Morning", -10.0, balance=90.0)
    id2 = db.create_spending_transaction(pid, "2026-01-10", "Evening", -5.0, balance=85.0)
    latest = db.get_latest_bank_balance(pid)
    assert latest["balance"] == 85.0
    assert id2 > 0  # sanity: second row really was inserted after the first


def test_get_latest_bank_balance_ignores_null_balance_rows(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "A", -10.0, balance=100.0)
    db.create_spending_transaction(pid, "2026-01-10", "B (no balance)", -20.0)
    latest = db.get_latest_bank_balance(pid)
    assert latest["date"] == "2026-01-05"
    assert latest["balance"] == 100.0


def test_get_latest_bank_balance_no_rows_returns_none(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    assert db.get_latest_bank_balance(pid) is None


def test_get_latest_bank_balance_scoped_to_portfolio(db):
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    db.create_spending_transaction(pid_a, "2026-01-05", "A", -10.0, balance=100.0)
    assert db.get_latest_bank_balance(pid_b) is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -v 2>&1 | tail -30
```

Expected: `create_spending_transaction() got an unexpected keyword argument 'balance'`, and `'Database' object has no attribute 'get_latest_bank_balance'`.

- [ ] **Step 3: Bump `DATABASE_VERSION` to 26**

In `portf_manager/database.py:17`:

```python
DATABASE_VERSION = 26
```

- [ ] **Step 4: Add `balance` column to `spending_transactions` in `_create_all_tables`**

In `portf_manager/database.py`, the `spending_transactions` table definition (around line 604):

```python
            CREATE TABLE IF NOT EXISTS spending_transactions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id       INTEGER NOT NULL,
                date               DATE NOT NULL,
                description        TEXT NOT NULL,
                amount             REAL NOT NULL,
                currency           TEXT NOT NULL DEFAULT 'EUR',
                category           TEXT NOT NULL DEFAULT 'uncategorized',
                is_transfer        INTEGER NOT NULL DEFAULT 0,
                transfer_link_type TEXT CHECK (transfer_link_type IN ('spending', 'booking')),
                transfer_link_id   INTEGER,
                source             TEXT,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios (id) ON DELETE CASCADE
            )
```

Add a `balance` column after `source` (before `created_at`):

```python
            CREATE TABLE IF NOT EXISTS spending_transactions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id       INTEGER NOT NULL,
                date               DATE NOT NULL,
                description        TEXT NOT NULL,
                amount             REAL NOT NULL,
                currency           TEXT NOT NULL DEFAULT 'EUR',
                category           TEXT NOT NULL DEFAULT 'uncategorized',
                is_transfer        INTEGER NOT NULL DEFAULT 0,
                transfer_link_type TEXT CHECK (transfer_link_type IN ('spending', 'booking')),
                transfer_link_id   INTEGER,
                source             TEXT,
                balance            REAL,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios (id) ON DELETE CASCADE
            )
```

There are two occurrences of this exact table definition in the file — one in `_create_all_tables` (~line 604) and one repeated verbatim inside `_migrate_to_v25` (~line 1434, since that migration also creates the table for pre-v25 databases). Update **both** occurrences identically, or the fresh-DB and migrated-DB schemas will diverge (`_migrate_to_v25` runs `CREATE TABLE IF NOT EXISTS`, so on a fresh DB it's a no-op, but on a DB that's upgrading from below v25 in one hop it would create the table WITHOUT the new column, then `_migrate_to_v26`'s `_add_column_if_missing` call in Step 5 below adds it — so this is actually safe either way, but keep both occurrences textually in sync for readability and to avoid confusion later).

- [ ] **Step 5: Add `_migrate_to_v26()` method**

In `portf_manager/database.py`, directly after `_migrate_to_v25` (ends around line 1461, right before the `# ── App settings` comment), add:

```python

    def _migrate_to_v26(self, conn: sqlite3.Connection) -> None:
        """Migrate from v25 to v26 — bank-account balance tracking.

        Adds spending_transactions.balance (nullable), populated from the
        optional `balance` column in imported bank-statement CSVs, so Net
        Worth can derive a bank account's current balance from the most
        recent imported row rather than requiring manual entry.
        """
        _add_column_if_missing(conn, "spending_transactions", "balance", "REAL")
        conn.commit()
```

- [ ] **Step 6: Wire migration into `_run_migrations()`**

In `portf_manager/database.py`, find (around line 704):

```python
        if current_version < 25:
            self._migrate_to_v25(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

Replace with:

```python
        if current_version < 25:
            self._migrate_to_v25(conn)
        if current_version < 26:
            self._migrate_to_v26(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

- [ ] **Step 7: Update `create_spending_transaction` to accept `balance`**

In `portf_manager/database.py` (around line 2650):

```python
    def create_spending_transaction(
        self,
        portfolio_id: int,
        date: str,
        description: str,
        amount: float,
        currency: str = "EUR",
        category: str = "uncategorized",
        source: str = None,
    ) -> int:
        """Create a spending transaction.

        Args:
            amount: Signed amount — negative = money out, positive = money in
                (bank-statement convention, not the bookings Deposit/Withdrawal
                text convention).
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO spending_transactions
                    (portfolio_id, date, description, amount, currency, category, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    date,
                    description,
                    amount,
                    currency.upper(),
                    category,
                    source,
                ),
            )
            conn.commit()
            return cursor.lastrowid
```

Replace with (adds `balance` param, threaded into the INSERT):

```python
    def create_spending_transaction(
        self,
        portfolio_id: int,
        date: str,
        description: str,
        amount: float,
        currency: str = "EUR",
        category: str = "uncategorized",
        source: str = None,
        balance: float = None,
    ) -> int:
        """Create a spending transaction.

        Args:
            amount: Signed amount — negative = money out, positive = money in
                (bank-statement convention, not the bookings Deposit/Withdrawal
                text convention).
            balance: Optional running account balance as of this transaction,
                from the bank statement's own balance column when present.
                Used by Net Worth to derive a bank account's current balance
                from the most recent row that has one — see
                `get_latest_bank_balance`.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO spending_transactions
                    (portfolio_id, date, description, amount, currency,
                     category, source, balance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio_id,
                    date,
                    description,
                    amount,
                    currency.upper(),
                    category,
                    source,
                    balance,
                ),
            )
            conn.commit()
            return cursor.lastrowid
```

- [ ] **Step 8: Add `get_latest_bank_balance`**

In `portf_manager/database.py`, immediately after `list_unlinked_spending_transactions` (around line 2777, before `delete_spending_transaction`):

```python
    def list_unlinked_spending_transactions(self) -> List[Dict]:
        """List spending rows not yet linked as a transfer (is_transfer = 0)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_transactions WHERE is_transfer = 0"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_bank_balance(self, portfolio_id: int) -> Optional[Dict]:
        """Return the most recent balance-bearing spending row for an account.

        Used by Net Worth to derive a bank account's current balance without
        requiring manual entry. Ties (multiple same-day rows, as most bank
        statements produce) are broken by highest id, i.e. the last row
        inserted for that date — correct as long as a statement's rows are
        imported in their original chronological order, which the generic
        bank CSV parser preserves.

        Returns:
            Dict with `date`, `balance`, `currency`, or None if this
            portfolio has no spending_transactions row with a non-null
            balance yet.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT date, balance, currency FROM spending_transactions
                WHERE portfolio_id = ? AND balance IS NOT NULL
                ORDER BY date DESC, id DESC
                LIMIT 1
                """,
                (portfolio_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
```

- [ ] **Step 9: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -v 2>&1 | tail -30
```

Expected: all pass (6 new tests).

- [ ] **Step 10: Bump version assertions in `test_database.py`**

In `tests/test_database.py`, there are four `== 25` occurrences (one with a trailing comment at the fresh-db check, three bare `assert version == 25` elsewhere) — bump all four to `== 26`, same pattern as the v24→v25 bump. Use `replace_all=True` on the Edit tool for the bare `assert version == 25` pattern (3 occurrences), handle the commented one separately.

- [ ] **Step 11: Run the full DB test suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/test_database.py tests/unit/test_spending_db.py -v 2>&1 | tail -20
```

Expected: all pass, no `== 25` failures.

- [ ] **Step 12: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/database.py tests/unit/test_spending_db.py tests/test_database.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/database.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 13: Commit**

```bash
git add portf_manager/database.py tests/unit/test_spending_db.py tests/test_database.py
git commit -m "feat: add spending_transactions.balance column and get_latest_bank_balance (db v26)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Thread `balance` through the spending router

**Files:**
- Modify: `portf_server/routers/spending.py`
- Modify: `tests/unit/test_spending_api.py`

**Depends on Task 1.**

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_spending_api.py`:

```python
def test_upload_preview_includes_balance(tmp_path):
    client, _ = _make_client(tmp_path)
    csv_text = "date,description,amount,balance\n2026-01-05,MERCADONA,-24.50,475.50\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["rows"][0]["balance"] == 475.50


def test_upload_preview_balance_none_when_absent(tmp_path):
    client, _ = _make_client(tmp_path)
    csv_text = "date,description,amount\n2026-01-05,MERCADONA,-24.50\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.json()["rows"][0]["balance"] is None


def test_save_persists_balance(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    r = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid,
            "rows": [
                {
                    "date": "2026-01-05",
                    "description": "MERCADONA",
                    "amount": -24.50,
                    "currency": "EUR",
                    "category": "Groceries",
                    "balance": 475.50,
                },
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["saved"] == 1
    listed = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert listed[0]["balance"] == 475.50
```

(`_csv_bytes`, `_make_client`, `HEADERS` already exist in this test file — reuse them, don't redefine.)

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v -k balance 2>&1 | tail -30
```

Expected: `KeyError: 'balance'` (Pydantic drops unknown fields silently by default, so `rows[0]["balance"]` won't exist in the response yet) or a `ValidationError` on the save request body, depending on which assertion fails first.

- [ ] **Step 3: Add `balance` to `PreviewSpendingRow` and `SpendingTransactionResponse`**

In `portf_server/routers/spending.py` (around line 55):

```python
class PreviewSpendingRow(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    category: str = "uncategorized"
    is_duplicate: bool = False
```

Replace with:

```python
class PreviewSpendingRow(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    category: str = "uncategorized"
    is_duplicate: bool = False
    balance: Optional[float] = None
```

And (around line 86):

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
```

- [ ] **Step 4: Populate `balance` in the upload preview**

In `upload_bank_statement` (around line 189), find:

```python
        rows.append(
            PreviewSpendingRow(
                date=r.date,
                description=r.description,
                amount=r.amount,
                currency=r.currency,
                category=category,
                is_duplicate=is_dup,
            )
        )
```

Replace with:

```python
        rows.append(
            PreviewSpendingRow(
                date=r.date,
                description=r.description,
                amount=r.amount,
                currency=r.currency,
                category=category,
                is_duplicate=is_dup,
                balance=r.balance,
            )
        )
```

(`r` here is a `SpendingRow` from the parser, which already has `.balance` — see `portf_manager/parsers/generic_bank_csv_parser.py`.)

- [ ] **Step 5: Persist `balance` on save**

In `save_spending_transactions` (around line 288), find:

```python
            new_id = db.create_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                description=row.description,
                amount=row.amount,
                currency=row.currency,
                category=row.category,
                source="generic",
            )
```

Replace with:

```python
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

- [ ] **Step 6: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v 2>&1 | tail -30
```

Expected: all pass (21 total in this file).

- [ ] **Step 7: Format, lint, full suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/spending.py tests/unit/test_spending_api.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/spending.py --max-line-length=88 --extend-ignore=E203,W503,E501
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: no lint errors; full suite passes, count higher than before.

- [ ] **Step 8: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: persist and expose imported balance on spending transactions

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: Derive bank-account balances in Net Worth

**Files:**
- Modify: `portf_server/routers/networth.py`
- Modify: `tests/unit/test_networth.py`

**Depends on Task 1** (`db.get_latest_bank_balance`, `db.get_all_portfolios` already exists).

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_networth.py`:

```python
def test_networth_includes_bank_account_balance(tmp_path):
    client = _make_client(tmp_path)
    from portf_server.app import app
    from portf_server.dependencies import get_database

    db = app.dependency_overrides[get_database]()
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "A", -10.0, balance=100.0)
    db.create_spending_transaction(pid, "2026-01-10", "B", -20.0, balance=475.50)

    r = client.get("/api/v1/networth/", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["bank_accounts_eur"] == 475.50
    assert len(d["bank_accounts"]) == 1
    acct = d["bank_accounts"][0]
    assert acct["portfolio_id"] == pid
    assert acct["name"] == "Example Bank"
    assert acct["balance"] == 475.50
    assert acct["balance_eur"] == 475.50
    assert acct["as_of"] == "2026-01-10"
    assert d["net_worth_eur"] == 475.50


def test_networth_excludes_bank_account_with_no_balance(tmp_path):
    client = _make_client(tmp_path)
    from portf_server.app import app
    from portf_server.dependencies import get_database

    db = app.dependency_overrides[get_database]()
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "No balance column", -10.0)

    r = client.get("/api/v1/networth/", headers=HEADERS)
    d = r.json()
    assert d["bank_accounts_eur"] == 0.0
    assert len(d["bank_accounts"]) == 1
    acct = d["bank_accounts"][0]
    assert acct["balance"] is None
    assert acct["balance_eur"] is None
    assert d["net_worth_eur"] == 0.0


def test_networth_ignores_brokerage_portfolios_for_bank_accounts(tmp_path):
    client = _make_client(tmp_path)
    from portf_server.app import app
    from portf_server.dependencies import get_database

    db = app.dependency_overrides[get_database]()
    db.create_portfolio("Example Broker", account_type="brokerage")

    r = client.get("/api/v1/networth/", headers=HEADERS)
    d = r.json()
    assert d["bank_accounts"] == []
    assert d["bank_accounts_eur"] == 0.0


def test_networth_sums_multiple_bank_accounts(tmp_path):
    client = _make_client(tmp_path)
    from portf_server.app import app
    from portf_server.dependencies import get_database

    db = app.dependency_overrides[get_database]()
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    db.create_spending_transaction(pid_a, "2026-01-05", "A", -10.0, balance=300.0)
    db.create_spending_transaction(pid_b, "2026-01-05", "B", -10.0, balance=200.0)

    r = client.get("/api/v1/networth/", headers=HEADERS)
    d = r.json()
    assert d["bank_accounts_eur"] == 500.0
    assert d["net_worth_eur"] == 500.0
```

Confirmed: `_make_client` in this file returns just `TestClient(app)` (not a tuple, unlike `test_spending_api.py`'s version) with `app.dependency_overrides[get_database] = lambda: db_instance` already set — the snippets above correctly retrieve the same `Database` instance via `app.dependency_overrides[get_database]()` (calling the lambda), no adjustment needed.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_networth.py -v -k bank_account 2>&1 | tail -30
```

Expected: `KeyError: 'bank_accounts_eur'`.

- [ ] **Step 3: Add `_bank_accounts_eur` helper**

In `portf_server/routers/networth.py`, immediately after `_brokerage_value_eur` (around line 75, before `net_worth_eur`):

```python
def _brokerage_value_eur(db) -> float:
    """EUR value of currently-held tracked positions."""
    positions, _ = compute_positions(db.get_all_transactions())
    total = 0.0
    for aid, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(aid)
        if not asset:
            continue
        pd_ = db.get_latest_price(aid)
        price = float(pd_["price"]) if pd_ else 0.0
        total += pos["quantity"] * price * _fx(asset.get("currency", "EUR"))
    return total
```

Add immediately after it:

```python
def _bank_accounts_eur(db) -> tuple:
    """EUR total + per-account detail for bank-type portfolios.

    Each account's balance is derived from the most recent imported
    spending-statement row that has one (see
    `Database.get_latest_bank_balance`) — mirroring how brokerage value is
    derived from transactions rather than manually re-entered. An account
    with no balance-bearing import yet is excluded from the EUR total (not
    silently counted as zero) and reported with `balance: None` so the
    frontend's setup checklist can flag it instead.

    Returns:
        (total_eur, accounts) where accounts is a list of dicts with
        portfolio_id, name, balance, currency, balance_eur, as_of — the
        latter four are None for an account with no balance data yet.
    """
    bank_portfolios = [
        p for p in db.get_all_portfolios() if p.get("account_type") == "bank"
    ]
    total_eur = 0.0
    accounts = []
    for p in bank_portfolios:
        latest = db.get_latest_bank_balance(p["id"])
        if latest is None:
            accounts.append(
                {
                    "portfolio_id": p["id"],
                    "name": p["name"],
                    "balance": None,
                    "currency": None,
                    "balance_eur": None,
                    "as_of": None,
                }
            )
            continue
        amt_eur = float(latest["balance"]) * _fx(latest.get("currency", "EUR"))
        total_eur += amt_eur
        accounts.append(
            {
                "portfolio_id": p["id"],
                "name": p["name"],
                "balance": latest["balance"],
                "currency": latest.get("currency", "EUR"),
                "balance_eur": round(amt_eur, 2),
                "as_of": latest["date"],
            }
        )
    return total_eur, accounts
```

- [ ] **Step 4: Include bank-account total in `net_worth_eur`**

In `portf_server/routers/networth.py`, find:

```python
def net_worth_eur(db) -> float:
    """Total net worth = brokerage + manual assets − liabilities + active deposits (EUR).

    Shared with Goals projections (see goals.py) so the two never drift apart.
    """
    items = db.get_manual_assets()
    assets_eur = 0.0
    liabilities_eur = 0.0
    for it in items:
        amt_eur = float(it["amount"] or 0) * _fx(it.get("currency", "EUR"))
        if it["is_liability"]:
            liabilities_eur += amt_eur
        else:
            assets_eur += amt_eur
    deposits_eur = sum(
        float(d["principal"]) * _fx(d.get("currency", "EUR"))
        for d in db.get_fixed_deposits(status="active")
    )
    return _brokerage_value_eur(db) + assets_eur - liabilities_eur + deposits_eur
```

Replace with:

```python
def net_worth_eur(db) -> float:
    """Total net worth = brokerage + bank accounts + manual assets
    − liabilities + active deposits (EUR).

    Shared with Goals projections (see goals.py) so the two never drift apart.
    """
    items = db.get_manual_assets()
    assets_eur = 0.0
    liabilities_eur = 0.0
    for it in items:
        amt_eur = float(it["amount"] or 0) * _fx(it.get("currency", "EUR"))
        if it["is_liability"]:
            liabilities_eur += amt_eur
        else:
            assets_eur += amt_eur
    deposits_eur = sum(
        float(d["principal"]) * _fx(d.get("currency", "EUR"))
        for d in db.get_fixed_deposits(status="active")
    )
    bank_eur, _ = _bank_accounts_eur(db)
    return (
        _brokerage_value_eur(db)
        + bank_eur
        + assets_eur
        - liabilities_eur
        + deposits_eur
    )
```

- [ ] **Step 5: Include bank accounts in `GET /api/v1/networth/`**

In `portf_server/routers/networth.py`, find:

```python
    brokerage = round(_brokerage_value_eur(db), 2)
    net_worth = round(brokerage + assets_eur - liabilities_eur + deposits_eur, 2)
    return {
        "brokerage_eur": brokerage,
        "manual_assets_eur": round(assets_eur, 2),
        "manual_liabilities_eur": round(liabilities_eur, 2),
        "deposits_eur": round(deposits_eur, 2),
        "deposits": [_enrich_deposit(dict(d)) for d in raw_deposits],
        "net_worth_eur": net_worth,
        "items": out,
    }
```

Replace with:

```python
    brokerage = round(_brokerage_value_eur(db), 2)
    bank_eur, bank_accounts = _bank_accounts_eur(db)
    net_worth = round(
        brokerage + bank_eur + assets_eur - liabilities_eur + deposits_eur, 2
    )
    return {
        "brokerage_eur": brokerage,
        "bank_accounts_eur": round(bank_eur, 2),
        "bank_accounts": bank_accounts,
        "manual_assets_eur": round(assets_eur, 2),
        "manual_liabilities_eur": round(liabilities_eur, 2),
        "deposits_eur": round(deposits_eur, 2),
        "deposits": [_enrich_deposit(dict(d)) for d in raw_deposits],
        "net_worth_eur": net_worth,
        "items": out,
    }
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_networth.py -v 2>&1 | tail -40
```

Expected: all pass.

- [ ] **Step 7: Check Goals didn't break**

`net_worth_eur(db)` is imported by `portf_server/routers/goals.py` — its signature and return type (a single float) are unchanged, only the computed value now includes bank accounts, so Goals projections should just work with the more complete number. Confirm by running the Goals test file if one exists:

```bash
grep -rl "net_worth_eur\|import goals" tests/unit/ | xargs -I{} echo {}
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/ -k goals -v 2>&1 | tail -20
```

Expected: no failures (Goals tests, if any, don't assert an exact net-worth figure that this change would invalidate — if one does and fails, that test's fixture predates bank accounts and the assertion is still numerically correct, just double-check the failure is a stale-fixture issue and not a real regression before touching it).

- [ ] **Step 8: Format, lint, full suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/networth.py tests/unit/test_networth.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/networth.py --max-line-length=88 --extend-ignore=E203,W503,E501
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: no lint errors; full suite passes.

- [ ] **Step 9: Commit**

```bash
git add portf_server/routers/networth.py tests/unit/test_networth.py
git commit -m "feat: derive bank-account balances into Net Worth from imported statements

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Setup checklist — missing balances + double-counting nudge

**Files:**
- Modify: `web_client/js/pfm_analytics.js`
- Modify: `web_client/js/tests/web_client.test.mjs`

**Depends on Task 3** (the `bank_accounts` shape this consumes). This task touches only the pure `computeNetWorthChecklist` function — DOM rendering changes are Task 5.

- [ ] **Step 1: Write failing tests**

Append to `web_client/js/tests/web_client.test.mjs` (mirror the existing `computeNetWorthChecklist` tests' style — search for `"computeNetWorthChecklist:"` to find them and match the `loadAppIntoContext()` pattern):

```javascript
test("computeNetWorthChecklist: flags a bank account with no balance yet", () => {
    const { computeNetWorthChecklist } = loadAppIntoContext();
    const bankAccounts = [
        { portfolio_id: 1, name: "Example Bank", balance: null, currency: null, balance_eur: null, as_of: null },
    ];
    const { missingBalanceAccounts } = computeNetWorthChecklist([], [], [], bankAccounts);
    assert.equal(missingBalanceAccounts.length, 1);
    assert.equal(missingBalanceAccounts[0].portfolio_id, 1);
    assert.equal(missingBalanceAccounts[0].name, "Example Bank");
});

test("computeNetWorthChecklist: no missing-balance accounts when all have a balance", () => {
    const { computeNetWorthChecklist } = loadAppIntoContext();
    const bankAccounts = [
        { portfolio_id: 1, name: "Example Bank", balance: 500.0, currency: "EUR", balance_eur: 500.0, as_of: "2026-01-10" },
    ];
    const { missingBalanceAccounts } = computeNetWorthChecklist([], [], [], bankAccounts);
    assert.equal(missingBalanceAccounts.length, 0);
});

test("computeNetWorthChecklist: no bank accounts at all → no missing-balance items, no crash", () => {
    const { computeNetWorthChecklist } = loadAppIntoContext();
    const { missingBalanceAccounts } = computeNetWorthChecklist([], [], [], []);
    assert.equal(missingBalanceAccounts.length, 0);
    const { missingBalanceAccounts: viaUndefined } = computeNetWorthChecklist([], [], []);
    assert.equal(viaUndefined.length, 0);
});

test("computeNetWorthChecklist: duplicateWarning null when only one of manual/imported exists", () => {
    const { computeNetWorthChecklist } = loadAppIntoContext();
    const manualCashOnly = [{ is_liability: false, category: "current_account", amount: 100 }];
    const importedOnly = [{ portfolio_id: 1, name: "Bank", balance: 500.0, currency: "EUR", balance_eur: 500.0, as_of: "2026-01-10" }];
    assert.equal(computeNetWorthChecklist(manualCashOnly, [], [], []).duplicateWarning, null);
    assert.equal(computeNetWorthChecklist([], [], [], importedOnly).duplicateWarning, null);
});

test("computeNetWorthChecklist: duplicateWarning set when both manual cash and an imported balance exist", () => {
    const { computeNetWorthChecklist } = loadAppIntoContext();
    const items = [{ is_liability: false, category: "savings_account", amount: 100 }];
    const bankAccounts = [{ portfolio_id: 1, name: "Bank", balance: 500.0, currency: "EUR", balance_eur: 500.0, as_of: "2026-01-10" }];
    const result = computeNetWorthChecklist(items, [], [], bankAccounts);
    assert.ok(result.duplicateWarning);
    assert.equal(typeof result.duplicateWarning, "string");
});

test("computeNetWorthChecklist: existing checklist/attention shape unaffected by the new params", () => {
    const { computeNetWorthChecklist } = loadAppIntoContext();
    const result = computeNetWorthChecklist([], [], []);
    assert.ok(Array.isArray(result.checklist));
    assert.ok(Array.isArray(result.attention));
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test web_client/js/tests/ 2>&1 | tail -40
```

Expected: failures on `missingBalanceAccounts`/`duplicateWarning` being `undefined`.

- [ ] **Step 3: Extend `computeNetWorthChecklist`**

In `web_client/js/pfm_analytics.js`, find:

```javascript
function computeNetWorthChecklist(items, cashflowItems, deposits) {
    items = items || [];
    cashflowItems = cashflowItems || [];
    deposits = deposits || [];
```

Replace with (adds a 4th, optional param):

```javascript
function computeNetWorthChecklist(items, cashflowItems, deposits, bankAccounts) {
    items = items || [];
    cashflowItems = cashflowItems || [];
    deposits = deposits || [];
    bankAccounts = bankAccounts || [];
```

Then find the function's `return` statement:

```javascript
    return { checklist, attention };
}
window.computeNetWorthChecklist = computeNetWorthChecklist;
```

Replace with (adds the two new fields — existing callers reading only `.checklist`/`.attention` are unaffected):

```javascript
    const missingBalanceAccounts = bankAccounts
        .filter(a => a.balance === null || a.balance === undefined)
        .map(a => ({ portfolio_id: a.portfolio_id, name: a.name }));

    const hasBalanceBearingBankAccount = bankAccounts.some(
        a => a.balance !== null && a.balance !== undefined
    );
    const hasManualCashAsset = items.some(
        it => !it.is_liability && NW_BANK_CATS.has(it.category)
    );
    const duplicateWarning = (hasBalanceBearingBankAccount && hasManualCashAsset)
        ? "You have both a manual cash/bank balance entry and at least one bank account with an imported balance — check you're not counting the same money twice, and remove the outdated manual entry if so."
        : null;

    return { checklist, attention, missingBalanceAccounts, duplicateWarning };
}
window.computeNetWorthChecklist = computeNetWorthChecklist;
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
node --test web_client/js/tests/ 2>&1 | tail -40
```

Expected: all pass (6 new tests), no regressions in the existing `computeNetWorthChecklist` tests.

- [ ] **Step 5: Syntax check**

```bash
node --check web_client/js/pfm_analytics.js
```

- [ ] **Step 6: Commit**

```bash
git add web_client/js/pfm_analytics.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: flag bank accounts missing a balance and warn on manual/imported double-counting

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: Net Worth page UI — Bank Accounts card + checklist rendering

**Files:**
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_analytics.js`

**Depends on Task 3** (response shape) **and Task 4** (`missingBalanceAccounts`/`duplicateWarning`). `pfm_core.js`'s `getNetworth()` needs no change — it's a thin `fetch` + `response.json()` passthrough with no typed shape, so the two new response fields (`bank_accounts_eur`, `bank_accounts`) reach the frontend automatically; verify this by reading the current `getNetworth` method before assuming.

- [ ] **Step 1: Add the "Bank Accounts" card to `index.html`**

In `web_client/index.html`, the Net Worth page's items-table row closes and the Fixed Deposits section begins around here:

```html
                        <!-- Items table -->
                        <div class="col-12 col-lg-8">
                            <div class="card">
                                <div class="card-header fw-semibold"><i class="bi bi-list-ul me-2"></i>Assets &amp; liabilities</div>
                                <div class="table-responsive">
                                    <table class="table table-hover mb-0">
                                        <thead><tr><th class="ps-3">Name</th><th>Type</th><th class="text-end">Amount</th><th class="text-end">EUR</th><th class="pe-3"></th></tr></thead>
                                        <tbody id="nwItemsBody"><tr><td colspan="5" class="text-center text-muted py-4">Loading…</td></tr></tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                        <!-- Fixed Deposits section -->
```

Insert a new "Bank Accounts" card immediately after the Items-table `</div>` and before the `<!-- Fixed Deposits section -->` comment:

```html
                        <!-- Items table -->
                        <div class="col-12 col-lg-8">
                            <div class="card">
                                <div class="card-header fw-semibold"><i class="bi bi-list-ul me-2"></i>Assets &amp; liabilities</div>
                                <div class="table-responsive">
                                    <table class="table table-hover mb-0">
                                        <thead><tr><th class="ps-3">Name</th><th>Type</th><th class="text-end">Amount</th><th class="text-end">EUR</th><th class="pe-3"></th></tr></thead>
                                        <tbody id="nwItemsBody"><tr><td colspan="5" class="text-center text-muted py-4">Loading…</td></tr></tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                        <!-- Bank Accounts (derived from imported Spending balances) -->
                        <div class="col-12 mt-2" id="nwBankAccountsWrap" style="display:none;">
                            <div class="card">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span><i class="bi bi-wallet2 me-2"></i>Bank Accounts
                                        <span class="ms-1 text-muted" style="cursor:help" title="Balances derived automatically from the most recent imported bank statement for each account tracked on the Spending page — not manually entered. Import a newer statement to refresh."><i class="bi bi-info-circle"></i></span>
                                    </span>
                                </div>
                                <div class="table-responsive">
                                    <table class="table table-hover mb-0">
                                        <thead><tr><th class="ps-3">Account</th><th class="text-end">Balance</th><th class="text-end">EUR</th><th>As of</th></tr></thead>
                                        <tbody id="nwBankAccountsBody"></tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                        <!-- Fixed Deposits section -->
```

- [ ] **Step 2: Add `_renderBankAccounts` and wire it into `loadNetworthPage`**

In `web_client/js/pfm_analytics.js`, find `loadNetworthPage`'s summary-tile block:

```javascript
        const d = await window.apiClient.getNetworth();
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        $('nwBrokerage').innerHTML = eur(d.brokerage_eur);
        $('nwAssets').innerHTML = eur(d.manual_assets_eur);
        if ($('nwDeposits')) $('nwDeposits').innerHTML = eur(d.deposits_eur || 0);
        $('nwLiabilities').innerHTML = eur(d.manual_liabilities_eur);
        $('nwTotal').innerHTML = eur(d.net_worth_eur);
```

Replace with (adds the Bank Accounts render call, leaves the rest unchanged):

```javascript
        const d = await window.apiClient.getNetworth();
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        $('nwBrokerage').innerHTML = eur(d.brokerage_eur);
        $('nwAssets').innerHTML = eur(d.manual_assets_eur);
        if ($('nwDeposits')) $('nwDeposits').innerHTML = eur(d.deposits_eur || 0);
        $('nwLiabilities').innerHTML = eur(d.manual_liabilities_eur);
        $('nwTotal').innerHTML = eur(d.net_worth_eur);
        _renderBankAccounts(d.bank_accounts || []);
```

Then find the closing `}` of `_renderChecklist`'s call site inside `loadNetworthPage`:

```javascript
        _renderDeposits(d.deposits || []);
        const cf = await _loadCashflow();
        _loadActualSpendingComparison();
        _renderChecklist(d, cf);
```

Replace with (pass `d.bank_accounts` through to the checklist):

```javascript
        _renderDeposits(d.deposits || []);
        const cf = await _loadCashflow();
        _loadActualSpendingComparison();
        _renderChecklist(d, cf, d.bank_accounts || []);
```

Now update `_renderChecklist`'s own signature and its `computeNetWorthChecklist` call. Find:

```javascript
function _renderChecklist(nwData, cfData) {
    const wrap = document.getElementById('nwChecklistWrap');
    const card = document.getElementById('nwChecklistCard');
    if (!wrap || !card) return;
    const { checklist, attention } = computeNetWorthChecklist(
        nwData.items, (cfData && cfData.items) || [], nwData.deposits
    );
    if (!checklist.length && !attention.length) { wrap.style.display = 'none'; return; }
    wrap.style.display = '';

    let html = attention.map(a => `
        <div class="alert alert-warning py-2 small mb-2 d-flex justify-content-between align-items-center">
            <span><i class="bi bi-exclamation-triangle me-1"></i><strong>${esc(a.name)}</strong> matured ${a.days_overdue} day${a.days_overdue === 1 ? '' : 's'} ago — mark it matured to include the payout in your net worth.</span>
            <button class="btn btn-sm btn-outline-success ms-2" onclick="openMatureDepositModal(${a.id}, 0, '${a.maturity_date}')">Mark matured</button>
        </div>`).join('');

    html += '<ul class="list-unstyled mb-0">' + checklist.map(c => `
        <li class="mb-1">
            <i class="bi ${c.done ? 'bi-check-circle-fill text-success' : 'bi-circle text-muted'} me-2"></i>
            ${esc(c.label)}
            ${c.done ? '' : `<button class="btn btn-sm btn-link p-0 ms-2" onclick="window.openNwWizard('${c.key}')">Add</button>`}
        </li>`).join('') + '</ul>';

    card.innerHTML = html;
}
```

Replace with (adds `bankAccounts` param, renders `missingBalanceAccounts` as attention-style alerts and `duplicateWarning` as its own banner — both use `esc()`, never a wizard-step button, since neither maps to an existing `NW_WIZARD_STEP_DEFS` entry):

```javascript
function _renderChecklist(nwData, cfData, bankAccounts) {
    const wrap = document.getElementById('nwChecklistWrap');
    const card = document.getElementById('nwChecklistCard');
    if (!wrap || !card) return;
    const { checklist, attention, missingBalanceAccounts, duplicateWarning } = computeNetWorthChecklist(
        nwData.items, (cfData && cfData.items) || [], nwData.deposits, bankAccounts || []
    );
    if (!checklist.length && !attention.length && !missingBalanceAccounts.length && !duplicateWarning) {
        wrap.style.display = 'none';
        return;
    }
    wrap.style.display = '';

    let html = attention.map(a => `
        <div class="alert alert-warning py-2 small mb-2 d-flex justify-content-between align-items-center">
            <span><i class="bi bi-exclamation-triangle me-1"></i><strong>${esc(a.name)}</strong> matured ${a.days_overdue} day${a.days_overdue === 1 ? '' : 's'} ago — mark it matured to include the payout in your net worth.</span>
            <button class="btn btn-sm btn-outline-success ms-2" onclick="openMatureDepositModal(${a.id}, 0, '${a.maturity_date}')">Mark matured</button>
        </div>`).join('');

    html += missingBalanceAccounts.map(a => `
        <div class="alert alert-warning py-2 small mb-2">
            <i class="bi bi-exclamation-triangle me-1"></i><strong>${esc(a.name)}</strong> has no imported balance yet — it isn't counted in your Net Worth total. Import a bank statement with a balance column on the Spending page, or add a manual cash/current-account entry as a stopgap.
        </div>`).join('');

    if (duplicateWarning) {
        html += `<div class="alert alert-warning py-2 small mb-2"><i class="bi bi-exclamation-triangle me-1"></i>${esc(duplicateWarning)}</div>`;
    }

    html += '<ul class="list-unstyled mb-0">' + checklist.map(c => `
        <li class="mb-1">
            <i class="bi ${c.done ? 'bi-check-circle-fill text-success' : 'bi-circle text-muted'} me-2"></i>
            ${esc(c.label)}
            ${c.done ? '' : `<button class="btn btn-sm btn-link p-0 ms-2" onclick="window.openNwWizard('${c.key}')">Add</button>`}
        </li>`).join('') + '</ul>';

    card.innerHTML = html;
}
```

- [ ] **Step 3: Add `_renderBankAccounts`**

In `web_client/js/pfm_analytics.js`, immediately after `_renderChecklist` (before `escapeForAttr`), add:

```javascript
function _renderBankAccounts(accounts) {
    const wrap = document.getElementById('nwBankAccountsWrap');
    const body = document.getElementById('nwBankAccountsBody');
    if (!wrap || !body) return;
    if (!accounts.length) { wrap.style.display = 'none'; return; }
    wrap.style.display = '';
    body.innerHTML = accounts.map(a => {
        if (a.balance === null || a.balance === undefined) {
            return `
                <tr>
                    <td class="ps-3">${esc(a.name)}</td>
                    <td class="text-end text-muted" colspan="3">No balance imported yet</td>
                </tr>`;
        }
        return `
            <tr>
                <td class="ps-3">${esc(a.name)}</td>
                <td class="text-end">${Fmt.num(a.balance, 2, 2)} ${esc(a.currency || '')}</td>
                <td class="text-end">${Fmt.amt('€' + Fmt.num(a.balance_eur, 0, 0))}</td>
                <td class="text-muted small">${Fmt.date(a.as_of)}</td>
            </tr>`;
    }).join('');
}
```

- [ ] **Step 4: Syntax check + JS tests**

```bash
node --check web_client/js/pfm_analytics.js
node --test web_client/js/tests/ 2>&1 | tail -20
```

Expected: valid syntax; existing tests still pass (this task adds no new pure-function tests, since `_renderBankAccounts`/`_renderChecklist` are DOM-coupled, same convention as the rest of this file's render functions).

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_analytics.js
git commit -m "feat: add Bank Accounts card to Net Worth page, wire checklist warnings

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 6: Rebuild, smoke test, docs

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rebuild and restart both services**

```bash
docker exec portf_backend_dev kill -HUP 1
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

- [ ] **Step 2: Run the full test suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -15
node --test web_client/js/tests/ 2>&1 | tail -15
```

Expected: all pass, 0 failures.

- [ ] **Step 3: Smoke-test via curl against the real running API**

- Create a fictional bank-type portfolio via `POST /api/v1/portfolios/` with `account_type: "bank"`.
- Save a spending transaction with a `balance` field via `POST /api/v1/spending/save` for that portfolio.
- `GET /api/v1/networth/` — confirm `bank_accounts_eur` matches the balance, `bank_accounts` lists the account with the right `balance`/`as_of`, and `net_worth_eur` includes it.
- Create a second bank-type portfolio with NO spending transactions — confirm it appears in `bank_accounts` with `balance: null` and contributes nothing to `bank_accounts_eur`.
- Hard-delete all fictional data created here directly against the container's SQLite (mirror how the controller cleaned up after the original feature's Task 13 and round 2's Task 6 — `docker exec portf_backend_dev python3 -c "..."` deleting the test portfolios and their spending_transactions rows) — do not leave test artifacts in the live database. Verify zero rows remain afterward.

- [ ] **Step 4: Update `PROJECT_STATUS.md`**

Bump `Last updated` to today's date, prepend a new `**Recent (v2.5.21):**` line before the existing v2.5.20 line summarizing: bank-account balances now derived automatically into Net Worth from the latest imported statement with a balance column (db v26); setup checklist flags accounts with no balance yet and warns about manual/imported double-counting; new Bank Accounts card on the Net Worth page.

- [ ] **Step 5: Extend `CLAUDE.md`**

Bump `**Current schema version: 25.**` to `**Current schema version: 26.**`. Add a v26 migration-history line after the v25 line: `- v26: `spending_transactions.balance` (nullable REAL) — populated from the optional \`balance\` column in imported bank statements. See "Net Worth API" section below.`

In the `### Net Worth API` section, add a paragraph documenting: `net_worth_eur(db)`/`GET /api/v1/networth/` now also sum bank-type portfolios' balances, derived via `Database.get_latest_bank_balance(portfolio_id)` (most recent `spending_transactions` row with a non-null `balance`, ties broken by highest id); an account with no balance-bearing import is excluded from the total (not zero) and reported as `bank_accounts: [{..., balance: null}]`; the Net Worth page's setup checklist (`computeNetWorthChecklist`) flags such accounts and separately warns if both a manual cash-category asset and a balance-bearing bank account exist (possible double-counting) — no automatic matching or deletion, prompt only.

- [ ] **Step 6: Format/lint everything one final time**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/ portf_server/ tests/
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
```

- [ ] **Step 7: Run the full test suite one final time**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
node --test web_client/js/tests/ 2>&1 | tail -10
```

- [ ] **Step 8: Final commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: document bank-account balance in Net Worth (db v26)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Self-Review

**Spec coverage:** Design point 1 (bank balances automatic in Net Worth, derived from latest imported balance) — Tasks 1-3, 5. Design point 2 (missing-balance accounts excluded from the total, flagged not zeroed) — Task 3 (`balance: None` excluded from `bank_accounts_eur`), Task 4 (`missingBalanceAccounts`), Task 5 (checklist alert). Design point 3 (generic double-counting nudge, no auto-matching/deletion) — Task 4 (`duplicateWarning`, a plain string with no per-account matching logic), Task 5 (rendered as a banner, no delete action wired to it). All three agreed points are covered by a task with a corresponding test.

**Placeholder scan:** No TBD/TODO markers. Task 3 Step 1 and Task 1's Step 4 both include an explicit "verify against the real current file before assuming" instruction where the plan's research couldn't fully pin down an exact pre-existing helper's shape (`_make_client`'s return signature in `test_networth.py`, and the duplicated table-definition text inside `_migrate_to_v25`) — these are deliberate "confirm against real repo state" instructions, not gaps in the design itself; the actual code given is complete and correct against everything else verified during planning.

**Type/interface consistency:** `get_latest_bank_balance` returns `{date, balance, currency}` (Task 1) — `_bank_accounts_eur` (Task 3) reads exactly those three keys. `computeNetWorthChecklist`'s new `bankAccounts` param (Task 4) expects the exact shape `_bank_accounts_eur`/`GET /api/v1/networth/` (Task 3) produces (`portfolio_id`, `name`, `balance`, `currency`, `balance_eur`, `as_of`) — verified field names match across both tasks. `_renderBankAccounts`/`_renderChecklist` (Task 5) consume the same shape Task 4's tests exercise.
