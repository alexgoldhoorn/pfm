# Portfolio Stress Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Stress Testing tab to the Analytics page that shows per-asset estimated losses for historical crash scenarios (2008, 2020, 2022, dot-com) and custom date ranges.

**Architecture:** A new `GET /api/v1/analytics/stress-test` endpoint in the existing analytics router fetches actual yfinance historical returns per held asset and falls back to conservative asset-type estimates for unpriced assets. Results for preset scenarios are cached 7 days via `kv_cache`. The frontend adds a lazy-loaded "Stress" tab with scenario buttons, a custom date picker, a summary card, and a per-asset loss table.

**Tech Stack:** Python/FastAPI backend, yfinance for historical prices, vanilla JS + Bootstrap 5 frontend, SQLite kv_cache for preset caching.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `portf_server/routers/analytics.py` | Modify | Add `HTTPException` import; add `_STRESS_SCENARIOS`, `_STRESS_FALLBACKS`, `_get_ticker_return()`; add `stress_test()` endpoint |
| `tests/unit/test_stress_testing.py` | Create | Unit tests for fallback table, `_get_ticker_return`, and endpoint |
| `web_client/js/pfm_core.js` | Modify | Add `getStressTest()` to `apiClient` |
| `web_client/index.html` | Modify | Add Stress tab button; add `data-an-section="stress"` card; bump `?v=` cache busters |
| `web_client/js/pfm_analytics.js` | Modify | Add `loadAnalyticsStress()`; add `stress` entry to `_ANALYTICS_LOADERS`; bump `?v=` |

---

## Task 1: Backend data tables and `_get_ticker_return` helper (TDD)

**Files:**
- Create: `tests/unit/test_stress_testing.py`
- Modify: `portf_server/routers/analytics.py`

- [ ] **Step 1: Write failing tests for data tables and helper**

Create `tests/unit/test_stress_testing.py`:

```python
"""Tests for portfolio stress testing — data tables, helper, endpoint."""

import pandas as pd
import pytest
from unittest.mock import patch
from httpx import AsyncClient
from fastapi import status

from portf_server.routers.analytics import (
    _STRESS_SCENARIOS,
    _STRESS_FALLBACKS,
    _get_ticker_return,
)


class TestStressFallbacks:
    def test_all_preset_scenarios_defined(self):
        for key in ("2008", "2020", "2022", "dotcom"):
            assert key in _STRESS_SCENARIOS
            meta = _STRESS_SCENARIOS[key]
            assert "label" in meta
            assert "from_date" in meta
            assert "to_date" in meta

    def test_fallback_covers_all_asset_types(self):
        required = {
            "stock", "etf", "index", "fund", "bond",
            "crypto", "commodity", "interest", "deposit",
        }
        for scenario_key, table in _STRESS_FALLBACKS.items():
            for asset_type in required:
                assert asset_type in table, f"{scenario_key} missing '{asset_type}'"

    def test_2008_equity_loss_is_severe(self):
        assert _STRESS_FALLBACKS["2008"]["stock"] <= -40.0

    def test_2022_bonds_are_negative(self):
        assert _STRESS_FALLBACKS["2022"]["bond"] < 0

    def test_deposits_always_zero_in_all_scenarios(self):
        for table in _STRESS_FALLBACKS.values():
            assert table["deposit"] == 0.0
            assert table["interest"] == 0.0


class TestGetTickerReturn:
    def test_returns_correct_pct_for_valid_history(self):
        hist = pd.DataFrame(
            {"Close": [100.0, 50.0]},
            index=pd.to_datetime(["2007-10-01", "2009-03-09"]),
        )
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = hist
            result = _get_ticker_return("AAPL", "2007-10-01", "2009-03-09")
        assert result == -50.0

    def test_returns_none_when_history_is_empty(self):
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = pd.DataFrame()
            result = _get_ticker_return("NOPE", "2007-10-01", "2009-03-09")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch(
            "portf_server.routers.analytics.yf.Ticker",
            side_effect=Exception("network down"),
        ):
            result = _get_ticker_return("AAPL", "2007-10-01", "2009-03-09")
        assert result is None

    def test_returns_none_when_only_one_row(self):
        hist = pd.DataFrame(
            {"Close": [100.0]},
            index=pd.to_datetime(["2007-10-01"]),
        )
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = hist
            result = _get_ticker_return("AAPL", "2007-10-01", "2007-10-01")
        assert result is None
```

- [ ] **Step 2: Run tests to confirm they fail (import error)**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_stress_testing.py::TestStressFallbacks tests/unit/test_stress_testing.py::TestGetTickerReturn -v 2>&1 | tail -20
```

Expected: `ImportError` — `_STRESS_SCENARIOS`, `_STRESS_FALLBACKS`, `_get_ticker_return` not yet defined.

- [ ] **Step 3: Add `HTTPException` to the analytics router import**

In `portf_server/routers/analytics.py`, line 13, change:

```python
from fastapi import APIRouter, Depends, Query, Request
```

to:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request
```

- [ ] **Step 4: Add data tables and `_get_ticker_return` to analytics.py**

Insert the following block after line 35 (`from .portfolios import _get_fx_rate as _fx`), before `router = APIRouter()`:

```python
_STRESS_SCENARIOS: dict[str, dict] = {
    "2008": {
        "label": "2008 Financial Crisis",
        "from_date": "2007-10-01",
        "to_date": "2009-03-09",
    },
    "2020": {
        "label": "2020 COVID Crash",
        "from_date": "2020-02-19",
        "to_date": "2020-03-23",
    },
    "2022": {
        "label": "2022 Rate Hike Selloff",
        "from_date": "2021-12-31",
        "to_date": "2022-10-12",
    },
    "dotcom": {
        "label": "Dot-com Bust",
        "from_date": "2000-03-24",
        "to_date": "2002-10-09",
    },
}

_STRESS_FALLBACKS: dict[str, dict[str, float]] = {
    "2008": {
        "stock": -50.0, "etf": -50.0, "index": -50.0,
        "fund": -40.0, "bond": -5.0,
        "crypto": 0.0,
        "commodity": -30.0,
        "interest": 0.0, "deposit": 0.0,
    },
    "2020": {
        "stock": -32.0, "etf": -32.0, "index": -32.0,
        "fund": -25.0, "bond": 5.0,
        "crypto": -50.0,
        "commodity": -20.0,
        "interest": 0.0, "deposit": 0.0,
    },
    "2022": {
        "stock": -22.0, "etf": -22.0, "index": -22.0,
        "fund": -18.0, "bond": -15.0,
        "crypto": -65.0,
        "commodity": 20.0,
        "interest": 0.0, "deposit": 0.0,
    },
    "dotcom": {
        "stock": -60.0, "etf": -60.0, "index": -60.0,
        "fund": -45.0, "bond": 5.0,
        "crypto": 0.0,
        "commodity": -15.0,
        "interest": 0.0, "deposit": 0.0,
    },
}


def _get_ticker_return(sym: str, from_date: str, to_date: str) -> float | None:
    """Return total return % for sym between from_date and to_date via yfinance.

    Returns None when data is unavailable (asset too new, bad ticker, network error).
    Extends the end date by 5 days so the last trading day before to_date is included.
    """
    try:
        end = (
            datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=5)
        ).strftime("%Y-%m-%d")
        hist = yf.Ticker(sym).history(start=from_date, end=end, auto_adjust=True)
        if hist.empty:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        price_from = float(closes.iloc[0])
        price_to = float(closes.iloc[-1])
        if price_from == 0:
            return None
        return round((price_to - price_from) / price_from * 100, 2)
    except Exception:
        return None
```

- [ ] **Step 5: Run data/helper tests — expect PASS**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_stress_testing.py::TestStressFallbacks tests/unit/test_stress_testing.py::TestGetTickerReturn -v 2>&1 | tail -20
```

Expected: all 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_stress_testing.py
git commit -m "feat: stress test data tables and ticker return helper"
```

---

## Task 2: Backend — `stress_test` endpoint (TDD)

**Files:**
- Modify: `tests/unit/test_stress_testing.py` (add `TestStressTestEndpoint`)
- Modify: `portf_server/routers/analytics.py` (add `stress_test()` endpoint at end of file)

- [ ] **Step 1: Add endpoint tests to `test_stress_testing.py`**

Append the following class to `tests/unit/test_stress_testing.py`:

```python
class TestStressTestEndpoint:
    @pytest.mark.asyncio
    async def test_missing_params_returns_400(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/stress-test", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_custom_to_before_from_returns_400(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/stress-test?from=2020-06-01&to=2020-01-01",
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "after" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_preset_scenario_returns_correct_shape(
        self, async_test_client: AsyncClient, auth_headers
    ):
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = pd.DataFrame()
            resp = await async_test_client.get(
                "/api/v1/analytics/stress-test?scenario=2008", headers=auth_headers
            )
        assert resp.status_code == status.HTTP_200_OK
        d = resp.json()
        assert d["scenario"] == "2008"
        assert d["label"] == "2008 Financial Crisis"
        assert d["from_date"] == "2007-10-01"
        assert d["to_date"] == "2009-03-09"
        assert "portfolio_current_value_eur" in d
        assert "portfolio_stressed_value_eur" in d
        assert "total_loss_eur" in d
        assert "total_loss_pct" in d
        assert isinstance(d["assets"], list)

    @pytest.mark.asyncio
    async def test_custom_date_range_returns_custom_scenario_key(
        self, async_test_client: AsyncClient, auth_headers
    ):
        with patch("portf_server.routers.analytics.yf.Ticker") as mock_t:
            mock_t.return_value.history.return_value = pd.DataFrame()
            resp = await async_test_client.get(
                "/api/v1/analytics/stress-test?from=2020-02-01&to=2020-04-01",
                headers=auth_headers,
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["scenario"] == "custom"

    @pytest.mark.asyncio
    async def test_unknown_preset_returns_400(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/stress-test?scenario=notreal", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
```

- [ ] **Step 2: Run endpoint tests — expect 404 (endpoint not yet defined)**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_stress_testing.py::TestStressTestEndpoint -v 2>&1 | tail -20
```

Expected: failures (404 Not Found or import errors).

- [ ] **Step 3: Add `stress_test` endpoint at the end of `portf_server/routers/analytics.py`**

Append the following to the end of the file:

```python
# ── Stress Testing ───────────────────────────────────────────────────────────


@router.get("/stress-test")
def stress_test(
    scenario: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Stress test portfolio against a historical crash scenario or custom date range.

    Pass ``scenario`` (one of: 2008, 2020, 2022, dotcom) OR both ``from`` and
    ``to`` (YYYY-MM-DD) for a custom period. Results for preset scenarios are
    cached 7 days; custom queries run live.
    """
    if scenario is not None and scenario not in _STRESS_SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{scenario}'. Valid: {list(_STRESS_SCENARIOS)}",
        )

    if scenario and scenario in _STRESS_SCENARIOS:
        meta = _STRESS_SCENARIOS[scenario]
        from_str = meta["from_date"]
        to_str = meta["to_date"]
        label = meta["label"]
        scenario_key = scenario
    elif from_date and to_date:
        try:
            fd = datetime.strptime(from_date, "%Y-%m-%d").date()
            td = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD.")
        if td <= fd:
            raise HTTPException(
                status_code=400, detail="End date must be after start date."
            )
        from_str = from_date
        to_str = to_date
        label = f"{from_date} to {to_date}"
        scenario_key = "custom"
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'scenario' or both 'from' and 'to' query parameters.",
        )

    preset_fallbacks = _STRESS_FALLBACKS.get(scenario_key, _STRESS_FALLBACKS["2008"])

    def _compute() -> dict:
        positions, _ = _compute_positions(db)
        assets_by_id = {a["id"]: a for a in db.get_all_assets(active_only=False)}

        if scenario_key == "custom":
            sp500_ret = _get_ticker_return("^GSPC", from_str, to_str)
            equity_fb = sp500_ret if sp500_ret is not None else -30.0
            active_fallbacks: dict[str, float] = {
                "stock": equity_fb,
                "etf": equity_fb,
                "index": equity_fb,
                "fund": round(equity_fb * 0.8, 2),
                "bond": 0.0,
                "crypto": round(equity_fb * 1.5, 2),
                "commodity": 0.0,
                "interest": 0.0,
                "deposit": 0.0,
            }
        else:
            active_fallbacks = preset_fallbacks

        assets_out = []
        total_current = 0.0
        total_stressed = 0.0

        for aid, pos in positions.items():
            if pos["quantity"] <= 0:
                continue
            asset = assets_by_id.get(aid)
            if not asset:
                continue
            cur = asset.get("currency", "EUR")
            price_data = db.get_latest_price(aid)
            price = float(price_data["price"]) if price_data else 0.0
            current_value_eur = pos["quantity"] * price * _fx(cur)
            if current_value_eur <= 0:
                continue

            sym = asset.get("ticker") or asset.get("symbol", "")
            hist_ret: float | None = None
            data_source = "fallback"
            if sym:
                hist_ret = _get_ticker_return(sym, from_str, to_str)
                if hist_ret is not None:
                    data_source = "yfinance"

            if hist_ret is None:
                asset_type = asset.get("asset_type", "stock")
                hist_ret = active_fallbacks.get(
                    asset_type, active_fallbacks.get("stock", -30.0)
                )

            stressed_value_eur = current_value_eur * (1 + hist_ret / 100)
            loss_eur = stressed_value_eur - current_value_eur
            total_current += current_value_eur
            total_stressed += stressed_value_eur

            assets_out.append(
                {
                    "symbol": asset.get("symbol", ""),
                    "name": asset.get("name", ""),
                    "asset_type": asset.get("asset_type", ""),
                    "current_value_eur": round(current_value_eur, 2),
                    "historical_return_pct": round(hist_ret, 2),
                    "stressed_value_eur": round(stressed_value_eur, 2),
                    "loss_eur": round(loss_eur, 2),
                    "data_source": data_source,
                }
            )

        assets_out.sort(key=lambda a: a["loss_eur"])
        total_loss = total_stressed - total_current
        total_loss_pct = (
            (total_loss / total_current * 100) if total_current > 0 else 0.0
        )

        return {
            "scenario": scenario_key,
            "label": label,
            "from_date": from_str,
            "to_date": to_str,
            "portfolio_current_value_eur": round(total_current, 2),
            "portfolio_stressed_value_eur": round(total_stressed, 2),
            "total_loss_eur": round(total_loss, 2),
            "total_loss_pct": round(total_loss_pct, 2),
            "assets": assets_out,
        }

    if scenario_key != "custom":
        return cached(db, f"stress:{scenario_key}", 7 * 24 * 3600, _compute)
    return _compute()
```

- [ ] **Step 4: Run all stress test tests — expect all PASS**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/unit/test_stress_testing.py -v 2>&1 | tail -25
```

Expected: 13 tests PASS, 0 failed.

- [ ] **Step 5: Run full unit suite to catch regressions**

```bash
UV_PROJECT_ENVIRONMENT=/home/agoldhoorn/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -10
```

Expected: same pass count as before (429+) plus 13 new, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/analytics.py tests/unit/test_stress_testing.py
git commit -m "feat: stress test endpoint with preset scenarios and custom date range"
```

---

## Task 3: Frontend — `getStressTest` API client method

**Files:**
- Modify: `web_client/js/pfm_core.js`

- [ ] **Step 1: Find the `getFees` method in `pfm_core.js` (around line 1381) and insert `getStressTest` immediately after it**

The existing `getFees` block ends at around line 1390. Add the new method right after:

```javascript
        async getStressTest(scenario, fromDate, toDate) {
            let url = this.baseURL + '/api/v1/analytics/stress-test';
            if (scenario) {
                url += '?scenario=' + encodeURIComponent(scenario);
            } else {
                url += '?from=' + encodeURIComponent(fromDate) + '&to=' + encodeURIComponent(toDate);
            }
            const resp = await fetch(url, { headers: { 'X-API-Key': this.apiKey } });
            if (!resp.ok) throw new Error(await resp.text());
            return resp.json();
        },
```

- [ ] **Step 2: Commit**

```bash
git add web_client/js/pfm_core.js
git commit -m "feat: add getStressTest API client method"
```

---

## Task 4: Frontend — HTML tab button and section card

**Files:**
- Modify: `web_client/index.html`

- [ ] **Step 1: Add the Stress tab button**

In `index.html` around line 833, find the fees tab button:

```html
<button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="fees"><i class="bi bi-cash-stack me-1"></i>Fees &amp; Costs</button>
```

Insert the stress button immediately after it:

```html
<button type="button" class="btn btn-sm btn-outline-secondary" data-an-tab="stress"><i class="bi bi-lightning-charge me-1"></i>Stress Test</button>
```

- [ ] **Step 2: Add the Stress section card**

In `index.html` around line 1001, find the closing `</div>` of the fees card:

```html
                    </div>
                </div>

                <!-- Watchlist Page -->
```

Insert the stress card before `<!-- Watchlist Page -->`:

```html
                    <!-- h) Stress Test -->
                    <div class="card mb-4" data-an-section="stress" style="display:none;">
                        <div class="card-header fw-semibold">
                            <i class="bi bi-lightning-charge me-2 text-danger"></i>Stress Testing
                        </div>
                        <div class="card-body" id="anStressBody">
                            <div class="text-center text-muted py-4">
                                <div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading&hellip;
                            </div>
                        </div>
                    </div>
```

- [ ] **Step 3: Bump `?v=` cache busters for changed JS files**

Find the script tags around line 2943:

```html
    <script src="js/pfm_core.js?v=1780000053"></script>
    <script src="js/pfm_pages.js?v=1780000050"></script>
    <script src="js/pfm_analytics.js?v=1780000053"></script>
    <script src="js/pfm_features.js?v=1780000053"></script>
```

Update `pfm_core.js` and `pfm_analytics.js` version numbers (increment by 1):

```html
    <script src="js/pfm_core.js?v=1780000054"></script>
    <script src="js/pfm_pages.js?v=1780000050"></script>
    <script src="js/pfm_analytics.js?v=1780000054"></script>
    <script src="js/pfm_features.js?v=1780000053"></script>
```

- [ ] **Step 4: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Stress Test tab and section card to analytics page"
```

---

## Task 5: Frontend — JS loader and analytics tab wiring

**Files:**
- Modify: `web_client/js/pfm_analytics.js`

- [ ] **Step 1: Add `stress` entry to `_ANALYTICS_LOADERS`**

Find the `_ANALYTICS_LOADERS` object (around line 903):

```javascript
const _ANALYTICS_LOADERS = {
    performance: () => { loadAnalyticsPerformance(); loadAnalyticsNetworth(); },
    dividends: () => { loadAnalyticsDividends(); },
    gainloss: () => { loadAnalyticsGainLoss(); },
    tax: () => { loadAnalyticsTax(); loadAnalyticsTaxReport(); loadTaxOptimizer(); },
    risk: () => { loadAnalyticsRisk(); _wireDiversificationButtons(); },
    fees: () => { loadAnalyticsFees(); },
};
```

Add the `stress` entry at the end:

```javascript
const _ANALYTICS_LOADERS = {
    performance: () => { loadAnalyticsPerformance(); loadAnalyticsNetworth(); },
    dividends: () => { loadAnalyticsDividends(); },
    gainloss: () => { loadAnalyticsGainLoss(); },
    tax: () => { loadAnalyticsTax(); loadAnalyticsTaxReport(); loadTaxOptimizer(); },
    risk: () => { loadAnalyticsRisk(); _wireDiversificationButtons(); },
    fees: () => { loadAnalyticsFees(); },
    stress: () => { loadAnalyticsStress('2008'); },
};
```

- [ ] **Step 2: Add `loadAnalyticsStress` and `_renderStressResults` functions**

Find `// g) Fees & costs section` near `loadAnalyticsFees`. Append the following after the closing `}` of `loadAnalyticsFees`:

```javascript
// h) Stress Testing section
async function loadAnalyticsStress(scenario, fromDate, toDate) {
    const body = document.getElementById('anStressBody');
    if (!body) return;

    // Build the scenario selector UI once; preserve it across scenario switches.
    if (!body.dataset.wired) {
        body.dataset.wired = '1';
        const scenarioDefs = [
            { key: '2008', label: '2008 Crisis' },
            { key: '2020', label: '2020 COVID' },
            { key: '2022', label: '2022 Rates' },
            { key: 'dotcom', label: 'Dot-com' },
        ];
        body.innerHTML = `
            <div class="mb-3">
                <div class="d-flex flex-wrap gap-1" id="stressScenarioBtns">
                    ${scenarioDefs.map(s =>
                        `<button type="button" class="btn btn-sm ${s.key === '2008' ? 'btn-secondary' : 'btn-outline-secondary'}" data-stress-scenario="${s.key}">${esc(s.label)}</button>`
                    ).join('')}
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-stress-scenario="custom">Custom</button>
                </div>
                <div id="stressCustomRow" class="mt-2 d-none d-flex align-items-center gap-2">
                    <input type="date" class="form-control form-control-sm" style="width:auto" id="stressFrom">
                    <span class="text-muted small">to</span>
                    <input type="date" class="form-control form-control-sm" style="width:auto" id="stressTo">
                    <button class="btn btn-sm btn-primary" id="stressRunCustom">Run</button>
                </div>
            </div>
            <div id="stressResults"><div class="text-center text-muted py-3"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Fetching historical data&hellip;</div></div>`;

        document.querySelectorAll('#stressScenarioBtns [data-stress-scenario]').forEach(btn => {
            btn.addEventListener('click', () => {
                const s = btn.dataset.stressScenario;
                document.querySelectorAll('#stressScenarioBtns [data-stress-scenario]').forEach(b => {
                    b.classList.toggle('btn-secondary', b === btn);
                    b.classList.toggle('btn-outline-secondary', b !== btn);
                });
                const customRow = document.getElementById('stressCustomRow');
                if (s === 'custom') {
                    customRow.classList.remove('d-none');
                } else {
                    customRow.classList.add('d-none');
                    loadAnalyticsStress(s);
                }
            });
        });

        document.getElementById('stressRunCustom').addEventListener('click', () => {
            const from = document.getElementById('stressFrom').value;
            const to = document.getElementById('stressTo').value;
            if (!from || !to) return;
            loadAnalyticsStress(null, from, to);
        });
    }

    const resultsDiv = document.getElementById('stressResults');
    if (!resultsDiv) return;
    resultsDiv.innerHTML = '<div class="text-center text-muted py-3"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Fetching historical data&hellip;</div>';

    try {
        const d = await window.apiClient.getStressTest(scenario, fromDate, toDate);
        _renderStressResults(resultsDiv, d);
    } catch (err) {
        resultsDiv.innerHTML = `<div class="text-danger small">Error loading stress test: ${esc(err.message)}</div>`;
    }
}

function _renderStressResults(resultsDiv, d) {
    if (!resultsDiv || !d) return;
    const lossCls = d.total_loss_pct < 0 ? 'text-danger' : 'text-success';
    const signStr = (v) => v >= 0 ? '+' : '';
    const hasFallback = d.assets.some(a => a.data_source === 'fallback');
    const maxLoss = Math.max(...d.assets.map(a => Math.abs(a.loss_eur)), 1);

    const rows = d.assets.map(a => {
        const pct = parseFloat(a.historical_return_pct);
        const opacity = Math.min(0.75, Math.abs(a.loss_eur) / maxLoss).toFixed(2);
        const cellStyle = a.loss_eur < 0 ? ` style="background:rgba(220,53,69,${opacity})"` : '';
        return `<tr>
            <td class="fw-semibold">${esc(a.symbol)}</td>
            <td class="text-muted small">${esc(a.name)}</td>
            <td class="text-end">${anFmtEur(a.current_value_eur)}</td>
            <td class="text-end ${pct < 0 ? 'text-danger' : 'text-success'}">${signStr(pct)}${pct.toFixed(1)}%${a.data_source === 'fallback' ? '<sup>*</sup>' : ''}</td>
            <td class="text-end">${anFmtEur(a.stressed_value_eur)}</td>
            <td class="text-end fw-semibold"${cellStyle}>${signStr(a.loss_eur)}${anFmtEur(a.loss_eur)}</td>
        </tr>`;
    }).join('');

    resultsDiv.innerHTML = `
        <div class="row g-3 mb-4">
            <div class="col-6 col-md-3">
                <div class="border rounded p-3">
                    <div class="small text-muted mb-1">Current value</div>
                    <div class="fs-6 fw-bold">${anFmtEur(d.portfolio_current_value_eur)}</div>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="border rounded p-3">
                    <div class="small text-muted mb-1">Stressed value</div>
                    <div class="fs-6 fw-bold">${anFmtEur(d.portfolio_stressed_value_eur)}</div>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="border rounded p-3">
                    <div class="small text-muted mb-1">Estimated loss</div>
                    <div class="fs-6 fw-bold ${lossCls}">${signStr(d.total_loss_eur)}${anFmtEur(d.total_loss_eur)}</div>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="border rounded p-3">
                    <div class="small text-muted mb-1">Loss %</div>
                    <div class="fs-6 fw-bold ${lossCls}">${signStr(d.total_loss_pct)}${parseFloat(d.total_loss_pct).toFixed(1)}%</div>
                </div>
            </div>
        </div>
        ${d.assets.length === 0
            ? '<p class="text-muted small">No priced positions found.</p>'
            : `<div class="table-responsive">
                <table class="table table-sm table-hover mb-1">
                    <thead class="table-light">
                        <tr>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th class="text-end">Value</th>
                            <th class="text-end">Scenario drop</th>
                            <th class="text-end">Stressed</th>
                            <th class="text-end">Loss</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
               </div>
               ${hasFallback ? '<p class="text-muted small mb-0">* Estimated — no historical market data for this asset; asset-type average used.</p>' : ''}`
        }`;
}
```

- [ ] **Step 3: Verify JS smoke test still passes**

```bash
node --test web_client/js/tests/
```

Expected: all JS tests pass (the smoke test loads all 4 JS files into one VM scope and checks for parse errors).

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_analytics.js
git commit -m "feat: stress test JS loader and results renderer"
```

---

## Task 6: Redeploy web container and smoke test

**Files:** None (operational step)

- [ ] **Step 1: Rebuild and restart the web container**

```bash
docker compose build web && docker stop portf_web && docker compose up -d web
```

- [ ] **Step 2: Open the app in a browser**

Navigate to the Analytics page. Confirm the "Stress Test" button appears in the tab bar.

- [ ] **Step 3: Click "Stress Test" tab — confirm 2008 scenario loads**

Expected: spinner shows briefly, then summary card (current/stressed/loss) and asset table render. If the portfolio has no priced positions, you'll see "No priced positions found." — that's correct behaviour.

- [ ] **Step 4: Click "2020 COVID" — confirm scenario switches and reloads**

Expected: new data loads, button highlights change.

- [ ] **Step 5: Click "Custom", enter a date range, click Run**

Example: From `2022-01-01`, To `2022-10-01`. Expected: results render with `scenario: "custom"` in the response.

- [ ] **Step 6: Verify error handling — enter `to` before `from` and click Run**

Expected: error toast or red error message ("End date must be after start date.").

- [ ] **Step 7: Final commit (if any stray changes remain)**

```bash
git status
# If clean: nothing to do. If not:
git add -p
git commit -m "fix: stress test post-deploy cleanup"
```
