"""
Coinbase CSV transaction parser.

This module provides functionality to parse Coinbase transaction CSV exports
and convert them to LLMTransaction objects for import into the portfolio system.
"""

import csv
from datetime import datetime
from io import StringIO
from typing import List, Tuple, Optional
from dataclasses import dataclass

from ..llm_types import LLMTransaction


@dataclass
class CoinbaseParseResult:
    """Result of parsing Coinbase CSV data."""

    importable: List[LLMTransaction]
    skipped: List[Tuple[str, str]]  # (transaction_type, reason)


class CoinbaseCSVParser:
    """Parser for Coinbase CSV transaction exports."""

    # Transaction types that should be imported as trades
    IMPORTABLE_TYPES = {"Advanced Trade Buy", "Advanced Trade Sell", "Buy", "Sell"}

    # Transaction types that represent income (imported as buy at market price)
    INCOME_TYPES = {"Staking Income", "Incentives Rewards Payout"}

    # Transaction types that should be logged but not imported
    REFERENCE_TYPES = {
        "Send",
        "Receive",
        "Deposit",
        "Withdrawal",
        "Retail Staking Transfer",
    }

    def parse_csv_content(self, csv_content: str) -> CoinbaseParseResult:
        """
        Parse Coinbase CSV content into LLMTransaction objects.

        Args:
            csv_content: Raw CSV content from Coinbase export

        Returns:
            CoinbaseParseResult with importable transactions and skipped entries
        """
        # Split lines and skip the first two header lines
        lines = csv_content.strip().split("\n")

        if len(lines) < 3:
            raise ValueError("Invalid Coinbase CSV format: insufficient lines")

        # Skip first line (usually "Transactions")
        # Skip second line (user info line)
        # Start from third line which should be the CSV header
        csv_data_lines = lines[2:]
        csv_data = "\n".join(csv_data_lines)

        importable = []
        skipped = []

        try:
            # Parse CSV using DictReader
            csv_reader = csv.DictReader(StringIO(csv_data))

            for row in csv_reader:
                try:
                    parsed_transaction = self._parse_transaction_row(row)
                    if parsed_transaction:
                        importable.append(parsed_transaction)
                    else:
                        # Transaction was skipped
                        tx_type = row.get("Transaction Type", "Unknown")
                        skipped.append((tx_type, self._get_skip_reason(tx_type)))

                except Exception as e:
                    # Log individual row parsing errors
                    tx_type = row.get("Transaction Type", "Unknown")
                    skipped.append((tx_type, f"Parse error: {str(e)}"))

        except Exception as e:
            raise ValueError(f"Failed to parse CSV data: {str(e)}")

        return CoinbaseParseResult(importable=importable, skipped=skipped)

    def _parse_transaction_row(self, row: dict) -> Optional[LLMTransaction]:
        """
        Parse a single CSV row into an LLMTransaction.

        Args:
            row: Dictionary representing a CSV row

        Returns:
            LLMTransaction if the row should be imported, None if should be skipped
        """
        tx_type = row.get("Transaction Type", "").strip()

        # Check if this is an income type (staking, rewards)
        is_income = tx_type in self.INCOME_TYPES

        # Skip non-importable and non-income transaction types
        if tx_type not in self.IMPORTABLE_TYPES and not is_income:
            return None

        # Extract required fields
        asset = row.get("Asset", "").strip()
        quantity_str = row.get("Quantity Transacted", "0").strip()
        price_currency = row.get("Price Currency", "EUR").strip()
        total_str = row.get("Total (inclusive of fees and/or spread)", "0").strip()
        price_at_transaction_str = row.get("Price at Transaction", "0").strip()
        timestamp_str = row.get("Timestamp", "").strip()

        # Validate required fields
        if (
            not asset
            or not quantity_str
            or not total_str
            or not price_at_transaction_str
        ):
            raise ValueError(f"Missing required fields in row: {row}")

        # Parse quantity (remove any negative signs for sells)
        try:
            quantity = abs(float(quantity_str))
        except ValueError:
            raise ValueError(f"Invalid quantity: {quantity_str}")

        # Parse total amount (remove currency symbols and convert)
        try:
            # Remove currency symbols and convert
            total_clean = total_str.replace("€", "").replace("$", "").replace(",", "")
            total_amount = abs(float(total_clean))
        except ValueError:
            raise ValueError(f"Invalid total amount: {total_str}")

        # Parse the Coinbase's per-unit price for reference
        try:
            # Remove currency symbols and convert
            price_clean = (
                price_at_transaction_str.replace("€", "")
                .replace("$", "")
                .replace(",", "")
            )
            coinbase_unit_price = float(price_clean)  # Price per 1 crypto unit
        except ValueError:
            raise ValueError(
                f"Invalid price at transaction: {price_at_transaction_str}"
            )

        # For income types, use market price as cost basis (no fees)
        # For trades, calculate from total (includes fees)
        if is_income:
            unit_price = coinbase_unit_price
            fees_amount = 0.0
        else:
            # FIX: Calculate the correct unit price from total_amount / quantity
            # This is the actual price per unit that was paid/received
            if quantity <= 0:
                raise ValueError("Quantity must be positive for unit price calculation")

            unit_price = total_amount / quantity

            # Calculate fees for reference (difference between what Coinbase says vs actual)
            theoretical_subtotal = coinbase_unit_price * quantity
            fees_amount = abs(total_amount - theoretical_subtotal)

        # Parse timestamp to date format
        try:
            # Parse Coinbase timestamp format: "2025-08-25 20:34:04 UTC"
            dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S UTC")
            iso_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid timestamp format: {timestamp_str}")

        # Determine transaction type (buy/sell)
        # Income types are treated as buy (cost basis = market price at receipt)
        if is_income:
            llm_tx_type = "buy"
        else:
            llm_tx_type = "buy" if "Buy" in tx_type else "sell"

        # Create raw text for traceability
        if is_income:
            raw_text = f"{tx_type}: {quantity} {asset} @ {unit_price:.4f} {price_currency} (income, cost basis at market price) - {timestamp_str}"
        else:
            raw_text = f"{tx_type}: {quantity} {asset} for {total_str} total (calculated unit price {unit_price:.4f} {price_currency}, fees ~{fees_amount:.4f}) - {timestamp_str}"

        # Add source info to description for income types
        description_suffix = f" [{tx_type}]" if is_income else ""

        return LLMTransaction(
            tx_type=llm_tx_type,
            symbol=asset,
            asset_name=f"{asset} (Crypto){description_suffix}",
            quantity=quantity,
            price=unit_price,
            date=iso_date,
            currency=price_currency,
            raw_text=raw_text,
            fees=fees_amount,
        )

    def _get_skip_reason(self, tx_type: str) -> str:
        """Get a human-readable reason for skipping a transaction type."""
        if tx_type in self.REFERENCE_TYPES:
            return "Reference only (transfer/deposit)"
        else:
            return f"Unsupported transaction type: {tx_type}"


def parse_coinbase_csv(csv_content: str) -> CoinbaseParseResult:
    """
    Convenience function to parse Coinbase CSV content.

    Args:
        csv_content: Raw CSV content from Coinbase export

    Returns:
        CoinbaseParseResult with importable transactions and skipped entries
    """
    parser = CoinbaseCSVParser()
    return parser.parse_csv_content(csv_content)
