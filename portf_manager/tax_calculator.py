#!/usr/bin/env python3
"""
Tax Calculator Module for Portfolio Manager

This module implements FIFO (First In First Out) tax calculation logic
for capital gains and losses reporting. It processes buy and sell transactions
to calculate realized gains/losses for tax reporting purposes.
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, List, Optional
from decimal import Decimal
import logging


@dataclass
class TaxLot:
    """Represents a tax lot (a purchase of shares at a specific price and date)."""

    purchase_date: date
    quantity: Decimal
    price: Decimal
    remaining_quantity: Decimal
    transaction_id: int
    description: str = ""

    def __post_init__(self):
        """Ensure remaining_quantity is initially set to quantity."""
        if self.remaining_quantity is None:
            self.remaining_quantity = self.quantity

    @property
    def cost_basis(self) -> Decimal:
        """Calculate total cost basis for this lot."""
        return self.quantity * self.price

    @property
    def remaining_cost_basis(self) -> Decimal:
        """Calculate remaining cost basis for this lot."""
        return self.remaining_quantity * self.price


@dataclass
class TaxTransaction:
    """Represents a realized gain/loss transaction for tax reporting."""

    symbol: str
    asset_name: str
    sell_date: date
    sell_quantity: Decimal
    sell_price: Decimal
    sell_amount: Decimal
    purchase_date: date
    purchase_price: Decimal
    purchase_amount: Decimal
    gain_loss: Decimal
    holding_period_days: int
    is_long_term: bool
    sell_transaction_id: int
    buy_transaction_id: int
    portfolio_name: str
    description: str = ""

    @property
    def gain_loss_percentage(self) -> Decimal:
        """Calculate gain/loss as percentage."""
        if self.purchase_amount == 0:
            return Decimal("0")
        return (self.gain_loss / self.purchase_amount) * 100


class TaxCalculator:
    """
    Tax calculator implementing FIFO methodology for capital gains/losses.

    This class processes buy and sell transactions to calculate realized
    gains and losses for tax reporting purposes using First In First Out
    (FIFO) cost basis methodology.
    """

    def __init__(self, db_manager):
        """Initialize tax calculator with database manager."""
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)

    def calculate_tax_report(
        self,
        user_id: int,
        start_date: date,
        end_date: date,
        symbols: Optional[List[str]] = None,
        portfolio_id: Optional[int] = None,
    ) -> Dict[str, List[TaxTransaction]]:
        """
        Calculate tax report for specified period using FIFO methodology.

        Args:
            user_id: User ID for transactions
            start_date: Start date for sell transactions
            end_date: End date for sell transactions
            symbols: Optional list of symbols to filter by
            portfolio_id: Optional portfolio ID to filter by (for per-broker reporting)

        Returns:
            Dictionary mapping symbol to list of tax transactions
        """
        self.logger.info(
            f"Calculating tax report for user {user_id} from {start_date} to {end_date}"
            + (f" portfolio_id={portfolio_id}" if portfolio_id is not None else "")
        )

        # Get transactions: filtered by portfolio if specified, otherwise all user transactions
        if portfolio_id is not None:
            all_transactions = self.db_manager.get_transactions_by_portfolio(
                portfolio_id
            )
        else:
            all_transactions = self.db_manager.get_all_transactions(user_id=user_id)

        # Filter by symbols if provided
        if symbols:
            symbol_set = set(s.upper() for s in symbols)
            all_transactions = [
                tx for tx in all_transactions if tx["symbol"].upper() in symbol_set
            ]

        # Group transactions by symbol
        transactions_by_symbol = {}
        for tx in all_transactions:
            symbol = tx["symbol"]
            if symbol not in transactions_by_symbol:
                transactions_by_symbol[symbol] = []
            transactions_by_symbol[symbol].append(tx)

        # Calculate tax transactions for each symbol
        tax_report = {}
        for symbol, transactions in transactions_by_symbol.items():
            tax_transactions = self._calculate_symbol_tax_transactions(
                symbol, transactions, start_date, end_date
            )
            if tax_transactions:
                tax_report[symbol] = tax_transactions

        return tax_report

    def _calculate_symbol_tax_transactions(
        self, symbol: str, transactions: List[Dict], start_date: date, end_date: date
    ) -> List[TaxTransaction]:
        """
        Calculate tax transactions for a specific symbol using FIFO.

        Args:
            symbol: Stock symbol
            transactions: All transactions for this symbol
            start_date: Start date for sell transactions
            end_date: End date for sell transactions

        Returns:
            List of tax transactions (realized gains/losses)
        """
        # Sort transactions by date (FIFO requirement)
        sorted_transactions = sorted(
            transactions, key=lambda x: (x["transaction_date"], x["id"])
        )

        # Track tax lots (purchases) using FIFO
        tax_lots = []
        tax_transactions = []

        for tx in sorted_transactions:
            tx_date = self._parse_date(tx["transaction_date"])
            tx_type = tx["transaction_type"]
            quantity = Decimal(str(tx["quantity"]))
            price = Decimal(str(tx["price"]))

            if tx_type == "buy":
                # Add new tax lot
                tax_lot = TaxLot(
                    purchase_date=tx_date,
                    quantity=quantity,
                    price=price,
                    remaining_quantity=quantity,
                    transaction_id=tx["id"],
                    description=tx.get("description", ""),
                )
                tax_lots.append(tax_lot)

            elif tx_type == "sell":
                # Only process sells within the specified time frame
                if start_date <= tx_date <= end_date:
                    sell_transactions = self._process_sell_transaction(
                        symbol, tx, tax_lots, tx_date, quantity, price
                    )
                    tax_transactions.extend(sell_transactions)

            elif tx_type == "split" and quantity > 0:
                # Split ratio is stored in quantity (2-for-1 → 2). Scale every
                # open lot's shares and divide its price so cost basis is kept.
                for lot in tax_lots:
                    lot.quantity *= quantity
                    lot.remaining_quantity *= quantity
                    lot.price /= quantity

        return tax_transactions

    def _process_sell_transaction(
        self,
        symbol: str,
        sell_tx: Dict,
        tax_lots: List[TaxLot],
        sell_date: date,
        sell_quantity: Decimal,
        sell_price: Decimal,
    ) -> List[TaxTransaction]:
        """
        Process a sell transaction using FIFO methodology.

        Args:
            symbol: Stock symbol
            sell_tx: Sell transaction data
            tax_lots: Available tax lots (purchases)
            sell_date: Date of sale
            sell_quantity: Quantity sold
            sell_price: Price per share sold

        Returns:
            List of tax transactions for this sell
        """
        tax_transactions = []
        remaining_to_sell = sell_quantity

        # Get asset and portfolio information
        asset_info = self.db_manager.get_asset_by_symbol(symbol)
        portfolio_info = self.db_manager.get_portfolio(sell_tx["portfolio_id"])

        asset_name = asset_info["name"] if asset_info else symbol
        portfolio_name = portfolio_info["name"] if portfolio_info else "Unknown"

        # Process tax lots in FIFO order
        for tax_lot in tax_lots:
            if remaining_to_sell <= 0:
                break

            if tax_lot.remaining_quantity <= 0:
                continue

            # Determine how much to sell from this lot
            quantity_from_lot = min(remaining_to_sell, tax_lot.remaining_quantity)

            # Calculate amounts
            sell_amount = quantity_from_lot * sell_price
            purchase_amount = quantity_from_lot * tax_lot.price
            gain_loss = sell_amount - purchase_amount

            # Calculate holding period
            holding_period_days = (sell_date - tax_lot.purchase_date).days
            is_long_term = holding_period_days >= 365

            # Create tax transaction
            tax_transaction = TaxTransaction(
                symbol=symbol,
                asset_name=asset_name,
                sell_date=sell_date,
                sell_quantity=quantity_from_lot,
                sell_price=sell_price,
                sell_amount=sell_amount,
                purchase_date=tax_lot.purchase_date,
                purchase_price=tax_lot.price,
                purchase_amount=purchase_amount,
                gain_loss=gain_loss,
                holding_period_days=holding_period_days,
                is_long_term=is_long_term,
                sell_transaction_id=sell_tx["id"],
                buy_transaction_id=tax_lot.transaction_id,
                portfolio_name=portfolio_name,
                description=sell_tx.get("description", ""),
            )

            tax_transactions.append(tax_transaction)

            # Update remaining quantities
            tax_lot.remaining_quantity -= quantity_from_lot
            remaining_to_sell -= quantity_from_lot

        # Check if we couldn't match all shares (shouldn't happen with proper data)
        if remaining_to_sell > 0:
            self.logger.warning(
                f"Could not match {remaining_to_sell} shares for {symbol} "
                f"sell on {sell_date} - insufficient purchase history"
            )

        return tax_transactions

    def _parse_date(self, date_str: str) -> date:
        """Parse date string to date object."""
        if isinstance(date_str, str):
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                try:
                    return datetime.fromisoformat(date_str).date()
                except ValueError:
                    self.logger.error(f"Could not parse date: {date_str}")
                    return date.today()
        return date_str

    def generate_tax_summary(
        self, tax_report: Dict[str, List[TaxTransaction]]
    ) -> Dict[str, any]:
        """
        Generate summary statistics for tax report.

        Args:
            tax_report: Tax report data

        Returns:
            Dictionary with summary statistics
        """
        total_gain_loss = Decimal("0")
        total_long_term_gain_loss = Decimal("0")
        total_short_term_gain_loss = Decimal("0")
        total_transactions = 0

        symbol_summaries = {}

        for symbol, transactions in tax_report.items():
            symbol_gain_loss = sum(tx.gain_loss for tx in transactions)
            symbol_long_term = sum(
                tx.gain_loss for tx in transactions if tx.is_long_term
            )
            symbol_short_term = sum(
                tx.gain_loss for tx in transactions if not tx.is_long_term
            )

            symbol_summaries[symbol] = {
                "total_gain_loss": symbol_gain_loss,
                "long_term_gain_loss": symbol_long_term,
                "short_term_gain_loss": symbol_short_term,
                "transaction_count": len(transactions),
            }

            total_gain_loss += symbol_gain_loss
            total_long_term_gain_loss += symbol_long_term
            total_short_term_gain_loss += symbol_short_term
            total_transactions += len(transactions)

        return {
            "total_gain_loss": total_gain_loss,
            "total_long_term_gain_loss": total_long_term_gain_loss,
            "total_short_term_gain_loss": total_short_term_gain_loss,
            "total_transactions": total_transactions,
            "symbol_summaries": symbol_summaries,
        }
