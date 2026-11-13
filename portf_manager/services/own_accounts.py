"""Settings-backed text patterns that identify the user's own accounts.

A row whose description or merchant contains one of these patterns is a
transfer to or from an account that is closed or not tracked, so it has no
counterpart row to link to.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

SETTING_KEY = "own_account_patterns"
MIN_PATTERN_LENGTH = 4


def clean_patterns(raw: Iterable[str]) -> List[str]:
    """Strip patterns and drop case-insensitive duplicates, keeping the first.

    Args:
        raw: Patterns as typed by the user.

    Returns:
        The cleaned list in original order.

    Raises:
        ValueError: A pattern is blank or shorter than ``MIN_PATTERN_LENGTH``
            after stripping; the message names the offender.
    """
    cleaned: List[str] = []
    seen = set()
    for item in raw:
        text = (item or "").strip()
        if not text:
            raise ValueError("Pattern cannot be blank")
        if len(text) < MIN_PATTERN_LENGTH:
            raise ValueError(
                f"Pattern '{text}' is too short "
                f"(at least {MIN_PATTERN_LENGTH} characters)"
            )
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def load_patterns(db) -> List[str]:
    """Read the stored patterns; anything unusable yields an empty list."""
    raw = db.get_setting(SETTING_KEY)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Setting %s is not valid JSON; ignoring", SETTING_KEY)
        return []
    if not isinstance(data, list) or not all(isinstance(p, str) for p in data):
        logger.warning("Setting %s is not a list of strings; ignoring", SETTING_KEY)
        return []
    return data


def save_patterns(db, patterns: Iterable[str]) -> List[str]:
    """Clean and store the patterns.

    Returns:
        The list as stored.

    Raises:
        ValueError: A pattern is blank or too short.
    """
    cleaned = clean_patterns(patterns)
    db.set_setting(SETTING_KEY, json.dumps(cleaned))
    return cleaned


def matches_own_account(
    patterns: Iterable[str], description: str, merchant: Optional[str]
) -> bool:
    """True when any pattern is a case-insensitive substring of either field."""
    texts = ((description or "").lower(), (merchant or "").lower())
    for pattern in patterns:
        needle = (pattern or "").strip().lower()
        if needle and any(needle in t for t in texts):
            return True
    return False
