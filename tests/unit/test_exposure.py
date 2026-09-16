"""Exposure rollups with funds looked through."""

from datetime import date
from unittest.mock import MagicMock

from portf_manager.services import exposure


def _db(assets, prices, profiles=None, txns=None):
    """A db whose holdings are one buy per asset, at quantity 1."""
    profiles = profiles or {}
    db = MagicMock()
    db.get_all_transactions.return_value = (
        txns
        if txns is not None
        else [
            {
                "id": i,
                "asset_id": a["id"],
                "portfolio_id": 1,
                "transaction_type": "buy",
                "quantity": 1.0,
                "price": prices[a["id"]],
                "total_amount": prices[a["id"]],
                "fees": 0.0,
                "currency": a.get("currency", "EUR"),
                "transaction_date": "2026-01-01",
            }
            for i, a in enumerate(assets, start=1)
        ]
    )
    by_id = {a["id"]: a for a in assets}
    db.get_asset.side_effect = lambda aid: by_id.get(aid)
    db.get_latest_price.side_effect = lambda aid: {"price": prices[aid]}
    db.get_fund_profile.side_effect = lambda aid: profiles.get(aid)
    db.get_all_portfolios.return_value = [{"id": 1, "name": "Example Broker"}]
    return db


def _profile_row(
    asset_id,
    regions,
    sectors=None,
    asset_class=None,
    as_of="2026-09-01",
    hedged=0,
    hedge_currency=None,
    benchmark_key="msci_world",
):
    import json

    return {
        "asset_id": asset_id,
        "benchmark_key": benchmark_key,
        "source": "benchmark",
        "asset_class": json.dumps(asset_class or {"equity": 1.0}),
        "regions": json.dumps(regions),
        "sectors": json.dumps(sectors or {}),
        "currency_hedged": hedged,
        "hedge_currency": hedge_currency,
        "as_of": as_of,
    }


FUND = {
    "id": 1,
    "symbol": "IE0000000001",
    "name": "Example World Index Fund",
    "asset_type": "etf",
    "currency": "EUR",
    "ticker": "EXMPL.DE",
}
STOCK = {
    "id": 2,
    "symbol": "EXCO",
    "name": "Example Corp",
    "asset_type": "stock",
    "currency": "EUR",
    "ticker": "EXCO.AS",
}
COIN = {
    "id": 3,
    "symbol": "BTC",
    "name": "Bitcoin",
    "asset_type": "crypto",
    "currency": "EUR",
    "ticker": None,
}
GOLD = {
    "id": 4,
    "symbol": "GOLD",
    "name": "Example Gold ETC",
    "asset_type": "commodity",
    "currency": "EUR",
    "ticker": "GOLD.AS",
}


class TestRegions:
    def test_splits_a_fund_across_its_regions(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {1: _profile_row(1, {"north_america": 0.75, "japan": 0.25})},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_region"]["north_america"] == 75.0
        assert out["by_region"]["japan"] == 25.0

    def test_direct_stock_maps_country_to_region(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Technology", "Netherlands"),
        )
        db = _db([STOCK], {2: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_region"]["europe_ex_uk"] == 100.0
        assert out["by_country"]["Netherlands"] == 100.0

    def test_crypto_is_excluded_from_regions_and_sectors(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Cryptocurrency", "Global"),
        )
        db = _db([COIN], {3: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_asset_class"]["crypto"] == 100.0
        assert out["by_region"] == {}
        assert out["by_sector"] == {}

    def test_direct_commodity_is_not_filed_as_equity(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Unknown", "Global"),
        )
        db = _db([GOLD], {4: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_asset_class"]["commodity"] == 100.0
        assert "equity" not in out["by_asset_class"]


class TestCoverage:
    def test_an_unprofiled_fund_is_named_not_just_unknown(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["classified_pct"] == 0.0
        assert out["coverage"]["unprofiled"][0]["symbol"] == "IE0000000001"
        assert out["coverage"]["unprofiled"][0]["value_eur"] == 100.0

    def test_classified_pct_mixes_profiled_and_direct(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Technology", "Netherlands"),
        )
        db = _db(
            [FUND, STOCK],
            {1: 100.0, 2: 100.0},
            {1: _profile_row(1, {"north_america": 1.0})},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["classified_pct"] == 100.0

    def test_stale_profile_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {1: _profile_row(1, {"north_america": 1.0}, as_of="2024-01-01")},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["stale_profiles"][0]["symbol"] == "IE0000000001"


class TestCurrencyExposure:
    def test_unhedged_fund_reports_underlying_currencies(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {1: _profile_row(1, {"north_america": 0.7, "japan": 0.3})},
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_currency_exposure"]["USD"] == 70.0
        assert out["by_currency_exposure"]["JPY"] == 30.0
        # Quote currency is unchanged and still EUR.
        assert out["by_currency"]["EUR"] == 100.0

    def test_hedged_fund_reports_its_hedge_currency(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {
                1: _profile_row(
                    1,
                    {"north_america": 1.0},
                    asset_class={"bond": 1.0},
                    hedged=1,
                    hedge_currency="EUR",
                    benchmark_key="global_agg_corp",
                )
            },
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_currency_exposure"]["EUR"] == 100.0


class TestSectors:
    def test_fund_sectors_are_weighted_by_its_equity_value(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db(
            [FUND],
            {1: 100.0},
            {
                1: _profile_row(
                    1,
                    {"north_america": 1.0},
                    sectors={"Technology": 0.4, "Healthcare": 0.6},
                )
            },
        )
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_sector"]["Healthcare"] == 60.0
        assert out["by_sector"]["Technology"] == 40.0
        assert out["coverage"]["sector_classified_pct"] == 100.0

    def test_missing_fund_sectors_lower_sector_coverage_only(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0}, {1: _profile_row(1, {"north_america": 1.0})})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["coverage"]["classified_pct"] == 100.0
        assert out["coverage"]["sector_classified_pct"] == 0.0


class TestBackwardCompatibility:
    def test_keeps_the_keys_finance_review_reads(self, monkeypatch):
        monkeypatch.setattr(
            exposure,
            "_resolve_sector_country",
            lambda db, a: ("Technology", "Netherlands"),
        )
        db = _db([STOCK], {2: 100.0})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        for key in (
            "by_asset_type",
            "by_currency",
            "by_sector",
            "by_country",
            "concentration_hhi",
            "largest_position_pct",
            "largest_position_symbol",
            "largest_position_name",
            "total_value_eur",
        ):
            assert key in out

    def test_country_puts_fund_value_in_one_named_bucket(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0}, {1: _profile_row(1, {"north_america": 1.0})})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["by_country"][exposure.VIA_FUNDS_LABEL] == 100.0

    def test_empty_portfolio_returns_zeros(self):
        db = _db([], {})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        assert out["total_value_eur"] == 0.0
        assert out["coverage"]["classified_pct"] == 0.0
        assert out["funds"] == []


class TestFundsList:
    def test_exposes_held_funds_for_overlap_detection(self, monkeypatch):
        monkeypatch.setattr(
            exposure, "_resolve_sector_country", lambda db, a: ("Unknown", "Unknown")
        )
        db = _db([FUND], {1: 100.0}, {1: _profile_row(1, {"north_america": 1.0})})
        out = exposure.compute_exposure(db, fx=lambda c: 1.0, today=date(2026, 9, 16))
        fund = out["funds"][0]
        assert fund["benchmark_key"] == "msci_world"
        assert fund["portfolio_name"] == "Example Broker"
        assert fund["value_eur"] == 100.0
