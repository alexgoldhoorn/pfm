#!/usr/bin/env python3
"""
Tax Calculator Module for Portfolio Manager

This module implements FIFO (First In First Out) tax calculation logic
for capital gains and losses reporting. It processes buy and sell transactions
to calculate realized gains/losses for tax reporting purposes.
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
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
    # Purchase fees spread over the lot's shares; IRPF acquisition cost
    # includes purchase expenses, so this is added to price in cost basis.
    fee_per_share: Decimal = Decimal("0")

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
            user_id: Legacy parameter, ignored — the app is single-user and
                stored transactions carry user_id NULL, so filtering by it
                would return an empty report.
            start_date: Start date for sell transactions
            end_date: End date for sell transactions
            symbols: Optional list of symbols to filter by
            portfolio_id: Optional portfolio ID to filter by (for per-broker reporting)

        Returns:
            Dictionary mapping symbol to list of tax transactions
        """
        self.logger.info(
            f"Calculating tax report from {start_date} to {end_date}"
            + (f" portfolio_id={portfolio_id}" if portfolio_id is not None else "")
        )

        # Get transactions: filtered by portfolio if specified, otherwise all.
        # Never filter by user_id — transaction rows store it as NULL.
        if portfolio_id is not None:
            all_transactions = self.db_manager.get_transactions_by_portfolio(
                portfolio_id
            )
        else:
            all_transactions = self.db_manager.get_all_transactions(user_id=None)

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

    def _replay_symbol_transactions(
        self, symbol: str, transactions: List[Dict], collect_sales: bool = True
    ) -> Tuple[List[TaxLot], List[Tuple[date, List[TaxTransaction]]]]:
        """Replay every buy/sell/split for *symbol* against a FIFO lot list.

        The one implementation of "consume this lot list against this
        transaction list" — both :meth:`calculate_tax_report` (via
        :meth:`_calculate_symbol_tax_transactions`) and :meth:`get_open_lots`
        drive it, so a report and an open-lot query can never disagree about
        which lots a past sell consumed.

        Args:
            symbol: Stock symbol (only used for labelling the output rows).
            transactions: All transactions for this symbol, any order.
            collect_sales: When False, sells still consume lots but no
                ``TaxTransaction`` rows (nor the asset/portfolio lookups they
                need) are built — the open-lot path has no use for them.

        Returns:
            ``(tax_lots, sales)`` — ``tax_lots`` is the lot list after the last
            transaction (a lot with ``remaining_quantity == 0`` is fully sold);
            ``sales`` is one ``(sell_date, [TaxTransaction, ...])`` pair per
            sell, in chronological order, empty when *collect_sales* is False.
        """
        # Sort transactions by date (FIFO requirement)
        sorted_transactions = sorted(
            transactions, key=lambda x: (x["transaction_date"], x["id"])
        )

        tax_lots: List[TaxLot] = []
        sales: List[Tuple[date, List[TaxTransaction]]] = []

        for tx in sorted_transactions:
            tx_date = self._parse_date(tx["transaction_date"])
            tx_type = tx["transaction_type"]
            quantity = Decimal(str(tx["quantity"]))
            price = Decimal(str(tx["price"]))
            fees = Decimal(str(tx.get("fees") or 0))

            if tx_type == "buy":
                # Add new tax lot
                tax_lot = TaxLot(
                    purchase_date=tx_date,
                    quantity=quantity,
                    price=price,
                    remaining_quantity=quantity,
                    transaction_id=tx["id"],
                    description=tx.get("description", ""),
                    fee_per_share=fees / quantity if quantity > 0 else Decimal("0"),
                )
                tax_lots.append(tax_lot)

            elif tx_type == "sell":
                # Every sell consumes lots (FIFO runs over the full history);
                # the caller decides which of them it wants reported.
                sell_transactions = self._process_sell_transaction(
                    symbol,
                    tx,
                    tax_lots,
                    tx_date,
                    quantity,
                    price,
                    sell_fee_per_share=(
                        fees / quantity if quantity > 0 else Decimal("0")
                    ),
                    collect=collect_sales,
                )
                if collect_sales:
                    sales.append((tx_date, sell_transactions))

            elif tx_type == "split" and quantity > 0:
                # Split ratio is stored in quantity (2-for-1 → 2). Scale every
                # open lot's shares and divide its per-share price and fee so
                # cost basis is kept.
                for lot in tax_lots:
                    lot.quantity *= quantity
                    lot.remaining_quantity *= quantity
                    lot.price /= quantity
                    lot.fee_per_share /= quantity

        return tax_lots, sales

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
        _lots, sales = self._replay_symbol_transactions(symbol, transactions)
        tax_transactions: List[TaxTransaction] = []
        for sell_date, sell_transactions in sales:
            if start_date <= sell_date <= end_date:
                tax_transactions.extend(sell_transactions)
        return tax_transactions

    def get_open_lots(
        self,
        symbol: str,
        portfolio_id: Optional[int] = None,
        transactions: Optional[List[Dict]] = None,
    ) -> List[TaxLot]:
        """FIFO lots for *symbol* that are still open as of the last transaction.

        There is no date window: every ``buy``/``sell``/``split`` row is
        replayed through :meth:`_replay_symbol_transactions` — the same lot
        consumption :meth:`calculate_tax_report` performs — and what is left
        holding shares is returned. "As of the last transaction in the DB" is
        "as of today" by construction.

        Args:
            symbol: Stock symbol.
            portfolio_id: Restrict to one portfolio, mirroring
                :meth:`calculate_tax_report`'s own parameter. Ignored when
                *transactions* is supplied.
            transactions: Pre-loaded transaction rows to replay instead of
                querying. They must already be scoped the way *portfolio_id*
                would have scoped them; rows for other symbols are filtered out
                here. Lets a caller that needs lots for many symbols load the
                table once rather than once per symbol.

        Returns:
            The lots with ``remaining_quantity > 0``, oldest first.
        """
        if transactions is None:
            if portfolio_id is not None:
                transactions = self.db_manager.get_transactions_by_portfolio(
                    portfolio_id
                )
            else:
                transactions = self.db_manager.get_all_transactions(user_id=None)

        wanted = symbol.upper()
        rows = [tx for tx in transactions if (tx.get("symbol") or "").upper() == wanted]
        lots, _sales = self._replay_symbol_transactions(
            symbol, rows, collect_sales=False
        )
        return [lot for lot in lots if lot.remaining_quantity > 0]

    def _process_sell_transaction(
        self,
        symbol: str,
        sell_tx: Dict,
        tax_lots: List[TaxLot],
        sell_date: date,
        sell_quantity: Decimal,
        sell_price: Decimal,
        sell_fee_per_share: Decimal = Decimal("0"),
        collect: bool = True,
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
            sell_fee_per_share: Sale fees spread over the shares sold; IRPF
                transmission value is net of sale expenses, so this is
                subtracted from the proceeds.
            collect: When False, lots are consumed exactly as always but no
                ``TaxTransaction`` is built and the asset/portfolio lookups
                they need are skipped — the caller only wants the lot
                mutation (see :meth:`get_open_lots`).

        Returns:
            List of tax transactions for this sell (empty when *collect* is
            False)
        """
        tax_transactions = []
        remaining_to_sell = sell_quantity

        # Get asset and portfolio information
        asset_name = symbol
        portfolio_name = "Unknown"
        if collect:
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

            if collect:
                # Amounts follow the IRPF definitions: proceeds net of sale
                # fees, cost basis including purchase fees (prices stay gross).
                sell_amount = quantity_from_lot * (sell_price - sell_fee_per_share)
                purchase_amount = quantity_from_lot * (
                    tax_lot.price + tax_lot.fee_per_share
                )
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
