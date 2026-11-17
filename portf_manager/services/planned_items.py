"""Planned items: future one-off expenses and income entered by hand.

The rules live here so the API (and later the chat and MCP layers) share them.
An item's ``amount`` is signed like a bank row: negative out, positive in. A
``spending`` or ``savings`` item must be negative, an ``income`` item positive.
"""

from datetime import date
from typing import Any, Dict

KINDS = ("spending", "savings", "income")


def _check_date(value: Any) -> str:
    """The date as ``YYYY-MM-DD`` or a ValueError."""
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must be a real date, YYYY-MM-DD")
    return value


def validate_item(fields: Dict[str, Any], existing: Dict[str, Any] | None = None):
    """Validate a create (no *existing*) or a partial update.

    Args:
        fields: The submitted fields; only the keys present are checked.
        existing: The stored item for an update. Its values fill the keys the
            update leaves out, so the amount sign is checked against the
            final kind.

    Returns:
        A cleaned copy of *fields*.

    Raises:
        ValueError: With the reason, when a rule is broken.
    """
    merged = {**(existing or {}), **fields}
    out = dict(fields)
    if existing is None:
        for key in ("date", "description", "amount", "kind"):
            if merged.get(key) is None:
                raise ValueError(f"{key} is required")
    if "date" in fields:
        out["date"] = _check_date(fields["date"])
    if "description" in fields:
        text = (fields["description"] or "").strip()
        if not text:
            raise ValueError("description must not be empty")
        out["description"] = text
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    if "amount" in fields:
        amount = fields["amount"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError("amount must be a number")
    if "currency" in fields:
        cur = (fields["currency"] or "").strip().upper()
        if len(cur) != 3:
            raise ValueError("currency must be a 3-letter code")
        out["currency"] = cur
    if "amount" in fields or "kind" in fields:
        amount, kind = merged["amount"], merged["kind"]
        if amount == 0:
            raise ValueError("amount must not be 0")
        if kind == "income" and amount < 0:
            raise ValueError("an income item needs a positive amount")
        if kind != "income" and amount > 0:
            raise ValueError(f"a {kind} item needs a negative amount")
    return out
