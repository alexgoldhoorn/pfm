"""
IndexaCapital CSV transaction parser.

This module provides functionality to parse IndexaCapital transaction CSV exports
and convert them to LLMTransaction objects for import into the portfolio system.
"""

import csv
from datetime import datetime
from io import StringIO
from typing import List, Tuple
from dataclasses import dataclass

from ..llm_types import LLMTransaction


@dataclass
class IndexaCapitalParseResult:
    """Result of parsing IndexaCapital CSV data."""

    importable: List[LLMTransaction]
    skipped: List[Tuple[str, str]]  # (transaction_type, reason)


class IndexaCapitalCSVParser:
    """Parser for IndexaCapital CSV transaction exports."""

    # Transaction types that should be imported as trades
    IMPORTABLE_TYPES = {"SUSCRIPCIÓN", "REEMBOLSO"}

    def _parse_european_amount(self, amount_str: str) -> float:
        """
        Parse European format monetary amount like "1.583,25 €" or "695,33 €"
        Handles dots as thousands separators and comma as decimal separator.
        """
        # Remove € symbol and spaces
        cleaned = amount_str.replace("€", "").replace(" ", "").strip()

        # Check if it contains both dot and comma
        if "." in cleaned and "," in cleaned:
            # European format: 1.583,25 (dot = thousands separator, comma = decimal)
            # Remove dots (thousands separator) and replace comma with dot
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            # Simple comma format: 695,33 (comma = decimal separator)
            cleaned = cleaned.replace(",", ".")
        # else: already in correct format or simple number

        return float(cleaned)

    def parse_csv_content(self, csv_content: str) -> IndexaCapitalParseResult:
        """
        Parse IndexaCapital CSV content into LLMTransaction objects.

        Args:
            csv_content: Raw CSV content from IndexaCapital export

        Returns:
            IndexaCapitalParseResult with importable transactions and skipped entries
        """
        importable = []
        skipped = []

        # Parse CSV with semicolon delimiter
        reader = csv.reader(StringIO(csv_content.strip()), delimiter=";")

        for row_num, row in enumerate(reader, 1):
            if len(row) < 9:
                skipped.append((f"Row {row_num}", f"Insufficient columns: {len(row)}"))
                continue

            try:
                row[0].strip()  # DD/MM/YYYY
                settlement_date = row[1].strip()  # YYYY-MM-DD
                asset_name = row[2].strip().strip('"')
                symbol = row[3].strip()  # ISIN
                tx_type = row[4].strip()
                quantity_str = row[5].strip()
                total_amount_str = row[6].strip().strip('"')
                fees_str = row[7].strip().strip('"')

                # Skip non-importable transaction types
                if tx_type not in self.IMPORTABLE_TYPES:
                    skipped.append((tx_type, "Non-importable transaction type"))
                    continue

                # Convert transaction type to standard format
                if tx_type == "SUSCRIPCIÓN":
                    standard_tx_type = "buy"
                elif tx_type == "REEMBOLSO":
                    standard_tx_type = "sell"
                else:
                    standard_tx_type = "buy"  # Default fallback

                # Parse quantity (comma as decimal separator)
                quantity = float(quantity_str.replace(",", "."))

                # Parse total amount (remove € symbol and convert comma to dot)
                total_amount = float(self._parse_european_amount(total_amount_str))

                # Calculate unit price
                price = total_amount / quantity if quantity > 0 else 0.0

                # Use settlement date (YYYY-MM-DD format) as it's already in ISO format
                transaction_date = settlement_date

                # Validate date format
                try:
                    datetime.strptime(transaction_date, "%Y-%m-%d")
                except ValueError:
                    skipped.append(
                        (tx_type, f"Invalid date format: {transaction_date}")
                    )
                    continue

                # Parse fees (European number format, may be empty)
                try:
                    fees = (
                        float(self._parse_european_amount(fees_str))
                        if fees_str
                        else 0.0
                    )
                except (ValueError, AttributeError):
                    fees = 0.0

                # Create LLMTransaction
                transaction = LLMTransaction(
                    tx_type=standard_tx_type,
                    symbol=symbol,
                    asset_name=asset_name,
                    quantity=quantity,
                    price=price,
                    date=transaction_date,
                    currency="EUR",  # IndexaCapital uses EUR
                    raw_text=";".join(row),  # Join back for traceability
                    fees=fees,
                )

                # Validate the transaction
                validation_error = transaction.validate()
                if validation_error:
                    skipped.append((tx_type, f"Validation error: {validation_error}"))
                    continue

                importable.append(transaction)

            except (ValueError, IndexError) as e:
                skipped.append((f"Row {row_num}", f"Parse error: {str(e)}"))
                continue

        return IndexaCapitalParseResult(importable=importable, skipped=skipped)


def parse_indexacapital_csv(csv_content: str) -> IndexaCapitalParseResult:
    """
    Convenience function to parse IndexaCapital CSV content.

    Args:
        csv_content: Raw CSV content string

    Returns:
        IndexaCapitalParseResult object with parsed transactions
    """
    parser = IndexaCapitalCSVParser()
    return parser.parse_csv_content(csv_content)
