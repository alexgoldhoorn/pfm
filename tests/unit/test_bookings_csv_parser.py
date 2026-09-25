"""Tests for the generic bookings (deposit/withdrawal) CSV parser."""

from portf_manager.parsers.bookings_csv_parser import parse_bookings_csv


def test_european_file_with_spanish_headers():
    csv = (
        "Fecha;Tipo;Importe;Divisa;Cuenta\n"
        "15/01/2025;Ingreso;1.500,00 €;eur;Example Broker\n"
        "20/01/2025;Retirada;-200,50;EUR;Example Broker\n"
    )
    result = parse_bookings_csv(csv)
    assert result.bookings == [
        {
            "broker": "Example Broker",
            "date": "2025-01-15",
            "action": "Deposit",
            "amount": 1500.0,
            "currency": "EUR",
        },
        {
            "broker": "Example Broker",
            "date": "2025-01-20",
            "action": "Withdrawal",
            "amount": 200.5,
            "currency": "EUR",
        },
    ]


def test_us_file_thousands_and_defaults():
    csv = 'Date,Action,Amount\n2025-02-01,deposit,"10,000"\n'
    (booking,) = parse_bookings_csv(csv).bookings
    assert booking["amount"] == 10000.0
    assert booking["currency"] == "EUR"
    assert booking["broker"] is None


def test_unrecognised_rows_are_skipped_with_row_numbers():
    csv = (
        "date;action;amount\n"
        "2025-01-01;deposit;100\n"
        "2025-01-02;transfer;100\n"  # unknown action
        "yesterday;deposit;100\n"  # unparseable date
        "2025-01-03;deposit;0\n"  # zero amount is not a booking
    )
    result = parse_bookings_csv(csv)
    assert len(result.bookings) == 1
    assert [row for row, _ in result.skipped] == ["row 3", "row 4", "row 5"]


def test_missing_required_columns():
    result = parse_bookings_csv("date;amount\n2025-01-01;100\n")
    assert result.bookings == []
    assert result.skipped == [("header", "missing required column(s): action")]


def test_empty_input():
    result = parse_bookings_csv("")
    assert result.bookings == [] and result.skipped == []
