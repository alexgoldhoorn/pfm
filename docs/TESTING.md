# Testing Suite Documentation

This document provides comprehensive information about the testing suite for the Portfolio Management System.

## 🧪 Overview

The testing suite includes comprehensive tests for:

- **FastAPI Endpoints** - Unit and integration tests with `pytest` + `httpx.AsyncClient`
- **CLI Operations** - Tests in both local and server modes using fixtures with ephemeral FastAPI server
- **Web Client E2E** - Smoke tests with Playwright for browser automation
- **Performance** - Load testing and performance benchmarking
- **Security** - Vulnerability scanning and code analysis

## 📁 Test Structure

```
tests/
├── conftest.py                 # Global pytest configuration
├── fixtures/
│   └── test_fixtures.py       # Shared test fixtures
├── unit/
│   └── test_api_routers.py    # Unit tests for FastAPI routers
├── integration/
│   ├── test_api_endpoints.py  # Integration tests for API endpoints
│   └── test_cli_modes.py      # CLI tests for both modes
├── e2e/
│   └── test_web_client_e2e.py # End-to-end web client tests
└── __init__.py
```

## 🚀 Quick Start

### Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (for E2E tests)
playwright install
```

### Running Tests

#### Using the Test Runner Script

```bash
# Run all tests (excluding slow tests)
python run_tests.py all

# Run specific test categories
python run_tests.py unit                    # Unit tests only
python run_tests.py integration             # Integration tests only
python run_tests.py e2e                     # End-to-end tests only
python run_tests.py cli                     # CLI tests only
python run_tests.py slow                    # Performance/slow tests

# Run with options
python run_tests.py unit --verbose --failfast
python run_tests.py e2e --browser firefox --no-headless
python run_tests.py all --include-slow     # Include performance tests
```

#### Using pytest Directly

```bash
# Run all tests with coverage
pytest tests/ --cov=portf_server --cov=portf_manager --cov-report=html

# Run specific test types using markers
pytest -m "unit and not slow"              # Unit tests, no slow tests
pytest -m "integration"                    # Integration tests only
pytest -m "e2e"                           # End-to-end tests only
pytest -m "slow"                          # Performance tests only

# Run tests for specific components
pytest tests/unit/test_api_routers.py      # API router tests
pytest tests/integration/test_cli_modes.py # CLI tests
pytest tests/e2e/test_web_client_e2e.py   # Web client tests

# Run with specific options
pytest tests/ -v --tb=short --maxfail=3    # Verbose, short traceback, stop after 3 failures
pytest tests/ --browser=firefox --headless=false  # E2E tests with Firefox, visible browser
```

## 🏷️ Test Categories and Markers

Tests are organized using pytest markers:

- `@pytest.mark.unit` - Unit tests (fast, isolated)
- `@pytest.mark.integration` - Integration tests (moderate speed, may require services)
- `@pytest.mark.e2e` - End-to-end tests (slower, full browser automation)
- `@pytest.mark.slow` - Performance/load tests (very slow)
- `@pytest.mark.api` - API-specific tests
- `@pytest.mark.cli` - CLI-specific tests
- `@pytest.mark.web` - Web client tests
- `@pytest.mark.auth` - Authentication tests
- `@pytest.mark.database` - Database-related tests

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

Coverage reports are generated automatically and saved in multiple formats:

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

Key configuration options in `pytest.ini`:

```ini
[tool:pytest]
# Test discovery
testpaths = tests tests/unit tests/integration tests/e2e
python_files = test_*.py *_test.py

# Coverage settings
addopts = 
    --cov=portf_server
    --cov=portf_manager
    --cov-report=term-missing
    --cov-report=html:htmlcov
    --tb=short

# Async support
asyncio_mode = auto

# Test markers
markers =
    unit: Unit tests
    integration: Integration tests
    e2e: End-to-end tests
    slow: Slow tests
    api: API tests
    cli: CLI tests
```

### Environment Variables

Tests use specific environment variables:

```bash
export PORTF_DATABASE_URL="sqlite:///test.db"
export PORTF_SECRET_KEY="test-secret-key"
export PORTF_ENVIRONMENT="testing"
export PORTF_LOG_LEVEL="DEBUG"
export GEMINI_API_KEY="test-gemini-key"  # For LLM tests
```

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

The comprehensive CI/CD pipeline (`.github/workflows/ci.yml`) includes:

1. **Lint and Code Quality**
   - Black formatter check
   - Flake8 linting
   - MyPy type checking
   - Pre-commit hooks

2. **Database Migration Tests**
   - SQLite and PostgreSQL
   - Multiple Python versions

3. **Unit and Integration Tests**
   - Parallel test execution
   - Coverage reporting
   - Multiple Python versions

4. **End-to-End Tests**
   - Multiple browsers (Chromium, Firefox, WebKit)
   - Screenshot capture on failure

5. **CLI Tests**
   - Both local and server modes
   - Cross-platform testing

6. **Security Scanning**
   - Safety vulnerability checks
   - Bandit security analysis

7. **Docker Image Build and Test**
   - Multi-stage builds
   - Container health checks
   - Image testing

8. **Performance Tests**
   - Load testing with Locust
   - Performance benchmarking

### Running Locally Like CI

```bash
# Run the full CI suite locally
python run_tests.py all --include-slow

# Run security checks
safety check
bandit -r portf_server portf_manager

# Run linting
black --check .
flake8 .
mypy portf_server portf_manager
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

## 📈 Performance Testing

### Load Testing

```bash
# Run performance tests
pytest -m "slow" --verbose

# Use Locust for load testing
locust -f tests/performance/locustfile.py --host=http://localhost:8000
```

### Memory and CPU Profiling

```python
import pytest
import psutil
import time

@pytest.mark.slow
def test_memory_usage():
    process = psutil.Process()
    start_memory = process.memory_info().rss
    
    # Run memory-intensive operation
    large_dataset = create_large_dataset()
    
    end_memory = process.memory_info().rss
    memory_increase = end_memory - start_memory
    
    # Assert memory usage is within acceptable limits
    assert memory_increase < 100 * 1024 * 1024  # 100MB
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

```bash
# Increase timeout for slow tests
pytest --timeout=300 tests/

# Or in pytest.ini
timeout = 300
```

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
6. **Follow black formatting**: Use `black .` to format code
7. **Update documentation**: Add new test categories to this document

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
