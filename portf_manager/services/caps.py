"""Position, sector and asset-type caps: how close each holding is to its limit.

Caps live in ``position_caps``. ``cap_status`` compares each with the live
exposure; the allocator reads the same table so a proposed line never pushes a
capped symbol or sector over its limit.
"""

from datetime import date
from typing import Callable, Optional

from portf_manager.services import deployable as deployable_service
from portf_manager.services.exposure import compute_exposure

# Headroom below this many percentage points reads as "near the cap".
NEAR_PP = 2.0


def _entry(
    scope: str,
    key: str,
    max_pct: float,
    current_pct: float,
    total: float,
    cap_id: Optional[int] = None,
    note: Optional[str] = None,
    implicit: bool = False,
) -> dict:
    """One row of the status list."""
    headroom = round(max_pct - current_pct, 2)
    if current_pct > max_pct:
        status = "breached"
    elif headroom < NEAR_PP:
        status = "near"
    else:
        status = "ok"
    return {
        "id": cap_id,
        "scope": scope,
        "key": key,
        "max_pct": max_pct,
        "current_pct": round(current_pct, 2),
        "headroom_pct": headroom,
        "headroom_eur": round(headroom / 100 * total, 2),
        "status": status,
        "implicit": implicit,
        "note": note,
    }


def cap_status(
    db,
    fx: Optional[Callable[[str], float]] = None,
    today: Optional[date] = None,
) -> dict:
    """Every cap with its current weight, headroom and status.

    Explicit caps come from the table. Every held stock without its own symbol
    cap also gets an ``implicit`` row at the allocator's
    ``max_single_stock_pct``, so one list shows all the limits in force.

    Args:
        db: Database.
        fx: Optional ``fx(currency) -> rate to EUR`` (tests).
        today: Reference date.

    Returns:
        ``{"as_of", "total_value_eur", "caps", "breached", "near"}``.
    """
    today = today or date.today()
    exposure = compute_exposure(db, fx=fx, today=today)
    total = exposure["total_value_eur"]
    position_eur = {k.upper(): v for k, v in exposure["position_value_eur"].items()}
    sectors = {k.lower(): v for k, v in exposure["by_sector"].items()}
    types = {k.lower(): v for k, v in exposure["by_asset_type"].items()}

    def current(scope: str, key: str) -> float:
        if scope == "symbol":
            return position_eur.get(key.upper(), 0.0) / total * 100 if total else 0.0
        source = sectors if scope == "sector" else types
        return source.get(key.lower(), 0.0)

    rows = []
    explicit_symbols = set()
    for cap in db.get_position_caps():
        if cap["scope"] == "symbol":
            explicit_symbols.add(cap["key"].upper())
        rows.append(
            _entry(
                cap["scope"],
                cap["key"],
                cap["max_pct"],
                current(cap["scope"], cap["key"]),
                total,
                cap_id=cap["id"],
                note=cap.get("note"),
            )
        )

    default_pct = float(deployable_service.load_settings(db)["max_single_stock_pct"])
    for symbol in sorted(position_eur):
        asset = db.get_asset_by_symbol(symbol)
        if symbol in explicit_symbols or not asset:
            continue
        if asset.get("asset_type") != "stock":
            continue
        rows.append(
            _entry(
                "symbol",
                asset["symbol"],
                default_pct,
                current("symbol", symbol),
                total,
                implicit=True,
            )
        )

    order = {"breached": 0, "near": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["scope"], r["key"]))
    return {
        "as_of": today.isoformat(),
        "total_value_eur": total,
        "caps": rows,
        "breached": sum(1 for r in rows if r["status"] == "breached"),
        "near": sum(1 for r in rows if r["status"] == "near"),
    }


def check_position_caps(db) -> list[dict]:
    """Action Items: one per breached explicit cap."""
    items = []
    for cap in cap_status(db)["caps"]:
        if cap["implicit"] or cap["status"] != "breached":
            continue
        items.append(
            {
                "id": f"cap:{cap['scope']}:{cap['key']}",
                "category": "allocation",
                "severity": "high",
                "title": (
                    f"{cap['key']} is over its {cap['max_pct']:g}% cap "
                    f"({cap['current_pct']:g}%)"
                ),
                "detail": (
                    f"The {cap['scope'].replace('_', ' ')} cap is "
                    f"{cap['max_pct']:g}%; it is {-cap['headroom_pct']:.1f} points "
                    f"over ({-cap['headroom_eur']:,.0f} EUR). New money should go "
                    "elsewhere, or raise the cap if the limit changed."
                ),
                "link_page": "caps",
                "context": {"scope": cap["scope"], "key": cap["key"]},
            }
        )
    return items
