"""What-if scenarios: runway, income change, house purchase, crash recovery."""

import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import life_scenarios as ls

_KEY = "test-key-scenarios-abc123"
HEADERS = {"X-API-Key": _KEY}
TODAY = date(2026, 7, 15)


def _fx(currency: str) -> float:
    return 1.0


def _base(**over) -> dict:
    """4000 in (3000 from work), 3000 out, 12k cash, 20k invested at cost."""
    base = {
        "as_of": "2026-07-15",
        "spending": {
            "months_used": ["2026-01", "2026-02", "2026-03"],
            "income_monthly_eur": 4000.0,
            "work_income_monthly_eur": 3000.0,
            "other_income_monthly_eur": 1000.0,
            "spend_monthly_eur": 3000.0,
            "savings_transfers_monthly_eur": 400.0,
            "by_tier_monthly_eur": {
                "fixed": 1500.0,
                "flexible": 1000.0,
                "optional": 500.0,
                "unclassified": 0.0,
            },
            "categories": [
                {"name": "Housing", "tier": "fixed", "monthly_eur": 1500.0},
                {"name": "Groceries", "tier": "flexible", "monthly_eur": 1000.0},
                {"name": "Restaurants", "tier": "optional", "monthly_eur": 300.0},
                {"name": "Travel", "tier": "optional", "monthly_eur": 200.0},
            ],
        },
        "liquid_eur": 12000.0,
        "investments_eur": 20000.0,
        "unrealised_gain_eur": 0.0,
        "net_worth_eur": 32000.0,
        "loans": [],
        "loan_payments_monthly_eur": 0.0,
        "warnings": [],
    }
    base.update(over)
    return base


# ── tiers and profile ───────────────────────────────────────────────────────


def test_tier_is_inherited_from_the_nearest_marked_ancestor():
    tree = {
        "Spend": {"name": "Spend", "parent_name": None, "essentiality": None},
        "Leisure": {
            "name": "Leisure",
            "parent_name": "Spend",
            "essentiality": "optional",
        },
        "Cinema": {"name": "Cinema", "parent_name": "Leisure", "essentiality": None},
        "Gym": {"name": "Gym", "parent_name": "Leisure", "essentiality": "flexible"},
        "Misc": {"name": "Misc", "parent_name": "Spend", "essentiality": None},
    }
    assert ls.tier_of("Cinema", tree) == "optional"
    assert ls.tier_of("Gym", tree) == "flexible"
    assert ls.tier_of("Misc", tree) == "unclassified"
    assert ls.tier_of("Unknown", tree) == "unclassified"
    assert ls.tier_of(None, tree) == "unclassified"


def test_spending_profile_averages_complete_months_only():
    tree = {
        "Spend": {"name": "Spend", "parent_name": None, "essentiality": None},
        "Rent": {"name": "Rent", "parent_name": "Spend", "essentiality": "fixed"},
        "Fun": {"name": "Fun", "parent_name": "Spend", "essentiality": "optional"},
        "Invest": {"name": "Invest", "parent_name": "Spend", "essentiality": None},
    }
    rows = [
        {"date": "2026-05-01", "amount_eur": 3000.0, "category": "Salary"},
        {"date": "2026-05-02", "amount_eur": -1000.0, "category": "Rent"},
        {"date": "2026-05-09", "amount_eur": -500.0, "category": "Invest"},
        {"date": "2026-06-01", "amount_eur": 3000.0, "category": "Salary"},
        {"date": "2026-06-02", "amount_eur": -1000.0, "category": "Rent"},
        {"date": "2026-06-20", "amount_eur": -200.0, "category": "Fun"},
        # the month in progress never counts
        {"date": "2026-07-02", "amount_eur": -9999.0, "category": "Fun"},
    ]
    p = ls.spending_profile(rows, tree, TODAY, months=12)
    assert p["months_used"] == ["2026-05", "2026-06"]
    assert p["income_monthly_eur"] == 3000.0
    assert p["work_income_monthly_eur"] == 3000.0
    assert p["spend_monthly_eur"] == 1100.0
    assert p["savings_transfers_monthly_eur"] == 250.0
    assert p["by_tier_monthly_eur"] == {
        "fixed": 1000.0,
        "flexible": 0.0,
        "optional": 100.0,
        "unclassified": 0.0,
    }
    assert [c["name"] for c in p["categories"]] == ["Rent", "Fun"]


def test_spending_profile_with_no_rows_is_all_zero():
    p = ls.spending_profile([], {}, TODAY)
    assert p["months_used"] == []
    assert p["income_monthly_eur"] == 0.0 and p["spend_monthly_eur"] == 0.0


# ── job loss ────────────────────────────────────────────────────────────────


def test_job_loss_three_levels():
    out = ls.job_loss(_base())
    assert out["income_lost_monthly_eur"] == 3000.0
    assert out["income_remaining_monthly_eur"] == 1000.0
    lv = out["levels"]
    assert lv["as_today"]["monthly_gap_eur"] == 2000.0
    assert lv["as_today"]["months_on_cash"] == 6
    assert lv["as_today"]["months_with_investments"] == 16
    assert lv["no_optional"]["months_on_cash"] == 8
    assert lv["no_optional"]["months_with_investments"] == 21
    assert lv["lean"]["monthly_spend_eur"] == 2300.0
    assert lv["lean"]["months_on_cash"] == 9
    assert lv["lean"]["months_with_investments"] == 24
    assert lv["as_today"]["money_left_by_month_eur"][:3] == [32000.0, 30000.0, 28000.0]
    assert not lv["as_today"]["sustainable"]


def test_job_loss_cut_order_is_optional_then_flexible():
    cuts = ls.job_loss(_base())["cut_first"]
    assert [c["category"] for c in cuts] == ["Restaurants", "Travel", "Groceries"]
    assert cuts[0]["saves_monthly_eur"] == 300.0
    assert cuts[2] == {
        "category": "Groceries",
        "tier": "flexible",
        "monthly_eur": 1000.0,
        "cut_pct": 20.0,
        "saves_monthly_eur": 200.0,
    }


def test_job_loss_partial_loss_can_be_sustainable():
    out = ls.job_loss(_base(), income_lost_monthly_eur=500)
    level = out["levels"]["as_today"]
    assert level["monthly_gap_eur"] == -500.0
    assert level["months_on_cash"] is None
    assert level["months_with_investments"] is None
    assert level["sustainable"] is True


def test_job_loss_benefit_and_severance_extend_the_runway():
    out = ls.job_loss(_base(), benefit_monthly_eur=2000, benefit_months=6)
    assert out["levels"]["as_today"]["months_on_cash"] == 12
    out = ls.job_loss(_base(), severance_eur=4000)
    assert out["cash_available_eur"] == 16000.0
    assert out["levels"]["as_today"]["months_on_cash"] == 8


def test_job_loss_market_drop_shrinks_the_reserve():
    out = ls.job_loss(_base(), market_drop_pct=50)
    assert out["investments_after_tax_eur"] == 10000.0
    assert out["levels"]["as_today"]["months_with_investments"] == 11


def test_job_loss_taxes_gains_when_selling():
    with patch(
        "portf_manager.services.tax_rates.progressive_tax", lambda gain: gain * 0.2
    ):
        out = ls.job_loss(_base(unrealised_gain_eur=5000.0))
    assert out["investments_after_tax_eur"] == 19000.0


def test_job_loss_warns_about_unclassified_spending():
    base = _base()
    base["spending"]["by_tier_monthly_eur"]["unclassified"] = 250.0
    out = ls.job_loss(base)
    assert any("no fixed/flexible/optional mark" in w for w in out["warnings"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"flexible_cut_pct": 120},
        {"market_drop_pct": 100},
        {"benefit_monthly_eur": -1},
        {"income_lost_monthly_eur": -5},
    ],
)
def test_job_loss_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        ls.job_loss(_base(), **kwargs)


# ── income change ───────────────────────────────────────────────────────────


def test_income_change_still_positive_lists_cuts_to_keep_saving():
    out = ls.income_change(_base(), change_pct=-10, goals_planned_monthly_eur=700)
    assert out["new_income_monthly_eur"] == 3600.0
    assert out["net_monthly_before_eur"] == 1000.0
    assert out["net_monthly_after_eur"] == 600.0
    assert out["savings_rate_before_pct"] == 25.0
    assert out["savings_rate_after_pct"] == 16.7
    assert out["goals_still_fit"] is False
    keep = out["to_keep_saving_the_same"]
    assert keep["needed_monthly_eur"] == 400.0
    assert keep["available_from_cuts_monthly_eur"] == 700.0
    assert "shortfall" not in out


def test_income_change_into_deficit_gives_runway():
    out = ls.income_change(_base(), new_income_monthly_eur=2000)
    assert out["net_monthly_after_eur"] == -1000.0
    assert out["shortfall"]["levels"]["as_today"]["months_on_cash"] == 12


def test_income_change_plan_impact_lowers_the_contribution():
    out = ls.income_change(
        _base(),
        change_pct=-10,
        plan_params={"stocks_amount": 10000, "stocks_contribution_monthly": 500},
    )
    impact = out["plan_impact"]
    assert impact["contribution_before_monthly_eur"] == 500
    assert impact["contribution_after_monthly_eur"] == 100.0
    last = impact["milestones"][-1]
    assert last["net_worth_after_eur"] < last["net_worth_before_eur"]


def test_income_change_needs_exactly_one_input():
    with pytest.raises(ValueError):
        ls.income_change(_base())
    with pytest.raises(ValueError):
        ls.income_change(_base(), change_pct=-10, new_income_monthly_eur=1)


# ── house purchase ──────────────────────────────────────────────────────────


def test_house_purchase_funded_from_cash_then_investments():
    out = ls.house_purchase(
        _base(liquid_eur=100000.0, investments_eur=50000.0),
        price_eur=300000,
        rate_pct=0,
    )
    assert out["down_payment_eur"] == 60000.0
    assert out["purchase_costs_eur"] == 36000.0
    assert out["cash_needed_eur"] == 96000.0
    assert out["mortgage"]["principal_eur"] == 240000.0
    assert out["mortgage"]["monthly_payment_eur"] == 666.67
    assert out["monthly"]["spend_after_eur"] == 3666.67
    f = out["funding"]
    assert f["cash_buffer_kept_eur"] == 22000.0
    assert f["from_cash_eur"] == 78000.0
    assert f["investments_sold_eur"] == 18000.0
    assert f["shortfall_eur"] == 0.0
    assert out["after"]["liquid_eur"] == 22000.0
    assert out["after"]["emergency_months"] == 6.0
    assert out["after"]["net_worth_change_eur"] == -36000.0
    assert out["debt_ratio_pct"] == 16.7
    assert out["max_price_at_debt_limit_eur"] == 630000.0


def test_house_purchase_shortfall_is_flagged():
    out = ls.house_purchase(_base(), price_eur=300000, rate_pct=0)
    assert out["funding"]["shortfall_eur"] == 76000.0
    assert out["funding"]["investments_sold_eur"] == 20000.0
    assert any("Not enough money" in f for f in out["flags"])


def test_house_purchase_sale_and_repaid_loans_free_money():
    base = _base(liquid_eur=100000.0, loan_payments_monthly_eur=800.0)
    out = ls.house_purchase(
        base,
        price_eur=300000,
        rate_pct=0,
        sale_proceeds_eur=96000,
        existing_loans_repaid=True,
    )
    assert out["cash_needed_eur"] == 0.0
    assert out["monthly"]["freed_eur"] == 800.0
    assert out["monthly"]["spend_after_eur"] == 2866.67
    assert out["funding"]["from_cash_eur"] == 0.0


def test_house_purchase_flags_a_heavy_mortgage():
    out = ls.house_purchase(
        _base(liquid_eur=900000.0), price_eur=900000, rate_pct=0, term_years=20
    )
    assert out["debt_ratio_pct"] == 75.0
    assert any("above the 35%" in f for f in out["flags"])
    assert any("no longer closes" in f for f in out["flags"])


def test_gross_sale_covers_the_tax():
    with patch(
        "portf_manager.services.tax_rates.progressive_tax", lambda gain: gain * 0.2
    ):
        # half of every euro sold is gain, taxed at 20% -> 10% of the sale
        sold = ls.gross_sale_for(9000.0, 50000.0, 25000.0)
        assert sold == pytest.approx(10000.0, abs=0.01)
        assert ls.gross_sale_for(60000.0, 50000.0, 25000.0) is None
    assert ls.gross_sale_for(0.0, 50000.0, 25000.0) == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_eur": 0},
        {"price_eur": 1000, "down_payment_pct": 101},
        {"price_eur": 1000, "term_years": 0},
        {"price_eur": 1000, "purchase_costs_pct": 31},
        {"price_eur": 1000, "monthly_running_eur": -1},
    ],
)
def test_house_purchase_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        ls.house_purchase(_base(), **kwargs)


# ── crash recovery ──────────────────────────────────────────────────────────


def test_recovery_months():
    assert ls.recovery_months(100, 100, 6) == 0
    assert ls.recovery_months(100, 50, 0, 10) == 5
    assert ls.recovery_months(100, 50, 0) is None
    assert ls.recovery_months(100, 60, 6) == 106


def test_crash_recovery_band():
    out = ls.crash_recovery(100000, 40000, 6, 500)
    assert out["value_after_eur"] == 60000.0
    assert out["drop_pct"] == 40.0
    assert out["gain_needed_to_recover_pct"] == 66.7
    cases = {r["case"]: r for r in out["recovery"]}
    assert cases["expected"]["months_without_contributions"] == 106
    assert (
        cases["expected"]["months_with_contributions"]
        < cases["expected"]["months_without_contributions"]
    )
    assert (
        cases["high"]["months_without_contributions"]
        < cases["low"]["months_without_contributions"]
    )
    assert len(out["historical_reference"]) == 5


# ── database wiring, endpoints and chat tools ───────────────────────────────


@pytest.fixture
def db(tmp_path):
    """Six complete months: 3000 in, 1000 rent, 600 food, 200 fun, 300 invested."""
    db = Database(str(tmp_path / "scenarios.db"))
    bank = db.create_portfolio("Example Bank", account_type="bank")
    spend = db.find_spending_category_by_name("Spend")["id"]
    for name, level in (("Rent", "fixed"), ("Food", "flexible"), ("Fun", "optional")):
        db.create_spending_category(name, spend)
        db.set_spending_category_essentiality(name, level)
    db.create_spending_category("Invest", spend)
    balance = 20000.0
    for month in range(1, 7):
        for day, desc, amount, cat in (
            (1, "Example Employer", 3000.0, "Salary"),
            (2, "Example Landlord", -1000.0, "Rent"),
            (10, "Example Market", -600.0, "Food"),
            (15, "Example Cinema", -200.0, "Fun"),
            (20, "Example Broker", -300.0, "Invest"),
        ):
            balance += amount
            db.create_spending_transaction(
                bank,
                f"2026-{month:02d}-{day:02d}",
                desc,
                amount,
                category=cat,
                balance=balance,
            )
    broker = db.get_or_create_portfolio("Example Broker", base_currency="EUR")
    asset = db.create_asset("EXCO", "Example Corp", "stock", currency="EUR")
    db.create_transaction(
        asset_id=asset,
        transaction_type="buy",
        quantity=100,
        price=100,
        total_amount=10000,
        transaction_date="2026-01-02",
        portfolio_id=broker,
        currency="EUR",
    )
    db.create_price(asset, 100.0, "2026-07-01")
    return db


@pytest.fixture
def fx_patches():
    with (
        patch("portf_server.routers.portfolios._get_fx_rate", _fx),
        patch("portf_server.routers.portfolios._get_fx_rate_on", lambda d, c, o: 1.0),
        patch("portf_manager.services.exposure._default_fx", _fx),
        patch("portf_manager.services.life_scenarios.date") as fake_date,
        patch(
            "portf_manager.services.stress._resolve_sector_country",
            return_value=("Technology", "Germany"),
        ),
    ):
        fake_date.today.return_value = TODAY
        fake_date.side_effect = lambda *a, **k: date(*a, **k)
        yield


def test_household_baseline_from_the_database(db, fx_patches):
    base = ls.household_baseline(db, TODAY, _fx)
    p = base["spending"]
    assert len(p["months_used"]) == 6
    assert p["income_monthly_eur"] == 3000.0
    assert p["spend_monthly_eur"] == 1800.0
    assert p["savings_transfers_monthly_eur"] == 300.0
    assert p["by_tier_monthly_eur"] == {
        "fixed": 1000.0,
        "flexible": 600.0,
        "optional": 200.0,
        "unclassified": 0.0,
    }
    assert base["liquid_eur"] == 25400.0
    assert base["investments_eur"] == 10000.0
    assert base["unrealised_gain_eur"] == 0.0


def test_run_job_loss_on_the_database(db, fx_patches):
    out = ls.run_job_loss(db, TODAY, _fx)
    assert out["income_lost_monthly_eur"] == 3000.0
    assert out["levels"]["as_today"]["months_on_cash"] == 14
    assert out["levels"]["no_optional"]["months_on_cash"] == 15
    assert [c["category"] for c in out["cut_first"]] == ["Fun", "Food"]


def test_run_market_crash_on_the_database(db, fx_patches):
    out = ls.run_market_crash(db, drop_pct=40, today=TODAY, fx=_fx)
    assert out["loss_eur"] == 4000.0
    assert out["value_after_eur"] == 6000.0
    assert out["monthly_contribution_eur"] == 300.0
    assert out["monthly_contribution_source"].startswith("average monthly transfer")
    with pytest.raises(ValueError):
        ls.run_market_crash(db, today=TODAY, fx=_fx)
    with pytest.raises(ValueError):
        ls.run_market_crash(db, scenario="nope", today=TODAY, fx=_fx)


@pytest.fixture
def client(db, fx_patches):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    km = APIKeyManager(db)
    km.create_api_key(key_name="test", description="test key", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_scenario_endpoints(client):
    r = client.get("/api/v1/scenarios/baseline", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["spending"]["spend_monthly_eur"] == 1800.0

    r = client.get("/api/v1/scenarios/job-loss", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["levels"]["as_today"]["months_on_cash"] == 14

    r = client.get(
        "/api/v1/scenarios/income-change", params={"change_pct": -50}, headers=HEADERS
    )
    assert r.status_code == 200
    assert r.json()["net_monthly_after_eur"] == -300.0

    r = client.get(
        "/api/v1/scenarios/house-purchase",
        params={"price_eur": 100000, "rate_pct": 0},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["cash_needed_eur"] == 32000.0

    r = client.get(
        "/api/v1/scenarios/market-crash",
        params={"scenario": "ai_bubble"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["loss_eur"] == 6000.0


def test_scenario_endpoints_reject_bad_input(client):
    r = client.get("/api/v1/scenarios/income-change", params={}, headers=HEADERS)
    assert r.status_code == 422
    r = client.get(
        "/api/v1/scenarios/house-purchase", params={"price_eur": -1}, headers=HEADERS
    )
    assert r.status_code == 422
    r = client.get(
        "/api/v1/scenarios/market-crash", params={"scenario": "nope"}, headers=HEADERS
    )
    assert r.status_code == 422
    assert client.get("/api/v1/scenarios/job-loss").status_code in (401, 403)


def test_scenario_chat_tools(db, fx_patches):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.chat_tools._fx", _fx):
        out = json.loads(execute_tool("run_job_loss_scenario", {}, db))
        assert out["levels"]["lean"]["monthly_spend_eur"] == 1480.0

        out = json.loads(
            execute_tool("run_income_change_scenario", {"change_pct": -10}, db)
        )
        assert out["new_income_monthly_eur"] == 2700.0

        out = json.loads(
            execute_tool("run_house_purchase_scenario", {"price_eur": 100000}, db)
        )
        assert out["mortgage"]["principal_eur"] == 80000.0

        out = json.loads(execute_tool("run_market_crash_scenario", {}, db))
        assert out["scenario"] == "AI bubble bursts"

        bad = execute_tool("run_income_change_scenario", {}, db)
        assert bad.startswith("Error:")

        out = json.loads(execute_tool("get_spending_cut_candidates", {}, db))
        assert out["spend_by_tier_monthly_eur"]["optional"] == 200.0
        assert out["total_saves_monthly_eur"] == 320.0


def test_spending_matrix_tool(db):
    from portf_server.chat_tools import execute_tool

    with (
        patch("portf_server.chat_tools._fx", _fx),
        patch("datetime.date") as fake,
    ):
        fake.today.return_value = TODAY
        fake.side_effect = lambda *a, **k: date(*a, **k)
        out = json.loads(execute_tool("get_spending_matrix", {"months": 3}, db))
        assert out["months"] == ["2026-05", "2026-06"]
        assert out["rows"][0] == {
            "category": "Rent",
            "by_month_eur": {"2026-05": 1000.0, "2026-06": 1000.0},
            "total_eur": 2000.0,
        }
        assert out["total_by_month_eur"]["2026-06"] == 2100.0
        assert out["total_eur"] == 4200.0

        out = json.loads(
            execute_tool(
                "get_spending_matrix",
                {"months": 3, "group_by": "merchant", "category": "Food"},
                db,
            )
        )
        assert len(out["rows"]) == 1 and out["rows"][0]["total_eur"] == 1200.0

        assert execute_tool("get_spending_matrix", {"group_by": "x"}, db).startswith(
            "Error:"
        )


def test_search_totals_cover_every_matching_row(db):
    from portf_server.chat_tools import execute_tool

    with patch("portf_server.chat_tools._fx", _fx):
        out = json.loads(
            execute_tool(
                "search_spending_transactions", {"category": "Food", "limit": 2}, db
            )
        )
    assert out["count"] == 2
    assert out["total_matching"] == 6
    assert out["total_matching_amount_eur"] == -3600.0


# ── irregular spending and exclusions ───────────────────────────────────────


def _year_rows():
    """Twelve months of rent plus one large roof repair in March."""
    rows = []
    for month in range(1, 13):
        rows.append(
            {"date": f"2025-{month:02d}-02", "amount_eur": -1000.0, "category": "Rent"}
        )
    rows.append({"date": "2025-03-10", "amount_eur": -6000.0, "category": "Roof"})
    rows.append({"date": "2025-03-11", "amount_eur": -600.0, "category": "Roofer"})
    return rows


_YEAR_TREE = {
    "Spend": {"name": "Spend", "parent_name": None, "essentiality": None},
    "Rent": {"name": "Rent", "parent_name": "Spend", "essentiality": "fixed"},
    "Roof": {"name": "Roof", "parent_name": "Spend", "essentiality": None},
    "Roofer": {"name": "Roofer", "parent_name": "Roof", "essentiality": None},
}


def test_one_off_spending_is_flagged_irregular():
    p = ls.spending_profile(_year_rows(), _YEAR_TREE, date(2026, 1, 15))
    cats = {c["name"]: c for c in p["categories"]}
    assert cats["Roof"]["irregular"] is True
    assert cats["Roof"]["months_with_spending"] == 1
    assert cats["Rent"]["irregular"] is False
    assert p["spend_monthly_eur"] == 1550.0


def test_irregular_needs_six_months_of_data():
    rows = [r for r in _year_rows() if r["date"] < "2025-05"]
    p = ls.spending_profile(rows, _YEAR_TREE, date(2025, 5, 15))
    assert all(c["irregular"] is False for c in p["categories"])


def test_excluded_categories_take_their_children_and_ignore_case():
    p = ls.spending_profile(
        _year_rows(), _YEAR_TREE, date(2026, 1, 15), exclude=["roof", " "]
    )
    assert p["spend_monthly_eur"] == 1000.0
    assert p["excluded_categories"] == ["Roof"]
    assert p["excluded_monthly_eur"] == 550.0
    assert [c["name"] for c in p["categories"]] == ["Rent"]


def test_exclude_through_the_database_api_and_tools(client, db):
    r = client.get(
        "/api/v1/scenarios/job-loss",
        params={"exclude_categories": "Fun, Food"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["levels"]["as_today"]["monthly_spend_eur"] == 1000.0
    assert any("Left out on request: Food, Fun" in w for w in data["warnings"])

    r = client.get(
        "/api/v1/scenarios/baseline",
        params={"exclude_categories": "Fun"},
        headers=HEADERS,
    )
    assert r.json()["spending"]["excluded_monthly_eur"] == 200.0

    from portf_server.chat_tools import execute_tool

    with patch("portf_server.chat_tools._fx", _fx):
        out = json.loads(
            execute_tool(
                "get_spending_cut_candidates", {"exclude_categories": "Fun"}, db
            )
        )
        assert out["excluded_categories"] == ["Fun"]
        assert out["spend_monthly_eur"] == 1600.0
        out = json.loads(
            execute_tool(
                "run_house_purchase_scenario",
                {"price_eur": 100000, "exclude_categories": ["Fun"]},
                db,
            )
        )
        assert out["monthly"]["spend_before_eur"] == 1600.0
