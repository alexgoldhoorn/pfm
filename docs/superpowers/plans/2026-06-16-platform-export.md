# Platform Export (Yahoo Finance + Simply Wall St) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CSV export for Yahoo Finance and Simply Wall St to the Import/Export page, generating platform-specific files from transaction history or current positions.

**Architecture:** A new pure-Python module (`portf_manager/platform_export.py`) handles all CSV generation logic with a dedicated SQL query that joins `a.ticker` (not in the shared `_TX_COLS`). Two thin FastAPI endpoints wrap it. The web UI adds a Platform Export card with a `fetch()`-based download that reads response headers to surface skipped-asset warnings.

**Tech Stack:** Python 3.13, FastAPI, SQLite, Vanilla JS + Bootstrap 5.3.

## Global Constraints

- Black formatting, line length 88. Run `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black <file>` after editing Python.
- All Python function signatures must have type hints.
- Test runner: `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_platform_export.py -v`
- Full test suite (pre-push hook): `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
- Web deploy after any `web_client/` change: `docker compose build web && docker stop portf_web && docker compose up -d web`
- Commit messages: conventional commits (`feat:`, `fix:`, `docs:`). Co-author: `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Do NOT modify `_TX_COLS` in `database.py` — use a dedicated query in `platform_export.py`.
- No new migrations needed — this feature only reads existing data.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `portf_manager/platform_export.py` | Create | CSV generation: `_is_isin`, `_resolve_ticker`, `_fetch_buy_sell_txs`, `build_yahoo_finance_csv`, `build_simply_wall_st_csv` |
| `tests/unit/test_platform_export.py` | Create | Unit tests for all export functions |
| `portf_server/routers/exports.py` | Modify | Add `GET /yahoo-finance` and `GET /simply-wall-st` endpoints |
| `web_client/index.html` | Modify | Add Platform Export card HTML (after existing Export card, before Bookings card) |
| `web_client/js/pfm_features.js` | Modify | Add Platform Export JS wiring in `setupImportExportPage()` |
| `CLAUDE.md` | Modify | Add new endpoint signatures under Export API section |
| `PROJECT_STATUS.md` | Modify | Bump date, add feature to recent summary |

---

## Task 1: Core export module (TDD)

**Files:**
- Create: `portf_manager/platform_export.py`
- Create: `tests/unit/test_platform_export.py`

**Interfaces:**
- Produces:
  - `_is_isin(s: str) -> bool`
  - `_resolve_ticker(symbol: str, ticker: str | None) -> str | None`
  - `build_yahoo_finance_csv(db, portfolio_id: int | None, mode: str) -> tuple[str, list[str]]`
  - `build_simply_wall_st_csv(db, portfolio_id: int | None, mode: str) -> tuple[str, list[str]]`
  - Both build functions return `(csv_content, skipped_symbols)` where `skipped_symbols` is a list of asset symbol strings.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_platform_export.py`:

```python
"""Unit tests for the platform_export module."""

import csv
import io

from portf_manager.platform_export import (
    _is_isin,
    _resolve_ticker,
    build_yahoo_finance_csv,
    build_simply_wall_st_csv,
)


# ---------------------------------------------------------------------------
# _is_isin
# ---------------------------------------------------------------------------

def test_is_isin_valid():
    assert _is_isin("US0378331005") is True


def test_is_isin_short_ticker():
    assert _is_isin("NVDA") is False


def test_is_isin_crypto_symbol():
    assert _is_isin("BTC-EUR") is False


def test_is_isin_mintos():
    assert _is_isin("MINTOS") is False


def test_is_isin_eu_fund():
    assert _is_isin("IE00B3XXRP09") is True


# ---------------------------------------------------------------------------
# _resolve_ticker
# ---------------------------------------------------------------------------

def test_resolve_ticker_uses_ticker_column():
    assert _resolve_ticker("US0378331005", "AAPL") == "AAPL"


def test_resolve_ticker_uses_symbol_when_not_isin():
    assert _resolve_ticker("NVDA", None) == "NVDA"


def test_resolve_ticker_uses_crypto_symbol():
    assert _resolve_ticker("BTC-EUR", None) == "BTC-EUR"


def test_resolve_ticker_isin_no_ticker_returns_none():
    assert _resolve_ticker("US0378331005", None) is None


def test_resolve_ticker_prefers_ticker_over_non_isin_symbol():
    assert _resolve_ticker("NVDA", "NVDA.AS") == "NVDA.AS"


# ---------------------------------------------------------------------------
# FakeDB helpers
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return [dict(r) for r in self._rows]


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=None):
        rows = self._rows
        if params and "AND t.portfolio_id" in query:
            pid = params[0]
            rows = [r for r in rows if r.get("portfolio_id") == pid]
        return _FakeCursor(rows)


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def get_connection(self):
        return _FakeConn(self._rows)


def _tx(
    asset_id, symbol, ticker, tx_type, qty, price,
    fees=0.0, date="2023-01-15", currency="EUR",
    asset_currency="EUR", portfolio_id=1,
):
    return {
        "id": asset_id * 1000 + len(date),
        "asset_id": asset_id,
        "symbol": symbol,
        "ticker": ticker,
        "transaction_type": tx_type,
        "quantity": float(qty),
        "price": float(price),
        "total_amount": float(qty) * float(price),
        "fees": float(fees),
        "transaction_date": date,
        "currency": currency,
        "asset_currency": asset_currency,
        "portfolio_id": portfolio_id,
    }


def _parse_csv(content):
    return list(csv.reader(io.StringIO(content)))


# ---------------------------------------------------------------------------
# build_yahoo_finance_csv — transactions mode
# ---------------------------------------------------------------------------

def test_yahoo_transactions_buy_positive_shares():
    db = FakeDB([_tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15")])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[0] == ["Symbol", "Shares", "Purchase Price", "Purchase Date", "Commission"]
    assert rows[1][0] == "NVDA"
    assert float(rows[1][1]) == 10.0
    assert rows[1][3] == "01/15/2023"
    assert skipped == []


def test_yahoo_transactions_sell_negative_shares():
    db = FakeDB([_tx(1, "NVDA", None, "sell", 5, 200.0, date="2023-06-01")])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert float(rows[1][1]) == -5.0
    assert skipped == []


def test_yahoo_transactions_isin_only_skipped():
    db = FakeDB([_tx(1, "US0378331005", None, "buy", 10, 150.0)])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1  # headers only
    assert "US0378331005" in skipped


def test_yahoo_transactions_isin_with_ticker_included():
    db = FakeDB([_tx(1, "US0378331005", "AAPL", "buy", 10, 150.0)])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[1][0] == "AAPL"
    assert skipped == []


def test_yahoo_transactions_empty_input():
    db = FakeDB([])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert skipped == []


def test_yahoo_transactions_isin_deduped_in_skipped():
    db = FakeDB([
        _tx(1, "US0378331005", None, "buy", 10, 150.0, date="2023-01-01"),
        _tx(1, "US0378331005", None, "buy", 5, 160.0, date="2023-02-01"),
    ])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    assert skipped.count("US0378331005") == 1


# ---------------------------------------------------------------------------
# build_yahoo_finance_csv — positions mode
# ---------------------------------------------------------------------------

def test_yahoo_positions_collapses_buys():
    db = FakeDB([
        _tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15"),
        _tx(1, "NVDA", None, "buy", 5, 200.0, date="2023-06-01"),
    ])
    content, skipped = build_yahoo_finance_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 2  # header + 1 data row
    assert rows[1][0] == "NVDA"
    assert float(rows[1][1]) == 15.0
    assert skipped == []


def test_yahoo_positions_excludes_sold_out():
    db = FakeDB([
        _tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15"),
        _tx(1, "NVDA", None, "sell", 10, 200.0, date="2023-06-01"),
    ])
    content, skipped = build_yahoo_finance_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 1  # headers only


def test_yahoo_positions_isin_only_skipped():
    db = FakeDB([_tx(1, "IE00B3XXRP09", None, "buy", 100, 50.0)])
    content, skipped = build_yahoo_finance_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert "IE00B3XXRP09" in skipped


# ---------------------------------------------------------------------------
# build_simply_wall_st_csv — transactions mode
# ---------------------------------------------------------------------------

def test_sws_transactions_headers():
    db = FakeDB([])
    content, _ = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[0] == [
        "Ticker Symbol", "Number of Shares",
        "Purchase Price (Per Share)", "Purchase Date", "Currency",
    ]


def test_sws_transactions_date_format_iso():
    db = FakeDB([_tx(1, "ASML.AS", None, "buy", 5, 680.5, fees=4.95, date="2023-03-10", currency="EUR")])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[1][0] == "ASML.AS"
    assert rows[1][3] == "2023-03-10"
    assert rows[1][4] == "EUR"
    assert skipped == []


def test_sws_transactions_sell_negative():
    db = FakeDB([_tx(1, "NVDA", None, "sell", 3, 200.0, date="2023-06-01")])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert float(rows[1][1]) == -3.0


def test_sws_transactions_isin_skipped():
    db = FakeDB([_tx(1, "IE00B3XXRP09", None, "buy", 100, 50.0)])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert "IE00B3XXRP09" in skipped


def test_sws_transactions_empty_input():
    db = FakeDB([])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert skipped == []


# ---------------------------------------------------------------------------
# build_simply_wall_st_csv — positions mode
# ---------------------------------------------------------------------------

def test_sws_positions_includes_currency():
    db = FakeDB([
        _tx(1, "ASML.AS", None, "buy", 5, 680.5, date="2023-03-10",
            currency="EUR", asset_currency="EUR"),
    ])
    content, skipped = build_simply_wall_st_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert rows[1][0] == "ASML.AS"
    assert float(rows[1][1]) == 5.0
    assert rows[1][4] == "EUR"
    assert skipped == []


def test_sws_positions_excludes_sold_out():
    db = FakeDB([
        _tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15"),
        _tx(1, "NVDA", None, "sell", 10, 200.0, date="2023-06-01"),
    ])
    content, skipped = build_simply_wall_st_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_platform_export.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'portf_manager.platform_export'`

- [ ] **Step 3: Create `portf_manager/platform_export.py`**

```python
"""
CSV export helpers for third-party portfolio platforms.

Supported platforms:
  - Yahoo Finance  (transactions or positions, mode="transactions"|"positions")
  - Simply Wall St (transactions or positions)
"""

import csv
import io
from typing import Optional

from portf_manager.positions import compute_positions


def _is_isin(s: str) -> bool:
    return len(s) == 12 and s[:2].isalpha() and s[2:].isalnum()


def _resolve_ticker(symbol: str, ticker: Optional[str]) -> Optional[str]:
    if ticker:
        return ticker
    if not _is_isin(symbol):
        return symbol
    return None


def _fetch_buy_sell_txs(db, portfolio_id: Optional[int]) -> list[dict]:
    query = """
        SELECT
            t.id, t.asset_id, t.transaction_type,
            t.quantity, t.price, t.total_amount, t.fees,
            t.transaction_date,
            COALESCE(t.currency, a.currency) AS currency,
            a.symbol, a.name, a.ticker,
            a.currency AS asset_currency
        FROM transactions t
        JOIN assets a ON t.asset_id = a.id
        WHERE t.transaction_type IN ('buy', 'sell')
    """
    params: list = []
    if portfolio_id is not None:
        query += " AND t.portfolio_id = ?"
        params.append(portfolio_id)
    query += " ORDER BY t.transaction_date ASC"

    with db.get_connection() as conn:
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def _build_asset_meta(txs: list[dict]) -> dict[int, dict]:
    meta: dict[int, dict] = {}
    for tx in txs:
        aid = tx["asset_id"]
        if aid not in meta:
            meta[aid] = {
                "symbol": tx["symbol"],
                "ticker": tx["ticker"],
                "asset_currency": tx.get("asset_currency", ""),
            }
    return meta


def build_yahoo_finance_csv(
    db, portfolio_id: Optional[int], mode: str
) -> tuple[str, list[str]]:
    txs = _fetch_buy_sell_txs(db, portfolio_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Symbol", "Shares", "Purchase Price", "Purchase Date", "Commission"])

    skipped: list[str] = []
    seen_skipped: set[str] = set()

    if mode == "positions":
        asset_meta = _build_asset_meta(txs)
        positions, _ = compute_positions(txs)
        for asset_id, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            meta = asset_meta.get(asset_id, {})
            sym = meta.get("symbol", str(asset_id))
            ticker = _resolve_ticker(sym, meta.get("ticker"))
            if ticker is None:
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            writer.writerow([ticker, round(pos["quantity"], 8), "", "", ""])
    else:
        for tx in txs:
            ticker = _resolve_ticker(tx["symbol"], tx["ticker"])
            if ticker is None:
                sym = tx["symbol"]
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            shares = tx["quantity"] if tx["transaction_type"] == "buy" else -tx["quantity"]
            date_str = ""
            raw = str(tx.get("transaction_date", ""))[:10]
            parts = raw.split("-")
            if len(parts) == 3:
                date_str = f"{parts[1]}/{parts[2]}/{parts[0]}"
            writer.writerow([
                ticker,
                round(shares, 8),
                round(tx["price"], 4) if tx.get("price") else "",
                date_str,
                round(tx["fees"], 2) if tx.get("fees") else "0.00",
            ])

    return buf.getvalue(), skipped


def build_simply_wall_st_csv(
    db, portfolio_id: Optional[int], mode: str
) -> tuple[str, list[str]]:
    txs = _fetch_buy_sell_txs(db, portfolio_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Ticker Symbol", "Number of Shares",
        "Purchase Price (Per Share)", "Purchase Date", "Currency",
    ])

    skipped: list[str] = []
    seen_skipped: set[str] = set()

    if mode == "positions":
        asset_meta = _build_asset_meta(txs)
        positions, _ = compute_positions(txs)
        for asset_id, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            meta = asset_meta.get(asset_id, {})
            sym = meta.get("symbol", str(asset_id))
            ticker = _resolve_ticker(sym, meta.get("ticker"))
            if ticker is None:
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            writer.writerow([ticker, round(pos["quantity"], 8), "", "", meta.get("asset_currency", "")])
    else:
        for tx in txs:
            ticker = _resolve_ticker(tx["symbol"], tx["ticker"])
            if ticker is None:
                sym = tx["symbol"]
                if sym not in seen_skipped:
                    skipped.append(sym)
                    seen_skipped.add(sym)
                continue
            shares = tx["quantity"] if tx["transaction_type"] == "buy" else -tx["quantity"]
            date_str = str(tx.get("transaction_date", ""))[:10]
            writer.writerow([
                ticker,
                round(shares, 8),
                round(tx["price"], 4) if tx.get("price") else "",
                date_str,
                tx.get("currency", ""),
            ])

    return buf.getvalue(), skipped
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_platform_export.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Format and lint**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black portf_manager/platform_export.py
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run flake8 portf_manager/platform_export.py --max-line-length=88 --extend-ignore=E203,W503,E501
```

- [ ] **Step 6: Commit**

```bash
git add portf_manager/platform_export.py tests/unit/test_platform_export.py
git commit -m "feat: add platform_export module with Yahoo Finance + Simply Wall St CSV builders

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: API endpoints

**Files:**
- Modify: `portf_server/routers/exports.py`

**Interfaces:**
- Consumes: `build_yahoo_finance_csv(db, portfolio_id, mode)` and `build_simply_wall_st_csv(db, portfolio_id, mode)` from Task 1.
- Produces:
  - `GET /api/v1/export/yahoo-finance?portfolio_id=<int>&mode=<str>` → CSV StreamingResponse
  - `GET /api/v1/export/simply-wall-st?portfolio_id=<int>&mode=<str>` → CSV StreamingResponse
  - Both set headers `X-Skipped-Count: N` and `X-Skipped-Symbols: SYM1,SYM2`

- [ ] **Step 1: Add the two endpoints to `portf_server/routers/exports.py`**

Add this import at the top of the file (after the existing imports):

```python
from portf_manager.platform_export import (
    build_yahoo_finance_csv,
    build_simply_wall_st_csv,
)
```

Add these two endpoints at the end of the file (before the final newline):

```python
@router.get("/yahoo-finance")
async def export_yahoo_finance(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    mode: str = Query(
        default="transactions",
        description="'transactions' for full history, 'positions' for current holdings",
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Download portfolio in Yahoo Finance CSV import format."""
    csv_content, skipped = build_yahoo_finance_csv(db, portfolio_id, mode)
    csv_bytes = b"\xef\xbb\xbf" + csv_content.encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=yahoo_finance_portfolio.csv",
            "X-Skipped-Count": str(len(skipped)),
            "X-Skipped-Symbols": ",".join(skipped),
            "Access-Control-Expose-Headers": "X-Skipped-Count,X-Skipped-Symbols",
        },
    )


@router.get("/simply-wall-st")
async def export_simply_wall_st(
    portfolio_id: Optional[int] = Query(
        default=None, description="Filter by portfolio ID"
    ),
    mode: str = Query(
        default="transactions",
        description="'transactions' for full history, 'positions' for current holdings",
    ),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Download portfolio in Simply Wall St CSV import format."""
    csv_content, skipped = build_simply_wall_st_csv(db, portfolio_id, mode)
    csv_bytes = b"\xef\xbb\xbf" + csv_content.encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=simply_wall_st_portfolio.csv",
            "X-Skipped-Count": str(len(skipped)),
            "X-Skipped-Symbols": ",".join(skipped),
            "Access-Control-Expose-Headers": "X-Skipped-Count,X-Skipped-Symbols",
        },
    )
```

- [ ] **Step 2: Format**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run black portf_server/routers/exports.py
```

- [ ] **Step 3: Verify the full test suite still passes**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```

Expected: all tests pass, no new failures.

- [ ] **Step 4: Commit**

```bash
git add portf_server/routers/exports.py
git commit -m "feat: add /export/yahoo-finance and /export/simply-wall-st endpoints

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: HTML card

**Files:**
- Modify: `web_client/index.html`

**Interfaces:**
- Produces HTML element IDs consumed by Task 4:
  - `platformExportSelect` — platform dropdown
  - `platformExportModeTransactions` / `platformExportModePositions` — radio inputs (`name="platformExportMode"`)
  - `platformExportPortfolio` — portfolio filter dropdown
  - `platformExportBtn` — download button
  - `platformExportWarning` — warning div (hidden by default)

- [ ] **Step 1: Insert the Platform Export card in `web_client/index.html`**

Find this comment (around line 1869):
```html
                        <!-- Bookings (cash transactions: deposits / withdrawals) -->
```

Insert the following block **immediately before** that comment:

```html
                        <!-- Platform Export -->
                        <div class="col-12">
                            <div class="card">
                                <div class="card-header fw-semibold">
                                    <i class="bi bi-cloud-upload me-2"></i>Platform Export
                                </div>
                                <div class="card-body">
                                    <p class="text-muted small mb-3">Export your portfolio for Yahoo Finance or Simply Wall St. Download the file and upload it to that platform&rsquo;s portfolio tracker.</p>
                                    <div class="row g-3 align-items-end">
                                        <div class="col-auto">
                                            <label class="form-label small mb-1" for="platformExportSelect">Platform</label>
                                            <select class="form-select form-select-sm" id="platformExportSelect">
                                                <option value="yahoo-finance">Yahoo Finance</option>
                                                <option value="simply-wall-st">Simply Wall St</option>
                                            </select>
                                        </div>
                                        <div class="col-auto">
                                            <label class="form-label small mb-1">Data</label>
                                            <div class="d-flex gap-3">
                                                <div class="form-check mb-0">
                                                    <input class="form-check-input" type="radio" name="platformExportMode" id="platformExportModeTransactions" value="transactions" checked>
                                                    <label class="form-check-label small" for="platformExportModeTransactions">Full transaction history</label>
                                                </div>
                                                <div class="form-check mb-0">
                                                    <input class="form-check-input" type="radio" name="platformExportMode" id="platformExportModePositions" value="positions">
                                                    <label class="form-check-label small" for="platformExportModePositions">Current positions only</label>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-auto">
                                            <label class="form-label small mb-1" for="platformExportPortfolio">Portfolio</label>
                                            <select class="form-select form-select-sm" id="platformExportPortfolio">
                                                <option value="">All portfolios</option>
                                            </select>
                                        </div>
                                        <div class="col-auto">
                                            <button class="btn btn-sm btn-outline-primary" id="platformExportBtn">
                                                <i class="bi bi-download me-2"></i>Download
                                            </button>
                                        </div>
                                    </div>
                                    <div id="platformExportWarning" class="alert alert-warning mt-3 mb-0 py-2 small d-none" role="alert"></div>
                                </div>
                            </div>
                        </div>

```

- [ ] **Step 2: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Platform Export card to Import/Export page HTML

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: JavaScript wiring

**Files:**
- Modify: `web_client/js/pfm_features.js`

**Interfaces:**
- Consumes: element IDs from Task 3 (`platformExportBtn`, `platformExportSelect`, `platformExportPortfolio`, `platformExportWarning`, `input[name="platformExportMode"]`)
- Consumes: `window.apiClient.getPortfolios()` → `[{id, name}]`
- Consumes: `window.apiClient.apiKey` (string), `window.apiClient.baseURL` (string)
- Consumes: API endpoints from Task 2: `GET /api/v1/export/yahoo-finance` and `GET /api/v1/export/simply-wall-st`

- [ ] **Step 1: Add Platform Export JS inside `setupImportExportPage()`**

In `web_client/js/pfm_features.js`, find this line (around line 1136):
```javascript
    const ioBackupBtn = document.getElementById('ioExportBackupBtn');
```

After the full block that handles `ioBackupBtn` (i.e., after the `ioRestoreConfirmBtn` click handler block ends), and before the line that starts the Bookings section, add:

```javascript
    // --- Platform Export section ---
    (async () => {
        const platformExportBtn = document.getElementById('platformExportBtn');
        const platformExportSelect = document.getElementById('platformExportSelect');
        const platformExportPortfolio = document.getElementById('platformExportPortfolio');
        const platformExportWarning = document.getElementById('platformExportWarning');
        if (!platformExportBtn) return;

        try {
            const portfolios = await window.apiClient.getPortfolios();
            portfolios.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name;
                platformExportPortfolio.appendChild(opt);
            });
        } catch (_) {}

        platformExportBtn.addEventListener('click', async () => {
            const platform = platformExportSelect ? platformExportSelect.value : 'yahoo-finance';
            const modeEl = document.querySelector('input[name="platformExportMode"]:checked');
            const mode = modeEl ? modeEl.value : 'transactions';
            const portfolioId = platformExportPortfolio && platformExportPortfolio.value
                ? platformExportPortfolio.value : '';
            const filenames = {
                'yahoo-finance': 'yahoo_finance_portfolio.csv',
                'simply-wall-st': 'simply_wall_st_portfolio.csv',
            };
            const filename = filenames[platform] || 'portfolio.csv';
            let url = `${window.apiClient.baseURL}/api/v1/export/${platform}?mode=${mode}`;
            if (portfolioId) url += `&portfolio_id=${portfolioId}`;

            platformExportBtn.disabled = true;
            if (platformExportWarning) platformExportWarning.classList.add('d-none');

            try {
                const resp = await fetch(url, { headers: { 'X-API-Key': window.apiClient.apiKey } });
                if (!resp.ok) throw new Error('Export failed: ' + resp.status);
                const skippedCount = parseInt(resp.headers.get('X-Skipped-Count') || '0');
                const skippedSymbols = resp.headers.get('X-Skipped-Symbols') || '';
                const blob = await resp.blob();
                const objectUrl = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = objectUrl;
                link.download = filename;
                link.click();
                URL.revokeObjectURL(objectUrl);
                if (skippedCount > 0 && platformExportWarning) {
                    platformExportWarning.textContent =
                        `${skippedCount} asset(s) skipped (no ticker assigned): ${skippedSymbols}`;
                    platformExportWarning.classList.remove('d-none');
                }
            } catch (err) {
                alert('Platform export error: ' + err.message);
            } finally {
                platformExportBtn.disabled = false;
            }
        });
    })();
```

- [ ] **Step 2: Deploy the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

- [ ] **Step 3: Manual smoke test**

1. Open the Import/Export page in the browser
2. Confirm the "Platform Export" card appears with platform dropdown, mode radios, portfolio dropdown, and Download button
3. Select Yahoo Finance, Full transaction history, All portfolios → click Download
4. Verify a `yahoo_finance_portfolio.csv` file downloads
5. Open the CSV and confirm: first row is `Symbol,Shares,Purchase Price,Purchase Date,Commission`; buy rows have positive Shares, sell rows negative; date format is `MM/DD/YYYY`
6. Repeat for Simply Wall St — file should be `simply_wall_st_portfolio.csv` with headers `Ticker Symbol,Number of Shares,Purchase Price (Per Share),Purchase Date,Currency` and ISO dates
7. If any assets are ISIN-only, confirm the warning div appears below the button listing them
8. Test "Current positions only" mode — CSV should have one row per held asset (quantity > 0 only)

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: wire Platform Export card in setupImportExportPage

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `PROJECT_STATUS.md`

- [ ] **Step 1: Update `CLAUDE.md` — Export API section**

Find the `### Export API` section and add the two new endpoint signatures:

```markdown
- `GET /api/v1/export/yahoo-finance?portfolio_id=&mode=transactions|positions` — Yahoo Finance CSV (Symbol/Shares/Purchase Price/Purchase Date/Commission); sells = negative Shares; date MM/DD/YYYY; assets without `ticker` skipped (X-Skipped-Count / X-Skipped-Symbols response headers)
- `GET /api/v1/export/simply-wall-st?portfolio_id=&mode=transactions|positions` — Simply Wall St CSV (Ticker Symbol/Shares/Price/Date/Currency); sells = negative shares; date YYYY-MM-DD; same skip behaviour
- Platform export logic lives in `portf_manager/platform_export.py`: `_is_isin`, `_resolve_ticker`, `build_yahoo_finance_csv`, `build_simply_wall_st_csv`. Uses dedicated SQL query (not `_TX_COLS`) to include `a.ticker`.
```

- [ ] **Step 2: Update `PROJECT_STATUS.md`**

Bump the "Last updated" date to `2026-06-16` and add to the recent summary line:
`Platform Export: Yahoo Finance + Simply Wall St CSV download (transactions or positions, ticker-resolved, skip warning for ISIN-only assets)`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: update CLAUDE.md and PROJECT_STATUS.md for platform export feature

Co-Authored-By: Oz <oz-agent@warp.dev>"
```
