"""Database glue for transfer matching.

Wraps the pure matcher in ``transfer_matcher`` with the reads and writes that
link spending rows to each other or to brokerage bookings. The ``bookings``
table is never modified here.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

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
    unlinked = db.list_unlinked_spending_transactions()
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


def settle_transfers(db, spending_ids: Optional[List[int]] = None) -> Dict[str, int]:
    """Link transfers over the given rows (None = all unlinked).

    Returns:
        ``{"linked": n, "flagged": 0}``; flagging by pattern comes later.
    """
    return {"linked": link_transfers(db, spending_ids), "flagged": 0}


def settle_after_bookings(db) -> Dict[str, int]:
    """Re-run transfer settling after bookings were created. Never raises."""
    try:
        return settle_transfers(db)
    except Exception:
        logger.exception("Transfer settling after bookings failed")
        return {"linked": 0, "flagged": 0}
