"""
Coinbase CSV transaction parser.

This module provides functionality to parse Coinbase transaction CSV exports
and convert them to LLMTransaction objects for import into the portfolio system.
"""

import csv
from datetime import datetime
from io import StringIO
from typing import List, Tuple, Optional
from dataclasses import dataclass, field

from ..llm_types import LLMTransaction


@dataclass
class CoinbaseParseResult:
    """Result of parsing Coinbase CSV data."""

    importable: List[LLMTransaction]
    skipped: List[Tuple[str, str]]  # (transaction_type, reason)
    bookings: List[dict] = field(default_factory=list)  # cash deposits/withdrawals


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

    # Cash-movement types. Only count as bookings when the Asset is fiat;
    # the crypto-asset variants are wallet transfers, left in `skipped`.
    DEPOSIT_TYPES = {"Deposit", "Pro Deposit"}
    WITHDRAWAL_TYPES = {"Withdrawal", "Pro Withdrawal"}
    FIAT_CURRENCIES = {
        "EUR",
        "USD",
        "GBP",
        "CHF",
        "SEK",
        "DKK",
        "NOK",
        "JPY",
        "CAD",
        "AUD",
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
        bookings = []

        try:
            # Parse CSV using DictReader
            csv_reader = csv.DictReader(StringIO(csv_data))

            for row in csv_reader:
                try:
                    booking = self._parse_booking_row(row)
                    if booking:
                        bookings.append(booking)
                        continue
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

        return CoinbaseParseResult(
            importable=importable, skipped=skipped, bookings=bookings
        )

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

        # For income types, store as price=EUR total, qty=1 (earnings pattern)
        # For trades, calculate from total (includes fees)
        if is_income:
            unit_price = total_amount  # EUR value of the staking reward
            quantity = 1.0
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

        # Determine transaction type
        if is_income:
            llm_tx_type = "interest"
        else:
            llm_tx_type = "buy" if "Buy" in tx_type else "sell"

        # Create raw text for traceability
        if is_income:
            raw_text = f"{tx_type}: {float(quantity_str):.8f} {asset} @ {coinbase_unit_price:.4f} {price_currency} = {total_amount:.5f} {price_currency} (staking income) - {timestamp_str}"
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

    def _parse_booking_row(self, row: dict) -> Optional[dict]:
        """Return a cash booking dict for a fiat Deposit/Withdrawal row, else None.

        Crypto-asset deposits/withdrawals (Asset not a fiat currency) return
        None so they fall through to the normal skip path — they are wallet
        transfers, not cash bookings.
        """
        tx_type = row.get("Transaction Type", "").strip()
        is_deposit = tx_type in self.DEPOSIT_TYPES
        is_withdrawal = tx_type in self.WITHDRAWAL_TYPES
        if not (is_deposit or is_withdrawal):
            return None
        asset = row.get("Asset", "").strip().upper()
        if asset not in self.FIAT_CURRENCIES:
            return None
        # Amount: the fiat Quantity Transacted is the cash amount; fall back to
        # the Total column if blank. Strip currency symbols/grouping.
        raw = (
            row.get("Quantity Transacted", "").strip()
            or row.get("Total (inclusive of fees and/or spread)", "").strip()
        )
        cleaned = raw.replace("€", "").replace("$", "").replace(",", "")
        try:
            amount = abs(float(cleaned))
        except ValueError:
            return None
        if amount == 0:
            return None
        return {
            "date": row.get("Timestamp", "").strip()[:10],
            "action": "Deposit" if is_deposit else "Withdrawal",
            "amount": amount,
            "currency": asset,
        }

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
