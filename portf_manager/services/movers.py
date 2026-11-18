"""Biggest latest price moves among the positions held.

Pure: the caller supplies the holdings and, per asset, its most recent stored
prices. A move is the change between an asset's last two stored closes, so it
is "since the previous price", which is a day for most assets but longer over a
weekend or for an asset priced less often.
"""

from datetime import date, timedelta
from typing import Dict, List, Optional

# An asset whose newest price is older than this is not a current move
STALE_AFTER_DAYS = 7


def compute_movers(
    holdings: List[Dict],
    prices_by_asset: Dict[int, List[Dict]],
    today: date,
    limit: int = 3,
) -> Dict:
    """Rank held positions by the EUR effect of their latest price move.

    Args:
        holdings: Rows from the holdings endpoint (``asset_id``, ``symbol``,
            ``name``, ``asset_type``, ``total_value_eur``).
        prices_by_asset: Per asset id, stored prices newest first; each row has
            ``price`` and ``price_date``.
        today: The reference day for the staleness cut-off.
        limit: How many gainers and how many losers to return.

    Returns:
        ``{up, down, as_of, counted, skipped}``. ``up`` and ``down`` hold rows
        ``{asset_id, symbol, name, change_pct, change_eur, price_date,
        prev_date}`` sorted by the size of ``change_eur``. ``skipped`` counts
        held positions with no usable pair of prices (one price only, a stale
        price, or cash).
    """
    cutoff = (today - timedelta(days=STALE_AFTER_DAYS)).isoformat()
    rows: List[Dict] = []
    skipped = 0
    for h in holdings:
        pair = _latest_pair(prices_by_asset.get(h.get("asset_id")) or [])
        if h.get("asset_type") == "cash" or pair is None or pair[0][1] < cutoff:
            skipped += 1
            continue
        (price, price_date), (prev, prev_date) = pair
        ratio = price / prev
        value = float(h.get("total_value_eur") or 0.0)
        rows.append(
            {
                "asset_id": h.get("asset_id"),
                "symbol": h.get("symbol") or "",
                "name": h.get("name") or "",
                "change_pct": round((ratio - 1) * 100, 2),
                # Today's value less what the same quantity was worth before
                "change_eur": round(value - value / ratio, 2),
                "price_date": price_date,
                "prev_date": prev_date,
            }
        )
    up = sorted(
        (r for r in rows if r["change_eur"] > 0), key=lambda r: -r["change_eur"]
    )
    down = sorted(
        (r for r in rows if r["change_eur"] < 0), key=lambda r: r["change_eur"]
    )
    return {
        "up": up[:limit],
        "down": down[:limit],
        "as_of": max((r["price_date"] for r in rows), default=None),
        "counted": len(rows),
        "skipped": skipped,
    }


def _latest_pair(prices: List[Dict]) -> Optional[tuple]:
    """The two newest usable prices as ``((price, date), (prev, prev_date))``."""
    usable = []
    for p in prices:
        try:
            value = float(p.get("price"))
        except (TypeError, ValueError):
            continue
        if value > 0 and p.get("price_date"):
            usable.append((value, str(p["price_date"])[:10]))
        if len(usable) == 2:
            return usable[0], usable[1]
    return None
