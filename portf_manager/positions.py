"""
Shared position math — the single source of truth for turning a transaction
list into open positions (quantity + cost basis) and realised P&L.

Processes transactions in **chronological** order and supports stock splits:
a ``split`` transaction stores the split ratio in its ``quantity`` field
(2-for-1 → 2.0; 1-for-10 reverse split → 0.1) and multiplies the quantity held
at that date, leaving total cost unchanged (so average cost per share scales).
``dividend`` and unknown types don't affect positions.
"""

from typing import Callable, Dict, Hashable, List, Tuple


def _sort_key(tx: dict):
    # Chronological; ties broken by id so same-day order is stable/insertion-like.
    return (str(tx.get("transaction_date", "")), tx.get("id", 0) or 0)


def compute_positions(
    transactions: List[dict],
    key: Callable[[dict], Hashable] = lambda tx: tx["asset_id"],
) -> Tuple[Dict[Hashable, dict], float]:
    """Return ``(positions, realised)``.

    ``positions`` maps ``key(tx)`` → ``{"quantity": float, "cost": float}`` for
    every key seen (including fully-closed ones, which end at quantity 0).
    ``realised`` is total realised P&L from sells (proceeds − average cost),
    in the transactions' own amounts (caller applies any FX).

    Args:
        transactions: rows with transaction_type, quantity, total_amount, date.
        key: how to group (default per asset; pass a (portfolio_id, asset_id)
             lambda for per-broker positions).
    """
    positions: Dict[Hashable, dict] = {}
    realised = 0.0
    for tx in sorted(transactions, key=_sort_key):
        k = key(tx)
        t = (tx.get("transaction_type") or "").lower()
        qty = float(tx.get("quantity") or 0)
        total = float(tx.get("total_amount") or 0)
        pos = positions.setdefault(k, {"quantity": 0.0, "cost": 0.0})
        if t == "buy":
            pos["quantity"] += qty
            pos["cost"] += total
        elif t == "sell":
            if pos["quantity"] > 0:
                avg = pos["cost"] / pos["quantity"]
                realised += total - avg * qty
                pos["cost"] *= max(pos["quantity"] - qty, 0.0) / pos["quantity"]
            pos["quantity"] -= qty
        elif t == "split" and qty > 0:
            # Quantity scales by the ratio; cost basis is unchanged.
            pos["quantity"] *= qty
    return positions, realised
