"""Duplicate detection shared by every import path.

The web import, the PDT Google Sheets pull and the CLI PDT import all use this,
so re-running any of them over data that is already stored adds nothing.

Two rules hold throughout:

- A row is compared only with data stored before this import started, never
  with rows of the same import. Two identical trades or deposits in one file
  are both real.
- Each stored row matches at most one incoming row.

Bookings get the same treatment from ``Database.match_existing_bookings``,
which matches a whole import at once.
"""

from typing import Dict, Optional, Set


class TransactionDeduplicator:
    """Tracks one import run's transaction matches and inserts.

    Args:
        db: The database adapter.
    """

    def __init__(self, db) -> None:
        self.db = db
        self._seen: Set[int] = set()
        self.skipped = 0

    def find(self, **fields) -> Optional[Dict]:
        """Return the stored transaction this row duplicates, or None.

        Takes the keyword arguments of ``Database.find_duplicate_transaction``.
        A match is consumed, so it can't match a second row, and counted in
        ``skipped``.
        """
        # The CLI's server mode passes an HTTP client, which can't look rows
        # up; it imports as before, without the check.
        if not hasattr(self.db, "find_duplicate_transaction"):
            return None
        match = self.db.find_duplicate_transaction(**fields, exclude_ids=self._seen)
        if match:
            self._seen.add(match["id"])
            self.skipped += 1
        return match

    def inserted(self, transaction_id: int) -> None:
        """Record a row this import created so later rows can't match it."""
        self._seen.add(transaction_id)
