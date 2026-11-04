"""Managed (robo-advisor) holdings count in the allocation but are never traded."""

from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from portf_manager.services.rebalance_planner import (
    build_plan,
    managed_only_symbols,
)


def _buy(db, aid, pid, qty, price=10.0):
    db.create_transaction(
        asset_id=aid,
        transaction_type="buy",
        quantity=qty,
        price=price,
        total_amount=qty * price,
        transaction_date=(date.today() - timedelta(days=400)).isoformat(),
        portfolio_id=pid,
        currency="EUR",
    )


def _asset(db, symbol, asset_type, price=10.0):
    aid = db.create_asset(symbol, f"{symbol} Example", asset_type, currency="EUR")
    db.create_price(aid, price, date.today().isoformat())
    return aid


def _seed(db, mixed=False):
    """ROBO (managed only, etf), SELFETF (self, etf), BONDX (self, bond).

    With ``mixed`` an extra MIX etf is held in both portfolios. Targets are
    etf 40 / bond 60, so the etf type is overweight and bond underweight.
    """
    broker = db.get_or_create_portfolio("Broker", base_currency="EUR")
    robo = db.create_portfolio("Robo", base_currency="EUR", is_managed=True)
    ids = {n: _asset(db, n, t) for n, t in (("ROBO", "etf"), ("SELFETF", "etf"))}
    ids["BONDX"] = _asset(db, "BONDX", "bond")
    _buy(db, ids["ROBO"], robo, 100)
    _buy(db, ids["SELFETF"], broker, 20)
    _buy(db, ids["BONDX"], broker, 10)
    if mixed:
        ids["MIX"] = _asset(db, "MIX", "etf")
        _buy(db, ids["MIX"], broker, 30)
        _buy(db, ids["MIX"], robo, 30)
    db.set_allocation_target("etf", 40.0)
    db.set_allocation_target("bond", 60.0)
    return broker, robo, ids


def _symbols(plan):
    return {t["symbol"] for t in plan["trades"]}


class TestPlanner:
    def test_managed_only_symbol_helper(self, test_database):
        _seed(test_database)
        txns = test_database.get_all_transactions()
        symbols, names = managed_only_symbols(test_database, txns)
        assert symbols == {"ROBO"}
        assert names == ["Robo"]

    def test_managed_only_symbol_never_traded_but_counted(self, test_database):
        _seed(test_database)
        result = build_plan(test_database, min_trade_eur=1.0)
        assert result["managed_symbols"] == ["ROBO"]
        etf = next(
            a for a in result["before"]["allocations"] if a["asset_type"] == "etf"
        )
        # 100 ROBO + 20 SELFETF at 10 EUR each.
        assert etf["current_value_eur"] == 1200.0
        sells = [t for p in result["plans"] for t in p["trades"] if t["side"] == "SELL"]
        assert sells, "the self-managed etf should still be sellable"
        for plan in result["plans"]:
            assert "ROBO" not in _symbols(plan)
        assert any("never traded: ROBO" in w for w in result["warnings"])

    def test_mixed_symbol_stays_tradable_with_warning(self, test_database):
        _seed(test_database, mixed=True)
        result = build_plan(
            test_database, min_trade_eur=1.0, excluded_symbols=["SELFETF"]
        )
        assert result["managed_symbols"] == ["ROBO"]
        sold = {
            t["symbol"]
            for p in result["plans"]
            for t in p["trades"]
            if t["side"] == "SELL"
        }
        assert sold == {"MIX"}
        assert any("Part of MIX" in w for w in result["warnings"])

    def test_managed_portfolio_id_yields_no_trades(self, test_database):
        _, robo, _ = _seed(test_database)
        result = build_plan(test_database, portfolio_id=robo, cash_budget_eur=500.0)
        assert all(p["trades"] == [] for p in result["plans"])
        assert any("managed by a robo-advisor" in w for w in result["warnings"])

    def test_no_managed_portfolio_is_unchanged(self, test_database):
        broker = test_database.get_or_create_portfolio("Broker", base_currency="EUR")
        a = _asset(test_database, "AAA", "etf")
        _buy(test_database, a, broker, 20)
        test_database.set_allocation_target("etf", 100.0)
        result = build_plan(test_database, min_trade_eur=1.0)
        assert result["managed_symbols"] == []
        assert not any("managed" in w for w in result["warnings"])


@pytest.mark.asyncio
class TestApi:
    async def test_analysis_managed_block(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _seed(test_database)
        resp = await async_test_client.get(
            "/api/v1/rebalance/analysis", headers=auth_headers
        )
        assert resp.status_code == 200
        managed = resp.json()["managed"]
        # 1000 of 1300 EUR.
        assert managed["value_eur"] == 1000.0
        assert managed["pct"] == 76.9
        assert managed["portfolios"] == ["Robo"]

    async def test_analysis_without_managed(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        resp = await async_test_client.get(
            "/api/v1/rebalance/analysis", headers=auth_headers
        )
        assert resp.json()["managed"] == {
            "value_eur": 0.0,
            "pct": 0.0,
            "portfolios": [],
        }

    async def test_plan_inputs_report_managed_symbols(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        _seed(test_database)
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"min_trade_eur": 1},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["inputs"]["managed_symbols"] == ["ROBO"]
        for plan in body["plans"]:
            assert "ROBO" not in {t["symbol"] for t in plan["trades"]}
