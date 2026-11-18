"""Browser smoke test: the real web client against the real API.

Starts the FastAPI app on a throwaway database with ``web_client/`` mounted on
the same origin, then drives Chromium through every sidebar page and fails on
any uncaught JS error or 5xx API response. CDN assets (Bootstrap, Chart.js,
marked, fonts) are replaced by no-op stubs, so the test needs no network and
checks our own scripts rather than third-party rendering.

Run with: uv run pytest tests/e2e
"""

import multiprocessing
import os
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

WEB_CLIENT = Path(__file__).resolve().parents[2] / "web_client"
API_KEY = "e2e-test-key-0123456789abcdef0123"

# Stands in for window.Chart / bootstrap / marked: any property, call or `new`
# returns another stub, and it stringifies to "" (e.g. marked.parse into HTML).
VENDOR_STUB = """
(() => {
  const stub = () => new Proxy(function () {}, {
    get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === "then" ? undefined : stub(),
    apply: () => stub(),
    construct: () => stub(),
    set: () => true,
  });
  window.Chart = stub();
  window.bootstrap = stub();
  window.marked = stub();
})();
"""


def _serve(port: int, db_path: str) -> None:
    """Run the API plus the static web client (in a spawned process)."""
    os.environ["PORTF_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SERVER_API_KEY"] = API_KEY
    import uvicorn
    from fastapi.staticfiles import StaticFiles

    from portf_server.app import app

    app.mount("/", StaticFiles(directory=str(WEB_CLIENT), html=True), name="web")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    db_path = tmp_path_factory.mktemp("e2e") / "web.db"
    # spawn: a forked child would inherit already-imported settings
    proc = multiprocessing.get_context("spawn").Process(
        target=_serve, args=(port, str(db_path))
    )
    proc.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.RequestError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("e2e server did not start")
    yield url
    proc.terminate()
    proc.join(5)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        # Prefer the preinstalled Chromium when Playwright's own build is absent
        exe = "/opt/pw-browsers/chromium"
        kwargs = {"executable_path": exe} if os.path.exists(exe) else {}
        try:
            b = p.chromium.launch(**kwargs)
        except Exception as e:
            pytest.skip(f"Chromium unavailable: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser, server_url):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_init_script(f"localStorage.setItem('apiKey', '{API_KEY}');")
    page = context.new_page()

    def route_external(route):
        url = route.request.url
        if url.startswith(server_url):
            route.continue_()
        elif url.endswith(".js"):
            route.fulfill(content_type="application/javascript", body=VENDOR_STUB)
        elif ".css" in url:
            route.fulfill(content_type="text/css", body="")
        else:
            route.fulfill(status=204, body="")

    page.route("**/*", route_external)
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(f"JS error: {e}"))
    page.on(
        "response",
        lambda r: r.status >= 500
        and "/api/" in r.url
        and page.errors.append(f"{r.status} {r.url}"),
    )
    yield page
    context.close()


def test_every_page_renders_without_errors(page, server_url):
    page.goto(f"{server_url}/index.html")
    page.wait_for_load_state("networkidle")
    assert page.locator("#appShell").is_visible()

    nav = page.locator("#appShell .sidebar-nav-link[data-page]")
    pages = sorted(set(nav.evaluate_all("els => els.map(e => e.dataset.page)")))
    assert "dashboard" in pages and len(pages) > 10

    for name in pages:
        # Groups start collapsed (collapse is stubbed), so fire the click directly
        link = page.locator(f"#appShell .sidebar-nav-link[data-page='{name}']")
        link.first.dispatch_event("click")
        # The load state is already "networkidle" from the first load, so give
        # the page's own fetches and timers a moment to start and finish.
        page.wait_for_timeout(300)
        page.wait_for_load_state("networkidle")
        assert page.errors == [], f"on page '{name}'"


def test_logs_tab_loads_from_the_real_api(page, server_url):
    page.goto(f"{server_url}/index.html")
    page.wait_for_load_state("networkidle")
    page.locator(
        "#appShell .sidebar-nav-link[data-page='diagnostics']"
    ).first.dispatch_event("click")
    # Tab switching is stubbed, so load the pane directly (same call the tab makes)
    page.evaluate("loadLogsTab()")
    page.wait_for_function(
        "!document.getElementById('diagLogBody').textContent.includes('Loading')"
    )
    text = page.locator("#diagLogBody").inner_text()
    assert "Could not load" not in text
    assert page.errors == []


def test_bad_api_key_keeps_app_hidden(browser, server_url):
    context = browser.new_context()
    context.add_init_script("localStorage.setItem('apiKey', 'wrong-key');")
    page = context.new_page()
    page.route(
        "**/*",
        lambda r: (
            r.continue_()
            if r.request.url.startswith(server_url)
            else r.fulfill(content_type="application/javascript", body=VENDOR_STUB)
        ),
    )
    page.goto(f"{server_url}/index.html")
    page.wait_for_load_state("networkidle")
    assert not page.locator("#appShell").is_visible()
    context.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_goals_render_on_their_page_and_the_dashboard(page, server_url):
    """A stored goal shows up as a card, in the summary strip and on the dashboard."""
    headers = {"X-API-Key": API_KEY}
    created = []
    for body in (
        {
            "name": "Example Emergency Fund",
            "target_amount_eur": 1_000_000,
            "target_date": "2032-01-01",
            "monthly_contribution_eur": 100,
            "expected_return_pct": 2,
            "notes": "Twelve months of expenses",
        },
        {
            "name": "Example Old Goal",
            "target_amount_eur": 1_000_000,
            "target_date": "2020-01-01",
            "monthly_contribution_eur": 100,
            "expected_return_pct": 2,
        },
    ):
        resp = httpx.post(f"{server_url}/api/v1/goals/", json=body, headers=headers)
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["id"])
    try:
        page.goto(f"{server_url}/index.html")
        page.wait_for_load_state("networkidle")
        page.locator(
            "#appShell .sidebar-nav-link[data-page='goals']"
        ).first.dispatch_event("click")
        page.wait_for_function(
            "document.querySelectorAll('#goalsList .card').length === 2"
        )
        cards = page.locator("#goalsList").inner_text()
        assert "Example Emergency Fund" in cards
        assert "Twelve months of expenses" in cards
        assert "Off track" in cards
        assert "Overdue" in cards
        # a goal created today has a baseline but no pace yet
        assert "Pace shows after the first month" in cards
        strip = page.locator("#goalsSummary").inner_text()
        assert "2 goals" in strip
        assert "1 off track" in strip and "1 overdue" in strip
        # the Ask AI message carries the goal's numbers
        msg = page.evaluate(
            f"goalChatMessage(window._goalsCache.find(g => g.id === {created[0]}))"
        )
        assert 'savings goal "Example Emergency Fund"' in msg
        assert "Notes: Twelve months of expenses" in msg

        page.locator(
            "#appShell .sidebar-nav-link[data-page='dashboard']"
        ).first.dispatch_event("click")
        page.wait_for_function(
            "document.getElementById('dashGoalsCard').style.display === ''"
        )
        dash = page.locator("#dashGoalsArea").inner_text()
        assert "Example Emergency Fund" in dash and "Example Old Goal" in dash
        assert "1 off track" in page.locator("#dashGoalsSummary").inner_text()
        assert page.errors == []
    finally:
        for gid in created:
            httpx.delete(f"{server_url}/api/v1/goals/{gid}", headers=headers)


def _month_back(today, k: int):
    """First day of the month ``k`` months before ``today``."""
    from datetime import date

    y, m = today.year, today.month - k
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def test_cost_control_views_render_against_real_data(page, server_url):
    """Forecast tab, comparison, unusual charges and tiers load without errors."""
    from datetime import date, timedelta

    headers = {"X-API-Key": API_KEY}
    today = date.today()
    pf = httpx.post(
        f"{server_url}/api/v1/portfolios/",
        json={"name": "Example Bank", "base_currency": "EUR", "account_type": "bank"},
        headers=headers,
    )
    assert pf.status_code in (200, 201), pf.text
    pid = pf.json()["id"]
    for cat in ("Groceries", "Insurance"):
        c = httpx.post(
            f"{server_url}/api/v1/spending/categories",
            json={"name": cat, "parent_name": "Spend"},
            headers=headers,
        )
        assert c.status_code in (201, 409), c.text
    rows = []
    # four complete months of invented, round-numbered rows
    for k in range(1, 5):
        first = _month_back(today, k)
        d = lambda day: first.replace(day=day).isoformat()  # noqa: E731
        rows += [
            {
                "date": d(1),
                "description": "EXAMPLE EMPLOYER",
                "amount": 3000,
                "category": "Salary",
                "balance": 5000.0,
            },
            {
                "date": d(5),
                "description": "EXAMPLE GROCER",
                "amount": -400,
                "category": "Groceries",
                "merchant": "EXAMPLE GROCER",
            },
            {
                "date": d(10),
                "description": "EXAMPLE LANDLORD",
                "amount": -900,
                "category": "uncategorized",
                "merchant": "EXAMPLE LANDLORD",
            },
        ]
    saved = httpx.post(
        f"{server_url}/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": rows, "duplicate_action": "add"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    budget = httpx.post(
        f"{server_url}/api/v1/budgets/",
        json={"name": "Example Budget", "is_active": True},
        headers=headers,
    )
    assert budget.status_code in (200, 201), budget.text
    bid = budget.json()["id"]
    # a monthly line and a yearly line (the amount is the YEARLY figure)
    for line in (
        {"line_type": "spending", "ref_key": "Groceries", "monthly_amount": 450},
        {
            "line_type": "spending",
            "ref_key": "Insurance",
            "monthly_amount": 1200,
            "period": "yearly",
        },
    ):
        r = httpx.post(
            f"{server_url}/api/v1/budgets/{bid}/lines", json=line, headers=headers
        )
        assert r.status_code in (200, 201), r.text
    planned = httpx.post(
        f"{server_url}/api/v1/spending/planned",
        json={
            "date": (today.replace(day=1) + timedelta(days=75)).isoformat(),
            "description": "Example trip",
            "amount": -800,
            "kind": "spending",
        },
        headers=headers,
    )
    assert planned.status_code == 201, planned.text
    try:
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(f"{server_url}/index.html")
        page.wait_for_load_state("networkidle")

        def no_overflow(where):
            overflow = page.evaluate(
                "document.documentElement.scrollWidth"
                " - document.documentElement.clientWidth"
            )
            assert (
                overflow <= 0
            ), f"horizontal overflow of {overflow}px at 390px: {where}"

        # Budget page, then the forecast tab (tab events are stubbed: call the loader)
        page.locator(
            "#appShell .sidebar-nav-link[data-page='budget']"
        ).first.dispatch_event("click")
        page.wait_for_function(
            "document.querySelectorAll('#bgVarianceArea .card').length > 0"
        )
        page.evaluate("loadCashflowForecast()")
        page.wait_for_function(
            "document.getElementById('bgFcStatus').textContent.startsWith('As of')"
        )
        assert "This month" in page.locator("#bgFcThisMonth").inner_text()
        assert page.locator("#bgFcTableBody tr").count() == 12
        assert page.locator("#bgFcAssumptions li").count() > 0
        # planned item listed; what-if answers on one line and saves nothing
        assert "Example trip" in page.locator("#bgPlanBody").inner_text()
        page.fill("#bgWiAmount", "5000")
        page.fill("#bgWiMonth", (today + timedelta(days=40)).strftime("%Y-%m"))
        page.click("#bgWhatIfForm button[type=submit]")
        page.wait_for_function(
            "document.getElementById('bgWiResult').textContent.includes('Nothing is saved')"
            " || document.getElementById('bgWiResult').textContent.includes('Lowest balance')"
        )
        assert "Lowest balance" in page.locator("#bgWiResult").inner_text()
        assert (
            len(
                httpx.get(
                    f"{server_url}/api/v1/spending/planned", headers=headers
                ).json()
            )
            == 1
        )
        # a planned item can be added through the form
        page.fill("#bgPlanDate", (today + timedelta(days=50)).isoformat())
        page.fill("#bgPlanDesc", "Example bonus")
        page.fill("#bgPlanAmount", "300")
        page.select_option("#bgPlanKind", "income")
        page.click("#bgPlanSubmit")
        page.wait_for_function(
            "document.getElementById('bgPlanBody').textContent.includes('Example bonus')"
        )
        assert page.locator("#bgFcTaxBody tr").count() >= 1
        no_overflow("forecast tab")
        # the seed dialog shows the suggested period, the reason and a period toggle
        page.evaluate("_bgLoadSeedProposals()")
        page.wait_for_function(
            "document.querySelectorAll('#bgSeedRows .bg-seed-check').length > 0"
        )
        assert page.locator("#bgSeedRows .bg-seed-period").count() >= 1
        assert "Suggested:" in page.locator("#bgSeedRows").inner_text()
        no_overflow("seed dialog")

        # Spending analytics cards
        page.locator(
            "#appShell .sidebar-nav-link[data-page='spending']"
        ).first.dispatch_event("click")
        page.wait_for_load_state("networkidle")
        page.evaluate("loadSpCostControl()")
        page.wait_for_function(
            "!document.getElementById('spCmpBody').textContent.includes('Loading')"
            " && !document.getElementById('spAnomBody').textContent.includes('Loading')"
        )
        cmp_text = page.locator("#spCmpBody").inner_text()
        assert "Groceries" in cmp_text
        assert "Failed" not in cmp_text
        # the year card and the queue
        page.wait_for_function(
            "!document.getElementById('spYearBody').textContent.includes('Loading')"
        )
        assert "Failed" not in page.locator("#spYearBody").inner_text()
        assert "Spending by month" in page.locator("#spYearBody").inner_text()
        page.wait_for_function(
            "!document.getElementById('spQueueBody').textContent.includes('Loading')"
        )
        assert "EXAMPLE LANDLORD" in page.locator("#spQueueBody").inner_text()
        page.fill("#spQueueBody .sp-queue-cat", "Insurance")
        page.click("#spQueueBody .sp-queue-apply")
        page.wait_for_function(
            "document.getElementById('spQueueBody').textContent.includes('Nothing is waiting')"
        )
        no_overflow("spending page")
        # the yearly line is labelled as such on the Overview, with its fund
        overview = page.locator("#bgVarianceArea").inner_text()
        assert "yearly" in overview
        assert "Last 12 months" in overview
        assert page.errors == []
    finally:
        httpx.delete(f"{server_url}/api/v1/budgets/{bid}", headers=headers)
        httpx.delete(f"{server_url}/api/v1/portfolios/{pid}", headers=headers)


def test_loans_page_rate_history_whatif_and_detect_at_phone_width(page, server_url):
    """Rate history, rate-change what-if and bank detection, at 390px wide."""
    headers = {"X-API-Key": API_KEY}
    base = f"{server_url}/api/v1/networth/loans"
    loan = httpx.post(
        base,
        headers=headers,
        json={
            "name": "Example Mortgage",
            "principal": 100000,
            "annual_rate_pct": 3.0,
            "start_date": "2020-01-01",
            "term_months": 240,
            "rate_as_of": "2025-01-01",
            "balance_as_of": 80000,
        },
    )
    assert loan.status_code == 200, loan.text
    lid = loan.json()["id"]
    first = httpx.post(
        f"{base}/{lid}/rate-periods",
        headers=headers,
        json={"from_date": "2020-01-01", "annual_rate_pct": 2.4},
    )
    assert first.status_code == 200, first.text
    try:
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(f"{server_url}/index.html")
        page.wait_for_load_state("networkidle")
        page.locator(
            "#appShell .sidebar-nav-link[data-page='loans']"
        ).first.dispatch_event("click")
        page.wait_for_function(
            "document.querySelectorAll('#lmPeriodsBody tr').length >= 1 "
            "&& !document.getElementById('lmPeriodsBody').textContent.includes('No earlier')"
        )
        assert "2.400%" in page.locator("#lmPeriodsBody").inner_text()
        assert "Paid since" in page.locator("#lmPaidToDate").inner_text()
        # history years show in the yearly table, marked and muted
        page.evaluate("document.getElementById('lmScheduleDetails').open = true")
        assert page.locator("#lmScheduleBody .badge").count() == 5
        assert page.locator("#lmHistoryLegend").is_visible()
        # borrowed card and the month-by-month list, open before the overflow check
        assert "Borrowed" in page.locator("#lmCards").inner_text()
        page.evaluate("document.getElementById('lmMonthlyDetails').open = true")
        assert (
            page.locator("#lmMonthlyBody details[open] .badge", has_text="next").count()
            == 1
        )
        assert page.locator("#lmMonthlyBody details").count() > 5

        # add a second period through the form
        page.fill("#lmPeriodFrom", "2023-01-01")
        page.fill("#lmPeriodRate", "3.6")
        page.click("#lmPeriodSubmit")
        page.wait_for_function(
            "document.getElementById('lmPeriodsBody').textContent.includes('3.600%')"
        )
        # a duplicate date is refused with the server's message
        page.fill("#lmPeriodFrom", "2023-01-01")
        page.fill("#lmPeriodRate", "4")
        page.click("#lmPeriodSubmit")
        page.wait_for_function(
            "document.getElementById('lmPeriodStatus').textContent.includes('exists')"
        )

        # a rate change on its own; the repayment options step aside
        page.fill("#lmWiNewRate", "4.5")
        page.wait_for_function(
            "document.getElementById('lmWiResult').textContent.includes('With new rate')"
        )
        assert page.locator("#lmWiLump").is_disabled()
        assert "Costs" in page.locator("#lmWiResult").inner_text()

        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"horizontal overflow of {overflow}px at 390px"

        # detect from bank on Net Worth: no bank rows gives a reason, not an error
        page.locator(
            "#appShell .sidebar-nav-link[data-page='networth']"
        ).first.dispatch_event("click")
        page.wait_for_selector("#nwLoanDetect")
        page.click("#nwLoanDetect")
        page.wait_for_function(
            "document.getElementById('nwLoanStatus').textContent.includes('category')"
        )
        assert page.errors == []
    finally:
        httpx.delete(f"{base}/{lid}", headers=headers)


def test_cost_control3_views_render_against_real_data(page, server_url):
    """Action Items groups, net-worth history, recurring marks and month close."""
    from calendar import monthrange
    from datetime import date

    headers = {"X-API-Key": API_KEY}
    today = date.today()
    pf = httpx.post(
        f"{server_url}/api/v1/portfolios/",
        json={
            "name": "Example Bank Three",
            "base_currency": "EUR",
            "account_type": "bank",
        },
        headers=headers,
    )
    assert pf.status_code in (200, 201), pf.text
    pid = pf.json()["id"]
    last_month = _month_back(today, 1)
    rows = []
    # five months of an invented monthly streaming charge, round amounts
    for k in range(1, 6):
        first = _month_back(today, k)
        rows += [
            {
                "date": first.replace(day=1).isoformat(),
                "description": "EXAMPLE EMPLOYER",
                "amount": 3000,
                "category": "Salary",
                "balance": 5000.0,
            },
            {
                "date": first.replace(day=12).isoformat(),
                "description": "EXAMPLE STREAMING",
                "amount": -15,
                "category": "uncategorized",
                "merchant": "EXAMPLE STREAMING",
            },
        ]
    # the statement reaches the last day of the last complete month
    rows.append(
        {
            "date": last_month.replace(
                day=monthrange(last_month.year, last_month.month)[1]
            ).isoformat(),
            "description": "EXAMPLE GROCER",
            "amount": -40,
            "category": "uncategorized",
            "merchant": "EXAMPLE GROCER",
        }
    )
    saved = httpx.post(
        f"{server_url}/api/v1/spending/save",
        json={"account_portfolio_id": pid, "rows": rows, "duplicate_action": "add"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    month = last_month.strftime("%Y-%m")
    try:
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(f"{server_url}/index.html")
        page.wait_for_load_state("networkidle")

        def no_overflow(where):
            overflow = page.evaluate(
                "document.documentElement.scrollWidth"
                " - document.documentElement.clientWidth"
            )
            assert overflow <= 0, f"horizontal overflow of {overflow}px: {where}"

        def open_page(name):
            page.locator(
                f"#appShell .sidebar-nav-link[data-page='{name}']"
            ).first.dispatch_event("click")
            page.wait_for_load_state("networkidle")

        # Action Items: the page loads grouped, a folded group expands
        open_page("actionitems")
        page.evaluate("loadActionItemsPage()")
        page.wait_for_function(
            "!document.getElementById('actionItemsList').textContent.includes('Loading')"
        )
        assert "Could not load" not in page.locator("#actionItemsList").inner_text()
        page.evaluate(
            """() => {
            const kids = ['A', 'B', 'C'].map(n => ({
                id: 'research:' + n, severity: 'medium', category: 'research',
                title: 'Example price ' + n, detail: '', link_page: 'research',
                status: 'open', ack: null, context: {},
            }));
            _aiState.items = [{
                id: 'group:price_targets', kind: 'group', family: 'price_targets',
                count: 3, severity: 'medium', category: 'research',
                title: '3 price targets crossed', detail: '2 BUY, 1 SELL',
                link_page: 'research', status: 'open', ack: null, children: kids,
            }];
            _aiState.expanded = new Set();
            _renderActionItems();
        }"""
        )
        assert (
            "3 price targets crossed" in page.locator("#actionItemsList").inner_text()
        )
        assert "Example price A" not in page.locator("#actionItemsList").inner_text()
        page.click("[data-ai-toggle='group:price_targets']")
        assert "Example price A" in page.locator("#actionItemsList").inner_text()
        assert page.locator("#actionItemsList [data-ai-ack='checked']").count() == 4
        no_overflow("action items groups")
        # dashboard "This week" card renders from a report
        open_page("dashboard")
        page.evaluate(
            """() => renderDashThisWeek({
            top: [{id: 'x', title: 'Example item', detail: 'd', severity: 'high', link_page: 'diagnostics'}],
            summary: {open_entries: 1}})"""
        )
        assert "Example item" in page.locator("#dashThisWeekArea").inner_text()
        no_overflow("dashboard this week")

        # Net worth history: one snapshot reads "History starts"
        assert (
            httpx.post(
                f"{server_url}/api/v1/networth/history/snapshot", headers=headers
            ).status_code
            == 200
        )
        open_page("networth")
        page.evaluate("loadNetworthHistory()")
        page.wait_for_function(
            "document.getElementById('nwHistoryNote').textContent.includes('History starts')"
        )
        assert "two snapshots" in page.locator("#nwChangeRow").inner_text()
        no_overflow("net worth history")

        # Recurring: mark the series cancelled, see the badge and the totals
        marked = httpx.put(
            f"{server_url}/api/v1/spending/recurring/resolution",
            json={
                "portfolio_id": pid,
                "merchant": "EXAMPLE STREAMING",
                "status": "cancelled",
                "note": "ended by phone",
            },
            headers=headers,
        )
        assert marked.status_code == 200, marked.text
        open_page("spending")
        page.evaluate("_wireSpRecurringTab(); _loadSpRecurring()")
        page.wait_for_function(
            "document.getElementById('spRecurringBody').textContent.includes('EXAMPLE STREAMING')"
        )
        body = page.locator("#spRecurringBody").inner_text()
        assert "Cancelled" in body
        assert "ended by phone" in body
        assert "cancellations save" in page.locator("#spRecurringTotals").inner_text()
        assert page.locator("#spRecurringBody .sp-rec-res").count() >= 1
        page.click("#spRecurringSortCost")
        page.wait_for_function(
            "document.getElementById('spRecurringSortCost').classList.contains('active')"
        )
        page.wait_for_function(
            "document.getElementById('spRecurringBody').textContent.includes('EXAMPLE STREAMING')"
        )
        no_overflow("recurring")

        # Month close: the checklist and figures load, Close marks the month
        open_page("budget")
        page.evaluate("_wireBgMonthClose(); loadMonthClose()")
        page.wait_for_function(
            "document.querySelectorAll('#bgMcChecklist .list-group-item').length > 0"
        )
        assert page.input_value("#bgMcMonth") == month
        assert (
            "imported through the month end"
            in page.locator("#bgMcChecklist").inner_text()
        )
        assert "Income" in page.locator("#bgMcFigures").inner_text()
        no_overflow("month close")
        page.fill("#bgMcNotes", "Example note")
        if page.locator("#bgMcForce").is_visible():
            # Another test's bank account may stop short of the month end: the
            # month is then not ready, and only "Close anyway" is offered.
            # That button asks through a dialog (stubbed here), so close
            # through the API call it makes and render the result.
            page.evaluate(
                "window.apiClient.closeMonth({month: _mc.month, notes: 'Example note', force: true})"
                ".then(_renderMonthClose)"
            )
        else:
            page.click("#bgMcClose")
        page.wait_for_function(
            "document.getElementById('bgMcBanner').textContent.includes('Closed on')"
        )
        assert page.locator("#bgMcReopen").is_visible()
        assert page.locator("#bgMcReport").is_visible()
        stored = httpx.get(
            f"{server_url}/api/v1/spending/month-close?month={month}", headers=headers
        ).json()
        assert stored["closed"]["notes"] == "Example note"
        assert page.errors == []
    finally:
        httpx.delete(
            f"{server_url}/api/v1/spending/month-close?month={month}", headers=headers
        )
        httpx.delete(f"{server_url}/api/v1/portfolios/{pid}", headers=headers)
