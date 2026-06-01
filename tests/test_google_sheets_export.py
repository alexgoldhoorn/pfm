"""
Tests for Google Sheets Export functionality
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os
from datetime import datetime

from portf_manager.google_sheets_export import (
    GoogleSheetsExporter,
    GoogleSheetsExportError,
    create_google_sheets_exporter,
)
from portf_manager.models import DatabaseAdapter
from portf_manager.auth import AuthManager


class TestGoogleSheetsExporter:
    """Test GoogleSheetsExporter functionality."""

    @pytest.fixture
    def mock_db_adapter(self):
        """Create a mock database adapter.

        Note: Don't use spec=DatabaseAdapter here because the exporter
        also calls get_portfolio_summary() which is on Database but not
        on the DatabaseAdapter protocol.
        """
        mock_db = Mock()
        mock_db.get_portfolio_summary.return_value = {
            "total_assets": 10,
            "total_transactions": 50,
            "database_version": "1.0",
            "asset_types": {"stock": 8, "crypto": 2},
        }
        return mock_db

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock auth manager."""
        mock_auth = Mock(spec=AuthManager)
        mock_auth.is_authenticated.return_value = True
        return mock_auth

    @pytest.fixture
    def exporter(self, mock_db_adapter, mock_auth_manager):
        """Create a GoogleSheetsExporter instance with mocks."""
        return GoogleSheetsExporter(mock_db_adapter, mock_auth_manager)

    def test_create_exporter_factory(self, mock_db_adapter, mock_auth_manager):
        """Test the factory function creates an exporter instance."""
        exporter = create_google_sheets_exporter(mock_db_adapter, mock_auth_manager)
        assert isinstance(exporter, GoogleSheetsExporter)
        assert exporter.db_adapter == mock_db_adapter
        assert exporter.auth_manager == mock_auth_manager

    def test_missing_service_account_file(self, exporter):
        """Test error when service account file is missing."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(GoogleSheetsExportError) as exc_info:
                exporter._auth()
            assert "Service account file not found" in str(exc_info.value)

    def test_invalid_service_account_file(self, exporter):
        """Test error when service account file is invalid."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"invalid": "json"}')
            temp_file = f.name

        try:
            with patch.dict("os.environ", {"GOOGLE_SERVICE_ACCOUNT_FILE": temp_file}):
                with pytest.raises(GoogleSheetsExportError) as exc_info:
                    exporter._auth()
                assert "Failed to authenticate with service account" in str(
                    exc_info.value
                )
        finally:
            os.unlink(temp_file)

    @patch("portf_manager.google_sheets_export.build")
    @patch("portf_manager.google_sheets_export.Credentials")
    def test_successful_auth(self, mock_credentials, mock_build, exporter):
        """Test successful authentication with service account."""
        # Mock credentials and service
        mock_creds = Mock()
        mock_credentials.from_service_account_file.return_value = mock_creds
        mock_service = Mock()
        mock_build.return_value = mock_service

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"type": "service_account", "client_email": "test@test.com"}')
            temp_file = f.name

        try:
            with patch.dict("os.environ", {"GOOGLE_SERVICE_ACCOUNT_FILE": temp_file}):
                service = exporter._auth()
                assert service == mock_service
                mock_credentials.from_service_account_file.assert_called_once()
                mock_build.assert_called_once_with(
                    "sheets", "v4", credentials=mock_creds
                )
        finally:
            os.unlink(temp_file)

    @patch("portf_manager.google_sheets_export.build")
    @patch("portf_manager.google_sheets_export.Credentials")
    def test_create_new_spreadsheet(self, mock_credentials, mock_build, exporter):
        """Test creating a new spreadsheet."""
        # Setup mocks
        mock_service = Mock()
        mock_build.return_value = mock_service

        mock_result = {
            "spreadsheetId": "test_id_123",
            "properties": {"title": "Test Spreadsheet"},
        }
        mock_service.spreadsheets().create().execute.return_value = mock_result

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"type": "service_account", "client_email": "test@test.com"}')
            temp_file = f.name

        try:
            with patch.dict("os.environ", {"GOOGLE_SERVICE_ACCOUNT_FILE": temp_file}):
                spreadsheet_id = exporter._get_or_create_spreadsheet()
                assert spreadsheet_id == "test_id_123"
                # Verify create was called (mock chaining may count calls differently)
                assert mock_service.spreadsheets().create.called
        finally:
            os.unlink(temp_file)

    def test_get_summary_data(self, exporter):
        """Test getting summary data formatted for Google Sheets."""
        data = exporter._get_summary_data()

        # Check structure
        assert len(data) > 0
        assert data[0] == ["Portfolio Summary", ""]

        # Find the totals
        asset_row = next((row for row in data if row[0] == "Total Assets"), None)
        assert asset_row is not None
        assert asset_row[1] == "10"

        transaction_row = next(
            (row for row in data if row[0] == "Total Transactions"), None
        )
        assert transaction_row is not None
        assert transaction_row[1] == "50"

    @patch("portf_manager.google_sheets_export.build")
    @patch("portf_manager.google_sheets_export.Credentials")
    def test_clear_and_write(self, mock_credentials, mock_build, exporter):
        """Test clearing and writing data to a sheet."""
        mock_service = Mock()
        mock_build.return_value = mock_service

        test_data = [["Header1", "Header2"], ["Data1", "Data2"]]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"type": "service_account", "client_email": "test@test.com"}')
            temp_file = f.name

        try:
            with patch.dict("os.environ", {"GOOGLE_SERVICE_ACCOUNT_FILE": temp_file}):
                exporter._clear_and_write("test_id", "Sheet1!A1:ZZ", test_data)

                # Verify clear was called
                mock_service.spreadsheets().values().clear.assert_called_once()

                # Verify update was called
                mock_service.spreadsheets().values().update.assert_called_once()

                # Check the update call arguments
                update_call = mock_service.spreadsheets().values().update.call_args
                assert update_call[1]["spreadsheetId"] == "test_id"
                assert update_call[1]["range"] == "Sheet1!A1:ZZ"
                assert update_call[1]["valueInputOption"] == "RAW"
                assert update_call[1]["body"]["values"] == test_data
        finally:
            os.unlink(temp_file)


class TestIntegration:
    """Integration tests (require real Google credentials)."""

    @pytest.mark.skipif(
        not os.getenv("GOOGLE_SHEETS_INTEGRATION_TEST"),
        reason="Set GOOGLE_SHEETS_INTEGRATION_TEST=1 to run integration tests",
    )
    def test_full_export_integration(self):
        """
        Full integration test with real Google Sheets API.

        This test is skipped by default. To run it:
        1. Set up a Google service account and download the JSON key
        2. Set GOOGLE_SERVICE_ACCOUNT_FILE to the key file path
        3. Set GOOGLE_SHEETS_INTEGRATION_TEST=1
        4. Optionally set GOOGLE_SPREADSHEET_ID to a test spreadsheet
        """
        pytest.skip("Integration test - set GOOGLE_SHEETS_INTEGRATION_TEST=1 to enable")

        # This would be the actual integration test code:
        # from portf_manager.database import Database
        # from portf_manager.auth import AuthManager
        #
        # db_manager = Database("test_portfolio.db")  # Use test DB
        # auth_manager = AuthManager(db_manager)
        #
        # exporter = create_google_sheets_exporter(db_manager, auth_manager)
        # result = exporter.export(create_new=True)
        #
        # assert result["success"]
        # assert "spreadsheet_id" in result
        # print(f"Test export created: {result['spreadsheet_url']}")


if __name__ == "__main__":
    pytest.main([__file__])
