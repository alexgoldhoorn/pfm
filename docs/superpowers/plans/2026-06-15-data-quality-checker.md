# Data Quality Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Data Quality" tab to the Diagnostics page with three independent checks: per-portfolio cash reconciliation, fuzzy duplicate detection, and suspicious-pattern flagging — all with inline delete/dismiss actions persisted in localStorage.

**Architecture:** Three new endpoints in `portf_server/routers/analytics.py` under `/api/v1/analytics/dq/`; three matching API client methods in `pfm_core.js`; Bootstrap tabs restructure of the Diagnostics page HTML with a new `loadDataQualityTab()` function. Dismissals live in `localStorage["pfmDismissedIssues"]`; delete reuses the existing `apiClient.deleteTransaction()`.

**Tech Stack:** FastAPI (Python 3.13), SQLite via `portf_manager.database.Database`, vanilla JS + Bootstrap 5.3, pytest + httpx for tests.

**Run tests with:** `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py -v`
**Run all unit tests:** `UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v`
**Deploy web after HTML/JS changes:** `docker compose build web && docker stop portf_web && docker compose up -d web`

---

## File Map

| File | Change |
|---|---|
| `portf_server/routers/analytics.py` | Add 3 DQ endpoints at the end of the file |
| `web_client/js/pfm_core.js` | Add 3 API client methods; update `loadDiagnosticsPage()`; add `loadDataQualityTab()` |
| `web_client/index.html` | Restructure Diagnostics page into Bootstrap tabs; add DQ pane |
| `tests/unit/test_data_quality.py` | New file — tests for all three DQ endpoints |
| `CLAUDE.md` | Add documentation-update rule |
| `PROJECT_STATUS.md` | Note new feature |

---

## Task 1: Reconciliation endpoint

**Files:**
- Modify: `portf_server/routers/analytics.py` (append after line ~1230, end of file)
- Create: `tests/unit/test_data_quality.py`

- [ ] **Step 1: Create the test file with a failing reconciliation test**

Create `tests/unit/test_data_quality.py`:

```python
"""Unit tests for the /analytics/dq/* data-quality endpoints."""

import pytest
from httpx import AsyncClient


# ── helpers ──────────────────────────────────────────────────────────────────

def _setup_portfolio_with_data(db):
    """Return (portfolio_id, asset_id) after inserting one portfolio,
    one asset, one deposit booking, and one buy transaction."""
    pid = db.get_or_create_portfolio("TestBroker", base_currency="EUR")
    aid = db.create_asset("VWCE", "Vanguard FTSE All-World ETF", "etf", currency="EUR")
    # Deposit 10 000 EUR
    db.create_booking("2025-01-01", "Deposit", 10000.0, "EUR", portfolio_id=pid)
    # Buy 100 units @ 80 EUR = 8 000 EUR total
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=100.0,
        price=80.0,
        total_amount=8000.0,
        transaction_date="2025-01-05",
        portfolio_id=pid,
        currency="EUR",
    )
    return pid, aid


# ── reconciliation ────────────────────────────────────────────────────────────


class TestDQReconciliation:
    @pytest.mark.asyncio
    async def test_reconciliation_returns_portfolios(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _setup_portfolio_with_data(test_database)
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "portfolios" in data
        assert len(data["portfolios"]) >= 1

    @pytest.mark.asyncio
    async def test_reconciliation_implied_cash_formula(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid, _ = _setup_portfolio_with_data(test_database)
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        data = resp.json()
        p = next(x for x in data["portfolios"] if x["portfolio_id"] == pid)
        # deposit 10000 - buy 8000 = implied_cash 2000
        assert p["net_bookings"] == pytest.approx(10000.0)
        assert p["buy_costs"] == pytest.approx(8000.0)
        assert p["implied_cash"] == pytest.approx(2000.0)

    @pytest.mark.asyncio
    async def test_reconciliation_includes_sell_proceeds(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid, aid = _setup_portfolio_with_data(test_database)
        # Sell 50 units @ 90 = 4500 EUR
        test_database.create_transaction(
            asset_id=aid,
            transaction_type="sell",
            quantity=50.0,
            price=90.0,
            total_amount=4500.0,
            transaction_date="2025-06-01",
            portfolio_id=pid,
            currency="EUR",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        data = resp.json()
        p = next(x for x in data["portfolios"] if x["portfolio_id"] == pid)
        # 10000 - 8000 + 4500 = 6500
        assert p["implied_cash"] == pytest.approx(6500.0)

    @pytest.mark.asyncio
    async def test_reconciliation_includes_dividends_and_interest(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid, aid = _setup_portfolio_with_data(test_database)
        test_database.create_transaction(
            asset_id=aid, transaction_type="dividend",
            quantity=0.0, price=0.0, total_amount=150.0,
            transaction_date="2025-03-15", portfolio_id=pid, currency="EUR",
        )
        test_database.create_transaction(
            asset_id=aid, transaction_type="interest",
            quantity=0.0, price=0.0, total_amount=50.0,
            transaction_date="2025-04-01", portfolio_id=pid, currency="EUR",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        data = resp.json()
        p = next(x for x in data["portfolios"] if x["portfolio_id"] == pid)
        # 10000 - 8000 + 150 + 50 = 2200
        assert p["implied_cash"] == pytest.approx(2200.0)
        assert p["dividend_income"] == pytest.approx(150.0)
        assert p["interest_income"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_reconciliation_empty_portfolio(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        test_database.get_or_create_portfolio("Empty", base_currency="EUR")
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/reconciliation", headers=auth_headers
        )
        assert resp.status_code == 200
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py::TestDQReconciliation -v
```

Expected: `FAILED` — `404 Not Found` for `/api/v1/analytics/dq/reconciliation`.

- [ ] **Step 3: Implement the reconciliation endpoint**

Append to `portf_server/routers/analytics.py` (after the last `@router` block, at end of file):

```python


# ── Data Quality ──────────────────────────────────────────────────────────────


@router.get("/dq/reconciliation")
def dq_reconciliation(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Per-portfolio cash reconciliation.

    Computes the 'implied cash' each portfolio should hold at the broker
    (deposits − withdrawals − buy costs + sell proceeds + dividends + interest)
    and the current invested value from stored prices. Both figures are EUR.
    The caller compares implied_cash against the broker's cash balance.

    Plain ``def`` because _fx() may make a blocking yfinance call for
    non-EUR assets.
    """
    portfolios = db.get_all_portfolios()
    result = []

    for p in portfolios:
        pid = p["id"]
        txns = db.get_all_transactions(portfolio_id=pid)
        bookings = db.get_all_bookings(portfolio_id=pid)

        deposits = sum(float(b["amount"] or 0) for b in bookings if b["action"] == "Deposit")
        withdrawals = sum(float(b["amount"] or 0) for b in bookings if b["action"] == "Withdrawal")
        net_bookings = deposits - withdrawals

        buy_costs = 0.0
        sell_proceeds = 0.0
        dividend_income_total = 0.0
        interest_income_total = 0.0
        for tx in txns:
            amt = float(tx["total_amount"] or 0)
            tx_type = tx["transaction_type"]
            if tx_type == "buy":
                buy_costs += amt
            elif tx_type == "sell":
                sell_proceeds += amt
            elif tx_type == "dividend":
                dividend_income_total += amt
            elif tx_type == "interest":
                interest_income_total += amt

        implied_cash = net_bookings - buy_costs + sell_proceeds + dividend_income_total + interest_income_total

        # Invested value: held quantity × latest stored price (EUR-converted).
        # Falls back to cost basis when no price is stored.
        positions, _ = compute_positions(txns)
        invested_value = 0.0
        for asset_id_key, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            price_data = db.get_latest_price(asset_id_key)
            if price_data and price_data.get("close"):
                price = float(price_data["close"])
                currency = price_data.get("currency") or "EUR"
                invested_value += pos["quantity"] * price * _fx(currency)
            else:
                asset = db.get_asset(asset_id_key)
                currency = (asset.get("currency") or "EUR") if asset else "EUR"
                invested_value += pos["cost"] * _fx(currency)

        result.append(
            {
                "portfolio_id": pid,
                "portfolio_name": p["name"],
                "net_bookings": round(net_bookings, 2),
                "buy_costs": round(buy_costs, 2),
                "sell_proceeds": round(sell_proceeds, 2),
                "dividend_income": round(dividend_income_total, 2),
                "interest_income": round(interest_income_total, 2),
                "implied_cash": round(implied_cash, 2),
                "invested_value": round(invested_value, 2),
                "total_accounted": round(implied_cash + invested_value, 2),
            }
        )

    return {"portfolios": result}
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py::TestDQReconciliation -v
```

Expected: 5 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_data_quality.py
git commit -m "feat: add /analytics/dq/reconciliation endpoint with tests"
```

---

## Task 2: Duplicates endpoint

**Files:**
- Modify: `portf_server/routers/analytics.py`
- Modify: `tests/unit/test_data_quality.py`

- [ ] **Step 1: Add failing tests for the duplicates endpoint**

Append to `tests/unit/test_data_quality.py`:

```python


# ── duplicates ────────────────────────────────────────────────────────────────


class TestDQDuplicates:
    @pytest.mark.asyncio
    async def test_no_duplicates_when_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["duplicates"] == []

    @pytest.mark.asyncio
    async def test_detects_exact_same_day_duplicate(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
        aid = test_database.create_asset("SPY", "S&P 500 ETF", "etf", currency="USD")
        for _ in range(2):
            test_database.create_transaction(
                asset_id=aid, transaction_type="buy",
                quantity=10.0, price=500.0, total_amount=5000.0,
                transaction_date="2025-03-15", portfolio_id=pid, currency="USD",
            )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        dups = resp.json()["duplicates"]
        assert len(dups) == 1
        assert dups[0]["label"] == "likely"
        assert dups[0]["key"].startswith("dup:")

    @pytest.mark.asyncio
    async def test_detects_within_3_day_window(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker2", base_currency="EUR")
        aid = test_database.create_asset("QQQ", "Nasdaq ETF", "etf", currency="USD")
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=5.0, price=400.0, total_amount=2000.0,
            transaction_date="2025-04-01", portfolio_id=pid, currency="USD",
        )
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=5.0, price=402.0, total_amount=2010.0,
            transaction_date="2025-04-03", portfolio_id=pid, currency="USD",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        dups = resp.json()["duplicates"]
        assert len(dups) == 1
        assert dups[0]["label"] == "possible"

    @pytest.mark.asyncio
    async def test_no_duplicate_outside_3_day_window(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker3", base_currency="EUR")
        aid = test_database.create_asset("GLD", "Gold ETF", "etf", currency="USD")
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=10.0, price=180.0, total_amount=1800.0,
            transaction_date="2025-05-01", portfolio_id=pid, currency="USD",
        )
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=10.0, price=180.0, total_amount=1800.0,
            transaction_date="2025-05-10", portfolio_id=pid, currency="USD",
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        assert resp.json()["duplicates"] == []

    @pytest.mark.asyncio
    async def test_no_duplicate_different_quantity(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("Broker4", base_currency="EUR")
        aid = test_database.create_asset("MSFT", "Microsoft", "stock", currency="USD")
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=5.0, price=300.0, total_amount=1500.0,
            transaction_date="2025-06-01", portfolio_id=pid, currency="USD",
        )
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=10.0, price=300.0, total_amount=3000.0,
            transaction_date="2025-06-01", portfolio_id=pid, currency="USD",
        )
        # quantities differ by >5% (5 vs 10 = 100% diff)
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/duplicates", headers=auth_headers
        )
        assert resp.json()["duplicates"] == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py::TestDQDuplicates -v
```

Expected: `FAILED` — 404.

- [ ] **Step 3: Implement the duplicates endpoint**

Append to `portf_server/routers/analytics.py` (after the reconciliation endpoint):

```python


@router.get("/dq/duplicates")
async def dq_duplicates(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Scan all transactions for fuzzy near-duplicates.

    Groups by (portfolio_id, asset_id, transaction_type). Within each group,
    flags pairs where date is within ±3 days AND quantity within ±5% AND
    price within ±5%. Labels 'likely' when same day + qty/price within ±1%.
    """
    from collections import defaultdict

    txns = db.get_all_transactions()
    groups: dict = defaultdict(list)
    for tx in txns:
        key = (
            tx.get("portfolio_id"),
            tx.get("asset_id"),
            tx.get("transaction_type"),
        )
        groups[key].append(tx)

    duplicates = []
    seen_pairs: set = set()

    for group_txns in groups.values():
        group_txns.sort(key=lambda t: str(t.get("transaction_date") or ""))
        n = len(group_txns)
        for i in range(n):
            tx_a = group_txns[i]
            date_a_str = str(tx_a.get("transaction_date") or "")[:10]
            try:
                d_a = datetime.strptime(date_a_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            for j in range(i + 1, n):
                tx_b = group_txns[j]
                date_b_str = str(tx_b.get("transaction_date") or "")[:10]
                try:
                    d_b = datetime.strptime(date_b_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                day_diff = abs((d_b - d_a).days)
                if day_diff > 3:
                    break  # list is sorted by date; no closer matches ahead

                qty_a = float(tx_a.get("quantity") or 0)
                qty_b = float(tx_b.get("quantity") or 0)
                price_a = float(tx_a.get("price") or 0)
                price_b = float(tx_b.get("price") or 0)

                def _within(a: float, b: float, pct: float) -> bool:
                    if a == 0 and b == 0:
                        return True
                    if a == 0 or b == 0:
                        return False
                    return abs(a - b) / max(abs(a), abs(b)) <= pct

                if not (_within(qty_a, qty_b, 0.05) and _within(price_a, price_b, 0.05)):
                    continue

                id_a, id_b = tx_a["id"], tx_b["id"]
                pair_key = f"dup:{min(id_a, id_b)}:{max(id_a, id_b)}"
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                label = (
                    "likely"
                    if day_diff == 0
                    and _within(qty_a, qty_b, 0.01)
                    and _within(price_a, price_b, 0.01)
                    else "possible"
                )

                def _summary(tx: dict) -> dict:
                    return {
                        "id": tx["id"],
                        "date": str(tx.get("transaction_date") or "")[:10],
                        "asset": tx.get("symbol") or "",
                        "asset_name": tx.get("name") or "",
                        "type": tx.get("transaction_type") or "",
                        "quantity": float(tx.get("quantity") or 0),
                        "price": float(tx.get("price") or 0),
                        "portfolio": tx.get("portfolio_name") or "",
                    }

                duplicates.append(
                    {
                        "label": label,
                        "key": pair_key,
                        "tx_a": _summary(tx_a),
                        "tx_b": _summary(tx_b),
                    }
                )

    return {"duplicates": duplicates}
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py::TestDQDuplicates -v
```

Expected: 5 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_data_quality.py
git commit -m "feat: add /analytics/dq/duplicates endpoint with tests"
```

---

## Task 3: Suspicious patterns endpoint

**Files:**
- Modify: `portf_server/routers/analytics.py`
- Modify: `tests/unit/test_data_quality.py`

- [ ] **Step 1: Add failing tests for the suspicious endpoint**

Append to `tests/unit/test_data_quality.py`:

```python


# ── suspicious ────────────────────────────────────────────────────────────────


class TestDQSuspicious:
    @pytest.mark.asyncio
    async def test_no_issues_when_empty(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/suspicious", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["issues"] == []

    @pytest.mark.asyncio
    async def test_flags_zero_price_buy(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("ZP", base_currency="EUR")
        aid = test_database.create_asset("AMZN", "Amazon", "stock", currency="USD")
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=1.0, price=0.0, total_amount=0.0,
            transaction_date="2025-01-10", portfolio_id=pid,
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/suspicious", headers=auth_headers
        )
        issues = resp.json()["issues"]
        zero_price = [i for i in issues if i["check"] == "zero_price"]
        assert len(zero_price) == 1
        assert zero_price[0]["severity"] == "warning"
        assert zero_price[0]["key"].startswith("susp:")

    @pytest.mark.asyncio
    async def test_does_not_flag_split_with_zero_price(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("SP", base_currency="EUR")
        aid = test_database.create_asset("TSLA", "Tesla", "stock", currency="USD")
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=10.0, price=200.0, total_amount=2000.0,
            transaction_date="2025-01-01", portfolio_id=pid,
        )
        test_database.create_transaction(
            asset_id=aid, transaction_type="split",
            quantity=3.0, price=0.0, total_amount=0.0,
            transaction_date="2025-02-01", portfolio_id=pid,
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/suspicious", headers=auth_headers
        )
        issues = resp.json()["issues"]
        assert not any(i["check"] == "zero_price" for i in issues)

    @pytest.mark.asyncio
    async def test_flags_negative_position(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("NP", base_currency="EUR")
        aid = test_database.create_asset("GOOG", "Alphabet", "stock", currency="USD")
        # Sell without a prior buy
        test_database.create_transaction(
            asset_id=aid, transaction_type="sell",
            quantity=5.0, price=150.0, total_amount=750.0,
            transaction_date="2025-03-01", portfolio_id=pid,
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/suspicious", headers=auth_headers
        )
        issues = resp.json()["issues"]
        neg = [i for i in issues if i["check"] == "negative_position"]
        assert len(neg) == 1
        assert neg[0]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_flags_dividend_before_buy(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("DB", base_currency="EUR")
        aid = test_database.create_asset("JNJ", "Johnson & Johnson", "stock", currency="USD")
        test_database.create_transaction(
            asset_id=aid, transaction_type="dividend",
            quantity=0.0, price=0.0, total_amount=25.0,
            transaction_date="2025-01-15", portfolio_id=pid,
        )
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=10.0, price=160.0, total_amount=1600.0,
            transaction_date="2025-02-01", portfolio_id=pid,
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/suspicious", headers=auth_headers
        )
        issues = resp.json()["issues"]
        dbf = [i for i in issues if i["check"] == "dividend_before_buy"]
        assert len(dbf) == 1
        assert dbf[0]["severity"] == "info"

    @pytest.mark.asyncio
    async def test_flags_price_outlier(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        pid = test_database.get_or_create_portfolio("PO", base_currency="EUR")
        aid = test_database.create_asset("BRK", "Berkshire", "stock", currency="USD")
        # 3 normal buys at ~300 to establish median
        for price in [298.0, 300.0, 302.0]:
            test_database.create_transaction(
                asset_id=aid, transaction_type="buy",
                quantity=1.0, price=price, total_amount=price,
                transaction_date="2025-01-01", portfolio_id=pid,
            )
        # One buy at 300 × 6 = 1800 (5x outlier, from e.g. GBX import)
        test_database.create_transaction(
            asset_id=aid, transaction_type="buy",
            quantity=1.0, price=1800.0, total_amount=1800.0,
            transaction_date="2025-01-02", portfolio_id=pid,
        )
        resp = await async_test_client.get(
            "/api/v1/analytics/dq/suspicious", headers=auth_headers
        )
        issues = resp.json()["issues"]
        outliers = [i for i in issues if i["check"] == "price_outlier"]
        assert len(outliers) == 1
        assert outliers[0]["severity"] == "warning"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py::TestDQSuspicious -v
```

Expected: `FAILED` — 404.

- [ ] **Step 3: Implement the suspicious patterns endpoint**

Append to `portf_server/routers/analytics.py` (after the duplicates endpoint):

```python


@router.get("/dq/suspicious")
async def dq_suspicious(
    db=Depends(get_database), api_key_info: dict = Depends(_auth)
):
    """Scan transactions for data anomalies.

    Checks (per transaction, chronologically):
    - zero_price: buy or sell with price = 0 (splits and dividends excluded)
    - zero_qty: non-split, non-dividend transaction with quantity = 0
    - negative_position: sell that pushes the running quantity below zero
    - dividend_before_buy: dividend recorded before the first buy for that asset
    - price_outlier: price > 5× or < 0.2× the median for that asset
      (requires ≥3 price data points to compute median)
    """
    txns = db.get_all_transactions()
    txns_sorted = sorted(txns, key=lambda t: str(t.get("transaction_date") or ""))

    # Pre-compute per-asset price median (buy/sell only, price > 0)
    asset_prices: dict = {}
    for tx in txns_sorted:
        if tx.get("transaction_type") in ("buy", "sell"):
            p = float(tx.get("price") or 0)
            if p > 0:
                asset_prices.setdefault(tx.get("asset_id"), []).append(p)

    asset_median: dict = {}
    for aid, prices in asset_prices.items():
        if len(prices) >= 3:
            asset_median[aid] = statistics.median(prices)

    running_qty: dict = {}
    first_buy: dict = {}
    issues = []

    for tx in txns_sorted:
        aid = tx.get("asset_id")
        tx_type = tx.get("transaction_type") or ""
        qty = float(tx.get("quantity") or 0)
        price = float(tx.get("price") or 0)
        tx_id = tx["id"]
        tx_date = str(tx.get("transaction_date") or "")[:10]
        asset_sym = tx.get("symbol") or ""
        asset_nm = tx.get("name") or ""

        def _flag(severity: str, check: str, description: str) -> None:
            issues.append(
                {
                    "severity": severity,
                    "key": f"susp:{tx_id}:{check}",
                    "check": check,
                    "transaction_id": tx_id,
                    "asset": asset_sym,
                    "asset_name": asset_nm,
                    "date": tx_date,
                    "type": tx_type,
                    "description": description,
                }
            )

        # zero_price: buy/sell only (splits and dividends legitimately have price 0)
        if tx_type in ("buy", "sell") and price == 0:
            _flag("warning", "zero_price", f"{tx_type.capitalize()} transaction has price = 0")

        # zero_qty: any non-split, non-dividend transaction
        if tx_type not in ("split", "dividend") and qty == 0:
            _flag("warning", "zero_qty", "Transaction has quantity = 0")

        # dividend_before_buy
        if tx_type == "dividend" and aid not in first_buy:
            _flag("info", "dividend_before_buy", "Dividend recorded before any buy for this asset")

        # price_outlier (buy/sell, price > 0, median established)
        if tx_type in ("buy", "sell") and price > 0 and aid in asset_median:
            med = asset_median[aid]
            if med > 0 and (price > 5.0 * med or price < 0.2 * med):
                _flag(
                    "warning",
                    "price_outlier",
                    f"Price {price:.4f} is far from median {med:.4f} (possible unit error)",
                )

        # Update running state
        if tx_type == "buy":
            running_qty[aid] = running_qty.get(aid, 0.0) + qty
            first_buy.setdefault(aid, tx_date)
        elif tx_type == "sell":
            prev = running_qty.get(aid, 0.0)
            new_qty = prev - qty
            if new_qty < -0.001:
                _flag(
                    "warning",
                    "negative_position",
                    f"Sell results in negative quantity ({new_qty:.4f}); missing buy transaction?",
                )
            running_qty[aid] = new_qty
        elif tx_type == "split":
            running_qty[aid] = running_qty.get(aid, 0.0) * qty

    return {"issues": issues}
```

- [ ] **Step 4: Run all DQ tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_data_quality.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 5: Run the full unit suite to check for regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_data_quality.py
git commit -m "feat: add /analytics/dq/suspicious endpoint with tests"
```

---

## Task 4: API client methods

**Files:**
- Modify: `web_client/js/pfm_core.js`

The three new methods go after `getUpdateRuns` (line ~1083 in `pfm_core.js`).

- [ ] **Step 1: Add three DQ methods to `window.apiClient`**

In `pfm_core.js`, find this block (around line 1077):

```javascript
        async getUpdateRuns(limit = 20) {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/update-runs?limit=' + limit, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load update runs');
            return resp.json();
        },
```

Insert immediately after the closing `,` of `getUpdateRuns`:

```javascript

        async getDQReconciliation() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dq/reconciliation', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load reconciliation data');
            return resp.json();
        },

        async getDQDuplicates() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dq/duplicates', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load duplicates');
            return resp.json();
        },

        async getDQSuspicious() {
            const resp = await fetch(this.baseURL + '/api/v1/analytics/dq/suspicious', {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error('Failed to load suspicious patterns');
            return resp.json();
        },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add getDQReconciliation/getDQDuplicates/getDQSuspicious API client methods"
```

---

## Task 5: HTML — tab structure

**Files:**
- Modify: `web_client/index.html` (lines 2112–2158)

- [ ] **Step 1: Replace the Diagnostics page content with a tabbed layout**

In `index.html`, find and replace the entire diagnostics block (from `<!-- Diagnostics Page -->` through its closing `</div>`):

Find:
```html
                <!-- Diagnostics Page -->
                <div id="diagnosticsPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-activity me-2 text-primary"></i>Diagnostics</h4>
                            <p class="text-muted small mb-0">Price-data freshness and the daily price-update history — when prices last refreshed, what was skipped, and why.</p>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary" id="refreshDiagnostics" title="Refresh diagnostics"><i class="bi bi-arrow-clockwise"></i></button>
                            <button class="btn btn-sm btn-outline-secondary" onclick="showPageHelp('diagnostics')" title="Help"><i class="bi bi-question-circle"></i></button>
                        </div>
                    </div>

                    <!-- Price-data freshness -->
                    <div class="card mb-3">
                        <div class="card-header fw-semibold"><i class="bi bi-clock-history me-2"></i>Price data freshness</div>
                        <div class="card-body" id="diagFreshness">
                            <div class="text-muted small">Loading…</div>
                        </div>
                    </div>

                    <!-- Stale / unpriced holdings -->
                    <div class="card mb-3">
                        <div class="card-header fw-semibold"><i class="bi bi-exclamation-triangle me-2"></i>Stale &amp; unpriced holdings</div>
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-sm mb-0 align-middle">
                                    <thead><tr><th>Symbol</th><th>Name</th><th class="text-end">Age</th><th>Reason</th></tr></thead>
                                    <tbody id="diagStaleBody"><tr><td colspan="4" class="text-muted small">Loading…</td></tr></tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- Update history -->
                    <div class="card mb-3">
                        <div class="card-header fw-semibold"><i class="bi bi-list-check me-2"></i>Price-update history</div>
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-sm mb-0 align-middle">
                                    <thead><tr><th>When</th><th>Source</th><th class="text-end">Duration</th><th class="text-end">Updated</th><th class="text-end">Skipped</th><th class="text-end">Errors</th><th>Skipped symbols</th></tr></thead>
                                    <tbody id="diagRunsBody"><tr><td colspan="7" class="text-muted small">Loading…</td></tr></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
```

Replace with:
```html
                <!-- Diagnostics Page -->
                <div id="diagnosticsPage" class="page-content" style="display: none;">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div>
                            <h4 class="mb-0"><i class="bi bi-activity me-2 text-primary"></i>Diagnostics</h4>
                            <p class="text-muted small mb-0">Price-data health, update history, and data-quality checks.</p>
                        </div>
                        <div class="d-flex gap-2">
                            <button class="btn btn-sm btn-outline-secondary" id="refreshDiagnostics" title="Refresh current tab"><i class="bi bi-arrow-clockwise"></i></button>
                            <button class="btn btn-sm btn-outline-secondary" onclick="showPageHelp('diagnostics')" title="Help"><i class="bi bi-question-circle"></i></button>
                        </div>
                    </div>

                    <!-- Tab navigation -->
                    <ul class="nav nav-tabs mb-3" id="diagTabs">
                        <li class="nav-item">
                            <button class="nav-link active" data-bs-toggle="tab" data-bs-target="#diagPriceHealth" id="diagTabPrice">
                                <i class="bi bi-clock-history me-1"></i>Price Health
                            </button>
                        </li>
                        <li class="nav-item">
                            <button class="nav-link" data-bs-toggle="tab" data-bs-target="#diagDataQuality" id="diagTabDQ">
                                <i class="bi bi-shield-check me-1"></i>Data Quality
                            </button>
                        </li>
                    </ul>

                    <div class="tab-content">

                        <!-- Price Health tab -->
                        <div class="tab-pane fade show active" id="diagPriceHealth">
                            <!-- Price-data freshness -->
                            <div class="card mb-3">
                                <div class="card-header fw-semibold"><i class="bi bi-clock-history me-2"></i>Price data freshness</div>
                                <div class="card-body" id="diagFreshness">
                                    <div class="text-muted small">Loading…</div>
                                </div>
                            </div>

                            <!-- Stale / unpriced holdings -->
                            <div class="card mb-3">
                                <div class="card-header fw-semibold"><i class="bi bi-exclamation-triangle me-2"></i>Stale &amp; unpriced holdings</div>
                                <div class="card-body p-0">
                                    <div class="table-responsive">
                                        <table class="table table-sm mb-0 align-middle">
                                            <thead><tr><th>Symbol</th><th>Name</th><th class="text-end">Age</th><th>Reason</th></tr></thead>
                                            <tbody id="diagStaleBody"><tr><td colspan="4" class="text-muted small">Loading…</td></tr></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>

                            <!-- Update history -->
                            <div class="card mb-3">
                                <div class="card-header fw-semibold"><i class="bi bi-list-check me-2"></i>Price-update history</div>
                                <div class="card-body p-0">
                                    <div class="table-responsive">
                                        <table class="table table-sm mb-0 align-middle">
                                            <thead><tr><th>When</th><th>Source</th><th class="text-end">Duration</th><th class="text-end">Updated</th><th class="text-end">Skipped</th><th class="text-end">Errors</th><th>Skipped symbols</th></tr></thead>
                                            <tbody id="diagRunsBody"><tr><td colspan="7" class="text-muted small">Loading…</td></tr></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Data Quality tab -->
                        <div class="tab-pane fade" id="diagDataQuality">

                            <!-- Reconciliation -->
                            <div class="card mb-3">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center">
                                    <span>
                                        <i class="bi bi-calculator me-2"></i>Cash &amp; Position Reconciliation
                                        <span class="ms-1 text-muted" style="cursor:help" title="Implied cash = deposits − withdrawals − buys + sells + dividends + interest. Compare against your broker's cash balance."><i class="bi bi-info-circle"></i></span>
                                    </span>
                                    <button class="btn btn-sm btn-outline-secondary" id="dqRerunRecon" title="Re-run"><i class="bi bi-arrow-clockwise"></i></button>
                                </div>
                                <div class="card-body p-0">
                                    <div class="table-responsive">
                                        <table class="table table-sm mb-0 align-middle">
                                            <thead><tr>
                                                <th>Portfolio</th>
                                                <th class="text-end">Implied Cash</th>
                                                <th class="text-end">Invested Value</th>
                                                <th class="text-end">Total Accounted</th>
                                                <th class="text-end">Net Bookings</th>
                                            </tr></thead>
                                            <tbody id="dqReconBody"><tr><td colspan="5" class="text-muted small p-3">Load the Data Quality tab to run checks.</td></tr></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>

                            <!-- Duplicates -->
                            <div class="card mb-3">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center">
                                    <span><i class="bi bi-files me-2"></i>Possible Duplicate Transactions</span>
                                    <button class="btn btn-sm btn-outline-secondary" id="dqRerunDups" title="Re-run"><i class="bi bi-arrow-clockwise"></i></button>
                                </div>
                                <div id="dqDupsBody"><div class="text-muted small p-3">Load the Data Quality tab to run checks.</div></div>
                                <div class="card-footer py-1 border-top-0" id="dqDupsFooter" style="min-height:0"></div>
                            </div>

                            <!-- Suspicious patterns -->
                            <div class="card mb-3">
                                <div class="card-header fw-semibold d-flex justify-content-between align-items-center">
                                    <span><i class="bi bi-exclamation-circle me-2"></i>Suspicious Patterns</span>
                                    <button class="btn btn-sm btn-outline-secondary" id="dqRerunSusp" title="Re-run"><i class="bi bi-arrow-clockwise"></i></button>
                                </div>
                                <div class="card-body p-0">
                                    <div class="table-responsive">
                                        <table class="table table-sm mb-0 align-middle">
                                            <thead><tr>
                                                <th>Severity</th><th>Asset</th><th>Date</th><th>Type</th><th>Issue</th><th>Actions</th>
                                            </tr></thead>
                                            <tbody id="dqSuspBody"><tr><td colspan="6" class="text-muted small p-3">Load the Data Quality tab to run checks.</td></tr></tbody>
                                        </table>
                                    </div>
                                </div>
                                <div class="card-footer py-1 border-top-0" id="dqSuspFooter" style="min-height:0"></div>
                            </div>

                        </div><!-- /diagDataQuality -->

                    </div><!-- /tab-content -->
                </div>
```

- [ ] **Step 2: Commit**

```bash
git add web_client/index.html
git commit -m "feat: restructure Diagnostics page into Price Health / Data Quality tabs"
```

---

## Task 6: JS — `loadDataQualityTab()` function

**Files:**
- Modify: `web_client/js/pfm_core.js`

This is the largest JS change. It adds `loadDataQualityTab()` and updates `loadDiagnosticsPage()` to wire the tabs.

- [ ] **Step 1: Add `loadDataQualityTab()` after `loadDiagnosticsPage()`**

In `pfm_core.js`, find this line (around line 464):

```javascript
window.loadDiagnosticsPage = loadDiagnosticsPage;
```

Insert the entire block below after that line:

```javascript

// ── Data Quality tab ──────────────────────────────────────────────────────────

let _dqLoaded = false;

function _dqDismissed(check, key) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedIssues') || '[]');
    return items.some(i => i.check === check && i.key === key);
}
function _dqDismiss(check, key) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedIssues') || '[]');
    if (!items.some(i => i.check === check && i.key === key)) {
        items.push({ check, key, dismissed_at: new Date().toISOString() });
        localStorage.setItem('pfmDismissedIssues', JSON.stringify(items));
    }
}
function _dqUndismiss(check, key) {
    const items = JSON.parse(localStorage.getItem('pfmDismissedIssues') || '[]');
    localStorage.setItem('pfmDismissedIssues',
        JSON.stringify(items.filter(i => !(i.check === check && i.key === key))));
}

async function loadDataQualityTab(force = false) {
    if (_dqLoaded && !force) return;
    _dqLoaded = true;

    function _wireOnce(id, fn) {
        const btn = document.getElementById(id);
        if (btn && !btn._dqWired) { btn._dqWired = true; btn.addEventListener('click', fn); }
    }
    _wireOnce('dqRerunRecon', () => { _dqLoaded = false; _loadReconCard(); });
    _wireOnce('dqRerunDups',  () => { _dqLoaded = false; _loadDupsCard(); });
    _wireOnce('dqRerunSusp',  () => { _dqLoaded = false; _loadSuspCard(); });

    await Promise.all([_loadReconCard(), _loadDupsCard(), _loadSuspCard()]);

    async function _loadReconCard() {
        const el = document.getElementById('dqReconBody');
        if (!el) return;
        el.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">Loading…</td></tr>';
        const data = await window.apiClient.getDQReconciliation().catch(() => null);
        if (!data) {
            el.innerHTML = '<tr><td colspan="5" class="text-danger small p-3">Could not load reconciliation data.</td></tr>';
            return;
        }
        if (!data.portfolios.length) {
            el.innerHTML = '<tr><td colspan="5" class="text-muted small p-3">No portfolios found.</td></tr>';
            return;
        }
        el.innerHTML = data.portfolios.map(p => `
            <tr>
                <td class="fw-semibold">${esc(p.portfolio_name)}</td>
                <td class="text-end font-monospace">${Fmt.money(p.implied_cash, 'EUR')}</td>
                <td class="text-end font-monospace">${Fmt.money(p.invested_value, 'EUR')}</td>
                <td class="text-end font-monospace fw-semibold">${Fmt.money(p.total_accounted, 'EUR')}</td>
                <td class="text-end small text-muted">${Fmt.money(p.net_bookings, 'EUR')}</td>
            </tr>`).join('');
    }

    async function _loadDupsCard() {
        const body   = document.getElementById('dqDupsBody');
        const footer = document.getElementById('dqDupsFooter');
        if (!body) return;
        body.innerHTML = '<div class="text-muted small p-3">Loading…</div>';
        const data = await window.apiClient.getDQDuplicates().catch(() => null);
        if (!data) {
            body.innerHTML = '<div class="text-danger small p-3">Could not load duplicates.</div>';
            return;
        }
        const dups = data.duplicates || [];
        if (!dups.length) {
            body.innerHTML = '<div class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>No possible duplicates found.</div>';
            if (footer) footer.innerHTML = '';
            return;
        }

        let showDismissed = false;

        function _renderDups() {
            const toShow = showDismissed ? dups : dups.filter(d => !_dqDismissed('dup', d.key));
            if (!toShow.length) {
                body.innerHTML = '<div class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>All findings dismissed.</div>';
            } else {
                body.innerHTML = toShow.map(d => {
                    const isDism = _dqDismissed('dup', d.key);
                    const badge = d.label === 'likely'
                        ? '<span class="badge bg-danger">LIKELY</span>'
                        : '<span class="badge bg-warning text-dark">POSSIBLE</span>';
                    const olderId = d.tx_a.date <= d.tx_b.date ? d.tx_a.id : d.tx_b.id;
                    const op = isDism ? ' opacity-50' : '';
                    return `<div class="border-bottom p-2${op}" data-dup-key="${esc(d.key)}">
                        <div class="d-flex justify-content-between align-items-start mb-1">
                            <div>${badge} <span class="small text-muted">${esc(d.tx_a.portfolio)}</span></div>
                            <div class="btn-group btn-group-sm">
                                <button class="btn btn-outline-danger btn-sm dq-del-older" data-id="${olderId}" data-key="${esc(d.key)}">Delete older</button>
                                <button class="btn btn-outline-danger btn-sm dropdown-toggle dropdown-toggle-split" data-bs-toggle="dropdown"></button>
                                <ul class="dropdown-menu dropdown-menu-end">
                                    <li><button class="dropdown-item dq-del-tx" data-id="${d.tx_a.id}" data-key="${esc(d.key)}">Delete #${d.tx_a.id} (${esc(d.tx_a.date)})</button></li>
                                    <li><button class="dropdown-item dq-del-tx" data-id="${d.tx_b.id}" data-key="${esc(d.key)}">Delete #${d.tx_b.id} (${esc(d.tx_b.date)})</button></li>
                                </ul>
                                <button class="btn btn-outline-secondary btn-sm dq-dism-dup" data-key="${esc(d.key)}">${isDism ? 'Undismiss' : '×'}</button>
                            </div>
                        </div>
                        <div class="row small g-1">
                            <div class="col-6 bg-body-secondary rounded p-1">
                                <div class="fw-semibold">${esc(d.tx_a.asset)}</div>
                                <div>${esc(d.tx_a.type)} · ${Fmt.num(d.tx_a.quantity, 4)} @ ${Fmt.num(d.tx_a.price, 4)}</div>
                                <div class="text-muted">${esc(d.tx_a.date)} · #${d.tx_a.id}</div>
                            </div>
                            <div class="col-6 bg-body-secondary rounded p-1">
                                <div class="fw-semibold">${esc(d.tx_b.asset)}</div>
                                <div>${esc(d.tx_b.type)} · ${Fmt.num(d.tx_b.quantity, 4)} @ ${Fmt.num(d.tx_b.price, 4)}</div>
                                <div class="text-muted">${esc(d.tx_b.date)} · #${d.tx_b.id}</div>
                            </div>
                        </div>
                    </div>`;
                }).join('');

                body.querySelectorAll('.dq-del-older, .dq-del-tx').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const id  = parseInt(btn.dataset.id);
                        const key = btn.dataset.key;
                        if (!confirm(`Delete transaction #${id}?`)) return;
                        try {
                            await window.apiClient.deleteTransaction(id);
                            _dqDismiss('dup', key);
                            await _loadDupsCard();
                        } catch (e) {
                            alert('Failed to delete: ' + e.message);
                        }
                    });
                });

                body.querySelectorAll('.dq-dism-dup').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const key = btn.dataset.key;
                        _dqDismissed('dup', key) ? _dqUndismiss('dup', key) : _dqDismiss('dup', key);
                        _renderDups();
                        _renderDupsFooter();
                    });
                });
            }
        }

        function _renderDupsFooter() {
            if (!footer) return;
            const n = dups.filter(d => _dqDismissed('dup', d.key)).length;
            if (!n) { footer.innerHTML = ''; return; }
            footer.innerHTML = `<button class="btn btn-link btn-sm p-0 text-muted">${showDismissed ? 'Hide' : 'Show'} ${n} dismissed</button>`;
            footer.querySelector('button').addEventListener('click', () => {
                showDismissed = !showDismissed;
                _renderDups();
                _renderDupsFooter();
            });
        }

        _renderDups();
        _renderDupsFooter();
    }

    async function _loadSuspCard() {
        const body   = document.getElementById('dqSuspBody');
        const footer = document.getElementById('dqSuspFooter');
        if (!body) return;
        body.innerHTML = '<tr><td colspan="6" class="text-muted small p-3">Loading…</td></tr>';
        const data = await window.apiClient.getDQSuspicious().catch(() => null);
        if (!data) {
            body.innerHTML = '<tr><td colspan="6" class="text-danger small p-3">Could not load suspicious patterns.</td></tr>';
            return;
        }
        const issues = data.issues || [];
        if (!issues.length) {
            body.innerHTML = '<tr><td colspan="6" class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>No suspicious patterns found.</td></tr>';
            if (footer) footer.innerHTML = '';
            return;
        }

        let showDismissed = false;

        function _renderSusp() {
            const toShow = showDismissed ? issues : issues.filter(i => !_dqDismissed('susp', i.key));
            if (!toShow.length) {
                body.innerHTML = '<tr><td colspan="6" class="text-success small p-3"><i class="bi bi-check-circle me-1"></i>All findings dismissed.</td></tr>';
            } else {
                body.innerHTML = toShow.map(i => {
                    const isDism = _dqDismissed('susp', i.key);
                    const badge = i.severity === 'warning'
                        ? '<span class="badge bg-warning text-dark">warning</span>'
                        : '<span class="badge bg-info text-dark">info</span>';
                    const op = isDism ? ' class="opacity-50"' : '';
                    return `<tr${op}>
                        <td>${badge}</td>
                        <td><code>${esc(i.asset)}</code><div class="small text-muted">${esc(i.asset_name)}</div></td>
                        <td class="small">${esc(i.date)}</td>
                        <td class="small">${esc(i.type)}</td>
                        <td class="small">${esc(i.description)}</td>
                        <td class="text-nowrap">
                            <button class="btn btn-link btn-sm p-0 me-2 dq-view-tx" data-asset="${esc(i.asset)}">View</button>
                            <button class="btn btn-link btn-sm p-0 text-muted dq-dism-susp" data-key="${esc(i.key)}">${isDism ? 'Undismiss' : '×'}</button>
                        </td>
                    </tr>`;
                }).join('');

                body.querySelectorAll('.dq-view-tx').forEach(btn => {
                    btn.addEventListener('click', () => {
                        if (window.navigationManager) window.navigationManager.showPage('transactions');
                        const f = document.getElementById('txAssetFilter');
                        if (f) { f.value = btn.dataset.asset; f.dispatchEvent(new Event('change')); }
                    });
                });

                body.querySelectorAll('.dq-dism-susp').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const key = btn.dataset.key;
                        _dqDismissed('susp', key) ? _dqUndismiss('susp', key) : _dqDismiss('susp', key);
                        _renderSusp();
                        _renderSuspFooter();
                    });
                });
            }
        }

        function _renderSuspFooter() {
            if (!footer) return;
            const n = issues.filter(i => _dqDismissed('susp', i.key)).length;
            if (!n) { footer.innerHTML = ''; return; }
            footer.innerHTML = `<button class="btn btn-link btn-sm p-0 text-muted">${showDismissed ? 'Hide' : 'Show'} ${n} dismissed</button>`;
            footer.querySelector('button').addEventListener('click', () => {
                showDismissed = !showDismissed;
                _renderSusp();
                _renderSuspFooter();
            });
        }

        _renderSusp();
        _renderSuspFooter();
    }
}
window.loadDataQualityTab = loadDataQualityTab;
```

- [ ] **Step 2: Update `loadDiagnosticsPage()` to wire tabs and restore tab state**

In `pfm_core.js`, inside `loadDiagnosticsPage()`, find:

```javascript
    const refreshBtn = document.getElementById('refreshDiagnostics');
    if (refreshBtn && !refreshBtn._wired) {
        refreshBtn._wired = true;
        refreshBtn.addEventListener('click', () => loadDiagnosticsPage());
    }
```

Replace with:

```javascript
    _dqLoaded = false; // reset so DQ refreshes when tab is next activated

    const refreshBtn = document.getElementById('refreshDiagnostics');
    if (refreshBtn && !refreshBtn._wired) {
        refreshBtn._wired = true;
        refreshBtn.addEventListener('click', () => {
            const dqPane = document.getElementById('diagDataQuality');
            const dqActive = dqPane && dqPane.classList.contains('active');
            if (dqActive) {
                _dqLoaded = false;
                loadDataQualityTab();
            } else {
                loadDiagnosticsPage();
            }
        });
    }

    // Wire DQ tab activation (lazy load on first switch)
    const dqTabBtn = document.getElementById('diagTabDQ');
    if (dqTabBtn && !dqTabBtn._dqWired) {
        dqTabBtn._dqWired = true;
        dqTabBtn.addEventListener('shown.bs.tab', () => loadDataQualityTab());
    }

    // Restore last active tab from localStorage
    const lastTab = localStorage.getItem('pfmDiagTab');
    if (lastTab === 'dq') {
        const dqBtn = document.getElementById('diagTabDQ');
        if (dqBtn && window.bootstrap) new window.bootstrap.Tab(dqBtn).show();
    }

    // Persist active tab to localStorage
    document.querySelectorAll('#diagTabs button[data-bs-toggle="tab"]').forEach(btn => {
        btn.addEventListener('shown.bs.tab', () => {
            localStorage.setItem('pfmDiagTab', btn.id === 'diagTabDQ' ? 'dq' : 'price');
        });
    });
```

- [ ] **Step 3: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add loadDataQualityTab() with reconciliation, duplicates, and suspicious cards"
```

---

## Task 7: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `PROJECT_STATUS.md`

- [ ] **Step 1: Add documentation rule to CLAUDE.md**

In `CLAUDE.md`, find the `## Git` section and insert a new rule before it:

```markdown
## Documentation

**Always update documentation when adding or changing features.** This is default behaviour:
- Add new API endpoints to the relevant section in `CLAUDE.md` (under the owning router's section).
- Update `PROJECT_STATUS.md` to reflect the new feature's status.
- Update router/function docstrings when behaviour changes.
- Bump `?v=` cache-busting query strings in `index.html` after any web file change.
```

- [ ] **Step 2: Update PROJECT_STATUS.md**

Open `PROJECT_STATUS.md` and add an entry under the Diagnostics / Analytics section noting:

```
- **Data Quality tab** (Diagnostics page): per-portfolio cash reconciliation, fuzzy
  duplicate detection, suspicious-pattern flagging; inline delete + dismiss with
  localStorage persistence. Backend: `/api/v1/analytics/dq/{reconciliation,duplicates,suspicious}`.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: add documentation-update rule to CLAUDE.md; note data quality feature in PROJECT_STATUS"
```

---

## Task 8: Deploy and verify

- [ ] **Step 1: Run the full unit test suite**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -v 2>&1 | tail -30
```

Expected: all tests pass, 0 failures.

- [ ] **Step 2: Deploy the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

- [ ] **Step 3: Manual smoke test**

1. Open the app and navigate to Diagnostics (Tools → Diagnostics in the sidebar).
2. Verify you see two tabs: "Price Health" and "Data Quality".
3. The Price Health tab should show the existing freshness / stale / update-history cards.
4. Click "Data Quality" — all three cards should load (reconciliation table, duplicates list, suspicious table).
5. Verify the reconciliation table shows your portfolios with implied cash and invested value.
6. Dismiss a suspicious finding (if any) — it should fade out and "Show 1 dismissed" appear.
7. Click "×" on a dismissed item to undismiss — it should reappear.
8. Click the ↺ Re-run button on any card — it should reload that card only.
9. Navigate away and back — Price Health tab should reload; Data Quality should be lazy (reload only if tab was active last).

- [ ] **Step 4: Final commit if any post-deploy tweaks were needed**

```bash
git add -p  # stage only the relevant files
git commit -m "fix: post-deploy tweaks to data quality tab"
```
