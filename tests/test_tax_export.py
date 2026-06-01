import unittest
import tempfile
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from portf_manager.tax_export import TaxReportExporter, generate_tax_report_filename
from portf_manager.tax_calculator import TaxTransaction


class TestTaxReportExporter(unittest.TestCase):

    def setUp(self):
        self.exporter = TaxReportExporter()
        self.sample_transaction = TaxTransaction(
            symbol="AAPL",
            asset_name="Apple Inc.",
            sell_date=date(2025, 3, 15),
            sell_quantity=Decimal("8"),
            sell_price=Decimal("150"),
            sell_amount=Decimal("1200"),
            purchase_date=date(2023, 12, 1),
            purchase_price=Decimal("100"),
            purchase_amount=Decimal("800"),
            gain_loss=Decimal("400"),
            holding_period_days=470,
            is_long_term=True,
            sell_transaction_id=3,
            buy_transaction_id=1,
            portfolio_name="Growth Portfolio",
            description="Test transaction",
        )

    def test_export_tax_report_basic(self):
        """Test basic tax report export functionality."""
        tax_report = {"AAPL": [self.sample_transaction]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            output_file = tmp.name

        try:
            result_path = self.exporter.export_tax_report(tax_report, output_file)
            self.assertEqual(result_path, output_file)

            # Verify file exists and has content
            with open(output_file, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Check header
            self.assertEqual(rows[0][0], "Symbol")
            self.assertEqual(rows[0][1], "Asset Name")

            # Check data row
            self.assertEqual(rows[1][0], "AAPL")
            self.assertEqual(rows[1][1], "Apple Inc.")
            self.assertEqual(rows[1][2], "2025-03-15")
            self.assertEqual(float(rows[1][3]), 8.0)
            self.assertEqual(float(rows[1][4]), 150.0)
            self.assertEqual(float(rows[1][5]), 1200.0)
            self.assertEqual(rows[1][6], "2023-12-01")
            self.assertEqual(float(rows[1][7]), 100.0)
            self.assertEqual(float(rows[1][8]), 800.0)
            self.assertEqual(float(rows[1][9]), 400.0)
            self.assertEqual(rows[1][12], "Long Term")

        finally:
            Path(output_file).unlink(missing_ok=True)

    def test_export_tax_report_with_summary(self):
        """Test tax report export with summary section."""
        tax_report = {"AAPL": [self.sample_transaction]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            output_file = tmp.name

        try:
            self.exporter.export_tax_report(
                tax_report, output_file, include_summary=True
            )

            with open(output_file, "r") as f:
                content = f.read()

            # Check that summary section exists
            self.assertIn("=== TAX REPORT SUMMARY ===", content)
            self.assertIn("TOTALS", content)
            self.assertIn("FIFO (First In First Out)", content)

        finally:
            Path(output_file).unlink(missing_ok=True)

    def test_export_tax_report_without_summary(self):
        """Test tax report export without summary section."""
        tax_report = {"AAPL": [self.sample_transaction]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            output_file = tmp.name

        try:
            self.exporter.export_tax_report(
                tax_report, output_file, include_summary=False
            )

            with open(output_file, "r") as f:
                content = f.read()

            # Check that summary section doesn't exist
            self.assertNotIn("=== TAX REPORT SUMMARY ===", content)
            self.assertNotIn("TOTALS", content)

        finally:
            Path(output_file).unlink(missing_ok=True)

    def test_export_multiple_symbols(self):
        """Test export with multiple symbols."""
        transaction2 = TaxTransaction(
            symbol="GOOG",
            asset_name="Google Inc.",
            sell_date=date(2025, 4, 10),
            sell_quantity=Decimal("5"),
            sell_price=Decimal("200"),
            sell_amount=Decimal("1000"),
            purchase_date=date(2024, 1, 15),
            purchase_price=Decimal("180"),
            purchase_amount=Decimal("900"),
            gain_loss=Decimal("100"),
            holding_period_days=85,
            is_long_term=False,
            sell_transaction_id=5,
            buy_transaction_id=4,
            portfolio_name="Tech Portfolio",
            description="Another test transaction",
        )

        tax_report = {"AAPL": [self.sample_transaction], "GOOG": [transaction2]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            output_file = tmp.name

        try:
            self.exporter.export_tax_report(tax_report, output_file)

            with open(output_file, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Should have header + 2 data rows (ignore empty rows and summary)
            # Transaction rows have 15 columns, summary rows have 5 columns
            data_rows = [
                row
                for row in rows
                if row and len(row) == 15 and row[0] in ["AAPL", "GOOG"]
            ]
            self.assertEqual(len(data_rows), 2)

            # Check symbols are sorted
            self.assertEqual(data_rows[0][0], "AAPL")
            self.assertEqual(data_rows[1][0], "GOOG")

        finally:
            Path(output_file).unlink(missing_ok=True)


class TestFilenameGeneration(unittest.TestCase):

    def test_generate_tax_report_filename_basic(self):
        """Test basic filename generation."""
        start_date = date(2025, 1, 1)
        end_date = date(2025, 12, 31)

        filename = generate_tax_report_filename(start_date, end_date)

        self.assertIn("tax_report_20250101_20251231", filename)
        self.assertTrue(filename.endswith(".csv"))

    def test_generate_tax_report_filename_with_symbols(self):
        """Test filename generation with symbols."""
        start_date = date(2025, 1, 1)
        end_date = date(2025, 12, 31)
        symbols = ["AAPL", "GOOG"]

        filename = generate_tax_report_filename(start_date, end_date, symbols)

        self.assertIn("tax_report_20250101_20251231_AAPL_GOOG", filename)
        self.assertTrue(filename.endswith(".csv"))

    def test_generate_tax_report_filename_many_symbols(self):
        """Test filename generation with many symbols."""
        start_date = date(2025, 1, 1)
        end_date = date(2025, 12, 31)
        symbols = ["AAPL", "GOOG", "MSFT", "TSLA", "NVDA"]

        filename = generate_tax_report_filename(start_date, end_date, symbols)

        self.assertIn("tax_report_20250101_20251231_AAPL_GOOG_MSFT_plus2more", filename)
        self.assertTrue(filename.endswith(".csv"))


if __name__ == "__main__":
    unittest.main()
