"""Tests for the running-balance continuity check."""

from portf_manager.parsers.generic_bank_csv_parser import SpendingRow
from portf_manager.services.balance_check import find_balance_breaks


def _row(day, amount, balance, desc="X"):
    return SpendingRow(date=day, description=desc, amount=amount, balance=balance)


def test_consistent_ascending_file_has_no_breaks():
    rows = [
        _row("2026-01-01", -10, 90),
        _row("2026-01-02", -5, 85),
        _row("2026-01-03", 15, 100),
    ]
    assert find_balance_breaks(rows) == []


def test_descending_file_is_consistent():
    rows = [
        _row("2026-01-03", 15, 100),
        _row("2026-01-02", -5, 85),
        _row("2026-01-01", -10, 90),
    ]
    assert find_balance_breaks(rows) == []


def test_missing_row_inside_file_reports_once():
    rows = [
        _row("2026-01-01", -10, 90),
        _row("2026-01-03", -5, 80, desc="AFTER GAP"),
        _row("2026-01-04", -5, 75),
    ]
    breaks = find_balance_breaks(rows)
    assert len(breaks) == 1
    b = breaks[0]
    assert (b.date, b.description, b.expected, b.actual, b.kind) == (
        "2026-01-03",
        "AFTER GAP",
        85.0,
        80.0,
        "within_file",
    )


def test_gap_before_file_uses_opening_balance():
    rows = [_row("2026-02-01", -10, 90)]
    breaks = find_balance_breaks(rows, opening_balance=120.0)
    assert len(breaks) == 1
    assert breaks[0].kind == "gap_before_file"
    assert breaks[0].expected == 110.0


def test_opening_balance_matching_is_clean():
    assert find_balance_breaks([_row("2026-02-01", -10, 90)], opening_balance=100) == []


def test_rows_without_balance_still_count_toward_running_total():
    rows = [
        _row("2026-01-01", -10, 90),
        _row("2026-01-02", -5, None),
        _row("2026-01-03", -5, 80),
    ]
    assert find_balance_breaks(rows) == []


def test_float_noise_within_tolerance():
    rows = [_row("2026-01-01", 0.1, 0.1), _row("2026-01-02", 0.2, 0.3)]
    assert find_balance_breaks(rows) == []


def test_no_balances_means_nothing_to_check():
    assert find_balance_breaks([_row("2026-01-01", -10, None)], opening_balance=5) == []
