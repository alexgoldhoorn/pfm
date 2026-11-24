"""Loose asset-name matching for imports that carry no ISIN."""

import pytest

from portf_manager.database import Database, normalize_asset_name


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


class TestNormalizeAssetName:
    def test_drops_legal_suffix(self):
        assert normalize_asset_name("EXAMPLE BANK NV") == "example bank"
        assert normalize_asset_name("Example Bank N.V.") == "example bank"
        assert normalize_asset_name("Example Corp, Inc.") == "example"
        assert normalize_asset_name("Example Group plc") == "example group"

    def test_corp_and_its_abbreviation_match(self):
        assert normalize_asset_name("EXAMPLE CORP") == normalize_asset_name(
            "EXAMPLE CP"
        )

    def test_keeps_share_class(self):
        assert normalize_asset_name("EXAMPLE WORLD ETF ACC") == "example world etf acc"
        assert normalize_asset_name("EXAMPLE WORLD ETF DIST") != normalize_asset_name(
            "EXAMPLE WORLD ETF ACC"
        )

    def test_suffix_alone_is_kept(self):
        assert normalize_asset_name("SA") == "sa"
        assert normalize_asset_name("") == ""
        assert normalize_asset_name(None) == ""


class TestGetAssetByName:
    def test_exact_match_still_wins(self, db):
        aid = db.create_asset("EXBANK", "EXAMPLE BANK NV", "stock")
        db.create_asset("NL0000000001", "EXAMPLE BANK", "stock")
        assert db.get_asset_by_name("example bank nv")["id"] == aid

    def test_suffix_difference_finds_isin_asset(self, db):
        aid = db.create_asset("NL0000000001", "EXAMPLE BANK", "stock")
        assert db.get_asset_by_name("EXAMPLE BANK NV")["id"] == aid
        assert db.get_asset_by_name("Example Bank N.V.")["id"] == aid

    def test_corp_finds_asset_stored_as_cp(self, db):
        aid = db.create_asset("US0000000001", "EXAMPLE CP", "stock")
        assert db.get_asset_by_name("EXAMPLE CORP")["id"] == aid

    def test_prefers_the_single_isin_record(self, db):
        db.create_asset("EXBANK", "Example Bank SA", "stock")
        aid = db.create_asset("ES0000000001", "EXAMPLE BANK", "stock")
        assert db.get_asset_by_name("Example Bank NV")["id"] == aid

    def test_ambiguous_is_no_match(self, db):
        db.create_asset("NL0000000001", "EXAMPLE BANK", "stock")
        db.create_asset("NL0000000002", "Example Bank SA", "stock")
        assert db.get_asset_by_name("Example Bank NV") is None

    def test_other_share_class_is_no_match(self, db):
        db.create_asset("IE0000000001", "EXAMPLE WORLD ETF ACC", "etf")
        assert db.get_asset_by_name("EXAMPLE WORLD ETF DIST") is None

    def test_unknown_name(self, db):
        db.create_asset("NL0000000001", "EXAMPLE BANK", "stock")
        assert db.get_asset_by_name("OTHER BANK NV") is None
