"""Budget pace, yearly lines, rollover and tiers (cost-control batch B)."""

from datetime import date

import pytest

from portf_manager.database import Database
from portf_manager.services import budget as bs

TODAY = date(2026, 7, 15)


def _fx(currency):
    return 1.0


def _section(result, key):
    return next(s for s in result["sections"] if s["key"] == key)


def _line(result, key, ref_key):
    return next(
        item for item in _section(result, key)["lines"] if item["ref_key"] == ref_key
    )


# ── pure helpers ────────────────────────────────────────────────────────────


def test_history_months_adds_the_start_of_each_year():
    assert bs.history_months(["2026-03", "2026-04"]) == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
    ]
    assert bs.history_months(["2025-12", "2026-01"])[0] == "2025-01"


def test_rollover_skips_a_month_with_no_imports():
    planned = {m: 100.0 for m in ("2026-01", "2026-02", "2026-03", "2026-04")}
    actual = {"2026-01": 60.0, "2026-02": 100.0, "2026-03": 0.0, "2026-04": 130.0}
    months = list(planned)
    # March has no imports at all: its 100 must not become a surplus
    ledger = bs.rollover_ledger(
        planned, actual, {"2026-01", "2026-02", "2026-04"}, months
    )
    assert ledger["carried"] == {
        "2026-01": 0.0,
        "2026-02": 40.0,
        "2026-03": 40.0,
        "2026-04": 40.0,
    }
    assert ledger["available"]["2026-04"] == 140.0
    # had March counted as imported the carry would be 140
    counted = bs.rollover_ledger(planned, actual, set(months), months)
    assert counted["carried"]["2026-04"] == 140.0


def test_rollover_overspend_carries_as_a_negative_and_resets_in_january():
    planned = {"2025-12": 100.0, "2026-01": 100.0}
    ledger = bs.rollover_ledger(
        planned, {"2025-12": 150.0, "2026-01": 30.0}, set(planned), list(planned)
    )
    assert ledger["closing"]["2025-12"] == -50.0
    assert ledger["carried"]["2026-01"] == 0.0
    assert ledger["forfeited"] == {"2026-01": -50.0}


@pytest.mark.parametrize(
    "actual,projected,limit,status",
    [
        (450.0, 450.0, 400.0, "over"),
        (200.0, 520.0, 400.0, "at_risk"),
        (200.0, 410.0, 400.0, "on_track"),
        (200.0, 440.0, 400.0, "on_track"),  # 40 EUR over: under both thresholds
        (200.0, 470.0, 400.0, "at_risk"),
        (0.0, 300.0, 0.0, "at_risk"),
    ],
)
def test_pace_status_uses_the_overrun_thresholds(actual, projected, limit, status):
    assert bs.pace_status(actual, projected, limit)["status"] == status


# ── fixture: a bank, a tree and a half-way through July ─────────────────────


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "cost_control.db"))
    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    for name, tier in (
        ("Groceries", "flexible"),
        ("Rent", "fixed"),
        ("Gym", "optional"),
        ("Insurance", "fixed"),
        ("Gadgets", None),
    ):
        db.create_spending_category(name, parent_id=spend)
        if tier:
            db.set_spending_category_essentiality(name, tier)
    db.create_spending_category("Salary", parent_id=income)
    return db


@pytest.fixture
def bank(db):
    return db.get_or_create_portfolio("Example Bank", account_type="bank")


def _tx(db, bank, day, description, amount, category):
    return db.create_spending_transaction(
        bank, day, description, amount, category=category
    )


def _half_year(db, bank):
    """Rent and a gym every month, salary, and everyday groceries Jan-Jun."""
    for m in range(1, 8):
        _tx(db, bank, f"2026-{m:02d}-01", "Example Landlord", -1000.0, "Rent")
    for m in range(4, 7):
        _tx(db, bank, f"2026-{m:02d}-20", "Example Gym", -30.0, "Gym")
    for m in range(1, 7):
        _tx(db, bank, f"2026-{m:02d}-25", "Example Employer", 3000.0, "Salary")


# ── pace ────────────────────────────────────────────────────────────────────


def test_pace_projects_everyday_spending_and_adds_recurring_once(db, bank):
    _half_year(db, bank)
    # July: groceries so far, data runs to the 10th (today is the 15th)
    _tx(db, bank, "2026-07-03", "Example Market", -100.0, "Groceries")
    _tx(db, bank, "2026-07-10", "Example Market", -100.0, "Groceries")
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Groceries", 400.0)
    db.create_budget_line(budget, "spending", "Rent", 1000.0)
    db.create_budget_line(budget, "spending", "Gym", 30.0)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)

    groceries = _line(result, "spending", "Groceries")["pace"]
    assert groceries["data_through"] == "2026-07-10"
    # measured to the 10th of 31 days, not to the 15th
    assert groceries["elapsed_share"] == pytest.approx(10 / 31, abs=0.001)
    # 200 so far + 200 x (21/10) of everyday spending still to come
    assert groceries["projected_month_end_eur"] == pytest.approx(620.0, abs=0.01)
    assert groceries["status"] == "at_risk"
    assert groceries["limit_eur"] == 400.0

    # the rent already arrived on the 1st: it is not extrapolated
    rent = _line(result, "spending", "Rent")["pace"]
    assert rent["projected_month_end_eur"] == 1000.0
    assert rent["status"] == "on_track"

    # the gym is due on the 20th, after the data: counted once, not scaled
    gym = _line(result, "spending", "Gym")["pace"]
    assert gym["actual_to_date_eur"] == 0.0
    assert gym["expected_recurring_remaining_eur"] == 30.0
    assert gym["projected_month_end_eur"] == 30.0
    assert gym["status"] == "on_track"


def test_section_pace_covers_the_whole_spending_section(db, bank):
    _half_year(db, bank)
    _tx(db, bank, "2026-07-10", "Example Shop", -60.0, "Gadgets")
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Rent", 1000.0)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    pace = _section(result, "spending")["pace"]
    # rent (arrived) + gym (due) + the unbudgeted gadgets run rate
    assert pace["actual_to_date_eur"] == 1060.0
    assert pace["expected_recurring_remaining_eur"] == 30.0
    assert pace["projected_month_end_eur"] == pytest.approx(
        1060 + 30 + 60 * 21 / 10, abs=0.01
    )


def test_one_off_spending_so_far_is_not_extrapolated(db, bank):
    _half_year(db, bank)
    tx = _tx(db, bank, "2026-07-05", "Example Sofa", -600.0, "Gadgets")
    db.update_spending_transaction(tx, one_off=True)
    _tx(db, bank, "2026-07-10", "Example Shop", -30.0, "Gadgets")
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Gadgets", 100.0)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    pace = _line(result, "spending", "Gadgets")["pace"]
    # the sofa counts in the actual but not in the run rate: 630 + 30 x 2.1
    assert pace["actual_to_date_eur"] == 630.0
    assert pace["projected_month_end_eur"] == pytest.approx(693.0, abs=0.01)


def test_no_pace_for_a_month_with_nothing_imported(db, bank):
    _half_year(db, bank)
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Groceries", 400.0)
    august = date(2026, 8, 5)
    result = bs.compute_budget_variance(
        db, budget, ["2026-07", "2026-08"], _fx, today=august
    )
    assert _line(result, "spending", "Groceries")["pace"] is None
    assert _section(result, "spending")["pace"] is None


def test_no_pace_when_the_month_in_progress_is_not_in_the_window(db, bank):
    _half_year(db, bank)
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Groceries", 400.0)
    result = bs.compute_budget_variance(db, budget, ["2026-05"], _fx, today=TODAY)
    assert _line(result, "spending", "Groceries")["pace"] is None


def test_income_and_investment_lines_have_no_pace(db, bank):
    _half_year(db, bank)
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "income", "Salary", 3000.0)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    assert _line(result, "income", "Salary")["pace"] is None


# ── yearly lines ────────────────────────────────────────────────────────────


def _yearly_budget(db):
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Insurance", 1200.0, period="yearly")
    return budget


def test_yearly_bill_in_a_month_its_fund_covers_is_not_over(db, bank):
    _half_year(db, bank)
    _tx(db, bank, "2026-07-05", "Example Insurer", -600.0, "Insurance")
    budget = _yearly_budget(db)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    line = _line(result, "spending", "Insurance")

    assert line["period"] == "yearly"
    # one twelfth is set aside in July ...
    assert line["planned_eur"] == {"2026-07": 100.0}
    assert line["planned_total"] == 100.0
    assert line["monthly_amount"] == 1200.0 and line["set_aside_eur"] == 100.0
    # ... six twelfths were set aside January-June and nothing was spent
    assert line["carried_eur"] == {"2026-07": 600.0}
    assert line["available_eur"] == {"2026-07": 700.0}
    # the actual is untouched, and the bill is covered
    assert line["actual_total"] == 600.0
    assert line["available_total"] == 700.0
    assert line["variance_eur"] == 100.0 and line["favourable"] is True
    assert line["fund"] == {
        "year": 2026,
        "yearly_amount_eur": 1200.0,
        "set_aside_eur": 700.0,
        "spent_eur": 600.0,
        "remaining_eur": 100.0,
    }
    assert line["fund_balance_eur"] == {"2026-07": 100.0}


def test_yearly_bill_bigger_than_the_fund_so_far_is_over(db, bank):
    _half_year(db, bank)
    _tx(db, bank, "2026-03-05", "Example Insurer", -1200.0, "Insurance")
    budget = _yearly_budget(db)
    result = bs.compute_budget_variance(db, budget, ["2026-03"], _fx, today=TODAY)
    line = _line(result, "spending", "Insurance")
    # Jan + Feb + Mar set aside = 300 against a 1200 bill
    assert line["available_total"] == 300.0
    assert line["variance_eur"] == -900.0 and line["favourable"] is False
    assert line["fund"]["remaining_eur"] == -900.0


def test_yearly_fund_resets_in_january(db, bank):
    for m in (11, 12):
        _tx(db, bank, f"2025-{m}-05", "Example Employer", 3000.0, "Salary")
    _tx(db, bank, "2026-01-05", "Example Employer", 3000.0, "Salary")
    budget = _yearly_budget(db)
    result = bs.compute_budget_variance(
        db, budget, ["2025-12", "2026-01"], _fx, today=date(2026, 2, 1)
    )
    line = _line(result, "spending", "Insurance")
    assert line["carried_eur"]["2026-01"] == 0.0
    assert line["fund"]["year"] == 2026
    assert line["fund"]["set_aside_eur"] == 100.0
    # December's leftover (11 x 100 set aside in 2025 - nothing spent) is
    # dropped at the reset, so the window is not credited with it
    assert line["available_total"] == pytest.approx(100 + 1100 + 100 - 1200)


def test_yearly_override_replaces_the_twelfth_for_that_month(db, bank):
    _half_year(db, bank)
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(
        budget,
        "spending",
        "Insurance",
        1200.0,
        '{"2026-07": 300.0}',
        period="yearly",
    )
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    assert _line(result, "spending", "Insurance")["planned_eur"] == {"2026-07": 300.0}


# ── rollover ────────────────────────────────────────────────────────────────


def test_rollover_carries_across_a_month_with_no_imports(db, bank):
    # Jan and Feb imported, March has no rows at all, April is looked at
    for m in (1, 2, 4):
        _tx(db, bank, f"2026-{m:02d}-25", "Example Employer", 3000.0, "Salary")
    _tx(db, bank, "2026-01-10", "Example Market", -300.0, "Groceries")
    _tx(db, bank, "2026-02-10", "Example Market", -350.0, "Groceries")
    _tx(db, bank, "2026-04-10", "Example Market", -380.0, "Groceries")
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Groceries", 400.0, rollover=True)
    result = bs.compute_budget_variance(
        db, budget, ["2026-04"], _fx, today=date(2026, 5, 20)
    )
    line = _line(result, "spending", "Groceries")
    # (400-300) + (400-350); March's untouched 400 is not carried
    assert line["carried_eur"] == {"2026-04": 150.0}
    assert line["available_eur"] == {"2026-04": 550.0}
    assert line["available_total"] == 550.0
    assert line["actual_total"] == 380.0
    assert line["variance_eur"] == 170.0
    assert line["rollover"] is True and line["fund"] is None


def test_a_line_without_rollover_is_unchanged(db, bank):
    _half_year(db, bank)
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Rent", 900.0)
    result = bs.compute_budget_variance(db, budget, ["2026-06"], _fx, today=TODAY)
    line = _line(result, "spending", "Rent")
    assert line["carried_eur"] is None and line["available_eur"] is None
    assert line["available_total"] == line["planned_total"] == 900.0
    assert line["variance_eur"] == -100.0


def test_section_and_net_follow_the_available_amount(db, bank):
    _half_year(db, bank)
    _tx(db, bank, "2026-07-05", "Example Insurer", -600.0, "Insurance")
    budget = _yearly_budget(db)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    section = _section(result, "spending")
    assert section["available_total"] == 700.0
    # rent + gym are unbudgeted and counted in the actual, as before
    assert section["actual_total"] == 600.0 + 1000.0
    assert section["variance_eur"] == round(700.0 - section["actual_total"], 2)


# ── the reconcile invariant ─────────────────────────────────────────────────


def test_actual_totals_do_not_change_with_pace_yearly_and_rollover(db, bank):
    _half_year(db, bank)
    _tx(db, bank, "2026-07-05", "Example Insurer", -600.0, "Insurance")
    _tx(db, bank, "2026-07-08", "Example Market", -80.0, "Groceries")
    sofa = _tx(db, bank, "2026-06-09", "Example Sofa", -500.0, "Gadgets")
    db.update_spending_transaction(sofa, one_off=True)
    plain = db.create_budget("Plain")
    db.create_budget_line(plain, "spending", "Insurance", 100.0)
    db.create_budget_line(plain, "spending", "Groceries", 400.0)
    rich = db.create_budget("Rich")
    db.create_budget_line(rich, "spending", "Insurance", 1200.0, period="yearly")
    db.create_budget_line(rich, "spending", "Groceries", 400.0, rollover=True)
    months = ["2026-05", "2026-06", "2026-07"]
    a = bs.compute_budget_variance(db, plain, months, _fx, today=TODAY)
    b = bs.compute_budget_variance(db, rich, months, _fx, today=TODAY)
    for key in ("spending", "income"):
        assert _section(a, key)["actual_total"] == _section(b, key)["actual_total"]
    # and both equal what the Spending page's trend adds up to
    spent = 0.0
    for r in db.list_spending_transactions(start_date="2026-05-01", is_transfer=False):
        if r["amount"] < 0:
            spent += -r["amount"]
    assert _section(b, "spending")["actual_total"] == round(spent, 2)


# ── tiers ───────────────────────────────────────────────────────────────────


def test_lines_and_the_section_carry_essentiality_tiers(db, bank):
    _half_year(db, bank)
    _tx(db, bank, "2026-07-08", "Example Market", -80.0, "Groceries")
    _tx(db, bank, "2026-07-09", "Example Shop", -40.0, "Gadgets")
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Groceries", 400.0)
    db.create_budget_line(budget, "spending", "Rent", 1000.0)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    section = _section(result, "spending")
    assert _line(result, "spending", "Groceries")["tier"] == "flexible"
    assert _line(result, "spending", "Rent")["tier"] == "fixed"
    gadgets = next(u for u in section["unbudgeted"] if u["ref_key"] == "Gadgets")
    assert gadgets["tier"] == "unclassified"
    totals = section["tier_totals"]
    assert totals["fixed"] == {"planned_eur": 1000.0, "actual_eur": 1000.0}
    assert totals["flexible"] == {"planned_eur": 400.0, "actual_eur": 80.0}
    assert totals["unclassified"]["actual_eur"] == 40.0
    # the gym (optional) has not been charged yet this month
    assert totals["optional"]["actual_eur"] == 0.0
    assert sum(t["actual_eur"] for t in totals.values()) == pytest.approx(
        section["actual_total"], abs=0.01
    )
    assert _section(result, "income")["tier_totals"] is None


def test_response_carries_as_of_and_data_through(db, bank):
    _half_year(db, bank)
    budget = db.create_budget("Base", is_active=True)
    result = bs.compute_budget_variance(db, budget, ["2026-07"], _fx, today=TODAY)
    assert result["as_of"] == "2026-07-15"
    assert result["data_through"] == "2026-07-01"
