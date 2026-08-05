# Dashboard Alert Grouping + Action Items Research Link

**Date:** 2026-08-05
**Status:** Approved

## Problem

Two small UX gaps surfaced immediately after using the new bulk research refresh
(see `2026-08-04-bulk-research-refresh-design.md`), which populated buy/sell
targets for nearly every holding and made both pre-existing surfaces below
noticeably worse:

1. **Dashboard alert banner is one flat, uncapped list.** `loadDashboardAlerts()`
   (`web_client/js/pfm_core.js:290-374`) renders every triggered BUY/SELL/WATCH
   alert as a `<li>` in a single list inside one alert box. With targets now set
   on most holdings, this list is long (12 BUY triggers as of writing) and only
   grows as more thresholds get crossed over time.
2. **Action Items "Go to page" loses context.** `check_price_alerts` in
   `portf_manager/services/action_items.py:257-275` already attaches
   `context: {"symbol": a["symbol"]}` to every price-target-crossing item, but
   the "Go to page" link (`web_client/js/pfm_features.js:350`) only reads
   `item.link_page` — for `link_page: "research"` items it always lands on a
   blank Research Workbench instead of loading that symbol's report.

## Scope

Frontend-only. No backend changes — both fixes work with data the API already
returns.

## Fix 1: Collapsible alert sections, grouped by type

Replace the single flat `<ul>` in `loadDashboardAlerts()` with three
independently-collapsible sections — **BUY**, **SELL**, **WATCH** — each headed
by a Bootstrap `data-bs-toggle="collapse"` row showing a count (e.g.
`BUY (12)`), same collapse pattern already used for the Portfolio Health panel
(`index.html` `#portfolioHealthPanel`/`#portfolioHealthBody`). All three
sections start **collapsed by default**. A section with zero items is omitted
entirely (no empty "SELL (0)" header). The stale-price DATA warning is
unaffected — it stays as its own single top-line item, not one of the
collapsible groups (it's already one aggregated message, not a per-symbol
list).

Each BUY/SELL/WATCH row keeps its existing content (symbol, name, price vs.
threshold, position info) and gains a small "research" icon-link that calls
the existing `openResearchModal(symbol, name)` (already shows cached
recommendation/confidence/summary/rationale — no new backend call needed,
since the bulk refresh already populated `research_notes`/`research_reports`
for these symbols).

Dismiss-by-content-signature behavior (`localStorage['pfmAlertsDismissed']`,
keyed by a hash of the rendered content) is unchanged — it still dismisses the
whole banner, re-appearing only when the alert set actually changes.

## Fix 2: Action Items "Go to page" loads the symbol into the Workbench

In `_renderActionItems` / the "Go to page" click handler (`pfm_features.js`),
when the clicked item's `link_page === 'research'` and `item.context.symbol`
is present: after navigating to the Research page, switch to the Workbench tab
and call `load(symbol)` — the exact same three-step sequence the Compare
table's row-click already does (`pfm_features.js:4005-4008`:
`page.querySelector('#researchTabs [data-rtab="workbench"]').click();
$('researchTicker').value = tr.dataset.sym; load(tr.dataset.sym);`).

Scoped to `link_page: "research"` items only — the watchlist-linked items
(`link_page: "watchlist"`) weren't reported as a problem and are left
unchanged.

## Testing

No backend changes, so no Python tests. No new pure functions are introduced
(this is DOM rendering + a click handler, consistent with how
`loadDashboardAlerts` itself isn't unit tested today) — verified by running
the existing `make test-js` suite (must stay green, no regressions) and manual
verification in the browser: click a BUY alert's research icon → modal shows
that symbol's cached report; click "Go to page" on a research-linked Action
Item → lands on Workbench with that symbol already loaded.

## Deploy

`web_client/` only — `docker compose build web && docker stop portf_web &&
WEB_PORT=8080 docker compose up -d web`.
