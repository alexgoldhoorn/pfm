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
