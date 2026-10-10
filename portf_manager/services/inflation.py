"""Spanish consumer-price inflation, for turning nominal returns into real ones.

Source: the Harmonised Index of Consumer Prices (HICP) for Spain, monthly index
(2015 = 100), published by Eurostat and served by the ECB Data Portal as series
``ICP.M.ES.N.000000.4.INX``. HICP is the EU-comparable measure the ECB's 2%
target refers to; Spain's own CPI (INE's IPC) runs very close to it.

There is no fallback: when the ECB can't be reached, real returns are reported
as unavailable rather than computed against a guessed inflation rate.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Optional

import httpx

from portf_manager.cache import cached

logger = logging.getLogger(__name__)

HICP_URL = "https://data-api.ecb.europa.eu/service/data/ICP/M.ES.N.000000.4.INX"
SOURCE_LABEL = "HICP Spain (Eurostat, via the ECB Data Portal)"


def _fetch_hicp(start_month: str) -> Optional[dict[str, float]]:
    """Monthly HICP index values from *start_month* (``YYYY-MM``), or None."""
    try:
        resp = httpx.get(
            HICP_URL,
            params={"startPeriod": start_month, "format": "csvdata"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"HICP fetch failed: {e}")
        return None
    index: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        try:
            index[row["TIME_PERIOD"][:7]] = float(row["OBS_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
    return index or None


def hicp_index(db, start: date) -> Optional[dict[str, float]]:
    """``{"YYYY-MM": index}`` from the December before *start*, cached 24h.

    Starting a month early gives the base for the first calendar year.
    """
    base = date(start.year - 1, 12, 1).strftime("%Y-%m")
    return cached(db, f"infl:hicp-es:{base}", 24 * 3600, lambda: _fetch_hicp(base))


def inflation_between(
    index: dict[str, float], start_month: str, end_month: str
) -> Optional[float]:
    """Price change from *start_month* to *end_month* (fraction), or None.

    Uses the latest published month at or before *end_month*, since the index
    is released about two weeks after the month ends.
    """
    if start_month not in index:
        return None
    available = [m for m in index if m <= end_month]
    if not available:
        return None
    last = max(available)
    if last <= start_month:
        return None
    return index[last] / index[start_month] - 1


def latest_month(index: dict[str, float], end_month: str) -> Optional[str]:
    """The latest published month at or before *end_month*."""
    available = [m for m in index if m <= end_month]
    return max(available) if available else None


def real_return(nominal: float, inflation: float) -> float:
    """Fisher relation: ``(1 + nominal) / (1 + inflation) − 1`` (fractions)."""
    return (1 + nominal) / (1 + inflation) - 1
