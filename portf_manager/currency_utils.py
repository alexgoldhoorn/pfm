"""
Currency normalization helpers.

Yahoo Finance quotes some UK-listed securities in GBX (pence), not GBP. Prices
and any monetary amounts imported in pence must be divided by 100 to become GBP,
otherwise cost basis is inflated 100×. This module detects GBX symbols (cached)
and normalizes imported transaction values.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# symbol -> is-quoted-in-GBX (cached for the process lifetime)
_GBX_CACHE: dict[str, bool] = {}


def is_gbx(symbol: str) -> bool:
    """Return True if *symbol* is quoted in GBX (pence) on Yahoo Finance.

    Cached per symbol. Network/lookup failures default to False (no change).
    """
    if not symbol:
        return False
    key = symbol.upper()
    if key in _GBX_CACHE:
        return _GBX_CACHE[key]
    result = False
    try:
        import yfinance as yf

        result = yf.Ticker(key).fast_info.currency == "GBp"
    except Exception:
        result = False
    _GBX_CACHE[key] = result
    return result


def normalize_gbx_amounts(
    symbol: str,
    price: float,
    total_amount: Optional[float] = None,
    fees: Optional[float] = None,
    currency: Optional[str] = None,
):
    """Normalize pence→pounds for GBX-quoted symbols.

    If *symbol* is GBX-quoted, divides price/total_amount/fees by 100 and sets
    the currency to ``"GBP"``. Otherwise returns the inputs unchanged.

    Returns:
        Tuple of (price, total_amount, fees, currency) with the same Nones
        preserved for omitted optional args.
    """
    if not is_gbx(symbol):
        return price, total_amount, fees, currency
    logger.info("%s is GBX-quoted — normalizing imported amounts ÷100", symbol)
    new_price = price / 100.0 if price is not None else price
    new_total = total_amount / 100.0 if total_amount is not None else total_amount
    new_fees = fees / 100.0 if fees is not None else fees
    return new_price, new_total, new_fees, "GBP"
