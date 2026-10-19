"""Output tests for the analytics tools; the API is patched at pfm_mcp.client._get."""

import urllib.error
from unittest.mock import patch

from helpers import reply
from pfm_mcp.tools import analytics


def test_performance_reports_returns_and_benchmark():
    payload = {
        "invested_eur": 10000.0,
        "current_value_eur": 12000.0,
        "realised_pnl_eur": 500.0,
        "total_return_pct": 20.0,
        "money_weighted_irr_pct": 18.5,
        "period_return_pct": 5.0,
        "benchmark_return_pct": 3.0,
        "benchmark": "^GSPC",
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.performance()
    assert "Total return:     +20.00%" in result
    assert "Benchmark (EUR):  +3.00%  [^GSPC]" in result


def test_performance_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.performance()
    assert "Error fetching performance:" in result


def test_dividends_reports_totals_and_by_symbol():
    payload = {
        "by_year": {"2025": 300.0, "2026": 150.0},
        "ttm": 400.0,
        "projected_annual": 500.0,
        "ttm_by_symbol": {"NVDA": 100.0},
        "names": {"NVDA": "NVIDIA"},
        "yield_on_cost": {"NVDA": 2.5},
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.dividends()
    assert "Trailing 12m total:  400.00 EUR" in result
    assert "NVDA" in result
    assert "YoC 2.5%" in result


def test_dividends_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.dividends()
    assert "Error fetching dividends:" in result


def test_diversification_reports_breakdown_and_concentration():
    payload = {
        "total_value_eur": 10000.0,
        "largest_position_symbol": "NVDA",
        "largest_position_name": "NVIDIA",
        "largest_position_pct": 25.0,
        "concentration_hhi": 1800.0,
        "by_asset_type": {"stock": 70.0, "crypto": 30.0},
        "by_currency": {"EUR": 60.0, "USD": 40.0},
        "by_sector": {},
        "by_country": {},
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.diversification()
    assert "Largest position: NVDA" in result
    assert "Concentration HHI: 1800" in result
    assert "stock" in result


def test_diversification_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.diversification()
    assert "Error fetching diversification:" in result


def test_diversification_shows_regions_coverage_and_overlap():
    diversification_payload = {
        "total_value_eur": 10000.0,
        "by_asset_type": {"etf": 60.0, "stock": 40.0},
        "by_asset_class": {"equity": 85.0, "bond": 15.0},
        "by_currency": {"EUR": 80.0, "USD": 20.0},
        "by_currency_exposure": {"USD": 55.0, "EUR": 35.0, "JPY": 10.0},
        "by_sector": {"Technology": 30.0},
        "by_country": {"Via funds (see regions)": 60.0, "Netherlands": 40.0},
        "by_region": {"north_america": 55.0, "europe_ex_uk": 35.0},
        "by_region_equity": {"north_america": 60.0, "europe_ex_uk": 40.0},
        "concentration_hhi": 1400.0,
        "largest_position_pct": 30.0,
        "largest_position_symbol": "IE0000000001",
        "largest_position_name": "Example World Index Fund",
        "coverage": {
            "classified_pct": 84.0,
            "sector_classified_pct": 70.0,
            "unprofiled": [
                {
                    "asset_id": 9,
                    "symbol": "IE0000000009",
                    "name": "Example Unprofiled Fund",
                    "value_eur": 1600.0,
                }
            ],
            "stale_profiles": [],
        },
    }
    overlap_payload = {
        "groups": [
            {
                "kind": "consolidation_candidate",
                "reason": "Both track the same exposure (MSCI Emerging Markets).",
                "members": [
                    {
                        "asset_id": 1,
                        "symbol": "IE0000000001",
                        "name": "Example EM Index Fund",
                        "portfolio_name": "Broker A",
                        "value_eur": 1000.0,
                    },
                    {
                        "asset_id": 2,
                        "symbol": "IE0000000002",
                        "name": "Example EM ETF",
                        "portfolio_name": "Broker B",
                        "value_eur": 800.0,
                    },
                ],
                "combined_value_eur": 1800.0,
                "combined_pct": 18.0,
                "transferable": False,
            }
        ],
        "total_value_eur": 10000.0,
    }
    with patch(
        "pfm_mcp.client._get",
        side_effect=[
            reply(diversification_payload),
            reply(overlap_payload),
        ],
    ):
        result = analytics.diversification()
    assert "By region (equity" in result
    assert "north_america" in result
    assert "84.0%" in result
    assert "Example Unprofiled Fund" in result
    assert "Example EM Index Fund" in result


def test_diversification_survives_a_failing_overlap_call():
    payload = {
        "total_value_eur": 100.0,
        "by_asset_type": {"stock": 100.0},
        "by_asset_class": {"equity": 100.0},
        "by_currency": {"EUR": 100.0},
        "by_currency_exposure": {"EUR": 100.0},
        "by_sector": {"Technology": 100.0},
        "by_country": {"Netherlands": 100.0},
        "by_region": {"europe_ex_uk": 100.0},
        "by_region_equity": {"europe_ex_uk": 100.0},
        "concentration_hhi": 10000.0,
        "coverage": {
            "classified_pct": 100.0,
            "sector_classified_pct": 100.0,
            "unprofiled": [],
            "stale_profiles": [],
        },
    }
    with patch(
        "pfm_mcp.client._get",
        side_effect=[reply(payload), urllib.error.URLError("boom")],
    ):
        result = analytics.diversification()
    assert "DIVERSIFICATION" in result
    assert "europe_ex_uk" in result


def test_risk_reports_metrics():
    payload = {
        "max_drawdown_pct": -15.0,
        "current_drawdown_pct": -3.0,
        "volatility_pct": 22.0,
        "sharpe_ratio": 1.1,
        "sortino_ratio": 1.5,
        "calmar_ratio": None,
        "period_return_pct": 12.5,
        "benchmark_return_pct": 9.0,
        "start_date": "2025-09-24",
        "end_date": "2026-09-24",
        "low_confidence": False,
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.risk()
    assert "Last 12 months (2025-09-24 → 2026-09-24)" in result
    assert "Max drawdown:     -15.00%" in result
    assert "Current drawdown: -3.00%" in result
    assert "Return:           +12.50% (benchmark +9.00%)" in result
    assert "Sharpe / Sortino / Calmar: 1.10 / 1.50 / n/a" in result
    assert "low confidence" not in result


def test_risk_flags_low_confidence():
    payload = {"sharpe_ratio": 0.4, "low_confidence": True}
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.risk()
    assert "low confidence" in result


def test_risk_reports_note_when_insufficient_data():
    with patch(
        "pfm_mcp.client._get", return_value=reply({"note": "Not enough snapshots yet."})
    ):
        result = analytics.risk()
    assert "Not enough snapshots yet." in result


def test_risk_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.risk()
    assert "Error fetching risk metrics:" in result


def test_portfolio_health_reports_scores_and_recommendations():
    payload = {
        "scores": {"diversification": {"score": 7, "reason": "Well spread"}},
        "summary": "Overall solid.",
        "recommendations": ["Rebalance towards bonds"],
        "cached_at": "2026-07-10T00:00:00Z",
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.portfolio_health()
    assert "Diversification" in result
    assert "7/10" in result
    assert "Rebalance towards bonds" in result


def test_portfolio_health_reports_cached_error():
    with patch(
        "pfm_mcp.client._get",
        return_value=reply(
            {"error": "No cached analysis. Run it from the web UI first."}
        ),
    ):
        result = analytics.portfolio_health()
    assert "No cached analysis" in result


def test_portfolio_health_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.portfolio_health()
    assert "Error fetching portfolio health:" in result


def test_fundamentals_lists_key_metrics():
    payload = {
        "shortName": "NVIDIA Corp",
        "sector": "Technology",
        "marketCap": 3_000_000_000_000,
        "trailingPE": 45.2,
        "dividendYield": 0.0003,
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.fundamentals("nvda")
    assert "FUNDAMENTALS — NVDA:" in result
    assert "NVIDIA Corp" in result
    assert "$3000.0B" in result


def test_fundamentals_reports_no_data():
    with patch("pfm_mcp.client._get", return_value=reply({})):
        result = analytics.fundamentals("ZZZZ")
    assert "No fundamentals available for ZZZZ." in result


def test_fundamentals_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.fundamentals("NVDA")
    assert "Error fetching fundamentals for NVDA:" in result


def _rebalance_payload(targets_sum=100.0):
    return {
        "total_value_eur": 100000.0,
        "targets_sum_pct": targets_sum,
        "allocations": [
            {
                "asset_type": "etf",
                "current_value_eur": 60000.0,
                "current_pct": 60.0,
                "target_pct": 70.0,
                "drift_pct": -10.0,
                "drift_eur": -10000.0,
            },
            {
                "asset_type": "stock",
                "current_value_eur": 40000.0,
                "current_pct": 40.0,
                "target_pct": 30.0,
                "drift_pct": 10.0,
                "drift_eur": 10000.0,
            },
        ],
        "actions": [
            {
                "asset_type": "etf",
                "action": "buy",
                "amount_eur": 10000.0,
                "reason": "Below target by 10.0%",
            }
        ],
    }


def test_rebalance_analysis_reports_drift_and_actions():
    with patch("pfm_mcp.client._get", return_value=reply(_rebalance_payload())):
        result = analytics.rebalance_analysis()
    assert "REBALANCE — total 100,000.00 EUR" in result
    etf = next(ln for ln in result.splitlines() if ln.strip().startswith("etf"))
    assert "drift -10.0pp" in etf
    action = next(ln for ln in result.splitlines() if "BUY" in ln)
    assert "10,000.00 EUR" in action
    assert "Targets sum to" not in result


def test_rebalance_analysis_warns_when_targets_do_not_sum_to_100():
    with patch("pfm_mcp.client._get", return_value=reply(_rebalance_payload(90.0))):
        result = analytics.rebalance_analysis()
    assert "Targets sum to 90.0%" in result


def test_rebalance_analysis_reports_no_targets():
    payload = {
        "total_value_eur": 100000.0,
        "allocations": [],
        "actions": [],
        "targets_sum_pct": 0,
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        assert "No allocation targets set" in analytics.rebalance_analysis()


def test_rebalance_analysis_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.rebalance_analysis()
    assert "Error fetching rebalance analysis:" in result


def test_data_freshness_lists_stale_assets():
    payload = {
        "last_refresh": "2026-09-14T20:00:05",
        "refresh_age_hours": 14.2,
        "prices_as_of": "2026-09-14",
        "stale_days_threshold": 4,
        "checked": 50,
        "stale_count": 2,
        "stale": [
            {
                "symbol": "XYZ",
                "name": "Example Corp",
                "price_date": "2026-09-01",
                "age_days": 14,
                "reason": "stale",
            },
            {
                "symbol": "LOAN",
                "name": "Example Loan",
                "price_date": None,
                "age_days": None,
                "reason": "no price data",
            },
        ],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.data_freshness()
    assert "14.2h ago" in result
    assert "2 of 50" in result
    xyz = next(ln for ln in result.splitlines() if "XYZ" in ln)
    assert "14d old" in xyz
    loan = next(ln for ln in result.splitlines() if "LOAN" in ln)
    assert "no price data" in loan


def test_data_freshness_reports_all_fresh():
    payload = {
        "last_refresh": "2026-09-14T20:00:05",
        "refresh_age_hours": 2.0,
        "prices_as_of": "2026-09-14",
        "stale_days_threshold": 4,
        "checked": 50,
        "stale_count": 0,
        "stale": [],
    }
    with patch("pfm_mcp.client._get", return_value=reply(payload)):
        result = analytics.data_freshness()
    assert "All 50 priced assets are fresh" in result


def test_data_freshness_reports_error_on_failure():
    with patch("pfm_mcp.client._get", side_effect=urllib.error.URLError("boom")):
        result = analytics.data_freshness()
    assert "Error fetching data freshness:" in result
