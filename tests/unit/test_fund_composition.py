"""Fund composition from yfinance funds_data: sectors + asset-class split."""

from unittest.mock import MagicMock, patch

from portf_manager import market


def _db():
    db = MagicMock()
    db.cache_get.return_value = None
    return db


def _funds_data(sectors=None, classes=None):
    fd = MagicMock()
    fd.sector_weightings = sectors
    fd.asset_classes = classes
    return fd


class TestGetFundComposition:
    def test_normalises_yfinance_sector_keys(self):
        fd = _funds_data(
            sectors={"realestate": 0.02, "consumer_cyclical": 0.1, "technology": 0.25},
            classes={"stockPosition": 1.0},
        )
        with patch.object(market, "_fetch_funds_data", return_value=fd):
            out = market.get_fund_composition(_db(), "EXMPL.DE")
        assert out["sectors"]["Real Estate"] == 0.02
        assert out["sectors"]["Consumer Cyclical"] == 0.1
        assert out["sectors"]["Technology"] == 0.25

    def test_maps_asset_classes(self):
        fd = _funds_data(
            sectors={"technology": 1.0},
            classes={"stockPosition": 0.98, "cashPosition": 0.02},
        )
        with patch.object(market, "_fetch_funds_data", return_value=fd):
            out = market.get_fund_composition(_db(), "EXMPL.DE")
        assert out["asset_class"]["equity"] == 0.98
        assert out["asset_class"]["cash"] == 0.02

    def test_bond_fund_reports_bond(self):
        fd = _funds_data(sectors=None, classes={"bondPosition": 1.0})
        with patch.object(market, "_fetch_funds_data", return_value=fd):
            out = market.get_fund_composition(_db(), "EXBND.DE")
        assert out["asset_class"] == {"bond": 1.0}
        assert out["sectors"] == {}

    def test_failure_returns_empty_maps_not_zeros(self):
        with patch.object(market, "_fetch_funds_data", side_effect=RuntimeError("no")):
            out = market.get_fund_composition(_db(), "NOPE")
        assert out["sectors"] == {}
        assert out["asset_class"] == {}
        assert out["stale"] is True
        assert "error" in out

    def test_uses_cache_when_fresh(self):
        db = MagicMock()
        db.cache_get.return_value = {
            "symbol": "EXMPL.DE",
            "sectors": {"Technology": 1.0},
            "asset_class": {"equity": 1.0},
        }
        with patch.object(market, "_fetch_funds_data") as fetch:
            out = market.get_fund_composition(db, "EXMPL.DE")
        fetch.assert_not_called()
        assert out["source"] == "cache"
