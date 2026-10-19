"""HTTP access to the pfm API and the formatting helpers the tools share."""

import json
import urllib.request
from datetime import date
from typing import Optional

from pfm_mcp import config


# ── Internal helpers ──────────────────────────────────────────────────────────
# The transactions API returns only its newest 100 rows when `limit` is omitted.
FULL_HISTORY_LIMIT = 100_000


def _get(path: str, params: Optional[dict] = None) -> dict | list:
    url = f"{config.settings().url}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"X-API-Key": config.settings().api_key})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _fmt_currency(value: float, currency: str = "EUR") -> str:
    return f"{value:,.2f} {currency}"


def _today() -> date:
    """Today's date — a seam so tests can pin the calendar."""
    return date.today()


def _na(value, fmt=None) -> str:
    """Format a possibly missing value; a missing one is 'n/a', never 0."""
    if value is None:
        return "n/a"
    return fmt(value) if fmt else str(value)
