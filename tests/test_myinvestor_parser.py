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


def test_fee_is_a_withdrawal_booking():
    r = _result()
    withdrawals = [b for b in r.bookings if b["action"] == "Withdrawal"]
    assert len(withdrawals) == 1
    assert withdrawals[0]["amount"] == 7.99
    assert withdrawals[0]["currency"] == "EUR"
    assert withdrawals[0]["broker"] == "MyInvestor"


def test_unitless_buy_is_skipped_with_clear_reason():
    r = _result()
    reasons = " | ".join(reason for _, reason in r.skipped)
    # a negative line with no '@ qty' is flagged as a unit-less buy, not a fee
    assert "without unit detail" in reasons and "WIDGET ETF JAPAN" in reasons
    # the fee line is no longer skipped
    assert "PREMIUM" not in reasons


def test_european_amount_parsing():
    # 1.234,56 € style and plain integers both parse
    csv = (
        "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n"
        "01/01/2026;01/01/2026;INVEST;1.500,00;EUR\n"
    )
    r = parse_myinvestor_csv(csv)
    assert r.bookings[0]["amount"] == 1500.00


INTEREST_SAMPLE = """Fecha de operación;Fecha de valor;Concepto;Importe;Divisa
05/01/2026;05/01/2026;Ret. IRPF intereses deciembre;-0,58;EUR
05/01/2026;05/01/2026;Liq. intereses diciembre;3,04;EUR
08/02/2026;07/02/2026;PERIODO 07/01/2026 07/02/2026;1,81;EUR
04/08/2026;04/08/2026;Ret. liq intereses julio promo;-9,82;EUR
04/08/2026;04/08/2026;liq intereses julio promo;51,7;EUR
10/08/2026;10/08/2026;regularizacion intereses julio;6,34;EUR
10/08/2026;10/08/2026;regularizacion intereses julio;-33,38;EUR
"""


def _interest_result():
    return parse_myinvestor_csv(INTEREST_SAMPLE)


def test_interest_credit_folds_same_date_withholding_into_tax():
    r = _interest_result()
    interest = [t for t in r.transactions if t.tx_type == "interest"]
    dec = next(t for t in interest if t.date == "2026-01-05")
    assert dec.symbol == "MYINVESTOR-CASH"
    assert dec.asset_type == "cash"
    assert dec.price == 3.04
    assert dec.tax == 0.58
    # the withholding line itself is consumed, not double-imported
    assert sum(1 for t in interest if t.date == "2026-01-05") == 1


def test_interest_credit_matches_promo_variant_withholding():
    r = _interest_result()
    interest = [t for t in r.transactions if t.tx_type == "interest"]
    promo = next(t for t in interest if t.date == "2026-08-04")
    assert promo.price == 51.7
    assert promo.tax == 9.82


def test_periodo_settlement_is_interest_with_no_withholding():
    r = _interest_result()
    interest = [t for t in r.transactions if t.tx_type == "interest"]
    periodo = next(t for t in interest if t.date == "2026-02-08")
    assert periodo.price == 1.81
    assert periodo.tax == 0.0


def test_regularizacion_pair_imported_as_two_separate_rows():
    # Same concept text on both sides (no distinguishing IRPF/Ret prefix), so
    # unlike the credit/withholding pair these are NOT folded together.
    r = _interest_result()
    interest = [
        t for t in r.transactions if t.tx_type == "interest" and t.date == "2026-08-10"
    ]
    assert len(interest) == 2
    prices = sorted(t.price for t in interest)
    assert prices == [-33.38, 6.34]
    assert all(t.tax == 0.0 for t in interest)


def test_unmatched_withholding_is_a_standalone_negative_interest_row():
    csv = (
        "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n"
        "01/03/2026;01/03/2026;Ret. IRPF intereses febrero;-1,10;EUR\n"
    )
    r = parse_myinvestor_csv(csv)
    interest = [t for t in r.transactions if t.tx_type == "interest"]
    assert len(interest) == 1
    assert interest[0].price == -1.10
    assert interest[0].tax == 0.0


def test_my_investor_transfer_is_a_deposit_not_a_dividend():
    # MyInvestor's own in-app P2P/instant-transfer feature — not a security.
    csv = (
        "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n"
        "20/07/2026;20/07/2026;MY INVESTOR;200;EUR\n"
    )
    r = parse_myinvestor_csv(csv)
    assert len(r.transactions) == 0
    assert len(r.bookings) == 1
    assert r.bookings[0]["action"] == "Deposit"
    assert r.bookings[0]["amount"] == 200.0


def test_external_transfer_is_a_deposit():
    csv = (
        "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n"
        "16/10/2025;16/10/2025;Sent from Revolut;44;EUR\n"
    )
    r = parse_myinvestor_csv(csv)
    assert len(r.transactions) == 0
    assert len(r.bookings) == 1
    assert r.bookings[0]["action"] == "Deposit"
    assert r.bookings[0]["amount"] == 44.0


def test_genuine_lump_sum_dividend_still_classified_as_dividend():
    # Regression guard: same shape as MY INVESTOR/Sent-from (positive, no
    # "@", not a fixed keyword) but a real security must stay a dividend.
    csv = (
        "Fecha de operación;Fecha de valor;Concepto;Importe;Divisa\n"
        "16/07/2025;16/07/2025;LABORATORIOS FARMACEUTIC ROVI;6,82;EUR\n"
    )
    r = parse_myinvestor_csv(csv)
    assert len(r.bookings) == 0
    assert len(r.transactions) == 1
    assert r.transactions[0].tx_type == "dividend"
    assert r.transactions[0].symbol == "LABORATORIOS FARMACEUTIC ROVI"
