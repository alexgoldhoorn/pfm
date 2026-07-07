"""Tests for the legacy Google Sheets exporter's tax sheet data."""

from unittest.mock import MagicMock

from portf_manager.google_sheets_export import GoogleSheetsExporter


class FakeDB:
    """Minimal db adapter serving one buy + one sell of a fictional stock."""

    def __init__(self):
        self.transactions = [
            {
                "id": 1,
                "transaction_date": "2024-01-10",
                "transaction_type": "buy",
                "quantity": "10",
                "price": "100",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
            {
                "id": 2,
                "transaction_date": "2025-03-15",
                "transaction_type": "sell",
                "quantity": "10",
                "price": "150",
                "symbol": "EXA",
                "portfolio_id": 1,
            },
        ]

    def get_all_transactions(self, user_id=None):
        return self.transactions

    def get_asset_by_symbol(self, symbol):
        return {"name": "Example Corp"}

    def get_portfolio(self, portfolio_id):
        return {"name": "Test Portfolio"}


def test_get_tax_data_returns_realised_lots():
    """_get_tax_data must return the realised lots, not silently just headers."""
    exporter = GoogleSheetsExporter(FakeDB(), MagicMock())
    data = exporter._get_tax_data()

    # Header row plus one realised-lot row for the EXA sell.
    assert len(data) == 2
    assert data[0][0] == "Symbol"
    row = data[1]
    assert row[0] == "EXA"
    assert row[2] == "2025-03-15"
