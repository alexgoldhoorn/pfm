"""Tests for the pure transfer-matching logic."""

from portf_manager.services.transfer_matcher import (
    find_transfer_match,
    find_all_transfer_matches,
)


def _spending(id, portfolio_id, date, amount, currency="EUR", is_transfer=False):
    return {
        "id": id,
        "portfolio_id": portfolio_id,
        "date": date,
        "amount": amount,
        "currency": currency,
        "is_transfer": is_transfer,
    }


def _booking(id, portfolio_id, date, action, amount, currency="EUR"):
    return {
        "id": id,
        "portfolio_id": portfolio_id,
        "date": date,
        "action": action,
        "amount": amount,
        "currency": currency,
    }


def test_matches_outflow_to_inflow_same_amount():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=500.0)
    match = find_transfer_match(row, [candidate], [])
    assert match is not None
    assert match.link_type == "spending"
    assert match.link_id == 2


def test_no_match_same_account():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=10, date="2026-01-11", amount=500.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_outside_window():
    row = _spending(1, portfolio_id=10, date="2026-01-01", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-10", amount=500.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_match_at_window_boundary():
    row = _spending(1, portfolio_id=10, date="2026-01-01", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-04", amount=500.0)
    assert find_transfer_match(row, [candidate], []) is not None


def test_no_match_different_amount():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=400.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_different_currency():
    row = _spending(
        1, portfolio_id=10, date="2026-01-10", amount=-500.0, currency="EUR"
    )
    candidate = _spending(
        2, portfolio_id=20, date="2026-01-11", amount=500.0, currency="USD"
    )
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_same_sign():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(2, portfolio_id=20, date="2026-01-11", amount=-500.0)
    assert find_transfer_match(row, [candidate], []) is None


def test_no_match_candidate_already_transfer():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    candidate = _spending(
        2, portfolio_id=20, date="2026-01-11", amount=500.0, is_transfer=True
    )
    assert find_transfer_match(row, [candidate], []) is None


def test_matches_outflow_to_deposit_booking():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-2000.0)
    booking = _booking(
        5, portfolio_id=30, date="2026-01-10", action="Deposit", amount=2000.0
    )
    match = find_transfer_match(row, [], [booking])
    assert match is not None
    assert match.link_type == "booking"
    assert match.link_id == 5


def test_inflow_does_not_match_booking():
    """Only an outflow can match a brokerage Deposit — an inflow row would mean
    money left the brokerage account, which bookings can't represent here."""
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=2000.0)
    booking = _booking(
        5, portfolio_id=30, date="2026-01-10", action="Deposit", amount=2000.0
    )
    assert find_transfer_match(row, [], [booking]) is None


def test_withdrawal_booking_not_matched():
    row = _spending(1, portfolio_id=10, date="2026-01-10", amount=-2000.0)
    booking = _booking(
        5, portfolio_id=30, date="2026-01-10", action="Withdrawal", amount=2000.0
    )
    assert find_transfer_match(row, [], [booking]) is None


def test_find_all_transfer_matches_no_double_linking():
    """Two rows in the same batch can't both link to the same single counterpart."""
    rows = [
        _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0),
        _spending(2, portfolio_id=10, date="2026-01-10", amount=-500.0),
    ]
    unlinked = rows + [_spending(3, portfolio_id=20, date="2026-01-10", amount=500.0)]
    matches = find_all_transfer_matches(rows, unlinked, [])
    assert len(matches) == 1
    assert matches[0].link_id == 3


def test_find_all_transfer_matches_multiple_pairs():
    rows = [
        _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0),
        _spending(2, portfolio_id=10, date="2026-01-11", amount=-300.0),
    ]
    unlinked = rows + [
        _spending(3, portfolio_id=20, date="2026-01-10", amount=500.0),
        _spending(4, portfolio_id=20, date="2026-01-11", amount=300.0),
    ]
    matches = find_all_transfer_matches(rows, unlinked, [])
    assert {m.spending_id for m in matches} == {1, 2}
    assert {m.link_id for m in matches} == {3, 4}


def test_find_all_transfer_matches_source_row_not_reused_as_counterpart():
    """A row already resolved as the *source* of one match must not itself be
    claimed as the *counterpart* for a later, unrelated row in the same batch.

    row1 (-500, portfolio 10) has a genuine pre-existing counterpart row3
    (+500, portfolio 30) that is NOT part of the batch being matched — it's
    only present in the unlinked pool. row6 (+500, portfolio 60) is an
    unrelated row in the batch with the same date/amount as row1 but no
    real counterpart of its own. Before the fix, row1 (already matched to
    row3) could still be picked up as row6's counterpart.
    """
    row1 = _spending(1, portfolio_id=10, date="2026-01-10", amount=-500.0)
    row3 = _spending(3, portfolio_id=30, date="2026-01-10", amount=500.0)
    row6 = _spending(6, portfolio_id=60, date="2026-01-10", amount=500.0)
    rows = [row1, row6]
    unlinked = [row1, row3, row6]

    matches = find_all_transfer_matches(rows, unlinked, [])

    assert len(matches) == 1
    assert matches[0].spending_id == 1
    assert matches[0].link_id == 3
    assert all(m.spending_id != 6 for m in matches)


def test_find_all_transfer_matches_skips_already_transfer_rows():
    rows = [
        _spending(
            1, portfolio_id=10, date="2026-01-10", amount=-500.0, is_transfer=True
        )
    ]
    matches = find_all_transfer_matches(rows, rows, [])
    assert matches == []
