# Fixed Deposits — Design Spec

**Date:** 2026-06-14
**Status:** Approved

## Overview

Add first-class support for fixed-term deposits (depósitos a plazo fijo) that are not covered by the PDT format. Deposits contribute to net worth while active, and their interest payout flows into the existing income analytics at maturity.

Scope: manual entry + LLM text extraction. No file import parser. No recurring interest — a single payout at maturity.

---

## Schema

New table `fixed_deposits` added in the next DB migration (bump `DATABASE_VERSION`):

```sql
CREATE TABLE fixed_deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    portfolio_id INTEGER REFERENCES portfolios(id),
    principal REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    interest_rate REAL NOT NULL,       -- annual %, e.g. 4.0
    start_date TEXT NOT NULL,          -- ISO date YYYY-MM-DD
    maturity_date TEXT NOT NULL,       -- ISO date YYYY-MM-DD
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'matured', 'closed')),
    interest_paid REAL,                -- populated when status → matured
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

`projected_interest` is computed on read (not stored):
```
principal × (interest_rate / 100) × (days_between(start_date, maturity_date) / 365)
```

No new `asset_type` value is needed — deposits are not assets in the existing sense.

---

## API

New router `portf_server/routers/deposits.py`, mounted at `/api/v1/deposits` in `app.py`.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/deposits/` | List all deposits; each row includes computed `projected_interest` and `days_remaining` |
| `POST` | `/api/v1/deposits/` | Create a deposit |
| `PUT` | `/api/v1/deposits/{id}` | Update fields (all optional) |
| `DELETE` | `/api/v1/deposits/{id}` | Delete |
| `POST` | `/api/v1/deposits/{id}/mature` | Mark as matured; body: `{interest_paid: float, date: str}` |

### Mature endpoint detail

`POST /api/v1/deposits/{id}/mature` does three things atomically:

1. `get_or_create_asset(symbol="DEPOSITS", name="Fixed Deposits Interest", asset_type="cash")` — synthetic asset, same pattern as `MINTOS`
2. `db.create_transaction(asset_id=..., tx_type="interest", quantity=1, price=interest_paid, date=date, portfolio_id=deposit.portfolio_id, currency=deposit.currency)`
3. `db.update_fixed_deposit(id, status="matured", interest_paid=interest_paid)`

Returns `{transaction_id, deposit_id}`.

### Net worth integration

`GET /api/v1/networth` gains a `deposits_eur` field: sum of `principal × fx(currency)` for all `status='active'` deposits. The `net_worth_eur` total includes this.

Response shape addition:
```json
{
  "deposits_eur": 5000.00,
  "deposits": [...],
  ...
}
```

---

## LLM Extraction

### New method: `GeminiClient.extract_deposits(text: str) -> List[dict]`

Located in `portf_manager/gemini_client.py`, following the same structure as `extract_bookings`.

Prompt extracts: `name`, `principal`, `currency`, `interest_rate` (annual %), `start_date`, `maturity_date`. Returns a JSON array; returns `[]` on any failure.

### New endpoint: `POST /api/v1/llm/extract-deposits`

In `portf_server/routers/llm.py`, following the `extract-bookings` pattern (synchronous, simple — deposit statements are short).

Request: `{"text": "..."}` (same `TransactionExtractionRequest` schema)
Response: `{"deposits": [...], "count": N}`

---

## Web UI

Changes to the Net Worth page (`pfm_features.js` — `setupNetworthPage` / `loadNetworthPage`).

### Fixed Deposits section

Rendered below the manual assets table. Contains:

1. **Deposits table** — columns: Name | Broker | Principal | Rate | Maturity | Projected Interest | Status | Actions
   - Active rows have a "Mature" button
   - All rows have a delete button
2. **Add deposit form** — inline: Name, Broker (portfolio select), Principal, Currency, Rate (%), Start Date, Maturity Date, Notes → `POST /api/v1/deposits/`
3. **LLM paste panel** — textarea + "Extract" button → calls `POST /api/v1/llm/extract-deposits` → shows preview table → "Save all" button calls `POST /api/v1/deposits/` for each row

### Mature modal

Triggered by "Mature" button on a deposit row. Fields:
- Actual interest paid (pre-filled with `projected_interest`)
- Payout date (pre-filled with `maturity_date`)

On confirm: `POST /api/v1/deposits/{id}/mature` → show toast "Interest of €X recorded" → refresh table.

---

## Income Analytics Integration

No analytics code changes required. The `interest` transaction created at maturity is picked up automatically by:

- Analytics → Dividends tab (interest income line, grouped by year/month)
- `GET /api/v1/analytics/tax-estimate` (interest feeds `interest_income_eur` in the IRPF savings base)
- `GET /api/v1/analytics/tax-report`

The synthetic `DEPOSITS` asset has `auto_price=0` (no price cron attempts) and no exchange — set these on creation.

---

## Testing

- `tests/unit/test_deposits.py` — CRUD + mature endpoint + projected_interest calculation
- `tests/test_database.py` — bump `assert version == N` to new version
- LLM extract: mock `GeminiClient.extract_deposits` in a unit test

---

## Out of Scope

- Automatic maturity notifications (could use the price-alerts cron pattern later)
- Partial early withdrawal
- Compound interest (simple interest only: `P × r × t`)
- PDT export of deposits
