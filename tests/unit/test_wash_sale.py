"""Tests for the shared antiaplicación service."""

from datetime import date

import pytest

from portf_manager.database import Database
from portf_manager.services.wash_sale import (
    WashSaleUnavailable,
    analyse,
    blocked_assets,
    is_fund,
    window_months,
)


def _asset(asset_type, exchange=None, name="Example Corp"):
    return {"asset_type": asset_type, "exchange": exchange, "name": name}


def test_is_fund_by_type_exchange_and_name():
    assert is_fund(_asset("mutual_fund"))
    assert is_fund(_asset("etf", "Funds", "Global Stock Idx Eur"))
    assert is_fund(_asset("stock", None, "Example Index Fund"))
    assert not is_fund(_asset("etf", "XETRA", "Example Core World Fund ETF"))
    assert not is_fund(_asset("stock", "XETRA"))
    assert not is_fund({})


def test_window_months():
    assert window_months({"asset_type": "etf"}) == 2
    assert window_months({"asset_type": "mutual_fund"}) == 12
    assert window_months({}) == 2


def test_analyse_moved_verbatim():
    # Fixture copied from the MCP server's own tests (wash_sale_check).
    lots = [
        {
            "symbol": "XYZ",
            "name": "XYZ name",
            "sell_date": "2026-08-20",
            "purchase_date": "2025-01-10",
            "quantity": 10,
            "gain_loss_eur": -300.0,
        },
        {
            "symbol": "ABC",
            "name": "ABC name",
            "sell_date": "2026-08-01",
            "purchase_date": "2024-05-01",
            "quantity": 5,
            "gain_loss_eur": -100.0,
        },
        {
            "symbol": "FND",
            "name": "FND name",
            "sell_date": "2026-03-01",
            "purchase_date": "2022-01-01",
            "quantity": 3,
            "gain_loss_eur": -80.0,
        },
        {
            "symbol": "BTC-EUR",
            "name": "BTC name",
            "sell_date": "2026-09-01",
            "purchase_date": "2021-01-01",
            "quantity": 1,
            "gain_loss_eur": -500.0,
        },
    ]

    def tx(sym, day, kind="buy", qty=1.0):
        return {
            "transaction_type": kind,
            "symbol": sym,
            "name": f"{sym} name",
            "transaction_date": day,
            "quantity": qty,
        }

    txs = [
        tx("XYZ", "2025-01-10", qty=10),
        tx("XYZ", "2026-09-05", qty=10),
        tx("ABC", "2024-05-01", qty=5),
        tx("BTC-EUR", "2026-09-10"),
    ]
    assets = {
        "XYZ": _asset("stock", "XETRA Exchange"),
        "ABC": _asset("etf", "XETRA Exchange"),
        "FND": _asset("mutual_fund"),
        "BTC-EUR": _asset("crypto"),
    }
    out = analyse(lots, txs, assets, date(2026, 9, 15))
    assert out["blocked"] == [
        {
            "symbol": "XYZ",
            "name": "XYZ name",
            "sell_date": "2026-08-20",
            "loss_eur": -300.0,
            "blocking": [{"date": "2026-09-05", "quantity": 10.0}],
        }
    ]
    assert [(n["symbol"], n["until"]) for n in out["no_rebuy"]] == [
        ("ABC", "2026-10-01"),
        ("FND", "2027-03-01"),
    ]
    assert {r["symbol"] for r in out["recent_buys"]} == {"XYZ"}


def _db_with_loss_sale(tmp_path, asset_type="etf", symbol="EXC"):
    db = Database(str(tmp_path / f"wash_{symbol}.db"))
    pid = db.get_or_create_portfolio("Broker")
    aid = db.create_asset(symbol, "Example Corp", asset_type, currency="EUR")
    db.create_transaction(aid, "buy", 10, 100, 1000, "2026-01-01", portfolio_id=pid)
    db.create_transaction(aid, "sell", 10, 80, 800, "2026-08-20", portfolio_id=pid)
    return db, aid


def test_blocked_59_days_ago_not_61(tmp_path):
    db, aid = _db_with_loss_sale(tmp_path)
    # Sale 2026-08-20 + 2 calendar months = 2026-10-20: blocked while that date
    # is still ahead, free on the date itself.
    blocked = blocked_assets(db, date(2026, 10, 18), fx_on=lambda c, d: 1.0)
    assert blocked == {
        aid: {
            "symbol": "EXC",
            "sold_on": "2026-08-20",
            "loss_eur": -200.0,
            "safe_from": "2026-10-20",
        }
    }
    assert blocked_assets(db, date(2026, 10, 20), fx_on=lambda c, d: 1.0) == {}


def test_blocked_assets_fund_window_and_crypto(tmp_path):
    db, aid = _db_with_loss_sale(tmp_path, "mutual_fund", "FND")
    out = blocked_assets(db, date(2026, 10, 20))
    assert out[aid]["safe_from"] == "2027-08-20"
    db2, _ = _db_with_loss_sale(tmp_path, "crypto", "BTC-EUR")
    assert blocked_assets(db2, date(2026, 10, 18)) == {}


def test_blocked_assets_uses_injected_fx_for_foreign_currency(tmp_path):
    db = Database(str(tmp_path / "usd.db"))
    pid = db.get_or_create_portfolio("Broker")
    aid = db.create_asset("USD1", "Example Corp", "etf", currency="USD")
    db.create_transaction(
        aid, "buy", 10, 100, 1000, "2026-01-01", portfolio_id=pid, currency="USD"
    )
    db.create_transaction(
        aid, "sell", 10, 80, 800, "2026-08-20", portfolio_id=pid, currency="USD"
    )
    out = blocked_assets(db, date(2026, 10, 18), fx_on=lambda c, d: 0.5)
    assert out[aid]["loss_eur"] == -100.0


def test_blocked_assets_raises_when_tax_calculation_fails(tmp_path, monkeypatch):
    db, _ = _db_with_loss_sale(tmp_path)

    def boom(self, *a, **k):
        raise RuntimeError("fifo broke")

    monkeypatch.setattr(
        "portf_manager.tax_calculator.TaxCalculator.calculate_tax_report", boom
    )
    with pytest.raises(WashSaleUnavailable):
        blocked_assets(db, date(2026, 10, 18), fx_on=lambda c, d: 1.0)
