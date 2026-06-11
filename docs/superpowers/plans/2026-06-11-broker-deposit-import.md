# Broker Deposit/Withdrawal Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture cash deposits/withdrawals on file import for Coinbase (fiat only) and Mintos, which currently drop them.

**Architecture:** Each parser emits a `bookings` list of `{date, action, amount, currency}` dicts (the pattern `parse_myinvestor_csv` already uses); the import upload endpoint wraps them in `PreviewBooking(broker=...)` and the existing save path resolves broker→portfolio and dedups them.

**Tech Stack:** Python 3.13, pytest. Run tooling with `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run ...` (root-owned .venv). Pre-commit runs black/flake8/autoflake on `git commit`; if a hook reformats and aborts, re-stage and re-commit.

Spec: `docs/superpowers/specs/2026-06-11-broker-deposit-import-design.md`

---

### Task 1: Coinbase parser — emit fiat deposit/withdrawal bookings

**Files:**
- Modify: `portf_manager/parsers/coinbase_csv_parser.py`
- Test: `tests/test_coinbase_parser.py` (new)

- [ ] **Step 1: Write the failing test.** Create `tests/test_coinbase_parser.py`:

```python
"""Unit tests for the Coinbase CSV parser (synthetic data)."""

from portf_manager.parsers.coinbase_csv_parser import parse_coinbase_csv

# Real Coinbase export shape: 2 preamble lines, then the CSV header, then rows.
HEADER = (
    "Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,"
    "Total (inclusive of fees and/or spread),Notes"
)
CSV = "Transactions\nuser@example.com\n" + HEADER + "\n" + "\n".join(
    [
        "2025-08-25 20:34:04 UTC,Buy,BTC,0.001,EUR,60000,60.50,bought",
        "2025-08-20 10:00:00 UTC,Deposit,EUR,500,EUR,,500,sepa in",
        "2025-08-22 11:00:00 UTC,Receive,BTC,0.002,EUR,,120,received btc",
        "2025-08-26 09:00:00 UTC,Withdrawal,EUR,200,EUR,,200,withdrew",
    ]
)


def test_fiat_deposit_and_withdrawal_become_bookings():
    r = parse_coinbase_csv(CSV)
    actions = {(b["action"], b["amount"], b["currency"]) for b in r.bookings}
    assert ("Deposit", 500.0, "EUR") in actions
    assert ("Withdrawal", 200.0, "EUR") in actions
    assert len(r.bookings) == 2
    # The booking date is the calendar date of the row.
    dep = next(b for b in r.bookings if b["action"] == "Deposit")
    assert dep["date"] == "2025-08-20"


def test_trade_still_imported_and_crypto_transfer_skipped():
    r = parse_coinbase_csv(CSV)
    # The Buy is still an importable trade.
    assert [t.symbol for t in r.importable] == ["BTC"]
    # The crypto Receive is skipped, NOT a booking.
    assert any(t == "Receive" for t, _reason in r.skipped)
    assert all(b["currency"] == "EUR" for b in r.bookings)  # no crypto bookings
```

- [ ] **Step 2: Run to verify it FAILS:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_coinbase_parser.py -v`
Expected: FAIL — `CoinbaseParseResult` has no attribute `bookings` (and currently the Deposit/Withdrawal rows are skipped).

- [ ] **Step 3: Add the `bookings` field + classification constants.** In `coinbase_csv_parser.py`:

First ensure `field` is imported. The top of the file has `from dataclasses import dataclass` — change it to:
```python
from dataclasses import dataclass, field
```

Add `bookings` to the dataclass:
```python
@dataclass
class CoinbaseParseResult:
    """Result of parsing Coinbase CSV data."""

    importable: List[LLMTransaction]
    skipped: List[Tuple[str, str]]  # (transaction_type, reason)
    bookings: List[dict] = field(default_factory=list)  # cash deposits/withdrawals
```

Add these class constants to `CoinbaseCSVParser` (next to `REFERENCE_TYPES`):
```python
    # Cash-movement types. Only count as bookings when the Asset is fiat;
    # the crypto-asset variants are wallet transfers, left in `skipped`.
    DEPOSIT_TYPES = {"Deposit", "Pro Deposit"}
    WITHDRAWAL_TYPES = {"Withdrawal", "Pro Withdrawal"}
    FIAT_CURRENCIES = {
        "EUR", "USD", "GBP", "CHF", "SEK", "DKK", "NOK", "JPY", "CAD", "AUD",
    }
```

- [ ] **Step 4: Add the booking-row parser + wire it into the loop.** Add this method to `CoinbaseCSVParser` (e.g. right after `_parse_transaction_row`):

```python
    def _parse_booking_row(self, row: dict) -> Optional[dict]:
        """Return a cash booking dict for a fiat Deposit/Withdrawal row, else None.

        Crypto-asset deposits/withdrawals (Asset not a fiat currency) return
        None so they fall through to the normal skip path — they are wallet
        transfers, not cash bookings.
        """
        tx_type = row.get("Transaction Type", "").strip()
        is_deposit = tx_type in self.DEPOSIT_TYPES
        is_withdrawal = tx_type in self.WITHDRAWAL_TYPES
        if not (is_deposit or is_withdrawal):
            return None
        asset = row.get("Asset", "").strip().upper()
        if asset not in self.FIAT_CURRENCIES:
            return None
        # Amount: the fiat Quantity Transacted is the cash amount; fall back to
        # the Total column if blank. Strip currency symbols/grouping.
        raw = row.get("Quantity Transacted", "").strip() or row.get(
            "Total (inclusive of fees and/or spread)", ""
        ).strip()
        cleaned = raw.replace("€", "").replace("$", "").replace(",", "")
        try:
            amount = abs(float(cleaned))
        except ValueError:
            return None
        if amount == 0:
            return None
        return {
            "date": row.get("Timestamp", "").strip()[:10],
            "action": "Deposit" if is_deposit else "Withdrawal",
            "amount": amount,
            "currency": asset,
        }
```

In `parse_csv_content`, inside the `for row in csv_reader:` loop, add the booking check **before** the existing `_parse_transaction_row` call, and collect bookings. The loop body becomes:

```python
            for row in csv_reader:
                try:
                    booking = self._parse_booking_row(row)
                    if booking:
                        bookings.append(booking)
                        continue
                    parsed_transaction = self._parse_transaction_row(row)
                    if parsed_transaction:
                        importable.append(parsed_transaction)
                    else:
                        # Transaction was skipped
                        tx_type = row.get("Transaction Type", "Unknown")
                        skipped.append((tx_type, self._get_skip_reason(tx_type)))

                except Exception as e:
                    # Log individual row parsing errors
                    tx_type = row.get("Transaction Type", "Unknown")
                    skipped.append((tx_type, f"Parse error: {str(e)}"))
```

Add `bookings = []` next to the existing `importable = []` / `skipped = []` initialisation, and update the return:
```python
        return CoinbaseParseResult(
            importable=importable, skipped=skipped, bookings=bookings
        )
```

(`Optional` is already imported in this file.)

- [ ] **Step 5: Run to verify it PASSES:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_coinbase_parser.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit:**
```bash
git add portf_manager/parsers/coinbase_csv_parser.py tests/test_coinbase_parser.py
git commit -m "feat(coinbase): parse fiat deposit/withdrawal rows as bookings"
```

---

### Task 2: Coinbase router wiring — return bookings from `_parse_coinbase`

**Files:**
- Modify: `portf_server/routers/imports.py` (`_parse_coinbase` ~216–234; upload dispatch ~471–474)
- Test: `tests/unit/test_imports_exports.py` (`test_upload_coinbase` ~155; add one new test)

- [ ] **Step 1: Write the failing test.** In `tests/unit/test_imports_exports.py`, add this test next to `test_upload_coinbase` (it uploads a real CSV with a deposit — exercises parser + wiring):

```python
    @pytest.mark.asyncio
    async def test_upload_coinbase_deposit_booking(
        self, async_test_client: AsyncClient, auth_headers
    ):
        csv = (
            "Transactions\nuser@example.com\n"
            "Timestamp,Transaction Type,Asset,Quantity Transacted,"
            "Price Currency,Price at Transaction,"
            "Total (inclusive of fees and/or spread),Notes\n"
            "2025-08-20 10:00:00 UTC,Deposit,EUR,500,EUR,,500,sepa in\n"
        )
        response = await async_test_client.post(
            "/api/v1/import/upload",
            headers=auth_headers,
            data={"broker": "coinbase"},
            files={"file": ("cb.csv", csv.encode(), "text/csv")},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["bookings"]) == 1
        bk = data["bookings"][0]
        assert bk["action"] == "Deposit"
        assert bk["amount"] == 500.0
        assert bk["currency"] == "EUR"
        assert bk["broker"] == "Coinbase"
```

- [ ] **Step 2: Run to verify it FAILS:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest "tests/unit/test_imports_exports.py::TestImportUpload::test_upload_coinbase_deposit_booking" -v`
Expected: FAIL — `_parse_coinbase` discards bookings (`bookings = []` in the dispatch), so `data["bookings"]` is empty.

- [ ] **Step 3: Make `_parse_coinbase` return bookings.** In `portf_server/routers/imports.py`, change the `_parse_coinbase` signature + body:

```python
def _parse_coinbase(
    content: str,
) -> tuple[List[PreviewTransaction], List[PreviewBooking], List[dict]]:
    result = parse_coinbase_csv(content)
    previews = [
        PreviewTransaction(
            symbol=tx.symbol,
            name=tx.asset_name,
            asset_type="crypto",
            tx_type=tx.tx_type,
            date=tx.date,
            quantity=tx.quantity,
            price=tx.price,
            currency=tx.currency or "USD",
            fees=tx.fees,
            notes=tx.raw_text or "",
            # Tag with the broker so the save step resolves the Coinbase
            # portfolio (get_or_create_portfolio). Without this the rows save
            # with portfolio_id=NULL and vanish under the broker filter — the
            # other parsers all set this; Coinbase was the omission.
            broker="Coinbase",
        )
        for tx in result.importable
    ]
    bookings = [PreviewBooking(broker="Coinbase", **bk) for bk in result.bookings]
    skipped = [{"type": t, "reason": r} for t, r in result.skipped]
    return previews, bookings, skipped
```

- [ ] **Step 4: Update the upload dispatch.** In the same file, the coinbase branch currently reads:
```python
        elif broker == "coinbase":
            content = file_bytes.decode("utf-8-sig")
            previews, skipped = _parse_coinbase(content)
            bookings = []
```
Change it to:
```python
        elif broker == "coinbase":
            content = file_bytes.decode("utf-8-sig")
            previews, bookings, skipped = _parse_coinbase(content)
```

- [ ] **Step 5: Fix the existing mocked test.** `test_upload_coinbase` mocks `parse_coinbase_csv` with a `MagicMock` whose `.bookings` is now read by `_parse_coinbase`. Set it to an empty list so `PreviewBooking(**bk)` isn't called on a MagicMock. In `test_upload_coinbase`, after `mock_result.skipped = [("Send", "non-trade")]`, add:
```python
        mock_result.bookings = []
```

- [ ] **Step 6: Run to verify PASS (both tests + full import suite):**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_imports_exports.py -v`
Expected: PASS (all import tests, including the new one).

- [ ] **Step 7: Commit:**
```bash
git add portf_server/routers/imports.py tests/unit/test_imports_exports.py
git commit -m "feat(import): wire Coinbase deposit/withdrawal bookings through upload"
```

---

### Task 3: Mintos parser — emit deposit/withdrawal bookings

**Files:**
- Modify: `portf_manager/parsers/mintos_csv_parser.py`
- Test: `tests/test_mintos_parser.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_mintos_parser.py`:

```python
DEPOSIT_SAMPLE = HEADER + "\n".join(
    [
        '"2025-11-02 09:00:00",d1,"Ingreso de fondos",100.00,100.00,EUR,"Depósito"',
        '"2025-11-03 02:52:28",i1,"Préstamo X Intereses recibidos",0.10,100.10,EUR,"Intereses recibidos"',
        '"2025-11-20 09:00:00",w1,"Retirada de fondos",-40.00,60.10,EUR,"Retirada de fondos"',
    ]
)


def test_deposits_and_withdrawals_become_bookings():
    r = parse_mintos_csv(DEPOSIT_SAMPLE)
    pairs = {(b["action"], b["amount"], b["currency"]) for b in r.bookings}
    assert ("Deposit", 100.0, "EUR") in pairs
    assert ("Withdrawal", 40.0, "EUR") in pairs
    assert len(r.bookings) == 2
    # Interest is still aggregated, unaffected by the new booking rows.
    assert any(e["amount"] == 0.10 for e in r.interest)
```

- [ ] **Step 2: Run to verify it FAILS:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_mintos_parser.py::test_deposits_and_withdrawals_become_bookings -v`
Expected: FAIL — `MintosParseResult` has no attribute `bookings`.

- [ ] **Step 3: Add the `bookings` field.** In `mintos_csv_parser.py`, update the dataclass:
```python
@dataclass
class MintosParseResult:
    # one dict per month: {date, amount, tax, count, currency}
    interest: List[dict] = field(default_factory=list)
    # one dict per cash deposit/withdrawal: {date, action, amount, currency}
    bookings: List[dict] = field(default_factory=list)
    # {payment_type: (row_count, summed_eur)} for the rows we skipped
    ignored_summary: Dict[str, Tuple[int, float]] = field(default_factory=dict)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
```

- [ ] **Step 4: Classify deposit/withdrawal rows.** In `parse_mintos_csv`, the row loop currently ends with an `else:` that aggregates ignored rows:
```python
        else:
            agg = ignored.setdefault(ptype or "(unknown)", [0, 0.0])
            agg[0] += 1
            agg[1] += amt
```
Replace that `else:` block with a deposit/withdrawal check first, then the ignored fallback:
```python
        elif any(k in low for k in ("depósit", "deposit", "ingreso", "incoming client")):
            res.bookings.append(
                {
                    "date": date,
                    "action": "Deposit",
                    "amount": abs(amt),
                    "currency": cur,
                }
            )
        elif any(k in low for k in ("retirada", "withdrawal", "outgoing")):
            res.bookings.append(
                {
                    "date": date,
                    "action": "Withdrawal",
                    "amount": abs(amt),
                    "currency": cur,
                }
            )
        else:
            agg = ignored.setdefault(ptype or "(unknown)", [0, 0.0])
            agg[0] += 1
            agg[1] += amt
```
NOTE: the interest/withholding branches (`"retenci"`, `"interes"`) come first and are unchanged, so interest rows never reach this classification. The keyword set is the spec's documented assumption — confirm against a real Mintos statement; unmatched types still land in `ignored_summary` (fail-safe).

- [ ] **Step 5: Run to verify PASS:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/test_mintos_parser.py -v`
Expected: PASS (all Mintos tests, including the new one).

- [ ] **Step 6: Commit:**
```bash
git add portf_manager/parsers/mintos_csv_parser.py tests/test_mintos_parser.py
git commit -m "feat(mintos): parse deposit/withdrawal rows as bookings"
```

---

### Task 4: Mintos router wiring — return bookings from `_parse_mintos`

**Files:**
- Modify: `portf_server/routers/imports.py` (`_parse_mintos` ~277–313)
- Test: `tests/unit/test_imports_exports.py` (add one test)

- [ ] **Step 1: Write the failing test.** In `tests/unit/test_imports_exports.py`, inside `class TestImportUpload`, add:

```python
    @pytest.mark.asyncio
    async def test_upload_mintos_deposit_booking(
        self, async_test_client: AsyncClient, auth_headers
    ):
        csv = (
            "Fecha,Identificación de la operación:,Detalles,"
            "Volumen de negocios,Saldo,Divisa,Tipo de pago\n"
            '"2025-11-02 09:00:00",d1,"Ingreso de fondos",100.00,100.00,EUR,"Depósito"\n'
        )
        response = await async_test_client.post(
            "/api/v1/import/upload",
            headers=auth_headers,
            data={"broker": "mintos"},
            files={"file": ("mintos.csv", csv.encode(), "text/csv")},
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["bookings"]) == 1
        bk = data["bookings"][0]
        assert bk["action"] == "Deposit"
        assert bk["amount"] == 100.0
        assert bk["broker"] == "Mintos"
```

- [ ] **Step 2: Run to verify it FAILS:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest "tests/unit/test_imports_exports.py::TestImportUpload::test_upload_mintos_deposit_booking" -v`
Expected: FAIL — `_parse_mintos` returns `[]` for bookings, so `data["bookings"]` is empty.

- [ ] **Step 3: Make `_parse_mintos` return bookings.** In `portf_server/routers/imports.py`, the `_parse_mintos` function ends with:
```python
    return previews, [], skipped
```
Just before the return, build the bookings list, and return it:
```python
    bookings = [PreviewBooking(broker="Mintos", **bk) for bk in result.bookings]
    return previews, bookings, skipped
```

- [ ] **Step 4: Run to verify PASS:**
`UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_imports_exports.py -v`
Expected: PASS.

- [ ] **Step 5: Commit:**
```bash
git add portf_server/routers/imports.py tests/unit/test_imports_exports.py
git commit -m "feat(import): wire Mintos deposit/withdrawal bookings through upload"
```

---

### Task 5: Verify end-to-end + update docs

**Files:**
- Modify: `CLAUDE.md` (CSV / File Parsers section)

- [ ] **Step 1: Full backend suite + lint:**
```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black --check .
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run ruff check portf_manager portf_server
```
Expected: all pass — **435 passed** (430 prior + 5 new: 2 Coinbase parser, 1 Coinbase upload, 1 Mintos parser, 1 Mintos upload), 6 skipped; black + ruff clean.

- [ ] **Step 2: Update CLAUDE.md.** In the `### CSV / File Parsers` section, update the Coinbase and Mintos bullets to note deposit/withdrawal support. Find:
```
- `coinbase_csv_parser.py` — Coinbase Advanced Trade CSV export
```
Replace with:
```
- `coinbase_csv_parser.py` — Coinbase Advanced Trade CSV export. Fiat `Deposit`/`Withdrawal` rows (Asset in a fiat allowlist) → deposit/withdrawal bookings; crypto `Send`/`Receive` stay skipped. `_parse_coinbase` returns `(previews, bookings, skipped)`.
```
And update the Mintos bullet (the line starting `` - `mintos_csv_parser.py` ``) by appending to it:
```
 Deposit/withdrawal rows (`Tipo de pago` deposit/withdrawal keywords) → bookings (kept individual, not aggregated).
```

- [ ] **Step 3: Commit:**
```bash
git add CLAUDE.md
git commit -m "docs: note Coinbase/Mintos deposit-import support in CLAUDE.md"
```

---

## Notes for the implementer
- Run all Python tooling with the `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv` prefix.
- The booking `date` is just the calendar date (`Timestamp[:10]` for Coinbase, `Fecha[:10]` for Mintos) — bookings don't need a time component.
- `PreviewBooking` fields: `broker, date, action, amount, currency, is_duplicate` (is_duplicate defaults). Parser dicts intentionally omit `broker`; the router adds it via `PreviewBooking(broker="...", **bk)`.
- Do not aggregate Mintos deposits (kept individual per the spec).
- The backend dev container bind-mounts the repo with `--reload`, so the fix is live on save; no rebuild needed for these backend-only changes.
