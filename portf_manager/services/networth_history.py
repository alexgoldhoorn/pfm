"""Net-worth history: one stored balance-sheet snapshot a day, and a split of
what changed between two of them.

``take_snapshot`` builds the balance sheet once and stores five components that
reconcile to ``totals.net_worth_eur`` to the cent. ``compute_change`` explains
the move between two snapshots::

    saved     = change in cash + net invested
    market    = change in investments - net invested
    debt_paid = - change in liabilities
    property  = change in property
    other     = change in other assets (+ any rounding remainder)

``net_invested`` is buys minus sells of non-cash assets strictly after the
start day up to and including the end day, each at its own transaction-date FX
(the same per-row convention as ``attribution``). Dividends, interest and fees
are deliberately not in it: they move cash, they are not money put into a
holding, so they show up as ``saved`` through the cash change (a dividend
raises cash, a fee lowers it). Nothing is backfilled: history starts the day
the first snapshot is stored.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable, Optional

from portf_manager.services.balance_sheet import build_balance_sheet

logger = logging.getLogger(__name__)

# The five stored components, in display order.
COMPONENTS = (
    "investments_eur",
    "cash_eur",
    "property_eur",
    "other_assets_eur",
    "liabilities_eur",
)

# Default window of the "what changed" split, in days before the latest snapshot.
DEFAULT_CHANGE_DAYS = 30

FxOn = Callable[[str, Any], float]


def components_from_sheet(sheet: dict) -> dict[str, float]:
    """Map the balance sheet's groups to the five stored columns.

    This is the only place the mapping lives: ``cash`` and ``deposits`` are
    both cash, ``investments`` / ``property`` / ``other_assets`` map to their
    own column and ``liabilities`` (loans and manual debts) stays positive.
    A group the mapping does not know goes to ``other_assets_eur``, so the five
    always add up to the net worth.

    Args:
        sheet: Result of ``build_balance_sheet``.

    Returns:
        The five components plus ``net_worth_eur`` (their sum, rounded to 2).
    """
    out = {name: 0.0 for name in COMPONENTS}
    target = {
        "cash": "cash_eur",
        "deposits": "cash_eur",
        "investments": "investments_eur",
        "property": "property_eur",
        "other_assets": "other_assets_eur",
        "liabilities": "liabilities_eur",
    }
    for group in sheet.get("groups", []):
        column = target.get(group["key"], "other_assets_eur")
        out[column] += float(group.get("total_eur") or 0.0)
    out = {k: round(v, 2) for k, v in out.items()}
    out["net_worth_eur"] = round(
        out["investments_eur"]
        + out["cash_eur"]
        + out["property_eur"]
        + out["other_assets_eur"]
        - out["liabilities_eur"],
        2,
    )
    return out


def _sheet_warnings(sheet: dict) -> list[str]:
    """One short line per stale balance-sheet line."""
    notes = []
    for group in sheet.get("groups", []):
        for line in group.get("lines", []):
            if line.get("stale"):
                notes.append(f"{line['label']}: stale")
    return notes


def take_snapshot(
    db: Any,
    today: Optional[date] = None,
    fx: Optional[Callable[[str], float]] = None,
    fx_on: Optional[FxOn] = None,
) -> dict:
    """Build the balance sheet once and store today's snapshot.

    A second call on the same day replaces the row. Nothing partial is stored:
    when the balance sheet cannot be built the exception propagates.

    Args:
        db: Database instance.
        today: Snapshot date; defaults to today.
        fx: Optional live-rate function, passed to the balance sheet.
        fx_on: Optional dated-rate function, passed to the balance sheet.

    Returns:
        The stored row (``snapshot_date`` + the six figures).
    """
    today = today or date.today()
    sheet = build_balance_sheet(db, today, fx=fx, fx_on=fx_on)
    parts = components_from_sheet(sheet)
    detail = {
        "totals": sheet.get("totals", {}),
        "groups": [
            {"key": g["key"], "label": g["label"], "total_eur": g["total_eur"]}
            for g in sheet.get("groups", [])
        ],
    }
    db.upsert_networth_snapshot(
        today.isoformat(),
        parts["net_worth_eur"],
        parts["investments_eur"],
        parts["cash_eur"],
        parts["property_eur"],
        parts["other_assets_eur"],
        parts["liabilities_eur"],
        detail=detail,
        warnings=_sheet_warnings(sheet),
    )
    return {"snapshot_date": today.isoformat(), **parts}


def snapshot_after_price_update(db: Any) -> None:
    """Store today's snapshot at the end of a price run; never raises."""
    try:
        take_snapshot(db)
    except Exception:
        logger.warning("Net-worth snapshot after price update failed", exc_info=True)


def _public(row: dict) -> dict:
    """A stored snapshot without its bulky detail and warning columns."""
    return {
        "snapshot_date": row["snapshot_date"],
        "net_worth_eur": row["net_worth_eur"],
        **{name: row[name] for name in COMPONENTS},
    }


def get_history(db: Any, months: int = 24, today: Optional[date] = None) -> dict:
    """Stored snapshots of the last ``months`` months, oldest first.

    Args:
        db: Database instance.
        months: Window length in months (30.44 days each).
        today: Reference date; defaults to today.

    Returns:
        ``snapshots``, ``first_date``, ``last_date``, ``count``, ``warnings``.
    """
    today = today or date.today()
    start = today - timedelta(days=round(months * 30.44))
    rows = db.list_networth_snapshots(start=start.isoformat())
    snapshots = [_public(r) for r in rows]
    warnings: list[str] = []
    if not snapshots:
        warnings.append("no snapshots stored yet")
    elif len(snapshots) == 1:
        warnings.append(f"history starts {snapshots[0]['snapshot_date']}")
    return {
        "snapshots": snapshots,
        "first_date": snapshots[0]["snapshot_date"] if snapshots else None,
        "last_date": snapshots[-1]["snapshot_date"] if snapshots else None,
        "count": len(snapshots),
        "warnings": warnings,
    }


def _d10(value: Any) -> str:
    return str(value or "")[:10]


def net_invested_eur(db: Any, start: date, end: date, fx_on: FxOn) -> float:
    """Buys minus sells of non-cash assets in ``(start, end]``, in EUR.

    Each row converts at its own currency and date, like ``attribution``.
    Dividends, interest and fees are not flows into a holding and stay out.

    Args:
        db: Database instance.
        start: Exclusive lower bound (already in the start snapshot).
        end: Inclusive upper bound.
        fx_on: ``(currency, 'YYYY-MM-DD')`` -> EUR rate.

    Returns:
        Net money put into non-cash holdings, in EUR.
    """
    assets = {a["id"]: a for a in db.get_all_assets(active_only=False)}
    total = 0.0
    for tx in db.get_all_transactions():
        kind = (tx.get("transaction_type") or "").lower()
        if kind not in ("buy", "sell"):
            continue
        try:
            day = date.fromisoformat(_d10(tx.get("transaction_date")))
        except ValueError:
            continue
        if not start < day <= end:
            continue
        if (assets.get(tx["asset_id"]) or {}).get("asset_type") == "cash":
            continue
        rate = fx_on(tx.get("currency") or "EUR", day.isoformat())
        amount = float(tx.get("total_amount") or 0) * rate
        total += amount if kind == "buy" else -amount
    return total


def _nearest(rows: list[dict], target: date) -> dict:
    """The snapshot closest to ``target`` (the earlier one on a tie)."""
    return min(
        rows,
        key=lambda r: (
            abs((date.fromisoformat(r["snapshot_date"]) - target).days),
            r["snapshot_date"],
        ),
    )


def _empty_change(start: Optional[str], end: Optional[str], warnings: list) -> dict:
    """The response when there is nothing to compare."""
    return {
        "start": start,
        "end": end,
        "net_worth_change_eur": None,
        "net_invested_eur": None,
        "parts": {
            "saved_eur": None,
            "market_eur": None,
            "debt_paid_eur": None,
            "property_change_eur": None,
            "other_change_eur": None,
        },
        "by_component": None,
        "warnings": warnings,
    }


def compute_change(
    db: Any,
    start: Optional[date] = None,
    end: Optional[date] = None,
    fx_on: Optional[FxOn] = None,
) -> dict:
    """Split the net-worth change between two stored snapshots.

    Args:
        db: Database instance.
        start: Wanted start day; the nearest stored snapshot is used. Default:
            the snapshot nearest to 30 days before the end.
        end: Wanted end day; the nearest stored snapshot is used. Default: the
            latest snapshot.
        fx_on: ``(currency, day)`` -> EUR rate; defaults to the stored daily
            closes via the portfolios router.

    Returns:
        ``start``, ``end``, ``net_worth_change_eur``, ``net_invested_eur``,
        ``parts`` (saved, market, debt paid, property, other; they sum to the
        change), ``by_component`` and ``warnings``. With fewer than two
        snapshots the figures are ``None`` and a warning says where history
        starts.
    """
    rows = db.list_networth_snapshots()
    if len(rows) < 2:
        note = (
            f"history starts {rows[0]['snapshot_date']}"
            if rows
            else "no snapshots stored yet"
        )
        return _empty_change(None, None, [note])

    warnings: list[str] = []
    end_row = _nearest(rows, end) if end else rows[-1]
    if end and end_row["snapshot_date"] != end.isoformat():
        warnings.append(
            f"no snapshot on {end.isoformat()}; used {end_row['snapshot_date']}"
        )
    end_day = date.fromisoformat(end_row["snapshot_date"])
    earlier = [r for r in rows if r["snapshot_date"] < end_row["snapshot_date"]]
    if not earlier:
        return _empty_change(
            None,
            end_row["snapshot_date"],
            [f"history starts {rows[0]['snapshot_date']}"],
        )
    wanted = start or end_day - timedelta(days=DEFAULT_CHANGE_DAYS)
    start_row = _nearest(earlier, wanted)
    if start and start_row["snapshot_date"] != start.isoformat():
        warnings.append(
            f"no snapshot on {start.isoformat()}; used {start_row['snapshot_date']}"
        )
    start_day = date.fromisoformat(start_row["snapshot_date"])

    delta = {name: round(end_row[name] - start_row[name], 2) for name in COMPONENTS}
    net_change = round(end_row["net_worth_eur"] - start_row["net_worth_eur"], 2)

    if fx_on is None:
        from portf_server.routers.portfolios import _get_fx_rate_on

        def fx_on(cur: str, day: Any) -> float:
            return _get_fx_rate_on(db, cur, day)

    invested: Optional[float]
    try:
        invested = round(net_invested_eur(db, start_day, end_day, fx_on), 2)
    except Exception as exc:
        logger.warning("Net-worth change: flows unreadable: %s", exc)
        invested = None
        warnings.append("buys and sells could not be read; saved and market unknown")

    debt_paid = round(-delta["liabilities_eur"], 2)
    prop = delta["property_eur"]
    if invested is None:
        saved = market = None
        other = delta["other_assets_eur"]
    else:
        saved = round(delta["cash_eur"] + invested, 2)
        market = round(delta["investments_eur"] - invested, 2)
        # the rounding remainder lands here so the five add up to the cent
        other = round(net_change - saved - market - debt_paid - prop, 2)

    return {
        "start": start_row["snapshot_date"],
        "end": end_row["snapshot_date"],
        "net_worth_change_eur": net_change,
        "net_invested_eur": invested,
        "parts": {
            "saved_eur": saved,
            "market_eur": market,
            "debt_paid_eur": debt_paid,
            "property_change_eur": prop,
            "other_change_eur": other,
        },
        "by_component": {
            name: {
                "start": start_row[name],
                "end": end_row[name],
                "change": delta[name],
            }
            for name in COMPONENTS
        },
        "warnings": warnings,
    }
