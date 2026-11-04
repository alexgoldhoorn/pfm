"""Score stored research reports against what the price did afterwards.

Pure arithmetic over ``research_history`` (LLM reports, append-only) and
``research_notes`` (the owner's manual notes, labelled ``manual``). Each entry
is compared with the price at two points: the report's horizon and the next
entry for the same symbol. No LLM is involved.
"""

from __future__ import annotations

import calendar
from bisect import bisect_left, bisect_right
from datetime import date
from statistics import median
from typing import Any, Optional

METHOD = (
    "Each report is compared with the stored close at its horizon and at the "
    "next report for the same symbol; target error is (price - fair value) / "
    "fair value, a BUY hits when the price rose, a SELL when it fell, a HOLD "
    "when it moved at most 10%."
)

# Manual notes carry no recommendation; derive one from fair value vs price.
_MANUAL_BAND = 0.10
_HOLD_BAND_PCT = 10.0


def _day(value: Any) -> date:
    """Parse the date part of an ISO or SQLite timestamp string."""
    return date.fromisoformat(str(value)[:10])


def _add_months(d: date, months: int) -> date:
    """Add calendar months, clamping the day to the target month's length."""
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _derive_recommendation(fair: float, price: Optional[float]) -> Optional[str]:
    """BUY / SELL / HOLD for a manual note from its fair value and the price."""
    if not price:
        return None
    if fair > price * (1 + _MANUAL_BAND):
        return "BUY"
    if fair < price * (1 - _MANUAL_BAND):
        return "SELL"
    return "HOLD"


def collect_entries(db, symbol: Optional[str] = None) -> list[dict]:
    """Merge report history and manual notes into one list of entries.

    Args:
        db: The Database.
        symbol: Restrict to one upper-case symbol.

    Returns:
        Entries sorted by date ascending, each with ``id`` (``h<n>`` for a
        report, ``n<n>`` for a manual note), ``asset_id``, ``symbol``,
        ``asset_type``, ``generated_at``, ``source``, ``recommendation``,
        ``confidence``, the bands, ``currency``, ``price_at_report`` and
        ``horizon_months``.
    """
    entries: list[dict] = []
    for h in db.get_research_history(symbol=symbol):
        entries.append(
            {
                "id": f"h{h['id']}",
                "asset_id": h["asset_id"],
                "symbol": h["symbol"],
                "asset_type": h.get("asset_type"),
                "generated_at": str(h["generated_at"]),
                "source": h.get("source") or "llm",
                "recommendation": h.get("recommendation"),
                "confidence": h.get("confidence"),
                "fair_value": h.get("fair_value"),
                "buy_below": h.get("buy_below"),
                "sell_above": h.get("sell_above"),
                "currency": h.get("currency"),
                "price_at_report": h.get("price_at_report"),
                "horizon_months": h.get("horizon_months") or 12,
            }
        )
    for n in db.get_all_research_notes(symbol=symbol):
        if n.get("fair_value") is None:
            continue
        asset = db.get_asset(n["asset_id"]) if n.get("asset_id") else None
        if asset is None:
            asset = db.get_asset_by_symbol(n["symbol"])
        if asset is None:
            continue
        price = n.get("price_at_save")
        entries.append(
            {
                "id": f"n{n['id']}",
                "asset_id": asset["id"],
                "symbol": n["symbol"],
                "asset_type": asset.get("asset_type"),
                "generated_at": str(n["created_at"]),
                "source": "manual",
                "recommendation": _derive_recommendation(n["fair_value"], price),
                "confidence": None,
                "fair_value": n["fair_value"],
                "buy_below": n.get("buy_below"),
                "sell_above": n.get("sell_above"),
                "currency": asset.get("currency"),
                "price_at_report": price,
                "horizon_months": 12,
            }
        )
    entries.sort(key=lambda e: (e["generated_at"], e["id"]))
    return entries


class _Prices:
    """Ascending close prices of one asset with on-or-before lookups."""

    def __init__(self, rows: list[dict]) -> None:
        pairs = sorted((str(r["price_date"])[:10], float(r["price"])) for r in rows)
        self.dates = [p[0] for p in pairs]
        self.values = [p[1] for p in pairs]

    def on_or_before(self, d: date) -> Optional[tuple[str, float]]:
        """The last stored close on or before ``d``, as (date, price)."""
        i = bisect_right(self.dates, d.isoformat())
        return (self.dates[i - 1], self.values[i - 1]) if i else None

    def between(self, start: date, end: date) -> list[float]:
        """Closes with ``start <= date <= end``."""
        lo = bisect_left(self.dates, start.isoformat())
        hi = bisect_right(self.dates, end.isoformat())
        return self.values[lo:hi]


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _score_point(
    entry: dict, prices: _Prices, point_date: str, price: float
) -> dict[str, Any]:
    """Score one entry against a price observed on ``point_date``."""
    base = entry["price_at_report"]
    fair = entry["fair_value"]
    rec = entry["recommendation"]
    realised = (price / base - 1) * 100 if base else None
    error = (price - fair) / fair * 100 if fair else None
    hit: Optional[bool] = None
    if rec and realised is not None:
        if rec == "BUY":
            hit = realised > 0
        elif rec == "SELL":
            hit = realised < 0
        elif rec == "HOLD":
            hit = abs(realised) <= _HOLD_BAND_PCT
    reached: Optional[bool] = None
    if fair is not None and rec in ("BUY", "SELL"):
        window = prices.between(_day(entry["generated_at"]), _day(point_date))
        if window:
            reached = max(window) >= fair if rec == "BUY" else min(window) <= fair
    return {
        "date": point_date,
        "price": round(price, 4),
        "realised_return_pct": _round(realised),
        "target_error_pct": _round(error),
        "direction_hit": hit,
        "reached_target": reached,
    }


def _aggregate(pairs: list[tuple[dict, Optional[dict]]], key: str) -> dict:
    """Aggregate the scored points ``key`` over (entry, point) pairs."""
    scored = [(e, e[key]) for e, _ in pairs if e.get(key)]
    hits = [p["direction_hit"] for _, p in scored if p["direction_hit"] is not None]
    errors = [
        abs(p["target_error_pct"])
        for _, p in scored
        if p["target_error_pct"] is not None
    ]
    implied = [
        e["implied_return_pct"]
        for e, _ in scored
        if e["implied_return_pct"] is not None
    ]
    realised = [
        p["realised_return_pct"]
        for _, p in scored
        if p["realised_return_pct"] is not None
    ]
    reached = [
        p["reached_target"] for _, p in scored if p["reached_target"] is not None
    ]

    def rate(flags: list[bool]) -> Optional[float]:
        return round(100 * sum(flags) / len(flags), 1) if flags else None

    return {
        "n": len(scored),
        "direction_hit_rate_pct": rate(hits),
        "median_abs_target_error_pct": _round(median(errors), 1) if errors else None,
        "mean_implied_return_pct": (
            _round(sum(implied) / len(implied), 1) if implied else None
        ),
        "mean_realised_return_pct": (
            _round(sum(realised) / len(realised), 1) if realised else None
        ),
        "reached_target_rate_pct": rate(reached),
    }


def _both(entries: list[dict]) -> dict:
    """The two aggregates for a group of entries."""
    pairs = [(e, None) for e in entries]
    return {
        "at_horizon": _aggregate(pairs, "at_horizon"),
        "at_next": _aggregate(pairs, "at_next"),
    }


def _breakdown(entries: list[dict], field: str) -> dict:
    """Aggregates per distinct value of ``field`` (entries with a value only)."""
    groups: dict[str, list[dict]] = {}
    for e in entries:
        if e.get(field):
            groups.setdefault(str(e[field]), []).append(e)
    out = {}
    for name, group in sorted(groups.items()):
        agg = _both(group)
        if agg["at_horizon"]["n"] or agg["at_next"]["n"]:
            out[name] = agg
    return out


def compute_scorecard(
    db, symbol: Optional[str] = None, today: Optional[date] = None
) -> dict:
    """Score every stored research entry against the price afterwards.

    Args:
        db: The Database.
        symbol: Restrict to one symbol (case-insensitive).
        today: Reference date; defaults to today.

    Returns:
        ``as_of``, ``totals`` (``at_horizon`` / ``at_next`` aggregates),
        breakdowns by recommendation, confidence, source and asset type,
        ``best`` / ``worst`` (five each), ``entries`` (newest first), ``total``,
        ``pending`` (entries whose horizon has not passed) and ``method``.
    """
    today = today or date.today()
    entries = collect_entries(db, symbol=symbol.upper() if symbol else None)
    price_cache: dict[int, _Prices] = {}

    def prices_for(asset_id: int) -> _Prices:
        if asset_id not in price_cache:
            price_cache[asset_id] = _Prices(db.get_price_history(asset_id))
        return price_cache[asset_id]

    by_symbol: dict[str, list[dict]] = {}
    for e in entries:
        by_symbol.setdefault(e["symbol"], []).append(e)

    for group in by_symbol.values():
        for i, e in enumerate(group):
            prices = prices_for(e["asset_id"])
            base, fair = e["price_at_report"], e["fair_value"]
            e["date"] = e["generated_at"][:10]
            e["implied_return_pct"] = (
                _round((fair / base - 1) * 100) if fair and base else None
            )
            # Point 1: the report's own horizon.
            e["at_horizon"] = None
            horizon = _add_months(_day(e["generated_at"]), e["horizon_months"])
            e["horizon_date"] = horizon.isoformat()
            if horizon <= today:
                hit = prices.on_or_before(horizon)
                if hit:
                    e["at_horizon"] = _score_point(e, prices, hit[0], hit[1])
            # Point 2: the next entry for the same symbol.
            e["at_next"] = None
            if i + 1 < len(group):
                nxt = group[i + 1]
                nxt_day = _day(nxt["generated_at"])
                if nxt["price_at_report"]:
                    e["at_next"] = _score_point(
                        e, prices, nxt_day.isoformat(), nxt["price_at_report"]
                    )
                else:
                    hit = prices.on_or_before(nxt_day)
                    if hit:
                        e["at_next"] = _score_point(e, prices, hit[0], hit[1])
            # Where the price stands now, for entries still pending.
            latest = prices.on_or_before(today)
            e["now"] = None
            if latest:
                e["now"] = {
                    "date": latest[0],
                    "price": round(latest[1], 4),
                    "realised_return_pct": (
                        _round((latest[1] / base - 1) * 100) if base else None
                    ),
                }

    def best_key(e: dict) -> Optional[float]:
        point = e["at_horizon"] or e["at_next"]
        return point["realised_return_pct"] if point else None

    ranked = [e for e in entries if best_key(e) is not None]
    ranked.sort(key=best_key, reverse=True)
    best = ranked[:5]
    worst = list(reversed(ranked[-5:])) if ranked else []

    newest_first = sorted(
        entries, key=lambda e: (e["generated_at"], e["id"]), reverse=True
    )
    return {
        "as_of": today.isoformat(),
        "totals": _both(entries),
        "by_recommendation": _breakdown(entries, "recommendation"),
        "by_confidence": _breakdown(entries, "confidence"),
        "by_source": _breakdown(entries, "source"),
        "by_asset_type": _breakdown(entries, "asset_type"),
        "best": best,
        "worst": worst,
        "entries": newest_first,
        "total": len(entries),
        "pending": sum(1 for e in entries if e["at_horizon"] is None),
        "method": METHOD,
    }
