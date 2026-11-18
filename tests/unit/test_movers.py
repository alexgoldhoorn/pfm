"""Latest price moves among held positions (invented data)."""

from datetime import date

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager.services.movers import compute_movers

TODAY = date(2030, 5, 10)


def _holding(asset_id, symbol, value, asset_type="stock"):
    return {
        "asset_id": asset_id,
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "asset_type": asset_type,
        "total_value_eur": value,
    }


def _prices(*pairs):
    return [{"price": p, "price_date": d} for p, d in pairs]


def test_ranks_by_eur_effect_not_by_percent():
    holdings = [_holding(1, "AAA", 1100.0), _holding(2, "BBB", 105.0)]
    prices = {
        # +10% on 1100 is +100; +50% on 105 is +35
        1: _prices((110, "2030-05-09"), (100, "2030-05-08")),
        2: _prices((15, "2030-05-09"), (10, "2030-05-08")),
    }
    out = compute_movers(holdings, prices, TODAY)
    assert [r["symbol"] for r in out["up"]] == ["AAA", "BBB"]
    assert out["up"][0]["change_pct"] == 10.0
    assert out["up"][0]["change_eur"] == 100.0
    assert out["up"][1]["change_eur"] == 35.0
    assert out["down"] == []
    assert out["as_of"] == "2030-05-09"
    assert (out["counted"], out["skipped"]) == (2, 0)


def test_losers_sorted_worst_first_and_limit_applies():
    holdings = [_holding(i, f"S{i}", 1000.0) for i in range(1, 5)]
    prices = {
        1: _prices((99, "2030-05-09"), (100, "2030-05-08")),
        2: _prices((90, "2030-05-09"), (100, "2030-05-08")),
        3: _prices((95, "2030-05-09"), (100, "2030-05-08")),
        4: _prices((100, "2030-05-09"), (100, "2030-05-08")),
    }
    out = compute_movers(holdings, prices, TODAY, limit=2)
    assert [r["symbol"] for r in out["down"]] == ["S2", "S3"]
    # An unchanged price is counted but is neither a gainer nor a loser
    assert out["up"] == [] and out["counted"] == 4


def test_skips_cash_single_prices_stale_and_bad_prices():
    holdings = [
        _holding(1, "CASH", 500.0, asset_type="cash"),
        _holding(2, "ONE", 500.0),
        _holding(3, "OLD", 500.0),
        _holding(4, "NONE", 500.0),
        _holding(5, "ZERO", 500.0),
    ]
    prices = {
        1: _prices((1, "2030-05-09"), (1, "2030-05-08")),
        2: _prices((10, "2030-05-09")),
        3: _prices((12, "2030-04-01"), (10, "2030-03-31")),
        5: _prices((12, "2030-05-09"), (0, "2030-05-08"), (None, "2030-05-07")),
    }
    out = compute_movers(holdings, prices, TODAY)
    assert out["up"] == [] and out["down"] == []
    assert (out["counted"], out["skipped"]) == (0, 5)
    assert out["as_of"] is None


@pytest.mark.asyncio
async def test_movers_endpoint_on_an_empty_database(
    async_test_client: AsyncClient, auth_headers
):
    resp = await async_test_client.get(
        "/api/v1/portfolios/movers", headers=auth_headers
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {
        "up": [],
        "down": [],
        "as_of": None,
        "counted": 0,
        "skipped": 0,
    }
