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

import json
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

# Line types, and the section each one reports under.
LINE_TYPES = ("income", "spending", "debt", "investment")

SECTION_LABELS = {
    "income": "Income",
    "spending": "Spending",
    "debt": "Debt",
    "investment": "Investments",
}

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


def current_month() -> str:
    """This calendar month as ``YYYY-MM``."""
    return date.today().strftime("%Y-%m")


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


def compute_budget_variance(
    db,
    budget_id: int,
    months: List[str],
    fx: Callable[[str], float],
) -> Dict:
    """Planned vs actual for one budget over a list of calendar months.

    Actuals come from the same sources, with the same conventions, as the
    Spending page: ``spending_transactions`` excluding transfers, converted at
    today's FX rate, bucketed by ``date[:7]``. Investment lines instead read net
    ``bookings`` (Deposits minus Withdrawals) for their portfolio — transfers
    into a broker are already excluded on the spending side, so the same euros
    are never counted twice.

    Args:
        db: Database handle.
        budget_id: Which budget to report on.
        months: Calendar months, oldest first (see ``month_range``).
        fx: Currency code -> EUR rate.

    Returns:
        A dict with ``budget_id``, ``budget_name``, ``months``, ``sections``
        (one per line type, each with ``lines``, ``unbudgeted`` and totals) and
        an overall ``net`` block.
    """
    budget = db.get_budget(budget_id)
    if budget is None:
        raise ValueError(f"Budget {budget_id} not found")

    lines = db.list_budget_lines(budget_id)
    tree = db.list_spending_categories_tree()
    by_name = {node["name"]: node for node in tree}
    children_by_parent = build_children_index(tree)
    month_set = set(months)

    start_date = f"{months[0]}-01" if months else None
    end_date = _month_end(months[-1]) if months else None
    rows = db.list_spending_transactions(
        start_date=start_date, end_date=end_date, is_transfer=False
    )
    category_actuals = _collect_category_actuals(rows, month_set, fx)
    booking_actuals = _collect_booking_actuals(db.get_all_bookings(), month_set, fx)

    portfolio_names = {str(p["id"]): p["name"] for p in db.get_all_portfolios()}

    # Every category any line covers — shared by spending and debt, which both
    # live under the Spend root, so an uncovered charge is only ever reported
    # once.
    covered_by_type: Dict[str, set] = {"income": set(), "spend": set()}
    for line in lines:
        # A category-keyed investment line covers its category on the SPEND
        # side even though it reports under Investments — otherwise the same
        # euros would show up again as unbudgeted spending.
        if not line_uses_category(line["line_type"], line["ref_key"]):
            continue
        bucket = "income" if line["line_type"] == "income" else "spend"
        covered_by_type[bucket].update(
            subtree_names(children_by_parent, line["ref_key"])
        )

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
            planned = planned_for_months(
                line["monthly_amount"], parse_overrides(line.get("overrides")), months
            )
            actual = {}
            broker_keyed = line_type == "investment" and is_broker_ref(line["ref_key"])
            names = (
                []
                if broker_keyed
                else subtree_names(children_by_parent, line["ref_key"])
            )
            for month in months:
                if broker_keyed:
                    actual[month] = max(
                        0.0, booking_actuals.get(month, {}).get(line["ref_key"], 0.0)
                    )
                else:
                    # A category-keyed investment line reads the outflow side,
                    # exactly like a spending line — it's the same bank rows,
                    # just reported as money kept rather than money spent.
                    field = "income" if line_type == "income" else "spend"
                    actual[month] = sum(
                        category_actuals.get(month, {}).get(name, {}).get(field, 0.0)
                        for name in names
                    )
            planned_total = sum(planned.values())
            actual_total = sum(actual.values())
            label = (
                portfolio_names.get(line["ref_key"], f"Portfolio {line['ref_key']}")
                if broker_keyed
                else category_path(tree, line["ref_key"])
            )
            section_lines.append(
                {
                    "line_id": line["id"],
                    "line_type": line_type,
                    "ref_key": line["ref_key"],
                    "label": label,
                    "notes": line.get("notes"),
                    "link_id": line.get("link_id"),
                    "monthly_amount": round(float(line["monthly_amount"] or 0), 2),
                    "planned_eur": {m: round(v, 2) for m, v in planned.items()},
                    "actual_eur": {m: round(v, 2) for m, v in actual.items()},
                    "planned_total": round(planned_total, 2),
                    "actual_total": round(actual_total, 2),
                    **variance(planned_total, actual_total, favourable_when_under),
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

        planned_total = sum(item["planned_total"] for item in section_lines)
        actual_total = sum(item["actual_total"] for item in section_lines) + sum(
            item["actual_total"] for item in unbudgeted
        )
        section_totals[line_type] = {
            "planned": planned_total,
            "actual": actual_total,
        }
        sections.append(
            {
                "key": line_type,
                "label": SECTION_LABELS[line_type],
                "favourable_when_under": favourable_when_under,
                "lines": section_lines,
                "unbudgeted": unbudgeted,
                "unbudgeted_suppressed": bool(line_type == "investment" and bank_basis),
                "planned_total": round(planned_total, 2),
                "actual_total": round(actual_total, 2),
                **variance(planned_total, actual_total, favourable_when_under),
            }
        )

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
        "sections": sections,
        "net": {
            "planned_total": round(net_planned, 2),
            "actual_total": round(net_actual, 2),
            # A bigger net than planned is the good outcome, like income.
            **variance(net_planned, net_actual, False),
        },
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

    rows = db.list_spending_transactions(
        start_date=f"{period[0]}-01", end_date=_month_end(period[-1]), is_transfer=False
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
