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


def test_internal_activity_is_ignored_but_summarised():
    r = parse_mintos_csv(SAMPLE)
    assert "Capital recibido" in r.ignored_summary
    assert r.ignored_summary["Capital recibido"][0] == 1
    assert "Inversión" in r.ignored_summary
    # interest entries do not include capital/inversión amounts
    assert all(e["amount"] > 0 for e in r.interest)


def test_symbol_constant():
    assert MINTOS_SYMBOL == "MINTOS"
