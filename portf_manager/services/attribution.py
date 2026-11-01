"""
Return attribution: which holdings, asset types and brokers made or lost the
money over a period.

Each (portfolio, asset) pair is valued at the start and at the end of the
window (held quantity × the last stored price on or before that day × the
day's FX), and the flows inside the window (buys, sells, dividends and
interest net of their withholding) are converted at transaction-date FX. The
flow-adjusted gain is then::

    gain = value_end − value_start − bought + sold + income

so a position that grew only because money was added shows no gain, and a
position that was sold at a profit keeps that profit even though it no longer
appears in the holdings. A holding with no stored price on or before a
boundary is valued at its EUR cost there (neutral) and flagged ``priced:
false`` so the reader knows that line is a floor, not a measurement.

The period return is a simple Dietz: gain over the start value plus half the
net money invested during the window. There is no intraperiod time weighting
and no benchmark-relative (Brinson) split; see ``risk_metrics`` and
``performance`` for the portfolio-level figures.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Optional

from portf_manager.positions import compute_positions
from portf_manager.services.performance import to_eur_transactions

FxOn = Callable[[str, Any], float]

# How many lines each "top" list carries.
TOP_N = 5


def _d10(value: Any) -> str:
    return str(value or "")[:10]


def _parse(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(_d10(value))
    except ValueError:
        return None


def _price_on(db, asset_id: int, on: date, cache: dict) -> Optional[float]:
    """Last stored close on or before *on*, memoised per (asset, day)."""
    key = (asset_id, on)
    if key not in cache:
        rows = db.get_price_history(asset_id, end_date=on.isoformat())
        cache[key] = float(rows[0]["price"]) if rows else None
    return cache[key]


def _round_row(row: dict) -> dict:
    return {
        k: (round(v, 2) if isinstance(v, float) and k.endswith("_eur") else v)
        for k, v in row.items()
    }


def compute_attribution(
    db,
    start: date,
    end: date,
    fx_now: Callable[[str], float],
    fx_on: FxOn,
    portfolio_id: Optional[int] = None,
) -> dict[str, Any]:
    """Flow-adjusted gain per asset, asset type and portfolio over ``(start, end]``.

    Args:
        db: Database handle.
        start: Positions are valued at the close of this day; flows on it are
            already in the start value.
        end: Positions are valued at the close of this day (inclusive).
        fx_now: currency → EUR rate today, used when *end* is today or later.
        fx_on: ``(currency, date)`` → EUR rate on that date.
        portfolio_id: limit to one portfolio (None = all).

    Returns:
        ``period`` (start, end, days), ``totals``, ``by_asset`` (sorted by gain,
        best first), ``top_contributors``, ``top_detractors``, ``by_type``,
        ``by_portfolio`` and ``unpriced`` (symbols valued at cost at a boundary).
    """
    if end < start:
        raise ValueError("end must not be before start")

    assets = {a["id"]: a for a in db.get_all_assets(active_only=False)}
    portfolios = {p["id"]: p["name"] for p in db.get_all_portfolios(active_only=False)}
    today = date.today()

    def fx_at(currency: str, on: date) -> float:
        return fx_now(currency) if on >= today else fx_on(currency, on.isoformat())

    txns = [
        t
        for t in db.get_all_transactions(portfolio_id=portfolio_id)
        if _parse(t.get("transaction_date")) is not None
    ]
    upto_start = [t for t in txns if _parse(t["transaction_date"]) <= start]
    upto_end = [t for t in txns if _parse(t["transaction_date"]) <= end]
    in_period = [t for t in upto_end if _parse(t["transaction_date"]) > start]

    key = lambda tx: (tx.get("portfolio_id"), tx["asset_id"])  # noqa: E731
    pos_start, _ = compute_positions(upto_start, key=key)
    pos_end, _ = compute_positions(upto_end, key=key)
    eur_start, _ = compute_positions(to_eur_transactions(upto_start, fx_on), key=key)
    eur_end, _ = compute_positions(to_eur_transactions(upto_end, fx_on), key=key)

    flows: dict[tuple, dict[str, float]] = {}
    for tx in in_period:
        f = flows.setdefault(key(tx), {"bought": 0.0, "sold": 0.0, "income": 0.0})
        t = (tx.get("transaction_type") or "").lower()
        # the row's own currency at its own date: EUR buys of a USD-priced
        # fund must not be converted as dollars
        rate = fx_on(tx.get("currency") or "EUR", _d10(tx["transaction_date"]))
        amount = float(tx.get("total_amount") or 0) * rate
        if t == "buy":
            f["bought"] += amount
        elif t == "sell":
            f["sold"] += amount
        elif t in ("dividend", "interest"):
            f["income"] += amount - float(tx.get("tax") or 0) * rate

    price_cache: dict = {}
    rows: list[dict] = []
    unpriced: set[str] = set()

    def value_at(k: tuple, positions: dict, eur_positions: dict, on: date):
        """``(value_eur, priced)`` of one (portfolio, asset) line at *on*."""
        qty = positions.get(k, {}).get("quantity", 0.0)
        if qty <= 1e-9:
            return 0.0, True
        asset = assets.get(k[1]) or {}
        price = _price_on(db, k[1], on, price_cache)
        if price is None:
            return eur_positions.get(k, {}).get("cost", 0.0), False
        return qty * price * fx_at(asset.get("currency") or "EUR", on), True

    for k in set(pos_start) | set(pos_end) | set(flows):
        v0, ok0 = value_at(k, pos_start, eur_start, start)
        v1, ok1 = value_at(k, pos_end, eur_end, end)
        f = flows.get(k, {"bought": 0.0, "sold": 0.0, "income": 0.0})
        if not any((v0, v1, f["bought"], f["sold"], f["income"])):
            continue
        asset = assets.get(k[1]) or {}
        symbol = asset.get("symbol") or f"#{k[1]}"
        if not (ok0 and ok1):
            unpriced.add(symbol)
        rows.append(
            {
                "portfolio_id": k[0],
                "portfolio": portfolios.get(k[0], "Unassigned"),
                "asset_id": k[1],
                "symbol": symbol,
                "name": asset.get("name") or symbol,
                "asset_type": asset.get("asset_type") or "unknown",
                "currency": asset.get("currency") or "EUR",
                "start_value_eur": v0,
                "end_value_eur": v1,
                "bought_eur": f["bought"],
                "sold_eur": f["sold"],
                "income_eur": f["income"],
                "gain_eur": v1 - v0 - f["bought"] + f["sold"] + f["income"],
                "priced": ok0 and ok1,
            }
        )

    total_start = sum(r["start_value_eur"] for r in rows)

    def aggregate(group_key: str, label_key: str) -> list[dict]:
        groups: dict = {}
        for r in rows:
            g = groups.setdefault(
                r[group_key],
                {
                    label_key: r[group_key],
                    "start_value_eur": 0.0,
                    "end_value_eur": 0.0,
                    "bought_eur": 0.0,
                    "sold_eur": 0.0,
                    "income_eur": 0.0,
                    "gain_eur": 0.0,
                    "priced": True,
                },
            )
            for fld in (
                "start_value_eur",
                "end_value_eur",
                "bought_eur",
                "sold_eur",
                "income_eur",
                "gain_eur",
            ):
                g[fld] += r[fld]
            g["priced"] = g["priced"] and r["priced"]
        out = []
        for g in groups.values():
            g["weight_start_pct"] = (
                round(g["start_value_eur"] / total_start * 100, 2)
                if total_start > 0
                else None
            )
            g["contribution_pct"] = (
                round(g["gain_eur"] / total_start * 100, 2) if total_start > 0 else None
            )
            out.append(_round_row(g))
        return sorted(out, key=lambda g: g["gain_eur"], reverse=True)

    # one line per asset even when it sits in several portfolios
    by_asset = aggregate("symbol", "symbol")
    names = {r["symbol"]: (r["name"], r["asset_type"], r["asset_id"]) for r in rows}
    for a in by_asset:
        a["name"], a["asset_type"], a["asset_id"] = names[a["symbol"]]

    totals = {
        fld: round(sum(r[fld] for r in rows), 2)
        for fld in (
            "start_value_eur",
            "end_value_eur",
            "bought_eur",
            "sold_eur",
            "income_eur",
            "gain_eur",
        )
    }
    # simple Dietz: start value plus half the net money put in during the window
    denominator = totals["start_value_eur"] + 0.5 * (
        totals["bought_eur"] - totals["sold_eur"]
    )
    totals["return_pct"] = (
        round(totals["gain_eur"] / denominator * 100, 2) if denominator > 0 else None
    )
    totals["positions"] = len(by_asset)

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": (end - start).days,
        },
        "portfolio_id": portfolio_id,
        "totals": totals,
        "by_asset": by_asset,
        "top_contributors": [a for a in by_asset if a["gain_eur"] > 0][:TOP_N],
        "top_detractors": [a for a in reversed(by_asset) if a["gain_eur"] < 0][:TOP_N],
        "by_type": aggregate("asset_type", "asset_type"),
        "by_portfolio": aggregate("portfolio", "portfolio"),
        "unpriced": sorted(unpriced),
    }
