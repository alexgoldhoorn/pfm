"""Tests for the loan amortisation service."""

from datetime import date

from portf_manager.services.loans import (
    balance_on,
    end_date,
    monthly_payment,
    payments_in_year,
    schedule,
)


def _loan(**over):
    loan = {
        "principal": 12000.0,
        "currency": "EUR",
        "annual_rate_pct": 0.0,
        "rate_as_of": None,
        "balance_as_of": None,
        "start_date": "2026-01-01",
        "term_months": 12,
        "extra_payment_eur": 0.0,
    }
    loan.update(over)
    return loan


def test_monthly_payment_zero_rate():
    assert monthly_payment(12000, 0, 12) == 1000


def test_monthly_payment_known_value():
    assert round(monthly_payment(100000, 3.0, 240), 2) == 554.60


def test_balance_before_start_is_principal():
    assert balance_on(_loan(), date(2025, 12, 31)) == 12000


def test_balance_after_end_is_zero():
    assert balance_on(_loan(), date(2027, 3, 1)) == 0


def test_balance_after_six_payments_zero_rate():
    assert balance_on(_loan(), date(2026, 7, 15)) == 6000


def test_payment_counts_on_anniversary_day():
    assert balance_on(_loan(), date(2026, 6, 30)) == 7000
    assert balance_on(_loan(), date(2026, 7, 1)) == 6000


def test_accepts_date_strings_and_dates():
    loan = _loan(start_date=date(2026, 1, 1))
    assert balance_on(loan, "2026-07-15") == 6000


def test_balance_with_interest_matches_annuity_formula():
    loan = _loan(principal=100000.0, annual_rate_pct=3.0, term_months=240)
    r = 0.03 / 12
    pay = monthly_payment(100000, 3.0, 240)
    expected = 100000 * (1 + r) ** 12 - pay * ((1 + r) ** 12 - 1) / r
    assert abs(balance_on(loan, date(2027, 1, 1)) - expected) < 1.0


def test_rate_change_anchors_on_balance_as_of():
    loan = _loan(
        principal=100000.0,
        annual_rate_pct=3.5,
        term_months=240,
        rate_as_of="2027-01-01",
        balance_as_of=96000.0,
    )
    assert balance_on(loan, date(2027, 1, 1)) == 96000
    # 228 payments remain after the anchor
    assert end_date(loan) == date(2046, 1, 1)
    assert balance_on(loan, date(2046, 1, 1)) == 0
    assert balance_on(loan, date(2045, 12, 31)) > 0


def test_extra_payment_shortens_schedule():
    loan = _loan(extra_payment_eur=1000.0)
    assert end_date(loan) == date(2026, 7, 1)
    assert balance_on(loan, date(2026, 7, 1)) == 0
    assert balance_on(loan, date(2026, 3, 1)) == 8000


def test_payments_in_year():
    assert payments_in_year(_loan(), 2026) == 11000
    assert payments_in_year(_loan(), 2027) == 1000
    assert payments_in_year(_loan(), 2028) == 0


def test_payments_in_year_after_end_is_zero():
    assert payments_in_year(_loan(extra_payment_eur=1000.0), 2027) == 0


def test_schedule_last_year_ends_at_zero():
    rows = schedule(_loan(principal=100000.0, annual_rate_pct=3.0, term_months=240))
    assert rows[0]["year"] == 2026
    assert rows[-1]["year"] == 2046
    assert rows[-1]["balance_end_eur"] == 0
    assert len(rows) == 21
    assert all(r["interest_paid_eur"] >= 0 for r in rows)


def test_schedule_zero_rate_totals():
    rows = schedule(_loan())
    assert rows[0] == {
        "year": 2026,
        "balance_end_eur": 1000,
        "interest_paid_eur": 0,
        "payments_eur": 11000,
    }
    assert rows[1]["balance_end_eur"] == 0
    assert rows[1]["payments_eur"] == 1000
