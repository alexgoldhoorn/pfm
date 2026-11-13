"""Stress test: what a market shock would cost the held portfolio.

A shock is a dict of percentage moves keyed by dimension::

    {"asset_class": {"equity": -30}, "sector": {"Technology": -50},
     "currency": {"USD": -10}, "asset_type": {"crypto": -60}}

Each position is split into slices the way ``compute_exposure`` looks funds
through (asset class, then sector for the equity part, and currency by region
or hedge). A slice moves by the most specific market shock that matches it
(sector, else asset class, else asset type); a currency shock then compounds
on top, multiplicatively. Money that is not in a held position (cash, deposits,
property, loans) is untouched, so net worth falls by the portfolio loss only.
"""

from datetime import date
from typing import Any, Callable, Optional

from portf_manager.positions import compute_positions
from portf_manager.services.exposure import (
    _ASSET_CLASS_BY_TYPE,
    _CURRENCY_BY_REGION,
    UNKNOWN,
    _default_fx,
)
from portf_manager.services.fund_profiles import FUND_ASSET_TYPES, parse_profile
from portf_manager.services.portfolio_advisor import _resolve_sector_country

# Ordered presets, each a shock dict (percent moves).
SCENARIOS: dict[str, dict] = {
    "equity_bear": {
        "label": "Equity bear market",
        "description": "All equities fall 30%.",
        "shocks": {"asset_class": {"equity": -30}},
    },
    "semis_crash": {
        "label": "Semiconductor crash",
        "description": "Technology and semiconductor names -50%, other equities -15%.",
        "shocks": {
            "asset_class": {"equity": -15},
            "sector": {
                "Technology": -50,
                "Information Technology": -50,
                "Semiconductors": -50,
            },
        },
    },
    "ai_bubble": {
        "label": "AI bubble bursts",
        "description": (
            "Technology, semiconductors and communication services -60%, "
            "other equities -30%."
        ),
        "shocks": {
            "asset_class": {"equity": -30},
            "sector": {
                "Technology": -60,
                "Information Technology": -60,
                "Semiconductors": -60,
                "Communication Services": -60,
            },
        },
    },
    "usd_down": {
        "label": "Dollar slump",
        "description": "USD falls 10% against the euro.",
        "shocks": {"currency": {"USD": -10}},
    },
    "rates_up": {
        "label": "Rates shock",
        "description": "Bonds -7%, equities -5%.",
        "shocks": {"asset_class": {"bond": -7, "equity": -5}},
    },
    "crypto_winter": {
        "label": "Crypto winter",
        "description": "Crypto -60%.",
        "shocks": {"asset_type": {"crypto": -60}},
    },
    "stagflation": {
        "label": "Stagflation",
        "description": "Equities -20%, bonds -10%, crypto -40%.",
        "shocks": {
            "asset_class": {"equity": -20, "bond": -10},
            "asset_type": {"crypto": -40},
        },
    },
}

_DIMENSIONS = ("asset_class", "sector", "currency", "asset_type")
_MIN_PCT, _MAX_PCT = -100.0, 200.0


def list_scenarios() -> list[dict]:
    """The presets, in order, with their shocks."""
    return [
        {
            "name": name,
            "label": s["label"],
            "description": s["description"],
            "shocks": s["shocks"],
        }
        for name, s in SCENARIOS.items()
    ]


def normalise_shocks(shocks: dict) -> dict:
    """Validate a shock dict and return it with lower-cased match keys.

    Args:
        shocks: ``{dimension: {key: percent}}``.

    Returns:
        The same shocks with float percents; currency keys upper-case, every
        other key lower-case.

    Raises:
        ValueError: on an unknown dimension, a non-numeric or out-of-range
            percent, or when no shock is given at all.
    """
    if not isinstance(shocks, dict):
        raise ValueError("shocks must be an object")
    out: dict[str, dict[str, float]] = {}
    for dim, moves in shocks.items():
        if dim not in _DIMENSIONS:
            raise ValueError(f"unknown shock dimension {dim!r}; use {_DIMENSIONS}")
        if not isinstance(moves, dict):
            raise ValueError(f"shocks[{dim!r}] must be an object")
        for key, pct in moves.items():
            if isinstance(pct, bool) or not isinstance(pct, (int, float)):
                raise ValueError(f"shock {dim}/{key} must be a number")
            if not _MIN_PCT <= pct <= _MAX_PCT:
                raise ValueError(
                    f"shock {dim}/{key} must be between {_MIN_PCT:.0f} and "
                    f"{_MAX_PCT:.0f} percent"
                )
            norm = str(key).strip()
            norm = norm.upper() if dim == "currency" else norm.lower()
            out.setdefault(dim, {})[norm] = float(pct)
    if not any(out.values()):
        raise ValueError("no shocks given")
    return out


def _market_move(shocks: dict, atype: str, cls: str, sector: Optional[str]) -> float:
    """Fractional move of one slice: sector, else class, else asset type."""
    if sector and sector.lower() in shocks.get("sector", {}):
        return shocks["sector"][sector.lower()] / 100
    if cls in shocks.get("asset_class", {}):
        return shocks["asset_class"][cls] / 100
    return shocks.get("asset_type", {}).get(atype, 0.0) / 100


def _slices(db: Any, asset: dict, asset_id: int, today: date) -> tuple[list, bool]:
    """Break one position into (class, sector, currency, weight) slices.

    Returns:
        The slices (weights sum to 1) and whether the position is an
        unprofiled fund that was assumed to be all equity.
    """
    atype = asset.get("asset_type", "other")
    currency = (asset.get("currency") or "EUR").upper()
    if atype == "crypto":
        return [("crypto", None, None, 1.0)], False
    if atype not in FUND_ASSET_TYPES:
        sector, _ = _resolve_sector_country(db, asset)
        cls = _ASSET_CLASS_BY_TYPE.get(atype, "other")
        return [(cls, sector if cls == "equity" else None, currency, 1.0)], False

    profile = parse_profile(db.get_fund_profile(asset_id))
    if not profile or not profile.get("regions"):
        return [("equity", None, currency, 1.0)], True
    classes = profile["asset_class"] or {"equity": 1.0}
    if profile.get("currency_hedged"):
        hedge = (profile.get("hedge_currency") or currency).upper()
        currencies = {hedge: 1.0}
    else:
        currencies = {}
        for region, share in profile["regions"].items():
            cur = _CURRENCY_BY_REGION.get(region, UNKNOWN)
            currencies[cur] = currencies.get(cur, 0.0) + share
    sectors = profile.get("sectors") or {}
    out = []
    for cls, cshare in classes.items():
        sector_split = sectors if cls == "equity" and sectors else {None: 1.0}
        for sector, sshare in sector_split.items():
            for cur, pshare in currencies.items():
                out.append((cls, sector, cur.upper(), cshare * sshare * pshare))
    return out, False


def _retirement_after(db: Any, loss_eur: float, today: date, fx: Callable) -> Any:
    """Readiness verdicts before and after the loss, or None without a plan."""
    from portf_manager.services.retirement import readiness

    before = readiness(db, today=today, fx=fx)
    if before.get("status") != "ok":
        return None
    investments = before["inputs"]["investments_eur"]
    after = readiness(
        db,
        today=today,
        fx=fx,
        investable_override_eur=max(0.0, investments - loss_eur),
    )

    def _brief(res: dict) -> list[dict]:
        return [
            {
                k: sc.get(k)
                for k in (
                    "retirement_age",
                    "case",
                    "verdict",
                    "lasts_until_age",
                    "pot_at_retirement_eur",
                    "sustainable_monthly_eur",
                    "gap_monthly_eur",
                )
            }
            for sc in res.get("scenarios", [])
        ]

    return {
        "investments_before_eur": round(investments, 2),
        "investments_after_eur": round(max(0.0, investments - loss_eur), 2),
        "scenarios_before": _brief(before),
        "scenarios_after": _brief(after),
    }


def run_stress(
    db: Any,
    shocks: dict,
    fx: Optional[Callable[[str], float]] = None,
    today: Optional[date] = None,
    fx_on: Optional[Callable[[str, object], float]] = None,
) -> dict:
    """Apply a shock to the held portfolio and report the damage.

    Args:
        db: Database instance.
        shocks: ``{dimension: {key: percent}}``, see the module docstring.
        fx: ``fx(currency)`` EUR rate; defaults to the live rate.
        today: reference date.
        fx_on: ``fx_on(currency, day)`` for the balance sheet's cash ledger;
            defaults to the live lookup.

    Returns:
        ``shocks``, ``portfolio_value_eur``, ``loss_eur`` (positive number =
        money lost), ``loss_pct_of_portfolio``, ``by_asset_type``,
        ``by_portfolio``, ``top_losers``, ``net_worth_before_eur`` /
        ``net_worth_after_eur`` and ``retirement_after`` (None when the plan
        is not set up).

    Raises:
        ValueError: for invalid shocks.
    """
    shocks = normalise_shocks(shocks)
    fx = fx or _default_fx
    today = today or date.today()

    txns = db.get_all_transactions()
    positions, _ = compute_positions(txns)
    names = {p["id"]: p["name"] for p in db.get_all_portfolios()}
    portfolio_by_asset: dict[int, str] = {}
    for tx in txns:
        portfolio_by_asset.setdefault(
            tx["asset_id"], names.get(tx.get("portfolio_id"), "Unassigned")
        )

    total = loss = 0.0
    by_type: dict[str, dict] = {}
    by_portfolio: dict[str, dict] = {}
    rows: list[dict] = []
    unprofiled: list[str] = []
    for asset_id, pos in positions.items():
        if pos["quantity"] <= 0:
            continue
        asset = db.get_asset(asset_id)
        if not asset:
            continue
        price_row = db.get_latest_price(asset_id)
        price = float(price_row["price"]) if price_row else 0.0
        value = pos["quantity"] * price * fx(asset.get("currency") or "EUR")
        if value <= 0:
            continue
        atype = asset.get("asset_type", "other")
        slices, assumed = _slices(db, asset, asset_id, today)
        if assumed:
            unprofiled.append(asset["symbol"])
        move = 0.0
        for cls, sector, cur, weight in slices:
            market = _market_move(shocks, atype, cls, sector)
            ccy = shocks.get("currency", {}).get(cur or "", 0.0) / 100
            move += weight * ((1 + market) * (1 + ccy) - 1)
        delta = value * move
        total += value
        loss -= delta
        pname = portfolio_by_asset.get(asset_id, "Unassigned")
        for bucket, key in ((by_type, atype), (by_portfolio, pname)):
            b = bucket.setdefault(key, {"value_eur": 0.0, "loss_eur": 0.0})
            b["value_eur"] += value
            b["loss_eur"] -= delta
        rows.append(
            {
                "symbol": asset["symbol"],
                "name": asset.get("name", asset["symbol"]),
                "asset_type": atype,
                "value_eur": round(value, 2),
                "loss_eur": round(-delta, 2) + 0.0,
                "loss_pct": round(move * -100, 2) + 0.0,
            }
        )

    def _group(bucket: dict, label: str) -> list[dict]:
        return [
            {
                label: key,
                "value_eur": round(b["value_eur"], 2),
                "loss_eur": round(b["loss_eur"], 2),
                "loss_pct": (
                    round(b["loss_eur"] / b["value_eur"] * 100, 2)
                    if b["value_eur"]
                    else 0.0
                ),
            }
            for key, b in sorted(bucket.items(), key=lambda kv: -kv[1]["loss_eur"])
        ]

    from portf_manager.services.balance_sheet import build_balance_sheet

    sheet = build_balance_sheet(db, today=today, fx=fx, fx_on=fx_on)
    net_before = float(sheet["totals"]["net_worth_eur"])
    loss_r = round(loss, 2)
    return {
        "shocks": shocks,
        "portfolio_value_eur": round(total, 2),
        "loss_eur": loss_r,
        "loss_pct_of_portfolio": round(loss / total * 100, 2) if total else 0.0,
        "by_asset_type": _group(by_type, "asset_type"),
        "by_portfolio": _group(by_portfolio, "portfolio"),
        "top_losers": sorted(
            (r for r in rows if r["loss_eur"] > 0), key=lambda r: -r["loss_eur"]
        )[:5],
        "net_worth_before_eur": round(net_before, 2),
        "net_worth_after_eur": round(net_before - loss, 2),
        "retirement_after": _retirement_after(db, loss, today, fx),
        "assumed_equity": unprofiled,
    }
