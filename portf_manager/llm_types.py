from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class LLMTransaction:
    tx_type: str  # buy / sell
    symbol: str
    asset_name: str
    quantity: float
    price: float
    date: str  # ISO-8601
    currency: str
    raw_text: str  # original line for traceability
    fees: float = 0.0

    def validate(self) -> Optional[str]:
        """
        Validate the transaction data for required fields and basic sanity checks.

        Returns:
            None if validation passes, error message string if validation fails
        """
        # Check required fields are not empty
        if not self.tx_type:
            return "Transaction type is required"

        if not self.symbol:
            return "Symbol is required"

        if not self.asset_name:
            return "Asset name is required"

        if not self.currency:
            return "Currency is required"

        if not self.raw_text:
            return "Raw text is required for traceability"

        # Validate transaction type
        if self.tx_type.lower() not in ["buy", "sell"]:
            return "Transaction type must be 'buy' or 'sell'"

        # Validate quantity is positive
        if self.quantity <= 0:
            return "Quantity must be positive"

        # Validate price is positive
        if self.price <= 0:
            return "Price must be positive"

        # Validate date is parseable as ISO-8601
        try:
            datetime.fromisoformat(self.date.replace("Z", "+00:00"))
        except ValueError:
            return "Date must be in ISO-8601 format"

        return None
