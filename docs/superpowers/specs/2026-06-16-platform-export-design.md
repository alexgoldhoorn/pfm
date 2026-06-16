# Platform Export — Yahoo Finance & Simply Wall St

**Date:** 2026-06-16  
**Status:** Approved  
**Scope:** CSV export of portfolio holdings/transactions in formats accepted by Yahoo Finance and Simply Wall St, surfaced via a new card on the Import/Export page.

---

## Background

Yahoo Finance and Simply Wall St both support portfolio tracking via CSV import. Neither offers a free write API, so "sync" means generating a downloadable CSV that the user uploads manually to each platform. This feature adds that export capability to PFM.

---

## Goals

- Generate a correctly-formatted CSV for Yahoo Finance (transaction history or current positions)
- Generate a correctly-formatted CSV for Simply Wall St (transaction history or current positions)
- Let the user choose platform, mode, and portfolio filter at export time
- Warn clearly when assets are skipped due to missing ticker symbols

## Non-goals

- Live API push/pull (no free API exists)
- Bidirectional sync (import from Yahoo Finance / SWS is out of scope)
- Exporting dividends/interest — not meaningful for these platforms' portfolio trackers

---

## Architecture

### New module: `portf_manager/platform_export.py`

Contains all CSV generation logic. No FastAPI dependencies — pure functions, independently testable.

**Public API:**

```python
def build_yahoo_finance_csv(
    db, portfolio_id: int | None, mode: str
) -> tuple[str, list[str]]:
    """Returns (csv_content, skipped_symbols). mode: transactions | positions"""

def build_simply_wall_st_csv(
    db, portfolio_id: int | None, mode: str
) -> tuple[str, list[str]]:
    """Returns (csv_content, skipped_symbols). mode: transactions | positions"""
```

**Internal helpers:**

```python
def _is_isin(s: str) -> bool:
    """True if s is a 12-char ISIN (2 alpha + 10 alnum)."""

def _resolve_ticker(symbol: str, ticker: str | None) -> str | None:
    """Returns ticker to use for export, or None if unresolvable.
    Priority: ticker column -> symbol if not ISIN -> None"""
```

**Data query** (dedicated SQL, not reusing `_TX_COLS` which lacks `a.ticker`):

```sql
SELECT
    t.transaction_type, t.quantity, t.price, t.fees,
    t.transaction_date, COALESCE(t.currency, a.currency) AS currency,
    a.symbol, a.name, a.ticker
FROM transactions t
JOIN assets a ON t.asset_id = a.id
WHERE t.transaction_type IN ('buy', 'sell')
  [AND t.portfolio_id = ?]
ORDER BY t.transaction_date ASC
```

**Positions mode** implementation:
1. Fetch all buy/sell transactions with the query above
2. Use `portf_manager.positions.compute_positions()` to collapse to current holdings
3. For each position with quantity > 0, resolve ticker and emit one row

---

### Extended `portf_server/routers/exports.py`

Two new endpoints on the existing `/api/v1/export` router:

```
GET /api/v1/export/yahoo-finance?portfolio_id=<int>&mode=transactions|positions
GET /api/v1/export/simply-wall-st?portfolio_id=<int>&mode=transactions|positions
```

Both return:
- `StreamingResponse` with `Content-Type: text/csv`
- `Content-Disposition: attachment; filename=yahoo_finance_portfolio.csv`
- `X-Skipped-Count: N`
- `X-Skipped-Symbols: SYM1,SYM2,...`

Auth via the existing `_auth` dependency. `mode` defaults to `transactions`.

---

### Web UI — `pfm_features.js` (`setupImportExport`)

New **Platform Export** card below existing export buttons.

Controls:
- Platform dropdown: Yahoo Finance | Simply Wall St
- Mode radio: Full transaction history (default) | Current positions only
- Portfolio dropdown: All portfolios + individual names (from existing portfolios list)
- Download button

Download uses `fetch()` (not a raw link) so response headers can be read before triggering the blob download. If `X-Skipped-Count > 0`, an inline warning renders below the button listing the skipped symbols.

Platform/mode selections are ephemeral — not persisted to `PREFS`.

---

## CSV Formats

### Yahoo Finance

Date format: `MM/DD/YYYY`. Sells = negative Shares.

```
Symbol,Shares,Purchase Price,Purchase Date,Commission
AAPL,10,150.00,01/15/2023,0.00
ASML.AS,5,680.50,03/10/2023,4.95
AAPL,-3,195.00,06/01/2024,0.00
```

Positions mode: one row per asset, quantity only, price/date/commission empty.

### Simply Wall St

Date format: `YYYY-MM-DD`. Sells = negative shares. Includes Currency column.

```
Ticker Symbol,Number of Shares,Purchase Price (Per Share),Purchase Date,Currency
AAPL,10,150.00,2023-01-15,USD
ASML.AS,5,680.50,2023-03-10,EUR
AAPL,-3,195.00,2024-06-01,USD
```

Positions mode: one row per asset, quantity + currency, price/date empty.

---

## Ticker Resolution

```
a.ticker set?          -> use it  (e.g. "NVDA", "ASML.AS", "BTC-EUR")
a.symbol not ISIN?     -> use symbol directly  (e.g. "MINTOS", "BTC-EUR")
a.symbol is ISIN + no ticker -> skip, add to skipped_list
```

ISIN detection: `len(s) == 12 and s[:2].isalpha() and s[2:].isalnum()`

Assets most likely skipped: MyInvestor/Indexa fund ISINs. User can resolve by setting `ticker` on those assets via the Assets page.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| No transactions for portfolio | CSV with headers only, X-Skipped-Count: 0 |
| All assets ISIN-only | CSV headers only, X-Skipped-Count: N |
| DB error | HTTP 500 |
| Unknown mode value | HTTP 422 (FastAPI validation) |

---

## Testing

New file: `tests/unit/test_platform_export.py`

- `_is_isin`: valid ISIN, short ticker, crypto `BTC-EUR`
- `_resolve_ticker`: ticker wins; short symbol used directly; ISIN-only returns None
- `build_yahoo_finance_csv` transactions: buys positive, sells negative, ISIN-only skipped
- `build_yahoo_finance_csv` positions: collapsed to net holdings, zero positions excluded
- `build_simply_wall_st_csv`: same coverage, YYYY-MM-DD dates, currency column present
- Both: empty input -> headers-only CSV, no crash
- Both: all ISIN-only -> headers-only CSV, all in skipped list

---

## Files Changed

| File | Change |
|---|---|
| `portf_manager/platform_export.py` | New — CSV generation logic |
| `portf_server/routers/exports.py` | Two new endpoints |
| `web_client/js/pfm_features.js` | Platform Export card in `setupImportExport()` |
| `tests/unit/test_platform_export.py` | New — unit tests |
| `CLAUDE.md` | Add endpoint signatures under Export API section |
| `PROJECT_STATUS.md` | Bump date, add to recent summary |
