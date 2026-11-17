"""Year view: one year of spending and income against the year before.

Deterministic, no LLM, on ``spending_insights.load_rows`` output. Rules shared
with the other spending services:

- Transfers never count; money moved to ``Invest`` / ``House purchase`` is
  ``savings_eur``, not spending (``spending_insights._spend_rows``).
- Everything compared is **like for like**: only months that are complete and
  have imports in BOTH years are summed, on both sides. For a year in progress
  the previous year is cut to the same months, through the last complete month
  (``compared_through``); the full previous year is given next to it.
- A month with no imports is unknown, never zero: it is listed in the month
  grid with ``has_imports: false`` and named in a warning.
- "Complete" uses the earliest last import over the accounts in the window
  (``account_data_through``).
- One-off rows count in every total and are listed separately.
"""

from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from portf_manager.services import spending_insights

MOVERS_TOP = 5
MERCHANTS_TOP = 10
MIN_YEAR = 2000
DEPTHS = (1, 2)
# Name given to spending filed directly on a category rather than a child.
DIRECT = "(direct)"


def _months(year: int) -> List[str]:
    return [f"{year}-{m:02d}" for m in range(1, 13)]


def _pct(delta: float, base: float) -> Optional[float]:
    return round(100 * delta / base, 1) if base else None


def _path(category: str, tree: Dict[str, dict]) -> List[str]:
    """Names from just below ``Spend`` down to ``category`` (itself if outside)."""
    path = [category]
    seen = 0
    row = tree.get(category)
    while row and row.get("parent_name") and seen < 100:
        parent = row["parent_name"]
        if parent == "Spend":
            break
        path.append(parent)
        row = tree.get(parent)
        seen += 1
    path.reverse()
    return path


def _month_entry(month: str, rows: List[dict], tree, complete: bool) -> Dict[str, Any]:
    income = sum(
        r["amount_eur"] for r in rows if not r["is_transfer"] and r["amount_eur"] > 0
    )
    spend = -sum(r["amount_eur"] for r in spending_insights._spend_rows(rows, tree))
    saved = spending_insights.savings_names(tree)
    savings = -sum(
        r["amount_eur"]
        for r in rows
        if not r["is_transfer"] and r["amount_eur"] < 0 and r["category"] in saved
    )
    return {
        "month": month,
        "income_eur": round(income, 2),
        "spending_eur": round(spend, 2),
        "savings_eur": round(savings, 2),
        "has_imports": bool(rows),
        "complete": complete,
    }


def _totals(entries: List[Dict[str, Any]], rows: List[dict], tree) -> Dict[str, Any]:
    income = sum(e["income_eur"] for e in entries)
    spending = sum(e["spending_eur"] for e in entries)
    savings = sum(e["savings_eur"] for e in entries)
    months = {e["month"] for e in entries}
    one_off = -sum(
        r["amount_eur"]
        for r in spending_insights._spend_rows(rows, tree)
        if r["one_off"] and r["date"][:7] in months
    )
    net = income - spending
    return {
        "months": sorted(months),
        "income_eur": round(income, 2),
        "spending_eur": round(spending, 2),
        "savings_eur": round(savings, 2),
        "net_eur": round(net, 2),
        "savings_rate_pct": round(100 * net / income, 1) if income > 0 else None,
        "one_off_eur": round(one_off, 2),
    }


def build_year(
    rows: List[dict],
    tree: Dict[str, dict],
    today: date,
    year: Optional[int] = None,
    depth: int = 1,
) -> Dict[str, Any]:
    """This year against last year, like for like.

    Args:
        rows: ``load_rows`` output covering the year and the one before.
        tree: ``{name: row}`` category tree.
        today: reference date.
        year: the year to show (default: the current one).
        depth: 1 for top-level Spend categories, 2 to add their children.

    Returns:
        ``{year, previous_year, complete, compared_through, compared_months,
        totals, months, categories, movers, merchants, one_offs, warnings}``.
        ``totals`` is ``{this, previous, previous_full}``; ``this`` and
        ``previous`` sum the compared months on both sides and are ``None``
        when there are none (``previous_full`` is None without any imports in
        the previous year). Deltas in ``categories`` and ``movers`` exist only
        for compared months.

    Raises:
        ValueError: a year outside 2000..current, or a bad ``depth``.
    """
    current_year = today.year
    year = current_year if year is None else year
    if year < MIN_YEAR or year > current_year:
        raise ValueError(f"year must be between {MIN_YEAR} and {current_year}")
    if depth not in DEPTHS:
        raise ValueError(f"depth must be one of {list(DEPTHS)}")
    previous_year = year - 1
    warnings: List[str] = []

    info = spending_insights.account_data_through(rows, start=f"{previous_year}-01-01")
    through = info["effective"]
    by_month: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_month[r["date"][:7]].append(r)

    def is_complete(month: str) -> bool:
        return bool(
            by_month.get(month)
            and through
            and spending_insights._month_end(month).isoformat() <= through
        )

    grid: Dict[int, List[Dict[str, Any]]] = {}
    for y in (year, previous_year):
        grid[y] = [
            _month_entry(m, by_month.get(m, []), tree, is_complete(m))
            for m in _months(y)
        ]
    year_complete = all(e["complete"] for e in grid[year])

    this_complete = [e["month"] for e in grid[year] if e["complete"]]
    compared_through = (
        None if year_complete else (this_complete[-1] if this_complete else None)
    )
    if year_complete:
        compared_through = f"{year}-12"
    scope = [m for m in _months(year) if compared_through and m <= compared_through]
    compared = [
        m for m in scope if is_complete(m) and by_month.get(f"{previous_year}-{m[5:]}")
    ]
    skipped = [m for m in scope if m not in compared]
    if skipped:
        warnings.append(
            "Left out of the comparison (not complete, or nothing imported in "
            f"{previous_year}): " + ", ".join(skipped) + "."
        )
    unknown = [
        e["month"]
        for e in grid[year]
        if not e["has_imports"] and e["month"] < today.strftime("%Y-%m")
    ]
    if unknown:
        warnings.append("No imports for " + ", ".join(unknown) + ": unknown, not zero.")
    if not compared:
        warnings.append("No month can be compared like for like: no deltas.")
    if not year_complete and compared_through:
        warnings.append(
            f"{year} is still in progress: {previous_year} is cut to the same "
            f"months, through {compared_through}."
        )

    prev_compared = [f"{previous_year}-{m[5:]}" for m in compared]
    this_entries = [e for e in grid[year] if e["month"] in compared]
    prev_entries = [e for e in grid[previous_year] if e["month"] in prev_compared]
    this_rows = [r for m in compared for r in by_month.get(m, [])]
    prev_rows = [r for m in prev_compared for r in by_month.get(m, [])]
    full_prev_entries = [e for e in grid[previous_year] if e["has_imports"]]
    full_prev_rows = [r for e in full_prev_entries for r in by_month[e["month"]]]
    totals = {
        "this": _totals(this_entries, this_rows, tree) if compared else None,
        "previous": _totals(prev_entries, prev_rows, tree) if compared else None,
        "previous_full": (
            _totals(full_prev_entries, full_prev_rows, tree)
            if full_prev_entries
            else None
        ),
    }

    categories: List[Dict[str, Any]] = []
    movers: Dict[str, List[Dict[str, Any]]] = {"increases": [], "decreases": []}
    merchants: List[Dict[str, Any]] = []
    one_offs: List[Dict[str, Any]] = []
    if compared:
        this_spend = spending_insights._spend_rows(this_rows, tree)
        prev_spend = spending_insights._spend_rows(prev_rows, tree)
        total_this = sum(-r["amount_eur"] for r in this_spend)

        def sums(spend_rows: List[dict], key) -> Dict[str, float]:
            out: Dict[str, float] = defaultdict(float)
            for r in spend_rows:
                out[key(r)] += -r["amount_eur"]
            return out

        def top_name(r):
            return _path(r["category"], tree)[0]

        def entry(name, this_v, prev_v, **extra):
            delta = this_v - prev_v
            return {
                "category": name,
                "this_eur": round(this_v, 2),
                "previous_eur": round(prev_v, 2),
                "delta_eur": round(delta, 2),
                "delta_pct": _pct(delta, prev_v),
                "share_pct": (
                    round(100 * this_v / total_this, 1) if total_this else None
                ),
                **extra,
            }

        this_top, prev_top = sums(this_spend, top_name), sums(prev_spend, top_name)
        for name in sorted(
            set(this_top) | set(prev_top),
            key=lambda n: (-this_top.get(n, 0.0), -prev_top.get(n, 0.0), n),
        ):
            item = entry(name, this_top.get(name, 0.0), prev_top.get(name, 0.0))
            if depth == 2:

                def child_name(r, parent=name):
                    path = _path(r["category"], tree)
                    return path[1] if len(path) > 1 else DIRECT

                in_parent = lambda r, parent=name: top_name(r) == parent  # noqa: E731
                this_c = sums([r for r in this_spend if in_parent(r)], child_name)
                prev_c = sums([r for r in prev_spend if in_parent(r)], child_name)
                item["children"] = [
                    entry(c, this_c.get(c, 0.0), prev_c.get(c, 0.0))
                    for c in sorted(
                        set(this_c) | set(prev_c),
                        key=lambda n: (-this_c.get(n, 0.0), -prev_c.get(n, 0.0), n),
                    )
                ]
            categories.append(item)

        this_leaf = sums(this_spend, lambda r: r["category"])
        prev_leaf = sums(prev_spend, lambda r: r["category"])
        leaf = [
            entry(n, this_leaf.get(n, 0.0), prev_leaf.get(n, 0.0))
            for n in set(this_leaf) | set(prev_leaf)
        ]
        for e in leaf:
            e.pop("share_pct")
        movers["increases"] = sorted(
            (e for e in leaf if e["delta_eur"] > 0),
            key=lambda e: (-e["delta_eur"], e["category"]),
        )[:MOVERS_TOP]
        movers["decreases"] = sorted(
            (e for e in leaf if e["delta_eur"] < 0),
            key=lambda e: (e["delta_eur"], e["category"]),
        )[:MOVERS_TOP]

        names: Dict[str, str] = {}
        for r in this_spend + prev_spend:
            names.setdefault(
                spending_insights._merchant_key(r), spending_insights._merchant_name(r)
            )
        this_m = sums(this_spend, spending_insights._merchant_key)
        prev_m = sums(prev_spend, spending_insights._merchant_key)
        for key, amount in sorted(this_m.items(), key=lambda kv: (-kv[1], kv[0]))[
            :MERCHANTS_TOP
        ]:
            merchants.append(
                {
                    "merchant": names.get(key) or key,
                    "this_eur": round(amount, 2),
                    "previous_eur": round(prev_m.get(key, 0.0), 2),
                }
            )
        one_offs = [
            {
                "id": r["id"],
                "date": r["date"],
                "description": r["description"],
                "amount_eur": round(r["amount_eur"], 2),
                "category": r["category"],
                "account": r.get("account"),
            }
            for r in sorted(this_rows, key=lambda r: (r["date"], r["id"]))
            if r["one_off"] and not r["is_transfer"]
        ]

    return {
        "year": year,
        "previous_year": previous_year,
        "complete": year_complete,
        "compared_through": compared_through,
        "compared_months": compared,
        "data_through": through,
        "totals": totals,
        "months": {"this": grid[year], "previous": grid[previous_year]},
        "categories": categories,
        "movers": movers,
        "merchants": merchants,
        "one_offs": one_offs,
        "warnings": warnings,
    }
