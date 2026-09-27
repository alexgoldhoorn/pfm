# Spending Usability Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make everyday work with bank transactions fast: find any row, fix a
category in one click and turn that fix into a rule, match rules on a clean
merchant name with account/sign/amount conditions, catch missing statement
rows, and see recurring charges.

**Architecture:** One schema bump (v32) adds a normalised `merchant` column to
`spending_transactions` and condition/priority columns to `spending_rules`.
Pure, DB-free services hold the logic (`merchant.py`, `spending_rules.py`,
`balance_check.py`, `recurring.py`); `portf_server/routers/spending.py` wires
them into the existing endpoints plus three new ones (`POST /rules/preview`,
`GET /recurring`, and new fields on `/upload`); the web client gets a search
box, click-to-edit categories, a richer Rules form and a Recurring tab. A new
Action Items check reports missed recurring charges.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLite, pytest; vanilla JS +
Bootstrap 5.3, Node built-in test runner.

**Spec:** No separate spec. The design is the six upgrades agreed in the
2026-09-27 conversation (split transactions explicitly excluded):
1. Search on description; 2. one-click category edit that offers a rule;
3. merchant normalisation; 4. smarter rules (account/sign/amount conditions,
priority, match preview); 6. recurring/subscription detection; 7. balance
continuity check on import. The design decisions are recorded per task below.

## Global Constraints

- Python formatted with black, line length 88: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black <file>`.
- Comments go on the line **before** the code they describe, never inline.
- Type hints on every function signature; Google-style docstrings.
- Run Python tooling as `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …` (the `.venv` is root-owned).
- Public repo: tests and docs use invented data only (`EXAMPLE SHOP`, `123456789012`, `Example Bank`). Never paste real descriptions from `portfolio.db`.
- New tables/columns must be in BOTH `_create_all_tables` (fresh DBs) AND the migration (`_migrate_to_v32`).
- Schema bump: `DATABASE_VERSION = 32`; update every `== 31` schema assertion in `tests/test_database.py`.
- LLM failures are errors, never empty results (unchanged; nothing here calls an LLM).
- Web: escape dynamic text with `esc()` before `innerHTML`; attributes with `escapeForAttr()`; money via `Fmt.money(v, currency, 2)`; numbers `Fmt.num(v, min, max)`; dates `Fmt.date()`; messages via `notify(msg, level)`; no native `alert()`/`confirm()`.
- Pure JS helpers are `window.`-exported and tested in `web_client/js/tests/spending_usability.test.mjs` (picked up by `make test-js`'s `*.test.mjs` glob).
- Check new UI at 390px width for horizontal overflow.
- Commits: conventional commits, ending with `Co-Authored-By: Oz <oz-agent@warp.dev>` (project CLAUDE.md rule).
- Unit tests: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`; JS: `make test-js`.

## Review Focus

1. **A description that normalises to nothing** (all digits, only a card reference) must still get a non-empty `merchant` (the collapsed description), or grouping/search on it silently drops the row. Test: Task 1 `test_all_digit_description_falls_back`.
2. **Search text containing `%`, `_` or `\`** must match literally, not as SQL wildcards. Test: Task 3 `test_search_treats_wildcards_literally`.
3. **A rule whose category's tree root conflicts with the row's sign** must be skipped so a later matching rule can apply (it used to stop at `uncategorized`). Test: Task 4 `test_root_mismatch_falls_through_to_next_rule`.
4. **A CSV exported newest-first with a balance column** must not raise false balance breaks. Test: Task 7 `test_descending_file_is_consistent`.
5. **A recurring charge whose account simply hasn't been imported past the due date** must read as `active`, never `missed` — a missing statement is not a cancelled subscription. Test: Task 8 `test_not_missed_when_statement_not_imported_yet`.

---

## File Structure

| File | Responsibility |
|---|---|
| `portf_manager/services/merchant.py` (new) | `normalize_merchant(description)` — pure string cleaning |
| `portf_manager/services/spending_rules.py` (new) | `rule_matches`, `rule_sort_key`, `pick_category` — pure rule engine |
| `portf_manager/services/balance_check.py` (new) | `find_balance_breaks` — pure running-balance validation |
| `portf_manager/services/recurring.py` (new) | `detect_recurring` (pure) + `load_recurring(db)` |
| `portf_manager/database.py` | v32 migration, `merchant` storage, rule conditions, `q` search, `get_latest_bank_balance_before` |
| `portf_server/routers/spending.py` | search param, rule conditions/preview, balance breaks on upload, `/recurring` |
| `portf_manager/services/action_items.py` | `check_recurring_charges` |
| `web_client/index.html` | search box, rules form fields, Recurring tab |
| `web_client/js/pfm_core.js` | API client: `createSpendingRule` options, `previewSpendingRule`, `getSpendingRecurring` |
| `web_client/js/pfm_features.js` | search wiring, merchant display, click-to-edit + rule offer, rules form, balance warning, Recurring tab |
| `web_client/js/help_text.js` | Spending page help bullets |
| Tests | `tests/unit/test_merchant.py`, `tests/unit/test_spending_rules_service.py`, `tests/unit/test_balance_check.py`, `tests/unit/test_recurring.py`, additions to `tests/unit/test_spending_db.py`, `tests/unit/test_spending_api.py`, `tests/unit/test_action_items*.py`, `web_client/js/tests/spending_usability.test.mjs` |

## Order and why

| Step | Task | Depends on |
|---|---|---|
| 1 | Merchant normaliser (pure) | — |
| 2 | Schema v32 + DB methods | 1 |
| 3 | Search (API + UI) | 2 |
| 4 | Rule engine v2 + preview API | 2 |
| 5 | Rules tab UI | 4 |
| 6 | Click-to-edit category + "make a rule" offer | 3, 4 |
| 7 | Balance continuity check | 2 |
| 8 | Recurring detection + API | 2 |
| 9 | Recurring tab UI | 3, 8 |
| 10 | Missed-recurring Action Item | 8 |
| 11 | Docs, deploy, real-data verification | all |

---

### Task 1: Merchant normaliser

**Files:**
- Create: `portf_manager/services/merchant.py`
- Test: `tests/unit/test_merchant.py`

**Interfaces:**
- Produces: `normalize_merchant(description: str) -> str` — never returns `""` for a non-blank description.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for bank-description → merchant-name normalisation."""

import pytest

from portf_manager.services.merchant import normalize_merchant


@pytest.mark.parametrize(
    "description, expected",
    [
        ("123456789012SUPERMERCAT EXEMPLE  \\TIANA\\ES26010112", "SUPERMERCAT EXEMPLE"),
        ("123456789012EXAMPLE SHOP MADRID 010012345", "EXAMPLE SHOP MADRID"),
        ("123456789012AMAZON* AB12CD34E LUXEMBOURG 010012345", "AMAZON LUXEMBOURG"),
        ("123456789012PAYPAL *EXAMPLE STORE BE 12345678901 00000", "EXAMPLE STORE BE"),
        ("123456789012SumUp  *Cafe Exemple Barcelona 010012345", "Cafe Exemple Barcelona"),
        ("LIQUIDACIO TARGETA CREDIT *1234 07/25", "LIQUIDACIO TARGETA CREDIT"),
        ("R/ Example Insurance S.A.", "Example Insurance S.A."),
        ("123456-001Example AG", "Example AG"),
        ("CARGO EXAMPLE LOAN 1234-567890-123 45-", "CARGO EXAMPLE LOAN"),
        ("BIZUM ENVIADO: Example Person", "BIZUM ENVIADO: Example Person"),
    ],
)
def test_normalize_merchant(description, expected):
    assert normalize_merchant(description) == expected


def test_all_digit_description_falls_back():
    assert normalize_merchant("123456789") == "123456789"


def test_blank_description_stays_blank():
    assert normalize_merchant("   ") == ""


def test_whitespace_is_collapsed():
    assert normalize_merchant("BIZUM ENVIADO:  Example   Person ") == (
        "BIZUM ENVIADO: Example Person"
    )


@pytest.mark.parametrize(
    "description",
    [
        "123456789012EXAMPLE SHOP MADRID 010012345",
        "123456789012AMAZON* AB12CD34E LUXEMBOURG 010012345",
        "R/ Example Insurance S.A.",
    ],
)
def test_idempotent(description):
    once = normalize_merchant(description)
    assert normalize_merchant(once) == once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_merchant.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'portf_manager.services.merchant'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Merchant-name normalisation for bank-statement descriptions.

Spanish bank exports wrap the merchant in noise: a 12-digit card/terminal
reference glued to the front, a ``\\CITY\\ES<digits>`` suffix, processor
prefixes (``PAYPAL *``, ``SumUp *``), order codes after ``*``, masked card
numbers and trailing reference numbers. That noise makes rule patterns fragile
and makes the same shop look like hundreds of different descriptions.

The result is stored next to the raw description (never instead of it) and is
used for search, rule matching and recurring-charge grouping.
"""

import re

# Everything from the first backslash: "\CITY\ES26010112" location suffix.
_LOCATION_SUFFIX = re.compile(r"\\.*$")
# Card/terminal reference glued to the merchant, optionally "-001".
_LEADING_REF = re.compile(r"^\d{6,}(?:-\d+)?")
# Direct-debit receipt ("R/ ") and card-purchase prefixes.
_RECEIPT_PREFIX = re.compile(
    r"^(?:R/|RECIBO\b|COMPRA\s+TARJ(?:ETA)?\.?|COMPRA\b)\s*", re.IGNORECASE
)
# Payment processors that put the real merchant after a star.
_PROCESSOR_PREFIX = re.compile(r"^(?:PAYPAL|SUMUP|ZETTLE|SQ)\s*\*\s*", re.IGNORECASE)
# Masked card number: "*1234".
_CARD_MASK = re.compile(r"\*\d{4}\b")
# Order code after a star: "AMAZON* AB12CD34E".
_STAR_CODE = re.compile(r"\*\s*[A-Za-z0-9]*\d[A-Za-z0-9]*")
# Short dates: "07/25", "07/25/2026".
_SHORT_DATE = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")
# Trailing reference numbers: " 010012345", " 1234-567890-123 45-".
_TRAILING_NUMBERS = re.compile(r"(?:\s+\d[\d\-]*)+\s*$")
_WHITESPACE = re.compile(r"\s+")


def normalize_merchant(description: str) -> str:
    """Reduce a bank-statement description to a stable merchant name.

    Args:
        description: The raw description exactly as the bank exported it.

    Returns:
        The cleaned merchant name. Falls back to the whitespace-collapsed
        description when cleaning leaves nothing, so a non-blank description
        never yields an empty merchant.
    """
    raw = _WHITESPACE.sub(" ", description or "").strip()
    text = _LOCATION_SUFFIX.sub("", description or "").strip()
    text = _LEADING_REF.sub("", text).strip()
    text = _RECEIPT_PREFIX.sub("", text).strip()
    text = _PROCESSOR_PREFIX.sub("", text).strip()
    text = _CARD_MASK.sub(" ", text)
    text = _STAR_CODE.sub(" ", text)
    text = _SHORT_DATE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    text = _TRAILING_NUMBERS.sub("", text).strip(" -,")
    return text or raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_merchant.py -q`
Expected: PASS (all parametrised cases). If a case fails, fix the regex, not the expectation — the expectations are the contract.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/merchant.py tests/unit/test_merchant.py
git commit -m "feat: normalise bank descriptions to merchant names

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 2: Schema v32 — merchant column, rule conditions, priority

**Files:**
- Modify: `portf_manager/database.py` (`DATABASE_VERSION` line 17; `_create_all_tables` spending tables ~line 604 and ~line 628; migration dispatch ~line 823; new `_migrate_to_v32` after `_migrate_to_v31`; `create_spending_transaction` ~line 3273; `get_latest_bank_balance` ~line 3512; rule methods ~lines 3632 and 3857–3903)
- Modify: `tests/test_database.py` (every `== 31` schema-version assertion)
- Test: `tests/unit/test_spending_db.py`

**Interfaces:**
- Consumes: `normalize_merchant(description: str) -> str` (Task 1).
- Produces:
  - column `spending_transactions.merchant TEXT` (always filled on insert)
  - columns `spending_rules.portfolio_id INTEGER`, `amount_sign TEXT`, `min_amount REAL`, `max_amount REAL`, `priority INTEGER NOT NULL DEFAULT 100`
  - `db.create_spending_transaction(..., merchant: str | None = None) -> int`
  - `db.create_spending_rule(pattern, category, portfolio_id=None, amount_sign=None, min_amount=None, max_amount=None, priority=100) -> int`
  - `db.find_duplicate_spending_rule(pattern, category, portfolio_id=None, amount_sign=None, min_amount=None, max_amount=None) -> dict | None`
  - `db.list_spending_rules()` ordered by `priority, id`
  - `db.update_spending_rule(rule_id, **kwargs)` accepts `pattern, category, portfolio_id, amount_sign, min_amount, max_amount, priority` (a `None` value clears a condition)
  - `db.get_latest_bank_balance_before(portfolio_id: int, before_date: str) -> dict | None` (`date`, `balance`, `currency`)

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_spending_db.py`)

```python
def _bank(db):
    return db.create_portfolio("Example Bank", account_type="bank")


def test_merchant_is_stored_on_create(db):
    pid = _bank(db)
    sid = db.create_spending_transaction(
        portfolio_id=pid,
        date="2026-01-05",
        description="123456789012EXAMPLE SHOP MADRID 010012345",
        amount=-10.0,
    )
    assert db.get_spending_transaction(sid)["merchant"] == "EXAMPLE SHOP MADRID"


def test_explicit_merchant_overrides_normalisation(db):
    pid = _bank(db)
    sid = db.create_spending_transaction(
        portfolio_id=pid,
        date="2026-01-05",
        description="RAW TEXT",
        amount=-10.0,
        merchant="Custom Name",
    )
    assert db.get_spending_transaction(sid)["merchant"] == "Custom Name"


def test_migration_v32_backfills_merchant_and_is_rerunnable(db):
    pid = _bank(db)
    sid = db.create_spending_transaction(
        portfolio_id=pid,
        date="2026-01-05",
        description="123456789012EXAMPLE SHOP MADRID 010012345",
        amount=-10.0,
    )
    with db.get_connection() as conn:
        conn.execute("UPDATE spending_transactions SET merchant = NULL")
        conn.commit()
        db._migrate_to_v32(conn)
        db._migrate_to_v32(conn)
    assert db.get_spending_transaction(sid)["merchant"] == "EXAMPLE SHOP MADRID"


def test_rule_conditions_round_trip(db):
    pid = _bank(db)
    rid = db.create_spending_rule(
        "BIZUM",
        "Gifts",
        portfolio_id=pid,
        amount_sign="negative",
        min_amount=5.0,
        max_amount=50.0,
        priority=10,
    )
    rule = db.get_spending_rule(rid)
    assert rule["portfolio_id"] == pid
    assert rule["amount_sign"] == "negative"
    assert rule["min_amount"] == 5.0
    assert rule["max_amount"] == 50.0
    assert rule["priority"] == 10


def test_rule_priority_defaults_to_100(db):
    rid = db.create_spending_rule("SHOP", "Groceries")
    assert db.get_spending_rule(rid)["priority"] == 100


def test_rules_listed_by_priority_then_id(db):
    first = db.create_spending_rule("A", "Groceries")
    second = db.create_spending_rule("B", "Groceries", priority=5)
    third = db.create_spending_rule("C", "Groceries")
    assert [r["id"] for r in db.list_spending_rules()] == [second, first, third]


def test_update_rule_none_clears_condition(db):
    pid = _bank(db)
    rid = db.create_spending_rule("SHOP", "Groceries", portfolio_id=pid)
    db.update_spending_rule(rid, portfolio_id=None)
    assert db.get_spending_rule(rid)["portfolio_id"] is None


def test_duplicate_rule_considers_conditions(db):
    pid = _bank(db)
    db.create_spending_rule("BIZUM", "Gifts", amount_sign="negative")
    assert db.find_duplicate_spending_rule("bizum", "Gifts", amount_sign="negative")
    assert db.find_duplicate_spending_rule("BIZUM", "Gifts") is None
    assert (
        db.find_duplicate_spending_rule(
            "BIZUM", "Gifts", portfolio_id=pid, amount_sign="negative"
        )
        is None
    )


def test_latest_bank_balance_before(db):
    pid = _bank(db)
    db.create_spending_transaction(
        portfolio_id=pid, date="2026-01-01", description="A", amount=-1, balance=99.0
    )
    db.create_spending_transaction(
        portfolio_id=pid, date="2026-01-03", description="B", amount=-1, balance=98.0
    )
    db.create_spending_transaction(
        portfolio_id=pid, date="2026-01-05", description="C", amount=-1, balance=97.0
    )
    assert db.get_latest_bank_balance_before(pid, "2026-01-05")["balance"] == 98.0
    assert db.get_latest_bank_balance_before(pid, "2026-01-01") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py -q`
Expected: FAIL (`KeyError: 'merchant'` / unexpected keyword `merchant` / no attribute `_migrate_to_v32`).

- [ ] **Step 3: Implement**

3a. Top of `portf_manager/database.py`, next to the other imports:

```python
from portf_manager.services.merchant import normalize_merchant
```

(`portf_manager/services/__init__.py` only holds a docstring, so there is no import cycle.)

3b. `DATABASE_VERSION = 31` → `DATABASE_VERSION = 32`.

3c. In `_create_all_tables`, the `spending_transactions` definition: add `merchant TEXT,` after `balance REAL,`:

```sql
                balance            REAL,
                merchant           TEXT,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
```

and replace the `spending_rules` definition (and its comment) with:

```python
        # Description → category rules for spending_transactions. The pattern is
        # a case-insensitive substring of the description or the merchant;
        # optional conditions narrow it to one account, one sign and an
        # absolute-amount range. Lowest priority first, then oldest id. See
        # _migrate_to_v25 and _migrate_to_v32.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spending_rules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern      TEXT NOT NULL,
                category     TEXT NOT NULL,
                portfolio_id INTEGER,
                amount_sign  TEXT,
                min_amount   REAL,
                max_amount   REAL,
                priority     INTEGER NOT NULL DEFAULT 100,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
```

Do **not** edit `_migrate_to_v25`: an old DB runs v25 and then v32, and v32's `ALTER` must be the one that adds the columns.

3d. Migration dispatch, after the v31 line:

```python
        if current_version < 32:
            self._migrate_to_v32(conn)
```

3e. New method directly after `_migrate_to_v31`:

```python
    def _migrate_to_v32(self, conn: sqlite3.Connection) -> None:
        """Migrate from v31 to v32 — merchant names and rule conditions.

        Adds spending_transactions.merchant (backfilled from the description
        with normalize_merchant) and the optional rule conditions
        portfolio_id/amount_sign/min_amount/max_amount plus priority on
        spending_rules. Column adds are guarded so a rerun is harmless.
        """
        tx_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(spending_transactions)")
        }
        if "merchant" not in tx_cols:
            conn.execute("ALTER TABLE spending_transactions ADD COLUMN merchant TEXT")
        missing = conn.execute(
            "SELECT id, description FROM spending_transactions WHERE merchant IS NULL"
        ).fetchall()
        conn.executemany(
            "UPDATE spending_transactions SET merchant = ? WHERE id = ?",
            [(normalize_merchant(row[1]), row[0]) for row in missing],
        )
        rule_cols = {row[1] for row in conn.execute("PRAGMA table_info(spending_rules)")}
        for name, ddl in (
            ("portfolio_id", "INTEGER"),
            ("amount_sign", "TEXT"),
            ("min_amount", "REAL"),
            ("max_amount", "REAL"),
            ("priority", "INTEGER NOT NULL DEFAULT 100"),
        ):
            if name not in rule_cols:
                conn.execute(f"ALTER TABLE spending_rules ADD COLUMN {name} {ddl}")
        conn.commit()
```

3f. `create_spending_transaction`: add parameter `merchant: str = None` after `balance`, document it in the docstring (`merchant: Cleaned merchant name; derived from description with normalize_merchant when omitted.`), and change the INSERT to:

```python
            cursor = conn.execute(
                """
                INSERT INTO spending_transactions
                    (portfolio_id, date, description, amount, currency,
                     category, source, balance, merchant)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    merchant if merchant is not None else normalize_merchant(description),
                ),
            )
```

3g. After `get_latest_bank_balance`, add:

```python
    def get_latest_bank_balance_before(
        self, portfolio_id: int, before_date: str
    ) -> Optional[Dict]:
        """Return the last balance-bearing row strictly before ``before_date``.

        The baseline for checking that a new statement continues where the
        previous import left off. Same tie-break as get_latest_bank_balance.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT date, balance, currency FROM spending_transactions
                WHERE portfolio_id = ? AND balance IS NOT NULL AND date < ?
                ORDER BY date DESC, id DESC
                LIMIT 1
                """,
                (portfolio_id, before_date),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
```

3h. Replace `create_spending_rule`, `find_duplicate_spending_rule`, `list_spending_rules` and `update_spending_rule` with:

```python
    _RULE_CONDITION_FIELDS = ("portfolio_id", "amount_sign", "min_amount", "max_amount")

    def create_spending_rule(
        self,
        pattern: str,
        category: str,
        portfolio_id: Optional[int] = None,
        amount_sign: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        priority: int = 100,
    ) -> int:
        """Create a spending category rule.

        Args:
            pattern: Case-insensitive substring of the description or merchant.
            category: Category assigned on a match.
            portfolio_id: Only match rows of this bank account.
            amount_sign: ``"negative"`` (money out) or ``"positive"`` (money in).
            min_amount: Minimum absolute amount, inclusive.
            max_amount: Maximum absolute amount, inclusive.
            priority: Lower runs first; ties fall back to the oldest rule.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO spending_rules
                    (pattern, category, portfolio_id, amount_sign,
                     min_amount, max_amount, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern,
                    category,
                    portfolio_id,
                    amount_sign,
                    min_amount,
                    max_amount,
                    priority,
                ),
            )
            conn.commit()
            return cursor.lastrowid
```

```python
    def find_duplicate_spending_rule(
        self,
        pattern: str,
        category: str,
        portfolio_id: Optional[int] = None,
        amount_sign: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
    ) -> Optional[Dict]:
        """Find a rule with the same pattern (case-insensitive), category and
        conditions. ``IS`` compares NULLs as equal, so an unconditioned rule
        only duplicates another unconditioned one."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM spending_rules
                WHERE LOWER(pattern) = LOWER(?) AND category = ?
                  AND portfolio_id IS ? AND amount_sign IS ?
                  AND min_amount IS ? AND max_amount IS ?
                """,
                (pattern, category, portfolio_id, amount_sign, min_amount, max_amount),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_spending_rules(self) -> List[Dict]:
        """List all spending rules in evaluation order: priority, then oldest."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM spending_rules ORDER BY priority, id")
            return [dict(row) for row in cursor.fetchall()]
```

```python
    def update_spending_rule(self, rule_id: int, **kwargs) -> bool:
        """Update a spending rule. A ``None`` condition value clears it."""
        valid_fields = {"pattern", "category", "priority", *self._RULE_CONDITION_FIELDS}
        update_fields = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not update_fields:
            return False
        with self.get_connection() as conn:
            set_clause = ", ".join(f"{field} = ?" for field in update_fields)
            values = list(update_fields.values()) + [rule_id]
            cursor = conn.execute(
                f"UPDATE spending_rules SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            return cursor.rowcount > 0
```

3i. In `tests/test_database.py`, bump the schema assertions (leave line ~627's `31000.0` alone):

```bash
sed -i -E 's/(assert (result\[0\]|version) == )31\b/\132/' tests/test_database.py
grep -n "== 31\b" tests/test_database.py
```

Expected after the sed: only the `total_amount` line remains. Then `grep -rn "DATABASE_VERSION\|== 31\b" tests/ | grep -v 31000` to catch any other schema pin.

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_db.py tests/test_database.py tests/unit/test_spending_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/database.py tests/unit/test_spending_db.py tests/test_database.py
git commit -m "feat: schema v32 — merchant names and rule conditions on spending

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: Search on description and merchant

**Files:**
- Modify: `portf_manager/database.py` (`_spending_where_clause`, `list_spending_transactions`, `count_spending_transactions`)
- Modify: `portf_server/routers/spending.py` (`SpendingTransactionResponse`, `list_spending`)
- Modify: `web_client/index.html` (Spending filter row)
- Modify: `web_client/js/pfm_features.js` (`loadSpendingPage`, `_fetchAndRenderSpendingTable`)
- Test: `tests/unit/test_spending_api.py`, `web_client/js/tests/spending_usability.test.mjs` (new)

**Interfaces:**
- Consumes: `spending_transactions.merchant` (Task 2).
- Produces:
  - `GET /api/v1/spending/?q=<text>` — case-insensitive literal substring match on description OR merchant.
  - `SpendingTransactionResponse.merchant: Optional[str]` (every list/preview response now carries it).
  - `db.list_spending_transactions(..., q: str = None)`, `db.count_spending_transactions(..., q: str = None)`.
  - JS `spDescriptionCellHtml(row) -> string` (window-exported), DOM id `#spSearch`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_spending_api.py`:

```python
def _add(db, pid, description, amount=-10.0, day="2026-01-05"):
    return db.create_spending_transaction(
        portfolio_id=pid, date=day, description=description, amount=amount
    )


def test_search_matches_description_case_insensitively(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _add(db, pid, "EXAMPLE SHOP")
    _add(db, pid, "OTHER STORE")
    r = client.get("/api/v1/spending/?q=example sh", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 1
    assert d["items"][0]["description"] == "EXAMPLE SHOP"
    assert d["items"][0]["merchant"] == "EXAMPLE SHOP"


def test_search_matches_merchant_only(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _add(db, pid, "123456789012EXAMPLE   SHOP \\TIANA\\ES26010112")
    r = client.get("/api/v1/spending/?q=example shop", headers=HEADERS)
    assert r.json()["total"] == 1


def test_search_treats_wildcards_literally(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _add(db, pid, "50% OFF STORE")
    _add(db, pid, "PLAIN STORE")
    _add(db, pid, "A_B SHOP")
    _add(db, pid, "AXB SHOP")
    _add(db, pid, "BACK\\SLASH")
    assert client.get("/api/v1/spending/?q=%25", headers=HEADERS).json()["total"] == 1
    assert client.get("/api/v1/spending/?q=_", headers=HEADERS).json()["total"] == 1
    assert (
        client.get("/api/v1/spending/?q=K%5CS", headers=HEADERS).json()["total"] == 1
    )


def test_blank_search_is_unfiltered(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _add(db, pid, "EXAMPLE SHOP")
    _add(db, pid, "OTHER STORE")
    assert client.get("/api/v1/spending/?q=%20%20", headers=HEADERS).json()["total"] == 2


def test_search_composes_with_other_filters(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _add(db, pid, "EXAMPLE SHOP", amount=-10.0)
    _add(db, pid, "EXAMPLE SHOP REFUND", amount=10.0)
    r = client.get(
        "/api/v1/spending/?q=example&amount_sign=positive", headers=HEADERS
    ).json()
    assert r["total"] == 1
    assert r["items"][0]["amount"] == 10.0
```

Create `web_client/js/tests/spending_usability.test.mjs`:

```js
// Pure helpers behind the Spending usability upgrades (search, rules,
// click-to-edit, balance check, recurring). Run: make test-js
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadAppIntoContext } from "./helpers.mjs";

test("spDescriptionCellHtml shows merchant first and raw description muted", () => {
    const { spDescriptionCellHtml } = loadAppIntoContext();
    const html = spDescriptionCellHtml({
        description: "123456789012EXAMPLE SHOP 0100",
        merchant: "EXAMPLE SHOP",
    });
    assert.match(html, /^EXAMPLE SHOP<div class="small text-muted">123456789012EXAMPLE SHOP 0100<\/div>$/);
});

test("spDescriptionCellHtml shows only the description when merchant equals it", () => {
    const { spDescriptionCellHtml } = loadAppIntoContext();
    assert.equal(spDescriptionCellHtml({ description: "SHOP", merchant: "SHOP" }), "SHOP");
    assert.equal(spDescriptionCellHtml({ description: "SHOP", merchant: null }), "SHOP");
});

test("spDescriptionCellHtml escapes both lines", () => {
    const { spDescriptionCellHtml } = loadAppIntoContext();
    const html = spDescriptionCellHtml({ description: "<b>x</b> 1", merchant: "<b>x</b>" });
    assert.ok(!html.includes("<b>"));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -q -k search` → FAIL (`q` ignored, totals wrong; `merchant` key missing).
Run: `make test-js` → FAIL (`spDescriptionCellHtml is not a function`).

- [ ] **Step 3: Implement**

3a. `database.py` `_spending_where_clause`: add `q: str = None` as the last parameter, add a docstring sentence (``q`` is a case-insensitive literal substring of description or merchant; `%`, `_` and `\` are escaped), and before building `clause`:

```python
        if q:
            # Escape LIKE metacharacters so the text matches literally.
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            conditions.append(
                "(s.description LIKE ? ESCAPE '\\' "
                "OR COALESCE(s.merchant, '') LIKE ? ESCAPE '\\')"
            )
            params.extend([like, like])
```

In `list_spending_transactions` and `count_spending_transactions` add `q: str = None` after `min_abs_amount` in the signature and pass `q` as the last positional argument of their `self._spending_where_clause(...)` calls.

3b. `spending.py`:
- `SpendingTransactionResponse`: add `merchant: Optional[str] = None` after `balance`.
- `list_spending`: add parameter `q: Optional[str] = Query(default=None, max_length=200),` after `min_abs_amount`; add to its docstring "`q` matches description or merchant, case-insensitively and literally."; add `q=(q or "").strip() or None,` to the `filters` dict.

3c. `index.html`: in the Spending filter row, insert as the first column (before the `Account` column):

```html
                                <div class="col-12 col-md-3">
                                    <label class="form-label small mb-1" for="spSearch">Search</label>
                                    <input type="search" class="form-control form-control-sm" id="spSearch" placeholder="Merchant or description" autocomplete="off">
                                </div>
```

3d. `pfm_features.js`:

Add near `dedupSpendingRowsByDescription` (module scope):

```js
// Pure: the Description cell — the cleaned merchant name first, the raw bank
// text underneath when it differs, so both stay searchable by eye.
function spDescriptionCellHtml(row) {
    const desc = row.description || '';
    const merchant = (row.merchant || '').trim();
    if (!merchant || merchant === desc) return esc(desc);
    return `${esc(merchant)}<div class="small text-muted">${esc(desc)}</div>`;
}
window.spDescriptionCellHtml = spDescriptionCellHtml;
```

In `_fetchAndRenderSpendingTable`, after the `minAbsAmount` lines:

```js
    const q = (document.getElementById('spSearch')?.value || '').trim();
    if (q) params.q = q;
```

and replace the row's `<td>${esc(r.description)}</td>` with `<td>${spDescriptionCellHtml(r)}</td>`.

In `loadSpendingPage`, next to the `spMinAbsAmount` wiring:

```js
    const searchInput = document.getElementById('spSearch');
    if (searchInput && !searchInput.dataset.wired) {
        searchInput.dataset.wired = '1';
        let searchTimer = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimer);
            // Wait for a pause in typing so each keystroke isn't a request.
            searchTimer = setTimeout(() => {
                window._spTxState.page = 0;
                _fetchAndRenderSpendingTable();
            }, 300);
        });
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -q` → PASS.
Run: `make test-js` → PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/database.py portf_server/routers/spending.py web_client/index.html web_client/js/pfm_features.js tests/unit/test_spending_api.py web_client/js/tests/spending_usability.test.mjs
git commit -m "feat: search spending by description or merchant

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Rule engine v2 — conditions, priority, match preview

**Files:**
- Create: `portf_manager/services/spending_rules.py`
- Modify: `portf_server/routers/spending.py` (imports, `PreviewSpendingRow`, rule models, `_apply_rules`, `upload_bank_statement`, `rescan_categories`, `create_rule`, `update_rule`, new `preview_rule`)
- Test: `tests/unit/test_spending_rules_service.py` (new), `tests/unit/test_spending_api.py`

**Interfaces:**
- Consumes: DB rule methods (Task 2), `normalize_merchant` (Task 1), `SpendingTransactionResponse.merchant` (Task 3).
- Produces:
  - `rule_sort_key(rule: dict) -> tuple[int, int]`
  - `rule_matches(rule: dict, description: str, merchant: str | None, amount: float, portfolio_id: int | None) -> bool`
  - `pick_category(rules: list[dict], description: str, merchant: str | None, amount: float, portfolio_id: int | None, category_fits: Callable[[str, float], bool]) -> str`
  - `POST /api/v1/spending/rules` and `PUT /rules/{id}` accept/return `portfolio_id`, `amount_sign` (`"positive"|"negative"|null`), `min_amount`, `max_amount`, `priority`.
  - `POST /api/v1/spending/rules/preview` body `{pattern, category?, portfolio_id?, amount_sign?, min_amount?, max_amount?, only_uncategorized=true, exclude_ids=[]}` → `{match_count: int, sample: [SpendingTransactionResponse, ≤10]}`.
  - `PreviewSpendingRow.merchant: Optional[str]`.

**Design decisions:**
- The pattern matches the raw description OR the merchant.
- A matching rule whose category's tree root conflicts with the row's sign is **skipped**, and evaluation continues to the next rule. Before, it stopped at `uncategorized`. This only differs when a later rule also matches, and then the later rule is the correct answer.
- Preview counts the rows *this rule* matches. It ignores whether a higher-priority rule would win first, and says so in the docstring.
- A rule pointing at a deleted account never matches. There is no foreign key, because `ON DELETE SET NULL` would silently turn it into a global rule.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_spending_rules_service.py`:

```python
"""Tests for the pure spending-rule engine."""

from portf_manager.services.spending_rules import (
    pick_category,
    rule_matches,
    rule_sort_key,
)


def _rule(pattern="SHOP", category="Groceries", **kw):
    return {"id": kw.pop("id", 1), "pattern": pattern, "category": category, **kw}


def _always(_category, _amount):
    return True


def test_matches_description_substring_case_insensitive():
    assert rule_matches(_rule("shop"), "EXAMPLE SHOP", None, -5, 1)


def test_matches_merchant_when_description_has_noise():
    rule = _rule("example shop")
    assert rule_matches(rule, "EXAMPLE   SHOP \\X", "EXAMPLE SHOP", -5, 1)


def test_blank_pattern_never_matches():
    assert not rule_matches(_rule("  "), "ANYTHING", "ANYTHING", -5, 1)


def test_account_condition():
    rule = _rule(portfolio_id=7)
    assert rule_matches(rule, "SHOP", None, -5, 7)
    assert not rule_matches(rule, "SHOP", None, -5, 8)


def test_sign_condition():
    out_rule = _rule(amount_sign="negative")
    in_rule = _rule(amount_sign="positive")
    assert rule_matches(out_rule, "SHOP", None, -5, 1)
    assert not rule_matches(out_rule, "SHOP", None, 5, 1)
    assert rule_matches(in_rule, "SHOP", None, 5, 1)
    assert not rule_matches(in_rule, "SHOP", None, -5, 1)


def test_amount_range_is_inclusive_on_absolute_value():
    rule = _rule(min_amount=10.0, max_amount=20.0)
    assert rule_matches(rule, "SHOP", None, -10.0, 1)
    assert rule_matches(rule, "SHOP", None, -20.0, 1)
    assert not rule_matches(rule, "SHOP", None, -9.99, 1)
    assert not rule_matches(rule, "SHOP", None, 20.01, 1)


def test_sort_key_priority_then_id():
    rules = [_rule(id=1), _rule(id=2, priority=5), _rule(id=3, priority=None)]
    assert [r["id"] for r in sorted(rules, key=rule_sort_key)] == [2, 1, 3]


def test_pick_category_respects_priority():
    rules = [
        _rule("BIZUM", "Gifts", id=1),
        _rule("BIZUM", "Dinners", id=2, priority=1),
    ]
    assert pick_category(rules, "BIZUM X", None, -5, 1, _always) == "Dinners"


def test_root_mismatch_falls_through_to_next_rule():
    rules = [
        _rule("BIZUM", "Salary", id=1),
        _rule("BIZUM", "Gifts", id=2),
    ]

    def fits(category, amount):
        return not (category == "Salary" and amount < 0)

    assert pick_category(rules, "BIZUM X", None, -5, 1, fits) == "Gifts"


def test_no_match_is_uncategorized():
    assert pick_category([_rule("ZZZ")], "SHOP", None, -5, 1, _always) == (
        "uncategorized"
    )
```

Append to `tests/unit/test_spending_api.py`:

```python
def test_create_rule_with_conditions(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    r = client.post(
        "/api/v1/spending/rules",
        json={
            "pattern": "BIZUM",
            "category": "Gifts",
            "portfolio_id": pid,
            "amount_sign": "negative",
            "min_amount": 1,
            "max_amount": 100,
            "priority": 20,
        },
        headers=HEADERS,
    )
    assert r.status_code == 201
    d = r.json()
    assert (d["portfolio_id"], d["amount_sign"], d["priority"]) == (
        pid,
        "negative",
        20,
    )
    assert (d["min_amount"], d["max_amount"]) == (1.0, 100.0)


def test_create_rule_rejects_unknown_account(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "X", "category": "Gifts", "portfolio_id": 9999},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_create_rule_rejects_min_above_max(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/rules",
        json={"pattern": "X", "category": "Gifts", "min_amount": 50, "max_amount": 5},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_same_pattern_different_conditions_is_not_duplicate(tmp_path):
    client, _ = _make_client(tmp_path)
    base = {"pattern": "BIZUM", "category": "Gifts"}
    assert client.post("/api/v1/spending/rules", json=base, headers=HEADERS).status_code == 201
    signed = {**base, "amount_sign": "negative"}
    assert client.post("/api/v1/spending/rules", json=signed, headers=HEADERS).status_code == 201
    assert client.post("/api/v1/spending/rules", json=signed, headers=HEADERS).status_code == 409


def test_update_rule_null_clears_and_omitted_keeps(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    rid = db.create_spending_rule(
        "SHOP", "Groceries", portfolio_id=pid, amount_sign="negative"
    )
    r = client.put(
        f"/api/v1/spending/rules/{rid}", json={"portfolio_id": None}, headers=HEADERS
    )
    assert r.status_code == 200
    d = r.json()
    assert d["portfolio_id"] is None
    assert d["amount_sign"] == "negative"


def test_update_rule_validates_merged_range(tmp_path):
    client, db = _make_client(tmp_path)
    rid = db.create_spending_rule("SHOP", "Groceries", max_amount=10.0)
    r = client.put(
        f"/api/v1/spending/rules/{rid}", json={"min_amount": 20}, headers=HEADERS
    )
    assert r.status_code == 400


def test_rescan_uses_sign_conditions(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_rule("BIZUM", "Gifts", amount_sign="negative")
    db.create_spending_rule("BIZUM", "Other income", amount_sign="positive")
    db.create_spending_category("Gifts", parent_name="Spend")
    db.create_spending_category("Other income", parent_name="Income")
    out_id = _add(db, pid, "BIZUM ENVIADO: Example Person", amount=-20.0)
    in_id = _add(db, pid, "BIZUM RECIBIDO: Example Person", amount=20.0)
    r = client.post("/api/v1/spending/rescan-categories", headers=HEADERS)
    assert r.json()["recategorized"] == 2
    assert db.get_spending_transaction(out_id)["category"] == "Gifts"
    assert db.get_spending_transaction(in_id)["category"] == "Other income"


def test_rule_preview_counts_uncategorized_matches(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    keep = _add(db, pid, "123456789012EXAMPLE SHOP MADRID 010012345")
    other = _add(db, pid, "123456789012EXAMPLE SHOP MADRID 010099999")
    done = _add(db, pid, "123456789012EXAMPLE SHOP MADRID 010088888")
    db.update_spending_transaction(done, category="Groceries")
    _add(db, pid, "UNRELATED")
    r = client.post(
        "/api/v1/spending/rules/preview",
        json={"pattern": "EXAMPLE SHOP MADRID", "exclude_ids": [keep]},
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["match_count"] == 1
    assert [row["id"] for row in d["sample"]] == [other]


def test_rule_preview_respects_category_sign(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_category("Groceries", parent_name="Spend")
    _add(db, pid, "EXAMPLE SHOP", amount=-5.0)
    _add(db, pid, "EXAMPLE SHOP REFUND", amount=5.0)
    r = client.post(
        "/api/v1/spending/rules/preview",
        json={"pattern": "EXAMPLE SHOP", "category": "Groceries"},
        headers=HEADERS,
    )
    assert r.json()["match_count"] == 1


def test_rule_preview_rejects_blank_pattern(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.post(
        "/api/v1/spending/rules/preview", json={"pattern": "  "}, headers=HEADERS
    )
    assert r.status_code == 400
```

Before running: confirm `db.create_spending_category`'s signature (`grep -n "def create_spending_category" -A4 portf_manager/database.py`) and match the keyword it uses for the parent. If it isn't `parent_name`, change these two tests to use the real keyword.

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_rules_service.py tests/unit/test_spending_api.py -q`
Expected: FAIL (module missing; 422/404 on the new fields and endpoint).

- [ ] **Step 3: Implement**

3a. `portf_manager/services/spending_rules.py`:

```python
"""Spending category rules — pure matching logic, no DB access.

A rule is a dict as stored in ``spending_rules``: ``pattern`` and ``category``
plus optional ``portfolio_id``, ``amount_sign``, ``min_amount``,
``max_amount`` and ``priority``. The router supplies ``category_fits`` so the
category tree (a DB concern) stays out of this module.
"""

from typing import Callable, Iterable, Optional

DEFAULT_PRIORITY = 100
# Float noise from bank CSVs must not push an exact bound out of range.
_EPSILON = 1e-9


def rule_sort_key(rule: dict) -> tuple[int, int]:
    """Evaluation order: lowest priority first, then oldest rule."""
    priority = rule.get("priority")
    return (
        DEFAULT_PRIORITY if priority is None else int(priority),
        int(rule.get("id") or 0),
    )


def rule_matches(
    rule: dict,
    description: str,
    merchant: Optional[str],
    amount: float,
    portfolio_id: Optional[int],
) -> bool:
    """True when the rule's pattern and every set condition hold for the row.

    A blank pattern never matches: "" is a substring of every string, so it
    would otherwise recategorize an entire backlog.
    """
    pattern = (rule.get("pattern") or "").strip().lower()
    if not pattern:
        return False
    haystacks = ((description or "").lower(), (merchant or "").lower())
    if not any(pattern in text for text in haystacks):
        return False
    if rule.get("portfolio_id") is not None and rule["portfolio_id"] != portfolio_id:
        return False
    sign = rule.get("amount_sign")
    if sign == "negative" and amount >= 0:
        return False
    if sign == "positive" and amount <= 0:
        return False
    magnitude = abs(amount)
    if rule.get("min_amount") is not None and magnitude < rule["min_amount"] - _EPSILON:
        return False
    if rule.get("max_amount") is not None and magnitude > rule["max_amount"] + _EPSILON:
        return False
    return True


def pick_category(
    rules: Iterable[dict],
    description: str,
    merchant: Optional[str],
    amount: float,
    portfolio_id: Optional[int],
    category_fits: Callable[[str, float], bool],
) -> str:
    """Category of the first matching rule whose category fits the amount.

    A match whose category can't hold this amount (an Income category on a
    debit) is skipped rather than ending the search, so a later rule can
    still apply. Nothing applicable → ``"uncategorized"``.
    """
    for rule in sorted(rules, key=rule_sort_key):
        if not rule_matches(rule, description, merchant, amount, portfolio_id):
            continue
        if category_fits(rule["category"], amount):
            return rule["category"]
    return "uncategorized"
```

3b. `spending.py` imports: change `from pydantic import BaseModel` to `from pydantic import BaseModel, Field`, and add:

```python
from portf_manager.services.merchant import normalize_merchant
from portf_manager.services.spending_rules import pick_category, rule_matches
```

3c. `PreviewSpendingRow`: add `merchant: Optional[str] = None` after `balance`.

3d. Replace the rule models:

```python
class SpendingRuleBody(BaseModel):
    pattern: str
    category: str
    portfolio_id: Optional[int] = None
    amount_sign: Optional[Literal["positive", "negative"]] = None
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    priority: int = Field(default=100, ge=0, le=10000)


class SpendingRuleResponse(BaseModel):
    id: int
    pattern: str
    category: str
    portfolio_id: Optional[int] = None
    amount_sign: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    priority: int = 100


class SpendingRuleUpdateBody(BaseModel):
    pattern: Optional[str] = None
    category: Optional[str] = None
    portfolio_id: Optional[int] = None
    amount_sign: Optional[Literal["positive", "negative"]] = None
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    priority: Optional[int] = Field(default=None, ge=0, le=10000)


class RulePreviewBody(BaseModel):
    pattern: str
    category: Optional[str] = None
    portfolio_id: Optional[int] = None
    amount_sign: Optional[Literal["positive", "negative"]] = None
    min_amount: Optional[float] = Field(default=None, ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    only_uncategorized: bool = True
    exclude_ids: List[int] = []
```

`RulePreviewResponse` must be defined **after** `SpendingTransactionResponse`:

```python
class RulePreviewResponse(BaseModel):
    match_count: int
    sample: List[SpendingTransactionResponse]
```

3e. Replace `_apply_rules`:

```python
def _apply_rules(
    description: str,
    rules: List[dict],
    amount: float,
    db,
    merchant: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> str:
    """Category for a row from the rules, or "uncategorized".

    Rule order, conditions and the blank-pattern guard live in
    services.spending_rules. A rule whose category's tree root doesn't match
    the amount's sign is skipped — this runs unattended over many rows, so it
    must never apply a wrong-signed category or raise.
    """
    return pick_category(
        rules,
        description,
        merchant,
        amount,
        portfolio_id,
        lambda category, amt: _sign_matches_root(
            db.get_spending_category_root(category), amt
        ),
    )
```

3f. `upload_bank_statement` loop: compute `merchant = normalize_merchant(r.description)` first, then call `_apply_rules(r.description, rules, r.amount, db, merchant=merchant, portfolio_id=portfolio_id)`, and pass `merchant=merchant` into `PreviewSpendingRow(...)`.

3g. `rescan_categories`: replace the `_apply_rules` call with

```python
        category = _apply_rules(
            row["description"],
            rules,
            row["amount"],
            db,
            merchant=row.get("merchant"),
            portfolio_id=row["portfolio_id"],
        )
```

3h. Add a validation helper above `list_rules`:

```python
def _validate_rule_conditions(
    db,
    portfolio_id: Optional[int],
    min_amount: Optional[float],
    max_amount: Optional[float],
) -> None:
    """400 on an unknown account or an inverted amount range."""
    if portfolio_id is not None and not db.get_portfolio(portfolio_id):
        raise HTTPException(status_code=400, detail="Unknown account for this rule")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(
            status_code=400, detail="Minimum amount is above maximum amount"
        )
```

3i. Replace `create_rule`'s body after the blank checks:

```python
    _validate_rule_conditions(db, body.portfolio_id, body.min_amount, body.max_amount)
    conditions = dict(
        portfolio_id=body.portfolio_id,
        amount_sign=body.amount_sign,
        min_amount=body.min_amount,
        max_amount=body.max_amount,
    )
    if db.find_duplicate_spending_rule(pattern, category, **conditions):
        raise HTTPException(
            status_code=409,
            detail=f"A rule with pattern '{pattern}' and category '{category}' and the same conditions already exists",
        )
    rule_id = db.create_spending_rule(
        pattern=pattern, category=category, priority=body.priority, **conditions
    )
    return SpendingRuleResponse(**db.get_spending_rule(rule_id))
```

3j. Replace `update_rule`'s body:

```python
    """Edit a rule. Pattern/category/priority change when given; a condition
    field present in the body is set, and present-as-null clears it."""
    existing = db.get_spending_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    sent = body.model_fields_set
    update_kwargs: dict = {}
    if body.pattern is not None:
        pattern = body.pattern.strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="Pattern cannot be empty")
        update_kwargs["pattern"] = pattern
    if body.category is not None:
        category = body.category.strip()
        if not category:
            raise HTTPException(status_code=400, detail="Category cannot be empty")
        update_kwargs["category"] = category
    if body.priority is not None:
        update_kwargs["priority"] = body.priority
    for field in ("portfolio_id", "amount_sign", "min_amount", "max_amount"):
        if field in sent:
            update_kwargs[field] = getattr(body, field)
    if not update_kwargs:
        raise HTTPException(status_code=400, detail="Nothing to update")
    merged = {**existing, **update_kwargs}
    _validate_rule_conditions(
        db, merged["portfolio_id"], merged["min_amount"], merged["max_amount"]
    )
    db.update_spending_rule(rule_id, **update_kwargs)
    return SpendingRuleResponse(**db.get_spending_rule(rule_id))
```

3k. New endpoint, placed directly after `create_rule`:

```python
@router.post("/rules/preview", response_model=RulePreviewResponse)
async def preview_rule(
    body: RulePreviewBody,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Rows a rule would match, without saving it.

    Counts rows this rule matches on its own; a higher-priority rule could
    still claim some of them first on a rescan. With ``category`` given, rows
    whose sign that category can't hold are left out, as a rescan would.
    Transfers are never counted.
    """
    pattern = body.pattern.strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="Pattern cannot be empty")
    rule = {
        "pattern": pattern,
        "portfolio_id": body.portfolio_id,
        "amount_sign": body.amount_sign,
        "min_amount": body.min_amount,
        "max_amount": body.max_amount,
    }
    candidates = db.list_spending_transactions(
        category="uncategorized" if body.only_uncategorized else None,
        portfolio_id=body.portfolio_id,
        is_transfer=False,
    )
    excluded = set(body.exclude_ids)
    root = db.get_spending_category_root(body.category) if body.category else None
    matches = [
        r
        for r in candidates
        if r["id"] not in excluded
        and rule_matches(
            rule, r["description"], r.get("merchant"), r["amount"], r["portfolio_id"]
        )
        and _sign_matches_root(root, r["amount"])
    ]
    return RulePreviewResponse(
        match_count=len(matches),
        sample=[
            SpendingTransactionResponse(**{**r, "is_transfer": bool(r["is_transfer"])})
            for r in matches[:10]
        ],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_rules_service.py tests/unit/test_spending_api.py tests/unit/test_spending_db.py -q`
Expected: PASS. Then run the full unit suite once. Budget and reconciliation tests read categories, so check them too.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/spending_rules.py portf_server/routers/spending.py tests/unit/test_spending_rules_service.py tests/unit/test_spending_api.py
git commit -m "feat: spending rules with account/sign/amount conditions, priority and preview

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: Rules tab UI — conditions, priority, live preview, edit in the form

**Files:**
- Modify: `web_client/index.html` (Rules card, ~line 2942–2966)
- Modify: `web_client/js/pfm_core.js` (`createSpendingRule`, new `previewSpendingRule`)
- Modify: `web_client/js/pfm_features.js` (`_refreshSpendingData`, `_renderSpendingRules`, `window.editSpendingRule`, `_wireSpendingRuleForm`)
- Test: `web_client/js/tests/spending_usability.test.mjs`

**Interfaces:**
- Consumes: rule API fields and `POST /rules/preview` (Task 4).
- Produces:
  - `apiClient.createSpendingRule(pattern, category, conditions = {})` — `conditions` spreads into the body.
  - `apiClient.previewSpendingRule(payload) -> {match_count, sample}`
  - JS pure: `ruleConditionSummary(rule, accountNames) -> string`, `spRulePayloadFromForm(values) -> object` (both window-exported).
  - `window._spAccountNames: {[id]: name}` (bank accounts), set in `_refreshSpendingData`.

**Design decision:** the old inline pencil-edit of pattern/category is replaced by "load the rule into the form". The inline editor could not show five condition fields. The table keeps a pencil that fills the form and switches its button to "Save".

- [ ] **Step 1: Write the failing tests** (append to `spending_usability.test.mjs`)

```js
test("ruleConditionSummary: no conditions reads 'Any'", () => {
    const { ruleConditionSummary } = loadAppIntoContext();
    assert.equal(ruleConditionSummary({}, {}), "Any");
});

test("ruleConditionSummary: account, sign and range", () => {
    const { ruleConditionSummary } = loadAppIntoContext();
    const s = ruleConditionSummary(
        { portfolio_id: 3, amount_sign: "negative", min_amount: 5, max_amount: 50 },
        { 3: "Example Bank" },
    );
    assert.equal(s, "Example Bank · money out · 5.00–50.00");
});

test("ruleConditionSummary: one-sided ranges and a deleted account", () => {
    const { ruleConditionSummary } = loadAppIntoContext();
    assert.equal(ruleConditionSummary({ min_amount: 5 }, {}), "≥ 5.00");
    assert.equal(ruleConditionSummary({ max_amount: 5, amount_sign: "positive" }, {}), "money in · ≤ 5.00");
    assert.equal(ruleConditionSummary({ portfolio_id: 9 }, {}), "Account #9 (deleted)");
});

test("spRulePayloadFromForm converts blanks to null and numbers to numbers", () => {
    const { spRulePayloadFromForm } = loadAppIntoContext();
    const p = spRulePayloadFromForm({
        pattern: "  SHOP ", category: " Groceries ", priority: "",
        portfolioId: "", sign: "", min: "", max: "",
    });
    assert.deepEqual({ ...p }, {
        pattern: "SHOP", category: "Groceries", priority: 100,
        portfolio_id: null, amount_sign: null, min_amount: null, max_amount: null,
    });
    const q = spRulePayloadFromForm({
        pattern: "X", category: "Y", priority: "5",
        portfolioId: "3", sign: "negative", min: "1.5", max: "20",
    });
    assert.deepEqual({ ...q }, {
        pattern: "X", category: "Y", priority: 5,
        portfolio_id: 3, amount_sign: "negative", min_amount: 1.5, max_amount: 20,
    });
});
```

(`{ ...p }` copies the object out of the vm context, so `deepEqual` compares plain objects.)

- [ ] **Step 2: Run to verify failure**

Run: `make test-js` → FAIL (`ruleConditionSummary is not a function`).

- [ ] **Step 3: Implement**

3a. `pfm_core.js`, replace `createSpendingRule` and add `previewSpendingRule` after it:

```js
        async createSpendingRule(pattern, category, conditions = {}) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify({ pattern, category, ...conditions })
            });
            if (!response.ok) {
                let detail = 'Failed to create rule';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
        // Rows a not-yet-saved rule would match (count + up to 10 examples).
        async previewSpendingRule(payload) {
            const response = await fetch(this.baseURL + '/api/v1/spending/rules/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Key': this.apiKey },
                body: JSON.stringify(payload)
            });
            if (!response.ok) {
                let detail = 'Failed to preview rule';
                try { const body = await response.json(); detail = body.detail || detail; }
                catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
```

3b. `index.html`, replace the Rules card's `<thead>` row, the empty-state row and the `<form id="spRuleAddForm">…</form>` plus the status div with:

```html
                                        <thead><tr><th class="ps-3" data-key="pattern">Pattern</th><th data-key="category">Category</th><th class="d-none d-md-table-cell">Conditions</th><th data-key="priority" class="text-end">Priority</th><th class="pe-3"></th></tr></thead>
                                        <tbody id="spRulesBody"><tr><td colspan="5" class="text-center text-muted py-2">No rules yet.</td></tr></tbody>
```

```html
                                    <form id="spRuleAddForm" class="row g-2 align-items-end">
                                        <div class="col-12 col-sm-4">
                                            <label class="form-label small mb-1" for="spRulePattern">Pattern (in description or merchant)</label>
                                            <input class="form-control form-control-sm" id="spRulePattern" placeholder="e.g. MERCADONA" required>
                                        </div>
                                        <div class="col-12 col-sm-4">
                                            <label class="form-label small mb-1" for="spRuleCategory">Category</label>
                                            <input class="form-control form-control-sm" list="spCategoryList" id="spRuleCategory" placeholder="e.g. Groceries" required>
                                        </div>
                                        <div class="col-6 col-sm-2">
                                            <label class="form-label small mb-1" for="spRulePriority" title="Lower numbers are tried first">Priority</label>
                                            <input type="number" min="0" max="10000" step="1" class="form-control form-control-sm" id="spRulePriority" value="100">
                                        </div>
                                        <div class="col-6 col-sm-2 d-flex gap-1">
                                            <button type="submit" class="btn btn-sm btn-primary flex-grow-1" id="spRuleSubmitBtn"><i class="bi bi-plus-lg me-1"></i>Add</button>
                                            <button type="button" class="btn btn-sm btn-outline-secondary d-none" id="spRuleCancelEdit" title="Cancel editing"><i class="bi bi-x-lg"></i></button>
                                        </div>
                                        <div class="col-12 col-sm-4">
                                            <label class="form-label small mb-1" for="spRuleAccount">Only for account</label>
                                            <select class="form-select form-select-sm" id="spRuleAccount"><option value="">Any account</option></select>
                                        </div>
                                        <div class="col-12 col-sm-4">
                                            <label class="form-label small mb-1" for="spRuleSign">Direction</label>
                                            <select class="form-select form-select-sm" id="spRuleSign">
                                                <option value="">Money in or out</option>
                                                <option value="negative">Money out only</option>
                                                <option value="positive">Money in only</option>
                                            </select>
                                        </div>
                                        <div class="col-6 col-sm-2">
                                            <label class="form-label small mb-1" for="spRuleMin">Min amount</label>
                                            <input type="number" min="0" step="0.01" class="form-control form-control-sm" id="spRuleMin">
                                        </div>
                                        <div class="col-6 col-sm-2">
                                            <label class="form-label small mb-1" for="spRuleMax">Max amount</label>
                                            <input type="number" min="0" step="0.01" class="form-control form-control-sm" id="spRuleMax">
                                        </div>
                                    </form>
                                    <div id="spRulePreview" class="small text-muted mt-2"></div>
                                    <div id="spRuleStatus" class="small text-muted mt-2"></div>
```

3c. `pfm_features.js`, pure helpers next to `spDescriptionCellHtml`:

```js
// Pure: one-line summary of a rule's optional conditions for the Rules table.
// Amounts are in each row's own currency, so no currency symbol is shown.
function ruleConditionSummary(rule, accountNames) {
    const parts = [];
    if (rule.portfolio_id != null) {
        parts.push((accountNames || {})[rule.portfolio_id] || `Account #${rule.portfolio_id} (deleted)`);
    }
    if (rule.amount_sign === 'negative') parts.push('money out');
    else if (rule.amount_sign === 'positive') parts.push('money in');
    const min = rule.min_amount, max = rule.max_amount;
    if (min != null && max != null) parts.push(`${Fmt.num(min, 2, 2)}–${Fmt.num(max, 2, 2)}`);
    else if (min != null) parts.push(`≥ ${Fmt.num(min, 2, 2)}`);
    else if (max != null) parts.push(`≤ ${Fmt.num(max, 2, 2)}`);
    return parts.length ? parts.join(' · ') : 'Any';
}
window.ruleConditionSummary = ruleConditionSummary;

// Pure: raw Rules-form values → the API payload (blank → null, text → number).
function spRulePayloadFromForm(v) {
    const num = s => (s === '' || s == null) ? null : Number(s);
    return {
        pattern: String(v.pattern || '').trim(),
        category: String(v.category || '').trim(),
        priority: (v.priority === '' || v.priority == null) ? 100 : Number(v.priority),
        portfolio_id: v.portfolioId ? Number(v.portfolioId) : null,
        amount_sign: v.sign || null,
        min_amount: num(v.min),
        max_amount: num(v.max),
    };
}
window.spRulePayloadFromForm = spRulePayloadFromForm;

function _readSpRuleForm() {
    const val = id => document.getElementById(id)?.value ?? '';
    return spRulePayloadFromForm({
        pattern: val('spRulePattern'),
        category: _resolveCategoryInput(val('spRuleCategory')),
        priority: val('spRulePriority'),
        portfolioId: val('spRuleAccount'),
        sign: val('spRuleSign'),
        min: val('spRuleMin'),
        max: val('spRuleMax'),
    });
}

function _resetSpRuleForm() {
    const form = document.getElementById('spRuleAddForm');
    if (!form) return;
    form.reset();
    delete form.dataset.editId;
    const submit = document.getElementById('spRuleSubmitBtn');
    if (submit) submit.innerHTML = '<i class="bi bi-plus-lg me-1"></i>Add';
    document.getElementById('spRuleCancelEdit')?.classList.add('d-none');
    const preview = document.getElementById('spRulePreview');
    if (preview) preview.textContent = '';
}
```

Check `_resolveCategoryInput` returns the input unchanged for text that isn't a breadcrumb path (`grep -n "function _resolveCategoryInput" -A12 web_client/js/pfm_features.js`). `spRulePayloadFromForm` trims its output either way.

3d. `_refreshSpendingData`: after `const bankAccounts = …`:

```js
        window._spAccountNames = Object.fromEntries(bankAccounts.map(p => [p.id, p.name]));
        const ruleAccountSel = document.getElementById('spRuleAccount');
        if (ruleAccountSel) {
            const keep = ruleAccountSel.value;
            ruleAccountSel.innerHTML = '<option value="">Any account</option>'
                + bankAccounts.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
            ruleAccountSel.value = keep;
        }
```

3e. `_renderSpendingRules`: replace the row template and empty-state with (sorting stays as is; the `priority` key sorts as a string, so change the comparator to handle numbers):

```js
    const sorted = st.key ? [...rules].sort((a, b) => {
        const cmp = st.key === 'priority'
            ? (a.priority ?? 100) - (b.priority ?? 100)
            : String(a[st.key]).toLowerCase().localeCompare(String(b[st.key]).toLowerCase());
        return st.dir === 'asc' ? cmp : -cmp;
    }) : rules;
    const body = document.getElementById('spRulesBody');
    if (!body) return;
    const names = window._spAccountNames || {};
    body.innerHTML = sorted.length ? sorted.map(r => `
        <tr>
            <td class="ps-3">${esc(r.pattern)}</td>
            <td>${esc(r.category)}</td>
            <td class="d-none d-md-table-cell small text-muted">${esc(ruleConditionSummary(r, names))}</td>
            <td class="text-end">${esc(String(r.priority ?? 100))}</td>
            <td class="pe-3 text-end text-nowrap">
                <button class="btn btn-sm btn-outline-secondary" onclick="window.editSpendingRule(${r.id})" title="Edit"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger" onclick="window.deleteSpendingRule(${r.id})" title="Delete"><i class="bi bi-trash"></i></button>
            </td>
        </tr>`).join('') : '<tr><td colspan="5" class="text-center text-muted py-2">No rules yet.</td></tr>';
```

3f. Replace the whole `window.editSpendingRule = function (id) { … };` block (it runs until the `};` just before `function _wireSpendingRuleForm`) with:

```js
// Loads a rule into the Rules form; the form's submit then saves it (PUT).
window.editSpendingRule = function (id) {
    const rule = (window._spRulesData || []).find(r => r.id === id);
    const form = document.getElementById('spRuleAddForm');
    if (!rule || !form) return;
    const set = (elId, v) => { const el = document.getElementById(elId); if (el) el.value = v ?? ''; };
    set('spRulePattern', rule.pattern);
    set('spRuleCategory', rule.category);
    set('spRulePriority', rule.priority ?? 100);
    set('spRuleAccount', rule.portfolio_id ?? '');
    set('spRuleSign', rule.amount_sign ?? '');
    set('spRuleMin', rule.min_amount ?? '');
    set('spRuleMax', rule.max_amount ?? '');
    form.dataset.editId = String(id);
    const submit = document.getElementById('spRuleSubmitBtn');
    if (submit) submit.innerHTML = '<i class="bi bi-check-lg me-1"></i>Save';
    document.getElementById('spRuleCancelEdit')?.classList.remove('d-none');
    document.getElementById('spRulePattern')?.focus();
    _scheduleSpRulePreview();
};
```

3g. Replace `_wireSpendingRuleForm` with:

```js
let _spRulePreviewTimer = null;

// Debounced "Matches N uncategorized rows" line under the Rules form.
function _scheduleSpRulePreview() {
    clearTimeout(_spRulePreviewTimer);
    _spRulePreviewTimer = setTimeout(async () => {
        const out = document.getElementById('spRulePreview');
        if (!out) return;
        const p = _readSpRuleForm();
        if (!p.pattern) { out.textContent = ''; return; }
        try {
            const res = await window.apiClient.previewSpendingRule({
                pattern: p.pattern,
                category: p.category || null,
                portfolio_id: p.portfolio_id,
                amount_sign: p.amount_sign,
                min_amount: p.min_amount,
                max_amount: p.max_amount,
            });
            const examples = (res.sample || []).slice(0, 3)
                .map(r => esc(r.merchant || r.description)).join(', ');
            out.innerHTML = res.match_count
                ? `Matches ${res.match_count} uncategorized row${res.match_count === 1 ? '' : 's'}${examples ? `, e.g. ${examples}` : ''}.`
                : 'Matches no uncategorized rows right now.';
        } catch (err) {
            out.textContent = 'Preview unavailable: ' + err.message;
        }
    }, 400);
}

function _wireSpendingRuleForm() {
    const form = document.getElementById('spRuleAddForm');
    if (!form || form.dataset.wired) return;
    form.dataset.wired = '1';
    form.querySelectorAll('input, select').forEach(el => {
        el.addEventListener('input', _scheduleSpRulePreview);
        el.addEventListener('change', _scheduleSpRulePreview);
    });
    document.getElementById('spRuleCancelEdit')?.addEventListener('click', _resetSpRuleForm);
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const p = _readSpRuleForm();
        if (!p.pattern || !p.category) return;
        if (!(await _warnIfSimilarCategory(p.category))) return;
        const status = document.getElementById('spRuleStatus');
        const editId = form.dataset.editId ? Number(form.dataset.editId) : null;
        try {
            if (editId) {
                await window.apiClient.updateSpendingRule(editId, p);
            } else {
                const { pattern, category, ...conditions } = p;
                await window.apiClient.createSpendingRule(pattern, category, conditions);
            }
            _resetSpRuleForm();
            let rescanned = 0;
            try {
                const result = await window.apiClient.rescanCategories();
                rescanned = (result && result.recategorized) || 0;
            } catch (e2) { /* rescan failing shouldn't block reporting the rule was saved */ }
            await _refreshSpendingData();
            if (status) {
                status.className = 'small text-success mt-2';
                const verb = editId ? 'Rule saved.' : 'Rule added.';
                status.textContent = rescanned > 0
                    ? `${verb} Applied to ${rescanned} uncategorized row${rescanned === 1 ? '' : 's'}.`
                    : verb;
            }
        } catch (err) {
            if (status) { status.className = 'small text-danger mt-2'; status.textContent = 'Error: ' + err.message; }
            else notify('Error: ' + err.message, 'danger');
        }
    });
}
```

`updateSpendingRule(id, payload)` already exists in `pfm_core.js` and sends `payload` as JSON. Sending every field on edit is intended: `null` clears a condition.

- [ ] **Step 4: Verify**

Run: `make test-js` → PASS.
Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/e2e -q` (smoke: no JS error on any page). Needs Chromium; if it's unavailable, say so rather than skipping silently.
Manual check after the web redeploy in Task 11. Add, edit (conditions load), cancel, and delete a rule. The preview line updates while typing. The layout fits at 390px.

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_core.js web_client/js/pfm_features.js web_client/js/tests/spending_usability.test.mjs
git commit -m "feat: rules tab with conditions, priority and live match preview

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 6: Click a category to change it, then offer a rule

**Files:**
- Modify: `web_client/js/pfm_features.js` (`_fetchAndRenderSpendingTable` row template; new `_wireSpCategoryCellEditing`, `_commitSpCategoryEdit`, `_offerRuleForMerchant`, `spRuleOfferText`; call the wiring from `loadSpendingPage`)
- Test: `web_client/js/tests/spending_usability.test.mjs`

**Interfaces:**
- Consumes: `row.merchant` (Task 3); `apiClient.previewSpendingRule`, `apiClient.createSpendingRule` (Task 5); existing `apiClient.updateSpendingCategory(id, category)`, `apiClient.rescanCategories()`, `_resolveCategoryInput`, `_warnIfSimilarCategory`, `_refreshSpendingData`.
- Produces: JS pure `spRuleOfferText(merchant, category, count) -> string` ('' when count is 0).

**Design:**
- Clicking the category text swaps in an input backed by the `#spCategoryList` datalist. Enter or blur saves, Escape cancels.
- After a successful save, if the row has a merchant and there are other *uncategorized* rows with that merchant, `#spBulkStatus` shows: `Also file N other uncategorized "MERCHANT" rows as CATEGORY? [Create rule]`.
- Accepting creates a plain pattern rule on the merchant, then rescans. A 409 means the rule already exists, so it still rescans.
- No offer is made for `Transfer` or `uncategorized`.
- This replaces the doc's statement that the category column is no longer inline-editable. Bulk-select stays for multi-row work.

- [ ] **Step 1: Write the failing tests** (append)

```js
test("spRuleOfferText: nothing to offer at zero", () => {
    const { spRuleOfferText } = loadAppIntoContext();
    assert.equal(spRuleOfferText("EXAMPLE SHOP", "Groceries", 0), "");
});

test("spRuleOfferText: singular and plural", () => {
    const { spRuleOfferText } = loadAppIntoContext();
    assert.equal(
        spRuleOfferText("EXAMPLE SHOP", "Groceries", 1),
        'Also file 1 other uncategorized "EXAMPLE SHOP" row as Groceries?',
    );
    assert.equal(
        spRuleOfferText("EXAMPLE SHOP", "Groceries", 14),
        'Also file 14 other uncategorized "EXAMPLE SHOP" rows as Groceries?',
    );
});
```

- [ ] **Step 2: Run to verify failure**

Run: `make test-js` → FAIL (`spRuleOfferText is not a function`).

- [ ] **Step 3: Implement**

3a. Row template in `_fetchAndRenderSpendingTable`, replace the category `<td>` with:

```js
                <td class="sp-cat-cell" data-id="${r.id}" title="Click to change category" style="cursor:pointer;">
                    <span class="sp-cat-text border-bottom border-secondary-subtle">${esc(r.category)}</span>
                    ${r.is_transfer ? '<span class="badge bg-info ms-1">Transfer</span>' : ''}
                </td>
```

3b. New functions (module scope, near the bulk-action code):

```js
// Pure: the one-line offer shown after a single-row category change.
function spRuleOfferText(merchant, category, count) {
    if (!count) return '';
    return `Also file ${count} other uncategorized "${merchant}" row${count === 1 ? '' : 's'} as ${category}?`;
}
window.spRuleOfferText = spRuleOfferText;

function _wireSpCategoryCellEditing() {
    const tbody = document.getElementById('spTxBody');
    if (!tbody || tbody.dataset.catEditWired) return;
    tbody.dataset.catEditWired = '1';
    tbody.addEventListener('click', (e) => {
        const cell = e.target.closest('.sp-cat-cell');
        if (!cell || cell.querySelector('input')) return;
        const id = Number(cell.dataset.id);
        const row = (window._spendingAllRows || []).find(r => r.id === id);
        if (!row) return;
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'form-control form-control-sm';
        input.setAttribute('list', 'spCategoryList');
        input.value = row.category;
        cell.innerHTML = '';
        cell.appendChild(input);
        input.focus();
        input.select();
        let done = false;
        const finish = async (save) => {
            if (done) return;
            done = true;
            if (save) await _commitSpCategoryEdit(row, input.value);
            else await _fetchAndRenderSpendingTable();
        };
        input.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
            if (ev.key === 'Escape') finish(false);
        });
        input.addEventListener('blur', () => finish(true));
    });
}

async function _commitSpCategoryEdit(row, rawValue) {
    const category = _resolveCategoryInput(rawValue).trim();
    if (!category || category === row.category) { await _fetchAndRenderSpendingTable(); return; }
    if (!(await _warnIfSimilarCategory(category))) { await _fetchAndRenderSpendingTable(); return; }
    try {
        await window.apiClient.updateSpendingCategory(row.id, category);
    } catch (err) {
        notify('Error: ' + err.message, 'danger');
        await _fetchAndRenderSpendingTable();
        return;
    }
    await _refreshSpendingData();
    await _offerRuleForMerchant(row, category);
}

async function _offerRuleForMerchant(row, category) {
    const status = document.getElementById('spBulkStatus');
    const merchant = (row.merchant || '').trim();
    if (!status || !merchant || category === 'Transfer' || category === 'uncategorized') return;
    let preview;
    try {
        preview = await window.apiClient.previewSpendingRule({
            pattern: merchant, category, exclude_ids: [row.id],
        });
    } catch (e) {
        // No offer is better than a wrong one; the category change itself succeeded.
        return;
    }
    const text = spRuleOfferText(merchant, category, preview.match_count);
    if (!text) return;
    status.className = 'small px-3 pt-2';
    status.innerHTML = `${esc(text)} <button type="button" class="btn btn-sm btn-outline-primary ms-2" id="spRuleOfferBtn">Create rule</button>`;
    document.getElementById('spRuleOfferBtn').addEventListener('click', async () => {
        try {
            try {
                await window.apiClient.createSpendingRule(merchant, category);
            } catch (err) {
                // An identical rule already exists: rescanning is still what the user asked for.
                if (!/already exists/i.test(err.message)) throw err;
            }
            const res = await window.apiClient.rescanCategories();
            await _refreshSpendingData();
            const n = (res && res.recategorized) || 0;
            status.className = 'small text-success px-3 pt-2';
            status.textContent = `Rule added. Filed ${n} more row${n === 1 ? '' : 's'} as ${category}.`;
        } catch (err) {
            status.className = 'small text-danger px-3 pt-2';
            status.textContent = 'Error: ' + err.message;
        }
    });
}
```

3c. In `loadSpendingPage`, next to the other `_wire…` calls at the top: `_wireSpCategoryCellEditing();`.

Check first: `grep -n "async updateSpendingCategory" -A8 web_client/js/pfm_core.js`. It must send `{category}` and throw with the server's `detail`, so the 400 message for a wrong-signed category reaches `notify`. Also check that `_refreshSpendingData` doesn't clear `#spBulkStatus` (`grep -n "spBulkStatus" web_client/js/pfm_features.js`). If `_updateSpBulkBar` blanks it, call `_offerRuleForMerchant` after the refresh has settled; that is already the order above. Then confirm that `_updateSpBulkBar` only touches `#spBulkBar`.

- [ ] **Step 4: Verify**

Run: `make test-js` → PASS; the e2e smoke test as in Task 5.
Manual check after redeploy:
- Click a category, pick one, press Enter: the row updates and the offer appears.
- "Create rule" files the other rows and the count matches the rescan.
- Escape cancels. A wrong-signed category shows the server's 400 message.

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_features.js web_client/js/tests/spending_usability.test.mjs
git commit -m "feat: click a spending category to change it and offer a merchant rule

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 7: Balance continuity check on import

**Files:**
- Create: `portf_manager/services/balance_check.py`
- Modify: `portf_server/routers/spending.py` (`SpendingUploadResponse`, `upload_bank_statement`)
- Modify: `web_client/js/pfm_features.js` (`_renderSpImportPreview`, new `spBalanceWarningHtml`)
- Test: `tests/unit/test_balance_check.py` (new), `tests/unit/test_spending_api.py`, `web_client/js/tests/spending_usability.test.mjs`

**Interfaces:**
- Consumes: `db.get_latest_bank_balance_before` (Task 2); parser rows with `.date`, `.amount`, `.balance`, `.description`, `.currency` (`SpendingRow`).
- Produces:
  - `BalanceBreak` dataclass: `date: str, description: str, currency: str, expected: float, actual: float, kind: str` (`"gap_before_file"` | `"within_file"`).
  - `find_balance_breaks(rows, opening_balance: float | None = None, tolerance: float = 0.01) -> list[BalanceBreak]`
  - `/upload` response adds `balance_rows_checked: int` and `balance_breaks: list[{date, description, currency, expected, actual, kind}]`.
  - JS pure `spBalanceWarningHtml(breaks) -> string` ('' for none).

**Design:**
- Rows are put into chronological order. A file whose first date is later than its last date is reversed, which handles newest-first exports.
- A running total starts at the opening balance (the last stored balance strictly before the file's first date) or at the first row's own balance. A row whose balance differs from the running total by more than 1 cent is a break, and the running total then resets to that row's balance, so one missing row reports once, not on every row after it.
- It is a warning on the preview only. Saving is never blocked, because the user may know the reason.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_balance_check.py`:

```python
"""Tests for the running-balance continuity check."""

from portf_manager.parsers.generic_bank_csv_parser import SpendingRow
from portf_manager.services.balance_check import find_balance_breaks


def _row(day, amount, balance, desc="X"):
    return SpendingRow(date=day, description=desc, amount=amount, balance=balance)


def test_consistent_ascending_file_has_no_breaks():
    rows = [
        _row("2026-01-01", -10, 90),
        _row("2026-01-02", -5, 85),
        _row("2026-01-03", 15, 100),
    ]
    assert find_balance_breaks(rows) == []


def test_descending_file_is_consistent():
    rows = [
        _row("2026-01-03", 15, 100),
        _row("2026-01-02", -5, 85),
        _row("2026-01-01", -10, 90),
    ]
    assert find_balance_breaks(rows) == []


def test_missing_row_inside_file_reports_once():
    rows = [
        _row("2026-01-01", -10, 90),
        _row("2026-01-03", -5, 80, desc="AFTER GAP"),
        _row("2026-01-04", -5, 75),
    ]
    breaks = find_balance_breaks(rows)
    assert len(breaks) == 1
    b = breaks[0]
    assert (b.date, b.description, b.expected, b.actual, b.kind) == (
        "2026-01-03",
        "AFTER GAP",
        85.0,
        80.0,
        "within_file",
    )


def test_gap_before_file_uses_opening_balance():
    rows = [_row("2026-02-01", -10, 90)]
    breaks = find_balance_breaks(rows, opening_balance=120.0)
    assert len(breaks) == 1
    assert breaks[0].kind == "gap_before_file"
    assert breaks[0].expected == 110.0


def test_opening_balance_matching_is_clean():
    assert find_balance_breaks([_row("2026-02-01", -10, 90)], opening_balance=100) == []


def test_rows_without_balance_still_count_toward_running_total():
    rows = [
        _row("2026-01-01", -10, 90),
        _row("2026-01-02", -5, None),
        _row("2026-01-03", -5, 80),
    ]
    assert find_balance_breaks(rows) == []


def test_float_noise_within_tolerance():
    rows = [_row("2026-01-01", 0.1, 0.1), _row("2026-01-02", 0.2, 0.3)]
    assert find_balance_breaks(rows) == []


def test_no_balances_means_nothing_to_check():
    assert find_balance_breaks([_row("2026-01-01", -10, None)], opening_balance=5) == []
```

Append to `tests/unit/test_spending_api.py`:

```python
def test_upload_reports_balance_break(tmp_path):
    client, _ = _make_client(tmp_path)
    csv_text = (
        "date,description,amount,balance\n"
        "2026-01-01,A,-10.00,90.00\n"
        "2026-01-03,B,-5.00,80.00\n"
    )
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("s.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    d = r.json()
    assert d["balance_rows_checked"] == 2
    assert len(d["balance_breaks"]) == 1
    assert d["balance_breaks"][0]["expected"] == 85.0
    assert d["balance_breaks"][0]["kind"] == "within_file"


def test_upload_checks_continuity_with_previous_import(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(
        portfolio_id=pid, date="2026-01-31", description="OLD", amount=-1, balance=100
    )
    csv_text = "date,description,amount,balance\n2026-02-01,A,-10.00,90.00\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_portfolio_id": str(pid)},
        files={"file": ("s.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    assert r.json()["balance_breaks"] == []


def test_upload_without_balance_column_checks_nothing(tmp_path):
    client, _ = _make_client(tmp_path)
    csv_text = "date,description,amount\n2026-01-01,A,-10.00\n"
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("s.csv", _csv_bytes(csv_text), "text/csv")},
        headers=HEADERS,
    )
    d = r.json()
    assert d["balance_rows_checked"] == 0
    assert d["balance_breaks"] == []
```

Append to `spending_usability.test.mjs`:

```js
test("spBalanceWarningHtml: empty for no breaks", () => {
    const { spBalanceWarningHtml } = loadAppIntoContext();
    assert.equal(spBalanceWarningHtml([]), "");
    assert.equal(spBalanceWarningHtml(undefined), "");
});

test("spBalanceWarningHtml: names the gap kinds and escapes descriptions", () => {
    const { spBalanceWarningHtml } = loadAppIntoContext();
    const html = spBalanceWarningHtml([
        { date: "2026-02-01", description: "<b>A</b>", currency: "EUR", expected: 110, actual: 90, kind: "gap_before_file" },
        { date: "2026-02-03", description: "B", currency: "EUR", expected: 85, actual: 80, kind: "within_file" },
    ]);
    assert.match(html, /alert-warning/);
    assert.match(html, /since your last import/);
    assert.match(html, /missing from this file/);
    assert.ok(!html.includes("<b>A</b>"));
});
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_balance_check.py tests/unit/test_spending_api.py -q -k "balance"` → FAIL.
Run: `make test-js` → FAIL.

- [ ] **Step 3: Implement**

3a. `portf_manager/services/balance_check.py`:

```python
"""Running-balance continuity check for imported bank statements.

When a statement carries a balance per row, ``previous balance + amount``
must equal the row's balance. A mismatch means rows are missing (or
duplicated) — inside the file, or between the previous import and this one.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

GAP_BEFORE_FILE = "gap_before_file"
WITHIN_FILE = "within_file"


@dataclass
class BalanceBreak:
    date: str
    description: str
    currency: str
    expected: float
    actual: float
    kind: str


def _chronological(rows: Sequence) -> list:
    """Rows oldest-first; a newest-first export is reversed as a whole."""
    if len(rows) >= 2 and str(rows[0].date)[:10] > str(rows[-1].date)[:10]:
        return list(reversed(rows))
    return list(rows)


def find_balance_breaks(
    rows: Sequence,
    opening_balance: Optional[float] = None,
    tolerance: float = 0.01,
) -> List[BalanceBreak]:
    """Rows whose stated balance doesn't follow from the one before.

    Args:
        rows: Parsed statement rows with ``date``, ``description``,
            ``amount``, ``balance`` (may be None) and ``currency``.
        opening_balance: Balance just before the first row, from the previous
            import; None when unknown, so the first balance row only anchors.
        tolerance: Allowed absolute difference (float noise, rounding).

    Returns:
        One entry per break. After a break the running total resets to the
        row's stated balance, so a single missing row is reported once.
    """
    breaks: List[BalanceBreak] = []
    running = opening_balance
    seen_balance = False
    for row in _chronological(rows):
        if running is not None:
            running = round(running + float(row.amount), 2)
        if row.balance is None:
            continue
        if running is not None and abs(running - float(row.balance)) > tolerance:
            breaks.append(
                BalanceBreak(
                    date=str(row.date)[:10],
                    description=row.description,
                    currency=getattr(row, "currency", "EUR") or "EUR",
                    expected=running,
                    actual=round(float(row.balance), 2),
                    kind=WITHIN_FILE if seen_balance else GAP_BEFORE_FILE,
                )
            )
        running = round(float(row.balance), 2)
        seen_balance = True
    return breaks
```

This one-pass design gives `gap_before_file` only for the first balance row, and only when `opening_balance` was given. Without one, `running` is None until the first balance row, so that row can't break.

3b. `spending.py`:
- import: `from portf_manager.services.balance_check import find_balance_breaks`
- models, before `SpendingUploadResponse`:

```python
class BalanceBreakOut(BaseModel):
    date: str
    description: str
    currency: str
    expected: float
    actual: float
    kind: str
```

- `SpendingUploadResponse`: add `balance_rows_checked: int = 0` and `balance_breaks: List[BalanceBreakOut] = []`.
- in `upload_bank_statement`, after the rows loop and before `skipped = …`:

```python
    # Warn (never block) when stated balances don't add up: rows missing from
    # this file, or between the previous import and this one.
    balance_rows = [r for r in result.rows if r.balance is not None]
    breaks = []
    if balance_rows:
        first_date = min(str(r.date)[:10] for r in result.rows)
        prior = db.get_latest_bank_balance_before(portfolio_id, first_date)
        breaks = find_balance_breaks(
            result.rows, opening_balance=prior["balance"] if prior else None
        )
```

and pass into the response:

```python
        balance_rows_checked=len(balance_rows),
        balance_breaks=[BalanceBreakOut(**vars(b)) for b in breaks],
```

3c. `pfm_features.js`, pure helper near `_renderSpImportPreview`:

```js
// Pure: warning above the import preview when stated balances don't add up.
function spBalanceWarningHtml(breaks) {
    if (!breaks || !breaks.length) return '';
    const lines = breaks.slice(0, 5).map(b => {
        const where = b.kind === 'gap_before_file'
            ? 'since your last import'
            : 'missing from this file';
        return `<li>${esc(Fmt.date(b.date))} · ${esc(b.description)}: expected ${Fmt.money(b.expected, b.currency, 2)}, statement says ${Fmt.money(b.actual, b.currency, 2)} (rows likely ${where})</li>`;
    }).join('');
    const more = breaks.length > 5 ? `<li>…and ${breaks.length - 5} more</li>` : '';
    return `<div class="alert alert-warning small py-2">
        <strong>Balance doesn't add up at ${breaks.length} point${breaks.length === 1 ? '' : 's'}.</strong>
        Some transactions may be missing. You can still save.
        <ul class="mb-0 mt-1">${lines}${more}</ul>
    </div>`;
}
window.spBalanceWarningHtml = spBalanceWarningHtml;
```

In `_renderSpImportPreview`, make the template start with `${spBalanceWarningHtml(result.balance_breaks)}` before `${_spDupControl(…)}`.

If `Fmt.money` returns a `<span>` wrapper, it is fine inside element content (only attributes are forbidden). The test's `!html.includes("<b>A</b>")` checks escaping of the description only.

- [ ] **Step 4: Verify**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_balance_check.py tests/unit/test_spending_api.py tests/unit/test_aeb43_parser.py -q` → PASS.
Run: `make test-js` → PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/balance_check.py portf_server/routers/spending.py web_client/js/pfm_features.js tests/unit/test_balance_check.py tests/unit/test_spending_api.py web_client/js/tests/spending_usability.test.mjs
git commit -m "feat: warn when an imported statement's balances don't add up

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 8: Recurring-charge detection + `GET /spending/recurring`

**Files:**
- Create: `portf_manager/services/recurring.py`
- Modify: `portf_server/routers/spending.py` (models + endpoint)
- Test: `tests/unit/test_recurring.py` (new), `tests/unit/test_spending_api.py`

**Interfaces:**
- Consumes: `merchant` column (Task 2); `db.list_spending_transactions(is_transfer=False, amount_sign="negative", portfolio_id=…)`; `db.get_portfolio_date_ranges()` → `{pid: {"last_spending_date": ...}}`; `db.get_all_portfolios()`.
- Produces:
  - `RecurringSeries` dataclass (fields below).
  - `detect_recurring(rows: list[dict], last_import_by_portfolio: dict[int, date], min_occurrences: int = 3) -> list[RecurringSeries]` (pure).
  - `load_recurring(db, portfolio_id: int | None = None) -> list[RecurringSeries]`.
  - `GET /api/v1/spending/recurring?portfolio_id=&include_ended=false` → `{items: [RecurringItem], monthly_total_eur, annual_total_eur}`.

**Algorithm (the contract the tests pin):**
1. Only money out (`amount < 0`), not transfers. Group by `(portfolio_id, merchant.upper(), currency)`, falling back to the description when the merchant is blank.
2. Merge same-day charges of a group into one charge (sum of amounts).
3. Need ≥2 charges. Take the gaps between consecutive charge dates and their median. The cadence is the first of weekly (7±2 d), monthly (30.44±5), quarterly (91.3±10), yearly (365.25±20) whose tolerance contains the median, **and** ≥75% of the gaps lie within 2× that tolerance. Otherwise there is no series.
4. Non-yearly cadences need ≥`min_occurrences` (3) charges. Yearly needs 2.
5. Amount stability: ≥75% of charges lie within ±25% of the median amount. Otherwise there is no series (this filters out groceries).
6. `next_expected` = last charge + 7 days (weekly) or + 1/3/12 calendar months, with the day clamped to the month's end.
7. Status uses the account's last imported spending date `L`, with `tol` = the cadence tolerance:
   - `L` unknown or `L ≤ next_expected + tol` → `active`. The statement doesn't cover the due date yet, so absence proves nothing.
   - `L ≤ next_after(next_expected) + tol` → `missed`.
   - otherwise → `ended`.
8. `price_change_pct` = (last − previous) / previous × 100, rounded to 1 decimal, set only when |change| ≥ 5%.
9. `annual_amount` = typical (median) × 52 / 12 / 4 / 1.
10. Sort: missed, active, ended; then `annual_amount` descending.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_recurring.py`:

```python
"""Tests for recurring-charge detection."""

from datetime import date

from portf_manager.services.recurring import detect_recurring

_ids = iter(range(1, 100000))


def _tx(day, amount, merchant="EXAMPLE STREAMING", pid=1, **kw):
    return {
        "id": next(_ids),
        "portfolio_id": pid,
        "date": day,
        "amount": amount,
        "currency": kw.get("currency", "EUR"),
        "merchant": merchant,
        "description": kw.get("description", merchant),
        "category": kw.get("category", "Subscriptions"),
        "is_transfer": kw.get("is_transfer", 0),
    }


def _monthly(amounts, start_month=1, merchant="EXAMPLE STREAMING", pid=1):
    return [
        _tx(f"2026-{start_month + i:02d}-15", -a, merchant=merchant, pid=pid)
        for i, a in enumerate(amounts)
    ]


def test_monthly_subscription_detected():
    rows = _monthly([9.99, 9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.merchant == "EXAMPLE STREAMING"
    assert s.cadence == "monthly"
    assert s.occurrences == 4
    assert s.next_expected == "2026-05-15"
    assert s.typical_amount == 9.99
    assert s.annual_amount == round(9.99 * 12, 2)
    assert s.status == "active"
    assert s.price_change_pct is None


def test_price_increase_flagged():
    rows = _monthly([10.0, 10.0, 10.0, 12.0])
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.price_change_pct == 20.0


def test_irregular_amounts_are_not_recurring():
    rows = [
        _tx("2026-01-03", -12.0, merchant="EXAMPLE SUPERMARKET"),
        _tx("2026-01-10", -85.0, merchant="EXAMPLE SUPERMARKET"),
        _tx("2026-01-17", -30.0, merchant="EXAMPLE SUPERMARKET"),
        _tx("2026-01-24", -140.0, merchant="EXAMPLE SUPERMARKET"),
    ]
    assert detect_recurring(rows, {1: date(2026, 1, 30)}) == []


def test_irregular_intervals_are_not_recurring():
    rows = [
        _tx("2026-01-01", -10.0),
        _tx("2026-01-04", -10.0),
        _tx("2026-02-20", -10.0),
        _tx("2026-02-22", -10.0),
    ]
    assert detect_recurring(rows, {1: date(2026, 3, 1)}) == []


def test_weekly_detected():
    rows = [_tx(f"2026-01-{d:02d}", -5.0) for d in (5, 12, 19, 26)]
    [s] = detect_recurring(rows, {1: date(2026, 1, 28)})
    assert s.cadence == "weekly"
    assert s.next_expected == "2026-02-02"


def test_yearly_needs_only_two():
    rows = [_tx("2025-03-01", -120.0), _tx("2026-03-02", -120.0)]
    [s] = detect_recurring(rows, {1: date(2026, 3, 10)})
    assert s.cadence == "yearly"
    assert s.next_expected == "2027-03-02"


def test_two_monthly_charges_are_not_enough():
    assert detect_recurring(_monthly([9.99, 9.99]), {1: date(2026, 2, 20)}) == []


def test_same_day_charges_merge():
    rows = _monthly([5.0, 5.0, 5.0, 5.0])
    rows.append(_tx("2026-04-15", -5.0))
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.occurrences == 4
    assert s.last_amount == 10.0
    assert len(s.transaction_ids) == 5


def test_not_missed_when_statement_not_imported_yet():
    rows = _monthly([9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 3, 31)})
    assert s.next_expected == "2026-04-15"
    assert s.status == "active"


def test_missed_when_statement_covers_due_date():
    rows = _monthly([9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 4, 25)})
    assert s.status == "missed"


def test_ended_after_two_missing_cycles():
    rows = _monthly([9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 6, 1)})
    assert s.status == "ended"


def test_income_and_transfers_ignored():
    rows = [_tx(f"2026-0{m}-15", 9.99) for m in (1, 2, 3)]
    rows += [_tx(f"2026-0{m}-15", -9.99, is_transfer=1) for m in (1, 2, 3)]
    assert detect_recurring(rows, {1: date(2026, 3, 20)}) == []


def test_accounts_are_separate_series():
    rows = _monthly([9.99] * 3, pid=1) + _monthly([9.99] * 3, pid=2)
    series = detect_recurring(rows, {1: date(2026, 3, 20), 2: date(2026, 3, 20)})
    assert sorted(s.portfolio_id for s in series) == [1, 2]


def test_sort_missed_first():
    missed = _monthly([5.0] * 3, merchant="A")
    active = _monthly([50.0] * 3, start_month=2, merchant="B")
    series = detect_recurring(missed + active, {1: date(2026, 4, 25)})
    assert [s.merchant for s in series] == ["A", "B"]
    assert [s.status for s in series] == ["missed", "active"]
```

Append to `tests/unit/test_spending_api.py`:

```python
def test_recurring_endpoint(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    for month in (1, 2, 3):
        _add(db, pid, "EXAMPLE STREAMING", amount=-9.99, day=f"2026-0{month}-15")
    r = client.get("/api/v1/spending/recurring", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert len(d["items"]) == 1
    item = d["items"][0]
    assert item["merchant"] == "EXAMPLE STREAMING"
    assert item["account_name"] == "Example Bank"
    assert item["cadence"] == "monthly"
    assert d["annual_total_eur"] == round(9.99 * 12, 2)
    assert d["monthly_total_eur"] == round(9.99 * 12 / 12, 2)


def test_recurring_endpoint_hides_ended_by_default(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    for month in (1, 2, 3):
        _add(db, pid, "OLD SERVICE", amount=-5.0, day=f"2026-0{month}-10")
    _add(db, pid, "SOMETHING ELSE", amount=-1.0, day="2026-08-01")
    assert client.get("/api/v1/spending/recurring", headers=HEADERS).json()["items"] == []
    shown = client.get(
        "/api/v1/spending/recurring?include_ended=true", headers=HEADERS
    ).json()
    assert [i["status"] for i in shown["items"]] == ["ended"]
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_recurring.py tests/unit/test_spending_api.py -q -k recurring` → FAIL.

- [ ] **Step 3: Implement**

3a. `portf_manager/services/recurring.py`:

```python
"""Recurring-charge (subscription) detection over bank spending rows.

Groups outflows by account + merchant, keeps groups whose charges arrive at
a steady cadence for a steady amount, and says when the next one is due and
whether it failed to arrive. A charge only counts as missed once a statement
covering its due date has been imported — a missing statement is not a
cancelled subscription.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Dict, List, Optional

# (name, typical gap in days, tolerance in days, charges per year)
CADENCES = (
    ("weekly", 7.0, 2.0, 52),
    ("monthly", 30.44, 5.0, 12),
    ("quarterly", 91.3, 10.0, 4),
    ("yearly", 365.25, 20.0, 1),
)
_MONTHS_AHEAD = {"monthly": 1, "quarterly": 3, "yearly": 12}
AMOUNT_TOLERANCE = 0.25
REGULAR_SHARE = 0.75
PRICE_CHANGE_MIN_PCT = 5.0
_STATUS_ORDER = {"missed": 0, "active": 1, "ended": 2}


@dataclass
class RecurringSeries:
    merchant: str
    portfolio_id: int
    currency: str
    category: str
    cadence: str
    occurrences: int
    first_date: str
    last_date: str
    last_amount: float
    typical_amount: float
    annual_amount: float
    next_expected: str
    status: str
    price_change_pct: Optional[float]
    transaction_ids: List[int]


def _add_months(day: date, months: int) -> date:
    """Same day-of-month ``months`` later, clamped to that month's end."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _advance(day: date, cadence: str) -> date:
    if cadence == "weekly":
        return day + timedelta(days=7)
    return _add_months(day, _MONTHS_AHEAD[cadence])


def _classify(gaps: List[int]) -> Optional[tuple]:
    """The cadence tuple the gaps fit, or None."""
    typical_gap = median(gaps)
    for cadence in CADENCES:
        _, days, tol, _ = cadence
        if abs(typical_gap - days) > tol:
            continue
        regular = sum(1 for g in gaps if abs(g - days) <= 2 * tol)
        if regular / len(gaps) >= REGULAR_SHARE:
            return cadence
    return None


def _status(
    next_expected: date, cadence: str, tol: float, last_import: Optional[date]
) -> str:
    grace = timedelta(days=tol)
    if last_import is None or last_import <= next_expected + grace:
        return "active"
    if last_import <= _advance(next_expected, cadence) + grace:
        return "missed"
    return "ended"


def detect_recurring(
    rows: List[dict],
    last_import_by_portfolio: Dict[int, date],
    min_occurrences: int = 3,
) -> List[RecurringSeries]:
    """Find steady recurring outflows.

    Args:
        rows: spending_transactions rows (dicts with id, portfolio_id, date,
            amount, currency, merchant, description, category, is_transfer).
        last_import_by_portfolio: Latest imported spending date per account.
        min_occurrences: Charges needed for a non-yearly cadence.

    Returns:
        Series sorted missed → active → ended, then by annual amount.
    """
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        if row["amount"] >= 0 or row.get("is_transfer"):
            continue
        name = (row.get("merchant") or row.get("description") or "").strip()
        if not name:
            continue
        currency = (row.get("currency") or "EUR").upper()
        groups[(row["portfolio_id"], name.upper(), currency)].append(row)

    found: List[RecurringSeries] = []
    for (portfolio_id, _, currency), items in groups.items():
        items.sort(key=lambda r: (str(r["date"])[:10], r["id"]))
        # One charge per day: [day, total amount, ids, latest row]
        charges: List[list] = []
        for r in items:
            day = date.fromisoformat(str(r["date"])[:10])
            if charges and charges[-1][0] == day:
                charges[-1][1] += -r["amount"]
                charges[-1][2].append(r["id"])
                charges[-1][3] = r
            else:
                charges.append([day, -r["amount"], [r["id"]], r])
        if len(charges) < 2:
            continue
        gaps = [(b[0] - a[0]).days for a, b in zip(charges, charges[1:])]
        cadence = _classify(gaps)
        if cadence is None:
            continue
        name, _, tol, per_year = cadence
        if name != "yearly" and len(charges) < min_occurrences:
            continue
        amounts = [round(c[1], 2) for c in charges]
        typical = median(amounts)
        steady = sum(1 for a in amounts if abs(a - typical) <= AMOUNT_TOLERANCE * typical)
        if steady / len(amounts) < REGULAR_SHARE:
            continue
        last_day = charges[-1][0]
        next_expected = _advance(last_day, name)
        change = (amounts[-1] - amounts[-2]) / amounts[-2] * 100
        latest = charges[-1][3]
        found.append(
            RecurringSeries(
                merchant=(latest.get("merchant") or latest.get("description")).strip(),
                portfolio_id=portfolio_id,
                currency=currency,
                category=latest.get("category") or "uncategorized",
                cadence=name,
                occurrences=len(charges),
                first_date=charges[0][0].isoformat(),
                last_date=last_day.isoformat(),
                last_amount=amounts[-1],
                typical_amount=round(typical, 2),
                annual_amount=round(typical * per_year, 2),
                next_expected=next_expected.isoformat(),
                status=_status(
                    next_expected, name, tol, last_import_by_portfolio.get(portfolio_id)
                ),
                price_change_pct=(
                    round(change, 1) if abs(change) >= PRICE_CHANGE_MIN_PCT else None
                ),
                transaction_ids=[i for c in charges for i in c[2]],
            )
        )
    found.sort(key=lambda s: (_STATUS_ORDER[s.status], -s.annual_amount))
    return found


def load_recurring(db, portfolio_id: Optional[int] = None) -> List[RecurringSeries]:
    """detect_recurring over the database's bank outflows."""
    rows = db.list_spending_transactions(
        portfolio_id=portfolio_id, is_transfer=False, amount_sign="negative"
    )
    last_import = {}
    for pid, dates in db.get_portfolio_date_ranges().items():
        last = (dates or {}).get("last_spending_date")
        if last:
            last_import[pid] = date.fromisoformat(str(last)[:10])
    return detect_recurring(rows, last_import)
```

Check before relying on `load_recurring`: `grep -n "def get_portfolio_date_ranges" -A30 portf_manager/database.py`. Confirm the keys are integer portfolio ids and that `last_spending_date` is the field name (Action Items' `check_stale_imports` already reads it this way).

A zero previous amount can't happen: only outflows (`amount < 0`) are kept, so every merged daily amount is positive and the price-change division is safe.

3b. `spending.py`: import `from portf_manager.services.recurring import load_recurring`, add models after `SpendingSummaryResponse`:

```python
class RecurringItem(BaseModel):
    merchant: str
    portfolio_id: int
    account_name: Optional[str] = None
    currency: str
    category: str
    cadence: str
    occurrences: int
    first_date: str
    last_date: str
    last_amount: float
    typical_amount: float
    annual_amount: float
    annual_amount_eur: float
    next_expected: str
    status: str
    price_change_pct: Optional[float] = None
    transaction_ids: List[int]


class RecurringResponse(BaseModel):
    items: List[RecurringItem]
    monthly_total_eur: float
    annual_total_eur: float
```

Endpoint, placed right after `/summary`:

```python
@router.get("/recurring", response_model=RecurringResponse)
def list_recurring(
    portfolio_id: Optional[int] = None,
    include_ended: bool = False,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Recurring charges (subscriptions, bills) detected from bank outflows.

    Totals are EUR at today's rate (same convention as /summary) over
    series that haven't ended. Ended series are hidden unless asked for.
    A plain ``def`` because the FX lookup can do blocking network I/O.
    """
    names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    items: List[RecurringItem] = []
    for s in load_recurring(db, portfolio_id=portfolio_id):
        if s.status == "ended" and not include_ended:
            continue
        items.append(
            RecurringItem(
                **vars(s),
                account_name=names.get(s.portfolio_id),
                annual_amount_eur=round(s.annual_amount * _fx(s.currency), 2),
            )
        )
    annual = round(sum(i.annual_amount_eur for i in items if i.status != "ended"), 2)
    return RecurringResponse(
        items=items, monthly_total_eur=round(annual / 12, 2), annual_total_eur=annual
    )
```

Check `_fx("EUR")` returns 1.0 without a network call (`grep -n "def _get_fx_rate" -A15 portf_server/routers/portfolios.py`). The API test depends on it, because tests can't reach the network. If it doesn't short-circuit EUR, patch `portf_server.routers.spending._fx` to `lambda c: 1.0` in the two recurring API tests.

- [ ] **Step 4: Verify**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_recurring.py tests/unit/test_spending_api.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/recurring.py portf_server/routers/spending.py tests/unit/test_recurring.py tests/unit/test_spending_api.py
git commit -m "feat: detect recurring charges from bank spending

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 9: Recurring tab on the Spending page

**Files:**
- Modify: `web_client/index.html` (`#spTabs`, new pane after `#spPaneRules`)
- Modify: `web_client/js/pfm_core.js` (`getSpendingRecurring`)
- Modify: `web_client/js/pfm_features.js` (`recurringStatusBadges`, `recurringCadenceLabel`, `_loadSpRecurring`, `_wireSpRecurringTab`; call from `loadSpendingPage`)
- Modify: `web_client/js/help_text.js` (`PAGE_HELP.spending` bullets)
- Test: `web_client/js/tests/spending_usability.test.mjs`

**Interfaces:**
- Consumes: `GET /spending/recurring` (Task 8), `#spSearch` (Task 3).
- Produces: `apiClient.getSpendingRecurring({includeEnded})`; JS pure `recurringStatusBadges(item) -> string`, `recurringCadenceLabel(cadence) -> string`.

- [ ] **Step 1: Write the failing tests** (append)

```js
test("recurringCadenceLabel", () => {
    const { recurringCadenceLabel } = loadAppIntoContext();
    assert.equal(recurringCadenceLabel("weekly"), "Week");
    assert.equal(recurringCadenceLabel("monthly"), "Month");
    assert.equal(recurringCadenceLabel("quarterly"), "Quarter");
    assert.equal(recurringCadenceLabel("yearly"), "Year");
});

test("recurringStatusBadges: missed, ended, price up and down", () => {
    const { recurringStatusBadges } = loadAppIntoContext();
    assert.equal(recurringStatusBadges({ status: "active", price_change_pct: null }), "");
    assert.match(recurringStatusBadges({ status: "missed" }), /bg-danger[^>]*>Missed</);
    assert.match(recurringStatusBadges({ status: "ended" }), /bg-secondary[^>]*>Ended</);
    assert.match(recurringStatusBadges({ status: "active", price_change_pct: 20 }), /▲ \+20%/);
    assert.match(recurringStatusBadges({ status: "active", price_change_pct: -12.5 }), /▼ -12.5%/);
});
```

- [ ] **Step 2: Run to verify failure** — `make test-js` → FAIL.

- [ ] **Step 3: Implement**

3a. `index.html`, in `#spTabs` after the Rules `<li>`:

```html
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#spPaneRecurring" id="spTabBtnRecurring"><i class="bi bi-arrow-repeat me-1"></i>Recurring</button>
                        </li>
```

(Match the surrounding `<li>` markup exactly. Check with `sed -n 2851,2865p web_client/index.html`.)

After the closing `</div>` of `#spPaneRules`:

```html
                        <div class="tab-pane fade" id="spPaneRecurring">
                            <div class="card">
                                <div class="card-header d-flex flex-wrap align-items-center gap-2">
                                    <span class="fw-semibold">Recurring charges</span>
                                    <span class="small text-muted ms-auto" id="spRecurringTotals"></span>
                                </div>
                                <div class="table-responsive">
                                    <table class="table table-sm mb-0">
                                        <thead><tr>
                                            <th class="ps-3">Merchant</th>
                                            <th class="d-none d-md-table-cell">Account</th>
                                            <th>Every</th>
                                            <th class="text-end">Typical</th>
                                            <th>Next</th>
                                            <th class="text-end d-none d-sm-table-cell">Per year</th>
                                            <th class="pe-3"></th>
                                        </tr></thead>
                                        <tbody id="spRecurringBody"><tr><td colspan="7" class="text-center text-muted py-3">Loading…</td></tr></tbody>
                                    </table>
                                </div>
                                <div class="card-body py-2 border-top">
                                    <div class="form-check small mb-0">
                                        <input class="form-check-input" type="checkbox" id="spRecurringShowEnded">
                                        <label class="form-check-label" for="spRecurringShowEnded">Show ended</label>
                                    </div>
                                </div>
                            </div>
                        </div>
```

3b. `pfm_core.js`, after `getSpendingRules` (or any spending method):

```js
        async getSpendingRecurring({ includeEnded = false } = {}) {
            const qs = includeEnded ? '?include_ended=true' : '';
            const response = await fetch(this.baseURL + '/api/v1/spending/recurring' + qs, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load recurring charges');
            return response.json();
        },
```

3c. `pfm_features.js`:

```js
const _SP_CADENCE_LABELS = { weekly: 'Week', monthly: 'Month', quarterly: 'Quarter', yearly: 'Year' };

// Pure: short cadence label for the Recurring table's "Every" column.
function recurringCadenceLabel(cadence) {
    return _SP_CADENCE_LABELS[cadence] || cadence;
}
window.recurringCadenceLabel = recurringCadenceLabel;

// Pure: status + price-change badges for one recurring series.
function recurringStatusBadges(item) {
    const badges = [];
    if (item.status === 'missed') badges.push('<span class="badge bg-danger" title="A statement covering the due date is imported, but the charge isn\'t in it">Missed</span>');
    if (item.status === 'ended') badges.push('<span class="badge bg-secondary">Ended</span>');
    const pct = item.price_change_pct;
    if (pct != null) {
        badges.push(pct > 0
            ? `<span class="badge bg-warning text-dark" title="Last charge vs the one before">▲ +${pct}%</span>`
            : `<span class="badge bg-info text-dark" title="Last charge vs the one before">▼ ${pct}%</span>`);
    }
    return badges.join(' ');
}
window.recurringStatusBadges = recurringStatusBadges;

async function _loadSpRecurring() {
    const body = document.getElementById('spRecurringBody');
    const totals = document.getElementById('spRecurringTotals');
    if (!body) return;
    const includeEnded = !!document.getElementById('spRecurringShowEnded')?.checked;
    let data;
    try {
        data = await window.apiClient.getSpendingRecurring({ includeEnded });
    } catch (err) {
        body.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3">${esc(err.message)}</td></tr>`;
        return;
    }
    const items = data.items || [];
    if (totals) {
        totals.innerHTML = items.length
            ? `≈ ${Fmt.money(data.monthly_total_eur, 'EUR', 0)}/month · ${Fmt.money(data.annual_total_eur, 'EUR', 0)}/year`
            : '';
    }
    body.innerHTML = items.length ? items.map(i => `
        <tr class="${i.status === 'ended' ? 'text-muted' : ''}">
            <td class="ps-3"><a href="#" class="sp-recurring-merchant" data-merchant="${escapeForAttr(i.merchant)}" title="Show these transactions">${esc(i.merchant)}</a>
                <div class="small text-muted">${esc(i.category)}</div></td>
            <td class="d-none d-md-table-cell">${esc(i.account_name || '')}</td>
            <td>${esc(recurringCadenceLabel(i.cadence))}</td>
            <td class="text-end">${Fmt.money(i.typical_amount, i.currency, 2)}</td>
            <td>${esc(Fmt.date(i.next_expected))}</td>
            <td class="text-end d-none d-sm-table-cell">${Fmt.money(i.annual_amount, i.currency, 0)}</td>
            <td class="pe-3 text-end">${recurringStatusBadges(i)}</td>
        </tr>`).join('')
        : '<tr><td colspan="7" class="text-center text-muted py-3">No recurring charges found yet. They show up after three regular charges (two for yearly ones).</td></tr>';
}

function _wireSpRecurringTab() {
    const tabBtn = document.getElementById('spTabBtnRecurring');
    if (tabBtn && !tabBtn.dataset.wired) {
        tabBtn.dataset.wired = '1';
        tabBtn.addEventListener('shown.bs.tab', _loadSpRecurring);
    }
    const showEnded = document.getElementById('spRecurringShowEnded');
    if (showEnded && !showEnded.dataset.wired) {
        showEnded.dataset.wired = '1';
        showEnded.addEventListener('change', _loadSpRecurring);
    }
    const body = document.getElementById('spRecurringBody');
    if (body && !body.dataset.wired) {
        body.dataset.wired = '1';
        body.addEventListener('click', async (e) => {
            const link = e.target.closest('.sp-recurring-merchant');
            if (!link) return;
            e.preventDefault();
            const search = document.getElementById('spSearch');
            if (search) search.value = link.dataset.merchant;
            window._spCategoryFilterSelected = null;
            window._spTxState.page = 0;
            bootstrap.Tab.getOrCreateInstance(document.getElementById('spTabBtnTransactions')).show();
            await _fetchAndRenderSpendingTable();
        });
    }
}
```

`Fmt.date` may return markup rather than plain text. If `grep -n "date(" web_client/js/pfm_core.js | head` shows it returns a plain string, drop the `esc()` wrapper around it for consistency with the table's existing `${Fmt.date(r.date)}`. Leave it in if unsure; escaping a plain date is harmless.

Call `_wireSpRecurringTab();` at the top of `loadSpendingPage` next to the other `_wire…` calls.

3d. `help_text.js`, in `PAGE_HELP.spending`'s `<ul>`, add:

```html
        <li><strong>Search</strong> matches the bank's description and the cleaned-up merchant name (card numbers, towns and reference codes stripped).</li>
        <li><strong>Click a category</strong> in the table to change it. If other uncategorized rows share the merchant, you're offered a rule that files them all.</li>
        <li><strong>Rules</strong> can be limited to one account, money in or out, and an amount range. Lower priority numbers are tried first.</li>
        <li><strong>Recurring</strong> lists charges that arrive at a steady interval for a steady amount. <em>Missed</em> means a statement covering the due date is imported but the charge isn't in it.</li>
        <li>When a statement has a balance column, the import preview warns if the balances don't add up, which usually means missing rows.</li>
```

- [ ] **Step 4: Verify**

Run: `make test-js` → PASS; e2e smoke (as Task 5).

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html web_client/js/pfm_core.js web_client/js/pfm_features.js web_client/js/help_text.js web_client/js/tests/spending_usability.test.mjs
git commit -m "feat: recurring charges tab on the spending page

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 10: Action Item for missed charges and price rises

**Files:**
- Modify: `portf_manager/services/action_items.py` (new check + `_CHECKS`)
- Test: the existing Action Items test file (find it: `ls tests/unit | grep -i action`)

**Interfaces:**
- Consumes: `load_recurring(db)` (Task 8).
- Produces: `check_recurring_charges(db, today: date = None) -> list[dict]` in `_CHECKS`. Item ids: `recurring:missed:<pid>:<merchant>` and `recurring:price:<pid>:<merchant>:<last_date>`. The last date is part of the price id so the next rise isn't hidden by a dismissal of this one.

**Rules:**
- `missed` series → severity `medium`.
- Active series whose last charge is within 45 days of `today` with `price_change_pct >= 10` → severity `low`.
- Price drops never raise an item.

- [ ] **Step 1: Write the failing tests** (append to the Action Items test file; reuse its `db` fixture or create `Database(str(tmp_path / "t.db"))`)

```python
from datetime import date

from portf_manager.services.action_items import (
    check_recurring_charges,
    checks_for_tests,
)


def _charges(db, pid, merchant, amounts, months):
    for amount, month in zip(amounts, months):
        db.create_spending_transaction(
            portfolio_id=pid,
            date=f"2026-{month:02d}-15",
            description=merchant,
            amount=-amount,
        )


def test_recurring_check_is_registered():
    assert check_recurring_charges in checks_for_tests()


def test_missed_recurring_charge_raises_item(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _charges(db, pid, "EXAMPLE STREAMING", [9.99] * 3, [1, 2, 3])
    db.create_spending_transaction(
        portfolio_id=pid, date="2026-04-25", description="OTHER", amount=-1.0
    )
    items = check_recurring_charges(db, today=date(2026, 4, 26))
    assert [i["id"] for i in items] == [f"recurring:missed:{pid}:EXAMPLE STREAMING"]
    assert items[0]["severity"] == "medium"
    assert items[0]["link_page"] == "spending"


def test_recent_price_rise_raises_low_item(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _charges(db, pid, "EXAMPLE STREAMING", [10.0, 10.0, 10.0, 12.0], [1, 2, 3, 4])
    items = check_recurring_charges(db, today=date(2026, 4, 20))
    assert len(items) == 1
    assert items[0]["severity"] == "low"
    assert items[0]["id"] == f"recurring:price:{pid}:EXAMPLE STREAMING:2026-04-15"


def test_old_price_rise_and_price_drop_are_quiet(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    pid = db.create_portfolio("Example Bank", account_type="bank")
    _charges(db, pid, "RISE", [10.0, 10.0, 10.0, 12.0], [1, 2, 3, 4])
    _charges(db, pid, "DROP", [10.0, 10.0, 10.0, 8.0], [1, 2, 3, 4])
    # Last import is 2026-04-15, before the 05-15 due date: both series are
    # active; the rise is 77 days old and a drop never nags.
    assert check_recurring_charges(db, today=date(2026, 7, 1)) == []
    assert [
        i["id"] for i in check_recurring_charges(db, today=date(2026, 4, 20))
    ] == [f"recurring:price:{pid}:RISE:2026-04-15"]
```

- [ ] **Step 2: Run to verify failure** — `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit -q -k recurring` → FAIL (ImportError).

- [ ] **Step 3: Implement** (before `_SEVERITY_ORDER` in `action_items.py`)

```python
RECURRING_PRICE_RISE_PCT = 10.0
RECURRING_PRICE_RECENT_DAYS = 45


def check_recurring_charges(db, today: date = None) -> list[dict]:
    """Recurring charges that didn't arrive, and recent price rises.

    "Missed" only fires once a statement covering the due date is imported
    (see services.recurring), so a late import never looks like a cancelled
    subscription. Price drops aren't worth a nag.
    """
    from portf_manager.services.recurring import load_recurring

    today = today or date.today()
    items = []
    for s in load_recurring(db):
        key = f"{s.portfolio_id}:{s.merchant}"
        context = {"portfolio_id": s.portfolio_id, "merchant": s.merchant}
        if s.status == "missed":
            items.append(
                {
                    "id": f"recurring:missed:{key}",
                    "category": "spending",
                    "severity": "medium",
                    "title": f"Expected charge from {s.merchant} didn't arrive",
                    "detail": (
                        f"Usually {s.typical_amount:,.2f} {s.currency} every "
                        f"{s.cadence.removesuffix('ly')}, due around "
                        f"{s.next_expected}. Cancelled, or paid another way?"
                    ),
                    "link_page": "spending",
                    "context": context,
                }
            )
            continue
        recent = (
            today - date.fromisoformat(s.last_date)
        ).days <= RECURRING_PRICE_RECENT_DAYS
        if (
            s.status == "active"
            and recent
            and s.price_change_pct is not None
            and s.price_change_pct >= RECURRING_PRICE_RISE_PCT
        ):
            items.append(
                {
                    "id": f"recurring:price:{key}:{s.last_date}",
                    "category": "spending",
                    "severity": "low",
                    "title": f"{s.merchant} went up {s.price_change_pct:.0f}%",
                    "detail": (
                        f"Last charge {s.last_amount:,.2f} {s.currency} on "
                        f"{s.last_date}, up from the usual "
                        f"{s.typical_amount:,.2f}."
                    ),
                    "link_page": "spending",
                    "context": context,
                }
            )
    return items
```

`removesuffix("ly")` turns weekly/monthly/quarterly/yearly into week/month/quarter/year, so "every month" reads correctly for all four.

Add `check_recurring_charges,` as the last entry of `_CHECKS`.

Check the frontend accepts a new `category` value: `grep -n "category" web_client/js/pfm_features.js | sed -n '1,400p' | grep -n "action\|stale_imports\|budget" | head`. If Action Items map categories to icons or labels, add a `spending` entry next to `budget`. If there is no mapping, nothing to do.

- [ ] **Step 4: Verify** — the Action Items tests + full unit suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/action_items.py tests/unit/<action items test file>
git commit -m "feat: action item for missed recurring charges and price rises

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 11: Docs, deploy, verify on real data

**Files:**
- Modify: `docs/features/spending.md`, `PROJECT_STATUS.md`, `CLAUDE.md`

- [ ] **Step 1: Docs**

`docs/features/spending.md`, add sections for:
- `merchant` (v32, `normalize_merchant`, stored alongside the description, used for search, rules and recurring)
- `q` on `GET /spending/`
- rule conditions, priority and `POST /rules/preview`, plus the root-mismatch fall-through change
- click-to-edit + rule offer. Also **delete** the sentence "the Transactions tab's category column is no longer inline-editable — bulk-select + "Set category" and this AI-suggest panel remain the only transaction-level recategorization paths"
- `balance_breaks` on `/upload`
- `GET /spending/recurring` and its status rules
- the new Action Item

`PROJECT_STATUS.md`: bump "Last updated" to the deploy date and add a **Recent (v2.5.74)** line. Check the latest version number first with `grep -n "Recent (v" PROJECT_STATUS.md | head -1`.

`CLAUDE.md`:
- "Current schema version: 31" → 32, and add a migration history line: `- v32: spending_transactions.merchant (normalize_merchant, backfilled); spending_rules.portfolio_id/amount_sign/min_amount/max_amount/priority — rules evaluate by priority then id. See docs/features/spending.md.`
- "DB version bump: update every `== 31`" → `== 32`
- Spending row of the Feature reference table, append: `Rules match description OR merchant; a rule whose category root conflicts with the sign is skipped, not a stop. "Missed" recurring needs an import past the due date.`

Run the pre-commit privacy hook on the docs (`git add` then `git commit` runs it). Invented examples only.

- [ ] **Step 2: Full test run**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/integration tests/e2e -q
make test-js
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501
cd ~/mcp/pfm && python3 -m pytest -q . ; cd ~/repos/pfm
```

Expected: all pass, flake8 reports 0. The `~/mcp/pfm` suite matters because `spending_summary` reads `/spending/*` and is exposed via the internet-facing gateway.

- [ ] **Step 3: Commit docs**

```bash
git add docs/features/spending.md PROJECT_STATUS.md CLAUDE.md
git commit -m "docs: spending search, merchant names, smarter rules, recurring, balance check

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

- [ ] **Step 4: Back up the live DB, then deploy**

```bash
docker exec portf_backend_dev sh -c 'cp /app/portfolio.db /app/portfolio.db.pre-v32.bak && ls -la /app/portfolio.db*'
docker compose restart portf_backend_dev
docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web
```

- [ ] **Step 5: Verify on real data (read-only checks; report numbers, don't paste descriptions anywhere committed)**

```bash
docker exec portf_backend_dev python3 -c "
import sqlite3
c=sqlite3.connect('/app/portfolio.db')
print('version', c.execute('select max(version) from database_version').fetchone())
print('null merchants', c.execute('select count(*) from spending_transactions where merchant is null or merchant=\"\"').fetchone())
print('unique descriptions', c.execute('select count(distinct description) from spending_transactions').fetchone())
print('unique merchants', c.execute('select count(distinct upper(merchant)) from spending_transactions').fetchone())
print('uncategorized desc/merchant', c.execute('select count(distinct description), count(distinct upper(merchant)) from spending_transactions where category=\"uncategorized\"').fetchone())
"
KEY=$(grep -E '^SERVER_API_KEY=' .env.local | cut -d= -f2-)
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/v1/spending/?q=a&limit=1" | python3 -c "import json,sys;d=json.load(sys.stdin);print('search total',d['total'])"
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/v1/spending/recurring" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d['items']),'recurring', d['monthly_total_eur'],'EUR/month', [i['status'] for i in d['items']].count('missed'),'missed')"
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/v1/action-items" | python3 -c "import json,sys;d=json.load(sys.stdin);d=d if isinstance(d,list) else d.get('items',d);print([i['id'].split(':')[0:2] for i in d if i['id'].startswith('recurring')])"
```

Expected:
- `version (32,)`, 0 null merchants.
- Unique merchants clearly below unique descriptions (the goal of Task 3 is a several-fold drop in the uncategorized column). Record both numbers in the PROJECT_STATUS line.
- Recurring returns a plausible list.

Check the Action Items path (`grep -n "action" portf_server/app.py`) and adjust the URL if it differs. Then do the manual UI pass from Tasks 5, 6 and 9 in the browser at desktop and 390px width.

If the merchant reduction is weak (< 2×), look at the 20 most common uncategorized merchants locally, then add patterns to `normalize_merchant` **with invented test cases** in `test_merchant.py`. A migration won't re-run on the live DB, so do the backfill directly:
`docker exec portf_backend_dev python3 -c "from portf_manager.database import Database; …"`, which calls `normalize_merchant` for every row.

- [ ] **Step 6: Push**

```bash
GIT_SSH_COMMAND="ssh -o IdentitiesOnly=no" git push origin main
```

(The pre-push hook runs the full unit suite.)
