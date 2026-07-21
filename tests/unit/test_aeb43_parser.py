"""Tests for the AEB43/N43 fixed-width bank statement parser."""

from portf_manager.parsers.aeb43_parser import looks_like_aeb43, parse_aeb43


def _header(
    entidad="1234",
    oficina="0001",
    cuenta="0000000001",
    clave="2",
    importe_cents=0,
    divisa="978",
    nombre="TEST",
):
    """Build a synthetic 80-byte AEB43 header ('11') record."""
    return (
        "11"
        + entidad.zfill(4)
        + oficina.zfill(4)
        + cuenta.zfill(10)
        + "260101"
        + "260101"
        + clave
        + str(importe_cents).zfill(14)
        + divisa
        + "0"
        + nombre.ljust(29)
    )


def _movement(fecha_op="260101", fecha_valor="260101", clave="1", importe_cents=1000):
    """Build a synthetic 80-byte AEB43 movement ('22') record."""
    return (
        "22"
        + "    "
        + "0000"
        + fecha_op
        + fecha_valor
        + "00"
        + "000"
        + clave
        + str(importe_cents).zfill(14)
        + "0".zfill(8)
        + "0".zfill(12)
        + "0".zfill(18)
    )


def _concept(text="", codigo="01"):
    """Build a synthetic 80-byte AEB43 complementary ('23') record."""
    return "23" + codigo + text.ljust(76)[:76]


def _trailer():
    """Build a synthetic 80-byte AEB43 trailer ('33') record (content unused by the parser)."""
    return "33" + " " * 78


def _crlf(*lines: str) -> str:
    return "\r\n".join(lines) + "\r\n"


def test_looks_like_aeb43_true_for_header_record():
    content = _crlf(_header(), _movement(), _concept("Example Corp"), _trailer())
    assert looks_like_aeb43(content) is True


def test_looks_like_aeb43_false_for_csv():
    content = "date,description,amount\n2026-01-05,Example,-10.00\n"
    assert looks_like_aeb43(content) is False


def test_looks_like_aeb43_false_for_empty_content():
    assert looks_like_aeb43("") is False


def test_parses_single_debit_movement_with_description():
    content = _crlf(
        _header(clave="2", importe_cents=100000),  # opening balance 1000.00
        _movement(fecha_op="260105", clave="1", importe_cents=2450),
        _concept("MERCADONA COMPRA"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.date == "2026-01-05"
    assert row.description == "MERCADONA COMPRA"
    assert row.amount == -24.50
    assert row.currency == "EUR"
    assert row.balance == 975.50


def test_parses_credit_movement():
    content = _crlf(
        _header(clave="2", importe_cents=0),
        _movement(fecha_op="260106", clave="2", importe_cents=210000),
        _concept("NOMINA EMPRESA SL"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].amount == 2100.00
    assert result.rows[0].balance == 2100.00


def test_negative_opening_balance_seed():
    content = _crlf(
        _header(clave="1", importe_cents=50000),  # opening balance -500.00
        _movement(fecha_op="260101", clave="2", importe_cents=20000),
        _concept("EXAMPLE DEPOSIT"),
        _trailer(),
    )
    result = parse_aeb43(content)
    assert result.rows[0].balance == -300.00


def test_missing_header_record_returns_skip():
    result = parse_aeb43("not an aeb43 file\r\n")
    assert result.rows == []
    assert result.skipped[0][0] == "file"


def test_empty_content():
    result = parse_aeb43("")
    assert result.rows == []
    assert result.skipped[0][0] == "file"
