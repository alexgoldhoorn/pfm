"""Spending insights: month-vs-normal comparison, unusual charges, tier split.

Everything here is deterministic and works on EUR-converted rows (see
``load_rows``), so it can be tested without a database or an FX source.

Shared rules:

- Only complete calendar months that have imported rows form a baseline; the
  month in progress never counts in an average.
- Transfers are never spending, and money moved to ``Invest`` /
  ``House purchase`` is reported apart (``savings_eur``), not as spending.
- A row marked ``one_off`` still counts in every actual total. It is left out
  of baselines, "usual amount" statistics and anomaly detection, because the
  user already said it is not normal.
- "Data through" is kept per bank account (the date of its latest imported
  row, not today). Whether a month is complete, and how much of the month in
  progress has elapsed, is judged against the **earliest** of those dates over
  the accounts that have rows in the window: one account that stopped
  importing early makes the month partial however fresh the others are.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any, Callable, Dict, List, Optional

from portf_manager.services.life_scenarios import (
    SAVINGS_CATEGORIES,
    TIERS,
    UNCLASSIFIED,
    complete_months,
    tier_of,
)
from portf_manager.services.retirement import expand_categories

DEFAULT_BASELINE_MONTHS = 6
GROUP_BY_VALUES = ("category", "merchant")

# A charge is "far above the usual" when it is at least this many times the
# merchant's median charge and 1.5 times its largest earlier charge.
LARGE_CHARGE_FACTOR = 3.0
LARGE_CHARGE_OVER_MAX = 1.5
# Earlier charges needed before a merchant has a "usual" amount.
LARGE_CHARGE_MIN_HISTORY = 3
# Charges below this are never worth a flag.
ANOMALY_MIN_EUR = 50.0
# A first charge from an unknown merchant is flagged from this size up ...
NEW_MERCHANT_MIN_EUR = 200.0
# ... but only when the data reaches back this many days before the charge,
# otherwise every merchant looks new.
NEW_MERCHANT_MIN_HISTORY_DAYS = 90
# A complete month's category total is "well above normal" at this share over
# its baseline average and at least this many euros over ...
CATEGORY_SPIKE_PCT = 50.0
CATEGORY_SPIKE_MIN_EUR = 75.0
# ... with this many baseline months behind the average.
CATEGORY_SPIKE_MIN_BASELINE = 3
# Same account, merchant and amount within this many days is a possible
# double charge, from this size up.
DUPLICATE_WINDOW_DAYS = 3
DUPLICATE_MIN_EUR = 20.0
DEFAULT_ANOMALY_DAYS = 60
# History fetched before the window, for "usual" amounts and baselines.
_ANOMALY_HISTORY_DAYS = 400


# ── rows ────────────────────────────────────────────────────────────────────


def load_rows(
    db: Any,
    fx: Callable[[str], float],
    start_date: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Spending rows with ``amount_eur`` (today's rate), transfers included.

    Transfers stay in so "has this month any imports" can see them; every
    function here filters on ``is_transfer`` itself.
    """
    rates: Dict[str, float] = {}

    def rate(currency: str) -> float:
        if currency not in rates:
            rates[currency] = fx(currency)
        return rates[currency]

    rows = []
    for r in db.list_spending_transactions(
        portfolio_id=portfolio_id, start_date=start_date
    ):
        currency = (r.get("currency") or "EUR").upper()
        rows.append(
            {
                "id": r["id"],
                "portfolio_id": r["portfolio_id"],
                "account": r.get("portfolio_name"),
                "date": str(r["date"])[:10],
                "description": r.get("description") or "",
                "merchant": r.get("merchant"),
                "category": r.get("category") or "uncategorized",
                "amount_eur": float(r["amount"]) * rate(currency),
                "one_off": bool(r.get("one_off")),
                "is_transfer": bool(r.get("is_transfer")),
            }
        )
    return rows


def savings_names(tree: Dict[str, dict]) -> frozenset:
    """Categories (with sub-categories) holding money moved, not spent."""
    return expand_categories(list(tree.values()), list(SAVINGS_CATEGORIES))


def _merchant_key(row: dict) -> str:
    return (row.get("merchant") or row.get("description") or "").strip().upper()


def _merchant_name(row: dict) -> str:
    return (row.get("merchant") or row.get("description") or "").strip()


def _month_end(month: str) -> date:
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        return date(year, 12, 31)
    return date(year, mon + 1, 1) - timedelta(days=1)


def shift_month(month: str, delta: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:7]) - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def previous_month(today: date) -> str:
    """The calendar month before ``today``'s, as ``YYYY-MM``."""
    return shift_month(today.strftime("%Y-%m"), -1)


def data_through(rows: List[dict]) -> Optional[str]:
    """Date of the latest imported row (transfers count), or None."""
    return max((r["date"] for r in rows), default=None)


def effective_data_through(
    last_by_account: Dict[int, str], start: Optional[str] = None
) -> Dict[str, Any]:
    """The date every account's data reaches: the earliest last-import date.

    An account whose last import is before ``start`` has no row in the window
    (it was closed or never used in it) and is ignored rather than treated as
    stale forever. This is the single rule behind "complete month",
    "elapsed share of the month" and the baseline months.

    Args:
        last_by_account: ``{portfolio_id: ISO date of the latest imported row}``.
        start: first day of the window (ISO), or None for every account.

    Returns:
        ``{effective, latest, lagging, accounts}``: the earliest date over the
        accounts in the window (None when there is none), the latest, the id
        of the account that sets the earliest date when others are ahead of it
        (else None), and the ids that were counted.
    """
    counted = {
        pid: d
        for pid, d in last_by_account.items()
        if d and (start is None or d >= start)
    }
    if not counted:
        return {"effective": None, "latest": None, "lagging": None, "accounts": []}
    effective = min(counted.values())
    latest = max(counted.values())
    lagging = None
    if effective < latest:
        lagging = min(
            (pid for pid, d in counted.items() if d == effective), default=None
        )
    return {
        "effective": effective,
        "latest": latest,
        "lagging": lagging,
        "accounts": sorted(counted),
    }


def account_data_through(
    rows: List[dict],
    start: Optional[str] = None,
    portfolio_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Per-account data-through dates from ``load_rows`` output.

    Args:
        rows: ``load_rows`` output (transfers count as imported).
        start: first day of the window (ISO); see ``effective_data_through``.
        portfolio_id: when given, only that account counts.

    Returns:
        ``effective_data_through``'s dict plus ``by_account`` (``{id: {name,
        data_through}}`` for every account seen) and ``lagging_name``.
    """
    last: Dict[int, str] = {}
    names: Dict[int, str] = {}
    for r in rows:
        pid = r["portfolio_id"]
        if portfolio_id is not None and pid != portfolio_id:
            continue
        if r["date"] > last.get(pid, ""):
            last[pid] = r["date"]
        if r.get("account"):
            names[pid] = r["account"]
    info = effective_data_through(last, start)
    info["by_account"] = {
        pid: {"name": names.get(pid) or f"account {pid}", "data_through": d}
        for pid, d in last.items()
    }
    lagging = info["lagging"]
    info["lagging_name"] = (
        info["by_account"][lagging]["name"] if lagging is not None else None
    )
    return info


def _spend_rows(rows: List[dict], tree: Dict[str, dict]):
    """Non-transfer outflows outside the savings categories."""
    saved = savings_names(tree)
    return [
        r
        for r in rows
        if not r["is_transfer"] and r["amount_eur"] < 0 and r["category"] not in saved
    ]


def _delta(actual: Optional[float], baseline: float):
    if actual is None:
        return None, None
    delta = round(actual - baseline, 2)
    pct = round(delta / baseline * 100, 1) if baseline else None
    return delta, pct


# ── comparison ──────────────────────────────────────────────────────────────


def compare_month(
    rows: List[dict],
    tree: Dict[str, dict],
    month: str,
    today: date,
    baseline_months: int = DEFAULT_BASELINE_MONTHS,
    group_by: str = "category",
) -> Dict[str, Any]:
    """One month against its normal and against the same month a year before.

    Args:
        rows: ``load_rows`` output (transfers included).
        tree: ``{name: row}`` category tree with ``essentiality``.
        month: ``YYYY-MM``; not later than the month in progress.
        today: reference date.
        baseline_months: how many calendar months before ``month`` to look
            back; those without imports are skipped.
        group_by: ``category`` or ``merchant``.

    Returns:
        ``{month, status, data_through, accounts, baseline_months, group_by,
        totals, items, warnings}``. ``data_through`` is the earliest
        last-import date over the accounts that have rows in the month or its
        baseline months (``accounts`` lists each). ``status`` is ``complete``,
        ``in_progress``, ``partial`` (any account's data ends before the month
        does; the warning names it) or ``no_imports``. Only
        a complete month gets deltas: comparing part of a month with a full
        average, or an empty month with one, would flatter the result.
        Items are ordered by the size of their move.

    Raises:
        ValueError: bad ``group_by`` or a month after the one in progress.
    """
    if group_by not in GROUP_BY_VALUES:
        raise ValueError(f"group_by must be one of {list(GROUP_BY_VALUES)}")
    current = today.strftime("%Y-%m")
    if month > current:
        raise ValueError("month cannot be after the month in progress")
    if baseline_months < 1:
        raise ValueError("baseline_months must be at least 1")

    warnings: List[str] = []
    # Each account's own last import, over the accounts that have rows in the
    # month or its baseline months; the earliest of them decides completeness.
    window_start = f"{shift_month(month, -baseline_months)}-01"
    info = account_data_through(rows, start=window_start)
    through = info["effective"]
    in_month = [r for r in rows if r["date"][:7] == month]
    if not in_month:
        status = "no_imports"
        warnings.append(f"Nothing is imported for {month}: no figures are shown.")
    elif month == current:
        status = "in_progress"
        warnings.append(
            f"{month} is still in progress (data through {through}): the actual "
            "is month-to-date, so no comparison is made."
        )
    elif through and through < _month_end(month).isoformat():
        status = "partial"
        who = info["lagging_name"]
        warnings.append(
            f"{who + ': imported' if who else 'Imported data'} "
            f"{'only ' if who else ''}through {through}, before the end of "
            f"{month}: no comparison is made."
        )
    else:
        status = "complete"

    # Complete months before `month` that have rows, newest `baseline_months`;
    # a month the data of any account stops short of is not complete.
    used = [
        m
        for m in complete_months(
            rows, date.fromisoformat(f"{month}-01"), baseline_months
        )
        if through and _month_end(m).isoformat() <= through
    ]
    if status == "complete" and not used:
        warnings.append("No earlier month with imports: there is no baseline.")
    elif status == "complete" and len(used) < baseline_months:
        warnings.append(
            f"Only {len(used)} of the {baseline_months} months before {month} "
            "have imports; the baseline uses those."
        )
    last_year_month = shift_month(month, -12)
    has_last_year = any(r["date"][:7] == last_year_month for r in rows)

    spend = _spend_rows(rows, tree)
    saved = savings_names(tree)

    # Merchants group without regard to case; the spelling shown is the most
    # common one among the rows of the group.
    spellings: Dict[str, Counter] = defaultdict(Counter)

    def key_of(row: dict) -> str:
        if group_by == "category":
            return row["category"]
        spellings[_merchant_key(row)][_merchant_name(row)] += 1
        return _merchant_key(row)

    def display(key: str) -> str:
        if group_by == "category" or key not in spellings:
            return key
        ranked = sorted(spellings[key].items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0]

    actual: Dict[str, float] = defaultdict(float)
    one_off: Dict[str, float] = defaultdict(float)
    base: Dict[str, float] = defaultdict(float)
    last: Dict[str, float] = defaultdict(float)
    savings = 0.0
    for r in rows:
        m = r["date"][:7]
        if (
            m == month
            and not r["is_transfer"]
            and r["amount_eur"] < 0
            and r["category"] in saved
        ):
            savings -= r["amount_eur"]
    for r in spend:
        m = r["date"][:7]
        amount = -r["amount_eur"]
        if m == month:
            actual[key_of(r)] += amount
            if r["one_off"]:
                one_off[key_of(r)] += amount
        elif m == last_year_month:
            last[key_of(r)] += amount
        if m in used and not r["one_off"]:
            base[key_of(r)] += amount

    divisor = len(used) or 1
    have_actual = status != "no_imports"
    names = set(actual) | set(base) | set(last)
    items = []
    for name in names:
        a = round(actual.get(name, 0.0), 2) if have_actual else None
        b = round(base.get(name, 0.0) / divisor, 2)
        d, pct = _delta(a, b) if status == "complete" and used else (None, None)
        item = {
            "name": display(name),
            "actual_eur": a,
            "baseline_eur": b,
            "last_year_eur": (round(last.get(name, 0.0), 2) if has_last_year else None),
            "delta_eur": d,
            "delta_pct": pct,
            "one_off_eur": round(one_off.get(name, 0.0), 2),
        }
        if group_by == "category":
            item["tier"] = tier_of(name, tree)
        items.append(item)
    items.sort(
        key=lambda i: (
            -abs(
                i["delta_eur"] if i["delta_eur"] is not None else i["actual_eur"] or 0
            ),
            i["name"],
        )
    )

    total_actual = round(sum(actual.values()), 2) if have_actual else None
    total_base = round(sum(base.values()) / divisor, 2)
    t_delta, t_pct = (
        _delta(total_actual, total_base)
        if status == "complete" and used
        else (None, None)
    )
    return {
        "month": month,
        "status": status,
        "in_progress": status == "in_progress",
        "data_through": through,
        "accounts": [
            {"name": v["name"], "data_through": v["data_through"]}
            for pid, v in info["by_account"].items()
            if pid in info["accounts"]
        ],
        "baseline_months": used,
        "group_by": group_by,
        "totals": {
            "actual_eur": total_actual,
            "baseline_eur": total_base,
            "last_year_eur": (round(sum(last.values()), 2) if has_last_year else None),
            "delta_eur": t_delta,
            "delta_pct": t_pct,
            "one_off_eur": round(sum(one_off.values()), 2),
            "savings_eur": round(savings, 2) if have_actual else None,
        },
        "items": items if have_actual else [],
        "warnings": warnings,
    }


# ── tiers ───────────────────────────────────────────────────────────────────


def tier_breakdown(
    rows: List[dict],
    tree: Dict[str, dict],
    today: date,
    months: int = 12,
    top: int = 3,
) -> List[Dict[str, Any]]:
    """Per month, spending split by fixed / flexible / optional / unclassified.

    The last ``months`` calendar months, the one in progress last. Every
    outflow counts (one-offs included), so ``total_eur + savings_eur`` equals
    the ``spent_eur`` of ``/spending/trend`` for the same month. Months
    without imports are all zero with ``has_imports`` false.
    """
    current = today.strftime("%Y-%m")
    window = [shift_month(current, -i) for i in range(months - 1, -1, -1)]
    present = {r["date"][:7] for r in rows}
    saved = savings_names(tree)
    per_month: Dict[str, Dict[str, Dict[str, float]]] = {
        m: {t: defaultdict(float) for t in (*TIERS, UNCLASSIFIED)} for m in window
    }
    savings: Dict[str, float] = defaultdict(float)
    one_off: Dict[str, float] = defaultdict(float)
    for r in rows:
        m = r["date"][:7]
        if m not in per_month or r["is_transfer"] or r["amount_eur"] >= 0:
            continue
        amount = -r["amount_eur"]
        if r["category"] in saved:
            savings[m] += amount
            continue
        per_month[m][tier_of(r["category"], tree)][r["category"]] += amount
        if r["one_off"]:
            one_off[m] += amount
    out = []
    for m in window:
        entry: Dict[str, Any] = {
            "month": m,
            "has_imports": m in present,
            "in_progress": m == current,
        }
        total = 0.0
        tops = {}
        for tier in (*TIERS, UNCLASSIFIED):
            cats = per_month[m][tier]
            amount = sum(cats.values())
            total += amount
            entry[f"{tier}_eur"] = round(amount, 2)
            tops[tier] = [
                {"name": name, "amount_eur": round(v, 2)}
                for name, v in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[
                    :top
                ]
            ]
        entry["total_eur"] = round(total, 2)
        entry["savings_eur"] = round(savings.get(m, 0.0), 2)
        entry["one_off_eur"] = round(one_off.get(m, 0.0), 2)
        entry["top_categories"] = tops
        out.append(entry)
    return out


# ── anomalies ───────────────────────────────────────────────────────────────


def _item(kind: str, ident: str, row_date: str, **fields) -> Dict[str, Any]:
    return {"id": ident, "kind": kind, "date": row_date, **fields}


def find_anomalies(
    rows: List[dict],
    tree: Dict[str, dict],
    today: date,
    days: int = DEFAULT_ANOMALY_DAYS,
) -> Dict[str, Any]:
    """Unusual charges in the last ``days`` days.

    Four kinds, each with a deterministic id (a row id, a category and month,
    or a pair of row ids), so an acknowledgement sticks:

    - ``large_charge``: far above the merchant's usual amount.
    - ``new_merchant``: a large first charge from a merchant never seen before.
    - ``category_spike``: a complete month's category total well above its
      baseline average (one-off rows left out).
    - ``possible_duplicate``: same account, merchant and amount within a few
      days.

    One-off rows are never flagged and never form a "usual".
    """
    window_start = (today - timedelta(days=days)).isoformat()
    today_s = today.isoformat()
    spend = _spend_rows(rows, tree)
    usual_rows = sorted(
        (r for r in spend if not r["one_off"]), key=lambda r: (r["date"], r["id"])
    )
    by_merchant: Dict[str, List[dict]] = defaultdict(list)
    for r in usual_rows:
        by_merchant[_merchant_key(r)].append(r)
    # "Is the data long enough for a merchant to count as new" is a question
    # about the charge's own account: a freshly imported second account must
    # not make every merchant it holds look new.
    earliest_by_account: Dict[Any, str] = {}
    for r in rows:
        pid = r["portfolio_id"]
        if r["date"] < earliest_by_account.get(pid, "9999-12-31"):
            earliest_by_account[pid] = r["date"]

    items: List[dict] = []
    for r in usual_rows:
        if not (window_start <= r["date"] <= today_s):
            continue
        amount = -r["amount_eur"]
        key = _merchant_key(r)
        if not key:
            continue
        prior = [
            -p["amount_eur"]
            for p in by_merchant[key]
            if (p["date"], p["id"]) < (r["date"], r["id"])
        ]
        if len(prior) >= LARGE_CHARGE_MIN_HISTORY:
            usual = median(prior)
            if (
                amount >= ANOMALY_MIN_EUR
                and amount >= LARGE_CHARGE_FACTOR * usual
                and amount > LARGE_CHARGE_OVER_MAX * max(prior)
            ):
                items.append(
                    _item(
                        "large_charge",
                        f"anomaly:large:{r['id']}",
                        r["date"],
                        merchant=_merchant_name(r),
                        category=r["category"],
                        amount_eur=round(amount, 2),
                        usual_eur=round(usual, 2),
                        transaction_ids=[r["id"]],
                        detail=(
                            f"{amount:,.2f} EUR at {_merchant_name(r)}; the usual "
                            f"charge there is {usual:,.2f} EUR."
                        ),
                    )
                )
        elif (
            not prior
            and amount >= NEW_MERCHANT_MIN_EUR
            and r["portfolio_id"] in earliest_by_account
            and (
                date.fromisoformat(r["date"])
                - date.fromisoformat(earliest_by_account[r["portfolio_id"]])
            ).days
            >= NEW_MERCHANT_MIN_HISTORY_DAYS
        ):
            items.append(
                _item(
                    "new_merchant",
                    f"anomaly:new_merchant:{r['id']}",
                    r["date"],
                    merchant=_merchant_name(r),
                    category=r["category"],
                    amount_eur=round(amount, 2),
                    usual_eur=None,
                    transaction_ids=[r["id"]],
                    detail=(
                        f"{amount:,.2f} EUR at {_merchant_name(r)}, a merchant "
                        "with no earlier charge."
                    ),
                )
            )

    # Category totals per complete month (one-off rows left out).
    month_totals: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in usual_rows:
        month_totals[r["date"][:7]][r["category"]] += -r["amount_eur"]
    current = today.strftime("%Y-%m")
    for month in sorted(month_totals):
        if month >= current or _month_end(month).isoformat() < window_start:
            continue
        used = complete_months(
            rows, date.fromisoformat(f"{month}-01"), DEFAULT_BASELINE_MONTHS
        )
        if len(used) < CATEGORY_SPIKE_MIN_BASELINE:
            continue
        for category, total in month_totals[month].items():
            avg = sum(month_totals[m].get(category, 0.0) for m in used) / len(used)
            if (
                avg > 0
                and total - avg >= CATEGORY_SPIKE_MIN_EUR
                and total >= avg * (1 + CATEGORY_SPIKE_PCT / 100)
            ):
                items.append(
                    _item(
                        "category_spike",
                        f"anomaly:category:{category}:{month}",
                        month,
                        merchant=None,
                        category=category,
                        amount_eur=round(total, 2),
                        usual_eur=round(avg, 2),
                        transaction_ids=[],
                        detail=(
                            f"{category} came to {total:,.2f} EUR in {month}; "
                            f"a normal month is {avg:,.2f} EUR."
                        ),
                    )
                )

    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for r in usual_rows:
        key = _merchant_key(r)
        if key and -r["amount_eur"] >= DUPLICATE_MIN_EUR:
            groups[(r["portfolio_id"], key, round(r["amount_eur"], 2))].append(r)
    for group in groups.values():
        for a, b in zip(group, group[1:]):
            gap = (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
            if gap <= DUPLICATE_WINDOW_DAYS and window_start <= b["date"] <= today_s:
                items.append(
                    _item(
                        "possible_duplicate",
                        f"anomaly:duplicate:{a['id']}:{b['id']}",
                        b["date"],
                        merchant=_merchant_name(b),
                        category=b["category"],
                        amount_eur=round(-b["amount_eur"], 2),
                        usual_eur=None,
                        transaction_ids=[a["id"], b["id"]],
                        detail=(
                            f"{-b['amount_eur']:,.2f} EUR at {_merchant_name(b)} "
                            f"on {a['date']} and again on {b['date']}."
                        ),
                    )
                )
    items.sort(key=lambda i: (i["date"], i["id"]), reverse=True)
    return {
        "as_of": today_s,
        "days": days,
        "data_through": data_through(rows),
        "items": items,
    }


def load_anomalies(
    db: Any,
    fx: Callable[[str], float],
    today: date,
    days: int = DEFAULT_ANOMALY_DAYS,
    portfolio_id: Optional[int] = None,
) -> Dict[str, Any]:
    """``find_anomalies`` over the database, with enough history behind it."""
    start = (today - timedelta(days=days + _ANOMALY_HISTORY_DAYS)).isoformat()
    rows = load_rows(db, fx, start_date=start, portfolio_id=portfolio_id)
    tree = {r["name"]: r for r in db.list_spending_categories_tree()}
    return find_anomalies(rows, tree, today, days)
