# Spending Analytics Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Analytics" tab to the Spending page with a 12-month spend/income/net trend chart and a click-to-drill-down category breakdown chart (tree-aware, ending in a transactions modal at leaf categories).

**Architecture:** Two new read-only GET endpoints on the existing `portf_server/routers/spending.py` router (`/trend`, `/categories/breakdown`) back a relocated-and-enhanced category chart plus a new trend chart on a 4th Spending-page tab. All new frontend code lives in the existing `pfm_core.js` (API client)/`pfm_features.js` (rendering + wiring) files, following those files' established module-scope-state + destroy-and-recreate Chart.js pattern. No new tables, no changes to `/summary`, `/save`, or any existing category CRUD/reparent endpoint.

**Tech Stack:** FastAPI + Pydantic (backend), Chart.js + vanilla JS + Bootstrap 5 tabs/modals (frontend), pytest + `node --test` (tests).

**Spec:** `docs/superpowers/specs/2026-07-30-spending-analytics-tab-design.md`

## Global Constraints

- Backend endpoints are plain `def` (not `async`), matching every other blocking-FX endpoint in this router (`_fx()` runs in FastAPI's threadpool).
- EUR conversion uses today's rate via the existing `_fx(currency)` helper (module-level in `spending.py`) — same convention as `/summary`, not historical/transaction-date FX.
- `date`/`timedelta` are imported locally inside each new function body (`from datetime import date, timedelta`), matching this file's existing style in `get_spending_summary` — no module-level datetime import exists in this file today, and this plan doesn't add one.
- No new dependencies (no `python-dateutil` — month arithmetic is done with plain integer math).
- Follow black formatting (line length 88) and Google-style docstrings on every new Python function, per `CLAUDE.md`.
- After every Python change, the plan assumes `docker exec portf_backend_dev kill -HUP 1` (or an equivalent restart) is run before manual verification; after every `web_client/` change, `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`. These are noted at the point they first become relevant, not repeated every task.

---

## Task 1: Backend — `GET /api/v1/spending/trend`

**Files:**
- Modify: `portf_server/routers/spending.py` (insert after `get_spending_summary`, which ends at line 746)
- Test: `tests/unit/test_spending_api.py` (append at end of file)

**Interfaces:**
- Consumes: `db.list_spending_transactions(start_date=..., is_transfer=False) -> List[Dict]` (existing method, rows have `date`, `amount`, `currency` keys); `_fx(currency: str) -> float` (existing module-level helper in this file).
- Produces: `GET /api/v1/spending/trend?months=<int, default 12>` → JSON array of `{month: "YYYY-MM", spent_eur: float, income_eur: float, net_eur: float}`, oldest month first, zero-filled for months with no matching rows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_spending_api.py`:

```python
def _months_ago_mid_month(n: int) -> str:
    """ISO date for the 15th of the month N months before the current one
    -- day 15 avoids any days-in-month edge case, and using the current
    real month (rather than a hardcoded date) keeps the test independent
    of when the suite runs, same convention as this file's existing
    `date.today() - timedelta(days=...)` usage for /summary tests."""
    y, m = date.today().year, date.today().month
    m -= n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15).isoformat()


def test_trend_buckets_by_month_and_excludes_transfers(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    this_month = _months_ago_mid_month(0)
    last_month = _months_ago_mid_month(1)
    db.create_spending_transaction(pid, this_month, "Groceries", -30.0, category="Groceries")
    db.create_spending_transaction(pid, this_month, "Salary", 100.0, category="uncategorized")
    db.create_spending_transaction(pid, last_month, "Rent", -20.0, category="uncategorized")
    tx_transfer = db.create_spending_transaction(pid, this_month, "Xfer", -50.0)
    db.update_spending_transaction(tx_transfer, category="Transfer", is_transfer=True)

    r = client.get("/api/v1/spending/trend?months=2", headers=HEADERS)
    assert r.status_code == 200
    months = r.json()
    assert len(months) == 2
    # Oldest first.
    assert months[0]["month"] == last_month[:7]
    assert months[0]["spent_eur"] == 20.0
    assert months[0]["income_eur"] == 0.0
    assert months[0]["net_eur"] == -20.0
    assert months[1]["month"] == this_month[:7]
    assert months[1]["spent_eur"] == 30.0
    assert months[1]["income_eur"] == 100.0
    assert months[1]["net_eur"] == 70.0


def test_trend_zero_fills_months_with_no_data(tmp_path):
    client, db = _make_client(tmp_path)
    pid = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(
        pid, _months_ago_mid_month(0), "Only tx", -10.0, category="uncategorized"
    )

    r = client.get("/api/v1/spending/trend?months=3", headers=HEADERS)
    assert r.status_code == 200
    months = r.json()
    assert len(months) == 3
    assert months[0]["spent_eur"] == 0.0
    assert months[0]["income_eur"] == 0.0
    assert months[1]["spent_eur"] == 0.0
    assert months[2]["spent_eur"] == 10.0


def test_trend_defaults_to_twelve_months(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get("/api/v1/spending/trend", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k trend -v`
Expected: FAIL — `404 Not Found` (the endpoint doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

Insert into `portf_server/routers/spending.py` immediately after `get_spending_summary`'s closing (after line 746, before the `SuggestCategoriesRequest` class at line 749):

```python
class SpendingTrendMonth(BaseModel):
    month: str
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
    Zero-filled for months with no matching rows, oldest month first.
    Powers the Spending page's Analytics tab trend chart.

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    from datetime import date

    today = date.today()
    first_of_this_month = today.replace(day=1)
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k trend -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full unit suite to check for regressions**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all passing (same count as before plus 3).

- [ ] **Step 6: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add GET /api/v1/spending/trend endpoint"
```

---

## Task 2: Backend — `GET /api/v1/spending/categories/breakdown`

**Files:**
- Modify: `portf_server/routers/spending.py` (insert after `list_categories_tree`, which ends at line 674)
- Test: `tests/unit/test_spending_api.py` (append at end of file)

**Interfaces:**
- Consumes: `db.list_spending_categories_tree() -> List[Dict]` (existing method, each dict has `id`, `name`, `parent_id`, `parent_name`, `is_root`); `db.create_spending_category(name, parent_id=...)`, `db.create_portfolio(...)`, `db.create_spending_transaction(...)` (existing, used only in tests).
- Produces: `GET /api/v1/spending/categories/breakdown?parent=<str, default "Spend">&days=<int, default 30>` → `{"parent": str, "children": [{"name": str, "amount_eur": float, "has_children": bool}, ...]}` sorted by `amount_eur` descending; `400` if `parent` has no children (including an unknown `parent` name, which also has no children).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_spending_api.py`:

```python
def _make_insurance_tree(db):
    """Spend > Insurance > {Car Insurance, Home Insurance}, plus a sibling
    Spend > Groceries leaf. Returns (pid, spend_id)."""
    pid = db.create_portfolio("Example Bank", account_type="bank")
    spend_id = next(
        c["id"] for c in db.list_spending_categories_tree() if c["name"] == "Spend"
    )
    insurance_id = db.create_spending_category("Insurance", parent_id=spend_id)
    db.create_spending_category("Car Insurance", parent_id=insurance_id)
    db.create_spending_category("Home Insurance", parent_id=insurance_id)
    db.create_spending_category("Groceries", parent_id=spend_id)
    today = date.today().isoformat()
    db.create_spending_transaction(pid, today, "Car ins", -30.0, category="Car Insurance")
    db.create_spending_transaction(pid, today, "Home ins", -20.0, category="Home Insurance")
    db.create_spending_transaction(pid, today, "Food", -15.0, category="Groceries")
    return pid, spend_id


def test_breakdown_returns_immediate_children_with_subtree_totals(tmp_path):
    client, db = _make_client(tmp_path)
    _make_insurance_tree(db)

    r = client.get(
        "/api/v1/spending/categories/breakdown",
        params={"parent": "Spend", "days": 30},
        headers=HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["parent"] == "Spend"
    by_name = {c["name"]: c for c in body["children"]}
    assert by_name["Insurance"]["amount_eur"] == 50.0
    assert by_name["Insurance"]["has_children"] is True
    assert by_name["Groceries"]["amount_eur"] == 15.0
    assert by_name["Groceries"]["has_children"] is False
    # Descending by amount.
    assert [c["name"] for c in body["children"]] == ["Insurance", "Groceries"]


def test_breakdown_drills_into_child(tmp_path):
    client, db = _make_client(tmp_path)
    _make_insurance_tree(db)

    r = client.get(
        "/api/v1/spending/categories/breakdown",
        params={"parent": "Insurance", "days": 30},
        headers=HEADERS,
    )
    assert r.status_code == 200
    by_name = {c["name"]: c for c in r.json()["children"]}
    assert by_name["Car Insurance"]["amount_eur"] == 30.0
    assert by_name["Car Insurance"]["has_children"] is False
    assert by_name["Home Insurance"]["amount_eur"] == 20.0


def test_breakdown_leaf_parent_returns_400(tmp_path):
    client, db = _make_client(tmp_path)
    _make_insurance_tree(db)

    r = client.get(
        "/api/v1/spending/categories/breakdown",
        params={"parent": "Groceries", "days": 30},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_breakdown_unknown_parent_returns_400(tmp_path):
    client, _ = _make_client(tmp_path)
    r = client.get(
        "/api/v1/spending/categories/breakdown",
        params={"parent": "Nonexistent", "days": 30},
        headers=HEADERS,
    )
    assert r.status_code == 400


def test_breakdown_default_parent_is_spend(tmp_path):
    client, db = _make_client(tmp_path)
    _make_insurance_tree(db)

    r = client.get("/api/v1/spending/categories/breakdown", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["parent"] == "Spend"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k breakdown -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Implement the endpoint**

Insert into `portf_server/routers/spending.py` immediately after `list_categories_tree` (after line 674, before `reparent_category` at line 675):

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
    flag, so the caller knows whether the next click should drill further
    or show transactions. Powers the Spending page's Analytics tab
    category chart drill-down. 400 if `parent` has no children (including
    an unknown `parent` name).

    Plain ``def`` — the blocking FX lookups in ``_fx`` run in the threadpool.
    """
    from datetime import date, timedelta

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
        # own cycle check — same precautionary pattern as this file's
        # _rollup_key and database.py's get_spending_category_root.
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/unit/test_spending_api.py -k breakdown -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full unit suite to check for regressions**

Run: `UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`
Expected: all passing.

- [ ] **Step 6: Restart the backend so the two new endpoints are live**

Run: `docker exec portf_backend_dev kill -HUP 1`

- [ ] **Step 7: Commit**

```bash
git add portf_server/routers/spending.py tests/unit/test_spending_api.py
git commit -m "feat: add GET /api/v1/spending/categories/breakdown endpoint"
```

---

## Task 3: Frontend — Analytics tab HTML scaffold

**Files:**
- Modify: `web_client/index.html`

**Interfaces:**
- Produces these element ids, consumed by Tasks 4–6: `spTabBtnAnalytics`, `#spPaneAnalytics`, `#spTrendChartCanvas`, `#spCategoryBreakdownPath` (breadcrumb container), `#spCategoryChartCanvas` + `#spCategoryChartTypeToggle` + `#spCategoryChartShowAll` (relocated, same ids as today — only their parent markup moves), `#spCategoryTxModal` + `#spCategoryTxModalTitle` + `#spCategoryTxModalBody` + `#spCategoryTxModalViewLink`.

- [ ] **Step 1: Add the 4th tab button**

In `web_client/index.html`, inside `<ul class="nav nav-tabs mb-3" id="spTabs">` (line 2662), add a new `<li>` after the Rules tab button (after line 2671, before the closing `</ul>` at line 2672):

```html
                        <li class="nav-item">
                            <button type="button" class="nav-link" data-bs-toggle="tab" data-bs-target="#spPaneAnalytics" id="spTabBtnAnalytics"><i class="bi bi-graph-up me-1"></i>Analytics</button>
                        </li>
```

- [ ] **Step 2: Move the category chart card out of the Categories pane, into a new Analytics pane, and add the trend chart + breadcrumb**

In the `<div class="tab-content">` block, cut the entire category-breakdown `<div class="card mb-3">...</div>` block currently at lines 2720–2733 (the one with `id="spCategoryChartTypeToggle"`, `id="spCategoryChartShowAll"`, `id="spCategoryChartCanvas"`) out of `#spPaneCategories`, and add a new pane after `#spPaneRules` closes (after line 2762's matching `</div>`, i.e. right before the tab-content's final closing `</div>`):

```html
                        <div class="tab-pane fade" id="spPaneAnalytics">
                            <div class="card mb-3">
                                <div class="card-header fw-semibold">Monthly trend (last 12 months)</div>
                                <div class="card-body">
                                    <div style="position: relative; height: 320px;">
                                        <canvas id="spTrendChartCanvas"></canvas>
                                    </div>
                                </div>
                            </div>
                            <div class="card mb-3">
                                <div class="card-header fw-semibold d-flex align-items-center justify-content-between">
                                    <span>Spending by category</span>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-sm btn-outline-secondary" id="spCategoryChartTypeToggle">Pie chart</button>
                                        <button class="btn btn-sm btn-outline-secondary" id="spCategoryChartShowAll">Show all</button>
                                    </div>
                                </div>
                                <div class="card-body">
                                    <nav class="small mb-2" id="spCategoryBreakdownPath"></nav>
                                    <div style="position: relative; height: 320px;">
                                        <canvas id="spCategoryChartCanvas"></canvas>
                                    </div>
                                </div>
                            </div>
                        </div>
```

`#spPaneCategories` (formerly lines 2719–2761) keeps everything except the card block just moved — the "Possible duplicate categories" card and the "All categories" tree card stay exactly where they are, unchanged.

- [ ] **Step 3: Add the category-transactions modal shell**

Add near the other dynamically-filled modals (e.g. next to `#addCashModal` around line 3411), as a new top-level modal:

```html
    <div class="modal fade" id="spCategoryTxModal" tabindex="-1">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="spCategoryTxModalTitle">Transactions</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="table-responsive">
                        <table class="table table-sm mb-0">
                            <thead><tr><th>Date</th><th>Description</th><th class="text-end">Amount</th></tr></thead>
                            <tbody id="spCategoryTxModalBody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="modal-footer">
                    <a href="#" class="btn btn-outline-primary" id="spCategoryTxModalViewLink">View in Transactions tab</a>
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    </div>
```

- [ ] **Step 4: Sanity-check the markup**

Run: `grep -c 'id="spTabBtnAnalytics"\|id="spPaneAnalytics"\|id="spTrendChartCanvas"\|id="spCategoryBreakdownPath"\|id="spCategoryTxModal"' web_client/index.html`
Expected: `5` (one occurrence of each id — confirms no accidental duplication and that the cut/paste in Step 2 didn't drop anything).

- [ ] **Step 5: Commit**

```bash
git add web_client/index.html
git commit -m "feat: add Spending page Analytics tab HTML scaffold"
```

---

## Task 4: Frontend — API client methods + trend chart

**Files:**
- Modify: `web_client/js/pfm_core.js` (insert after `getSpendingCategoryTree`, ~line 1559)
- Modify: `web_client/js/pfm_features.js` (new function + tab-shown wiring)

**Interfaces:**
- Consumes: `#spTrendChartCanvas`, `spTabBtnAnalytics` (from Task 3); global `Chart` (Chart.js, already loaded); `SP_CATEGORY_CHART_COLORS` array (existing module-scope const in `pfm_features.js`, line 4645).
- Produces: `window.apiClient.getSpendingTrend(months = 12) -> Promise<Array<{month, spent_eur, income_eur, net_eur}>>`; `_renderSpTrendChart()` (module-scope function in `pfm_features.js`, no params — always fetches fresh).

- [ ] **Step 1: Add the API client method**

In `web_client/js/pfm_core.js`, insert immediately after `getSpendingCategoryTree` (after line 1559, before `reparentSpendingCategory` at line 1560):

```javascript
        async getSpendingTrend(months = 12) {
            const response = await fetch(this.baseURL + `/api/v1/spending/trend?months=${months}`, {
                headers: { 'X-API-Key': this.apiKey }
            });
            if (!response.ok) throw new Error('Failed to load spending trend');
            return response.json();
        },
```

- [ ] **Step 2: Add the trend chart render function**

In `web_client/js/pfm_features.js`, insert immediately before `let _spCategoryChartInstance = null;` (line 4642):

```javascript
let _spTrendChartInstance = null;

async function _renderSpTrendChart() {
    const canvas = document.getElementById('spTrendChartCanvas');
    if (!canvas) return;
    const months = await window.apiClient.getSpendingTrend(12);
    if (_spTrendChartInstance) {
        _spTrendChartInstance.destroy();
        _spTrendChartInstance = null;
    }
    _spTrendChartInstance = new Chart(canvas, {
        data: {
            labels: months.map(m => m.month),
            datasets: [
                {
                    type: 'bar',
                    label: 'Spent',
                    data: months.map(m => m.spent_eur),
                    backgroundColor: SP_CATEGORY_CHART_COLORS[3],
                },
                {
                    type: 'bar',
                    label: 'Income',
                    data: months.map(m => m.income_eur),
                    backgroundColor: SP_CATEGORY_CHART_COLORS[1],
                },
                {
                    type: 'line',
                    label: 'Net',
                    data: months.map(m => m.net_eur),
                    borderColor: SP_CATEGORY_CHART_COLORS[5],
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true, position: 'top' } },
            scales: { y: { ticks: { callback: v => '€' + v } } },
        },
    });
}
window._renderSpTrendChart = _renderSpTrendChart;
```

- [ ] **Step 3: Wire the trend chart to render when the Analytics tab is first shown**

In `web_client/js/pfm_features.js`, inside `loadSpendingPage()`, add alongside the existing `categoriesTabBtn`/`chartTypeBtn` wiring block (near line 4569–4588):

```javascript
    const analyticsTabBtn = document.getElementById('spTabBtnAnalytics');
    if (analyticsTabBtn && !analyticsTabBtn.dataset.wired) {
        analyticsTabBtn.dataset.wired = '1';
        analyticsTabBtn.addEventListener('shown.bs.tab', () => {
            _renderSpTrendChart();
        });
    }
```

(This is additive wiring only — Task 5 rewires what `categoriesTabBtn`'s own listener does and adds the category-chart half of the Analytics tab's `shown.bs.tab` handling; keep both listeners on their respective button ids, don't merge them yet.)

- [ ] **Step 4: Manual verification**

Run `docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`, open the Spending page in a browser, click the new Analytics tab, and confirm a bar+line chart renders with 12 months of data (or an empty/zero chart on a fresh install — not a crash).

- [ ] **Step 5: Commit**

```bash
git add web_client/js/pfm_core.js web_client/js/pfm_features.js
git commit -m "feat: add Spending Analytics tab monthly trend chart"
```

---

## Task 5: Frontend — drill-down state, breadcrumb, and pure helpers

**Files:**
- Modify: `web_client/js/pfm_core.js` (new API client method)
- Modify: `web_client/js/pfm_features.js` (new state, new functions, rewire `_refreshSpendingData` and the Categories-tab `shown.bs.tab` listener)
- Test: `web_client/js/tests/web_client.test.mjs` (append new tests)

**Interfaces:**
- Consumes: `_renderSpendingCategoryChart(byCategoryEur)` (existing, unmodified until Task 6), `getSpendingPeriodDays()` (existing).
- Produces: `window.apiClient.getSpendingCategoryBreakdown(parent, days) -> Promise<{parent, children}>`; `window._spBreakdownPath: string[]`; `window._spBreakdownChildren: Array<{name, amount_eur, has_children}>`; `_loadSpBreakdownLevel()` (async, no params, re-fetches and re-renders the chart at the current path's tail); `_renderSpBreadcrumb()`; pure helpers `_spPathAfterCrumbClick(path, depth)` and `_spFindBreakdownChild(children, name)`, both exposed on `window` for testing.

- [ ] **Step 1: Write the failing frontend tests for the pure helpers**

Append to `web_client/js/tests/web_client.test.mjs`:

```javascript
test('_spPathAfterCrumbClick: clicking the last crumb keeps the full path', () => {
    const ctx = loadAppIntoContext();
    assert.deepEqual(ctx._spPathAfterCrumbClick(['Spend', 'Insurance'], 1), ['Spend', 'Insurance']);
});

test('_spPathAfterCrumbClick: clicking an earlier crumb truncates the path', () => {
    const ctx = loadAppIntoContext();
    assert.deepEqual(ctx._spPathAfterCrumbClick(['Spend', 'Insurance', 'Car Insurance'], 0), ['Spend']);
});

test('_spFindBreakdownChild: finds a child by name', () => {
    const ctx = loadAppIntoContext();
    const children = [{ name: 'Insurance', amount_eur: 50, has_children: true }, { name: 'Groceries', amount_eur: 15, has_children: false }];
    assert.deepEqual(ctx._spFindBreakdownChild(children, 'Groceries'), { name: 'Groceries', amount_eur: 15, has_children: false });
});

test('_spFindBreakdownChild: returns undefined for an unknown name', () => {
    const ctx = loadAppIntoContext();
    const children = [{ name: 'Insurance', amount_eur: 50, has_children: true }];
    assert.equal(ctx._spFindBreakdownChild(children, 'Nonexistent'), undefined);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test web_client/js/tests/`
Expected: FAIL — `ctx._spPathAfterCrumbClick is not a function` (and similarly for `_spFindBreakdownChild`).

- [ ] **Step 3: Add the API client method**

In `web_client/js/pfm_core.js`, insert immediately after the `getSpendingTrend` method added in Task 4:

```javascript
        async getSpendingCategoryBreakdown(parent, days) {
            const response = await fetch(
                this.baseURL + `/api/v1/spending/categories/breakdown?parent=${encodeURIComponent(parent)}&days=${days}`,
                { headers: { 'X-API-Key': this.apiKey } }
            );
            if (!response.ok) {
                let detail = 'Failed to load category breakdown';
                try {
                    const body = await response.json();
                    detail = body.detail || detail;
                } catch (e) { /* response wasn't JSON, use the generic message */ }
                throw new Error(detail);
            }
            return response.json();
        },
```

- [ ] **Step 4: Add the pure helpers and the breakdown-loading/breadcrumb functions**

In `web_client/js/pfm_features.js`, insert immediately after the `_renderSpTrendChart` function added in Task 4:

```javascript
function _spPathAfterCrumbClick(path, depth) {
    return path.slice(0, depth + 1);
}
window._spPathAfterCrumbClick = _spPathAfterCrumbClick;

function _spFindBreakdownChild(children, name) {
    return (children || []).find(c => c.name === name);
}
window._spFindBreakdownChild = _spFindBreakdownChild;

async function _loadSpBreakdownLevel() {
    const path = window._spBreakdownPath || ['Spend'];
    const parent = path[path.length - 1];
    const days = getSpendingPeriodDays();
    let data;
    try {
        data = await window.apiClient.getSpendingCategoryBreakdown(parent, days);
    } catch (err) {
        window._spBreakdownChildren = [];
        _renderSpendingCategoryChart({});
        return;
    }
    window._spBreakdownChildren = data.children;
    _renderSpBreadcrumb();
    _renderSpendingCategoryChart(
        Object.fromEntries(data.children.map(c => [c.name, c.amount_eur]))
    );
}
window._loadSpBreakdownLevel = _loadSpBreakdownLevel;

function _renderSpBreadcrumb() {
    const el = document.getElementById('spCategoryBreakdownPath');
    if (!el) return;
    const path = window._spBreakdownPath || ['Spend'];
    el.innerHTML = path
        .map((name, i) => `<a href="#" data-depth="${i}">${esc(name)}</a>`)
        .join(' <span class="text-muted">&gt;</span> ');
    el.querySelectorAll('a').forEach(a => a.addEventListener('click', (e) => {
        e.preventDefault();
        window._spBreakdownPath = _spPathAfterCrumbClick(window._spBreakdownPath, Number(a.dataset.depth));
        _loadSpBreakdownLevel();
    }));
}
window._renderSpBreadcrumb = _renderSpBreadcrumb;
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test web_client/js/tests/`
Expected: all passing, including the 4 new tests.

- [ ] **Step 6: Rewire `_refreshSpendingData` to reset the breakdown path instead of rendering from `/summary`**

In `web_client/js/pfm_features.js`, in `_refreshSpendingData` (around line 4614), replace:

```javascript
        _renderSpendingCategoryChart(summary.by_category_eur || {});
```

with:

```javascript
        window._spBreakdownPath = ['Spend'];
        await _loadSpBreakdownLevel();
```

(`summary` is still destructured and still used for `spSpent`/`spIncome`/`spTransferred` a few lines above — only this one render call-site changes.)

- [ ] **Step 7: Move the Categories tab's `shown.bs.tab` re-render listener to the Analytics tab**

In `web_client/js/pfm_features.js`, replace the existing block (lines 4569–4579):

```javascript
    const categoriesTabBtn = document.getElementById('spTabBtnCategories');
    if (categoriesTabBtn && !categoriesTabBtn.dataset.wired) {
        categoriesTabBtn.dataset.wired = '1';
        // A Chart.js chart built while its canvas sits inside a
        // display:none tab-pane renders at zero size — re-render (no
        // re-fetch, the data's already in memory) once the pane is
        // actually visible and the canvas has real dimensions.
        categoriesTabBtn.addEventListener('shown.bs.tab', () => {
            _renderSpendingCategoryChart(window._spCategoryChartData || {});
        });
    }
```

with:

```javascript
    const categoriesTabBtn = document.getElementById('spTabBtnCategories');
    if (categoriesTabBtn && !categoriesTabBtn.dataset.wired) {
        categoriesTabBtn.dataset.wired = '1';
    }
```

(The chart itself no longer lives on the Categories tab, so there's nothing to re-render when that tab is shown — the `dataset.wired` guard is kept only so this block still matches the file's established "wire once" pattern for other buttons on this tab, in case something else gets attached to `categoriesTabBtn` later.) Then extend the Analytics tab wiring added in Task 4 Step 3 (around what is now a few lines later) so a tab-show re-fetches the breakdown, not just the trend chart:

```javascript
    const analyticsTabBtn = document.getElementById('spTabBtnAnalytics');
    if (analyticsTabBtn && !analyticsTabBtn.dataset.wired) {
        analyticsTabBtn.dataset.wired = '1';
        analyticsTabBtn.addEventListener('shown.bs.tab', () => {
            _renderSpTrendChart();
            _loadSpBreakdownLevel();
        });
    }
```

(This replaces the single-line body added in Task 4 Step 3 — same listener, now calling both functions.)

- [ ] **Step 8: Run the full JS test suite**

Run: `node --test web_client/js/tests/`
Expected: all passing.

- [ ] **Step 9: Manual verification**

Rebuild/redeploy web (`docker compose build web && docker stop portf_web && WEB_PORT=8080 docker compose up -d web`). Open the Spending page → Analytics tab: confirm the category chart renders from the new breakdown endpoint (same numbers as before, since the Spend-level rollup is unchanged) and a breadcrumb reading just "Spend" appears above it. Open the Categories tab: confirm no chart appears there anymore, only the tree/CRUD UI.

- [ ] **Step 10: Commit**

```bash
git add web_client/js/pfm_core.js web_client/js/pfm_features.js web_client/js/tests/web_client.test.mjs
git commit -m "feat: wire Spending Analytics category chart to the breakdown endpoint"
```

---

## Task 6: Frontend — chart click-to-drill-down and transactions modal

**Files:**
- Modify: `web_client/js/pfm_features.js`

**Interfaces:**
- Consumes: `_spFindBreakdownChild`, `window._spBreakdownPath`, `window._spBreakdownChildren`, `_loadSpBreakdownLevel` (Task 5); `window.apiClient.getSpendingTransactions(params)` (existing); `getSpendingPeriodDays()` (existing); `#spCategoryTxModal`/`#spCategoryTxModalTitle`/`#spCategoryTxModalBody`/`#spCategoryTxModalViewLink` (Task 3); `window._spCategoryFilterSelected`, `#spFromDate`, `#spToDate`, `_fetchAndRenderSpendingTable()` (existing Transactions-tab filter state/loader).
- Produces: `openSpCategoryTransactionsModal(categoryName, days)` (global function, no return value — side effect: shows the modal).

- [ ] **Step 1: Add the click handler to both chart branches in `_renderSpendingCategoryChart`**

In `web_client/js/pfm_features.js`, inside `_renderSpendingCategoryChart` (the function body starting at line 4647), immediately after the line `const chartType = window._spCategoryChartType || 'bar';` (currently right before the `if (chartType === 'pie')` split), add:

```javascript
    const handleCategoryClick = (evt, elements) => {
        if (!elements.length) return;
        const clickedName = labels[elements[0].index];
        const child = _spFindBreakdownChild(window._spBreakdownChildren, clickedName);
        if (!child) return;
        if (child.has_children) {
            window._spBreakdownPath = (window._spBreakdownPath || ['Spend']).concat([child.name]);
            _loadSpBreakdownLevel();
        } else {
            openSpCategoryTransactionsModal(child.name, getSpendingPeriodDays());
        }
    };
```

Then add `onClick: handleCategoryClick,` to the `options` object in **both** branches: the pie chart's `options` (currently `{ responsive: true, maintainAspectRatio: false, plugins: {...} }`) and the bar chart's `options` (currently `{ indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: {...}, scales: {...} }`) — one line added to each, no other changes to either config.

- [ ] **Step 2: Add the transactions modal function**

In `web_client/js/pfm_features.js`, insert after `_renderSpendingCategoryChart`'s closing brace:

```javascript
async function openSpCategoryTransactionsModal(categoryName, days) {
    const endDate = new Date().toISOString().slice(0, 10);
    const startDate = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);

    const titleEl = document.getElementById('spCategoryTxModalTitle');
    const bodyEl = document.getElementById('spCategoryTxModalBody');
    const linkEl = document.getElementById('spCategoryTxModalViewLink');
    if (titleEl) titleEl.textContent = `${categoryName} — transactions`;
    if (bodyEl) bodyEl.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-2">Loading...</td></tr>';

    new bootstrap.Modal(document.getElementById('spCategoryTxModal')).show();

    let result;
    try {
        result = await window.apiClient.getSpendingTransactions({
            categories: [categoryName],
            start_date: startDate,
            end_date: endDate,
            sort_by: 'date',
            sort_dir: 'desc',
            limit: 200,
        });
    } catch (err) {
        if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="3" class="text-center text-danger py-2">${esc(err.message)}</td></tr>`;
        return;
    }
    const rows = result.items || [];
    if (bodyEl) {
        bodyEl.innerHTML = rows.length ? rows.map(r => `
            <tr>
                <td>${Fmt.date(r.date)}</td>
                <td>${esc(r.description)}</td>
                <td class="text-end ${r.amount < 0 ? 'text-danger' : 'text-success'}">${Fmt.num(r.amount, 2, 2)} ${r.currency || ''}</td>
            </tr>`).join('') : '<tr><td colspan="3" class="text-center text-muted py-2">No transactions in this period.</td></tr>';
    }
    if (linkEl) {
        linkEl.onclick = (e) => {
            e.preventDefault();
            window._spCategoryFilterSelected = new Set([categoryName]);
            const fromEl = document.getElementById('spFromDate');
            const toEl = document.getElementById('spToDate');
            if (fromEl) fromEl.value = startDate;
            if (toEl) toEl.value = endDate;
            bootstrap.Modal.getInstance(document.getElementById('spCategoryTxModal'))?.hide();
            new window.bootstrap.Tab(document.getElementById('spTabBtnTransactions')).show();
            _fetchAndRenderSpendingTable();
        };
    }
}
window.openSpCategoryTransactionsModal = openSpCategoryTransactionsModal;
```

- [ ] **Step 3: Manual verification**

Rebuild/redeploy web. On the Spending page's Analytics tab: click a category bar/slice that has children (e.g. "Insurance") — confirm the chart re-scopes to its children and the breadcrumb grows to "Spend > Insurance". Click a leaf category (e.g. "Groceries") — confirm the transactions modal opens and lists the right rows for the current period. Click "View in Transactions tab" — confirm it switches tabs, sets the category filter to that one category, and the table shows the same rows. Click an earlier breadcrumb segment — confirm it jumps back up a level. Toggle Show-all/Bar-Pie at a drilled-in level — confirm they still work.

- [ ] **Step 4: Commit**

```bash
git add web_client/js/pfm_features.js
git commit -m "feat: add click-to-drill-down and transactions modal to Spending Analytics category chart"
```

---

## Task 7: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:** None — documentation only, no code interfaces produced or consumed.

- [ ] **Step 1: Update `CLAUDE.md`'s Spending Tracking section**

Add a new paragraph at the end of the existing "Spending Tracking" section (after the hierarchical-categories v28 paragraph), documenting: the new `GET /api/v1/spending/trend` and `GET /api/v1/spending/categories/breakdown` endpoints (signatures and response shapes, as in this plan's Tasks 1–2); the new Analytics tab (`#spTabBtnAnalytics`/`#spPaneAnalytics`) holding the trend chart and the relocated, now-clickable category chart; the drill-down mechanism (`window._spBreakdownPath`, breadcrumb, leaf categories opening `openSpCategoryTransactionsModal`); and that the Categories tab no longer shows a chart, only the tree/CRUD UI.

- [ ] **Step 2: Update `PROJECT_STATUS.md`**

Bump "Last updated" to the date this task is executed, and add a new `**Recent (v2.5.36):**` bullet (incrementing from the existing v2.5.35 entry) summarizing the feature in the same style as the existing bullets in that section — one paragraph, matching the level of detail of the v2.5.30–v2.5.35 entries already there.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md PROJECT_STATUS.md
git commit -m "docs: document Spending Analytics tab"
```
