"""Broker-name mapping between pfm portfolios and PDT brokers.

PDT validates the Broker column of its import against a fixed list (the
template's ``Brokers`` tab). A pfm portfolio named after a broker PDT does not
list makes every one of its rows fail with "Selected broker is invalid". The
``pdt_broker_map`` app setting maps such a portfolio name to a PDT broker on
export, and back to the portfolio on import, so pfm keeps its own name.

The setting is a JSON object ``{"<pfm portfolio name>": "<PDT broker>"}``.
"""

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

SETTING_KEY = "pdt_broker_map"


def parse_broker_map(raw: Optional[str]) -> Dict[str, str]:
    """Parse the stored setting; anything malformed is an empty map.

    Args:
        raw: The JSON string stored in ``app_settings``, or None.

    Returns:
        A dict of non-empty pfm name to non-empty PDT name.
    """
    if not raw or not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed %s setting", SETTING_KEY)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring %s setting that is not an object", SETTING_KEY)
        return {}
    return {
        str(k).strip(): str(v).strip()
        for k, v in data.items()
        if str(k).strip() and v is not None and str(v).strip()
    }


def load_broker_map(db: Any) -> Dict[str, str]:
    """Read the broker map from the database, empty when unavailable.

    Args:
        db: A database adapter with ``get_setting``; other objects (the CLI's
            HTTP client) give an empty map.

    Returns:
        The parsed broker map.
    """
    getter = getattr(db, "get_setting", None)
    if getter is None:
        return {}
    try:
        return parse_broker_map(getter(SETTING_KEY))
    except Exception as e:
        logger.warning("Could not read %s: %s", SETTING_KEY, e)
        return {}


def to_pdt(name: Optional[str], broker_map: Dict[str, str]) -> str:
    """Return the PDT broker name for a pfm portfolio name.

    Args:
        name: The pfm portfolio name.
        broker_map: The map from ``load_broker_map``.

    Returns:
        The mapped PDT name, else the name unchanged ("" for None).
    """
    if not name:
        return ""
    return broker_map.get(name, name)


def from_pdt(name: Optional[str], broker_map: Dict[str, str]) -> Optional[str]:
    """Return the pfm portfolio name for a PDT broker name.

    Matching is case-insensitive. When several portfolios map to the same PDT
    broker, the first one in the map wins.

    Args:
        name: The broker name read from a PDT sheet or file.
        broker_map: The map from ``load_broker_map``.

    Returns:
        The pfm portfolio name, else the name unchanged.
    """
    if not name:
        return name
    key = name.strip().casefold()
    for pfm_name, pdt_name in broker_map.items():
        if pdt_name.casefold() == key:
            return pfm_name
    return name


def unknown_brokers(names: Iterable[str], valid: Iterable[str]) -> List[str]:
    """List exported broker names that PDT will reject.

    Args:
        names: Broker names written to the export.
        valid: PDT's accepted broker names (its ``Brokers`` tab).

    Returns:
        Sorted names not in ``valid`` (case-insensitive), empty names left out.
    """
    accepted = {v.strip().casefold() for v in valid if v and v.strip()}
    return sorted(
        {n for n in names if n and n.strip() and n.strip().casefold() not in accepted}
    )
