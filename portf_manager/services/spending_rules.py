"""Spending category rules — pure matching logic, no DB access.

A rule is a dict as stored in ``spending_rules``: ``pattern`` and ``category``
plus optional ``portfolio_id``, ``amount_sign``, ``min_amount``,
``max_amount`` and ``priority``. The router supplies ``category_fits`` so the
category tree (a DB concern) stays out of this module.
"""

from typing import Callable, Iterable, Optional

DEFAULT_PRIORITY = 100
# Float noise from bank CSVs must not push an exact bound out of range.
_EPSILON = 1e-9


def rule_sort_key(rule: dict) -> tuple[int, int]:
    """Evaluation order: lowest priority first, then oldest rule."""
    priority = rule.get("priority")
    return (
        DEFAULT_PRIORITY if priority is None else int(priority),
        int(rule.get("id") or 0),
    )


def rule_matches(
    rule: dict,
    description: str,
    merchant: Optional[str],
    amount: float,
    portfolio_id: Optional[int],
) -> bool:
    """True when the rule's pattern and every set condition hold for the row.

    A blank pattern never matches: "" is a substring of every string, so it
    would otherwise recategorize an entire backlog.
    """
    pattern = (rule.get("pattern") or "").strip().lower()
    if not pattern:
        return False
    haystacks = ((description or "").lower(), (merchant or "").lower())
    if not any(pattern in text for text in haystacks):
        return False
    if rule.get("portfolio_id") is not None and rule["portfolio_id"] != portfolio_id:
        return False
    sign = rule.get("amount_sign")
    if sign == "negative" and amount >= 0:
        return False
    if sign == "positive" and amount <= 0:
        return False
    magnitude = abs(amount)
    if rule.get("min_amount") is not None and magnitude < rule["min_amount"] - _EPSILON:
        return False
    if rule.get("max_amount") is not None and magnitude > rule["max_amount"] + _EPSILON:
        return False
    return True


def pick_category(
    rules: Iterable[dict],
    description: str,
    merchant: Optional[str],
    amount: float,
    portfolio_id: Optional[int],
    category_fits: Callable[[str, float], bool],
) -> str:
    """Category of the first matching rule whose category fits the amount.

    A match whose category can't hold this amount (an Income category on a
    debit) is skipped rather than ending the search, so a later rule can
    still apply. Nothing applicable → ``"uncategorized"``.
    """
    for rule in sorted(rules, key=rule_sort_key):
        if not rule_matches(rule, description, merchant, amount, portfolio_id):
            continue
        if category_fits(rule["category"], amount):
            return rule["category"]
    return "uncategorized"
