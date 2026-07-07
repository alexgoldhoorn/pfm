# Historical FX Tax Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all IRPF tax figures (tax-report, tax-estimate, tax-optimizer) convert non-EUR amounts to EUR at **transaction-date** FX rates instead of current rates, and fix dividend/interest income that was summed with **no** FX conversion at all.

**Architecture:** Add one new function `get_fx_eur_on(db, currency, on_date)` to the shared market-data service (`portf_manager/market.py`) that fetches a full calendar year of `{CUR}EUR=X` daily closes from yfinance in one call and caches the series in `kv_cache` (key `mkt:fxhist:{CUR}:{year}`). Three small helpers in `portf_server/routers/analytics.py` (`_fx_on`, `_savings_income_eur`, `_lot_eur_amounts`) then apply it in the three tax endpoints. No schema change, no frontend change (only response *additions*).

**Tech Stack:** Python 3.13, FastAPI, yfinance, pandas (already a yfinance dependency), pytest, SQLite kv_cache.

## Global Constraints

- Format with black, line length 88: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black <files>`
- All commands via uv with the local venv: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run …` (the project `.venv` is root-owned)
- Comments go on the **line before** the code, never inline
- Type hints on all function signatures; Google-style docstrings
- Conventional commits (`fix:`, `docs:`) with co-author line `Co-Authored-By: Oz <oz-agent@warp.dev>`
- Public repo: tests must use invented asset names ("Example Corp") and fictional prices; never real personal amounts
- Pre-commit hooks run black + flake8 + autoflake; pre-push runs the full unit suite — do not bypass them
- These are **backend-only** changes: after the final task, reload with `docker exec portf_backend_dev kill -HUP 1` (gunicorn has no auto-reload). No web rebuild needed.
- Docs mandate: a feature is not done until `CLAUDE.md` and `PROJECT_STATUS.md` reflect it (Task 6)

## Background: the three bugs being fixed

1. **`GET /api/v1/analytics/tax-estimate`** (`portf_server/routers/analytics.py:498`) — dividend income comes from `dividend_income(all_txns)` which sums `total_amount` raw across currencies (no FX). Interest income (lines 540–546) likewise sums raw `total_amount`. A $100 USD dividend is counted as €100.
2. **`GET /api/v1/analytics/tax-optimizer`** (`analytics.py:593`) — realised FIFO gains are summed with **no FX conversion at all** (line 612), and dividends/interest have the same raw-sum bug as above (lines 617–624).
3. **`GET /api/v1/analytics/tax-report`** (`analytics.py:985`) — converts everything at *current* FX via `_fx()`. Spanish IRPF requires proceeds at sell-date FX and cost basis at purchase-date FX; the FX gain/loss is itself taxable. Same current-rate issue in the dividends/withholding loop (lines 1049–1061) and in tax-estimate's realised-gain loop (lines 513–530).

Key existing code facts (verified):

- `_fx(currency)` in analytics.py is an alias: `from .portfolios import _get_fx_rate as _fx` (line 44) — returns the **current** EUR rate, cached.
- `portf_manager/market.py` has `get_fx_eur(db, currency, max_age) -> tuple[float, bool]` (rate, stale), `_cache_get(db, key)`, `_cache_set(db, key, value)` (7-day store TTL), `_FX_FALLBACK` dict.
- `TaxCalculator.calculate_tax_report(user_id, start_date, end_date)` returns `dict[symbol, list[TaxTransaction]]`; `TaxTransaction` (dataclass in `portf_manager/tax_calculator.py:45`) has `sell_date: date`, `purchase_date: date`, `sell_amount`, `purchase_amount`, `gain_loss`, `sell_quantity`, `holding_period_days` (Decimal/date types).
- Transactions carry per-row `currency` (`COALESCE(t.currency, a.currency)` in all SELECTs).
- Existing test patterns: `tests/unit/test_market_service.py` (fake `yf.Ticker` via monkeypatch), `tests/unit/test_api_routers.py::test_tax_report_shape_with_realised_gain` (seeds buy/sell via API, asserts lot EUR fields).

---

### Task 1: Commit the pending docker-compose memory limit

The working tree has an unrelated, intentional uncommitted change (1.5g memory limit on the backend-dev service). Commit it first so the feature work starts from a clean tree.

**Files:**
- Modify: `docker-compose.yml` (already modified — just commit)

- [ ] **Step 1: Verify the diff is only the memory limit**

Run: `git -P diff docker-compose.yml`
Expected: exactly one hunk adding under the backend-dev service:

```yaml
    deploy:
      resources:
        limits:
          memory: 1.5g
```

If anything else appears in the diff, STOP and report instead of committing.

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: cap backend-dev container memory at 1.5g

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

Note: `.claude/worktrees/*` entries in `git status` are agent worktrees — leave them alone.

---

### Task 2: `market.get_fx_eur_on()` — historical FX rates

**Files:**
- Modify: `portf_manager/market.py` (add two functions after `get_fx_eur`, which ends at line 204)
- Test: `tests/unit/test_market_service.py` (append a new test class)

**Interfaces:**
- Consumes: existing `get_fx_eur(db, currency, max_age)`, `_cache_get`, `_cache_set`, `_FX_FALLBACK`, module-level `yf`, `logger`.
- Produces: `get_fx_eur_on(db, currency: str, on_date: Optional[date]) -> tuple[float, bool]` — EUR rate at (or nearest trading day before) `on_date`; `(rate, stale)` where `stale=True` means it fell back to the current rate. Task 3 depends on this exact signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_market_service.py`:

```python
def _patch_ticker_history(monkeypatch, rates=None, raise_exc=False):
    """Replace market.yf.Ticker with a fake whose .history() returns *rates*.

    *rates* is a {"YYYY-MM-DD": close} dict rendered as a pandas DataFrame
    with a DatetimeIndex and a Close column, matching real yfinance output.
    fast_info also raises so any accidental get_fx_eur live fetch fails loudly.
    """
    import pandas as pd

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def fast_info(self):
            raise RuntimeError("fast_info not available in this test")

        def history(self, start=None, end=None):
            if raise_exc:
                raise RuntimeError("yahoo down")
            idx = pd.to_datetime(list(rates.keys()))
            return pd.DataFrame({"Close": list(rates.values())}, index=idx)

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)


class TestGetFxEurOn:
    def test_eur_short_circuits(self, db):
        assert market.get_fx_eur_on(db, "EUR", date(2024, 6, 3)) == (1.0, False)

    def test_exact_date_hit(self, db, monkeypatch):
        _patch_ticker_history(monkeypatch, {"2024-06-03": 0.92, "2024-06-04": 0.93})
        rate, stale = market.get_fx_eur_on(db, "USD", date(2024, 6, 3))
        assert rate == 0.92
        assert stale is False

    def test_weekend_uses_prior_trading_day(self, db, monkeypatch):
        # 2024-06-09 is a Sunday; nearest prior close is Friday 06-07.
        _patch_ticker_history(monkeypatch, {"2024-06-07": 0.91})
        rate, stale = market.get_fx_eur_on(db, "USD", date(2024, 6, 9))
        assert rate == 0.91
        assert stale is False

    def test_cached_series_avoids_refetch(self, db, monkeypatch):
        _patch_ticker_history(monkeypatch, {"2024-06-03": 0.92})
        market.get_fx_eur_on(db, "USD", date(2024, 6, 3))
        # Second call must be served from kv_cache even though yfinance is down.
        _patch_ticker_history(monkeypatch, raise_exc=True)
        rate, stale = market.get_fx_eur_on(db, "USD", date(2024, 6, 3))
        assert rate == 0.92
        assert stale is False

    def test_fetch_failure_falls_back_to_current_rate(self, db, monkeypatch):
        _patch_ticker_history(monkeypatch, raise_exc=True)
        rate, stale = market.get_fx_eur_on(db, "USD", date(2024, 6, 3))
        # History and fast_info both fail → hard fallback table, flagged stale.
        assert rate == market._FX_FALLBACK["USD"]
        assert stale is True

    def test_today_or_future_delegates_to_current_rate(self, db, monkeypatch):
        monkeypatch.setattr(
            market, "get_fx_eur", lambda d, cur, max_age=3600: (0.5, False)
        )
        assert market.get_fx_eur_on(db, "USD", date.today()) == (0.5, False)

    def test_none_date_delegates_to_current_rate(self, db, monkeypatch):
        monkeypatch.setattr(
            market, "get_fx_eur", lambda d, cur, max_age=3600: (0.5, False)
        )
        assert market.get_fx_eur_on(db, "USD", None) == (0.5, False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py::TestGetFxEurOn -v`
Expected: all FAIL/ERROR with `AttributeError: module 'portf_manager.market' has no attribute 'get_fx_eur_on'`

- [ ] **Step 3: Implement `get_fx_eur_on`**

Add to `portf_manager/market.py` immediately after `get_fx_eur` (after line 204):

```python
def _rate_at_or_before(
    rates: dict, on_date: date, max_lookback: int = 7
) -> Optional[float]:
    """Close for *on_date* or the nearest prior day within *max_lookback* days."""
    for i in range(max_lookback + 1):
        value = rates.get(str(on_date - timedelta(days=i)))
        if value:
            return float(value)
    return None


def get_fx_eur_on(db, currency: str, on_date: Optional[date]) -> tuple[float, bool]:
    """EUR rate for *currency* on *on_date* (nearest prior trading day).

    Daily closes for the ``{CUR}EUR=X`` ticker are fetched one calendar year
    at a time (single yfinance call) and cached under
    ``mkt:fxhist:{CUR}:{year}``. Historical closes never change, so a cache
    hit is always fresh. Falls back to the current rate with ``stale=True``
    when history is unavailable; ``on_date`` of None/today/future delegates
    to :func:`get_fx_eur` directly.

    Args:
        db: Database handle for the kv_cache (may be None).
        currency: ISO currency code, e.g. "USD".
        on_date: The historical date to price, or None for "now".

    Returns:
        (rate, stale) — stale=True means the rate is NOT transaction-date
        accurate (current-rate fallback was used).
    """
    cur = currency.strip().upper()
    if cur == "EUR":
        return 1.0, False
    if on_date is None or on_date >= date.today():
        return get_fx_eur(db, cur)
    key = f"mkt:fxhist:{cur}:{on_date.year}"
    cached = _cache_get(db, key)
    rates = (cached or {}).get("rates", {})
    rate = _rate_at_or_before(rates, on_date)
    if rate is not None:
        return rate, False
    try:
        # Start in late December of the prior year so early-January dates can
        # look back to the last trading days of the previous year.
        hist = yf.Ticker(f"{cur}EUR=X").history(
            start=f"{on_date.year - 1}-12-20", end=f"{on_date.year + 1}-01-01"
        )
        fetched = {
            str(idx.date()): round(float(close), 6)
            for idx, close in hist["Close"].items()
            if close and close > 0
        }
        if fetched:
            rates.update(fetched)
            _cache_set(db, key, {"rates": rates, "fetched_at": time.time()})
            rate = _rate_at_or_before(rates, on_date)
            if rate is not None:
                return rate, False
    except Exception as e:
        logger.warning(f"FX history {cur}->EUR fetch failed for {on_date}: {e}")
    return get_fx_eur(db, cur)[0], True
```

`Optional`, `date`, `timedelta`, and `time` are already imported at the top of `market.py` — do not re-import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_market_service.py -v`
Expected: all PASS (the whole file, not just the new class — the existing `TestGetFxEur` tests must not regress)

- [ ] **Step 5: Format and commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_manager/market.py tests/unit/test_market_service.py
git add portf_manager/market.py tests/unit/test_market_service.py
git commit -m "feat: historical FX rates via market.get_fx_eur_on (kv-cached per year)

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 3: analytics helpers `_fx_on`, `_savings_income_eur`, `_lot_eur_amounts`

**Files:**
- Modify: `portf_server/routers/analytics.py` (add three module-level helpers near `_fx`; the alias `from .portfolios import _get_fx_rate as _fx` is at line 44)
- Test: `tests/unit/test_analytics.py` (append a new test class)

**Interfaces:**
- Consumes: `market.get_fx_eur_on(db, currency, on_date) -> tuple[float, bool]` from Task 2; existing `_fx(currency) -> float` alias.
- Produces (Tasks 4 and 5 call these exact signatures):
  - `_fx_on(db, currency: str, on_date) -> float` — accepts `date`, `"YYYY-MM-DD..."` string, or None; returns the transaction-date EUR rate, falling back to the current rate.
  - `_savings_income_eur(db, transactions: list, yr: int) -> tuple[float, float]` — `(dividends_eur, interest_eur)` for the year, each converted at per-transaction-date FX.
  - `_lot_eur_amounts(db, currency: str, t) -> tuple[float, float]` — `(proceeds_eur, cost_basis_eur)` for one `TaxTransaction` (proceeds at sell-date FX, cost at purchase-date FX).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_analytics.py`:

```python
from datetime import date as _date
from decimal import Decimal
from types import SimpleNamespace

from portf_server.routers import analytics as analytics_router


class TestHistoricalFxHelpers:
    def _patch_rates(self, monkeypatch, table):
        """Route _fx_on through a {(cur, 'YYYY-MM-DD'): rate} lookup table."""

        def fake(db, cur, on_date):
            key = (cur.upper(), str(on_date)[:10])
            return table.get(key, 1.0)

        monkeypatch.setattr(analytics_router, "_fx_on", fake)

    def test_savings_income_eur_converts_per_transaction(self, monkeypatch):
        self._patch_rates(
            monkeypatch,
            {("USD", "2025-03-01"): 0.9, ("USD", "2025-05-01"): 0.8},
        )
        txns = [
            {
                "transaction_type": "dividend",
                "transaction_date": "2025-03-01",
                "total_amount": 100,
                "currency": "USD",
            },
            {
                "transaction_type": "dividend",
                "transaction_date": "2025-04-01",
                "total_amount": 50,
                "currency": "EUR",
            },
            {
                "transaction_type": "interest",
                "transaction_date": "2025-05-01",
                "total_amount": 20,
                "currency": "USD",
            },
            # Wrong year — must be ignored.
            {
                "transaction_type": "dividend",
                "transaction_date": "2024-03-01",
                "total_amount": 999,
                "currency": "USD",
            },
            # Not income — must be ignored.
            {
                "transaction_type": "buy",
                "transaction_date": "2025-03-01",
                "total_amount": 999,
                "currency": "USD",
            },
        ]
        div, interest = analytics_router._savings_income_eur(None, txns, 2025)
        assert div == pytest.approx(100 * 0.9 + 50)
        assert interest == pytest.approx(20 * 0.8)

    def test_lot_eur_amounts_uses_both_dates(self, monkeypatch):
        self._patch_rates(
            monkeypatch,
            {("USD", "2024-01-10"): 0.9, ("USD", "2024-06-01"): 0.8},
        )
        lot = SimpleNamespace(
            sell_date=_date(2024, 6, 1),
            purchase_date=_date(2024, 1, 10),
            sell_amount=Decimal("750"),
            purchase_amount=Decimal("500"),
        )
        proceeds_eur, cost_eur = analytics_router._lot_eur_amounts(None, "USD", lot)
        assert proceeds_eur == pytest.approx(750 * 0.8)
        assert cost_eur == pytest.approx(500 * 0.9)

    def test_fx_on_eur_is_one_without_any_lookup(self):
        # db=None would crash any real lookup — EUR must short-circuit first.
        assert analytics_router._fx_on(None, "EUR", "2024-06-01") == 1.0

    def test_fx_on_parses_string_dates(self, monkeypatch):
        seen = {}

        def fake_market_fx(db, cur, on_date):
            seen["on_date"] = on_date
            return 0.77, False

        monkeypatch.setattr(
            analytics_router.market, "get_fx_eur_on", fake_market_fx
        )
        assert analytics_router._fx_on(None, "usd", "2024-06-01 15:30:00") == 0.77
        assert seen["on_date"] == _date(2024, 6, 1)

    def test_fx_on_bad_date_falls_back_to_current(self, monkeypatch):
        monkeypatch.setattr(analytics_router, "_fx", lambda cur: 0.5)
        assert analytics_router._fx_on(None, "USD", "not-a-date") == 0.5
```

`pytest` is already imported at the top of `test_analytics.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py::TestHistoricalFxHelpers -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_savings_income_eur'` (and similar)

- [ ] **Step 3: Implement the helpers**

In `portf_server/routers/analytics.py`:

First, add the market import next to the existing portf_manager imports near the top of the file (it is not currently imported):

```python
from portf_manager import market
```

Then add the three helpers directly below the `from .portfolios import _get_fx_rate as _fx` alias (line 44):

```python
# Historical rates are immutable — memoise per (currency, date) for the
# lifetime of the worker so per-lot loops don't re-hit the kv_cache.
_FX_HIST_MEMO: dict[tuple[str, str], float] = {}


def _fx_on(db, currency: str, on_date) -> float:
    """EUR rate at *on_date* (transaction-date FX); current rate as fallback.

    Accepts a date, a 'YYYY-MM-DD...' string, or None. Only genuinely
    historical (non-stale) rates are memoised.
    """
    cur = (currency or "EUR").strip().upper()
    if cur == "EUR":
        return 1.0
    if isinstance(on_date, str):
        try:
            on_date = datetime.strptime(on_date[:10], "%Y-%m-%d").date()
        except ValueError:
            on_date = None
    if on_date is None:
        return _fx(cur)
    memo_key = (cur, on_date.isoformat())
    if memo_key in _FX_HIST_MEMO:
        return _FX_HIST_MEMO[memo_key]
    rate, stale = market.get_fx_eur_on(db, cur, on_date)
    if not stale:
        _FX_HIST_MEMO[memo_key] = rate
    return rate


def _savings_income_eur(db, transactions: list, yr: int) -> tuple[float, float]:
    """Dividend and interest income for *yr* in EUR at transaction-date FX.

    Returns:
        (dividends_eur, interest_eur) — the two savings-base income legs.
    """
    dividends = 0.0
    interest = 0.0
    for tx in transactions:
        tx_type = (tx.get("transaction_type") or "").lower()
        if tx_type not in ("dividend", "interest"):
            continue
        d = str(tx.get("transaction_date", ""))[:10]
        if d[:4] != str(yr):
            continue
        cur = (tx.get("currency") or "EUR").upper()
        amount_eur = float(tx.get("total_amount") or 0) * _fx_on(db, cur, d)
        if tx_type == "dividend":
            dividends += amount_eur
        else:
            interest += amount_eur
    return dividends, interest


def _lot_eur_amounts(db, currency: str, t) -> tuple[float, float]:
    """(proceeds_eur, cost_basis_eur) for one TaxTransaction lot.

    IRPF rule: proceeds convert at sell-date FX, cost basis at purchase-date
    FX — the FX gain/loss is itself part of the taxable result.
    """
    proceeds = float(getattr(t, "sell_amount", 0) or 0)
    cost = float(getattr(t, "purchase_amount", 0) or 0)
    fx_sell = _fx_on(db, currency, getattr(t, "sell_date", None))
    fx_buy = _fx_on(db, currency, getattr(t, "purchase_date", None))
    return proceeds * fx_sell, cost * fx_buy
```

`datetime` is already imported in analytics.py (used at line 1054) — do not re-import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v`
Expected: all PASS (existing tests in the file must not regress)

- [ ] **Step 5: Format and commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/analytics.py tests/unit/test_analytics.py
git add portf_server/routers/analytics.py tests/unit/test_analytics.py
git commit -m "feat: transaction-date FX helpers for tax endpoints

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 4: Fix dividend/interest FX in tax-estimate and tax-optimizer

**Files:**
- Modify: `portf_server/routers/analytics.py` — `get_tax_estimate` (lines ~534–546) and `get_tax_optimizer` (lines ~616–625)
- Test: `tests/unit/test_analytics.py` (extend `TestHistoricalFxHelpers` or add endpoint tests to the existing endpoint test class)

**Interfaces:**
- Consumes: `_savings_income_eur(db, transactions, yr) -> tuple[float, float]` from Task 3.
- Produces: unchanged response schemas — `dividend_income_eur` / `interest_income_eur` keys now actually contain EUR.

- [ ] **Step 1: Write the failing endpoint test**

Append to `tests/unit/test_analytics.py` (inside the module, as a new class; `AsyncClient` and `status` are already imported):

```python
class TestTaxEstimateFx:
    @pytest.mark.asyncio
    async def test_usd_dividend_converted_at_transaction_date_fx(
        self, async_test_client, auth_headers, monkeypatch
    ):
        # Any USD lookup resolves at 0.5 so conversion is unmistakable.
        monkeypatch.setattr(
            analytics_router,
            "_fx_on",
            lambda db, cur, d: 0.5 if cur.upper() == "USD" else 1.0,
        )
        p = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "FX Div Broker", "base_currency": "EUR"},
            headers=auth_headers,
        )
        portfolio_id = p.json()["id"]
        a = await async_test_client.post(
            "/api/v1/assets",
            json={
                "symbol": "FXDIV",
                "name": "Example Corp",
                "asset_type": "stock",
                "currency": "USD",
            },
            headers=auth_headers,
        )
        asset_id = a.json()["id"]
        year = _date.today().year
        r = await async_test_client.post(
            "/api/v1/transactions",
            json={
                "asset_id": asset_id,
                "transaction_type": "dividend",
                "quantity": 1,
                "price": 100.0,
                "total_amount": 100.0,
                "transaction_date": f"{year}-02-15",
                "portfolio_id": portfolio_id,
                "currency": "USD",
                "user_id": 1,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200

        resp = await async_test_client.get(
            f"/api/v1/analytics/tax-estimate?year={year}", headers=auth_headers
        )
        assert resp.status_code == 200
        # $100 at 0.5 → €50, not €100 (the old raw-sum bug).
        assert resp.json()["dividend_income_eur"] == pytest.approx(50.0)
```

The `async_test_client` and `auth_headers` fixtures already exist in the test suite's conftest (used by `test_tax_estimate_endpoint` in this same file).

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py::TestTaxEstimateFx -v`
Expected: FAIL with `assert 100.0 == approx(50.0)` (the endpoint still raw-sums)

- [ ] **Step 3: Wire `_savings_income_eur` into both endpoints**

In `get_tax_estimate`, replace this block (lines ~534–546):

```python
    # Dividend income this year
    all_txns = db.get_all_transactions()
    div = dividend_income(all_txns)
    div_this_year = div["by_year"].get(str(yr), 0.0)

    # Interest income this year (P2P / savings — taxed in the savings base too)
    interest_this_year = 0.0
    for tx in all_txns:
        if (tx.get("transaction_type") or "").lower() != "interest":
            continue
        d = str(tx.get("transaction_date", ""))[:10]
        if d[:4] == str(yr):
            interest_this_year += float(tx.get("total_amount") or 0)
```

with:

```python
    # Dividend + interest income this year, converted at transaction-date FX
    all_txns = db.get_all_transactions()
    div_this_year, interest_this_year = _savings_income_eur(db, all_txns, yr)
```

In `get_tax_optimizer`, replace this block (lines ~616–625):

```python
    # Income this year (dividends + interest) — both in the savings base
    all_txns = db.get_all_transactions()
    div_this_year = dividend_income(all_txns)["by_year"].get(str(yr), 0.0)
    interest_this_year = sum(
        float(t.get("total_amount") or 0)
        for t in all_txns
        if (t.get("transaction_type") or "").lower() == "interest"
        and str(t.get("transaction_date", ""))[:4] == str(yr)
    )
    income = div_this_year + interest_this_year
```

with:

```python
    # Income this year (dividends + interest) at transaction-date FX
    all_txns = db.get_all_transactions()
    div_this_year, interest_this_year = _savings_income_eur(db, all_txns, yr)
    income = div_this_year + interest_this_year
```

Check whether `dividend_income` is still used elsewhere in analytics.py (it is — line 164) so keep its import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v`
Expected: all PASS, including the pre-existing `test_tax_estimate_endpoint`

- [ ] **Step 5: Format and commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/analytics.py tests/unit/test_analytics.py
git add portf_server/routers/analytics.py tests/unit/test_analytics.py
git commit -m "fix: tax-estimate/optimizer convert dividend+interest income at transaction-date FX

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 5: Historical FX for realised lots (tax-report, tax-estimate, tax-optimizer)

**Files:**
- Modify: `portf_server/routers/analytics.py` — `get_tax_report` (lines ~1013–1074), `get_tax_estimate` realised loop (lines ~513–530), `get_tax_optimizer` realised loop (lines ~606–614)
- Test: `tests/unit/test_api_routers.py` (add one test to `TestAnalyticsRouter`)

**Interfaces:**
- Consumes: `_lot_eur_amounts(db, currency, t)` and `_fx_on(db, currency, on_date)` from Task 3.
- Produces: tax-report lots gain a new `purchase_date` key; `proceeds_eur`/`cost_basis_eur`/`gain_loss_eur` semantics change from current-FX to transaction-date FX (`gain_loss_eur = proceeds_eur - cost_basis_eur`, which now includes the FX gain/loss as IRPF requires). Native-currency keys unchanged. No key is removed, so the web client needs no changes.

- [ ] **Step 1: Write the failing test**

Append to `TestAnalyticsRouter` in `tests/unit/test_api_routers.py`:

```python
    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_tax_report_uses_transaction_date_fx(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        """USD lot: proceeds at sell-date FX, cost basis at purchase-date FX."""
        from portf_server.routers import analytics as analytics_router

        rates = {"2024-01-10": 0.90, "2024-06-01": 0.80}

        def fake_fx_on(db, cur, on_date):
            if cur.upper() == "EUR":
                return 1.0
            return rates.get(str(on_date)[:10], 1.0)

        monkeypatch.setattr(analytics_router, "_fx_on", fake_fx_on)

        p = await async_test_client.post(
            "/api/v1/portfolios",
            json={"name": "FX Lot Broker", "base_currency": "EUR"},
            headers=auth_headers,
        )
        portfolio_id = p.json()["id"]
        a = await async_test_client.post(
            "/api/v1/assets",
            json={
                "symbol": "FXLOT",
                "name": "Example FX Corp",
                "asset_type": "stock",
                "currency": "USD",
            },
            headers=auth_headers,
        )
        asset_id = a.json()["id"]
        # Buy 10 @ $100 on 2024-01-10, sell 5 @ $150 on 2024-06-01.
        for tx in [
            {
                "transaction_type": "buy",
                "quantity": 10,
                "price": 100.0,
                "total_amount": 1000.0,
                "transaction_date": "2024-01-10",
            },
            {
                "transaction_type": "sell",
                "quantity": 5,
                "price": 150.0,
                "total_amount": 750.0,
                "transaction_date": "2024-06-01",
            },
        ]:
            r = await async_test_client.post(
                "/api/v1/transactions",
                json={
                    **tx,
                    "asset_id": asset_id,
                    "portfolio_id": portfolio_id,
                    "currency": "USD",
                    "user_id": 1,
                },
                headers=auth_headers,
            )
            assert r.status_code == 200

        resp = await async_test_client.get(
            "/api/v1/analytics/tax-report?year=2024", headers=auth_headers
        )
        assert resp.status_code == 200
        lot = next(
            lo for lo in resp.json()["realised_lots"] if lo["symbol"] == "FXLOT"
        )
        # $750 proceeds at sell-date 0.80 → €600.
        assert lot["proceeds_eur"] == pytest.approx(600.0, rel=0.01)
        # $500 cost at purchase-date 0.90 → €450.
        assert lot["cost_basis_eur"] == pytest.approx(450.0, rel=0.01)
        # Gain includes the FX loss: 600 - 450 = €150 (NOT $250 × one rate).
        assert lot["gain_loss_eur"] == pytest.approx(150.0, rel=0.01)
        assert lot["purchase_date"] == "2024-01-10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_api_routers.py::TestAnalyticsRouter::test_tax_report_uses_transaction_date_fx -v`
Expected: FAIL — old code converts gain 250 at one current rate, and `purchase_date` key is missing

- [ ] **Step 3: Rewrite the three realised-gain loops**

**(a) `get_tax_report`** — replace the lot loop body (lines ~1015–1040):

```python
        for symbol, txns in report.items():
            currency = asset_currencies.get(symbol, "EUR")
            fx = _fx(currency)
            for t in txns:
                # TaxTransaction uses sell_quantity / sell_amount / purchase_amount
                qty = float(getattr(t, "sell_quantity", 0) or 0)
                proceeds = float(getattr(t, "sell_amount", 0) or 0)
                cost_basis = float(getattr(t, "purchase_amount", 0) or 0)
                gain = float(getattr(t, "gain_loss", 0) or 0)
                total_gain_eur += gain * fx
                lots.append(
                    {
                        "symbol": symbol,
                        "name": asset_names.get(symbol, ""),
                        "sell_date": str(getattr(t, "sell_date", "")),
                        "quantity": qty,
                        "currency": currency,
                        "proceeds": round(proceeds, 2),
                        "cost_basis": round(cost_basis, 2),
                        "gain_loss": round(gain, 2),
                        "proceeds_eur": round(proceeds * fx, 2),
                        "cost_basis_eur": round(cost_basis * fx, 2),
                        "gain_loss_eur": round(gain * fx, 2),
                        "holding_days": getattr(t, "holding_period_days", None),
                    }
                )
```

with:

```python
        for symbol, txns in report.items():
            currency = asset_currencies.get(symbol, "EUR")
            for t in txns:
                # TaxTransaction uses sell_quantity / sell_amount / purchase_amount
                qty = float(getattr(t, "sell_quantity", 0) or 0)
                proceeds = float(getattr(t, "sell_amount", 0) or 0)
                cost_basis = float(getattr(t, "purchase_amount", 0) or 0)
                gain = float(getattr(t, "gain_loss", 0) or 0)
                # IRPF: proceeds at sell-date FX, cost at purchase-date FX; the
                # FX gain/loss is part of the taxable result.
                proceeds_eur, cost_basis_eur = _lot_eur_amounts(db, currency, t)
                gain_eur = proceeds_eur - cost_basis_eur
                total_gain_eur += gain_eur
                lots.append(
                    {
                        "symbol": symbol,
                        "name": asset_names.get(symbol, ""),
                        "sell_date": str(getattr(t, "sell_date", "")),
                        "purchase_date": str(getattr(t, "purchase_date", "")),
                        "quantity": qty,
                        "currency": currency,
                        "proceeds": round(proceeds, 2),
                        "cost_basis": round(cost_basis, 2),
                        "gain_loss": round(gain, 2),
                        "proceeds_eur": round(proceeds_eur, 2),
                        "cost_basis_eur": round(cost_basis_eur, 2),
                        "gain_loss_eur": round(gain_eur, 2),
                        "holding_days": getattr(t, "holding_period_days", None),
                    }
                )
```

**(b) `get_tax_report` dividends/withholding loop** — in the same function, change the per-dividend conversion (lines ~1057–1061) from:

```python
        if start <= dd <= end:
            cur = (tx.get("currency") or "EUR").upper()
            fx = _fx(cur)
            withholding_eur += float(tx.get("tax") or 0) * fx
            dividends_gross_eur += float(tx.get("total_amount") or 0) * fx
```

to:

```python
        if start <= dd <= end:
            cur = (tx.get("currency") or "EUR").upper()
            fx = _fx_on(db, cur, dd)
            withholding_eur += float(tx.get("tax") or 0) * fx
            dividends_gross_eur += float(tx.get("total_amount") or 0) * fx
```

and update the response `note` from:

```python
        "note": (
            "FIFO realised gains converted to EUR at current FX rates. "
            "Withholding is the tax already paid at source on dividends."
        ),
```

to:

```python
        "note": (
            "FIFO realised gains converted to EUR at transaction-date FX "
            "(proceeds at sell-date, cost at purchase-date). Withholding is "
            "the tax already paid at source on dividends."
        ),
```

**(c) `get_tax_estimate` realised loop** — replace (lines ~515–521):

```python
        for sym, txns in report.items():
            a = db.get_asset_by_symbol(sym)
            currency = ((a or {}).get("currency") or "EUR").upper()
            fx = _fx(currency)
            sym_total_eur = sum(
                float(getattr(t, "gain_loss", 0) or 0) * fx for t in txns
            )
```

with:

```python
        for sym, txns in report.items():
            a = db.get_asset_by_symbol(sym)
            currency = ((a or {}).get("currency") or "EUR").upper()
            sym_total_eur = 0.0
            for t in txns:
                proceeds_eur, cost_eur = _lot_eur_amounts(db, currency, t)
                sym_total_eur += proceeds_eur - cost_eur
```

**(d) `get_tax_optimizer` realised loop** — this one had NO FX conversion at all. Replace (lines ~610–612):

```python
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for _sym, txns in report.items():
            realised_gain += sum(float(getattr(t, "gain_loss", 0) or 0) for t in txns)
```

with:

```python
        report = calc.calculate_tax_report(user_id=1, start_date=start, end_date=end)
        for sym, txns in report.items():
            a = db.get_asset_by_symbol(sym)
            currency = ((a or {}).get("currency") or "EUR").upper()
            for t in txns:
                proceeds_eur, cost_eur = _lot_eur_amounts(db, currency, t)
                realised_gain += proceeds_eur - cost_eur
```

- [ ] **Step 4: Run the affected test files**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_api_routers.py tests/unit/test_analytics.py -v`
Expected: all PASS — including the pre-existing EUR-lot test `test_tax_report_shape_with_realised_gain` (EUR short-circuits to 1.0 in `_fx_on`, so its numbers are unchanged) and `tests/unit/test_analytics.py::…::test_tax_report_shape`

- [ ] **Step 5: Run the full unit suite**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
Expected: 0 failures (was 705 passed / 6 skipped before this work; count will be higher now)

- [ ] **Step 6: Format and commit**

```bash
UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run black portf_server/routers/analytics.py tests/unit/test_api_routers.py
git add portf_server/routers/analytics.py tests/unit/test_api_routers.py
git commit -m "fix: tax-report/estimate/optimizer realised gains at transaction-date FX

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

### Task 6: Docs, version bump, deploy, live verification

**Files:**
- Modify: `portf_server/settings.py:82` (version `2.5.7` → `2.5.8`)
- Modify: `CLAUDE.md` (three spots, see below)
- Modify: `PROJECT_STATUS.md` (header date, Recent line, Pending Work, test count)

- [ ] **Step 1: Bump the API version**

In `portf_server/settings.py` line 82, change `default="2.5.7"` to `default="2.5.8"`.

- [ ] **Step 2: Update CLAUDE.md**

Three edits:

1. In the **Analytics API** section, the `tax-report` bullet currently says "all amounts converted to EUR via `_fx()`". Change that clause to: "all amounts converted to EUR at **transaction-date FX** via `_fx_on()` (proceeds at sell-date, cost basis at purchase-date; dividends/withholding at dividend date)" and add `purchase_date` to the listed lot response keys.
2. In the **Market Data API** section, add to the endpoint/cache list: "`portf_manager.market.get_fx_eur_on(db, currency, on_date)` — historical FX at a date (nearest prior trading day), one yfinance call per currency-year, kv_cache key `mkt:fxhist:{CUR}:{year}`. Returns `(rate, stale)`; stale=True means current-rate fallback."
3. In **Important Gotchas**, the Linting bullet says "~11 known warnings in `cli.py`/`portfolio_aware_agent.py`" — that is stale; flake8 is clean. Change to: "flake8 currently reports 0 warnings — keep it that way."

- [ ] **Step 3: Update PROJECT_STATUS.md**

1. Change `Last updated: 2026-06-25` to `Last updated: 2026-07-04`.
2. Insert a new Recent line above the v2.5.7 one:

```markdown
**Recent (v2.5.8):** **Transaction-date FX for all tax figures** — new `market.get_fx_eur_on(db, currency, date)` (per-currency-year yfinance history, kv-cached as `mkt:fxhist:{CUR}:{year}`); tax-report lots now convert proceeds at sell-date FX and cost basis at purchase-date FX (`gain_loss_eur` includes the FX gain/loss, as IRPF requires) and each lot carries `purchase_date`; tax-estimate and tax-optimizer convert dividend + interest income per transaction at its own date/currency (previously raw-summed across currencies) and realised gains per lot at historical FX (tax-optimizer previously applied no FX at all).
```

3. In **Pending Work**, delete the `dividend_income FX` item (now fixed). Keep the web-client smoke test item.
4. Update the **Test Status** section with the real counts from Task 5 Step 5's output and today's date.

- [ ] **Step 4: Commit**

```bash
git add portf_server/settings.py CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: v2.5.8 — transaction-date FX for tax figures

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

(The pre-push hook runs the full unit suite on push; that is expected and must pass.)

- [ ] **Step 5: Deploy — reload the backend**

Backend Python changed; gunicorn has no auto-reload:

```bash
docker exec portf_backend_dev kill -HUP 1
sleep 3
curl -s http://localhost:8000/ | head -c 200
```

Expected: JSON containing `"version":"2.5.8"`.

- [ ] **Step 6: Live verification against the real portfolio**

```bash
API_KEY=$(grep -E '^(SERVER_API_KEY|PORTF_API_KEY)=' ~/repos/pfm/.env.local | head -1 | cut -d= -f2)
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/api/v1/analytics/tax-report?year=2026" | python3 -m json.tool | head -40
curl -s -H "X-API-Key: $API_KEY" "http://localhost:8000/api/v1/analytics/tax-estimate?year=2026" | python3 -m json.tool | head -20
```

Expected: both return 200 JSON; tax-report lots include `purchase_date`; the note mentions transaction-date FX; no 500s. The first call may take a few extra seconds while FX history series are fetched and cached — a second identical call should be fast. Check the log for FX warnings:

```bash
docker logs portf_backend_dev --since 5m 2>&1 | grep -i "fx\|error" | head
```

Expected: no tracebacks (a "FX history … fetch failed" warning for an exotic currency is acceptable — it falls back to current rate).

- [ ] **Step 7: Report deployment status**

State explicitly in the final summary: backend reloaded via HUP and verified live at v2.5.8; no web rebuild was needed (no `web_client/` changes). If a Todoist ticket in **#Dev Projects / #pfm** covers the dividend-FX pending item, note that it can be closed (or close it if Todoist access is available).

---

## Self-review notes

- **Spec coverage:** dividend_income FX (Task 4), historical FX in tax-report (Task 5a/5b), plus the two same-family bugs found during research: tax-optimizer's missing FX (5d) and tax-estimate's current-rate realised gains (5c). Housekeeping: docker-compose commit (Task 1), stale CLAUDE.md lint note (Task 6).
- **Type consistency:** `get_fx_eur_on` returns `tuple[float, bool]`; `_fx_on` returns bare `float` (used by all call sites); `_savings_income_eur` returns `(float, float)`; `_lot_eur_amounts` returns `(float, float)`. TaxTransaction fields accessed only via `getattr` with the verified names (`sell_amount`, `purchase_amount`, `sell_date`, `purchase_date`, `sell_quantity`, `gain_loss`, `holding_period_days`).
- **Behavioural invariant:** EUR-only portfolios see zero numeric change everywhere (`_fx_on` short-circuits EUR to 1.0), which is why every pre-existing EUR-based test must keep passing untouched.
- Line numbers cited are from the pre-change file; use the quoted code blocks (not the line numbers) to locate edit sites if they have drifted.
