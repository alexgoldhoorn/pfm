# Action Items Page — Cross-Cutting Maintenance Checklist

**Date:** 2026-07-16
**Status:** Approved

## Problem

Alex currently has to visit several separate pages to figure out "what needs my
attention": Diagnostics (price health, data quality), Net Worth (setup
checklist), Goals (on-track flag), Watchlist/Research (price-target alerts),
and Portfolios (has a broker gone quiet?). There's no single place that says
"here's what to do today." Todoist ticket asked for one aggregated TODO list
covering: stale broker imports, transaction inconsistencies, errors, missing/
stale net worth data, goals issues, and buy/sell watchlist signals.

## Scope

- New backend endpoint `GET /api/v1/action-items/` aggregating checks that
  don't already have a single-call equivalent: stale broker imports, data
  quality summary, price-update-run failures, stale research on held assets,
  off-track goals, watchlist/research price-target alerts.
- New frontend page ("Action Items") that calls that endpoint plus
  `GET /api/v1/networth/` (to run the existing, already-tested
  `computeNetWorthChecklist()` client-side) and merges everything into one
  sorted, dismissible list.
- **Not doing**: no new backend logic for the net-worth checklist (stays JS,
  see rationale below), no unread-count nav badge, no push/Telegram wiring.

## Backend

### New files
- `portf_manager/services/action_items.py` — `get_action_items(db) -> list[dict]`
- `portf_server/routers/action_items.py` — `GET /api/v1/action-items/`, plain
  `def` (pure DB reads, no yfinance calls), registered in `app.py`.

### Item shape

```python
{
  "id": str,          # deterministic per underlying entity, e.g. "import:portfolio:3"
  "category": str,    # "import" | "data_quality" | "errors" | "goals" | "watchlist"
  "severity": str,    # "high" | "medium" | "low"
  "title": str,
  "detail": str,
  "link_page": str,   # frontend nav page key, e.g. "importexport", "diagnostics"
  "context": dict,    # optional ids for the frontend (portfolio_id, goal_id, ...)
}
```

Response: `{"items": [...], "generated_at": "<iso timestamp>"}`.

### Checks

Each check runs independently, wrapped so one failure doesn't take down the
others (log + skip that category on exception).

1. **Stale broker imports** (`import`, medium) — for each portfolio with at
   least one transaction ever, if neither `last_transaction_date` nor
   `last_booking_date` falls within the last 60 days, emit one item. Reuses
   the same date fields already computed for `GET /api/v1/portfolios/`.
2. **Data quality** (`data_quality`, high for duplicates/suspicious, medium
   for reconciliation gaps) — calls the same in-process functions behind
   `/api/v1/analytics/dq/reconciliation|duplicates|suspicious`; one summary
   item per non-empty category with a count, `link_page: "diagnostics"`.
3. **Price-update failures** (`errors`, high) — reads the most recent
   `price_update_runs` row; if `error_count > 0`, one item listing
   `error_symbols`, `link_page: "diagnostics"`.
4. **Stale research** (`errors`, low) — held assets (quantity > 0 in current
   positions) with no `research_notes` row in the last 90 days → one grouped
   item ("N holdings not re-valued in 90+ days"), `link_page: "research"`.
   Note: there is no persisted record of *failed* LLM valuation calls (only
   successful saves are stored in `research_notes`), so this checks staleness,
   not past failures — the ASML-style failure fixed earlier today isn't
   retroactively detectable here.
5. **Goals off-track** (`goals`, medium) — reuses the same in-process
   `on_track` computation behind `GET /api/v1/goals/`; one item per off-track
   goal, `link_page: "goals"`.
6. **Watchlist / price-target alerts** (`watchlist`, medium) — reuses the
   in-process logic behind `watchlist/alerts/check` and
   `research/alerts/check`; one item per crossed alert, `link_page: "watchlist"`
   or `"research"` depending on source.

### Deliberate exception: Net Worth gaps

`computeNetWorthChecklist()` (in `pfm_analytics.js`) is pure client-side JS,
already unit-tested, with **no backend equivalent by design** (per existing
CLAUDE.md note: "no new backend endpoints/schema"). Porting the same rules
into Python would mean maintaining and testing identical logic in two
languages. Instead, the frontend Action Items page makes one additional call
to `GET /api/v1/networth/` and runs the existing JS function against it,
merging the resulting items into the same displayed list. Net cost: 2 HTTP
requests instead of 1, but zero duplicated business logic.

## Frontend

- New sidebar nav entry "Action Items" (`data-page="actionitems"`, icon
  `bi-list-check`), placed directly under Dashboard.
- Page: flat list sorted by severity (high → medium → low), grouped visually
  by category heading. Each item: title, detail, severity badge, "Go to
  {page}" button (calls `navigateToPage(link_page)`), dismiss (×) button.
  Empty state: "✓ All clear" when the merged, non-dismissed list is empty.
- Dismissal: `localStorage["pfmDismissedActionItems"]` (array of ids), same
  mechanism as the existing DQ tab dismissals
  (`pfmDismissedIssues`). IDs are deterministic per entity so a dismissed
  broker/goal/alert item reappears automatically if the underlying issue
  changes (e.g. a new failing price-update run gets a new `run_id`-based id).
- Pure merge function `mergeActionItems(backendItems, netWorthChecklistItems,
  dismissedIds)` extracted for unit testing — combines both sources, applies
  dismissal filter, sorts by severity.

## Testing

- Backend: `tests/unit/test_action_items.py` — one test per check (flagged /
  not-flagged for each), plus an all-healthy → empty list case, plus a
  check-throws-but-others-still-return case.
- Frontend: `web_client/js/tests/` — `mergeActionItems` covering merge,
  dismiss filtering, and severity sort order.

## Documentation

Update `PROJECT_STATUS.md` (new version entry) and `CLAUDE.md` (new endpoint
signature under a new "Action Items API" section, nav entry, dismiss
mechanism note).
