"""Saved Wealth Simulator scenarios: projection, summary and tracking.

A scenario is the set of inputs of the Wealth Simulator page saved under a
name, with a baseline (the date and the balance-sheet net worth on the day
it was saved). The projection here mirrors ``runProjection`` in
``web_client/js/pfm_features.js`` point for point — a change to one must be
made to the other (``tests/unit/test_wealth_scenarios.py`` and
``web_client/js/tests/wealth_scenarios.test.mjs`` pin the same fixture) — so
the dashboard, the chat tool and the Action Items see the same numbers as
the page.

Tracking compares the *change* in net worth since the baseline with the
change the scenario expected over the same elapsed time. Deltas, not levels:
the simulator models liquid assets minus the mortgage while the balance
sheet also holds property and other manual lines, so the two levels differ
by a constant that cancels out.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Fixed volatilities of the buckets without their own input, as on the page.
CASH_VOL = 0.01
BONDS_VOL = 0.06
DEFAULT_STOCKS_VOL_PCT = 16.0

# Years at which the summary reports the expected net worth, if within the
# horizon; the horizon itself is always included.
SUMMARY_YEARS = (5, 10)

# A verdict needs some elapsed time: on day two every scenario reads as
# "behind" or "ahead" because the band is still a hair wide.
MIN_TRACKING_DAYS = 30

# The confidence multipliers the page offers, keyed by the band they label.
CONFIDENCE_LABELS = {0.674: "50%", 1.0: "68%", 1.96: "95%", 2.576: "99%"}

DEFAULT_PARAMS: dict[str, float] = {
    "cash_amount": 0.0,
    "cash_rate_pct": 1.5,
    "stocks_amount": 0.0,
    "stocks_rate_pct": 8.0,
    "stocks_vol_pct": DEFAULT_STOCKS_VOL_PCT,
    "stocks_contribution_monthly": 0.0,
    "bonds_amount": 0.0,
    "bonds_rate_pct": 4.0,
    "mortgage_principal": 0.0,
    "mortgage_rate_pct": 3.5,
    "mortgage_payment_monthly": 0.0,
    "years": 30,
    "confidence_z": 0.674,
}

# (min, max) per parameter; the router rejects anything outside.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "cash_amount": (0, 1e10),
    "cash_rate_pct": (-50, 50),
    "stocks_amount": (0, 1e10),
    "stocks_rate_pct": (-50, 50),
    "stocks_vol_pct": (0, 100),
    "stocks_contribution_monthly": (0, 1e8),
    "bonds_amount": (0, 1e10),
    "bonds_rate_pct": (-50, 50),
    "mortgage_principal": (0, 1e10),
    "mortgage_rate_pct": (0, 50),
    "mortgage_payment_monthly": (0, 1e8),
    "years": (1, 60),
    "confidence_z": (0.01, 5),
}


def normalize_params(raw: Optional[dict]) -> dict[str, float]:
    """Merge ``raw`` over the defaults, check types and ranges.

    Raises ``ValueError`` naming the first offending parameter. Unknown keys
    are dropped; ``years`` becomes an int.
    """
    out: dict[str, Any] = dict(DEFAULT_PARAMS)
    for key, value in (raw or {}).items():
        if key not in DEFAULT_PARAMS:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number")
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"{key} must be finite")
        lo, hi = PARAM_BOUNDS[key]
        if not (lo <= value <= hi):
            raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
        out[key] = value
    out["years"] = int(out["years"])
    return out


def params_from_json(text: Optional[str]) -> dict[str, float]:
    """Decode the stored ``params`` column; a corrupt value falls back to defaults."""
    try:
        return normalize_params(json.loads(text) if text else {})
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        logger.warning("wealth scenario params unreadable (%s); using defaults", e)
        return dict(DEFAULT_PARAMS)


# ── projection (mirrors pfm_features.js projectAccount/runProjection) ──────


def project_account_at(
    start: float,
    rate_pct: float,
    vol: float,
    t: float,
    z: float,
    monthly_contribution: float = 0.0,
    exact_origin: bool = False,
) -> dict[str, float]:
    """One bucket at ``t`` years: deterministic mean and the GBM band.

    Contributions are an ordinary annuity with annual compounding, as on the
    page. At ``t == 0`` the band uses half a year of volatility — the page's
    behaviour, kept so the two charts agree at the origin — unless
    ``exact_origin`` is set, which tracking uses so that on the baseline day
    the expected range is exactly zero.
    """
    r = rate_pct / 100.0
    grown = start * (1 + r) ** t
    if monthly_contribution <= 0:
        contributed = 0.0
    elif r > 0:
        contributed = monthly_contribution * 12 * (((1 + r) ** t - 1) / r)
    else:
        contributed = monthly_contribution * 12 * t
    mean = grown + contributed
    total_vol = vol * math.sqrt(t if (t > 0 or exact_origin) else 0.5)
    return {
        "mean": max(0.0, mean),
        "high": max(0.0, mean * math.exp(z * total_vol)),
        "low": max(0.0, mean * math.exp(-z * total_vol)),
    }


def _mortgage_after(params: dict, months: int) -> tuple[float, float, Optional[int]]:
    """Balance, cumulative interest and payoff month after ``months`` payments.

    Month by month like the page; a payment below the interest lets the
    balance grow. ``None`` payoff when the balance is still positive.
    """
    balance = float(params["mortgage_principal"])
    payment = float(params["mortgage_payment_monthly"])
    monthly_rate = params["mortgage_rate_pct"] / 100.0 / 12.0
    interest_paid = 0.0
    paid_off_month: Optional[int] = None
    if balance <= 0:
        return 0.0, 0.0, None
    for m in range(1, months + 1):
        if balance <= 0:
            break
        interest = balance * monthly_rate
        interest_paid += interest
        balance -= payment - interest
        if balance <= 0:
            balance = 0.0
            paid_off_month = m
            break
    return balance, interest_paid, paid_off_month


def _assets_at(params: dict, t: float, exact_origin: bool = False) -> dict[str, float]:
    z = float(params["confidence_z"])
    cash = project_account_at(
        params["cash_amount"],
        params["cash_rate_pct"],
        CASH_VOL,
        t,
        z,
        exact_origin=exact_origin,
    )
    stocks = project_account_at(
        params["stocks_amount"],
        params["stocks_rate_pct"],
        params["stocks_vol_pct"] / 100.0,
        t,
        z,
        params["stocks_contribution_monthly"],
        exact_origin=exact_origin,
    )
    bonds = project_account_at(
        params["bonds_amount"],
        params["bonds_rate_pct"],
        BONDS_VOL,
        t,
        z,
        exact_origin=exact_origin,
    )
    return {
        key: cash[key] + stocks[key] + bonds[key] for key in ("mean", "high", "low")
    }


def point_at(params: dict, t: float, exact_origin: bool = False) -> dict[str, float]:
    """The projection at ``t`` years (fractional allowed) from the start.

    The mortgage is amortised for the whole months elapsed.
    """
    assets = _assets_at(params, t, exact_origin)
    mortgage, _, _ = _mortgage_after(params, int(math.floor(t * 12 + 1e-9)))
    return {
        "year": t,
        "assets": assets["mean"],
        "assets_high": assets["high"],
        "assets_low": assets["low"],
        "mortgage": mortgage,
        "net_worth": assets["mean"] - mortgage,
        "net_worth_high": assets["high"] - mortgage,
        "net_worth_low": assets["low"] - mortgage,
    }


def project(params: dict) -> dict:
    """Yearly points 0..years plus the mortgage payoff year and total interest."""
    years = int(params["years"])
    points = [point_at(params, float(y)) for y in range(years + 1)]
    _, interest, paid_off_month = _mortgage_after(params, years * 12)
    paid_off_year = None
    if paid_off_month is not None:
        paid_off_year = int(math.ceil(paid_off_month / 12))
    return {
        "points": points,
        "mortgage_paid_off_year": paid_off_year,
        "total_interest_eur": interest,
    }


def summarize(params: dict) -> dict:
    """The headline figures the page's summary cards show, plus milestones."""
    proj = project(params)
    years = int(params["years"])
    start = proj["points"][0]
    final = proj["points"][-1]
    start_liquid = (
        params["cash_amount"] + params["stocks_amount"] + params["bonds_amount"]
    )
    contributed = params["stocks_contribution_monthly"] * 12 * years
    milestones = []
    for y in sorted({y for y in SUMMARY_YEARS if y < years} | {years}):
        p = proj["points"][y]
        milestones.append(
            {
                "year": y,
                "calendar_year": date.today().year + y,
                "net_worth_eur": round(p["net_worth"], 2),
                "net_worth_low_eur": round(p["net_worth_low"], 2),
                "net_worth_high_eur": round(p["net_worth_high"], 2),
                "mortgage_eur": round(p["mortgage"], 2),
            }
        )
    return {
        "horizon_years": years,
        "confidence_label": CONFIDENCE_LABELS.get(float(params["confidence_z"])),
        "start_liquid_eur": round(start_liquid, 2),
        "start_net_worth_eur": round(start["net_worth"], 2),
        "final_liquid_eur": round(final["assets"], 2),
        "final_net_worth_eur": round(final["net_worth"], 2),
        "final_net_worth_low_eur": round(final["net_worth_low"], 2),
        "final_net_worth_high_eur": round(final["net_worth_high"], 2),
        "investment_gains_eur": round(final["assets"] - start_liquid - contributed, 2),
        "total_contributions_eur": round(contributed, 2),
        "mortgage_paid_off_year": proj["mortgage_paid_off_year"],
        "total_interest_eur": round(proj["total_interest_eur"], 2),
        "milestones": milestones,
    }


# ── tracking ────────────────────────────────────────────────────────────────


def current_net_worth(db) -> Optional[float]:
    """Today's balance-sheet net worth, or None when it cannot be built."""
    try:
        from portf_manager.services.balance_sheet import build_balance_sheet

        return float(build_balance_sheet(db)["totals"]["net_worth_eur"])
    except Exception as e:
        logger.warning("wealth scenario tracking: no net worth (%s)", e)
        return None


def track(
    params: dict,
    baseline_date: Optional[str],
    baseline_net_worth_eur: Optional[float],
    actual_net_worth_eur: Optional[float],
    today: Optional[date] = None,
) -> dict:
    """Compare the real change in net worth with the scenario's expected one.

    ``status``: ``on_track`` when the actual change lies inside the scenario's
    confidence band for the elapsed time, ``ahead``/``behind`` outside it,
    ``too_early`` under ``MIN_TRACKING_DAYS``, ``no_baseline`` when either
    net worth figure is missing.
    """
    today = today or date.today()
    out: dict[str, Any] = {
        "status": "no_baseline",
        "baseline_date": baseline_date,
        "baseline_net_worth_eur": baseline_net_worth_eur,
        "actual_net_worth_eur": actual_net_worth_eur,
        "days_elapsed": None,
        "expected_change_eur": None,
        "expected_low_change_eur": None,
        "expected_high_change_eur": None,
        "actual_change_eur": None,
        "gap_eur": None,
        "gap_pct": None,
    }
    if not baseline_date:
        return out
    try:
        start = date.fromisoformat(str(baseline_date)[:10])
    except ValueError:
        return out
    days = (today - start).days
    out["days_elapsed"] = days
    t = max(0.0, days / 365.25)
    p0 = point_at(params, 0.0, exact_origin=True)
    p = point_at(params, t, exact_origin=True)
    out["expected_change_eur"] = round(p["net_worth"] - p0["net_worth"], 2)
    out["expected_low_change_eur"] = round(p["net_worth_low"] - p0["net_worth"], 2)
    out["expected_high_change_eur"] = round(p["net_worth_high"] - p0["net_worth"], 2)
    if baseline_net_worth_eur is None or actual_net_worth_eur is None:
        return out
    actual_change = actual_net_worth_eur - baseline_net_worth_eur
    out["actual_change_eur"] = round(actual_change, 2)
    out["gap_eur"] = round(actual_change - (p["net_worth"] - p0["net_worth"]), 2)
    if baseline_net_worth_eur > 0:
        out["gap_pct"] = round(out["gap_eur"] / baseline_net_worth_eur * 100, 2)
    if days < MIN_TRACKING_DAYS:
        out["status"] = "too_early"
    elif actual_change > out["expected_high_change_eur"]:
        out["status"] = "ahead"
    elif actual_change < out["expected_low_change_eur"]:
        out["status"] = "behind"
    else:
        out["status"] = "on_track"
    return out


# ── rows ────────────────────────────────────────────────────────────────────


def scenario_view(
    row: dict,
    actual_net_worth_eur: Optional[float],
    today: Optional[date] = None,
    with_projection: bool = False,
) -> dict:
    """A stored row as the API returns it: params decoded, summary, tracking."""
    params = params_from_json(row.get("params"))
    view = {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "is_primary": bool(row.get("is_primary")),
        "params": params,
        "baseline_date": row.get("baseline_date"),
        "baseline_net_worth_eur": row.get("baseline_net_worth_eur"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "summary": summarize(params),
        "tracking": track(
            params,
            row.get("baseline_date"),
            row.get("baseline_net_worth_eur"),
            actual_net_worth_eur,
            today,
        ),
    }
    if with_projection:
        proj = project(params)
        view["projection"] = [
            {k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items()}
            for p in proj["points"]
        ]
    return view


def list_views(db, today: Optional[date] = None) -> list[dict]:
    """Every saved scenario with summary and tracking against today's net worth."""
    rows = db.get_wealth_scenarios()
    actual = current_net_worth(db) if rows else None
    return [scenario_view(r, actual, today) for r in rows]
