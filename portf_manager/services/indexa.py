"""Indexa Capital account valuations: the provider-reported value of an account.

Positions computed from transactions can lag the broker; for Indexa accounts the
API's own total is the better figure, so it is stored (with its date) in
``account_valuations`` and the balance sheet prefers it while it is fresh.
"""

import json
import logging
from datetime import date, datetime
from typing import Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

BASE_URL = "https://api.indexacapital.com"
TIMEOUT_SECONDS = 15


def _get(path: str, token: str) -> dict:
    """GET *path* from the Indexa API and decode the JSON body.

    The only network call in this module; tests replace it.

    Args:
        path: Path starting with ``/``.
        token: Indexa API token.

    Returns:
        The decoded JSON response.
    """
    request = Request(BASE_URL + path, headers={"X-AUTH-TOKEN": token})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_portfolio(account_ref: str, token: str) -> dict:
    """Fetch an account's current value, cash and the date they refer to.

    Args:
        account_ref: Indexa account number.
        token: Indexa API token.

    Returns:
        ``{"value": float, "cash": float, "as_of": "YYYY-MM-DD"}``.

    Raises:
        KeyError: If the response has no ``portfolio.total_amount``.
    """
    data = _get(f"/accounts/{account_ref}/portfolio", token)
    portfolio = data["portfolio"]
    stamp = data.get("date") or portfolio.get("date")
    return {
        "value": float(portfolio["total_amount"]),
        "cash": float(portfolio.get("cash_amount") or 0.0),
        "as_of": str(stamp)[:10] if stamp else date.today().isoformat(),
    }


def refresh_account_valuations(
    db,
    token: Optional[str],
    today: Optional[date] = None,
    only_portfolio: Optional[int] = None,
) -> list[dict]:
    """Refresh every Indexa valuation row; a failure keeps the old value.

    Args:
        db: Database instance.
        token: Indexa API token; None or empty does nothing.
        today: Unused clock seam kept for callers that pin the date.
        only_portfolio: Refresh just this portfolio's row.

    Returns:
        One ``{"portfolio_id", "ok", "error"}`` dict per Indexa row.
    """
    if not token:
        return []
    results = []
    for row in db.get_account_valuations():
        if row["provider"] != "indexa":
            continue
        if only_portfolio is not None and row["portfolio_id"] != only_portfolio:
            continue
        pid = row["portfolio_id"]
        fetched_at = datetime.now().isoformat(timespec="seconds")
        try:
            got = fetch_portfolio(row["account_ref"], token)
            db.upsert_account_valuation(
                pid,
                row["provider"],
                row["account_ref"],
                got["value"],
                got["cash"],
                row["currency"],
                got["as_of"],
                fetched_at,
                last_error=None,
            )
            results.append({"portfolio_id": pid, "ok": True, "error": None})
        except Exception as e:
            logger.warning("Indexa refresh failed for portfolio %s: %s", pid, e)
            db.upsert_account_valuation(
                pid,
                row["provider"],
                row["account_ref"],
                row["value"],
                row["cash"],
                row["currency"],
                row["as_of"],
                fetched_at,
                last_error=str(e),
            )
            results.append({"portfolio_id": pid, "ok": False, "error": str(e)})
    return results
