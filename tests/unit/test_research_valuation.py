"""Tests for the research valuation calculator (compute_targets)."""

import json
from unittest.mock import MagicMock

from portf_manager.services.research import compute_targets


def test_pe_valuation_with_margin_and_premium():
    r = compute_targets(
        {"trailingEps": 6.0},
        "pe",
        {"target_pe": 25, "margin_of_safety": 20, "premium": 25},
    )
    assert r["fair_value"] == 150.0
    assert r["buy_below"] == 120.0  # 150 * (1-0.20)
    assert r["sell_above"] == 187.5  # 150 * (1+0.25)


def test_pe_uses_explicit_eps_override():
    r = compute_targets({"trailingEps": 1.0}, "pe", {"eps": 10, "target_pe": 20})
    assert r["fair_value"] == 200.0


def test_dividend_yield_valuation():
    # €2 dividend at a 4% target yield → fair value €50.
    r = compute_targets(
        {"dividendRate": 2.0},
        "dividend_yield",
        {"target_yield": 4, "margin_of_safety": 10},
    )
    assert r["fair_value"] == 50.0
    assert r["buy_below"] == 45.0


def test_missing_inputs_return_none():
    r = compute_targets({}, "pe", {"target_pe": 25})  # no EPS
    assert r["fair_value"] is None
    assert r["buy_below"] is None


def test_unknown_method_is_safe():
    r = compute_targets({"trailingEps": 5}, "dcf", {})
    assert r["fair_value"] is None


_MOCK_FUND = {"symbol": "AAPL", "trailingPE": 25.0}
_MOCK_REPORT_JSON = json.dumps(
    {
        "recommendation": "BUY",
        "confidence": "high",
        "summary": "Solid outlook.",
        "rationale": "Strong margins.",
        "risks": ["competition"],
        "catalysts": ["new product"],
        "fair_value": 175.0,
        "buy_below": 140.0,
        "sell_above": 200.0,
    }
)


def test_generate_valuation_uses_plain_generate_when_not_search_capable(mocker):
    """Non-search LLM calls generate() and sources = pre-fetched yfinance news."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.return_value = _MOCK_REPORT_JSON
    mocker.patch(
        "portf_manager.services.research.get_llm_client", return_value=mock_llm
    )

    news = [
        {"title": "Apple Q1 beats", "url": "http://example.com", "publisher": "Reuters"}
    ]
    result = generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=120.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
        news=news,
    )

    mock_llm.generate.assert_called_once()
    assert result["recommendation"] == "BUY"
    assert result["sources"] == news


def test_generate_valuation_uses_search_when_capable(mocker):
    """Search-capable LLM calls generate_with_search() and sources = grounding metadata."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate", "generate_with_search"])
    envelope = json.dumps(
        {
            "text": _MOCK_REPORT_JSON,
            "sources": [{"title": "Earnings beat", "url": "http://news.example.com"}],
        }
    )
    mock_llm.generate_with_search.return_value = envelope
    mocker.patch(
        "portf_manager.services.research.get_llm_client", return_value=mock_llm
    )

    result = generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=120.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
        news=[],
    )

    mock_llm.generate_with_search.assert_called_once()
    mock_llm.generate.assert_not_called()
    assert result["recommendation"] == "BUY"
    assert result["sources"] == [
        {"title": "Earnings beat", "url": "http://news.example.com"}
    ]


def test_search_prompt_omits_prefetched_headlines(mocker):
    """The search-path prompt tells the model to search; it does not include pre-fetched news."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate", "generate_with_search"])
    mock_llm.generate_with_search.return_value = json.dumps(
        {"text": _MOCK_REPORT_JSON, "sources": []}
    )
    mocker.patch(
        "portf_manager.services.research.get_llm_client", return_value=mock_llm
    )

    generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=0.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
        news=[{"title": "Should not appear", "url": "http://x.com", "publisher": "X"}],
    )

    prompt_used = mock_llm.generate_with_search.call_args[0][0]
    assert "Should not appear" not in prompt_used
    assert "web search" in prompt_used.lower()


def test_generate_valuation_returns_error_dict_on_llm_failure(mocker):
    """LLM exception returns a safe error dict instead of raising."""
    from portf_manager.services.research import generate_valuation_report

    mock_llm = MagicMock(spec=["generate"])
    mock_llm.generate.side_effect = RuntimeError("API down")
    mocker.patch(
        "portf_manager.services.research.get_llm_client", return_value=mock_llm
    )

    result = generate_valuation_report(
        symbol="AAPL",
        asset_name="Apple Inc.",
        asset_type="stock",
        current_price=150.0,
        avg_cost=120.0,
        currency="USD",
        fundamentals=_MOCK_FUND,
    )

    assert result["recommendation"] == "HOLD"
    assert result["confidence"] == "low"
    assert "API down" in result["summary"]
