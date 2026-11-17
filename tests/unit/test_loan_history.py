"""Loan rate history (schema v45): maths, paid to date, reconciliation, API."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from portf_manager.database import DATABASE_VERSION, Database
from portf_manager.services import loans, mortgage

_KEY = "test-key-loan-history-abc123"
HEADERS = {"X-API-Key": _KEY}
TODAY = date(2026, 6, 15)

# an anchored loan: 20 years from 2020, the lender's balance recorded in 2025
BASE = {
    "name": "Example Mortgage",
    "principal": 100000.0,
    "currency": "EUR",
    "annual_rate_pct": 3.0,
    "rate_as_of": "2025-01-01",
    "balance_as_of": 80000.0,
    "start_date": "2020-01-01",
    "term_months": 240,
    "extra_payment_eur": 0.0,
}
PERIODS = [
    {"from_date": "2020-01-01", "annual_rate_pct": 2.4, "balance_eur": None},
    {"from_date": "2023-01-01", "annual_rate_pct": 3.6, "balance_eur": None},
]


def _loan(periods=None, **over):
    loan = {**BASE, **over}
    if periods is not None:
        loan["rate_periods"] = periods
    return loan


# ── maths ──────────────────────────────────────────────────────────────────


def test_no_periods_is_exactly_the_old_behaviour():
    plain = _loan()
    empty = _loan(periods=[])
    assert loans.history_rows(plain) == []
    assert loans.schedule(plain) == loans.schedule(empty)
    assert loans.payment_rows(plain) == loans.payment_rows(empty)
    # no extra keys on the yearly rows without history
    assert all("history" not in row for row in loans.schedule(plain))


def test_periods_leave_the_forward_projection_unchanged():
    plain, with_history = _loan(), _loan(periods=PERIODS)
    assert loans.payment_rows(plain) == loans.payment_rows(with_history)
    assert loans.balance_on(plain, TODAY) == loans.balance_on(with_history, TODAY)
    assert loans.end_date(plain) == loans.end_date(with_history)
    assert loans.payment_of(plain) == loans.payment_of(with_history)
    forward = [r for r in loans.schedule(with_history) if not r["history"]]
    stripped = [
        {k: v for k, v in r.items() if not k.startswith("history")} for r in forward
    ]
    assert stripped == loans.schedule(plain)


def test_history_payments_follow_the_rate_revisions():
    rows = loans.history_rows(_loan(periods=PERIODS))
    # 60 monthly payments from 2020-02-01 up to the anchor 2025-01-01
    assert len(rows) == 60
    assert rows[0]["date"] == date(2020, 2, 1) and rows[-1]["date"] == date(2025, 1, 1)
    first = round(loans.monthly_payment(100000, 2.4, 240), 2)
    assert rows[0]["payment"] == first == rows[35]["payment"]
    assert rows[0]["interest"] == round(100000 * 0.024 / 12, 2)
    # the revision applies from the payment after 2023-01-01, over 204 months left
    revised = round(loans.monthly_payment(rows[35]["balance"], 3.6, 204), 2)
    assert rows[36]["payment"] == revised == rows[-1]["payment"]
    assert revised > first


def test_a_period_balance_reanchors_the_history():
    periods = [
        PERIODS[0],
        {"from_date": "2023-01-01", "annual_rate_pct": 3.6, "balance_eur": 90000.0},
    ]
    rows = loans.history_rows(_loan(periods=periods))
    assert rows[36]["payment"] == round(loans.monthly_payment(90000, 3.6, 204), 2)
    assert rows[36]["interest"] == round(90000 * 0.036 / 12, 2)


def test_periods_need_an_anchor_and_are_cut_at_it():
    unanchored = _loan(periods=PERIODS, rate_as_of=None, balance_as_of=None)
    assert loans.history_rows(unanchored) == []
    late = PERIODS + [
        {"from_date": "2025-06-01", "annual_rate_pct": 9.0, "balance_eur": None}
    ]
    assert loans.history_rows(_loan(periods=late)) == loans.history_rows(
        _loan(periods=PERIODS)
    )


def test_paid_to_date_adds_history_and_forward_rows():
    loan = _loan(periods=PERIODS)
    paid = loans.paid_to_date(loan, TODAY)
    hist = loans.history_rows(loan)
    fwd = [r for r in loans.payment_rows(loan) if r["date"] <= TODAY]
    assert paid["complete"] is True
    assert paid["payments_count"] == len(hist) + len(fwd) == 60 + 17
    assert paid["interest_eur"] == round(sum(r["interest"] for r in hist + fwd), 2)
    assert paid["principal_eur"] == round(sum(r["principal"] for r in hist + fwd), 2)
    assert paid["total_eur"] == round(paid["interest_eur"] + paid["principal_eur"], 2)
    assert paid["from_date"] == "2020-02-01"
    # the history balance against the lender's recorded balance
    assert paid["anchor_gap_eur"] == round(hist[-1]["balance"] - 80000.0, 2)


def test_paid_to_date_is_incomplete_without_history():
    paid = loans.paid_to_date(_loan(), TODAY)
    assert paid["complete"] is False and paid["history_payments"] == 0
    assert paid["from_date"] == "2025-02-01"
    # an unanchored loan runs from its start, so it is complete without periods
    whole = loans.paid_to_date(_loan(rate_as_of=None, balance_as_of=None), TODAY)
    assert whole["complete"] is True and whole["from_date"] == "2020-02-01"


def test_schedule_marks_history_years():
    rows = loans.schedule(_loan(periods=PERIODS))
    assert [r["year"] for r in rows][:6] == [2020, 2021, 2022, 2023, 2024, 2025]
    assert [r["history"] for r in rows][:6] == [True] * 5 + [False]
    assert rows[0]["payments_count"] == 11
    anchor_year = rows[5]
    # the 2025-01-01 payment is history, the other eleven are scheduled
    assert anchor_year["history_payments_count"] == 1
    assert anchor_year["payments_count"] == 11


def test_solve_rate_inverts_the_annuity():
    pay = round(loans.monthly_payment(150000, 3.25, 300), 2)
    assert loans.solve_rate(150000, pay, 300) == pytest.approx(3.25, abs=0.005)
    # a payment below principal / months needs a negative rate
    assert loans.solve_rate(150000, 400, 300) is None
    assert loans.solve_rate(0, 100, 10) is None


# ── rate-change what-if ────────────────────────────────────────────────────

PLAIN = {
    "principal": 120000.0,
    "currency": "EUR",
    "annual_rate_pct": 3.0,
    "rate_as_of": None,
    "balance_as_of": None,
    "start_date": "2020-01-01",
    "term_months": 240,
    "extra_payment_eur": 0.0,
}


def test_rate_change_matches_a_hand_computed_annuity():
    res = loans.what_if(PLAIN, new_rate_pct=4.5, rate_from="2026-01-01")
    balance = loans.balance_on(PLAIN, date(2026, 1, 1))
    n, r = 168, 0.045 / 12
    expected = round(balance * r / (1 - (1 + r) ** -n), 2)
    rc = res["rate_change"]
    assert rc["payment_after_eur"] == expected == res["scenario"]["monthly_payment_eur"]
    assert rc["payment_before_eur"] == loans.payment_of(PLAIN)
    assert rc["payment_diff_eur"] == round(expected - loans.payment_of(PLAIN), 2)
    assert rc["previous_rate_pct"] == 3.0 and rc["balance_eur"] == round(balance, 2)
    # the interest over the remaining term is about payments less the balance
    assert res["scenario"]["interest_remaining_eur"] == pytest.approx(
        expected * n - balance, abs=2.0
    )
    assert rc["interest_diff_eur"] == round(
        res["scenario"]["interest_remaining_eur"]
        - res["baseline"]["interest_remaining_eur"],
        2,
    )
    assert rc["interest_diff_eur"] > 0
    assert res["interest_saved_eur"] == -rc["interest_diff_eur"]
    assert res["months_saved"] == 0 and res["invest"] is None


def test_rate_change_in_points_equals_the_new_rate():
    by_points = loans.what_if(PLAIN, rate_change_pts=1.5, rate_from="2026-01-01")
    by_rate = loans.what_if(PLAIN, new_rate_pct=4.5, rate_from="2026-01-01")
    assert by_points == by_rate


def test_a_lower_rate_lowers_the_payment_and_the_interest():
    res = loans.what_if(PLAIN, rate_change_pts=-1.0, rate_from="2026-01-01")
    assert res["rate_change"]["payment_diff_eur"] < 0
    assert res["interest_saved_eur"] > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"new_rate_pct": 4.0, "lump_sum": 1000},
        {"new_rate_pct": 4.0, "extra_monthly": 50},
        {"new_rate_pct": 4.0, "mode": "payment"},
        {"new_rate_pct": 4.0, "invest_return_pct": 5},
        {"new_rate_pct": 4.0, "rate_change_pts": 1.0},
        {"rate_change_pts": -5.0},
        {"new_rate_pct": 120},
        {"rate_from": "2026-01-01"},
        {"new_rate_pct": 4.0, "rate_from": "2060-01-01"},
    ],
)
def test_rate_change_rejects_bad_combinations(kwargs):
    with pytest.raises(ValueError):
        loans.what_if(PLAIN, **kwargs)


# ── database and API ───────────────────────────────────────────────────────


def _client(tmp_path):
    from portf_server.app import app
    from portf_server.auth_middleware import APIKeyManager
    from portf_server.dependencies import get_api_key_manager, get_database

    db = Database(str(tmp_path / "h.db"))
    km = APIKeyManager(db)
    km.create_api_key(key_name="t", description="t", raw_key=_KEY)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_api_key_manager] = lambda: km
    return TestClient(app), db


def _make_loan(db, **over):
    fields = {
        k: BASE[k]
        for k in (
            "name",
            "principal",
            "annual_rate_pct",
            "rate_as_of",
            "balance_as_of",
            "start_date",
            "term_months",
        )
    }
    fields.update(over)
    return db.create_loan(**fields)


def _url(loan_id, tail=""):
    return f"/api/v1/networth/loans/{loan_id}{tail}"


def test_schema_has_the_table_and_version():
    assert DATABASE_VERSION >= 45


def test_period_crud_roundtrip(tmp_path):
    client, db = _client(tmp_path)
    lid = _make_loan(db)
    body = {"from_date": "2020-01-01", "annual_rate_pct": 2.4, "note": "fixed"}
    r = client.post(_url(lid, "/rate-periods"), json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    rows = client.get(_url(lid, "/rate-periods"), headers=HEADERS).json()
    assert [(p["id"], p["from_date"], p["balance_eur"]) for p in rows] == [
        (pid, "2020-01-01", None)
    ]
    r = client.put(
        _url(lid, f"/rate-periods/{pid}"),
        json={"annual_rate_pct": 2.6, "balance_eur": 99000},
        headers=HEADERS,
    )
    assert r.status_code == 200
    got = client.get(_url(lid, "/rate-periods"), headers=HEADERS).json()[0]
    assert got["annual_rate_pct"] == 2.6 and got["balance_eur"] == 99000
    assert got["note"] == "fixed"
    # an explicit null clears the balance, an omitted field stays
    client.put(
        _url(lid, f"/rate-periods/{pid}"), json={"balance_eur": None}, headers=HEADERS
    )
    got = client.get(_url(lid, "/rate-periods"), headers=HEADERS).json()[0]
    assert got["balance_eur"] is None and got["annual_rate_pct"] == 2.6
    assert (
        client.delete(_url(lid, f"/rate-periods/{pid}"), headers=HEADERS).status_code
        == 200
    )
    assert client.get(_url(lid, "/rate-periods"), headers=HEADERS).json() == []


@pytest.mark.parametrize(
    "body,status",
    [
        ({"from_date": "2019-12-31", "annual_rate_pct": 2.0}, 422),
        ({"from_date": "2025-01-02", "annual_rate_pct": 2.0}, 422),
        ({"from_date": "2021-01-01", "annual_rate_pct": -1}, 422),
        ({"from_date": "2021-01-01", "annual_rate_pct": 99}, 422),
        ({"from_date": "2021-01-01", "annual_rate_pct": 2, "balance_eur": 1e9}, 422),
        ({"from_date": "2020-01-01", "annual_rate_pct": 2.0}, 409),
    ],
)
def test_period_validation(tmp_path, body, status):
    client, db = _client(tmp_path)
    lid = _make_loan(db)
    db.create_loan_rate_period(lid, "2020-01-01", 2.4)
    r = client.post(_url(lid, "/rate-periods"), json=body, headers=HEADERS)
    assert r.status_code == status
    assert r.json()["detail"]


def test_period_needs_an_anchored_loan_and_known_ids(tmp_path):
    client, db = _client(tmp_path)
    lid = _make_loan(db, rate_as_of=None, balance_as_of=None)
    body = {"from_date": "2020-01-01", "annual_rate_pct": 2.0}
    r = client.post(_url(lid, "/rate-periods"), json=body, headers=HEADERS)
    assert r.status_code == 422 and "first" in r.json()["detail"]
    assert client.get(_url(999, "/rate-periods"), headers=HEADERS).status_code == 404
    other = _make_loan(db)
    pid = db.create_loan_rate_period(other, "2021-01-01", 2.0)
    # a period of another loan is not found under this one
    r = client.delete(_url(lid, f"/rate-periods/{pid}"), headers=HEADERS)
    assert r.status_code == 404


def test_duplicate_date_on_update_is_refused(tmp_path):
    client, db = _client(tmp_path)
    lid = _make_loan(db)
    db.create_loan_rate_period(lid, "2020-01-01", 2.4)
    pid = db.create_loan_rate_period(lid, "2022-01-01", 3.0)
    r = client.put(
        _url(lid, f"/rate-periods/{pid}"),
        json={"from_date": "2020-01-01"},
        headers=HEADERS,
    )
    assert r.status_code == 409
    # keeping its own date is fine
    r = client.put(
        _url(lid, f"/rate-periods/{pid}"),
        json={"from_date": "2022-01-01", "annual_rate_pct": 3.1},
        headers=HEADERS,
    )
    assert r.status_code == 200


def test_deleting_a_loan_removes_its_periods(tmp_path):
    client, db = _client(tmp_path)
    lid = _make_loan(db)
    pid = db.create_loan_rate_period(lid, "2020-01-01", 2.4)
    assert client.delete(_url(lid), headers=HEADERS).status_code == 200
    assert db.get_loan_rate_period(pid) is None


def test_overview_carries_periods_and_paid_to_date(tmp_path):
    client, db = _client(tmp_path)
    lid = _make_loan(db)
    plain = client.get(_url(lid, "/overview"), headers=HEADERS).json()
    assert plain["rate_periods"] == []
    assert plain["summary"]["has_history"] is False
    assert plain["summary"]["paid_to_date"]["complete"] is False
    db.create_loan_rate_period(lid, "2020-01-01", 2.4)
    db.create_loan_rate_period(lid, "2023-01-01", 3.6)
    ov = client.get(_url(lid, "/overview"), headers=HEADERS).json()
    assert [p["from_date"] for p in ov["rate_periods"]] == ["2020-01-01", "2023-01-01"]
    s = ov["summary"]
    assert s["has_history"] is True and s["paid_to_date"]["complete"] is True
    assert "rebuilt from the rate history" in s["anchor_note"]
    # the forward figures do not move
    for key in (
        "balance_eur",
        "monthly_payment_eur",
        "end_date",
        "months_left",
        "interest_remaining_eur",
    ):
        assert s[key] == plain["summary"][key]
    assert ov["schedule"][0]["history"] is True
    sched = client.get(_url(lid, "/schedule"), headers=HEADERS).json()
    assert sched == ov["schedule"]


def test_what_if_endpoint_takes_a_rate_change(tmp_path):
    client, db = _client(tmp_path)
    lid = _make_loan(db, rate_as_of=None, balance_as_of=None)
    r = client.get(
        _url(lid, "/what-if"),
        params={"rate_change_pts": 1.0, "rate_from": "2026-01-01"},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    assert r.json()["rate_change"]["new_rate_pct"] == 4.0
    r = client.get(
        _url(lid, "/what-if"),
        params={"new_rate_pct": 4, "lump_sum": 500},
        headers=HEADERS,
    )
    assert r.status_code == 422 and "cannot be combined" in r.json()["detail"]


# ── reconciliation before the anchor ───────────────────────────────────────


def _bank(db):
    bank = db.get_or_create_portfolio("Example Bank", account_type="bank")
    # a later statement row so months up to June 2026 count as covered
    db.create_spending_transaction(
        bank, "2026-06-10", "Example Shop", -5, category="Groceries"
    )
    return bank


def test_months_before_the_anchor_are_compared_with_the_history(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    lid = _make_loan(db)
    for p in PERIODS:
        db.create_loan_rate_period(lid, p["from_date"], p["annual_rate_pct"])
    loan = mortgage.with_periods(db, db.get_loan(lid))
    hist = {r["date"].strftime("%Y-%m"): r["payment"] for r in loans.history_rows(loan)}
    bank = _bank(db)
    for month, delta in (("2023-10", 0.0), ("2023-11", 25.0), ("2023-12", 0.5)):
        db.create_spending_transaction(
            bank, f"{month}-02", "Example Bank debit", -(hist[month] + delta),
            category="Mortgage",
        )  # fmt: skip
    recon = mortgage.reconciliation(db, loan, TODAY)
    before = {b["month"]: b for b in recon["before_anchor"]}
    assert before["2023-10"]["status"] == "ok"
    assert before["2023-11"]["status"] == "differs"
    assert before["2023-11"]["difference_eur"] == 25.0
    assert before["2023-12"]["status"] == "ok"
    assert before["2023-09"]["status"] == "missing"
    assert before["2023-09"]["scheduled_eur"] == hist["2023-09"]
    assert all(b["compared"] for b in before.values())
    # forward rows and the hint are untouched by the history
    assert [r["month"] for r in recon["rows"]][0] == "2025-02"


def test_before_anchor_keeps_its_old_shape_without_periods(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    lid = _make_loan(db)
    loan = mortgage.with_periods(db, db.get_loan(lid))
    bank = _bank(db)
    db.create_spending_transaction(
        bank, "2023-10-02", "Example Bank debit", -400, category="Mortgage"
    )
    recon = mortgage.reconciliation(db, loan, TODAY)
    assert recon["before_anchor"] == [{"month": "2023-10", "actual_eur": 400.0}]


# ── month-by-month payments ───────────────────────────────────────────────


def test_monthly_rows_put_the_history_before_the_anchor():
    rows = loans.monthly_rows(_loan(PERIODS), TODAY)
    hist = [r for r in rows if r["history"]]
    fwd = [r for r in rows if not r["history"]]
    assert hist and fwd
    # history runs from one month after the start up to the anchor, then the schedule
    assert hist[0]["date"] == "2020-02-01" and hist[-1]["date"] == "2025-01-01"
    assert fwd[0]["date"] == "2025-02-01"
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert len(fwd) == len(loans.payment_rows(_loan(PERIODS)))


def test_monthly_rows_without_periods_start_at_the_anchor():
    rows = loans.monthly_rows(_loan(), TODAY)
    assert rows[0]["date"] == "2025-02-01"
    assert not any(r["history"] for r in rows)


def test_monthly_rows_mark_what_is_paid():
    rows = loans.monthly_rows(_loan(), TODAY)
    paid = [r for r in rows if r["paid"]]
    assert paid[-1]["date"] == "2026-06-01"
    assert not any(r["paid"] for r in rows if r["date"] > "2026-06-15")
    r = paid[-1]
    assert r["principal"] == pytest.approx(r["payment"] - r["interest"], abs=0.01)
