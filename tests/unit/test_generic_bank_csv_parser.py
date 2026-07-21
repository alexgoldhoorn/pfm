"""Tests for the generic bank-statement CSV parser."""

from portf_manager.parsers.generic_bank_csv_parser import parse_generic_bank_csv


def test_basic_parse():
    csv_text = (
        "date,description,amount,currency\n"
        "2026-01-05,MERCADONA COMPRA,-24.50,EUR\n"
        "2026-01-06,NOMINA EMPRESA SL,2100.00,EUR\n"
    )
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 2
    assert result.rows[0].date == "2026-01-05"
    assert result.rows[0].description == "MERCADONA COMPRA"
    assert result.rows[0].amount == -24.50
    assert result.rows[0].currency == "EUR"
    assert result.rows[1].amount == 2100.00


def test_header_synonyms_spanish():
    csv_text = "fecha;concepto;importe\n05/01/2026;MERCADONA COMPRA;-24,50\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 1
    assert result.rows[0].date == "2026-01-05"
    assert result.rows[0].amount == -24.50


def test_header_synonyms_dutch():
    csv_text = "datum,omschrijving,bedrag\n2026-01-05,BOODSCHAPPEN,-24.50\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 1
    assert result.rows[0].description == "BOODSCHAPPEN"


def test_missing_required_columns():
    csv_text = "date,amount\n2026-01-05,-10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows == []
    assert any("Missing required columns" in reason for _, reason in result.skipped)


def test_optional_balance_and_currency_default():
    csv_text = "date,description,amount,balance\n2026-01-05,Desc,-10.00,500.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows[0].currency == "EUR"
    assert result.rows[0].balance == 500.00


def test_us_date_style_detected():
    csv_text = "date,description,amount\n01/20/2026,Desc,-10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows[0].date == "2026-01-20"


def test_eu_date_style_detected():
    csv_text = "date,description,amount\n20/01/2026,Desc,-10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows[0].date == "2026-01-20"


def test_semicolon_delimiter_detected():
    csv_text = "date;description;amount\n2026-01-05;Desc;-10,00\n"
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 1
    assert result.rows[0].amount == -10.00


def test_zero_amount_skipped():
    csv_text = "date,description,amount\n2026-01-05,Desc,0\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows == []
    assert any("zero" in reason.lower() for _, reason in result.skipped)


def test_empty_description_skipped():
    csv_text = "date,description,amount\n2026-01-05,,10.00\n"
    result = parse_generic_bank_csv(csv_text)
    assert result.rows == []
    assert any("description" in reason.lower() for _, reason in result.skipped)


def test_blank_lines_skipped_silently():
    csv_text = (
        "date,description,amount\n2026-01-05,Desc,-10.00\n\n2026-01-06,Desc2,-5.00\n"
    )
    result = parse_generic_bank_csv(csv_text)
    assert len(result.rows) == 2


def test_empty_file():
    result = parse_generic_bank_csv("")
    assert result.rows == []
    assert result.skipped[0][0] == "file"
