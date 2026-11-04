"""
Shared position math — the single source of truth for turning a transaction
list into open positions (quantity + cost basis) and realised P&L.

Processes transactions in **chronological** order and supports stock splits:
a ``split`` transaction stores the split ratio in its ``quantity`` field
(2-for-1 → 2.0; 1-for-10 reverse split → 0.1) and multiplies the quantity held
at that date, leaving total cost unchanged (so average cost per share scales).
``dividend`` and unknown types don't affect positions.
"""

from typing import Callable, Dict, Hashable, Iterable, List, Optional, Tuple


def _sort_key(tx: dict):
    # Chronological; ties broken by id so same-day order is stable/insertion-like.
    return (str(tx.get("transaction_date", "")), tx.get("id", 0) or 0)


def compute_positions(
    transactions: List[dict],
    key: Callable[[dict], Hashable] = lambda tx: tx["asset_id"],
    on_sell: Optional[Callable[[dict, float], None]] = None,
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
        on_sell: optional callback, called with each sell and the realised
            gain it booked (in the transaction's own currency).
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
                gain = total - avg * qty
                realised += gain
                if on_sell is not None:
                    on_sell(tx, gain)
                pos["cost"] *= max(pos["quantity"] - qty, 0.0) / pos["quantity"]
            pos["quantity"] -= qty
        elif t == "split" and qty > 0:
            # Quantity scales by the ratio; cost basis is unchanged.
            pos["quantity"] *= qty
    return positions, realised


def self_managed_held_asset_ids(
    transactions: List[dict], managed_portfolio_ids: Iterable[int]
) -> set:
    """Asset ids held (quantity > 0) in at least one *non*-managed portfolio.

    A managed portfolio (robo-advisor) is one where someone else makes the
    trades, so its holdings are tracked for net worth and exposure but should
    never raise a buy/sell signal. An asset held both there and in a
    self-managed account is still actionable and is returned.

    Args:
        transactions: rows with portfolio_id, asset_id, transaction_type,
            quantity, total_amount, date.
        managed_portfolio_ids: ids of the portfolios flagged ``is_managed``.
    """
    managed = set(managed_portfolio_ids)
    by_portfolio, _ = compute_positions(
        transactions, key=lambda tx: (tx.get("portfolio_id"), tx["asset_id"])
    )
    return {
        aid
        for (pid, aid), pos in by_portfolio.items()
        if pos["quantity"] > 0 and pid not in managed
    }


def managed_portfolio_ids(db) -> set:
    """Ids of the portfolios flagged ``is_managed`` (active or not)."""
    return {
        p["id"] for p in db.get_all_portfolios(active_only=False) if p.get("is_managed")
    }
