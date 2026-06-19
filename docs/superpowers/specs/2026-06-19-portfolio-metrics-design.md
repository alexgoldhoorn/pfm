# Portfolio Metrics Expansion — Design Spec

**Date:** 2026-06-19
**Status:** Approved

## Overview

Add CAGR, Annualized Gain, Inception Date, 3Y/5Y period windows, Sortino ratio, Calmar ratio, Beta, and Alpha to the Portfolio Manager. Metrics are split across three surfaces: Analytics > Performance tab, Analytics > Risk tab, and the Dashboard. All new metrics get hover tooltips; the Analytics help modal gets a "Metrics Explained" section.

---

## Section 1: Backend

### 1a. `portf_manager/services/analytics_service.py`

Add three new pure functions alongside the existing `money_weighted_irr`, `simple_return`, and `period_return`:

**`compute_cagr(invested, current_value, realised, inception_date) -> Optional[float]`**
- Returns `((current_value + realised) / invested) ^ (1 / years) − 1) * 100`, rounded to 2 dp.
- `years = (date.today() − inception_date).days / 365.25`
- Returns `None` if `invested <= 0`, `years < 1`, or the ratio is non-positive.

**`sortino_ratio(returns: list[float]) -> Optional[float]`**
- `returns`: list of daily raw returns (not %).
- Downside returns = `[r for r in returns if r < 0]`.
- Returns `None` if fewer than 2 downside observations.
- `downside_std = stdev(downside_returns) * sqrt(252)`
- Result: `(mean(returns) * 252) / downside_std`, rounded to 2 dp.

**`calmar_ratio(cagr_pct, max_drawdown_pct) -> Optional[float]`**
- Returns `None` if `max_drawdown_pct >= 0` (no drawdown recorded yet).
- Result: `−cagr_pct / max_drawdown_pct`, rounded to 2 dp.
- (Both inputs are already %; a positive CAGR over a negative drawdown → positive Calmar.)

**`period_start_date()` — extend existing function**
- Add `3y`: `date(today.year − 3, today.month, min(today.day, 28))`
- Add `5y`: `date(today.year − 5, today.month, min(today.day, 28))`

### 1b. `portf_server/routers/analytics.py` — `/analytics/performance`

Add to the existing `get_performance()` response (no new endpoint):

| New field | Source |
|---|---|
| `inception_date` | `min(d for d, _ in cash_flows).isoformat()` — already computed for IRR |
| `cagr_pct` | `compute_cagr(invested, current_value, realised, inception_date)` |
| `annualized_gain_eur` | `round(invested * cagr_pct / 100, 2)` when `cagr_pct` is not None |

### 1c. `portf_server/routers/analytics.py` — `/analytics/risk`

**Breaking changes:**
- `async def get_risk(...)` → `def get_risk(...)` (yfinance I/O must run in threadpool per project convention)
- Add `benchmark: str = Query("^GSPC")` parameter

**New computed fields** (appended to existing response):

| Field | Computation |
|---|---|
| `sortino_ratio` | `sortino_ratio(daily_returns)` from `analytics_service` |
| `snapshot_cagr_pct` | `((last_val / first_val) ^ (365.25 / days) − 1) * 100` — snapshot-only CAGR, used for Calmar |
| `calmar_ratio` | `calmar_ratio(snapshot_cagr_pct, max_drawdown_pct)` |
| `beta` | `Cov(portfolio_daily, benchmark_daily) / Var(benchmark_daily)` |
| `alpha_pct` | `(snapshot_cagr_pct/100 − beta × ann_benchmark_return) × 100`, rf=0; `ann_benchmark_return = (last_bench/first_bench)^(365.25/days) − 1` |

**Benchmark fetch for alpha/beta:**
- Download benchmark daily closes for the snapshot date range via `yf.download()`.
- Align dates to snapshot dates (inner join on date).
- Cache result in `kv_cache` at key `yf:bench-daily:{benchmark}:{start}:{end}` with 12h TTL.
- If fewer than 10 aligned observations, return `beta: null, alpha_pct: null`.
- Wrap in try/except; on failure return nulls (same pattern as existing benchmark fetch).

**Minimum snapshot guard:**
- Existing guard (`len(snapshots) < 3`) still applies. The early-return dict is extended to explicitly include `sortino_ratio: None, calmar_ratio: None, beta: None, alpha_pct: None` so the frontend doesn't need to handle missing keys differently.

---

## Section 2: Frontend

### 2a. `web_client/index.html`

**Period radio buttons** (`#anPeriodGroup`) — add between `1Y` and `1M`:
```html
<input type="radio" class="btn-check" name="anPeriod" id="anPeriod3y" value="3y" autocomplete="off">
<label class="btn btn-outline-secondary" for="anPeriod3y">3Y</label>
<input type="radio" class="btn-check" name="anPeriod" id="anPeriod5y" value="5y" autocomplete="off">
<label class="btn btn-outline-secondary" for="anPeriod5y">5Y</label>
```

### 2b. `web_client/js/pfm_analytics.js` — Performance tab

`loadAnalyticsPerformance()` renders a second row of KPI cards after the existing four:

| Card | Value | Color |
|---|---|---|
| CAGR | `d.cagr_pct` | green ≥0, red <0, `—` when null |
| Inception | `Fmt.date(d.inception_date)` | none |
| Ann. Gain | `anFmtEur(d.annualized_gain_eur)` | green ≥0, red <0, `—` when null |

Cards use `col-6 col-md-4` (three per row at md+).

### 2c. `web_client/js/pfm_analytics.js` — Risk tab

`loadAnalyticsRisk()`:

1. Passes `benchmark` param: reads `document.getElementById('anBenchmark')?.value || '^GSPC'` and appends `?benchmark=` to the `getRisk()` call (requires updating `apiClient.getRisk()` in `pfm_core.js`).
2. Renders four new cards after existing three (Snapshots Used card stays last):

| Card | Value | Color rule |
|---|---|---|
| Sortino Ratio | `d.sortino_ratio` | green ≥1, yellow ≥0, red <0, `—` when null |
| Calmar Ratio | `d.calmar_ratio` | green ≥1, yellow ≥0.5, red <0.5, `—` when null |
| Beta | `d.beta` | none (informational) |
| Alpha | `d.alpha_pct` + `%` | green ≥0, red <0, `—` when null |

### 2d. `web_client/js/pfm_pages.js` — Dashboard

`loadDashboardReturn()` already receives the full performance response. After rendering the main return value, append a second line inside the same card:

```
CAGR: +8.4%/yr
```

Rendered as `<div class="small opacity-75 mt-1">CAGR: <span class="fw-semibold ${cls}">${cagrTxt}/yr</span></div>` — no new card, no new API call.

---

## Section 3: Documentation

### 3a. `web_client/js/help_text.js` — `METRIC_HELP` additions

```js
cagr:          "CAGR: Compound Annual Growth Rate — average annual growth since inception assuming constant compounding. Formula: (end/start)^(1/years) − 1. Unlike IRR it ignores contribution timing.",
annualizedGain:"Annualized Gain: average annual profit in euros — CAGR × invested capital. A rough sense of how much the portfolio earns per year.",
inception:     "Inception Date: date of your first transaction — the start point for CAGR.",
sortino:       "Sortino Ratio: like Sharpe but only penalises downside volatility (negative-return days). Ignores upside swings that inflate Sharpe's denominator. >1 is good.",
calmar:        "Calmar Ratio: annualised return ÷ max drawdown. >1 means your annual gain exceeds your worst peak-to-trough drop.",
beta:          "Beta: sensitivity to the benchmark. 1.0 = moves with the market; >1 amplifies swings; <1 is more stable.",
alpha:         "Alpha: annualised excess return above what Beta predicts (CAPM, rf=0). Positive = outperformance beyond market exposure.",
```

### 3b. `web_client/js/help_text.js` — `PAGE_HELP.analytics` body extension

Append a "Metrics Explained" section to the existing analytics help modal body:

**Return metrics:**
- **Total Return** — lifetime (current + realised − invested) / invested. Simple, no time-weighting.
- **CAGR** — annualised version of Total Return. Best for headline comparisons.
- **IRR (MWRR)** — accounts for contribution timing. Use when you invest irregularly.
- **TWR (Period Return)** — strips out deposits/withdrawals. Best for comparing to a benchmark.
- **Alpha** — excess return above what market exposure (Beta) predicts.

**Risk metrics:**
- **Volatility** — how much daily returns swing (annualised std dev). Higher = bumpier ride.
- **Sharpe** — return per unit of total volatility. Penalises all swings equally.
- **Sortino** — like Sharpe but only penalises losses. Better for portfolios with positive skew.
- **Max Drawdown** — worst peak-to-trough drop in portfolio history.
- **Calmar** — CAGR ÷ drawdown. Combines return and worst-case loss in one number.
- **Beta** — market sensitivity. Not good or bad by itself; context depends on your goals.

---

## Files Changed

| File | Change |
|---|---|
| `portf_manager/services/analytics_service.py` | Add `compute_cagr`, `sortino_ratio`, `calmar_ratio`; extend `period_start_date` |
| `portf_server/routers/analytics.py` | Extend performance response; extend + convert risk endpoint |
| `portf_server/schemas/` | No schema changes needed (plain dict responses) |
| `web_client/index.html` | Add 3Y/5Y radio buttons |
| `web_client/js/pfm_analytics.js` | New cards in Performance + Risk tabs |
| `web_client/js/pfm_pages.js` | CAGR sub-line on dashboard Return card |
| `web_client/js/pfm_core.js` | Add `benchmark` param to `apiClient.getRisk()` |
| `web_client/js/help_text.js` | New `METRIC_HELP` entries + extended `PAGE_HELP.analytics` |
| `CLAUDE.md` | Document new endpoint fields and new analytics_service functions |
| `PROJECT_STATUS.md` | Bump last-updated, add feature to Recent summary |

## Out of Scope

- Per-asset CAGR (only portfolio-level)
- Risk-free rate input (all ratios use rf=0)
- Currency-specific Beta/Alpha (all EUR-converted)
- Exporting new metrics to CSV
