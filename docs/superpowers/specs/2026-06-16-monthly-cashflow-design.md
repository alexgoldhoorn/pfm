# Monthly Cash Flow — Design Spec

**Date:** 2026-06-16
**Status:** Approved

## Summary

Add a simple monthly cash flow tracker to the Net Worth page. Users maintain a small list of recurring income and expense entries (salary, mortgage payment, loan payment, etc.). The page shows a summary card — total income, expense breakdown by category, and net monthly figure. The net figure is also available to Goals and Forecast pages later.

---

## Data Model

New table `monthly_cashflow`, added in **database v20**.

```sql
CREATE TABLE monthly_cashflow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    category    TEXT NOT NULL CHECK(category IN ('salary','other_income','mortgage','loan','rest')),
    amount      REAL NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'EUR',
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
)
```

**Category semantics:**
- Income: `salary`, `other_income`
- Expense: `mortgage`, `loan`, `rest`

`type` (income/expense) is derived from the category — no separate column. This matches the pattern used by `NW_LIABILITY_CATS` in `manual_assets`.

The fixed category set is intentional. Future mortgage/loan calculator helpers will target known category names. New categories can be added by extending a constant.

---

## API

Endpoints added inline to the existing `networth.py` router (already mounted at `/api/v1/networth`).

```
GET  /api/v1/networth/cashflow         → list all entries + net_monthly_eur
POST /api/v1/networth/cashflow         → create {label, category, amount, currency, notes?}
DELETE /api/v1/networth/cashflow/{id}  → remove entry
```

### GET response shape

```json
{
  "items": [
    {"id": 1, "label": "Salary", "category": "salary", "amount": 3500, "currency": "EUR", "amount_eur": 3500},
    {"id": 2, "label": "Mortgage", "category": "mortgage", "amount": 1200, "currency": "EUR", "amount_eur": 1200}
  ],
  "income_eur": 3500,
  "expenses_eur": 1200,
  "net_monthly_eur": 2300,
  "by_category": {
    "salary": 3500, "other_income": 0,
    "mortgage": 1200, "loan": 0, "rest": 0
  }
}
```

`amount_eur` converts non-EUR entries via the same `_fx()` helper used by manual assets. `net_monthly_eur` = `income_eur − expenses_eur`.

No update endpoint — delete and re-add is sufficient for low-frequency data.

---

## Database Layer (`portf_manager/database.py`)

Four methods on `Database`:
- `get_monthly_cashflow() → List[Dict]`
- `create_monthly_cashflow(label, category, amount, currency, notes) → int`
- `delete_monthly_cashflow(id) → bool`

Table added in:
- `_create_all_tables()` — for fresh databases
- `_migrate_to_v20()` — for existing databases (same dual-add pattern as v17+)

`DATABASE_VERSION` bumped from 19 → 20. `tests/test_database.py` version assertion updated accordingly.

---

## Web UI

### Summary cards (new row on Net Worth page)

Four cards in a row, same style as existing `nwBrokerage` / `nwAssets` cards:

| Card | Value |
|---|---|
| Monthly income | `income_eur` (green) |
| Mortgage | `by_category.mortgage` |
| Loan | `by_category.loan` |
| Rest | `by_category.rest` |
| **Net / month** | `net_monthly_eur` (blue if ≥ 0, red if < 0) |

Five cards total; "Net / month" spans or is the last card, styled like `nwTotalCard`.

### Entries table

Below the summary cards: a small table listing all entries with columns: Label | Category badge | Amount | EUR | Delete button. Same pattern as the manual assets table.

### Add form

Inline form (same pattern as the manual assets add form):
- Label (text)
- Category (select: Salary / Other income / Mortgage / Loan / Rest)
- Amount (number)
- Currency (text, default EUR)
- Notes (optional text)
- Submit button

### JS location

New functions in `pfm_analytics.js` (alongside `loadNetworthPage`):
- `_loadCashflow()` — fetches and renders the cashflow section
- `_wireCashflowForm()` — wires the add form submit
- `window.confirmDeleteCashflow(id)` — delete with confirm

`loadNetworthPage()` calls `_loadCashflow()` as part of its load sequence.

---

## HTML (`index.html`)

New section within `#networthPage`, after the fixed deposits section:

```html
<!-- Monthly Cash Flow -->
<div class="row g-3 mb-3">
  <!-- 5 summary cards -->
</div>
<div class="row g-3">
  <div class="col-lg-4"><!-- add form --></div>
  <div class="col-lg-8"><!-- entries table --></div>
</div>
```

---

## Help Text

Add `monthly_cashflow` entry to `PAGE_HELP` in `help_text.js` (or extend the existing `networth` entry). Short description: recurring income and expense items used to compute net monthly cash flow.

---

## Testing

- `tests/test_database.py` — bump version assertion to 20; add CRUD tests for `monthly_cashflow`
- `tests/unit/test_networth.py` (new file) — GET returns correct `net_monthly_eur`, FX conversion, empty state
- No JS unit tests needed (no pure-logic functions beyond what's already tested)

---

## Out of Scope

- Mortgage amortization calculator (future helper)
- Net monthly figure feeding Goals/Forecast pages (future wiring — endpoint is ready, consumers not built yet)
- Edit-in-place for cashflow entries (delete + re-add is sufficient)
- Historical monthly cash flow tracking
