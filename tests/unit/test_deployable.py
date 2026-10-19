"""Tests for the allocator settings and the autónomo tax reserve."""

from datetime import date

import pytest

from portf_manager.database import Database
from portf_manager.services.deployable import (
    DEFAULT_SETTINGS,
    due_date,
    load_settings,
    quarter_bounds,
    quarter_of,
    quarter_reserve,
    save_settings,
    tax_reserve,
)

S = DEFAULT_SETTINGS


def _row(day, amount, merchant="Example Client", category="Income", transfer=False):
    return {
        "date": day,
        "amount_eur": amount,
        "category": category,
        "merchant": merchant,
        "is_transfer": transfer,
    }


def _tax(day, amount):
    return _row(day, -amount, merchant="Tax Office", category="Income Tax")


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "dep.db"))


def test_defaults_and_validation(db):
    assert load_settings(db) == DEFAULT_SETTINGS
    with pytest.raises(ValueError):
        save_settings(db, {"buffer_months_min": 7})
    saved = save_settings(db, {"iva_pct": 10, "bogus": 1})
    assert saved["iva_pct"] == 10 and "bogus" not in saved
    assert load_settings(db)["iva_pct"] == 10
    assert load_settings(db)["buffer_months_target"] == 6
    with pytest.raises(ValueError):
        save_settings(db, {"iva_pct": 120})
    with pytest.raises(ValueError):
        save_settings(db, {"managed_portfolio_ids": [999]})
    # the shared default lists are never mutated
    load_settings(db)["tax_paid_categories"].append("x")
    assert DEFAULT_SETTINGS["tax_paid_categories"] == ["Income Tax"]


def test_indexa_portfolio_is_the_managed_default(db):
    pid = db.create_portfolio("Indexa Capital", "EUR")
    db.create_portfolio("Broker", "EUR")
    loaded = load_settings(db)
    assert loaded["managed_portfolio_ids"] == [pid]
    assert loaded["defaulted_managed"] is True
    save_settings(db, {"managed_portfolio_ids": [pid]})
    assert "defaulted_managed" not in load_settings(db)


def test_quarter_reserve_formula():
    rows = [_row("2026-11-05", 12100)]
    q = quarter_reserve(rows, [], 2026, 4, S, date(2026, 11, 15))
    assert q["gross_eur"] == 12100
    assert q["iva_eur"] == 2100
    assert q["irpf_eur"] == 2000
    assert q["accrued_eur"] == 4100


def test_retention_reduces_irpf():
    rows = [_row("2026-11-05", 12100)]
    s = S | {"irpf_retention_pct": 15.0}
    q = quarter_reserve(rows, [], 2026, 4, s, date(2026, 11, 15))
    assert q["irpf_eur"] == 500
    assert q["accrued_eur"] == 2600


def test_partner_transfers_not_counted():
    rows = [
        _row("2026-11-05", 12100),
        _row("2026-11-06", 3000, merchant="Partner"),
        _row("2026-11-07", 500, transfer=True),
    ]
    s = S | {"work_income_merchants": ["Example Client"]}
    res = tax_reserve(rows, [], s, date(2026, 11, 15))
    assert res["merchants_counted"] == ["Example Client"]
    assert res["quarters"][1]["gross_eur"] == 12100
    assert res["warnings"] == []


def test_empty_merchant_list_counts_all_with_warning():
    rows = [_row("2026-11-05", 6050), _row("2026-11-06", 6050, merchant="Partner")]
    res = tax_reserve(rows, [], S, date(2026, 11, 15))
    assert res["quarters"][1]["gross_eur"] == 12100
    assert res["merchants_counted"] == ["Example Client", "Partner"]
    assert (
        "all income counted as work income: tick the work merchants in the settings"
        in res["warnings"]
    )


def test_quarter_without_income_reserves_zero():
    res = tax_reserve([], [], S, date(2026, 11, 15))
    assert res["reserve_eur"] == 0
    assert all(q["accrued_eur"] == 0 for q in res["quarters"])
    assert "no work income imported this quarter" in res["warnings"]


def test_payment_on_due_date_settles_previous_quarter():
    rows = [_row("2026-08-05", 12100)]
    today = date(2026, 10, 25)
    res = tax_reserve(rows, [_tax("2026-10-20", 4100)], S, today)
    prev = res["quarters"][0]
    assert prev["accrued_eur"] == 4100 and prev["paid_eur"] == 4100
    assert prev["settled"] and prev["remaining_eur"] == 0
    assert res["reserve_eur"] == 0
    # 31 days after the quarter end: not that quarter's payment
    res = tax_reserve(rows, [_tax("2026-10-31", 4100)], S, today)
    assert res["quarters"][0]["paid_eur"] == 0
    assert res["reserve_eur"] == 4100


def test_previous_quarter_kept_until_due_plus_30():
    rows = [_row("2026-08-05", 12100)]
    res = tax_reserve(rows, [], S, date(2026, 10, 10))
    assert res["reserve_eur"] == 4100
    res = tax_reserve(rows, [], S, date(2026, 11, 25))
    assert res["reserve_eur"] == 0
    prev = res["quarters"][0]
    assert prev["settled"] and prev["dropped_reason"] == "past due"
    assert prev["remaining_eur"] == 0


def test_due_date_q4_is_30_january():
    assert due_date(2026, 4) == date(2027, 1, 30)
    assert due_date(2026, 1) == date(2026, 4, 20)
    assert due_date(2026, 3) == date(2026, 10, 20)
    assert quarter_of(date(2026, 2, 28)) == (2026, 1)
    assert quarter_bounds(2026, 2) == (date(2026, 4, 1), date(2026, 6, 30))


def test_january_looks_back_to_previous_year_q4():
    rows = [_row("2026-12-10", 12100)]
    res = tax_reserve(rows, [], S, date(2027, 1, 15))
    assert res["quarters"][0]["quarter"] == "2026-Q4"
    assert res["reserve_eur"] == 4100


def test_partial_payment_dropped_after_due_plus_30():
    rows = [_row("2026-08-05", 12100)]
    tax = [_tax("2026-10-20", 4000)]
    res = tax_reserve(rows, tax, S, date(2026, 11, 10))
    assert res["quarters"][0]["remaining_eur"] == 100
    assert res["reserve_eur"] == 100
    res = tax_reserve(rows, tax, S, date(2026, 11, 25))
    prev = res["quarters"][0]
    assert prev["remaining_eur"] == 0 and prev["dropped_reason"] == "past due"
    assert res["reserve_eur"] == 0


def test_merchants_only_from_quarters_in_play(db):
    rows = [_row("2025-03-05", 100, merchant="Old Client"), _row("2026-11-05", 100)]
    res = tax_reserve(rows, [], S, date(2026, 11, 15))
    assert res["merchants_counted"] == ["Example Client"]


def test_settings_type_checks(db):
    for patch in (
        {"managed_portfolio_ids": [True]},
        {"managed_portfolio_ids": ["1"]},
        {"max_lines_per_bucket": 2.5},
        {"max_lines_per_bucket": True},
    ):
        with pytest.raises(ValueError):
            save_settings(db, patch)


# ── buffer and deployable ────────────────────────────────────────────────────

from portf_manager.services.deployable import buffer, deployable  # noqa: E402

TODAY = date(2026, 10, 19)
FX = {"fx": lambda c: 1.0, "fx_on": lambda c, d: 1.0}


def test_buffer_statuses():
    def b(liquid):
        return buffer(liquid, 0.0, 1000.0, S)

    assert b(3500)["status"] == "rebuild"
    assert b(5000)["status"] == "topping_up"
    assert b(7000)["status"] == "ok"
    idle = b(9000)
    assert idle["status"] == "idle" and idle["idle_eur"] == 1000
    assert b(7000)["target_eur"] == 6000


def _spend_db(tmp_path, months=12, social=300.0):
    db = Database(str(tmp_path / "dep.db"))
    pid = db.get_or_create_portfolio("Example Bank", account_type="bank")
    year, month = 2026, 9
    for _ in range(months):
        db.create_spending_transaction(
            pid, f"{year}-{month:02d}-05", "Rent", -1000.0, category="Rent"
        )
        if social:
            db.create_spending_transaction(
                pid,
                f"{year}-{month:02d}-07",
                "Social",
                -social,
                category="Social Security",
            )
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return db


def test_deployable_formula_and_zero_floor(tmp_path):
    db = _spend_db(tmp_path, social=0.0)
    db.create_manual_asset("Example Jar", "cash", 10000.0, as_of="2026-10-01")
    r = deployable(db, today=TODAY, **FX)
    assert r["status"] == "ok"
    assert r["liquid_eur"] == 10000
    assert r["tax_reserve"]["reserve_eur"] == 0
    assert r["deployable_eur"] == 4000  # 10000 - 6 * 1000
    assert any("buffer: 6 months target" in w for w in r["warnings"])


def test_deployable_zero_floor(tmp_path):
    db = _spend_db(tmp_path, social=0.0)
    db.create_manual_asset("Example Jar", "cash", 6000.0, as_of="2026-10-01")
    r = deployable(db, today=TODAY, **FX)
    assert r["deployable_eur"] == 0 and r["buffer"]["status"] == "ok"


def test_monthly_spend_adds_social_security_back(tmp_path):
    db = _spend_db(tmp_path)
    db.create_manual_asset("Example Jar", "cash", 20000.0, as_of="2026-10-01")
    r = deployable(db, today=TODAY, **FX)
    b = r["buffer"]
    assert b["retirement_monthly_spend_eur"] == 1000
    assert b["monthly_spend_eur"] == 1300
    assert len(b["months_used"]) >= 6
    assert r["deployable_eur"] == 20000 - 6 * 1300


def test_insufficient_data_under_six_months(tmp_path):
    db = _spend_db(tmp_path, months=5)
    db.create_manual_asset("Example Jar", "cash", 5000.0, as_of="2026-10-01")
    r = deployable(db, today=TODAY, **FX)
    assert r["status"] == "insufficient_data" and r["reason"]
    assert r["liquid_eur"] == 5000 and "tax_reserve" in r
    assert r["deployable_eur"] is None


def test_negative_ledger_line_not_liquid(tmp_path):
    db = _spend_db(tmp_path)
    pid = db.create_portfolio("Example Broker", "EUR")
    aid = db.create_asset("EXC", "Example Corp", "stock", currency="EUR")
    db.create_price(aid, 10.0, "2026-10-17")
    db.create_transaction(aid, "buy", 10, 10.0, 100.0, "2026-01-05", portfolio_id=pid)
    db.create_manual_asset("Example Jar", "cash", 1000.0, as_of="2026-10-01")
    r = deployable(db, today=TODAY, **FX)
    assert r["liquid_eur"] == 1000
    assert [ln["label"] for ln in r["liquid_lines"]] == ["Example Jar"]
    assert any(w.startswith("left out:") for w in r["warnings"])


def test_coming_in_lists_deposit_maturing_within_30_days(tmp_path):
    db = _spend_db(tmp_path)
    db.create_fixed_deposit(
        "Example Deposit", 2000.0, start_date="2026-01-01", maturity_date="2026-11-10"
    )
    db.create_fixed_deposit(
        "Far Deposit", 3000.0, start_date="2026-01-01", maturity_date="2027-03-01"
    )
    r = deployable(db, today=TODAY, **FX)
    assert r["coming_in"] == [
        {"label": "Example Deposit", "amount_eur": 2000.0, "date": "2026-11-10"}
    ]
