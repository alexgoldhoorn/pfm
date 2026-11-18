"""Triage of Action Items: families folded into groups, a priority order.

Pure functions over the item dicts ``action_items`` builds (after the follow-up
state is annotated). Nothing here changes a check or an item id. A *family* is
a set of items of one kind (every price-target crossing, every pace line);
three or more open items of a family fold into one ``group:<family>`` entry,
and every item or group gets a ``priority`` so the list can be sorted and the
top few shown first.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

GROUP_MIN_ITEMS = 3
TOP_N = 5
NEW_DAYS = 7
URGENCY_WINDOW_DAYS = 45
URGENCY_MAX = 50
OVERSHOOT_LABELS = 3

# Item id prefix -> family. The first matching prefix wins; an item that
# matches none stays alone.
FAMILY_PREFIXES = (
    ("research:", "price_targets"),
    ("watchlist:", "price_targets"),
    ("budget:pace:", "budget_pace"),
    ("budget:line:", "budget_overrun"),
    ("recurring:missed:", "recurring_missed"),
    ("exposure:", "fund_exposure"),
    ("import:portfolio:", "stale_imports"),
    ("balance_sheet_stale:", "cash_freshness"),
)

# Count title per family; ``{n}`` is the child count.
FAMILY_TITLES = {
    "price_targets": "{n} price targets crossed",
    "budget_pace": "{n} budget lines on pace to exceed their limit",
    "budget_overrun": "{n} budget lines over plan",
    "recurring_missed": "{n} recurring charges did not arrive",
    "fund_exposure": "{n} fund exposure items",
    "stale_imports": "{n} accounts need a fresh import",
    "cash_freshness": "{n} balance-sheet lines are out of date",
}

# Priority = severity base + category weight + urgency (0..URGENCY_MAX).
# Severity dominates: the largest category weight stays below the gap between
# two severities, so a high item always outranks a medium one. The category
# weight orders items of equal severity: what costs money or misses a
# deadline above routine housekeeping, watchlist crossings last.
SEVERITY_BASE = {"high": 300, "medium": 200, "low": 100}
CATEGORY_WEIGHT = {
    "tax": 40,
    "data_quality": 40,
    "retirement": 40,
    "allocate": 35,
    "allocation": 35,
    "errors": 30,
    "goals": 25,
    "decisions": 25,
    "import": 20,
    "networth": 20,
    "exposure": 15,
    "forecast": 15,
    "budget": 10,
    "spending": 10,
    "watchlist": 0,
}
DEFAULT_CATEGORY_WEIGHT = 10


def family_of(item: dict) -> Optional[str]:
    """The family an item belongs to, from its id prefix, or None."""
    item_id = str(item.get("id") or "")
    for prefix, family in FAMILY_PREFIXES:
        if item_id.startswith(prefix):
            return family
    return None


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_until(item: dict, today: date) -> Optional[int]:
    """Days to the item's deadline, from ``days_left`` or a due date."""
    ctx = item.get("context") or {}
    left = ctx.get("days_left")
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        return int(left)
    for key in ("due", "due_date"):
        due = _parse_date(ctx.get(key))
        if due is not None:
            return (due - today).days
    return None


def priority(item: dict, today: Optional[date] = None) -> int:
    """Sort key for an item or group: higher comes first.

    Args:
        item: An action item (or group item).
        today: Injectable clock for the urgency term.

    Returns:
        Severity base + category weight + urgency. Urgency is 0 beyond 45
        days or without a deadline, and grows to 50 on the day itself.
    """
    today = today or date.today()
    base = SEVERITY_BASE.get(item.get("severity"), 0)
    weight = CATEGORY_WEIGHT.get(item.get("category"), DEFAULT_CATEGORY_WEIGHT)
    return base + weight + _urgency(item, today)


def _urgency(item: dict, today: date) -> int:
    days = _days_until(item, today)
    if days is None or days > URGENCY_WINDOW_DAYS:
        return 0
    return round(
        URGENCY_MAX * (URGENCY_WINDOW_DAYS - max(days, 0)) / URGENCY_WINDOW_DAYS
    )


def sort_by_priority(items: List[dict]) -> List[dict]:
    """Highest priority first; ties keep their existing order."""
    return sorted(items, key=lambda i: -i.get("priority", 0))


def _price_target_summary(children: List[dict], today: date) -> str:
    buy = sell = new = 0
    for child in children:
        ctx = child.get("context") or {}
        triggers = ctx.get("triggers") or []
        buy += "BUY" in triggers
        sell += "SELL" in triggers
        since = _parse_date(ctx.get("since"))
        if since is not None and (today - since).days <= NEW_DAYS:
            new += 1
    return f"{buy} BUY, {sell} SELL; {new} new in the last {NEW_DAYS} days."


def _pace_summary(children: List[dict], today: date) -> str:
    ranked = sorted(
        children,
        key=lambda c: -float((c.get("context") or {}).get("projected_over_eur") or 0),
    )[:OVERSHOOT_LABELS]
    parts = [
        f"{(c.get('context') or {}).get('label') or c.get('title')} "
        f"(+{float((c.get('context') or {}).get('projected_over_eur') or 0):,.0f} EUR)"
        for c in ranked
    ]
    return "Largest projected overshoots: " + ", ".join(parts) + "."


def _generic_summary(children: List[dict], today: date) -> str:
    return "; ".join(str(c.get("title")) for c in children[:OVERSHOOT_LABELS]) + (
        "; ..." if len(children) > OVERSHOOT_LABELS else ""
    )


_SUMMARIES = {
    "price_targets": _price_target_summary,
    "budget_pace": _pace_summary,
}

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _make_group(family: str, children: List[dict], today: date) -> Dict[str, Any]:
    ordered = sort_by_priority(children)
    severity = min(
        (c.get("severity") for c in ordered),
        key=lambda s: _SEVERITY_RANK.get(s, 99),
    )
    links = {c.get("link_page") for c in ordered}
    group = {
        "id": f"group:{family}",
        "kind": "group",
        "family": family,
        "count": len(ordered),
        "category": ordered[0].get("category"),
        "severity": severity,
        "title": FAMILY_TITLES[family].format(n=len(ordered)),
        "detail": _SUMMARIES.get(family, _generic_summary)(ordered, today),
        "link_page": ordered[0].get("link_page") if len(links) == 1 else None,
        "status": "open",
        "ack": None,
        "children": ordered,
    }
    # A group is as urgent as its most urgent child.
    group["priority"] = (
        SEVERITY_BASE.get(severity, 0)
        + CATEGORY_WEIGHT.get(group["category"], DEFAULT_CATEGORY_WEIGHT)
        + max(_urgency(c, today) for c in ordered)
    )
    return group


def fold(items: List[dict], today: Optional[date] = None) -> List[dict]:
    """Fold families of 3+ open items into group items.

    Each item must already carry ``status`` (follow-up annotated) and
    ``priority``. Checked and snoozed items are never folded or counted. A
    group takes the place of its first child in the list; the result is not
    re-sorted here.

    Args:
        items: Annotated items, in list order.
        today: Injectable clock for the group's own priority.

    Returns:
        Items with each qualifying family replaced by one group.
    """
    today = today or date.today()
    members: Dict[str, List[dict]] = {}
    for item in items:
        family = family_of(item)
        if family and item.get("status", "open") == "open":
            members.setdefault(family, []).append(item)
    folded = {f for f, m in members.items() if len(m) >= GROUP_MIN_ITEMS}
    out: List[dict] = []
    placed = set()
    for item in items:
        family = family_of(item)
        if family in folded and item.get("status", "open") == "open":
            if family not in placed:
                placed.add(family)
                out.append(_make_group(family, members[family], today))
            continue
        out.append(item)
    return out
