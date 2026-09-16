"""Portfolio exposure with funds looked through.

The single source for every breakdown the diversification view shows. A fund's
value is split across asset class, region and sector using its stored profile;
a direct holding keeps the per-symbol yfinance lookup; a fund with no profile is
reported by name rather than folded anonymously into "Unknown".

Both /analytics/diversification and portfolio_advisor.gather_diversification
call this, so the two can no longer disagree.
"""

import logging
from datetime import date
from typing import Any, Callable, Optional

from portf_manager.positions import compute_positions
from portf_manager.services.fund_profiles import (
    FUND_ASSET_TYPES,
    is_stale,
    parse_profile,
)
from portf_manager.services.portfolio_advisor import _resolve_sector_country

logger = logging.getLogger(__name__)

# Country names as yfinance reports them, mapped to the fixed region taxonomy.
# Anything unlisted falls through to "unknown" and shows up in coverage.
_REGION_BY_COUNTRY: dict[str, str] = {
    "United States": "north_america",
    "Canada": "north_america",
    "Mexico": "north_america",
    "United Kingdom": "uk",
    "Japan": "japan",
    "Australia": "pacific_ex_japan",
    "New Zealand": "pacific_ex_japan",
    "Singapore": "pacific_ex_japan",
    "Hong Kong": "pacific_ex_japan",
    "China": "emerging",
    "Taiwan": "emerging",
    "South Korea": "emerging",
    "India": "emerging",
    "Brazil": "emerging",
    "South Africa": "emerging",
    "Netherlands": "europe_ex_uk",
    "France": "europe_ex_uk",
    "Germany": "europe_ex_uk",
    "Spain": "europe_ex_uk",
    "Italy": "europe_ex_uk",
    "Switzerland": "europe_ex_uk",
    "Sweden": "europe_ex_uk",
    "Denmark": "europe_ex_uk",
    "Norway": "europe_ex_uk",
    "Finland": "europe_ex_uk",
    "Belgium": "europe_ex_uk",
    "Ireland": "europe_ex_uk",
    "Austria": "europe_ex_uk",
    "Portugal": "europe_ex_uk",
    "Luxembourg": "europe_ex_uk",
}

# Approximate currency of each region's underlying assets. europe_ex_uk is
# mostly but not only EUR, and emerging is a basket — this is labelled as an
# approximation everywhere it is shown.
_CURRENCY_BY_REGION: dict[str, str] = {
    "north_america": "USD",
    "europe_ex_uk": "EUR",
    "uk": "GBP",
    "japan": "JPY",
    "pacific_ex_japan": "AUD",
    "emerging": "EM basket",
    "unknown": "Unknown",
}

# Where a fund's value lands in the country breakdown, which stays a
# direct-holdings view. Naming it beats leaving the value in "Unknown".
VIA_FUNDS_LABEL = "Via funds (see regions)"

UNKNOWN = "unknown"


def _default_fx(currency: str) -> float:
    """EUR conversion rate. Lazy import, same shim the other services use."""
    from portf_server.routers.portfolios import _get_fx_rate

    return _get_fx_rate(currency)


def _add(target: dict, key: str, value: float) -> None:
    target[key] = target.get(key, 0.0) + value


def compute_exposure(
    db: Any,
    portfolio_id: Optional[int] = None,
    fx: Optional[Callable[[str], float]] = None,
    today: Optional[date] = None,
) -> dict:
    """Every exposure breakdown for the held portfolio, funds looked through."""
    fx = fx or _default_fx
    today = today or date.today()

    txns = db.get_all_transactions(portfolio_id=portfolio_id)
    positions, _ = compute_positions(txns)
    portfolio_names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    # A position's portfolio is whichever one its transactions belong to.
    portfolio_by_asset: dict[int, str] = {}
    for tx in txns:
        name = portfolio_names.get(tx.get("portfolio_id"), "Unassigned")
        portfolio_by_asset.setdefault(tx["asset_id"], name)

    by_type: dict[str, float] = {}
    by_class: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    by_currency_exposure: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_country: dict[str, float] = {}
    by_region: dict[str, float] = {}
    by_region_equity: dict[str, float] = {}
    by_position: dict[str, float] = {}
    position_names: dict[str, str] = {}

    total = 0.0
    region_known = 0.0
    sector_known = 0.0
    unprofiled: list[dict] = []
    stale_profiles: list[dict] = []
    funds: list[dict] = []

    for asset_id, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        price_row = db.get_latest_price(asset_id)
        price = float(price_row["price"]) if price_row else 0.0
        currency = asset.get("currency", "EUR")
        value = pos["quantity"] * price * fx(currency)
        if value <= 0:
            continue

        symbol = asset["symbol"]
        atype = asset.get("asset_type", "other")
        total += value
        _add(by_position, symbol, value)
        position_names[symbol] = asset.get("name", symbol)
        _add(by_type, atype, value)
        _add(by_currency, currency, value)

        if atype == "crypto":
            _add(by_class, "crypto", value)
            _add(by_currency_exposure, "Crypto", value)
            _add(by_country, "Global", value)
            region_known += value
            sector_known += value
            continue

        if atype in FUND_ASSET_TYPES:
            profile = parse_profile(db.get_fund_profile(asset_id))
            if not profile or not profile.get("regions"):
                _add(by_class, UNKNOWN, value)
                _add(by_region, UNKNOWN, value)
                _add(by_country, VIA_FUNDS_LABEL, value)
                _add(by_currency_exposure, "Unknown", value)
                unprofiled.append(
                    {
                        "asset_id": asset_id,
                        "symbol": symbol,
                        "name": asset.get("name", symbol),
                        "value_eur": round(value, 2),
                    }
                )
                continue

            if is_stale(profile, today):
                stale_profiles.append(
                    {
                        "asset_id": asset_id,
                        "symbol": symbol,
                        "name": asset.get("name", symbol),
                        "as_of": profile.get("as_of"),
                    }
                )

            classes = profile["asset_class"] or {"equity": 1.0}
            for class_name, share in classes.items():
                _add(by_class, class_name, value * share)
            equity_value = value * float(classes.get("equity", 0.0))

            for region, share in profile["regions"].items():
                _add(by_region, region, value * share)
                if equity_value:
                    _add(by_region_equity, region, equity_value * share)
            region_known += value
            _add(by_country, VIA_FUNDS_LABEL, value)

            sectors = profile.get("sectors") or {}
            if sectors and equity_value:
                for sector, share in sectors.items():
                    _add(by_sector, sector, equity_value * share)
                sector_known += equity_value
            elif equity_value:
                _add(by_sector, "Unknown", equity_value)
            # A bond sleeve has no sector, so it counts as classified.
            sector_known += value - equity_value

            if profile.get("currency_hedged"):
                hedge = profile.get("hedge_currency") or currency
                _add(by_currency_exposure, hedge, value)
            else:
                for region, share in profile["regions"].items():
                    _add(
                        by_currency_exposure,
                        _CURRENCY_BY_REGION.get(region, "Unknown"),
                        value * share,
                    )

            funds.append(
                {
                    "asset_id": asset_id,
                    "symbol": symbol,
                    "name": asset.get("name", symbol),
                    "portfolio_name": portfolio_by_asset.get(asset_id, "Unassigned"),
                    "value_eur": round(value, 2),
                    "asset_type": atype,
                    "benchmark_key": profile.get("benchmark_key"),
                    "regions": profile["regions"],
                    "sectors": sectors,
                    "asset_class": classes,
                }
            )
            continue

        # Direct holding: the existing per-symbol resolution.
        sector, country = _resolve_sector_country(db, asset)
        _add(by_sector, sector, value)
        _add(by_country, country, value)
        if sector != "Unknown":
            sector_known += value
        region = _REGION_BY_COUNTRY.get(country, UNKNOWN)
        _add(by_region, region, value)
        if region != UNKNOWN:
            region_known += value
        _add(by_class, "bond" if atype == "bond" else "equity", value)
        _add(by_currency_exposure, currency, value)

    def pct_map(source: dict) -> dict:
        if not total:
            return {}
        return {
            key: round(value / total * 100, 1)
            for key, value in sorted(source.items(), key=lambda kv: -kv[1])
            if round(value / total * 100, 1) > 0
        }

    hhi = (
        round(sum((v / total) ** 2 for v in by_position.values()) * 10000, 0)
        if total
        else 0.0
    )
    largest_symbol = max(by_position, key=by_position.get) if by_position else None
    largest_pct = (
        round(by_position[largest_symbol] / total * 100, 1)
        if total and largest_symbol
        else 0
    )

    return {
        "total_value_eur": round(total, 2),
        "by_asset_type": pct_map(by_type),
        "by_asset_class": pct_map(by_class),
        "by_currency": pct_map(by_currency),
        "by_currency_exposure": pct_map(by_currency_exposure),
        "by_sector": pct_map(by_sector),
        "by_country": pct_map(by_country),
        "by_region": pct_map(by_region),
        "by_region_equity": pct_map(by_region_equity),
        "concentration_hhi": hhi,
        "largest_position_pct": largest_pct,
        "largest_position_symbol": largest_symbol,
        "largest_position_name": position_names.get(largest_symbol, largest_symbol),
        "coverage": {
            "classified_pct": round(region_known / total * 100, 1) if total else 0.0,
            "sector_classified_pct": (
                round(sector_known / total * 100, 1) if total else 0.0
            ),
            "unprofiled": sorted(unprofiled, key=lambda f: -f["value_eur"]),
            "stale_profiles": stale_profiles,
        },
        "funds": funds,
    }
