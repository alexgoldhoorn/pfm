"""Tests for the retirement readiness projection (pure maths and orchestration)."""

from datetime import date

import pytest

from portf_manager.database import Database
from portf_manager.services.progress import savings_and_buffer
from portf_manager.services.retirement import (
    DEFAULT_PLAN,
    expand_categories,
    extra_saving_needed_monthly,
    project,
    readiness,
    sustainable_monthly,
)

TODAY = date(2026, 1, 1)


def _plan(**over) -> dict:
    """A zero-return plan for someone born 1961-01-01 (65 on TODAY)."""
    plan = dict(DEFAULT_PLAN)
    plan.update(
        birth_date="1961-01-01",
        retirement_age=65,
        statutory_age=67,
        horizon_age=95,
        real_return_pct=0.0,
        pessimistic_real_return_pct=0.0,
        cash_real_return_pct=0.0,
    )
    plan.update(over)
    return plan


def _pot(cash=0.0, investments=0.0, deposits=None) -> dict:
    return {
        "investments_eur": investments,
        "cash_eur": cash,
        "deposits": deposits or [],
    }


def _spending(target=1000.0, savings=0.0) -> dict:
    return {"target_monthly_eur": target, "savings_now_yearly_eur": savings}


def _run(plan=None, pot=None, loans=None, incomes=None, spending=None, **kw):
    plan = plan or _plan()
    return project(
        plan,
        kw.pop("retirement_age", plan["retirement_age"]),
        kw.pop("real_return_pct", plan["real_return_pct"]),
        pot or _pot(cash=120000),
        loans or [],
        incomes or [],
        spending or _spending(),
        kw.pop("today", TODAY),
        **kw,
    )


def _pension(monthly=1000.0, owner="self", from_age=65, indexed=1, to_age=None):
    return {
        "name": "Example Pension",
        "owner": owner,
        "monthly_eur": monthly,
        "from_age": from_age,
        "to_age": to_age,
        "indexed": indexed,
    }


def test_pot_lasts_exactly_ten_years_with_zero_returns():
    s = _run()
    assert s["lasts_until_age"] == 75
    assert s["verdict"] == "at_risk"
    assert s["pot_at_retirement_eur"] == 120000
    assert s["pot_at_horizon_eur"] == 0
    assert s["path"][0]["age"] == 65 and s["path"][0]["year"] == 2026


def test_pension_covering_spending_never_depletes():
    s = _run(incomes=[_pension(1000)])
    assert s["verdict"] == "on_track"
    assert s["lasts_until_age"] == 95
    assert s["pot_at_horizon_eur"] == 120000


def test_short_verdict_between_ten_years_and_horizon():
    # 180000 / 12000 a year = 15 years: depleted at 80
    s = _run(pot=_pot(cash=180000))
    assert s["lasts_until_age"] == 80
    assert s["verdict"] == "short"


def test_loan_payment_drops_out_after_loan_ends():
    # 60 payments of 500, 2026-01 to 2030-12: ended from 2031 (age 70)
    loan = {
        "principal": 30000,
        "annual_rate_pct": 0,
        "start_date": "2025-12-01",
        "term_months": 60,
        "extra_payment_eur": 0,
    }
    s = _run(pot=_pot(cash=1000000), loans=[loan])
    by_age = {p["age"]: p["withdrawal_eur"] for p in s["path"]}
    assert by_age[69] == 12000
    assert by_age[70] == 6000
    assert s["path"][0]["loans_eur"] == pytest.approx(30000 - 6000)


def test_loan_added_when_spending_excludes_payments():
    loan = {
        "principal": 30000,
        "annual_rate_pct": 0,
        "start_date": "2025-12-01",
        "term_months": 60,
        "extra_payment_eur": 0,
    }
    plan = _plan(spending_includes_loan_payments=False)
    s = _run(plan=plan, pot=_pot(cash=1000000), loans=[loan])
    by_age = {p["age"]: p["withdrawal_eur"] for p in s["path"]}
    assert by_age[69] == 18000
    assert by_age[70] == 12000


def test_non_indexed_income_decays_two_percent_a_year():
    s = _run(
        pot=_pot(cash=1000000),
        incomes=[_pension(1000, indexed=0)],
        spending=_spending(0),
    )
    by_age = {p["age"]: p["pensions_eur"] for p in s["path"]}
    assert by_age[65] == pytest.approx(12000)
    assert by_age[66] == pytest.approx(12000 * 0.98)
    assert by_age[67] == pytest.approx(12000 * 0.98**2)


def test_income_runs_from_age_to_age_exclusive():
    s = _run(
        pot=_pot(cash=1000000),
        incomes=[_pension(1000, from_age=66, to_age=68)],
        spending=_spending(0),
    )
    by_age = {p["age"]: p["pensions_eur"] for p in s["path"]}
    assert by_age[65] == 0
    assert by_age[66] == 12000 and by_age[67] == 12000
    assert by_age[68] == 0


def test_partner_income_without_birth_date_is_ignored():
    s = _run(incomes=[_pension(1000, owner="partner")])
    assert s["lasts_until_age"] == 75
    assert all(p["pensions_eur"] == 0 for p in s["path"])


def test_partner_income_uses_partner_age():
    plan = _plan(partner_birth_date="1963-01-01")
    s = _run(
        plan=plan,
        pot=_pot(cash=1000000),
        incomes=[_pension(1000, owner="partner", from_age=65)],
        spending=_spending(0),
    )
    by_age = {p["age"]: p["pensions_eur"] for p in s["path"]}
    # partner is 63 on TODAY, so 65 two years after the owner's 65
    assert by_age[66] == 0
    assert by_age[67] == 12000


def test_savings_continue_until_retirement_then_stop():
    plan = _plan(birth_date="1971-01-01")
    s = _run(plan=plan, pot=_pot(), spending=_spending(0, savings=12000))
    # ages 55..64 save ten years
    assert s["pot_at_retirement_eur"] == 120000
    assert s["path"][0]["age"] == 55
    assert s["path"][-1]["investments_eur"] == 120000


def test_first_step_is_prorated_by_months_left():
    plan = _plan(birth_date="1971-01-01")
    s = _run(
        plan=plan,
        pot=_pot(),
        spending=_spending(0, savings=12000),
        today=date(2026, 7, 1),
    )
    # July to December: 6/12 of a year
    assert s["path"][0]["investments_eur"] == pytest.approx(6000)


def test_cash_is_drawn_before_investments_down_to_six_months():
    s = _run(pot=_pot(cash=100000, investments=50000))
    first = s["path"][0]
    assert first["cash_eur"] == 88000
    assert first["investments_eur"] == 50000
    # 94000 above the 6000 reserve funds 7 years and 10000 of the 8th; the
    # other 2000 of that year comes from investments
    by_age = {p["age"]: p for p in s["path"]}
    assert by_age[71]["cash_eur"] == 16000
    assert by_age[72]["cash_eur"] == 6000
    assert by_age[72]["investments_eur"] == 48000


def test_withdrawal_tax_grosses_up_the_draw():
    plan = _plan(withdrawal_tax_pct=20.0)
    s = _run(plan=plan, pot=_pot(cash=1000000))
    assert s["path"][0]["withdrawal_eur"] == pytest.approx(15000)


def test_deposit_grows_to_maturity_then_joins_cash():
    # 2 % nominal minus 2 % inflation = 0 real; at 6 % it grows 4 %
    dep = {"principal": 10000, "rate_pct": 6.0, "maturity_date": "2027-06-30"}
    plan = _plan(retirement_age=90, statutory_age=90, birth_date="1961-01-01")
    s = _run(plan=plan, pot=_pot(deposits=[dep]), spending=_spending(0))
    assert s["path"][0]["cash_eur"] == pytest.approx(10400)
    # from its maturity year (2027) it is cash, which earns 0 here
    assert s["path"][1]["cash_eur"] == pytest.approx(10400)
    assert s["path"][2]["cash_eur"] == pytest.approx(10400)


def test_sustainable_monthly_matches_target_when_exactly_funded():
    plan = _plan(horizon_age=75)
    got = sustainable_monthly(
        plan, 65, 0.0, _pot(cash=120000), [], [], _spending(), TODAY
    )
    assert got == pytest.approx(1000, abs=1.5)


def test_extra_saving_needed_is_zero_when_on_track():
    got = extra_saving_needed_monthly(
        _plan(), 65, 0.0, _pot(cash=2000000), [], [], _spending(), TODAY
    )
    assert got == 0


def test_extra_saving_needed_closes_the_gap():
    plan = _plan(birth_date="1971-01-01", horizon_age=75)
    got = extra_saving_needed_monthly(
        plan, 65, 0.0, _pot(), [], [], _spending(1000), TODAY
    )
    # 120000 needed over ten saving years
    assert got == pytest.approx(1000, abs=2)


def test_expand_categories_includes_descendants():
    tree = [
        {"name": "Taxes", "parent_name": None},
        {"name": "Income Tax", "parent_name": "Taxes"},
        {"name": "Local Tax", "parent_name": "Income Tax"},
        {"name": "Food", "parent_name": None},
    ]
    got = expand_categories(tree, ["Taxes"])
    assert got == frozenset({"Taxes", "Income Tax", "Local Tax"})
    assert expand_categories(tree, ["Unknown"]) == frozenset({"Unknown"})


def test_progress_savings_unchanged_without_exclude():
    rows = [
        {"date": "2026-08-05", "amount_eur": -100, "category": "Invest"},
        {"date": "2026-08-06", "amount_eur": 300, "category": "Salary"},
    ]
    today = date(2026, 10, 19)
    plain = savings_and_buffer(rows, None, today)
    assert plain["spent_eur"] == 100
    cut = savings_and_buffer(rows, None, today, exclude=frozenset({"Invest"}))
    assert cut["spent_eur"] == 0 and cut["income_eur"] == 300


# ── readiness over a real database ────────────────────────────────────────────


def _bank_db(tmp_path, months: int, plan_birth: str | None = "1983-07-20"):
    db = Database(str(tmp_path / "ready.db"))
    pid = db.get_or_create_portfolio("Example Bank", account_type="bank")
    year, month = 2026, 9
    for _ in range(months):
        db.create_spending_transaction(
            pid, f"{year}-{month:02d}-05", "Rent", -1000.0, category="Rent"
        )
        db.create_spending_transaction(
            pid, f"{year}-{month:02d}-06", "Pay", 3000.0, category="Salary"
        )
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    if plan_birth:
        from portf_manager.services.retirement import load_plan, save_plan

        save_plan(db, {**load_plan(db), "birth_date": plan_birth})
    return db


def test_readiness_insufficient_data_under_six_months(tmp_path):
    db = _bank_db(tmp_path, 5)
    r = readiness(db, today=date(2026, 10, 19), fx=lambda c: 1.0)
    assert r["status"] == "insufficient_data"
    assert r["reason"]
    assert not r.get("scenarios")


def test_readiness_needs_setup_without_birth_date(tmp_path):
    db = _bank_db(tmp_path, 12, plan_birth=None)
    r = readiness(db, today=date(2026, 10, 19), fx=lambda c: 1.0)
    assert r["status"] == "needs_setup"
    assert not r.get("scenarios")


def test_readiness_ok_inputs_and_scenario_order(tmp_path):
    db = _bank_db(tmp_path, 12)
    r = readiness(db, today=date(2026, 10, 19), fx=lambda c: 1.0)
    assert r["status"] == "ok"
    assert r["inputs"]["age"] == 43
    assert r["inputs"]["spending_target_monthly_eur"] == 1000
    assert r["inputs"]["spending_months_used"] == 12
    assert r["inputs"]["savings_now_yearly_eur"] == 24000
    order = [(s["retirement_age"], s["case"]) for s in r["scenarios"]]
    assert order == [
        (65, "base"),
        (65, "pessimistic"),
        (67, "base"),
        (67, "pessimistic"),
    ]
    s = r["scenarios"][0]
    for key in (
        "sustainable_monthly_eur",
        "gap_monthly_eur",
        "extra_saving_needed_monthly_eur",
    ):
        assert key in s
    assert s["gap_monthly_eur"] == pytest.approx(
        1000 - s["sustainable_monthly_eur"], abs=0.01
    )
    assert any("No state pension" in w for w in r["warnings"])
    assert any("before tax" in w for w in r["warnings"])


def test_readiness_splits_a_fresh_valuation_into_investments_and_cash(tmp_path):
    db = _bank_db(tmp_path, 12)
    pid = db.get_or_create_portfolio("Example Broker")
    db.upsert_account_valuation(
        pid, "indexa", "REF", 50000.0, 10000.0, "EUR", "2026-10-18", "2026-10-18"
    )
    r = readiness(db, today=date(2026, 10, 19), fx=lambda c: 1.0)
    assert r["inputs"]["investments_eur"] == 40000
    assert r["inputs"]["cash_eur"] >= 10000


def test_extra_saving_zero_in_calendar_year_of_retirement():
    # turns 65 on 2026-12-31: project() already treats 2026 as retired
    plan = _plan(birth_date="1961-12-31")
    got = extra_saving_needed_monthly(
        plan, 65, 0.0, _pot(), [], [], _spending(1000), date(2026, 6, 1)
    )
    assert got == 0


def test_extra_saving_none_when_cap_is_not_enough():
    plan = _plan(birth_date="1971-01-01")
    got = extra_saving_needed_monthly(
        plan, 65, 0.0, _pot(), [], [], _spending(1_000_000), TODAY
    )
    assert got is None


def test_loan_starting_after_retirement_is_not_treated_as_ended():
    # 500 a month from 2031 (age 70); before that there is no loan to drop out
    loan = {
        "principal": 30000,
        "annual_rate_pct": 0,
        "start_date": "2030-12-01",
        "term_months": 60,
        "extra_payment_eur": 0,
    }
    s = _run(pot=_pot(cash=1000000), loans=[loan])
    by_age = {p["age"]: p["withdrawal_eur"] for p in s["path"]}
    assert by_age[65] == 12000
    assert by_age[69] == 12000
    # running 2031-2035, ended from age 75
    assert by_age[74] == 12000
    assert by_age[75] == 6000


def test_save_plan_bounds_withdrawal_tax():
    from portf_manager.services.retirement import save_plan

    db = Database(":memory:") if False else None
    assert db is None
    with pytest.raises(ValueError):
        save_plan(_NoDb(), {"withdrawal_tax_pct": 100.0})


class _NoDb:
    def set_setting(self, *a):
        raise AssertionError("must validate before storing")
