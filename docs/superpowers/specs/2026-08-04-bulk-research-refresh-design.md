# Bulk Research Refresh ("Refresh all" buy/sell targets)

**Date:** 2026-08-04
**Status:** Approved

## Problem

Buy/sell price ranges (`price_targets.buy_below`/`sell_above`) only get populated when a
user runs the per-symbol Research Workbench (`Generate` → `Save`) for that symbol. As of
this writing, 45 of 46 held positions have no research note less than 90 days old (most
have none at all), which the Action Items page already surfaces as
`errors:stale-research`. There is no way to populate/refresh targets for many assets at
once — only one at a time, by hand.

## Scope

- **Assets covered:** held positions (qty > 0) **and** watchlist symbols, deduplicated by
  symbol. Not the full asset catalogue (assets with zero holdings and not watchlisted are
  out of scope).
- **Selection per run:** only symbols with **no research note, or one 90+ days old**
  (reuses `STALE_RESEARCH_DAYS = 90` from `portf_manager/services/action_items.py`).
  Symbols already researched recently are skipped — no artificial cap is needed since the
  eligible set is naturally bounded by this staleness filter.
- **Overwrite behavior:** when a stale symbol already has a target, the bulk run
  overwrites it automatically with the newly generated value. There is no practical way to
  reproduce the single-symbol flow's `confirm()` prompt (`pfm_features.js`) in an
  unattended background batch.

## Backend

### `get_symbols_needing_refresh(db)` — `portf_manager/services/research.py`

Pure-ish helper (DB read only):
1. `compute_positions(db.get_all_transactions())` → symbols with `quantity > 0`.
2. `db.get_watchlist()` → watchlist symbols.
3. Union of the two, deduplicated by symbol (uppercase).
4. `db.get_latest_research_notes()` is already keyed by `symbol` (not `asset_id`), so it
   works for watchlist-only symbols with no linked asset — include a symbol if it has no
   note, or its latest note's `created_at` is `>= STALE_RESEARCH_DAYS` old.
5. Returns `[{symbol, asset_id (nullable), name}, ...]`.

`STALE_RESEARCH_DAYS` is imported from `action_items.py` inside the function body
(function-level import), matching the codebase's existing convention for avoiding
module-load-time circular imports (e.g. `_fx` in `portfolio_advisor.py`).

### Background job state + endpoints — `portf_server/routers/research.py`

Follows the existing `_BACKFILL` pattern (`portf_server/routers/analytics.py:473-599`)
rather than the simpler `_price_update_state` pattern, because this job needs per-item
progress, not just a running flag:

```python
_BULK_RESEARCH: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_symbol": None,
    "results": [],   # [{symbol, status: "updated" | "no_data" | "error", detail}]
    "started_at": None,
    "finished_at": None,
}
```

- `POST /api/v1/research/bulk-refresh` — if already running, returns
  `{"status": "running", **_BULK_RESEARCH}` (same shape as `backfill-snapshots`);
  otherwise resets `_BULK_RESEARCH`, starts a daemon `threading.Thread`, returns
  `{"status": "started"}`.
- `GET /api/v1/research/bulk-refresh-status` — returns `_BULK_RESEARCH` verbatim, for
  polling.

### Worker loop

Sequential (no concurrency — stays rate-limit-friendly, matches how the single-symbol
flow already retries through `_is_rate_limited` + OpenRouter fallback inside
`generate_valuation_report`). For each eligible symbol:

1. `fetch_fundamentals(symbol, db)`, `fetch_recent_news(symbol, db=db)`.
2. `generate_valuation_report(...)`.
3. **Check for a usable result** (see Error handling — this function swallows its own
   exceptions and returns a fallback dict, so a null check is required, not a try/except).
4. If usable: persist using the same DB calls `/save` uses today —
   `create_research_note(...)`, then `upsert_price_target(...)` for held assets
   (`asset_id` present) and/or `add_watchlist(...)` buy-zone sync for watchlisted symbols
   — always overwriting (per Scope above).
5. If not usable: record `status: "no_data"`, write nothing.
6. Update `_BULK_RESEARCH["done"]`/`["current_symbol"]`/`["results"]` after each symbol so
   polling reflects live progress.

The whole per-symbol pipeline (steps 1–5) is wrapped in try/except; a single symbol
raising an unexpected error is recorded as `status: "error"` and the loop continues with
the next symbol — one bad symbol must not abort the batch.

No new DB table or migration: reuses `research_notes`, `price_targets`, `watchlist`
exactly as the existing single-symbol `/save` endpoint does.

## Frontend (`web_client/js/pfm_core.js`, `web_client/index.html`)

New button next to the `#researchTabs` group at the top of the Research page (visible
from both Workbench and Compare tabs — it's a page-wide action, not tied to whichever
ticker happens to be loaded in the Workbench).

Behavior mirrors `triggerPriceUpdate()`:
- Click → `POST bulk-refresh` → disable button, show spinner.
- Poll `GET bulk-refresh-status` every ~3s; update label to `Refreshing {done}/{total}…`.
- On completion: re-enable button, show a one-line summary (e.g. `Updated 40 of 46 · 6
  had no usable data`), and reload whichever of the Compare table / currently-loaded
  Workbench targets are visible so the new values show up without a manual page reload.

## Error handling & edge cases

- `generate_valuation_report` catches its own exceptions and returns
  `{"fair_value": None, "buy_below": None, "sell_above": None, "summary": "Could not
  generate automated analysis for {symbol}: {e}", ...}` on failure rather than raising.
  The worker treats "all three of `fair_value`/`buy_below`/`sell_above` are null" as
  failure (`status: "no_data"`) and **does not write anything** — an existing target is
  never overwritten with nulls.
- Existing rate-limit handling (`_is_rate_limited` + OpenRouter fallback) inside
  `generate_valuation_report` needs no changes.
- No eligible symbols (everything already fresh): `POST bulk-refresh` still returns
  `{"status": "started"}`, the thread finds an empty list and finishes immediately with
  `total: 0, done: 0`.

## Testing

- Unit tests for `get_symbols_needing_refresh`: held-only, watchlist-only, both, dedup
  across held+watchlist, and the 90-day staleness boundary (89 vs 90 vs 91 days).
- Unit test for the worker's "no usable data → don't overwrite" branch, mocking
  `generate_valuation_report` to return the null-fields fallback and asserting
  `upsert_price_target`/`add_watchlist` are not called.
- Unit test for the "one symbol raises → batch continues" branch.
- No e2e test against a live LLM (consistent with how `generate_report`/`/save` are
  tested elsewhere in this codebase — mocked, not live).

## Out of scope (explicitly deferred)

- A cap on symbols-per-run (like the AI category-suggest feature's cap of 30) — not
  needed here since this runs as a background thread with polling, not a synchronous
  request bound by nginx's `proxy_read_timeout`.
- Concurrency/parallel LLM calls — sequential only, for rate-limit safety.
- Surfacing the full per-symbol `results` list in the UI beyond the one-line summary —
  can be added later if the summary proves insufficient.
