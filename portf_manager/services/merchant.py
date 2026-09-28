"""Merchant-name normalisation for bank-statement descriptions.

Spanish bank exports wrap the merchant in noise: a 12-digit card/terminal
reference glued to the front, a ``\\CITY\\ES<digits>`` suffix, processor
prefixes (``PAYPAL *``, ``SumUp *``), order codes after ``*``, masked card
numbers and trailing reference numbers. That noise makes rule patterns fragile
and makes the same shop look like hundreds of different descriptions.

The result is stored next to the raw description (never instead of it) and is
used for search, rule matching and recurring-charge grouping.
"""

import re
import unicodedata
from typing import Optional

# Everything from the first backslash: "\CITY\ES26010112" location suffix.
_LOCATION_SUFFIX = re.compile(r"\\.*$")
# Card/terminal reference glued to the merchant, optionally "-001".
_LEADING_REF = re.compile(r"^\d{6,}(?:-\d+)?")
# Direct-debit receipt ("R/ ") and card-purchase prefixes.
_RECEIPT_PREFIX = re.compile(
    r"^(?:R/|RECIBO\b|COMPRA\s+TARJ(?:ETA)?\.?|COMPRA\b)\s*", re.IGNORECASE
)
# Payment processors that put the real merchant after a star.
_PROCESSOR_PREFIX = re.compile(r"^(?:PAYPAL|SUMUP|ZETTLE|SQ)\s*\*\s*", re.IGNORECASE)
# Masked card number: "*1234".
_CARD_MASK = re.compile(r"\*\d{4}\b")
# Order code after a star: "AMAZON* AB12CD34E".
_STAR_CODE = re.compile(r"\*\s*[A-Za-z0-9]*\d[A-Za-z0-9]*")
# Short dates: "07/25", "07/25/2026".
_SHORT_DATE = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")
# Trailing reference numbers: " 010012345", " 1234-567890-123 45-".
_TRAILING_NUMBERS = re.compile(r"(?:\s+\d[\d\-]*)+\s*$")
_WHITESPACE = re.compile(r"\s+")


def normalize_merchant(description: str) -> str:
    """Reduce a bank-statement description to a stable merchant name.

    Args:
        description: The raw description exactly as the bank exported it.

    Returns:
        The cleaned merchant name. Falls back to the whitespace-collapsed
        description when cleaning leaves nothing, so a non-blank description
        never yields an empty merchant.
    """
    raw = _WHITESPACE.sub(" ", description or "").strip()
    text = _LOCATION_SUFFIX.sub("", description or "").strip()
    text = _LEADING_REF.sub("", text).strip()
    text = _RECEIPT_PREFIX.sub("", text).strip()
    text = _PROCESSOR_PREFIX.sub("", text).strip()
    text = _CARD_MASK.sub(" ", text)
    text = _STAR_CODE.sub(" ", text)
    text = _SHORT_DATE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    text = _TRAILING_NUMBERS.sub("", text).strip(" -,")
    return text or raw


def fold_for_search(text: Optional[str]) -> str:
    """Accent- and case-insensitive form of ``text`` for substring search.

    SQLite's LIKE folds only ASCII case, so ``comissió`` would miss
    ``COMISSIÓ`` and ``comissio`` would miss both. Registered as the SQLite
    function ``pfm_fold`` on every connection and applied to both sides of
    the comparison.

    Args:
        text: Any text; None is treated as empty.

    Returns:
        The casefolded text with combining marks removed after NFKD
        decomposition (``"Café Ñandú"`` -> ``"cafe nandu"``).
    """
    if not text:
        return ""
    # Casefold first so characters that fold into a base letter plus a
    # combining mark (e.g. dotted capital I) lose the mark too.
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
