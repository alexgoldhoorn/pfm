"""Unit tests for the pfm <-> PDT broker-name mapping."""

from unittest.mock import MagicMock

from openpyxl import load_workbook

from portf_manager.parsers.pdt_brokers import (
    from_pdt,
    load_broker_map,
    parse_broker_map,
    to_pdt,
    unknown_brokers,
)
from portf_manager.parsers.pdt_xlsx_parser import PDTXLSXExporter


class TestParseBrokerMap:
    def test_valid_map(self):
        assert parse_broker_map('{"A Bank": "B Broker"}') == {"A Bank": "B Broker"}

    def test_strips_and_drops_empty_entries(self):
        raw = '{" A Bank ": " B Broker ", "": "X", "C": "", "D": null}'
        assert parse_broker_map(raw) == {"A Bank": "B Broker"}

    def test_malformed_is_empty(self):
        assert parse_broker_map("{not json") == {}
        assert parse_broker_map('["a", "b"]') == {}
        assert parse_broker_map(None) == {}
        assert parse_broker_map("") == {}


class TestLoadBrokerMap:
    def test_reads_setting(self):
        db = MagicMock()
        db.get_setting.return_value = '{"A Bank": "B Broker"}'
        assert load_broker_map(db) == {"A Bank": "B Broker"}
        db.get_setting.assert_called_once_with("pdt_broker_map")

    def test_adapter_without_settings_is_empty(self):
        assert load_broker_map(object()) == {}

    def test_failing_read_is_empty(self):
        db = MagicMock()
        db.get_setting.side_effect = RuntimeError("db gone")
        assert load_broker_map(db) == {}


class TestMapping:
    def test_to_pdt(self):
        m = {"A Bank": "B Broker"}
        assert to_pdt("A Bank", m) == "B Broker"
        assert to_pdt("Other", m) == "Other"
        assert to_pdt(None, m) == ""

    def test_from_pdt_is_case_insensitive(self):
        m = {"A Bank": "B Broker"}
        assert from_pdt("b broker", m) == "A Bank"
        assert from_pdt(" B Broker ", m) == "A Bank"
        assert from_pdt("Other", m) == "Other"
        assert from_pdt(None, m) is None

    def test_unknown_brokers(self):
        valid = ["MyInvestor", "Example Broker"]
        names = ["myinvestor", "Example Broker", "Nope", "", "Nope"]
        assert unknown_brokers(names, valid) == ["Nope"]


class TestXlsxExportUsesMap:
    def test_portfolio_name_is_written_as_mapped_broker(self, tmp_path):
        db = MagicMock()
        db.get_setting.return_value = '{"Example Plan Bank": "Example Broker"}'
        db.get_all_transactions.return_value = [
            {
                "id": 1,
                "asset_id": 10,
                "portfolio_id": 2,
                "transaction_type": "buy",
                "quantity": 3.0,
                "price": 10.0,
                "total_amount": 30.0,
                "fees": 0,
                "tax": 0,
                "currency": "EUR",
                "transaction_date": "2024-08-27",
            }
        ]
        db.get_asset.return_value = {
            "id": 10,
            "symbol": "DE0000000001",
            "name": "Example Corp",
            "asset_type": "stock",
            "exchange": "XETRA Exchange",
            "currency": "EUR",
        }
        db.get_portfolio.return_value = {"id": 2, "name": "Example Plan Bank"}
        db.get_all_bookings.return_value = [
            {
                "id": 1,
                "portfolio_id": 2,
                "date": "2024-02-05",
                "action": "Deposit",
                "amount": 250.0,
                "currency": "EUR",
            }
        ]
        out = tmp_path / "pdt.xlsx"
        PDTXLSXExporter().export(db, str(out))

        wb = load_workbook(out)
        assert wb["Transactions"].cell(row=4, column=1).value == "Example Broker"
        assert wb["Bookings"].cell(row=4, column=1).value == "Example Broker"
