"""Retirement plan settings (projection lives here too from Task 6)."""

import json
from typing import Any

PLAN_KEY = "retirement_plan"

DEFAULT_PLAN: dict[str, Any] = {
    "retirement_age": 65,
    "statutory_age": 67,
    "horizon_age": 95,
    "real_return_pct": 3.0,
    "pessimistic_real_return_pct": 1.0,
    "cash_real_return_pct": -2.0,
    "withdrawal_tax_pct": 0.0,
    "spending_months": 12,
    "spending_includes_loan_payments": True,
    "excluded_categories": [
        "Income Tax",
        "Social Security",
        "Invest",
        "House purchase",
    ],
    "birth_date": None,
    "partner_birth_date": None,
}


def _merge(stored: dict) -> dict:
    """Defaults overlaid with the known keys of *stored*."""
    plan = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_PLAN.items()}
    plan.update({k: v for k, v in stored.items() if k in DEFAULT_PLAN})
    return plan


def load_plan(db) -> dict:
    """The stored plan merged over the defaults; unknown keys are dropped."""
    raw = db.get_setting(PLAN_KEY)
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        stored = {}
    return _merge(stored if isinstance(stored, dict) else {})


def _validate_ages(plan: dict) -> None:
    """Raise ValueError unless 0 < retirement <= statutory < horizon <= 120."""
    retire, statutory = plan["retirement_age"], plan["statutory_age"]
    horizon = plan["horizon_age"]
    if not 0 < retire <= statutory < horizon <= 120:
        raise ValueError(
            "ages must satisfy 0 < retirement_age <= statutory_age "
            "< horizon_age <= 120"
        )


def save_plan(db, plan: dict) -> dict:
    """Validate and store *plan*; return it merged over the defaults.

    Raises:
        ValueError: If the ages are inconsistent.
    """
    merged = _merge(plan)
    _validate_ages(merged)
    db.set_setting(PLAN_KEY, json.dumps(merged))
    return merged
