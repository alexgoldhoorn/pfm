"""Output tests for the tax tools; the API is patched at pfm_mcp.client._get."""

import urllib.error
from unittest.mock import patch

from helpers import reply
from pfm_mcp.tools import tax


def test_tax_report_lists_realised_lots_and_estimate():
    report_payload = {
        "year": 2026,
        "realised_lots": [
            {
                "symbol": "NVDA",
                "sell_date": "2026-03-01",
                "quantity": 5.0,
                "cost_basis": 500.0,
                "proceeds": 750.0,
                "gain_loss": 250.0,
            }
        ],
        "realised_gain_total": 250.0,
        "lot_count": 1,
        "dividends_gross_eur": 100.0,
        "dividend_withholding_eur": 15.0,
    }
    estimate_payload = {
        "interest_income_eur": 10.0,
        "savings_base_eur": 360.0,
        "estimated_tax_eur": 70.0,
        "unrealised_gain_eur": 2000.0,
    }
    with patch(
        "pfm_mcp.client._get",
        side_effect=[reply(report_payload), reply(estimate_payload)],
    ):
        result = tax.tax_report(2026)
    assert "IRPF TAX REPORT — 2026:" in result
    assert "NVDA" in result
    assert "Realised gain total:  +250.00 EUR" in result
    assert "IRPF savings-base estimate:" in result


def test_tax_report_survives_estimate_failure():
    report_payload = {
        "year": 2026,
        "realised_lots": [],
        "realised_gain_total": 0,
        "lot_count": 0,
        "dividends_gross_eur": 0,
        "dividend_withholding_eur": 0,
    }
    with patch(
        "pfm_mcp.client._get",
        side_effect=[reply(report_payload), urllib.error.URLError("boom")],
    ):
        result = tax.tax_report(2026)
    assert "IRPF TAX REPORT — 2026:" in result
    assert "IRPF savings-base estimate:" not in result


def test_tax_report_reports_error_on_report_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = tax.tax_report(2026)
    assert "Error fetching tax report:" in result


def test_tax_estimate_reports_savings_base_and_tax():
    payload = {
        "year": 2026,
        "realised_gain_eur": 250.0,
        "dividend_income_eur": 100.0,
        "interest_income_eur": 10.0,
        "savings_base_eur": 360.0,
        "estimated_tax_eur": 70.0,
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = tax.tax_estimate(2026)
    assert "IRPF TAX ESTIMATE — 2026:" in result
    assert "Estimated IRPF tax:      70.00 EUR" in result


def test_tax_estimate_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = tax.tax_estimate(2026)
    assert "Error fetching tax estimate:" in result


def test_add_months_clamps_to_month_end():
    from datetime import date

    assert tax._add_months(date(2026, 9, 15), 2) == date(2026, 11, 15)
    assert tax._add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
    assert tax._add_months(date(2026, 3, 31), -2) == date(2026, 1, 31)
    assert tax._add_months(date(2026, 3, 1), 12) == date(2027, 3, 1)


def _wash_lot(symbol, sell, purchase, gain, qty=1.0):
    return {
        "symbol": symbol,
        "name": f"{symbol} name",
        "sell_date": sell,
        "purchase_date": purchase,
        "quantity": qty,
        "gain_loss_eur": gain,
    }


def _wash_tx(symbol, date_str, tx_type="buy", qty=1.0):
    return {
        "transaction_type": tx_type,
        "symbol": symbol,
        "name": f"{symbol} name",
        "transaction_date": date_str,
        "quantity": qty,
    }


_WASH_LOTS = [
    _wash_lot(
        "XYZ", "2026-08-20", "2025-01-10", -300.0, 10
    ),  # rebought 2026-09-05 -> blocked
    _wash_lot(
        "ABC", "2026-08-01", "2024-05-01", -100.0, 5
    ),  # inside window, not rebought
    _wash_lot("OLD", "2026-01-10", "2023-01-01", -50.0),  # window long closed
    _wash_lot("GAIN", "2026-09-01", "2020-01-01", 200.0),  # a gain: rule doesn't apply
    _wash_lot(
        "FND", "2026-03-01", "2022-01-01", -80.0, 3
    ),  # mutual_fund: 1-year window
    _wash_lot(
        "IDX", "2026-04-01", "2022-01-01", -40.0
    ),  # "Funds" exchange: 1-year window
    _wash_lot(
        "FNDN", "2026-05-01", "2022-01-01", -30.0
    ),  # fund by name, typed stock: 1 year
    _wash_lot("BTC-EUR", "2026-09-01", "2021-01-01", -500.0),  # crypto: not covered
    _wash_lot(
        "MINTOS", "2026-09-01", "2025-01-01", -20.0
    ),  # P2P principal: not a security
    _wash_lot(
        "DEF", "2026-09-01", "2025-01-01", -60.0
    ),  # bought 2026-08-10, kept -> blocked
]


_WASH_TXS = [
    _wash_tx("XYZ", "2025-01-10", qty=10),
    _wash_tx("XYZ", "2026-09-05", qty=10),
    _wash_tx("XYZ", "2026-08-20", tx_type="sell", qty=10),
    _wash_tx("ABC", "2024-05-01", qty=5),
    _wash_tx("BTC-EUR", "2026-09-10", qty=0.1),
    _wash_tx("MINTOS", "2026-09-10", qty=100),
    _wash_tx("DEF", "2025-01-01"),
    _wash_tx("DEF", "2026-08-10"),
    _wash_tx("GONE", "2026-09-01"),  # bought recently, since sold
]


def _wash_asset(asset_type, exchange=None, name="Example Corp"):
    return {"asset_type": asset_type, "exchange": exchange, "name": name}


_WASH_ASSETS = {
    "XYZ": _wash_asset("stock", "XETRA Exchange"),
    "ABC": _wash_asset("etf", "XETRA Exchange"),
    "OLD": _wash_asset("stock"),
    "GAIN": _wash_asset("stock"),
    "DEF": _wash_asset("stock"),
    "GONE": _wash_asset("stock"),
    "FND": _wash_asset("mutual_fund"),
    "IDX": _wash_asset("etf", "Funds", "Global Stock Idx Eur"),
    "FNDN": _wash_asset(
        "stock", None, "Example Emerging Markets Index Fund (IE) S Acc"
    ),
    "BTC-EUR": _wash_asset("crypto"),
    "MINTOS": _wash_asset("bond", None, "Mintos P2P"),
}


_WASH_EXPECTED = {
    "all": 'WASH-SALE CHECK (antiaplicación, art. 33.5 LIRPF) — as of 2026-09-15:\n\nLoss sales blocked by a repurchase (loss deferred while those units are held):\n  XYZ (XYZ name): sold 2026-08-20, loss -300.00 EUR — blocked by buy(s) on 2026-09-05 (10 units)\n  DEF (DEF name): sold 2026-09-01, loss -60.00 EUR — blocked by buy(s) on 2026-08-10 (1 units)\n\nLoss sales still inside the window — don\'t rebuy yet:\n  ABC (ABC name): sold 2026-08-01, loss -100.00 EUR — buying it again before 2026-10-01 would block that loss\n  FND (FND name): sold 2026-03-01, loss -80.00 EUR — buying it again before 2027-03-01 would block that loss\n  IDX (IDX name): sold 2026-04-01, loss -40.00 EUR — buying it again before 2027-04-01 would block that loss\n  FNDN (FNDN name): sold 2026-05-01, loss -30.00 EUR — buying it again before 2027-05-01 would block that loss\n\nHeld positions bought recently — a sale at a loss would be blocked:\n  XYZ (XYZ name) (currently at a loss): last bought 2026-09-05 — a sale at a loss before 2026-11-05 is not deductible yet\n\nWindows: 2 months before/after the sale for listed securities; 1 year for unlisted fund units (typed mutual_fund, on the "Funds" exchange, or named as a fund). Crypto, cash and Mintos P2P are excluded. Repurchases are matched against FIFO lots — a screen, so confirm edge cases before filing.',
    "sym": 'WASH-SALE CHECK (antiaplicación, art. 33.5 LIRPF) for ABC — as of 2026-09-15:\n\nLoss sales still inside the window — don\'t rebuy yet:\n  ABC (ABC name): sold 2026-08-01, loss -100.00 EUR — buying it again before 2026-10-01 would block that loss\n\nWindows: 2 months before/after the sale for listed securities; 1 year for unlisted fund units (typed mutual_fund, on the "Funds" exchange, or named as a fund). Crypto, cash and Mintos P2P are excluded. Repurchases are matched against FIFO lots — a screen, so confirm edge cases before filing.',
    "empty": 'WASH-SALE CHECK (antiaplicación, art. 33.5 LIRPF) — as of 2026-09-15:\n  Nothing affected: no recent loss sales and no held position bought inside an antiaplicación window.\n\nWindows: 2 months before/after the sale for listed securities; 1 year for unlisted fund units (typed mutual_fund, on the "Funds" exchange, or named as a fund). Crypto, cash and Mintos P2P are excluded. Repurchases are matched against FIFO lots — a screen, so confirm edge cases before filing.',
}


# What GET /analytics/wash-sale returns for the fixtures at 2026-09-15 (holdings:
# XYZ at a loss), captured from wash_sale.analyse.
_WASH_ANSWER = {
    "all": {
        "blocked": [
            {
                "symbol": "XYZ",
                "name": "XYZ name",
                "sell_date": "2026-08-20",
                "loss_eur": -300.0,
                "blocking": [{"date": "2026-09-05", "quantity": 10.0}],
            },
            {
                "symbol": "DEF",
                "name": "DEF name",
                "sell_date": "2026-09-01",
                "loss_eur": -60.0,
                "blocking": [{"date": "2026-08-10", "quantity": 1.0}],
            },
        ],
        "no_rebuy": [
            {
                "symbol": "ABC",
                "name": "ABC name",
                "sell_date": "2026-08-01",
                "loss_eur": -100.0,
                "until": "2026-10-01",
            },
            {
                "symbol": "FND",
                "name": "FND name",
                "sell_date": "2026-03-01",
                "loss_eur": -80.0,
                "until": "2027-03-01",
            },
            {
                "symbol": "IDX",
                "name": "IDX name",
                "sell_date": "2026-04-01",
                "loss_eur": -40.0,
                "until": "2027-04-01",
            },
            {
                "symbol": "FNDN",
                "name": "FNDN name",
                "sell_date": "2026-05-01",
                "loss_eur": -30.0,
                "until": "2027-05-01",
            },
        ],
        "recent_buys": [
            {
                "symbol": "XYZ",
                "name": "XYZ name",
                "last_buy": "2026-09-05",
                "until": "2026-11-05",
                "at_loss": True,
            }
        ],
    },
    "sym": {
        "blocked": [],
        "no_rebuy": [
            {
                "symbol": "ABC",
                "name": "ABC name",
                "sell_date": "2026-08-01",
                "loss_eur": -100.0,
                "until": "2026-10-01",
            }
        ],
        "recent_buys": [],
    },
}


def test_wash_sale_check_text_is_unchanged():
    from datetime import date

    with patch("pfm_mcp.client._today", return_value=date(2026, 9, 15)):
        with patch("pfm_mcp.client._get", return_value=_WASH_ANSWER["all"]) as get:
            assert tax.wash_sale_check() == _WASH_EXPECTED["all"]
        get.assert_called_once_with("/api/v1/analytics/wash-sale", None)
        with patch("pfm_mcp.client._get", return_value=_WASH_ANSWER["sym"]) as get:
            assert tax.wash_sale_check("abc") == _WASH_EXPECTED["sym"]
        get.assert_called_once_with("/api/v1/analytics/wash-sale", {"symbol": "abc"})
        empty = {"blocked": [], "no_rebuy": [], "recent_buys": []}
        with patch("pfm_mcp.client._get", return_value=empty):
            assert tax.wash_sale_check() == _WASH_EXPECTED["empty"]


def test_wash_sale_check_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=RuntimeError("boom")):
        result = tax.wash_sale_check()
    assert result == "Error fetching data for wash-sale check: boom"
