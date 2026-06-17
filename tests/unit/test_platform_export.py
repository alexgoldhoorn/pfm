"""Unit tests for the platform_export module."""

import csv
import io

from portf_manager.platform_export import (
    _is_isin,
    _resolve_ticker,
    build_yahoo_finance_csv,
    build_simply_wall_st_csv,
)


# ---------------------------------------------------------------------------
# _is_isin
# ---------------------------------------------------------------------------


def test_is_isin_valid():
    assert _is_isin("US0378331005") is True


def test_is_isin_short_ticker():
    assert _is_isin("NVDA") is False


def test_is_isin_crypto_symbol():
    assert _is_isin("BTC-EUR") is False


def test_is_isin_mintos():
    assert _is_isin("MINTOS") is False


def test_is_isin_eu_fund():
    assert _is_isin("IE00B3XXRP09") is True


# ---------------------------------------------------------------------------
# _resolve_ticker
# ---------------------------------------------------------------------------


def test_resolve_ticker_uses_ticker_column():
    assert _resolve_ticker("US0378331005", "AAPL") == "AAPL"


def test_resolve_ticker_uses_symbol_when_not_isin():
    assert _resolve_ticker("NVDA", None) == "NVDA"


def test_resolve_ticker_uses_crypto_symbol():
    assert _resolve_ticker("BTC-EUR", None) == "BTC-EUR"


def test_resolve_ticker_isin_no_ticker_returns_none():
    assert _resolve_ticker("US0378331005", None) is None


def test_resolve_ticker_prefers_ticker_over_non_isin_symbol():
    assert _resolve_ticker("NVDA", "NVDA.AS") == "NVDA.AS"


# ---------------------------------------------------------------------------
# FakeDB helpers
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return [dict(r) for r in self._rows]


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, params=None):
        rows = self._rows
        if params and "AND t.portfolio_id" in query:
            pid = params[0]
            rows = [r for r in rows if r.get("portfolio_id") == pid]
        return _FakeCursor(rows)


class FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def get_connection(self):
        return _FakeConn(self._rows)


def _tx(
    asset_id,
    symbol,
    ticker,
    tx_type,
    qty,
    price,
    fees=0.0,
    date="2023-01-15",
    currency="EUR",
    asset_currency="EUR",
    portfolio_id=1,
):
    return {
        "id": asset_id * 1000 + len(date),
        "asset_id": asset_id,
        "symbol": symbol,
        "ticker": ticker,
        "transaction_type": tx_type,
        "quantity": float(qty),
        "price": float(price),
        "total_amount": float(qty) * float(price),
        "fees": float(fees),
        "transaction_date": date,
        "currency": currency,
        "asset_currency": asset_currency,
        "portfolio_id": portfolio_id,
    }


def _parse_csv(content):
    return list(csv.reader(io.StringIO(content)))


# ---------------------------------------------------------------------------
# build_yahoo_finance_csv — transactions mode
# ---------------------------------------------------------------------------


def test_yahoo_transactions_buy_positive_shares():
    db = FakeDB([_tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15")])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[0] == [
        "Symbol",
        "Shares",
        "Purchase Price",
        "Purchase Date",
        "Commission",
    ]
    assert rows[1][0] == "NVDA"
    assert float(rows[1][1]) == 10.0
    assert rows[1][3] == "01/15/2023"
    assert skipped == []


def test_yahoo_transactions_sell_negative_shares():
    db = FakeDB([_tx(1, "NVDA", None, "sell", 5, 200.0, date="2023-06-01")])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert float(rows[1][1]) == -5.0
    assert skipped == []


def test_yahoo_transactions_isin_only_skipped():
    db = FakeDB([_tx(1, "US0378331005", None, "buy", 10, 150.0)])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1  # headers only
    assert "US0378331005" in skipped


def test_yahoo_transactions_isin_with_ticker_included():
    db = FakeDB([_tx(1, "US0378331005", "AAPL", "buy", 10, 150.0)])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[1][0] == "AAPL"
    assert skipped == []


def test_yahoo_transactions_empty_input():
    db = FakeDB([])
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert skipped == []


def test_yahoo_transactions_isin_deduped_in_skipped():
    db = FakeDB(
        [
            _tx(1, "US0378331005", None, "buy", 10, 150.0, date="2023-01-01"),
            _tx(1, "US0378331005", None, "buy", 5, 160.0, date="2023-02-01"),
        ]
    )
    content, skipped = build_yahoo_finance_csv(db, None, "transactions")
    assert skipped.count("US0378331005") == 1


# ---------------------------------------------------------------------------
# build_yahoo_finance_csv — positions mode
# ---------------------------------------------------------------------------


def test_yahoo_positions_collapses_buys():
    db = FakeDB(
        [
            _tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15"),
            _tx(1, "NVDA", None, "buy", 5, 200.0, date="2023-06-01"),
        ]
    )
    content, skipped = build_yahoo_finance_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 2  # header + 1 data row
    assert rows[1][0] == "NVDA"
    assert float(rows[1][1]) == 15.0
    assert skipped == []


def test_yahoo_positions_excludes_sold_out():
    db = FakeDB(
        [
            _tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15"),
            _tx(1, "NVDA", None, "sell", 10, 200.0, date="2023-06-01"),
        ]
    )
    content, skipped = build_yahoo_finance_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 1  # headers only


def test_yahoo_positions_isin_only_skipped():
    db = FakeDB([_tx(1, "IE00B3XXRP09", None, "buy", 100, 50.0)])
    content, skipped = build_yahoo_finance_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert "IE00B3XXRP09" in skipped


# ---------------------------------------------------------------------------
# build_simply_wall_st_csv — transactions mode
# ---------------------------------------------------------------------------


def test_sws_transactions_headers():
    db = FakeDB([])
    content, _ = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[0] == [
        "Ticker Symbol",
        "Number of Shares",
        "Purchase Price (Per Share)",
        "Purchase Date",
        "Currency",
    ]


def test_sws_transactions_date_format_iso():
    db = FakeDB(
        [
            _tx(
                1,
                "ASML.AS",
                None,
                "buy",
                5,
                680.5,
                fees=4.95,
                date="2023-03-10",
                currency="EUR",
            )
        ]
    )
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert rows[1][0] == "ASML.AS"
    assert rows[1][3] == "2023-03-10"
    assert rows[1][4] == "EUR"
    assert skipped == []


def test_sws_transactions_sell_negative():
    db = FakeDB([_tx(1, "NVDA", None, "sell", 3, 200.0, date="2023-06-01")])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert float(rows[1][1]) == -3.0


def test_sws_transactions_isin_skipped():
    db = FakeDB([_tx(1, "IE00B3XXRP09", None, "buy", 100, 50.0)])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert "IE00B3XXRP09" in skipped


def test_sws_transactions_empty_input():
    db = FakeDB([])
    content, skipped = build_simply_wall_st_csv(db, None, "transactions")
    rows = _parse_csv(content)
    assert len(rows) == 1
    assert skipped == []


# ---------------------------------------------------------------------------
# build_simply_wall_st_csv — positions mode
# ---------------------------------------------------------------------------


def test_sws_positions_includes_currency():
    db = FakeDB(
        [
            _tx(
                1,
                "ASML.AS",
                None,
                "buy",
                5,
                680.5,
                date="2023-03-10",
                currency="EUR",
                asset_currency="EUR",
            ),
        ]
    )
    content, skipped = build_simply_wall_st_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert rows[1][0] == "ASML.AS"
    assert float(rows[1][1]) == 5.0
    assert rows[1][4] == "EUR"
    assert skipped == []


def test_sws_positions_excludes_sold_out():
    db = FakeDB(
        [
            _tx(1, "NVDA", None, "buy", 10, 150.0, date="2023-01-15"),
            _tx(1, "NVDA", None, "sell", 10, 200.0, date="2023-06-01"),
        ]
    )
    content, skipped = build_simply_wall_st_csv(db, None, "positions")
    rows = _parse_csv(content)
    assert len(rows) == 1
