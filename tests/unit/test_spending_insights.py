"""Comparison, anomalies and tier breakdown over spending rows."""

from datetime import date

import pytest

from portf_manager.database import Database
from portf_manager.services import spending_insights as si

TODAY = date(2026, 7, 15)


def _row(rid, day, amount, category="Groceries", merchant="Example Market", **over):
    row = {
        "id": rid,
        "portfolio_id": 1,
        "date": day,
        "amount_eur": amount,
        "category": category,
        "merchant": merchant,
        "description": merchant,
        "one_off": False,
        "is_transfer": False,
    }
    row.update(over)
    return row


def _tree():
    names = [
        ("Spend", None, None),
        ("Income", None, None),
        ("Groceries", "Spend", "flexible"),
        ("Rent", "Spend", "fixed"),
        ("Fun", "Spend", "optional"),
        ("Gadgets", "Spend", None),
        ("Invest", "Spend", None),
    ]
    return {n: {"name": n, "parent_name": p, "essentiality": e} for n, p, e in names}


def _months(rows_by_month):
    """rows for 2026-01..06: groceries 100, rent 1000 each month."""
    rows, rid = [], 1
    for m in range(1, 7):
        rows.append(_row(rid, f"2026-{m:02d}-03", -1000.0, "Rent", "Example Landlord"))
        rid += 1
        rows.append(_row(rid, f"2026-{m:02d}-10", -100.0))
        rid += 1
    # income on the last imported day, so June reads as fully imported
    rows.append(_row(800, "2026-06-30", 10.0, "Income", "Example Employer"))
    return rows


# ── compare ─────────────────────────────────────────────────────────────────


def test_compare_against_baseline_and_last_year():
    rows = _months(None)
    # a year earlier there was data for May only
    rows.append(_row(900, "2025-05-10", -70.0))
    rows.append(_row(901, "2026-06-20", -50.0, "Fun", "Example Cinema"))
    out = si.compare_month(
        rows, _tree(), "2026-05", TODAY, baseline_months=3, group_by="category"
    )
    assert out["status"] == "complete"
    assert out["baseline_months"] == ["2026-02", "2026-03", "2026-04"]
    groc = next(i for i in out["items"] if i["name"] == "Groceries")
    assert groc["actual_eur"] == 100.0
    assert groc["baseline_eur"] == 100.0
    assert groc["last_year_eur"] == 70.0
    assert groc["delta_eur"] == 0.0
    assert out["totals"]["actual_eur"] == 1100.0
    assert out["totals"]["last_year_eur"] == 70.0
    assert groc["tier"] == "flexible"


def test_last_year_is_none_without_data_that_month():
    out = si.compare_month(_months(None), _tree(), "2026-05", TODAY)
    assert out["totals"]["last_year_eur"] is None
    assert all(i["last_year_eur"] is None for i in out["items"])


def test_group_by_merchant():
    out = si.compare_month(
        _months(None), _tree(), "2026-05", TODAY, group_by="merchant"
    )
    assert {i["name"] for i in out["items"]} == {"Example Market", "Example Landlord"}


def test_delta_percent_and_ordering_by_size_of_move():
    rows = _months(None)
    rows.append(_row(910, "2026-06-12", -300.0, "Fun", "Example Cinema"))
    out = si.compare_month(rows, _tree(), "2026-06", TODAY, baseline_months=3)
    assert out["items"][0]["name"] == "Fun"
    fun = out["items"][0]
    assert fun["baseline_eur"] == 0.0
    assert fun["delta_eur"] == 300.0
    assert fun["delta_pct"] is None  # no baseline, no percentage
    assert out["totals"]["delta_eur"] == 300.0


def test_one_off_counts_in_actual_but_not_in_baseline():
    rows = _months(None)
    rows.append(
        _row(920, "2026-03-15", -500.0, "Gadgets", "Example Shop", one_off=True)
    )
    in_month = si.compare_month(rows, _tree(), "2026-03", TODAY, baseline_months=2)
    gad = next(i for i in in_month["items"] if i["name"] == "Gadgets")
    assert gad["actual_eur"] == 500.0
    assert gad["one_off_eur"] == 500.0
    later = si.compare_month(rows, _tree(), "2026-05", TODAY, baseline_months=3)
    assert later["totals"]["baseline_eur"] == 1100.0  # Mar's 500 not averaged in


def test_savings_categories_are_not_spending():
    rows = _months(None)
    rows.append(_row(930, "2026-05-20", -400.0, "Invest", "Example Broker"))
    out = si.compare_month(rows, _tree(), "2026-05", TODAY, baseline_months=3)
    assert out["totals"]["actual_eur"] == 1100.0
    assert out["totals"]["savings_eur"] == 400.0
    assert all(i["name"] != "Invest" for i in out["items"])


def test_transfers_are_left_out():
    rows = _months(None)
    rows.append(_row(940, "2026-05-21", -999.0, "Transfer", is_transfer=True))
    out = si.compare_month(rows, _tree(), "2026-05", TODAY)
    assert out["totals"]["actual_eur"] == 1100.0


def test_month_in_progress_has_no_delta():
    rows = _months(None) + [_row(950, "2026-07-05", -40.0)]
    out = si.compare_month(rows, _tree(), "2026-07", TODAY)
    assert out["status"] == "in_progress"
    assert out["data_through"] == "2026-07-05"
    assert out["totals"]["actual_eur"] == 40.0
    assert out["totals"]["delta_eur"] is None
    assert out["warnings"]


def test_month_without_imports_has_no_numbers():
    rows = [r for r in _months(None) if not r["date"].startswith("2026-04")]
    out = si.compare_month(rows, _tree(), "2026-04", TODAY)
    assert out["status"] == "no_imports"
    assert out["totals"]["actual_eur"] is None
    assert out["totals"]["delta_eur"] is None
    assert out["items"] == []


def test_months_without_imports_do_not_enter_the_baseline():
    rows = [r for r in _months(None) if not r["date"].startswith("2026-04")]
    out = si.compare_month(rows, _tree(), "2026-06", TODAY, baseline_months=3)
    assert out["baseline_months"] == ["2026-03", "2026-05"]
    assert out["totals"]["baseline_eur"] == 1100.0


def test_future_month_is_rejected():
    with pytest.raises(ValueError):
        si.compare_month([], _tree(), "2026-08", TODAY)


def test_bad_group_by_is_rejected():
    with pytest.raises(ValueError):
        si.compare_month([], _tree(), "2026-05", TODAY, group_by="colour")


# ── tiers ───────────────────────────────────────────────────────────────────


def test_tiers_split_each_month_and_reconcile():
    rows = _months(None)
    rows.append(_row(960, "2026-06-15", -30.0, "Fun", "Example Cinema"))
    rows.append(_row(961, "2026-06-16", -20.0, "Gadgets", "Example Shop"))
    rows.append(_row(962, "2026-06-17", -400.0, "Invest", "Example Broker"))
    out = si.tier_breakdown(rows, _tree(), TODAY, months=2)
    assert [m["month"] for m in out] == ["2026-06", "2026-07"]
    june = out[0]
    assert june["fixed_eur"] == 1000.0
    assert june["flexible_eur"] == 100.0
    assert june["optional_eur"] == 30.0
    assert june["unclassified_eur"] == 20.0
    assert june["total_eur"] == 1150.0
    assert june["savings_eur"] == 400.0
    assert june["top_categories"]["optional"] == [{"name": "Fun", "amount_eur": 30.0}]
    assert june["has_imports"] is True and june["in_progress"] is False
    july = out[1]
    assert july["in_progress"] is True and july["has_imports"] is False
    assert july["total_eur"] == 0.0


def test_tiers_count_one_off_rows_and_report_them():
    rows = _months(None)
    rows.append(_row(970, "2026-06-15", -500.0, "Gadgets", one_off=True))
    june = si.tier_breakdown(rows, _tree(), TODAY, months=2)[0]
    assert june["unclassified_eur"] == 500.0
    assert june["one_off_eur"] == 500.0


# ── anomalies ───────────────────────────────────────────────────────────────


def _history():
    """Six months of groceries at 100 (merchant Example Market, weekly-ish)."""
    return _months(None)


def test_large_charge_far_above_the_usual():
    rows = _history() + [_row(1000, "2026-07-01", -400.0)]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    big = [a for a in out["items"] if a["kind"] == "large_charge"]
    assert [a["id"] for a in big] == ["anomaly:large:1000"]
    assert big[0]["usual_eur"] == 100.0
    assert big[0]["amount_eur"] == 400.0
    assert big[0]["transaction_ids"] == [1000]


def test_charge_in_line_with_history_is_quiet():
    rows = _history() + [_row(1001, "2026-07-01", -150.0)]
    assert si.find_anomalies(rows, _tree(), TODAY, days=60)["items"] == []


def test_small_amounts_are_never_flagged():
    rows = [
        _row(i, f"2026-0{1 + i % 5}-0{1 + i % 5}", -2.0, merchant="Tiny")
        for i in range(1, 7)
    ]
    rows.append(_row(1002, "2026-07-01", -20.0, merchant="Tiny"))
    assert si.find_anomalies(rows, _tree(), TODAY, days=60)["items"] == []


def test_charge_older_than_the_window_is_not_reported():
    rows = _history() + [_row(1003, "2026-04-01", -400.0)]
    assert si.find_anomalies(rows, _tree(), TODAY, days=30)["items"] == []


def test_one_off_rows_are_not_flagged_and_not_a_baseline():
    rows = _history() + [_row(1004, "2026-07-01", -400.0, one_off=True)]
    assert si.find_anomalies(rows, _tree(), TODAY, days=60)["items"] == []


def test_new_merchant_with_a_large_charge():
    rows = _history() + [
        _row(1005, "2026-07-02", -350.0, "Gadgets", "Sample Electronics")
    ]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    kinds = {a["id"]: a for a in out["items"]}
    assert "anomaly:new_merchant:1005" in kinds
    assert kinds["anomaly:new_merchant:1005"]["merchant"] == "Sample Electronics"


def test_new_merchant_needs_enough_history():
    rows = [
        _row(1, "2026-06-20", -100.0),
        _row(2, "2026-07-02", -350.0, merchant="New"),
    ]
    assert si.find_anomalies(rows, _tree(), TODAY, days=60)["items"] == []


def test_small_new_merchant_is_quiet():
    rows = _history() + [_row(1006, "2026-07-02", -60.0, "Gadgets", "Sample Kiosk")]
    assert si.find_anomalies(rows, _tree(), TODAY, days=60)["items"] == []


def test_category_well_above_its_baseline():
    rows = _history() + [
        _row(2000 + i, f"2026-06-{10 + i}", -100.0, "Fun", f"Example Venue {i}")
        for i in range(3)
    ]
    # Fun was 20/month before June
    rows += [
        _row(2100 + m, f"2026-{m:02d}-20", -20.0, "Fun", "Example Cinema")
        for m in range(1, 6)
    ]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    spikes = [a for a in out["items"] if a["kind"] == "category_spike"]
    assert [a["id"] for a in spikes] == ["anomaly:category:Fun:2026-06"]
    assert spikes[0]["amount_eur"] == 300.0
    assert spikes[0]["usual_eur"] == 20.0


def test_category_spike_ignores_one_off_rows():
    rows = _history() + [_row(2200, "2026-06-12", -900.0, "Groceries", one_off=True)]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    assert [a for a in out["items"] if a["kind"] == "category_spike"] == []


def test_possible_duplicate_charge():
    rows = _history() + [
        _row(3000, "2026-07-01", -45.0, "Fun", "Example Cinema"),
        _row(3001, "2026-07-02", -45.0, "Fun", "Example Cinema"),
    ]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    dups = [a for a in out["items"] if a["kind"] == "possible_duplicate"]
    assert [a["id"] for a in dups] == ["anomaly:duplicate:3000:3001"]
    assert dups[0]["transaction_ids"] == [3000, 3001]


def test_same_amount_a_week_apart_is_not_a_duplicate():
    rows = _history() + [
        _row(3010, "2026-07-01", -45.0, "Fun", "Example Cinema"),
        _row(3011, "2026-07-09", -45.0, "Fun", "Example Cinema"),
    ]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    assert [a for a in out["items"] if a["kind"] == "possible_duplicate"] == []


def test_same_amount_at_different_accounts_is_not_a_duplicate():
    rows = _history() + [
        _row(3020, "2026-07-01", -45.0, "Fun", "Example Cinema"),
        _row(3021, "2026-07-01", -45.0, "Fun", "Example Cinema", portfolio_id=2),
    ]
    out = si.find_anomalies(rows, _tree(), TODAY, days=60)
    assert [a for a in out["items"] if a["kind"] == "possible_duplicate"] == []


def test_anomaly_ids_are_stable():
    rows = _history() + [_row(1000, "2026-07-01", -400.0)]
    a = si.find_anomalies(rows, _tree(), TODAY, days=60)["items"]
    b = si.find_anomalies(list(reversed(rows)), _tree(), TODAY, days=60)["items"]
    assert [x["id"] for x in a] == [x["id"] for x in b]


# ── database wiring ─────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    db = Database(str(tmp_path / "insights.db"))
    bank = db.create_portfolio("Example Bank", account_type="bank")
    db.create_spending_transaction(bank, "2026-06-03", "Example Gym", -30.0)
    gbp = db.create_spending_transaction(
        bank, "2026-06-04", "Sample Energy", -10.0, currency="GBP"
    )
    t = db.create_spending_transaction(bank, "2026-06-05", "Own", -99.0)
    db.update_spending_transaction(t, is_transfer=True)
    db.update_spending_transaction(gbp, one_off=True)
    return db


def test_load_rows_converts_to_eur_and_keeps_flags(db):
    rows = si.load_rows(db, lambda c: 2.0 if c == "GBP" else 1.0)
    by_desc = {r["description"]: r for r in rows}
    assert by_desc["Sample Energy"]["amount_eur"] == -20.0
    assert by_desc["Sample Energy"]["one_off"] is True
    assert by_desc["Own"]["is_transfer"] is True
    assert by_desc["Example Gym"]["one_off"] is False


def test_load_rows_filters_by_account(db):
    other = db.create_portfolio("Sample Bank", account_type="bank")
    db.create_spending_transaction(other, "2026-06-06", "Elsewhere", -5.0)
    rows = si.load_rows(db, lambda c: 1.0, portfolio_id=other)
    assert [r["description"] for r in rows] == ["Elsewhere"]
