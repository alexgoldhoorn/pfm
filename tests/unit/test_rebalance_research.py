"""Tests for the rebalancing and research/valuation routers."""

import pytest
from httpx import AsyncClient
from fastapi import status

from portf_manager.services.rebalance_planner import NO_CASH_WARNING


class TestRebalance:
    @pytest.mark.asyncio
    async def test_set_and_get_targets(
        self, async_test_client: AsyncClient, auth_headers
    ):
        targets = [
            {"asset_type": "stock", "target_pct": 60.0},
            {"asset_type": "etf", "target_pct": 40.0},
        ]
        resp = await async_test_client.put(
            "/api/v1/rebalance/targets", json=targets, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 2

        resp2 = await async_test_client.get(
            "/api/v1/rebalance/targets", headers=auth_headers
        )
        assert resp2.status_code == status.HTTP_200_OK
        types = {t["asset_type"] for t in resp2.json()}
        assert types == {"stock", "etf"}

    @pytest.mark.asyncio
    async def test_analysis_shape(self, async_test_client: AsyncClient, auth_headers):
        resp = await async_test_client.get(
            "/api/v1/rebalance/analysis", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "total_value_eur" in data
        assert "allocations" in data
        assert "actions" in data
        assert isinstance(data["allocations"], list)

    @pytest.mark.asyncio
    async def test_target_pct_validation(
        self, async_test_client: AsyncClient, auth_headers
    ):
        # >100 should be rejected by pydantic
        resp = await async_test_client.put(
            "/api/v1/rebalance/targets",
            json=[{"asset_type": "stock", "target_pct": 150.0}],
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestRebalancePlan:
    """POST /api/v1/rebalance/plan — end-to-end.

    The planner's own ranking/constraint rules are unit-tested in
    ``tests/unit/test_rebalance_planner.py``; these cover the HTTP contract:
    that a real portfolio produces real trades through the route, and that
    request validation still rejects what it should.

    All fixture data is invented — round prices, made-up symbols, EUR only
    (so nothing reaches a live FX lookup).
    """

    async def _set_targets(self, client: AsyncClient, headers: dict, targets: list):
        resp = await client.put(
            "/api/v1/rebalance/targets", json=targets, headers=headers
        )
        assert resp.status_code == status.HTTP_200_OK

    def _seed_portfolio(self, db):
        """€1,000 of a stock and €1,000 of an ETF, both priced in EUR.

        Against a 60/40 stock/etf target that is €200 underweight in stock
        and €200 overweight in etf — above the €100 default ``min_trade_eur``,
        so a real plan is one sell and one buy.
        """
        portfolio_id = db.create_portfolio("Example Broker", base_currency="EUR")
        seeded = {}
        for symbol, name, asset_type in (
            ("EXST", "Example Industries", "stock"),
            ("EXETF", "Example Global ETF", "etf"),
        ):
            asset_id = db.create_asset(
                symbol=symbol, name=name, asset_type=asset_type, currency="EUR"
            )
            db.create_transaction(
                asset_id=asset_id,
                transaction_type="buy",
                quantity=100.0,
                price=8.0,
                total_amount=800.0,
                transaction_date="2024-03-01",
                portfolio_id=portfolio_id,
                currency="EUR",
            )
            db.create_price(asset_id, 10.0, "2026-09-17")
            seeded[symbol] = asset_id
        return seeded

    @pytest.mark.asyncio
    async def test_plan_shape(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        # No target_overrides in the request => the planner must fall back
        # to saved allocation targets, so seed a valid (summing to 100) set
        # first.
        self._seed_portfolio(test_database)
        await self._set_targets(
            async_test_client,
            auth_headers,
            [
                {"asset_type": "stock", "target_pct": 60.0},
                {"asset_type": "etf", "target_pct": 40.0},
            ],
        )

        resp = await async_test_client.post(
            "/api/v1/rebalance/plan", json={}, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()

        assert "generated_at" in data
        assert "inputs" in data
        assert data["inputs"]["strategy"] == "balanced"
        assert data["inputs"]["max_trades"] == 12

        assert data["before"]["total_value_eur"] == 2000.0
        assert isinstance(data["before"]["allocations"], list)

        # Clean fixture data: nothing is mispriced, so nothing is warned
        # about at the top level.
        assert data["warnings"] == []

        plans = data["plans"]
        assert {p["strategy"] for p in plans} == {
            "tax_minimal",
            "closest_to_target",
            "balanced",
        }
        for plan in plans:
            trades = plan["trades"]
            # One sell out of the overweight etf, one buy into the
            # underweight stock — every strategy can see the same single
            # candidate, so all three agree here.
            assert [(t["side"], t["symbol"]) for t in trades] == [
                ("SELL", "EXETF"),
                ("BUY", "EXST"),
            ]
            sell, buy = trades
            assert sell["amount_eur"] == 200.0
            assert buy["amount_eur"] == 200.0
            # Bought at 8, now 10 → a 20% gain on the €200 sold.
            assert sell["estimated_gain_eur"] == 40.0
            assert sell["estimated_tax_eur"] == pytest.approx(40.0 * 0.19, abs=0.01)
            assert buy["estimated_gain_eur"] is None
            assert buy["estimated_tax_eur"] is None
            assert sell["reason"] and buy["reason"]

            summary = plan["summary"]
            assert summary["trade_count"] == len(trades)
            assert summary["buy_total_eur"] == 200.0
            assert summary["sell_total_eur"] == 200.0
            assert summary["estimated_realized_gain_eur"] == 40.0
            # Selling to target leaves the portfolio exactly on 60/40.
            assert summary["max_abs_drift_pct_after"] == 0.0
            assert plan["warnings"] == []

    @pytest.mark.asyncio
    async def test_plan_validation(self, async_test_client: AsyncClient, auth_headers):
        # Invalid strategy value.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"strategy": "not_a_real_strategy"},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # max_trades out of range.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan", json={"max_trades": 0}, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        resp = await async_test_client.post(
            "/api/v1/rebalance/plan", json={"max_trades": 101}, headers=auth_headers
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # min_trade_eur negative.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"min_trade_eur": -1},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # cash_budget_eur negative — a negative budget would otherwise reach
        # build_strategy_plan's available_cash computation and silently
        # produce a sell-only plan instead of being rejected.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"cash_budget_eur": -500.0},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # max_sell_gain_eur negative — same gap, a negative cap still "works"
        # (every sell trips it immediately) rather than being rejected.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={"max_sell_gain_eur": -1.0},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # target_overrides summing outside 99.5..100.5 — a service/route-level
        # check (it merges with DB-stored targets), still a 422 like the
        # Pydantic-enforced cases above, for a consistent client contract.
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={
                "target_overrides": [
                    {"asset_type": "stock", "target_pct": 60.0},
                    {"asset_type": "etf", "target_pct": 60.0},
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "detail" in resp.json()

    @pytest.mark.asyncio
    async def test_plan_buy_only_mode(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        # allow_sells=false with no cash budget: nothing funds a buy, so the
        # plan is empty *and says why* rather than looking like "no drift".
        self._seed_portfolio(test_database)
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={
                "allow_sells": False,
                "target_overrides": [
                    {"asset_type": "stock", "target_pct": 55.0},
                    {"asset_type": "etf", "target_pct": 45.0},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["inputs"]["allow_sells"] is False
        assert len(data["plans"]) == 3
        for plan in data["plans"]:
            assert plan["trades"] == []
            assert NO_CASH_WARNING in plan["warnings"]

    @pytest.mark.asyncio
    async def test_plan_buy_only_with_cash_budget(
        self, async_test_client: AsyncClient, auth_headers, test_database
    ):
        """A cash budget funds buys without any sell, and grows the total."""
        self._seed_portfolio(test_database)
        resp = await async_test_client.post(
            "/api/v1/rebalance/plan",
            json={
                "allow_sells": False,
                "cash_budget_eur": 500.0,
                "target_overrides": [
                    {"asset_type": "stock", "target_pct": 60.0},
                    {"asset_type": "etf", "target_pct": 40.0},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        for plan in resp.json()["plans"]:
            assert [(t["side"], t["symbol"]) for t in plan["trades"]] == [
                ("BUY", "EXST")
            ]
            # The stock gap is €200 and the budget covers it, so the whole
            # gap is bought and nothing is realised.
            assert plan["trades"][0]["amount_eur"] == 200.0
            assert plan["summary"]["sell_total_eur"] == 0.0
            assert plan["summary"]["estimated_realized_gain_eur"] == 0.0
            assert NO_CASH_WARNING not in plan["warnings"]


class TestResearch:
    @pytest.mark.asyncio
    async def test_report_404_when_none(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        # Create an asset first
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        resp = await async_test_client.get(
            f"/api/v1/research/{sample_asset_data['symbol']}", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_unknown_symbol_404(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/research/NOPE", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_set_and_get_price_targets(
        self, async_test_client: AsyncClient, auth_headers, sample_asset_data
    ):
        await async_test_client.post(
            "/api/v1/assets", json=sample_asset_data, headers=auth_headers
        )
        sym = sample_asset_data["symbol"]
        resp = await async_test_client.put(
            f"/api/v1/research/{sym}/targets",
            json={"buy_below": 100.0, "sell_above": 130.0},
            headers=auth_headers,
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["buy_below"] == 100.0
        assert data["sell_above"] == 130.0

    @pytest.mark.asyncio
    async def test_alerts_check_shape(
        self, async_test_client: AsyncClient, auth_headers
    ):
        resp = await async_test_client.get(
            "/api/v1/research/alerts/check", headers=auth_headers
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "alerts" in data
        assert "total" in data


class TestUpsertPriceTargetClearSemantics:
    """`upsert_price_target`'s COALESCE-vs-`clear` split (database.py).

    Manual/partial saves keep passing ``None`` for an untouched field and
    must keep the other stored fields exactly as they were (COALESCE
    no-op). ``clear`` is the opposite signal — "this field was evaluated and
    is explicitly empty" — and must write a real NULL. Only the
    automated/bulk research writer (`research.py::_run_bulk_research_refresh`)
    passes `clear`; see `tests/unit/test_research_bulk_refresh.py` for that
    caller's own regression test.
    """

    def _asset(self, db, symbol="ZANDER"):
        # Invented ticker/name — public repo, no real holdings in fixtures.
        return db.create_asset(symbol, "Zander Testing Corp", "stock", currency="USD")

    def test_partial_manual_upsert_leaves_other_fields_intact(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid,
            buy_below=10.0,
            sell_above=20.0,
            fair_value=15.0,
            notes="Initial manual note.",
        )

        # The manual UI's "edit one field" case: only buy_below is passed,
        # everything else arrives as None.
        test_database.upsert_price_target(asset_id=aid, buy_below=11.0)

        target = test_database.get_price_target(aid)
        assert target["buy_below"] == 11.0
        assert target["sell_above"] == 20.0
        assert target["fair_value"] == 15.0
        assert target["notes"] == "Initial manual note."

    def test_clear_nulls_only_the_named_field(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(
            asset_id=aid, buy_below=10.0, sell_above=20.0, fair_value=15.0
        )

        test_database.upsert_price_target(asset_id=aid, clear={"fair_value"})

        target = test_database.get_price_target(aid)
        assert target["fair_value"] is None
        assert target["buy_below"] == 10.0
        assert target["sell_above"] == 20.0

    def test_clear_overrides_a_value_passed_for_the_same_field(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(asset_id=aid, fair_value=15.0)

        # A value passed alongside clear for the same column is ignored.
        test_database.upsert_price_target(
            asset_id=aid, fair_value=99.0, clear={"fair_value"}
        )

        assert test_database.get_price_target(aid)["fair_value"] is None

    def test_clear_still_moves_updated_at(self, test_database):
        aid = self._asset(test_database)
        test_database.upsert_price_target(asset_id=aid, fair_value=15.0)
        with test_database.get_connection() as conn:
            conn.execute(
                "UPDATE price_targets SET updated_at = '2020-01-01 00:00:00' "
                "WHERE asset_id = ?",
                (aid,),
            )
            conn.commit()

        test_database.upsert_price_target(asset_id=aid, clear={"fair_value"})

        assert (
            test_database.get_price_target(aid)["updated_at"] != "2020-01-01 00:00:00"
        )

    def test_unknown_clear_field_raises(self, test_database):
        aid = self._asset(test_database)
        with pytest.raises(ValueError):
            test_database.upsert_price_target(asset_id=aid, clear={"bogus_field"})
