"""Monthly spending review: assembled from the existing services."""

from datetime import date
from unittest.mock import patch

import pytest

from portf_manager.database import Database
from portf_manager.services import spending_review as sr

TODAY = date(2026, 7, 15)


def _fx(currency):
    return 1.0


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "review.db"))
    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    for name, tier in (("Groceries", "flexible"), ("Rent", "fixed"), ("Gym", None)):
        db.create_spending_category(name, parent_id=spend)
        if tier:
            db.set_spending_category_essentiality(name, tier)
    db.create_spending_category("Salary", parent_id=income)
    return db


@pytest.fixture
def bank(db):
    return db.get_or_create_portfolio("Example Bank", account_type="bank")


def _tx(db, bank, day, description, amount, category, **kw):
    return db.create_spending_transaction(
        bank, day, description, amount, category=category, **kw
    )


def _history(db, bank):
    """Jan-Jul: rent, salary, steady groceries; a big grocery month in June."""
    for m in range(1, 8):
        _tx(db, bank, f"2026-{m:02d}-01", "Example Landlord", -1000.0, "Rent")
    for m in range(1, 7):
        _tx(db, bank, f"2026-{m:02d}-25", "Example Employer", 3000.0, "Salary")
    for m in range(1, 6):
        _tx(db, bank, f"2026-{m:02d}-10", "Example Market", -200.0, "Groceries")
    _tx(db, bank, "2026-06-10", "Example Market", -700.0, "Groceries")
    _tx(db, bank, "2026-07-05", "Example Market", -60.0, "Groceries", balance=900.0)


def test_review_defaults_to_the_last_complete_month(db, bank):
    _history(db, bank)
    review = sr.build_review(db, _fx, TODAY)
    assert review["month"] == "2026-06"
    assert review["status"] == "complete"
    assert review["data_through"] == "2026-07-05"
    totals = review["totals"]["totals"]
    assert totals["actual_eur"] == 1700.0
    assert totals["baseline_eur"] == 1200.0
    assert totals["delta_eur"] == 500.0
    top = review["movers"]["categories"][0]
    assert top["name"] == "Groceries" and top["delta_eur"] == 500.0
    assert review["movers"]["merchants"][0]["name"] == "Example Market"
    assert review["tiers"]["month"]["month"] == "2026-06"
    assert review["tiers"]["previous"]["month"] == "2026-05"
    assert review["tiers"]["month"]["fixed_eur"] == 1000.0
    assert review["budget"] is None
    assert review["freshness"][0]["name"] == "Example Bank"
    assert review["forecast"]["next_month"]["month"] == "2026-08"
    assert review["forecast"]["this_month"]["month"] == "2026-07"


def test_review_lists_only_that_months_anomalies(db, bank):
    _history(db, bank)
    review = sr.build_review(db, _fx, TODAY)
    kinds = {(i["kind"], i["date"]) for i in review["anomalies"]["items"]}
    assert ("category_spike", "2026-06") in kinds
    assert ("large_charge", "2026-06-10") in kinds
    assert review["anomalies"]["count"] == len(review["anomalies"]["items"])
    assert all(i["date"][:7] == "2026-06" for i in review["anomalies"]["items"])


def test_month_in_progress_has_no_deltas(db, bank):
    _history(db, bank)
    review = sr.build_review(db, _fx, TODAY, month="2026-07")
    assert review["status"] == "in_progress"
    assert review["totals"]["totals"]["delta_eur"] is None
    assert review["movers"]["categories"][0]["delta_eur"] is None
    assert any("still in progress" in w for w in review["warnings"])


def test_month_without_imports_is_reported_not_flattered(db, bank):
    _history(db, bank)
    review = sr.build_review(db, _fx, TODAY, month="2025-03")
    assert review["status"] == "no_imports"
    assert review["totals"]["totals"]["actual_eur"] is None
    assert review["movers"]["categories"] == []
    assert any("Nothing is imported for 2025-03" in w for w in review["warnings"])


def test_month_after_the_current_one_is_refused(db, bank):
    with pytest.raises(ValueError):
        sr.build_review(db, _fx, TODAY, month="2026-08")


def test_budget_result_for_the_month(db, bank):
    _history(db, bank)
    budget = db.create_budget("Base", is_active=True)
    db.create_budget_line(budget, "spending", "Groceries", 300.0)
    db.create_budget_line(budget, "spending", "Rent", 1000.0)
    review = sr.build_review(db, _fx, TODAY)
    spending = next(s for s in review["budget"]["sections"] if s["key"] == "spending")
    assert spending["available_total"] == 1300.0
    assert spending["actual_total"] == 1700.0
    assert spending["variance_eur"] == -400.0
    assert [ln["label"] for ln in spending["over_lines"]] == ["Spend > Groceries"]
    assert review["budget"]["no_activity"] is False


def test_recurring_changes(db, bank):
    _history(db, bank)
    for day in ("2026-04-18", "2026-05-18", "2026-06-18"):
        _tx(db, bank, day, "Example Streaming", -10.0, "Gym")
    for day in ("2026-01-05", "2026-02-05", "2026-03-05"):
        _tx(db, bank, day, "Example Old Club", -25.0, "Gym")
    for day in ("2026-03-12", "2026-04-12", "2026-05-12"):
        _tx(db, bank, day, "Example Paper", -15.0, "Gym")
    for day, amount in (
        ("2026-03-22", 30.0),
        ("2026-04-22", 30.0),
        ("2026-05-22", 30.0),
        ("2026-06-22", 40.0),
    ):
        _tx(db, bank, day, "Example Gym", -amount, "Gym")
    recurring = sr.build_review(db, _fx, TODAY)["recurring"]
    assert [r["merchant"] for r in recurring["new"]] == ["Example Streaming"]
    assert [r["merchant"] for r in recurring["ended"]] == ["Example Old Club"]
    assert [r["merchant"] for r in recurring["missed"]] == ["Example Paper"]
    change = recurring["price_change"]
    assert [r["merchant"] for r in change] == ["Example Gym"]
    assert change[0]["price_change_pct"] == 33.3 and change[0]["last_eur"] == 40.0


def test_a_failing_part_is_none_plus_a_warning(db, bank):
    _history(db, bank)
    with patch.object(sr, "load_recurring", side_effect=RuntimeError("boom")):
        review = sr.build_review(db, _fx, TODAY)
    assert review["recurring"] is None
    assert any(
        "Recurring changes could not be built: boom" in w for w in review["warnings"]
    )
    assert review["totals"] is not None and review["forecast"] is not None


def test_unreadable_rows_degrade_every_part_that_needs_them(db, bank):
    with patch.object(sr.spending_insights, "load_rows", side_effect=OSError("down")):
        review = sr.build_review(db, _fx, TODAY)
    assert review["totals"] is None and review["movers"] is None
    assert review["anomalies"] is None and review["tiers"] is None
    assert any("Bank rows could not be read" in w for w in review["warnings"])
    assert review["freshness"] is not None


def test_no_database_content_at_all_still_answers(db):
    review = sr.build_review(db, _fx, TODAY)
    assert review["status"] == "no_imports"
    assert review["data_through"] is None
    assert review["budget"] is None
