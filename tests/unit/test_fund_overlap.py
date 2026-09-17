"""Overlap between held funds: same index family, nesting, or similar weights."""

from portf_manager.services.exposure import find_fund_overlaps


def _fund(
    asset_id,
    name,
    benchmark_key=None,
    regions=None,
    sectors=None,
    value=1000.0,
    asset_type="etf",
):
    return {
        "asset_id": asset_id,
        "symbol": f"IE000000000{asset_id}",
        "name": name,
        "portfolio_name": "Example Broker",
        "value_eur": value,
        "asset_type": asset_type,
        "benchmark_key": benchmark_key,
        "regions": regions or {"north_america": 1.0},
        "sectors": sectors or {},
        "asset_class": {"equity": 1.0},
    }


class TestSameFamily:
    def test_two_emerging_market_funds_are_a_consolidation_candidate(self):
        funds = [
            _fund(1, "Example EM Index Fund", "msci_em", {"emerging": 1.0}),
            _fund(2, "Example EM ETF", "msci_em", {"emerging": 1.0}),
        ]
        groups = find_fund_overlaps(funds, total_value_eur=4000.0)
        assert groups[0]["kind"] == "consolidation_candidate"
        assert len(groups[0]["members"]) == 2
        assert groups[0]["combined_value_eur"] == 2000.0
        assert groups[0]["combined_pct"] == 50.0

    def test_different_families_do_not_group(self):
        funds = [
            _fund(1, "Example EM Index Fund", "msci_em", {"emerging": 1.0}),
            _fund(2, "Example Japan ETF", "msci_japan", {"japan": 1.0}),
        ]
        assert find_fund_overlaps(funds, total_value_eur=2000.0) == []

    def test_transferable_only_when_every_member_is_a_mutual_fund(self):
        both_funds = [
            _fund(1, "Example EM Index Fund A", "msci_em", asset_type="mutual_fund"),
            _fund(2, "Example EM Index Fund B", "msci_em", asset_type="mutual_fund"),
        ]
        mixed = [
            _fund(1, "Example EM Index Fund", "msci_em", asset_type="mutual_fund"),
            _fund(2, "Example EM ETF", "msci_em", asset_type="etf"),
        ]
        assert find_fund_overlaps(both_funds, 4000.0)[0]["transferable"] is True
        assert find_fund_overlaps(mixed, 4000.0)[0]["transferable"] is False


class TestNesting:
    def test_an_sp500_fund_inside_a_world_fund_is_informational(self):
        funds = [
            _fund(1, "Example World Index Fund", "msci_world"),
            _fund(2, "Example S&P 500 ETF", "sp500"),
        ]
        groups = find_fund_overlaps(funds, total_value_eur=4000.0)
        assert [g["kind"] for g in groups] == ["informational"]
        assert "S&P 500" in groups[0]["reason"] or "MSCI World" in groups[0]["reason"]

    def test_consolidation_candidates_are_listed_before_informational(self):
        funds = [
            _fund(1, "Example World Index Fund", "msci_world"),
            _fund(2, "Example S&P 500 ETF", "sp500"),
            _fund(3, "Example EM Index Fund", "msci_em", {"emerging": 1.0}),
            _fund(4, "Example EM ETF", "msci_em", {"emerging": 1.0}),
        ]
        kinds = [g["kind"] for g in find_fund_overlaps(funds, 8000.0)]
        assert kinds[0] == "consolidation_candidate"
        assert "informational" in kinds


class TestSimilarity:
    def test_near_identical_weights_without_a_benchmark_are_similar(self):
        regions = {"north_america": 0.7, "europe_ex_uk": 0.3}
        funds = [
            _fund(
                1,
                "Example Global Fund A",
                None,
                regions,
                {"Technology": 0.5, "Healthcare": 0.5},
            ),
            _fund(
                2,
                "Example Global Fund B",
                None,
                {"north_america": 0.71, "europe_ex_uk": 0.29},
                {"Technology": 0.51, "Healthcare": 0.49},
            ),
        ]
        groups = find_fund_overlaps(funds, total_value_eur=4000.0)
        assert groups[0]["kind"] == "similar"

    def test_different_weights_do_not_group(self):
        funds = [
            _fund(1, "Example US Fund", None, {"north_america": 1.0}),
            _fund(2, "Example Japan Fund", None, {"japan": 1.0}),
        ]
        assert find_fund_overlaps(funds, total_value_eur=2000.0) == []

    def test_a_fund_with_a_benchmark_is_not_matched_by_similarity(self):
        funds = [
            _fund(1, "Example World Index Fund", "msci_world"),
            _fund(2, "Example Global Fund", None, {"north_america": 1.0}),
        ]
        assert find_fund_overlaps(funds, total_value_eur=2000.0) == []


class TestEdges:
    def test_a_single_fund_produces_nothing(self):
        assert (
            find_fund_overlaps(
                [_fund(1, "Example World Index Fund", "msci_world")], 1000.0
            )
            == []
        )

    def test_no_funds_produces_nothing(self):
        assert find_fund_overlaps([], 0.0) == []

    def test_members_carry_portfolio_and_value(self):
        funds = [
            _fund(1, "Example EM Index Fund", "msci_em"),
            _fund(2, "Example EM ETF", "msci_em"),
        ]
        member = find_fund_overlaps(funds, 4000.0)[0]["members"][0]
        assert member["portfolio_name"] == "Example Broker"
        assert member["value_eur"] == 1000.0
        assert "name" in member and "symbol" in member
