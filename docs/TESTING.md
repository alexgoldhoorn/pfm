# Testing Suite Documentation

This document provides comprehensive information about the testing suite for the Portfolio Management System.

## 🧪 Overview

The testing suite includes tests for:

- **FastAPI Endpoints** - Unit and integration tests with `pytest` + `httpx.AsyncClient`
- **CLI Operations** - Tests in both local and server modes using fixtures with ephemeral FastAPI server
- **Web Client E2E** - Smoke tests with Playwright for browser automation
- **Web Client JS** - DOM-free tests of the `window.`-exported helpers with Node's built-in runner (`web_client/js/tests/*.test.mjs`, `make test-js`)

There are no performance/load or security-scanning tests in the repo.

## 📁 Test Structure

```
tests/
├── conftest.py                 # Global pytest configuration (HOME isolation, network guard, markers)
├── fixtures/
│   └── test_fixtures.py       # Shared test fixtures
├── test_*.py                   # Older unit tests at the top level (database, CLI, parsers, tax, ...)
├── unit/
│   ├── test_api_routers.py    # Unit tests for FastAPI routers
│   └── test_*.py              # ~80 more modules, one per service/router/feature
├── integration/
│   ├── test_api_endpoints.py  # Integration tests for API endpoints
│   └── test_cli_modes.py      # CLI tests for both modes
├── e2e/
│   └── test_web_client_e2e.py # End-to-end web client smoke test
└── __init__.py

web_client/js/tests/*.test.mjs  # JS tests (Node built-in runner)
mcp/tests/                      # MCP server tests, run separately (cd mcp && python3 -m pytest -q)
```

## 🚀 Quick Start

### Prerequisites

```bash
# Install dependencies (dev deps included; pytest, pytest-asyncio, pytest-cov,
# pytest-timeout, playwright, ... come from [tool.uv] dev-dependencies)
uv sync

# Install the Playwright browser (for E2E tests; the smoke test needs Chromium)
uv run playwright install chromium
```

If the project `.venv` is root-owned (Docker-created), run tooling with
`UV_PROJECT_ENVIRONMENT=~/.cache/pfm-venv uv run ...`.

### Running Tests

```bash
# Unit suite (what CI runs; ~1.9k tests, under a minute)
uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e

# Integration + e2e (each spawns the real app on a temp DB; e2e needs Chromium)
uv run pytest tests/integration tests/e2e

# Web client JS tests
make test-js

# MCP server tests (separate package, no pfm dependencies)
cd mcp && python3 -m pytest -q

# Everything under tests/ (make test)
uv run pytest tests/ -v --tb=short
```

```bash
# Run specific test types using markers
uv run pytest -m "unit"                       # Unit tests only
uv run pytest -m "integration"                # Integration tests only
uv run pytest -m "e2e"                        # End-to-end tests only

# Run tests for specific components
uv run pytest tests/unit/test_api_routers.py      # API router tests
uv run pytest tests/integration/test_cli_modes.py # CLI tests
uv run pytest tests/e2e/test_web_client_e2e.py   # Web client tests

# Run with specific options
uv run pytest tests/ -v --tb=short --maxfail=3    # Verbose, short traceback, stop after 3 failures
uv run pytest tests/e2e --browser firefox --headed # pytest-playwright options
```

Live network tests (`@pytest.mark.network`) are skipped unless
`PFM_LIVE_NETWORK_TESTS=1` is set.

## 🏷️ Test Categories and Markers

Tests are organized using pytest markers (`pytest.ini` runs with
`--strict-markers`, so every marker must be declared there):

- `unit` / `integration` / `e2e` - added automatically from the directory by
  `pytest_collection_modifyitems` in `tests/conftest.py` (never from test names)
- `@pytest.mark.slow` - Slow tests
- `@pytest.mark.api` - API-specific tests
- `@pytest.mark.cli` - CLI-specific tests
- `@pytest.mark.web` - Web client tests
- `@pytest.mark.auth` - Authentication tests
- `@pytest.mark.database` - Database-related tests
- `@pytest.mark.network` - Makes real outbound calls; skipped unless `PFM_LIVE_NETWORK_TESTS=1`

### Running Tests by Category

```bash
# Run only fast tests
pytest -m "not slow"

# Run API tests only
pytest -m "api"

# Run CLI tests in local mode only
pytest -m "cli" -k "local"

# Exclude E2E tests
pytest -m "not e2e"

# Run authentication tests
pytest -m "auth"
```

## 📊 Coverage Reports

Coverage is not part of the default `addopts`; ask for it explicitly
(`pytest-cov` is a dev dependency, `make test-coverage` does this):

```bash
# HTML report (browse to htmlcov/index.html)
pytest --cov=portf_server --cov=portf_manager --cov-report=html

# Terminal report
pytest --cov=portf_server --cov=portf_manager --cov-report=term-missing

# XML report (for CI/CD)
pytest --cov=portf_server --cov=portf_manager --cov-report=xml
```

## 🔧 Test Configuration

### pytest.ini Configuration

Key configuration options in `pytest.ini` (the section header must be
`[pytest]`; `[tool:pytest]` is silently ignored):

```ini
[pytest]
minversion = 6.0
addopts = -ra --ignore=mcp --strict-config --strict-markers --tb=short -v
testpaths = tests tests/unit tests/integration tests/e2e
python_files = test_*.py *_test.py
markers =
    unit: ...
    integration: ...
    e2e: ...
    slow: ...
    api: ...
    cli: ...
    web: ...
    database: ...
    auth: ...
    network: makes real outbound calls; skipped unless PFM_LIVE_NETWORK_TESTS=1
asyncio_mode = strict
timeout = 300          # needs the pytest-timeout dev dep
filterwarnings = error, ignore::UserWarning, ignore::DeprecationWarning, ...
```

### Environment and isolation

No environment variables need to be exported. `tests/conftest.py` sets up each
test:

- every test gets its own empty `HOME` (the CLI writes `~/.portf_session`);
  write test files under `tmp_path`, never the working directory
- `PORTF_LLM_RETRY_DELAY=0` so LLM retry tests do not sleep
- the €STR (`risk_free.py`) and HICP (`inflation.py`) fetchers are stubbed
- **a network guard fails any real HTTP call**, even one the app swallows;
  mock at the seam (`portf_manager.market._fetch_quote_live`,
  `currency_utils.is_gbx`, `portf_server.routers.llm.get_llm_client`) or mark
  the test `@pytest.mark.network`. Loopback is allowed only in integration/e2e.

Integration and e2e tests start the real app in a **spawned** (not forked)
process on a temp DB with `SERVER_API_KEY` seeded.

## 🖥️ CLI Testing

CLI tests run in both local and server modes using ephemeral FastAPI servers.

### Local Mode Tests

Tests CLI operations using SQLite database:

```python
@pytest.mark.cli
def test_local_mode_asset_management(local_config, test_user):
    cli = PortfolioManagerCLI(local_config)
    cli.add_asset("AAPL", "Apple Inc.", "stock")
    # Verify asset creation...
```

### Server Mode Tests

Tests CLI operations against a running FastAPI server:

```python
@pytest.mark.cli
def test_server_mode_http_communication(ephemeral_server):
    config = PortfolioConfig(
        server_url=ephemeral_server.server_url,
        api_key=ephemeral_server.create_api_key()
    )
    cli = PortfolioManagerCLI(config)
    # Test HTTP operations...
```

## 🌐 End-to-End Web Client Tests

E2E tests use Playwright for browser automation:

### Browser Configuration

```bash
# Run with different browsers
pytest tests/e2e/ --browser=chromium    # Default
pytest tests/e2e/ --browser=firefox
pytest tests/e2e/ --browser=webkit

# Run with visible browser (for debugging)
pytest tests/e2e/ --headless=false
```

### Test Structure

```python
@pytest.mark.e2e
@pytest.mark.web
@pytest.mark.slow
async def test_assets_page_functionality(page: Page):
    await page.set_content("""<!-- HTML content -->""")
    
    # Test interactions
    add_asset_btn = await page.query_selector("#add-asset-btn")
    await add_asset_btn.click()
    
    # Verify results
    modal = await page.query_selector("#add-asset-modal")
    assert await modal.is_visible()
```

## 🏗️ FastAPI Endpoint Tests

API tests use `httpx.AsyncClient` for async HTTP testing:

### Unit Tests

Test individual router functions:

```python
@pytest.mark.unit
@pytest.mark.api
@pytest.mark.asyncio
async def test_create_asset(async_test_client: AsyncClient, auth_headers, sample_asset_data):
    response = await async_test_client.post(
        "/api/v1/assets", 
        json=sample_asset_data, 
        headers=auth_headers
    )
    assert response.status_code == status.HTTP_201_CREATED
```

### Integration Tests

Test complete API workflows:

```python
@pytest.mark.integration
@pytest.mark.api
@pytest.mark.asyncio
async def test_asset_workflow(async_test_client: AsyncClient, auth_headers):
    # Create asset
    asset_response = await async_test_client.post("/api/v1/assets", ...)
    asset_id = asset_response.json()["id"]
    
    # Create transaction
    transaction_response = await async_test_client.post("/api/v1/transactions", ...)
    
    # Verify relationship
    assert transaction_response.json()["asset_id"] == asset_id
```

## 🔍 Test Fixtures

Comprehensive fixtures in `tests/fixtures/test_fixtures.py`:

### Database Fixtures

```python
@pytest.fixture
def temp_db_path():
    """Create temporary database file."""
    
@pytest.fixture
def test_database(temp_db_path):
    """Create test database instance."""
    
@pytest.fixture
def test_user(test_auth_manager, test_user_data):
    """Create test user in database."""
```

### API Fixtures

```python
@pytest.fixture
def test_app(test_database, test_auth_manager):
    """Create test FastAPI app."""
    
@pytest.fixture
async def async_test_client(test_app):
    """Create async test client."""
    
@pytest.fixture
def auth_headers(test_api_key):
    """Generate authentication headers."""
```

### CLI Fixtures

```python
@pytest.fixture
def ephemeral_server(test_app):
    """Create ephemeral FastAPI server."""
    
@pytest.fixture
def local_config(temp_db_path):
    """Create local mode configuration."""
    
@pytest.fixture
def server_config(ephemeral_server):
    """Create server mode configuration."""
```

## 🚀 CI/CD Integration

### GitHub Actions Workflow

`.github/workflows/ci.yml` runs on pushes and pull requests to `main`, on
Python 3.13 with `uv sync --frozen`, in three jobs:

1. **lint** - `black --check .`, `ruff check portf_manager portf_server`,
   `flake8 portf_manager portf_server --max-line-length=88 --extend-ignore=E203,W503,E501`
2. **test** - the unit suite:
   `uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
3. **test-js** - `node --test web_client/js/tests/` on Node 20

Integration and e2e tests are **not** run in CI (they need a spawned server
and Chromium); run them locally before pushing. Docker images are built by
`.github/workflows/docker-build-push.yml` only on `v*` tags or manual dispatch
(see `docs/DOCKER_CI_CD.md`).

Locally, `.pre-commit-config.yaml` runs the real-financials check, black,
flake8 and autoflake on commit. The project convention (`CLAUDE.md`) is a
pre-push hook that runs the full unit suite; it is a local git hook, not
installed by `.pre-commit-config.yaml`.

### Running Locally Like CI

```bash
uv run black --check .
uv run ruff check portf_manager portf_server
uv run flake8 portf_manager portf_server --max-line-length=88 --extend-ignore=E203,W503,E501
uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
make test-js
```

## 🐛 Debugging Tests

### Debugging E2E Tests

```bash
# Run with visible browser
pytest tests/e2e/ --headless=false --browser=chromium -s

# Add debugging in test code
async def test_example(page):
    await page.pause()  # Opens browser dev tools
    # ... rest of test
```

### Debugging Async Tests

```python
import asyncio

# Add debugging points
async def test_async_function():
    breakpoint()  # Use with async-aware debugger
    result = await some_async_operation()
    assert result is not None
```

### Test Database Inspection

```python
@pytest.mark.unit
def test_with_db_inspection(test_database):
    # Add data
    test_database.create_asset(...)
    
    # Inspect database state
    import sqlite3
    conn = sqlite3.connect(test_database.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assets")
    print(cursor.fetchall())
```

## 🛠️ Custom Test Utilities

### Mock Services

```python
@pytest.fixture
def mock_yfinance():
    """Mock yfinance responses."""
    mock = Mock()
    mock.history.return_value = {'Close': [150.0, 151.0]}
    return mock

@pytest.fixture
def mock_gemini_client():
    """Mock Gemini client for LLM testing."""
    mock = Mock()
    mock.extract_transactions.return_value = [...]
    return mock
```

### Test Data Generators

```python
@pytest.fixture
def performance_data():
    """Generate performance test data."""
    return {
        "assets": [
            {"symbol": f"TEST{i:03d}", "name": f"Test Company {i}"}
            for i in range(100)
        ],
        "transactions": [
            {"asset_id": i % 100 + 1, "quantity": float(i * 10)}
            for i in range(1000)
        ]
    }
```

## 🔧 Troubleshooting

### Common Issues

1. **Import Errors**
   ```bash
   # Ensure project root is in Python path
   export PYTHONPATH="${PYTHONPATH}:$(pwd)"
   ```

2. **Database Lock Issues**
   ```python
   # Use separate test databases
   @pytest.fixture
   def isolated_db():
       return Database(f"test_{uuid.uuid4().hex}.db")
   ```

3. **Async Test Issues**
   ```python
   # Ensure proper async markers
   @pytest.mark.asyncio
   async def test_async_function():
       pass
   ```

4. **Browser Issues in E2E Tests**
   ```bash
   # Install browser dependencies
   playwright install-deps
   
   # Run with specific browser
   pytest --browser=chromium tests/e2e/
   ```

### Test Timeout Issues

`pytest.ini` sets `timeout = 300` (thread method) via `pytest-timeout`;
override per run with `pytest --timeout=600 tests/`.

## 📚 Resources

- [pytest Documentation](https://docs.pytest.org/)
- [httpx Documentation](https://www.python-httpx.org/)
- [Playwright Python Documentation](https://playwright.dev/python/)
- [FastAPI Testing Guide](https://fastapi.tiangolo.com/tutorial/testing/)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)

## 📝 Contributing to Tests

When adding new tests:

1. **Follow naming conventions**: `test_*.py` for files, `test_*` for functions
2. **Use appropriate markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, etc.
3. **Write descriptive test names**: `test_create_asset_with_valid_data`
4. **Use fixtures for setup**: Leverage existing fixtures or create new ones
5. **Add docstrings**: Document what the test is verifying
6. **Follow black formatting**: Use `uv run black <file>` to format code
7. **Assert the exact outcome**: no "any of 200/403/500" or `if "id" in data: ... else: pass` tests — they pass against missing endpoints
8. **Invent data**: fictional asset names and prices, never real holdings
9. **Update documentation**: Add new test categories to this document

### Example New Test

```python
@pytest.mark.unit
@pytest.mark.api
@pytest.mark.asyncio
async def test_create_portfolio_with_entity_relationship(
    async_test_client: AsyncClient, 
    auth_headers, 
    sample_entity_data, 
    sample_portfolio_data
):
    """Test creating a portfolio with an associated entity."""
    # Create entity first
    entity_response = await async_test_client.post(
        "/api/v1/entities", 
        json=sample_entity_data, 
        headers=auth_headers
    )
    assert entity_response.status_code == 201
    entity_id = entity_response.json()["id"]
    
    # Create portfolio with entity
    portfolio_data = {**sample_portfolio_data, "entity_id": entity_id}
    portfolio_response = await async_test_client.post(
        "/api/v1/portfolios",
        json=portfolio_data,
        headers=auth_headers
    )
    
    assert portfolio_response.status_code == 201
    portfolio = portfolio_response.json()
    assert portfolio["entity_id"] == entity_id
```

This comprehensive testing suite ensures high code quality, reliability, and maintainability of the Portfolio Management System across all components and deployment modes.
