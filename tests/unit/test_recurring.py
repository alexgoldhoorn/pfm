"""Tests for recurring-charge detection."""

from datetime import date

from portf_manager.services.recurring import detect_recurring

_ids = iter(range(1, 100000))


def _tx(day, amount, merchant="EXAMPLE STREAMING", pid=1, **kw):
    return {
        "id": next(_ids),
        "portfolio_id": pid,
        "date": day,
        "amount": amount,
        "currency": kw.get("currency", "EUR"),
        "merchant": merchant,
        "description": kw.get("description", merchant),
        "category": kw.get("category", "Subscriptions"),
        "is_transfer": kw.get("is_transfer", 0),
    }


def _monthly(amounts, start_month=1, merchant="EXAMPLE STREAMING", pid=1, **kw):
    return [
        _tx(f"2026-{start_month + i:02d}-15", -a, merchant=merchant, pid=pid, **kw)
        for i, a in enumerate(amounts)
    ]


def test_monthly_subscription_detected():
    rows = _monthly([9.99, 9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.merchant == "EXAMPLE STREAMING"
    assert s.cadence == "monthly"
    assert s.occurrences == 4
    assert s.next_expected == "2026-05-15"
    assert s.typical_amount == 9.99
    assert s.annual_amount == round(9.99 * 12, 2)
    assert s.status == "active"
    assert s.price_change_pct is None


def test_price_increase_flagged():
    rows = _monthly([10.0, 10.0, 10.0, 12.0])
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.price_change_pct == 20.0


def test_varying_bill_gets_no_price_change():
    # Every charge is within ±25% of the median, so it is still recurring,
    # but the earlier charges are not flat, so a rise is just noise.
    rows = _monthly([50.0, 58.0, 46.0, 57.0], merchant="EXAMPLE UTILITY")
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.price_change_pct is None


def test_price_change_needs_two_prior_charges():
    rows = [_tx("2025-03-01", -100.0), _tx("2026-03-02", -120.0)]
    [s] = detect_recurring(rows, {1: date(2026, 3, 10)})
    assert s.cadence == "yearly"
    assert s.price_change_pct is None


def test_price_change_with_zero_previous_amount_is_none():
    # Sub-cent charges round to 0.00, which must not divide by zero.
    rows = _monthly([0.001, 0.001, 0.001, 10.0])
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.price_change_pct is None


def test_price_change_uses_run_since_last_change_not_whole_history():
    # An earlier price change (8 -> 10) must not blind the flatness check
    # to the most recent one (10 -> 12): only the run of charges since the
    # last change (the three 10.0s) matters.
    rows = _monthly([8.0, 8.0, 8.0, 10.0, 10.0, 10.0, 12.0])
    [s] = detect_recurring(rows, {1: date(2026, 8, 1)})
    assert s.price_change_pct == 20.0


def test_price_change_needs_a_three_charge_flat_run():
    # The anchor (57) has only one earlier charge within 2% of it (58), a
    # flat run of two: too short to call the 64 a price rise on a utility.
    rows = _monthly([50.0, 58.0, 57.0, 64.0], merchant="EXAMPLE UTILITY")
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.price_change_pct is None


def test_price_change_run_of_two_is_not_enough():
    rows = _monthly([10.0, 10.0, 12.0])
    [s] = detect_recurring(rows, {1: date(2026, 3, 20)})
    assert s.price_change_pct is None


def test_irregular_amounts_are_not_recurring():
    rows = [
        _tx("2026-01-03", -12.0, merchant="EXAMPLE SUPERMARKET"),
        _tx("2026-01-10", -85.0, merchant="EXAMPLE SUPERMARKET"),
        _tx("2026-01-17", -30.0, merchant="EXAMPLE SUPERMARKET"),
        _tx("2026-01-24", -140.0, merchant="EXAMPLE SUPERMARKET"),
    ]
    assert detect_recurring(rows, {1: date(2026, 1, 30)}) == []


def test_irregular_intervals_are_not_recurring():
    rows = [
        _tx("2026-01-01", -10.0),
        _tx("2026-01-04", -10.0),
        _tx("2026-02-20", -10.0),
        _tx("2026-02-22", -10.0),
    ]
    assert detect_recurring(rows, {1: date(2026, 3, 1)}) == []


def test_weekly_detected():
    rows = [_tx(f"2026-01-{d:02d}", -5.0) for d in (5, 12, 19, 26)]
    [s] = detect_recurring(rows, {1: date(2026, 1, 28)})
    assert s.cadence == "weekly"
    assert s.next_expected == "2026-02-02"


def test_yearly_needs_only_two():
    rows = [_tx("2025-03-01", -120.0), _tx("2026-03-02", -120.0)]
    [s] = detect_recurring(rows, {1: date(2026, 3, 10)})
    assert s.cadence == "yearly"
    assert s.next_expected == "2027-03-02"


def test_two_monthly_charges_are_not_enough():
    assert detect_recurring(_monthly([9.99, 9.99]), {1: date(2026, 2, 20)}) == []


def test_same_day_charges_merge():
    rows = _monthly([5.0, 5.0, 5.0, 5.0])
    rows.append(_tx("2026-04-15", -5.0))
    [s] = detect_recurring(rows, {1: date(2026, 4, 20)})
    assert s.occurrences == 4
    assert s.last_amount == 10.0
    assert len(s.transaction_ids) == 5


def test_not_missed_when_statement_not_imported_yet():
    rows = _monthly([9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 3, 31)})
    assert s.next_expected == "2026-04-15"
    assert s.status == "active"


def test_missed_when_statement_covers_due_date():
    rows = _monthly([9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 4, 25)})
    assert s.status == "missed"


def test_ended_after_two_missing_cycles():
    rows = _monthly([9.99, 9.99, 9.99])
    [s] = detect_recurring(rows, {1: date(2026, 6, 1)})
    assert s.status == "ended"


def test_second_due_date_computed_from_last_charge_not_clamped_next():
    # last charge on the 31st -> next_expected clamps to Feb 28 (2026 isn't
    # leap). The second cycle must be measured two months from the last
    # charge itself (Mar 31), not one more month from the clamped Feb 28
    # (which would wrongly land on Mar 28).
    rows = [
        _tx("2025-11-30", -9.99),
        _tx("2025-12-31", -9.99),
        _tx("2026-01-31", -9.99),
    ]
    [s] = detect_recurring(rows, {1: date(2026, 4, 2)})
    assert s.next_expected == "2026-02-28"
    assert s.status == "missed"


def test_second_due_date_not_shortened_by_clamped_next_expected():
    # Same series as above, imported a day later: computing the second
    # cycle from the already-clamped next_expected (Feb 28 -> Mar 28)
    # would call this "ended" three days early. Two full months from the
    # actual last charge (Jan 31 -> Mar 31) still calls it "missed".
    rows = [
        _tx("2025-11-30", -9.99),
        _tx("2025-12-31", -9.99),
        _tx("2026-01-31", -9.99),
    ]
    [s] = detect_recurring(rows, {1: date(2026, 4, 3)})
    assert s.status == "missed"


def test_income_and_transfers_ignored():
    rows = [_tx(f"2026-0{m}-15", 9.99) for m in (1, 2, 3)]
    rows += [_tx(f"2026-0{m}-15", -9.99, is_transfer=1) for m in (1, 2, 3)]
    assert detect_recurring(rows, {1: date(2026, 3, 20)}) == []


def test_accounts_are_separate_series():
    rows = _monthly([9.99] * 3, pid=1) + _monthly([9.99] * 3, pid=2)
    series = detect_recurring(rows, {1: date(2026, 3, 20), 2: date(2026, 3, 20)})
    assert sorted(s.portfolio_id for s in series) == [1, 2]


def test_annual_amount_uses_rounded_typical_at_half_cent():
    # Median of these four amounts lands exactly on a half cent (10.005).
    # annual_amount must be derived from the rounded, displayed
    # typical_amount (10.0), not the raw fractional median, or the two
    # figures visibly disagree (120.06 vs 10.0 * 12).
    rows = _monthly([10.00, 10.00, 10.01, 10.01])
    [s] = detect_recurring(rows, {1: date(2026, 5, 1)})
    assert s.typical_amount == 10.0
    assert s.annual_amount == 120.0


def test_sort_missed_first():
    missed = _monthly([5.0] * 3, merchant="A")
    active = _monthly([50.0] * 3, start_month=2, merchant="B")
    series = detect_recurring(missed + active, {1: date(2026, 4, 25)})
    assert [s.merchant for s in series] == ["A", "B"]
    assert [s.status for s in series] == ["missed", "active"]


def test_quarterly_cadence_detected():
    rows = [
        _tx("2026-01-10", -30.0, merchant="EXAMPLE INSURANCE"),
        _tx("2026-04-10", -30.0, merchant="EXAMPLE INSURANCE"),
        _tx("2026-07-10", -30.0, merchant="EXAMPLE INSURANCE"),
    ]
    [s] = detect_recurring(rows, {1: date(2026, 7, 20)})
    assert s.cadence == "quarterly"
    assert s.next_expected == "2026-10-10"
    assert s.annual_amount == 120.0


def test_merchants_differing_only_in_case_merge_into_one_series():
    rows = _monthly([9.99, 9.99], merchant="Example Streaming")
    rows += _monthly([9.99], start_month=3, merchant="EXAMPLE STREAMING")
    [s] = detect_recurring(rows, {1: date(2026, 3, 20)})
    assert s.occurrences == 3


def test_blank_merchant_falls_back_to_description():
    rows = [
        _tx(f"2026-{m:02d}-15", -9.99, merchant="", description="EXAMPLE DESC")
        for m in (1, 2, 3)
    ]
    [s] = detect_recurring(rows, {1: date(2026, 3, 20)})
    assert s.merchant == "EXAMPLE DESC"


def test_non_eur_currency_is_kept():
    rows = _monthly([9.99, 9.99, 9.99], currency="USD")
    [s] = detect_recurring(rows, {1: date(2026, 3, 20)})
    assert s.currency == "USD"
