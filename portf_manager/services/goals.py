"""Savings goals — progress, projection and saving pace.

One place computes every goal figure, so the Goals page, the dashboard card,
the chat tool and the Action Items check all report the same numbers.

A goal projects today's net worth forward with its planned monthly
contribution and assumed return, and compares that with the target. Since
schema v36 a goal also keeps the net worth it was set at
(``start_networth_eur``), which turns the page from a snapshot into a
follow-up: how much of the gap has closed since the goal was set, and the
actual monthly pace against the pace still needed.
"""

from datetime import date, datetime
from typing import Optional

STATUS_ACHIEVED = "achieved"
STATUS_ON_TRACK = "on_track"
STATUS_OFF_TRACK = "off_track"
STATUS_OVERDUE = "overdue"

# average month length in days, for a fractional "months elapsed"
_DAYS_PER_MONTH = 365.25 / 12
# below this the actual pace is noise (one paycheck, one price move)
MIN_MONTHS_FOR_PACE = 1.0


def project_value(
    current: float, monthly: float, annual_return_pct: float, months: int
) -> float:
    """Future value of ``current`` plus monthly contributions, compounded monthly.

    Args:
        current: Starting amount in EUR.
        monthly: Contribution added at the end of every month.
        annual_return_pct: Assumed annual return in percent (6 = 6 %).
        months: Number of months to project.

    Returns:
        The projected value in EUR.
    """
    r = annual_return_pct / 100 / 12
    fv = current * ((1 + r) ** months)
    if r > 0:
        fv += monthly * (((1 + r) ** months - 1) / r)
    else:
        fv += monthly * months
    return fv


def required_monthly(
    current: float, target: float, annual_return_pct: float, months: int
) -> Optional[float]:
    """Monthly contribution needed from now to reach ``target`` in ``months``.

    Returns:
        0 when the current amount alone grows past the target, None when no
        months are left (nothing can be contributed any more).
    """
    if months <= 0:
        return None
    r = annual_return_pct / 100 / 12
    gap = target - current * ((1 + r) ** months)
    if gap <= 0:
        return 0.0
    if r > 0:
        return round(gap / (((1 + r) ** months - 1) / r), 2)
    return round(gap / months, 2)


def months_between(start: date, end: date) -> int:
    """Whole calendar months from ``start`` to ``end``, never negative."""
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _parse_date(value) -> Optional[date]:
    """Date from a DB value (``date``, ``datetime`` or an ISO-ish string)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def goal_status(
    current: float, target: float, on_track: bool, target_dt: date, today: date
) -> str:
    """Classify a goal: achieved, overdue, on_track or off_track.

    Achieved wins over everything (a passed date with the target met is a
    success, not a failure). An unmet goal whose date has passed is overdue;
    otherwise the projection decides.
    """
    if target > 0 and current >= target:
        return STATUS_ACHIEVED
    if target_dt < today:
        return STATUS_OVERDUE
    return STATUS_ON_TRACK if on_track else STATUS_OFF_TRACK


def goal_progress(goal: dict, current_nw: float, today: Optional[date] = None) -> dict:
    """Return the goal row merged with its progress, projection and pace.

    Args:
        goal: A ``goals`` row as a dict.
        current_nw: Today's net worth in EUR (the projection's starting point).
        today: Valuation date, defaults to ``date.today()``.

    Returns:
        The goal with these fields added: ``current_networth_eur``,
        ``progress_pct``, ``months_left``, ``projected_value_eur``,
        ``on_track``, ``shortfall_eur``, ``required_monthly_eur``, ``status``,
        ``start_date``, ``months_elapsed``, ``gained_since_start_eur``,
        ``gap_closed_pct``, ``actual_monthly_eur`` and ``pace``. The pace
        fields are None when the goal has no baseline (created before schema
        v36) or is younger than a month.
    """
    today = today or date.today()
    target = float(goal.get("target_amount_eur") or 0)
    monthly = float(goal.get("monthly_contribution_eur") or 0)
    annual = float(goal.get("expected_return_pct") or 0)
    target_dt = _parse_date(goal.get("target_date")) or today
    months_left = months_between(today, target_dt)

    projected = project_value(current_nw, monthly, annual, months_left)
    on_track = projected >= target
    progress_pct = round(current_nw / target * 100, 1) if target else 0.0
    needed = required_monthly(current_nw, target, annual, months_left)
    status = goal_status(current_nw, target, on_track, target_dt, today)

    # Follow-up since the goal was set: only with a stored baseline.
    start_nw = goal.get("start_networth_eur")
    start_dt = _parse_date(goal.get("created_at"))
    months_elapsed = None
    gained = None
    gap_closed_pct = None
    actual_monthly = None
    pace = None
    if start_nw is not None:
        start_nw = float(start_nw)
        gained = round(current_nw - start_nw, 2)
        if target > start_nw:
            gap_closed_pct = round(
                max(0.0, min(100.0, gained / (target - start_nw) * 100)), 1
            )
        if start_dt is not None:
            months_elapsed = round(max(0, (today - start_dt).days) / _DAYS_PER_MONTH, 1)
            if months_elapsed >= MIN_MONTHS_FOR_PACE:
                actual_monthly = round(gained / months_elapsed, 2)
                if status == STATUS_ACHIEVED:
                    pace = "ahead"
                elif needed is not None:
                    pace = "ahead" if actual_monthly >= needed else "behind"

    return {
        **goal,
        "current_networth_eur": round(current_nw, 2),
        "progress_pct": progress_pct,
        "months_left": months_left,
        "projected_value_eur": round(projected, 2),
        "on_track": on_track,
        "shortfall_eur": round(max(0.0, target - projected), 2),
        "required_monthly_eur": needed,
        "status": status,
        "start_date": start_dt.isoformat() if start_dt else None,
        "months_elapsed": months_elapsed,
        "gained_since_start_eur": gained,
        "gap_closed_pct": gap_closed_pct,
        "actual_monthly_eur": actual_monthly,
        "pace": pace,
    }


def list_goals_with_progress(db, today: Optional[date] = None) -> list[dict]:
    """Every goal with its progress fields, ordered by target date.

    Reads the net worth once (the balance sheet does FX lookups), so call it
    from a sync endpoint or a thread.
    """
    from portf_server.routers.networth import net_worth_eur

    current_nw = net_worth_eur(db)
    return [goal_progress(g, current_nw, today) for g in db.get_goals()]


def goals_summary(goals: list[dict]) -> dict:
    """Counts per status plus the planned contributions, for a one-line strip."""
    counts = {
        STATUS_ACHIEVED: 0,
        STATUS_ON_TRACK: 0,
        STATUS_OFF_TRACK: 0,
        STATUS_OVERDUE: 0,
    }
    for g in goals:
        status = g.get("status") or STATUS_OFF_TRACK
        counts[status] = counts.get(status, 0) + 1
    open_goals = [g for g in goals if g.get("status") != STATUS_ACHIEVED]
    return {
        "total": len(goals),
        **counts,
        "planned_monthly_eur": round(
            sum(float(g.get("monthly_contribution_eur") or 0) for g in open_goals), 2
        ),
        "next_target_date": min(
            (str(g.get("target_date"))[:10] for g in open_goals), default=None
        ),
    }
