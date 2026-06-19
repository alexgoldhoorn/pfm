# Portfolio Health Analysis — Design Spec

**Date:** 2026-06-19
**Status:** Approved

## Overview

A new "Portfolio Health" panel on the Research page that gathers all available portfolio metrics plus per-holding fundamentals, feeds them to the LLM, and returns a scored health report with a prioritised action list. Results are cached with a user-configurable TTL.

## Location

A collapsible panel inserted at the **top of the Research page**, above the existing per-stock workbench. Consistent with the Research page being the home for LLM-powered portfolio intelligence.

## Backend

### Endpoint

```
GET /api/v1/research/portfolio-analysis?portfolio_id=&refresh=false
```

- Plain `def` endpoint (threadpool — consistent with other yfinance-heavy endpoints)
- Optional `portfolio_id` filter; defaults to all portfolios combined (same as `/analytics/performance`, `/analytics/risk`, etc.)
- `refresh=true` deletes the cache entry and forces a fresh run
- Returns 200 in all cases; errors and missing data are communicated in the response body

### Data Gathering (Parallel)

Six threads run concurrently via `ThreadPoolExecutor`.

The analytics endpoint functions in `routers/analytics.py` currently contain inline logic. As part of this feature, the shared computation blocks (performance, risk, diversification, fees, dividends, tax) are extracted into standalone helper functions — either added to `portf_manager/analytics_helpers.py` (new file) or kept in `analytics.py` as module-level functions. Both the existing analytics endpoints and the new portfolio-analysis endpoint call these helpers directly (no HTTP self-calls).

| Thread | Data | Source |
|--------|------|--------|
| 1 | Performance: CAGR, IRR, Alpha, annualised gain, inception date | Extracted helper from `analytics.py` |
| 2 | Risk: Sharpe, Sortino, Calmar, Beta, Alpha, max drawdown, volatility | Extracted helper from `analytics.py` |
| 3 | Diversification: sector/country/currency/type breakdown, HHI | Extracted helper from `analytics.py` (already fetches yfinance) |
| 4 | Fees + dividends: fee drag per broker, TTM dividend income, yield | Extracted helper from `analytics.py` |
| 5 | Tax: harvestable loss candidates, estimated tax saving | Extracted helper from `analytics.py` |
| 6 | Per-holding fundamentals: P/E, yield, sector via `market.get_fundamentals()` per held ticker | `portf_manager/market.py` (no extraction needed) |

If a thread fails (e.g. yfinance rate-limit), the endpoint logs the error, omits that data, and continues. The LLM prompt notes any missing sections. The response includes a `data_warnings` list.

### LLM Call

A single structured prompt (no search grounding — data is already rich). Prompt sections:

1. Portfolio snapshot — total value, CAGR, IRR, inception date
2. Risk metrics — Sharpe, Sortino, Calmar, Beta, Alpha, max drawdown, volatility
3. Diversification — sector/country/currency/type breakdown with HHI
4. Holdings + fundamentals — per-asset: weight %, P/E, yield, sector
5. Income — dividend TTM, projected annual, yield-on-cost
6. Fees — per-broker fee drag %
7. Tax — harvestable loss candidates with estimated saving

The prompt requests **strict JSON** matching the response schema. Uses the same `json.loads()` + fallback pattern as `generate_valuation_report()`.

### Response Schema

```json
{
  "scores": {
    "diversification":      {"score": 7, "reason": "Good sector spread but 45% tech concentration"},
    "risk_adjusted_return": {"score": 8, "reason": "Sharpe 1.2 above typical 0.8 threshold"},
    "income":               {"score": 5, "reason": "Low dividend yield, few income-generating assets"},
    "fees":                 {"score": 9, "reason": "Avg fee drag 0.12%, well below 0.5% benchmark"},
    "tax_efficiency":       {"score": 6, "reason": "€800 harvestable losses available"}
  },
  "recommendations": [
    {
      "priority": 1,
      "category": "Diversification",
      "action": "Reduce tech sector weight from 45% to below 30%",
      "rationale": "HHI of 0.28 indicates high concentration; a sector correction would hit this portfolio hard."
    }
  ],
  "summary": "Strong risk-adjusted returns but geographic and sector concentration are the main weaknesses.",
  "generated_at": "2026-06-19T10:00:00",
  "cache_ttl_hours": 24,
  "data_warnings": []
}
```

On LLM parse failure: `{"error": "Analysis unavailable — LLM returned unexpected format. Try again."}`.

### Caching

- Table: existing `kv_cache`
- Key: `portf:advisor:all` or `portf:advisor:{portfolio_id}`
- TTL: read at request time from `app_settings.advisor_cache_ttl_hours` (default 24)
- One cache entry per portfolio scope

### Settings Endpoint

```
GET  /api/v1/research/portfolio-analysis/settings
PUT  /api/v1/research/portfolio-analysis/settings   body: {"cache_ttl_hours": 24}
```

Reads/writes `advisor_cache_ttl_hours` in the `app_settings` table.

## Frontend

### Panel States

**No cache:** Panel open by default. Shows a brief description, a portfolio dropdown (defaulting to "All"), and a "Run Analysis" button.

**Loading:** Button replaced with a spinner. Status text cycles through: "Gathering metrics… Fetching fundamentals… Analysing…" (animated while awaiting the synchronous response).

**Result loaded:** Panel collapses to a summary bar: `Overall health: 7/10 · Last analysed 2h ago · Refresh`. Clicking expands the full report.

### Report Layout (Expanded)

```
[ Portfolio: All ▾ ]                          [ Refresh ]  [ Last analysed 2h ago ]

SCORES
┌─────────────────┬──────────────────┬─────────────────┐
│ Diversification │ Risk/Return      │ Income          │
│    7 / 10       │    8 / 10        │    5 / 10       │
│ Good spread but │ Sharpe 1.2,      │ Low yield,      │
│ 45% tech        │ above avg        │ few dividends   │
└─────────────────┴──────────────────┴─────────────────┘
┌─────────────────┬─────────────────┐
│ Fees            │ Tax Efficiency  │
│    9 / 10       │    6 / 10       │
│ 0.12% drag      │ €800 harvestable│
└─────────────────┴─────────────────┘

SUMMARY
Strong risk-adjusted returns but geographic and sector concentration
are the main weaknesses to address.

TOP RECOMMENDATIONS
1. [Diversification]  Reduce tech sector from 45% → <30%
2. [Income]           Add 1-2 dividend ETFs to reach 2%+ yield
3. [Tax]              Harvest €800 loss on XYZ before year-end
```

- Score cards: Bootstrap color scale — `success` (8–10), `warning` (5–7), `danger` (1–4)
- Recommendation categories shown as Bootstrap pill badges
- `data_warnings` shown as a small `info` alert above the scores if non-empty

### Settings Modal

New "Portfolio Advisor" row in the Settings modal with a TTL dropdown: **6h / 24h (default) / 7d**. Saves via the settings endpoint and reflects immediately on the next analysis run.

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Thread fails (yfinance rate-limit) | Log + continue; add to `data_warnings`; LLM caveat output |
| LLM returns malformed JSON | Return `{"error": "..."}` in 200; frontend shows inline alert |
| Empty portfolio (no transactions) | Return friendly message, no 422 |
| Cache miss + LLM unavailable | Return error message; no stale cache served |

## Testing

- `tests/unit/test_portfolio_analysis.py` — unit tests for data-gathering helpers and LLM prompt builder (mocked LLM + mocked yfinance)
- Settings endpoint covered in `tests/unit/test_imports_exports.py` pattern

## Out of Scope (V1)

- Historical comparison of health scores over time
- Per-asset improvement suggestions (that's the existing Research workbench)
- Web search grounding (data context is already rich without it)
- Push notifications when score drops below a threshold
