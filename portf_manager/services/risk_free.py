"""Risk-free rate for Sharpe, Sortino and alpha: the euro short-term rate (€STR).

The Sharpe ratio is defined on *excess* return over a risk-free rate (Sharpe,
1994, "The Sharpe Ratio", Journal of Portfolio Management). For a euro investor
the standard overnight risk-free rate is the ECB's €STR, so the rate used is the
average published €STR over the measurement window.

Source: ECB Data Portal, €STR volume-weighted trimmed mean rate (% per year),
series key in :data:`ESTR_URL`. When the ECB can't be reached the rate falls back
to the ``risk_free_rate_pct`` app setting, then to :data:`DEFAULT_RATE_PCT`, and
the result says which source it came from — a fallback is never passed off as
the published rate.
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

# The ECB's series key looks like an ISIN, but names the €STR rate series.
ESTR_URL = "https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT"  # allow-financial
# Fallback only: the ECB deposit facility rate set in June 2025 (€STR trades
# just below it). risk_free_source says when this was used.
DEFAULT_RATE_PCT = 2.0
SETTING_KEY = "risk_free_rate_pct"


def _fetch_estr(start: str, end: str) -> Optional[list[float]]:
    """Daily €STR fixings (% per year) between *start* and *end*, or None.

    Args:
        start: First date, ``YYYY-MM-DD``.
        end: Last date, ``YYYY-MM-DD``.
    """
    try:
        resp = httpx.get(
            ESTR_URL,
            params={"startPeriod": start, "endPeriod": end, "format": "csvdata"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"€STR fetch failed: {e}")
        return None
    values = []
    for row in csv.DictReader(io.StringIO(resp.text)):
        try:
            values.append(float(row["OBS_VALUE"]))
        except (KeyError, TypeError, ValueError):
            continue
    return values or None


def risk_free_rate(db, start: date, end: date) -> tuple[float, str]:
    """Average annual risk-free rate over ``[start, end]`` as a fraction.

    Args:
        db: Database handle (kv_cache and app settings); may be None.
        start: First day of the measurement window.
        end: Last day of the measurement window.

    Returns:
        ``(rate, source)`` — rate as a fraction (0.02 = 2%/yr); source is
        ``"ecb_estr"``, ``"setting"`` or ``"default"``.
    """
    key = f"rf:estr:{start.isoformat()}:{end.isoformat()}"
    fixings = cached(
        db, key, 24 * 3600, lambda: _fetch_estr(start.isoformat(), end.isoformat())
    )
    if fixings:
        return sum(fixings) / len(fixings) / 100, "ecb_estr"
    if db is not None:
        try:
            raw = db.get_setting(SETTING_KEY)
            if raw not in (None, ""):
                return float(raw) / 100, "setting"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not read {SETTING_KEY}: {e}")
    return DEFAULT_RATE_PCT / 100, "default"
