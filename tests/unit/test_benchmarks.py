"""The benchmark index table is hand-edited data, so it gets a schema test."""

from portf_manager.services import benchmarks as bm


class TestSchema:
    def test_shipped_file_is_valid(self):
        assert bm.validate_benchmarks(bm.load_benchmarks()) == []

    def test_every_entry_has_the_required_fields(self):
        for key, entry in bm.load_benchmarks().items():
            for field in ("label", "family", "asset_class", "regions", "as_of"):
                assert field in entry, f"{key} missing {field}"

    def test_region_weights_sum_to_one(self):
        for key, entry in bm.load_benchmarks().items():
            total = sum(entry["regions"].values())
            assert abs(total - 1.0) < 0.005, f"{key} regions sum to {total}"

    def test_only_known_region_keys_are_used(self):
        for key, entry in bm.load_benchmarks().items():
            unknown = set(entry["regions"]) - set(bm.REGIONS)
            assert not unknown, f"{key} uses unknown regions {unknown}"

    def test_parents_refer_to_known_keys(self):
        data = bm.load_benchmarks()
        for key, entry in data.items():
            parent = entry.get("parent")
            assert parent is None or parent in data, f"{key} parent {parent} unknown"

    def test_covers_the_index_families_actually_held(self):
        keys = set(bm.load_benchmarks())
        for expected in (
            "msci_world",
            "msci_em",
            "sp500",
            "msci_japan",
            "global_agg_corp",
            "global_agg_gov",
        ):
            assert expected in keys


class TestValidation:
    def test_reports_a_bad_weight_sum(self):
        bad = {
            "x": {
                "label": "X",
                "family": "f",
                "asset_class": {"equity": 1.0},
                "regions": {"uk": 0.5},
                "as_of": "2026-09-01",
            }
        }
        problems = bm.validate_benchmarks(bad)
        assert any("regions sum" in p for p in problems)

    def test_reports_an_unknown_parent(self):
        bad = {
            "x": {
                "label": "X",
                "family": "f",
                "parent": "nope",
                "asset_class": {"equity": 1.0},
                "regions": {"uk": 1.0},
                "as_of": "2026-09-01",
            }
        }
        assert any("parent" in p for p in bm.validate_benchmarks(bad))

    def test_reports_empty_table(self):
        problems = bm.validate_benchmarks({})
        assert len(problems) > 0
        assert any("empty" in p for p in problems)


class TestAncestors:
    def test_walks_the_parent_chain(self):
        chain = bm.ancestors("sp500")
        assert "msci_world" in chain

    def test_unknown_key_has_no_ancestors(self):
        assert bm.ancestors("nope") == []

    def test_is_cycle_safe(self, monkeypatch):
        cyclic = {
            "a": {
                "parent": "b",
                "label": "A",
                "family": "f",
                "asset_class": {"equity": 1.0},
                "regions": {"uk": 1.0},
                "as_of": "2026-09-01",
            },
            "b": {
                "parent": "a",
                "label": "B",
                "family": "f",
                "asset_class": {"equity": 1.0},
                "regions": {"uk": 1.0},
                "as_of": "2026-09-01",
            },
        }
        monkeypatch.setattr(bm, "_CACHE", cyclic)
        assert bm.ancestors("a") == ["b"]


class TestChoices:
    def test_returns_key_and_label_sorted(self):
        choices = bm.benchmark_choices()
        labels = [c["label"] for c in choices]
        assert labels == sorted(labels)
        assert all({"key", "label", "family", "asset_class"} <= set(c) for c in choices)
