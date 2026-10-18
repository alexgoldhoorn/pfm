"""Retirement plan settings (projection lives here too from Task 6)."""

import json
from datetime import date
from typing import Any, Callable, Optional

from portf_manager.services.loans import balance_on as loan_balance_on
from portf_manager.services.loans import payment_of as loan_payment_of
from portf_manager.services.loans import schedule as loan_schedule
from portf_manager.services.progress import savings_and_buffer

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
    if not 0 <= merged["withdrawal_tax_pct"] < 100:
        raise ValueError("withdrawal_tax_pct must satisfy 0 <= x < 100")
    db.set_setting(PLAN_KEY, json.dumps(merged))
    return merged


# ── Readiness ─────────────────────────────────────────────────────────────────

# Spec 6.2: the inflation the simulator assumes for non-indexed incomes and
# for deposit rates (nominal rate minus this is the real rate).
ASSUMED_INFLATION_PCT = 2.0
# Cash is drawn down to this many months of spending before investments.
CASH_RESERVE_MONTHS = 6
# Categories that are not spending when working out what is saved today.
SAVINGS_EXCLUDED = ("Invest", "House purchase")
MIN_MONTHS = 6
_BISECT_ITERATIONS = 60
_BISECT_TOLERANCE_EUR = 1.0
_EXTRA_SAVING_CAP = 100_000.0


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _age_on(birth: date, today: date) -> int:
    years = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        years -= 1
    return years


def expand_categories(tree: list[dict], names: list[str]) -> frozenset[str]:
    """The names plus every descendant in the category tree.

    Args:
        tree: ``db.list_spending_categories_tree()`` rows (``name``,
            ``parent_name``).
        names: category names to expand; unknown names are kept as given.
    """
    children: dict[str, list[str]] = {}
    for row in tree:
        parent = row.get("parent_name")
        if parent:
            children.setdefault(parent, []).append(row["name"])
    out: set[str] = set()
    stack = list(names)
    while stack:
        name = stack.pop()
        if name in out:
            continue
        out.add(name)
        stack.extend(children.get(name, []))
    return frozenset(out)


def spending_inputs(db, plan: dict, today: date, fx: Callable[[str], float]) -> dict:
    """Spending target and savings from the imported bank statements.

    Returns:
        ``target_monthly_eur``, ``months_used`` (complete imported months),
        ``breakdown`` (category -> monthly EUR in the target), ``excluded``,
        ``savings_now_yearly_eur`` and ``income_monthly_eur``.
    """
    months = int(plan["spending_months"])
    index = today.year * 12 + today.month - 1 - months
    start = date(index // 12, index % 12 + 1, 1).isoformat()
    rows = [
        {
            "date": r["date"],
            "amount_eur": float(r["amount"]) * fx(r.get("currency") or "EUR"),
            "category": r.get("category"),
        }
        for r in db.list_spending_transactions(start_date=start, is_transfer=False)
    ]
    tree = db.list_spending_categories_tree()
    excluded = expand_categories(tree, list(plan["excluded_categories"]))
    target = savings_and_buffer(rows, None, today, months, exclude=excluded)
    used = target["months_used"]
    n = len(used)
    breakdown: dict[str, float] = {}
    for r in rows:
        if (
            r["date"][:7] in used
            and r["amount_eur"] < 0
            and r["category"] not in excluded
        ):
            cat = r["category"] or "uncategorized"
            breakdown[cat] = breakdown.get(cat, 0.0) - r["amount_eur"] / n
    saved = savings_and_buffer(
        rows,
        None,
        today,
        months,
        exclude=expand_categories(tree, list(SAVINGS_EXCLUDED)),
    )
    n_saved = len(saved["months_used"])
    yearly = (
        max(saved["income_eur"] - saved["spent_eur"], 0.0) / n_saved * 12
        if n_saved
        else 0.0
    )
    return {
        "target_monthly_eur": target["avg_monthly_spend_eur"] or 0.0,
        "months_used": used,
        "breakdown": {k: round(v, 2) for k, v in sorted(breakdown.items())},
        "excluded": sorted(excluded),
        "savings_now_yearly_eur": round(yearly, 2),
        "income_monthly_eur": (
            round(saved["income_eur"] / n_saved, 2) if n_saved else 0.0
        ),
    }


def _loan_tables(loans: list[dict]) -> list[dict]:
    """Per loan: monthly payment and per-year payments and closing balance."""
    out = []
    for loan in loans:
        rows = {r["year"]: r for r in loan_schedule(loan)}
        out.append({"loan": loan, "monthly": loan_payment_of(loan), "years": rows})
    return out


def _loan_year(table: dict, year: int) -> tuple[float, float]:
    """(payments in *year*, balance at the end of *year*) for one loan."""
    row = table["years"].get(year)
    if row:
        return row["payments_eur"], row["balance_end_eur"]
    if table["years"] and year > max(table["years"]):
        return 0.0, 0.0
    return 0.0, loan_balance_on(table["loan"], date(year, 12, 31))


def _pension_in_year(incomes: list[dict], ages: dict[str, Optional[int]]) -> float:
    """Annual pension income (today's euros) for the given owner ages."""
    total = 0.0
    for inc in incomes:
        age = ages.get(inc["owner"])
        if age is None or age < inc["from_age"]:
            continue
        if inc.get("to_age") is not None and age >= inc["to_age"]:
            continue
        factor = 1.0
        if not inc.get("indexed", 1):
            factor = (1 - ASSUMED_INFLATION_PCT / 100) ** (age - inc["from_age"])
        total += inc["monthly_eur"] * 12 * factor
    return total


def project(
    plan: dict,
    retirement_age: int,
    real_return_pct: float,
    pot: dict,
    loans: list[dict],
    incomes: list[dict],
    spending: dict,
    today: date,
    *,
    case: str = "base",
    monthly_override: Optional[float] = None,
    extra_saving_monthly: float = 0.0,
) -> dict:
    """Project one scenario year by year, in real euros.

    Args:
        plan: plan settings (needs ``birth_date``).
        retirement_age: the owner's age when saving stops and drawing starts.
        real_return_pct: real yearly return on investments.
        pot: ``{"investments_eur", "cash_eur", "deposits": [{principal,
            rate_pct, maturity_date}]}``.
        loans: loan dicts (see ``services.loans``).
        incomes: retirement income rows.
        spending: ``{"target_monthly_eur", "savings_now_yearly_eur"}``.
        today: reference date.
        case: label copied to the result.
        monthly_override: replaces the spending target (used by bisection).
        extra_saving_monthly: extra monthly saving until retirement.

    Returns:
        ``retirement_age, case, verdict, lasts_until_age, pot_at_retirement_eur,
        pot_at_horizon_eur, depleted`` and ``path`` (one entry per year).
    """
    birth = _as_date(plan["birth_date"])
    partner = plan.get("partner_birth_date")
    partner_birth = _as_date(partner) if partner else None
    horizon = int(plan["horizon_age"])
    target = (
        spending["target_monthly_eur"] if monthly_override is None else monthly_override
    )
    reserve = CASH_RESERVE_MONTHS * target
    includes = bool(plan["spending_includes_loan_payments"])
    tax = float(plan["withdrawal_tax_pct"]) / 100
    saving = spending["savings_now_yearly_eur"] + extra_saving_monthly * 12
    # accept prebuilt tables so callers can build the schedules once
    tables = loans if loans and "years" in loans[0] else _loan_tables(loans)

    invest = float(pot["investments_eur"])
    cash = float(pot["cash_eur"])
    deposits = [
        {
            "value": float(d["principal"]),
            "rate": (float(d["rate_pct"]) - ASSUMED_INFLATION_PCT) / 100,
            "year": _as_date(d["maturity_date"]).year if d.get("maturity_date") else 0,
        }
        for d in pot.get("deposits", [])
    ]
    r_inv = real_return_pct / 100
    r_cash = float(plan["cash_real_return_pct"]) / 100

    path: list[dict] = []
    depleted_age: Optional[int] = None
    at_retirement: Optional[float] = None
    year = today.year
    first = True
    # Ages are calendar ages (year - birth year): retirement, pension starts
    # and the end of saving land in the calendar year of the birthday.
    while year - birth.year < horizon:
        age = year - birth.year
        frac = (13 - today.month) / 12 if first else 1.0
        first = False
        total_before = invest + cash + sum(d["value"] for d in deposits)
        if age >= retirement_age and at_retirement is None:
            at_retirement = total_before
        # Deposits mature into cash at the start of their maturity year.
        for d in list(deposits):
            if year >= d["year"]:
                cash += d["value"]
                deposits.remove(d)
        invest *= (1 + r_inv) ** frac
        cash *= (1 + r_cash) ** frac
        for d in deposits:
            d["value"] *= (1 + d["rate"]) ** frac

        pensions = withdrawal = 0.0
        if age < retirement_age:
            invest += saving * frac
        else:
            ages = {
                "self": age,
                "partner": year - partner_birth.year if partner_birth else None,
            }
            pensions = _pension_in_year(incomes, ages) * frac
            need = target * 12 * frac - pensions
            for t in tables:
                paid, _ = _loan_year(t, year)
                if includes:
                    # a loan that has not started yet has not ended either
                    first_pay = min(
                        (y for y, r in t["years"].items() if r["payments_eur"] > 0),
                        default=None,
                    )
                    if first_pay is None or year < first_pay:
                        continue
                    need -= (t["monthly"] * 12 - paid) * frac
                else:
                    need += paid * frac
            need = max(need, 0.0)
            withdrawal = need / (1 - tax)
            dep_total = sum(d["value"] for d in deposits)
            if invest + cash + dep_total + 1e-6 < withdrawal:
                withdrawal = invest + cash + dep_total
                if depleted_age is None:
                    depleted_age = age
                invest = cash = 0.0
                deposits = []
            else:
                left = withdrawal
                take = min(max(cash - reserve, 0.0), left)
                cash -= take
                left -= take
                take = min(invest, left)
                invest -= take
                left -= take
                take = min(cash, left)
                cash -= take
                left -= take
                for d in deposits:
                    take = min(d["value"], left)
                    d["value"] -= take
                    left -= take
        path.append(
            {
                "age": age,
                "year": year,
                "investments_eur": round(invest, 2),
                "cash_eur": round(cash + sum(d["value"] for d in deposits), 2),
                "loans_eur": round(sum(_loan_year(t, year)[1] for t in tables), 2),
                "pensions_eur": round(pensions, 2),
                "withdrawal_eur": round(withdrawal, 2),
            }
        )
        year += 1

    end_pot = invest + cash + sum(d["value"] for d in deposits)
    if at_retirement is None:
        at_retirement = end_pot
    if depleted_age is None:
        verdict, lasts = "on_track", horizon
    else:
        lasts = depleted_age
        verdict = "short" if lasts > retirement_age + 10 else "at_risk"
    return {
        "retirement_age": retirement_age,
        "case": case,
        "verdict": verdict,
        "lasts_until_age": lasts,
        "pot_at_retirement_eur": round(at_retirement, 2),
        "pot_at_horizon_eur": round(end_pot, 2),
        "depleted": depleted_age is not None,
        "path": path,
    }


def _bisect(lo: float, hi: float, ok: Callable[[float], bool]) -> tuple[float, float]:
    """Narrow [lo, hi] around the threshold below which ok(x) holds."""
    for _ in range(_BISECT_ITERATIONS):
        if hi - lo <= _BISECT_TOLERANCE_EUR:
            break
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo, hi


def sustainable_monthly(
    plan, retirement_age, real_return_pct, pot, loans, incomes, spending, today
) -> float:
    """The total monthly spend (pensions included) the plan funds to the horizon."""
    target = spending["target_monthly_eur"]

    def lasts(m: float) -> bool:
        return not project(
            plan,
            retirement_age,
            real_return_pct,
            pot,
            loans,
            incomes,
            spending,
            today,
            monthly_override=m,
        )["depleted"]

    hi = 10 * target if target > 0 else 1_000_000.0
    if lasts(hi):
        return hi
    if not lasts(0.0):
        return 0.0
    return _bisect(0.0, hi, lasts)[0]


def extra_saving_needed_monthly(
    plan, retirement_age, real_return_pct, pot, loans, incomes, spending, today
) -> Optional[float]:
    """Extra monthly saving until retirement that makes the pot last.

    Returns 0 when already on track, and None when no saving year is left or
    even the cap does not make the pot last.
    """

    def lasts(extra: float) -> bool:
        return not project(
            plan,
            retirement_age,
            real_return_pct,
            pot,
            loans,
            incomes,
            spending,
            today,
            extra_saving_monthly=extra,
        )["depleted"]

    if lasts(0.0):
        return 0.0
    birth = _as_date(plan["birth_date"])
    # same calendar-age rule as project()
    if today.year - birth.year >= retirement_age:
        return None
    if not lasts(_EXTRA_SAVING_CAP):
        return None
    # the smallest extra that lasts is the top of the failing range
    return _bisect(0.0, _EXTRA_SAVING_CAP, lambda x: not lasts(x))[1]


def _pot_from_sheet(db, sheet: dict, fx: Callable[[str], float]) -> dict:
    """Split the sheet's financial lines into investments, cash and deposits."""
    valuations = {v["portfolio_id"]: v for v in db.get_account_valuations()}
    deposits_db = {d["id"]: d for d in db.get_fixed_deposits(status="active")}
    investments = cash = 0.0
    deposits: list[dict] = []
    for group in sheet["groups"]:
        for line in group["lines"]:
            if not line.get("in_total"):
                continue
            amount = float(line["amount_eur"] or 0)
            if group["key"] == "cash":
                cash += amount
            elif group["key"] == "investments":
                lid = str(line["id"])
                val = (
                    valuations.get(int(lid.split(":")[1]))
                    if lid.startswith("valuation:")
                    else None
                )
                if val:
                    part = float(val.get("cash") or 0) * fx(
                        val.get("currency") or "EUR"
                    )
                    cash += part
                    amount -= part
                investments += amount
            elif group["key"] == "deposits":
                dep = deposits_db.get(int(str(line["id"]).split(":")[1]))
                if dep:
                    deposits.append(
                        {
                            "principal": amount,
                            "rate_pct": float(dep.get("interest_rate") or 0),
                            "maturity_date": dep.get("maturity_date"),
                        }
                    )
                else:
                    cash += amount
    return {"investments_eur": investments, "cash_eur": cash, "deposits": deposits}


def _loan_summary(loans: list[dict], today: date) -> list[dict]:
    from portf_manager.services.loans import end_date

    return [
        {
            "id": loan["id"],
            "name": loan["name"],
            "balance_eur": round(loan_balance_on(loan, today), 2),
            "monthly_payment_eur": loan_payment_of(loan),
            "end_date": end_date(loan).isoformat(),
        }
        for loan in loans
    ]


def readiness(
    db, today: Optional[date] = None, fx: Optional[Callable[[str], float]] = None
) -> dict:
    """The retirement readiness response (spec 6.4).

    Args:
        db: Database instance.
        today: reference date; defaults to today.
        fx: ``fx(currency)`` EUR rate; defaults to the live rate.

    Returns:
        ``status`` (``ok`` | ``needs_setup`` | ``insufficient_data``), the plan,
        inputs, four scenarios (retirement age and statutory age, base and
        pessimistic) and warnings. Each scenario's sustainable spend and extra
        saving are found with that scenario's own return case.
    """
    from portf_manager.services.balance_sheet import build_balance_sheet

    today = today or date.today()
    if fx is None:
        from portf_server.routers.portfolios import _get_fx_rate as fx
    plan = load_plan(db)
    base = {"as_of": today.isoformat(), "plan": plan, "scenarios": [], "warnings": []}
    if not plan.get("birth_date"):
        return {
            **base,
            "status": "needs_setup",
            "reason": "Set your birth date in the plan settings.",
        }
    spend = spending_inputs(db, plan, today, fx)
    if len(spend["months_used"]) < MIN_MONTHS:
        return {
            **base,
            "status": "insufficient_data",
            "reason": (
                f"Only {len(spend['months_used'])} complete months of bank "
                f"statements are imported; at least {MIN_MONTHS} are needed."
            ),
        }

    sheet = build_balance_sheet(db, today=today, fx=fx)
    pot = _pot_from_sheet(db, sheet, fx)
    loans = _loan_tables(db.get_loans())
    incomes = [
        {**i, "indexed": bool(i["indexed"])} for i in db.get_retirement_incomes()
    ]
    birth = _as_date(plan["birth_date"])
    spending = {
        "target_monthly_eur": spend["target_monthly_eur"],
        "savings_now_yearly_eur": spend["savings_now_yearly_eur"],
    }
    cases = [
        ("base", float(plan["real_return_pct"])),
        ("pessimistic", float(plan["pessimistic_real_return_pct"])),
    ]
    scenarios = []
    for age in (plan["retirement_age"], plan["statutory_age"]):
        for case, ret in cases:
            args = (plan, age, ret, pot, loans, incomes, spending, today)
            sc = project(*args[:7], today, case=case)
            sc.pop("depleted")
            sustainable = sustainable_monthly(*args)
            sc["sustainable_monthly_eur"] = round(sustainable, 2)
            sc["gap_monthly_eur"] = round(
                spending["target_monthly_eur"] - sustainable, 2
            )
            extra = extra_saving_needed_monthly(*args)
            sc["extra_saving_needed_monthly_eur"] = (
                None if extra is None else round(extra, 2)
            )
            scenarios.append(
                {
                    k: sc[k]
                    for k in (
                        "retirement_age",
                        "case",
                        "verdict",
                        "lasts_until_age",
                        "pot_at_retirement_eur",
                        "pot_at_horizon_eur",
                        "sustainable_monthly_eur",
                        "gap_monthly_eur",
                        "extra_saving_needed_monthly_eur",
                        "path",
                    )
                }
            )

    stale_lines = [ln for g in sheet["groups"] for ln in g["lines"] if ln["stale"]]
    counted = sum(1 for ln in stale_lines if ln["in_total"])
    left_out = len(stale_lines) - counted
    stale = int(sheet["totals"].get("stale_lines") or 0)
    warnings = []
    if not incomes:
        warnings.append("No state pension entered: the projection assumes none.")
    if stale_lines:
        msg = f"{counted} stale line(s) counted at their stale value"
        if left_out:
            msg += f"; {left_out} left out of the total"
        warnings.append(msg + ".")
    if not plan.get("partner_birth_date") and any(
        i["owner"] == "partner" for i in incomes
    ):
        warnings.append(
            "Partner income ignored: no partner birth date is set in the plan."
        )
    if plan["spending_includes_loan_payments"]:
        warnings.append(
            "Loan payments are assumed to be inside the spending target, so they "
            "drop out of the need once each loan ends."
        )
    else:
        warnings.append(
            "Loan payments are assumed to be outside the spending target, so the "
            "scheduled payments are added while each loan runs."
        )
    if float(plan["withdrawal_tax_pct"]) == 0:
        warnings.append("Figures are before tax on withdrawals.")

    financial = (
        pot["investments_eur"]
        + pot["cash_eur"]
        + sum(d["principal"] for d in pot["deposits"])
    )
    return {
        **base,
        "status": "ok",
        "inputs": {
            "age": _age_on(birth, today),
            "financial_assets_eur": round(financial, 2),
            "investments_eur": round(pot["investments_eur"], 2),
            "cash_eur": round(
                pot["cash_eur"] + sum(d["principal"] for d in pot["deposits"]), 2
            ),
            "spending_target_monthly_eur": spend["target_monthly_eur"],
            "spending_breakdown": spend["breakdown"],
            "spending_excluded": spend["excluded"],
            "spending_months_used": len(spend["months_used"]),
            "spending_includes_loan_payments": plan["spending_includes_loan_payments"],
            "savings_now_yearly_eur": spend["savings_now_yearly_eur"],
            "income_monthly_eur": spend["income_monthly_eur"],
            "loans": _loan_summary(db.get_loans(), today),
            "incomes": incomes,
            "stale_lines": stale,
        },
        "scenarios": scenarios,
        "warnings": warnings,
    }
