"""Shared parsing utilities for broker CSV parsers."""

import re
from typing import Optional, Union


def parse_european_number(raw: Union[str, None]) -> float:
    """Parse a European-formatted number to float.

    Handles dot-as-thousands-separator and comma-as-decimal:
      '1.583,25'  -> 1583.25
      '695,33'    -> 695.33
      '-1.488,58' -> -1488.58
      '1200'      -> 1200.0
      '10.000'    -> 10000.0  (dot in groups of three: thousands)

    Strips currency symbols (euro sign, EUR) and surrounding whitespace.
    Returns 0.0 for empty or un-parseable input.
    """
    s = re.sub(r"[€EUReur\s]", "", (raw or "").strip())
    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s or s == "-":
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # European: 1.234,56 -> dot is thousands, comma is decimal
            s = s.replace(".", "").replace(",", ".")
        else:
            # Unusual: 1,234.56 -> comma is thousands, dot is decimal
            s = s.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_unsigned_amount(raw: Union[str, None]) -> Optional[float]:
    """Parse a money amount in either European or US notation, as a magnitude.

    The last of ``,``/``.`` is the decimal point when both appear. A single
    separator kind in groups of three is a thousands separator ("10.000",
    "10,000"), since money has at most two decimals; otherwise a lone comma is
    a decimal comma. Currency symbols are ignored.

    Returns None for empty or unparseable input.
    """
    s = re.sub(r"[^0-9,.\-]", "", (raw or "").strip())
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", s):
        s = s.replace(".", "").replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return None
