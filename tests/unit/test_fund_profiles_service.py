"""Fund profile parsing, validation and construction from a benchmark."""

from datetime import date
from unittest.mock import MagicMock, patch

from portf_manager.services import fund_profiles as fp


class TestParse:
    def test_parses_json_columns(self):
        row = {
            "asset_id": 1,
            "benchmark_key": "msci_world",
            "source": "benchmark",
            "asset_class": '{"equity": 1.0}',
            "regions": '{"north_america": 0.72, "europe_ex_uk": 0.28}',
            "sectors": '{"Technology": 0.25}',
            "currency_hedged": 0,
            "hedge_currency": None,
            "as_of": "2026-09-01",
        }
        parsed = fp.parse_profile(row)
        assert parsed["regions"]["north_america"] == 0.72
        assert parsed["asset_class"] == {"equity": 1.0}
        assert parsed["currency_hedged"] is False

    def test_none_row_returns_none(self):
        assert fp.parse_profile(None) is None

    def test_malformed_json_becomes_empty_map(self):
        row = {
            "asset_id": 1,
            "benchmark_key": None,
            "source": "manual",
            "asset_class": "not json",
            "regions": "{}",
            "sectors": "{}",
            "currency_hedged": 1,
            "hedge_currency": "EUR",
            "as_of": "2026-09-01",
        }
        assert fp.parse_profile(row)["asset_class"] == {}


class TestSerialize:
    def test_roundtrips_through_parse(self):
        profile = {
            "asset_id": 7,
            "benchmark_key": "msci_em",
            "source": "manual",
            "asset_class": {"equity": 1.0},
            "regions": {"emerging": 1.0},
            "sectors": {},
            "currency_hedged": True,
            "hedge_currency": "EUR",
            "as_of": "2026-09-01",
            "notes": None,
        }
        row = fp.serialize_profile(profile)
        assert row["currency_hedged"] == 1
        assert fp.parse_profile({**row, "asset_id": 7})["regions"] == {"emerging": 1.0}


class TestValidate:
    def _valid(self):
        return {
            "asset_class": {"equity": 1.0},
            "regions": {"north_america": 0.5, "emerging": 0.5},
            "sectors": {"Technology": 1.0},
            "benchmark_key": "msci_world",
            "source": "manual",
        }

    def test_accepts_a_valid_profile(self):
        assert fp.validate_profile(self._valid()) == []

    def test_rejects_regions_that_do_not_sum_to_one(self):
        bad = self._valid()
        bad["regions"] = {"north_america": 0.5}
        assert any("regions" in p for p in fp.validate_profile(bad))

    def test_rejects_an_unknown_region_key(self):
        bad = self._valid()
        bad["regions"] = {"mars": 1.0}
        assert any("mars" in p for p in fp.validate_profile(bad))

    def test_rejects_an_unknown_benchmark(self):
        bad = self._valid()
        bad["benchmark_key"] = "nope"
        assert any("benchmark" in p for p in fp.validate_profile(bad))

    def test_rejects_an_unknown_source(self):
        bad = self._valid()
        bad["source"] = "guesswork"
        assert any("source" in p for p in fp.validate_profile(bad))

    def test_allows_empty_sectors(self):
        ok = self._valid()
        ok["sectors"] = {}
        assert fp.validate_profile(ok) == []


class TestBuildFromBenchmark:
    def test_takes_regions_from_the_benchmark_and_sectors_from_yfinance(self):
        asset = {
            "id": 3,
            "symbol": "IE0000000001",
            "ticker": "EXMPL.DE",
            "currency": "EUR",
            "name": "Example World Index Fund",
        }
        composition = {
            "sectors": {"Technology": 0.25, "Healthcare": 0.1},
            "asset_class": {"equity": 1.0},
            "stale": False,
        }
        with patch.object(fp.market, "get_fund_composition", return_value=composition):
            profile = fp.build_from_benchmark(MagicMock(), asset, "msci_world")
        assert profile["source"] == "benchmark"
        assert profile["benchmark_key"] == "msci_world"
        assert profile["regions"]["north_america"] == 0.72
        assert profile["sectors"]["Technology"] == 0.25
        assert profile["asset_class"] == {"equity": 1.0}

    def test_falls_back_to_the_benchmark_asset_class_when_yfinance_fails(self):
        asset = {
            "id": 4,
            "symbol": "IE0000000002",
            "ticker": None,
            "currency": "EUR",
            "name": "Example Bond Index Fund",
        }
        composition = {"sectors": {}, "asset_class": {}, "stale": True}
        with patch.object(fp.market, "get_fund_composition", return_value=composition):
            profile = fp.build_from_benchmark(MagicMock(), asset, "global_agg_corp")
        assert profile["asset_class"] == {"bond": 1.0}
        assert profile["sectors"] == {}

    def test_unknown_benchmark_raises(self):
        asset = {
            "id": 5,
            "symbol": "IE0000000003",
            "ticker": "X",
            "currency": "EUR",
        }
        try:
            fp.build_from_benchmark(MagicMock(), asset, "nope")
        except ValueError as e:
            assert "nope" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_uses_the_symbol_when_no_ticker_is_set(self):
        asset = {
            "id": 6,
            "symbol": "EXMPL.DE",
            "ticker": None,
            "currency": "EUR",
        }
        with patch.object(
            fp.market,
            "get_fund_composition",
            return_value={"sectors": {}, "asset_class": {}, "stale": True},
        ) as get_comp:
            fp.build_from_benchmark(MagicMock(), asset, "msci_world")
        assert get_comp.call_args[0][1] == "EXMPL.DE"


class TestIsStale:
    def test_older_than_a_year_is_stale(self):
        assert fp.is_stale({"as_of": "2025-01-01"}, date(2026, 9, 16)) is True

    def test_recent_is_not_stale(self):
        assert fp.is_stale({"as_of": "2026-06-01"}, date(2026, 9, 16)) is False

    def test_missing_as_of_counts_as_stale(self):
        assert fp.is_stale({"as_of": None}, date(2026, 9, 16)) is True


def test_ongoing_charges_must_be_between_zero_and_ten():
    from portf_manager.services import fund_profiles as fp

    base = {
        "regions": {"north_america": 1.0},
        "asset_class": {"equity": 1.0},
        "source": "manual",
    }
    assert fp.validate_profile({**base, "ongoing_charges_pct": 0.2}) == []
    assert fp.validate_profile({**base, "ongoing_charges_pct": None}) == []
    assert fp.validate_profile({**base, "ongoing_charges_pct": 0}) == []
    for bad in (-0.1, 10.5):
        problems = fp.validate_profile({**base, "ongoing_charges_pct": bad})
        assert any("ongoing_charges_pct" in p for p in problems)


def test_ongoing_charges_round_trip_through_serialize():
    from portf_manager.services import fund_profiles as fp

    out = fp.serialize_profile({"asset_id": 1, "ongoing_charges_pct": 0.2})
    assert out["ongoing_charges_pct"] == 0.2
    assert fp.serialize_profile({"asset_id": 1})["ongoing_charges_pct"] is None


def test_migration_v34_adds_ongoing_charges_and_is_rerunnable(tmp_path):
    from portf_manager.database import Database

    db = Database(str(tmp_path / "v33.db"))
    with db.get_connection() as conn:
        # Drop the column to look like a v33 database (SQLite >= 3.35).
        conn.execute("ALTER TABLE fund_profiles DROP COLUMN ongoing_charges_pct")
        conn.commit()
        db._migrate_to_v34(conn)
        db._migrate_to_v34(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fund_profiles)")}
    assert "ongoing_charges_pct" in cols
