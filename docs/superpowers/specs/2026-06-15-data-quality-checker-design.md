# Data Quality Checker — Design Spec
Date: 2026-06-15

## Summary
Add a "Data Quality" tab to the existing Diagnostics page. Three independent checks run in parallel and surface actionable findings: per-portfolio cash reconciliation, fuzzy duplicate transaction detection, and anomaly/suspicious-pattern flagging. Users can delete duplicates and dismiss warnings inline; dismissals persist in localStorage.

## Motivation
The current Diagnostics page only covers price-data freshness. Users have no way to detect:
- Cash balance discrepancies between pfm and the broker (e.g. 15.7k vs 17.4k cash at MyInvestor)
- Transactions re-imported by mistake (slipped past the exact-match dedup)
- Data anomalies such as GBX mis-imports, sells before buys, dividends before first buy

## Architecture

### Backend — Three new endpoints in `portf_server/routers/analytics.py`

All are pure DB queries (no yfinance), so `async def`. Registered under `/api/v1/analytics/dq/`.

```
GET /api/v1/analytics/dq/reconciliation
GET /api/v1/analytics/dq/duplicates
GET /api/v1/analytics/dq/suspicious
```

No new router file — added alongside existing `/data-freshness` and `/update-runs` in `analytics.py`.

### Frontend — `web_client/` changes

- `index.html`: Add Bootstrap tab bar (`Price Health` | `Data Quality`) above the existing Diagnostics cards. Wrap existing cards in the Price Health tab pane. Add Data Quality tab pane with three new card skeletons.
- `pfm_core.js`: Update `loadDiagnosticsPage()` — on tab switch to Data Quality, fire `loadDataQualityTab()` (lazy, only once per page load unless re-run clicked). Wire per-card Re-run buttons.
- `pfm_core.js`: New `loadDataQualityTab()` — parallel fetch of all three DQ endpoints, render each card independently.

Tab state persists in `localStorage` key `pfmDiagTab`.  
Dismissals persist in `localStorage` key `pfmDismissedIssues` as `[{check, key, dismissed_at}]`.

### Existing endpoints reused
- `DELETE /api/v1/transactions/{id}` — used by the Delete action on duplicate findings.

---

## Check Specifications

### 1. Reconciliation (`/dq/reconciliation`)

Per portfolio, computes:

| Field | Formula |
|---|---|
| `net_bookings` | Σ deposits − Σ withdrawals |
| `buy_costs` | Σ (buy_qty × price + fees) |
| `sell_proceeds` | Σ (sell_qty × price − fees) |
| `dividend_income` | Σ dividend total_amount |
| `interest_income` | Σ interest total_amount |
| `implied_cash` | net_bookings − buy_costs + sell_proceeds + dividend_income + interest_income |
| `invested_value` | Σ (held_qty × latest_stored_price, EUR-converted) |
| `total_accounted` | implied_cash + invested_value |

Returns one object per portfolio. No tolerance threshold — user compares raw numbers against broker.

Response shape:
```json
{
  "portfolios": [
    {
      "portfolio_id": 1,
      "portfolio_name": "MyInvestor",
      "net_bookings": 83000.0,
      "buy_costs": 67000.0,
      "sell_proceeds": 1200.0,
      "dividend_income": 450.0,
      "interest_income": 120.0,
      "implied_cash": 17770.0,
      "invested_value": 69200.0,
      "total_accounted": 86970.0
    }
  ]
}
```

### 2. Duplicates (`/dq/duplicates`)

Scans all transactions. Groups by `(portfolio_id, asset_id, transaction_type)`. Within each group, compares every pair where:
- Date within ±3 calendar days
- Quantity within ±5%
- Price within ±5%

Labels:
- `"likely"`: same calendar day + qty within ±1% + price within ±1%
- `"possible"`: wider match (up to ±3 days, ±5%)

Issue key: `dup:{min_id}:{max_id}` (deterministic, order-independent).

Response shape:
```json
{
  "duplicates": [
    {
      "label": "likely",
      "key": "dup:42:99",
      "tx_a": { "id": 42, "date": "2025-03-15", "asset": "VWCE", "type": "buy", "quantity": 10.0, "price": 120.50, "portfolio": "MyInvestor" },
      "tx_b": { "id": 99, "date": "2025-03-15", "asset": "VWCE", "type": "buy", "quantity": 10.0, "price": 120.50, "portfolio": "MyInvestor" }
    }
  ]
}
```

### 3. Suspicious (`/dq/suspicious`)

Replays transactions chronologically per asset. Flags:

| Check | Condition | Severity |
|---|---|---|
| Zero price | buy or sell with price = 0 | warning |
| Zero quantity | any transaction with qty = 0 | warning |
| Negative position | sell pushes running qty below 0 | warning |
| Dividend before buy | dividend date < first buy date for asset | info |
| Price outlier | price > 5× or < 0.2× median price for asset | warning |

Excludes: splits (price = 0 is valid), dividends (qty = 0 is valid for cash dividends).

Issue key: `susp:{tx_id}:{check_slug}` (e.g. `susp:123:zero_price`).

Response shape:
```json
{
  "issues": [
    {
      "severity": "warning",
      "key": "susp:123:zero_price",
      "check": "zero_price",
      "transaction_id": 123,
      "asset": "VWCE",
      "date": "2025-01-10",
      "type": "buy",
      "description": "Buy transaction has price = 0"
    }
  ]
}
```

---

## Frontend UI

### Tab bar (Diagnostics page header area)
```
[ Price Health ]  [ Data Quality ]
```

Bootstrap `nav-tabs` + `tab-content`. Existing cards move into the Price Health pane unchanged.

### Data Quality tab — three cards

**Reconciliation card**
- Header: "Cash & Position Reconciliation" with Re-run (↺) button
- Table: Portfolio · Implied Cash · Invested Value · Total Accounted
- ℹ icon in header opens a tooltip explaining "Implied cash = deposits − withdrawals − buys + sells + dividends + interest"
- Numbers formatted with `Fmt.num()` (respects user locale/decimals prefs)

**Duplicates card**
- Header: "Possible Duplicate Transactions" with Re-run (↺) button
- Each finding: two rows side-by-side, diff-highlighted on differing fields
- Badge: `LIKELY` (danger) or `POSSIBLE` (warning)
- Actions per finding: **Delete older** button (one-click, deletes the transaction with the lower date, tie-breaks on lower ID) · **Delete ▾** dropdown to pick which to delete · **Dismiss ×**
- Footer: "Show N dismissed" toggle (hidden when 0)

**Suspicious card**
- Header: "Suspicious Patterns" with Re-run (↺) button
- Table: Severity badge · Asset · Date · Type · Issue description · Actions
- Actions: **View** (navigates to Transactions page filtered by asset) · **Dismiss ×**
- Footer: "Show N dismissed" toggle

### Dismissal flow
1. User clicks Dismiss → issue key added to `pfmDismissedIssues` in localStorage
2. Item fades out; footer count increments
3. "Show N dismissed" toggles a faded overlay of dismissed items
4. Re-run button reloads from API; dismissals are reapplied client-side

---

## CLAUDE.md Update

Add rule: always update `PROJECT_STATUS.md` and any relevant inline docs (router docstrings, CLAUDE.md sections) when adding or changing features. This is default behaviour for all future work.

---

## Out of Scope
- Server-side dismissal persistence (localStorage is sufficient for a single-user app)
- Automatic fix suggestions beyond delete/view
- Scheduled/background DQ runs
- Cross-portfolio duplicate detection (different brokers may legitimately hold the same asset)
