"""Price-spike guard — hold an out-of-band fetched price until a later one agrees.

A yfinance glitch once stored every GBP asset ~100x too high for a day. Exchanges
treat an out-of-band print as suspect until a later print confirms it; so does
this. The rule (``evaluate``) is a pure function; the held observation lives in
``app_settings`` under ``price_guard:{asset_id}`` (no migration needed).
"""

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Largest believable move in one day. Chosen: wider than any exchange daily
# limit, far inside a x100 or /100 unit error.
MAX_DAILY_MOVE = 0.5

# A held price is confirmed by a later fetch within this fraction of it: a real
# repricing persists, a glitch does not.
CONFIRM_TOLERANCE = 0.05

SETTING_PREFIX = "price_guard:"

STORE = "store"
HOLD = "hold"
CONFIRM = "confirm"
CLEAR = "clear"


@dataclass(frozen=True)
class Decision:
    """Outcome of one guard check."""

    outcome: str
    days: int = 1
    # new / prev price; None when there is no previous price.
    ratio: Optional[float] = None


def band_limit(days: int) -> float:
    """Largest allowed |ln(new / prev)| after *days* days (sqrt-of-time growth)."""
    return math.log(1 + MAX_DAILY_MOVE) * math.sqrt(max(1, days))


def evaluate(
    prev_price: Optional[float],
    prev_date: Optional[date],
    new_price: float,
    today: date,
    held: Optional[dict],
) -> Decision:
    """Decide what to do with a freshly fetched price.

    Args:
        prev_price: Latest stored price, or None for a new asset.
        prev_date: Date of that stored price.
        new_price: The price just fetched.
        today: Today's date.
        held: The currently held observation (``{"price", "date", ...}``) or None.

    Returns:
        A Decision: ``store`` (inside the band, or nothing to compare with),
        ``hold`` (outside the band and not yet confirmed), ``confirm`` (a held
        value from an earlier day is matched within 5 %) or ``clear`` (a held
        value exists but this price is back inside the band).
    """
    if not prev_price or prev_price <= 0 or new_price <= 0 or prev_date is None:
        # Nothing sensible to compare with. A non-positive new price is not
        # this guard's job (the data-quality check flags zero prices).
        return Decision(STORE)

    days = max(1, (today - prev_date).days)
    ratio = new_price / prev_price
    inside = abs(math.log(ratio)) <= band_limit(days)

    if inside:
        return Decision(CLEAR if held else STORE, days, ratio)

    if held:
        held_price = float(held.get("price") or 0)
        held_date = held.get("date")
        earlier_day = bool(held_date) and str(held_date)[:10] < today.isoformat()
        if earlier_day and held_price > 0:
            if abs(new_price / held_price - 1) <= CONFIRM_TOLERANCE:
                return Decision(CONFIRM, days, ratio)
    return Decision(HOLD, days, ratio)


def _key(asset_id: int) -> str:
    return f"{SETTING_PREFIX}{asset_id}"


def get_held(db, asset_id: int) -> Optional[dict]:
    """The held observation for an asset, or None."""
    raw = db.get_setting(_key(asset_id))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def set_held(db, asset_id: int, observation: dict) -> None:
    """Store (or replace) the held observation for an asset."""
    db.set_setting(_key(asset_id), json.dumps(observation))


def clear_held(db, asset_id: int) -> None:
    """Drop the held observation for an asset."""
    db.delete_setting(_key(asset_id))


def list_held_prices(db) -> list[dict]:
    """Every held observation, each with its ``asset_id`` added."""
    held = []
    for key, raw in db.list_settings(SETTING_PREFIX).items():
        try:
            value = json.loads(raw)
            asset_id = int(key[len(SETTING_PREFIX) :])
        except ValueError:
            continue
        if isinstance(value, dict):
            held.append({**value, "asset_id": asset_id})
    return held
