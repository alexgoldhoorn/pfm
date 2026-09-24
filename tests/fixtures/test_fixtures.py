"""
Test fixtures and utilities for the portfolio management system.

Reusable fixtures: a temp database, an app wired to it, an authenticated
async client, and sample payloads.
"""

import os
import tempfile
import uuid

import pytest
import pytest_asyncio
import httpx

from portf_server.app import app
from portf_server.dependencies import (
    get_database,
    get_auth_manager,
    get_api_key_manager,
)
from portf_manager.database import Database
from portf_manager.auth import AuthManager
from portf_server.auth_middleware import APIKeyManager
from portf_manager.models import AssetType, TransactionType


@pytest.fixture
def temp_db_path():
    """Create temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def test_database(temp_db_path):
    """Create test database instance."""
    db = Database(temp_db_path)
    yield db


@pytest.fixture
def test_auth_manager(test_database):
    """Create test auth manager instance."""
    auth_manager = AuthManager(test_database)
    yield auth_manager


@pytest.fixture
def test_api_key_manager(test_database):
    """Create test API key manager instance."""
    api_key_manager = APIKeyManager(test_database)
    yield api_key_manager


@pytest.fixture
def test_user_data():
    """Sample test user data."""
    return {
        "username": f"testuser_{uuid.uuid4().hex[:8]}",
        "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
        "password": "testpassword123",
        "full_name": "Test User",
    }


@pytest.fixture
def test_user(test_auth_manager, test_user_data):
    """Create test user in database."""
    test_auth_manager.register_user(**test_user_data)
    test_auth_manager.login(test_user_data["username"], test_user_data["password"])
    user_data = test_auth_manager.get_current_user()
    yield user_data


@pytest.fixture
def test_api_key(test_api_key_manager, test_user):
    """Create test API key."""
    api_key = test_api_key_manager.create_api_key(
        key_name="test-key", description="Test API key"
    )
    yield api_key


@pytest.fixture
def test_app(test_database, test_auth_manager, test_api_key_manager):
    """Create test FastAPI app with overridden dependencies."""
    # Override dependencies
    app.dependency_overrides[get_database] = lambda: test_database
    app.dependency_overrides[get_auth_manager] = lambda: test_auth_manager
    app.dependency_overrides[get_api_key_manager] = lambda: test_api_key_manager

    # Tests exercise the /auth/register HTTP endpoint to set up users; that
    # endpoint is gated by allow_registration (off in production). Enable it on
    # the shared settings singleton for the duration of the test.
    from portf_server.settings import get_settings

    settings = get_settings()
    prev_allow_registration = settings.allow_registration
    settings.allow_registration = True

    yield app

    # Clean up
    settings.allow_registration = prev_allow_registration
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_test_client(test_app):
    """Create async test client for FastAPI app."""
    from httpx import ASGITransport

    async with httpx.AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
        follow_redirects=True,
    ) as client:
        yield client


@pytest.fixture
def auth_headers(test_api_key):
    """Generate authentication headers with API key."""
    return {"X-API-Key": test_api_key["api_key"]}


@pytest.fixture
def sample_asset_data():
    """Sample asset data for testing."""
    return {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "asset_type": AssetType.STOCK.value,
        "exchange": "NASDAQ",
        "currency": "USD",
        "sector": "Technology",
        "description": "Technology company",
    }


@pytest.fixture
def sample_transaction_data():
    """Sample transaction data for testing."""
    return {
        "transaction_type": TransactionType.BUY.value,
        "quantity": 10.0,
        "price": 150.0,
        "total_amount": 1500.0,
        "transaction_date": "2024-01-15",
        "description": "Test transaction",
    }


@pytest.fixture
def sample_portfolio_data():
    """Sample portfolio data for testing."""
    return {
        "name": "Test Portfolio",
        "base_currency": "USD",
        "description": "Test portfolio for unit tests",
    }


@pytest.fixture
def test_csv_data():
    """Sample CSV data for import testing."""
    return [
        "Fecha de operación;Fecha valor;Concepto;Importe;Divisa",
        "15/01/2024;15/01/2024;APPLE @ 10;-1500,00;USD",
        "16/01/2024;16/01/2024;MICROSOFT @ 5;-1000,00;USD",
    ]


@pytest.fixture
def test_csv_file(test_csv_data, temp_db_path):
    """Create temporary CSV file for testing."""
    csv_path = temp_db_path.replace(".db", ".csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("\n".join(test_csv_data))

    yield csv_path

    try:
        os.unlink(csv_path)
    except OSError:
        pass
