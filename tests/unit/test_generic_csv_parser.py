"""Unit tests for portf_manager.parsers.generic_csv_parser."""

import pytest
from portf_manager.parsers.generic_csv_parser import (
    parse_generic_csv,
    _parse_number,
    _parse_date,
    _detect_delimiter,
)


MINIMAL_CSV = """date,symbol,type,quantity,price,currency
2024-01-15,AAPL,buy,10,185.50,USD
2024-02-01,AAPL,dividend,10,0.24,USD
2024-03-10,MSFT,sell,5,420.00,USD
"""

FULL_CSV = """date,symbol,name,type,quantity,price,currency,fees,asset_type,notes
2024-01-15,AAPL,Apple Inc,buy,10,185.50,USD,1.00,stock,first purchase
2024-01-20,BTC-EUR,Bitcoin,buy,0.5,40000,EUR,0,crypto,
"""

SEMICOLON_CSV = """date;symbol;name;type;quantity;price;currency;fees
2024-01-15;AAPL;Apple Inc;buy;10;185,50;USD;1,00
"""

EUROPEAN_NUMBERS_CSV = """date,symbol,type,quantity,price,currency
2024-01-15,AAPL,buy,10,"1.234,56",EUR
"""


class TestDelimiterDetection:
    def test_comma_delimiter(self):
        assert _detect_delimiter("a,b,c\n1,2,3") == ","

    def test_semicolon_delimiter(self):
        assert _detect_delimiter("a;b;c\n1;2;3") == ";"


class TestNumberParsing:
    def test_plain_float(self):
        assert _parse_number("185.50") == pytest.approx(185.50)

    def test_european_decimal(self):
        assert _parse_number("185,50") == pytest.approx(185.50)

    def test_european_thousands(self):
        assert _parse_number("1.234,56") == pytest.approx(1234.56)

    def test_us_thousands(self):
        assert _parse_number("1,234.56") == pytest.approx(1234.56)

    def test_zero(self):
        assert _parse_number("0") == 0.0

    def test_empty(self):
        assert _parse_number("") == 0.0


class TestDateParsing:
    def test_iso_date(self):
        assert _parse_date("2024-01-15") == "2024-01-15"

    def test_european_date(self):
        assert _parse_date("15/01/2024") == "2024-01-15"

    def test_us_date(self):
        assert _parse_date("01/15/2024") == "2024-01-15"

    def test_invalid_date(self):
        with pytest.raises(ValueError):
            _parse_date("not-a-date")


class TestParseGenericCSV:
    def test_minimal_columns(self):
        result = parse_generic_csv(MINIMAL_CSV)
        assert len(result.importable) == 3
        assert not result.skipped

    def test_full_columns(self):
        result = parse_generic_csv(FULL_CSV)
        assert len(result.importable) == 2
        assert result.importable[0].symbol == "AAPL"
        assert result.importable[0].asset_name == "Apple Inc"
        assert result.importable[0].fees == pytest.approx(1.0)
        assert "first purchase" in result.importable[0].raw_text

    def test_tx_types_mapped(self):
        result = parse_generic_csv(MINIMAL_CSV)
        types = {tx.tx_type for tx in result.importable}
        assert types == {"buy", "dividend", "sell"}

    def test_semicolon_delimiter(self):
        result = parse_generic_csv(SEMICOLON_CSV)
        assert len(result.importable) == 1
        assert result.importable[0].price == pytest.approx(185.50)
        assert result.importable[0].fees == pytest.approx(1.0)

    def test_european_number_in_price(self):
        result = parse_generic_csv(EUROPEAN_NUMBERS_CSV)
        assert len(result.importable) == 1
        assert result.importable[0].price == pytest.approx(1234.56)

    def test_missing_required_column_skips_file(self):
        csv = "date,symbol,type,quantity\n2024-01-15,AAPL,buy,10"
        result = parse_generic_csv(csv)
        assert not result.importable
        assert any("price" in r for _, r in result.skipped)

    def test_unknown_type_skipped(self):
        csv = "date,symbol,type,quantity,price,currency\n2024-01-15,AAPL,transfer,10,185,USD"
        result = parse_generic_csv(csv)
        assert not result.importable
        assert len(result.skipped) == 1
        assert "transfer" in result.skipped[0][1]

    def test_spanish_type_synonyms(self):
        csv = "date,symbol,tipo,cantidad,precio,moneda\n2024-01-15,AAPL,compra,10,185.50,USD"
        result = parse_generic_csv(csv)
        assert len(result.importable) == 1
        assert result.importable[0].tx_type == "buy"

    def test_empty_file_skipped(self):
        result = parse_generic_csv("")
        assert not result.importable
        assert result.skipped

    def test_symbol_uppercased(self):
        csv = "date,symbol,type,quantity,price,currency\n2024-01-15,aapl,buy,10,185,usd"
        result = parse_generic_csv(csv)
        assert result.importable[0].symbol == "AAPL"
        assert result.importable[0].currency == "USD"
