"""
Pytest configuration and shared fixtures.

This module provides global pytest configuration and imports all test fixtures
from the fixtures module for use across all test modules.
"""

# Import all fixtures to make them available globally
from tests.fixtures.test_fixtures import *

import os
import sys
import logging
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Configure logging for tests
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Disable noisy loggers during tests
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    # Register custom markers
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests (deselect with '-m \"not unit\"')"
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
    )
    config.addinivalue_line(
        "markers",
        "e2e: marks tests as end-to-end tests (deselect with '-m \"not e2e\"')",
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line("markers", "api: marks tests as API tests")
    config.addinivalue_line("markers", "cli: marks tests as CLI tests")
    config.addinivalue_line("markers", "web: marks tests as web client tests")
    config.addinivalue_line(
        "markers", "database: marks tests as database-related tests"
    )
    config.addinivalue_line(
        "markers", "auth: marks tests as authentication-related tests"
    )


def pytest_collection_modifyitems(config, items):
    """Automatically mark tests based on their location."""
    for item in items:
        # Add markers based on test file location
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)

        # Add markers based on test names
        if "slow" in item.name or "performance" in item.name:
            item.add_marker(pytest.mark.slow)
        if "api" in item.name or "endpoint" in item.name:
            item.add_marker(pytest.mark.api)
        if "cli" in item.name:
            item.add_marker(pytest.mark.cli)
        if "web" in item.name or "client" in item.name:
            item.add_marker(pytest.mark.web)
        if "database" in item.name or "db" in item.name:
            item.add_marker(pytest.mark.database)
        if "auth" in item.name or "login" in item.name:
            item.add_marker(pytest.mark.auth)


def pytest_sessionstart(session):
    """Called after the Session object has been created."""
    print("\n" + "=" * 80)
    print("🚀 Starting Portfolio Manager Test Suite")
    print("=" * 80)


def pytest_sessionfinish(session, exitstatus):
    """Called after whole test run finished."""
    if exitstatus == 0:
        print("\n" + "=" * 80)
        print("✅ All tests passed successfully!")
        print("=" * 80)
    else:
        print("\n" + "=" * 80)
        print("❌ Some tests failed.")
        print("=" * 80)


def pytest_runtest_setup(item):
    """Called for each test item before the test is run."""
    # Skip slow tests unless explicitly requested
    if "slow" in [marker.name for marker in item.iter_markers()]:
        if not item.config.getoption("-m") or "slow" not in item.config.getoption("-m"):
            pytest.skip("Skipping slow test (use -m slow to run)")


# HTML report hooks (only available if pytest-html is installed)
try:
    import pytest_html

    def pytest_html_report_title(report):
        """Customize HTML report title."""
        report.title = "Portfolio Manager Test Report"

    def pytest_metadata(metadata):
        """Add metadata to HTML report."""
        metadata["Project"] = "Portfolio Manager"
        metadata["Test Environment"] = os.getenv("PORTF_ENVIRONMENT", "testing")
        metadata["Database URL"] = os.getenv("PORTF_DATABASE_URL", "sqlite:///test.db")
        metadata["Python Path"] = sys.executable

except ImportError:
    # pytest-html not installed, skip HTML report customization
    pass


# Pytest command line options
def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--db-engine",
        action="store",
        default="sqlite",
        choices=["sqlite", "postgresql"],
        help="Database engine to use for tests",
    )


# Browser fixture for E2E tests
@pytest.fixture(scope="session")
def database_engine(pytestconfig):
    """Get database engine from command line option."""
    return pytestconfig.getoption("db_engine")
