"""Unit tests for the budget service's pure logic and variance aggregation."""

import json

import pytest

from portf_manager.database import Database
from portf_manager.services import budget as bs


# ── Pure helpers ───────────────────────────────────────────────────────────


def test_parse_overrides_handles_every_bad_input():
    assert bs.parse_overrides(None) == {}
    assert bs.parse_overrides("") == {}
    assert bs.parse_overrides("not json") == {}
    # A JSON array is valid JSON but the wrong shape.
    assert bs.parse_overrides("[1, 2]") == {}
    # Non-numeric values are dropped, the rest survives.
    assert bs.parse_overrides('{"2026-03": 450, "2026-04": "nope"}') == {
        "2026-03": 450.0
    }


def test_serialize_overrides_round_trips_and_nulls_empty():
    assert bs.serialize_overrides(None) is None
    assert bs.serialize_overrides({}) is None
    raw = bs.serialize_overrides({"2026-03": 450})
    assert bs.parse_overrides(raw) == {"2026-03": 450.0}


def test_month_range_counts_back_and_crosses_years():
    assert bs.month_range("2026-02", 3) == ["2025-12", "2026-01", "2026-02"]
    assert bs.month_range("2026-05", 1) == ["2026-05"]
    assert bs.month_range("2026-01", 0) == []
    fourteen = bs.month_range("2026-01", 14)
    assert fourteen[0] == "2024-12" and fourteen[-1] == "2026-01"
    assert len(fourteen) == 14


def test_month_end_handles_february_and_december():
    assert bs._month_end("2026-02") == "2026-02-28"
    assert bs._month_end("2024-02") == "2024-02-29"
    assert bs._month_end("2026-12") == "2026-12-31"
    assert bs._month_end("2026-04") == "2026-04-30"


def test_planned_for_months_applies_overrides_only_where_given():
    planned = bs.planned_for_months(400, {"2026-03": 550}, ["2026-02", "2026-03"])
    assert planned == {"2026-02": 400.0, "2026-03": 550.0}


def test_variance_sign_is_favourable_positive_in_both_directions():
    # Spending: under plan is good.
    over = bs.variance(400, 500, favourable_when_under=True)
    assert over["variance_eur"] == -100 and over["favourable"] is False
    under = bs.variance(400, 300, favourable_when_under=True)
    assert under["variance_eur"] == 100 and under["favourable"] is True
    # Income: over plan is good.
    earned_more = bs.variance(3000, 3100, favourable_when_under=False)
    assert earned_more["variance_eur"] == 100 and earned_more["favourable"] is True


def test_variance_pct_is_none_when_nothing_was_planned():
    # A percentage against a zero base is meaningless — callers render a dash.
    assert bs.variance(0, 50, True)["variance_pct"] is None


# ── Tree helpers ───────────────────────────────────────────────────────────

TREE = [
    {"name": "Spend", "parent_name": None, "is_root": 1},
    {"name": "Housing", "parent_name": "Spend", "is_root": 0},
    {"name": "Rent", "parent_name": "Housing", "is_root": 0},
    {"name": "Utilities", "parent_name": "Housing", "is_root": 0},
    {"name": "Groceries", "parent_name": "Spend", "is_root": 0},
    {"name": "Income", "parent_name": None, "is_root": 1},
    {"name": "Salary", "parent_name": "Income", "is_root": 0},
]


def test_subtree_names_collects_the_whole_branch():
    index = bs.build_children_index(TREE)
    assert sorted(bs.subtree_names(index, "Housing")) == [
        "Housing",
        "Rent",
        "Utilities",
    ]
    assert bs.subtree_names(index, "Groceries") == ["Groceries"]


def test_is_ancestor_walks_up_only():
    assert bs.is_ancestor(TREE, "Housing", "Rent") is True
    assert bs.is_ancestor(TREE, "Spend", "Rent") is True
    assert bs.is_ancestor(TREE, "Rent", "Housing") is False
    assert bs.is_ancestor(TREE, "Groceries", "Rent") is False


def test_coverage_conflict_catches_both_directions_and_self():
    assert bs.coverage_conflict(TREE, ["Housing"], "Rent") == "Housing"
    assert bs.coverage_conflict(TREE, ["Rent"], "Housing") == "Rent"
    assert bs.coverage_conflict(TREE, ["Groceries"], "Groceries") == "Groceries"
    assert bs.coverage_conflict(TREE, ["Groceries"], "Rent") is None
    assert bs.coverage_conflict(TREE, [], "Rent") is None


def test_category_path_renders_a_breadcrumb():
    assert bs.category_path(TREE, "Rent") == "Spend > Housing > Rent"
    assert bs.category_path(TREE, "Spend") == "Spend"
    assert bs.category_path(TREE, "unknown") == "unknown"


def test_uncovered_rollup_stops_below_anything_already_budgeted():
    by_name = {n["name"]: n for n in TREE}
    # Nothing budgeted: everything rolls up to a direct child of the root.
    assert bs.uncovered_rollup_key(by_name, set(), "Rent") == "Housing"
    # Rent budgeted: a stray Utilities charge stays Utilities rather than being
    # folded back into the partly-budgeted Housing.
    blocked = bs.budgeted_or_above(by_name, {"Rent"})
    assert bs.uncovered_rollup_key(by_name, blocked, "Utilities") == "Utilities"
    # A category outside the tree reports under its own name.
    assert bs.uncovered_rollup_key(by_name, set(), "uncategorized") == "uncategorized"


def test_budgeted_or_above_collects_ancestors():
    by_name = {n["name"]: n for n in TREE}
    assert bs.budgeted_or_above(by_name, {"Rent"}) == {"Rent", "Housing", "Spend"}
    assert bs.budgeted_or_above(by_name, set()) == set()


def test_tree_walks_survive_a_cycle():
    # reparent_spending_category prevents cycles, but a walk must not hang if
    # one ever slips through.
    cyclic = [
        {"name": "A", "parent_name": "B", "is_root": 0},
        {"name": "B", "parent_name": "A", "is_root": 0},
    ]
    assert bs.is_ancestor(cyclic, "C", "A") is False
    assert bs.category_path(cyclic, "A").endswith("A")
    index = bs.build_children_index(cyclic)
    assert len(bs.subtree_names(index, "A")) <= bs._MAX_TREE_DEPTH + 2


# ── Variance over a real database ──────────────────────────────────────────


def _fx(currency):
    """Fixed rates, so the aggregation tests never touch the network."""
    return {"EUR": 1.0, "USD": 0.5}.get(currency, 1.0)


@pytest.fixture
def seeded(tmp_path):
    """A bank account, a broker, a category tree and two months of activity."""
    db = Database(str(tmp_path / "budget_service.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    broker = db.get_or_create_portfolio("Example Broker")

    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    housing = db.create_spending_category("Housing", parent_id=spend)
    db.create_spending_category("Rent", parent_id=housing)
    db.create_spending_category("Utilities", parent_id=housing)
    db.create_spending_category("Groceries", parent_id=spend)
    db.create_spending_category("Salary", parent_id=income)

    for month in ("2026-07", "2026-08"):
        db.create_spending_transaction(
            bank, f"{month}-05", "Rent", -900.0, category="Rent"
        )
        db.create_spending_transaction(
            bank, f"{month}-06", "Power co", -120.0, category="Utilities"
        )
        db.create_spending_transaction(
            bank, f"{month}-10", "Supermarket", -410.0, category="Groceries"
        )
        db.create_spending_transaction(
            bank, f"{month}-12", "Odd thing", -60.0, category="uncategorized"
        )
        db.create_spending_transaction(
            bank, f"{month}-01", "Payroll", 3000.0, category="Salary"
        )
        db.create_booking(f"{month}-15", "Deposit", 500.0, "EUR", portfolio_id=broker)
    return db, bank, broker


def _section(result, key):
    return next(s for s in result["sections"] if s["key"] == key)


def _recent_complete_months(count):
    """The last `count` complete months, so seeding tests never depend on today."""
    from datetime import date

    today = date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        month, year = 12, year - 1
    return bs.month_range(f"{year:04d}-{month:02d}", count)


def test_variance_sums_a_subtree_and_applies_overrides(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(
        budget_id, "spending", "Housing", 1000.0, json.dumps({"2026-08": 1100.0})
    )
    result = bs.compute_budget_variance(db, budget_id, ["2026-07", "2026-08"], _fx)

    line = _section(result, "spending")["lines"][0]
    assert line["label"] == "Spend > Housing"
    # Rent + Utilities, both months.
    assert line["actual_eur"] == {"2026-07": 1020.0, "2026-08": 1020.0}
    assert line["planned_eur"] == {"2026-07": 1000.0, "2026-08": 1100.0}
    assert line["actual_total"] == 2040.0 and line["planned_total"] == 2100.0
    assert line["variance_eur"] == 60.0 and line["favourable"] is True


def test_variance_reports_uncovered_categories_as_unbudgeted(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Housing", 1000.0)
    section = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07", "2026-08"], _fx),
        "spending",
    )

    labels = {u["ref_key"]: u["actual_total"] for u in section["unbudgeted"]}
    assert labels == {"Groceries": 820.0, "uncategorized": 120.0}
    # The section total counts unbudgeted spend, so "under budget" can't be an
    # artifact of leaving half the spending unbudgeted.
    assert section["actual_total"] == 2040.0 + 820.0 + 120.0
    assert section["planned_total"] == 2000.0
    assert section["favourable"] is False


def test_budgeting_a_leaf_keeps_its_sibling_visible_as_unbudgeted(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Rent", 900.0)
    section = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "spending"
    )
    keys = {u["ref_key"] for u in section["unbudgeted"]}
    # Utilities is named directly rather than rolled into the partly-budgeted
    # Housing, so the fix is obvious: add a Utilities line.
    assert "Utilities" in keys and "Housing" not in keys


def test_income_and_investment_are_favourable_when_over_plan(seeded):
    db, _bank, broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "income", "Salary", 2800.0)
    db.create_budget_line(budget_id, "investment", str(broker), 400.0)
    result = bs.compute_budget_variance(db, budget_id, ["2026-07", "2026-08"], _fx)

    income = _section(result, "income")["lines"][0]
    assert income["actual_total"] == 6000.0 and income["favourable"] is True
    investment = _section(result, "investment")["lines"][0]
    assert investment["label"] == "Example Broker"
    assert investment["actual_total"] == 1000.0 and investment["favourable"] is True


def test_investment_actual_is_net_of_withdrawals(seeded):
    db, _bank, broker = seeded
    db.create_booking("2026-07-20", "Withdrawal", 200.0, "EUR", portfolio_id=broker)
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "investment", str(broker), 400.0)
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)
    assert _section(result, "investment")["lines"][0]["actual_total"] == 300.0


def test_transfers_are_excluded_from_actuals(seeded):
    db, bank, _broker = seeded
    transfer_id = db.create_spending_transaction(
        bank, "2026-07-18", "To broker", -500.0, category="Transfer"
    )
    db.update_spending_transaction(transfer_id, is_transfer=1)
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    section = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "spending"
    )
    # 900 rent + 120 utilities + 60 uncategorized + 410 groceries, no transfer.
    assert section["actual_total"] == 1490.0


def test_foreign_currency_rows_are_converted(seeded):
    db, bank, _broker = seeded
    db.create_spending_transaction(
        bank, "2026-07-11", "US shop", -100.0, currency="USD", category="Groceries"
    )
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    line = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "spending"
    )["lines"][0]
    assert line["actual_total"] == 410.0 + 50.0


def test_debt_shares_the_spend_tree_and_never_double_reports(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "debt", "Housing", 1000.0)
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)
    debt = _section(result, "debt")
    spending = _section(result, "spending")
    assert debt["lines"][0]["actual_total"] == 1020.0
    # Uncovered spend is reported once, in the Spending section.
    assert debt["unbudgeted"] == []
    assert {u["ref_key"] for u in spending["unbudgeted"]} == {
        "Groceries",
        "uncategorized",
    }


def test_net_compares_income_against_every_outflow(seeded):
    db, _bank, broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "income", "Salary", 3000.0)
    db.create_budget_line(budget_id, "spending", "Housing", 1000.0)
    db.create_budget_line(budget_id, "investment", str(broker), 400.0)
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)
    # planned 3000 - (1000 + 400) = 1600
    assert result["net"]["planned_total"] == 1600.0
    # actual 3000 - (1020 + 410 + 60 unbudgeted spend + 500 invested) = 1010
    assert result["net"]["actual_total"] == 1010.0
    assert result["net"]["favourable"] is False


def test_variance_on_a_budget_with_no_lines_is_all_unbudgeted(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Empty")
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)
    spending = _section(result, "spending")
    assert spending["planned_total"] == 0
    assert spending["actual_total"] == 1490.0
    assert {u["ref_key"] for u in spending["unbudgeted"]} == {
        "Housing",
        "Groceries",
        "uncategorized",
    }


def test_unbudgeted_is_attributed_by_sign_not_by_tree_position(seeded):
    """Cross-sign rows must land somewhere, not vanish.

    Regression: attributing unbudgeted actuals by the category's tree root
    silently dropped a refund in a Spend category and a charge in an Income
    category, so the section totals stopped reconciling with
    /api/v1/spending/trend.
    """
    db, bank, _broker = seeded
    # A refund in a Spend-rooted category: income by sign.
    db.create_spending_transaction(
        bank, "2026-07-20", "Supermarket refund", 25.0, category="Groceries"
    )
    # A charge in an Income-rooted category: spend by sign.
    db.create_spending_transaction(
        bank, "2026-07-21", "Payroll correction", -30.0, category="Salary"
    )
    budget_id = db.create_budget("Base")
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)

    income_unbudgeted = {
        u["ref_key"]: u["actual_total"]
        for u in _section(result, "income")["unbudgeted"]
    }
    spend_unbudgeted = {
        u["ref_key"]: u["actual_total"]
        for u in _section(result, "spending")["unbudgeted"]
    }
    assert income_unbudgeted["Groceries"] == 25.0
    assert spend_unbudgeted["Salary"] == 30.0


def test_an_unfiled_category_still_reports(seeded):
    """A parentless, non-root tree node has no root — it must not be dropped.

    Regression: "uncategorized" exists in a real database as exactly this
    shape (parent_id NULL, is_root 0), and a root-based filter discarded every
    euro booked against it.
    """
    db, bank, _broker = seeded
    db.create_spending_category("Unfiled", parent_id=None)
    db.create_spending_transaction(
        bank, "2026-07-22", "Mystery charge", -75.0, category="Unfiled"
    )
    budget_id = db.create_budget("Base")
    section = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "spending"
    )
    assert {u["ref_key"]: u["actual_total"] for u in section["unbudgeted"]}[
        "Unfiled"
    ] == 75.0


def test_section_totals_reconcile_with_the_spending_trend_convention(seeded):
    """Every non-transfer euro in the period lands in exactly one section.

    This is the invariant that lets the Budget page and the Spending page's
    trend chart agree: spending + debt actuals equal total outflow, income
    actuals equal total inflow, both measured by sign.
    """
    db, bank, _broker = seeded
    db.create_spending_transaction(
        bank, "2026-07-20", "Supermarket refund", 25.0, category="Groceries"
    )
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Housing", 1000.0)
    db.create_budget_line(budget_id, "income", "Salary", 3000.0)
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)

    rows = db.list_spending_transactions(
        start_date="2026-07-01", end_date="2026-07-31", is_transfer=False
    )
    expected_out = sum(
        abs(float(r["amount"]) * _fx(r["currency"])) for r in rows if r["amount"] < 0
    )
    expected_in = sum(
        float(r["amount"]) * _fx(r["currency"]) for r in rows if r["amount"] > 0
    )
    actual_out = (
        _section(result, "spending")["actual_total"]
        + _section(result, "debt")["actual_total"]
    )
    assert round(actual_out, 2) == round(expected_out, 2)
    assert round(_section(result, "income")["actual_total"], 2) == round(expected_in, 2)


def test_is_broker_ref_tells_the_two_keyings_apart():
    assert bs.is_broker_ref("7") is True
    assert bs.is_broker_ref(7) is True
    assert bs.is_broker_ref("Invest") is False
    # Every non-investment line resolves against the category tree.
    assert bs.line_uses_category("spending", "Groceries") is True
    assert bs.line_uses_category("investment", "Invest") is True
    assert bs.line_uses_category("investment", "7") is False


def test_a_category_keyed_investment_line_reads_bank_outflows(seeded):
    """The point of the feature: money moved out isn't money spent.

    A category-keyed investment line measures the same bank rows a spending
    line would, but reports them under Investments so they stop inflating the
    spending total.
    """
    db, bank, _broker = seeded
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Pension", parent_id=spend)
    db.create_spending_transaction(
        bank, "2026-07-01", "Monthly pension contribution", -125.0, category="Pension"
    )
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "investment", "Pension", 125.0)
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)

    investment = _section(result, "investment")
    assert investment["lines"][0]["label"] == "Spend > Pension"
    assert investment["lines"][0]["actual_total"] == 125.0
    # ...and it is NOT also counted as spending, nor surfaced as unbudgeted
    # spending, which would double it.
    spending = _section(result, "spending")
    assert "Pension" not in {u["ref_key"] for u in spending["unbudgeted"]}
    assert spending["actual_total"] == 1490.0


def test_a_bank_side_line_suppresses_leftover_broker_deposits(seeded):
    """A section reports one measurement basis, never the sum of two.

    The bank outflow to a broker and that broker's deposit are the same euros;
    adding both doubles the total (and broker deposits are inflated further by
    moves between the user's own accounts).
    """
    db, bank, broker = seeded
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Invest", parent_id=spend)
    db.create_spending_transaction(
        bank, "2026-07-02", "Transfer to broker", -500.0, category="Invest"
    )
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "investment", "Invest", 500.0)
    section = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "investment"
    )
    # The seeded fixture has a 500 EUR Deposit booking for this month; it must
    # not be added on top of the bank-side line.
    assert section["unbudgeted"] == []
    assert section["unbudgeted_suppressed"] is True
    assert section["actual_total"] == 500.0

    # With no bank-side line, the broker side is the basis and shows through.
    broker_only = db.create_budget("Broker basis")
    broker_section = _section(
        bs.compute_budget_variance(db, broker_only, ["2026-07"], _fx), "investment"
    )
    assert broker_section["unbudgeted_suppressed"] is False
    assert broker_section["unbudgeted"][0]["label"] == "Example Broker"


def test_a_broker_keyed_line_still_reads_bookings(seeded):
    db, _bank, broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "investment", str(broker), 400.0)
    line = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "investment"
    )["lines"][0]
    assert line["label"] == "Example Broker"
    assert line["actual_total"] == 500.0


def test_seeding_proposes_a_root_line_when_the_root_holds_most_of_the_money(tmp_path):
    """Regression: income booked against the bare "Income" root was skipped.

    Seeding only walked a root's children, so an account with nearly all its
    income filed directly against "Income" got a budget an order of magnitude
    too small.
    """
    db = Database(str(tmp_path / "root_seed.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    income = db.find_spending_category_by_name("Income")["id"]
    db.create_spending_category("Other", parent_id=income)

    period = _recent_complete_months(3)
    for month in period:
        # Most of the money against the root itself, a little in a child.
        db.create_spending_transaction(
            bank, f"{month}-01", "Payroll", 3000.0, category="Income"
        )
        db.create_spending_transaction(
            bank, f"{month}-15", "Odd credit", 50.0, category="Other"
        )

    proposals = [
        p for p in bs.propose_budget_lines(db, 3, _fx) if p["line_type"] == "income"
    ]
    assert len(proposals) == 1
    assert proposals[0]["ref_key"] == "Income"
    assert proposals[0]["monthly_amount"] == 3050.0


def test_seeding_still_prefers_children_when_they_hold_the_money(tmp_path):
    db = Database(str(tmp_path / "child_seed.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    income = db.find_spending_category_by_name("Income")["id"]
    db.create_spending_category("Salary", parent_id=income)

    for month in _recent_complete_months(3):
        db.create_spending_transaction(
            bank, f"{month}-01", "Payroll", 3000.0, category="Salary"
        )
        db.create_spending_transaction(
            bank, f"{month}-20", "Stray credit", 10.0, category="Income"
        )

    proposals = [
        p for p in bs.propose_budget_lines(db, 3, _fx) if p["line_type"] == "income"
    ]
    assert {p["ref_key"] for p in proposals} == {"Salary"}


def test_months_without_activity_names_only_the_empty_months(seeded):
    db, bank, _broker = seeded
    budget_id = db.create_budget("Base")
    db.create_budget_line(budget_id, "spending", "Housing", 1000.0)
    result = bs.compute_budget_variance(
        db, budget_id, ["2026-07", "2026-08", "2026-09"], _fx
    )
    # The fixture has activity in July and August, none in September.
    assert bs.months_without_activity(result) == ["2026-09"]
    assert bank  # fixture guard


def test_months_without_activity_counts_unbudgeted_rows_as_activity(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Empty")
    # No lines at all, so every euro lands in `unbudgeted` -- which is still
    # activity, and must not read as an un-imported month.
    result = bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx)
    assert bs.months_without_activity(result) == []


def test_months_without_activity_tolerates_an_empty_report():
    assert bs.months_without_activity({}) == []
    assert bs.months_without_activity({"months": ["2026-07"], "sections": []}) == [
        "2026-07"
    ]


def test_variance_raises_for_an_unknown_budget(seeded):
    db, _bank, _broker = seeded
    with pytest.raises(ValueError):
        bs.compute_budget_variance(db, 999, ["2026-07"], _fx)


def test_malformed_overrides_fall_back_to_the_monthly_amount(seeded):
    db, _bank, _broker = seeded
    budget_id = db.create_budget("Base")
    line_id = db.create_budget_line(budget_id, "spending", "Groceries", 400.0)
    db.update_budget_line(line_id, overrides="{not json")
    line = _section(
        bs.compute_budget_variance(db, budget_id, ["2026-07"], _fx), "spending"
    )["lines"][0]
    assert line["planned_total"] == 400.0


def test_seed_proposals_average_complete_months_and_skip_empty_categories(tmp_path):
    from datetime import date

    db = Database(str(tmp_path / "seed.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    broker = db.get_or_create_portfolio("Example Broker")
    spend = db.find_spending_category_by_name("Spend")["id"]
    income = db.find_spending_category_by_name("Income")["id"]
    db.create_spending_category("Groceries", parent_id=spend)
    db.create_spending_category("Salary", parent_id=income)
    # A category with no activity at all must not be proposed.
    db.create_spending_category("Hobbies", parent_id=spend)

    # Three complete months ending last month, so "today" never matters.
    today = date.today()
    end_year, end_month = today.year, today.month - 1
    if end_month == 0:
        end_month, end_year = 12, end_year - 1
    period = bs.month_range(f"{end_year:04d}-{end_month:02d}", 3)
    for month in period:
        db.create_spending_transaction(
            bank, f"{month}-10", "Supermarket", -300.0, category="Groceries"
        )
        db.create_spending_transaction(
            bank, f"{month}-01", "Payroll", 2400.0, category="Salary"
        )
        db.create_booking(f"{month}-15", "Deposit", 150.0, "EUR", portfolio_id=broker)

    proposals = {
        (p["line_type"], p["ref_key"]): p for p in bs.propose_budget_lines(db, 3, _fx)
    }
    assert proposals[("spending", "Groceries")]["monthly_amount"] == 300.0
    assert proposals[("spending", "Groceries")]["months_seen"] == 3
    assert proposals[("income", "Salary")]["monthly_amount"] == 2400.0
    assert proposals[("investment", str(broker))]["monthly_amount"] == 150.0
    assert ("spending", "Hobbies") not in proposals


def test_seed_proposals_are_empty_without_activity(tmp_path):
    db = Database(str(tmp_path / "seed_empty.db"))
    assert bs.propose_budget_lines(db, 12, _fx) == []
