"""Tests for the generic fixed-deposit CSV parser."""

import pytest

from portf_manager.parsers.deposits_csv_parser import _parse_amount, parse_deposits_csv

EU_CSV = (
    "Nombre;Importe;Divisa;TAE;Fecha inicio;Vencimiento;Cuenta\n"
    "Depósito 12m;10.000,00 €;eur;2,5 %;01/03/2025;01/03/2026;Example Bank\n"
)


def test_european_file():
    (dep,) = parse_deposits_csv(EU_CSV).deposits
    assert dep == {
        "name": "Depósito 12m",
        "principal": 10000.0,
        "currency": "EUR",
        "interest_rate": 2.5,
        "start_date": "2025-03-01",
        "maturity_date": "2026-03-01",
        "broker": "Example Bank",
        "notes": None,
    }


def test_us_file_with_bom_and_defaults():
    csv = (
        "﻿Name,Principal,Rate,Start Date,Maturity Date\n"
        'CD,"5,000.50",4.1,2025-01-15,2025-07-15\n'
    )
    (dep,) = parse_deposits_csv(csv).deposits
    assert dep["principal"] == 5000.50
    assert dep["currency"] == "EUR"
    assert dep["broker"] is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.000", 10000.0),  # EU thousands, no decimals
        ("10,000", 10000.0),  # US thousands, no decimals
        ("1.000.000", 1000000.0),
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("1500,5", 1500.5),  # lone comma with 1-2 digits is a decimal
        ("1500.5", 1500.5),
        ("-2.000 €", 2000.0),
        ("", None),
        ("n/a", None),
    ],
)
def test_amount_formats(raw, expected):
    assert _parse_amount(raw) == expected


def test_missing_required_column_reports_header():
    result = parse_deposits_csv("Name;Principal\nX;100\n")
    assert result.deposits == []
    ((where, why),) = result.skipped
    assert where == "header"
    assert "interest_rate" in why and "maturity_date" in why


def test_bad_row_is_skipped_not_fatal():
    csv = (
        "Name;Principal;Rate;Start;Maturity\n"
        "Good;1000;2;2025-01-01;2026-01-01\n"
        "Bad date;1000;2;soon;2026-01-01\n"
        ";1000;2;2025-01-01;2026-01-01\n"
    )
    result = parse_deposits_csv(csv)
    assert [d["name"] for d in result.deposits] == ["Good"]
    assert [row for row, _ in result.skipped] == ["row 3", "row 4"]


def test_empty_input():
    result = parse_deposits_csv("   \n")
    assert result.deposits == [] and result.skipped == []
