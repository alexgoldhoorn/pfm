"""Ongoing charges (TER) across the held funds.

The ongoing-charges figure is the annual cost of a fund as a percentage of its
net asset value, the number on the UCITS KIID. It is stored per fund profile
(``fund_profiles.ongoing_charges_pct``) and nothing is guessed: a fund without
one is listed as uncovered, never counted as free.
"""

from typing import Any, Callable, Optional

from portf_manager.services.exposure import compute_exposure

# Gross return assumed for the cost projection. Chosen, not forecast: a round
# long-run equity-ish figure so the effect of costs is comparable between funds.
ASSUMED_GROSS_RETURN_PCT = 5.0

# Horizons (years) of the projection.
PROJECTION_YEARS = (10, 20, 30)


def _projection(funds: list[tuple[float, float]], years: int, gross_pct: float) -> dict:
    """Cost of the charges over *years* and the reduction in yield.

    Args:
        funds: (value_eur, ongoing_charges_pct) pairs.
        years: Horizon in years.
        gross_pct: Assumed gross annual return in percent.

    Returns:
        ``cost_eur``: sum over funds of v((1+r)^H - (1+r-t)^H), the growth lost
        to charges. ``riy_pct``: the reduction in yield, PRIIPs style, as the
        gross return minus the value-weighted net annualised return.
    """
    r = gross_pct / 100
    total = sum(v for v, _ in funds)
    gross_end = sum(v * (1 + r) ** years for v, _ in funds)
    net_end = sum(v * (1 + r - t / 100) ** years for v, t in funds)
    net_return = (net_end / total) ** (1 / years) - 1
    return {
        "years": years,
        "cost_eur": round(gross_end - net_end, 2),
        "riy_pct": round((r - net_return) * 100, 3),
    }


def compute_ongoing_charges(
    db: Any, fx: Optional[Callable[[str], float]] = None
) -> dict:
    """Value-weighted ongoing charges of the held funds, with a cost projection.

    Fund positions and their EUR values come from ``compute_exposure``: its
    ``funds`` list holds profiled funds and ``coverage.unprofiled`` the rest, so
    together they are every held fund-like asset with a positive value.

    Args:
        db: PortfolioDatabase instance.
        fx: Currency -> EUR rate function (defaults to compute_exposure's).

    Returns:
        Dict with ``weighted_avg_pct`` (None when no fund has a TER),
        ``annual_cost_eur``, ``covered_value_eur``, ``fund_value_eur``,
        ``uncovered`` ([{asset_id, symbol, name, value_eur}]) and ``projection`` (one
        entry per horizon in PROJECTION_YEARS; empty when nothing is covered).
    """
    exposure = compute_exposure(db, fx=fx)
    held = [
        (f["asset_id"], f["symbol"], f["name"], f["value_eur"])
        for f in exposure["funds"]
    ] + [
        (f["asset_id"], f["symbol"], f["name"], f["value_eur"])
        for f in exposure["coverage"]["unprofiled"]
    ]

    covered: list[tuple[float, float]] = []
    uncovered: list[dict] = []
    for asset_id, symbol, name, value in held:
        profile = db.get_fund_profile(asset_id)
        pct = profile.get("ongoing_charges_pct") if profile else None
        if pct is None:
            uncovered.append(
                {
                    "asset_id": asset_id,
                    "symbol": symbol,
                    "name": name,
                    "value_eur": value,
                }
            )
        else:
            covered.append((float(value), float(pct)))

    covered_value = sum(v for v, _ in covered)
    annual_cost = sum(v * t / 100 for v, t in covered)
    return {
        "weighted_avg_pct": (
            round(annual_cost / covered_value * 100, 3) if covered_value else None
        ),
        "annual_cost_eur": round(annual_cost, 2),
        "covered_value_eur": round(covered_value, 2),
        "fund_value_eur": round(sum(v for *_, v in held), 2),
        "uncovered": sorted(uncovered, key=lambda f: -f["value_eur"]),
        "assumed_gross_return_pct": ASSUMED_GROSS_RETURN_PCT,
        "projection": (
            [
                _projection(covered, h, ASSUMED_GROSS_RETURN_PCT)
                for h in PROJECTION_YEARS
            ]
            if covered
            else []
        ),
    }
