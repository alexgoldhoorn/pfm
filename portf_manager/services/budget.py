"""Budget planning and budget-vs-actual variance.

A budget is a named, open-ended set of planned monthly amounts keyed to things
pfm already tracks: spending and income categories (the ``spending_categories``
Income/Spend tree), per-broker investment contributions (``bookings``), and debt
payments. Several budgets can coexist as scenarios (base / best case / worst
case); exactly one is flagged active.

Everything here that can be pure is pure — the month arithmetic, the override
expansion, the tree walks and the variance sign convention are all plain
functions over plain data, so they are testable without a database or a network
FX lookup. The one aggregator that needs both, ``compute_budget_variance``,
takes its FX converter as an argument rather than importing one.

Sign convention, applied everywhere: amounts are positive magnitudes within a
section, and ``variance_eur`` is signed so that **positive always means
favourable** — under plan for spending/debt/investment, over plan for income.
Each line also carries an explicit ``favourable`` flag so callers never have to
re-derive the direction.
"""

import calendar
import json
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services.cashflow_forecast import (
    BASELINE_MONTHS,
    MIN_RELIABLE_BASELINE_MONTHS,
    baseline_month_list,
    everyday_outflows,
)
from portf_manager.services import loans as loan_calc
from portf_manager.services.life_scenarios import UNCLASSIFIED, tier_of
from portf_manager.services.recurring import (
    last_import_dates,
    load_recurring,
    upcoming_charges,
)
from portf_manager.services.spending_insights import (
    effective_data_through,
    load_rows,
    shift_month,
)

# Line types, and the section each one reports under.
LINE_TYPES = ("income", "spending", "debt", "investment")

SECTION_LABELS = {
    "income": "Income",
    "spending": "Spending",
    "debt": "Debt",
    "investment": "Investments",
}

# A line is "over" when it misses plan by both of these, whether measured on
# what has been spent (the overrun check) or on where the month is heading (the
# pace). One definition, so the two Action Items agree.
OVERRUN_PCT = 10.0
OVERRUN_EUR = 50.0

# Share of the month that must have elapsed before a month-to-date run rate is
# trusted. It is only the fallback for a cost with no reliable baseline (fewer
# than MIN_RELIABLE_BASELINE_MONTHS complete months): below this share the
# pace says ``too_early`` instead of projecting a few early purchases.
PACE_MIN_ELAPSED_SHARE = 0.25

PERIODS = ("monthly", "yearly")
# Only cost lines can be yearly (a sinking fund) or roll over.
FUND_LINE_TYPES = ("spending", "debt")

# Which tree root a category-keyed line must live under. Investment lines are
# keyed by portfolio id, not by a category, so they have no root.
LINE_TYPE_ROOT = {
    "income": "Income",
    "spending": "Spend",
    "debt": "Spend",
}


def is_broker_ref(ref_key) -> bool:
    """True if an investment line's ref_key names a broker rather than a category.

    An investment line can be measured two ways: from the **broker side**
    (`ref_key` is a `portfolio_id`, actual = net Deposit bookings) or from the
    **bank side** (`ref_key` is a spending category, actual = what left the
    bank account under that category). The bank side is what makes an untracked
    destination — a pension plan, say — budgetable at all, and it's the only
    side that can be reclassified out of the spending total.

    The two are told apart by shape: an all-digits ref_key is a portfolio id.
    The router rejects an all-digits *category* name on an investment line so
    the two can never collide.
    """
    return str(ref_key).isdigit()


def line_uses_category(line_type: str, ref_key) -> bool:
    """True if this line resolves against the spending category tree.

    Income, spending and debt lines always do; an investment line does when
    it's keyed to a category rather than a broker. Everything that resolves
    against the tree shares one coverage space, so a category can't be
    budgeted twice under different line types.
    """
    if line_type != "investment":
        return True
    return not is_broker_ref(ref_key)


# For these line types a smaller actual than planned is the good outcome: they
# are costs. Income and investment go the other way — earning more than planned
# and contributing more than planned both build wealth, so for those an actual
# above plan is what's favourable.
FAVOURABLE_WHEN_UNDER = {"spending", "debt"}

# Depth guard for every tree walk, matching database.get_spending_category_root
# and the spending router's own _rollup_key/_subtree_names guards — a cycle that
# slipped past reparent's check must not hang a request.
_MAX_TREE_DEPTH = 100


def parse_overrides(raw: Optional[str]) -> Dict[str, float]:
    """Parse a budget line's per-month override JSON into a plain dict.

    Tolerant by design: ``None``, an empty string, malformed JSON, a non-object
    payload and non-numeric values all degrade to an empty dict (or are dropped)
    rather than raising, so one bad row can never 500 a whole variance report.

    Args:
        raw: The stored JSON text, e.g. ``'{"2026-03": 450.0}'``.

    Returns:
        Month key -> amount, e.g. ``{"2026-03": 450.0}``.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, float] = {}
    for month, amount in parsed.items():
        try:
            out[str(month)] = float(amount)
        except (TypeError, ValueError):
            continue
    return out


def serialize_overrides(overrides: Optional[Dict[str, float]]) -> Optional[str]:
    """Serialize an override dict for storage. Empty/None becomes NULL."""
    if not overrides:
        return None
    return json.dumps({str(k): float(v) for k, v in overrides.items()})


def month_key(value: str) -> str:
    """The ``YYYY-MM`` bucket an ISO date string falls in."""
    return str(value)[:7]


def _today() -> date:
    """Today; the one place tests patch to fix the clock."""
    return date.today()


def current_month() -> str:
    """This calendar month as ``YYYY-MM``."""
    return _today().strftime("%Y-%m")


def month_range(end_month: str, count: int) -> List[str]:
    """The ``count`` calendar months ending at ``end_month``, oldest first.

    Mirrors the month arithmetic in the spending router's ``/trend`` endpoint so
    the two surfaces bucket identically.

    Args:
        end_month: Last month of the range, ``"YYYY-MM"``.
        count: How many months to return (values below 1 yield an empty list).

    Returns:
        e.g. ``month_range("2026-02", 3) == ["2025-12", "2026-01", "2026-02"]``.
    """
    if count < 1:
        return []
    year, month = int(end_month[:4]), int(end_month[5:7])
    month -= count - 1
    while month <= 0:
        month += 12
        year -= 1
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def planned_for_months(
    monthly_amount: float, overrides: Dict[str, float], months: List[str]
) -> Dict[str, float]:
    """Expand a line's default monthly amount plus overrides across months."""
    base = float(monthly_amount or 0)
    return {m: float(overrides.get(m, base)) for m in months}


def build_children_index(tree: List[Dict]) -> Dict[Optional[str], List[Dict]]:
    """Group a category tree (``db.list_spending_categories_tree()``) by parent."""
    children_by_parent: Dict[Optional[str], List[Dict]] = {}
    for node in tree:
        children_by_parent.setdefault(node["parent_name"], []).append(node)
    return children_by_parent


def subtree_names(
    children_by_parent: Dict[Optional[str], List[Dict]], name: str
) -> List[str]:
    """A category plus every category beneath it, depth-guarded.

    Shared with the spending router's ``/categories/breakdown``, which used to
    carry its own copy of this walk.
    """

    def walk(node_name: str, depth: int = 0) -> List[str]:
        if depth > _MAX_TREE_DEPTH:
            return [node_name]
        names = [node_name]
        for child in children_by_parent.get(node_name, []):
            names.extend(walk(child["name"], depth + 1))
        return names

    return walk(name)


def is_ancestor(tree: List[Dict], ancestor_name: str, candidate_name: str) -> bool:
    """True if ``ancestor_name`` sits above ``candidate_name`` in the tree."""
    by_name = {node["name"]: node for node in tree}
    node = by_name.get(candidate_name)
    for _ in range(_MAX_TREE_DEPTH):
        if node is None or not node.get("parent_name"):
            return False
        if node["parent_name"] == ancestor_name:
            return True
        node = by_name.get(node["parent_name"])
    return False


def coverage_conflict(
    tree: List[Dict], existing_ref_keys: List[str], candidate: str
) -> Optional[str]:
    """The existing budgeted category that would double-count ``candidate``.

    A budget must not hold two category lines where one sits above the other
    (e.g. ``Housing`` and ``Housing > Mortgage``) — the parent's actual already
    sums the child's subtree, so both lines would count the same euros.

    Returns:
        The conflicting existing key, or None if ``candidate`` is safe to add.
    """
    for existing in existing_ref_keys:
        if existing == candidate:
            return existing
        if is_ancestor(tree, existing, candidate) or is_ancestor(
            tree, candidate, existing
        ):
            return existing
    return None


def category_path(tree: List[Dict], name: str) -> str:
    """A category's full breadcrumb, e.g. ``"Spend > Insurance > Car"``."""
    by_name = {node["name"]: node for node in tree}
    parts = [name]
    node = by_name.get(name)
    for _ in range(_MAX_TREE_DEPTH):
        if node is None or not node.get("parent_name"):
            break
        parts.append(node["parent_name"])
        node = by_name.get(node["parent_name"])
    return " > ".join(reversed(parts))


def variance(
    planned: float, actual: float, favourable_when_under: bool
) -> Dict[str, object]:
    """Signed variance plus its direction, so positive always means favourable.

    Args:
        planned: Planned amount for the period (positive magnitude).
        actual: Actual amount for the period (positive magnitude).
        favourable_when_under: True for spending-shaped lines (under plan is
            good), False for income (over plan is good).

    Returns:
        ``{"variance_eur": float, "variance_pct": float | None,
        "favourable": bool}``. ``variance_pct`` is None when nothing was
        planned — a percentage against a zero base is meaningless, and callers
        should render an em dash rather than an infinity.
    """
    signed = (planned - actual) if favourable_when_under else (actual - planned)
    pct = round(signed / planned * 100, 1) if planned else None
    return {
        "variance_eur": round(signed, 2),
        "variance_pct": pct,
        "favourable": signed >= 0,
    }


def budgeted_or_above(by_name: Dict[str, Dict], covered: set) -> set:
    """``covered`` plus every ancestor of anything in it.

    Climbing an unbudgeted charge up past one of these would report a
    partly-budgeted parent as unbudgeted — e.g. showing "Housing" as unbudgeted
    while a "Housing > Rent" line sits right above it in the same table.
    """
    blocked = set(covered)
    for name in covered:
        node = by_name.get(name)
        for _ in range(_MAX_TREE_DEPTH):
            if node is None or not node.get("parent_name"):
                break
            blocked.add(node["parent_name"])
            node = by_name.get(node["parent_name"])
    return blocked


def uncovered_rollup_key(by_name: Dict[str, Dict], blocked: set, category: str) -> str:
    """Where an unbudgeted category's actual should be reported.

    Walks up from ``category`` to the highest ancestor that is still outside the
    budget's reach, stopping below the tree root and below anything budgeted or
    holding a budgeted descendant. So with no lines at all, everything rolls up
    to the direct children of Income/Spend; with a line on ``Housing > Rent``, a
    stray ``Housing > Utilities`` charge is reported as ``Utilities`` — naming
    the line worth adding — rather than as a confusing second ``Housing`` row.

    Args:
        by_name: Category tree indexed by name.
        blocked: Output of ``budgeted_or_above`` for this section.
        category: The category the actual was recorded against.

    Returns:
        The category to report under. A category that isn't in the tree at all
        (``uncategorized``, or a name only ever seen on a transaction) is
        reported under its own name.
    """
    node = by_name.get(category)
    if node is None:
        return category
    best = category
    current = node
    for _ in range(_MAX_TREE_DEPTH):
        parent = current.get("parent_name")
        if not parent:
            break
        parent_node = by_name.get(parent)
        if parent_node is None or parent_node.get("is_root") or parent in blocked:
            break
        best = parent
        current = parent_node
    return best


def _month_end(month: str) -> str:
    """Last calendar day of a ``YYYY-MM`` month, as an ISO date string."""
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        return f"{year:04d}-12-31"
    return (date(year, mon + 1, 1) - timedelta(days=1)).isoformat()


def _collect_category_actuals(
    rows: List[Dict], months: set, fx: Callable[[str], float]
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Bucket spending rows into ``{month: {category: {"spend": x, "income": y}}}``.

    Amounts are positive magnitudes, EUR-converted. Callers pass rows that
    already exclude transfers.
    """
    buckets: Dict[str, Dict[str, Dict[str, float]]] = {}
    for row in rows:
        month = month_key(row["date"])
        if month not in months:
            continue
        amount_eur = float(row["amount"]) * fx(row.get("currency") or "EUR")
        cell = buckets.setdefault(month, {}).setdefault(
            row["category"], {"spend": 0.0, "income": 0.0}
        )
        if amount_eur < 0:
            cell["spend"] += abs(amount_eur)
        else:
            cell["income"] += amount_eur
    return buckets


def _collect_booking_actuals(
    bookings: List[Dict], months: set, fx: Callable[[str], float]
) -> Dict[str, Dict[str, float]]:
    """Net contributions per ``{month: {portfolio_id_str: deposits - withdrawals}}``."""
    buckets: Dict[str, Dict[str, float]] = {}
    for booking in bookings:
        if booking.get("portfolio_id") is None:
            continue
        month = month_key(booking["date"])
        if month not in months:
            continue
        amount_eur = float(booking["amount"]) * fx(booking.get("currency") or "EUR")
        signed = amount_eur if booking["action"] == "Deposit" else -amount_eur
        key = str(booking["portfolio_id"])
        month_bucket = buckets.setdefault(month, {})
        month_bucket[key] = month_bucket.get(key, 0.0) + signed
    return buckets


def months_without_activity(variance: Dict) -> List[str]:
    """Months in a variance report with no imported activity at all.

    Actuals only exist for what has been imported, so a month nobody has
    imported yet reads as zero of everything — which looks like heroic
    underspending on a cost and like falling behind on income or a
    contribution. Any consumer that judges a line against its plan has to
    exclude these months or it reports noise; the Python twin of the web
    client's ``budgetMonthsWithoutActivity``.
    """
    months = variance.get("months") or []
    out = []
    for month in months:
        total = 0.0
        for section in variance.get("sections") or []:
            total += sum(
                line["actual_eur"].get(month, 0.0) for line in section["lines"]
            )
            total += sum(
                item["actual_eur"].get(month, 0.0) for item in section["unbudgeted"]
            )
        if total == 0:
            out.append(month)
    return out


def history_months(months: List[str]) -> List[str]:
    """``months`` plus every earlier month of the same calendar years.

    Yearly funds and rollover start over every January, so a window that
    starts in July still needs January to June to know what has been set aside
    and spent.
    """
    out = set(months)
    for month in months:
        for k in range(1, int(month[5:7]) + 1):
            out.add(f"{month[:4]}-{k:02d}")
    return sorted(out)


def rolling_months(months: List[str], length: int = 12) -> List[str]:
    """``months`` plus the ``length - 1`` months before the first one.

    A yearly line looks at the 12 months ending at each month, so the window
    needs the 11 months that precede it.
    """
    if not months:
        return []
    count = _month_index(months[-1]) - _month_index(months[0]) + length
    return month_range(months[-1], count)


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7])


def rollover_ledger(
    planned: Dict[str, float],
    actual: Dict[str, float],
    imported: set,
    months: List[str],
) -> Dict[str, Dict[str, float]]:
    """Carry under- and overspend from month to month within a calendar year.

    Walks ``months`` oldest first. What a month leaves over (planned minus
    actual) carries into the next month of the same year; a negative carry is
    an overspend that later months have to make up. Only months that have
    imported rows take part: a month nobody has imported reads as zero spent,
    and carrying that forward would invent a surplus. The balance starts over
    in January, and what was left at the end of December is forfeited.

    Args:
        planned: month -> planned (or set-aside) amount.
        actual: month -> actual spend.
        imported: months that have imported rows.
        months: the calendar months to walk, oldest first.

    Returns:
        ``{"carried": {m: x}, "available": {m: planned + carried},
        "closing": {m: balance after the month}, "forfeited": {m: x}}``
        where ``forfeited[m]`` is the balance dropped when ``m`` is the first
        month of a new year in the walk.
    """
    carried: Dict[str, float] = {}
    available: Dict[str, float] = {}
    closing: Dict[str, float] = {}
    forfeited: Dict[str, float] = {}
    balance = 0.0
    previous_year = None
    for month in months:
        if previous_year is not None and month[:4] != previous_year:
            forfeited[month] = balance
            balance = 0.0
        previous_year = month[:4]
        carried[month] = balance
        available[month] = planned.get(month, 0.0) + balance
        if month in imported:
            balance += planned.get(month, 0.0) - actual.get(month, 0.0)
        closing[month] = balance
    return {
        "carried": carried,
        "available": available,
        "closing": closing,
        "forfeited": forfeited,
    }


def pace_status(
    actual_to_date: float, projected: float, limit: float
) -> Dict[str, Any]:
    """Where a cost is heading against what it may spend this month.

    ``over`` means more than the limit is already spent. ``at_risk`` means the
    month is projected past the limit by both ``OVERRUN_EUR`` and
    ``OVERRUN_PCT`` (the thresholds of the overrun check). Anything else is
    ``on_track``; a projection a few euros over is not a warning.
    """
    over_eur = max(0.0, projected - limit)
    meaningful = over_eur >= OVERRUN_EUR and (
        limit <= 0 or over_eur / limit * 100 >= OVERRUN_PCT
    )
    if actual_to_date > limit:
        status = "over"
    elif meaningful:
        status = "at_risk"
    else:
        status = "on_track"
    return {
        "status": status,
        "projected_over_eur": round(over_eur, 2),
        "meaningful_overshoot": meaningful,
    }


class _PaceContext:
    """What the month in progress needs to project a cost line to month end.

    Projected month end = actual to date + recurring charges still due this
    month + the line's baseline everyday average x the share of the month
    still to come. The baseline everyday average is what the line's categories
    cost per month over the baseline months, with one-off rows and rows of a
    recurring series left out (``cashflow_forecast.everyday_outflows``, the
    rule the forecast uses): a lump sum paid early in the month is neither
    extrapolated nor double counted.

    Without a reliable baseline (fewer than ``MIN_RELIABLE_BASELINE_MONTHS``
    complete months) the month-to-date run rate stands in once at least
    ``PACE_MIN_ELAPSED_SHARE`` of the month has elapsed; before that the pace
    says ``too_early``.

    Elapsed share is measured to the date the data runs to: the earliest
    last-import date over the accounts, not today. Recurring charges still to
    arrive are counted per account, after its own last import.
    """

    def __init__(
        self, month, through, rows, recurring_ids, upcoming, base_months, base_by_month
    ):
        self.month = month
        self.through = through
        year, mon = int(month[:4]), int(month[5:7])
        self.days = calendar.monthrange(year, mon)[1]
        start = date(year, mon, 1)
        self.elapsed = 0.0 if through < start else min(1.0, through.day / self.days)
        self.rows = rows
        self.recurring_ids = recurring_ids
        self.upcoming = upcoming
        self.base_months = base_months
        self.base_by_month = base_by_month

    @property
    def reliable(self) -> bool:
        return len(self.base_months) >= MIN_RELIABLE_BASELINE_MONTHS

    def categories(self) -> set:
        """Every category with spending this month or in the baseline months."""
        out = {r["category"] for r in self.rows if r["amount_eur"] < 0}
        for cats in self.base_by_month.values():
            out.update(cats)
        return out

    def baseline_everyday(self, names: set) -> Optional[float]:
        """Average monthly everyday spending of ``names``; None if unreliable."""
        if not self.reliable:
            return None
        total = sum(
            amount
            for m in self.base_months
            for category, amount in self.base_by_month.get(m, {}).items()
            if category in names
        )
        return total / len(self.base_months)

    def project(self, names: set, limit: float) -> Dict[str, Any]:
        spent = arrived = one_off = 0.0
        for r in self.rows:
            if r["category"] not in names or r["amount_eur"] >= 0:
                continue
            amount = -r["amount_eur"]
            spent += amount
            if r["id"] in self.recurring_ids:
                arrived += amount
            elif r["one_off"]:
                one_off += amount
        remaining_recurring = sum(
            amount for category, amount in self.upcoming if category in names
        )
        base_avg = self.baseline_everyday(names)
        everyday_left: Optional[float]
        if self.elapsed >= 1.0:
            everyday_left, method = 0.0, "month_complete"
        elif base_avg is not None:
            everyday_left, method = base_avg * (1.0 - self.elapsed), "baseline"
        elif self.elapsed >= PACE_MIN_ELAPSED_SHARE:
            everyday = spent - arrived - one_off
            everyday_left = everyday * (1.0 - self.elapsed) / self.elapsed
            method = "run_rate"
        else:
            everyday_left, method = None, None
        out = {
            "month": self.month,
            "data_through": self.through.isoformat(),
            "elapsed_share": round(self.elapsed, 3),
            "actual_to_date_eur": round(spent, 2),
            "limit_eur": round(limit, 2),
            "method": method,
            "baseline_everyday_eur": None if base_avg is None else round(base_avg, 2),
            "baseline_months": len(self.base_months),
            "expected_recurring_remaining_eur": round(remaining_recurring, 2),
            "everyday_remaining_eur": (
                None if everyday_left is None else round(everyday_left, 2)
            ),
        }
        if everyday_left is None:
            out.update(
                projected_month_end_eur=None,
                status="too_early",
                projected_over_eur=None,
                meaningful_overshoot=False,
            )
            return out
        projected = spent + remaining_recurring + everyday_left
        out["projected_month_end_eur"] = round(projected, 2)
        out.update(pace_status(spent, projected, limit))
        return out


def data_through_by_account(db, start: Optional[str]) -> Dict[str, Any]:
    """``effective_data_through`` over the database's bank accounts.

    An account's last import is the date of its latest spending row (the
    window rule of ``effective_data_through`` then drops accounts with no row
    since ``start``).
    """
    last = {
        pid: str(d["last_spending_date"])[:10]
        for pid, d in db.get_portfolio_date_ranges().items()
        if d and d.get("last_spending_date")
    }
    info = effective_data_through(last, start)
    info["last_by_account"] = last
    return info


def _pace_context(
    db,
    by_name: Dict[str, dict],
    months: List[str],
    fx,
    today: date,
    warnings: List[str],
) -> Optional[_PaceContext]:
    """The pace context for the month in progress, or None.

    None when that month is not in the window or has nothing imported: with no
    statement for it, zero spent reads as a month on track, which is the
    flattering number the pace must never show.
    """
    month = today.strftime("%Y-%m")
    if month not in months:
        return None
    start = f"{shift_month(month, -BASELINE_MONTHS)}-01"
    rows = load_rows(db, fx, start_date=start)
    in_month = [r for r in rows if r["date"][:7] == month and not r["is_transfer"]]
    if not in_month:
        return None
    month_end = date.fromisoformat(_month_end(month))
    through = max(date.fromisoformat(r["date"]) for r in rows if r["date"][:7] == month)
    try:
        info = data_through_by_account(db, start)
        if info["effective"]:
            through = date.fromisoformat(info["effective"])
    except Exception as e:
        warnings.append(f"Data-through date could not be read: {e}")
    through = min(through, month_end)

    series = []
    last_import: Dict[int, date] = {}
    try:
        series = load_recurring(db)
        last_import = last_import_dates(db)
    except Exception as e:
        warnings.append(f"Recurring charges could not be read for the pace: {e}")
    month_start = date(int(month[:4]), int(month[5:7]), 1)
    upcoming = []
    for s, due in upcoming_charges(series, last_import, month_end):
        if due >= month_start:
            upcoming.append((s.category, s.typical_amount * fx(s.currency)))
    recurring_ids = {i for s in series for i in s.transaction_ids}
    base_months = baseline_month_list(rows, through, today)
    base_by_month = (
        everyday_outflows(rows, by_name, recurring_ids, base_months)
        if base_months
        else {}
    )
    return _PaceContext(
        month,
        through,
        in_month,
        recurring_ids,
        upcoming,
        base_months,
        base_by_month,
    )


def _link_info(
    line: Dict,
    link_type: str,
    liabilities: Dict[int, Dict],
    loans_by_id: Dict[int, Dict],
    fx: Callable[[str], float],
    today: date,
    month: str,
) -> Dict[str, Any]:
    """What a line's ``link_id`` points at, for display next to the line.

    A link to a deleted manual liability or loan is not an error: the line
    comes back with a null balance and ``link_broken`` true. Only a debt line
    carries a link in the UI, but a stored one is always resolved.
    """
    link_id = line.get("link_id")
    info: Dict[str, Any] = {
        "link_id": link_id,
        "link_type": link_type,
        "link_label": None,
        "link_amount_eur": None,
        "link_broken": False,
    }
    if link_id is None:
        return info
    if link_type == "loan":
        loan = loans_by_id.get(link_id)
        if loan is None:
            info["link_broken"] = True
            return info
        info["link_label"] = loan["name"]
        try:
            info["link_amount_eur"] = round(
                loan_calc.balance_on(loan, today) * fx(loan.get("currency") or "EUR"),
                2,
            )
            split = loan_calc.payment_split(loan, month)
        except Exception:  # a loan with an unreadable date must not break the page
            return info
        if split:
            rate = fx(loan.get("currency") or "EUR")
            info["link_principal_eur"] = round(split["principal"] * rate, 2)
            info["link_interest_eur"] = round(split["interest"] * rate, 2)
        return info
    liability = liabilities.get(link_id)
    if liability is None:
        info["link_broken"] = True
        return info
    info["link_label"] = liability.get("name")
    info["link_amount_eur"] = round(
        float(liability["amount"] or 0) * fx(liability.get("currency") or "EUR"), 2
    )
    return info


def compute_budget_variance(
    db,
    budget_id: int,
    months: List[str],
    fx: Callable[[str], float],
    today: Optional[date] = None,
) -> Dict:
    """Planned vs actual for one budget over a list of calendar months.

    Actuals come from the same sources, with the same conventions, as the
    Spending page: ``spending_transactions`` excluding transfers, converted at
    today's FX rate, bucketed by ``date[:7]``. Investment lines instead read net
    ``bookings`` (Deposits minus Withdrawals) for their portfolio — transfers
    into a broker are already excluded on the spending side, so the same euros
    are never counted twice. Actual totals never depend on anything below:
    pace, fund and rollover are extra fields, so the variance still reconciles
    with ``/spending/trend`` to the cent.

    **Yearly lines** (``period='yearly'``, spending and debt only). The stored
    ``monthly_amount`` is the *yearly* figure. Each month sets aside a twelfth
    of it (``planned_eur`` per month; a per-month override replaces the
    twelfth): that is the cash to put aside, not a limit. The limit is a
    rolling 12 months: in month m, ``available_eur`` is the yearly amount less
    what the line spent in the 11 months before m, so a bill is covered
    whenever the 12 months ending at it stay within the yearly amount, and the
    line is over only when they do not. There is no January reset.
    ``carried_eur`` is ``available`` less that month's twelfth. For a window,
    ``available_total`` is set so that the variance is the fund left at the
    window's last month. ``fund`` describes those 12 months (``window_start``,
    ``window_end``, ``spent_eur``, ``remaining_eur``). A month of the span
    without imports cannot be known and is named in a warning.

    **Rollover** (``rollover=1``, spending and debt only). What earlier months
    of the same calendar year left over (or overspent) carries into the month:
    ``carried_eur`` and ``available_eur`` per month. Months without imports do
    not take part, and the balance starts over in January for the per-month
    figures. The variance of a window is ``carried into its first month +
    sum(planned) - sum(actual)``: the January reset never changes the window
    total, so a December overspend (or an underspend built inside the window)
    is neither hidden nor lost. Every other line has ``available_total ==
    planned_total``, so nothing changes for a budget without these features.

    **Pace**. For the month in progress each cost line (and the spending
    section) gains ``pace``; see ``_PaceContext``. No pace for a month with
    nothing imported.

    Args:
        db: Database handle.
        budget_id: Which budget to report on.
        months: Calendar months, oldest first (see ``month_range``).
        fx: Currency code -> EUR rate.
        today: Reference date (default: today); fixes "the month in progress".

    Returns:
        A dict with ``budget_id``, ``budget_name``, ``months``, ``as_of``,
        ``data_through``, ``sections`` (one per line type, each with
        ``lines``, ``unbudgeted`` and totals; the spending section also has
        ``tier_totals`` and ``pace``), an overall ``net`` block and
        ``warnings``.
    """
    today = today or _today()
    warnings: List[str] = []
    budget = db.get_budget(budget_id)
    if budget is None:
        raise ValueError(f"Budget {budget_id} not found")

    lines = db.list_budget_lines(budget_id)
    tree = db.list_spending_categories_tree()
    by_name = {node["name"]: node for node in tree}
    children_by_parent = build_children_index(tree)

    def _is_yearly(line: Dict) -> bool:
        return (
            line["line_type"] in FUND_LINE_TYPES
            and (line.get("period") or "monthly") == "yearly"
        )

    def _is_rollover(line: Dict) -> bool:
        return (
            line["line_type"] in FUND_LINE_TYPES
            and bool(line.get("rollover"))
            and not _is_yearly(line)
        )

    # Rollover needs every earlier month of the calendar year; a yearly line
    # needs the 11 months before the window.
    needed = set(months)
    if any(_is_rollover(line) for line in lines):
        needed.update(history_months(months))
    if any(_is_yearly(line) for line in lines):
        needed.update(rolling_months(months))
    calc_months = sorted(needed)
    month_set = set(months)
    calc_set = set(calc_months)

    start_date = f"{calc_months[0]}-01" if calc_months else None
    end_date = _month_end(months[-1]) if months else None
    rows = db.list_spending_transactions(
        start_date=start_date, end_date=end_date, is_transfer=False
    )
    category_actuals = _collect_category_actuals(rows, calc_set, fx)
    booking_actuals = _collect_booking_actuals(db.get_all_bookings(), calc_set, fx)
    imported = {month_key(r["date"]) for r in rows}

    pace_ctx = _pace_context(db, by_name, months, fx, today, warnings)
    # the date every account's data reaches: the earliest last import over the
    # accounts that have rows in the window
    through_all: Optional[str] = None
    try:
        info = data_through_by_account(db, f"{months[0]}-01" if months else None)
        last_dates = info["last_by_account"].values()
        through_all = info["effective"] or (max(last_dates) if last_dates else None)
    except Exception:
        through_all = pace_ctx.through.isoformat() if pace_ctx else None
    current = today.strftime("%Y-%m")

    portfolio_names = {str(p["id"]): p["name"] for p in db.get_all_portfolios()}
    # A debt line can point at the manual liability it repays, so the payment
    # shows next to the balance it's chipping away at. Display only -- the
    # balance never enters any total, because a bank statement can't say how
    # much of a payment was principal and how much was interest.
    liabilities = {
        item["id"]: item for item in db.get_manual_assets() if item["is_liability"]
    }
    # ... or at a loan record, whose balance and this month's split come from
    # its schedule (link_type 'loan')
    loans_by_id = {loan["id"]: loan for loan in db.get_loans()}

    # Every category any line covers — shared by spending and debt, which both
    # live under the Spend root, so an uncovered charge is only ever reported
    # once.
    covered_by_type: Dict[str, set] = {"income": set(), "spend": set()}
    non_spending_covered: set = set()
    for line in lines:
        # A category-keyed investment line covers its category on the SPEND
        # side even though it reports under Investments — otherwise the same
        # euros would show up again as unbudgeted spending.
        if not line_uses_category(line["line_type"], line["ref_key"]):
            continue
        bucket = "income" if line["line_type"] == "income" else "spend"
        names = subtree_names(children_by_parent, line["ref_key"])
        covered_by_type[bucket].update(names)
        if line["line_type"] in ("debt", "investment"):
            non_spending_covered.update(names)

    # An investment line can be measured from the bank side (a category's
    # outflows) or the broker side (Deposit bookings). A section must report ONE
    # basis: the bank outflow to a broker and that broker's deposit are the same
    # euros, so summing both double-counts -- and broker deposits are inflated
    # further by moves between the user's own accounts. Once any bank-side line
    # exists, the bank side is the chosen basis and broker-side leftovers are
    # suppressed rather than added.
    bank_basis = any(
        line["line_type"] == "investment" and not is_broker_ref(line["ref_key"])
        for line in lines
    )

    sections = []
    section_totals: Dict[str, Dict[str, float]] = {}
    for line_type in LINE_TYPES:
        favourable_when_under = line_type in FAVOURABLE_WHEN_UNDER
        section_lines = []
        for line in [line for line in lines if line["line_type"] == line_type]:
            period = line.get("period") or "monthly"
            yearly = _is_yearly(line)
            carries = _is_rollover(line)
            stored = float(line["monthly_amount"] or 0)
            planned_all = planned_for_months(
                stored / 12 if yearly else stored,
                parse_overrides(line.get("overrides")),
                calc_months,
            )
            planned = {m: planned_all[m] for m in months}
            actual_all = {}
            broker_keyed = line_type == "investment" and is_broker_ref(line["ref_key"])
            names = (
                []
                if broker_keyed
                else subtree_names(children_by_parent, line["ref_key"])
            )
            for month in calc_months:
                if broker_keyed:
                    actual_all[month] = max(
                        0.0, booking_actuals.get(month, {}).get(line["ref_key"], 0.0)
                    )
                else:
                    # A category-keyed investment line reads the outflow side,
                    # exactly like a spending line — it's the same bank rows,
                    # just reported as money kept rather than money spent.
                    field = "income" if line_type == "income" else "spend"
                    actual_all[month] = sum(
                        category_actuals.get(month, {}).get(name, {}).get(field, 0.0)
                        for name in names
                    )
            actual = {m: actual_all[m] for m in months}
            planned_total = sum(planned.values())
            actual_total = sum(actual.values())

            extra: Dict[str, Any] = {
                "carried_eur": None,
                "available_eur": None,
                "carried_total": None,
                "fund": None,
                "fund_balance_eur": None,
            }
            label = (
                portfolio_names.get(line["ref_key"], f"Portfolio {line['ref_key']}")
                if broker_keyed
                else category_path(tree, line["ref_key"])
            )
            available = dict(planned)
            available_total = planned_total
            if carries and months:
                ledger = rollover_ledger(planned_all, actual_all, imported, calc_months)
                carried = {m: ledger["carried"][m] for m in months}
                available = {m: planned[m] + carried[m] for m in months}
                # What was carried into the first month, plus the whole
                # window's plan, less its spend. The January reset belongs to
                # the per-month figures only: applying it to the total hid a
                # December overspend.
                available_total = planned_total + carried[months[0]]
                extra.update(
                    carried_eur={m: round(v, 2) for m, v in carried.items()},
                    available_eur={m: round(v, 2) for m, v in available.items()},
                    carried_total=round(carried[months[0]], 2),
                )
            elif yearly and months:
                # rolling 12 months: what is left of the yearly amount after
                # the 11 months before each month
                def spent_in(end: str) -> float:
                    return sum(actual_all.get(x, 0.0) for x in month_range(end, 12))

                spent12 = {m: spent_in(m) for m in months}
                available = {
                    m: stored - (spent12[m] - actual_all.get(m, 0.0)) for m in months
                }
                carried = {m: available[m] - planned[m] for m in months}
                last = months[-1]
                remaining_last = stored - spent12[last]
                # variance = the fund left at the window's last month
                available_total = actual_total + remaining_last
                span = month_range(last, 12)
                unknown = [x for x in span if x not in imported]
                if unknown:
                    warnings.append(
                        f"Yearly line {label}: no statement is imported for "
                        f"{', '.join(unknown)}, so what was spent in "
                        "those months is not known and the amount left may be "
                        "overstated."
                    )
                extra.update(
                    carried_eur={m: round(v, 2) for m, v in carried.items()},
                    available_eur={m: round(v, 2) for m, v in available.items()},
                    carried_total=round(carried[months[0]], 2),
                    fund={
                        "yearly_amount_eur": round(stored, 2),
                        "window_start": span[0],
                        "window_end": span[-1],
                        "spent_eur": round(spent12[last], 2),
                        "remaining_eur": round(remaining_last, 2),
                    },
                    fund_balance_eur={m: round(stored - spent12[m], 2) for m in months},
                )

            link_type = line.get("link_type") or "manual"
            link_info = _link_info(
                line, link_type, liabilities, loans_by_id, fx, today, current
            )
            pace = None
            if (
                pace_ctx is not None
                and favourable_when_under
                and current in available
                and not broker_keyed
            ):
                pace = pace_ctx.project(set(names), available[current])
            section_lines.append(
                {
                    "line_id": line["id"],
                    "line_type": line_type,
                    "ref_key": line["ref_key"],
                    "label": label,
                    "notes": line.get("notes"),
                    **link_info,
                    "monthly_amount": round(stored, 2),
                    "period": period,
                    "rollover": bool(line.get("rollover")),
                    "set_aside_eur": round(stored / 12 if yearly else stored, 2),
                    "tier": (
                        tier_of(line["ref_key"], by_name)
                        if favourable_when_under
                        and line_uses_category(line_type, line["ref_key"])
                        else None
                    ),
                    "planned_eur": {m: round(v, 2) for m, v in planned.items()},
                    "actual_eur": {m: round(v, 2) for m, v in actual.items()},
                    "planned_total": round(planned_total, 2),
                    "actual_total": round(actual_total, 2),
                    "available_total": round(available_total, 2),
                    "pace": pace,
                    **extra,
                    **variance(available_total, actual_total, favourable_when_under),
                }
            )
        section_lines.sort(key=lambda item: -item["planned_total"])

        unbudgeted = _unbudgeted_for_section(
            line_type,
            months,
            category_actuals,
            booking_actuals,
            by_name,
            covered_by_type,
            {
                line["ref_key"]
                for line in lines
                if line["line_type"] == "investment" and is_broker_ref(line["ref_key"])
            },
            portfolio_names,
            tree,
            bank_basis,
        )
        for item in unbudgeted:
            item["tier"] = (
                tier_of(item["ref_key"], by_name) if line_type == "spending" else None
            )

        planned_total = sum(item["planned_total"] for item in section_lines)
        available_total = sum(item["available_total"] for item in section_lines)
        actual_total = sum(item["actual_total"] for item in section_lines) + sum(
            item["actual_total"] for item in unbudgeted
        )
        section_totals[line_type] = {
            "planned": available_total,
            "actual": actual_total,
        }
        section: Dict[str, Any] = {
            "key": line_type,
            "label": SECTION_LABELS[line_type],
            "favourable_when_under": favourable_when_under,
            "lines": section_lines,
            "unbudgeted": unbudgeted,
            "unbudgeted_suppressed": bool(line_type == "investment" and bank_basis),
            "planned_total": round(planned_total, 2),
            "available_total": round(available_total, 2),
            "actual_total": round(actual_total, 2),
            "tier_totals": None,
            "pace": None,
            **variance(available_total, actual_total, favourable_when_under),
        }
        if line_type == "spending":
            section["tier_totals"] = _tier_totals(section_lines, unbudgeted)
            if pace_ctx is not None and current in month_set:
                # categories with spending so far, plus those with a recurring
                # charge still to arrive (no row yet, but money to come)
                spend_names = pace_ctx.categories() | {
                    category for category, _ in pace_ctx.upcoming
                }
                spend_names -= non_spending_covered
                limit = sum(
                    (item["available_eur"] or item["planned_eur"]).get(current, 0.0)
                    for item in section_lines
                )
                section["pace"] = pace_ctx.project(spend_names, limit)
        sections.append(section)

    outflow_planned = sum(
        section_totals[t]["planned"] for t in ("spending", "debt", "investment")
    )
    outflow_actual = sum(
        section_totals[t]["actual"] for t in ("spending", "debt", "investment")
    )
    net_planned = section_totals["income"]["planned"] - outflow_planned
    net_actual = section_totals["income"]["actual"] - outflow_actual

    return {
        "budget_id": budget_id,
        "budget_name": budget["name"],
        "is_active": bool(budget["is_active"]),
        "months": months,
        "as_of": today.isoformat(),
        "data_through": through_all,
        "sections": sections,
        "net": {
            "planned_total": round(net_planned, 2),
            "actual_total": round(net_actual, 2),
            # A bigger net than planned is the good outcome, like income.
            **variance(net_planned, net_actual, False),
        },
        "warnings": warnings,
    }


def _tier_totals(section_lines: List[Dict], unbudgeted: List[Dict]) -> Dict:
    """Planned and actual spending per essentiality tier.

    A line takes its category's (inherited) tier; a child category with a
    mark of its own is not split out of its parent's line. The actuals add up
    to the section's ``actual_total``.
    """
    from portf_manager.services.life_scenarios import TIERS

    totals = {
        t: {"planned_eur": 0.0, "actual_eur": 0.0} for t in (*TIERS, UNCLASSIFIED)
    }
    for item in section_lines:
        tier = item["tier"] or UNCLASSIFIED
        totals[tier]["planned_eur"] += item["planned_total"]
        totals[tier]["actual_eur"] += item["actual_total"]
    for item in unbudgeted:
        tier = item["tier"] or UNCLASSIFIED
        totals[tier]["actual_eur"] += item["actual_total"]
    return {
        tier: {k: round(v, 2) for k, v in vals.items()} for tier, vals in totals.items()
    }


def _unbudgeted_for_section(
    line_type: str,
    months: List[str],
    category_actuals: Dict,
    booking_actuals: Dict,
    by_name: Dict[str, Dict],
    covered_by_type: Dict[str, set],
    budgeted_portfolios: set,
    portfolio_names: Dict[str, str],
    tree: List[Dict],
    bank_basis: bool = False,
) -> List[Dict]:
    """Actuals in the period that no line in this section covers.

    Two sections are empty here by design. Debt shares the Spend tree with
    spending, so uncovered charges are reported once, in the Spending section.
    And once the budget measures investment from the bank side, leftover broker
    deposits are the same euros seen from the other end — reporting them would
    double the total.
    """
    if line_type == "debt":
        return []
    if line_type == "investment" and bank_basis:
        return []

    totals: Dict[str, Dict[str, float]] = {}

    if line_type == "investment":
        for month in months:
            for portfolio_id, amount in booking_actuals.get(month, {}).items():
                if portfolio_id in budgeted_portfolios or amount <= 0:
                    continue
                entry = totals.setdefault(portfolio_id, {})
                entry[month] = entry.get(month, 0.0) + amount
    else:
        field = "income" if line_type == "income" else "spend"
        covered = covered_by_type["income" if line_type == "income" else "spend"]
        blocked = budgeted_or_above(by_name, covered)
        for month in months:
            for category, cell in category_actuals.get(month, {}).items():
                amount = cell.get(field, 0.0)
                if amount <= 0 or category in covered:
                    continue
                # Attribution is by SIGN, not by tree position — the same rule
                # /spending/summary and /spending/trend use, so the three
                # reconcile. Filtering on the category's tree root instead
                # would silently drop three real cases: a refund in a Spend
                # category (positive, Spend-rooted), a charge in an Income
                # category, and anything unfiled — including "uncategorized",
                # which exists as a parentless tree node with no root at all.
                key = uncovered_rollup_key(by_name, blocked, category)
                entry = totals.setdefault(key, {})
                entry[month] = entry.get(month, 0.0) + amount

    result = []
    for key, per_month in totals.items():
        label = (
            portfolio_names.get(key, f"Portfolio {key}")
            if line_type == "investment"
            else (category_path(tree, key) if key in by_name else key)
        )
        result.append(
            {
                "ref_key": key,
                "label": label,
                "actual_eur": {m: round(per_month.get(m, 0.0), 2) for m in months},
                "actual_total": round(sum(per_month.values()), 2),
            }
        )
    result.sort(key=lambda item: -item["actual_total"])
    return result


def propose_budget_lines(db, months: int, fx: Callable[[str], float]) -> List[Dict]:
    """Suggest budget lines from the last N complete months of real activity.

    One proposal per direct child of Spend and of Income that actually saw
    money, plus one investment proposal per portfolio with net deposits. The
    current, partial month is excluded so a mid-month run doesn't halve every
    average.

    Writes nothing — the caller reviews the proposals and applies the ones it
    wants via the bulk-upsert path.

    Returns:
        Proposals as ``{line_type, ref_key, label, monthly_amount,
        months_seen, total_eur}``, largest first within each section.
    """
    today = date.today()
    end_year, end_month_num = today.year, today.month - 1
    if end_month_num == 0:
        end_month_num, end_year = 12, end_year - 1
    period = month_range(f"{end_year:04d}-{end_month_num:02d}", max(1, months))
    if not period:
        return []

    tree = db.list_spending_categories_tree()
    children_by_parent = build_children_index(tree)
    month_set = set(period)

    # A row marked one-off still counts in the budget's actuals, but a plan
    # built from averages must not carry it forward.
    rows = db.list_spending_transactions(
        start_date=f"{period[0]}-01",
        end_date=_month_end(period[-1]),
        is_transfer=False,
        one_off=False,
    )
    category_actuals = _collect_category_actuals(rows, month_set, fx)
    booking_actuals = _collect_booking_actuals(db.get_all_bookings(), month_set, fx)
    portfolio_names = {str(p["id"]): p["name"] for p in db.get_all_portfolios()}

    divisor = float(len(period))
    proposals: List[Dict] = []

    def _period_total(names: List[str], field: str) -> Dict[str, float]:
        return {
            month: sum(
                category_actuals.get(month, {}).get(name, {}).get(field, 0.0)
                for name in names
            )
            for month in period
        }

    def _proposal(line_type: str, ref_key: str, per_month: Dict[str, float]) -> Dict:
        total = sum(per_month.values())
        return {
            "line_type": line_type,
            "ref_key": ref_key,
            "label": category_path(tree, ref_key),
            "monthly_amount": round(total / divisor, 2),
            "months_seen": sum(1 for v in per_month.values() if v > 0),
            "total_eur": round(total, 2),
        }

    for line_type, root in (("income", "Income"), ("spending", "Spend")):
        field = "income" if line_type == "income" else "spend"
        children = children_by_parent.get(root, [])

        # Transactions can be filed against a tree root directly rather than
        # against one of its children. Proposing per-child lines then misses
        # that money entirely (a real account had ~99% of its income booked
        # straight against "Income", which produced a budget an order of
        # magnitude too small). A root line covers the whole subtree, so it
        # can't coexist with child lines — propose whichever side holds more.
        root_direct = _period_total([root], field)
        children_total = sum(
            sum(
                _period_total(
                    subtree_names(children_by_parent, child["name"]), field
                ).values()
            )
            for child in children
        )
        if sum(root_direct.values()) > children_total:
            # Propose the WHOLE subtree, not just the root-direct rows: a root
            # line's actual covers its children too, so a root-direct-only
            # amount would under-plan by whatever sits in the children.
            root_subtree = _period_total(subtree_names(children_by_parent, root), field)
            if sum(root_subtree.values()) > 0:
                proposals.append(_proposal(line_type, root, root_subtree))
            continue

        for child in children:
            names = subtree_names(children_by_parent, child["name"])
            per_month = _period_total(names, field)
            if sum(per_month.values()) <= 0:
                continue
            proposals.append(_proposal(line_type, child["name"], per_month))

    portfolio_totals: Dict[str, float] = {}
    for month in period:
        for portfolio_id, amount in booking_actuals.get(month, {}).items():
            portfolio_totals[portfolio_id] = (
                portfolio_totals.get(portfolio_id, 0.0) + amount
            )
    for portfolio_id, total in portfolio_totals.items():
        if total <= 0:
            continue
        proposals.append(
            {
                "line_type": "investment",
                "ref_key": portfolio_id,
                "label": portfolio_names.get(portfolio_id, f"Portfolio {portfolio_id}"),
                "monthly_amount": round(total / divisor, 2),
                "months_seen": sum(
                    1
                    for month in period
                    if booking_actuals.get(month, {}).get(portfolio_id, 0.0) > 0
                ),
                "total_eur": round(total, 2),
            }
        )

    order = {name: index for index, name in enumerate(LINE_TYPES)}
    proposals.sort(key=lambda p: (order[p["line_type"]], -p["monthly_amount"]))
    return proposals
