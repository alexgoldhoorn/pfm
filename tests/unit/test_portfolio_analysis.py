from unittest.mock import MagicMock


def _mock_db(txns=None, assets=None):
    db = MagicMock()
    db.get_all_transactions.return_value = txns or []
    db.get_all_assets.return_value = assets or []
    db.get_latest_price.return_value = None
    db.get_asset.return_value = None
    db.get_all_portfolios.return_value = []
    db.get_snapshots.return_value = []
    return db


class TestGatherPerformance:
    def test_empty_returns_zeros(self):
        from portf_manager.services.portfolio_advisor import gather_performance

        result = gather_performance(_mock_db(), portfolio_id=None)
        assert result["invested_eur"] == 0.0
        assert result["current_value_eur"] == 0.0
        assert result["cagr_pct"] is None

    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_performance

        result = gather_performance(_mock_db(), portfolio_id=None)
        for key in (
            "invested_eur",
            "current_value_eur",
            "total_return_pct",
            "cagr_pct",
            "irr_pct",
            "inception_date",
        ):
            assert key in result


class TestGatherRisk:
    def test_insufficient_snapshots(self):
        from portf_manager.services.portfolio_advisor import gather_risk

        db = _mock_db()
        db.get_snapshots.return_value = [
            {"total_value_eur": 1000, "snapshot_date": "2026-01-01"}
        ]
        result = gather_risk(db)
        assert result["sharpe_ratio"] is None
        assert "note" in result

    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_risk

        result = gather_risk(_mock_db())
        for key in (
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "max_drawdown_pct",
            "volatility_pct",
        ):
            assert key in result


class TestGatherFeesAndDividends:
    def test_empty_returns_zeros(self):
        from portf_manager.services.portfolio_advisor import gather_fees_and_dividends

        result = gather_fees_and_dividends(_mock_db(), portfolio_id=None)
        assert result["total_fees_eur"] == 0.0
        assert result["ttm_dividends_eur"] == 0.0

    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_fees_and_dividends

        result = gather_fees_and_dividends(_mock_db(), portfolio_id=None)
        for key in (
            "total_fees_eur",
            "fee_drag_pct",
            "ttm_dividends_eur",
            "projected_annual_eur",
        ):
            assert key in result


class TestGatherTax:
    def test_returns_required_keys(self):
        from portf_manager.services.portfolio_advisor import gather_tax

        result = gather_tax(_mock_db(), portfolio_id=None)
        for key in ("harvest_candidates", "harvestable_loss_eur", "estimated_tax_eur"):
            assert key in result


class TestPromptAndParse:
    _bundle = {
        "performance": {
            "invested_eur": 10000,
            "current_value_eur": 11000,
            "total_return_pct": 10.0,
            "cagr_pct": 5.0,
            "irr_pct": 5.5,
            "inception_date": "2023-01-01",
        },
        "risk": {
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "calmar_ratio": 0.8,
            "max_drawdown_pct": -8.0,
            "volatility_pct": 12.0,
            "note": None,
        },
        "diversification": {
            "by_sector": {"Technology": 45},
            "by_country": {"US": 60},
            "by_currency": {"USD": 70},
            "by_asset_type": {"stock": 80},
            "concentration_hhi": 2800,
            "total_value_eur": 11000,
        },
        "fees_and_dividends": {
            "total_fees_eur": 50,
            "fee_drag_pct": 0.5,
            "ttm_dividends_eur": 300,
            "projected_annual_eur": 320,
        },
        "tax": {
            "harvest_candidates": [],
            "harvestable_loss_eur": 0,
            "estimated_tax_eur": 500,
            "realised_gain_eur": 1000,
            "savings_base_eur": 1300,
            "year": 2026,
        },
        "holdings": [
            {
                "symbol": "AAPL",
                "name": "Apple",
                "weight_pct": 20,
                "value_eur": 2200,
                "pe": 28,
                "dividend_yield": 0.5,
                "sector": "Technology",
            }
        ],
    }

    def test_prompt_contains_key_sections(self):
        from portf_manager.services.portfolio_advisor import build_analysis_prompt

        prompt = build_analysis_prompt(self._bundle)
        assert "Sharpe" in prompt
        assert "Technology" in prompt
        assert "AAPL" in prompt
        assert '"scores"' in prompt

    def test_parse_valid_json(self):
        from portf_manager.services.portfolio_advisor import parse_analysis_response

        raw = '{"scores": {"diversification": {"score": 7, "reason": "ok"}}, "recommendations": [], "summary": "Good"}'
        result = parse_analysis_response(raw)
        assert result["scores"]["diversification"]["score"] == 7
        assert result["summary"] == "Good"

    def test_parse_strips_code_fences(self):
        from portf_manager.services.portfolio_advisor import parse_analysis_response

        raw = '```json\n{"scores": {}, "recommendations": [], "summary": "x"}\n```'
        result = parse_analysis_response(raw)
        assert result["summary"] == "x"

    def test_parse_malformed_returns_error_key(self):
        from portf_manager.services.portfolio_advisor import parse_analysis_response

        result = parse_analysis_response("not json")
        assert "error" in result
