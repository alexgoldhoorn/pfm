"""Unit tests for the Mintos statement parser (synthetic data)."""

from portf_manager.parsers.mintos_csv_parser import parse_mintos_csv, MINTOS_SYMBOL

HEADER = (
    "Fecha,Identificación de la operación:,Detalles,"
    "Volumen de negocios,Saldo,Divisa,Tipo de pago\n"
)
SAMPLE = HEADER + "\n".join(
    [
        '"2025-11-03 02:52:28",id1,"Préstamo X Intereses recibidos",0.10,34.10,EUR,"Intereses recibidos"',
        '"2025-11-03 02:52:28",id2,"Préstamo X Retención de impuestos",-0.01,34.09,EUR,"Retención de impuestos"',
        '"2025-11-15 10:00:00",id3,"Préstamo Y Intereses recibidos",0.20,34.29,EUR,"Intereses recibidos"',
        '"2025-11-03 02:52:29",id4,"Préstamo X Capital recibido",0.50,34.79,EUR,"Capital recibido"',
        '"2025-11-04 09:00:00",id5,"Inversión en préstamo",-1.00,33.79,EUR,"Inversión"',
        '"2025-12-10 12:00:00",id6,"Préstamo Z Intereses recibidos",0.30,34.09,EUR,"Intereses recibidos"',
    ]
)


def test_interest_aggregated_per_month():
    r = parse_mintos_csv(SAMPLE)
    by_month = {e["date"][:7]: e for e in r.interest}
    assert set(by_month) == {"2025-11", "2025-12"}
    # Nov: 0.10 + 0.20 interest, 0.01 withholding
    assert by_month["2025-11"]["amount"] == 0.30
    assert by_month["2025-11"]["tax"] == 0.01
    # Dec: 0.30 interest
    assert by_month["2025-12"]["amount"] == 0.30


def test_month_date_is_last_seen_in_month():
    r = parse_mintos_csv(SAMPLE)
    nov = next(e for e in r.interest if e["date"][:7] == "2025-11")
    assert nov["date"] == "2025-11-15"  # latest interest/withholding date in Nov


def test_principal_in_and_out_aggregated_per_month_not_ignored():
    r = parse_mintos_csv(SAMPLE)
    assert "Capital recibido" not in r.ignored_summary
    assert "Inversión" not in r.ignored_summary
    nov = next(p for p in r.principal if p["date"][:7] == "2025-11")
    # Nov: Inversión 1.00 (buy), Capital recibido 0.50 (sell)
    assert nov["buy_amount"] == 1.00
    assert nov["sell_amount"] == 0.50
    # interest entries do not include capital/inversión amounts
    assert all(e["amount"] > 0 for e in r.interest)


def test_symbol_constant():
    assert MINTOS_SYMBOL == "MINTOS"


DEPOSIT_SAMPLE = HEADER + "\n".join(
    [
        '"2025-11-02 09:00:00",d1,"Ingreso de fondos",100.00,100.00,EUR,"Depósito"',
        '"2025-11-03 02:52:28",i1,"Préstamo X Intereses recibidos",0.10,100.10,EUR,"Intereses recibidos"',
        '"2025-11-20 09:00:00",w1,"Retirada de fondos",-40.00,60.10,EUR,"Retirada de fondos"',
        '"2025-11-21 10:00:00",w2,"Transferencia saliente",-200.00,-139.90,EUR,"Pago mediante transferencia electrónica saliente"',
        '"2025-11-22 11:00:00",p1,"Buyback principal",5.00,5.00,EUR,"Ingresos del principal recibidos por la recompra del préstamo"',
    ]
)


def test_deposits_and_withdrawals_become_bookings():
    r = parse_mintos_csv(DEPOSIT_SAMPLE)
    pairs = {(b["action"], b["amount"], b["currency"]) for b in r.bookings}
    assert ("Deposit", 100.0, "EUR") in pairs
    assert ("Withdrawal", 40.0, "EUR") in pairs
    assert len(r.bookings) == 3
    # Interest is still aggregated, unaffected by the new booking rows.
    assert any(e["amount"] == 0.10 for e in r.interest)


def test_outgoing_wire_is_withdrawal():
    r = parse_mintos_csv(DEPOSIT_SAMPLE)
    pairs = {(b["action"], b["amount"], b["currency"]) for b in r.bookings}
    assert ("Withdrawal", 200.0, "EUR") in pairs


def test_buyback_principal_is_a_sell_not_a_deposit():
    r = parse_mintos_csv(DEPOSIT_SAMPLE)
    # principal repayment from buyback guarantee must not become a booking...
    assert not any(b["amount"] == 5.0 for b in r.bookings)
    # ...it's P2P principal returning, aggregated into the monthly sell side
    nov = next(p for p in r.principal if p["date"][:7] == "2025-11")
    assert nov["sell_amount"] == 5.0


class TestBondsSleeve:
    SAMPLE = HEADER + "\n".join(
        [
            '"2025-09-19 11:47:51",b1,"ISIN: NO0000000012 (Bono) Transferencia a inversiones en bonos",-523.83,-523.83,EUR,"Transferencia a inversiones en bonos"',
            '"2025-10-06 10:06:42",b2,"ISIN: NO0000000012 (Bono) Transferencia desde inversiones en bonos",23.13,-500.70,EUR,"Transferencia desde inversiones en bonos"',
        ]
    )

    def test_bond_buy_carries_isin(self):
        r = parse_mintos_csv(self.SAMPLE)
        assert len(r.bond_buys) == 1
        assert r.bond_buys[0]["isin"] == "NO0000000012"
        assert r.bond_buys[0]["amount"] == 523.83

    def test_bond_income_carries_isin(self):
        r = parse_mintos_csv(self.SAMPLE)
        assert len(r.bond_income) == 1
        assert r.bond_income[0]["isin"] == "NO0000000012"
        assert r.bond_income[0]["amount"] == 23.13

    def test_bond_rows_are_not_bookings_or_ignored(self):
        r = parse_mintos_csv(self.SAMPLE)
        assert r.bookings == []
        assert r.ignored_summary == {}


class TestSecondaryMarketAndFees:
    SAMPLE = HEADER + "\n".join(
        [
            '"2025-11-05 09:00:00",s1,"Venta en mercado secundario",10.00,10.00,EUR,"Operación del Mercado Secundario"',
            '"2025-11-06 09:00:00",s2,"Compra en mercado secundario",-3.00,7.00,EUR,"Operación del Mercado Secundario"',
            '"2025-11-10 09:00:00",f1,"Comisión mensual",-1.50,5.50,EUR,"Mintos Core fee"',
            '"2025-08-29 11:00:57",e1,"Pago de la cartera ETF saliente: ref123",-811.00,-805.50,EUR,"Pago de la cartera ETF saliente"',
        ]
    )

    def test_secondary_market_buy_and_sell_sides_summed_separately(self):
        r = parse_mintos_csv(self.SAMPLE)
        nov = next(p for p in r.principal if p["date"][:7] == "2025-11")
        # +10.00 sell-side row and -3.00 buy-side row, not netted together
        assert nov["sell_amount"] == 10.00
        assert nov["buy_amount"] == 3.00

    def test_core_fee_is_a_withdrawal(self):
        r = parse_mintos_csv(self.SAMPLE)
        fees = [b for b in r.bookings if b["amount"] == 1.50]
        assert len(fees) == 1
        assert fees[0]["action"] == "Withdrawal"

    def test_etf_portfolio_funding_is_a_withdrawal_not_saliente_generic(self):
        # Regression: "saliente" is also a substring of the generic outgoing-
        # wire keyword match — the ETF line must be caught by its own exact
        # check first, not fall into (or double-match) the generic one.
        r = parse_mintos_csv(self.SAMPLE)
        etf_withdrawals = [b for b in r.bookings if b["amount"] == 811.00]
        assert len(etf_withdrawals) == 1
        assert etf_withdrawals[0]["action"] == "Withdrawal"
