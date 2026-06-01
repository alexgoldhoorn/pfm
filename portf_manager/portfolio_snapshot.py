"""
Portfolio Snapshot Module

Provides current portfolio data injection for AI chat agents.
Generates compact JSON representations of portfolio positions and transactions.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from .database import Database


@dataclass
class PositionSummary:
    """Summary of a position in the portfolio"""

    ticker: str
    name: Optional[str]
    asset_type: str
    current_shares: Decimal
    avg_cost_basis: Decimal
    total_invested: Decimal
    current_value: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    sector: Optional[str] = None


@dataclass
class PortfolioSummary:
    """Complete portfolio summary"""

    as_of: str
    total_positions: int
    total_invested: Decimal
    current_value: Optional[Decimal]
    cash_balance: Decimal
    positions: List[PositionSummary]
    recent_transactions: List[Dict[str, Any]]


class PortfolioSnapshot:
    """
    Generate portfolio snapshots for AI agent consumption.

    Provides current positions, recent transactions, and portfolio metrics
    in a compact JSON format suitable for prompt injection.
    """

    def __init__(
        self, database: Database, max_positions: int = 100, days_recent: int = 30
    ):
        self.db = database
        self.max_positions = max_positions
        self.days_recent = days_recent

    def build_current_positions(self) -> List[PositionSummary]:
        """Build current positions summary"""
        positions = []

        # Get all assets with current positions using correct column names
        position_query = """
        SELECT
            a.symbol,
            a.name,
            a.asset_type,
            a.sector,
            SUM(
                CASE
                    WHEN t.transaction_type IN ('buy', 'transfer_in') THEN t.quantity
                    WHEN t.transaction_type IN ('sell', 'transfer_out') THEN -t.quantity
                    ELSE 0
                END
            ) as current_shares,
            SUM(
                CASE
                    WHEN t.transaction_type IN ('buy', 'transfer_in') THEN t.total_amount
                    WHEN t.transaction_type IN ('sell', 'transfer_out') THEN -t.total_amount
                    ELSE 0
                END
            ) as total_invested
        FROM assets a
        LEFT JOIN transactions t ON a.id = t.asset_id
        GROUP BY a.id, a.symbol, a.name, a.asset_type, a.sector
        HAVING current_shares > 0
        ORDER BY total_invested DESC
        LIMIT ?
        """

        with self.db.get_connection() as conn:
            cursor = conn.execute(position_query, (self.max_positions,))
            rows = cursor.fetchall()

        for row in rows:
            # Calculate average cost basis
            current_shares = Decimal(str(row[4])) if row[4] else Decimal("0")
            total_invested = Decimal(str(row[5])) if row[5] else Decimal("0")

            avg_cost_basis = Decimal("0")
            if current_shares > 0:
                avg_cost_basis = total_invested / current_shares

            position = PositionSummary(
                ticker=row[0] or "UNKNOWN",
                name=row[1],
                asset_type=row[2] or "stock",
                current_shares=current_shares,
                avg_cost_basis=avg_cost_basis,
                total_invested=total_invested,
                sector=row[3],
            )
            positions.append(position)

        return positions

    def build_recent_transactions(self) -> List[Dict[str, Any]]:
        """Build recent transactions summary"""
        cutoff_date = datetime.now() - timedelta(days=self.days_recent)

        transactions_query = """
        SELECT
            t.transaction_date,
            a.symbol,
            t.transaction_type,
            t.quantity,
            t.price,
            t.total_amount,
            t.description
        FROM transactions t
        LEFT JOIN assets a ON t.asset_id = a.id
        WHERE t.transaction_date >= ?
        ORDER BY t.transaction_date DESC
        LIMIT 50
        """

        with self.db.get_connection() as conn:
            cursor = conn.execute(transactions_query, (cutoff_date.date().isoformat(),))
            rows = cursor.fetchall()

        transactions = []
        for row in rows:
            transaction = {
                "date": row[0] if row[0] else None,
                "ticker": row[1] or "UNKNOWN",
                "type": row[2],
                "quantity": float(row[3]) if row[3] else 0,
                "price": float(row[4]) if row[4] else 0,
                "total_amount": float(row[5]) if row[5] else 0,
                "notes": row[6],
            }
            transactions.append(transaction)

        return transactions

    def calculate_cash_balance(self) -> Decimal:
        """Calculate current cash balance from transactions"""
        cash_query = """
        SELECT
            SUM(
                CASE
                    WHEN transaction_type IN ('sell', 'dividend') THEN total_amount
                    WHEN transaction_type IN ('buy') THEN -total_amount
                    ELSE 0
                END
            ) as cash_balance
        FROM transactions
        """

        with self.db.get_connection() as conn:
            cursor = conn.execute(cash_query)
            result = cursor.fetchone()
            if result and result[0]:
                return Decimal(str(result[0]))
        return Decimal("0")

    def build_portfolio_summary(self) -> PortfolioSummary:
        """Build complete portfolio summary"""
        positions = self.build_current_positions()
        recent_transactions = self.build_recent_transactions()
        cash_balance = self.calculate_cash_balance()

        total_invested = sum(pos.total_invested for pos in positions)

        return PortfolioSummary(
            as_of=datetime.now().isoformat(),
            total_positions=len(positions),
            total_invested=total_invested,
            current_value=None,  # Would need market data integration
            cash_balance=cash_balance,
            positions=positions,
            recent_transactions=recent_transactions,
        )

    def build_compact_json(self) -> Dict[str, Any]:
        """Build compact JSON representation for prompt injection"""
        summary = self.build_portfolio_summary()

        # Convert to serializable format
        compact_positions = []
        for pos in summary.positions:
            compact_pos = {
                "ticker": pos.ticker,
                "shares": float(pos.current_shares),
                "avg_cost": float(pos.avg_cost_basis),
                "invested": float(pos.total_invested),
                "type": pos.asset_type,
            }
            if pos.name:
                compact_pos["name"] = pos.name
            if pos.sector:
                compact_pos["sector"] = pos.sector
            compact_positions.append(compact_pos)

        return {
            "timestamp": summary.as_of,
            "summary": {
                "positions_count": summary.total_positions,
                "total_invested": float(summary.total_invested),
                "cash_balance": float(summary.cash_balance),
            },
            "positions": compact_positions,
            "recent_activity": summary.recent_transactions[
                :10
            ],  # Limit to 10 most recent
        }

    def estimate_token_count(self, json_data: Dict[str, Any]) -> int:
        """Estimate token count for the JSON data"""
        json_string = json.dumps(json_data, separators=(",", ":"))
        # Rough estimate: 1 token per 4 characters
        return len(json_string) // 4

    def build_prompt_context(self, max_tokens: int = 4000) -> str:
        """
        Build portfolio context for prompt injection.

        Returns a formatted string ready for insertion into AI prompts.
        """
        portfolio_data = self.build_compact_json()
        estimated_tokens = self.estimate_token_count(portfolio_data)

        if estimated_tokens > max_tokens:
            # Truncate positions if too large
            positions = portfolio_data["positions"]
            while estimated_tokens > max_tokens and len(positions) > 5:
                positions.pop()
                portfolio_data["positions"] = positions
                estimated_tokens = self.estimate_token_count(portfolio_data)

        # Format as readable context
        context = f"""Current Portfolio (as of {portfolio_data['timestamp']}):

Portfolio Summary:
- Total Positions: {portfolio_data['summary']['positions_count']}
- Total Invested: ${portfolio_data['summary']['total_invested']:,.2f}
- Cash Balance: ${portfolio_data['summary']['cash_balance']:,.2f}

Current Holdings:"""

        for pos in portfolio_data["positions"]:
            context += f"\n- {pos['ticker']}"
            if pos.get("name"):
                context += f" ({pos['name']})"
            context += f": {pos['shares']:.2f} shares @ ${pos['avg_cost']:.2f} avg (${pos['invested']:,.2f} invested)"
            if pos.get("sector"):
                context += f" [{pos['sector']}]"

        if portfolio_data["recent_activity"]:
            context += "\n\nRecent Transactions:"
            for txn in portfolio_data["recent_activity"][:5]:
                context += f"\n- {txn['date']}: {txn['type'].upper()} {txn['quantity']:.2f} {txn['ticker']} @ ${txn['price']:.2f}"

        return context


# Utility functions for CLI integration
def create_snapshot_from_db_path(db_path: str) -> PortfolioSnapshot:
    """Create a portfolio snapshot from database path"""
    db = Database(db_path)
    return PortfolioSnapshot(db)


def get_portfolio_context_for_chat(db_path: str, max_tokens: int = 4000) -> str:
    """Get portfolio context string for chat integration"""
    try:
        snapshot = create_snapshot_from_db_path(db_path)
        return snapshot.build_prompt_context(max_tokens)
    except Exception as e:
        return f"Error loading portfolio data: {str(e)}"
