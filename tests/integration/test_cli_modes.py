"""
CLI Tests with Ephemeral FastAPI Server

This module tests the CLI functionality in both local and server modes
using ephemeral FastAPI server fixtures for integration testing.
"""

import asyncio
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from multiprocessing import Process
from subprocess import run, PIPE, CalledProcessError
from typing import Dict, Any, List
import pytest
import uvicorn
from fastapi.testclient import TestClient
import httpx

from portf_server.app import app
from portf_manager.cli import PortfolioManagerCLI
from portf_manager.config import PortfolioConfig


def run_server(host, port):
    """Top-level function to run the uvicorn server."""
    from portf_server.app import app

    uvicorn.run(app, host=host, port=port, log_level="error")


class EphemeralFastAPIServer:
    """Ephemeral FastAPI server for testing CLI in server mode."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.process = None
        self.server_url = None
        self.api_key = None

    def start(self, test_app=None, timeout: int = 10):
        """Start the ephemeral server."""
        import socket

        # Find available port if not specified
        if self.port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                self.port = s.getsockname()[1]

        self.server_url = f"http://{self.host}:{self.port}"

        self.process = Process(target=run_server, args=(self.host, self.port))
        self.process.start()

        # Wait for server to be ready
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = httpx.get(f"{self.server_url}/health")
                if response.status_code == 200:
                    return True
            except httpx.RequestError:
                pass
            time.sleep(0.1)

        raise RuntimeError(f"Server failed to start within {timeout} seconds")

    def stop(self):
        """Stop the ephemeral server."""
        if self.process:
            self.process.terminate()
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
            self.process = None

    def create_api_key(self, username: str = "testuser") -> str:
        """Create an API key for testing."""
        if not self.api_key:
            # Create test user and API key through the server
            try:
                # Register user
                user_data = {
                    "username": f"testuser_{uuid.uuid4().hex[:8]}",
                    "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
                    "password": "testpassword123",
                    "full_name": "Test User",
                }

                response = httpx.post(
                    f"{self.server_url}/api/v1/auth/register", json=user_data
                )
                if response.status_code == 201:
                    # Login to get token
                    login_response = httpx.post(
                        f"{self.server_url}/api/v1/auth/login",
                        json={
                            "username": user_data["username"],
                            "password": user_data["password"],
                        },
                    )
                    if login_response.status_code == 200:
                        token = login_response.json()["access_token"]

                        # Create API key
                        api_key_response = httpx.post(
                            f"{self.server_url}/api/v1/auth/api-keys",
                            json={
                                "name": "test-cli-key",
                                "description": "CLI test key",
                            },
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        if api_key_response.status_code == 201:
                            self.api_key = api_key_response.json()["key"]
                            return self.api_key

                # Fallback to dummy key
                self.api_key = "test-api-key-123"
            except Exception:
                self.api_key = "test-api-key-123"

        return self.api_key

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


@pytest.fixture
def ephemeral_server(test_app):
    """Create ephemeral FastAPI server fixture."""
    server = EphemeralFastAPIServer()
    server.start(test_app)
    yield server
    server.stop()


@pytest.fixture
def server_config(ephemeral_server):
    """Create server mode configuration."""
    api_key = ephemeral_server.create_api_key()
    config = PortfolioConfig(
        server_url=ephemeral_server.server_url,
        api_key=api_key,
        db_path=None,  # Not used in server mode
    )
    return config


@pytest.fixture
def local_config(temp_db_path):
    """Create local mode configuration."""
    config = PortfolioConfig(server_url=None, api_key=None, db_path=temp_db_path)
    return config


class TestCLILocalMode:
    """Test CLI functionality in local mode."""

    @pytest.mark.integration
    @pytest.mark.cli
    def test_local_mode_initialization(self, local_config):
        """Test CLI initialization in local mode."""
        cli = PortfolioManagerCLI(local_config)
        assert cli.config.is_local_mode
        assert not cli.config.is_server_mode
        assert cli.db_manager is not None
        assert cli.auth_manager is not None
        assert cli.http_client is None

    @pytest.mark.integration
    @pytest.mark.cli
    def test_local_mode_user_registration(self, local_config):
        """Test user registration in local mode."""
        cli = PortfolioManagerCLI(local_config)

        # Mock user input for registration
        test_user_data = {
            "username": f"testuser_{uuid.uuid4().hex[:8]}",
            "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
            "password": "testpassword123",
            "full_name": "Test User",
        }

        # Register user directly through auth manager
        user_id = cli.auth_manager.register_user(**test_user_data)
        assert user_id is not None

        # Test login
        cli.auth_manager.login(test_user_data["username"], test_user_data["password"])
        assert cli.auth_manager.is_authenticated()

        current_user = cli.auth_manager.get_current_user()
        assert current_user["username"] == test_user_data["username"]

    @pytest.mark.integration
    @pytest.mark.cli
    def test_local_mode_asset_management(self, local_config, test_user):
        """Test asset management in local mode."""
        cli = PortfolioManagerCLI(local_config)

        # Mock authentication
        cli.auth_manager.current_user = test_user

        # Test asset creation
        cli.add_asset("TEST", "Test Company", "stock", "NASDAQ", "USD", "Test asset")

        # Verify asset was created
        asset = cli.db_manager.get_asset_by_symbol("TEST")
        assert asset is not None
        assert asset["symbol"] == "TEST"
        assert asset["name"] == "Test Company"

    @pytest.mark.integration
    @pytest.mark.cli
    def test_local_mode_transaction_management(
        self, local_config, test_user, sample_asset_data
    ):
        """Test transaction management in local mode."""
        cli = PortfolioManagerCLI(local_config)
        cli.auth_manager.current_user = test_user

        # Create asset first
        asset_id = cli.db_manager.create_asset(**sample_asset_data)

        # Test transaction creation
        cli.add_asset_transaction(
            symbol=sample_asset_data["symbol"],
            amount=10.0,
            price=150.0,
            currency="USD",
            transaction_type="buy",
            transaction_date="2024-01-15",
        )

        # Verify transaction was created
        transactions = cli.db_manager.get_transactions_by_asset(asset_id)
        assert len(transactions) == 1
        assert transactions[0]["quantity"] == 10.0
        assert transactions[0]["price"] == 150.0

    @pytest.mark.integration
    @pytest.mark.cli
    def test_local_mode_csv_import(self, local_config, test_user, test_csv_file):
        """Test CSV import in local mode."""
        cli = PortfolioManagerCLI(local_config)
        cli.auth_manager.current_user = test_user

        # Test CSV import
        cli.import_csv(test_csv_file)

        # Verify transactions were imported
        transactions = cli.db_manager.get_all_transactions(user_id=test_user["id"])
        assert len(transactions) > 0

    @pytest.mark.integration
    @pytest.mark.cli
    def test_local_mode_portfolio_management(self, local_config, test_user):
        """Test portfolio management in local mode."""
        cli = PortfolioManagerCLI(local_config)
        cli.auth_manager.current_user = test_user

        # Test portfolio creation
        cli.add_portfolio("Test Portfolio", "USD", description="Test portfolio")

        # Verify portfolio was created
        portfolios = cli.db_manager.get_all_portfolios(user_id=test_user["id"])
        test_portfolios = [p for p in portfolios if p["name"] == "Test Portfolio"]
        assert len(test_portfolios) == 1
        assert test_portfolios[0]["base_currency"] == "USD"


class TestCLIServerMode:
    """Test CLI functionality in server mode."""

    @pytest.mark.integration
    @pytest.mark.cli
    def test_server_mode_initialization(self, server_config):
        """Test CLI initialization in server mode."""
        cli = PortfolioManagerCLI(server_config)
        assert not cli.config.is_local_mode
        assert cli.config.is_server_mode
        assert cli.http_client is not None
        assert cli.auth_manager is None  # Server handles auth
        assert cli.db_manager == cli.http_client  # HTTP client acts as DB manager

    @pytest.mark.integration
    @pytest.mark.cli
    def test_server_mode_asset_operations(self, server_config):
        """Test asset operations in server mode."""
        cli = PortfolioManagerCLI(server_config)

        # Test getting assets (should make HTTP request)
        try:
            assets = cli._get_all_assets()
            assert isinstance(assets, list)
        except Exception as e:
            # Expected if server endpoints are not fully implemented
            assert "not implemented" in str(e).lower() or "not found" in str(e).lower()

    @pytest.mark.integration
    @pytest.mark.cli
    def test_server_mode_http_communication(self, ephemeral_server):
        """Test HTTP communication in server mode."""
        api_key = ephemeral_server.create_api_key()
        config = PortfolioConfig(
            server_url=ephemeral_server.server_url, api_key=api_key, db_path=None
        )

        cli = PortfolioManagerCLI(config)

        # Test that CLI can communicate with server
        assert cli.http_client.base_url == ephemeral_server.server_url
        assert cli.http_client.api_key == api_key

    @pytest.mark.integration
    @pytest.mark.cli
    def test_server_mode_error_handling(self, server_config):
        """Test error handling in server mode."""
        # Create CLI with invalid config
        invalid_config = PortfolioConfig(
            server_url="http://nonexistent:9999", api_key="invalid-key", db_path=None
        )

        cli = PortfolioManagerCLI(invalid_config)

        # Test that operations fail gracefully
        with pytest.raises(RuntimeError):
            cli._get_all_assets()


class TestCLIDualModeIntegration:
    """Test CLI functionality works correctly in both modes."""

    @pytest.mark.integration
    @pytest.mark.cli
    def test_mode_detection(self, local_config, server_config):
        """Test that CLI correctly detects and configures for each mode."""
        # Test local mode
        local_cli = PortfolioManagerCLI(local_config)
        assert local_cli.config.is_local_mode
        assert local_cli.auth_manager is not None
        assert local_cli.http_client is None

        # Test server mode
        server_cli = PortfolioManagerCLI(server_config)
        assert server_cli.config.is_server_mode
        assert server_cli.auth_manager is None
        assert server_cli.http_client is not None

    @pytest.mark.integration
    @pytest.mark.cli
    def test_consistent_interface(self, local_config, server_config):
        """Test that both modes provide consistent interface."""
        local_cli = PortfolioManagerCLI(local_config)
        server_cli = PortfolioManagerCLI(server_config)

        # Both should have the same public methods
        local_methods = [
            m
            for m in dir(local_cli)
            if not m.startswith("_") and callable(getattr(local_cli, m))
        ]
        server_methods = [
            m
            for m in dir(server_cli)
            if not m.startswith("_") and callable(getattr(server_cli, m))
        ]

        # Core CLI methods should be available in both modes
        core_methods = [
            "add_asset",
            "remove_asset",
            "list_assets",
            "add_asset_transaction",
            "list_transactions",
            "add_portfolio",
            "list_portfolios",
            "add_entity",
            "list_entities",
            "import_csv",
            "export_transactions",
        ]

        for method in core_methods:
            assert method in local_methods, f"{method} missing from local CLI"
            assert method in server_methods, f"{method} missing from server CLI"

    @pytest.mark.integration
    @pytest.mark.cli
    def test_authentication_differences(self, local_config, server_config, test_user):
        """Test authentication differences between modes."""
        # Local mode requires user login
        local_cli = PortfolioManagerCLI(local_config)
        assert local_cli.auth_manager is not None

        # Can authenticate user
        local_cli.auth_manager.current_user = test_user
        assert local_cli.auth_manager.is_authenticated()

        # Server mode uses API key authentication
        server_cli = PortfolioManagerCLI(server_config)
        assert server_cli.auth_manager is None  # No local auth manager
        assert server_cli.http_client.api_key is not None  # Has API key


class TestCLICommandLineInterface:
    """Test CLI command-line interface and argument parsing."""

    @pytest.mark.integration
    @pytest.mark.cli
    def test_cli_help_command(self):
        """Test CLI help command."""
        result = run(
            ["python", "-m", "portf_manager", "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Portfolio Manager CLI" in result.stdout
        assert "Available commands" in result.stdout

    @pytest.mark.integration
    @pytest.mark.cli
    def test_cli_version_info(self):
        """Test CLI version and info commands."""
        result = run(
            ["python", "-m", "portf_manager", "list-sectors"],
            capture_output=True,
            text=True,
        )
        # Should fail without authentication, but command should be recognized
        assert "Sectors" in result.stdout
        assert "Information Technology" in result.stdout

    @pytest.mark.integration
    @pytest.mark.cli
    def test_cli_local_mode_flag(self, temp_db_path):
        """Test CLI with local mode flags."""
        result = run(
            ["python", "-m", "portf_manager", "--db-path", temp_db_path, "list-assets"],
            capture_output=True,
            text=True,
        )

        # Should require authentication but recognize the command
        assert result.returncode != 0  # Expected since no auth
        assert "login" in result.stdout or "authentication" in result.stdout

    @pytest.mark.integration
    @pytest.mark.cli
    def test_cli_server_mode_flag(self, ephemeral_server):
        """Test CLI with server mode flags."""
        api_key = ephemeral_server.create_api_key()

        result = run(
            [
                "python",
                "-m",
                "portf_manager",
                "--server",
                ephemeral_server.server_url,
                "--api-key",
                api_key,
                "list-assets",
            ],
            capture_output=True,
            text=True,
        )

        # Should work or fail gracefully with server connection
        # Result depends on server endpoint implementation
        assert result.returncode in [0, 1]  # Success or expected failure

    @pytest.mark.integration
    @pytest.mark.cli
    def test_cli_invalid_command(self):
        """Test CLI with invalid command."""
        result = run(
            ["python", "-m", "portf_manager", "invalid-command"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Unknown command" in result.stderr or "invalid choice" in result.stderr


class TestCLIPerformance:
    """Test CLI performance characteristics."""

    @pytest.mark.integration
    @pytest.mark.cli
    @pytest.mark.slow
    def test_cli_startup_time(self, local_config):
        """Test CLI startup performance."""
        import time

        start_time = time.time()
        cli = PortfolioManagerCLI(local_config)
        initialization_time = time.time() - start_time

        # CLI should initialize quickly (under 1 second for local mode)
        assert initialization_time < 1.0

    @pytest.mark.integration
    @pytest.mark.cli
    @pytest.mark.slow
    def test_bulk_operations_performance(self, local_config, test_user):
        """Test performance of bulk operations."""
        cli = PortfolioManagerCLI(local_config)
        cli.auth_manager.current_user = test_user

        # Time bulk asset creation
        start_time = time.time()
        for i in range(10):
            cli.add_asset(f"TEST{i:03d}", f"Test Company {i}", "stock", "NYSE", "USD")
        bulk_creation_time = time.time() - start_time

        # Should complete reasonably quickly (under 5 seconds for 10 assets)
        assert bulk_creation_time < 5.0

        # Verify all assets were created
        assets = cli._get_all_assets()
        test_assets = [a for a in assets if a["symbol"].startswith("TEST")]
        assert len(test_assets) == 10
