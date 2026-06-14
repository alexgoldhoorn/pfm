# Portfolio Stress Testing — Design Spec

**Date:** 2026-06-14
**Status:** Approved

## Overview

Add a Stress Testing tab to the Analytics page. Users select a historical market crash scenario (or define a custom date range) and see how their current holdings would have performed had they held them through that period. Results are per-asset, using actual yfinance historical returns where available and conservative asset-type fallbacks where not.

---

## Scenarios

Four built-in presets, each defined as a named date range (not hardcoded percentages):

| Scenario key | Label | Date range | Reference index peak-to-trough |
|---|---|---|---|
| `2008` | 2008 Financial Crisis | 2007-10-01 to 2009-03-09 | S&P 500 -57% |
| `2020` | 2020 COVID Crash | 2020-02-19 to 2020-03-23 | S&P 500 -34% |
| `2022` | 2022 Rate Hike Selloff | 2021-12-31 to 2022-10-12 | S&P 500 -25% |
| `dotcom` | Dot-com Bust | 2000-03-24 to 2002-10-09 | Nasdaq -78% |

Custom mode: user supplies `from` and `to` dates (YYYY-MM-DD).

---

## Backend

### Endpoint

```
GET /api/v1/analytics/stress-test
```

**Query params:**
- `scenario` — one of the preset keys above; OR
- `from` + `to` — custom date range (ISO 8601)

Exactly one of `scenario` or (`from` + `to`) must be present; return 400 otherwise.

### Logic

1. Load all current holdings (positions with EUR value) via the existing `compute_positions` from `portf_manager/positions.py`.
2. For each held asset, resolve the yfinance symbol: use `asset.ticker` if set (v18 column), else `asset.symbol`.
3. Fetch the closing price on (or nearest trading day to) `from` and `to` via `yf.Ticker(sym).history(start=from, end=to+1d)`. yfinance handles weekend/holiday alignment automatically.
4. Compute `historical_return_pct = (price_to - price_from) / price_from * 100`.
5. For assets where yfinance returns no data (ISIN-only funds, Mintos/P2P, fixed deposits, manual assets, or any fetch failure): apply a conservative asset-type fallback from the hardcoded table below. Log a warning server-side; do not surface the error to the client.
6. Compute `stressed_value_eur = current_value_eur * (1 + historical_return_pct / 100)`.
7. Aggregate portfolio totals.

### Fallback table (asset_type -> return % per scenario)

| Asset type | 2008 | 2020 | 2022 | dotcom |
|---|---|---|---|---|
| `stock` / `etf` / `index` | -50% | -32% | -22% | -60% |
| `fund` | -40% | -25% | -18% | -45% |
| `bond` | -5% | +5% | -15% | +5% |
| `crypto` | 0%* | -50% | -65% | 0%* |
| `commodity` | -30% | -20% | +20% | -15% |
| `interest` / `deposit` / manual | 0% | 0% | 0% | 0% |

*Crypto didn't exist during 2008/dotcom — treated as unaffected (no meaningful proxy).

For custom date ranges, assets with no yfinance data fall back to the `stock` row percentage computed from the S&P 500 (`^GSPC`) return over the same period.

### Caching

Preset scenarios: cache key `stress:{scenario}`, TTL 7 days (via `kv_cache`). The stress test applies to the full portfolio (no per-portfolio filtering). Custom date ranges are not cached.

### Response schema

```json
{
  "scenario": "2008",
  "label": "2008 Financial Crisis",
  "from_date": "2007-10-01",
  "to_date": "2009-03-09",
  "portfolio_current_value_eur": 45000.00,
  "portfolio_stressed_value_eur": 23400.00,
  "total_loss_eur": -21600.00,
  "total_loss_pct": -48.0,
  "assets": [
    {
      "symbol": "AAPL",
      "name": "Apple Inc.",
      "asset_type": "stock",
      "current_value_eur": 5000.00,
      "historical_return_pct": -55.2,
      "stressed_value_eur": 2240.00,
      "loss_eur": -2760.00,
      "data_source": "yfinance"
    },
    {
      "symbol": "MINTOS",
      "name": "Mintos P2P",
      "asset_type": "interest",
      "current_value_eur": 3000.00,
      "historical_return_pct": 0.0,
      "stressed_value_eur": 3000.00,
      "loss_eur": 0.00,
      "data_source": "fallback"
    }
  ]
}
```

For custom date ranges, `"scenario": "custom"` is returned in the response.

### Router

New function `stress_test()` in `portf_server/routers/analytics.py`. Blocking `def` (yfinance I/O), runs in FastAPI threadpool. Reuses `compute_positions` from `portf_manager/positions.py` and the existing EUR conversion helpers already used in the portfolios router.

---

## Frontend

### Placement

New lazy tab **"Stress"** in the Analytics page (`pfm_analytics.js`), alongside Performance / Dividends / Gain-Loss / Tax / Risk / Fees. Loaded on first click (same lazy pattern as other tabs).

### Layout

```
[ 2008 Crisis ] [ 2020 COVID ] [ 2022 Rates ] [ Dot-com ] [ Custom v ]

  Custom: From [____] To [____] [Run]

+----------------------------------------------------------+
|  Current value    Stressed value    Total loss            |
|  EUR 45,000       EUR 23,400       -EUR 21,600 (-48%)    |
+----------------------------------------------------------+

+----------+--------------+----------+---------------+----------+----------+
| Symbol   | Name         | Value    | Scenario drop | Stressed | Loss     |
+----------+--------------+----------+---------------+----------+----------+
| AAPL     | Apple Inc.   | EUR 5000 | -55.2%        | EUR 2240 | -EUR2760 |
| MINTOS   | Mintos P2P   | EUR 3000 | 0% *          | EUR 3000 | EUR 0    |
+----------+--------------+----------+---------------+----------+----------+

* Estimated — no historical data for this asset
```

- Scenario buttons are a button group; active scenario highlighted.
- Custom panel (From/To date inputs + Run button) hidden by default, shown on Custom click.
- Summary card uses `pfm-amt` span wrapper for privacy blur.
- Table sorted by loss EUR descending by default; sortable via `makeSortableTable`.
- Rows where `data_source == "fallback"` show a `*` on the drop % cell with a footnote below the table.
- Loss column cells colour-coded: red gradient scaled to magnitude of % loss.
- Loading state: spinner while fetching. Error toast on failure.

---

## Error handling

- No holdings: show "No positions found. Import transactions first."
- yfinance fetch failure for a specific ticker: demote silently to fallback; log warning server-side.
- Custom date range where `to <= from`: return 400 with message "End date must be after start date."
- Custom date range where yfinance returns no data at all (e.g. future date or very old): return 400 with message "No market data available for the selected date range."

---

## Testing

- Unit test: fallback table lookup returns correct % per scenario and asset type.
- Unit test: response aggregates correctly for a mock portfolio with mixed yfinance / fallback assets.
- Unit test: 400 on invalid params (missing both scenario+dates, `to <= from`).
- Unit test: `scenario: "custom"` returned when using date range params.
- No real yfinance calls in tests — mock `yf.Ticker().history()`.
