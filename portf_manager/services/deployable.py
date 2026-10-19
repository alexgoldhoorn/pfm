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


def _stored(db) -> dict:
    """The stored settings merged over the defaults; unknown keys dropped."""
    raw = db.get_setting(SETTINGS_KEY)
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        stored = {}
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
    if not settings["managed_portfolio_ids"]:
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
    db.set_setting(SETTINGS_KEY, json.dumps(merged))
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
    irpf = net * settings["irpf_pct"] / 100 - net * settings["irpf_retention_pct"] / 100
    accrued = max(0.0, iva + irpf)
    _, end = quarter_bounds(year, q)
    lo = end.isoformat()
    hi = (end + timedelta(days=PAYMENT_WINDOW_DAYS)).isoformat()
    paid = sum(abs(r["amount_eur"]) for r in tax_rows if lo < r["date"] <= hi)
    due = due_date(year, q)
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
