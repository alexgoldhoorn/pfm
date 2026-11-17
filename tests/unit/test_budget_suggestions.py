"""Yearly and carry-over budget suggestions (cost-control 2, section 3)."""

import pytest

from portf_manager.database import Database
from portf_manager.services import budget as bs


def _fx(currency):
    return 1.0


def _months(count):
    """The last ``count`` complete months, so tests never depend on today."""
    from datetime import date

    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        month, year = 12, year - 1
    return bs.month_range(f"{year:04d}-{month:02d}", count)


# ── pure choice ─────────────────────────────────────────────────────────────


def test_few_active_months_suggest_yearly():
    spend = {f"m{i}": 0.0 for i in range(12)}
    spend["m1"], spend["m7"], spend["m9"] = 100.0, 100.0, 100.0
    choice = bs.suggest_period(spend, "flexible", 12)
    assert choice["suggested_period"] == "yearly"
    assert choice["months_with_activity"] == 3
    assert choice["yearly_total"] == 300.0


def test_two_months_holding_most_of_the_total_suggest_yearly():
    spend = {f"m{i}": 10.0 for i in range(12)}
    spend["m0"], spend["m1"] = 400.0, 400.0
    choice = bs.suggest_period(spend, "flexible", 12)
    assert choice["suggested_period"] == "yearly"


def test_uneven_but_frequent_spending_suggests_carry_over():
    values = [50.0, 200.0, 40.0, 220.0, 30.0, 210.0, 60.0, 190.0, 45.0, 205.0]
    spend = {f"m{i}": v for i, v in enumerate(values + [50.0, 200.0])}
    choice = bs.suggest_period(spend, "flexible", 12)
    assert choice["suggested_period"] == "monthly"
    assert choice["suggested_rollover"] is True


def test_a_fixed_category_never_gets_carry_over():
    values = [50.0, 200.0, 40.0, 220.0, 30.0, 210.0, 60.0, 190.0, 45.0, 205.0]
    spend = {f"m{i}": v for i, v in enumerate(values + [50.0, 200.0])}
    choice = bs.suggest_period(spend, "fixed", 12)
    assert choice["suggested_period"] == "monthly"
    assert choice["suggested_rollover"] is False


def test_short_history_stays_monthly_and_says_so():
    spend = {f"m{i}": 0.0 for i in range(5)}
    spend["m1"] = 500.0
    choice = bs.suggest_period(spend, "flexible", 5)
    assert choice["suggested_period"] == "monthly"
    assert choice["suggested_rollover"] is False
    assert "too short" in choice["reason"]


# ── through the database ────────────────────────────────────────────────────


@pytest.fixture
def seeded(tmp_path):
    db = Database(str(tmp_path / "suggest.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Rent", parent_id=spend)
    db.create_spending_category("Insurance", parent_id=spend)
    db.create_spending_category("Coffee", parent_id=spend)
    for month in _months(12):
        db.create_spending_transaction(
            bank, f"{month}-02", "Landlord", -1000.0, category="Rent"
        )
    # Insurance is paid in two months only
    months = _months(12)
    for month in (months[2], months[8]):
        db.create_spending_transaction(
            bank, f"{month}-10", "Example Insurer", -415.0, category="Insurance"
        )
    # a one-off must not count towards the yearly total
    db.create_spending_transaction(
        bank, f"{months[5]}-11", "Example Insurer", -900.0, category="Insurance"
    )
    db.update_spending_transaction(
        db.list_spending_transactions(q="Example Insurer", min_abs_amount=800.0)[0][
            "id"
        ],
        one_off=True,
    )
    return db


def test_proposals_carry_the_suggestion_fields(seeded):
    by_ref = {
        p["ref_key"]: p
        for p in bs.propose_budget_lines(seeded, 12, _fx)
        if p["line_type"] == "spending"
    }
    insurance = by_ref["Insurance"]
    assert insurance["suggested_period"] == "yearly"
    assert insurance["months_with_activity"] == 2
    assert insurance["suggested_amount"] == 830.0
    assert insurance["suggested_rollover"] is False
    rent = by_ref["Rent"]
    assert rent["suggested_period"] == "monthly"
    assert rent["suggested_amount"] == rent["monthly_amount"] == 1000.0
    assert rent["months_with_activity"] == 12
    # current fields are unchanged and there is no budget to compare with
    assert "existing_line_id" not in rent


def test_existing_lines_get_a_change_hint(seeded):
    budget = seeded.create_budget("Base", is_active=True)
    seeded.create_budget_line(budget, "spending", "Insurance", 70.0)
    seeded.create_budget_line(budget, "spending", "Rent", 1000.0)
    by_ref = {
        p["ref_key"]: p
        for p in bs.propose_budget_lines(seeded, 12, _fx, budget_id=budget)
        if p["line_type"] == "spending"
    }
    assert by_ref["Insurance"]["change"] == "to_yearly"
    assert by_ref["Insurance"]["existing_line_id"] is not None
    assert by_ref["Rent"]["change"] is None


def test_a_short_history_keeps_every_suggestion_monthly(tmp_path):
    db = Database(str(tmp_path / "short.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Insurance", parent_id=spend)
    months = _months(3)
    db.create_spending_transaction(
        bank, f"{months[0]}-10", "Example Insurer", -400.0, category="Insurance"
    )
    db.create_spending_transaction(
        bank, f"{months[1]}-10", "Other Corp", -10.0, category="uncategorized"
    )
    db.create_spending_transaction(
        bank, f"{months[2]}-10", "Other Corp", -10.0, category="uncategorized"
    )
    proposal = next(
        p for p in bs.propose_budget_lines(db, 12, _fx) if p["ref_key"] == "Insurance"
    )
    assert proposal["suggested_period"] == "monthly"
    assert "too short" in proposal["reason"]
