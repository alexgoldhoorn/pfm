"""Unit tests for the MyInvestor 'Movimientos' CSV parser (synthetic data)."""

from portf_manager.parsers.myinvestor_csv_parser import parse_myinvestor_csv

SAMPLE = """Fecha de operación;Fecha de valor;Concepto;Importe;Divisa
03/06/2026;03/06/2026;SUSCRIPCIÓN PREMIUM;-7,99;EUR
03/06/2026;03/06/2026;ACME CORP;7,61;EUR
01/06/2026;01/06/2026;INVEST;1200;EUR
28/05/2026;01/06/2026;WIDGET ETF JAPAN;-1359,03;EUR
28/05/2026;29/05/2026;FOO INC @ 4;-1462,40;EUR
28/05/2026;29/05/2026;BAR LTD @ 20;2051,14;EUR
"""


def _result():
    return parse_myinvestor_csv(SAMPLE)


def test_invest_is_a_deposit_booking():
    r = _result()
    deposits = [b for b in r.bookings if b["action"] == "Deposit"]
    assert len(deposits) == 1
    assert deposits[0]["amount"] == 1200.0
    assert deposits[0]["currency"] == "EUR"
    assert deposits[0]["broker"] == "MyInvestor"


def test_at_qty_negative_is_a_buy_with_unit_price():
    r = _result()
    buys = [t for t in r.transactions if t.tx_type == "buy"]
    assert len(buys) == 1
    buy = buys[0]
    assert buy.symbol == "FOO INC"
    assert buy.quantity == 4.0
    assert round(buy.price * buy.quantity, 2) == 1462.40
    assert buy.date == "2026-05-28"


def test_at_qty_positive_is_a_dividend():
    # MyInvestor uses "NAME @ QTY" for dividends (QTY = shares held); we no
    # longer misclassify these as sells.
    r = _result()
    sells = [t for t in r.transactions if t.tx_type == "sell"]
    assert len(sells) == 0
    divs = [t for t in r.transactions if t.tx_type == "dividend"]
    bar = next(d for d in divs if d.symbol == "BAR LTD")
    assert bar.quantity == 20.0


def test_positive_no_at_is_also_a_dividend():
    r = _result()
    divs = [t for t in r.transactions if t.tx_type == "dividend"]
    assert len(divs) == 2
    acme = next(d for d in divs if d.symbol == "ACME CORP")
    assert acme.price == 7.61


def test_fee_and_unitless_buy_are_skipped_with_clear_reasons():
    r = _result()
    reasons = " | ".join(reason for _, reason in r.skipped)
    # platform fee is identified as a fee
    assert "fee/charge" in reasons and "PREMIUM" in reasons
    # a negative line with no '@ qty' is flagged as a unit-less buy, not a fee
    assert "without unit detail" in reasons and "WIDGET ETF JAPAN" in reasons


def test_european_amount_parsing():
    # 1.234,56 € style and plain integers both parse
    csv = (
        "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n"
        "01/01/2026;01/01/2026;INVEST;1.500,00;EUR\n"
    )
    r = parse_myinvestor_csv(csv)
    assert r.bookings[0]["amount"] == 1500.00
