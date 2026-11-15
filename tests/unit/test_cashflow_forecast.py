"""Cash-flow forecast: this month, the next months, balance and degradation."""

from datetime import date

import pytest

from portf_manager.database import Database
from portf_manager.services import cashflow_forecast as cf
from portf_manager.services.recurring import detect_recurring

TODAY = date(2026, 7, 15)
THROUGH = date(2026, 7, 10)

_ids = iter(range(1, 100000))


def _fx(currency):
    return 1.0


def _row(day, amount, category, merchant, one_off=False, pid=1):
    return {
        "id": next(_ids),
        "portfolio_id": pid,
        "date": day,
        "description": merchant,
        "merchant": merchant,
        "category": category,
        "amount_eur": float(amount),
        "one_off": one_off,
        "is_transfer": False,
    }


def _tree():
    names = [
        ("Spend", None, None),
        ("Income", None, None),
        ("Salary", "Income", None),
        ("Groceries", "Spend", "flexible"),
        ("Rent", "Spend", "fixed"),
        ("Gym", "Spend", "optional"),
        ("Insurance", "Spend", "fixed"),
        ("Invest", "Spend", None),
        ("Furniture", "Spend", "optional"),
    ]
    return {n: {"name": n, "parent_name": p, "essentiality": e} for n, p, e in names}


def _rows(salary=True):
    rows = []
    # a yearly insurance bill: two charges a year apart, next due 2026-08-20
    rows.append(_row("2024-08-20", -600, "Insurance", "Example Insurer"))
    rows.append(_row("2025-08-20", -600, "Insurance", "Example Insurer"))
    for m in range(1, 8):
        rows.append(_row(f"2026-{m:02d}-01", -1000, "Rent", "Example Landlord"))
    for m in range(4, 7):
        rows.append(_row(f"2026-{m:02d}-20", -30, "Gym", "Example Gym"))
    # varying amounts, so groceries are everyday spending, not a "bill"
    for m, groceries in zip(range(1, 7), (250, 550, 300, 500, 350, 450)):
        rows.append(_row(f"2026-{m:02d}-12", -groceries, "Groceries", "Example Market"))
        if salary:
            rows.append(_row(f"2026-{m:02d}-25", 3000, "Salary", "Example Employer"))
    # a one-off in March must not lift the baseline
    rows.append(_row("2026-03-14", -500, "Furniture", "Example Sofa", one_off=True))
    rows.append(_row("2026-07-08", -150, "Groceries", "Example Market"))
    return rows


def _series(rows):
    plain = [
        {
            "id": r["id"],
            "portfolio_id": r["portfolio_id"],
            "date": r["date"],
            "amount": r["amount_eur"],
            "currency": "EUR",
            "merchant": r["merchant"],
            "description": r["description"],
            "category": r["category"],
            "is_transfer": 0,
        }
        for r in rows
        if r["amount_eur"] < 0
    ]
    return detect_recurring(plain, {1: THROUGH})


def _account(balance=5000.0, name="Example Bank", pid=1, through=THROUGH):
    return {
        "portfolio_id": pid,
        "name": name,
        "currency": "EUR",
        "data_through": through,
        "balance_eur": balance,
        "balance_date": through.isoformat() if through else None,
    }


def _forecast(rows=None, accounts=None, series="auto", tree="auto", months=3):
    rows = _rows() if rows is None else rows
    return cf.compute_forecast(
        rows,
        _tree() if tree == "auto" else tree,
        _series(rows) if series == "auto" else series,
        [_account()] if accounts is None else accounts,
        _fx,
        TODAY,
        months,
    )


# ── the invariants ──────────────────────────────────────────────────────────


def test_recurring_charge_due_later_this_month_is_counted_once():
    out = _forecast()
    this = out["this_month"]
    # the gym (30, due the 20th) is a recurring charge: it is in the remaining
    # recurring figure ...
    assert this["recurring_remaining_eur"] == 30.0
    # ... and NOT in the variable baseline, which is the groceries only
    # (the one-off sofa is left out as well)
    assert out["baseline"]["variable_eur"]["avg"] == 400.0
    # 150 + 1000 rent so far, 30 gym once, 400 x 21/31 of everyday spending
    assert this["spent_to_date_eur"] == 1150.0
    assert this["variable_remaining_eur"] == pytest.approx(400 * 21 / 31, abs=0.01)
    assert this["projected_spending_eur"] == pytest.approx(
        1150 + 30 + 400 * 21 / 31, abs=0.01
    )


def test_income_is_the_baseline_less_what_already_arrived():
    out = _forecast()
    this = out["this_month"]
    assert out["baseline"]["income_eur"]["avg"] == 3000.0
    # no salary yet in July
    assert this["income_remaining_eur"] == 3000.0
    assert this["projected_net_eur"] == pytest.approx(
        3000 - (1150 + 30 + 400 * 21 / 31), abs=0.01
    )


def test_next_months_place_bills_on_their_due_months():
    out = _forecast()
    aug, sep, _oct = out["next_months"]
    assert aug["month"] == "2026-08"
    # rent + gym + the yearly insurance only land in August
    assert aug["recurring_eur"] == 1000 + 30 + 600
    assert sep["recurring_eur"] == 1000 + 30
    assert aug["variable_eur"] == 400.0
    assert aug["net_eur"] == 3000 - 1630 - 400
    bills = {(b["date"], b["merchant"]) for b in out["known_bills"]}
    assert ("2026-08-20", "Example Insurer") in bills
    assert ("2026-07-20", "Example Gym") in bills
    assert all(b["amount_eur"] > 0 for b in out["known_bills"])


def test_balance_chain_starts_from_the_stored_balance():
    out = _forecast()
    this = out["this_month"]
    # 5000 + 3000 salary still to come - 30 gym - everyday spending left
    assert this["projected_balance_end_eur"] == pytest.approx(
        5000 + 3000 - 30 - 400 * 21 / 31, abs=0.01
    )
    aug = out["next_months"][0]
    assert aug["projected_balance_eur"] == pytest.approx(
        this["projected_balance_end_eur"] + 970, abs=0.02
    )
    assert out["first_below_zero"] is None


def test_balance_below_zero_is_flagged_on_the_month():
    rows = _rows(salary=False)
    out = _forecast(rows=rows, accounts=[_account(balance=500.0)])
    assert out["baseline"]["income_eur"]["avg"] == 0.0
    flagged = [m["month"] for m in out["next_months"] if m["balance_below_zero"]]
    assert flagged[0] == "2026-08" == out["first_below_zero"]
    assert out["this_month"]["projected_balance_end_eur"] > 0


def test_one_off_rows_stay_out_of_the_baseline_but_in_the_actuals():
    rows = _rows()
    # a one-off sofa this month still counts in what was spent so far
    rows.append(_row("2026-07-09", -300, "Furniture", "Example Sofa", one_off=True))
    out = _forecast(rows=rows)
    assert out["this_month"]["spent_to_date_eur"] == 1450.0
    assert out["baseline"]["variable_eur"]["avg"] == 400.0


def test_savings_are_not_spending():
    rows = _rows()
    for m in range(1, 7):
        rows.append(_row(f"2026-{m:02d}-26", -200, "Invest", "Example Broker"))
    out = _forecast(rows=rows, months=1)
    # recurring monthly 200 to Invest: shown as savings, not spending
    aug = out["next_months"][0]
    assert aug["savings_eur"] == 200.0
    assert aug["spending_eur"] == 1630 + 400
    assert aug["balance_change_eur"] == aug["net_eur"] - 200.0
    assert out["baseline"]["savings_eur"]["avg"] == 0.0


# ── data through, balances and degradation ──────────────────────────────────


def test_stale_account_warns_and_the_month_is_measured_from_data_through():
    out = _forecast(accounts=[_account(through=date(2026, 7, 1))])
    [acc] = out["accounts"]
    assert acc["stale"] is True and acc["days_behind"] == 14
    assert any("14 days" in w for w in out["warnings"])
    assert out["data_through"] == "2026-07-01"
    # 30 of 31 days still ahead of the data, not 16 of 31 as "today" would say
    assert out["this_month"]["elapsed_share"] == pytest.approx(0.032, abs=0.001)


def test_account_without_a_balance_is_left_out_and_named():
    out = _forecast(
        accounts=[_account(), _account(balance=None, name="Example Savings", pid=2)]
    )
    assert out["balance"]["start_eur"] == 5000.0
    assert out["balance"]["excluded"] == ["Example Savings"]
    assert any("Example Savings" in w for w in out["warnings"])


def test_no_balance_anywhere_means_no_projection_not_zero():
    out = _forecast(accounts=[_account(balance=None)])
    assert out["balance"]["start_eur"] is None
    assert out["this_month"]["projected_balance_end_eur"] is None
    assert all(m["projected_balance_eur"] is None for m in out["next_months"])
    # the rest of the forecast is still there
    assert out["next_months"][0]["net_eur"] == 970.0


def test_failing_recurring_source_becomes_a_warning_and_none():
    out = cf.compute_forecast(
        _rows(), _tree(), None, [_account()], _fx, TODAY, 2, ["Recurring broke"]
    )
    assert "Recurring broke" in out["warnings"]
    assert out["known_bills"] == []
    assert out["this_month"]["recurring_remaining_eur"] is None
    assert out["this_month"]["projected_spending_eur"] is None
    assert out["next_months"][0]["recurring_eur"] is None
    assert out["next_months"][0]["balance_change_eur"] is None
    # the accounts still come through
    assert out["accounts"][0]["balance_eur"] == 5000.0


def test_failing_rows_source_keeps_the_bills_and_balances():
    rows = _rows()
    out = cf.compute_forecast(
        None, _tree(), _series(rows), [_account()], _fx, TODAY, 2, ["rows broke"]
    )
    assert out["baseline"] is None
    assert out["next_months"][0]["recurring_eur"] == 1630.0
    assert out["next_months"][0]["income_eur"] is None
    assert out["next_months"][0]["net_eur"] is None


def test_failing_fx_for_recurring_is_contained():
    def bad_fx(currency):
        raise RuntimeError("no rate")

    rows = _rows()
    out = cf.compute_forecast(
        rows, _tree(), _series(rows), [_account()], bad_fx, TODAY, 2
    )
    assert out["known_bills"] == []
    assert any("no rate" in w for w in out["warnings"])


def test_no_complete_month_gives_no_baseline_and_no_zero_projection():
    rows = [_row("2026-07-08", -150, "Groceries", "Example Market")]
    out = _forecast(rows=rows, series=[])
    assert out["baseline"]["months"] == []
    this = out["this_month"]
    assert this["projected_spending_eur"] is None
    assert this["variable_remaining_eur"] is None
    assert any("baseline" in w for w in out["warnings"])


def test_month_with_nothing_imported_is_flagged_not_hidden():
    rows = [r for r in _rows() if not r["date"].startswith("2026-07")]
    out = _forecast(
        rows=rows,
        accounts=[_account(through=date(2026, 6, 30))],
    )
    this = out["this_month"]
    assert this["status"] == "no_imports"
    assert this["spent_to_date_eur"] is None
    assert any("Nothing is imported for 2026-07" in w for w in out["warnings"])


def test_a_partial_baseline_month_is_not_used():
    # data ends mid-June: June is not a complete month of data
    out = _forecast(accounts=[_account(through=date(2026, 6, 20))])
    assert "2026-06" not in out["baseline"]["months"]


def test_assumptions_are_listed_in_plain_words():
    out = _forecast()
    assert len(out["assumptions"]) >= 4
    assert all(isinstance(a, str) and a for a in out["assumptions"])


# ── loading from the database ───────────────────────────────────────────────


def test_load_forecast_reads_the_database_and_tolerates_a_broken_source(tmp_path):
    db = Database(str(tmp_path / "forecast.db"))
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    spend = db.find_spending_category_by_name("Spend")["id"]
    db.create_spending_category("Rent", parent_id=spend)
    for m in (3, 4, 5, 6):
        db.create_spending_transaction(
            bank,
            f"2026-{m:02d}-01",
            "Example Landlord",
            -1000.0,
            category="Rent",
            balance=2000.0 + m,
        )
    out = cf.load_forecast(db, _fx, TODAY, months=2)
    assert out["accounts"][0]["name"] == "Example Bank"
    assert out["balance"]["start_eur"] == 2006.0
    assert out["next_months"][0]["recurring_eur"] == 1000.0

    # a source that raises degrades to a warning
    def boom(*a, **k):
        raise RuntimeError("tree down")

    db.list_spending_categories_tree = boom
    out = cf.load_forecast(db, _fx, TODAY, months=2)
    assert any("tree down" in w for w in out["warnings"])
    assert out["accounts"][0]["name"] == "Example Bank"


# ── per-account data-through and bills of the previous month (review round) ─


def test_a_lagging_second_account_sets_the_data_through_date():
    rows = _rows()
    rows.append(_row("2026-06-05", -40, "Groceries", "Example Shop", pid=2))
    rows.append(_row("2026-07-02", -40, "Groceries", "Example Shop", pid=2))
    accounts = [
        _account(),
        _account(name="Second Bank", pid=2, through=date(2026, 7, 2)),
    ]
    out = _forecast(rows=rows, accounts=accounts)
    assert out["data_through"] == "2026-07-02"
    assert out["this_month"]["data_through"] == "2026-07-02"
    # measured from the 2nd of 31 days, not from the 10th
    assert out["this_month"]["elapsed_share"] == pytest.approx(2 / 31, abs=0.001)
    assert any("Second Bank" in w and "2026-07-02" in w for w in out["warnings"])


def test_an_account_with_no_rows_in_the_window_is_ignored_for_data_through():
    accounts = [
        _account(),
        _account(name="Old Bank", pid=2, through=date(2024, 1, 5)),
    ]
    out = _forecast(accounts=accounts)
    assert out["data_through"] == "2026-07-10"


def test_baseline_months_wait_for_the_lagging_account():
    rows = _rows()
    # the second account reaches only Jun 10: June is not a complete month
    rows.append(_row("2026-06-03", -40, "Groceries", "Example Shop", pid=2))
    accounts = [
        _account(),
        _account(name="Second Bank", pid=2, through=date(2026, 6, 10)),
    ]
    out = _forecast(rows=rows, accounts=accounts)
    assert "2026-06" not in out["baseline"]["months"]
    assert "2026-05" in out["baseline"]["months"]


def test_bills_of_the_previous_month_move_the_balance_but_not_this_months_spending():
    today = date(2026, 8, 5)
    through = date(2026, 7, 20)
    rows = [
        _row(f"2026-{m:02d}-{d:02d}", -100, "Gym", "Example Gym")
        for m, d in ((5, 25), (6, 25), (7, 15))
    ]
    rows += [_row("2026-07-01", -10, "Groceries", "Example Market")]
    # the gym is expected on Aug 15 (this month) after Jul 15; add a bill due
    # Jul 25 for a different series so it falls in the gap after the import
    rows += [
        _row(f"2026-{m:02d}-28", -60, "Insurance", "Example Insurer") for m in (4, 5, 6)
    ]
    plain = [
        {
            "id": r["id"],
            "portfolio_id": 1,
            "date": r["date"],
            "amount": r["amount_eur"],
            "currency": "EUR",
            "merchant": r["merchant"],
            "description": r["merchant"],
            "category": r["category"],
            "is_transfer": 0,
        }
        for r in rows
        if r["amount_eur"] < 0
    ]
    series = detect_recurring(plain, {1: through})
    acct = _account(through=through)
    out = cf.compute_forecast(rows, _tree(), series, [acct], _fx, today, 1)
    bills = out["known_bills"]
    prior = [b for b in bills if b["date"] < "2026-08-01"]
    assert prior, "the fixture needs a bill dated in July after the import"
    prior_total = sum(b["amount_eur"] for b in prior)
    tm = out["this_month"]
    assert tm["prior_month_bills_eur"] == round(prior_total, 2)
    in_august = sum(
        b["amount_eur"]
        for b in bills
        if "2026-08-01" <= b["date"] <= "2026-08-31" and b["kind"] == "spending"
    )
    assert tm["recurring_remaining_eur"] == round(in_august, 2)
    assert tm["projected_spending_eur"] == round(
        (tm["spent_to_date_eur"] or 0) + in_august + tm["variable_remaining_eur"], 2
    )
    # the balance still pays the July bill
    expected = (
        5000.0
        + (tm["income_remaining_eur"] or 0)
        - prior_total
        - in_august
        - tm["variable_remaining_eur"]
    )
    assert tm["projected_balance_end_eur"] == pytest.approx(expected, abs=0.02)
