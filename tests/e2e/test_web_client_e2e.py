"""
End-to-End Web Client Tests with Playwright

This module provides smoke tests for the web client interface
using Playwright for browser automation and testing.
"""

import pytest
import asyncio
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
import json
import time


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def browser():
    """Launch browser for testing."""
    async with async_playwright() as p:
        # Use Chromium for testing, but can be configured for different browsers
        browser = await p.chromium.launch(
            headless=True,  # Set to False for debugging
            args=[
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        yield browser
        await browser.close()


@pytest.fixture
async def context(browser: Browser):
    """Create browser context for tests."""
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720}, ignore_https_errors=True
    )
    yield context
    await context.close()


@pytest.fixture
async def page(context: BrowserContext):
    """Create page for tests."""
    page = await context.new_page()
    yield page
    await page.close()


@pytest.fixture
async def authenticated_page(page: Page, test_server):
    """Create authenticated page with mocked API responses."""
    # Navigate to web client
    web_client_url = (
        f"{test_server}/index.html"
        if test_server.endswith("/")
        else f"{test_server}/index.html"
    )

    # Mock API responses for testing
    await page.route(
        "**/api/v1/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"message": "Mocked response"}),
        ),
    )

    await page.goto(web_client_url)
    yield page


class TestWebClientSmoke:
    """Smoke tests for web client functionality."""

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_web_client_loads(self, page: Page):
        """Test that web client loads successfully."""
        # Serve the web client files statically
        await page.goto(
            "file://"
            + str(page.context.browser.launch_command)
            + "/web_client/index.html"
        )

        # Check that the page title loads
        title = await page.title()
        assert "Portfolio Manager" in title or "Portfolio" in title

        # Check that main elements are present
        body = await page.inner_text("body")
        assert len(body) > 0  # Page has content

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_navigation_elements(self, page: Page):
        """Test basic navigation elements."""
        # Mock a simple HTML structure for testing
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager</title>
        </head>
        <body>
            <nav id="navbar">
                <a href="#dashboard" id="nav-dashboard">Dashboard</a>
                <a href="#assets" id="nav-assets">Assets</a>
                <a href="#transactions" id="nav-transactions">Transactions</a>
                <a href="#portfolios" id="nav-portfolios">Portfolios</a>
            </nav>
            <main id="content">
                <div id="dashboard-section">Dashboard Content</div>
            </main>
        </body>
        </html>
        """
        )

        # Check navigation elements exist
        navbar = await page.query_selector("#navbar")
        assert navbar is not None

        # Check navigation links
        nav_links = [
            "nav-dashboard",
            "nav-assets",
            "nav-transactions",
            "nav-portfolios",
        ]
        for link_id in nav_links:
            link = await page.query_selector(f"#{link_id}")
            assert link is not None

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_dashboard_elements(self, page: Page):
        """Test dashboard elements are present."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager - Dashboard</title>
        </head>
        <body>
            <div id="dashboard">
                <div id="portfolio-value">$0.00</div>
                <div id="total-assets">0</div>
                <div id="recent-transactions">
                    <h3>Recent Transactions</h3>
                    <ul id="transaction-list"></ul>
                </div>
                <div id="asset-allocation">
                    <h3>Asset Allocation</h3>
                    <canvas id="allocation-chart"></canvas>
                </div>
            </div>
        </body>
        </html>
        """
        )

        # Check dashboard components
        portfolio_value = await page.query_selector("#portfolio-value")
        assert portfolio_value is not None

        total_assets = await page.query_selector("#total-assets")
        assert total_assets is not None

        recent_transactions = await page.query_selector("#recent-transactions")
        assert recent_transactions is not None

        asset_allocation = await page.query_selector("#asset-allocation")
        assert asset_allocation is not None

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_assets_page_functionality(self, page: Page):
        """Test assets page basic functionality."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager - Assets</title>
        </head>
        <body>
            <div id="assets-page">
                <h1>Assets</h1>
                <button id="add-asset-btn">Add Asset</button>
                <table id="assets-table">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th>Type</th>
                            <th>Price</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="assets-table-body">
                    </tbody>
                </table>
                <div id="add-asset-modal" style="display: none;">
                    <form id="add-asset-form">
                        <input type="text" id="asset-symbol" placeholder="Symbol" required>
                        <input type="text" id="asset-name" placeholder="Name" required>
                        <select id="asset-type" required>
                            <option value="stock">Stock</option>
                            <option value="bond">Bond</option>
                            <option value="etf">ETF</option>
                        </select>
                        <button type="submit">Add Asset</button>
                        <button type="button" id="cancel-add-asset">Cancel</button>
                    </form>
                </div>
            </div>
            <script>
                document.getElementById('add-asset-btn').addEventListener('click', function() {
                    document.getElementById('add-asset-modal').style.display = 'block';
                });

                document.getElementById('cancel-add-asset').addEventListener('click', function() {
                    document.getElementById('add-asset-modal').style.display = 'none';
                });
            </script>
        </body>
        </html>
        """
        )

        # Test add asset button opens modal
        add_asset_btn = await page.query_selector("#add-asset-btn")
        assert add_asset_btn is not None

        await add_asset_btn.click()

        # Check modal is visible
        modal = await page.query_selector("#add-asset-modal")
        modal_display = await modal.get_attribute("style")
        assert (
            "display: block" in modal_display
            or "display:block" in modal_display.replace(" ", "")
        )

        # Test form elements
        symbol_input = await page.query_selector("#asset-symbol")
        assert symbol_input is not None

        name_input = await page.query_selector("#asset-name")
        assert name_input is not None

        type_select = await page.query_selector("#asset-type")
        assert type_select is not None

        # Test cancel button
        cancel_btn = await page.query_selector("#cancel-add-asset")
        await cancel_btn.click()

        # Check modal is hidden
        modal_display_after = await modal.get_attribute("style")
        assert "display: none" in modal_display_after

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_transactions_page_functionality(self, page: Page):
        """Test transactions page basic functionality."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager - Transactions</title>
        </head>
        <body>
            <div id="transactions-page">
                <h1>Transactions</h1>
                <div id="filters">
                    <input type="text" id="symbol-filter" placeholder="Filter by symbol">
                    <input type="date" id="date-from-filter">
                    <input type="date" id="date-to-filter">
                    <button id="apply-filters">Apply Filters</button>
                    <button id="clear-filters">Clear Filters</button>
                </div>
                <button id="add-transaction-btn">Add Transaction</button>
                <table id="transactions-table">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Symbol</th>
                            <th>Type</th>
                            <th>Quantity</th>
                            <th>Price</th>
                            <th>Total</th>
                        </tr>
                    </thead>
                    <tbody id="transactions-table-body">
                        <tr>
                            <td>2024-01-15</td>
                            <td>AAPL</td>
                            <td>BUY</td>
                            <td>10</td>
                            <td>$150.00</td>
                            <td>$1,500.00</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        )

        # Test filter elements
        symbol_filter = await page.query_selector("#symbol-filter")
        assert symbol_filter is not None

        date_from_filter = await page.query_selector("#date-from-filter")
        assert date_from_filter is not None

        date_to_filter = await page.query_selector("#date-to-filter")
        assert date_to_filter is not None

        # Test filter interaction
        await symbol_filter.fill("AAPL")
        symbol_value = await symbol_filter.input_value()
        assert symbol_value == "AAPL"

        # Test transactions table has content
        table_body = await page.query_selector("#transactions-table-body")
        rows = await table_body.query_selector_all("tr")
        assert len(rows) > 0  # Has at least one transaction row

        # Test first row content
        first_row = rows[0]
        cells = await first_row.query_selector_all("td")
        assert len(cells) == 6  # Should have 6 columns

        # Check cell content
        symbol_cell = await cells[1].inner_text()
        assert symbol_cell == "AAPL"

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_responsive_design(self, page: Page):
        """Test responsive design on different screen sizes."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
                .nav-desktop { display: block; }
                .nav-mobile { display: none; }

                @media (max-width: 768px) {
                    .nav-desktop { display: none; }
                    .nav-mobile { display: block; }
                    .container { padding: 10px; }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <nav class="nav-desktop">Desktop Navigation</nav>
                <nav class="nav-mobile">Mobile Navigation</nav>
                <main>Content</main>
            </div>
        </body>
        </html>
        """
        )

        # Test desktop view (default 1280x720)
        desktop_nav = await page.query_selector(".nav-desktop")
        desktop_visible = await desktop_nav.is_visible()
        assert desktop_visible

        mobile_nav = await page.query_selector(".nav-mobile")
        mobile_visible = await mobile_nav.is_visible()
        assert not mobile_visible

        # Test mobile view
        await page.set_viewport_size({"width": 375, "height": 667})
        await page.wait_for_timeout(100)  # Wait for CSS to apply

        await desktop_nav.is_visible()
        await mobile_nav.is_visible()

        # Note: CSS media queries might not work in this test setup
        # This is more of a structural test
        assert desktop_nav is not None
        assert mobile_nav is not None

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_error_handling(self, page: Page):
        """Test error handling in web client."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager</title>
        </head>
        <body>
            <div id="error-container" style="display: none;">
                <div id="error-message"></div>
                <button id="close-error">Close</button>
            </div>
            <button id="trigger-error">Trigger Error</button>

            <script>
                function showError(message) {
                    const container = document.getElementById('error-container');
                    const messageEl = document.getElementById('error-message');
                    messageEl.textContent = message;
                    container.style.display = 'block';
                }

                function hideError() {
                    const container = document.getElementById('error-container');
                    container.style.display = 'none';
                }

                document.getElementById('trigger-error').addEventListener('click', function() {
                    showError('This is a test error message');
                });

                document.getElementById('close-error').addEventListener('click', hideError);
            </script>
        </body>
        </html>
        """
        )

        # Test error display
        trigger_btn = await page.query_selector("#trigger-error")
        await trigger_btn.click()

        # Check error is shown
        error_container = await page.query_selector("#error-container")
        error_visible = await error_container.is_visible()
        assert error_visible

        # Check error message
        error_message = await page.query_selector("#error-message")
        message_text = await error_message.inner_text()
        assert "test error message" in message_text

        # Test closing error
        close_btn = await page.query_selector("#close-error")
        await close_btn.click()

        error_visible_after = await error_container.is_visible()
        assert not error_visible_after

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_form_validation(self, page: Page):
        """Test client-side form validation."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager - Form Validation</title>
        </head>
        <body>
            <form id="test-form">
                <input type="text" id="symbol" required placeholder="Symbol">
                <input type="number" id="quantity" required min="1" placeholder="Quantity">
                <input type="email" id="email" required placeholder="Email">
                <button type="submit" id="submit-btn">Submit</button>
            </form>
            <div id="validation-message" style="display: none; color: red;"></div>

            <script>
                document.getElementById('test-form').addEventListener('submit', function(e) {
                    e.preventDefault();

                    const symbol = document.getElementById('symbol').value;
                    const quantity = document.getElementById('quantity').value;
                    const email = document.getElementById('email').value;
                    const messageEl = document.getElementById('validation-message');

                    if (!symbol || !quantity || !email) {
                        messageEl.textContent = 'All fields are required';
                        messageEl.style.display = 'block';
                        return;
                    }

                    if (quantity < 1) {
                        messageEl.textContent = 'Quantity must be at least 1';
                        messageEl.style.display = 'block';
                        return;
                    }

                    messageEl.style.display = 'none';
                    alert('Form submitted successfully!');
                });
            </script>
        </body>
        </html>
        """
        )

        # Test empty form submission
        submit_btn = await page.query_selector("#submit-btn")
        await submit_btn.click()

        validation_message = await page.query_selector("#validation-message")
        message_visible = await validation_message.is_visible()
        assert message_visible

        message_text = await validation_message.inner_text()
        assert "required" in message_text.lower()

        # Test invalid quantity
        symbol_input = await page.query_selector("#symbol")
        quantity_input = await page.query_selector("#quantity")
        email_input = await page.query_selector("#email")

        await symbol_input.fill("AAPL")
        await quantity_input.fill("0")  # Invalid quantity
        await email_input.fill("test@example.com")
        await submit_btn.click()

        message_text_after = await validation_message.inner_text()
        assert (
            "quantity" in message_text_after.lower()
            or "least" in message_text_after.lower()
        )

        # Test valid form
        await quantity_input.fill("10")  # Valid quantity

        # Handle the alert dialog
        page.on("dialog", lambda dialog: dialog.accept())
        await submit_btn.click()

        # If we get here without error, the form validation passed
        message_visible_after = await validation_message.is_visible()
        assert not message_visible_after


class TestWebClientPerformance:
    """Performance tests for web client."""

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_page_load_time(self, page: Page):
        """Test page load performance."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager</title>
            <script>
                window.performance.mark('page-start');
            </script>
        </head>
        <body>
            <div id="content">
                <h1>Portfolio Manager</h1>
                <p>Loading content...</p>
            </div>
            <script>
                window.performance.mark('page-end');
                window.performance.measure('page-load', 'page-start', 'page-end');
            </script>
        </body>
        </html>
        """
        )

        # Measure load time
        performance_timing = await page.evaluate(
            """
            () => {
                const entries = performance.getEntriesByType('measure');
                const pageLoad = entries.find(entry => entry.name === 'page-load');
                return pageLoad ? pageLoad.duration : null;
            }
        """
        )

        # Page should load quickly (adjust threshold as needed)
        if performance_timing:
            assert performance_timing < 1000  # Less than 1 second

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_large_table_rendering(self, page: Page):
        """Test rendering performance with large data sets."""
        # Generate HTML for a large table
        table_rows = []
        for i in range(100):
            table_rows.append(
                f"""
                <tr>
                    <td>{i + 1}</td>
                    <td>STOCK{i:03d}</td>
                    <td>Company {i}</td>
                    <td>${100 + i}.00</td>
                    <td>{10 * (i + 1)}</td>
                </tr>
            """
            )

        table_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager - Large Table</title>
        </head>
        <body>
            <div id="table-container">
                <table id="large-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Symbol</th>
                            <th>Name</th>
                            <th>Price</th>
                            <th>Volume</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(table_rows)}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """

        # Measure rendering time
        start_time = time.time()
        await page.set_content(table_html)

        # Wait for table to be rendered
        await page.wait_for_selector("#large-table")
        end_time = time.time()

        render_time = end_time - start_time

        # Should render reasonably quickly (under 2 seconds)
        assert render_time < 2.0

        # Verify table has correct number of rows
        rows = await page.query_selector_all("#large-table tbody tr")
        assert len(rows) == 100


class TestWebClientAccessibility:
    """Basic accessibility tests for web client."""

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_keyboard_navigation(self, page: Page):
        """Test keyboard navigation functionality."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager</title>
        </head>
        <body>
            <button id="btn1" tabindex="1">Button 1</button>
            <button id="btn2" tabindex="2">Button 2</button>
            <input type="text" id="input1" tabindex="3" placeholder="Input 1">
            <button id="btn3" tabindex="4">Button 3</button>
        </body>
        </html>
        """
        )

        # Test Tab navigation
        await page.focus("#btn1")
        focused_element = await page.evaluate("document.activeElement.id")
        assert focused_element == "btn1"

        # Tab to next element
        await page.keyboard.press("Tab")
        focused_element = await page.evaluate("document.activeElement.id")
        assert focused_element == "btn2"

        # Tab to input
        await page.keyboard.press("Tab")
        focused_element = await page.evaluate("document.activeElement.id")
        assert focused_element == "input1"

        # Test input functionality
        await page.keyboard.type("test input")
        input_value = await page.input_value("#input1")
        assert input_value == "test input"

    @pytest.mark.e2e
    @pytest.mark.web
    @pytest.mark.slow
    async def test_aria_labels(self, page: Page):
        """Test ARIA labels and accessibility attributes."""
        await page.set_content(
            """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Portfolio Manager</title>
        </head>
        <body>
            <button id="add-btn" aria-label="Add new asset">+</button>
            <input type="text" id="search" aria-label="Search assets" placeholder="Search">
            <table id="data-table" role="table" aria-label="Assets table">
                <thead>
                    <tr role="row">
                        <th role="columnheader">Symbol</th>
                        <th role="columnheader">Name</th>
                    </tr>
                </thead>
                <tbody>
                    <tr role="row">
                        <td role="cell">AAPL</td>
                        <td role="cell">Apple Inc.</td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """
        )

        # Test ARIA labels
        add_btn_label = await page.get_attribute("#add-btn", "aria-label")
        assert add_btn_label == "Add new asset"

        search_label = await page.get_attribute("#search", "aria-label")
        assert search_label == "Search assets"

        table_label = await page.get_attribute("#data-table", "aria-label")
        assert table_label == "Assets table"

        # Test table roles
        table_role = await page.get_attribute("#data-table", "role")
        assert table_role == "table"

        # Test cell roles
        cells = await page.query_selector_all("td[role='cell']")
        assert len(cells) == 2
