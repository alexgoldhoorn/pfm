"""Detect a loan from recurring bank debits (read-only helper)."""

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import loans, mortgage

_KEY = "test-key-loan-detect-abc123"
HEADERS = {"X-API-Key": _KEY}

# invented loan: two rates, so the bank sees the payment change once
HISTORY_LOAN = {
    "principal": 150000.0,
    "annual_rate_pct": 3.5,
    "rate_as_of": "2024-01-01",
    "balance_as_of": 130000.0,
    "start_date": "2021-01-01",
    "term_months": 300,
    "extra_payment_eur": 0.0,
    "rate_periods": [
        {"from_date": "2021-01-01", "annual_rate_pct": 2.0, "balance_eur": None},
        {"from_date": "2023-01-01", "annual_rate_pct": 3.5, "balance_eur": None},
    ],
}


def _db(tmp_path):
    return Database(str(tmp_path / "d.db"))


def _bank_rows(db, rows, category="Mortgage", desc="Example Bank loan"):
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    for day, amount in rows:
        db.create_spending_transaction(bank, day, desc, -amount, category=category)
    return bank


def _history_debits():
    return [
        (r["date"].isoformat(), r["payment"]) for r in loans.history_rows(HISTORY_LOAN)
    ]


def test_empty_database_gives_a_reason_not_an_error(tmp_path):
    out = mortgage.detect(_db(tmp_path))
    assert out["found"] is False and "category" in out["reason"]
    assert out["stretches"] == [] and out["form"] == {}


def test_too_few_months(tmp_path):
    db = _db(tmp_path)
    _bank_rows(db, [("2024-01-02", 500), ("2024-02-02", 500)])
    out = mortgage.detect(db)
    assert out["found"] is False and "at least 3" in out["reason"]


def test_irregular_debits_are_not_monthly(tmp_path):
    db = _db(tmp_path)
    _bank_rows(db, [("2023-01-02", 500), ("2023-06-02", 500), ("2023-12-02", 500)])
    assert "not monthly" in mortgage.detect(db)["reason"]


def test_amounts_that_never_repeat_find_no_payment(tmp_path):
    db = _db(tmp_path)
    _bank_rows(db, [(f"2024-0{m}-02", 100 * m) for m in range(1, 6)])
    assert "repeats" in mortgage.detect(db)["reason"]


def test_flat_payment_without_principal_has_no_rates(tmp_path):
    db = _db(tmp_path)
    _bank_rows(db, [(f"2024-0{m}-05", 612.34) for m in range(1, 7)])
    out = mortgage.detect(db)
    assert out["found"] is True
    assert out["monthly_payment_eur"] == 612.34
    assert out["first_payment_date"] == "2024-01-05"
    assert out["months_observed"] == 6
    assert len(out["stretches"]) == 1 and out["rate_periods"] == []
    assert out["stretches"][0]["annual_rate_pct"] is None
    assert "principal" in out["rates_reason"]
    # the loan would have started a month before the first payment
    assert out["form"] == {"start_date": "2023-12-05"}


def test_single_stretch_solves_the_rate(tmp_path):
    db = _db(tmp_path)
    pay = round(loans.monthly_payment(120000, 3.0, 240), 2)
    _bank_rows(db, [(f"2024-0{m}-05", pay) for m in range(1, 8)])
    out = mortgage.detect(db, principal=120000, term_months=240)
    assert out["stretches"][0]["annual_rate_pct"] == pytest.approx(3.0, abs=0.01)
    assert out["form"]["annual_rate_pct"] == out["stretches"][0]["annual_rate_pct"]
    assert out["form"]["principal"] == 120000.0 and out["form"]["term_months"] == 240
    assert "rate_as_of" not in out["form"]


def test_an_amount_change_gives_a_stretch_and_a_candidate_period(tmp_path):
    db = _db(tmp_path)
    _bank_rows(db, _history_debits())
    out = mortgage.detect(db, principal=150000, term_months=300)
    first, second = out["stretches"]
    assert first["from_date"] == "2021-02-01" and second["from_date"] == "2023-02-01"
    assert first["months"] == 24 and second["months"] == 12
    assert first["annual_rate_pct"] == pytest.approx(2.0, abs=0.02)
    assert second["annual_rate_pct"] == pytest.approx(3.5, abs=0.02)
    assert out["monthly_payment_eur"] == second["payment_eur"] > first["payment_eur"]
    # the earlier stretch is a candidate rate period, dated the payment before
    assert [(p["from_date"]) for p in out["rate_periods"]] == ["2021-01-01"]
    assert out["rate_periods"][0]["annual_rate_pct"] == first["annual_rate_pct"]
    form = out["form"]
    assert form["start_date"] == "2021-01-01"
    assert form["annual_rate_pct"] == second["annual_rate_pct"]
    assert form["rate_as_of"] == "2023-01-01"
    # the balance after 24 payments of the first stretch
    expected = loans.history_rows(HISTORY_LOAN)[23]["balance"]
    assert form["balance_as_of"] == pytest.approx(expected, abs=5.0)


def test_one_off_months_are_not_stretches(tmp_path):
    db = _db(tmp_path)
    rows = [(f"2024-0{m}-05", 500.0) for m in range(1, 8)]
    # an extra repayment in one month, and a newest month that has changed
    rows[3] = ("2024-04-05", 5500.0)
    rows.append(("2024-08-05", 650.0))
    _bank_rows(db, rows)
    out = mortgage.detect(db)
    assert out["found"] is True
    assert [s["payment_eur"] for s in out["stretches"]] == [500.0]
    assert out["stretches"][0]["months"] == 6  # 3 + 3, merged across the one-off
    assert any("newest month" in n for n in out["notes"])


def test_payment_that_fits_no_rate_is_explained(tmp_path):
    db = _db(tmp_path)
    _bank_rows(db, [(f"2024-0{m}-05", 100.0) for m in range(1, 7)])
    out = mortgage.detect(db, principal=120000, term_months=240)
    assert out["found"] is True
    assert out["stretches"][0]["annual_rate_pct"] is None
    assert "does not fit" in out["rates_reason"]
    assert "annual_rate_pct" not in out["form"]


def test_description_filter_and_a_disbursement_row(tmp_path):
    db = _db(tmp_path)
    pay = round(loans.monthly_payment(90000, 4.0, 180), 2)
    bank = _bank_rows(
        db, [(f"2024-0{m}-05", pay) for m in range(2, 9)], category="uncategorized"
    )
    db.create_spending_transaction(
        bank, "2024-01-05", "Example Bank loan payout", 90000, category="uncategorized"
    )
    db.create_spending_transaction(
        bank, "2024-03-09", "Example Gym", -39, category="uncategorized"
    )
    out = mortgage.detect(db, description="example bank loan", term_months=180)
    assert out["found"] is True and out["monthly_payment_eur"] == pay
    assert out["principal_eur"] == 90000.0
    assert out["principal_source"] == "disbursement"
    assert out["stretches"][0]["annual_rate_pct"] == pytest.approx(4.0, abs=0.01)
    assert any("incoming row" in n for n in out["notes"])


def test_detect_endpoint_is_not_captured_by_a_loan_id_route(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = _db(tmp_path)
    km = APIKeyManager(db)
    km.create_api_key(key_name="t", description="t", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    client = TestClient(app)
    r = client.get("/api/v1/networth/loans/detect", headers=HEADERS)
    assert r.status_code == 200 and r.json()["found"] is False
    pay = round(loans.monthly_payment(120000, 3.0, 240), 2)
    _bank_rows(db, [(f"2024-0{m}-05", pay) for m in range(1, 8)])
    r = client.get(
        "/api/v1/networth/loans/detect",
        params={"category": "Mortgage", "principal": 120000, "term_months": 240},
        headers=HEADERS,
    )
    body = r.json()
    assert r.status_code == 200 and body["found"] is True
    assert body["stretches"][0]["annual_rate_pct"] == pytest.approx(3.0, abs=0.01)
    bad = client.get(
        "/api/v1/networth/loans/detect", params={"principal": -5}, headers=HEADERS
    )
    assert bad.status_code == 422
    # nothing was created
    assert db.get_loans() == []
