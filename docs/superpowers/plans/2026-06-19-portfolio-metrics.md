# Portfolio Metrics Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CAGR, Annualized Gain, Inception Date, 3Y/5Y period windows, Sortino, Calmar, Beta, and Alpha to the Analytics and Dashboard pages.

**Architecture:** Pure analytics functions live in `analytics_service.py` (pure, testable, no I/O); routers call them and handle yfinance I/O (cached). Frontend reads new fields from existing endpoints — no new API calls from the browser.

**Tech Stack:** Python 3.13, FastAPI, yfinance, pandas, statistics stdlib, vanilla JS + Bootstrap 5.

---

## File Map

| File | Change |
|---|---|
| `portf_manager/services/analytics_service.py` | Add `compute_cagr`, `sortino_ratio`, `calmar_ratio`, `compute_beta_alpha`; extend `period_start_date` |
| `portf_server/routers/analytics.py` | Extend `get_performance()` response; rewrite `get_risk()` (async→def, new fields) |
| `web_client/index.html` | Add 3Y/5Y radio buttons; add `#dashCagrLine` div |
| `web_client/js/pfm_core.js` | Add `benchmark` param to `apiClient.getRisk()` |
| `web_client/js/help_text.js` | New `METRIC_HELP` entries; extend `PAGE_HELP.analytics` body |
| `web_client/js/pfm_analytics.js` | Performance tab new cards; risk tab new cards; dashboard CAGR sub-line |
| `tests/unit/test_analytics.py` | Tests for all new pure functions and new endpoint fields |
| `CLAUDE.md` | Document new endpoint fields and new analytics_service functions |
| `PROJECT_STATUS.md` | Bump last-updated, add feature to Recent summary |

---

## Task 1: Pure analytics functions + period_start_date extension

**Files:**
- Modify: `portf_manager/services/analytics_service.py`
- Test: `tests/unit/test_analytics.py`

- [ ] **Step 1: Write the failing tests**

Add a new `TestNewMetrics` class to `tests/unit/test_analytics.py`:

```python
import math
import statistics


class TestNewMetrics:
    def test_compute_cagr_basic(self):
        from datetime import date
        from portf_manager.services.analytics_service import compute_cagr

        # 1000 invested 2 years ago, now worth 1210, 0 realised → 10% CAGR
        inception = date(date.today().year - 2, date.today().month, min(date.today().day, 28))
        result = compute_cagr(1000.0, 1210.0, 0.0, inception)
        assert result is not None
        assert abs(result - 10.0) < 0.5

    def test_compute_cagr_none_cases(self):
        from datetime import date
        from portf_manager.services.analytics_service import compute_cagr

        recent = date(date.today().year, 1, 1)  # less than 1 year
        assert compute_cagr(1000.0, 1200.0, 0.0, recent) is None
        assert compute_cagr(0.0, 1200.0, 0.0, date(2020, 1, 1)) is None
        assert compute_cagr(1000.0, 1200.0, 0.0, None) is None

    def test_sortino_ratio_basic(self):
        from portf_manager.services.analytics_service import sortino_ratio

        # Mix of positive and negative daily returns
        rets = [0.01, -0.02, 0.015, -0.005, 0.008, -0.003, 0.012, -0.007, 0.01, -0.001]
        result = sortino_ratio(rets)
        assert result is not None
        assert isinstance(result, float)

    def test_sortino_ratio_none_when_no_downside(self):
        from portf_manager.services.analytics_service import sortino_ratio

        # All positive → fewer than 2 downside observations
        assert sortino_ratio([0.01, 0.02, 0.005]) is None
        # Only 1 negative → stdev undefined
        assert sortino_ratio([0.01, -0.01, 0.02, 0.005]) is None
        assert sortino_ratio([]) is None

    def test_calmar_ratio_basic(self):
        from portf_manager.services.analytics_service import calmar_ratio

        # cagr=10%, drawdown=-15% → calmar = 10/15 ≈ 0.67
        result = calmar_ratio(10.0, -15.0)
        assert result is not None
        assert abs(result - 0.67) < 0.01

    def test_calmar_ratio_none_cases(self):
        from portf_manager.services.analytics_service import calmar_ratio

        assert calmar_ratio(None, -15.0) is None
        assert calmar_ratio(10.0, None) is None
        assert calmar_ratio(10.0, 0.0) is None   # no drawdown recorded
        assert calmar_ratio(10.0, 2.0) is None   # positive drawdown (impossible, guard)

    def test_compute_beta_alpha(self):
        from portf_manager.services.analytics_service import compute_beta_alpha

        # Portfolio returns ≈ 1.2× benchmark → beta ≈ 1.2
        bench = [0.01, -0.02, 0.015, -0.005, 0.008, -0.003, 0.012, -0.007, 0.01, -0.001,
                 0.005, -0.008, 0.02, -0.01, 0.003]
        port  = [r * 1.2 for r in bench]
        beta, alpha = compute_beta_alpha(port, bench, 0.10, 0.08)
        assert beta is not None
        assert abs(beta - 1.2) < 0.05
        # alpha = (0.10 - 1.2 * 0.08) * 100 = 0.4%
        assert alpha is not None
        assert abs(alpha - 0.4) < 0.2

    def test_compute_beta_alpha_none_cases(self):
        from portf_manager.services.analytics_service import compute_beta_alpha

        assert compute_beta_alpha([], [], None, None) == (None, None)
        short = [0.01] * 5
        assert compute_beta_alpha(short, short, None, None) == (None, None)
        # alpha None when cagrs not available
        bench = [0.01] * 15
        beta, alpha = compute_beta_alpha(bench, bench, None, None)
        assert beta is not None
        assert alpha is None

    def test_period_start_date_3y_5y(self):
        from datetime import date
        from portf_manager.services.analytics_service import period_start_date

        ref = date(2026, 6, 19)
        assert period_start_date("3y", ref) == date(2023, 6, 19)
        assert period_start_date("5y", ref) == date(2021, 6, 19)

    def test_period_start_date_5y_day_capped(self):
        from datetime import date
        from portf_manager.services.analytics_service import period_start_date

        # Jan 31 → day capped at 28 for Feb compatibility
        ref = date(2026, 1, 31)
        assert period_start_date("5y", ref) == date(2021, 1, 28)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py::TestNewMetrics -v
```

Expected: `ImportError` or `FAILED` — functions not yet defined.

- [ ] **Step 3: Add imports + new functions to analytics_service.py**

At the top of `portf_manager/services/analytics_service.py`, after the existing imports, add:

```python
import math
import statistics as _stats
```

After the `period_return` function (around line 220+), add:

```python
# ── New performance metrics ───────────────────────────────────────────────────


def compute_cagr(
    invested: float,
    current_value: float,
    realised: float,
    inception_date: Optional[date],
) -> Optional[float]:
    """Compound Annual Growth Rate since inception.

    Returns None when invested is zero, inception is unknown, or history
    is shorter than one year (CAGR is misleading over shorter spans).
    """
    if invested <= 0 or inception_date is None:
        return None
    years = (date.today() - inception_date).days / 365.25
    if years < 1:
        return None
    ratio = (current_value + realised) / invested
    if ratio <= 0:
        return None
    return round((ratio ** (1.0 / years) - 1) * 100, 2)


def sortino_ratio(returns: list[float]) -> Optional[float]:
    """Annualised Sortino ratio (rf=0) from a list of raw daily returns.

    Penalises only downside volatility (negative-return days).
    Returns None when fewer than 2 downside observations are available.
    """
    if not returns:
        return None
    downside = [r for r in returns if r < 0]
    if len(downside) < 2:
        return None
    downside_std = _stats.stdev(downside) * math.sqrt(252)
    if downside_std == 0:
        return None
    return round((_stats.mean(returns) * 252) / downside_std, 2)


def calmar_ratio(
    cagr_pct: Optional[float], max_drawdown_pct: Optional[float]
) -> Optional[float]:
    """Calmar ratio: CAGR ÷ |max drawdown|.

    Both arguments are in % (not fractions). Returns None when no drawdown
    has been recorded (max_drawdown_pct >= 0) or either input is None.
    """
    if cagr_pct is None or max_drawdown_pct is None or max_drawdown_pct >= 0:
        return None
    return round(-cagr_pct / max_drawdown_pct, 2)


def compute_beta_alpha(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    snapshot_cagr: Optional[float],
    benchmark_cagr: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Beta and alpha (annualised, rf=0, CAPM) from aligned daily return series.

    Args:
        portfolio_returns: daily raw returns (not %) aligned to benchmark.
        benchmark_returns: daily raw returns (not %) for the same dates.
        snapshot_cagr: portfolio annualised return as a fraction (e.g. 0.10).
        benchmark_cagr: benchmark annualised return as a fraction.

    Returns:
        (beta, alpha_pct) — alpha_pct is in %, rounded to 2 dp.
        Either may be None when insufficient data.
    """
    if len(portfolio_returns) < 10 or len(portfolio_returns) != len(benchmark_returns):
        return None, None
    var_b = _stats.variance(benchmark_returns)
    if var_b == 0:
        return None, None
    beta = round(
        _stats.covariance(portfolio_returns, benchmark_returns) / var_b, 3
    )
    if snapshot_cagr is None or benchmark_cagr is None:
        return beta, None
    alpha_pct = round((snapshot_cagr - beta * benchmark_cagr) * 100, 2)
    return beta, alpha_pct
```

Also extend `period_start_date` to handle `3y` and `5y`. Find the function and add two branches before the final `return None`:

```python
    if p == "3y":
        return date(today.year - 3, today.month, min(today.day, 28))
    if p == "5y":
        return date(today.year - 5, today.month, min(today.day, 28))
    return None  # 'all'
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v
```

Expected: all 14 existing + 9 new = **23 passed**.

- [ ] **Step 5: Commit**

```bash
git add portf_manager/services/analytics_service.py tests/unit/test_analytics.py
git commit -m "feat: add compute_cagr, sortino_ratio, calmar_ratio, compute_beta_alpha; extend period_start_date with 3y/5y

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 2: Extend /analytics/performance with CAGR fields

**Files:**
- Modify: `portf_server/routers/analytics.py`
- Test: `tests/unit/test_analytics.py`

- [ ] **Step 1: Write the failing test**

Add to the `TestAnalyticsRouter` class in `tests/unit/test_analytics.py`:

```python
    @pytest.mark.asyncio
    async def test_performance_has_cagr_fields(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/performance", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert "inception_date" in d
        assert "cagr_pct" in d
        assert "annualized_gain_eur" in d
        # Empty DB → no cash flows → None values are expected
        assert d["inception_date"] is None
        assert d["cagr_pct"] is None
        assert d["annualized_gain_eur"] is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest "tests/unit/test_analytics.py::TestAnalyticsRouter::test_performance_has_cagr_fields" -v
```

Expected: `FAILED` — `inception_date` not in response.

- [ ] **Step 3: Update the import in analytics.py**

In `portf_server/routers/analytics.py`, extend the import from `analytics_service`:

```python
from portf_manager.services.analytics_service import (
    compute_cagr,
    dividend_income,
    irpf_savings_tax,
    money_weighted_irr,
    period_return,
    period_start_date,
    simple_return,
)
```

- [ ] **Step 4: Add new fields to get_performance()**

In `get_performance()`, after the existing `irr = money_weighted_irr(...)` and `total_ret = simple_return(...)` lines, add:

```python
    inception_date = min((d for d, _ in cash_flows), default=None)
    inception_date_str = inception_date.isoformat() if inception_date else None
    cagr_pct = (
        compute_cagr(invested, current_value, realised, inception_date)
        if inception_date
        else None
    )
    annualized_gain_eur = (
        round(invested * cagr_pct / 100, 2) if cagr_pct is not None else None
    )
```

Then extend the `return` dict at the end of `get_performance()`:

```python
    return {
        "invested_eur": round(invested, 2),
        "current_value_eur": round(current_value, 2),
        "realised_pnl_eur": round(realised, 2),
        "total_return_pct": total_ret,
        "money_weighted_irr_pct": irr,
        "period": period,
        "period_return_pct": period_ret,
        "benchmark": benchmark,
        "benchmark_return_pct": benchmark_ret,
        "inception_date": inception_date_str,
        "cagr_pct": cagr_pct,
        "annualized_gain_eur": annualized_gain_eur,
    }
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v
```

Expected: all **24 passed**.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_analytics.py
git commit -m "feat: add inception_date, cagr_pct, annualized_gain_eur to /analytics/performance

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 3: Extend /analytics/risk — Sortino, Calmar, benchmark param

**Files:**
- Modify: `portf_server/routers/analytics.py`
- Test: `tests/unit/test_analytics.py`

- [ ] **Step 1: Write the failing test**

Add to `TestAnalyticsRouter` in `tests/unit/test_analytics.py`:

```python
    @pytest.mark.asyncio
    async def test_risk_has_sortino_calmar_fields(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/risk", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert "sortino_ratio" in d
        assert "calmar_ratio" in d
        # Empty DB → insufficient snapshots → null
        assert d["sortino_ratio"] is None
        assert d["calmar_ratio"] is None

    @pytest.mark.asyncio
    async def test_risk_accepts_benchmark_param(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/risk?benchmark=%5EGSPC", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest "tests/unit/test_analytics.py::TestAnalyticsRouter::test_risk_has_sortino_calmar_fields" "tests/unit/test_analytics.py::TestAnalyticsRouter::test_risk_accepts_benchmark_param" -v
```

Expected: `FAILED` — fields not in response.

- [ ] **Step 3: Update imports in analytics.py**

Extend the import from `analytics_service` (building on Task 2's change):

```python
from portf_manager.services.analytics_service import (
    calmar_ratio,
    compute_beta_alpha,
    compute_cagr,
    dividend_income,
    irpf_savings_tax,
    money_weighted_irr,
    period_return,
    period_start_date,
    simple_return,
    sortino_ratio,
)
```

- [ ] **Step 4: Rewrite get_risk()**

Replace the entire `get_risk` function in `portf_server/routers/analytics.py`:

```python
@router.get("/risk")
def get_risk(
    benchmark: str = Query("^GSPC", description="Benchmark ticker for beta/alpha"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Max drawdown, volatility, Sharpe, Sortino, Calmar, Beta, Alpha from snapshots."""
    snapshots = db.get_snapshots()
    if len(snapshots) < 3:
        return {
            "max_drawdown_pct": None,
            "volatility_pct": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "calmar_ratio": None,
            "beta": None,
            "alpha_pct": None,
            "note": "Need at least 3 daily snapshots — collected automatically each day.",
        }

    values = [s["total_value_eur"] for s in snapshots]

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            max_dd = min(max_dd, dd)

    # Daily returns
    returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    vol = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else None
    mean_daily = statistics.mean(returns) if returns else 0
    sharpe = None
    if vol and vol > 0:
        sharpe = round((mean_daily * 252) / vol, 2)

    # Sortino
    sortino = sortino_ratio(returns)

    # Snapshot-based CAGR (for Calmar)
    snap_dates = [s["snapshot_date"][:10] for s in snapshots]
    snap_days = (
        date.fromisoformat(snap_dates[-1]) - date.fromisoformat(snap_dates[0])
    ).days
    snap_cagr_pct = None
    if snap_days >= 365 and values[0] > 0 and values[-1] > 0:
        snap_cagr_pct = round(
            ((values[-1] / values[0]) ** (365.25 / snap_days) - 1) * 100, 2
        )

    max_dd_pct = round(max_dd * 100, 2)
    calmar = calmar_ratio(snap_cagr_pct, max_dd_pct)

    # Beta / Alpha — fetches benchmark daily closes (cached 12h)
    beta_val: Optional[float] = None
    alpha_val: Optional[float] = None
    try:
        cache_key = (
            f"yf:bench-daily:{benchmark}:{snap_dates[0]}:{date.today().isoformat()}"
        )

        def _fetch_bench(b=benchmark, s=snap_dates[0]):
            hist = yf.download(b, start=s, progress=False, auto_adjust=True)
            if hist.empty:
                return []
            closes = hist["Close"]
            if hasattr(closes, "columns"):
                closes = closes.iloc[:, 0]
            closes = closes.dropna()
            return [
                (str(d.date()), float(p))
                for d, p in zip(closes.index, closes.tolist())
            ]

        bench_data: list[tuple[str, float]] = cached(
            db, cache_key, 12 * 3600, _fetch_bench
        )

        if bench_data and len(bench_data) >= 2:
            bench_by_date: dict[str, float] = {}
            for i in range(1, len(bench_data)):
                d_str, prev_p, curr_p = (
                    bench_data[i][0],
                    bench_data[i - 1][1],
                    bench_data[i][1],
                )
                if prev_p > 0:
                    bench_by_date[d_str] = (curr_p - prev_p) / prev_p

            return_dates = snap_dates[1:]
            aligned_p: list[float] = []
            aligned_b: list[float] = []
            for i, d_str in enumerate(return_dates):
                if d_str in bench_by_date and i < len(returns):
                    aligned_p.append(returns[i])
                    aligned_b.append(bench_by_date[d_str])

            bench_cagr = None
            if snap_days >= 365 and bench_data[0][1] > 0 and bench_data[-1][1] > 0:
                bench_cagr = (
                    (bench_data[-1][1] / bench_data[0][1]) ** (365.25 / snap_days)
                ) - 1

            snap_cagr_frac = snap_cagr_pct / 100 if snap_cagr_pct is not None else None
            beta_val, alpha_val = compute_beta_alpha(
                aligned_p, aligned_b, snap_cagr_frac, bench_cagr
            )
    except Exception as e:
        logger.warning(f"Beta/alpha computation failed: {e}")

    return {
        "max_drawdown_pct": max_dd_pct,
        "volatility_pct": round(vol * 100, 2) if vol else None,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "beta": beta_val,
        "alpha_pct": alpha_val,
        "snapshots_used": len(snapshots),
    }
```

- [ ] **Step 5: Run all analytics tests**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_analytics.py -v
```

Expected: all **27 passed** (14 original + 9 from Task 1 + 2 from Task 2 + 2 new).

- [ ] **Step 6: Run full unit suite to confirm no regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```

Expected: 580+ passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_analytics.py
git commit -m "feat: extend /analytics/risk with Sortino, Calmar, Beta, Alpha; add benchmark param

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 4: Frontend base — apiClient.getRisk(), 3Y/5Y buttons, dashCagrLine

**Files:**
- Modify: `web_client/js/pfm_core.js`
- Modify: `web_client/index.html`

- [ ] **Step 1: Update apiClient.getRisk() to accept benchmark**

In `web_client/js/pfm_core.js`, replace the existing `getRisk()` method (around line 1757):

```js
        async getRisk(benchmark) {
            const params = benchmark ? `?benchmark=${encodeURIComponent(benchmark)}` : '';
            const resp = await fetch(this.baseURL + '/api/v1/analytics/risk' + params, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },
```

- [ ] **Step 2: Add 3Y and 5Y period radio buttons to index.html**

In `web_client/index.html`, find the `#anPeriodGroup` radio group (around line 857). Between the `1Y` and `1M` entries, insert:

```html
                                    <input type="radio" class="btn-check" name="anPeriod" id="anPeriod3y" value="3y" autocomplete="off">
                                    <label class="btn btn-outline-secondary" for="anPeriod3y">3Y</label>
                                    <input type="radio" class="btn-check" name="anPeriod" id="anPeriod5y" value="5y" autocomplete="off">
                                    <label class="btn btn-outline-secondary" for="anPeriod5y">5Y</label>
```

The block should read in order: `All`, `YTD`, `1Y`, `3Y`, `5Y`, `1M`.

- [ ] **Step 3: Add #dashCagrLine div to index.html**

In `web_client/index.html`, find `<div class="fs-4 fw-bold" id="dashReturnPct">—</div>` (around line 319). Add a sibling div immediately after it:

```html
                                            <div class="small opacity-75" id="dashCagrLine"></div>
```

- [ ] **Step 4: Run JS test suite to confirm no syntax errors**

```bash
node --test web_client/js/tests/
```

Expected: all tests pass (the suite does a load/smoke test of all 4 JS files).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_core.js web_client/index.html
git commit -m "feat: add benchmark param to apiClient.getRisk(); add 3Y/5Y period buttons; add dashCagrLine

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 5: help_text.js — new METRIC_HELP entries and PAGE_HELP extension

**Files:**
- Modify: `web_client/js/help_text.js`

- [ ] **Step 1: Add new entries to METRIC_HELP**

In `web_client/js/help_text.js`, find the `window.METRIC_HELP = {` block and add the following entries (after the existing `sharpe` entry):

```js
  cagr:          "CAGR: Compound Annual Growth Rate — average annual growth since inception assuming constant compounding. Formula: (end/start)^(1/years) − 1. Unlike IRR it ignores contribution timing.",
  annualizedGain:"Annualized Gain: average annual profit in euros — CAGR × invested capital. A rough sense of how much the portfolio earns per year.",
  inception:     "Inception Date: date of your first transaction — the start point for CAGR.",
  sortino:       "Sortino Ratio: like Sharpe but only penalises downside volatility (negative-return days). Ignores upside swings that inflate Sharpe's denominator. >1 is good.",
  calmar:        "Calmar Ratio: annualised return ÷ max drawdown. >1 means your annual gain exceeds your worst peak-to-trough drop.",
  beta:          "Beta: sensitivity to the benchmark. 1.0 = moves with the market; >1 amplifies swings; <1 is more stable. Computed from daily snapshot returns.",
  alpha:         "Alpha: annualised excess return above what Beta predicts (CAPM, rf=0). Positive = outperformance beyond market exposure.",
```

- [ ] **Step 2: Extend PAGE_HELP.analytics body**

Find the `analytics` entry in `window.PAGE_HELP` and append the following HTML to its `body` string (before the closing backtick):

```html
      <hr class="my-3">
      <h6 class="fw-semibold">Metrics Explained</h6>
      <p class="fw-semibold mb-1">Return metrics</p>
      <ul class="mb-2">
        <li><strong>Total Return</strong> — lifetime (current + realised − invested) / invested. Simple, no time-weighting.</li>
        <li><strong>CAGR</strong> — annualised version of Total Return. Best for headline comparisons between portfolios.</li>
        <li><strong>IRR (MWRR)</strong> — accounts for contribution timing. Use when you invest irregularly.</li>
        <li><strong>TWR (Period Return)</strong> — strips out deposits/withdrawals. Best for comparing to a benchmark.</li>
        <li><strong>Alpha</strong> — excess return above what market exposure (Beta) predicts.</li>
      </ul>
      <p class="fw-semibold mb-1">Risk metrics</p>
      <ul class="mb-2">
        <li><strong>Volatility</strong> — annualised std dev of daily returns. Higher = bumpier ride.</li>
        <li><strong>Sharpe</strong> — return per unit of total volatility. Penalises all swings equally.</li>
        <li><strong>Sortino</strong> — like Sharpe but only penalises losses. Better for portfolios with positive skew.</li>
        <li><strong>Max Drawdown</strong> — worst peak-to-trough drop in portfolio history.</li>
        <li><strong>Calmar</strong> — CAGR ÷ drawdown. Combines return and worst-case loss in one number.</li>
        <li><strong>Beta</strong> — market sensitivity. Not good or bad by itself; depends on your goals.</li>
      </ul>
```

- [ ] **Step 3: Run JS test suite**

```bash
node --test web_client/js/tests/
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/help_text.js
git commit -m "docs: add CAGR, Sortino, Calmar, Beta, Alpha tooltip text and analytics help section

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 6: Analytics performance tab — CAGR, Inception, Annualized Gain cards

**Files:**
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Update anPeriodLabel() to include 3Y and 5Y**

Find `function anPeriodLabel(period)` and replace the return statement:

```js
function anPeriodLabel(period) {
    return ({ all: 'All-Time', ytd: 'YTD', '1y': '1Y', '3y': '3Y', '5y': '5Y', '1m': '1M' }[period] || 'All-Time');
}
```

- [ ] **Step 2: Add CAGR/Inception/AnnGain card row to loadAnalyticsPerformance()**

In `loadAnalyticsPerformance()`, find the closing `</div>` of the existing first card row (the one ending after the Period Return card), and add a new row immediately after it — inside the same `body.innerHTML` template literal.

Replace the end of the `body.innerHTML = \`` assignment. The existing HTML ends with the benchmark comparison line:

```js
            <div class="small">
                <i class="bi bi-flag me-1"></i>
                vs <strong ...>${d.benchmark || benchmark}</strong> (${anFmtPct(benchReturn)}, ${anPeriodLabel(period)}):
                <span class="${beatCls} fw-semibold">${anFmtPct(beat)} ${beatWord} benchmark</span>
            </div>`;
```

Change to:

```js
            <div class="small">
                <i class="bi bi-flag me-1"></i>
                vs <strong data-bs-toggle="tooltip" title="${METRIC_HELP.benchmark}">${d.benchmark || benchmark}</strong> (${anFmtPct(benchReturn)}, ${anPeriodLabel(period)}):
                <span class="${beatCls} fw-semibold">${anFmtPct(beat)} ${beatWord} benchmark</span>
            </div>
            <div class="row g-3 mt-1">
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.cagr}">CAGR</div>
                        ${(() => {
                            const v = d.cagr_pct;
                            if (v == null) return '<div class="fs-5 fw-bold text-muted">—</div><div class="small text-muted">Need 1+ year of history</div>';
                            const n = parseFloat(v);
                            const cls = n >= 0 ? 'text-success' : 'text-danger';
                            return `<div class="fs-5 fw-bold ${cls}">${anFmtPct(n)}/yr</div>`;
                        })()}
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.inception}">Inception Date</div>
                        <div class="fs-5 fw-bold">${d.inception_date ? Fmt.date(d.inception_date) : '—'}</div>
                    </div>
                </div>
                <div class="col-6 col-md-4">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.annualizedGain}">Ann. Gain (€/yr)</div>
                        ${(() => {
                            const v = d.annualized_gain_eur;
                            if (v == null) return '<div class="fs-5 fw-bold text-muted">—</div>';
                            const n = parseFloat(v);
                            const cls = n >= 0 ? 'text-success' : 'text-danger';
                            return `<div class="fs-5 fw-bold ${cls}">${anFmtEur(n)}</div>`;
                        })()}
                    </div>
                </div>
            </div>`;
```

- [ ] **Step 3: Run JS test suite**

```bash
node --test web_client/js/tests/
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: add CAGR, Inception Date, Annualized Gain cards to analytics performance tab; add 3Y/5Y period labels

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 7: Analytics risk tab — Sortino, Calmar, Beta, Alpha cards

**Files:**
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Pass benchmark to getRisk() in loadAnalyticsRisk()**

Find `loadAnalyticsRisk()`. Replace:

```js
        const d = await window.apiClient.getRisk();
```

With:

```js
        const benchmark = document.getElementById('anBenchmark')?.value || '^GSPC';
        const d = await window.apiClient.getRisk(benchmark);
```

- [ ] **Step 2: Add four new cards to the risk tab grid**

In `loadAnalyticsRisk()`, find the end of the existing `body.innerHTML = \`` template. It currently ends after the Snapshots Used card:

```js
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.snapshots}">Snapshots Used</div>
                        <div class="fs-5 fw-bold">${d.snapshots_used != null ? d.snapshots_used : '—'}</div>
                    </div>
                </div>
            </div>`;
```

Replace that closing `</div></div>\`` with:

```js
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.snapshots}">Snapshots Used</div>
                        <div class="fs-5 fw-bold">${d.snapshots_used != null ? d.snapshots_used : '—'}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.sortino}">Sortino Ratio</div>
                        ${(() => {
                            const v = d.sortino_ratio;
                            if (v == null) return '<div class="fs-5 fw-bold text-muted">—</div>';
                            const n = parseFloat(v);
                            const cls = n >= 1 ? 'text-success' : (n >= 0 ? 'text-warning' : 'text-danger');
                            return `<div class="fs-5 fw-bold ${cls}">${n.toFixed(2)}</div>`;
                        })()}
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.calmar}">Calmar Ratio</div>
                        ${(() => {
                            const v = d.calmar_ratio;
                            if (v == null) return '<div class="fs-5 fw-bold text-muted">—</div>';
                            const n = parseFloat(v);
                            const cls = n >= 1 ? 'text-success' : (n >= 0.5 ? 'text-warning' : 'text-danger');
                            return `<div class="fs-5 fw-bold ${cls}">${n.toFixed(2)}</div>`;
                        })()}
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.beta}">Beta</div>
                        <div class="fs-5 fw-bold">${d.beta != null ? parseFloat(d.beta).toFixed(2) : '—'}</div>
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <div class="border rounded p-3 h-100">
                        <div class="small text-muted mb-1" data-bs-toggle="tooltip" title="${METRIC_HELP.alpha}">Alpha</div>
                        ${(() => {
                            const v = d.alpha_pct;
                            if (v == null) return '<div class="fs-5 fw-bold text-muted">—</div>';
                            const n = parseFloat(v);
                            const cls = n >= 0 ? 'text-success' : 'text-danger';
                            return `<div class="fs-5 fw-bold ${cls}">${anFmtPct(n)}/yr</div>`;
                        })()}
                    </div>
                </div>
            </div>`;
```

- [ ] **Step 3: Run JS test suite**

```bash
node --test web_client/js/tests/
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: add Sortino, Calmar, Beta, Alpha cards to analytics risk tab

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 8: Dashboard — CAGR sub-line in loadDashboardReturn()

**Files:**
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Update loadDashboardReturn() to populate #dashCagrLine**

Find `loadDashboardReturn()` in `pfm_analytics.js`. The function currently sets `el.textContent` on `#dashReturnPct` and returns. Replace the entire function body:

```js
async function loadDashboardReturn(period) {
    const el = document.getElementById('dashReturnPct');
    const cagrEl = document.getElementById('dashCagrLine');
    if (!el) return;
    el.textContent = '…';
    if (cagrEl) cagrEl.textContent = '';
    try {
        const d = await window.apiClient.getPerformance(null, period || 'all');
        const pct = (period && period !== 'all')
            ? d.period_return_pct
            : d.total_return_pct;
        if (pct == null) {
            el.textContent = '—';
            el.title = (period && period !== 'all')
                ? 'Not enough daily snapshot history for this period yet'
                : 'No data';
        } else {
            const n = parseFloat(pct);
            el.textContent = (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
            el.title = (period && period !== 'all')
                ? 'Change over the selected period (from daily snapshots)'
                : 'Lifetime return vs cost basis';
        }
        if (cagrEl) {
            const cagr = d.cagr_pct;
            if (cagr != null) {
                const cn = parseFloat(cagr);
                const ccls = cn >= 0 ? 'opacity-90' : 'opacity-90';
                cagrEl.innerHTML = `CAGR: <span class="fw-semibold">${(cn >= 0 ? '+' : '') + cn.toFixed(2)}%/yr</span>`;
            } else {
                cagrEl.textContent = '';
            }
        }
    } catch (err) {
        el.textContent = '—';
        el.title = err.message;
        if (cagrEl) cagrEl.textContent = '';
    }
}
```

- [ ] **Step 2: Run JS test suite**

```bash
node --test web_client/js/tests/
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: add CAGR sub-line to dashboard Return card

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Task 9: Docs — CLAUDE.md and PROJECT_STATUS.md

**Files:**
- Modify: `CLAUDE.md`
- Modify: `PROJECT_STATUS.md`

- [ ] **Step 1: Update CLAUDE.md**

In the `### Analytics API` section, find the `/api/v1/analytics/performance` line and update it to mention the new fields:

```
- `GET /api/v1/analytics/performance?benchmark=^GSPC&period=` — invested, current value, total return, money-weighted IRR, period TWR, benchmark return, **plus** `inception_date`, `cagr_pct`, `annualized_gain_eur` (new)
```

Find the `/api/v1/analytics/risk` line and update it:

```
- `GET /api/v1/analytics/risk?benchmark=^GSPC` — max drawdown, annualised volatility, Sharpe, **plus** `sortino_ratio`, `calmar_ratio`, `beta`, `alpha_pct` (new); needs ≥3 snapshots; benchmark param controls beta/alpha reference index
```

In the `analytics_service.py` section of the Architecture notes, add:

```
New functions (Task 1): `compute_cagr(invested, current_value, realised, inception_date)`, `sortino_ratio(returns)`, `calmar_ratio(cagr_pct, max_drawdown_pct)`, `compute_beta_alpha(port_rets, bench_rets, snap_cagr, bench_cagr)`. `period_start_date` now accepts `'3y'` and `'5y'`.
```

- [ ] **Step 2: Update PROJECT_STATUS.md**

Bump the "Last updated" date to `2026-06-19` and add to the Recent summary line:

```
Analytics: CAGR, Annualized Gain, Inception Date on Performance tab; Sortino, Calmar, Beta, Alpha on Risk tab; 3Y/5Y period windows; CAGR sub-line on Dashboard.
```

- [ ] **Step 3: Run full unit suite one final time**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
```

Expected: 580+ passed, 0 failed.

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: update CLAUDE.md and PROJECT_STATUS.md for portfolio metrics expansion

Co-Authored-By: Oz <oz-agent@warp.dev>"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `compute_cagr`, `sortino_ratio`, `calmar_ratio`, `compute_beta_alpha` — Task 1
- ✅ `period_start_date` 3y/5y — Task 1
- ✅ Performance endpoint `inception_date`, `cagr_pct`, `annualized_gain_eur` — Task 2
- ✅ Risk endpoint `async→def`, `benchmark` param, Sortino, Calmar, Beta, Alpha — Task 3
- ✅ `apiClient.getRisk(benchmark)` — Task 4
- ✅ 3Y/5Y radio buttons in `index.html` — Task 4
- ✅ `#dashCagrLine` div — Task 4
- ✅ `METRIC_HELP` entries, `PAGE_HELP.analytics` extension — Task 5
- ✅ Performance tab CAGR/Inception/AnnGain cards, `anPeriodLabel` 3Y/5Y — Task 6
- ✅ Risk tab Sortino/Calmar/Beta/Alpha cards — Task 7
- ✅ Dashboard CAGR sub-line — Task 8
- ✅ `CLAUDE.md` + `PROJECT_STATUS.md` — Task 9

**Type consistency:**
- `compute_cagr` returns `Optional[float]` — used as `d.cagr_pct` in JS ✓
- `sortino_ratio` / `calmar_ratio` return `Optional[float]` — exposed as `sortino_ratio` / `calmar_ratio` in risk response ✓
- `compute_beta_alpha` returns `tuple[Optional[float], Optional[float]]` — assigned to `beta_val, alpha_val` ✓
- `getRisk(benchmark)` in pfm_core.js — called with benchmark in Task 7 ✓
- `#dashCagrLine` added in Task 4, populated in Task 8 ✓
