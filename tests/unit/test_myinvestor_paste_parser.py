"""Tests for the MyInvestor copy/paste ("Movimientos") parser."""

from datetime import date, timedelta

import pytest

from portf_manager.parsers.myinvestor_paste_parser import parse_myinvestor_paste

PASTE = """
15/01/2025

CR
Compra Rv Contado
1.083,02 €
EXAMPLE CORP @ 3
16.679,05 €

AD
Abono De Dividendo
12,40 €
OTHER FUND @ 100
16.691,45 €

16/01/2025

TS
Transferencia Sepa
2.000,00 €
Ingreso
18.691,45 €

SL
Salida
500,00 €
Retirada
18.191,45 €

VD
Venta De Valores
640,00 €
EXAMPLE CORP @ 2
18.831,45 €
"""


@pytest.fixture
def result():
    return parse_myinvestor_paste(PASTE)


def test_buy_price_is_amount_over_quantity(result):
    buy = result.transactions[0]
    assert (buy.tx_type, buy.symbol, buy.quantity) == ("buy", "EXAMPLE CORP", 3.0)
    assert buy.price == pytest.approx(1083.02 / 3)
    assert buy.date == "2025-01-15"


def test_dividend_is_one_unit_of_the_payout(result):
    div = result.transactions[1]
    assert (div.tx_type, div.symbol, div.quantity, div.price) == (
        "dividend",
        "OTHER FUND",
        1.0,
        12.40,
    )


def test_sell_uses_the_date_header_above_it(result):
    sell = result.transactions[2]
    assert (sell.tx_type, sell.quantity, sell.price) == ("sell", 2.0, 320.0)
    assert sell.date == "2025-01-16"


def test_cash_movements_become_bookings(result):
    assert [(b["action"], b["amount"], b["date"]) for b in result.bookings] == [
        ("Deposit", 2000.0, "2025-01-16"),
        ("Withdrawal", 500.0, "2025-01-16"),
    ]


def test_running_balance_is_ignored(result):
    assert 16679.05 not in {t.price for t in result.transactions}


def test_windows_line_endings():
    crlf = parse_myinvestor_paste(PASTE.replace("\n", "\r\n"))
    assert len(crlf.transactions) == 3 and len(crlf.bookings) == 2


@pytest.mark.parametrize(
    "header,days_ago", [("Hoy", 0), ("Today", 0), ("Ayer", 1), ("Yesterday", 1)]
)
def test_relative_date_headers(header, days_ago):
    text = f"{header}\nAD\nDividendo\n5,00 €\nEXAMPLE CORP\n"
    (div,) = parse_myinvestor_paste(text).transactions
    assert div.date == (date.today() - timedelta(days=days_ago)).isoformat()


def test_buy_without_quantity_is_skipped_not_guessed():
    result = parse_myinvestor_paste("15/01/2025\nCR\nCompra\n100,00 €\nEXAMPLE CORP\n")
    assert result.transactions == []
    assert "no '@ qty'" in result.skipped[0][1]


def test_sell_without_quantity_is_one_unit():
    result = parse_myinvestor_paste("15/01/2025\nVD\nVenta\n90,00 €\nEXAMPLE CORP\n")
    (sell,) = result.transactions
    assert (sell.quantity, sell.price) == (1.0, 90.0)


def test_fee_withdrawal_is_skipped_not_booked():
    result = parse_myinvestor_paste(
        "15/01/2025\nSL\nSalida\n4,99 €\nComisión custodia\n"
    )
    assert result.bookings == []
    assert "fee" in result.skipped[0][1]


def test_block_without_amount_is_skipped():
    result = parse_myinvestor_paste("15/01/2025\nCR\nCompra\nEXAMPLE CORP @ 3\n")
    assert result.transactions == []
    assert "no amount" in result.skipped[0][1]


def test_unknown_lines_are_ignored():
    assert parse_myinvestor_paste("hello\nworld\n").transactions == []
