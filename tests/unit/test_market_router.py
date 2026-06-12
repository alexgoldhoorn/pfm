"""Tests for the /api/v1/market router."""

import pytest
from httpx import AsyncClient
from fastapi import status

from portf_manager import market


def _fake_quote(symbol, price=100.0):
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": 99.0,
        "change_pct": 1.01,
        "currency": "USD",
        "name": None,
        "fetched_at": 0,
        "source": "cache",
        "stale": False,
    }


class TestMarketQuotes:
    @pytest.mark.asyncio
    async def test_batch_quotes(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market,
            "get_quotes",
            lambda db, syms, max_age: [_fake_quote(s) for s in syms],
        )
        resp = await async_test_client.get(
            "/api/v1/market/quotes?symbols=NVDA,ASML.AS&max_age=900",
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        quotes = resp.json()["quotes"]
        assert [q["symbol"] for q in quotes] == ["NVDA", "ASML.AS"]

    @pytest.mark.asyncio
    async def test_empty_symbols_rejected(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/market/quotes?symbols=,,", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_too_many_symbols_rejected(
        self, async_test_client: AsyncClient, auth_headers
    ):
        syms = ",".join(f"S{i}" for i in range(51))
        resp = await async_test_client.get(
            f"/api/v1/market/quotes?symbols={syms}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_single_quote(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(market, "get_quote", lambda db, s, max_age: _fake_quote(s))
        resp = await async_test_client.get(
            "/api/v1/market/quote/NVDA", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["symbol"] == "NVDA"

    @pytest.mark.asyncio
    async def test_requires_auth(self, async_test_client: AsyncClient):
        resp = await async_test_client.get("/api/v1/market/quotes?symbols=NVDA")
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class TestMarketFx:
    @pytest.mark.asyncio
    async def test_fx_rates(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market, "get_fx_eur", lambda db, cur, max_age: (0.93, False)
        )
        resp = await async_test_client.get(
            "/api/v1/market/fx?currencies=USD,GBP", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        rates = resp.json()["rates"]
        assert rates["USD"] == {"rate": 0.93, "stale": False}
        assert rates["GBP"] == {"rate": 0.93, "stale": False}


class TestMarketFundamentals:
    @pytest.mark.asyncio
    async def test_fundamentals(
        self, async_test_client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            market,
            "get_fundamentals",
            lambda db, s, max_age: {
                "symbol": s,
                "trailingPE": 30.0,
                "source": "cache",
                "stale": False,
            },
        )
        resp = await async_test_client.get(
            "/api/v1/market/fundamentals/NVDA", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["trailingPE"] == 30.0
