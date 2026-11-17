"""The uncategorised queue: rows with no category, grouped and ordered by money.

Deterministic, no LLM. Works on ``spending_insights.load_rows`` output
(EUR-converted, transfers included and filtered here). Figures about a share
of spending count outflows only: an uncategorised credit is not spending.

Rules shared with the rest of the spending services: transfers are never
counted, a month only counts as complete when every account's data reaches its
end (``account_data_through``), and a month with nothing imported carries no
figures.
"""

import os
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from portf_manager.services import spending_insights
from portf_manager.services.merchant import normalize_merchant

UNCATEGORIZED = "uncategorized"
DEFAULT_LIMIT = 20
DEFAULT_MONTHS = 12
# Sample descriptions kept per group.
SAMPLES_PER_GROUP = 3
# A common description prefix shorter than this is not a usable rule pattern.
MIN_PATTERN_LENGTH = 5
# The Action Item and the review highlight start here.
SHARE_ALERT_PCT = 5.0
TOTAL_ALERT_EUR = 500.0
# How far back "the last complete month" is looked for.
_LAST_COMPLETE_LOOKBACK = 12


def _group_name(row: Dict[str, Any]) -> str:
    """The merchant, else the normalised description, of a row."""
    merchant = (row.get("merchant") or "").strip()
    if merchant:
        return merchant
    return normalize_merchant(row.get("description") or "") or (
        (row.get("description") or "").strip()
    )


def _common_prefix(descriptions: List[str]) -> Optional[str]:
    """Longest case-insensitive common prefix of the descriptions, if usable."""
    cleaned = [d.strip() for d in descriptions if d and d.strip()]
    if not cleaned:
        return None
    prefix_len = len(os.path.commonprefix([d.lower() for d in cleaned]))
    prefix = cleaned[0][:prefix_len].strip()
    return prefix if len(prefix) >= MIN_PATTERN_LENGTH else None


def _uncategorized_rows(rows: List[dict], start: Optional[str] = None) -> List[dict]:
    return [
        r
        for r in rows
        if r["category"] == UNCATEGORIZED
        and not r["is_transfer"]
        and (start is None or r["date"] >= start)
    ]


def _spend_total(rows: List[dict], tree: Dict[str, dict], month: str) -> float:
    return sum(
        -r["amount_eur"]
        for r in spending_insights._spend_rows(rows, tree)
        if r["date"][:7] == month
    )


def month_figures(
    rows: List[dict], tree: Dict[str, dict], month: str
) -> Dict[str, Any]:
    """Uncategorised outflows of one month against that month's spending.

    Returns:
        ``{month, count, total_eur, share_pct}``; ``share_pct`` is None when
        the month has no spending.
    """
    out = [
        r
        for r in _uncategorized_rows(rows)
        if r["date"][:7] == month and r["amount_eur"] < 0
    ]
    total = sum(-r["amount_eur"] for r in out)
    spend = _spend_total(rows, tree, month)
    return {
        "month": month,
        "count": len(out),
        "total_eur": round(total, 2),
        "share_pct": round(100 * total / spend, 1) if spend > 0 else None,
    }


def last_complete_month(rows: List[dict], today: date) -> Optional[str]:
    """The newest month before the one in progress that has imports and that
    every account's data reaches the end of, or None."""
    current = today.strftime("%Y-%m")
    first = spending_insights.shift_month(current, -_LAST_COMPLETE_LOOKBACK)
    info = spending_insights.account_data_through(rows, start=f"{first}-01")
    through = info["effective"]
    if not through:
        return None
    present = {r["date"][:7] for r in rows}
    for back in range(1, _LAST_COMPLETE_LOOKBACK + 1):
        month = spending_insights.shift_month(current, -back)
        if month in present and spending_insights._month_end(month).isoformat() <= (
            through
        ):
            return month
    return None


def highlight(figures: Dict[str, Any]) -> Optional[str]:
    """A one-line note when the uncategorised share is above the threshold."""
    share = figures.get("share_pct")
    if share is None or share <= SHARE_ALERT_PCT:
        return None
    return (
        f"{figures['count']} uncategorised outflow(s) worth "
        f"{figures['total_eur']:,.0f} EUR are {share:.0f}% of {figures['month']}'s "
        "spending: classify them to make the comparison reliable."
    )


def build_queue(
    rows: List[dict],
    tree: Dict[str, dict],
    today: date,
    limit: int = DEFAULT_LIMIT,
    months: int = DEFAULT_MONTHS,
) -> Dict[str, Any]:
    """Uncategorised rows of the last ``months`` months, grouped by merchant.

    Args:
        rows: ``load_rows`` output.
        tree: ``{name: row}`` category tree.
        today: reference date.
        limit: groups returned (the totals cover every group).
        months: calendar months in the window, the one in progress included.

    Returns:
        ``{groups, total_count, total_eur, share_of_spending_pct,
        last_complete_month, window_start}``. Groups are ordered by total
        absolute EUR; ``total_eur`` covers every group, credits included,
        while ``share_of_spending_pct`` and ``last_complete_month`` use
        outflows only. ``share_of_spending_pct`` is None without spending.
    """
    current = today.strftime("%Y-%m")
    window_start = f"{spending_insights.shift_month(current, -(months - 1))}-01"
    queue = _uncategorized_rows(rows, window_start)

    buckets: Dict[tuple, List[dict]] = defaultdict(list)
    for r in queue:
        direction = "out" if r["amount_eur"] < 0 else "in"
        buckets[(direction, _group_name(r).upper())].append(r)

    groups = []
    for (direction, name_key), members in buckets.items():
        members.sort(key=lambda r: (r["date"], r["id"]))
        merchants = [(r.get("merchant") or "").strip() for r in members]
        merchant = next((m for m in merchants if m), None)
        samples: List[str] = []
        for r in members:
            desc = r["description"]
            if desc and desc not in samples:
                samples.append(desc)
            if len(samples) == SAMPLES_PER_GROUP:
                break
        if merchant:
            pattern: Optional[str] = merchant
        else:
            pattern = _common_prefix([r["description"] for r in members])
        accounts = sorted({r["account"] for r in members if r.get("account")})
        groups.append(
            {
                "key": f"{direction}:{name_key}",
                "merchant": merchant or _group_name(members[0]),
                "direction": direction,
                "count": len(members),
                "total_eur": round(sum(abs(r["amount_eur"]) for r in members), 2),
                "first_date": members[0]["date"],
                "last_date": members[-1]["date"],
                "sample_descriptions": samples,
                "transaction_ids": [r["id"] for r in members],
                "proposed_pattern": pattern,
                "account_names": accounts,
            }
        )
    groups.sort(key=lambda g: (-g["total_eur"], g["key"]))

    out_total = sum(-r["amount_eur"] for r in queue if r["amount_eur"] < 0)
    spend_total = sum(
        -r["amount_eur"]
        for r in spending_insights._spend_rows(rows, tree)
        if r["date"] >= window_start
    )
    month = last_complete_month(rows, today)
    return {
        "window_start": window_start,
        "groups": groups[: max(0, limit)],
        "total_count": len(queue),
        "total_eur": round(sum(abs(r["amount_eur"]) for r in queue), 2),
        "share_of_spending_pct": (
            round(100 * out_total / spend_total, 1) if spend_total > 0 else None
        ),
        "last_complete_month": month_figures(rows, tree, month) if month else None,
    }
