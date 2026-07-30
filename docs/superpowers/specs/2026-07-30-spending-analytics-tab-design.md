# Spending: Analytics tab (trend chart + category drill-down)

**Date:** 2026-07-30
**Status:** Approved

## Problem

The Spending page's Categories tab shows a single category-breakdown chart
(top 8 by period, bar/pie toggle) with no way to see spending trend over
time, and no way to drill from a category bar into the actual transactions
behind it — you have to switch to the Transactions tab and manually apply
filters. There's also no rollup-aware drill-down: since v28 introduced a
category tree (Income/Spend roots with nested groups), the chart already
rolls every leaf up to its top-level Spend group, but there's no way to
"zoom in" on one group to see its own children, or further down to actual
transactions.

## Scope

**A) New "Analytics" tab** on the Spending page (`#spTabs`), alongside the
existing Transactions/Categories/Rules tabs. Holds two charts:
- A new **monthly trend chart** (spent/income/net, last 12 calendar months,
  fixed window — independent of the page's day-based period selector).
- The **existing category-breakdown chart**, relocated here from the
  Categories tab (same rendering code, same Show-all/Bar-Pie toggles,
  still driven by the shared `pfmSpendingSummaryDays` period selector), now
  with click-to-drill-down.

The Categories tab keeps only its tree/CRUD UI (rename-in-place, reparent,
add) — the chart and its toggle buttons move out entirely, not duplicated.

**B) Drill-down through the category tree.** Clicking a bar/slice for a
category that has sub-categories re-scopes the same chart to show that
category's children (with a breadcrumb showing the path, each segment
clickable to jump back up). Clicking a bar/slice for a leaf category (no
children) opens a modal listing that leaf's transactions for the current
period, with a link to open the same filter in the full Transactions tab.

**C) Two new read-only endpoints**, both under `/api/v1/spending/`:
- `GET /trend?months=12` — last N calendar months of spent/income/net,
  EUR-converted at today's rate (same convention as `/summary` — not
  historical FX), transfers excluded, zero-filled for months with no
  activity.
- `GET /categories/breakdown?parent=Spend&days=30` — a tree node's
  immediate children, each with its **subtree-summed** amount (includes
  all descendants, not just direct transactions) and a `has_children`
  flag, so the frontend knows whether the next click should drill further
  or open the transactions modal. 400 if `parent` has no children.

No changes to `/summary`, `/save`, `/upload`, transfer matching, or any
existing category CRUD/reparent behavior — this is purely additive
read/display surface on top of the v28 tree.

**Not doing:** budgets or budget-vs-actual (separate, not requested here);
historical (month-of-transaction) FX conversion for the trend chart
(matches `/summary`'s existing current-rate convention — changing that
convention is out of scope); an Income-side breakdown chart (the existing
chart, and this one, stay Spend-only, matching today's behavior); a
dedicated "all money movements" ledger (separate ask); drag-to-zoom or any
interaction beyond click (keeps parity with the existing chart's
click-free design, minus the one new click handler).

## Design

### A) Backend: `GET /trend`

`portf_server/routers/spending.py`, placed after `get_spending_summary`
(~line 746):

```python
class SpendingTrendMonth(BaseModel):
    month: str  # "YYYY-MM"
    spent_eur: float
    income_eur: float
    net_eur: float


@router.get("/trend", response_model=List[SpendingTrendMonth])
def get_spending_trend(
    months: int = 12,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Monthly spent/income/net for the last N calendar months, EUR-converted
    at today's rate (same convention as /summary). Transfers excluded.
    Zero-filled for months with no matching rows. Plain `def` — blocking
    FX lookups in `_fx` run in the threadpool.
    """
    today = date.today()
    first_of_this_month = today.replace(day=1)
    # Walk back `months - 1` calendar months without a date-math dependency.
    start_year = first_of_this_month.year
    start_month_num = first_of_this_month.month - (months - 1)
    while start_month_num <= 0:
        start_month_num += 12
        start_year -= 1
    start_date = date(start_year, start_month_num, 1).isoformat()

    rows = db.list_spending_transactions(start_date=start_date, is_transfer=False)
    buckets: dict = {}
    for r in rows:
        key = r["date"][:7]
        amt_eur = float(r["amount"]) * _fx(r.get("currency", "EUR"))
        b = buckets.setdefault(key, {"spent": 0.0, "income": 0.0})
        if amt_eur < 0:
            b["spent"] += abs(amt_eur)
        else:
            b["income"] += amt_eur

    result = []
    y, m = start_year, start_month_num
    for _ in range(months):
        key = f"{y:04d}-{m:02d}"
        b = buckets.get(key, {"spent": 0.0, "income": 0.0})
        result.append(
            SpendingTrendMonth(
                month=key,
                spent_eur=round(b["spent"], 2),
                income_eur=round(b["income"], 2),
                net_eur=round(b["income"] - b["spent"], 2),
            )
        )
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result
```

(Manual month-walk instead of adding `python-dateutil` — the project has
no existing dependency on it, and the arithmetic is a handful of lines.)

### B) Backend: `GET /categories/breakdown`

Placed right after `list_categories_tree` (~line 674 in the existing
file):

```python
class SpendingCategoryBreakdownChild(BaseModel):
    name: str
    amount_eur: float
    has_children: bool


class SpendingCategoryBreakdownResponse(BaseModel):
    parent: str
    children: List[SpendingCategoryBreakdownChild]


@router.get(
    "/categories/breakdown", response_model=SpendingCategoryBreakdownResponse
)
def get_spending_category_breakdown(
    parent: str = "Spend",
    days: int = 30,
    db=Depends(get_database),
    api_key_info: dict = Depends(_auth),
):
    """Immediate children of a category tree node, each with its full
    subtree total for the period (EUR, today's rate) and a has_children
    flag so the caller knows whether to drill further or show transactions.
    Plain `def` — blocking FX lookups in `_fx` run in the threadpool.
    """
    tree = db.list_spending_categories_tree()
    children_by_parent: dict = {}
    for c in tree:
        children_by_parent.setdefault(c["parent_name"], []).append(c)

    direct_children = children_by_parent.get(parent, [])
    if not direct_children:
        raise HTTPException(
            status_code=400, detail=f"'{parent}' has no sub-categories"
        )

    def _subtree_names(name: str, _depth: int = 0) -> List[str]:
        # Defensive depth guard against a cycle slipping through reparent's
        # own cycle check — same precautionary pattern as _rollup_key and
        # get_spending_category_root elsewhere in this file/module.
        if _depth > 100:
            return [name]
        names = [name]
        for child in children_by_parent.get(name, []):
            names.extend(_subtree_names(child["name"], _depth + 1))
        return names

    start_date = (date.today() - timedelta(days=days)).isoformat()
    rows = db.list_spending_transactions(start_date=start_date, is_transfer=False)

    result = []
    for child in direct_children:
        names = set(_subtree_names(child["name"]))
        amount_eur = sum(
            abs(float(r["amount"]) * _fx(r.get("currency", "EUR")))
            for r in rows
            if r["category"] in names
        )
        result.append(
            SpendingCategoryBreakdownChild(
                name=child["name"],
                amount_eur=round(amount_eur, 2),
                has_children=bool(children_by_parent.get(child["name"])),
            )
        )
    result.sort(key=lambda c: -c.amount_eur)
    return SpendingCategoryBreakdownResponse(parent=parent, children=result)
```

(`amount_eur` sums `abs()` unconditionally rather than branching on sign —
a v28-valid tree guarantees every transaction under the `Spend` subtree is
already negative, so this is equivalent to the existing chart's spend-only
total without re-deriving the sign check. `is_transfer=False` passed
directly to `list_spending_transactions`, reusing the existing filter
param instead of re-checking it per row like `/summary` and `/trend` do —
consistent with how `/` already uses this same param.)

### C) Frontend: tab wiring

`web_client/index.html` — new 4th tab following the existing pattern
(`#spTabs` ~line 2662, panes ~2674/2719/2762):

```html
<button class="nav-link" id="spTabBtnAnalytics" data-bs-toggle="tab"
        data-bs-target="#spPaneAnalytics" type="button">Analytics</button>
```
```html
<div class="tab-pane fade" id="spPaneAnalytics">
  <!-- trend chart canvas + category breakdown chart canvas + breadcrumb -->
</div>
```

The category chart's canvas, its two toggle buttons
(`spCategoryChartShowAll`, `spCategoryChartTypeToggle`), and its container
move out of `#spPaneCategories` into `#spPaneAnalytics`. A new breadcrumb
element (`#spCategoryBreakdownPath`) sits above it.

### D) Frontend: trend chart

`web_client/js/pfm_features.js`, new function alongside
`_renderSpendingCategoryChart`:

```javascript
let _spTrendChartInstance = null;

async function _renderSpTrendChart() {
    const canvas = document.getElementById('spTrendChart');
    if (!canvas) return;
    const months = await window.apiClient.getSpendingTrend(12);
    if (_spTrendChartInstance) _spTrendChartInstance.destroy();
    _spTrendChartInstance = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: months.map(m => m.month),
            datasets: [
                { label: 'Spent', data: months.map(m => m.spent_eur), backgroundColor: SP_CATEGORY_CHART_COLORS[3] },
                { label: 'Income', data: months.map(m => m.income_eur), backgroundColor: SP_CATEGORY_CHART_COLORS[1] },
                { label: 'Net', data: months.map(m => m.net_eur), type: 'line', yAxisID: 'y' },
            ],
        },
        options: { responsive: true, maintainAspectRatio: false },
    });
}
```

Fetched/rendered once when the Analytics tab is first shown (`shown.bs.tab`
listener on `spTabBtnAnalytics`, same pattern already used for
`spTabBtnCategories` — Chart.js can't size a canvas inside a `display:none`
tab-pane), and re-rendered on every subsequent show (cheap, always
current — no caching needed since the trend window is fixed).

`getSpendingTrend(months)` added to `pfm_core.js`'s API client, following
the existing fetch-with-`X-API-Key` pattern used by
`getSpendingCategoryTree`.

### E) Frontend: category chart drill-down

State: `window._spBreakdownPath = ['Spend']` (array acting as a stack),
reset to `['Spend']` whenever the Analytics tab data is refreshed (period
change, tab re-entry).

```javascript
async function _loadSpBreakdownLevel() {
    const parent = window._spBreakdownPath[window._spBreakdownPath.length - 1];
    const days = getSpendingPeriodDays();
    const data = await window.apiClient.getSpendingCategoryBreakdown(parent, days);
    window._spBreakdownChildren = data.children;  // keep has_children per bar
    _renderSpBreadcrumb();
    _renderSpendingCategoryChart(
        Object.fromEntries(data.children.map(c => [c.name, c.amount_eur]))
    );
}

function _renderSpBreadcrumb() {
    const el = document.getElementById('spCategoryBreakdownPath');
    if (!el) return;
    el.innerHTML = window._spBreakdownPath
        .map((name, i) => `<a href="#" data-depth="${i}">${esc(name)}</a>`)
        .join(' <span class="text-muted">&gt;</span> ');
    el.querySelectorAll('a').forEach(a => a.addEventListener('click', (e) => {
        e.preventDefault();
        window._spBreakdownPath = window._spBreakdownPath.slice(0, Number(a.dataset.depth) + 1);
        _loadSpBreakdownLevel();
    }));
}
```

**Wiring changes to existing code** (`pfm_features.js`):

- `_refreshSpendingData` (~line 4614) currently calls
  `_renderSpendingCategoryChart(summary.by_category_eur || {})` directly.
  That line is replaced with `window._spBreakdownPath = ['Spend']; await
  _loadSpBreakdownLevel();` — the chart's data now comes from
  `/categories/breakdown`, not `summary.by_category_eur`. The summary
  fields (`spSpent`/`spIncome`/`spTransferred`) and the Dashboard's own
  `by_category_eur` usage (`renderDashboardTopCategories`) are untouched —
  both keep reading `/summary` as today.
- The `shown.bs.tab` listener currently on `spTabBtnCategories` (~line
  4576, re-renders from cached data when the tab becomes visible since
  Chart.js can't size a canvas in a `display:none` pane) moves to the new
  `spTabBtnAnalytics`, calling `_loadSpBreakdownLevel()` instead of
  `_renderSpendingCategoryChart(window._spCategoryChartData || {})` — it
  needs the `has_children` metadata refetched, not just a re-render, in
  case the tree changed since the tab was last shown (e.g. a category was
  reparented on the Categories tab in the same session).
- The Show-all toggle (`spCategoryChartShowAll`, ~line 4560) and Bar/Pie
  toggle (`spCategoryChartTypeToggle`, ~line 4580) keep calling
  `_renderSpendingCategoryChart(window._spCategoryChartData || {})`
  unchanged — no refetch needed, since `window._spCategoryChartData` is
  still populated by every `_renderSpendingCategoryChart` call regardless
  of which endpoint supplied the data.
- The period selector's change handler (whatever currently triggers
  `_refreshSpendingData()` on a new `pfmSpendingSummaryDays` value) is
  unaffected — `_refreshSpendingData` already resets `_spBreakdownPath` to
  `['Spend']` per the point above, so changing the period always returns
  the drill-down to the top level rather than re-querying a stale nested
  parent at the new period.

`_renderSpendingCategoryChart`'s Chart.js config gains one shared `onClick`
handler, added to `options` in **both** the pie and bar branches (~line
4664 and ~line 4705 respectively — identical handler, since both branches
already build the same `labels` array from the same `entries`):

```javascript
const handleCategoryClick = (evt, elements) => {
    if (!elements.length) return;
    const clickedName = labels[elements[0].index];
    const child = (window._spBreakdownChildren || []).find(c => c.name === clickedName);
    if (!child) return;
    if (child.has_children) {
        window._spBreakdownPath.push(child.name);
        _loadSpBreakdownLevel();
    } else {
        openSpCategoryTransactionsModal(child.name, getSpendingPeriodDays());
    }
};
```

placed just before the `if (chartType === 'pie') { ... } else { ... }`
split (already in scope of `labels`), with `onClick: handleCategoryClick`
added to both Chart configs' top-level `options`.

`openSpCategoryTransactionsModal(categoryName, days)` — new function,
Bootstrap modal following the project's existing modal patterns (e.g.
`#addCashModal`): fetches
`GET /api/v1/spending/?categories=<name>&start_date=...&end_date=...&sort_by=date&sort_dir=desc`
(existing endpoint, existing `categories` repeatable param — no backend
change), renders a plain table (date/description/amount), and a "View in
Transactions tab" link that sets the Transactions tab's category filter to
`categoryName` + the same date range, switches tabs
(`spTabBtnTransactions.click()` or equivalent), and re-runs its existing
load.

The Show-all and Bar/Pie toggles are unaffected — they still act on
whatever's currently in `window._spCategoryChartData` (now the current
breakdown level's children, not always the top-level rollup).

### F) `pfm_core.js` API client additions

```javascript
async getSpendingTrend(months = 12) {
    const response = await fetch(this.baseURL + `/api/v1/spending/trend?months=${months}`, {
        headers: { 'X-API-Key': this.apiKey }
    });
    if (!response.ok) throw new Error('Failed to load spending trend');
    return response.json();
},
async getSpendingCategoryBreakdown(parent, days) {
    const response = await fetch(
        this.baseURL + `/api/v1/spending/categories/breakdown?parent=${encodeURIComponent(parent)}&days=${days}`,
        { headers: { 'X-API-Key': this.apiKey } }
    );
    if (!response.ok) {
        let detail = 'Failed to load category breakdown';
        try { detail = (await response.json()).detail || detail; } catch (e) { /* not JSON */ }
        throw new Error(detail);
    }
    return response.json();
},
```

### Error handling

- `/categories/breakdown` on a leaf `parent` → 400, surfaced to the user
  only if triggered directly (shouldn't happen from the UI, since clicks
  are gated on `has_children`) — a defensive guard, not a normal path.
- Empty tree state (no Spend children at all, e.g. a brand-new install
  with zero categorized spend transactions): `/categories/breakdown`
  itself 400s on `parent=Spend` with no children — the Analytics tab
  catches this and shows the existing chart's established empty-state
  message instead of an error.
- `/trend` with zero transactions in the entire window: returns 12
  zero-filled months, not an error — same "always render, never blank
  crash" convention as the category chart.

### Testing

- **Backend** (`tests/unit/test_spending_api.py`): `/trend` — correct
  month bucketing across a year boundary, EUR conversion, transfer
  exclusion, zero-filled months with no data, `months` param respected.
  `/categories/breakdown` — two-level subtree summation (a grandchild's
  amount rolls into its parent's total), `has_children` correctly true/false,
  leaf `parent` → 400, unknown `parent` name → 400 (falls out of the "no
  children" branch, same status), sorting descending by amount.
- **Frontend** (`web_client/js/tests/`): pure helpers only —
  `_renderSpBreadcrumb`'s path-slicing logic (extract as a small pure
  function if not already trivial inline) and any label→child lookup
  logic used by `onClick`. Chart.js rendering and DOM wiring itself follow
  this file's established precedent of manual verification instead of
  automated tests (see the hierarchical-categories spec's testing section
  for the same convention) — manually verify: Analytics tab renders both
  charts on first show; clicking a top-level group with children re-scopes
  the chart and updates the breadcrumb; clicking a leaf opens the
  transactions modal with the right rows; breadcrumb "back" click restores
  the parent level; Show-all/Bar-Pie toggles keep working at a drilled-in
  level; Categories tab no longer shows a chart.
