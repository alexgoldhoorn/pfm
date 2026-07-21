"""
Pure transfer-matching logic.

Links an outflow in one of the user's own accounts to a matching inflow in
another (bank-to-bank) or a brokerage Deposit booking (bank-to-brokerage),
so both sides can be excluded from spending totals and shown as a transfer
instead. No DB access here — callers pass in plain dicts already fetched
from the database, which keeps this fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

_MATCH_WINDOW_DAYS = 3


@dataclass
class TransferMatch:
    spending_id: int
    link_type: str  # "spending" or "booking"
    link_id: int


def _within_window(date_a: str, date_b: str, days: int = _MATCH_WINDOW_DAYS) -> bool:
    da = datetime.strptime(date_a, "%Y-%m-%d")
    db_date = datetime.strptime(date_b, "%Y-%m-%d")
    return abs((da - db_date).days) <= days


def find_transfer_match(
    row: dict,
    candidate_spending: List[dict],
    candidate_bookings: List[dict],
) -> Optional[TransferMatch]:
    """Find a transfer counterpart for a single spending row.

    Args:
        row: The candidate spending_transactions row (id, portfolio_id, date,
            amount, currency, is_transfer).
        candidate_spending: Other unlinked spending_transactions rows (any account).
        candidate_bookings: bookings rows (any action) — only 'Deposit' rows
            in a different portfolio are considered.

    Returns:
        TransferMatch if a counterpart is found, else None.

    Matching rule: same currency, opposite-sign equal absolute amount, date
    within +/-3 days, counterpart belongs to a different portfolio_id, and
    (for spending counterparts) not already linked as a transfer.
    """
    target_abs = abs(row["amount"])

    for cand in candidate_spending:
        if cand["id"] == row["id"]:
            continue
        if cand.get("is_transfer"):
            continue
        if cand["portfolio_id"] == row["portfolio_id"]:
            continue
        if cand.get("currency", "EUR") != row.get("currency", "EUR"):
            continue
        if abs(cand["amount"]) != target_abs:
            continue
        # Opposite sign: one is an outflow (<0), the other an inflow (>0).
        if (cand["amount"] < 0) == (row["amount"] < 0):
            continue
        if not _within_window(row["date"], cand["date"]):
            continue
        return TransferMatch(
            spending_id=row["id"], link_type="spending", link_id=cand["id"]
        )

    # Only an outflow can match a brokerage Deposit booking (money leaving a
    # bank account and landing as a deposit in a brokerage account).
    if row["amount"] < 0:
        for bk in candidate_bookings:
            if bk.get("action") != "Deposit":
                continue
            if bk.get("portfolio_id") == row["portfolio_id"]:
                continue
            if bk.get("currency", "EUR") != row.get("currency", "EUR"):
                continue
            if abs(bk["amount"]) != target_abs:
                continue
            if not _within_window(row["date"], bk["date"]):
                continue
            return TransferMatch(
                spending_id=row["id"], link_type="booking", link_id=bk["id"]
            )

    return None


def find_all_transfer_matches(
    rows: List[dict],
    all_unlinked_spending: List[dict],
    all_deposit_bookings: List[dict],
) -> List[TransferMatch]:
    """Run find_transfer_match for a batch of rows (e.g. a freshly-saved import).

    Each row is matched independently against the full unlinked-spending pool
    (excluding anything already matched earlier in this same call, so two
    rows in the same batch cannot both link to the same counterpart).

    Args:
        rows: The batch to match (e.g. newly-saved rows).
        all_unlinked_spending: Full pool of unlinked spending rows, including `rows`.
        all_deposit_bookings: bookings rows with action == 'Deposit'.
    """
    matches: List[TransferMatch] = []
    consumed_spending_ids: set = set()
    consumed_booking_ids: set = set()

    for row in rows:
        if row.get("is_transfer"):
            continue
        pool = [
            c for c in all_unlinked_spending if c["id"] not in consumed_spending_ids
        ]
        bookings_pool = [
            b for b in all_deposit_bookings if b["id"] not in consumed_booking_ids
        ]
        match = find_transfer_match(row, pool, bookings_pool)
        if match:
            matches.append(match)
            # Exclude the row itself from later candidate pools too, not just
            # its counterpart — otherwise an already-resolved source row can
            # be claimed as a *different* row's counterpart later in the
            # same batch.
            consumed_spending_ids.add(row["id"])
            if match.link_type == "spending":
                consumed_spending_ids.add(match.link_id)
            else:
                consumed_booking_ids.add(match.link_id)
    return matches
