"""Output tests for the portfolio tools; the API is patched at pfm_mcp.client._get."""

import urllib.error
from unittest.mock import patch

from helpers import reply, requested_url
from pfm_mcp.client import FULL_HISTORY_LIMIT
from pfm_mcp.tools import portfolio


def test_portfolio_holdings_lists_positions_sorted_by_value():
    payload = {
        "holdings": [
            {
                "symbol": "NVDA",
                "asset_type": "stock",
                "quantity": 10.0,
                "avg_price": 100.0,
                "current_price": 150.0,
                "total_value": 1500.0,
                "total_value_eur": 1500.0,
                "cost_basis": 1000.0,
                "pnl_amount": 500.0,
                "pnl_pct": 50.0,
                "currency": "USD",
                "name": "NVIDIA",
            },
            {
                "symbol": "ASML",
                "asset_type": "stock",
                "quantity": 2.0,
                "avg_price": 600.0,
                "current_price": 650.0,
                "total_value": 1300.0,
                "total_value_eur": 1300.0,
                "cost_basis": 1200.0,
                "pnl_amount": 100.0,
                "pnl_pct": 8.3,
                "currency": "EUR",
                "name": "ASML",
            },
        ],
        "summary": {
            "total_value": 2800.0,
            "total_cost": 2200.0,
            "total_pnl": 600.0,
            "total_pnl_pct": 27.3,
        },
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.portfolio_holdings()
    assert "NVDA" in result
    assert "TOTAL VALUE:  2,800.00 EUR" in result


def test_portfolio_holdings_reports_no_holdings():
    with patch(
        "pfm_mcp.client._get", return_value=reply({"holdings": [], "summary": {}})
    ):
        result = portfolio.portfolio_holdings()
    assert result == "No holdings found."


def test_portfolio_holdings_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.portfolio_holdings()
    assert "Error fetching holdings:" in result


def test_list_portfolios_lists_accounts():
    payload = [
        {"id": 1, "name": "MyInvestor", "currency": "EUR", "description": "Main broker"}
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.list_portfolios()
    assert "MyInvestor" in result


def test_list_portfolios_reports_none_found():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        result = portfolio.list_portfolios()
    assert result == "No portfolios found."


def test_list_portfolios_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.list_portfolios()
    assert "Error fetching portfolios:" in result


def test_list_assets_filters_by_type():
    payload = [
        {
            "symbol": "NVDA",
            "asset_type": "stock",
            "name": "NVIDIA",
            "latest_price": 150.0,
            "currency": "USD",
        },
        {
            "symbol": "BTC-EUR",
            "asset_type": "crypto",
            "name": "Bitcoin",
            "latest_price": 60000.0,
            "currency": "EUR",
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.list_assets(asset_type="crypto")
    assert "BTC-EUR" in result
    assert "NVDA" not in result


def test_list_assets_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.list_assets()
    assert "Error fetching assets:" in result


def test_list_transactions_filters_by_symbol():
    payload = [
        {
            "transaction_type": "BUY",
            "symbol": "NVDA",
            "transaction_date": "2026-01-15",
            "quantity": 5,
            "price": 100.0,
            "total_amount": 500.0,
            "currency": "USD",
        },
        {
            "transaction_type": "BUY",
            "symbol": "ASML",
            "transaction_date": "2026-02-01",
            "quantity": 2,
            "price": 600.0,
            "total_amount": 1200.0,
            "currency": "EUR",
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.list_transactions(asset_symbol="NVDA")
    assert "NVDA" in result
    assert "ASML" not in result


def _requested_url(mock_urlopen):
    return requested_url(mock_urlopen)


def test_list_transactions_symbol_filter_fetches_full_history():
    # Regression 2026-10-01: the symbol filter ran over only the newest `limit`
    # rows, so an Adidas buy at position 83 of 615 came back "No transactions found".
    with patch("pfm_mcp.client._get", return_value=reply([])) as m:
        portfolio.list_transactions(asset_symbol="US0000000001")
    assert f"limit={FULL_HISTORY_LIMIT}" in _requested_url(m)


def test_list_transactions_client_filters_never_rely_on_api_default_limit():
    # Omitting `limit` makes the API return only its newest 100 rows.
    for kwargs in (
        {"order": "oldest"},
        {"date_from": "2025-01-01"},
        {"transaction_type": "BUY"},
        {"asset_type": "stock"},
    ):
        with patch("pfm_mcp.client._get", return_value=reply([])) as m:
            portfolio.list_transactions(**kwargs)
        assert f"limit={FULL_HISTORY_LIMIT}" in _requested_url(m), kwargs


def test_list_transactions_unfiltered_recent_uses_requested_limit():
    with patch("pfm_mcp.client._get", return_value=reply([])) as m:
        portfolio.list_transactions(limit=7)
    assert "limit=7" in _requested_url(m)


def test_list_transactions_reports_none_matching():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        result = portfolio.list_transactions()
    assert result == "No transactions found matching the criteria."


def test_list_transactions_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.list_transactions()
    assert "Error fetching transactions:" in result


def test_bookings_lists_deposits_and_withdrawals():
    payload = [
        {
            "action": "Deposit",
            "amount": 1000.0,
            "currency": "EUR",
            "date": "2026-01-01",
            "portfolio_name": "MyInvestor",
        },
        {
            "action": "Withdrawal",
            "amount": 200.0,
            "currency": "EUR",
            "date": "2026-02-01",
            "portfolio_name": "MyInvestor",
        },
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.bookings()
    assert "Total deposited:   1,000.00 EUR" in result
    assert "Total withdrawn:   200.00 EUR" in result
    assert "Net cash in:       800.00 EUR" in result


def test_bookings_reports_none_found():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        result = portfolio.bookings()
    assert result == "No bookings found."


def test_bookings_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.bookings()
    assert "Error fetching bookings:" in result


def test_quote_lists_prices_and_flags_stale():
    payload = {
        "quotes": [
            {
                "symbol": "NVDA",
                "price": 150.0,
                "currency": "USD",
                "change_pct": 2.5,
                "stale": False,
            },
            {
                "symbol": "OLD",
                "price": 10.0,
                "currency": "EUR",
                "change_pct": None,
                "stale": True,
            },
            {"symbol": "BAD", "error": "not found"},
        ]
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.quote("NVDA,OLD,BAD")
    assert "NVDA" in result and "+2.50%" in result
    assert "[stale]" in result
    assert "unavailable (not found)" in result


def test_quote_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.quote("NVDA")
    assert "Error fetching quotes:" in result


def test_watchlist_lists_entries_and_buy_zone():
    payload = [
        {
            "symbol": "ASML",
            "name": "ASML",
            "current_price": 650.0,
            "buy_below": 600.0,
            "distance_to_buy_pct": -7.7,
            "in_buy_zone": False,
        }
    ]
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = portfolio.watchlist()
    assert "ASML" in result
    assert "buy<600.00 EUR" in result


def test_watchlist_reports_empty():
    with patch("pfm_mcp.client._get", return_value=reply([])):
        result = portfolio.watchlist()
    assert result == "Watchlist is empty."


def test_watchlist_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = portfolio.watchlist()
    assert "Error fetching watchlist:" in result
