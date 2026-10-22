#
# Unit and integration tests for the `update-prices` CLI command.
#
# These tests verify:
#   - Correct parsing of CLI arguments.
#   - Mocked yfinance responses for successful and failing API calls.
#   - Graceful handling of invalid symbols.
#   - Accurate creation of database records with correct data.
#
import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock, ANY
from io import StringIO
from datetime import datetime

from portf_manager.cli import PortfolioManagerCLI
from portf_manager.config import PortfolioConfig
from portf_manager.api_client import APIClient, DataNotFoundError


class TestUpdatePricesCLI:
    """Test suite for the update-prices CLI command."""

    def setup_method(self):
        """Set up the test environment before each test."""
        self.temp_dir = tempfile.mkdtemp()
        # The db_path is now a string, not a Path object
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = PortfolioConfig(db_path=self.db_path)
        self.cli = PortfolioManagerCLI(self.config)

        # Create a test user
        self.user_id = self.cli.db_manager.create_user(
            username="testuser",
            email="test@example.com",
            password_hash="testhash",
            salt="testsalt",
            full_name="Test User",
        )

        # Mock authentication
        mock_auth_manager = MagicMock()
        mock_auth_manager.is_authenticated.return_value = True
        mock_auth_manager.get_current_user.return_value = {"id": self.user_id}
        self.cli.auth_manager = mock_auth_manager

        # Add a test asset
        self.cli.db_manager.create_asset(
            symbol="AAPL",
            name="Apple Inc.",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="An American multinational technology company.",
        )

    def teardown_method(self):
        """Clean up the test environment after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_success(self, mock_get_client):
        """Test successful price update for a single symbol."""
        # Mock the API client and its fetch_latest_prices method
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices(symbols=["AAPL"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Successfully updated: 1 assets" in output

        # Verify that insert_price_record was called correctly
        self.cli.db_manager.insert_price_record.assert_called_once_with(
            symbol="AAPL",
            price=150.0,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_uses_ticker_when_set(self, mock_get_client):
        """An asset with a Yahoo ticker (v18 assets.ticker) is fetched by that
        ticker, and the returned price is stored under the asset's ISIN symbol."""
        # A fund whose ISIN Yahoo can't price, but whose exchange listing can.
        self.cli.db_manager.create_asset(
            symbol="LU0000000099",
            name="Example Global Bond Fund",
            asset_type="index",
            currency="EUR",
        )
        # Set the Yahoo ticker (create_asset has no ticker param).
        with self.cli.db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE assets SET ticker = ? WHERE symbol = ?",
                ("LU0000000099.LU", "LU0000000099"),
            )
            conn.commit()

        # The batch fetch is keyed by the Yahoo ticker, not the ISIN.
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"LU0000000099.LU": 125.50}
        mock_get_client.return_value = mock_api_client
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO):
            self.cli.update_prices(symbols=["LU0000000099"])

        # fetch_latest_prices was asked for the ticker, not the ISIN.
        requested = mock_api_client.fetch_latest_prices.call_args[0][0]
        assert "LU0000000099.LU" in requested
        assert "LU0000000099" not in requested

        # The price is stored under the DB symbol (the ISIN).
        self.cli.db_manager.insert_price_record.assert_called_once_with(
            symbol="LU0000000099",
            price=125.50,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_invalid_symbol(self, mock_get_client):
        """Test price update with an invalid symbol."""
        # Mock the API client to return an empty dict for the invalid symbol
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.side_effect = DataNotFoundError(
            "Invalid Ticker"
        )
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices(symbols=["INVALID"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "No valid assets found" in output

        # Verify that insert_price_record was not called
        self.cli.db_manager.insert_price_record.assert_not_called()

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_partial_success(self, mock_get_client):
        """Test price update with a mix of valid and invalid symbols."""
        # Add another asset
        self.cli.db_manager.create_asset(
            symbol="GOOG",
            name="Google LLC",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="An American multinational technology company.",
        )

        # Mock the API client to return a price for one symbol but not the other
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices(symbols=["AAPL", "GOOG"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Successfully updated: 1 assets" in output
        assert "Skipped (no data): 1 assets" in output
        assert "GOOG" in output

        # Verify that insert_price_record was called for the valid symbol
        self.cli.db_manager.insert_price_record.assert_called_once_with(
            symbol="AAPL",
            price=150.0,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_no_symbols_provided(self, mock_get_client):
        """Test price update when no symbols are provided (updates all assets)."""
        # Add another asset
        self.cli.db_manager.create_asset(
            symbol="GOOG",
            name="Google LLC",
            asset_type="stock",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            description="An American multinational technology company.",
        )

        # Mock the API client to return prices for all assets
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {
            "AAPL": 150.0,
            "GOOG": 2800.0,
        }
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record method
        self.cli.db_manager.insert_price_record = MagicMock()

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.cli.update_prices()

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Successfully updated: 2 assets" in output

        # Verify that insert_price_record was called for both symbols
        assert self.cli.db_manager.insert_price_record.call_count == 2
        self.cli.db_manager.insert_price_record.assert_any_call(
            symbol="AAPL",
            price=150.0,
            fetched_ts=ANY,
            source="yfinance",
        )
        self.cli.db_manager.insert_price_record.assert_any_call(
            symbol="GOOG",
            price=2800.0,
            fetched_ts=ANY,
            source="yfinance",
        )

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_db_record_creation(self, mock_get_client):
        """
        Verify correct DB records creation with accurate created_at, price_date, price_type, source.
        """
        # Mock the API client to return a successful response
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 175.0}
        mock_get_client.return_value = mock_api_client

        with patch("sys.stdout", new_callable=StringIO):
            self.cli.update_prices(symbols=["AAPL"])

        # Check the database directly to verify the created record
        db = self.cli.db_manager
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT asset_id, price, source, price_date, created_at, price_type FROM prices WHERE asset_id = (SELECT id FROM assets WHERE symbol = 'AAPL')"
            )
            record = cursor.fetchone()

        assert record is not None, "Price record was not created in the database"
        # Fetches the results as a dictionary
        record_dict = dict(record)
        assert record_dict["price"] == 175.0
        assert record_dict["source"] == "yfinance"
        assert record_dict["price_date"] == datetime.now().strftime("%Y-%m-%d")
        assert record_dict["price_type"] == "close"

        # Verify created_at is a valid timestamp and is recent
        created_at_datetime = datetime.fromisoformat(record_dict["created_at"])
        assert (datetime.now() - created_at_datetime).total_seconds() < 5

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_db_error(self, mock_get_client):
        """Test graceful handling of a database error during price insertion."""
        # Mock the API client to return a successful response
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_get_client.return_value = mock_api_client

        # Mock the database manager's insert_price_record to raise an exception
        self.cli.db_manager.insert_price_record = MagicMock(
            side_effect=Exception("DB error")
        )

        # A DB write failure is a real error: update_prices exits non-zero so
        # the cron wrapper alerts instead of silently reporting success.
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                self.cli.update_prices(symbols=["AAPL"])

        # Verify the output
        output = mock_stdout.getvalue()
        assert "Failed (database errors): 1 assets" in output
        assert "AAPL" in output

        # Verify that insert_price_record was called
        self.cli.db_manager.insert_price_record.assert_called_once()

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_corrects_currency_mismatch(self, mock_get_client):
        """An asset stored with the wrong currency (e.g. EUR-labeled but
        actually trading in USD, like the MyInvestor IJPA.L bug) gets its
        `currency` field self-corrected during the price update."""
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_api_client.get_quote_currency.return_value = "USD"
        mock_get_client.return_value = mock_api_client

        # Test asset was created with currency="USD" in setup_method already
        # matching, so force a mismatch by relabeling it EUR first.
        asset = self.cli.db_manager.get_asset_by_symbol("AAPL")
        self.cli.db_manager.update_asset(asset["id"], currency="EUR")

        with patch("sys.stdout", new_callable=StringIO):
            self.cli.update_prices(symbols=["AAPL"])

        corrected = self.cli.db_manager.get_asset_by_symbol("AAPL")
        assert corrected["currency"] == "USD"

    @patch("portf_manager.api_client.get_client")
    def test_update_prices_no_correction_when_currency_matches(self, mock_get_client):
        """No spurious currency change when yfinance agrees with the DB."""
        mock_api_client = MagicMock(spec=APIClient)
        mock_api_client.fetch_latest_prices.return_value = {"AAPL": 150.0}
        mock_api_client.get_quote_currency.return_value = "USD"
        mock_get_client.return_value = mock_api_client

        with patch("sys.stdout", new_callable=StringIO):
            self.cli.update_prices(symbols=["AAPL"])

        unchanged = self.cli.db_manager.get_asset_by_symbol("AAPL")
        assert unchanged["currency"] == "USD"


class TestUpdatePricesServerMode:
    """In server mode the CLI never runs the updater itself.

    The updater needs the real Database (the price guard reads and writes
    app_settings and app_logs); PortfolioHTTPClient is not one, and feeding it
    in made every asset fail with ``'PortfolioHTTPClient' object has no
    attribute 'get_setting'`` from 2026-10-18 on. The CLI must delegate to the
    server's trigger endpoint, wait for it, and report that run.
    """

    TRIGGER = "/api/v1/analytics/trigger-price-update"
    STATUS = "/api/v1/analytics/price-update-status"
    RUNS = "/api/v1/analytics/update-runs"

    def _run(self, runs):
        return {
            "started_at": "2026-10-22T20:00:01",
            "source": "cron",
            "updated_count": 62,
            "skipped_count": 1,
            "error_count": 0,
            "skipped_symbols": ["EXAMPLE BANK"],
            "error_symbols": [],
            "api_errors": [],
            **runs,
        }

    def _cli(self, monkeypatch, run=None, trigger=None, running=(True, False)):
        config = PortfolioConfig(
            server_url="http://127.0.0.1:1", api_key="test-key", db_path=None
        )
        cli = PortfolioManagerCLI(config)
        calls = []
        pending = list(running)

        def fake_request(method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            if endpoint == self.TRIGGER:
                if isinstance(trigger, Exception):
                    raise trigger
                return {"status": "started", "started_at": "2026-10-22T20:00:00"}
            if endpoint == self.STATUS:
                return {"running": pending.pop(0) if pending else False}
            if endpoint == self.RUNS:
                return {"runs": [run] if run else []}
            raise AssertionError(f"unexpected request {method} {endpoint}")

        monkeypatch.setattr(cli.http_client, "_make_request", fake_request)

        def never(*args, **kwargs):
            raise AssertionError("run_price_update must not run in server mode")

        monkeypatch.setattr(
            "portf_manager.services.price_updater.run_price_update", never
        )
        monkeypatch.setattr("time.sleep", lambda seconds: None)
        return cli, calls

    def test_delegates_to_the_server_and_reports_that_run(self, monkeypatch):
        cli, calls = self._cli(monkeypatch, run=self._run({}))

        with patch("sys.stdout", new_callable=StringIO) as out:
            cli.update_prices()

        assert ("POST", self.TRIGGER) == calls[0][:2]
        assert calls[0][2].get("params", {}).get("source") == "cron"
        assert [c[1] for c in calls].count(self.STATUS) == 2
        text = out.getvalue()
        assert "Successfully updated: 62 assets" in text
        assert "EXAMPLE BANK" in text

    def test_symbols_are_forwarded_as_a_manual_run(self, monkeypatch):
        cli, calls = self._cli(
            monkeypatch, run=self._run({"updated_count": 2, "skipped_count": 0})
        )
        cli.http_client.get_asset_by_symbol = lambda s: {"id": 1, "symbol": s}

        with patch("sys.stdout", new_callable=StringIO):
            cli.update_prices(symbols=["exa", "oth"])

        params = calls[0][2]["params"]
        assert params["symbols"] == ["EXA", "OTH"] and params["source"] == "manual"

    def test_a_run_with_errors_exits_non_zero(self, monkeypatch):
        failed = self._run(
            {
                "updated_count": 0,
                "error_count": 62,
                "error_symbols": ["ADA", "BTC"],
                "api_errors": ["DB error for ADA: boom"],
            }
        )
        cli, _ = self._cli(monkeypatch, run=failed)

        with patch("sys.stdout", new_callable=StringIO) as out:
            with pytest.raises(SystemExit) as exc:
                cli.update_prices()

        assert exc.value.code == 1
        assert "Failed (database errors): 2 assets" in out.getvalue()

    def test_an_update_already_running_exits_non_zero(self, monkeypatch):
        busy = RuntimeError("Server error (409): Price update already in progress")
        cli, calls = self._cli(monkeypatch, trigger=busy)

        with patch("sys.stdout", new_callable=StringIO) as out:
            with pytest.raises(SystemExit) as exc:
                cli.update_prices()

        assert exc.value.code == 1
        assert "already in progress" in out.getvalue()
        assert [c[1] for c in calls] == [self.TRIGGER]

    def test_no_run_recorded_after_the_wait_is_an_error(self, monkeypatch):
        # The server thread died before writing its run record: the CLI must
        # not report the previous night's run as tonight's.
        stale = self._run({"started_at": "2026-10-21T20:00:01"})
        cli, _ = self._cli(monkeypatch, run=stale)

        with patch("sys.stdout", new_callable=StringIO) as out:
            with pytest.raises(SystemExit) as exc:
                cli.update_prices()

        assert exc.value.code == 1
        assert "no run record" in out.getvalue().lower()
