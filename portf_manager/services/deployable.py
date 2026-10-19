"""Allocator settings and the autónomo tax reserve (spec 4.2 and 5.7)."""

import calendar
import json
from datetime import date, timedelta
from typing import Any

SETTINGS_KEY = "allocator_settings"

DEFAULT_SETTINGS: dict[str, Any] = {
    "buffer_months_target": 6,
    "buffer_months_min": 4,
    "buffer_months_max": 8,
    "iva_pct": 21.0,
    "irpf_pct": 20.0,
    "irpf_retention_pct": 0.0,
    "income_category": "Income",
    "work_income_merchants": [],
    "tax_paid_categories": ["Income Tax"],
    "managed_portfolio_ids": [],
    "min_line_eur": 100,
    "max_lines_per_bucket": 2,
    "max_single_stock_pct": 5.0,
    "traspaso_threshold_pp": 5.0,
}

# A quarter's payment is made within this many days after the quarter ends,
# and the quarter stays reserved this long past its legal due date.
PAYMENT_WINDOW_DAYS = 30
GRACE_DAYS = 30
# A payment due on a weekend or holiday shifts to the next working day.
DUE_SHIFT_DAYS = 5
# Seconds a ``deployable`` result stays cached.
DEPLOYABLE_CACHE_TTL = 60
DEPLOYABLE_CACHE_PREFIX = "allocator:deployable:"

_PCT_KEYS = (
    "iva_pct",
    "irpf_pct",
    "irpf_retention_pct",
    "max_single_stock_pct",
    "traspaso_threshold_pp",
)
_AMOUNT_KEYS = ("min_line_eur",)
_LIST_KEYS = (
    "work_income_merchants",
    "tax_paid_categories",
    "managed_portfolio_ids",
)


def _copy_defaults() -> dict:
    """A copy of the defaults whose lists are not shared."""
    return {
        k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()
    }


def _raw_stored(db) -> dict:
    """The stored settings as saved, without the defaults."""
    raw = db.get_setting(SETTINGS_KEY)
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def _stored(db) -> dict:
    """The stored settings merged over the defaults; unknown keys dropped."""
    stored = _raw_stored(db)
    merged = _copy_defaults()
    if isinstance(stored, dict):
        merged.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    return merged


def load_settings(db) -> dict:
    """Stored settings over the defaults.

    When ``managed_portfolio_ids`` is empty and a portfolio name contains
    "indexa", its id is filled in and ``defaulted_managed`` is True.

    Args:
        db: Database with ``get_setting`` and ``get_all_portfolios``.

    Returns:
        The settings dict.
    """
    settings = _stored(db)
    # filled in only while the key was never stored: a saved empty list is
    # the owner turning the managed default off
    if "managed_portfolio_ids" not in _raw_stored(db):
        for p in db.get_all_portfolios(active_only=False):
            if "indexa" in (p.get("name") or "").lower():
                settings["managed_portfolio_ids"] = [p["id"]]
                settings["defaulted_managed"] = True
                break
    return settings


def _is_number(value: Any) -> bool:
    """True for int/float, not bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate(settings: dict, db) -> None:
    """Raise ValueError when *settings* breaks a rule of spec 5.7."""
    for key in (
        "buffer_months_target",
        "buffer_months_min",
        "buffer_months_max",
        *_PCT_KEYS,
        *_AMOUNT_KEYS,
        "max_lines_per_bucket",
    ):
        if not _is_number(settings[key]):
            raise ValueError(f"{key} must be a number")
    low = settings["buffer_months_min"]
    target = settings["buffer_months_target"]
    high = settings["buffer_months_max"]
    if not 0 < low <= target <= high <= 24:
        raise ValueError(
            "buffer months must satisfy 0 < buffer_months_min <= "
            "buffer_months_target <= buffer_months_max <= 24"
        )
    for key in _PCT_KEYS:
        if not 0 <= settings[key] <= 100:
            raise ValueError(f"{key} must be between 0 and 100")
    for key in _AMOUNT_KEYS:
        if settings[key] < 0:
            raise ValueError(f"{key} must be 0 or more")
    if not isinstance(settings["max_lines_per_bucket"], int) or isinstance(
        settings["max_lines_per_bucket"], bool
    ):
        raise ValueError("max_lines_per_bucket must be a whole number")
    if settings["max_lines_per_bucket"] < 1:
        raise ValueError("max_lines_per_bucket must be at least 1")
    if not isinstance(settings["income_category"], str):
        raise ValueError("income_category must be text")
    for key in _LIST_KEYS:
        if not isinstance(settings[key], list):
            raise ValueError(f"{key} must be a list")
    for key in ("work_income_merchants", "tax_paid_categories"):
        if not all(isinstance(v, str) for v in settings[key]):
            raise ValueError(f"{key} must hold text only")
    ids = settings["managed_portfolio_ids"]
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ValueError("managed_portfolio_ids must hold whole numbers only")
    known = {p["id"] for p in db.get_all_portfolios(active_only=False)}
    unknown = [i for i in settings["managed_portfolio_ids"] if i not in known]
    if unknown:
        raise ValueError(f"unknown portfolio ids in managed_portfolio_ids: {unknown}")


def save_settings(db, patch: dict) -> dict:
    """Merge *patch* over the stored settings, validate and store them.

    Unknown keys in *patch* are ignored.

    Raises:
        ValueError: If a value breaks the validation rules.
    """
    merged = _stored(db)
    merged.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    _validate(merged, db)
    to_store = dict(merged)
    # keep the managed list absent until the owner sets it, so the Indexa
    # default still applies after an unrelated save
    if (
        "managed_portfolio_ids" not in patch
        and "managed_portfolio_ids" not in _raw_stored(db)
    ):
        to_store.pop("managed_portfolio_ids")
    db.set_setting(SETTINGS_KEY, json.dumps(to_store))
    try:
        db.cache_clear(DEPLOYABLE_CACHE_PREFIX)
    except Exception:
        pass
    return merged


# ── Quarters ─────────────────────────────────────────────────────────────────


def quarter_of(day: date) -> tuple[int, int]:
    """(year, quarter 1-4) of *day*."""
    return day.year, (day.month - 1) // 3 + 1


def quarter_bounds(year: int, q: int) -> tuple[date, date]:
    """First and last day of quarter *q* of *year*."""
    first_month = 3 * (q - 1) + 1
    last_month = first_month + 2
    return (
        date(year, first_month, 1),
        date(year, last_month, calendar.monthrange(year, last_month)[1]),
    )


def due_date(year: int, q: int) -> date:
    """Filing date of the quarter: 20th of the next month, 30 Jan for Q4."""
    if q == 4:
        return date(year + 1, 1, 30)
    return date(year, 3 * q + 1, 20)


def _previous(year: int, q: int) -> tuple[int, int]:
    """The quarter before (year, q)."""
    return (year, q - 1) if q > 1 else (year - 1, 4)


# ── Reserve ──────────────────────────────────────────────────────────────────


def work_income_rows(rows: list[dict], settings: dict) -> list[dict]:
    """Positive, non-transfer rows of the income category from work merchants.

    An empty ``work_income_merchants`` list accepts every merchant.
    """
    merchants = settings.get("work_income_merchants") or []
    return [
        r
        for r in rows
        if r["amount_eur"] > 0
        and not r.get("is_transfer")
        and r.get("category") == settings["income_category"]
        and (not merchants or r.get("merchant") in merchants)
    ]


def _cents(value: float) -> float:
    """Round to cents."""
    return round(value + 0.0, 2)


def _in_quarter(rows: list[dict], year: int, q: int) -> list[dict]:
    """Rows dated within the quarter."""
    start, end = quarter_bounds(year, q)
    return [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]


def quarter_reserve(
    rows: list[dict],
    tax_rows: list[dict],
    year: int,
    q: int,
    settings: dict,
    today: date,
) -> dict:
    """The tax set aside for one quarter (Modelo 303 + 130 estimate).

    Args:
        rows: Spending rows ``{date, amount_eur, category, merchant,
            is_transfer}``.
        tax_rows: Tax-paid rows (negative amounts).
        year: Quarter year.
        q: Quarter 1-4.
        settings: Allocator settings.
        today: Reference date for the past-due rule.

    Returns:
        ``{quarter, gross_eur, iva_eur, irpf_eur, paid_eur, accrued_eur,
        remaining_eur, due, settled}`` plus ``dropped_reason`` when the
        quarter is no longer reserved.
    """
    gross = sum(
        r["amount_eur"] for r in _in_quarter(work_income_rows(rows, settings), year, q)
    )
    iva_pct = settings["iva_pct"]
    iva = gross * iva_pct / (100 + iva_pct)
    net = gross - iva
    # a retention above the IRPF rate must not cancel the IVA
    irpf = max(
        0.0,
        net * settings["irpf_pct"] / 100 - net * settings["irpf_retention_pct"] / 100,
    )
    accrued = max(0.0, iva + irpf)
    _, end = quarter_bounds(year, q)
    due = due_date(year, q)
    lo = end.isoformat()
    hi = max(
        end + timedelta(days=PAYMENT_WINDOW_DAYS),
        due + timedelta(days=DUE_SHIFT_DAYS),
    ).isoformat()
    # only outgoing rows are payments; a refund row is ignored
    paid = sum(
        abs(r["amount_eur"])
        for r in tax_rows
        if r["amount_eur"] < 0 and lo < r["date"] <= hi
    )
    remaining = max(0.0, accrued - paid)
    out = {
        "quarter": f"{year}-Q{q}",
        "gross_eur": _cents(gross),
        "iva_eur": _cents(iva),
        "irpf_eur": _cents(irpf),
        "paid_eur": _cents(paid),
        "accrued_eur": _cents(accrued),
        "remaining_eur": _cents(remaining),
        "due": due.isoformat(),
        "settled": remaining <= 0.005,
    }
    if remaining > 0.005 and today > due + timedelta(days=GRACE_DAYS):
        out["remaining_eur"] = 0.0
        out["settled"] = True
        out["dropped_reason"] = "past due"
    return out


def tax_reserve(
    rows: list[dict],
    tax_rows: list[dict],
    settings: dict,
    today: date,
) -> dict:
    """Reserve for the current and the previous quarter.

    Returns:
        ``{reserve_eur, quarters: [previous, current], merchants_counted,
        warnings}``.
    """
    year, q = quarter_of(today)
    py, pq = _previous(year, q)
    previous = quarter_reserve(rows, tax_rows, py, pq, settings, today)
    current = quarter_reserve(rows, tax_rows, year, q, settings, today)
    reserve = current["accrued_eur"] + previous["remaining_eur"]

    work = work_income_rows(rows, settings)
    in_play = _in_quarter(work, py, pq) + _in_quarter(work, year, q)
    merchants = sorted({r["merchant"] for r in in_play if r.get("merchant")})
    warnings: list[str] = []
    if not settings.get("work_income_merchants"):
        warnings.append(
            "all income counted as work income: tick the work merchants in the settings"
        )
    if not _in_quarter(work, year, q):
        warnings.append("no work income imported this quarter")
    return {
        "reserve_eur": _cents(reserve),
        "quarters": [previous, current],
        "merchants_counted": merchants,
        "warnings": warnings,
    }


# ── Buffer and deployable cash ───────────────────────────────────────────────

MIN_SPEND_MONTHS = 6
COMING_IN_DAYS = 30


def buffer(
    liquid_eur: float, reserve_eur: float, monthly_spend_eur: float, settings: dict
) -> dict:
    """Judge the cash buffer left after the tax reserve.

    Args:
        liquid_eur: Counted liquid cash.
        reserve_eur: Tax reserve.
        monthly_spend_eur: Monthly spending including Social Security.
        settings: Allocator settings.

    Returns:
        ``{target_eur, months, status, idle_eur}``; status is ``rebuild``
        (below the minimum), ``topping_up``, ``ok`` or ``idle`` (above the
        maximum); ``unknown`` without a spending figure.
    """
    free = liquid_eur - reserve_eur
    target = settings["buffer_months_target"] * monthly_spend_eur
    if monthly_spend_eur <= 0:
        return {"target_eur": 0.0, "months": None, "status": "unknown", "idle_eur": 0.0}
    months = free / monthly_spend_eur
    if months < settings["buffer_months_min"]:
        status = "rebuild"
    elif months < settings["buffer_months_target"]:
        status = "topping_up"
    elif months <= settings["buffer_months_max"]:
        status = "ok"
    else:
        status = "idle"
    idle = free - settings["buffer_months_max"] * monthly_spend_eur
    return {
        "target_eur": _cents(target),
        "months": round(months, 2),
        "status": status,
        "idle_eur": _cents(idle) if status == "idle" else 0.0,
    }


def _buffer_rule(settings: dict) -> str:
    """The buffer rule in words."""
    return (
        f"buffer: {settings['buffer_months_target']} months target, nothing "
        f"deployable under {settings['buffer_months_min']}, idle above "
        f"{settings['buffer_months_max']}"
    )


def deployable(
    db,
    today: date | None = None,
    fx=None,
    fx_on=None,
    settings: dict | None = None,
) -> dict:
    """Cash that can go into investments now (spec 4.3).

    ``deployable = max(0, liquid - tax_reserve - target_months * spend)``.

    Args:
        db: Database.
        today: Reference date; defaults to today.
        fx: ``fx(currency)`` live EUR rate.
        fx_on: ``fx_on(currency, day)`` EUR rate on a date.
        settings: Allocator settings; loaded from *db* when omitted.

    Returns:
        The response of spec 4.3.
    """
    from . import retirement
    from .balance_sheet import build_balance_sheet

    today = today or date.today()
    if fx is None or fx_on is None:
        from portf_server.routers.portfolios import _get_fx_rate, _get_fx_rate_on

        fx = fx or _get_fx_rate
        fx_on = fx_on or (lambda cur, day: _get_fx_rate_on(db, cur, day))
    settings = settings or load_settings(db)
    warnings: list[str] = []

    sheet = build_balance_sheet(db, today=today, fx=fx, fx_on=fx_on)
    cash = next((g for g in sheet["groups"] if g["key"] == "cash"), {"lines": []})
    liquid_lines = []
    stale = 0
    stale_counted = 0
    for ln in cash["lines"]:
        if ln.get("in_total"):
            liquid_lines.append(
                {
                    "id": ln["id"],
                    "label": ln["label"],
                    "amount_eur": ln["amount_eur"],
                    "stale": bool(ln.get("stale")),
                }
            )
        else:
            warnings.append(f"left out: {ln['label']}")
        if ln.get("stale"):
            stale += 1
            if ln.get("in_total"):
                stale_counted += 1
    if stale_counted:
        warnings.append(
            f"{stale_counted} stale cash line(s) counted at their stale value"
        )
    liquid = sum(ln["amount_eur"] for ln in liquid_lines)

    year, q = quarter_of(today)
    py, pq = _previous(year, q)
    start = quarter_bounds(py, pq)[0].isoformat()
    rows = [
        {
            "date": r["date"],
            "amount_eur": float(r["amount"]) * fx(r.get("currency") or "EUR"),
            "category": r.get("category"),
            "merchant": r.get("merchant"),
            "is_transfer": bool(r.get("is_transfer")),
        }
        for r in db.list_spending_transactions(start_date=start, is_transfer=False)
    ]
    paid = set(settings["tax_paid_categories"])
    tax_rows = [r for r in rows if r["category"] in paid]
    reserve = tax_reserve(rows, tax_rows, settings, today)
    warnings.extend(reserve["warnings"])

    plan = retirement.load_plan(db)
    retirement_spend = retirement.spending_inputs(db, plan, today, fx)
    full_plan = {
        **plan,
        "excluded_categories": [
            c for c in plan["excluded_categories"] if c != "Social Security"
        ],
    }
    spend = retirement.spending_inputs(db, full_plan, today, fx)
    months_used = spend["months_used"]
    monthly = spend["target_monthly_eur"]

    horizon = (today + timedelta(days=COMING_IN_DAYS)).isoformat()
    coming_in = [
        {
            "label": d["name"],
            "amount_eur": _cents(
                float(d["principal"]) * fx(d.get("currency") or "EUR")
            ),
            "date": d["maturity_date"],
        }
        for d in db.get_fixed_deposits(status="active")
        if d.get("maturity_date") and today.isoformat() <= d["maturity_date"] <= horizon
    ]

    out = {
        "status": "ok",
        "as_of": today.isoformat(),
        "liquid_eur": _cents(liquid),
        "liquid_lines": liquid_lines,
        "tax_reserve": reserve,
        "coming_in": coming_in,
        "stale_lines": stale,
        "warnings": warnings,
    }
    base_buffer = buffer(liquid, reserve["reserve_eur"], monthly, settings)
    out["buffer"] = {
        **base_buffer,
        "target_months": settings["buffer_months_target"],
        "min_months": settings["buffer_months_min"],
        "max_months": settings["buffer_months_max"],
        "monthly_spend_eur": _cents(monthly),
        "months_used": months_used,
        "months_used_count": len(months_used),
        "retirement_monthly_spend_eur": _cents(retirement_spend["target_monthly_eur"]),
    }
    if len(months_used) < MIN_SPEND_MONTHS:
        out["status"] = "insufficient_data"
        out["buffer"]["status"] = "unknown"
        out["reason"] = (
            f"only {len(months_used)} complete months of bank statements; "
            f"{MIN_SPEND_MONTHS} needed to size the buffer"
        )
        out["deployable_eur"] = None
        warnings.append(_buffer_rule(settings))
        return out
    out["deployable_eur"] = _cents(
        max(0.0, liquid - reserve["reserve_eur"] - base_buffer["target_eur"])
    )
    warnings.append(_buffer_rule(settings))
    return out


def deployable_cached(db, today: date | None = None, fx=None, fx_on=None) -> dict:
    """:func:`deployable` cached for 60 seconds, keyed on today's date.

    Only for callers using the live FX and the stored settings; a settings
    PUT clears the cache. Pass an explicit ``fx`` or ``settings`` to
    :func:`deployable` itself to bypass it.
    """
    from portf_manager.cache import cached

    day = today or date.today()
    kwargs = {"today": today}
    if fx is not None:
        kwargs["fx"] = fx
    if fx_on is not None:
        kwargs["fx_on"] = fx_on
    return cached(
        db,
        f"{DEPLOYABLE_CACHE_PREFIX}{day.isoformat()}",
        DEPLOYABLE_CACHE_TTL,
        lambda: deployable(db, **kwargs),
    )
