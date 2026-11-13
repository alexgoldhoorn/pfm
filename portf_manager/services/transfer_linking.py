"""Database glue for transfer matching.

Wraps the pure matcher in ``transfer_matcher`` with the reads and writes that
link spending rows to each other or to brokerage bookings. The ``bookings``
table is never modified here.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from portf_manager.services.own_accounts import load_patterns, matches_own_account
from portf_manager.services.transfer_matcher import find_all_transfer_matches

logger = logging.getLogger(__name__)


def link_transfers(db, spending_ids: Optional[List[int]] = None) -> int:
    """Pair unlinked spending rows with a counterpart and write the links.

    Args:
        db: A ``Database``.
        spending_ids: Rows to match; None means every unlinked row.

    Returns:
        The number of matches made.
    """
    # A row the owner un-marked is never linked automatically, neither as the
    # row being matched nor as someone else's counterpart.
    unlinked = [
        r
        for r in db.list_unlinked_spending_transactions()
        if r.get("transfer_source") != "rejected"
    ]
    if spending_ids is None:
        rows = list(unlinked)
    else:
        wanted = set(spending_ids)
        rows = [r for r in unlinked if r["id"] in wanted]
    if not rows:
        return 0
    # A booking already claimed by an earlier call must not be claimed again:
    # bookings have no per-call "consumed" tracking of their own.
    claimed_booking_ids = {
        r["transfer_link_id"]
        for r in db.list_spending_transactions(is_transfer=True)
        if r.get("transfer_link_type") == "booking"
    }
    bookings = [b for b in db.get_all_bookings() if b["id"] not in claimed_booking_ids]
    matches = find_all_transfer_matches(rows, unlinked, bookings)
    for m in matches:
        db.update_spending_transaction(
            m.spending_id,
            category="Transfer",
            is_transfer=True,
            transfer_link_type=m.link_type,
            transfer_link_id=m.link_id,
            transfer_source="match",
        )
        if m.link_type == "spending":
            db.update_spending_transaction(
                m.link_id,
                category="Transfer",
                is_transfer=True,
                transfer_link_type="spending",
                transfer_link_id=m.spending_id,
                transfer_source="match",
            )
    return len(matches)


def _pattern_candidates(db, spending_ids: Optional[List[int]]) -> List[Dict]:
    """Rows eligible for pattern flagging: not a transfer, not rejected."""
    rows = db.list_spending_transactions(is_transfer=False)
    wanted = None if spending_ids is None else set(spending_ids)
    return [
        r
        for r in rows
        if r.get("transfer_source") != "rejected"
        and (wanted is None or r["id"] in wanted)
    ]


def flag_own_account_rows(
    db,
    spending_ids: Optional[List[int]] = None,
    patterns: Optional[List[str]] = None,
) -> int:
    """Flag rows matching an own-account pattern as one-legged transfers.

    Rows already flagged as transfers and rows the user rejected are left
    alone. A flagged row gets no link.

    Args:
        db: A ``Database``.
        spending_ids: Rows to consider; None means every eligible row.
        patterns: Patterns to use; None loads the stored ones.

    Returns:
        The number of rows flagged.
    """
    if patterns is None:
        patterns = load_patterns(db)
    if not patterns:
        return 0
    flagged = 0
    for row in _pattern_candidates(db, spending_ids):
        if matches_own_account(patterns, row["description"], row.get("merchant")):
            db.update_spending_transaction(
                row["id"],
                category="Transfer",
                is_transfer=True,
                transfer_source="pattern",
            )
            flagged += 1
    return flagged


def preview_own_account_pattern(db, pattern: str) -> List[Dict]:
    """Rows that flagging with this single pattern would change; writes nothing."""
    return [
        r
        for r in _pattern_candidates(db, None)
        if matches_own_account([pattern], r["description"], r.get("merchant"))
    ]


def settle_transfers(db, spending_ids: Optional[List[int]] = None) -> Dict[str, int]:
    """Link transfers over the given rows (None = all), then flag by pattern.

    Pair matching runs first so a real counterpart always wins over a pattern.

    Returns:
        ``{"linked": n, "flagged": m}``.
    """
    linked = link_transfers(db, spending_ids)
    flagged = flag_own_account_rows(db, spending_ids)
    return {"linked": linked, "flagged": flagged}


def settle_after_bookings(db) -> Dict[str, int]:
    """Re-run transfer settling after bookings were created. Never raises."""
    try:
        return settle_transfers(db)
    except Exception:
        logger.exception("Transfer settling after bookings failed")
        return {"linked": 0, "flagged": 0}
