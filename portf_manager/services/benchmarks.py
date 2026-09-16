"""Benchmark index table — region weights for the indices funds track.

yfinance exposes a fund's sector weights but no geography at all, so region
exposure is resolved by mapping a fund to the index it tracks and reading that
index's region weights from ``portf_manager/data/benchmarks.json``.

The file is hand-maintained public data. ``validate_benchmarks`` is run against
it by the test suite so an edit cannot silently break the shape.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The fixed region taxonomy. Region rather than country: it is granular enough
# for allocation targets and cheap enough to keep current by hand.
REGIONS: tuple[str, ...] = (
    "north_america",
    "europe_ex_uk",
    "uk",
    "japan",
    "pacific_ex_japan",
    "emerging",
    "unknown",
)

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmarks.json"

# Parsed file, kept for the process's lifetime. Tests patch this directly.
_CACHE: Optional[dict[str, dict]] = None


def load_benchmarks() -> dict[str, dict]:
    """Return the benchmark table, reading the JSON file once per process."""
    global _CACHE
    if _CACHE is None:
        try:
            with open(_DATA_PATH, encoding="utf-8") as fh:
                _CACHE = json.load(fh)
        except Exception as e:
            logger.error(f"Could not load benchmarks.json: {e}")
            _CACHE = {}
    return _CACHE


def get_benchmark(key: str) -> Optional[dict]:
    """Return one benchmark entry, or None when the key is unknown."""
    return load_benchmarks().get(key)


def benchmark_choices() -> list[dict]:
    """Return the table as dropdown options, sorted by label."""
    out = [
        {
            "key": key,
            "label": entry.get("label", key),
            "family": entry.get("family"),
            "asset_class": entry.get("asset_class", {}),
        }
        for key, entry in load_benchmarks().items()
    ]
    return sorted(out, key=lambda c: c["label"])


def ancestors(key: str) -> list[str]:
    """Return the ``parent`` chain above *key*, nearest first.

    Used by overlap detection to spot a fund nested inside a broader one (an
    S&P 500 fund held alongside a world fund). Cycle-safe: a mis-edited file
    that loops stops instead of hanging.
    """
    data = load_benchmarks()
    chain: list[str] = []
    seen = {key}
    current = data.get(key, {}).get("parent")
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = data.get(current, {}).get("parent")
    return chain


def validate_benchmarks(data: dict) -> list[str]:
    """Return a list of problems with *data*, empty when it is valid."""
    problems: list[str] = []
    for key, entry in data.items():
        for field in ("label", "family", "asset_class", "regions", "as_of"):
            if field not in entry:
                problems.append(f"{key}: missing '{field}'")
        regions = entry.get("regions", {})
        unknown = set(regions) - set(REGIONS)
        if unknown:
            problems.append(f"{key}: unknown regions {sorted(unknown)}")
        if regions:
            total = sum(regions.values())
            if abs(total - 1.0) >= 0.005:
                problems.append(f"{key}: regions sum to {total:.3f}, expected 1.0")
        classes = entry.get("asset_class", {})
        if classes:
            total = sum(classes.values())
            if abs(total - 1.0) >= 0.005:
                problems.append(f"{key}: asset_class sums to {total:.3f}, expected 1.0")
        parent = entry.get("parent")
        if parent is not None and parent not in data:
            problems.append(f"{key}: parent '{parent}' is not a known benchmark")
    return problems
