# AEB43/N43 Bank Statement Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add support for importing AEB43/Norma 43 ("Cuaderno 43") fixed-width
bank statement exports (Caixa Enginyers, Abanca, and any other Spanish bank
using the same national standard) into Spending Tracking, alongside the
existing generic CSV import — with automatic format detection and no new UI.

**Architecture:** A new standalone parser module
(`portf_manager/parsers/aeb43_parser.py`) decodes the fixed-width 80-byte
record format into the same `SpendingRow`/`BankParseResult` shapes the
existing generic CSV parser already produces, including a genuine running
`balance` per row computed from the file's own opening balance. The upload
endpoint sniffs the decoded content and dispatches to this parser instead of
the CSV parser when it detects an AEB43 header record; everything downstream
(categorization, duplicate detection, save) is unchanged.

**Tech Stack:** Python 3.13, pytest, FastAPI (existing `portf_server`
router), no new dependencies.

## Global Constraints

- Code style: **black** (line length 88); comments on the line before the
  code they describe; type hints on all function signatures; Google-style
  docstrings.
- Never commit real personal/financial data — all test fixtures use
  synthetic/fictional data (e.g. "Example Corp", "TEST"), never content from
  the real sample files used to validate the field layout during design.
- `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e` must
  pass after every task.
- `uv run flake8 portf_manager/ portf_server/ --max-line-length=88
  --extend-ignore=E203,W503,E501` must report 0 warnings.
- Pre-commit runs black + flake8 + autoflake automatically on `git commit`.
- Both `PROJECT_STATUS.md` (bump "Last updated" + add a "Recent" line) and
  `CLAUDE.md` (Spending Tracking section) must be updated as part of this
  work — this is a mandatory project convention, not optional polish.
- No restart is needed for pure Python source changes made during
  development/testing via `uv run pytest` — a live-container restart
  (`docker exec portf_backend_dev kill -HUP 1`) is only needed once this
  ships to the running dev container, called out at the end of the plan.

---

## Task 1: AEB43 parser module

**Files:**
- Create: `portf_manager/parsers/aeb43_parser.py`
- Test: `tests/unit/test_aeb43_parser.py`

**Interfaces:**
- Consumes: `SpendingRow`, `BankParseResult` dataclasses from
  `portf_manager/parsers/generic_bank_csv_parser.py` (already defined there:
  `SpendingRow(date: str, description: str, amount: float, currency: str =
  "EUR", balance: Optional[float] = None)`, `BankParseResult(rows:
  List[SpendingRow], skipped: List[Tuple[str, str]])`).
- Produces: `looks_like_aeb43(content: str) -> bool` and
  `parse_aeb43(content: str) -> BankParseResult` — both consumed by Task 2.

- [ ] **Step 1: Write the test fixture helpers and first sniff tests**

Create `tests/unit/test_aeb43_parser.py`:

```python
"""Tests for the AEB43/N43 fixed-width bank statement parser."""

from portf_manager.parsers.aeb43_parser import looks_like_aeb43, parse_aeb43


def _header(
    entidad="1234",
    oficina="0001",
    cuenta="0000000001",
    clave="2",
    importe_cents=0,
    divisa="978",
    nombre="TEST",
):
    """Build a synthetic 80-byte AEB43 header ('11') record."""
    return (
        "11"
        + entidad.zfill(4)
        + oficina.zfill(4)
        + cuenta.zfill(10)
        + "260101"
        + "260101"
        + clave
        + str(importe_cents).zfill(14)
        + divisa
        + "0"
        + nombre.ljust(29)
    )


def _movement(fecha_op="260101", fecha_valor="260101", clave="1", importe_cents=1000):
    """Build a synthetic 80-byte AEB43 movement ('22') record."""
    return (
        "22"
        + "    "
        + "0000"
        + fecha_op
        + fecha_valor
        + "00"
        + "000"
        + clave
        + str(importe_cents).zfill(14)
        + "0".zfill(8)
        + "0".zfill(12)
        + "0".zfill(18)
    )


def _concept(text="", codigo="01"):
    """Build a synthetic 80-byte AEB43 complementary ('23') record."""
    return "23" + codigo + text.ljust(76)[:76]


def _trailer():
    """Build a synthetic 80-byte AEB43 trailer ('33') record (content unused by the parser)."""
    return "33" + " " * 78


def _crlf(*lines: str) -> str:
    return "\r\n".join(lines) + "\r\n"


def test_looks_like_aeb43_true_for_header_record():
    content = _crlf(_header(), _movement(), _concept("Example Corp"), _trailer())
    assert looks_like_aeb43(content) is True


def test_looks_like_aeb43_false_for_csv():
    content = "date,description,amount\n2026-01-05,Example,-10.00\n"
    assert looks_like_aeb43(content) is False


def test_looks_like_aeb43_false_for_empty_content():
    assert looks_like_aeb43("") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: `ModuleNotFoundError: No module named 'portf_manager.parsers.aeb43_parser'`

- [ ] **Step 3: Create the module with `looks_like_aeb43` only**

Create `portf_manager/parsers/aeb43_parser.py`:

```python
"""
AEB43 / Norma 43 ("Cuaderno 43") fixed-width bank statement parser.

National Spanish banking standard used by (at least) Caixa Enginyers and
Abanca as an export format for account movements — an alternative to CSV.
80-byte fixed-width records, CRLF line endings, record type in the first two
characters:

  11  Header        — one per file: opening balance + sign, currency
  22  Movement      — one per transaction: date, debit/credit flag, amount
  23  Complementary — one or more per movement, immediately following it:
                       free-text description
  33  Trailer       — one per file, totals only (not parsed here)

Some exports (Caixa Enginyers) pad every line to a flat 80 bytes; others
(Abanca) strip trailing whitespace, so movement/complementary lines can be
shorter than 80 bytes. Every line is left-padded to 80 bytes with
``ljust(80)`` before any fixed-position slicing, so both export styles parse
identically.

Field layout was reverse-engineered and cross-validated against two real
bank exports: computed debit/credit counts, sums, and running balance all
matched each file's own trailer record to the cent for both banks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .generic_bank_csv_parser import BankParseResult, SpendingRow

_CURRENCY_ISO_NUMERIC = {
    "978": "EUR",
    "840": "USD",
    "826": "GBP",
}


def looks_like_aeb43(content: str) -> bool:
    """Sniff whether `content` is an AEB43/Norma 43 file (vs. delimited CSV).

    Checks the first non-blank line: a real AEB43 header record starts with
    "11" and positions 3-50 (entity/office/account/dates/sign/amount/
    currency) are all digits — a CSV header row never is.

    Args:
        content: Raw decoded file text.

    Returns:
        True if the content looks like an AEB43 header record.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("11"):
            return False
        padded = stripped.ljust(80)
        return padded[2:50].isdigit()
    return False
```

- [ ] **Step 4: Run tests to verify the sniff tests pass**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: 3 passed (the sniff tests); any test calling `parse_aeb43` still
fails with `ImportError` (not written yet — none exist yet at this step).

- [ ] **Step 5: Commit**

```bash
git add portf_manager/parsers/aeb43_parser.py tests/unit/test_aeb43_parser.py
git commit -m "feat: add AEB43/N43 format detection (looks_like_aeb43)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

- [ ] **Step 6: Write failing tests for basic movement parsing**

Append to `tests/unit/test_aeb43_parser.py`:

```python
def test_parses_single_debit_movement_with_description():
    content = _crlf(
        _header(clave="2", importe_cents=100000),  # opening balance 1000.00
        _movement(fecha_op="260105", clave="1", importe_cents=2450),
        _concept("MERCADONA COMPRA"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.date == "2026-01-05"
    assert row.description == "MERCADONA COMPRA"
    assert row.amount == -24.50
    assert row.currency == "EUR"
    assert row.balance == 975.50


def test_parses_credit_movement():
    content = _crlf(
        _header(clave="2", importe_cents=0),
        _movement(fecha_op="260106", clave="2", importe_cents=210000),
        _concept("NOMINA EMPRESA SL"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].amount == 2100.00
    assert result.rows[0].balance == 2100.00


def test_negative_opening_balance_seed():
    content = _crlf(
        _header(clave="1", importe_cents=50000),  # opening balance -500.00
        _movement(fecha_op="260101", clave="2", importe_cents=20000),
        _concept("EXAMPLE DEPOSIT"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].balance == -300.00


def test_missing_header_record_returns_skip():
    result = parse_aeb43("not an aeb43 file\r\n")
    assert result.rows == []
    assert result.skipped[0][0] == "file"


def test_empty_content():
    result = parse_aeb43("")
    assert result.rows == []
    assert result.skipped[0][0] == "file"
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: `ImportError: cannot import name 'parse_aeb43'`

- [ ] **Step 8: Implement `parse_aeb43` (header + single movement + description)**

Append to `portf_manager/parsers/aeb43_parser.py`:

```python
def _aammdd_to_iso(raw: str) -> Optional[str]:
    """Convert an AAMMDD date field to YYYY-MM-DD, or None if invalid."""
    try:
        return datetime.strptime(raw, "%y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_aeb43(content: str) -> BankParseResult:
    """Parse AEB43/Norma 43 fixed-width content into signed SpendingRow objects.

    Args:
        content: Raw AEB43 text (already decoded).

    Returns:
        BankParseResult with parsed rows and skipped records with reasons.
    """
    result = BankParseResult()
    lines = [ln.rstrip("\r\n").ljust(80) for ln in content.splitlines() if ln.strip()]

    if not lines or not lines[0].startswith("11"):
        result.skipped.append(
            ("file", "Not a valid AEB43 file: missing header record")
        )
        return result

    header = lines[0]
    clave_inicial = header[32]
    saldo_inicial = int(header[33:47]) / 100
    currency = _CURRENCY_ISO_NUMERIC.get(header[47:50], "EUR")
    balance = saldo_inicial if clave_inicial == "2" else -saldo_inicial

    i = 1
    while i < len(lines):
        line = lines[i]
        if line[:2] != "22":
            i += 1
            continue

        fecha_op = _aammdd_to_iso(line[10:16])
        clave = line[27]
        importe = int(line[28:42]) / 100

        j = i + 1
        desc_parts = []
        while j < len(lines) and lines[j][:2] == "23":
            desc_parts.append(lines[j][4:80].rstrip())
            j += 1
        description = " ".join(p for p in desc_parts if p).strip()

        if clave not in ("1", "2"):
            result.skipped.append(
                (f"record {i + 1}", f"Unknown debit/credit flag: {clave!r}")
            )
            i = j
            continue
        if fecha_op is None:
            result.skipped.append((f"record {i + 1}", "Invalid operation date"))
            i = j
            continue

        signed_amount = importe if clave == "2" else -importe
        balance += signed_amount

        if importe == 0:
            result.skipped.append((f"record {i + 1}", "Amount is zero"))
            i = j
            continue

        result.rows.append(
            SpendingRow(
                date=fecha_op,
                description=description,
                amount=signed_amount,
                currency=currency,
                balance=round(balance, 2),
            )
        )
        i = j

    return result
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: all tests pass (8 total so far).

- [ ] **Step 10: Commit**

```bash
git add portf_manager/parsers/aeb43_parser.py tests/unit/test_aeb43_parser.py
git commit -m "feat: parse AEB43 header + movement records into SpendingRow

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

- [ ] **Step 11: Write failing tests for multi-line descriptions and trimmed-line exports**

Append to `tests/unit/test_aeb43_parser.py`:

```python
def test_multiple_complementary_lines_concatenated():
    content = _crlf(
        _header(),
        _movement(clave="1", importe_cents=5000),
        _concept("TRANSFERENCIA A:", codigo="01"),
        _concept("Example Person", codigo="02"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].description == "TRANSFERENCIA A: Example Person"


def test_trailing_whitespace_trimmed_lines_parse_same_as_full_width():
    # Real AEB43 exports (e.g. Abanca) omit trailing padding spaces per
    # line, so movement/complementary records can be shorter than 80 bytes.
    trimmed_movement = _movement(clave="1", importe_cents=1500)[:74]
    trimmed_concept = "2301R/ EXAMPLE CHARITY"
    content = _crlf(_header(), trimmed_movement, trimmed_concept, _trailer())
    result = parse_aeb43(content)
    assert len(result.rows) == 1
    assert result.rows[0].amount == -15.00
    assert result.rows[0].description == "R/ EXAMPLE CHARITY"
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: both new tests pass immediately — the padding (`ljust(80)`) and
multi-record consumption logic from Step 8 already handle these cases with
no further implementation change. This step exists to prove that, not to
add code.

If either test fails, re-check the slicing offsets in `parse_aeb43` against
the field layout table in the module docstring before changing anything
else.

- [ ] **Step 13: Commit**

```bash
git add tests/unit/test_aeb43_parser.py
git commit -m "test: cover multi-line descriptions and trimmed-line AEB43 exports

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

- [ ] **Step 14: Write failing tests for currency mapping, invalid flags, and zero-amount handling**

Append to `tests/unit/test_aeb43_parser.py`:

```python
def test_mapped_currency_code_translated():
    content = _crlf(
        _header(divisa="840"),
        _movement(clave="2", importe_cents=1000),
        _concept("USD DEPOSIT"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].currency == "USD"


def test_unknown_currency_code_falls_back_to_eur():
    content = _crlf(
        _header(divisa="999"),
        _movement(clave="2", importe_cents=1000),
        _concept("UNKNOWN CCY"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].currency == "EUR"


def test_invalid_debit_credit_flag_skipped():
    bad_movement = _movement(clave="9", importe_cents=1000)
    content = _crlf(_header(), bad_movement, _concept("BAD FLAG"), _trailer())
    result = parse_aeb43(content)
    assert result.rows == []
    assert any("debit/credit" in reason.lower() for _, reason in result.skipped)


def test_zero_amount_movement_skipped_but_running_balance_unaffected():
    content = _crlf(
        _header(clave="2", importe_cents=100000),
        _movement(fecha_op="260101", clave="2", importe_cents=0),
        _concept("APERTURA CUENTA"),
        _movement(fecha_op="260102", clave="1", importe_cents=2000),
        _concept("EXAMPLE PAYMENT"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert len(result.rows) == 1
    assert result.rows[0].description == "EXAMPLE PAYMENT"
    assert result.rows[0].balance == 980.00
```

- [ ] **Step 15: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: all pass — this exercises existing logic from Step 8 (the
`_CURRENCY_ISO_NUMERIC` lookup, the `clave not in ("1", "2")` branch, and the
`importe == 0` branch) with no further implementation change needed. If any
fails, fix `parse_aeb43` (not the test) to match the behavior documented in
Step 8's code.

- [ ] **Step 16: Run the full parser test file and commit**

Run: `uv run pytest tests/unit/test_aeb43_parser.py -v`
Expected: 14 passed.

```bash
git add tests/unit/test_aeb43_parser.py
git commit -m "test: cover AEB43 currency mapping, invalid flags, zero-amount rows

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Wire AEB43 detection into the Spending upload endpoint

**Files:**
- Modify: `portf_server/routers/spending.py:153-181` (`upload_bank_statement`)
- Test: `tests/unit/test_spending_api.py`

**Interfaces:**
- Consumes: `looks_like_aeb43(content: str) -> bool`,
  `parse_aeb43(content: str) -> BankParseResult` from Task 1
  (`portf_manager.parsers.aeb43_parser`).
- Produces: no new interface — `upload_bank_statement`'s request/response
  shape is unchanged; only its internal parser dispatch changes.

- [ ] **Step 1: Write failing integration tests**

Add to `tests/unit/test_spending_api.py` (near the other upload tests, e.g.
after `test_upload_preview_balance_none_when_absent`):

```python
def _aeb43_bytes(
    description: str, amount_cents: int, clave: str = "1", balance_cents: int = 100000
) -> bytes:
    """Build a minimal single-movement AEB43 file, encoded as Latin-1 bytes
    (real AEB43 exports are commonly Latin-1, not UTF-8)."""
    header = (
        "11"
        + "1234"
        + "0001"
        + "0000000001"
        + "260101"
        + "260101"
        + "2"
        + str(balance_cents).zfill(14)
        + "978"
        + "0"
        + "TEST".ljust(29)
    )
    movement = (
        "22"
        + "    "
        + "0000"
        + "260105"
        + "260105"
        + "00"
        + "000"
        + clave
        + str(amount_cents).zfill(14)
        + "0".zfill(8)
        + "0".zfill(12)
        + "0".zfill(18)
    )
    concept = "23" + "01" + description.ljust(76)[:76]
    trailer = "33" + " " * 78
    text = "\r\n".join([header, movement, concept, trailer]) + "\r\n"
    return text.encode("latin-1")


def test_upload_detects_aeb43_and_computes_balance(tmp_path):
    client, _ = _make_client(tmp_path)
    file_bytes = _aeb43_bytes("MERCADONA COMPRA", 2450, clave="1", balance_cents=100000)
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.n43", io.BytesIO(file_bytes), "text/plain")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert len(d["rows"]) == 1
    assert d["rows"][0]["amount"] == -24.50
    assert d["rows"][0]["balance"] == 975.50


def test_upload_aeb43_latin1_bytes_decoded_without_error(tmp_path):
    client, _ = _make_client(tmp_path)
    file_bytes = _aeb43_bytes("TRANSFERENCIA A: José González", 5000, clave="1")
    r = client.post(
        "/api/v1/spending/upload",
        data={"account_name": "Example Bank"},
        files={"file": ("statement.n43", io.BytesIO(file_bytes), "text/plain")},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["rows"][0]["description"] == "TRANSFERENCIA A: José González"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_spending_api.py -k aeb43 -v`
Expected: `test_upload_detects_aeb43_and_computes_balance` fails because the
endpoint still routes everything through `parse_generic_bank_csv`, which has
no `date`/`description`/`amount` header columns to match in fixed-width
content, so it returns 0 rows (assertion `len(d["rows"]) == 1` fails).
`test_upload_aeb43_latin1_bytes_decoded_without_error` fails with a 422
(`UnicodeDecodeError` surfaced as "Failed to parse file") because
`.decode("utf-8-sig")` raises on the Latin-1 `é`/`á`/`ó` bytes.

- [ ] **Step 3: Read the current endpoint code**

Read `portf_server/routers/spending.py` lines 153-181 to confirm the exact
current text of `upload_bank_statement` before editing (shown here for
reference — do not skip re-reading the live file, since line numbers may
have shifted since this plan was written):

```python
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
```

- [ ] **Step 4: Add the import**

In `portf_server/routers/spending.py`, immediately after the existing line
`from portf_manager.parsers.generic_bank_csv_parser import
parse_generic_bank_csv`, add:

```python
from portf_manager.parsers.aeb43_parser import looks_like_aeb43, parse_aeb43
```

- [ ] **Step 5: Update the decode + dispatch logic**

In `portf_server/routers/spending.py`, replace:

```python
    file_bytes = await file.read()
    try:
        content = file_bytes.decode("utf-8-sig")
        result = parse_generic_bank_csv(content)
    except Exception as e:
```

with:

```python
    file_bytes = await file.read()
    try:
        # AEB43 exports commonly contain raw Latin-1 bytes (accented
        # characters); fall back when the file isn't valid UTF-8.
        try:
            content = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = file_bytes.decode("latin-1")
        if looks_like_aeb43(content):
            result = parse_aeb43(content)
        else:
            result = parse_generic_bank_csv(content)
    except Exception as e:
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/test_spending_api.py -k aeb43 -v`
Expected: 2 passed.

- [ ] **Step 7: Run the full spending test file to check for regressions**

Run: `uv run pytest tests/unit/test_spending_api.py -v`
Expected: all tests pass (existing CSV upload tests unaffected — plain CSV
content never starts with a numeric "11" header, so `looks_like_aeb43`
returns `False` for every existing fixture and the `parse_generic_bank_csv`
branch runs exactly as before).

- [ ] **Step 8: Run the full unit suite**

Run: `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all tests pass, no regressions elsewhere.

- [ ] **Step 9: Lint check**

Run: `uv run flake8 portf_manager/ portf_server/ --max-line-length=88 --extend-ignore=E203,W503,E501`
Expected: 0 warnings. If `spending.py` reports any, run `uv run black
portf_server/routers/spending.py portf_manager/parsers/aeb43_parser.py` and
re-check.

- [ ] **Step 10: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: auto-detect AEB43/N43 bank statements on Spending upload

Falls back to Latin-1 decoding when a statement isn't valid UTF-8 —
AEB43 exports commonly contain raw Latin-1 bytes for accented text.
Non-AEB43 uploads (the existing generic CSV path) are unaffected.

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: Documentation updates

**Files:**
- Modify: `CLAUDE.md` (Spending Tracking section)
- Modify: `PROJECT_STATUS.md` (header date + new "Recent" line)

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing consumed by later tasks — this is the final task.

- [ ] **Step 1: Update CLAUDE.md**

In `/home/agoldhoorn/repos/pfm/CLAUDE.md`, find the "Spending Tracking"
section's bullet that starts with `- **Deferred**: dedicated parsers for
Abanca, Caixa Enginyers, Revolut, and MyInvestor-cash...` and replace it
with:

```markdown
- `POST /api/v1/spending/upload` auto-detects **AEB43/Norma 43** ("Cuaderno
  43") fixed-width bank exports — a Spanish national standard offered by
  Caixa Enginyers and Abanca as an alternative to CSV — via
  `portf_manager/parsers/aeb43_parser.py` (`looks_like_aeb43`/`parse_aeb43`).
  No new API param or UI control: the endpoint decodes the upload (falling
  back to Latin-1 when the bytes aren't valid UTF-8 — common for AEB43
  exports) and sniffs whether the first record is a valid AEB43 header
  before choosing between `parse_aeb43` and the generic CSV parser. Unlike
  the generic CSV parser, AEB43 exports carry a genuine per-row running
  `balance`, computed from the file's own opening-balance record — feeding
  directly into the Net Worth "bank balance" derivation (see Net Worth API
  section). Bank-agnostic by construction (validated against real Caixa
  Enginyers and Abanca exports with zero bank-specific branching), so it
  should also cover any other bank using the same national standard.
  **Deferred**: dedicated parsers for Revolut and MyInvestor-cash (real
  export column layouts not yet available) — the generic CSV parser covers
  them for now.
```

- [ ] **Step 2: Update PROJECT_STATUS.md**

In `/home/agoldhoorn/repos/pfm/PROJECT_STATUS.md`, change line 8 from:

```
Last updated: 2026-07-20
```

to:

```
Last updated: 2026-07-21
```

Then insert a new line immediately after line 8 (before the existing
`**Recent (v2.5.21):**` line):

```markdown

**Recent (v2.5.22):** **AEB43/N43 fixed-width bank statement import.** Spending Tracking's upload endpoint now auto-detects AEB43/Norma 43 ("Cuaderno 43") exports — the Spanish national fixed-width bank-statement standard offered by Caixa Enginyers and Abanca as an alternative to CSV — via a new `aeb43_parser.py`, with zero new UI/API surface (content-sniffed, falls back to the existing generic CSV parser for everything else). Unlike CSV imports, AEB43 exports carry a genuine per-row running balance computed from the file's own opening-balance record. Field layout was reverse-engineered and validated against two independent real bank exports (debit/credit counts, sums, and computed running balance all matched each file's own trailer record to the cent). Upload decoding also gained a Latin-1 fallback for non-UTF-8 statement files, fixing a latent bug that would have rejected both real AEB43 exports outright.
```

- [ ] **Step 3: Verify no code changes are needed for this step**

This task is docs-only — no test run or restart required. Confirm with:

Run: `git diff --stat CLAUDE.md PROJECT_STATUS.md`
Expected: both files show changes, no other files touched.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document AEB43/N43 bank statement import support

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## After this plan ships

Per the project's restart table: `portf_server`/`portf_manager` Python
changes need `docker exec portf_backend_dev kill -HUP 1` to take effect in
the running dev container (gunicorn has no autoreload). No web/frontend
files were touched, so no `docker compose build web` step is needed. This
step is **not** part of the plan's tasks — call it out to the user
separately once implementation is verified, since it depends on the live
container being up, which isn't guaranteed during plan execution.
