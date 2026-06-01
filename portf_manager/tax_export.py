#!/usr/bin/env python3
"""
Tax Report CSV Export Module for Portfolio Manager

This module provides functionality to export tax reports (capital gains/losses)
to CSV format for tax filing and analysis purposes.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from decimal import Decimal

from .tax_calculator import TaxTransaction


class TaxReportExporter:
    """
    CSV exporter for tax reports with FIFO capital gains/losses.

    Exports detailed tax transactions and summary data to CSV format
    suitable for tax preparation software and manual tax filing.
    """

    def __init__(self):
        """Initialize tax report exporter."""

    def export_tax_report(
        self,
        tax_report: Dict[str, List[TaxTransaction]],
        output_file: str,
        include_summary: bool = True,
    ) -> str:
        """
        Export tax report to CSV file.

        Args:
            tax_report: Tax report data from TaxCalculator
            output_file: Output CSV file path
            include_summary: Whether to include summary statistics

        Returns:
            Path to created CSV file
        """
        output_path = Path(output_file)

        # Ensure directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)

            # Write header
            self._write_header(writer)

            # Write tax transactions
            transaction_count = 0
            for symbol in sorted(tax_report.keys()):
                transactions = tax_report[symbol]
                for tx in sorted(transactions, key=lambda x: x.sell_date):
                    self._write_transaction_row(writer, tx)
                    transaction_count += 1

            # Write summary if requested
            if include_summary:
                self._write_summary_section(writer, tax_report)

        return str(output_path)

    def _write_header(self, writer: csv.writer):
        """Write CSV header row."""
        header = [
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
        writer.writerow(header)

    def _write_transaction_row(self, writer: csv.writer, tx: TaxTransaction):
        """Write a single tax transaction row."""
        row = [
            tx.symbol,
            tx.asset_name,
            tx.sell_date.strftime("%Y-%m-%d"),
            float(tx.sell_quantity),
            float(tx.sell_price),
            float(tx.sell_amount),
            tx.purchase_date.strftime("%Y-%m-%d"),
            float(tx.purchase_price),
            float(tx.purchase_amount),
            float(tx.gain_loss),
            float(tx.gain_loss_percentage),
            tx.holding_period_days,
            "Long Term" if tx.is_long_term else "Short Term",
            tx.portfolio_name,
            tx.description,
        ]
        writer.writerow(row)

    def _write_summary_section(
        self, writer: csv.writer, tax_report: Dict[str, List[TaxTransaction]]
    ):
        """Write summary section to CSV."""
        # Add empty rows for separation
        writer.writerow([])
        writer.writerow([])
        writer.writerow(["=== TAX REPORT SUMMARY ==="])
        writer.writerow([])

        # Calculate totals
        total_gain_loss = Decimal("0")
        total_long_term = Decimal("0")
        total_short_term = Decimal("0")
        total_transactions = 0

        # Symbol-level summary
        writer.writerow(
            ["Symbol", "Total Gain/Loss", "Long Term", "Short Term", "Transactions"]
        )

        for symbol in sorted(tax_report.keys()):
            transactions = tax_report[symbol]
            symbol_total = sum(tx.gain_loss for tx in transactions)
            symbol_long_term = sum(
                tx.gain_loss for tx in transactions if tx.is_long_term
            )
            symbol_short_term = sum(
                tx.gain_loss for tx in transactions if not tx.is_long_term
            )

            writer.writerow(
                [
                    symbol,
                    float(symbol_total),
                    float(symbol_long_term),
                    float(symbol_short_term),
                    len(transactions),
                ]
            )

            total_gain_loss += symbol_total
            total_long_term += symbol_long_term
            total_short_term += symbol_short_term
            total_transactions += len(transactions)

        # Write totals
        writer.writerow([])
        writer.writerow(
            [
                "TOTALS",
                float(total_gain_loss),
                float(total_long_term),
                float(total_short_term),
                total_transactions,
            ]
        )

        # Write additional summary info
        writer.writerow([])
        writer.writerow(
            ["Report Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        )
        writer.writerow(["Methodology:", "FIFO (First In First Out)"])
        writer.writerow(["Long Term Threshold:", "365 days"])


def generate_tax_report_filename(start_date, end_date, symbols=None):
    """
    Generate a default filename for tax report export.

    Args:
        start_date: Start date for report
        end_date: End date for report
        symbols: Optional list of symbols

    Returns:
        Generated filename string
    """
    # Format dates
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    # Add timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Build filename
    if symbols:
        symbols_str = "_".join(symbols[:3])  # Limit to first 3 symbols
        if len(symbols) > 3:
            symbols_str += f"_plus{len(symbols)-3}more"
        filename = f"tax_report_{start_str}_{end_str}_{symbols_str}_{timestamp}.csv"
    else:
        filename = f"tax_report_{start_str}_{end_str}_{timestamp}.csv"

    return filename
