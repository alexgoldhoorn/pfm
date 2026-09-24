"""Endpoint tests for Portfolio Health (LLM analysis) and the Compare table."""

import json
from datetime import datetime

import pytest
from httpx import AsyncClient

from portf_manager.llm_client import LLMError

GATHERERS = (
    "gather_performance",
    "gather_risk",
    "gather_diversification",
    "gather_fees_and_dividends",
    "gather_tax",
    "gather_holdings_fundamentals",
)
GOOD_REPLY = json.dumps(
    {
        "scores": {"diversification": {"score": 7, "reason": "ok"}},
        "recommendations": ["Rebalance"],
        "summary": "Fine",
    }
)


class ScriptedLLM:
    model_name = "fake"

    def __init__(self, reply=GOOD_REPLY, error=None):
        self.reply, self.error, self.calls = reply, error, 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def advisor(monkeypatch):
    """Stub the six data gatherers, the prompt builder and the LLM."""
    for name in GATHERERS:
        monkeypatch.setattr(
            f"portf_manager.services.portfolio_advisor.{name}",
            lambda *a, _n=name: {"source": _n},
        )
    monkeypatch.setattr(
        "portf_manager.services.portfolio_advisor.build_analysis_prompt",
        lambda bundle: f"analyse {sorted(bundle)}",
    )
    llm = ScriptedLLM()
    monkeypatch.setattr("portf_manager.llm_client.get_llm_client", lambda: llm)
    return llm


async def _analyse(client: AsyncClient, headers, **params):
    resp = await client.get(
        "/api/v1/research/portfolio-analysis", params=params, headers=headers
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_analysis_is_cached_until_refresh(
    async_test_client, auth_headers, advisor
):
    first = await _analyse(async_test_client, auth_headers)
    assert first["summary"] == "Fine"
    assert first["cache_ttl_hours"] == 24
    assert first["data_warnings"] == []

    await _analyse(async_test_client, auth_headers)
    assert advisor.calls == 1

    await _analyse(async_test_client, auth_headers, refresh="true")
    assert advisor.calls == 2


@pytest.mark.asyncio
async def test_llm_failure_names_the_reason_and_is_not_cached(
    async_test_client, auth_headers, advisor
):
    advisor.error = LLMError("Gemini", "m", "generate", 3, RuntimeError("503"))

    result = await _analyse(async_test_client, auth_headers)

    assert "failed after 3 attempts" in result["error"]
    advisor.error = None
    assert "error" not in await _analyse(async_test_client, auth_headers)


@pytest.mark.asyncio
async def test_unreadable_reply_is_an_error_and_not_cached(
    async_test_client, auth_headers, advisor
):
    advisor.reply = "not json"
    assert (
        "unexpected format"
        in (await _analyse(async_test_client, auth_headers))["error"]
    )
    advisor.reply = GOOD_REPLY
    assert "error" not in await _analyse(async_test_client, auth_headers)


@pytest.mark.asyncio
async def test_a_failed_gatherer_is_reported_not_fatal(
    async_test_client, auth_headers, advisor, monkeypatch
):
    def broken(*args):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr("portf_manager.services.portfolio_advisor.gather_risk", broken)

    result = await _analyse(async_test_client, auth_headers)

    assert result["data_warnings"] == ["risk data unavailable"]
    assert result["summary"] == "Fine"


@pytest.mark.asyncio
async def test_no_data_at_all_skips_the_llm(
    async_test_client, auth_headers, advisor, monkeypatch
):
    for name in GATHERERS:
        monkeypatch.setattr(
            f"portf_manager.services.portfolio_advisor.{name}", lambda *a: None
        )

    result = await _analyse(async_test_client, auth_headers)

    assert result["error"].startswith("No portfolio data available")
    assert advisor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,stored", [(0, 1), (12, 12), (500, 168)])
async def test_cache_ttl_is_clamped(async_test_client, auth_headers, requested, stored):
    put = await async_test_client.put(
        "/api/v1/research/portfolio-analysis/settings",
        json={"cache_ttl_hours": requested},
        headers=auth_headers,
    )
    assert put.json() == {"cache_ttl_hours": stored}
    got = await async_test_client.get(
        "/api/v1/research/portfolio-analysis/settings", headers=auth_headers
    )
    assert got.json() == {"cache_ttl_hours": stored}


@pytest.mark.asyncio
async def test_compare_computes_upside_and_sorts_best_first(
    async_test_client, auth_headers, test_database, monkeypatch
):
    monkeypatch.setattr("portf_manager.market._fetch_quote_live", lambda s: None)
    for symbol, price, fair in (("LOW", 100.0, 110.0), ("HIGH", 50.0, 75.0)):
        aid = test_database.create_asset(symbol, f"{symbol} Corp", "stock", "EUR")
        test_database.insert_price_record(
            symbol=symbol, price=price, fetched_ts=datetime.now(), source="test"
        )
        test_database.create_research_note(asset_id=aid, symbol=symbol, fair_value=fair)
    # Watchlist-only research with no price anywhere: no upside, listed last
    test_database.create_research_note(asset_id=None, symbol="NOPX", fair_value=10.0)

    resp = await async_test_client.get("/api/v1/research/compare", headers=auth_headers)

    rows = resp.json()
    assert [r["symbol"] for r in rows] == ["HIGH", "LOW", "NOPX"]
    assert [r["upside_pct"] for r in rows] == [50.0, 10.0, None]
    assert rows[0]["name"] == "HIGH Corp"
