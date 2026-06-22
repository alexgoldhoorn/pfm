"""Shared parsing utilities for broker CSV parsers."""

import re
from typing import Union


def parse_european_number(raw: Union[str, None]) -> float:
    """Parse a European-formatted number to float.

    Handles dot-as-thousands-separator and comma-as-decimal:
      '1.583,25'  -> 1583.25
      '695,33'    -> 695.33
      '-1.488,58' -> -1488.58
      '1200'      -> 1200.0

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
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0
