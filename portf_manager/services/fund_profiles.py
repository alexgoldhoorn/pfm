"""Fund profiles — the stored weight maps that make a fund see-through.

A profile is stored rather than computed per request so it can be reviewed and
corrected, and so a diversification call needs no live fund lookup. The DB layer
keeps the weight maps as JSON text; parsing and validation live here, the same
split budget_lines.overrides uses.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Optional

from portf_manager import market
from portf_manager.services import benchmarks

logger = logging.getLogger(__name__)

# Asset types that need a profile to be seen through.
FUND_ASSET_TYPES: frozenset[str] = frozenset({"etf", "mutual_fund", "index"})

_SOURCES = ("benchmark", "llm", "manual")

# A profile older than this is reported stale — index weights drift a few points
# a year, which is slow enough to be worth an annual review, not a refetch.
STALE_AFTER_DAYS = 365

_JSON_COLUMNS = ("asset_class", "regions", "sectors")


def _loads(value: Any) -> dict:
    """Parse a JSON weight map, returning {} for anything malformed."""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_profile(row: Optional[dict]) -> Optional[dict]:
    """Turn a DB row into a profile with real dicts and a bool hedge flag."""
    if row is None:
        return None
    profile = dict(row)
    for column in _JSON_COLUMNS:
        profile[column] = _loads(profile.get(column))
    profile["currency_hedged"] = bool(profile.get("currency_hedged"))
    return profile


def serialize_profile(profile: dict) -> dict:
    """Turn a profile into the keyword arguments db.upsert_fund_profile takes."""
    return {
        "asset_id": profile["asset_id"],
        "benchmark_key": profile.get("benchmark_key"),
        "source": profile.get("source", "manual"),
        "asset_class": json.dumps(profile.get("asset_class", {})),
        "regions": json.dumps(profile.get("regions", {})),
        "sectors": json.dumps(profile.get("sectors", {})),
        "currency_hedged": 1 if profile.get("currency_hedged") else 0,
        "hedge_currency": profile.get("hedge_currency"),
        "as_of": profile.get("as_of"),
        "notes": profile.get("notes"),
    }


def _sums_to_one(weights: dict) -> bool:
    return abs(sum(weights.values()) - 1.0) < 0.005


def validate_profile(profile: dict) -> list[str]:
    """Return a list of problems with *profile*, empty when it is valid."""
    problems: list[str] = []

    regions = profile.get("regions") or {}
    unknown = set(regions) - set(benchmarks.REGIONS)
    if unknown:
        problems.append(f"unknown region keys: {sorted(unknown)}")
    if not regions or not _sums_to_one(regions):
        problems.append(f"regions must sum to 1.0, got {sum(regions.values()):.3f}")

    classes = profile.get("asset_class") or {}
    if not classes or not _sums_to_one(classes):
        problems.append(f"asset_class must sum to 1.0, got {sum(classes.values()):.3f}")

    # Sectors may legitimately be empty: yfinance has none for bond funds, and a
    # failed fetch must not be papered over with invented weights.
    sectors = profile.get("sectors") or {}
    if sectors and not _sums_to_one(sectors):
        problems.append(f"sectors must sum to 1.0, got {sum(sectors.values()):.3f}")

    key = profile.get("benchmark_key")
    if key is not None and benchmarks.get_benchmark(key) is None:
        problems.append(f"unknown benchmark '{key}'")

    source = profile.get("source")
    if source not in _SOURCES:
        problems.append(f"source must be one of {_SOURCES}, got '{source}'")

    return problems


def build_from_benchmark(db, asset: dict, benchmark_key: str) -> dict:
    """Build a profile: regions from the benchmark, the rest from yfinance."""
    entry = benchmarks.get_benchmark(benchmark_key)
    if entry is None:
        raise ValueError(f"unknown benchmark '{benchmark_key}'")

    symbol = asset.get("ticker") or asset["symbol"]
    composition = market.get_fund_composition(db, symbol)

    return {
        "asset_id": asset["id"],
        "benchmark_key": benchmark_key,
        "source": "benchmark",
        # yfinance's split when it answered, the benchmark's own otherwise.
        "asset_class": composition.get("asset_class") or dict(entry["asset_class"]),
        "regions": dict(entry["regions"]),
        "sectors": composition.get("sectors") or {},
        "currency_hedged": False,
        "hedge_currency": None,
        "as_of": entry.get("as_of") or date.today().isoformat(),
        "notes": None,
    }


def is_stale(profile: dict, today: date, max_age_days: int = STALE_AFTER_DAYS) -> bool:
    """True when a profile's data is older than *max_age_days*, or undated."""
    raw = profile.get("as_of")
    if not raw:
        return True
    try:
        as_of = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return (today - as_of).days > max_age_days
