"""Return attribution: flow-adjusted gain per asset, type and portfolio."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi import status
from httpx import AsyncClient

from portf_manager.services.attribution import compute_attribution

START = date(2026, 1, 31)
END = date(2026, 6, 30)


def _fx_now(currency: str) -> float:
    return 1.0 if currency == "EUR" else 0.5


def _fx_on(currency: str, on) -> float:
    return 1.0 if currency == "EUR" else 0.5


def _buy(db, aid, pid, day, qty, price, currency="EUR", tx_type="buy", tax=0.0):
    db.create_transaction(
        asset_id=aid,
        transaction_type=tx_type,
        quantity=qty,
        price=price,
        total_amount=qty * price,
        transaction_date=day,
        portfolio_id=pid,
        currency=currency,
        tax=tax,
    )


def _seed(db):
    """Two brokers, three assets, prices at both boundaries."""
    self_pid = db.get_or_create_portfolio("Self", base_currency="EUR")
    robo_pid = db.get_or_create_portfolio("Robo", base_currency="EUR")
    up = db.create_asset("UPX", "Example Up Corp", "stock", currency="EUR")
    down = db.create_asset("DNX", "Example Down Fund", "etf", currency="EUR")
    usd = db.create_asset("USX", "Example USD ETF", "etf", currency="USD")

    # held through the whole window, price 100 → 120 (+200 on 10 units)
    _buy(db, up, self_pid, "2025-12-01", 10, 90)
    db.create_price(up, 100.0, START.isoformat())
    db.create_price(up, 120.0, END.isoformat())

    # bought inside the window at 50, ends at 40 (−100 on 10 units)
    _buy(db, down, robo_pid, "2026-03-15", 10, 50)
    db.create_price(down, 40.0, END.isoformat())

    # USD asset: 10 units, price 200 → 200 USD, FX 0.5 → no gain; one 10 USD
    # dividend with 1.5 USD withholding → +4.25 EUR income
    _buy(db, usd, self_pid, "2025-11-20", 10, 180, currency="USD")
    db.create_price(usd, 200.0, START.isoformat())
    db.create_price(usd, 200.0, END.isoformat())
    _buy(
        db,
        usd,
        self_pid,
        "2026-05-02",
        1,
        10,
        currency="USD",
        tx_type="dividend",
        tax=1.5,
    )
    return self_pid, robo_pid


class TestComputeAttribution:
    def test_gain_is_flow_adjusted_per_asset(self, test_database):
        _seed(test_database)
        out = compute_attribution(test_database, START, END, _fx_now, _fx_on)
        by = {a["symbol"]: a for a in out["by_asset"]}

        assert by["UPX"]["start_value_eur"] == 1000.0
        assert by["UPX"]["end_value_eur"] == 1200.0
        assert by["UPX"]["gain_eur"] == 200.0

        # the 500 EUR buy is money in, not a gain; the price drop is the loss
        assert by["DNX"]["start_value_eur"] == 0.0
        assert by["DNX"]["bought_eur"] == 500.0
        assert by["DNX"]["gain_eur"] == -100.0

        # USD: (10 − 1.5) USD × 0.5 = 4.25 EUR of income, no price move
        assert by["USX"]["start_value_eur"] == 1000.0
        assert by["USX"]["income_eur"] == 4.25
        assert by["USX"]["gain_eur"] == 4.25

    def test_sorted_best_first_with_tops_and_shares(self, test_database):
        _seed(test_database)
        out = compute_attribution(test_database, START, END, _fx_now, _fx_on)
        assert [a["symbol"] for a in out["by_asset"]] == ["UPX", "USX", "DNX"]
        assert [a["symbol"] for a in out["top_contributors"]] == ["UPX", "USX"]
        assert [a["symbol"] for a in out["top_detractors"]] == ["DNX"]
        # weight and contribution are shares of the 2000 EUR start value
        up = out["by_asset"][0]
        assert up["weight_start_pct"] == 50.0
        assert up["contribution_pct"] == 10.0

    def test_totals_and_groupings(self, test_database):
        _seed(test_database)
        out = compute_attribution(test_database, START, END, _fx_now, _fx_on)
        t = out["totals"]
        assert t["start_value_eur"] == 2000.0
        assert t["end_value_eur"] == 2600.0
        assert t["bought_eur"] == 500.0
        assert t["gain_eur"] == 104.25
        # simple Dietz: 104.25 / (2000 + 0.5 × 500)
        assert t["return_pct"] == 4.63
        assert t["positions"] == 3

        by_type = {g["asset_type"]: g["gain_eur"] for g in out["by_type"]}
        assert by_type == {"stock": 200.0, "etf": -95.75}
        by_port = {g["portfolio"]: g["gain_eur"] for g in out["by_portfolio"]}
        assert by_port == {"Self": 204.25, "Robo": -100.0}
        assert out["unpriced"] == []
        assert out["period"] == {
            "start": "2026-01-31",
            "end": "2026-06-30",
            "days": 150,
        }

    def test_portfolio_filter(self, test_database):
        _, robo = _seed(test_database)
        out = compute_attribution(
            test_database, START, END, _fx_now, _fx_on, portfolio_id=robo
        )
        assert [a["symbol"] for a in out["by_asset"]] == ["DNX"]
        assert out["totals"]["gain_eur"] == -100.0

    def test_unpriced_boundary_is_valued_at_cost_and_flagged(self, test_database):
        pid = test_database.get_or_create_portfolio("Self", base_currency="EUR")
        aid = test_database.create_asset(
            "NOP", "No Price Corp", "stock", currency="EUR"
        )
        _buy(test_database, aid, pid, "2025-12-01", 10, 90)
        # a price only at the end: the start falls back to the 900 EUR cost
        test_database.create_price(aid, 95.0, END.isoformat())
        out = compute_attribution(test_database, START, END, _fx_now, _fx_on)
        row = out["by_asset"][0]
        assert row["start_value_eur"] == 900.0
        assert row["end_value_eur"] == 950.0
        assert row["priced"] is False
        assert out["unpriced"] == ["NOP"]

    def test_sold_position_keeps_its_profit(self, test_database):
        pid = test_database.get_or_create_portfolio("Self", base_currency="EUR")
        aid = test_database.create_asset("SLD", "Sold Corp", "stock", currency="EUR")
        _buy(test_database, aid, pid, "2025-12-01", 10, 100)
        test_database.create_price(aid, 100.0, START.isoformat())
        _buy(test_database, aid, pid, "2026-04-01", 10, 130, tx_type="sell")
        out = compute_attribution(test_database, START, END, _fx_now, _fx_on)
        row = out["by_asset"][0]
        assert row["end_value_eur"] == 0.0
        assert row["sold_eur"] == 1300.0
        assert row["gain_eur"] == 300.0

    def test_empty_database(self, test_database):
        out = compute_attribution(test_database, START, END, _fx_now, _fx_on)
        assert out["by_asset"] == []
        assert out["totals"]["return_pct"] is None

    def test_end_before_start_is_an_error(self, test_database):
        with pytest.raises(ValueError):
            compute_attribution(test_database, END, START, _fx_now, _fx_on)


@pytest.fixture
def _offline_fx():
    """The router's FX helpers would hit Yahoo for the USD line; pin them."""
    with (
        patch("portf_server.routers.analytics._fx", _fx_now),
        patch(
            "portf_server.routers.analytics._fx_on",
            lambda db, cur, d: _fx_on(cur, d),
        ),
    ):
        yield


@pytest.mark.usefixtures("_offline_fx")
class TestAttributionEndpoint:
    @pytest.mark.asyncio
    async def test_explicit_window(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _seed(test_database)
        resp = await async_test_client.get(
            "/api/v1/analytics/attribution",
            params={"start": START.isoformat(), "end": END.isoformat()},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["period"]["start"] == "2026-01-31"
        assert data["by_asset"][0]["symbol"] == "UPX"

    @pytest.mark.asyncio
    async def test_all_starts_before_first_transaction(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _seed(test_database)
        resp = await async_test_client.get(
            "/api/v1/analytics/attribution",
            params={"period": "all", "end": END.isoformat()},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["period"]["start"] == "2025-11-19"
        # everything is a flow when the window opens before the first buy
        assert data["totals"]["start_value_eur"] == 0.0

    @pytest.mark.asyncio
    async def test_bad_period_and_inverted_window(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/attribution",
            params={"period": "2w"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        resp = await async_test_client.get(
            "/api/v1/analytics/attribution",
            params={"start": "2026-06-30", "end": "2026-01-31"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_all_with_no_transactions_is_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/analytics/attribution",
            params={"period": "all"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
