"""Year view: this year against last year, like for like."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import spending_insights as si
from portf_manager.services import spending_year

_KEY = "test-key-year-abc123"
HEADERS = {"X-API-Key": _KEY}


def _build(db, today, year, depth=1):
    rows = si.load_rows(db, lambda c: 1.0, start_date=f"{year - 1}-01-01")
    tree = {r["name"]: r for r in db.list_spending_categories_tree()}
    return spending_year.build_year(rows, tree, today, year=year, depth=depth)


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "year.db"))
    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    home = db.create_spending_category("Home", parent_id=spend)
    db.create_spending_category("Rent", parent_id=home)
    db.create_spending_category("Repairs", parent_id=home)
    db.create_spending_category("Groceries", parent_id=spend)
    db.create_spending_category("Travel", parent_id=spend)
    db.create_spending_category("Salary", parent_id=income)
    return db


def _fill(db, bank, year, months, groceries):
    for m in months:
        d = f"{year}-{m:02d}"
        db.create_spending_transaction(
            bank, f"{d}-01", "EXAMPLE EMPLOYER", 3000.0, category="Salary"
        )
        db.create_spending_transaction(
            bank, f"{d}-02", "EXAMPLE LANDLORD", -1000.0, category="Rent"
        )
        db.create_spending_transaction(
            bank, f"{d}-10", "EXAMPLE MARKET", -groceries, category="Groceries"
        )


@pytest.fixture
def two_years(db):
    bank = db.create_portfolio("Example Bank", account_type="bank")
    # June 2024 has no imports at all
    _fill(db, bank, 2024, [m for m in range(1, 13) if m != 6], 300.0)
    _fill(db, bank, 2025, range(1, 13), 350.0)
    db.create_spending_transaction(
        bank, "2024-03-15", "EXAMPLE AIRLINE", -500.0, category="Travel"
    )
    repair = db.create_spending_transaction(
        bank, "2025-03-15", "EXAMPLE PLUMBER", -900.0, category="Repairs"
    )
    db.update_spending_transaction(repair, one_off=1)
    # data reaches into 2026, so 2025 is complete
    db.create_spending_transaction(
        bank, "2026-01-05", "EXAMPLE EMPLOYER", 3000.0, category="Salary"
    )
    return db


def test_complete_year_is_compared_like_for_like(two_years):
    d = _build(two_years, date(2026, 2, 1), 2025)
    assert d["year"] == 2025 and d["previous_year"] == 2024
    assert d["complete"] is True
    assert d["compared_through"] == "2025-12"
    # June has no 2024 counterpart: left out on both sides
    assert "2025-06" not in d["compared_months"]
    assert len(d["compared_months"]) == 11
    this, prev, full = (
        d["totals"]["this"],
        d["totals"]["previous"],
        d["totals"]["previous_full"],
    )
    assert this["income_eur"] == 33000.0
    assert this["spending_eur"] == 11 * 1350.0 + 900.0
    assert this["one_off_eur"] == 900.0
    assert this["net_eur"] == 33000.0 - 15750.0
    assert this["savings_rate_pct"] == pytest.approx(52.3, abs=0.1)
    assert prev["spending_eur"] == 11 * 1300.0 + 500.0
    assert full["spending_eur"] == prev["spending_eur"]
    assert any("2025-06" in w for w in d["warnings"])


def test_month_grid_marks_unknown_months(two_years):
    d = _build(two_years, date(2026, 2, 1), 2025)
    assert len(d["months"]["this"]) == 12 and len(d["months"]["previous"]) == 12
    june_prev = d["months"]["previous"][5]
    assert june_prev["month"] == "2024-06"
    assert june_prev["has_imports"] is False and june_prev["spending_eur"] == 0.0
    assert d["months"]["this"][2]["spending_eur"] == 2250.0


def test_categories_movers_merchants_and_one_offs(two_years):
    d = _build(two_years, date(2026, 2, 1), 2025)
    by_name = {c["category"]: c for c in d["categories"]}
    assert [c["category"] for c in d["categories"]][:2] == ["Home", "Groceries"]
    assert by_name["Home"]["this_eur"] == 11900.0
    assert by_name["Home"]["previous_eur"] == 11000.0
    assert by_name["Groceries"]["delta_eur"] == 550.0
    assert by_name["Groceries"]["delta_pct"] == pytest.approx(16.7, abs=0.1)
    assert by_name["Travel"]["this_eur"] == 0.0
    assert by_name["Travel"]["delta_pct"] == -100.0
    assert by_name["Home"]["share_pct"] == pytest.approx(75.6, abs=0.1)
    assert "children" not in by_name["Home"]
    assert [m["category"] for m in d["movers"]["increases"]] == [
        "Repairs",
        "Groceries",
    ]
    assert [m["category"] for m in d["movers"]["decreases"]] == ["Travel"]
    assert d["merchants"][0] == {
        "merchant": "EXAMPLE LANDLORD",
        "this_eur": 11000.0,
        "previous_eur": 11000.0,
    }
    assert [o["description"] for o in d["one_offs"]] == ["EXAMPLE PLUMBER"]


def test_depth_two_adds_children(two_years):
    d = _build(two_years, date(2026, 2, 1), 2025, depth=2)
    home = next(c for c in d["categories"] if c["category"] == "Home")
    kids = {c["category"]: c for c in home["children"]}
    assert kids["Rent"]["this_eur"] == 11000.0
    assert kids["Repairs"]["delta_eur"] == 900.0


def test_year_in_progress_cuts_the_previous_year(db):
    bank = db.create_portfolio("Example Bank", account_type="bank")
    _fill(db, bank, 2025, range(1, 13), 350.0)
    _fill(db, bank, 2026, range(1, 8), 400.0)
    # July 2026 runs only to the 10th: partial
    db.create_spending_transaction(
        bank, "2026-07-10", "EXAMPLE MARKET", -5.0, category="Groceries"
    )
    d = _build(db, date(2026, 7, 15), 2026)
    assert d["complete"] is False
    assert d["compared_through"] == "2026-06"
    assert len(d["compared_months"]) == 6
    assert d["totals"]["this"]["spending_eur"] == 6 * 1400.0
    assert d["totals"]["previous"]["spending_eur"] == 6 * 1350.0
    assert d["totals"]["previous_full"]["spending_eur"] == 12 * 1350.0
    assert any("2026-06" in w and "in progress" in w for w in d["warnings"])
    # the partial month is in the grid but never in the comparison
    assert d["months"]["this"][6]["complete"] is False
    assert "2026-07" not in d["compared_months"]


def test_nothing_comparable_gives_no_totals_or_deltas(db):
    bank = db.create_portfolio("Example Bank", account_type="bank")
    _fill(db, bank, 2025, range(1, 13), 350.0)
    db.create_spending_transaction(
        bank, "2026-01-05", "EXAMPLE EMPLOYER", 3000.0, category="Salary"
    )
    d = _build(db, date(2026, 2, 1), 2025)
    assert d["totals"]["this"] is None and d["totals"]["previous"] is None
    assert d["totals"]["previous_full"] is None
    assert d["categories"] == []
    assert d["movers"] == {"increases": [], "decreases": []}
    assert any("like for like" in w for w in d["warnings"])


def test_bad_year_or_depth_is_refused(db):
    with pytest.raises(ValueError):
        _build(db, date(2026, 2, 1), 2027)
    with pytest.raises(ValueError):
        spending_year.build_year([], {}, date(2026, 2, 1), depth=3)


def test_year_endpoint(two_years, tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    km = APIKeyManager(two_years)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: two_years
    app.dependency_overrides[get_api_key_manager] = lambda: km
    try:
        client = TestClient(app)
        r = client.get("/api/v1/spending/year?year=2025&depth=2", headers=HEADERS)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["year"] == 2025 and d["totals"]["this"]["income_eur"] == 33000.0
        assert "children" in d["categories"][0]
        default = client.get("/api/v1/spending/year", headers=HEADERS)
        assert default.status_code == 200
        assert default.json()["year"] == date.today().year
        assert (
            client.get("/api/v1/spending/year?year=2999", headers=HEADERS).status_code
            == 400
        )
        assert (
            client.get("/api/v1/spending/year?depth=3", headers=HEADERS).status_code
            == 422
        )
    finally:
        app.dependency_overrides.clear()
