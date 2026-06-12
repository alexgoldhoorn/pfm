# Ticker-driven pricing + fast_info fallback

**Date:** 2026-06-12
**Status:** Approved design (pending spec review)
**Scope:** Price-update path (`portf_manager/cli.py` + `portf_manager/api_client.py`).
Backend only; no schema change.

## Context & goal

Some held funds (e.g. `IE000AK4O3W6` iShares € Corp Bond, `LU0389812693` Amundi
JPM Global Govt Bond) are skipped every price run and flagged stale. Two reasons:

1. `cli.update_prices` fetches by the ISIN `symbol`; the v18 `assets.ticker`
   column (the Yahoo symbol) is never used for pricing.
2. Even with the right Yahoo ticker, those listings resolve via
   `yf.Ticker(sym).fast_info.last_price` but **not** via the batch `yf.download`
   that `fetch_latest_prices` uses — so the batch marks them "invalid".

Verified: `IE000AK4O3W6.SG` → 5.54 €, `LU0389812693.LU` → 1276.8 € via fast_info
(matching held values). Their tickers are already set in the DB.

Goal: let the `ticker` column drive the fetch, and recover download-misses via
fast_info, so these (and any future ticker-mapped fund) price daily.

## Changes

### 1. Use `ticker` in `cli.update_prices` (`portf_manager/cli.py`)
In the symbol→yf mapping loop, the non-crypto branch currently does
`yf_to_db[sym] = sym`. Change to fetch by the ticker when set:
```python
                    else:
                        yf_ticker = asset.get("ticker") or sym
                        yf_to_db[yf_ticker] = sym
                        yf_quote_ccy[yf_ticker] = None
```
`yf_to_db` already re-keys results back to the DB `symbol`, so the price fetched
under `IE000AK4O3W6.SG` is stored under `IE000AK4O3W6`. Crypto keeps its
`_CRYPTO_YF_OVERRIDES` path unchanged.

### 2. fast_info fallback in `fetch_latest_prices` (`portf_manager/api_client.py`)
After the batch download populates `results` / `invalid_symbols`, before the
existing "complete failure" individual fallback, retry each batch-invalid symbol
via `fast_info` (handles GBp like the batch path):
```python
            # yf.download returns nothing for some listings (e.g. Stuttgart .SG /
            # Luxembourg .LU fund quotes) that still resolve via fast_info. Retry
            # each symbol the batch marked invalid before giving up on it.
            if invalid_symbols:
                still_invalid = []
                for symbol in invalid_symbols:
                    try:
                        fi = yf.Ticker(symbol).fast_info
                        px = fi.last_price
                        if px is not None and not pd.isna(px) and float(px) > 0:
                            px = float(px)
                            if fi.currency == "GBp":
                                px = px / 100.0
                            results[symbol] = px
                        else:
                            still_invalid.append(symbol)
                    except Exception:
                        still_invalid.append(symbol)
                invalid_symbols[:] = still_invalid
```
Only the few download-misses hit this per-symbol path, so overhead is minimal.
`pd` (pandas) is already imported in this module.

## Testing
**Unit (`tests/`, mocked — no live yfinance):**
- `fetch_latest_prices`: mock the batch to return one valid + one invalid symbol,
  and `yf.Ticker(<invalid>).fast_info` to return a price → the invalid symbol is
  recovered into results, not raised. A second case: fast_info also fails → stays
  invalid (warning, not crash). A GBp fast_info price is ÷100.
- `cli.update_prices`: with `fetch_latest_prices` mocked, an asset that has a
  `ticker` set causes the ticker (not the ISIN) to be in the symbols passed to
  `fetch_latest_prices`, and the returned price is stored under the ISIN symbol.

**Manual:** run `update-prices` (server-mode cron path); confirm
`IE000AK4O3W6` / `LU0389812693` now get a fresh price and drop off the stale list.

## Out of scope
- Auto-discovering tickers (the user sets them; we just use them).
- Changing crypto handling or the GBX path for batch results.
- A general per-symbol fetch rewrite (keep the batch as primary).
