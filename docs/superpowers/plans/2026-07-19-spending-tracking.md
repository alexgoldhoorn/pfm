# Bank Spending Tracking + Inter-Account Transfer Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import bank-account CSV statements, auto-categorize each row (rule-based, LLM-assisted for the rest), and automatically link transfers between the user's own accounts (bank↔bank and bank↔brokerage) so they're excluded from spending totals.

**Architecture:** Two new tables (`spending_transactions`, `spending_rules`) plus an `account_type` column on the existing `portfolios` table (bank accounts reuse the broker/account concept). A new pure parser (`generic_bank_csv_parser.py`) and a new pure transfer-matcher (`transfer_matcher.py`) back a new `spending.py` router, mirroring the existing `imports.py`/`bookings.py` router conventions. A new "Spending" nav page plus a small read-only comparison widget on the existing Net Worth page.

**Tech Stack:** Python 3.13 / SQLite / FastAPI / Pydantic / Vanilla JS / Bootstrap 5 / pytest / `uv run` / Node built-in test runner

## Global Constraints

- Black formatting (line length 88): `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black <file>`
- Type hints on all function signatures; Google-style docstrings
- Comments go on the line before the code they describe, not inline
- All Python commands: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run ...` (the `.venv` is root-owned)
- flake8 clean: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 <file> --max-line-length=88 --extend-ignore=E203,W503,E501`
- Any new DB table/column must land in **both** `_create_all_tables` (fresh DBs) and the versioned `_migrate_to_vN` (existing DBs) — migration-only adds break fresh installs/tests with "no such table"
- Blocking-IO endpoints (yfinance, FX lookups) are plain `def`, not `async def`, so FastAPI runs them in a threadpool
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`), each ending with `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Web changes only go live after: `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`
- Python changes only go live after: `docker exec portf_backend_dev kill -HUP 1`
- No real personal/financial data anywhere (tests, fixtures, sample files) — use fictional data (e.g. "MERCADONA COMPRA", fictional amounts); no real IBANs/account numbers
- Public repo: no home-directory paths (`/home/agoldhoorn/` → `~/`) in anything committed

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `portf_manager/database.py` | Modify | `account_type` column, `spending_transactions` + `spending_rules` tables, migration v25, CRUD helpers |
| `tests/unit/test_spending_db.py` | Create | DB-layer tests for the above |
| `tests/test_database.py` | Modify | Bump version assertions to 25, add new-table checks |
| `portf_manager/parsers/generic_bank_csv_parser.py` | Create | Pure bank-statement CSV parser |
| `tests/unit/test_generic_bank_csv_parser.py` | Create | Parser unit tests |
| `portf_manager/services/transfer_matcher.py` | Create | Pure transfer-matching function |
| `tests/unit/test_transfer_matcher.py` | Create | Matcher unit tests |
| `portf_server/routers/spending.py` | Create | New router: upload/save/list/update/rescan/rules/summary/suggest-categories |
| `portf_server/app.py` | Modify | Register `spending` router |
| `portf_server/routers/portfolios.py` | Modify | Expose `account_type` on `GET /api/v1/portfolios/` |
| `tests/unit/test_spending_api.py` | Create | API tests for the new router |
| `web_client/js/pfm_core.js` | Modify | API client methods for all new endpoints |
| `web_client/index.html` | Modify | Spending nav entries + page skeleton + import modal; Net Worth "Actual" widget |
| `web_client/js/pfm_features.js` | Modify | Spending page logic, nav wiring, pure `filterSpendingRows` helper |
| `web_client/js/pfm_analytics.js` | Modify | Net Worth "Actual" comparison widget loader |
| `web_client/js/tests/web_client.test.mjs` | Modify | Test for `filterSpendingRows` |
| `web_client/js/help_text.js` | Modify | New `spending` PAGE_HELP entry, extend `networth` entry |
| `PROJECT_STATUS.md` | Modify | Bump summary line |
| `CLAUDE.md` | Modify | New `## Spending Tracking` section, bump schema version to 25 |

---

## Task 1: DB layer — `account_type`, `spending_transactions`, `spending_rules`

**Files:**
- Modify: `portf_manager/database.py`
- Modify: `portf_server/routers/portfolios.py`
- Create: `tests/unit/test_spending_db.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_spending_db.py`:

```python
"""Tests for the spending-tracking DB layer (spending_transactions, spending_rules,
portfolios.account_type)."""

import pytest
from portf_manager.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_portfolio_defaults_to_brokerage(db):
    pid = db.create_portfolio("Example Broker")
    p = db.get_portfolio(pid)
    assert p["account_type"] == "brokerage"


def test_create_bank_portfolio(db):
    pid = db.create_portfolio("Example Bank Checking", account_type="bank")
    p = db.get_portfolio(pid)
    assert p["account_type"] == "bank"


def test_get_or_create_portfolio_bank_type(db):
    pid1 = db.get_or_create_portfolio("Example Bank", account_type="bank")
    pid2 = db.get_or_create_portfolio("Example Bank", account_type="bank")
    assert pid1 == pid2
    assert db.get_portfolio(pid1)["account_type"] == "bank"


def test_create_and_list_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        portfolio_id=pid, date="2026-01-05", description="MERCADONA COMPRA",
        amount=-24.50, currency="EUR", category="Groceries", source="generic",
    )
    assert tx_id > 0
    rows = db.list_spending_transactions()
    assert len(rows) == 1
    assert rows[0]["description"] == "MERCADONA COMPRA"
    assert rows[0]["amount"] == -24.50
    assert rows[0]["category"] == "Groceries"
    assert rows[0]["is_transfer"] == 0
    assert rows[0]["portfolio_name"] == "Example Bank"


def test_spending_transaction_defaults(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(
        portfolio_id=pid, date="2026-01-05", description="NOMINA", amount=2100.0,
    )
    row = db.get_spending_transaction(tx_id)
    assert row["currency"] == "EUR"
    assert row["category"] == "uncategorized"


def test_list_spending_transactions_filters(db):
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    db.create_spending_transaction(pid_a, "2026-01-01", "Groceries A", -10.0, category="Groceries")
    db.create_spending_transaction(pid_b, "2026-02-01", "Dining B", -20.0, category="Dining")

    assert len(db.list_spending_transactions(portfolio_id=pid_a)) == 1
    assert len(db.list_spending_transactions(category="Dining")) == 1
    assert len(db.list_spending_transactions(start_date="2026-02-01")) == 1
    assert len(db.list_spending_transactions(end_date="2026-01-01")) == 1
    assert len(db.list_spending_transactions()) == 2


def test_find_duplicate_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "MERCADONA COMPRA", -24.50)
    dup = db.find_duplicate_spending_transaction(
        portfolio_id=pid, date="2026-01-05", amount=-24.50, description="MERCADONA COMPRA"
    )
    assert dup is not None
    no_dup = db.find_duplicate_spending_transaction(
        portfolio_id=pid, date="2026-01-06", amount=-24.50, description="MERCADONA COMPRA"
    )
    assert no_dup is None


def test_update_spending_transaction(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    assert db.update_spending_transaction(tx_id, category="Transport") is True
    assert db.get_spending_transaction(tx_id)["category"] == "Transport"
    assert db.update_spending_transaction(999999, category="X") is False


def test_update_spending_transaction_transfer_link(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    db.update_spending_transaction(
        tx_id, category="Transfer", is_transfer=True,
        transfer_link_type="booking", transfer_link_id=42,
    )
    row = db.get_spending_transaction(tx_id)
    assert row["is_transfer"] == 1
    assert row["transfer_link_type"] == "booking"
    assert row["transfer_link_id"] == 42


def test_list_unlinked_spending_transactions(db):
    pid = db.create_portfolio("Example Bank", account_type="bank")
    id1 = db.create_spending_transaction(pid, "2026-01-05", "A", -10.0)
    id2 = db.create_spending_transaction(pid, "2026-01-06", "B", -20.0)
    db.update_spending_transaction(id1, is_transfer=True)
    unlinked = db.list_unlinked_spending_transactions()
    ids = [r["id"] for r in unlinked]
    assert id1 not in ids
    assert id2 in ids


def test_spending_rules_crud(db):
    rule_id = db.create_spending_rule(pattern="MERCADONA", category="Groceries")
    assert rule_id > 0
    rules = db.list_spending_rules()
    assert len(rules) == 1
    assert rules[0]["pattern"] == "MERCADONA"
    assert rules[0]["category"] == "Groceries"
    assert db.delete_spending_rule(rule_id) is True
    assert db.list_spending_rules() == []


def test_delete_spending_rule_missing_returns_false(db):
    assert db.delete_spending_rule(999999) is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -v 2>&1 | tail -30
```

Expected: failures — `account_type` not a recognized `create_portfolio` kwarg, `create_spending_transaction` not defined, etc.

- [ ] **Step 3: Bump `DATABASE_VERSION` to 25**

In `portf_manager/database.py:17`:

```python
DATABASE_VERSION = 25
```

- [ ] **Step 4: Add `account_type` to the `portfolios` table in `_create_all_tables`**

In `portf_manager/database.py`, the `portfolios` table definition is at lines 192-206:

```python
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                entity_id INTEGER,
                user_id INTEGER,
                description TEXT,
                website TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities (id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
```

Replace with (adds `account_type` after `is_active`):

```python
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                entity_id INTEGER,
                user_id INTEGER,
                description TEXT,
                website TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                account_type TEXT NOT NULL DEFAULT 'brokerage'
                    CHECK (account_type IN ('brokerage', 'bank')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entity_id) REFERENCES entities (id) ON DELETE SET NULL,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
```

- [ ] **Step 5: Add `spending_transactions` + `spending_rules` tables to `_create_all_tables`**

In `portf_manager/database.py`, find the `chat_sessions` table block (lines 584-596):

```python
        # Named chat threads with persistent message history. See _migrate_to_v24.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id               TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                last_message_at  TEXT NOT NULL DEFAULT (datetime('now')),
                message_count    INTEGER DEFAULT 0,
                messages         TEXT DEFAULT '[]'
            )
            """
        )
```

Insert immediately after it (before the `# Create triggers for updated_at timestamps` comment):

```python

        # Categorized bank-account transactions (spending/income), separate from
        # the asset-shaped `transactions` table. See _migrate_to_v25.
        conn.execute(
            """
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
            """
        )

        # Description → category rules for spending_transactions. Global (not
        # per-account); case-insensitive substring match, first match (by id,
        # i.e. oldest = highest priority) wins. See _migrate_to_v25.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spending_rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern     TEXT NOT NULL,
                category    TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
```

- [ ] **Step 6: Add `_migrate_to_v25()` method**

In `portf_manager/database.py`, directly after `_migrate_to_v24` (ends at line 1378), add:

```python

    def _migrate_to_v25(self, conn: sqlite3.Connection) -> None:
        """Migrate from v24 to v25 — bank spending tracking.

        Adds portfolios.account_type (brokerage vs bank) plus the
        spending_transactions and spending_rules tables.
        """
        _add_column_if_missing(
            conn,
            "portfolios",
            "account_type",
            "TEXT NOT NULL DEFAULT 'brokerage' CHECK (account_type IN ('brokerage', 'bank'))",
        )
        conn.execute(
            """
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
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spending_rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern     TEXT NOT NULL,
                category    TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
```

- [ ] **Step 7: Wire migration into `_run_migrations()`**

In `portf_manager/database.py`, find (lines 664-666):

```python
        if current_version < 24:
            self._migrate_to_v24(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

Replace with:

```python
        if current_version < 24:
            self._migrate_to_v24(conn)
        if current_version < 25:
            self._migrate_to_v25(conn)

        self._set_database_version(conn, DATABASE_VERSION)
```

- [ ] **Step 8: Add `account_type` param to `create_portfolio` and `get_or_create_portfolio`**

In `portf_manager/database.py`, `create_portfolio` (lines 1936-1954):

```python
    def create_portfolio(
        self,
        name: str,
        base_currency: str = "USD",
        entity_id: int = None,
        description: str = None,
        user_id: int = None,
    ) -> int:
        """Create a new portfolio."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO portfolios (name, base_currency, entity_id, description, user_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (name, base_currency, entity_id, description, user_id),
            )
            conn.commit()
            return cursor.lastrowid
```

Replace with:

```python
    def create_portfolio(
        self,
        name: str,
        base_currency: str = "USD",
        entity_id: int = None,
        description: str = None,
        user_id: int = None,
        account_type: str = "brokerage",
    ) -> int:
        """Create a new portfolio (a portfolio doubles as a broker/bank account)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO portfolios
                    (name, base_currency, entity_id, description, user_id, account_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (name, base_currency, entity_id, description, user_id, account_type),
            )
            conn.commit()
            return cursor.lastrowid
```

Then `get_or_create_portfolio` (lines 1988-2008):

```python
    def get_or_create_portfolio(
        self, name: str, base_currency: str = "EUR", description: str = None
    ) -> int:
        """Return the portfolio ID for *name*, creating it if it does not exist.

        Args:
            name: Portfolio / broker name.
            base_currency: Currency used when creating a new portfolio.
            description: Description used only when creating a new portfolio.

        Returns:
            int: Portfolio ID (existing or newly created).
        """
        existing = self.get_portfolio_by_name(name)
        if existing:
            return existing["id"]
        return self.create_portfolio(
            name=name,
            base_currency=base_currency,
            description=description or "Auto-created from import",
        )
```

Replace with:

```python
    def get_or_create_portfolio(
        self,
        name: str,
        base_currency: str = "EUR",
        description: str = None,
        account_type: str = "brokerage",
    ) -> int:
        """Return the portfolio ID for *name*, creating it if it does not exist.

        Args:
            name: Portfolio / broker / bank-account name.
            base_currency: Currency used when creating a new portfolio.
            description: Description used only when creating a new portfolio.
            account_type: 'brokerage' or 'bank', used only when creating.

        Returns:
            int: Portfolio ID (existing or newly created).
        """
        existing = self.get_portfolio_by_name(name)
        if existing:
            return existing["id"]
        return self.create_portfolio(
            name=name,
            base_currency=base_currency,
            description=description or "Auto-created from import",
            account_type=account_type,
        )
```

- [ ] **Step 9: Add spending CRUD helpers**

In `portf_manager/database.py`, find `delete_booking` (lines 2550-2555):

```python
    def delete_booking(self, booking_id: int) -> bool:
        """Delete a booking by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            conn.commit()
            return cursor.rowcount > 0

    # CRUD Operations for Prices
```

Insert a new section between `delete_booking` and the `# CRUD Operations for Prices` comment:

```python
    def delete_booking(self, booking_id: int) -> bool:
        """Delete a booking by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
            conn.commit()
            return cursor.rowcount > 0

    # CRUD Operations for Spending Transactions (categorized bank-account rows)

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
                (portfolio_id, date, description, amount, currency.upper(), category, source),
            )
            conn.commit()
            return cursor.lastrowid

    def find_duplicate_spending_transaction(
        self, portfolio_id: int, date: str, amount: float, description: str
    ) -> Optional[Dict]:
        """Return an existing spending row matching portfolio+date+amount+description, or None."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id FROM spending_transactions
                WHERE portfolio_id = ? AND date = ?
                  AND ABS(amount - ?) < 0.001 AND description = ?
                LIMIT 1
                """,
                (portfolio_id, date, amount, description),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

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

    def get_spending_transaction(self, spending_id: int) -> Optional[Dict]:
        """Get a spending transaction by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_transactions WHERE id = ?", (spending_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_spending_transaction(self, spending_id: int, **kwargs) -> bool:
        """Update spending transaction fields (category, is_transfer, transfer link)."""
        valid_fields = {"category", "is_transfer", "transfer_link_type", "transfer_link_id"}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not update_fields:
            return False
        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields)
            values = list(update_fields.values()) + [spending_id]
            cursor = conn.execute(
                f"UPDATE spending_transactions SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_unlinked_spending_transactions(self) -> List[Dict]:
        """List spending rows not yet linked as a transfer (is_transfer = 0)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM spending_transactions WHERE is_transfer = 0"
            )
            return [dict(row) for row in cursor.fetchall()]

    # CRUD Operations for Spending Rules (description → category matching)

    def create_spending_rule(self, pattern: str, category: str) -> int:
        """Create a spending category rule (case-insensitive substring match on description)."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO spending_rules (pattern, category) VALUES (?, ?)",
                (pattern, category),
            )
            conn.commit()
            return cursor.lastrowid

    def list_spending_rules(self) -> List[Dict]:
        """List all spending rules, oldest (highest priority) first."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM spending_rules ORDER BY id")
            return [dict(row) for row in cursor.fetchall()]

    def delete_spending_rule(self, rule_id: int) -> bool:
        """Delete a spending rule by ID."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM spending_rules WHERE id = ?", (rule_id,))
            conn.commit()
            return cursor.rowcount > 0

    # CRUD Operations for Prices
```

- [ ] **Step 10: Expose `account_type` on `GET /api/v1/portfolios/`**

In `portf_server/routers/portfolios.py`, `list_portfolios` (lines 170-204) builds an explicit dict per portfolio rather than spreading `p.*`. Find:

```python
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "base_currency": p.get("base_currency", "EUR"),
                "entity_id": p.get("entity_id"),
                "entity_name": p.get("entity_name"),
                "description": p.get("description") or defaults.get("description"),
                "website": p.get("website") or defaults.get("website"),
                "website_is_default": not p.get("website")
                and bool(defaults.get("website")),
                "is_active": p.get("is_active", True),
                "first_transaction_date": r.get("first_transaction_date"),
                "last_transaction_date": r.get("last_transaction_date"),
                "first_booking_date": r.get("first_booking_date"),
                "last_booking_date": r.get("last_booking_date"),
            }
        )
```

Replace with (adds `account_type` after `is_active`):

```python
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "base_currency": p.get("base_currency", "EUR"),
                "entity_id": p.get("entity_id"),
                "entity_name": p.get("entity_name"),
                "description": p.get("description") or defaults.get("description"),
                "website": p.get("website") or defaults.get("website"),
                "website_is_default": not p.get("website")
                and bool(defaults.get("website")),
                "is_active": p.get("is_active", True),
                "account_type": p.get("account_type", "brokerage"),
                "first_transaction_date": r.get("first_transaction_date"),
                "last_transaction_date": r.get("last_transaction_date"),
                "first_booking_date": r.get("first_booking_date"),
                "last_booking_date": r.get("last_booking_date"),
            }
        )
```

No filtering change is needed on Holdings/Rebalance/position endpoints: they source positions exclusively from the `transactions` table (`database.get_all_transactions`), which bank accounts never populate (they only ever get `spending_transactions` rows) — a bank portfolio simply yields zero positions there by construction, verified in `get_holdings` (`portf_server/routers/portfolios.py:335`).

- [ ] **Step 11: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -v 2>&1 | tail -25
```

Expected: `14 passed`.

- [ ] **Step 12: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/database.py portf_server/routers/portfolios.py tests/unit/test_spending_db.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/database.py portf_server/routers/portfolios.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 13: Commit**

```bash
git add portf_manager/database.py portf_server/routers/portfolios.py tests/unit/test_spending_db.py
git commit -m "feat: add spending_transactions/spending_rules tables + portfolios.account_type (db v25)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Bump version assertions in `test_database.py`

**Files:**
- Modify: `tests/test_database.py`

- [ ] **Step 1: Replace all four `== 24` version assertions with `== 25`**

In `tests/test_database.py`:

```python
# Line 53
assert result[0] == 24  # Current schema version
```
→
```python
assert result[0] == 25  # Current schema version
```

Lines 1001, 1031, 1100 all read `assert version == 24` — use `replace_all=True` on the Edit tool for that exact pattern (3 occurrences), and handle line 53 separately since it has a trailing comment.

- [ ] **Step 2: Add table-existence checks to `test_fresh_database_creation`**

In `tests/test_database.py`, `test_fresh_database_creation` (around line 1021) already checks `"users"`, `"entities"`, `"portfolios"`. Add after those assertions:

```python
            assert "spending_transactions" in tables
            assert "spending_rules" in tables
```

- [ ] **Step 3: Run the full DB test suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/test_database.py -v 2>&1 | tail -20
```

Expected: all tests pass (no `== 24` failures).

- [ ] **Step 4: Commit**

```bash
git add tests/test_database.py
git commit -m "test: bump DB version assertions to 25, add spending table checks

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: Generic bank-statement CSV parser

**Files:**
- Create: `portf_manager/parsers/generic_bank_csv_parser.py`
- Create: `tests/unit/test_generic_bank_csv_parser.py`

**Interfaces:**
- Produces: `SpendingRow(date, description, amount, currency='EUR', balance=None)`, `BankParseResult(rows: list[SpendingRow], skipped: list[tuple[str, str]])`, `parse_generic_bank_csv(content: str) -> BankParseResult` — consumed by Task 5's router.
- Consumes: reuses `_detect_delimiter`, `_detect_slash_date_style`, `_detect_decimal_style`, `_parse_number`, `_parse_date`, `_DATE_FORMATS_EU`, `_DATE_FORMATS_US` from `portf_manager/parsers/generic_csv_parser.py` (already free functions there — imported directly, not duplicated).

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_generic_bank_csv_parser.py`:

```python
"""Tests for the generic bank-statement CSV parser."""

from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv


def test_basic_parse():
    csv_text = (
        "date,description,amount,currency\n"
        "2026-01-05,MERCADONA COMPRA,-24.50,EUR\n"
        "2026-01-06,NOMINA EMPRESA SL,2100.00,EUR\n"
    )
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 2
    assert result.rows[0].date == "2026-01-05"
    assert result.rows[0].description == "MERCADONA COMPRA"
    assert result.rows[0].amount == -24.50
    assert result.rows[0].currency == "EUR"
    assert result.rows[1].amount == 2100.00


def test_header_synonyms_spanish():
    csv_text = "fecha;concepto;importe\n05/01/2026;MERCADONA COMPRA;-24,50\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 1
    assert result.rows[0].date == "2026-01-05"
    assert result.rows[0].amount == -24.50


def test_header_synonyms_dutch():
    csv_text = "datum,omschrijving,bedrag\n2026-01-05,BOODSCHAPPEN,-24.50\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 1
    assert result.rows[0].description == "BOODSCHAPPEN"


def test_missing_required_columns():
    csv_text = "date,amount\n2026-01-05,-10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows == []
    assert any("Missing required columns" in reason for _, reason in result.skipped)


def test_optional_balance_and_currency_default():
    csv_text = "date,description,amount,balance\n2026-01-05,Desc,-10.00,500.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows[0].currency == "EUR"
    assert result.rows[0].balance == 500.00


def test_us_date_style_detected():
    csv_text = "date,description,amount\n01/20/2026,Desc,-10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows[0].date == "2026-01-20"


def test_eu_date_style_detected():
    csv_text = "date,description,amount\n20/01/2026,Desc,-10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows[0].date == "2026-01-20"


def test_semicolon_delimiter_detected():
    csv_text = "date;description;amount\n2026-01-05;Desc;-10,00\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 1
    assert result.rows[0].amount == -10.00


def test_zero_amount_skipped():
    csv_text = "date,description,amount\n2026-01-05,Desc,0\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows == []
    assert any("zero" in reason.lower() for _, reason in result.skipped)


def test_empty_description_skipped():
    csv_text = "date,description,amount\n2026-01-05,,10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows == []
    assert any("description" in reason.lower() for _, reason in result.skipped)


def test_blank_lines_skipped_silently():
    csv_text = "date,description,amount\n2026-01-05,Desc,-10.00\n\n2026-01-06,Desc2,-5.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 2


def test_empty_file():
    result = parse_generic_bank_csv("")
    assert result.rows == []
    assert result.skipped[0][0] == "file"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_generic_bank_csv_parser.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError: No module named 'portf_manager.parsers.generic_bank_csv_parser'`.

- [ ] **Step 3: Write the parser**

Create `portf_manager/parsers/generic_bank_csv_parser.py`:

```python
"""
Generic bank-statement CSV parser for spending tracking.

Canonical column layout (order doesn't matter; headers are case-insensitive):
  date, description, amount, balance, currency

Only date/description/amount are required. balance/currency are optional
(currency defaults to EUR).

Template::

    date,description,amount,currency
    2026-01-05,MERCADONA COMPRA,-24.50,EUR
    2026-01-06,NOMINA EMPRESA SL,2100.00,EUR
    2026-01-10,TRASPASO A AHORRO,-500.00,EUR

amount is signed: negative = money out, positive = money in (bank-statement
convention — NOT the bookings table's Deposit/Withdrawal-as-text convention).

Delimiter and EU/US date/decimal style are auto-detected by reusing the
detection helpers already in generic_csv_parser.py rather than duplicating
them (they are plain module-level functions there, safely importable).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .generic_csv_parser import (
    _detect_delimiter,
    _detect_slash_date_style,
    _detect_decimal_style,
    _parse_number,
    _parse_date,
    _DATE_FORMATS_EU,
    _DATE_FORMATS_US,
)

_HEADER_SYNONYMS: dict[str, set[str]] = {
    "date": {
        "date",
        "fecha",
        "datum",
        "value_date",
        "valuedate",
        "transaction_date",
        "transactiondate",
        "booking_date",
        "bookingdate",
    },
    "description": {
        "description",
        "descripcion",
        "descripción",
        "concepto",
        "concept",
        "omschrijving",
        "detail",
        "details",
        "memo",
        "movimiento",
    },
    "amount": {
        "amount",
        "importe",
        "bedrag",
        "value",
        "monto",
        "cantidad",
    },
    "balance": {
        "balance",
        "saldo",
        "running_balance",
        "runningbalance",
    },
    "currency": {
        "currency",
        "divisa",
        "moneda",
        "ccy",
        "valuta",
    },
}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _resolve_header(raw: str) -> Optional[str]:
    n = _norm_header(raw)
    for canonical, synonyms in _HEADER_SYNONYMS.items():
        if n in {_norm_header(s) for s in synonyms}:
            return canonical
    return None


@dataclass
class SpendingRow:
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    balance: Optional[float] = None


@dataclass
class BankParseResult:
    rows: List[SpendingRow] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def parse_generic_bank_csv(content: str) -> BankParseResult:
    """Parse a generic bank-statement CSV into signed SpendingRow objects.

    Args:
        content: Raw CSV text.

    Returns:
        BankParseResult with parsed rows and skipped rows with reasons.
    """
    result = BankParseResult()
    delimiter = _detect_delimiter(content)

    reader = csv.reader(io.StringIO(content.strip()), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        result.skipped.append(("file", "Empty file"))
        return result

    raw_headers = rows[0]
    col_map: dict[str, int] = {}
    for i, h in enumerate(raw_headers):
        canonical = _resolve_header(h)
        if canonical and canonical not in col_map:
            col_map[canonical] = i

    required = {"date", "description", "amount"}
    missing = required - col_map.keys()
    if missing:
        result.skipped.append(
            ("header", f"Missing required columns: {', '.join(sorted(missing))}")
        )
        return result

    date_formats = (
        _DATE_FORMATS_US
        if _detect_slash_date_style(rows, col_map["date"]) == "us"
        else _DATE_FORMATS_EU
    )
    num_col_indices = [col_map[c] for c in ("amount", "balance") if c in col_map]
    decimal_style = _detect_decimal_style(rows, num_col_indices)

    def _get(row: list[str], col: str, default: str = "") -> str:
        idx = col_map.get(col)
        if idx is None or idx >= len(row):
            return default
        return row[idx].strip()

    for row_num, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # skip blank lines
        try:
            date_str = _parse_date(_get(row, "date"), date_formats)
        except ValueError as e:
            result.skipped.append((f"row {row_num}", f"Date error: {e}"))
            continue

        description = _get(row, "description")
        if not description:
            result.skipped.append((f"row {row_num}", "Empty description"))
            continue

        try:
            amount = _parse_number(_get(row, "amount"), decimal_style)
        except ValueError:
            result.skipped.append(
                (f"row {row_num}", f"Invalid amount: {_get(row, 'amount')!r}")
            )
            continue
        if amount == 0:
            result.skipped.append((f"row {row_num}", "Amount is zero"))
            continue

        balance_raw = _get(row, "balance")
        balance = None
        if balance_raw:
            try:
                balance = _parse_number(balance_raw, decimal_style)
            except ValueError:
                balance = None

        currency = _get(row, "currency", "EUR").upper()[:3] or "EUR"

        result.rows.append(
            SpendingRow(
                date=date_str,
                description=description,
                amount=amount,
                currency=currency,
                balance=balance,
            )
        )

    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_generic_bank_csv_parser.py -v 2>&1 | tail -20
```

Expected: `12 passed`.

- [ ] **Step 5: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/parsers/generic_bank_csv_parser.py tests/unit/test_generic_bank_csv_parser.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/parsers/generic_bank_csv_parser.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add portf_manager/parsers/generic_bank_csv_parser.py tests/unit/test_generic_bank_csv_parser.py
git commit -m "feat: add generic bank-statement CSV parser

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Transfer matcher (pure function)

**Files:**
- Create: `portf_manager/services/transfer_matcher.py`
- Create: `tests/unit/test_transfer_matcher.py`

**Interfaces:**
- Produces: `TransferMatch(spending_id, link_type, link_id)`, `find_transfer_match(row, candidate_spending, candidate_bookings) -> Optional[TransferMatch]`, `find_all_transfer_matches(rows, all_unlinked_spending, all_deposit_bookings) -> list[TransferMatch]` — consumed by Task 5's router.
- Consumes: plain dicts shaped like `db.list_spending_transactions()` rows (`id, portfolio_id, date, amount, currency, is_transfer`) and `db.get_all_bookings()` rows (`id, portfolio_id, date, action, amount, currency`).

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_transfer_matcher.py`:

```python
"""Tests for the pure transfer-matching logic."""

from portf_manager.services.transfer_matcher import (
    find_transfer_match,
    find_all_transfer_matches,
)


def _spending(id, portfolio_id, date, amount, currency="EUR", is_transfer=False):
    return {
        "id": id, "portfolio_id": portfolio_id, "date": date,
        "amount": amount, "currency": currency, "is_transfer": is_transfer,
    }


def _booking(id, portfolio_id, date, action, amount, currency="EUR"):
    return {
        "id": id, "portfolio_id": portfolio_id, "date": date,
        "action": action, "amount": amount, "currency": currency,
    }


def test_matches_outflow_to_inflow_same_amount():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=500.0)
    match = find_transfer_match(row, [candidate], [])
    assert match is not None
    assert match.link_type == "spending"
    assert match.link_id == 2


def test_no_match_same_account():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=10, date="2026-01-11", amount=500.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_outside_window():
    row = _spending(1, portfolio_id=10, date="2026-01-01", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-10", amount=500.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_match_at_window_boundary():
    row = _spending(1, portfolio_id=10, date="2026-01-01", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-04", amount=500.0)
    assert find_transfer_match(row, [candidate], []) is not None


def test_no_match_different_amount():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=400.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_different_currency():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0, currency="EUR")
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=500.0, currency="USD")
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_same_sign():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=-500.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_candidate_already_transfer():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=500.0, is_transfer=True)
    assert find_transfer_match(row, [candidate], []) is None


def test_matches_outflow_to_deposit_booking():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-2000.0)
    booking = _booking(5, portfolio_id=30, date="2026-01-10", action="Deposit", amount=2000.0)
    match = find_transfer_match(row, [], [booking])
    assert match is not None
    assert match.link_type == "booking"
    assert match.link_id == 5


def test_inflow_does_not_match_booking():
    """Only an outflow can match a brokerage Deposit — an inflow row would mean
    money left the brokerage account, which bookings can't represent here."""
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=2000.0)
    booking = _booking(5, portfolio_id=30, date="2026-01-10", action="Deposit", amount=2000.0)
    assert find_transfer_match(row, [], [booking]) is None


def test_withdrawal_booking_not_matched():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-2000.0)
    booking = _booking(5, portfolio_id=30, date="2026-01-10", action="Withdrawal", amount=2000.0)
    assert find_transfer_match(row, [], [booking]) is None


def test_find_all_transfer_matches_no_double_linking():
    """Two rows in the same batch can't both link to the same single counterpart."""
    rows = [
        _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0),
        _spending(2, portfolio_id=10, date="2026-01-10", amount=-500.0),
    ]
    unlinked = rows + [_spending(3, portfolio_id=20, date="2026-01-10", amount=500.0)]
    matches = find_all_transfer_matches(rows, unlinked, [])
    assert len(matches) == 1
    assert matches[0].link_id == 3


def test_find_all_transfer_matches_multiple_pairs():
    rows = [
        _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0),
        _spending(2, portfolio_id=10, date="2026-01-11", amount=-300.0),
    ]
    unlinked = rows + [
        _spending(3, portfolio_id=20, date="2026-01-10", amount=500.0),
        _spending(4, portfolio_id=20, date="2026-01-11", amount=300.0),
    ]
    matches = find_all_transfer_matches(rows, unlinked, [])
    assert {m.spending_id for m in matches} == {1, 2}
    assert {m.link_id for m in matches} == {3, 4}


def test_find_all_transfer_matches_skips_already_transfer_rows():
    rows = [_spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0, is_transfer=True)]
    matches = find_all_transfer_matches(rows, rows, [])
    assert matches == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_transfer_matcher.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError: No module named 'portf_manager.services.transfer_matcher'`.

- [ ] **Step 3: Write the matcher**

Create `portf_manager/services/transfer_matcher.py`:

```python
"""
Pure transfer-matching logic.

Links an outflow in one of the user's own accounts to a matching inflow in
another (bank-to-bank) or a brokerage Deposit booking (bank-to-brokerage),
so both sides can be excluded from spending totals and shown as a transfer
instead. No DB access here — callers pass in plain dicts already fetched
from the database, which keeps this fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

_MATCH_WINDOW_DAYS = 3


@dataclass
class TransferMatch:
    spending_id: int
    link_type: str  # "spending" or "booking"
    link_id: int


def _within_window(date_a: str, date_b: str, days: int = _MATCH_WINDOW_DAYS) -> bool:
    da = datetime.strptime(date_a, "%Y-%m-%d")
    db_date = datetime.strptime(date_b, "%Y-%m-%d")
    return abs((da - db_date).days) <= days


def find_transfer_match(
    row: dict,
    candidate_spending: List[dict],
    candidate_bookings: List[dict],
) -> Optional[TransferMatch]:
    """Find a transfer counterpart for a single spending row.

    Args:
        row: The candidate spending_transactions row (id, portfolio_id, date,
            amount, currency, is_transfer).
        candidate_spending: Other unlinked spending_transactions rows (any account).
        candidate_bookings: bookings rows (any action) — only 'Deposit' rows
            in a different portfolio are considered.

    Returns:
        TransferMatch if a counterpart is found, else None.

    Matching rule: same currency, opposite-sign equal absolute amount, date
    within +/-3 days, counterpart belongs to a different portfolio_id, and
    (for spending counterparts) not already linked as a transfer.
    """
    target_abs = abs(row["amount"])

    for cand in candidate_spending:
        if cand["id"] == row["id"]:
            continue
        if cand.get("is_transfer"):
            continue
        if cand["portfolio_id"] == row["portfolio_id"]:
            continue
        if cand.get("currency", "EUR") != row.get("currency", "EUR"):
            continue
        if abs(cand["amount"]) != target_abs:
            continue
        # Opposite sign: one is an outflow (<0), the other an inflow (>0).
        if (cand["amount"] < 0) == (row["amount"] < 0):
            continue
        if not _within_window(row["date"], cand["date"]):
            continue
        return TransferMatch(spending_id=row["id"], link_type="spending", link_id=cand["id"])

    # Only an outflow can match a brokerage Deposit booking (money leaving a
    # bank account and landing as a deposit in a brokerage account).
    if row["amount"] < 0:
        for bk in candidate_bookings:
            if bk.get("action") != "Deposit":
                continue
            if bk.get("portfolio_id") == row["portfolio_id"]:
                continue
            if bk.get("currency", "EUR") != row.get("currency", "EUR"):
                continue
            if abs(bk["amount"]) != target_abs:
                continue
            if not _within_window(row["date"], bk["date"]):
                continue
            return TransferMatch(
                spending_id=row["id"], link_type="booking", link_id=bk["id"]
            )

    return None


def find_all_transfer_matches(
    rows: List[dict],
    all_unlinked_spending: List[dict],
    all_deposit_bookings: List[dict],
) -> List[TransferMatch]:
    """Run find_transfer_match for a batch of rows (e.g. a freshly-saved import).

    Each row is matched independently against the full unlinked-spending pool
    (excluding anything already matched earlier in this same call, so two
    rows in the same batch cannot both link to the same counterpart).

    Args:
        rows: The batch to match (e.g. newly-saved rows).
        all_unlinked_spending: Full pool of unlinked spending rows, including `rows`.
        all_deposit_bookings: bookings rows with action == 'Deposit'.
    """
    matches: List[TransferMatch] = []
    consumed_spending_ids: set = set()
    consumed_booking_ids: set = set()

    for row in rows:
        if row.get("is_transfer"):
            continue
        pool = [c for c in all_unlinked_spending if c["id"] not in consumed_spending_ids]
        bookings_pool = [
            b for b in all_deposit_bookings if b["id"] not in consumed_booking_ids
        ]
        match = find_transfer_match(row, pool, bookings_pool)
        if match:
            matches.append(match)
            if match.link_type == "spending":
                consumed_spending_ids.add(match.link_id)
            else:
                consumed_booking_ids.add(match.link_id)
    return matches
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_transfer_matcher.py -v 2>&1 | tail -20
```

Expected: `13 passed`.

- [ ] **Step 5: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/services/transfer_matcher.py tests/unit/test_transfer_matcher.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/services/transfer_matcher.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add portf_manager/services/transfer_matcher.py tests/unit/test_transfer_matcher.py
git commit -m "feat: add pure transfer-matching logic for spending transactions

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: Spending router — upload/save/list/update/rescan/rules/summary

**Files:**
- Create: `portf_server/routers/spending.py`
- Modify: `portf_server/app.py`

**Interfaces:**
- Consumes: `parse_generic_bank_csv` (Task 3), `find_all_transfer_matches` (Task 4), `db.create_spending_transaction` / `find_duplicate_spending_transaction` / `list_spending_transactions` / `get_spending_transaction` / `update_spending_transaction` / `list_unlinked_spending_transactions` / `create_spending_rule` / `list_spending_rules` / `delete_spending_rule` / `get_or_create_portfolio` / `get_all_bookings` (Task 1).
- Produces: the 9 endpoints listed below, registered at prefix `/api/v1/spending`. Task 6 appends `/suggest-categories` to this same file. Task 8 (JS) consumes all of them.

- [ ] **Step 1: Write the router**

Create `portf_server/routers/spending.py`:

```python
"""
Spending Router for Portfolio Management API

Bank-statement transaction import, rule-based categorization, and
inter-account transfer detection. Kept separate from the investment import
router (imports.py) — spending rows have no asset/quantity/price and use
different dedup + transfer semantics.
"""

import logging
from typing import List, Literal, Optional

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
from pydantic import BaseModel

from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv
from portf_manager.services.transfer_matcher import find_all_transfer_matches

from ..dependencies import get_database
from ..auth_middleware import APIKeyManager, require_api_key
from ..dependencies import get_api_key_manager

router = APIRouter()
logger = logging.getLogger(__name__)


async def _auth(
    request: Request, api_key_manager: APIKeyManager = Depends(get_api_key_manager)
) -> dict:
    return await require_api_key(api_key_manager)(request)


def _fx(currency: str) -> float:
    """EUR conversion rate — delegates to the portfolios router helper.

    Lazy import to avoid a circular import (portfolios.py doesn't import
    this module), matching the pattern already used in portfolio_advisor.py.
    """
    from portf_server.routers.portfolios import _get_fx_rate

    return _get_fx_rate(currency)


class PreviewSpendingRow(BaseModel):
    date: str
    description: str
    amount: float
    currency: str = "EUR"
    category: str = "uncategorized"
    is_duplicate: bool = False


class SpendingUploadResponse(BaseModel):
    account_portfolio_id: int
    rows: List[PreviewSpendingRow]
    skipped_count: int
    skipped: List[dict]
    duplicate_count: int


class SpendingSaveRequest(BaseModel):
    account_portfolio_id: int
    rows: List[PreviewSpendingRow]
    duplicate_action: Literal["skip", "add", "overwrite"] = "skip"


class SpendingSaveResponse(BaseModel):
    saved: int
    duplicates_skipped: int
    overwritten: int
    transfers_linked: int
    errors: List[str]


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


class CategoryUpdateBody(BaseModel):
    category: str


class SpendingRuleBody(BaseModel):
    pattern: str
    category: str


class SpendingRuleResponse(BaseModel):
    id: int
    pattern: str
    category: str


class SpendingSummaryResponse(BaseModel):
    spent_eur: float
    income_eur: float
    transferred_eur: float
    by_category_eur: dict


def _apply_rules(description: str, rules: List[dict]) -> str:
    """First-match-wins, case-insensitive substring match.

    Rules are already ordered by id (oldest = highest priority) by
    db.list_spending_rules().
    """
    desc_lower = description.lower()
    for rule in rules:
        if rule["pattern"].lower() in desc_lower:
            return rule["category"]
    return "uncategorized"


def _resolve_account(
    db, account_portfolio_id: Optional[int], account_name: Optional[str]
) -> int:
    if account_portfolio_id:
        return account_portfolio_id
    if account_name:
        return db.get_or_create_portfolio(
            account_name, base_currency="EUR", account_type="bank"
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Provide either account_portfolio_id or account_name",
    )


@router.post("/upload", response_model=SpendingUploadResponse)
async def upload_bank_statement(
    file: UploadFile = File(..., description="Bank statement CSV"),
    account_portfolio_id: Optional[int] = Form(None),
    account_name: Optional[str] = Form(None),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Parse a bank statement CSV and return a rule-categorized preview. No DB write."""
    portfolio_id = _resolve_account(db, account_portfolio_id, account_name)

    file_bytes = await file.read()
    try:
        content = file_bytes.decode("utf-8-sig")
        result = parse_generic_bank_csv(content)
    except Exception as e:
        logger.exception("Error parsing bank statement")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file: {str(e)}",
        )

    rules = db.list_spending_rules()
    dup_count = 0
    rows: List[PreviewSpendingRow] = []
    for r in result.rows:
        category = _apply_rules(r.description, rules)
        is_dup = (
            db.find_duplicate_spending_transaction(
                portfolio_id=portfolio_id,
                date=r.date,
                amount=r.amount,
                description=r.description,
            )
            is not None
        )
        if is_dup:
            dup_count += 1
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

    skipped = [{"row": row, "reason": reason} for row, reason in result.skipped]
    return SpendingUploadResponse(
        account_portfolio_id=portfolio_id,
        rows=rows,
        skipped_count=len(skipped),
        skipped=skipped,
        duplicate_count=dup_count,
    )


def _run_transfer_matching(db, saved_ids: List[int]) -> int:
    """Run transfer auto-linking over the given spending row ids. Returns count linked."""
    if not saved_ids:
        return 0
    unlinked = db.list_unlinked_spending_transactions()
    rows = [r for r in unlinked if r["id"] in saved_ids]
    if not rows:
        return 0
    deposit_bookings = [b for b in db.get_all_bookings() if b.get("action") == "Deposit"]
    matches = find_all_transfer_matches(rows, unlinked, deposit_bookings)
    for m in matches:
        db.update_spending_transaction(
            m.spending_id,
            category="Transfer",
            is_transfer=True,
            transfer_link_type=m.link_type,
            transfer_link_id=m.link_id,
        )
        if m.link_type == "spending":
            db.update_spending_transaction(
                m.link_id,
                category="Transfer",
                is_transfer=True,
                transfer_link_type="spending",
                transfer_link_id=m.spending_id,
            )
    return len(matches)


@router.post("/save", response_model=SpendingSaveResponse)
async def save_spending_transactions(
    body: SpendingSaveRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Save previewed spending rows, honoring duplicate_action, then auto-link transfers."""
    saved = 0
    duplicates_skipped = 0
    overwritten = 0
    errors: List[str] = []
    saved_ids: List[int] = []

    for row in body.rows:
        try:
            existing = db.find_duplicate_spending_transaction(
                portfolio_id=body.account_portfolio_id,
                date=row.date,
                amount=row.amount,
                description=row.description,
            )
            if existing:
                if body.duplicate_action == "skip":
                    duplicates_skipped += 1
                    continue
                if body.duplicate_action == "overwrite":
                    db.update_spending_transaction(existing["id"], category=row.category)
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
            )
            saved += 1
            saved_ids.append(new_id)
        except Exception as e:
            errors.append(f"{row.date} {row.description}: {str(e)}")
            logger.warning(f"Failed to save spending row: {e}")

    transfers_linked = _run_transfer_matching(db, saved_ids)

    return SpendingSaveResponse(
        saved=saved,
        duplicates_skipped=duplicates_skipped,
        overwritten=overwritten,
        transfers_linked=transfers_linked,
        errors=errors,
    )


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
async def rescan_transfers(
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Re-run transfer matching over all currently-unlinked spending rows.

    Covers the case where a matching leg is imported later, from a different
    account's statement.
    """
    unlinked = db.list_unlinked_spending_transactions()
    ids = [r["id"] for r in unlinked]
    linked = _run_transfer_matching(db, ids)
    return {"transfers_linked": linked}


@router.get("/rules", response_model=List[SpendingRuleResponse])
async def list_rules(db=Depends(get_database), api_key_info: dict = Depends(_auth)):
    """List all spending category rules."""
    return [SpendingRuleResponse(**r) for r in db.list_spending_rules()]


@router.post("/rules", response_model=SpendingRuleResponse, status_code=201)
async def create_rule(
    body: SpendingRuleBody, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Create a spending category rule."""
    rule_id = db.create_spending_rule(pattern=body.pattern, category=body.category)
    return SpendingRuleResponse(id=rule_id, pattern=body.pattern, category=body.category)


@router.delete("/rules/{rule_id}", response_model=dict)
async def delete_rule(
    rule_id: int, db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Delete a spending category rule."""
    if not db.delete_spending_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True, "id": rule_id}


@router.get("/summary", response_model=SpendingSummaryResponse)
def get_spending_summary(
    days: int = 30,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Aggregate spending/income/transfers across all bank accounts for the
    last N days, converted to EUR. Powers the Spending page summary cards and
    the Net Worth page's read-only comparison widget.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    from datetime import date, timedelta

    start_date = (date.today() - timedelta(days=days)).isoformat()
    rows = db.list_spending_transactions(start_date=start_date)

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
            by_category_eur[r["category"]] = (
                by_category_eur.get(r["category"], 0.0) + abs(amt_eur)
            )
        else:
            income_eur += amt_eur

    return SpendingSummaryResponse(
        spent_eur=round(spent_eur, 2),
        income_eur=round(income_eur, 2),
        transferred_eur=round(transferred_eur, 2),
        by_category_eur={k: round(v, 2) for k, v in by_category_eur.items()},
    )
```

- [ ] **Step 2: Register the router in `app.py`**

In `portf_server/app.py`, the router import block (lines 27-52) ends with:

```python
from .routers import (
    action_items,
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
    deposits,
    notifications,
)
```

Add `spending,` after `bookings,`:

```python
from .routers import (
    action_items,
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
    spending,
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
    deposits,
    notifications,
)
```

Then find the `bookings` router registration (lines 338-343):

```python
app.include_router(
    bookings.router,
    prefix="/api/v1/bookings",
    tags=["Bookings"],
    dependencies=_PROTECTED,
)
```

Add immediately after it:

```python
app.include_router(
    bookings.router,
    prefix="/api/v1/bookings",
    tags=["Bookings"],
    dependencies=_PROTECTED,
)

app.include_router(
    spending.router,
    prefix="/api/v1/spending",
    tags=["Spending"],
    dependencies=_PROTECTED,
)
```

- [ ] **Step 3: Verify the app starts and the router is mounted**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run python -c "from portf_server.app import app; print([r.path for r in app.routes if 'spending' in r.path])"
```

Expected: a list of `/api/v1/spending/...` paths (upload, save, list, update, rescan-transfers, rules, summary).

- [ ] **Step 4: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/spending.py portf_server/app.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/spending.py portf_server/app.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/spending.py portf_server/app.py
git commit -m "feat: add spending router (upload/save/list/update/rescan/rules/summary)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 6: LLM-assisted category suggestions

**Files:**
- Modify: `portf_server/routers/spending.py`

**Interfaces:**
- Consumes: `get_llm_client()` from `portf_manager.llm_client` (`generate(prompt: str) -> str`).
- Produces: `POST /api/v1/spending/suggest-categories` — consumed by Task 10 (JS import UI).

- [ ] **Step 1: Add imports**

In `portf_server/routers/spending.py`, the top-of-file imports currently end with:

```python
from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv
from portf_manager.services.transfer_matcher import find_all_transfer_matches

from ..dependencies import get_database
```

Add `json` to the stdlib import and `get_llm_client`:

```python
import json
import logging
from typing import List, Literal, Optional
```

(replacing the existing `import logging` / `from typing import ...` lines at the top with the `json` import added before `logging`), and add after the `transfer_matcher` import:

```python
from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv
from portf_manager.services.transfer_matcher import find_all_transfer_matches
from portf_manager.llm_client import get_llm_client

from ..dependencies import get_database
```

- [ ] **Step 2: Add the prompt builder, request/response models, and endpoint**

At the end of `portf_server/routers/spending.py` (after `get_spending_summary`), add:

```python


class SuggestCategoriesRequest(BaseModel):
    rows: List[PreviewSpendingRow]


class CategorySuggestion(BaseModel):
    description: str
    category: str
    suggested_pattern: str


class SuggestCategoriesResponse(BaseModel):
    suggestions: List[CategorySuggestion]


def _build_suggest_prompt(descriptions: List[str]) -> str:
    lines = "\n".join(f"- {d}" for d in descriptions)
    return f"""
You categorize bank statement transaction descriptions into everyday spending
categories. For each description below, suggest ONE category from this set
(or a similarly short new one if none fit): Groceries, Dining, Transport,
Utilities, Housing, Health, Entertainment, Shopping, Income, Subscriptions,
Other.

Also suggest a short "pattern" — a distinctive substring of the description
(e.g. the merchant name) that could be reused to auto-match future rows with
the same category. Keep it as short as possible while still being specific
to this merchant (avoid matching unrelated transactions).

Return ONLY a JSON array, one object per description, in the same order:
[{{"description": "...", "category": "...", "suggested_pattern": "..."}}]

Descriptions:
{lines}
"""


@router.post("/suggest-categories", response_model=SuggestCategoriesResponse)
async def suggest_categories(
    body: SuggestCategoriesRequest,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """LLM-assisted category suggestions for rows no rule matched.

    Explicit user-triggered action (a button in the import preview) — not
    run automatically on every upload, since LLM calls are slow/costly.
    """
    if not body.rows:
        return SuggestCategoriesResponse(suggestions=[])

    descriptions = [r.description for r in body.rows]
    prompt = _build_suggest_prompt(descriptions)

    try:
        llm = get_llm_client()
        response_text = llm.generate(prompt).strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(
                ln for ln in lines if not ln.strip().startswith("```")
            )
        data = json.loads(response_text)
    except Exception as e:
        logger.warning(f"Category suggestion LLM call failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Category suggestion failed: {str(e)}",
        )

    suggestions: List[CategorySuggestion] = []
    for item in data if isinstance(data, list) else []:
        desc = str(item.get("description", "")).strip()
        category = str(item.get("category", "")).strip() or "Other"
        pattern = str(item.get("suggested_pattern", "")).strip() or desc[:20]
        if not desc:
            continue
        suggestions.append(
            CategorySuggestion(
                description=desc, category=category, suggested_pattern=pattern
            )
        )
    return SuggestCategoriesResponse(suggestions=suggestions)
```

- [ ] **Step 3: Verify the app still starts**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run python -c "from portf_server.app import app; print('ok')"
```

Expected: `ok` (no import errors — confirms `get_llm_client` import doesn't crash at module load, since it's lazy-initialized per CLAUDE.md's chat-engine gotcha and doesn't require an API key just to import).

- [ ] **Step 4: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/spending.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_server/routers/spending.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/spending.py
git commit -m "feat: add LLM-assisted category suggestions to spending router

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 7: API tests for the spending router

**Files:**
- Create: `tests/unit/test_spending_api.py`

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_spending_api.py`:

```python
"""API tests for the spending router (upload/save/list/update/rescan/rules/summary)."""

import io

import pytest
from fastapi.testclient import TestClient
from portf_manager.database import Database

_TEST_API_KEY = "test-key-spending-abc123"
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
    return TestClient(app), db_instance


def _csv_bytes(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def test_upload_creates_bank_account_and_categorizes(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_spending_rule(pattern="MERCADONA", category="Groceries")

    csv_text = "date,description,amount\n2026-01-05,MERCADONA COMPRA,-24.50\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["rows"][0]["category"] == "Groceries"
    assert d["duplicate_count"] == 0
    assert d["account_portfolio_id"] > 0

    portfolios = client.get("/api/v1/portfolios/", headers=HEADERS).json()
    bank = next(p for p in portfolios if p["name"] == "Example Bank")
    assert bank["account_type"] == "bank"


def test_upload_requires_account(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/upload",
        files={"file": ("s.csv", _csv_bytes("date,description,amount\n"), "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_save_and_list(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    r = client.post(
        "/api/v1/spending/save",
        json={
            "account_portfolio_id": pid,
            "rows": [
                {"date": "2026-01-05", "description": "MERCADONA", "amount": -24.50,
                 "currency": "EUR", "category": "Groceries"},
            ],
        },
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["saved"] == 1
    assert d["duplicates_skipped"] == 0

    listed = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert len(listed) == 1
    assert listed[0]["description"] == "MERCADONA"


def test_save_skips_duplicates_by_default(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    row = {"date": "2026-01-05", "description": "MERCADONA", "amount": -24.50,
           "currency": "EUR", "category": "Groceries"}
    client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row]},
        headers=HEADERS,
    )
    r2 = client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row]},
        headers=HEADERS,
    )
    d2 = r2.json()
    assert d2["saved"] == 0
    assert d2["duplicates_skipped"] == 1
    assert len(client.get("/api/v1/spending/", headers=HEADERS).json()) == 1


def test_save_add_duplicate_anyway(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    row = {"date": "2026-01-05", "description": "MERCADONA", "amount": -24.50,
           "currency": "EUR", "category": "Groceries"}
    client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row]},
        headers=HEADERS,
    )
    r2 = client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": [row], "duplicate_action": "add"},
        headers=HEADERS,
    )
    assert r2.json()["saved"] == 1
    assert len(client.get("/api/v1/spending/", headers=HEADERS).json()) == 2


def test_update_category(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    tx_id = db.create_spending_transaction(pid, "2026-01-05", "Desc", -10.0)
    r = client.put(
        f"/api/v1/spending/{tx_id}", json={"category": "Transport"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert client.get("/api/v1/spending/", headers=HEADERS).json()[0]["category"] == "Transport"


def test_update_category_missing_row(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.put("/api/v1/spending/999999", json={"category": "X"}, headers=HEADERS)
    assert r.status_code == 404


def test_save_auto_links_transfer_between_two_accounts(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")

    client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid_a, "rows": [
            {"date": "2026-01-10", "description": "TRASPASO A AHORRO", "amount": -500.0,
             "currency": "EUR", "category": "uncategorized"},
        ]},
        headers=HEADERS,
    )
    r = client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid_b, "rows": [
            {"date": "2026-01-11", "description": "TRASPASO", "amount": 500.0,
             "currency": "EUR", "category": "uncategorized"},
        ]},
        headers=HEADERS,
    )
    assert r.json()["transfers_linked"] == 1

    rows = client.get("/api/v1/spending/", headers=HEADERS).json()
    assert all(row["is_transfer"] for row in rows)
    assert all(row["category"] == "Transfer" for row in rows)


def test_rescan_transfers(tmp_path):
    client, db = _make_client(tmp_path)
    pid_a = db.create_portfolio("Bank A", account_type="bank")
    pid_b = db.create_portfolio("Bank B", account_type="bank")
    db.create_spending_transaction(pid_a, "2026-01-10", "Out", -500.0)
    db.create_spending_transaction(pid_b, "2026-01-11", "In", 500.0)

    r = client.post("/api/v1/spending/rescan-transfers", headers=HEADERS)
    assert r.json()["transfers_linked"] == 1


def test_transfer_to_brokerage_booking(tmp_path):
    client, db = _make_client(tmp_path)
    pid_bank = db.create_portfolio("Bank A", account_type="bank")
    pid_broker = db.create_portfolio("Example Broker", account_type="brokerage")
    db.create_booking(date="2026-01-10", action="Deposit", amount=1000.0, currency="EUR", portfolio_id=pid_broker)

    r = client.post(
        "/api/v1/spending/save",
        json={"account_portfolio_id": pid_bank, "rows": [
            {"date": "2026-01-10", "description": "TRANSFERENCIA A BROKER", "amount": -1000.0,
             "currency": "EUR", "category": "uncategorized"},
        ]},
        headers=HEADERS,
    )
    assert r.json()["transfers_linked"] == 1
    row = client.get("/api/v1/spending/", headers=HEADERS).json()[0]
    assert row["is_transfer"] is True
    assert row["transfer_link_type"] == "booking"


def test_rules_crud(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "MERCADONA", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    rule_id = r.json()["id"]

    listed = client.get("/api/v1/spending/rules", headers=HEADERS).json()
    assert len(listed) == 1

    r2 = client.delete(f"/api/v1/spending/rules/{rule_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert client.get("/api/v1/spending/rules", headers=HEADERS).json() == []


def test_delete_missing_rule(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.delete("/api/v1/spending/rules/999999", headers=HEADERS)
    assert r.status_code == 404


def test_summary_excludes_transfers(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(pid, "2026-01-05", "Groceries", -24.50, category="Groceries")
    db.create_spending_transaction(pid, "2026-01-06", "Salary", 2000.0, category="uncategorized")
    tx_transfer = db.create_spending_transaction(pid, "2026-01-07", "Transfer", -500.0)
    db.update_spending_transaction(tx_transfer, category="Transfer", is_transfer=True)

    r = client.get("/api/v1/spending/summary?days=30", headers=HEADERS)
    d = r.json()
    assert d["spent_eur"] == 24.50
    assert d["income_eur"] == 2000.0
    assert d["transferred_eur"] == 500.0
    assert d["by_category_eur"]["Groceries"] == 24.50


def test_suggest_categories(tmp_path, mocker):
    from unittest.mock import MagicMock

    client, _ = _make_client(tmp_path)
    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.return_value = (
        '[{"description": "MERCADONA COMPRA", "category": "Groceries", '
        '"suggested_pattern": "MERCADONA"}]'
    )
    mocker.patch("portf_server.routers.spending.get_llm_client", return_value=mock_llm)

    r = client.post(
        "/api/v1/spending/suggest-categories",
        json={"rows": [
            {"date": "2026-01-05", "description": "MERCADONA COMPRA", "amount": -24.50,
             "currency": "EUR", "category": "uncategorized"},
        ]},
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["suggestions"][0]["category"] == "Groceries"
    assert d["suggestions"][0]["suggested_pattern"] == "MERCADONA"


def test_suggest_categories_empty_rows_skips_llm_call(tmp_path, mocker):
    client, _ = _make_client(tmp_path)
    spy = mocker.patch("portf_server.routers.spending.get_llm_client")
    r = client.post("/api/v1/spending/suggest-categories", json={"rows": []}, headers=HEADERS)
    assert r.json()["suggestions"] == []
    spy.assert_not_called()


def test_suggest_categories_llm_failure_returns_502(tmp_path, mocker):
    from unittest.mock import MagicMock

    client, _ = _make_client(tmp_path)
    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.side_effect = RuntimeError("LLM unavailable")
    mocker.patch("portf_server.routers.spending.get_llm_client", return_value=mock_llm)

    r = client.post(
        "/api/v1/spending/suggest-categories",
        json={"rows": [
            {"date": "2026-01-05", "description": "X", "amount": -1.0,
             "currency": "EUR", "category": "uncategorized"},
        ]},
        headers=HEADERS,
    )
    assert r.status_code == 502
```

- [ ] **Step 2: Run tests**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -v 2>&1 | tail -30
```

Expected: `18 passed`.

- [ ] **Step 3: Run the full unit test suite to check nothing is broken**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -15
```

Expected: all tests pass, count higher than the pre-feature baseline (728), no failures.

- [ ] **Step 4: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black tests/unit/test_spending_api.py
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 tests/unit/test_spending_api.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_spending_api.py
git commit -m "test: add API tests for spending router

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 8: `pfm_core.js` API client methods

**Files:**
- Modify: `web_client/js/pfm_core.js`

- [ ] **Step 1: Add API client methods**

In `web_client/js/pfm_core.js`, find `saveImportedTransactions` (lines 1360-1371):

```javascript
        async saveImportedTransactions(transactions, bookings = [], portfolioId = null, duplicateAction = 'skip', deposits = []) {
            const response = await fetch(this.baseURL + '/api/v1/import/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ transactions, bookings, deposits, portfolio_id: portfolioId, duplicate_action: duplicateAction })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Save failed: ${err}`);
            }
            return response.json();
        },
```

Insert the new spending methods immediately after it:

```javascript
        async saveImportedTransactions(transactions, bookings = [], portfolioId = null, duplicateAction = 'skip', deposits = []) {
            const response = await fetch(this.baseURL + '/api/v1/import/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ transactions, bookings, deposits, portfolio_id: portfolioId, duplicate_action: duplicateAction })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Save failed: ${err}`);
            }
            return response.json();
        },

        async uploadBankStatement(file, accountPortfolioId, accountName) {
            const form = new FormData();
            form.append('file', file);
            if (accountPortfolioId) form.append('account_portfolio_id', accountPortfolioId);
            if (accountName) form.append('account_name', accountName);
            const response = await fetch(this.baseURL + '/api/v1/spending/upload', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey },
                body: form
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Parse failed: ${err}`);
            }
            return response.json();
        },
        async saveSpendingTransactions(accountPortfolioId, rows, duplicateAction = 'skip') {
            const response = await fetch(this.baseURL + '/api/v1/spending/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ account_portfolio_id: accountPortfolioId, rows, duplicate_action: duplicateAction })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Save failed: ${err}`);
            }
            return response.json();
        },
        async suggestSpendingCategories(rows) {
            const response = await fetch(this.baseURL + '/api/v1/spending/suggest-categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ rows })
            });
            if (!response.ok) {
                const err = await response.text();
                throw new Error(`Suggestion failed: ${err}`);
            }
            return response.json();
        },
        async getSpendingTransactions(params = {}) {
            const qs = new URLSearchParams(params).toString();
            const response = await fetch(this.baseURL + '/api/v1/spending/' + (qs ? '?' + qs : ''), {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load spending transactions');
            return response.json();
        },
        async updateSpendingCategory(id, category) {
            const response = await fetch(this.baseURL + '/api/v1/spending/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ category })
            });
            if (!response.ok) throw new Error('Failed to update category');
            return response.json();
        },
        async rescanTransfers() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rescan-transfers', {
                method: 'POST',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to rescan transfers');
            return response.json();
        },
        async getSpendingRules() {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load rules');
            return response.json();
        },
        async createSpendingRule(pattern, category) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ pattern, category })
            });
            if (!response.ok) throw new Error('Failed to create rule');
            return response.json();
        },
        async deleteSpendingRule(id) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules/' + id, {
                method: 'DELETE',
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to delete rule');
            return response.json();
        },
        async getSpendingSummary(days = 30) {
            const response = await fetch(this.baseURL + '/api/v1/spending/summary?days=' + days, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load spending summary');
            return response.json();
        },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add spending API client methods

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 9: Spending page skeleton + nav entries (`index.html`)

**Files:**
- Modify: `web_client/index.html`

- [ ] **Step 1: Add nav entries (mobile offcanvas)**

In `web_client/index.html`, find (lines 141-143):

```html
                <a class="sidebar-nav-link" href="#" data-page="actionitems">
                    <i class="bi bi-list-check me-2"></i>Action Items
                </a>
```

Insert immediately after it:

```html
                <a class="sidebar-nav-link" href="#" data-page="actionitems">
                    <i class="bi bi-list-check me-2"></i>Action Items
                </a>

                <a class="sidebar-nav-link" href="#" data-page="spending">
                    <i class="bi bi-wallet2 me-2"></i>Spending
                </a>
```

- [ ] **Step 2: Add nav entries (desktop sidebar)**

In `web_client/index.html`, find (lines 205-207 — the second, near-identical copy):

```html
                <a class="sidebar-nav-link" href="#" data-page="actionitems">
                    <i class="bi bi-list-check me-2"></i>Action Items
                </a>
```

Insert immediately after it (same block, second occurrence):

```html
                <a class="sidebar-nav-link" href="#" data-page="actionitems">
                    <i class="bi bi-list-check me-2"></i>Action Items
                </a>

                <a class="sidebar-nav-link" href="#" data-page="spending">
                    <i class="bi bi-wallet2 me-2"></i>Spending
                </a>
```

- [ ] **Step 3: Add the page content div + import modal**

In `web_client/index.html`, find the `actionitemsPage` div (lines 2449-2460):

```html
                <div id="actionitemsPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-list-check me-2 text-primary"></i>Action Items<button class="btn btn-sm btn-link p-0 ms-2 align-baseline" onclick="showPageHelp('actionitems')" title="What is this page?"><i class="bi bi-question-circle"></i></button></h4>
                            <p class="text-muted small mb-0">Everything that needs your attention, in one place.</p>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary" id="refreshActionItems" title="Refresh"><i class="bi bi-arrow-clockwise"></i></button>
                        </div>
                    </div>
                    <div id="actionItemsList">
                        <div class="text-muted small">Loading…</div>
                    </div>
                </div>
```

Insert a new page div immediately after its closing `</div>` (before the `<!-- Diagnostics Page -->` comment):

```html
                <!-- Spending Page -->
                <div id="spendingPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-wallet2 me-2 text-primary"></i>Spending<button class="btn btn-sm btn-link p-0 ms-2 align-baseline" onclick="showPageHelp('spending')" title="What is this page?"><i class="bi bi-question-circle"></i></button></h4>
                            <p class="text-muted small mb-0">Categorized bank-account spending, income, and transfers between your own accounts.</p>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary" id="spRescanTransfers" title="Re-scan for transfers"><i class="bi bi-arrow-repeat"></i> Re-scan transfers</button>
                            <button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#spImportModal"><i class="bi bi-upload me-1"></i>Import statement</button>
                        </div>
                    </div>

                    <div class="row g-2 mb-3">
                        <div class="col-6 col-md-3">
                            <div class="card h-100 border-danger">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1">Spent (30d)</div>
                                    <div class="fs-6 fw-bold text-danger" id="spSpent">—</div>
                                </div>
                            </div>
                        </div>
                        <div class="col-6 col-md-3">
                            <div class="card h-100 border-success">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1">Income (30d)</div>
                                    <div class="fs-6 fw-bold text-success" id="spIncome">—</div>
                                </div>
                            </div>
                        </div>
                        <div class="col-12 col-md-6">
                            <div class="card h-100">
                                <div class="card-body py-2">
                                    <div class="small text-muted mb-1">Moved to other accounts (30d)</div>
                                    <div class="fs-6 fw-bold" id="spTransferred">—</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="card mb-3">
                        <div class="card-body py-2">
                            <div class="row g-2 align-items-end">
                                <div class="col-6 col-md-3">
                                    <label class="form-label small mb-1">Account</label>
                                    <select class="form-select form-select-sm" id="spAccountFilter"><option value="">All accounts</option></select>
                                </div>
                                <div class="col-6 col-md-3">
                                    <label class="form-label small mb-1">Category</label>
                                    <select class="form-select form-select-sm" id="spCategoryFilter"><option value="">All categories</option></select>
                                </div>
                                <div class="col-6 col-md-3">
                                    <label class="form-label small mb-1">From</label>
                                    <input type="date" class="form-control form-control-sm" id="spFromDate">
                                </div>
                                <div class="col-6 col-md-3">
                                    <label class="form-label small mb-1">To</label>
                                    <input type="date" class="form-control form-control-sm" id="spToDate">
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="card mb-3">
                        <div class="card-header fw-semibold">Spending by category</div>
                        <div class="card-body">
                            <div id="spCategoryChart"><div class="text-muted small">Loading…</div></div>
                        </div>
                    </div>

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
                        </div>
                    </div>
                </div>

                <!-- Import Bank Statement Modal -->
                <div class="modal fade" id="spImportModal" tabindex="-1">
                    <div class="modal-dialog modal-lg modal-dialog-scrollable">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h5 class="modal-title">Import Bank Statement</h5>
                                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                            </div>
                            <div class="modal-body">
                                <div class="row g-2 mb-3">
                                    <div class="col-12 col-sm-6">
                                        <label class="form-label small mb-1">Account</label>
                                        <select class="form-select form-select-sm" id="spImportAccountSelect"><option value="">— New account —</option></select>
                                    </div>
                                    <div class="col-12 col-sm-6">
                                        <label class="form-label small mb-1">New account name (if not selected above)</label>
                                        <input class="form-control form-control-sm" id="spImportAccountName" placeholder="e.g. BBVA Checking">
                                    </div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label small mb-1">CSV file</label>
                                    <input type="file" class="form-control form-control-sm" id="spImportFile" accept=".csv">
                                    <div class="form-text">Columns: date, description, amount (optional: balance, currency). <a href="#" id="spDownloadTemplate">Download template</a></div>
                                </div>
                                <div id="spImportPreview"></div>
                            </div>
                            <div class="modal-footer">
                                <div class="small text-muted me-auto" id="spImportStatus"></div>
                                <button type="button" class="btn btn-outline-secondary" id="spParseBtn">Parse</button>
                                <button type="button" class="btn btn-outline-primary" id="spSuggestBtn" style="display:none;">Suggest categories (AI)</button>
                                <button type="button" class="btn btn-primary" id="spSaveBtn" style="display:none;">Save</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Diagnostics Page -->
```

- [ ] **Step 2: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Spending page skeleton, nav entries, and import modal

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 10: Spending page logic (`pfm_features.js`)

**Files:**
- Modify: `web_client/js/pfm_features.js`
- Modify: `web_client/js/tests/web_client.test.mjs`

**Interfaces:**
- Consumes: all `pfm_core.js` methods from Task 8; `makeSortableTable`, `esc`, `escapeForAttr`, `Fmt` from `pfm_core.js` (already global).
- Produces: `window.loadSpendingPage`, `window.filterSpendingRows` (pure, unit-tested), `window.updateSpendingRowCategory`, `window.deleteSpendingRule`, `window.downloadGenericBankTemplate`.

- [ ] **Step 1: Register the page in the navigation manager**

In `web_client/js/pfm_features.js`, find the `pages` array in `showPage` (line 407):

```javascript
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage', 'helpPage', 'versionPage', 'aboutPage', 'resourcesPage', 'networthPage', 'diagnosticsPage', 'actionitemsPage'];
```

Replace with (adds `'spendingPage'`):

```javascript
            const pages = ['dashboardPage', 'assetsPage', 'transactionsPage', 'analyticsPage', 'watchlistPage', 'goalsPage', 'researchPage', 'chatPage', 'importexportPage', 'portfoliosPage', 'forecastPage', 'helpPage', 'versionPage', 'aboutPage', 'resourcesPage', 'networthPage', 'diagnosticsPage', 'actionitemsPage', 'spendingPage'];
```

Find `PAGE_TITLES` (lines 432-441):

```javascript
            const PAGE_TITLES = {
                dashboard: 'Dashboard', assets: 'Assets', transactions: 'Transactions',
                analytics: 'Analytics', watchlist: 'Watchlist',
                goals: 'Goals', research: 'Research', chat: 'AI Chat',
                importexport: 'Import / Export', portfolios: 'Brokers',
                forecast: 'Wealth Simulator', help: 'Help & Guide',
                version: "What's New", about: 'About', resources: 'Resources',
                networth: 'Net Worth', diagnostics: 'Diagnostics',
                actionitems: 'Action Items',
            };
```

Replace with (adds `spending: 'Spending'`):

```javascript
            const PAGE_TITLES = {
                dashboard: 'Dashboard', assets: 'Assets', transactions: 'Transactions',
                analytics: 'Analytics', watchlist: 'Watchlist',
                goals: 'Goals', research: 'Research', chat: 'AI Chat',
                importexport: 'Import / Export', portfolios: 'Brokers',
                forecast: 'Wealth Simulator', help: 'Help & Guide',
                version: "What's New", about: 'About', resources: 'Resources',
                networth: 'Net Worth', diagnostics: 'Diagnostics',
                actionitems: 'Action Items', spending: 'Spending',
            };
```

Find the `loadPageData` switch (line 467, the `actionitems` case):

```javascript
                case 'actionitems':  if (window.loadActionItemsPage) window.loadActionItemsPage(); break;
```

Insert immediately after it:

```javascript
                case 'actionitems':  if (window.loadActionItemsPage) window.loadActionItemsPage(); break;
                case 'spending':     if (window.loadSpendingPage) window.loadSpendingPage(); break;
```

- [ ] **Step 2: Add the Spending page module**

At the end of `web_client/js/pfm_features.js`, add:

```javascript

// ---------------------------------------------------------------------------
// Spending Page
// ---------------------------------------------------------------------------

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

async function loadSpendingPage() {
    _wireSpendingRuleForm();
    _wireSpendingImportModal();
    const rescanBtn = document.getElementById('spRescanTransfers');
    if (rescanBtn && !rescanBtn.dataset.wired) {
        rescanBtn.dataset.wired = '1';
        rescanBtn.addEventListener('click', async () => {
            rescanBtn.disabled = true;
            try {
                await window.apiClient.rescanTransfers();
                await _refreshSpendingData();
            } catch (err) { alert('Error: ' + err.message); }
            rescanBtn.disabled = false;
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

function _populateSpendingAccountFilters(bankAccounts) {
    const filterSel = document.getElementById('spAccountFilter');
    const importSel = document.getElementById('spImportAccountSelect');
    const opts = bankAccounts.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
    if (filterSel) filterSel.innerHTML = '<option value="">All accounts</option>' + opts;
    if (importSel) importSel.innerHTML = '<option value="">— New account —</option>' + opts;
}

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

window.updateSpendingRowCategory = async function (id, category) {
    try {
        await window.apiClient.updateSpendingCategory(id, category);
        const row = (window._spendingAllRows || []).find(r => r.id === id);
        if (row) row.category = category;
    } catch (err) { alert('Error: ' + err.message); }
};

function _renderSpendingRules(rules) {
    const body = document.getElementById('spRulesBody');
    if (!body) return;
    body.innerHTML = rules.length ? rules.map(r => `
        <tr>
            <td class="ps-3">${esc(r.pattern)}</td>
            <td>${esc(r.category)}</td>
            <td class="pe-3 text-end"><button class="btn btn-sm btn-outline-danger" onclick="window.deleteSpendingRule(${r.id})"><i class="bi bi-trash"></i></button></td>
        </tr>`).join('') : '<tr><td colspan="3" class="text-center text-muted py-2">No rules yet.</td></tr>';
}

window.deleteSpendingRule = async function (id) {
    if (!confirm('Delete this rule?')) return;
    try {
        await window.apiClient.deleteSpendingRule(id);
        await _refreshSpendingData();
    } catch (err) { alert('Error: ' + err.message); }
};

function _wireSpendingRuleForm() {
    const form = document.getElementById('spRuleAddForm');
    if (form && !form.dataset.wired) {
        form.dataset.wired = '1';
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const pattern = document.getElementById('spRulePattern').value.trim();
            const category = document.getElementById('spRuleCategory').value.trim();
            if (!pattern || !category) return;
            try {
                await window.apiClient.createSpendingRule(pattern, category);
                form.reset();
                await _refreshSpendingData();
            } catch (err) { alert('Error: ' + err.message); }
        });
    }
}

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
                    preview_.account_portfolio_id, preview_.rows, 'skip'
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

function _renderSpImportPreview(result) {
    const preview = document.getElementById('spImportPreview');
    if (!preview) return;
    preview.innerHTML = `
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

// Generates and downloads the generic bank-statement CSV import template.
function downloadGenericBankTemplate() {
    const csv = [
        'date,description,amount,currency',
        '2026-01-05,MERCADONA COMPRA,-24.50,EUR',
        '2026-01-06,NOMINA EMPRESA SL,2100.00,EUR',
        '2026-01-10,TRASPASO A AHORRO,-500.00,EUR',
    ].join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'generic_bank_import_template.csv';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
}
window.downloadGenericBankTemplate = downloadGenericBankTemplate;
```

- [ ] **Step 3: Add a unit test for the pure `filterSpendingRows` helper**

In `web_client/js/tests/web_client.test.mjs`, add at the end of the file:

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
```

- [ ] **Step 4: Run the JS test suite**

```bash
node --test web_client/js/tests/ 2>&1 | tail -30
```

Expected: all tests pass, including the 4 new `filterSpendingRows` tests.

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: add Spending page logic, nav wiring, and filterSpendingRows helper

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 11: Net Worth "Actual" comparison widget

**Files:**
- Modify: `web_client/index.html`
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Add the widget HTML**

In `web_client/index.html`, the Monthly Cash Flow summary-cards row is at lines 2775-2816, ending:

```html
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
```

Insert a new row immediately after the `row g-2 mb-3` closing `</div>` and before the `card-body pb-0` closing `</div>`:

```html
                                        <div class="col-12 col-md-4">
                                            <div class="card h-100 text-white" id="cfNetCard" style="background:#0d6efd;">
                                                <div class="card-body py-2">
                                                    <div class="small opacity-75 mb-1">Net / month</div>
                                                    <div class="fs-6 fw-bold" id="cfNet">—</div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="row g-2 mb-2" id="cfActualRow" style="display:none;">
                                        <div class="col-12">
                                            <div class="small text-muted mb-1">Actual (last 30 days, from imported spending) <span class="ms-1" style="cursor:help" title="Computed from categorized bank imports on the Spending page. Comparison only — does not affect the figures above or Goals/Forecast projections."><i class="bi bi-info-circle"></i></span></div>
                                        </div>
                                        <div class="col-6 col-md-3">
                                            <div class="card h-100 border-success">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Actual income</div>
                                                    <div class="fs-6 fw-bold text-success" id="cfActualIncome">—</div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-6 col-md-3">
                                            <div class="card h-100 border-danger">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Actual spending</div>
                                                    <div class="fs-6 fw-bold text-danger" id="cfActualSpent">—</div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-12 col-md-6">
                                            <div class="card h-100">
                                                <div class="card-body py-2">
                                                    <div class="small text-muted mb-1">Moved to other accounts</div>
                                                    <div class="fs-6 fw-bold" id="cfActualTransferred">—</div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
```

- [ ] **Step 2: Add the widget loader in `pfm_analytics.js`**

In `web_client/js/pfm_analytics.js`, `loadNetworthPage` (lines 30-64) contains:

```javascript
        const cf = await _loadCashflow();
```

Insert immediately after it:

```javascript
        const cf = await _loadCashflow();
        _loadActualSpendingComparison();
```

Then, immediately after the closing `}` of `_loadCashflow` (after line 427, before `function _wireCashflowForm()`), add:

```javascript
async function _loadActualSpendingComparison() {
    const row = document.getElementById('cfActualRow');
    if (!row) return;
    try {
        const s = await window.apiClient.getSpendingSummary(30);
        if (!s || (s.spent_eur === 0 && s.income_eur === 0 && s.transferred_eur === 0)) return;
        const eur = v => Fmt.amt('€' + Fmt.num(v, 0, 0));
        const el = id => document.getElementById(id);
        if (el('cfActualIncome')) el('cfActualIncome').innerHTML = eur(s.income_eur);
        if (el('cfActualSpent')) el('cfActualSpent').innerHTML = eur(s.spent_eur);
        if (el('cfActualTransferred')) el('cfActualTransferred').innerHTML = eur(s.transferred_eur);
        row.style.display = '';
    } catch (err) {
        // Silent — this is a supplementary comparison widget, not core Net Worth data.
    }
}
```

- [ ] **Step 3: Run the JS test suite to confirm nothing broke**

```bash
node --test web_client/js/tests/ 2>&1 | tail -20
```

Expected: all tests pass (the smoke-load test in Step "split loads in one scope" would fail if this introduced a load-time syntax error).

- [ ] **Step 4: Commit**

```bash
git add web_client/index.html web_client/js/pfm_analytics.js
git commit -m "feat: add read-only Actual spending comparison widget to Net Worth page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 12: Help text

**Files:**
- Modify: `web_client/js/help_text.js`

- [ ] **Step 1: Add a `spending` PAGE_HELP entry**

In `web_client/js/help_text.js`, find the end of the `networth` entry and the start of `stressTest` (lines 187-188):

```javascript
      <p class="text-muted small mb-0">All amounts converted to EUR at live FX rates.</p>`
  },
  stressTest: {
```

Insert a new `spending` entry between them:

```javascript
      <p class="text-muted small mb-0">All amounts converted to EUR at live FX rates.</p>`
  },
  spending: {
    title: "Spending",
    body: `
      <p>Categorized day-to-day spending imported from your bank/checking accounts — separate from the investment transactions tracked elsewhere in the app.</p>
      <ul class="mb-2">
        <li><strong>Import a statement</strong> (CSV: date, description, amount) via the button top-right. Each row is matched against your saved <strong>category rules</strong> (a keyword in the description, e.g. "MERCADONA" → Groceries); anything left uncategorized can be resolved with the <strong>"Suggest categories (AI)"</strong> button — review and edit before saving. Accepting a suggestion creates a new rule automatically, so future imports for that merchant auto-categorize.</li>
        <li><strong>Transfers</strong> between your own accounts (e.g. checking → savings, or checking → a brokerage account already tracked here) are detected automatically by matching an outflow in one account to an inflow of the same amount within a few days in another — shown separately, not counted as spending. Use "Re-scan transfers" if you import a matching account's statement later.</li>
        <li>Click a row's category to change it by hand at any time.</li>
      </ul>
      <p class="text-muted small mb-0">A read-only summary of the last 30 days also appears on the Net Worth page, next to your manual Monthly Cash Flow entries, for comparison.</p>`
  },
  stressTest: {
```

- [ ] **Step 2: Extend the `networth` entry**

In `web_client/js/help_text.js`, the `networth` entry's `<ul>` currently ends with (line 184):

```javascript
        <li>FIRE goals project from total net worth, not just the brokerage value.</li>
      </ul>
```

Replace with (adds a bullet before the closing `</ul>`):

```javascript
        <li>FIRE goals project from total net worth, not just the brokerage value.</li>
        <li>If you import bank statements on the <strong>Spending</strong> page, an "Actual" comparison appears here for the last 30 days — read-only, doesn't change the manual figures above.</li>
      </ul>
```

- [ ] **Step 3: Commit**

```bash
git add web_client/js/help_text.js
git commit -m "docs: add Spending page help text, extend Net Worth help for Actual widget

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 13: Rebuild, smoke test, docs

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rebuild and restart both services**

```bash
docker exec portf_backend_dev kill -HUP 1
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

Expected: both containers restart cleanly (`docker compose ps` shows them `Up`).

- [ ] **Step 2: Create fictional sample CSVs for an end-to-end smoke test**

Create `/tmp/spending-smoke-a.csv` (do not commit this file):

```
date,description,amount,currency
2026-01-05,MERCADONA COMPRA,-24.50,EUR
2026-01-06,NOMINA EMPRESA SL,2100.00,EUR
2026-01-10,TRASPASO A AHORRO,-500.00,EUR
```

Create `/tmp/spending-smoke-b.csv`:

```
date,description,amount,currency
2026-01-11,TRASPASO RECIBIDO,500.00,EUR
```

- [ ] **Step 3: Exercise the golden path via the running API**

```bash
API_KEY=$(docker exec portf_backend_dev python3 -c "
from portf_manager.database import Database
db = Database('/app/portfolio.db')
print(db.get_connection().__enter__().execute('SELECT raw_key FROM api_keys LIMIT 1').fetchone()[0])
" 2>/dev/null) || echo "Use your existing SERVER_API_KEY from ~/repos/pfm/.env.local instead"

curl -s -X POST http://localhost:8080/api/v1/spending/upload \
  -H "X-API-Key: $API_KEY" \
  -F "account_name=Smoke Test Bank A" \
  -F "file=@/tmp/spending-smoke-a.csv" | tee /tmp/spending-smoke-a-preview.json

# copy the account_portfolio_id and rows array from the preview into a save call:
curl -s -X POST http://localhost:8080/api/v1/spending/save \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d @/tmp/spending-smoke-a-preview.json

curl -s -X POST http://localhost:8080/api/v1/spending/upload \
  -H "X-API-Key: $API_KEY" \
  -F "account_name=Smoke Test Bank B" \
  -F "file=@/tmp/spending-smoke-b.csv" | tee /tmp/spending-smoke-b-preview.json

curl -s -X POST http://localhost:8080/api/v1/spending/save \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d @/tmp/spending-smoke-b-preview.json

curl -s "http://localhost:8080/api/v1/spending/?is_transfer=true" -H "X-API-Key: $API_KEY"
```

Expected: the last call returns two rows (one per account), both `"is_transfer": true`, `"category": "Transfer"` — confirming the transfer auto-link worked end-to-end through the real HTTP API against the running containers, not just in-process tests. The `/save` calls' JSON body needs `account_portfolio_id` + `rows` — if the preview JSON shape doesn't paste directly into `-d @file`, adapt manually referencing `SpendingSaveRequest`'s shape from Task 5.

Then open the app in a browser at `http://localhost:8080`, navigate to **Spending**, and confirm: the two Smoke Test accounts and their transfer-linked rows render correctly, the summary cards show non-zero values, and the Net Worth page's new "Actual" widget appears with matching figures. Clean up the two smoke-test bank portfolios afterward via the Brokers page (or leave them — they contain no real data).

- [ ] **Step 4: Run the full test suite**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -15
node --test web_client/js/tests/ 2>&1 | tail -20
```

Expected: all tests pass, 0 failures.

- [ ] **Step 5: Update `PROJECT_STATUS.md`**

Change:
```
Last updated: 2026-07-17
```
to:
```
Last updated: 2026-07-19
```

Prepend a new line before the existing `**Recent (v2.5.18):**` line:

```
**Recent (v2.5.19):** **Bank spending tracking + inter-account transfer detection.** New `spending_transactions`/`spending_rules` tables (db v25) plus `portfolios.account_type` (brokerage/bank). Bank statements import via a new generic CSV parser (date/description/amount, EU/US auto-detect) into a new `spending.py` router; rows auto-categorize against saved description-match rules, with an LLM "Suggest categories" fallback for anything unmatched (accepting a suggestion creates a new rule). A pure `transfer_matcher.py` auto-links an outflow in one account to a same-amount inflow in another within ±3 days — bank-to-bank or bank-to-brokerage (matched against existing `bookings` Deposits) — so transfers are excluded from spending totals. New "Spending" nav page (import, category breakdown, sortable transaction table, rules management); Net Worth page gets a read-only 30-day "Actual" comparison next to the manual Monthly Cash Flow entries. Dedicated parsers for specific banks (Abanca, Caixa Enginyers, Revolut, MyInvestor cash) deferred pending real sample export files — the generic parser covers them in the meantime.
```

- [ ] **Step 6: Add a `## Spending Tracking` section to `CLAUDE.md`**

In `CLAUDE.md`, change:
```
**Current schema version: 24.**
```
to:
```
**Current schema version: 25.**
```

Find the v24 migration-history line:
```
- v24: `chat_sessions` (id TEXT PK, name, created_at, last_message_at, message_count, messages JSON) — persistent named chat threads; `db.create/get/list/update/delete_chat_session`; web: col-md-3 sidebar + col-md-9 message area
```

Add immediately after it:
```
- v25: `portfolios.account_type` (`'brokerage'`|`'bank'`, default brokerage — a bank account is a portfolio too); `spending_transactions` (id, portfolio_id, date, description, amount [signed: −out/+in], currency, category, is_transfer, transfer_link_type [`'spending'`|`'booking'`], transfer_link_id, source, created_at); `spending_rules` (id, pattern, category, created_at — global, case-insensitive substring match, first-match-by-id wins). See "Spending Tracking" section below.
```

Then add a new top-level section — find the `### Watchlist / Goals / Sync APIs` heading and insert a new section immediately before it:

```markdown
### Spending Tracking (`portf_server/routers/spending.py` + `portf_manager/parsers/generic_bank_csv_parser.py` + `portf_manager/services/transfer_matcher.py`)

Bank-account spending tracking, kept deliberately separate from the investment `transactions`/`bookings` tables — a bank statement row has no asset/quantity/price and uses different dedup + transfer semantics. A bank account is still a `portfolios` row (`account_type='bank'`) so it gets the existing broker/account list machinery for free; Holdings/Rebalance/position endpoints need no filtering change since they source only from `transactions`, which bank accounts never populate.

- `POST /api/v1/spending/upload` — multipart file + `account_portfolio_id` or `account_name` (auto-creates via `get_or_create_portfolio(..., account_type='bank')`) → parsed + rule-categorized preview, no DB write. Uses `generic_bank_csv_parser.parse_generic_bank_csv` (required cols `date, description, amount`; optional `balance, currency`; EN/ES/NL header synonyms; reuses `generic_csv_parser.py`'s EU/US delimiter/date/decimal detection helpers rather than duplicating them).
- `POST /api/v1/spending/suggest-categories` — LLM-assisted category + rule-pattern suggestions for rows no rule matched, via `get_llm_client().generate()`. Explicit user-triggered button, not automatic (LLM calls are slow/costly). Accepting a suggestion in the UI creates a new `spending_rules` row so future imports auto-match — rules only grow from confirmed human decisions.
- `POST /api/v1/spending/save` — writes `spending_transactions`, honoring `duplicate_action` (skip/add/overwrite, dedup on portfolio+date+amount+description via `find_duplicate_spending_transaction`), then runs transfer auto-linking over the newly-saved batch.
- `GET /api/v1/spending/` (filters: `portfolio_id`, `category`, `start_date`, `end_date`, `is_transfer`), `PUT /api/v1/spending/{id}` (category only), `GET/POST /api/v1/spending/rules`, `DELETE /api/v1/spending/rules/{id}`, `GET /api/v1/spending/summary?days=30` (EUR-converted spent/income/transferred + by-category breakdown, powers both the Spending page cards and the Net Worth "Actual" widget), `POST /api/v1/spending/rescan-transfers`.
- **Transfer auto-linking** (`transfer_matcher.find_all_transfer_matches`, pure/DB-free): an outflow matches an inflow of the same absolute amount + currency within ±3 days in a *different* account — either another bank account's unlinked `spending_transactions` row, or an existing brokerage `bookings` Deposit (covers "transfer to Indexa/MyInvestor" — `bookings` itself is never modified, only the spending side gets `transfer_link_type='booking'`). Matched rows get `category='Transfer'`, `is_transfer=1`. Runs after every `/save` and on-demand via `/rescan-transfers` (covers importing the matching leg later, from a different account's statement).
- **Deferred**: dedicated parsers for Abanca, Caixa Enginyers, Revolut, and MyInvestor-cash (real export column layouts not yet available) — the generic parser covers them for now; add dedicated parsers following the exact pattern of `indexacapital_csv_parser.py`/`myinvestor_csv_parser.py` once sample files are provided.
- Web: new top-level "Spending" nav page (import modal, category breakdown, sortable transaction table with inline category edit, rules management) in `pfm_features.js`; Net Worth page gets a read-only "Actual (last 30 days)" comparison next to the manual Monthly Cash Flow fields in `pfm_analytics.js` — comparison only, does not feed Goals/Forecast.

```

- [ ] **Step 7: Format and lint everything one final time**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/ portf_server/ tests/
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
```

Expected: no errors.

- [ ] **Step 8: Run the full test suite one final time**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
node --test web_client/js/tests/ 2>&1 | tail -10
```

Expected: all tests pass, 0 failures.

- [ ] **Step 9: Final commit**

```bash
git add PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: document bank spending tracking + transfer detection (db v25)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Self-Review

**Spec coverage:** Data model (Task 1), parser (Task 3), categorization rules + LLM assist (Tasks 5-6), transfer auto-linking (Task 4-5), import/save/list/update/rescan/rules/summary API (Tasks 5-6), web Spending page (Tasks 9-10), Net Worth comparison (Task 11), tests at every layer (Tasks 1-2, 3, 4, 7, 10), docs (Task 13) — all sections of the approved design are covered. The explicitly-deferred dedicated bank parsers are called out in Task 13's docs rather than silently dropped.

**Placeholder scan:** No TBD/TODO markers; every step has complete code, not descriptions of code.

**Type consistency:** `SpendingRow`/`BankParseResult` (Task 3) → consumed identically in Task 5's `upload_bank_statement`. `TransferMatch(spending_id, link_type, link_id)` (Task 4) → consumed identically in Task 5's `_run_transfer_matching`. DB helper names/signatures from Task 1 (`create_spending_transaction`, `find_duplicate_spending_transaction`, `list_spending_transactions`, `update_spending_transaction`, `list_unlinked_spending_transactions`, `create_spending_rule`/`list_spending_rules`/`delete_spending_rule`, `get_or_create_portfolio(..., account_type=)`) are used with matching signatures in Tasks 5-7. JS API client method names from Task 8 (`uploadBankStatement`, `saveSpendingTransactions`, `suggestSpendingCategories`, `getSpendingTransactions`, `updateSpendingCategory`, `rescanTransfers`, `getSpendingRules`/`createSpendingRule`/`deleteSpendingRule`, `getSpendingSummary`) match their call sites in Tasks 10-11. HTML element ids introduced in Task 9 (`spSpent`, `spIncome`, `spTransferred`, `spAccountFilter`, `spCategoryFilter`, `spFromDate`, `spToDate`, `spCategoryChart`, `spTxBody`, `spRulesBody`, `spRuleAddForm`/`spRulePattern`/`spRuleCategory`, `spImportAccountSelect`/`spImportAccountName`/`spImportFile`/`spDownloadTemplate`/`spImportPreview`/`spImportStatus`/`spParseBtn`/`spSuggestBtn`/`spSaveBtn`) match their references in Task 10. Net Worth widget ids (`cfActualRow`/`cfActualIncome`/`cfActualSpent`/`cfActualTransferred`) match between Task 11's HTML and JS steps.
