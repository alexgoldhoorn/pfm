"""
Test fixtures and utilities for the portfolio management system.

This module provides reusable fixtures for testing FastAPI endpoints,
CLI operations, database interactions, and web client functionality.
"""

import asyncio
import os
import tempfile
import uuid
from datetime import date, timedelta
from typing import Dict, Any
from unittest.mock import Mock

import pytest
import pytest_asyncio
import httpx
from fastapi.testclient import TestClient

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


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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


@pytest.fixture
def test_client(test_app):
    """Create test client for FastAPI app."""
    with TestClient(test_app) as client:
        yield client


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
def sample_entity_data():
    """Sample entity data for testing."""
    return {
        "name": "Test Broker",
        "entity_type": "broker",
        "website": "https://testbroker.com",
        "description": "Test broker entity",
    }


@pytest.fixture
def mock_yfinance():
    """Mock yfinance responses."""
    mock = Mock()
    mock.history.return_value = {
        "Close": [150.0, 151.0, 152.0],
        "Volume": [1000000, 1100000, 1200000],
    }
    mock.info = {
        "longName": "Apple Inc.",
        "sector": "Technology",
        "exchange": "NMS",
        "currency": "USD",
        "marketCap": 3000000000000,
    }
    return mock


@pytest.fixture
def mock_gemini_client():
    """Mock Gemini client for LLM testing."""
    mock = Mock()
    mock.extract_transactions.return_value = [
        Mock(
            symbol="AAPL",
            asset_name="Apple Inc.",
            tx_type="buy",
            quantity=10,
            price=150.0,
            currency="USD",
            date="2024-01-15",
            raw_text="Bought 10 shares of AAPL at $150",
        )
    ]
    return mock


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


class MockServerFixture:
    """Mock server fixture for testing server mode CLI operations."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.responses = {}
        self.called_endpoints = []

    def set_response(self, endpoint: str, method: str, response: Dict[str, Any]):
        """Set mock response for endpoint."""
        key = f"{method.upper()}:{endpoint}"
        self.responses[key] = response

    def get_called_endpoints(self):
        """Get list of called endpoints."""
        return self.called_endpoints

    async def request(self, method: str, url: str, **kwargs):
        """Mock request method."""
        endpoint = url.replace(self.base_url, "")
        key = f"{method.upper()}:{endpoint}"
        self.called_endpoints.append(key)

        if key in self.responses:
            response_data = self.responses[key]
            mock_response = Mock()
            mock_response.status_code = response_data.get("status_code", 200)
            mock_response.json.return_value = response_data.get("json", {})
            mock_response.raise_for_status.return_value = None
            return mock_response
        else:
            # Default response
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.json.return_value = {"error": "Not found"}
            return mock_response


@pytest.fixture
def mock_server():
    """Create mock server fixture."""
    return MockServerFixture()


@pytest.fixture(scope="session")
def test_server_port():
    """Get available port for test server."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture
async def test_server(test_app, test_server_port):
    """Start test server for integration tests."""
    import uvicorn
    from multiprocessing import Process
    import time

    def run_server():
        uvicorn.run(
            test_app, host="127.0.0.1", port=test_server_port, log_level="error"
        )

    server_process = Process(target=run_server)
    server_process.start()

    # Wait for server to start
    time.sleep(2)

    yield f"http://127.0.0.1:{test_server_port}"

    server_process.terminate()
    server_process.join()


@pytest.fixture
def cli_runner():
    """Create CLI runner for testing CLI commands."""
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def mock_config():
    """Mock configuration for testing."""
    from portf_manager.config import PortfolioConfig

    config = Mock(spec=PortfolioConfig)
    config.is_local_mode = True
    config.is_server_mode = False
    config.db_path = "test.db"
    config.server_url = None
    config.api_key = None
    config.debug = True

    return config


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables for testing."""
    test_env = {
        "PORTF_DATABASE_URL": "sqlite:///test.db",
        "PORTF_SECRET_KEY": "test-secret-key",
        "PORTF_ENVIRONMENT": "testing",
        "PORTF_LOG_LEVEL": "DEBUG",
        "GEMINI_API_KEY": "test-gemini-key",
    }

    for key, value in test_env.items():
        monkeypatch.setenv(key, value)

    return test_env


# Performance and load testing fixtures
@pytest.fixture
def performance_data():
    """Generate performance test data."""
    return {
        "assets": [
            {
                "symbol": f"TEST{i:03d}",
                "name": f"Test Company {i}",
                "asset_type": "stock",
                "currency": "USD",
            }
            for i in range(100)
        ],
        "transactions": [
            {
                "asset_id": i % 100 + 1,
                "transaction_type": "buy" if i % 2 == 0 else "sell",
                "quantity": float(i * 10 + 1),
                "price": float(100 + i * 0.5),
                "transaction_date": (date.today() - timedelta(days=i)).isoformat(),
            }
            for i in range(1000)
        ],
    }


# Async context managers for testing
class AsyncContextManager:
    """Helper for testing async context managers."""

    def __init__(self, return_value=None):
        self.return_value = return_value
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True


@pytest.fixture
def async_context_manager():
    """Create async context manager for testing."""
    return AsyncContextManager
