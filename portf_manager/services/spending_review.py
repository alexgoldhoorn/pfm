"""Monthly spending review: one bundle assembled from the existing services.

No maths of its own. Every figure comes from ``spending_insights`` (comparison,
anomalies, tiers), ``recurring`` (series), ``budget`` (the active budget's
result for the month) or ``cashflow_forecast`` (next month, upcoming bills,
data freshness); this module only picks, trims and merges.

Each part is built on its own: a part that fails becomes ``None`` plus a
warning, never an exception, so one broken source cannot take the review
down. A month the data does not cover completely (in progress, partial, no
imports) is reported as such and carries no deltas, exactly as ``compare_month``
decides.
"""

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services import budget as budget_service
from portf_manager.services import cashflow_forecast, spending_insights
from portf_manager.services.recurring import load_recurring

# Items kept per list, so the bundle stays readable in chat and in a tool reply.
MOVERS_TOP = 5
ANOMALIES_TOP = 10
RECURRING_TOP = 8
BILLS_TOP = 10
BUDGET_LINES_TOP = 5
# A recurring series needs three charges before it is detected, so one that
# started in the last few months cannot show earlier: "new" and "ended" look
# at the review month and the two before it.
RECURRING_WINDOW_MONTHS = 3
# Bills shown: those due within this many days after today.
BILLS_HORIZON_DAYS = 45
# History fetched before the month, for baselines, last year and "usual" amounts.
_HISTORY_DAYS = 400


def default_month(today: date) -> str:
    """The last complete calendar month."""
    return spending_insights.previous_month(today)


def _month_bounds(month: str) -> tuple:
    start = date(int(month[:4]), int(month[5:7]), 1)
    return start, spending_insights._month_end(month)


def _movers(comparison: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The biggest movers of a comparison (it is already ordered by move)."""
    keep = (
        "name",
        "actual_eur",
        "baseline_eur",
        "last_year_eur",
        "delta_eur",
        "delta_pct",
        "tier",
    )
    return [
        {k: item.get(k) for k in keep if k in item}
        for item in comparison["items"][:MOVERS_TOP]
    ]


def _comparison_part(rows, tree, month, today):
    cat = spending_insights.compare_month(rows, tree, month, today, group_by="category")
    mer = spending_insights.compare_month(rows, tree, month, today, group_by="merchant")
    return (
        {
            "status": cat["status"],
            "in_progress": cat["in_progress"],
            "baseline_months": cat["baseline_months"],
            "totals": cat["totals"],
        },
        {"categories": _movers(cat), "merchants": _movers(mer)},
        list(cat["warnings"]),
    )


def _anomalies_part(rows, tree, month, today):
    start, _ = _month_bounds(month)
    days = max(1, (today - start).days)
    found = spending_insights.find_anomalies(rows, tree, today, days)
    items = [i for i in found["items"] if i["date"][:7] == month]
    return {
        "count": len(items),
        "items": items[:ANOMALIES_TOP],
    }


def _tiers_part(rows, tree, month, today):
    _, end = _month_bounds(month)
    ref = min(today, end)
    window = spending_insights.tier_breakdown(rows, tree, ref, months=2)
    by_month = {e["month"]: e for e in window}
    return {"month": by_month.get(month), "previous": by_month.get(window[0]["month"])}


def _recurring_part(db, fx, month, portfolio_id):
    start, end = _month_bounds(month)
    window_start = date.fromisoformat(
        f"{spending_insights.shift_month(month, -(RECURRING_WINDOW_MONTHS - 1))}-01"
    )
    out: Dict[str, List[dict]] = {
        "new": [],
        "ended": [],
        "missed": [],
        "price_change": [],
    }

    def entry(s, **extra) -> dict:
        return {
            "merchant": s.merchant,
            "category": s.category,
            "cadence": s.cadence,
            "typical_eur": round(s.typical_amount * fx(s.currency), 2),
            "last_date": s.last_date,
            **extra,
        }

    for s in load_recurring(db, portfolio_id=portfolio_id):
        first = date.fromisoformat(s.first_date)
        expected = date.fromisoformat(s.next_expected)
        if window_start <= first <= end:
            out["new"].append(entry(s, first_date=s.first_date))
        if s.status == "ended" and window_start <= expected <= end:
            out["ended"].append(entry(s, expected=s.next_expected))
        if s.status == "missed" and expected <= end:
            out["missed"].append(entry(s, expected=s.next_expected))
        if (
            s.price_change_pct is not None
            and start <= date.fromisoformat(s.last_date) <= end
        ):
            out["price_change"].append(
                entry(
                    s,
                    price_change_pct=s.price_change_pct,
                    last_eur=round(s.last_amount * fx(s.currency), 2),
                )
            )
    for key in out:
        out[key] = out[key][:RECURRING_TOP]
    return out


def _budget_part(db, fx, month, today):
    active = db.get_active_budget()
    if active is None:
        return None, []
    data = budget_service.compute_budget_variance(db, active["id"], [month], fx, today)
    sections = []
    for sec in data["sections"]:
        lines = [
            ln
            for ln in sec["lines"]
            if sec["favourable_when_under"] and ln["variance_eur"] < 0
        ]
        lines.sort(key=lambda ln: ln["variance_eur"])
        sections.append(
            {
                "key": sec["key"],
                "label": sec["label"],
                "planned_total": sec["planned_total"],
                "available_total": sec["available_total"],
                "actual_total": sec["actual_total"],
                "variance_eur": sec["variance_eur"],
                "variance_pct": sec["variance_pct"],
                "favourable": sec["favourable"],
                "tier_totals": sec.get("tier_totals"),
                "over_lines": [
                    {
                        "label": ln["label"],
                        "period": ln["period"],
                        "available_total": ln["available_total"],
                        "actual_total": ln["actual_total"],
                        "variance_eur": ln["variance_eur"],
                        "fund_remaining_eur": (ln.get("fund") or {}).get(
                            "remaining_eur"
                        ),
                    }
                    for ln in lines[:BUDGET_LINES_TOP]
                ],
            }
        )
    result = {
        "budget_id": data["budget_id"],
        "budget_name": data["budget_name"],
        "sections": sections,
        "net": data["net"],
        "no_activity": budget_service.months_without_activity(data) == [month],
    }
    return result, list(data["warnings"])


def _forecast_part(forecast: Dict[str, Any], today: date):
    horizon = (today + timedelta(days=BILLS_HORIZON_DAYS)).isoformat()
    this_month = forecast["this_month"]
    return {
        "data_through": forecast["data_through"],
        "this_month": {
            k: this_month.get(k)
            for k in (
                "month",
                "status",
                "data_through",
                "projected_spending_eur",
                "projected_income_eur",
                "projected_net_eur",
                "range",
                "projected_balance_end_eur",
            )
        },
        "next_month": (forecast["next_months"] or [None])[0],
        "upcoming_bills": [
            b
            for b in forecast["known_bills"]
            if today.isoformat() <= b["date"] <= horizon
        ][:BILLS_TOP],
        "first_below_zero": forecast["first_below_zero"],
    }


def build_review(
    db: Any,
    fx: Callable[[str], float],
    today: date,
    month: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> Dict[str, Any]:
    """The monthly review for ``month`` (default: the last complete month).

    Args:
        db: Database handle.
        fx: currency -> EUR rate.
        today: reference date.
        month: ``YYYY-MM``, not after the month in progress.
        portfolio_id: limit the bank rows to one account.

    Returns:
        ``{month, as_of, data_through, status, totals, movers, anomalies,
        tiers, recurring, budget, forecast, freshness, warnings}``. Every part
        after ``month`` / ``as_of`` is ``None`` when it could not be built (a
        warning says why); ``budget`` is also ``None`` without an active
        budget. ``totals`` and ``movers`` carry no deltas unless the month is
        complete.

    Raises:
        ValueError: ``month`` is after the month in progress.
    """
    month = month or default_month(today)
    if month > today.strftime("%Y-%m"):
        raise ValueError("month cannot be after the month in progress")
    warnings: List[str] = []

    def guard(name: str, build):
        try:
            return build()
        except Exception as e:  # one failing part never takes the review down
            warnings.append(f"{name} could not be built: {e}")
            return None

    rows = tree = None
    start, _ = _month_bounds(month)
    try:
        rows = spending_insights.load_rows(
            db,
            fx,
            start_date=(start - timedelta(days=_HISTORY_DAYS)).isoformat(),
            portfolio_id=portfolio_id,
        )
    except Exception as e:
        warnings.append(f"Bank rows could not be read: {e}")
    try:
        tree = {r["name"]: r for r in db.list_spending_categories_tree()}
    except Exception as e:
        warnings.append(f"The category tree could not be read: {e}")
    have = rows is not None and tree is not None

    summary = movers = None
    if have:
        got = guard("Comparison", lambda: _comparison_part(rows, tree, month, today))
        if got:
            summary, movers, cmp_warnings = got
            warnings.extend(cmp_warnings)
    anomalies = (
        guard("Anomalies", lambda: _anomalies_part(rows, tree, month, today))
        if have
        else None
    )
    tiers = (
        guard("Tier split", lambda: _tiers_part(rows, tree, month, today))
        if have
        else None
    )
    recurring = guard(
        "Recurring changes", lambda: _recurring_part(db, fx, month, portfolio_id)
    )

    def build_budget():
        result, budget_warnings = _budget_part(db, fx, month, today)
        warnings.extend(budget_warnings)
        return result

    budget = guard("Budget", build_budget)

    forecast = freshness = None
    full = guard(
        "Forecast",
        lambda: cashflow_forecast.load_forecast(
            db, fx, today, months=1, portfolio_id=portfolio_id
        ),
    )
    if full is not None:
        warnings.extend(full["warnings"])
        forecast = guard("Forecast", lambda: _forecast_part(full, today))
        freshness = [
            {
                k: a[k]
                for k in (
                    "portfolio_id",
                    "name",
                    "data_through",
                    "days_behind",
                    "stale",
                )
            }
            for a in full["accounts"]
        ]

    through = (
        spending_insights.data_through(rows)
        if rows is not None
        else (full or {}).get("data_through")
    )
    return {
        "month": month,
        "as_of": today.isoformat(),
        "data_through": through,
        "status": (summary or {}).get("status"),
        "totals": summary,
        "movers": movers,
        "anomalies": anomalies,
        "tiers": tiers,
        "recurring": recurring,
        "budget": budget,
        "forecast": forecast,
        "freshness": freshness,
        "warnings": list(dict.fromkeys(warnings)),
    }
