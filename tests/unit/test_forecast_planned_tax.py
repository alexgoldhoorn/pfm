"""Cash-flow forecast: planned items, what-if and the estimated tax payments."""

from datetime import date

import pytest

from portf_manager.services import cashflow_forecast as cf
from portf_manager.services import deployable
from portf_manager.services.recurring import detect_recurring

TODAY = date(2026, 7, 15)
THROUGH = date(2026, 7, 10)
_ids = iter(range(1, 100000))


def _fx(currency):
    return 1.0


def _row(day, amount, category, merchant, one_off=False):
    return {
        "id": next(_ids),
        "portfolio_id": 1,
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
        ("Spend", None),
        ("Income", None),
        ("Groceries", "Spend"),
        ("Rent", "Spend"),
        ("Income Tax", "Spend"),
    ]
    return {n: {"name": n, "parent_name": p, "essentiality": None} for n, p in names}


def _rows():
    rows = []
    for m in range(1, 8):
        rows.append(_row(f"2026-{m:02d}-01", -1000, "Rent", "Example Landlord"))
    for m, groceries in zip(range(1, 7), (250, 550, 300, 500, 350, 450)):
        rows.append(_row(f"2026-{m:02d}-12", -groceries, "Groceries", "Example Market"))
    # work income in every month of the last three complete quarters
    for y, m in [(2025, 10), (2025, 11), (2025, 12)] + [(2026, k) for k in range(1, 7)]:
        rows.append(_row(f"{y}-{m:02d}-25", 3000, "Income", "Example Client"))
    return rows


def _tax_rows():
    return [
        _row("2025-10-20", -3000, "Income Tax", "Example Tax Agency"),
        _row("2026-01-20", -3000, "Income Tax", "Example Tax Agency"),
        _row("2026-04-20", -3000, "Income Tax", "Example Tax Agency"),
        # a one-time payment that would lift the baseline if it counted
        _row("2026-03-10", -600, "Income Tax", "Example Other Agency"),
    ]


def _series(rows):
    plain = [
        {
            "id": r["id"],
            "portfolio_id": 1,
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


def _account(balance=5000.0):
    return {
        "portfolio_id": 1,
        "name": "Example Bank",
        "currency": "EUR",
        "data_through": THROUGH,
        "balance_eur": balance,
        "balance_date": THROUGH.isoformat(),
    }


def _settings(**over):
    return {
        **deployable.DEFAULT_SETTINGS,
        "work_income_merchants": ["Example Client"],
        **over,
    }


def _run(rows=None, months=3, planned=None, tax=None, what_if=None, balance=5000.0):
    rows = _rows() if rows is None else rows
    return cf.compute_forecast(
        rows,
        _tree(),
        _series(rows),
        [_account(balance)],
        _fx,
        TODAY,
        months,
        planned=planned,
        tax=tax,
        what_if=what_if,
    )


def _item(day, amount, kind="spending", description="Example Roof", id=1, **extra):
    return {
        "id": id,
        "date": day,
        "description": description,
        "amount": amount,
        "currency": "EUR",
        "kind": kind,
        **extra,
    }


# ── planned items ───────────────────────────────────────────────────────────


def test_no_planned_items_leaves_the_figures_alone_and_fields_zero():
    plain, with_empty = _run(), _run(planned=[])
    assert plain == with_empty
    assert plain["planned_items"] == [] and plain["tax_payments"] == []
    aug = plain["next_months"][0]
    assert aug["planned_spending_eur"] == 0.0 and aug["tax_eur"] == 0.0
    assert plain["this_month"]["planned_income_eur"] == 0.0


def test_planned_spending_savings_and_income_land_in_their_months():
    base = _run()
    out = _run(
        planned=[
            _item("2026-08-20", -400.0, id=1),
            _item("2026-08-21", -150.0, "savings", id=2),
            _item("2026-09-05", 800.0, "income", id=3),
        ]
    )
    aug, sep = out["next_months"][0], out["next_months"][1]
    b_aug, b_sep = base["next_months"][0], base["next_months"][1]
    assert aug["planned_spending_eur"] == 400.0
    assert aug["planned_savings_eur"] == 150.0
    assert aug["spending_eur"] == b_aug["spending_eur"] + 400.0
    assert aug["savings_eur"] == b_aug["savings_eur"] + 150.0
    assert aug["balance_change_eur"] == pytest.approx(
        b_aug["balance_change_eur"] - 550.0, abs=0.01
    )
    assert sep["planned_income_eur"] == 800.0
    assert sep["income_eur"] == pytest.approx(b_sep["income_eur"] + 800.0, abs=0.01)
    # the low/high range shifts equally
    assert aug["range"]["net_low_eur"] == pytest.approx(
        b_aug["range"]["net_low_eur"] - 400.0, abs=0.01
    )
    assert aug["range"]["net_high_eur"] == pytest.approx(
        b_aug["range"]["net_high_eur"] - 400.0, abs=0.01
    )
    # nothing leaks into the baseline
    assert out["baseline"] == base["baseline"]
    statuses = {i["id"]: i["status"] for i in out["planned_items"]}
    assert statuses == {1: "projected", 2: "projected", 3: "projected"}


def test_planned_item_this_month_shifts_the_end_balance_and_range():
    base = _run()
    out = _run(planned=[_item("2026-07-28", -300.0)])
    this, b = out["this_month"], base["this_month"]
    assert this["planned_spending_eur"] == 300.0
    assert this["projected_spending_eur"] == pytest.approx(
        b["projected_spending_eur"] + 300.0, abs=0.01
    )
    assert this["projected_balance_end_eur"] == pytest.approx(
        b["projected_balance_end_eur"] - 300.0, abs=0.01
    )
    for key in ("spending_low_eur", "spending_high_eur"):
        assert this["range"][key] == pytest.approx(b["range"][key] + 300.0, abs=0.01)


def test_overdue_and_beyond_horizon_items_are_listed_not_projected():
    base = _run()
    out = _run(
        planned=[
            _item("2026-07-05", -300.0, id=1),
            _item("2027-06-01", -900.0, id=2),
        ]
    )
    status = {i["id"]: i["status"] for i in out["planned_items"]}
    assert status == {1: "overdue", 2: "beyond_horizon"}
    assert out["this_month"]["planned_spending_eur"] == 0.0
    assert out["next_months"] == base["next_months"]
    assert sum("mark them done or move their date" in w for w in out["warnings"]) == 1


def test_planned_item_in_a_foreign_currency_is_converted():
    out = cf.compute_forecast(
        _rows(),
        _tree(),
        _series(_rows()),
        [_account()],
        lambda c: 0.5 if c == "USD" else 1.0,
        TODAY,
        3,
        planned=[_item("2026-08-20", -400.0, currency="USD")],
    )
    assert out["next_months"][0]["planned_spending_eur"] == 200.0
    assert out["planned_items"][0]["amount_eur"] == -200.0


def test_what_if_reports_the_lowest_balance_with_and_without():
    base = _run()
    out = _run(
        what_if={"amount": 20000.0, "date": "2026-09-15", "kind": "spending"},
        balance=5000.0,
    )
    w = out["what_if"]
    assert w["amount_eur"] == -20000.0 and w["date"] == "2026-09-15"
    assert w["goes_below_zero"] is True
    assert w["lowest_balance_eur"] < 0
    # the baseline lowest is the current month's end or later: it is higher
    assert w["lowest_balance_without_eur"] > w["lowest_balance_eur"]
    assert w["lowest_balance_month"] in {m["month"] for m in out["next_months"]}
    item = out["planned_items"][0]
    assert item["id"] is None and item["status"] == "what_if"
    # the saved forecast is untouched by the what-if pass
    assert "what_if" not in base
    assert base["planned_items"] == []


def test_what_if_income_raises_the_balance_and_never_goes_below_zero():
    out = _run(what_if={"amount": 500.0, "date": "2026-08-10", "kind": "income"})
    assert out["what_if"]["amount_eur"] == 500.0
    assert out["what_if"]["goes_below_zero"] is False


def test_what_if_without_a_balance_is_unknown_not_zero():
    out = cf.compute_forecast(
        _rows(),
        _tree(),
        _series(_rows()),
        [{**_account(), "balance_eur": None}],
        _fx,
        TODAY,
        3,
        what_if={"amount": 500.0, "date": "2026-08-10", "kind": "spending"},
    )
    w = out["what_if"]
    assert w["lowest_balance_eur"] is None and w["goes_below_zero"] is None


# ── tax payments ────────────────────────────────────────────────────────────


def _taxed(months=7, **over):
    rows = _rows() + _tax_rows()
    return rows, _run(
        rows=rows, months=months, tax={"settings": _settings(**over), "rows": rows}
    )


def test_quarterly_tax_payments_use_the_allocator_reserve_maths():
    rows = _rows() + _tax_rows()
    # 3 x 3000 gross a quarter, IVA 21 % inside, IRPF 20 % of the net
    expected = deployable.quarter_reserve(rows, [], 2026, 2, _settings(), TODAY)[
        "accrued_eur"
    ]
    assert expected == 3049.59
    _, out = _taxed()
    pays = {p["quarter"]: p for p in out["tax_payments"]}
    # Q2 is unpaid (nothing in its payment window): its accrual is due 07-20
    assert pays["2026-Q2"] == {
        "quarter": "2026-Q2",
        "due": "2026-07-20",
        "amount_eur": 3049.59,
        "basis": "accrued",
    }
    # Q3 has no income yet: the average of the complete quarters stands in
    assert pays["2026-Q3"]["basis"] == "average"
    assert pays["2026-Q3"]["amount_eur"] == 3049.59
    assert pays["2026-Q3"]["due"] == "2026-10-20"
    assert pays["2026-Q4"]["due"] == "2027-01-30"
    assert out["this_month"]["tax_eur"] == 3049.59
    by_month = {m["month"]: m["tax_eur"] for m in out["next_months"]}
    assert by_month["2026-10"] == 3049.59 and by_month["2027-01"] == 3049.59
    assert by_month["2026-08"] == 0.0
    assert any("IVA 21%, IRPF 20%" in a for a in out["assumptions"])


def test_current_quarter_takes_the_larger_of_accrued_and_average():
    rows = _rows() + _tax_rows()
    # July income already above the average quarter
    rows.append(_row("2026-07-08", 20000, "Income", "Example Client"))
    out = _run(
        rows=rows,
        months=4,
        tax={"settings": _settings(), "rows": rows},
    )
    q3 = next(p for p in out["tax_payments"] if p["quarter"] == "2026-Q3")
    assert q3["basis"] == "accrued" and q3["amount_eur"] > 3049.59


def test_tax_is_not_counted_twice():
    rows = _rows() + _tax_rows()
    off = _run(rows=rows, months=7)
    on = _run(rows=rows, months=7, tax={"settings": _settings(), "rows": rows})
    # without the estimate the detected quarterly series is a known bill
    assert any(b["category"] == "Income Tax" for b in off["known_bills"])
    assert not any(b["category"] == "Income Tax" for b in on["known_bills"])
    # and the one-time payment sits in the baseline only without the estimate
    assert off["baseline"]["variable_eur"]["avg"] == pytest.approx(
        on["baseline"]["variable_eur"]["avg"] + 100.0, abs=0.01
    )


def test_everyday_outflows_default_is_unchanged_and_exclusion_is_opt_in():
    rows = _rows() + _tax_rows()
    ids = {r["id"] for r in rows if r["category"] == "Rent"}
    months = ["2026-03"]
    plain = cf.everyday_outflows(rows, _tree(), ids, months)
    assert plain["2026-03"]["Income Tax"] == 600.0
    cut = cf.everyday_outflows(rows, _tree(), ids, months, frozenset({"Income Tax"}))
    assert "Income Tax" not in cut["2026-03"]


def test_no_work_income_merchants_ticked_adds_no_tax_and_one_warning():
    rows = _rows() + _tax_rows()
    plain = _run(rows=rows, months=7)
    out = _run(
        rows=rows,
        months=7,
        tax={"settings": _settings(work_income_merchants=[]), "rows": []},
    )
    assert out["tax_payments"] == []
    assert out["known_bills"] == plain["known_bills"]
    assert out["baseline"] == plain["baseline"]
    assert sum("work-income merchants" in w for w in out["warnings"]) == 1


def test_no_quarter_with_work_income_warns_and_adds_nothing():
    rows = [r for r in _rows() if r["category"] != "Income"]
    out = _run(rows=rows, tax={"settings": _settings(), "rows": rows})
    assert out["tax_payments"] == []
    assert any("not estimated" in w for w in out["warnings"])


def test_a_failing_tax_input_is_a_warning_not_an_error():
    out = _run(tax={"settings": _settings(), "rows": None})
    assert out["tax_payments"] == []
    assert any("could not be estimated" in w for w in out["warnings"])
    assert out["this_month"]["projected_spending_eur"] is not None
