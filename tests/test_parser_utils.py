"""Tests for shared parser utility functions."""

import pytest
from portf_manager.parsers.utils import parse_european_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.583,25", 1583.25),
        ("695,33", 695.33),
        ("1200", 1200.0),
        ("-1.488,58", -1488.58),
        ("-1488,58", -1488.58),
        ("0,00", 0.0),
        ("1.200.000,50", 1200000.50),
        ("", 0.0),
        ("  ", 0.0),
    ],
)
def test_parse_european_number(raw, expected):
    assert parse_european_number(raw) == pytest.approx(expected)


def test_parse_european_number_strips_euro():
    assert parse_european_number("1.234,56 €") == pytest.approx(1234.56)


def test_parse_european_number_strips_currency_code():
    assert parse_european_number("1.234,56 EUR") == pytest.approx(1234.56)
