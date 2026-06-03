"""Tiny DB-backed cache helper.

``cached(db, key, ttl, producer)`` returns the cached value for ``key`` if it is
present and unexpired, otherwise calls ``producer()``, stores the result, and
returns it. Used to memoise slow/stable yfinance lookups (sector/country,
fundamentals, benchmark history). Cache misses and DB errors degrade gracefully
to just calling the producer.
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def cached(
    db, key: str, ttl_seconds: Optional[float], producer: Callable[[], Any]
) -> Any:
    """Return a cached value for *key*, else produce, store and return it."""
    if db is not None:
        try:
            hit = db.cache_get(key)
            if hit is not None:
                return hit
        except Exception as e:
            logger.warning(f"cache_get failed for {key}: {e}")
    value = producer()
    # Don't cache empty/None results — let the next call retry the source.
    if db is not None and value is not None and value != {} and value != []:
        try:
            db.cache_set(key, value, ttl_seconds)
        except Exception as e:
            logger.warning(f"cache_set failed for {key}: {e}")
    return value
