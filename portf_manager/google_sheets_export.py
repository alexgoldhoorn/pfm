"""
Google Sheets Export Service for Portfolio Manager

This module provides functionality to export portfolio data to Google Sheets
with three sheets: All Transactions, Tax Report, and Portfolio Summary.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Any

try:
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
except ImportError:
    print("Google API libraries not installed. Please install with:")
    print(
        "pip install google-api-python-client google-auth google-auth-httplib2 google-auth-oauthlib"
    )
    raise

from .csv_export import create_csv_exporter
from .tax_export import TaxReportExporter
from .tax_calculator import TaxCalculator
from .transaction_filter import TransactionFilter
from .models import DatabaseAdapter
from .auth import AuthManager
from .error_handling import PortfolioManagerError, ExitCodes


class GoogleSheetsExportError(PortfolioManagerError):
    """Error during Google Sheets export operation."""

    def __init__(self, message: str):
        super().__init__(message, ExitCodes.NETWORK_ERROR)


class GoogleSheetsExporter:
    """Service for exporting portfolio data to Google Sheets."""

    # Google Sheets API configuration
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    # Sheet names
    TRANSACTIONS_SHEET = "All Transactions"
    TAX_SHEET = "Tax Report"
    SUMMARY_SHEET = "Portfolio Summary"

    def __init__(self, db_adapter: DatabaseAdapter, auth_manager: AuthManager):
        self.db_adapter = db_adapter
        self.auth_manager = auth_manager

        # Initialize exporters
        self.csv_exporter = create_csv_exporter(db_adapter, auth_manager)
        self.tax_exporter = TaxReportExporter()

        # Google Sheets service (will be initialized on first use)
        self._sheets_service = None

    def _auth(self):
        """Authenticate with Google Sheets API and return service object."""
        if self._sheets_service:
            return self._sheets_service

        creds = None
        service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

        if service_account_file and os.path.exists(service_account_file):
            # Use service account authentication
            try:
                creds = Credentials.from_service_account_file(
                    service_account_file, scopes=self.SCOPES
                )
            except Exception as e:
                raise GoogleSheetsExportError(
                    f"Failed to authenticate with service account: {e}"
                )
        else:
            # Fallback to OAuth2 flow
            # Note: This would require setting up OAuth2 credentials
            # For now, we'll raise an error suggesting service account setup
            raise GoogleSheetsExportError(
                "Service account file not found. Please set GOOGLE_SERVICE_ACCOUNT_FILE "
                "environment variable to your Google service account JSON file path."
            )

        try:
            self._sheets_service = build("sheets", "v4", credentials=creds)
            return self._sheets_service
        except Exception as e:
            raise GoogleSheetsExportError(f"Failed to build Google Sheets service: {e}")

    def _get_or_create_spreadsheet(self, spreadsheet_id: Optional[str] = None) -> str:
        """Get existing spreadsheet or create a new one."""
        service = self._auth()

        if spreadsheet_id:
            # Verify the spreadsheet exists and is accessible
            try:
                service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
                return spreadsheet_id
            except Exception as e:
                raise GoogleSheetsExportError(
                    f"Cannot access spreadsheet {spreadsheet_id}: {e}"
                )

        # Create new spreadsheet
        try:
            spreadsheet_body = {
                "properties": {
                    "title": f"Portfolio Export {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                },
                "sheets": [
                    {"properties": {"title": self.TRANSACTIONS_SHEET}},
                    {"properties": {"title": self.TAX_SHEET}},
                    {"properties": {"title": self.SUMMARY_SHEET}},
                ],
            }

            result = service.spreadsheets().create(body=spreadsheet_body).execute()
            new_id = result["spreadsheetId"]
            print(f"📊 Created new spreadsheet: {result['properties']['title']}")
            print(f"🔗 URL: https://docs.google.com/spreadsheets/d/{new_id}/")
            return new_id
        except Exception as e:
            raise GoogleSheetsExportError(f"Failed to create spreadsheet: {e}")

    def _clear_and_write(
        self, spreadsheet_id: str, range_name: str, values: List[List[Any]]
    ):
        """Clear a sheet range and write new data."""
        service = self._auth()

        try:
            # Clear existing data
            service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id, range=range_name
            ).execute()

            # Write new data
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": values},
            ).execute()
        except Exception as e:
            raise GoogleSheetsExportError(f"Failed to write data to {range_name}: {e}")

    def _get_transactions_data(self) -> List[List[Any]]:
        """Get all transactions data formatted for Google Sheets."""
        # Use empty filter to get all transactions
        filter_criteria = TransactionFilter()

        try:
            transactions = self.csv_exporter.filter_service.get_user_transactions(
                filter_criteria
            )
        except Exception as e:
            raise GoogleSheetsExportError(f"Failed to get transactions data: {e}")

        if not transactions:
            return [self.csv_exporter.CSV_HEADERS]

        # Convert to list format for Google Sheets
        data = [self.csv_exporter.CSV_HEADERS]

        for transaction in transactions:
            csv_row = self.csv_exporter._transaction_to_csv_row(transaction)

            # Convert dictionary to list in the same order as headers
            row = [
                str(csv_row.get(header, "")) for header in self.csv_exporter.CSV_HEADERS
            ]
            data.append(row)

        return data

    def _get_tax_data(self) -> List[List[Any]]:
        """Get tax report data formatted for Google Sheets."""
        try:
            # Create tax calculator
            tax_calc = TaxCalculator(self.db_adapter)

            # Get all tax transactions (you might want to filter by year)
            tax_report = tax_calc.calculate_tax_report()

            if not tax_report:
                return [
                    [
                        "Symbol",
                        "Asset Name",
                        "Sell Date",
                        "Sell Quantity",
                        "Sell Price",
                        "Sell Amount",
                        "Purchase Date",
                        "Purchase Price",
                        "Purchase Amount",
                        "Gain/Loss",
                        "Gain/Loss %",
                        "Holding Period (Days)",
                        "Term Type",
                        "Portfolio",
                        "Description",
                    ]
                ]

            # Convert tax report to Google Sheets format
            data = []

            # Add headers
            headers = [
                "Symbol",
                "Asset Name",
                "Sell Date",
                "Sell Quantity",
                "Sell Price",
                "Sell Amount",
                "Purchase Date",
                "Purchase Price",
                "Purchase Amount",
                "Gain/Loss",
                "Gain/Loss %",
                "Holding Period (Days)",
                "Term Type",
                "Portfolio",
                "Description",
            ]
            data.append(headers)

            # Add tax transactions
            for symbol in sorted(tax_report.keys()):
                transactions = tax_report[symbol]
                for tx in sorted(transactions, key=lambda x: x.sell_date):
                    row = [
                        str(tx.symbol),
                        str(tx.asset_name),
                        tx.sell_date.strftime("%Y-%m-%d"),
                        str(float(tx.sell_quantity)),
                        str(float(tx.sell_price)),
                        str(float(tx.sell_amount)),
                        tx.purchase_date.strftime("%Y-%m-%d"),
                        str(float(tx.purchase_price)),
                        str(float(tx.purchase_amount)),
                        str(float(tx.gain_loss)),
                        str(float(tx.gain_loss_percentage)),
                        str(tx.holding_period_days),
                        "Long Term" if tx.is_long_term else "Short Term",
                        str(tx.portfolio_name),
                        str(tx.description),
                    ]
                    data.append(row)

            return data

        except Exception as e:
            # Return just headers if tax calculation fails
            print(f"⚠️  Tax calculation failed: {e}")
            return [
                [
                    "Symbol",
                    "Asset Name",
                    "Sell Date",
                    "Sell Quantity",
                    "Sell Price",
                    "Sell Amount",
                    "Purchase Date",
                    "Purchase Price",
                    "Purchase Amount",
                    "Gain/Loss",
                    "Gain/Loss %",
                    "Holding Period (Days)",
                    "Term Type",
                    "Portfolio",
                    "Description",
                ]
            ]

    def _get_summary_data(self) -> List[List[Any]]:
        """Get portfolio summary data formatted for Google Sheets."""
        try:
            summary = self.db_adapter.get_portfolio_summary()

            # Format summary data for Google Sheets
            data = [
                ["Portfolio Summary", ""],
                ["", ""],
                ["Total Assets", str(summary.get("total_assets", 0))],
                ["Total Transactions", str(summary.get("total_transactions", 0))],
                ["Database Version", str(summary.get("database_version", "Unknown"))],
                ["", ""],
                ["Asset Types Breakdown", ""],
            ]

            # Add asset types
            asset_types = summary.get("asset_types", {})
            for asset_type, count in asset_types.items():
                data.append([f"  {asset_type}", str(count)])

            # Add generation timestamp
            data.extend(
                [
                    ["", ""],
                    ["Report Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                ]
            )

            return data

        except Exception as e:
            # Return minimal data if summary fails
            print(f"⚠️  Portfolio summary failed: {e}")
            return [
                ["Portfolio Summary", ""],
                ["Error", f"Failed to generate summary: {e}"],
                ["Report Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ]

    def export(
        self, spreadsheet_id: Optional[str] = None, create_new: bool = False
    ) -> Dict[str, Any]:
        """
        Export all portfolio data to Google Sheets.

        Args:
            spreadsheet_id: Optional existing spreadsheet ID
            create_new: If True, always create a new spreadsheet

        Returns:
            Dictionary with export results and spreadsheet information
        """
        try:
            # Determine spreadsheet ID
            if create_new:
                final_spreadsheet_id = self._get_or_create_spreadsheet()
            else:
                final_spreadsheet_id = self._get_or_create_spreadsheet(
                    spreadsheet_id or os.getenv("GOOGLE_SPREADSHEET_ID")
                )

            print(f"📊 Exporting to spreadsheet: {final_spreadsheet_id}")

            # Export transactions data
            print("📈 Exporting transactions...")
            transactions_data = self._get_transactions_data()
            self._clear_and_write(
                final_spreadsheet_id,
                f"{self.TRANSACTIONS_SHEET}!A1:ZZ",
                transactions_data,
            )

            # Export tax data
            print("💰 Exporting tax report...")
            tax_data = self._get_tax_data()
            self._clear_and_write(
                final_spreadsheet_id, f"{self.TAX_SHEET}!A1:ZZ", tax_data
            )

            # Export summary data
            print("📋 Exporting portfolio summary...")
            summary_data = self._get_summary_data()
            self._clear_and_write(
                final_spreadsheet_id, f"{self.SUMMARY_SHEET}!A1:ZZ", summary_data
            )

            # Success result
            result = {
                "success": True,
                "message": "Successfully exported to Google Sheets",
                "spreadsheet_id": final_spreadsheet_id,
                "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{final_spreadsheet_id}/",
                "sheets_exported": [
                    f"{self.TRANSACTIONS_SHEET} ({len(transactions_data) - 1} transactions)",
                    f"{self.TAX_SHEET} ({len(tax_data) - 1} tax events)",
                    f"{self.SUMMARY_SHEET} (portfolio overview)",
                ],
            }

            print("✅ Export completed successfully!")
            print(f"🔗 View at: {result['spreadsheet_url']}")

            return result

        except GoogleSheetsExportError:
            # Re-raise our custom errors
            raise
        except Exception as e:
            raise GoogleSheetsExportError(f"Unexpected error during export: {e}")


def create_google_sheets_exporter(
    db_adapter: DatabaseAdapter, auth_manager: AuthManager
) -> GoogleSheetsExporter:
    """
    Factory function to create a GoogleSheetsExporter instance.

    Args:
        db_adapter: Database adapter for data access
        auth_manager: Authentication manager for user context

    Returns:
        GoogleSheetsExporter instance
    """
    return GoogleSheetsExporter(db_adapter, auth_manager)
