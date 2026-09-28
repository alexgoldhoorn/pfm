"""Running-balance continuity check for imported bank statements.

When a statement carries a balance per row, ``previous balance + amount``
must equal the row's balance. A mismatch means rows are missing (or
duplicated) — inside the file, or between the previous import and this one.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

GAP_BEFORE_FILE = "gap_before_file"
WITHIN_FILE = "within_file"


@dataclass
class BalanceBreak:
    date: str
    description: str
    currency: str
    expected: float
    actual: float
    kind: str


def _chronological(rows: Sequence) -> list:
    """Rows oldest-first; a newest-first export is reversed as a whole."""
    if len(rows) >= 2 and str(rows[0].date)[:10] > str(rows[-1].date)[:10]:
        return list(reversed(rows))
    return list(rows)


def find_balance_breaks(
    rows: Sequence,
    opening_balance: Optional[float] = None,
    tolerance: float = 0.01,
) -> List[BalanceBreak]:
    """Rows whose stated balance doesn't follow from the one before.

    Args:
        rows: Parsed statement rows with ``date``, ``description``,
            ``amount``, ``balance`` (may be None) and ``currency``.
        opening_balance: Balance just before the first row, from the previous
            import; None when unknown, so the first balance row only anchors.
        tolerance: Allowed absolute difference (float noise, rounding).

    Returns:
        One entry per break. After a break the running total resets to the
        row's stated balance, so a single missing row is reported once.
    """
    breaks: List[BalanceBreak] = []
    running = opening_balance
    seen_balance = False
    for row in _chronological(rows):
        if running is not None:
            running = round(running + float(row.amount), 2)
        if row.balance is None:
            continue
        if running is not None and abs(running - float(row.balance)) > tolerance:
            breaks.append(
                BalanceBreak(
                    date=str(row.date)[:10],
                    description=row.description,
                    currency=getattr(row, "currency", "EUR") or "EUR",
                    expected=running,
                    actual=round(float(row.balance), 2),
                    kind=WITHIN_FILE if seen_balance else GAP_BEFORE_FILE,
                )
            )
        running = round(float(row.balance), 2)
        seen_balance = True
    return breaks
