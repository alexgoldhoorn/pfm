"""Loans & Mortgage page: what-if maths, overview, reconciliation, action item."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import Database
from portf_manager.services import loans, mortgage
from portf_manager.services.action_items import (
    check_mortgage_payment,
    checks_for_tests,
)

_KEY = "test-key-mortgage-abc123"
HEADERS = {"X-API-Key": _KEY}
TODAY = date(2026, 6, 15)
LOAN = {
    "name": "Example Mortgage",
    "principal": 200000.0,
    "currency": "EUR",
    "annual_rate_pct": 3.0,
    "rate_as_of": None,
    "balance_as_of": None,
    "start_date": "2020-01-01",
    "term_months": 300,
    "extra_payment_eur": 0.0,
}


def _client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "m.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="t", description="t", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db


def _db_with_loan(tmp_path, **over):
    db = Database(str(tmp_path / "m.db"))
    fields = {
        k: LOAN[k]
        for k in (
            "name",
            "principal",
            "annual_rate_pct",
            "start_date",
            "term_months",
        )
    }
    fields.update(over)
    lid = db.create_loan(**fields)
    return db, db.get_loan(lid)


def _debit(db, bank, day, amount, category="Mortgage"):
    db.create_spending_transaction(
        bank, day, "Example Bank debit", -amount, category=category
    )


# ── what-if maths ──────────────────────────────────────────────────────────


def test_what_if_without_changes_matches_the_baseline():
    out = loans.what_if(LOAN, today=TODAY)
    assert out["interest_saved_eur"] == 0
    assert out["months_saved"] == 0
    assert out["scenario"]["end_date"] == out["baseline"]["end_date"]
    assert out["invest"] is None


def test_term_mode_lump_sum_shortens_the_term():
    out = loans.what_if(LOAN, lump_sum=20000, today=TODAY)
    assert out["months_saved"] > 0
    assert out["interest_saved_eur"] > 0
    assert (
        out["scenario"]["monthly_payment_eur"] == out["baseline"]["monthly_payment_eur"]
    )
    assert out["scenario"]["end_date"] < out["baseline"]["end_date"]


def test_payment_mode_keeps_the_end_date_and_lowers_the_payment():
    out = loans.what_if(LOAN, lump_sum=20000, mode="payment", today=TODAY)
    assert out["scenario"]["end_date"] == out["baseline"]["end_date"]
    assert (
        out["scenario"]["monthly_payment_eur"] < out["baseline"]["monthly_payment_eur"]
    )
    assert out["interest_saved_eur"] > 0
    assert out["months_saved"] == 0


def test_extra_monthly_in_term_mode():
    out = loans.what_if(LOAN, extra_monthly=200, today=TODAY)
    assert out["months_saved"] > 0
    assert out["scenario"]["monthly_payment_eur"] == pytest.approx(
        out["baseline"]["monthly_payment_eur"] + 200
    )


def test_invest_comparison_is_labelled_and_compounds():
    out = loans.what_if(LOAN, lump_sum=10000, invest_return_pct=5.0, today=TODAY)
    inv = out["invest"]
    assert inv["contributed_eur"] == 10000
    assert inv["value_at_baseline_end_eur"] > 10000
    assert "not advice" in inv["note"]
    assert inv["gain_minus_interest_saved_eur"] == pytest.approx(
        inv["gain_eur"] - out["interest_saved_eur"], abs=0.01
    )


def test_zero_return_invests_nothing_extra():
    inv = loans.what_if(LOAN, lump_sum=10000, invest_return_pct=0.0, today=TODAY)[
        "invest"
    ]
    assert inv["value_at_baseline_end_eur"] == 10000 and inv["gain_eur"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lump_sum": -1},
        {"extra_monthly": -5},
        {"lump_sum": 10**9},
        {"mode": "bogus"},
        {"mode": "payment", "extra_monthly": 50},
        {"lump_date": "2060-01-01", "lump_sum": 1},
    ],
)
def test_what_if_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        loans.what_if(LOAN, today=TODAY, **kwargs)


def test_payment_split_and_schedule_principal():
    row = loans.payment_split(LOAN, "2026-06")
    assert row["payment"] == pytest.approx(row["principal"] + row["interest"])
    assert loans.payment_split(LOAN, "2019-12") is None
    year = loans.schedule(LOAN)[0]
    assert year["principal_paid_eur"] == pytest.approx(
        year["payments_eur"] - year["interest_paid_eur"]
    )
    # the first payment falls one month after the start
    assert year["payments_count"] == 11


# ── overview ───────────────────────────────────────────────────────────────


def test_summary_figures(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    s = mortgage.summary(loan, TODAY)
    assert s["balance_eur"] == loans.balance_on(loan, TODAY)
    assert s["months_left"] > 100
    assert s["anchored"] is False and s["anchor_note"] is None
    # Jan-Jun 2026 payments are paid by the 15th, the full year has 12
    assert s["principal_paid_this_year_eur"] < s["principal_scheduled_this_year_eur"]
    assert s["interest_remaining_eur"] > 0


def test_summary_notes_an_anchor(tmp_path):
    db, loan = _db_with_loan(tmp_path, rate_as_of="2025-01-01", balance_as_of=150000.0)
    s = mortgage.summary(loan, TODAY)
    assert s["anchored"] is True and "not known" in s["anchor_note"]


def test_equity_without_property_gives_a_reason(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    eq = mortgage.equity(db, loan, TODAY, lambda c: 1.0)
    assert eq["equity_eur"] is None and "property" in eq["reason"]


def test_equity_and_ltv(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    db.create_manual_asset(
        name="Example House", category="property", amount=400000.0, as_of="2026-01-01"
    )
    eq = mortgage.equity(db, loan, TODAY, lambda c: 1.0)
    balance = loans.balance_on(loan, TODAY)
    assert eq["equity_eur"] == pytest.approx(400000 - balance, abs=0.01)
    assert eq["ltv_pct"] == pytest.approx(balance / 4000, abs=0.1)
    assert eq["note"] is None and eq["property"][0]["as_of"] == "2026-01-01"


def test_equity_with_several_loans_uses_all_and_says_so(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    db.create_loan("Example Car", 10000, 5.0, "2025-01-01", 48)
    db.create_manual_asset(name="Example House", category="property", amount=400000)
    eq = mortgage.equity(db, loan, TODAY, lambda c: 1.0)
    assert "all of them" in eq["note"]
    assert eq["loans_balance_eur"] > loans.balance_on(loan, TODAY)


# ── reconciliation ─────────────────────────────────────────────────────────


def _bank_with_history(db, loan, months, extra=0.0, skip=()):
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    pay = loans.payment_of(loan)
    for m in months:
        if m in skip:
            continue
        _debit(db, bank, f"{m}-02", pay + (extra if m >= "2026-04" else 0))
    # statement reaches June 10th
    db.create_spending_transaction(
        bank, "2026-06-10", "Example Shop", -5, category="Groceries"
    )
    return bank


def test_reconciliation_needs_a_category(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    out = mortgage.reconciliation(db, loan, TODAY)
    assert out["rows"] is None and "category" in out["reason"]


def test_default_category_prefers_the_loan_name_then_mortgage(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    _debit(db, bank, "2026-01-02", 1, category="Mortgage")
    assert mortgage.default_category(db, loan) == "Mortgage"
    _debit(db, bank, "2026-01-02", 1, category="example mortgage")
    assert mortgage.default_category(db, loan) == "example mortgage"


def test_ok_differs_missing_and_pending(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    _bank_with_history(db, loan, months, extra=50.0, skip=("2026-02",))
    rec = mortgage.reconciliation(db, loan, TODAY)
    by = {r["month"]: r for r in rec["rows"]}
    assert by["2026-01"]["status"] == "ok"
    assert by["2026-02"]["status"] == "missing"
    assert by["2026-03"]["status"] == "ok"
    assert by["2026-04"]["status"] == "differs"
    assert by["2026-04"]["difference_eur"] == pytest.approx(50.0)
    # June is not covered by the statement yet: pending, never missing
    assert by["2026-06"]["status"] == "pending"
    assert rec["hint"] is not None and "rate may have been revised" in rec["hint"]
    assert rec["category"] == "Mortgage"


def test_no_hint_for_a_single_differing_month(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    pay = loans.payment_of(loan)
    for m in ("2026-02", "2026-03", "2026-04"):
        _debit(db, bank, f"{m}-02", pay)
    _debit(db, bank, "2026-05-02", pay + 30)
    db.create_spending_transaction(bank, "2026-06-10", "Shop", -5, category="Food")
    assert mortgage.reconciliation(db, loan, TODAY)["hint"] is None


def test_nothing_imported_is_never_missing(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    db.get_or_create_portfolio("Example Bank", account_type="bank")
    rec = mortgage.reconciliation(db, loan, TODAY, category="Mortgage")
    assert all(r["status"] == "pending" for r in rec["rows"])


def test_months_before_the_anchor_come_back_separately(tmp_path):
    db, loan = _db_with_loan(tmp_path, rate_as_of="2026-03-01", balance_as_of=150000.0)
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    _debit(db, bank, "2026-01-02", 700)
    _debit(db, bank, "2026-04-02", loans.payment_of(loan))
    rec = mortgage.reconciliation(db, loan, TODAY, category="Mortgage")
    assert rec["rows"][0]["month"] == "2026-04"
    assert rec["before_anchor"] == [{"month": "2026-01", "actual_eur": 700.0}]


def test_action_item_for_a_differing_complete_month(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    _bank_with_history(
        db, loan, ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"], extra=50
    )
    items = check_mortgage_payment(db, today=TODAY)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == f"mortgage:payment-differs:{loan['id']}"
    assert "update the rate and balance" in item["title"]
    assert item["link_page"] == "loans"
    assert check_mortgage_payment in checks_for_tests()


def test_no_action_item_when_payments_match_or_no_category(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    assert check_mortgage_payment(db, today=TODAY) == []
    _bank_with_history(
        db, loan, ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    )
    assert check_mortgage_payment(db, today=TODAY) == []


# ── API ────────────────────────────────────────────────────────────────────


def test_overview_endpoint_with_one_loan(tmp_path):
    client, db = _client(tmp_path)
    lid = client.post(
        "/api/v1/networth/loans",
        json={
            k: LOAN[k]
            for k in (
                "name",
                "principal",
                "annual_rate_pct",
                "start_date",
                "term_months",
            )
        },
        headers=HEADERS,
    ).json()["id"]
    r = client.get(f"/api/v1/networth/loans/{lid}/overview", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert {"summary", "equity", "schedule", "reconciliation"} <= set(body)
    assert body["summary"]["id"] == lid
    assert body["reconciliation"]["rows"] is None
    assert (
        client.get("/api/v1/networth/loans/999/overview", headers=HEADERS).status_code
        == 404
    )


def test_what_if_endpoint_and_validation(tmp_path):
    client, db = _client(tmp_path)
    lid = client.post(
        "/api/v1/networth/loans",
        json={
            k: LOAN[k]
            for k in (
                "name",
                "principal",
                "annual_rate_pct",
                "start_date",
                "term_months",
            )
        },
        headers=HEADERS,
    ).json()["id"]
    url = f"/api/v1/networth/loans/{lid}/what-if"
    ok = client.get(url + "?lump_sum=10000&invest_return_pct=4", headers=HEADERS)
    assert ok.status_code == 200
    assert ok.json()["months_saved"] > 0 and ok.json()["invest"] is not None
    for bad in (
        "?lump_sum=-1",
        "?lump_sum=999999999",
        "?mode=other",
        "?mode=payment&extra_monthly=10",
        "?lump_date=notadate",
    ):
        assert client.get(url + bad, headers=HEADERS).status_code == 422, bad
    assert (
        client.get("/api/v1/networth/loans/999/what-if", headers=HEADERS).status_code
        == 404
    )


def test_partial_put_keeps_other_fields_and_null_clears(tmp_path):
    client, db = _client(tmp_path)
    body = {
        k: LOAN[k]
        for k in ("name", "principal", "annual_rate_pct", "start_date", "term_months")
    }
    body.update(lender="Example Bank", rate_as_of="2026-01-01", balance_as_of=150000)
    lid = client.post("/api/v1/networth/loans", json=body, headers=HEADERS).json()["id"]
    r = client.put(
        f"/api/v1/networth/loans/{lid}", json={"annual_rate_pct": 4.0}, headers=HEADERS
    )
    assert r.status_code == 200
    loan = db.get_loan(lid)
    assert loan["annual_rate_pct"] == 4.0 and loan["name"] == "Example Mortgage"
    assert loan["lender"] == "Example Bank" and loan["balance_as_of"] == 150000
    client.put(
        f"/api/v1/networth/loans/{lid}",
        json={"lender": None, "balance_as_of": None, "rate_as_of": None},
        headers=HEADERS,
    )
    loan = db.get_loan(lid)
    assert loan["lender"] is None and loan["balance_as_of"] is None
    assert loan["rate_as_of"] is None and loan["principal"] == 200000
    # a required field cannot be nulled; unknown loan is 404
    assert (
        client.put(
            f"/api/v1/networth/loans/{lid}", json={"name": None}, headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/networth/loans/999", json={"notes": "x"}, headers=HEADERS
        ).status_code
        == 404
    )


def test_summary_borrowed_and_repaid(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    s = mortgage.summary(loan, TODAY)
    assert s["principal_eur"] == 200000.0
    assert s["start_date"] == "2020-01-01" and s["term_months"] == 300
    assert s["repaid_eur"] == pytest.approx(200000.0 - s["balance_eur"], abs=0.01)
    assert s["repaid_pct"] == pytest.approx(s["repaid_eur"] / 2000, abs=0.05)


def test_overview_lists_every_monthly_payment(tmp_path):
    db, loan = _db_with_loan(tmp_path)
    ov = mortgage.overview(db, loan, lambda c: 1.0, today=TODAY)
    pays = ov["payments"]
    assert len(pays) == 300
    assert pays[0]["date"] == "2020-02-01" and pays[0]["paid"] is True
    assert pays[-1]["balance"] == 0.0 and pays[-1]["paid"] is False
    # the yearly table and the monthly list agree
    y2026 = next(r for r in ov["schedule"] if r["year"] == 2026)
    in_2026 = [p for p in pays if p["date"].startswith("2026")]
    assert sum(p["interest"] for p in in_2026) == pytest.approx(
        y2026["interest_paid_eur"], abs=0.01
    )
